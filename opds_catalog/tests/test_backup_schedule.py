import importlib.util
from pathlib import Path
from datetime import datetime
from unittest import TestCase
from zoneinfo import ZoneInfo

spec=importlib.util.spec_from_file_location('sopds_daily',Path(__file__).resolve().parents[2]/'scripts/ops/daily-maintenance.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BackupScheduleTests(TestCase):
    def test_moscow_time_is_used_independent_of_host_timezone(self):
        self.assertFalse(module.is_due(datetime.fromisoformat('2026-10-18T00:29:00+00:00'),{}))
        self.assertTrue(module.is_due(datetime.fromisoformat('2026-10-18T00:30:00+00:00'),{}))

    def test_today_verified_full_backup_prevents_duplicate_run(self):
        now=datetime.fromisoformat('2026-10-18T10:00:00+00:00')
        value={'verified':True,'scope':'database-and-library','completed_at':'2026-10-18T08:00:00+00:00'}
        self.assertFalse(module.is_due(now,value))
        value['scope']='database-only'
        self.assertTrue(module.is_due(now,value))
        self.assertTrue(module.is_due(now,[]))
        self.assertTrue(module.is_due(now,{'completed_at':'broken'}))
