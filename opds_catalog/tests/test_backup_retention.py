import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

spec = importlib.util.spec_from_file_location('sopds_maintenance', Path(__file__).resolve().parents[2] / 'scripts/ops/maintenance.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BackupRetentionTests(TestCase):
    def test_only_own_verified_snapshots_are_removed(self):
        with TemporaryDirectory() as root, TemporaryDirectory() as external:
            folder = Path(root)
            names = ['sopds-20261001T030000Z-aaaaaaaa', 'sopds-20261002T030000Z-bbbbbbbb']
            for name in names:
                p=folder/name; p.mkdir()
                (p/'manifest.json').write_text(json.dumps({'verified':True,'tool':'sopds-maintenance-v1'}))
            unknown=folder/'operator-data'; unknown.mkdir()
            incomplete=folder/'sopds-20261003T030000Z-cccccccc'; incomplete.mkdir()
            linked=folder/'sopds-20261004T030000Z-dddddddd'; linked.symlink_to(external)
            module.prune(folder, 1)
            self.assertFalse((folder/names[0]).exists())
            self.assertTrue((folder/names[1]).exists())
            self.assertTrue(unknown.exists())
            self.assertTrue(incomplete.exists())
            self.assertTrue(linked.is_symlink())
            self.assertTrue(Path(external).exists())
