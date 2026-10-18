#!/usr/bin/env python3
"""Quiesced Compose backups with mandatory restore verification and safe retention."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[2]
BACKUP_NAME = re.compile(r'sopds-\d{8}T\d{6}Z-[a-f0-9]{8}$')


def run(args, *, output=None, data=None):
    result = subprocess.run(args, input=data, stdout=output or subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        # Do not echo stderr: a database/proxy error could contain credentials.
        raise RuntimeError('Maintenance subprocess failed (exit %s)' % result.returncode)
    return result.stdout


def checksum(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def prune(root, retain):
    """Only completed snapshots created by this tool are eligible for deletion."""
    if retain < 1:
        raise ValueError('Retain at least one backup')
    root = root.resolve()
    complete = []
    for path in root.iterdir():
        if not BACKUP_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_dir():
            continue
        manifest = path / 'manifest.json'
        if manifest.is_symlink():
            continue
        try:
            value = json.loads(manifest.read_text())
            if isinstance(value, dict) and value.get('verified') is True and value.get('tool') == 'sopds-maintenance-v1':
                complete.append(path)
        except (OSError, ValueError, TypeError):
            continue
    for path in sorted(complete, reverse=True)[retain:]:
        shutil.rmtree(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', required=True, type=Path)
    parser.add_argument('--backup-dir', required=True, type=Path)
    parser.add_argument('--status-dir', type=Path, default=ROOT/'.local'/'backup-status')
    parser.add_argument('--retain', type=int, default=7)
    parser.add_argument('--database-only', action='store_true')
    parser.add_argument('--amd64', action='store_true')
    args = parser.parse_args()
    if args.retain < 1:
        parser.error('--retain must be positive')
    backup_root = args.backup_dir.resolve()
    if backup_root == Path('/') or backup_root == ROOT:
        parser.error('Use a dedicated backup directory')
    os.umask(0o077)
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if backup_root.stat().st_mode & 0o077:
        parser.error('Backup directory must have private permissions (0700)')
    compose = ['docker', 'compose', '--project-name', 'sopds-container',
               '--env-file', str(args.env_file.resolve()),
               '-f', str(ROOT / 'infra/docker/compose.yaml')]
    if args.amd64:
        compose += ['-f', str(ROOT / 'infra/docker/compose.amd64.yaml')]
    running = set(run(compose + ['ps', '--services', '--status', 'running']).decode().split())
    if not {'db', 'web', 'caddy'}.issubset(running):
        raise RuntimeError('Start the sopds-container environment before maintenance')
    services = [name for name in ('scanner', 'annotations', 'web') if name in running]
    web_id = run(compose + ['ps', '-q', 'web']).decode().strip()
    app_image = run(['docker', 'inspect', '--format', '{{.Image}}', web_id]).decode().strip()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    name = 'sopds-' + stamp + '-' + uuid.uuid4().hex[:8]
    pending = backup_root / ('.pending-' + name)
    pending.mkdir(mode=0o700)
    restore_db = 'sopds_restore_' + uuid.uuid4().hex
    db = compose + ['exec', '-T', 'db']
    psql = ['psql', '-X', '-U', 'postgres', '-At', '-v', 'ON_ERROR_STOP=1']
    created = False
    try:
        run(compose + ['stop'] + services)
        # No application writes can race the dump or row fingerprints.
        with (pending/'database.dump').open('xb') as output:
            run(db + ['pg_dump', '-U', 'postgres', '-Fc', 'sopds'], output=output)
        with (pending/'caddy-data.tar').open('xb') as output:
            run(compose + ['exec', '-T', 'caddy', 'tar', '-C', '/data', '-cf', '-', '.'], output=output)
        if not args.database_only:
            with (pending/'library.tar').open('xb') as output:
                run(compose + ['run', '--rm', '--no-deps', '--entrypoint', 'tar',
                               'manage', '-C', '/library', '-cf', '-', '.'], output=output)
        # Keep private configuration with its matching snapshot, never in Git.
        shutil.copyfile(args.env_file, pending/'runtime.env')
        (pending/'runtime.env').chmod(0o600)
        run(db + ['createdb', '-U', 'postgres', '-O', 'sopds', restore_db])
        created = True
        with (pending/'database.dump').open('rb') as source:
            result = subprocess.run(db + ['pg_restore', '-U', 'postgres', '-d', restore_db,
                '--exit-on-error', '--no-owner', '--no-privileges'], stdin=source,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if result.returncode:
            raise RuntimeError('Backup restore failed')
        tables = run(db + psql + ['-d', 'sopds', '-c',
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"]).decode().splitlines()
        fingerprints = {}
        for table in tables:
            identifier = '"' + table.replace('"', '""') + '"'
            query = "SELECT count(*)::text || ':' || md5(COALESCE(string_agg(row_data, E'\\n' ORDER BY row_data), '')) FROM (SELECT row_to_json(t)::text AS row_data FROM public." + identifier + ' t) s'
            before = run(db + psql + ['-d', 'sopds', '-c', query])
            after = run(db + psql + ['-d', restore_db, '-c', query])
            if before != after:
                raise RuntimeError('Restored data fingerprint mismatch')
            fingerprints[table] = before.decode().strip()
        for archive_path in pending.glob('*.tar'):
            with tarfile.open(archive_path) as archive:
                for member in archive:
                    if member.name.startswith('/') or '..' in Path(member.name).parts:
                        raise RuntimeError('Unsafe backup archive member')
        files = {p.name: checksum(p) for p in pending.iterdir() if p.is_file()}
        commit = run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).decode().strip()
        manifest = {'tool': 'sopds-maintenance-v1', 'verified': True,
            'completed_at': datetime.now(timezone.utc).isoformat(), 'commit': commit,
            'app_image_id': app_image, 'uv_lock_sha256': checksum(ROOT/'uv.lock'),
            'git_working_tree_dirty': bool(run(['git', '-C', str(ROOT), 'status', '--porcelain'])),
            'scope': 'database-and-library' if not args.database_only else 'database-only',
            'files': files, 'tables': fingerprints}
        (pending/'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        pending.rename(backup_root/name)
        status_dir = args.status_dir.resolve()
        if status_dir == backup_root or status_dir == Path('/') or status_dir == ROOT:
            raise ValueError('Use a separate directory for public backup status')
        status_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
        status_dir.chmod(0o755)
        temporary = status_dir / ('.status-' + uuid.uuid4().hex + '.json')
        temporary.write_text(json.dumps({'verified': True, 'completed_at':manifest['completed_at'],
            'name': name, 'scope':manifest['scope']}))
        temporary.chmod(0o644)
        temporary.replace(status_dir/'latest.json')
        prune(backup_root, args.retain)
        print('Verified backup: ' + str(backup_root/name))
    finally:
        try:
            if created:
                run(db + ['dropdb', '-U', 'postgres', '--if-exists', restore_db])
        finally:
            # Restart exactly the services that were running before this invocation.
            run(compose + ['start'] + services)
    run(compose + ['run', '--rm', '--no-deps', 'manage', 'clearsessions'])


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError) as error:
        raise SystemExit(type(error).__name__ + ': maintenance failed; inspect service logs')
