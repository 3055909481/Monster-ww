"""Weekly routine steps (战歌重奏 / 周度游历奖励) run-day scheduling."""

import unittest
from datetime import date

from src.task.routine.RoutinePlans import (
    ANY_DAY, ENTRIES_BY_ID, RUN_DAYS_KEY, RUN_DAY_OPTIONS, WEEKDAYS,
    is_scheduled_today, normalize_items,
)


class TestRoutineRunDays(unittest.TestCase):
    # 2026-11-19 is Thursday -> 周四 (index 3)
    THURSDAY = date(2026, 11, 19)

    def test_weekly_entries_use_drop_down_default_any_day(self):
        for task_id in ('weekly_garden', 'weekly_boss'):
            entry = ENTRIES_BY_ID[task_id]
            self.assertEqual(entry.default_config[RUN_DAYS_KEY], ANY_DAY)
            ctype = entry.config_type[RUN_DAYS_KEY]
            self.assertEqual(ctype['type'], 'drop_down')
            self.assertEqual(ctype['options'], WEEKDAYS + [ANY_DAY])

    def test_run_day_options_are_weekdays_plus_any(self):
        self.assertEqual(RUN_DAY_OPTIONS, ['周一', '周二', '周三', '周四', '周五', '周六', '周日', '不限制'])

    def test_non_weekly_entry_has_no_run_days(self):
        self.assertNotIn(RUN_DAYS_KEY, ENTRIES_BY_ID['forgery'].default_config)

    def test_runs_only_on_selected_day(self):
        params = {RUN_DAYS_KEY: '周四'}
        self.assertTrue(is_scheduled_today(params, self.THURSDAY))
        self.assertFalse(is_scheduled_today(params, date(2026, 11, 20)))  # 周五

    def test_any_day_runs_every_day(self):
        for params in ({}, {RUN_DAYS_KEY: ANY_DAY}, {RUN_DAYS_KEY: ''},
                       {RUN_DAYS_KEY: 'bad-value'}, None):
            self.assertTrue(is_scheduled_today(params, self.THURSDAY))

    def test_legacy_multi_select_list_still_works(self):
        # A plan saved by the previous (multi-select) build must not break.
        self.assertTrue(is_scheduled_today({RUN_DAYS_KEY: ['周四']}, self.THURSDAY))
        self.assertFalse(is_scheduled_today({RUN_DAYS_KEY: ['周一']}, self.THURSDAY))
        self.assertTrue(is_scheduled_today({RUN_DAYS_KEY: WEEKDAYS}, self.THURSDAY))
        self.assertTrue(is_scheduled_today({RUN_DAYS_KEY: []}, self.THURSDAY))

    def test_normalize_fills_run_days_for_legacy_plan(self):
        # A saved plan from before this feature has no 运行日 key; normalize must
        # add the default (不限制) so behaviour is unchanged.
        items = normalize_items([
            {'id': 'weekly_boss', 'enabled': True, 'params': {'次数': 2}},
            {'id': 'weekly_garden', 'enabled': True, 'params': {}},
        ])
        for item in items:
            self.assertEqual(item['params'][RUN_DAYS_KEY], ANY_DAY)

    def test_normalize_coerces_legacy_list_to_single_value(self):
        items = normalize_items([
            {'id': 'weekly_boss', 'enabled': True, 'params': {RUN_DAYS_KEY: ['周六']}},
            {'id': 'weekly_garden', 'enabled': True, 'params': {RUN_DAYS_KEY: WEEKDAYS}},
        ])
        self.assertEqual(items[0]['params'][RUN_DAYS_KEY], '周六')
        self.assertEqual(items[1]['params'][RUN_DAYS_KEY], ANY_DAY)

    def test_normalize_preserves_valid_single_value(self):
        items = normalize_items([
            {'id': 'weekly_boss', 'enabled': True, 'params': {RUN_DAYS_KEY: '周日'}},
        ])
        self.assertEqual(items[0]['params'][RUN_DAYS_KEY], '周日')


if __name__ == '__main__':
    unittest.main()
