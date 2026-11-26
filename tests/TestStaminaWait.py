"""等待体力恢复：缺口阈值 / 超时 / 次数0等一次即结束 / 开关默认关。"""

import unittest
from unittest.mock import Mock, patch

from src.task.BaseWWTask import BaseWWTask
from src.task.DomainTask import DomainTask
from src.task.RoutineTask import RoutineTask, STAMINA_GAP_KEY, WAIT_STAMINA_KEY
from src.task.TacetTask import TacetTask


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
        # 缺口 5 点 * 360s/点 + 300s 余量 = 2100s（抵消 OCR 轮询开销）
        self.assertEqual(task.wait_until.call_args.kwargs['time_out'], 2100)

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

    def test_read_failure_retries_then_skips(self):
        # OCR 偶发失败要重试，不是一次读不到就放弃
        task = _stamina_task([-1, -1, 60])
        task.sleep = Mock()
        task.wait_until = Mock()
        self.assertTrue(task.wait_for_stamina_enough(60, 10))
        self.assertEqual(task.sleep.call_count, 2)
        task.wait_until.assert_not_called()  # 读到的 60 已够，无需等待

    def test_read_failure_all_retries_skips(self):
        task = _stamina_task([-1])
        task.sleep = Mock()
        task.wait_until = Mock()
        self.assertFalse(task.wait_for_stamina_enough(60, 10))
        self.assertEqual(task.sleep.call_count, 2)
        task.wait_until.assert_not_called()


class TestUseStaminaContinuation(unittest.TestCase):
    """can_continue 只看扣减前的自然体力，不看备用结晶/本地模拟扣减。"""

    def _task(self, values):
        task = BaseWWTask.__new__(BaseWWTask)
        task.sleep = Mock()
        it = iter(values)

        def read():
            return next(it, values[-1])

        task.get_stamina = Mock(side_effect=read)
        task.click_dialog_left_button = Mock(return_value=(100, 100))
        task.click_dialog_right_button = Mock(return_value=(100, 100))
        task.wait_feature = Mock(return_value=False)
        return task

    def test_119_single_then_wait(self):
        # 119 不够双倍 → 单倍刷 60 → 剩 59 < 60 → 返回 False 让外层等恢复补刷
        task = self._task([(119, 0, 119)])
        can_continue, used = task.use_stamina(once=60, must_use=0)
        self.assertEqual(used, 60)  # 单倍，不是双倍
        self.assertFalse(can_continue)

    def test_double_deduction_stops_when_drained(self):
        # 120 够双倍 → 扣 120 剩 0 → False（外层缺口大，正常结束）
        task = self._task([(120, 48, 168)])
        can_continue, used = task.use_stamina(once=60, must_use=0)
        self.assertEqual(used, 120)
        self.assertFalse(can_continue)

    def test_stops_only_when_current_below_once(self):
        task = self._task([(50, 200, 250)])  # 备用很多，但自然体力 50 < 60
        can_continue, _ = task.use_stamina(once=60, must_use=0)
        self.assertFalse(can_continue)

    def test_daily_must_use_completion_stops(self):
        task = self._task([(120, 48, 168)])
        can_continue, _ = task.use_stamina(once=60, must_use=120)
        self.assertFalse(can_continue)  # 日常额度扣完


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
        self.assertFalse(domain._routine_stamina_single_run)

    def test_enabled_for_unlimited_runs_with_single_run_flag(self):
        domain = Mock()
        self._task({WAIT_STAMINA_KEY: True})._configure_stamina_wait(domain, 0)
        self.assertTrue(domain._routine_wait_stamina)
        self.assertTrue(domain._routine_stamina_single_run)

    def test_unlimited_runs_disabled_when_switch_off(self):
        domain = Mock()
        self._task({})._configure_stamina_wait(domain, 0)
        self.assertFalse(domain._routine_wait_stamina)
        self.assertFalse(domain._routine_stamina_single_run)


class TestSingleThenWaitReplay(unittest.TestCase):
    """119 体力：不够双倍→单倍刷→剩 59 缺口小→等恢复→补刷一次→结束。"""

    def test_119_waits_one_point_then_replays_once(self):
        task = TacetTask.__new__(TacetTask)
        task.stamina_once = 60
        task._routine_wait_stamina = True
        task._routine_stamina_gap = 10
        task._routine_stamina_single_run = True
        task.config = {'Which Tacet Suppression to Farm': 1}
        task.log_info = Mock()
        task.tr = lambda msg, **kw: msg
        task.info_incr = Mock()
        task.realm_entry_at_heal_point = False
        task.sleep = Mock()
        task.openF2Book = Mock()
        task.open_boss_book = Mock()
        task.teleport_to_tacet = Mock()
        task.click_team_challenge = Mock()
        task.wait_in_team_and_world = Mock()
        task.combat_once = Mock()
        task.walk_to_treasure = Mock()
        task.pick_f = Mock()
        task.has_claim_stamina = Mock(return_value=True)
        task.click_relative = Mock()
        task.click_dialog_left_button = Mock()
        task.click_dialog_right_button = Mock()
        task.wait_feature = Mock(return_value=False)
        task.back = Mock()

        # 每次 get_stamina：外层进本读 119 → 领奖 use_stamina 读 119(单倍剩59)
        # → 等待后外层读 60 → 补刷领奖读 60(单倍剩 0)
        readings = iter([(119, 0, 119), (119, 0, 119), (60, 0, 60), (60, 0, 60)])
        task.get_stamina = Mock(side_effect=lambda: next(readings, (0, 0, 0)))
        waited = []
        task.wait_for_stamina_enough = Mock(side_effect=lambda need, gap: (
            waited.append((need, gap)) or True))

        task.farm_tacet(max_runs=0)

        # 打了两轮（首轮 + 补刷一轮），等待只触发一次，缺口阈值 10
        self.assertEqual(task.combat_once.call_count, 2)
        self.assertEqual(waited, [(60, 10)])
        task.wait_for_stamina_enough.assert_called_once_with(60, 10)


