#!/usr/bin/env python3
"""Package a TrueNAS kit with offline images or immutable registry references; never deploys."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
POSTGRES = 'postgres:17.11-bookworm@sha256:91eb910c44c7ed13f7f1a4ccadaa9ca72ef14cddc04cacb6e070e48eb44731a3'


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def capture(command, **kwargs):
    return run(command, stdout=subprocess.PIPE, **kwargs).stdout.decode().strip()


def checksum(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def base_config():
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith(('SOPDS_', 'COMPOSE_')) or key in ('POSTGRES_PASSWORD', 'TIME_ZONE'):
            env.pop(key)
    env.update(SOPDS_PRIVATE_HOST='192.0.2.10', SOPDS_LIBRARY='/template/library',
               SOPDS_BACKUP_STATUS_DIR='/template/backup-status', SOPDS_HTTPS_PORT='9444',
               SOPDS_SECRET_KEY='a' * 64, SOPDS_DB_PASSWORD='b' * 64,
               POSTGRES_PASSWORD='c' * 64, TIME_ZONE='Europe/Moscow')
    return json.loads(capture(['docker', 'compose', '--env-file', '/dev/null',
                              '-f', str(ROOT / 'infra/docker/compose.yaml'), '--profile', 'tools',
                              'config', '--format', 'json'], env=env))



def add_portable_image_ids(archive_path, images):
    """Docker's classic store uses config IDs; containerd can expose manifest/index IDs."""
    with tarfile.open(archive_path) as archive:
        manifests = json.load(archive.extractfile('manifest.json'))
        for entry in images.values():
            saved = next(item for item in manifests if entry['tag'] in (item['RepoTags'] or []))
            config = archive.extractfile(saved['Config']).read()
            config_id = 'sha256:' + hashlib.sha256(config).hexdigest()
            accepted = {entry['id'], config_id}
            # Also accept the verified platform manifest when a loader unwraps the index.
            for member in archive.getmembers():
                if not member.isfile() or not member.name.startswith('blobs/sha256/') or member.size > 1024 * 1024:
                    continue
                data = archive.extractfile(member).read()
                try:
                    value = json.loads(data)
                except (ValueError, UnicodeDecodeError):
                    continue
                if (isinstance(value, dict) and isinstance(value.get('config'), dict)
                        and value['config'].get('digest') == config_id):
                    accepted.add('sha256:' + hashlib.sha256(data).hexdigest())
            entry['accepted_ids'] = sorted(accepted)


def registry_images(app, caddy):
    images = {}
    for key, reference in (('app', app), ('caddy', caddy)):
        if not re.fullmatch(r'ghcr\.io/[a-z0-9_.-]+/[a-z0-9_./-]+@sha256:[0-9a-f]{64}', reference or ''):
            raise ValueError(key + ' must be a GHCR image pinned by sha256 digest')
        images[key] = {'tag': reference, 'identity': 'repo-digest'}
    images['db'] = {'tag': POSTGRES, 'identity': 'repo-digest'}
    return images


def offline_images(destination, revision, dirty):
    tag = 'truenas-' + revision[:12] + '-' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    images = {key: {'tag': 'sopds-' + key + ':' + tag} for key in ('app', 'caddy', 'db')}
    for key, dockerfile, context in (('app', ROOT / 'Dockerfile', ROOT),
                                     ('caddy', ROOT / 'infra/docker/Caddy.Dockerfile', ROOT / 'infra/docker')):
        run(['docker', 'build', '--platform', 'linux/amd64', '--label', 'org.opencontainers.image.revision=' + revision,
             '--label', 'io.sopds.source-dirty=' + str(dirty).lower(), '-t', images[key]['tag'],
             '-f', str(dockerfile), str(context)])
    # The pinned amd64 manifest is the same member used by the tested local stack.
    # It is re-tagged for offline loading: docker save/load need not preserve RepoDigests.
    postgres = POSTGRES
    available = subprocess.run(['docker', 'image', 'inspect', postgres], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if available.returncode:
        run(['docker', 'pull', '--platform', 'linux/amd64', postgres])
    run(['docker', 'tag', postgres, images['db']['tag']])
    for value in images.values():
        # Query just immutable image identity/platform, not runtime container environment.
        value['id'] = capture(['docker', 'image', 'inspect', '--format', '{{.Id}}', value['tag']])
        platform = capture(['docker', 'image', 'inspect', '--format', '{{.Os}}/{{.Architecture}}', value['tag']])
        if platform != 'linux/amd64':
            raise RuntimeError('Unexpected image platform')
    run(['docker', 'image', 'save', '--output', str(destination / 'images.tar')] + [x['tag'] for x in images.values()])
    add_portable_image_ids(destination / 'images.tar', images)
    return images


def write_kit(destination, archive_path, base, release):
    for filename, value in (('release.json', release), ('compose.base.json', base)):
        (destination / filename).write_text(json.dumps(value, indent=2) + '\n')
    for source, name in (
        (ROOT / 'scripts/truenas/prepare.py', 'prepare.py'),
        (ROOT / 'scripts/truenas/control.py', 'control.py'),
        (ROOT / 'scripts/ops/maintenance.py', 'maintenance.py'),
        (ROOT / 'infra/truenas/settings.example.json', 'settings.example.json'),
        (ROOT / 'infra/docker/init-db.sh', 'init-db.sh'),
        (ROOT / 'docs/modernization/truenas-kit.md', 'README.md'),
    ):
        shutil.copyfile(source, destination / name)
    # PostgreSQL must be able to read its file bind mount; it contains no credentials.
    (destination / 'init-db.sh').chmod(0o444)
    entries = [checksum(path) + '  ' + path.name for path in sorted(destination.iterdir()) if path.is_file()]
    (destination / 'SHA256SUMS').write_text('\n'.join(entries) + '\n')
    with tarfile.open(archive_path, 'x:gz') as archive:
        archive.add(destination, arcname=destination.name)
    (archive_path.with_name(archive_path.name + '.sha256')).write_text(checksum(archive_path) + '  ' + archive_path.name + '\n')
    return archive_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=('offline', 'registry'), default='offline')
    parser.add_argument('--app-image')
    parser.add_argument('--caddy-image')
    args = parser.parse_args()
    if args.mode == 'registry':
        images = registry_images(args.app_image, args.caddy_image)
    elif args.app_image or args.caddy_image:
        parser.error('Image references require --mode registry')
    os.umask(0o077)
    destination = args.output.resolve()
    archive_path = destination.with_name(destination.name + '.tar.gz')
    if destination.exists() or archive_path.exists():
        raise SystemExit('Output already exists; choose a new output path')
    base = base_config()
    revision = capture(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'])
    dirty = bool(capture(['git', '-C', str(ROOT), 'status', '--porcelain']))
    destination.mkdir(parents=True, mode=0o700)
    if args.mode == 'offline':
        images = offline_images(destination, revision, dirty)
    release = {'format': 'sopds-truenas-v1', 'delivery': args.mode,
               'commit': revision, 'git_working_tree_dirty': dirty,
               'uv_lock_sha256': checksum(ROOT / 'uv.lock'), 'images': images,
               'postgres_source': POSTGRES}
    if args.mode == 'offline':
        release['images_sha256'] = checksum(destination / 'images.tar')
    write_kit(destination, archive_path, base, release)
    print('Prepared ' + args.mode + ' kit: ' + str(archive_path))
    print('No containers were started and no images were published by this script.')


if __name__ == '__main__':
    main()
