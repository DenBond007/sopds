#!/usr/bin/env python3
"""Explicit operator commands for an already installed SOPDS TrueNAS application."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

LABEL = 'io.sopds.installation'


def run(command):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError('Command failed; inspect the application logs (exit %s)' % result.returncode)
    return result.stdout.decode().strip()


def discover_project(installation_id):
    identifiers = run(['docker', 'ps', '--filter', 'label=' + LABEL + '=' + installation_id,
                       '--filter', 'label=com.docker.compose.service=db', '--format', '{{.ID}}']).splitlines()
    if len(identifiers) != 1:
        raise RuntimeError('Expected exactly one running database for this installation; install bootstrap.yaml first')
    project = run(['docker', 'inspect', '--format', '{{index .Config.Labels "com.docker.compose.project"}}', identifiers[0]])
    if not project or project == '<no value>':
        raise RuntimeError('Database has no Compose project label')
    return project


def compose_command(directory, project):
    return ['docker', 'compose', '--project-name', project,
            '--env-file', str(directory / 'deployment/runtime.env'),
            '-f', str(directory / 'deployment/app.yaml')]


def initialize(compose):
    running = set(run(compose + ['ps', '--services', '--status', 'running']).split())
    if running != {'db'}:
        raise RuntimeError('Initialization requires bootstrap mode with only db running')
    count = run(compose + ['exec', '-T', 'db', 'psql', '-X', '-U', 'postgres', '-d', 'sopds',
                          '-At', '-v', 'ON_ERROR_STOP=1', '-c',
                          "SELECT count(*) FROM pg_tables WHERE schemaname='public'"])
    if count != '0':
        raise RuntimeError('Database is not empty; init-new is refused. Use the upgrade/recovery instructions.')
    manage = compose + ['run', '--rm', '--no-deps', '-T', 'manage']
    commands = [
        ['check'], ['migrate', '--noinput'], ['loaddata', 'genre.json'],
        ['sopds_util', 'setconf', 'SOPDS_ROOT_LIB', '/library'],
        ['sopds_util', 'setconf', 'SOPDS_TEMP_DIR', '/tmp/sopds'],
        ['sopds_util', 'setconf', 'SOPDS_SCANNER_LOG', '/tmp/sopds-scanner.log'],
        ['sopds_util', 'setconf', 'SOPDS_AUTH', 'True'], ['collectstatic', '--noinput'],
    ]
    for command in commands:
        run(manage + command)
    print('New database initialized. Create an administrator, then switch the app to app.yaml.')


def verify_image(info, entry):
    if entry.get('identity') == 'repo-digest':
        digest = entry['tag'].rsplit('@', 1)[1]
        # RepoDigests can normalize docker.io/library and remove the human-readable tag.
        matched = any(value.rsplit('@', 1)[-1] == digest for value in info.get('RepoDigests', []))
    else:
        matched = info['Id'] in entry.get('accepted_ids', [entry['id']])
    if not matched or info['Architecture'] != 'amd64' or info['Os'] != 'linux':
        raise RuntimeError('Loaded image does not match release.json')


def pull_images(release):
    if release.get('delivery', 'offline') != 'registry':
        raise RuntimeError('Offline kit: load images.tar with docker load instead')
    for entry in release['images'].values():
        if entry.get('identity') != 'repo-digest' or '@sha256:' not in entry['tag']:
            raise RuntimeError('Registry images must be pinned by digest')
    for entry in release['images'].values():
        print('Pulling ' + entry['tag'], flush=True)
        try:
            run(['docker', 'pull', '--platform', 'linux/amd64', entry['tag']])
        except RuntimeError as error:
            raise RuntimeError('Image pull failed; check registry login (read:packages), network and digest') from error
        info = json.loads(run(['docker', 'image', 'inspect', '--format', '{{json .}}', entry['tag']]))
        verify_image(info, entry)
    print('All release images downloaded and verified. No containers were started.')


def preflight(directory, settings, release):
    if str(directory.resolve()) != settings['package_dir']:
        raise RuntimeError('Package location differs from package_dir in settings')
    for key in ('library', 'data_dir', 'backup_dir', 'package_dir'):
        path = Path(settings[key])
        if not path.is_dir() or path.resolve() != path:
            raise RuntimeError('Missing directory or symlink: ' + key)
    owners = {'pgdata': 999, 'static': 10001, 'caddy-data': 10001,
              'caddy-config': 10001, 'backup-status': None}
    for name, owner in owners.items():
        path = Path(settings['data_dir']) / name
        if not path.is_dir() or path.resolve() != path:
            raise RuntimeError('Missing data directory: ' + name)
        if owner is not None and path.stat().st_uid != owner:
            raise RuntimeError('Wrong owner for %s (expected UID %s)' % (name, owner))
    if Path(settings['backup_dir']).stat().st_mode & 0o077:
        raise RuntimeError('Backup directory must be private (0700)')
    for entry in release['images'].values():
        info = json.loads(run(['docker', 'image', 'inspect', '--format', '{{json .}}', entry['tag']]))
        verify_image(info, entry)
    print('Paths, owners and image IDs verified. Check library ACL read/traverse access for UID 10001.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('pull', 'preflight', 'init-new', 'create-admin', 'check', 'upgrade', 'backup'))
    parser.add_argument('--retain', type=int, default=7)
    parser.add_argument('--database-only', action='store_true')
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    release = json.loads((directory / 'release.json').read_text())
    if args.command == 'pull':
        pull_images(release)
        return
    settings = json.loads((directory / 'deployment/settings.json').read_text())
    if args.command == 'preflight':
        preflight(directory, settings, release)
        return
    if str(directory) != settings['package_dir']:
        raise RuntimeError('Run operator commands from package_dir on the NAS')
    credentials = json.loads((directory / 'deployment/secrets.json').read_text())
    project = discover_project(credentials['installation_id'])
    compose = compose_command(directory, project)
    manage = compose + ['run', '--rm', '--no-deps', '-T', 'manage']
    if args.command == 'init-new':
        initialize(compose)
    elif args.command == 'create-admin':
        subprocess.run(compose + ['run', '--rm', '--no-deps', 'manage', 'createsuperuser'], check=True)
    elif args.command == 'check':
        run(manage + ['check'])
        run(manage + ['migrate', '--check'])
        print(run(compose + ['ps', '--format', 'table']))
    elif args.command == 'upgrade':
        running = set(run(compose + ['ps', '--services', '--status', 'running']).split())
        if running != {'db'}:
            raise RuntimeError('Switch to bootstrap.yaml before upgrading the database')
        run(manage + ['migrate', '--noinput'])
        run(manage + ['collectstatic', '--noinput'])
        print('Migrations/static complete; switch the application back to app.yaml.')
    elif args.command == 'backup':
        command = [sys.executable, str(directory / 'maintenance.py'),
                   '--env-file', str(directory / 'deployment/runtime.env'),
                   '--compose-file', str(directory / 'deployment/app.yaml'), '--project-name', project,
                   '--release-file', str(directory / 'release.json'),
                   '--backup-dir', settings['backup_dir'],
                   '--status-dir', settings['data_dir'] + '/backup-status', '--retain', str(args.retain)]
        for name in ('secrets.json', 'settings.json', 'bootstrap.yaml'):
            command += ['--extra-config', str(directory / 'deployment' / name)]
        if args.database_only:
            command.append('--database-only')
        subprocess.run(command, check=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        raise SystemExit('Operator command failed: ' + str(error))
