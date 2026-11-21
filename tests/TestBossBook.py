"""Tests for name-based boss lookup used by the F2 workshop book."""

import sys
import types
import unittest
from unittest.mock import patch

# Stub Mon.Logger so pure logic imports without the framework runtime.
if "Mon" not in sys.modules:
    _mon = types.ModuleType("Mon")
    _mon.Logger = types.SimpleNamespace(get_logger=lambda name: types.SimpleNamespace(warning=lambda *a: None))
    sys.modules["Mon"] = _mon

from src.task.BossBook import (
    CATEGORY_OF_MODE, display_name, edit_distance, known_names, match_distance,
    merge_order, nearer_to_other, norm, option_label, realm_of, same_name, to_option,
    serial_target,
)


class TestBossBook(unittest.TestCase):
    def test_category_mapping(self):
        self.assertEqual(CATEGORY_OF_MODE['Weekly Challenge'], 'zhange')
        self.assertEqual(CATEGORY_OF_MODE['Boss Challenge'], 'qiangdi')

    def test_norm_strips_decoration_and_confusables(self):
        self.assertEqual(norm('梦魇·赫卡忒·战歌重奏'), '梦魇赫卡忒')
        self.assertEqual(norm('無冠巨像之心'), '无冠巨像之心')  # 繁体常见误读被归一

    def test_display_name(self):
        self.assertEqual(display_name('虚妄诞生之种·战歌重奏'), '虚妄诞生之种')

    def test_edit_distance_and_tolerance(self):
        self.assertEqual(edit_distance('abc', 'abd'), 1)
        self.assertEqual(match_distance('梦魇朔雷之鳞', '梦魇·朔雷之鳞'), 0)
        self.assertIsNone(match_distance('完全不同', '虚妄诞生之种'))

    def test_nearer_to_other_disambiguates_similar_names(self):
        others = ['梦魇·云闪之鳞']
        self.assertTrue(nearer_to_other('梦魇·云闪之鳞', 1, others))

    def test_same_name_symmetric(self):
        # same_name 是 OCR 模糊比对；Boss 名↔副本名的映射走 option_label/realm_of
        self.assertTrue(same_name('虚妄诞生之种', '虚妄诞生之种'))
        self.assertTrue(same_name('梦魇朔雷之鳞', '梦魇·朔雷之鳞'))
        self.assertTrue(same_name('梦魇·朔雷之鳞', '梦魇朔雷之鳞'))

    def test_option_label_and_realm_of(self):
        self.assertEqual(option_label('zhange', '虚妄诞生之种'), '达妮娅（虚妄诞生之种）')
        self.assertEqual(realm_of('达妮娅（虚妄诞生之种）'), '虚妄诞生之种')

    def test_to_option_migrates_legacy_realm_name(self):
        self.assertEqual(to_option('zhange', '虚妄诞生之种'), '达妮娅（虚妄诞生之种）')

    def test_known_names_seed(self):
        names = known_names('zhange')
        self.assertIn('虚妄诞生之种', names)
        self.assertIn('定序诸理之律', names)

    def test_serial_target_maps_weekly_order(self):
        # 实机 bug：serial=5 只读到 3 行，目标却是列表第 5 个「烬夜天启之章」
        self.assertEqual(serial_target('zhange', 5), '烬夜天启之章')
        self.assertEqual(serial_target('zhange', 1), '定序诸理之律')
        self.assertEqual(serial_target('zhange', 11), '昔日咏叹之钟')

    def test_serial_target_out_of_range_is_empty(self):
        self.assertEqual(serial_target('zhange', 0), '')
        self.assertEqual(serial_target('zhange', 99), '')
        self.assertEqual(serial_target('zhange', -1), '')

    def test_merge_order_inserts_new_name_before_anchor(self):
        known = ['无冠者', '飞廉之猩']
        merged, new = merge_order(known, ['无冠者', '无常凶鹭', '飞廉之猩'])
        self.assertEqual(merged, ['无冠者', '无常凶鹭', '飞廉之猩'])
        self.assertEqual(new, ['无常凶鹭'])


if __name__ == '__main__':
    unittest.main()
