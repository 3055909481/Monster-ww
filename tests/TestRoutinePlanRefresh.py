"""删除/新建计划后，外层自定义页下拉框能刷新：回调选择逻辑。

任务设置页的「配置流水线」按钮会把 clicked 的 bool 传给 configure_plans，
此时 on_saved 不可调用，必须主动刷新 RoutineTab；从自定义页「编辑步骤」
打开时则走调用方传入的 on_saved。
"""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.task.RoutineTask import RoutineTask
from src.task.TacetTask import TacetTask


class TestConfigurePlansRefresh(unittest.TestCase):
    def _task(self):
        task = RoutineTask.__new__(RoutineTask)
        task.tr = lambda msg, **kw: msg
        return task

    def _run_configure(self, on_saved, dialog_saved, refresh):
        task = self._task()
        dialog = Mock()
        dialog.exec.return_value = dialog_saved
        with patch('src.gui.RoutinePlanDialog.RoutinePlanDialog', return_value=dialog), \
             patch.object(RoutineTask, '_refresh_routine_tabs', refresh):
            task.configure_plans(on_saved=on_saved)
        return task

    def test_button_click_bool_falls_back_to_tab_refresh(self):
        """clicked 的 bool(False) 走主动刷新，不当作回调。"""
        on_saved = False
        refresh = Mock()
        self._run_configure(on_saved, True, refresh)
        refresh.assert_called_once()

    def test_callable_on_saved_is_used_without_tab_scan(self):
        """自定义页传入可调用回调时用它，不再扫描刷新全部 tab。"""
        on_saved = Mock()
        refresh = Mock()
        self._run_configure(on_saved, True, refresh)
        on_saved.assert_called_once()
        refresh.assert_not_called()

    def test_cancel_does_not_refresh(self):
        """取消弹窗（未保存）时什么都不刷新。"""
        on_saved = Mock()
        refresh = Mock()
        self._run_configure(on_saved, False, refresh)
        on_saved.assert_not_called()
        refresh.assert_not_called()


class TestAddStepAfterSelection(unittest.TestCase):
    """添加步骤时插入到选中条目之后, 而不是一律追加到末尾。"""

    def _add_step_source(self, path):
        source = Path(path).read_text(encoding='utf-8')
        start = source.index('    def _add_step(self):')
        end = source.index('    def _delete_step(self):', start)
        return source[start:end]

    def test_routine_plan_inserts_after_current_row(self):
        body = self._add_step_source('src/gui/RoutinePlanDialog.py')
        self.assertIn('current_row = self.step_list.currentRow()', body)
        self.assertIn('self.step_list.insertItem(current_row + 1, widget_item)', body)
        self.assertIn('self.step_list.addItem(widget_item)', body)  # 无选中时兜底

    def test_custom_flow_inserts_after_current_row(self):
        body = self._add_step_source('src/gui/CustomFlowDialog.py')
        self.assertIn('current_row = self.step_list.currentRow()', body)
        self.assertIn('self.step_list.insertItem(current_row + 1, widget_item)', body)


class TestThemedInputDialog(unittest.TestCase):
    """新建任务流/计划的输入弹窗必须跟随深色主题。"""

    def test_both_dialogs_use_themed_input(self):
        for path, title in (
            ('src/gui/CustomFlowDialog.py', '新建任务流'),
            ('src/gui/RoutinePlanDialog.py', '新建计划'),
        ):
            source = Path(path).read_text(encoding='utf-8')
            self.assertIn('QInputDialog(self)', source, path)
            self.assertIn('theme.install(dialog)', source, path)
            self.assertIn('dialog.exec() == QInputDialog.Accepted', source, path)
            # No live static getText call (ignore the explanatory comment line).
            live_calls = [line for line in source.splitlines()
                          if 'QInputDialog.getText' in line and not line.lstrip().startswith('#')]
            self.assertEqual(live_calls, [], path)
            self.assertIn(title, source, path)


