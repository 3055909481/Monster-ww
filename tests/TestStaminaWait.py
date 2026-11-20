"""等待体力恢复：缺口阈值 / 超时 / 次数0不等待 / 开关默认关。"""

import unittest
from unittest.mock import Mock, patch

from src.task.BaseWWTask import BaseWWTask
from src.task.RoutineTask import RoutineTask, STAMINA_GAP_KEY, WAIT_STAMINA_KEY


def _stamina_task(values):
    """values: list of current stamina readings across polls."""
    task = BaseWWTask.__new__(BaseWWTask)
    task.log_info = Mock()
    task.tr = lambda msg, **kw: msg
    it = iter(values)

    def read():
        current = next(it, values[-1])
        return current, 0, current  # (current, backup, total)

    task.get_stamina = Mock(side_effect=read)
    return task


class TestWaitForStaminaEnough(unittest.TestCase):
    def test_already_enough_no_wait(self):
        task = _stamina_task([60])
        task.wait_until = Mock()
        self.assertTrue(task.wait_for_stamina_enough(60, 10))
        task.wait_until.assert_not_called()

    def test_gap_within_limit_waits(self):
        task = _stamina_task([55, 60])
        task.wait_until = Mock(side_effect=lambda cond, **kw: cond() or True)
        self.assertTrue(task.wait_for_stamina_enough(60, 10))
        task.wait_until.assert_called_once()
        # 缺口 5 点 * 240s/点 = 1200s，低于 60 分钟硬上限
        self.assertEqual(task.wait_until.call_args.kwargs['time_out'], 1200)

    def test_gap_above_limit_skips(self):
        task = _stamina_task([30])
        task.wait_until = Mock()
        self.assertFalse(task.wait_for_stamina_enough(60, 10))
        task.wait_until.assert_not_called()

    def test_timeout_returns_false(self):
        task = _stamina_task([55, 55, 55])
        task.wait_until = Mock(return_value=False)
        self.assertFalse(task.wait_for_stamina_enough(60, 10))

    def test_hard_cap_on_large_gap(self):
        # 即使 gap_limit 放宽到 60，超时也被 3600s 封顶
        task = _stamina_task([10])
        gap_limit = 60
        self.assertTrue(gap_limit * BaseWWTask.STAMINA_RECOVER_SECONDS > BaseWWTask.STAMINA_WAIT_HARD_CAP)
        task.wait_until = Mock(side_effect=lambda cond, **kw: kw['time_out'] <= BaseWWTask.STAMINA_WAIT_HARD_CAP)
        task.wait_for_stamina_enough(70, gap_limit)
        self.assertLessEqual(task.wait_until.call_args.kwargs['time_out'], BaseWWTask.STAMINA_WAIT_HARD_CAP)

    def test_read_failure_skips(self):
        task = _stamina_task([-1])
        task.wait_until = Mock()
        self.assertFalse(task.wait_for_stamina_enough(60, 10))
        task.wait_until.assert_not_called()


class TestRoutineStaminaWaitConfig(unittest.TestCase):
    def _task(self, routine_config):
        task = RoutineTask.__new__(RoutineTask)
        task.config = routine_config
        return task

    def test_disabled_by_default(self):
        domain = Mock()
        self._task({})._configure_stamina_wait(domain, 4)
        self.assertFalse(domain._routine_wait_stamina)

    def test_enabled_for_finite_runs(self):
        domain = Mock()
        self._task({WAIT_STAMINA_KEY: True, STAMINA_GAP_KEY: 10})._configure_stamina_wait(domain, 4)
        self.assertTrue(domain._routine_wait_stamina)
        self.assertEqual(domain._routine_stamina_gap, 10)

    def test_never_wait_for_unlimited_runs(self):
        domain = Mock()
        self._task({WAIT_STAMINA_KEY: True})._configure_stamina_wait(domain, 0)
        self.assertFalse(domain._routine_wait_stamina)


if __name__ == '__main__':
    unittest.main()
