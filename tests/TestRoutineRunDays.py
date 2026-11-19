"""Weekly routine steps (战歌重奏 / 周度游历奖励) run-day scheduling."""

import unittest
from datetime import date

from src.task.routine.RoutinePlans import (
    ENTRIES_BY_ID, RUN_DAYS_KEY, WEEKDAYS, is_scheduled_today, normalize_items,
)


class TestRoutineRunDays(unittest.TestCase):
    # 2026-11-19 is Thursday -> 周四 (index 3)
    THURSDAY = date(2026, 11, 19)

    def test_weekly_entries_have_run_days_default_all_week(self):
        for task_id in ('weekly_garden', 'weekly_boss'):
            entry = ENTRIES_BY_ID[task_id]
            self.assertEqual(entry.default_config[RUN_DAYS_KEY], WEEKDAYS)
            self.assertEqual(entry.config_type[RUN_DAYS_KEY]['type'], 'multi_selection')
            self.assertEqual(entry.config_type[RUN_DAYS_KEY]['options'], WEEKDAYS)

    def test_non_weekly_entry_has_no_run_days(self):
        self.assertNotIn(RUN_DAYS_KEY, ENTRIES_BY_ID['forgery'].default_config)

    def test_runs_only_on_selected_day(self):
        params = {RUN_DAYS_KEY: ['周四']}
        self.assertTrue(is_scheduled_today(params, self.THURSDAY))
        self.assertFalse(is_scheduled_today(params, date(2026, 11, 20)))  # 周五

    def test_runs_on_any_of_selected_days(self):
        params = {RUN_DAYS_KEY: ['周一', '周四', '周日']}
        self.assertTrue(is_scheduled_today(params, self.THURSDAY))
        self.assertTrue(is_scheduled_today(params, date(2026, 11, 23)))  # 周一
        self.assertFalse(is_scheduled_today(params, date(2026, 11, 21)))  # 周六

    def test_empty_or_missing_selection_means_no_restriction(self):
        for params in ({}, {RUN_DAYS_KEY: []}, {RUN_DAYS_KEY: 'bad'}, None):
            self.assertTrue(is_scheduled_today(params, self.THURSDAY))

    def test_normalize_fills_run_days_for_legacy_plan(self):
        # A saved plan from before this feature has no 运行日 key; normalize must
        # add the default (all week) so behaviour is unchanged.
        items = normalize_items([
            {'id': 'weekly_boss', 'enabled': True, 'params': {'次数': 2}},
            {'id': 'weekly_garden', 'enabled': True, 'params': {}},
        ])
        for item in items:
            self.assertEqual(item['params'][RUN_DAYS_KEY], WEEKDAYS)

    def test_normalize_preserves_custom_run_days(self):
        items = normalize_items([
            {'id': 'weekly_boss', 'enabled': True,
             'params': {RUN_DAYS_KEY: ['周六', '周日']}},
        ])
        self.assertEqual(items[0]['params'][RUN_DAYS_KEY], ['周六', '周日'])
        self.assertTrue(is_scheduled_today(items[0]['params'], date(2026, 11, 21)))
        self.assertFalse(is_scheduled_today(items[0]['params'], self.THURSDAY))


if __name__ == '__main__':
    unittest.main()
