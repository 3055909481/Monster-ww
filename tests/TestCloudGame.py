"""云游戏增强（来自 xjtu-ww fork 7899772/8015242）：锁定图标金圈识别、
血条 grace、断线弹窗检测。用 mock 隔离图像依赖，只验证判定逻辑。"""

import time
import unittest
from unittest.mock import Mock, patch

import numpy as np

from src.combat.CombatCheck import CombatCheck
from src.task.BaseWWTask import BaseWWTask


class TestCloudTargetDetection(unittest.TestCase):
    def setUp(self):
        self.task = CombatCheck.__new__(CombatCheck)
        self.executor = Mock()
        self.executor.frame = None
        self.task._executor = self.executor
        self.task._cloud_health_lost_at = 0
        self.task.box_of_screen = Mock(return_value=Mock())

    def test_gold_ratio_returns_none_without_icon_or_frame(self):
        self.task.find_best_match_in_box = Mock(return_value=None)
        self.assertIsNone(self.task.cloud_lock_icon_gold_ratio())
        self.task.find_best_match_in_box = Mock(return_value=Mock(x=0, y=0, width=10, height=10))
        self.executor.frame = None
        self.assertIsNone(self.task.cloud_lock_icon_gold_ratio())

    def test_gold_ratio_counts_gold_pixels(self):
        # 构造一块纯金色(BGR)区域: HSV H~25 S>90 V>150
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        frame[20:30, 20:30] = (30, 200, 200)  # BGR -> 金色
        self.executor.frame = frame
        self.task.find_best_match_in_box = Mock(return_value=Mock(x=20, y=20, width=10, height=10))
        self.assertAlmostEqual(1.0, self.task.cloud_lock_icon_gold_ratio(), places=2)

    def test_cloud_has_target_threshold(self):
        self.task.cloud_lock_icon_gold_ratio = Mock(return_value=0.03)
        self.assertTrue(self.task.cloud_has_target())
        self.task.cloud_lock_icon_gold_ratio = Mock(return_value=0.005)
        self.assertFalse(self.task.cloud_has_target())
        self.task.cloud_lock_icon_gold_ratio = Mock(return_value=None)
        self.assertFalse(self.task.cloud_has_target())

    def test_health_grace_resets_on_visible_health(self):
        self.assertTrue(self.task.cloud_health_bar_grace(True))
        self.assertEqual(0, self.task._cloud_health_lost_at)

    def test_health_grace_within_window(self):
        self.assertTrue(self.task.cloud_health_bar_grace(False))
        self.assertTrue(self.task.cloud_health_bar_grace(False))

    def test_health_grace_expires(self):
        self.task.cloud_health_bar_grace(False)
        self.task._cloud_health_lost_at = time.time() - 10
        self.assertFalse(self.task.cloud_health_bar_grace(False))

    def test_target_visible_uses_both_signals_in_cloud(self):
        self.task.is_browser = Mock(return_value=True)
        self.task.has_target = Mock(return_value=False)
        self.task.check_health_bar = Mock(return_value=True)
        self.assertTrue(self.task.target_visible())
        self.task.has_target.return_value = False
        self.task.check_health_bar.return_value = False
        self.assertFalse(self.task.target_visible())

    def test_target_visible_pc_only_uses_has_target(self):
        self.task.is_browser = Mock(return_value=False)
        self.task.has_target = Mock(return_value=True)
        self.task.check_health_bar = Mock(return_value=False)
        self.assertTrue(self.task.target_visible())


class TestCloudDisconnect(unittest.TestCase):
    def setUp(self):
        self.task = BaseWWTask.__new__(BaseWWTask)

    def test_skipped_outside_cloud(self):
        self.task.is_browser = Mock(return_value=False)
        self.assertIsNone(self.task.find_cloud_disconnect())

    def test_detects_disconnect_dialog(self):
        self.task.is_browser = Mock(return_value=True)
        self.task.box_of_screen = Mock(return_value=Mock())
        dialog = Mock()
        exit_button = Mock()
        self.task.ocr = Mock(return_value=[dialog, exit_button])
        self.task.find_boxes = Mock(side_effect=lambda texts, match: [dialog] if '云服务错误' in match.pattern else [exit_button])
        with patch.object(type(self.task), 'debug', False):
            self.assertEqual([exit_button], self.task.find_cloud_disconnect())

    def test_ignores_launcher_without_error_text(self):
        self.task.is_browser = Mock(return_value=True)
        self.task.box_of_screen = Mock(return_value=Mock())
        self.task.ocr = Mock(return_value=[Mock()])
        self.task.find_boxes = Mock(return_value=[])
        with patch.object(type(self.task), 'debug', False):
            self.assertIsNone(self.task.find_cloud_disconnect())


if __name__ == '__main__':
    unittest.main()
