"""Guards the per-step metadata the routine editor renders."""

import unittest

from src.task.routine.RoutinePlans import (
    ENTRIES, ENTRIES_BY_ID, describe_params, stamina_text,
)

# Icons the kit ships; a typo here would leave a blank row in the editor.
KIT_ICONS = {'bell', 'pulse', 'calendar', 'bolt', 'layers', 'grid', 'scan',
             'history', 'tasks'}


class RoutineCatalogMetadataTest(unittest.TestCase):
    def test_every_step_has_a_description(self):
        for entry in ENTRIES:
            with self.subTest(step=entry.task_id):
                self.assertTrue(entry.description, entry.task_id)
                self.assertTrue(entry.description.endswith('。'), entry.description)

    def test_every_step_uses_a_kit_icon(self):
        for entry in ENTRIES:
            with self.subTest(step=entry.task_id):
                self.assertIn(entry.icon, KIT_ICONS)

    def test_stamina_matches_the_known_costs(self):
        for entry in ENTRIES:
            with self.subTest(step=entry.task_id):
                self.assertIn(entry.stamina, (0, 40, 60))
                # Reward pickups are free; farming and weekly bosses cost stamina.
                if entry.group == '奖励':
                    self.assertEqual(entry.stamina, 0)

    def test_id_lookup_covers_every_entry(self):
        self.assertEqual({entry.task_id for entry in ENTRIES}, set(ENTRIES_BY_ID))

    def test_stamina_text(self):
        self.assertEqual(stamina_text(ENTRIES_BY_ID['forgery']), '40 体力 / 次')
        self.assertEqual(stamina_text(ENTRIES_BY_ID['tacet']), '60 体力 / 次')
        self.assertEqual(stamina_text(ENTRIES_BY_ID['claim_daily']), '')


class DescribeParamsTest(unittest.TestCase):
    def test_lists_key_and_value_in_config_order(self):
        entry = ENTRIES_BY_ID['forgery']
        pairs = describe_params(entry, {'地区': '测试区', '武器类型': '长刃', '次数': 0})
        self.assertEqual(pairs, ['地区 测试区', '武器类型 长刃', '次数 0'])

    def test_booleans_read_as_enabled_state(self):
        entry = ENTRIES_BY_ID['training_guide']
        pairs = describe_params(entry, {'目标角色': '', '体力预算': 240,
                                        '补凝素领域': True, '补讨伐强敌': False,
                                        '补战歌重奏': True})
        self.assertEqual(pairs, ['体力预算 240', '补凝素领域 已开启',
                                 '补讨伐强敌 未开启', '补战歌重奏 已开启'])

    def test_empty_values_are_skipped(self):
        entry = ENTRIES_BY_ID['training_guide']
        pairs = describe_params(entry, {'目标角色': '', '体力预算': 0,
                                        '补凝素领域': False, '补讨伐强敌': False,
                                        '补战歌重奏': False})
        self.assertEqual(pairs, ['体力预算 0', '补凝素领域 未开启',
                                 '补讨伐强敌 未开启', '补战歌重奏 未开启'])

    def test_missing_params_fall_back_to_defaults(self):
        entry = ENTRIES_BY_ID['weekly_garden']
        pairs = describe_params(entry, None)
        self.assertTrue(pairs)
        self.assertTrue(all(pair.split(' ')[0] in entry.default_config for pair in pairs))

    def test_free_step_has_no_pairs(self):
        self.assertEqual(describe_params(ENTRIES_BY_ID['claim_daily'], {}), [])


if __name__ == '__main__':
    unittest.main()
