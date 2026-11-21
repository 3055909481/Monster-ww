"""删除/新建计划后，外层自定义页下拉框能刷新：回调选择逻辑。

任务设置页的「配置流水线」按钮会把 clicked 的 bool 传给 configure_plans，
此时 on_saved 不可调用，必须主动刷新 RoutineTab；从自定义页「编辑步骤」
打开时则走调用方传入的 on_saved。
"""

import unittest
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


if __name__ == '__main__':
    unittest.main()
