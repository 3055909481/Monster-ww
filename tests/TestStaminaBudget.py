"""Pure-Python tests for the custom-flow stamina wait ledger."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.task.routine.StaminaBudget import (
    DAILY_STAMINA_CAP, RESET_HOUR, StaminaWaitLedger, day_key, plan_stamina_wait,
    reset_day,
)


class ResetDayTest(unittest.TestCase):
    def test_before_reset_belongs_to_previous_day(self):
        self.assertEqual(reset_day(datetime(2026, 11, 26, 3, 59)).isoformat(), '2026-11-25')

    def test_at_reset_belongs_to_current_day(self):
        self.assertEqual(reset_day(datetime(2026, 11, 26, 4, 0)).isoformat(), '2026-11-26')
        self.assertEqual(reset_day(datetime(2026, 11, 26, 23, 59)).isoformat(), '2026-11-26')

    def test_day_key_isolates_accounts(self):
        now = datetime(2026, 11, 26, 12, 0)
        self.assertNotEqual(day_key(now, '1317520'), day_key(now, 'abc@outlook.com'))
        self.assertEqual(day_key(now, ' 1317520 '), day_key(now, '1317520'))


class LedgerTest(unittest.TestCase):
    def test_add_waited_and_remaining(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = StaminaWaitLedger(folder)
            self.assertEqual(ledger.waited(), 0)
            self.assertEqual(ledger.remaining(), DAILY_STAMINA_CAP)
            ledger.add(60, now=datetime(2026, 11, 26, 12, 0))
            self.assertEqual(ledger.waited(now=datetime(2026, 11, 26, 12, 0)), 60)
            self.assertEqual(ledger.remaining(now=datetime(2026, 11, 26, 12, 0)), 180)

    def test_cap_at_240(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = StaminaWaitLedger(folder)
            now = datetime(2026, 11, 26, 12, 0)
            ledger.add(200, now=now)
            ledger.add(100, now=now)
            self.assertEqual(ledger.waited(now=now), DAILY_STAMINA_CAP)
            self.assertEqual(ledger.remaining(now=now), 0)

    def test_new_game_day_resets_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = StaminaWaitLedger(folder)
            ledger.add(240, now=datetime(2026, 11, 26, 12, 0))
            self.assertEqual(ledger.remaining(now=datetime(2026, 11, 26, 12, 0)), 0)
            # 03:59 is still the previous game day, so the old entry is kept.
            self.assertEqual(ledger.remaining(now=datetime(2026, 11, 27, 3, 59)), 0)
            # 04:00 rolls over to a fresh day.
            self.assertEqual(ledger.remaining(now=datetime(2026, 11, 27, 4, 0)), DAILY_STAMINA_CAP)

    def test_accounts_have_separate_budgets(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = StaminaWaitLedger(folder)
            now = datetime(2026, 11, 26, 12, 0)
            ledger.add(120, now=now, account='1317520')
            self.assertEqual(ledger.waited(now=now, account='1317520'), 120)
            self.assertEqual(ledger.waited(now=now, account='abc@outlook.com'), 0)

    def test_corrupt_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'StaminaWaitLedger.json').write_text('{bad json', encoding='utf-8')
            self.assertEqual(StaminaWaitLedger(folder).waited(), 0)

    def test_atomic_write_produces_valid_json(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = StaminaWaitLedger(folder)
            ledger.add(30, now=datetime(2026, 11, 26, 12, 0))
            data = json.loads(Path(folder, 'StaminaWaitLedger.json').read_text(encoding='utf-8'))
            self.assertTrue(data)
            self.assertFalse(list(Path(folder).glob('.*.tmp')))


class PlanStaminaWaitTest(unittest.TestCase):
    def test_already_at_target_skips(self):
        self.assertEqual(plan_stamina_wait(160, 160, 240), (False, 160))

    def test_no_budget_skips(self):
        self.assertEqual(plan_stamina_wait(60, 160, 0), (False, 60))

    def test_normal_wait(self):
        self.assertEqual(plan_stamina_wait(100, 160, 240), (True, 160))

    def test_capped_by_daily_budget(self):
        # 30 remaining but a run needs 60: not worth waiting at all.
        self.assertEqual(plan_stamina_wait(100, 160, 30), (False, 100))
        # With a smaller per-run cost the capped wait is still useful.
        self.assertEqual(plan_stamina_wait(100, 160, 30, need=20), (True, 130))

    def test_gap_smaller_than_one_run_skips(self):
        self.assertEqual(plan_stamina_wait(155, 160, 240, need=60), (False, 155))

    def test_negative_stamina_skips(self):
        self.assertEqual(plan_stamina_wait(-1, 160, 240), (False, -1))


if __name__ == '__main__':
    unittest.main()
