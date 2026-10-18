#!/usr/bin/env python3
"""Run one verified daily backup after 03:30 Moscow, independent of login sessions."""
import argparse
from datetime import datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
ZONE = ZoneInfo('Europe/Moscow')


def is_due(now, latest):
    now = now.astimezone(ZONE)
    if now.hour * 60 + now.minute < 210:
        return False
    try:
        stamp = datetime.fromisoformat(latest['completed_at']).astimezone(ZONE)
        return not (latest.get('verified') is True and latest.get('scope') == 'database-and-library'
                    and stamp.date() == now.date() and stamp <= now)
    except (KeyError, TypeError, ValueError, AttributeError):
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--backup-dir', type=Path, required=True)
    parser.add_argument('--amd64', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    status_dir = ROOT/'.local'/'backup-status'
    state = ROOT/'.local'/'maintenance-attempt.json'
    fd = os.open(ROOT/'.local'/'maintenance.lock', os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        now = datetime.now(ZONE)
        try:
            latest = json.loads((status_dir/'latest.json').read_text())
        except (OSError, ValueError):
            latest = {}
        if not is_due(now, latest):
            return 0
        try:
            previous = datetime.fromisoformat(json.loads(state.read_text())['attempted_at'])
            if now - previous < timedelta(hours=1):
                return 0
        except (OSError, ValueError, KeyError, TypeError):
            pass
        state.write_text(json.dumps({'attempted_at':now.isoformat()}))
        command = [sys.executable, str(ROOT/'scripts/ops/maintenance.py'),
                   '--env-file', str(args.env_file.resolve()), '--backup-dir', str(args.backup_dir.resolve())]
        if args.amd64:
            command.append('--amd64')
        return subprocess.run(command).returncode
    finally:
        os.close(fd)


if __name__ == '__main__':
    raise SystemExit(main())
