"""删除/新建计划后，外层自定义页下拉框能刷新：回调选择逻辑。

任务设置页的「配置流水线」按钮会把 clicked 的 bool 传给 configure_plans，
此时 on_saved 不可调用，必须主动刷新 RoutineTab；从自定义页「编辑步骤」
打开时则走调用方传入的 on_saved。
"""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.task.RoutineTask import RoutineTask


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


class TestTacetStaminaCost(unittest.TestCase):
    """无音区单次体力消耗可配置(部分副本 40, 标准 60)。"""

    def test_config_overrides_stamina_once(self):
        source = Path('src/task/TacetTask.py').read_text(encoding='utf-8')
        self.assertIn("'Tacet Stamina Cost': 60", source)
        self.assertIn("config.get('Tacet Stamina Cost', self.stamina_once)", source)
        self.assertIn('self.stamina_once = configured_cost', source)


if __name__ == '__main__':
    unittest.main()
