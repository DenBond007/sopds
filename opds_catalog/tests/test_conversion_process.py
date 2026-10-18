import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.test import SimpleTestCase

from opds_catalog.conversion_process import wait_for_converter


class ConversionProcessTests(SimpleTestCase):
    def test_worker_abort_reaps_converter_and_stops_its_child(self):
        with TemporaryDirectory() as temporary:
            child_file = Path(temporary, 'child.pid')
            program = (
                'import subprocess, sys, time; from pathlib import Path; '
                'child = subprocess.Popen([sys.executable, "-c", '
                '"import time; time.sleep(60)"]); '
                'Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(60)'
            )
            process = subprocess.Popen(
                [sys.executable, '-c', program, str(child_file)],
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 5
                while not child_file.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(child_file.exists())
                communicate = process.communicate
                interrupted = False

                def abort_once(*args, **kwargs):
                    nonlocal interrupted
                    if not interrupted:
                        interrupted = True
                        raise SystemExit(1)
                    return communicate(*args, **kwargs)

                with patch.object(process, 'communicate', side_effect=abort_once):
                    with self.assertRaises(SystemExit):
                        wait_for_converter(process, 10)
                self.assertEqual(process.returncode, -signal.SIGKILL)
                child = int(child_file.read_text())
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        status = Path(f'/proc/{child}/stat').read_text()
                    except (FileNotFoundError, ProcessLookupError):
                        # The process may disappear while procfs reads its stat.
                        break
                    if status.split(') ', 1)[1].split()[0] == 'Z':
                        break
                    time.sleep(0.01)
                else:
                    self.fail('Converter child is still running after worker abort')
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
