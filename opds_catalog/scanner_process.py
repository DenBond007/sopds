"""Linux process identity for the legacy scanner PID-file commands."""
import fcntl
import json
import os
import select
import signal
from contextlib import contextmanager
from pathlib import Path

from django.core.management.base import CommandError


def process_start_time(pid):
    # The command name in stat may contain spaces or parentheses.
    return Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()[19]


def process_record(pid):
    return json.dumps({'pid': pid, 'start_time': process_start_time(pid)})


@contextmanager
def scanner_pidfile(path):
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'r+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise CommandError('A scanner already owns this PID file') from error
        os.fchmod(stream.fileno(), 0o600)
        record = process_record(os.getpid())
        stream.seek(0)
        stream.truncate()
        stream.write(record)
        stream.flush()
        try:
            yield
        finally:
            try:
                if Path(path).read_text() == record:
                    Path(path).unlink()
            except FileNotFoundError:
                pass


def stop_scanner(record, timeout=30):
    try:
        identity = json.loads(record)
        if not isinstance(identity, dict):
            raise ValueError('Legacy numeric PID has no process identity')
        pid = identity['pid']
        started = identity['start_time']
        if type(pid) is not int or pid < 2 or not isinstance(started, str):
            raise ValueError('Invalid PID identity')
    except (ValueError, KeyError, TypeError) as error:
        raise CommandError(
            'Untrusted or old PID file; no signal sent. Stop the verified '
            'old process manually, then start the scanner under systemd.'
        ) from error
    try:
        descriptor = os.pidfd_open(pid)
    except ProcessLookupError as error:
        raise CommandError('Scanner PID is no longer running; no signal sent') from error
    try:
        try:
            matches = process_start_time(pid) == started
        except FileNotFoundError:
            matches = False
        if not matches:
            raise CommandError('Stale PID identity; no signal sent')
        # The fd pins the process identity across exit/PID reuse.
        signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        if not select.select([descriptor], [], [], timeout)[0]:
            raise CommandError(
                'Scanner is still finishing its current scan; restart cancelled'
            )
    finally:
        os.close(descriptor)
