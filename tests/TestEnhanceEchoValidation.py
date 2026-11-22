"""P1 声骸属性完整性校验：OCR 漏识/错位时安全停止，不误删好声骸。"""
import unittest

from Mon.feature.Box import Box
from src.task.EnhanceEchoTask import EnhanceEchoTask, parse_number


def prop(name, y, height=20):
    return Box(0, y, 100, height, name=name)


def val(name, y, height=20):
    return Box(100, y, 50, height, name=name)


class FakeTask:
    tr = staticmethod(str)
    fail_reason = ''
    config = {'有效词条': [], '双爆总计>=': 0, '首条双爆>=': 0, '有效词条>=': 0}
    log_info = staticmethod(lambda *a, **k: None)
    log_debug = staticmethod(lambda *a, **k: None)
    log_warning = staticmethod(lambda *a, **k: None)
    info_set = staticmethod(lambda *a, **k: None)


class TestParseNumber(unittest.TestCase):

    def test_valid_numbers(self):
        self.assertEqual(parse_number('12.5'), 12.5)
        self.assertEqual(parse_number('12.5%'), 12.5)
        self.assertEqual(parse_number('８％'.replace('８', '8')), 8.0)
        self.assertEqual(parse_number(' 3.5 % '), 3.5)

    def test_invalid_raises(self):
        for text in ('', 'abc', 'N/A', '-1', '1..2', '12px'):
            with self.assertRaises(ValueError):
                parse_number(text)


class TestCheckEchoStatsValidation(unittest.TestCase):

    def check(self, properties, values):
        task = FakeTask()
        return EnhanceEchoTask.check_echo_stats(task, properties, values)

    def test_mismatched_counts_raise(self):
        with self.assertRaises(ValueError):
            self.check([prop('暴击', 10)], [])

    def test_too_many_properties_raise(self):
        props = [prop('暴击', i * 25) for i in range(6)]
        vals = [val('10%', i * 25) for i in range(6)]
        with self.assertRaises(ValueError):
            self.check(props, vals)

    def test_unknown_property_raises(self):
        with self.assertRaises(ValueError):
            self.check([prop('未知属性', 10)], [val('10%', 10)])

    def test_misaligned_value_raises(self):
        # 属性与数值 y 相差过大，无法确定唯一配对
        with self.assertRaises(ValueError):
            self.check([prop('暴击', 10)], [val('10%', 200)])

    def test_bad_value_text_raises(self):
        with self.assertRaises(ValueError):
            self.check([prop('暴击', 10)], [val('abc', 10)])

    def test_valid_pair_passes(self):
        # 完整合法的输入应该走完流程，不抛异常
        props = [prop('暴击', 10), prop('暴击伤害', 35)]
        vals = [val('10%', 10), val('20%', 35)]
        result = self.check(props, vals)
        self.assertIsInstance(result, bool)


if __name__ == '__main__':
    unittest.main()
