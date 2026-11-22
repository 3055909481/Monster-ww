"""P4 周常完成判定：分数>=6000 才算完成，缺失不误判。"""
import unittest
from src.task.GardenTask import GardenTask


class FakeTask:
    pass


class TestIsGardenDone(unittest.TestCase):

    def done(self, *names):
        return GardenTask.is_garden_done(FakeTask(), names)

    def test_score_reaches_cap(self):
        self.assertTrue(self.done('本周积分 6000/6000'))
        self.assertTrue(self.done('积分 6200／6000'))

    def test_score_below_cap(self):
        self.assertFalse(self.done('本周积分 3000/6000'))
        self.assertFalse(self.done('积分 100/6000'))

    def test_missing_score_is_not_done(self):
        # 只有一个 6000（分数缺失）不能判定完成
        self.assertFalse(self.done('6000'))
        self.assertFalse(self.done('未知/6000'))

    def test_split_boxes_done(self):
        # OCR 把当前与上限拆成两个框
        self.assertTrue(self.done('6000', '6000'))

    def test_no_numbers(self):
        self.assertFalse(self.done(''))
        self.assertFalse(self.done('积分'))


if __name__ == '__main__':
    unittest.main()
