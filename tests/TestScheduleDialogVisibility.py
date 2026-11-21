"""创建计划任务弹窗：计划配置/执行账号仅对日常一条龙动态显示。"""

import unittest
from unittest.mock import patch as mock_patch

from PySide6.QtWidgets import QApplication, QWidget

from src.schedule_patch import install
from src.task.RoutineTask import RoutineTask


APP = QApplication.instance() or QApplication([])
PARENT = QWidget()


class RoutineTaskStub:
    support_schedule_task = True
    name = '日常一条龙'


class OtherTaskStub:
    support_schedule_task = True
    name = '噩梦巢穴任务'


def FakeTask(name, cls_name):
    """按类名返回对应 stub 实例，使 type(task).__name__ 可被补丁识别。"""
    if cls_name == 'RoutineTask':
        obj = RoutineTaskStub()
    else:
        obj = OtherTaskStub()
    obj.name = name
    return obj


class TestScheduleDialogVisibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install()

    def _dialog(self, tasks):
        from Mon.ui.qt.tasks.ScheduleTaskTab import CreateScheduleTaskDialog
        from Mon import og
        import src.schedule_patch as sp
        fake_executor = type('E', (), {'onetime_tasks': tasks})()
        fake_app = type('A', (), {'tr': lambda self, text: text})()
        with mock_patch('src.schedule_patch._plans', return_value=['默认', '国际服']), \
             mock_patch('src.schedule_patch._accounts', return_value=['acc@x.com']), \
             mock_patch('src.schedule_patch._is_routine_task',
                        side_effect=lambda t: type(t).__name__ == 'RoutineTaskStub'), \
             mock_patch.object(og, 'executor', fake_executor, create=True), \
             mock_patch.object(og, 'app', fake_app, create=True):
            dlg = CreateScheduleTaskDialog(PARENT)
        dlg.tasks = tasks
        dlg.task_combo.clear()
        dlg.task_combo.addItems([t.name for t in tasks])
        return dlg

    def _sync(self, dlg, index):
        with mock_patch('src.schedule_patch._is_routine_task',
                        side_effect=lambda t: type(t).__name__ == 'RoutineTaskStub'):
            dlg._sync_schedule_extras(index)

    def test_extras_hidden_for_non_routine_task(self):
        dlg = self._dialog([FakeTask('噩梦巢穴任务', 'NightmareNestTask')])
        self._sync(dlg, 0)
        self.assertTrue(dlg.schedule_plan_combo.isHidden())
        self.assertTrue(dlg.schedule_account_combo.isHidden())

    def test_extras_shown_for_routine_task(self):
        dlg = self._dialog([FakeTask('日常一条龙', 'RoutineTask')])
        self._sync(dlg, 0)
        self.assertFalse(dlg.schedule_plan_combo.isHidden())
        self.assertFalse(dlg.schedule_account_combo.isHidden())

    def test_switching_task_resets_values(self):
        dlg = self._dialog([
            FakeTask('日常一条龙', 'RoutineTask'),
            FakeTask('噩梦巢穴任务', 'NightmareNestTask'),
        ])
        dlg.schedule_plan_combo.setCurrentText('国际服')
        dlg.schedule_account_combo.setCurrentText('acc@x.com')
        # 切到非一条龙：控件隐藏且值被重置为首项
        self._sync(dlg, 1)
        self.assertEqual(dlg.schedule_plan_combo.currentIndex(), 0)
        self.assertEqual(dlg.schedule_account_combo.currentIndex(), 0)
        self.assertTrue(dlg.schedule_plan_combo.isHidden())

    def test_hidden_dialog_never_adjusts_size(self):
        """窗口未显示时切任务，不能调用 dialog.adjustSize()——它会把无 layout
        的 dialog 收缩到极小 sizeHint，令 panel 错位、布局显得拥挤。"""
        dlg = self._dialog([
            FakeTask('日常一条龙', 'RoutineTask'),
            FakeTask('噩梦巢穴任务', 'NightmareNestTask'),
        ])
        dialog = dlg.form.dialog
        with mock_patch.object(dialog, 'isVisible', return_value=False), \
             mock_patch.object(dialog, 'adjustSize') as adjust, \
             mock_patch.object(dialog, '_fit') as fit:
            self._sync(dlg, 1)
        adjust.assert_not_called()
        fit.assert_not_called()

    def test_visible_dialog_uses_fit_not_adjust_size(self):
        """窗口可见时切任务，改用框架的 _fit() 重新居中，且绝不 adjustSize。"""
        dlg = self._dialog([
            FakeTask('日常一条龙', 'RoutineTask'),
            FakeTask('噩梦巢穴任务', 'NightmareNestTask'),
        ])
        dialog = dlg.form.dialog
        with mock_patch.object(dialog, 'isVisible', return_value=True), \
             mock_patch.object(dialog, 'adjustSize') as adjust, \
             mock_patch.object(dialog, '_fit') as fit:
            self._sync(dlg, 1)
        fit.assert_called_once()
        adjust.assert_not_called()


if __name__ == '__main__':
    unittest.main()