class TestFixedStaminaCost(unittest.TestCase):
    """各副本单次体力固定，不给用户可配置项；双倍由 use_stamina 按 once*2 推导。"""

    def test_each_domain_has_fixed_cost(self):
        cases = (
            ('src/task/TacetTask.py', 60, '无音清剿'),
            ('src/task/ForgeryTask.py', 40, '凝素领域'),
            ('src/task/SimulationTask.py', 40, '模拟领域'),
        )
        for path, cost, name in cases:
            source = Path(path).read_text(encoding='utf-8')
            self.assertIn(f'self.stamina_once = {cost}', source, f'{name} 固定消耗')

    def test_tacet_has_no_stamina_config(self):
        source = Path('src/task/TacetTask.py').read_text(encoding='utf-8')
        self.assertNotIn('Tacet Stamina Cost', source)
        self.assertNotIn('configured_cost', source)

    def test_stale_config_cannot_override_fixed_cost(self):
        """旧配置里残留的 Tacet Stamina Cost 也不能改变固定 60。"""
        task = TacetTask.__new__(TacetTask)
        task.tr = lambda message, **kwargs: message
        task.stamina_once = 60
        config = {'Tacet Stamina Cost': 40, 'Which Tacet Suppression to Farm': 1}
        calls = []
        noop = lambda *args, **kwargs: None
        for name in ('info_incr', 'sleep', 'openF2Book', 'click_relative', 'open_boss_book',
                     'click_team_challenge', 'wait_in_team_and_world', 'walk_to_treasure',
                     'pick_f', 'ensure_main', 'esc_cancel'):
            setattr(task, name, noop)
        task.get_stamina = lambda: (120, 0, 120)
        task.teleport_to_tacet = noop
        task.clear_tacet_and_reach_reward = noop
        task.has_claim_stamina = lambda: True
        task.use_stamina = lambda once, must_use: calls.append((once, must_use)) or (False, once)
        task.log_info = noop
        task.log_warning = noop
        task.realm_entry_at_heal_point = False
        self.assertIsNone(task.farm_tacet(config=config))
        self.assertEqual([(60, 0)], calls)
        self.assertEqual(60, task.stamina_once)

    def test_double_claim_costs_double_stamina(self):
        """双倍领取按 once*2 扣体力/日常进度。"""
        source = Path('src/task/BaseWWTask.py').read_text(encoding='utf-8')
        body = source[source.index('    def use_stamina('):source.index('    @contextmanager', source.index('    def use_stamina('))]
        self.assertIn('used = once * 2', body)
        self.assertIn('must_use -= used', body)
        self.assertIn('return can_continue, used', body)

    def test_double_claim_counts_as_two_runs(self):
        """双倍领取一次按两次计入 max_runs，单次挑战不再只加 1。"""
        for path in ('src/task/TacetTask.py', 'src/task/DomainTask.py'):
            source = Path(path).read_text(encoding='utf-8')
            self.assertIn('runs += used // self.stamina_once if self.stamina_once else 1',
                          source, path)
            self.assertNotIn('runs += 1', source, path)

        task = TacetTask.__new__(TacetTask)
        task.tr = lambda message, **kwargs: message
        task.stamina_once = 60
        task.config = {}
        logs = []
        noop = lambda *args, **kwargs: None
        for name in ('info_incr', 'sleep', 'openF2Book', 'click_relative', 'open_boss_book',
                     'click_team_challenge', 'wait_in_team_and_world', 'walk_to_treasure',
                     'pick_f', 'ensure_main', 'esc_cancel', 'teleport_to_tacet',
                     'clear_tacet_and_reach_reward'):
            setattr(task, name, noop)
        task.get_stamina = lambda: (160, 0, 160)
        task.has_claim_stamina = lambda: True
        task.use_stamina = lambda once, must_use: (True, once * 2)
        task.log_info = lambda message, **kwargs: logs.append(message)
        task.log_warning = noop
        task.rechallenge_tacet = noop
        task.realm_entry_at_heal_point = False
        self.assertIsNone(task.farm_tacet(max_runs=2))
        self.assertEqual(1, len([m for m in logs if 'reached configured run count' in m]))


if __name__ == '__main__':
    unittest.main()