class TestUnlimitedRunCap(unittest.TestCase):
    """次数为 0 时耗尽体力：等待恢复后补刷一轮，然后结束。"""

    def test_domain_waits_and_caps_to_one_run(self):
        """进入时体力不足：等恢复后 max_runs=1，只补刷一轮。"""
        task = DomainTask.__new__(DomainTask)
        task.stamina_once = 60
        task._routine_wait_stamina = True
        task._routine_stamina_gap = 10
        task._routine_stamina_single_run = True
        task.log_info = Mock()
        task.tr = lambda msg, **kw: msg
        task.sleep = Mock()
        task.realm_entry_at_heal_point = False
        # 55 不足一次 -> 等待成功后恢复为 60 -> 进入副本补刷
        readings = iter([(55, 0, 55), (60, 0, 60)])
        task.open_F2_book_and_get_stamina = Mock(
            side_effect=lambda: next(readings, (60, 0, 60)))
        task.wait_for_stamina_enough = Mock(return_value=True)
        seen = {}

        def farm_in_domain(must_use=0, max_runs=0):
            seen['max_runs'] = max_runs
            return True, must_use

        task.farm_in_domain = Mock(side_effect=farm_in_domain)
        task.farm_domain_with_recovery_loop(0, Mock(), max_runs=0)
        self.assertEqual(seen['max_runs'], 1)
        task.wait_for_stamina_enough.assert_called_once_with(60, 10)

    def test_domain_retries_when_stamina_drops_after_runs(self):
        """刷几轮后体力不足：回顶层等待，再补刷一轮后结束。"""
        task = DomainTask.__new__(DomainTask)
        task.stamina_once = 60
        task._routine_wait_stamina = True
        task._routine_stamina_gap = 10
        task._routine_stamina_single_run = True
        task.log_info = Mock()
        task.tr = lambda msg, **kw: msg
        task.sleep = Mock()
        task.realm_entry_at_heal_point = False
        # 第一轮读体力充足(110) -> 刷完掉到 55 -> 等待恢复到 60 -> 补刷
        readings = iter([(110, 0, 110), (55, 0, 55), (60, 0, 60)])
        task.open_F2_book_and_get_stamina = Mock(
            side_effect=lambda: next(readings, (60, 0, 60)))
        task.wait_for_stamina_enough = Mock(return_value=True)
        farm_calls = []

        def farm_in_domain(must_use=0, max_runs=0):
            farm_calls.append(max_runs)
            return True, must_use

        task.farm_in_domain = Mock(side_effect=farm_in_domain)
        task.farm_domain_with_recovery_loop(0, Mock(), max_runs=0)
        # 第一次不限次数刷, 补刷时限制为 1 轮
        self.assertEqual(farm_calls, [0, 1])
        task.wait_for_stamina_enough.assert_called_once_with(60, 10)

    def test_tacet_waits_and_caps_to_one_run(self):
        task = TacetTask.__new__(TacetTask)
        task.stamina_once = 60
        task._routine_wait_stamina = True
        task._routine_stamina_gap = 10
        task._routine_stamina_single_run = True
        task.config = {'Which Tacet Suppression to Farm': 1}
        task.log_info = Mock()
        task.tr = lambda msg, **kw: msg
        task.info_incr = Mock()
        task.realm_entry_at_heal_point = False
        task.sleep = Mock()
        task.openF2Book = Mock()
        readings = iter([(55, 0, 55), (60, 0, 60)])
        task.get_stamina = Mock(side_effect=lambda: next(readings, (60, 0, 60)))
        task.wait_for_stamina_enough = Mock(return_value=True)
        task.open_boss_book = Mock()
        task.teleport_to_tacet = Mock()
        task.click_team_challenge = Mock()
        task.wait_in_team_and_world = Mock()
        task.combat_once = Mock()
        task.walk_to_treasure = Mock()
        task.pick_f = Mock()
        task.has_claim_stamina = Mock(return_value=True)
        task.use_stamina = Mock(return_value=(True, 60))
        task.click_relative = Mock()

        task.farm_tacet(max_runs=0)
        self.assertEqual(task.combat_once.call_count, 1)
        task.wait_for_stamina_enough.assert_called_once_with(60, 10)

    def test_disabled_wait_stops_without_retry(self):
        """未开启等待时，次数=0 刷到体力不足直接结束，不回顶层补刷。"""
        task = DomainTask.__new__(DomainTask)
        task.stamina_once = 60
        task._routine_wait_stamina = False
        task.log_info = Mock()
        task.tr = lambda msg, **kw: msg
        task.sleep = Mock()
        task.realm_entry_at_heal_point = False
        task.open_F2_book_and_get_stamina = Mock(return_value=(110, 0, 110))
        task.wait_for_stamina_enough = Mock()
        task.farm_in_domain = Mock(return_value=(True, 0))
        task.farm_domain_with_recovery_loop(0, Mock(), max_runs=0)
        task.farm_in_domain.assert_called_once()
        task.wait_for_stamina_enough.assert_not_called()


if __name__ == '__main__':
    unittest.main()
