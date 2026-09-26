"""Bound the lifetime of external converters, including worker interruption."""

import os
import signal
import subprocess


CONVERTER_TIMEOUT_SECONDS = 300


def wait_for_converter(process, timeout):
    try:
        process.communicate(timeout=timeout)
    except BaseException:
        # Gunicorn raises SystemExit on worker abort. Timeout alone is not
        # sufficient: the converter runs in its own process group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise
