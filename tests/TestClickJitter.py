"""点击坐标微抖动测试：纯逻辑，不依赖 Qt/真实点击。"""
import unittest
from unittest.mock import Mock, patch as mock_patch

from src import click_jitter as cj


def _executor(enabled=True, radius=2):
    ex = Mock()
    cfg = {cj.KEY_ENABLED: enabled, cj.KEY_RADIUS: radius}
    gc = Mock()
    gc.get_config.return_value = cfg
    ex.global_config = gc
    return ex


class TestJitterRadius(unittest.TestCase):

    def test_disabled_returns_zero(self):
        self.assertEqual(cj._jitter_radius(_executor(enabled=False)), 0)

    def test_no_config_returns_zero(self):
        ex = Mock()
        ex.global_config = None
        self.assertEqual(cj._jitter_radius(ex), 0)

    def test_config_exception_returns_zero(self):
        ex = _executor()
        ex.global_config.get_config.side_effect = Exception('boom')
        self.assertEqual(cj._jitter_radius(ex), 0)

    def test_radius_normal(self):
        self.assertEqual(cj._jitter_radius(_executor(radius=3)), 3)

    def test_radius_clamped(self):
        self.assertEqual(cj._jitter_radius(_executor(radius=999)), cj._MAX_RADIUS)
        self.assertEqual(cj._jitter_radius(_executor(radius=-5)), 0)

    def test_negative_from_string(self):
        self.assertEqual(cj._jitter_radius(_executor(radius='abc')), 0)


class TestPatchedClick(unittest.TestCase):

    def setUp(self):
        self.orig = cj._ORIGINAL_CLICK
        self.calls = []
        cj._ORIGINAL_CLICK = lambda self_, x, y, *a, **k: self.calls.append((x, y)) or True

    def tearDown(self):
        cj._ORIGINAL_CLICK = self.orig

    def test_absolute_click_stays_near_center(self):
        task = Mock()
        task.executor = _executor(radius=3)
        for _ in range(200):
            cj._patched_click(task, 500, 400)
        xs = [x for x, _ in self.calls]
        ys = [y for _, y in self.calls]
        self.assertLessEqual(max(xs), 503)
        self.assertGreaterEqual(min(xs), 497)
        self.assertLessEqual(max(ys), 403)
        self.assertGreaterEqual(min(ys), 397)
        # 震荡覆盖两侧，不是系统性偏移
        self.assertLess(sum(xs) / len(xs), 501)
        self.assertGreater(sum(xs) / len(xs), 499)

    def test_disabled_click_exact(self):
        task = Mock()
        task.executor = _executor(enabled=False)
        cj._patched_click(task, 500, 400)
        self.assertEqual(self.calls, [(500, 400)])

    def test_relative_coords_not_jittered(self):
        # 0<x<1 是相对坐标代理情形，不能加像素偏移
        task = Mock()
        task.executor = _executor(radius=8)
        cj._patched_click(task, 0.5, 0.3)
        self.assertEqual(self.calls, [(0.5, 0.3)])

    def test_args_kwargs_forwarded(self):
        task = Mock()
        task.executor = _executor(enabled=False)
        cj._patched_click(task, 1, 2, 'pos', name='foo', key='right')
        self.assertEqual(self.calls, [(1, 2)])
        # 确认原方法收到额外参数（通过捕获重新验证）
        seen = {}
        def orig(self_, x, y, *a, **k):
            seen['args'] = a
            seen['kwargs'] = k
            return True
        cj._ORIGINAL_CLICK = orig
        cj._patched_click(task, 1, 2, 'pos', name='foo')
        self.assertEqual(seen['args'], ('pos',))
        self.assertEqual(seen['kwargs'], {'name': 'foo'})


class TestInstall(unittest.TestCase):

    def test_install_is_idempotent(self):
        from Mon.task.task import BaseTask
        real_click = BaseTask.click
        real_orig = cj._ORIGINAL_CLICK
        real_installed = cj._INSTALLED
        try:
            cj._INSTALLED = False
            cj._ORIGINAL_CLICK = None
            self.assertTrue(cj.install())
            self.assertIs(BaseTask.click, cj._patched_click)
            self.assertFalse(cj.install())
        finally:
            BaseTask.click = real_click
            cj._ORIGINAL_CLICK = real_orig
            cj._INSTALLED = real_installed


if __name__ == '__main__':
    unittest.main()
