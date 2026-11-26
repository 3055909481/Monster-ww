"""Source guards for the stuck-combat and stop-not-honoured fixes.

Both modules import the Mon framework (unavailable locally), so the key
invariants are locked with AST/source checks. CI runs the full suite.
"""

import ast
import unittest
from pathlib import Path


class StuckCombatTest(unittest.TestCase):
    def setUp(self):
        self.source = Path('src/char/ShoreKeeper.py').read_text(encoding='utf-8')

    def test_intro_skip_is_time_bounded(self):
        fn = next(n for n in ast.walk(ast.parse(self.source))
                  if isinstance(n, ast.FunctionDef) and n.name == 'skip_combat_check')
        body = ast.unparse(fn)
        self.assertIn('INTRO_COMBAT_CHECK_GRACE', body)
        self.assertIn('last_switch_in_time', body)
        self.assertIn('switched_at < 0', body)  # unset timestamp = fresh intro
        self.assertIn('self.has_intro = False', body)  # stale flag must clear
        self.assertIn('return self.flying()', body)

    def test_grace_constant_is_finite(self):
        cls = next(n for n in ast.walk(ast.parse(self.source))
                   if isinstance(n, ast.ClassDef) and n.name == 'ShoreKeeper')
        assign = next(n for n in cls.body if isinstance(n, ast.Assign)
                      and any(getattr(t, 'id', None) == 'INTRO_COMBAT_CHECK_GRACE' for t in n.targets))
        value = ast.literal_eval(assign.value)
        self.assertIsInstance(value, (int, float))
        self.assertGreater(value, 0)
        self.assertLessEqual(value, 30)


class MultiAccountStopTest(unittest.TestCase):
    def setUp(self):
        self.source = Path('src/task/MultiAccountConfigTask.py').read_text(encoding='utf-8')

    def test_stop_check_exists_and_guards_click(self):
        self.assertIn('def _check_stopped(self):', self.source)
        self.assertIn("getattr(self, 'enabled', True) is False", self.source)
        self.assertIn('raise TaskDisabledException', self.source)
        click = next(n for n in ast.walk(ast.parse(self.source))
                     if isinstance(n, ast.FunctionDef) and n.name == 'click')
        self.assertIn('_check_stopped()', ast.unparse(click))

    def test_stop_check_guards_all_game_input(self):
        # Click is the single choke point for every login/switch action;
        # checking there stops mid-account too, without changing the account
        # loop's control flow (which tests rely on for result recording).
        tree = ast.parse(self.source)
        click = next(n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == 'click')
        self.assertIn('self._check_stopped()', ast.unparse(click))
        run = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == 'run')
        self.assertNotIn('self._check_stopped()', ast.unparse(run))


class TaskMonitorTest(unittest.TestCase):
    """全局任务监控页与退出清理的源码守卫。"""

    def test_monitor_tab_registered(self):
        self.assertIn('["src.gui.TaskMonitorTab", "TaskMonitorTab"]',
                      Path('config.py').read_text(encoding='utf-8'))

    def test_monitor_has_stop_all(self):
        source = Path('src/gui/TaskMonitorTab.py').read_text(encoding='utf-8')
        self.assertIn('def _stop_all(self):', source)
        self.assertIn('task.disable()', source)
        self.assertIn('self.executor.stop_current_task()', source)

    def test_exit_cleanup_installed(self):
        main = Path('main.py').read_text(encoding='utf-8')
        self.assertIn('from src.exit_cleanup import install as install_exit_cleanup', main)
        source = Path('src/exit_cleanup.py').read_text(encoding='utf-8')
        self.assertIn('app.aboutToQuit.connect(stop_all_manual_tasks)', source)
        self.assertIn('task.disable()', source)


if __name__ == "__main__":
    unittest.main()
