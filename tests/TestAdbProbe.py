import itertools
import unittest
from types import SimpleNamespace

import adb_probe


class FakeCapture:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def get_frame(self, busy_wait=0):
        self.calls.append(busy_wait)
        result = next(self.results)
        if isinstance(result, Exception):
            raise result
        return result


def frame(width=1920, height=1080):
    return SimpleNamespace(shape=(height, width, 3))


class TestAdbConfig(unittest.TestCase):

    def test_packages_cover_both_servers(self):
        self.assertEqual(
            ['com.kurogame.mingchao', 'com.kurogame.wutheringwaves'],
            adb_probe.adb_config()['packages'],
        )

    def test_defaults_to_generic_adb_capture(self):
        self.assertEqual('adb', adb_probe.adb_config()['capture_method'])


class TestSampleCapture(unittest.TestCase):

    def test_samples_frames_and_reports_fps(self):
        capture = FakeCapture([frame(), frame(), frame()])
        clock = itertools.count(0, 0.1).__next__

        result = adb_probe.sample_capture(capture, count=3, clock=clock)

        self.assertEqual(3, result['frames'])
        self.assertEqual((1920, 1080), result['resolution'])
        self.assertAlmostEqual(10.0, result['fps'], places=3)
        self.assertEqual([1.0, 1.0, 1.0], capture.calls)
        self.assertEqual([], result['errors'])

    def test_keeps_going_after_a_failed_frame(self):
        capture = FakeCapture([None, RuntimeError('adb offline'), frame(2400, 1080), frame()])
        clock = itertools.count(0, 0.25).__next__

        result = adb_probe.sample_capture(capture, count=4, clock=clock)

        self.assertEqual(2, result['frames'])
        self.assertEqual((1920, 1080), result['resolution'])
        self.assertEqual(2, len(result['errors']))
        self.assertIn('no frame returned', result['errors'][0])
        self.assertIn('RuntimeError: adb offline', result['errors'][1])

    def test_zero_frames_has_no_division_error(self):
        result = adb_probe.sample_capture(FakeCapture([None, None]), count=2, clock=lambda: 0.0)

        self.assertEqual(0, result['frames'])
        self.assertEqual(0.0, result['fps'])
        self.assertEqual(0.0, result['avg_ms'])
        self.assertIsNone(result['resolution'])

    def test_rejects_non_positive_count(self):
        with self.assertRaises(ValueError):
            adb_probe.sample_capture(FakeCapture([]), count=0)


class TestFormatReport(unittest.TestCase):

    def test_includes_device_capture_and_tap_state(self):
        sample = {'frames': 2, 'requested': 2, 'resolution': (1920, 1080),
                  'avg_ms': 300.0, 'fps': 3.3, 'errors': []}
        report = adb_probe.format_report(
            {'nick': 'MuMuPlayer-12', 'device': 'adb'}, 'Nemu Ipc Capture', sample, 7, True)

        self.assertIn('MuMuPlayer-12', report)
        self.assertIn('Nemu Ipc Capture', report)
        self.assertIn('7 个文本区域', report)
        self.assertIn('单点点击: 已发送', report)
        self.assertIn('不验证完整鸣潮任务流程', report)

    def test_missing_device_is_safe_and_shows_errors(self):
        sample = {'frames': 0, 'requested': 3, 'resolution': None,
                  'avg_ms': 0.0, 'fps': 0.0, 'errors': ['no frame returned']}
        report = adb_probe.format_report(None, 'ADB command line Capture', sample, None, False)

        self.assertIn('未知 (adb)', report)
        self.assertIn('OCR: 未执行', report)
        self.assertIn('单点点击: 未发送/失败', report)
        self.assertIn('截图异常: no frame returned', report)


if __name__ == '__main__':
    unittest.main()
