import unittest

import cv2

from config import config
from Mon.test.TaskTestCase import TaskTestCase
from src.char.BaseChar import Elements
from src.task.AutoCombatTask import AutoCombatTask

config['debug'] = True


class TestCon(TaskTestCase):
    task_class = AutoCombatTask
    config = config

    def test_con_full(self):
        self.task.do_reset_to_false()
        self.set_image('tests/images/con_full.png')
        self.task.load_chars()
        con_full = self.task.get_current_char().is_con_full()
        self.assertTrue(con_full)

    def test_con_full2(self):
        self.task.do_reset_to_false()
        self.set_image('tests/images/in_combat.png')
        self.task.load_chars()
        con_full = self.task.get_current_char().is_con_full()
        self.assertTrue(con_full)

    def test_con_full3(self):
        self.task.do_reset_to_false()
        self.set_image('tests/images/all_cd_1080p.png')
        self.task.load_chars()
        con = self.task.get_current_char().get_current_con()
        self.task.log_info(f'{self.task.get_current_char()} con = {con}')
        con_full = self.task.get_current_char().is_con_full()
        self.assertTrue(con_full)

    def test_con_full4(self):
        self.task.do_reset_to_false()
        self.set_image('tests/images/absorb.png')
        self.task.load_chars()
        con_full = self.task.get_current_char().is_con_full()
        self.assertFalse(con_full)

    def test_con_full5(self):
        self.task.do_reset_to_false()
        self.set_image('tests/images/angle_130.png')
        self.task.load_chars()
        con = self.task.get_current_char().get_current_con()
        self.task.log_info(f'{self.task.get_current_char()} con = {con}')
        con_full = self.task.get_current_char().is_con_full()
        self.assertFalse(con_full)

    def test_con_full6(self):
        self.task.do_reset_to_false()
        self.set_image('tests/images/con_full2.png')
        self.task.load_chars()
        con_full = self.task.get_current_char().is_con_full()
        self.assertTrue(con_full)

    def test_con_arc_from_live_1080p_crops(self):
        # 上游 3.0.15 的实测协奏环截图：按环周角占比识别，旧面积法会把 85% 读成满、
        # 短弧读成 0。这些样本直接验证移植过来的 measure_con_arc。
        cases = [
            ('con_arc_spectro_full', Elements.SPECTRO, 1, 1),
            ('con_arc_spectro_96', Elements.SPECTRO, 0.9, 0.99),  # 3 点方向小缺口：不满
            ('con_arc_spectro_85', Elements.SPECTRO, 0.78, 0.9),  # 旧算法读成满
            ('con_arc_wind_80', Elements.WIND, 0.72, 0.88),  # 旧算法读成 0.99
            ('con_arc_wind_25', Elements.WIND, 0.18, 0.32),
            ('con_arc_wind_18', Elements.WIND, 0.1, 0.25),  # 短弧旧算法读成 0
            ('con_arc_wind_empty', Elements.WIND, 0, 0),
        ]
        for name, element, low, high in cases:
            with self.subTest(name):
                image = cv2.imread(f'tests/images/{name}.png')
                con = self.task.measure_con_arc(image, element)
                self.assertGreaterEqual(con, low)
                self.assertLessEqual(con, high)


if __name__ == '__main__':
    unittest.main()
