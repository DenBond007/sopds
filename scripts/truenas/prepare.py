#!/usr/bin/env python3
"""Render private TrueNAS Compose files without contacting Docker or the NAS."""
import argparse
from copy import deepcopy
import ipaddress
import json
import os
import re
from pathlib import Path, PurePosixPath
import secrets
import uuid
from zoneinfo import ZoneInfo

LABEL = 'io.sopds.installation'
SECRET_KEYS = ('SOPDS_SECRET_KEY', 'SOPDS_DB_PASSWORD', 'POSTGRES_PASSWORD')


def validate_settings(value):
    required = {'host', 'port', 'timezone', 'library', 'data_dir', 'backup_dir', 'package_dir'}
    if set(value) != required:
        raise ValueError('Settings must contain exactly: ' + ', '.join(sorted(required)))
    if not isinstance(value['host'], str):
        raise ValueError('host must be an IPv4 address string')
    address = ipaddress.IPv4Address(value['host'])
    if address.is_loopback or address.is_unspecified or address.is_multicast or address.is_link_local or address.is_reserved:
        raise ValueError('host must be the NAS LAN IPv4 address')
    if type(value['port']) is not int or not 1024 <= value['port'] <= 65535:
        raise ValueError('port must be an integer between 1024 and 65535')
    ZoneInfo(value['timezone'])
    paths = []
    for key in ('library', 'data_dir', 'backup_dir', 'package_dir'):
        raw = value[key]
        path = PurePosixPath(raw)
        if (not raw.startswith('/mnt/') or len(path.parts) < 4 or str(path) != raw
                or '..' in path.parts or '$' in raw or any(ord(c) < 32 for c in raw)):
            raise ValueError(key + ' must be a normalized absolute path below /mnt/<pool>/')
        paths.append(path)
    for i, left in enumerate(paths):
        for right in paths[i + 1:]:
            if left == right or left in right.parents or right in left.parents:
                raise ValueError('Library, data, backup and package directories must not overlap')
    return value


def load_secrets(path):
    if path.exists():
        if path.is_symlink() or path.stat().st_mode & 0o077:
            raise ValueError('secrets.json must be a private regular file (0600)')
        value = json.loads(path.read_text())
    else:
        value = {key: secrets.token_hex(32) for key in SECRET_KEYS}
        value['installation_id'] = str(uuid.uuid4())
        with path.open('x') as output:
            json.dump(value, output, indent=2)
        path.chmod(0o600)
    for key in SECRET_KEYS:
        secret = value.get(key, '')
        if len(secret) < 64 or any(c not in '0123456789abcdef' for c in secret):
            raise ValueError('Secrets must be independent hexadecimal strings of at least 64 characters')
    if len({value[key] for key in SECRET_KEYS}) != len(SECRET_KEYS):
        raise ValueError('Secrets must be independent')
    uuid.UUID(value['installation_id'])
    return value


def render(base, settings, credentials, release):
    validate_settings(settings)
    delivery = release.get('delivery', 'offline')
    if delivery not in ('offline', 'registry'):
        raise ValueError('Unknown image delivery mode')
    if delivery == 'registry':
        for entry in release['images'].values():
            if (entry.get('identity') != 'repo-digest'
                    or not re.fullmatch(r'[a-z0-9][a-z0-9_./:-]*@sha256:[0-9a-f]{64}', entry['tag'])):
                raise ValueError('Registry images must be pinned by sha256 digest')
    result = deepcopy(base)
    result.pop('name', None)
    # Canonical Compose output contains fixed project names; let TrueNAS name its networks.
    for network in result['networks'].values():
        network.pop('name', None)
    result.pop('volumes', None)
    result = {key: value for key, value in result.items() if not key.startswith('x-')}
    mounts = {
        '/library': settings['library'],
        '/backup-status': settings['data_dir'] + '/backup-status',
        '/srv/static': settings['data_dir'] + '/static',
        '/var/lib/postgresql/data': settings['data_dir'] + '/pgdata',
        '/data': settings['data_dir'] + '/caddy-data',
        '/config': settings['data_dir'] + '/caddy-config',
        '/docker-entrypoint-initdb.d/10-sopds.sh': settings['package_dir'] + '/init-db.sh',
    }
    for name, service in result['services'].items():
        service.pop('build', None)
        service['image'] = release['images']['db' if name == 'db' else 'caddy' if name == 'caddy' else 'app']['tag']
        service['platform'] = 'linux/amd64'
        service['pull_policy'] = 'missing' if delivery == 'registry' else 'never'
        service.setdefault('labels', {})[LABEL] = credentials['installation_id']
        env = service.setdefault('environment', {})
        for key in SECRET_KEYS:
            if key in env:
                env[key] = credentials[key]
        if 'DB_PASS' in env:
            env['DB_PASS'] = credentials['SOPDS_DB_PASSWORD']
        if 'SOPDS_PRIVATE_HOST' in env:
            env['SOPDS_PRIVATE_HOST'] = settings['host']
        if 'TIME_ZONE' in env:
            env['TIME_ZONE'] = settings['timezone']
        for mount in service.get('volumes', []):
            target = mount['target']
            if target not in mounts:
                raise ValueError('Unrecognized mount target: ' + target)
            mount['type'] = 'bind'
            mount['source'] = mounts[target]
            mount['bind'] = {'create_host_path': False}
            mount.pop('volume', None)
        if name == 'caddy':
            service['ports'] = [{'target': 8443, 'published': str(settings['port']),
                                 'host_ip': settings['host'], 'protocol': 'tcp'}]
    bootstrap = deepcopy(result)
    bootstrap['services'] = {name: result['services'][name] for name in ('db', 'manage')}
    bootstrap['networks'] = {'backend': result['networks']['backend']}
    return result, bootstrap


def private_write(path, value):
    # Atomic replacement avoids leaving half-written configuration after interruption.
    temporary = path.with_name('.' + path.name + '.tmp-' + uuid.uuid4().hex)
    try:
        with temporary.open('x') as output:
            output.write(value)
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare(directory, settings):
    validate_settings(settings)
    os.umask(0o077)
    deployment = directory / 'deployment'
    deployment.mkdir(mode=0o700, exist_ok=True)
    if deployment.is_symlink() or deployment.stat().st_mode & 0o077:
        raise ValueError('deployment directory must have private permissions (0700)')
    credentials = load_secrets(deployment / 'secrets.json')
    app, bootstrap = render(json.loads((directory / 'compose.base.json').read_text()), settings,
                            credentials, json.loads((directory / 'release.json').read_text()))
    for name, value in (('app.yaml', app), ('bootstrap.yaml', bootstrap), ('settings.json', settings)):
        # JSON is a YAML 1.2 subset, accepted by Docker Compose and TrueNAS's YAML parser.
        private_write(deployment / name, json.dumps(value, indent=2) + '\n')
    private_write(deployment / 'runtime.env', ''.join(key + '=' + credentials[key] + '\n' for key in SECRET_KEYS))
    return deployment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--settings', type=Path, required=True)
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    try:
        target = prepare(directory, json.loads(args.settings.read_text()))
    except (ValueError, KeyError, TypeError, OSError) as error:
        raise SystemExit('Preparation failed: ' + str(error))
    print('Prepared private configuration in ' + str(target))
    print('Existing secrets are preserved. No images, containers or datasets were changed.')


if __name__ == '__main__':
    main()
