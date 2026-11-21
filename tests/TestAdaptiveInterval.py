"""自适应触发间隔测试：空闲降占用、战斗零影响、防御性降级。"""
import unittest
from unittest.mock import Mock, patch as mock_patch

from src import adaptive_interval as ai


def _executor(cfg=None, in_combat_scene=None, trigger_tasks=None, onetime_tasks=None,
              basic_interval=1):
    """构造一个最小 executor 替身。cfg=None 表示没有 global_config。"""
    ex = Mock()
    ex.global_config = None
    if cfg is not None:
        gc = Mock()
        gc.get_config.return_value = cfg
        ex.global_config = gc
    ex.scene = Mock()
    ex.scene.in_combat.return_value = in_combat_scene
    ex.trigger_tasks = trigger_tasks if trigger_tasks is not None else []
    ex.onetime_tasks = onetime_tasks if onetime_tasks is not None else []
    ex.basic_options = {'Trigger Interval': basic_interval}
    ex.sleep = Mock()
    return ex


def _default_cfg(**overrides):
    cfg = dict(ai._FALLBACK)
    cfg.update(overrides)
    return cfg


def _combat_task(value=True):
    t = Mock()
    t._in_combat = value
    return t


class TestInCombat(unittest.TestCase):

    def test_scene_true_wins(self):
        ex = _executor(in_combat_scene=True)
        self.assertTrue(ai._in_combat(ex))

    def test_trigger_task_in_combat(self):
        ex = _executor(in_combat_scene=None, trigger_tasks=[_combat_task(True)])
        self.assertTrue(ai._in_combat(ex))

    def test_onetime_task_in_combat(self):
        ex = _executor(in_combat_scene=None, onetime_tasks=[_combat_task(True)])
        self.assertTrue(ai._in_combat(ex))

    def test_scene_reset_to_none_but_task_still_combat(self):
        """scene.reset() 把 _in_combat 清成 None，但任务实例仍 True：不得误判脱战。"""
        ex = _executor(in_combat_scene=None, trigger_tasks=[_combat_task(True)])
        self.assertTrue(ai._in_combat(ex))

    def test_idle_when_all_false(self):
        ex = _executor(in_combat_scene=False,
                       trigger_tasks=[_combat_task(False)],
                       onetime_tasks=[_combat_task(False)])
        self.assertFalse(ai._in_combat(ex))

    def test_scene_none_no_tasks_is_idle(self):
        ex = _executor(in_combat_scene=None)
        self.assertFalse(ai._in_combat(ex))

    def test_scene_exception_does_not_crash(self):
        ex = _executor()
        ex.scene.in_combat.side_effect = Exception('boom')
        self.assertFalse(ai._in_combat(ex))


class TestResolveInterval(unittest.TestCase):

    def test_combat_uses_combat_range(self):
        cfg = _default_cfg(**{
            ai.KEY_COMBAT_MIN: 1, ai.KEY_COMBAT_MAX: 1,
            ai.KEY_IDLE_MIN: 25, ai.KEY_IDLE_MAX: 35,
        })
        ex = _executor(cfg=cfg, in_combat_scene=True)
        enabled, ms = ai._resolve_interval_ms(ex)
        self.assertTrue(enabled)
        self.assertEqual(ms, 1.0)  # 上下限相同 = 固定值

    def test_idle_uses_idle_range(self):
        cfg = _default_cfg(**{
            ai.KEY_COMBAT_MIN: 1, ai.KEY_COMBAT_MAX: 1,
            ai.KEY_IDLE_MIN: 30, ai.KEY_IDLE_MAX: 30,
        })
        ex = _executor(cfg=cfg, in_combat_scene=False)
        enabled, ms = ai._resolve_interval_ms(ex)
        self.assertTrue(enabled)
        self.assertEqual(ms, 30.0)

    def test_idle_range_produces_varied_values(self):
        cfg = _default_cfg(**{ai.KEY_IDLE_MIN: 25, ai.KEY_IDLE_MAX: 35})
        ex = _executor(cfg=cfg, in_combat_scene=False)
        seen = set()
        for _ in range(200):
            _, ms = ai._resolve_interval_ms(ex)
            seen.add(round(ms, 3))
            self.assertGreaterEqual(ms, 25.0)
            self.assertLessEqual(ms, 35.0)
        self.assertGreater(len(seen), 1, 'range should produce varied values')

    def test_reversed_range_is_corrected(self):
        cfg = _default_cfg(**{ai.KEY_IDLE_MIN: 35, ai.KEY_IDLE_MAX: 25})
        ex = _executor(cfg=cfg, in_combat_scene=False)
        for _ in range(50):
            _, ms = ai._resolve_interval_ms(ex)
            self.assertGreaterEqual(ms, 25.0)
            self.assertLessEqual(ms, 35.0)

    def test_negative_bounds_clamped_to_zero(self):
        cfg = _default_cfg(**{ai.KEY_IDLE_MIN: -10, ai.KEY_IDLE_MAX: -5})
        ex = _executor(cfg=cfg, in_combat_scene=False)
        _, ms = ai._resolve_interval_ms(ex)
        self.assertEqual(ms, 0.0)

    def test_disabled_returns_false(self):
        cfg = _default_cfg(**{ai.KEY_ENABLED: False})
        ex = _executor(cfg=cfg)
        enabled, ms = ai._resolve_interval_ms(ex)
        self.assertFalse(enabled)
        self.assertEqual(ms, 0)

    def test_no_config_degrades_to_framework(self):
        ex = _executor(cfg=None, in_combat_scene=False)
        enabled, ms = ai._resolve_interval_ms(ex)
        self.assertFalse(enabled)
        self.assertEqual(ms, 0)

    def test_bad_value_falls_back(self):
        cfg = _default_cfg(**{ai.KEY_IDLE_MIN: 'oops', ai.KEY_IDLE_MAX: 'oops'})
        ex = _executor(cfg=cfg, in_combat_scene=False)
        _, ms = ai._resolve_interval_ms(ex)
        self.assertGreaterEqual(ms, float(ai._FALLBACK[ai.KEY_IDLE_MIN]))
        self.assertLessEqual(ms, float(ai._FALLBACK[ai.KEY_IDLE_MAX]))

    def test_config_read_exception_degrades(self):
        ex = _executor(cfg={})
        ex.global_config.get_config.side_effect = Exception('boom')
        enabled, ms = ai._resolve_interval_ms(ex)
        self.assertFalse(enabled)
        self.assertEqual(ms, 0)


