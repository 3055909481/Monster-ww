"""Unit tests for the shared run/stop card helper (no Mon/Qt dependency)."""

import unittest
from pathlib import Path
from unittest.mock import Mock

from src.gui.task_control import stop_task


class StopTaskTest(unittest.TestCase):
    def test_stops_the_clicked_task(self):
        task = Mock()
        task.running = False
        self.assertTrue(stop_task(task, None))
        task.disable.assert_called_once_with()

    def test_unpauses_a_paused_run(self):
        task = Mock()
        task.running = True
        stop_task(task, None)
        task.unpause.assert_called_once_with()

    def test_does_not_unpause_when_not_running(self):
        task = Mock()
        task.running = False
        stop_task(task, None)
        task.unpause.assert_not_called()

    def test_chains_to_current_task_for_nested_runs(self):
        task = Mock()
        task.running = False
        executor = Mock()
        executor.current_task = Mock()  # a different (parent) task
        stop_task(task, executor)
        executor.stop_current_task.assert_called_once_with()

    def test_does_not_stop_current_when_clicking_it(self):
        task = Mock()
        task.running = False
        executor = Mock()
        executor.current_task = task
        stop_task(task, executor)
        executor.stop_current_task.assert_not_called()

    def test_never_raises_on_broken_task(self):
        task = Mock()
        task.disable.side_effect = RuntimeError('already gone')
        task.running = True
        task.unpause.side_effect = RuntimeError('gone')
        executor = Mock()
        executor.current_task = Mock()
        executor.stop_current_task.side_effect = RuntimeError('gone')
        # All failures are swallowed so a stale widget can't break the UI.
        self.assertFalse(stop_task(task, executor))

    def test_none_task_is_noop(self):
        self.assertFalse(stop_task(None, None))


if __name__ == '__main__':
    unittest.main()


class RunStopCardGuard(unittest.TestCase):
    """两个启动型卡片都接入就地停止, 不能只有一个。"""

    TABS = ('src/gui/CustomFlowTab.py', 'src/gui/RoutineTab.py')

    def test_buttons_toggle_run_stop(self):
        for path in self.TABS:
            source = Path(path).read_text(encoding='utf-8')
            self.assertIn('from src.gui.task_control import stop_task', source, path)
            self.assertIn('def _on_run_clicked(self):', source, path)
            self.assertIn("button.setText(self.tr('停止任务') if running else self.tr('运行任务'))", source, path)
            self.assertIn("button.set_icon('stop' if running else 'play')", source, path)
            self.assertIn('stop_task(self.task, self.executor)', source, path)

    def test_button_wired_to_dispatcher_not_start(self):
        for path in self.TABS:
            source = Path(path).read_text(encoding='utf-8')
            self.assertIn('self.run_button.clicked.connect(self._on_run_clicked)', source, path)