class TestPatchedTriggerSleep(unittest.TestCase):

    def test_idle_sleeps_idle_interval(self):
        cfg = _default_cfg(**{ai.KEY_IDLE_MIN: 30, ai.KEY_IDLE_MAX: 30})
        ex = _executor(cfg=cfg, in_combat_scene=False)
        ai._patched_trigger_sleep(ex)
        ex.sleep.assert_called_once_with(0.03)

    def test_combat_sleeps_combat_interval(self):
        cfg = _default_cfg(**{ai.KEY_COMBAT_MIN: 1, ai.KEY_COMBAT_MAX: 1})
        ex = _executor(cfg=cfg, in_combat_scene=True)
        ai._patched_trigger_sleep(ex)
        ex.sleep.assert_called_once_with(0.001)

    def test_disabled_falls_back_to_framework_behavior(self):
        cfg = _default_cfg(**{ai.KEY_ENABLED: False})
        ex = _executor(cfg=cfg, basic_interval=7)
        ai._patched_trigger_sleep(ex)
        ex.sleep.assert_called_once_with(0.007)

    def test_no_config_falls_back_to_framework_behavior(self):
        ex = _executor(cfg=None, basic_interval=5)
        ai._patched_trigger_sleep(ex)
        ex.sleep.assert_called_once_with(0.005)

    def test_zero_interval_skips_sleep(self):
        cfg = _default_cfg(**{ai.KEY_IDLE_MIN: 0, ai.KEY_IDLE_MAX: 0})
        ex = _executor(cfg=cfg, in_combat_scene=False)
        ai._patched_trigger_sleep(ex)
        ex.sleep.assert_not_called()

    def test_resolve_exception_degrades_to_framework(self):
        ex = _executor(cfg=None, basic_interval=9)
        with mock_patch.object(ai, '_resolve_interval_ms', side_effect=Exception('boom')):
            ai._patched_trigger_sleep(ex)
        ex.sleep.assert_called_once_with(0.009)


class TestInstall(unittest.TestCase):

    def test_install_is_idempotent(self):
        fake_cls = Mock()
        with mock_patch.object(ai, '_INSTALLED', False), \
             mock_patch('Mon.task.TaskExecutor.TaskExecutor', fake_cls, create=True):
            self.assertTrue(ai.install())
            self.assertEqual(fake_cls.trigger_sleep, ai._patched_trigger_sleep)
            self.assertFalse(ai.install())

    def test_install_failure_returns_false(self):
        with mock_patch.object(ai, '_INSTALLED', False), \
             mock_patch('builtins.__import__', side_effect=Exception('boom')):
            self.assertFalse(ai.install())

    def test_install_failure_leaves_flag_unset(self):
        with mock_patch.object(ai, '_INSTALLED', False), \
             mock_patch('builtins.__import__', side_effect=Exception('boom')):
            ai.install()
            self.assertFalse(ai._INSTALLED)


if __name__ == '__main__':
    unittest.main()
