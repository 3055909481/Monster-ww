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

    def test_monitor_translates_task_names(self):
        source = Path('src/gui/TaskMonitorTab.py').read_text(encoding='utf-8')
        self.assertIn('from Mon import og, TriggerTask', source)
        self.assertIn('app.tr(name)', source)  # Chinese display name via i18n
        self.assertIn('def _display_name(task):', source)

    def test_monitor_has_per_task_stop_button(self):
        source = Path('src/gui/TaskMonitorTab.py').read_text(encoding='utf-8')
        self.assertIn('def _stop_one(self, task):', source)
        self.assertIn('def _on_row_stop(self):', source)
        self.assertIn('button.clicked.connect(self._on_row_stop)', source)
        self.assertIn('self._button_tasks[id(button)] = task', source)
        self.assertIn('table.setCellWidget(row, 4, button)', source)  # stop column
        # Reuse rows instead of rebuilding each second (avoids lost clicks).
        self.assertNotIn('table.setRowCount(0)', source)
        self.assertIn('QTimer.singleShot(0, self._refresh)', source)

    def test_monitor_separates_trigger_and_manual(self):
        source = Path('src/gui/TaskMonitorTab.py').read_text(encoding='utf-8')
        self.assertIn('def _is_trigger(task):', source)
        self.assertIn('isinstance(task, TriggerTask)', source)
        self.assertIn('self.trigger_table', source)
        self.assertIn('self.manual_table', source)
        self.assertIn("def _stop_group(self, kind):", source)
        self.assertIn("self.trigger_stop_all", source)
        self.assertIn("self.manual_stop_all", source)

    def test_monitor_filters_hidden_and_stops_current(self):
        source = Path('src/gui/TaskMonitorTab.py').read_text(encoding='utf-8')
        self.assertIn("getattr(task, 'visible', True)", source)
        one = next(n for n in ast.walk(ast.parse(source))
                   if isinstance(n, ast.FunctionDef) and n.name == '_stop_one')
        self.assertIn('self.executor.stop_current_task()', ast.unparse(one))


class CombatTimeoutTest(unittest.TestCase):
    """整场战斗硬超时与 in_liberation 短路兜底的源码守卫。"""

    def test_combat_hard_timeout(self):
        source = Path('src/task/BaseCombatTask.py').read_text(encoding='utf-8')
        check_src = Path('src/combat/CombatCheck.py').read_text(encoding='utf-8')
        # 独立异常(非 NotInCombatException 子类), 否则副本循环当普通脱战重试。
        self.assertIn('class CombatTimeoutException(Exception):', check_src)
        self.assertIn('from src.combat.CombatCheck import CombatCheck, CombatTimeoutException', source)
        self.assertIn('COMBAT_HARD_TIMEOUT = 900', source)
        sleep_check = next(n for n in ast.walk(ast.parse(source))
                           if isinstance(n, ast.FunctionDef) and n.name == 'sleep_check')
        self.assertIn('CombatTimeoutException', ast.unparse(sleep_check))
        self.assertIn('_global_combat_deadline', ast.unparse(sleep_check))
        in_combat = next(n for n in ast.walk(ast.parse(check_src))
                         if isinstance(n, ast.FunctionDef) and n.name == 'in_combat')
        self.assertIn('CombatTimeoutException', ast.unparse(in_combat))  # must re-raise

    def test_stamina_wait_has_buffer(self):
        for path in ('src/task/BaseWWTask.py', 'src/task/CustomFlowTask.py'):
            source = Path(path).read_text(encoding='utf-8')
            self.assertIn('STAMINA_RECOVER_SECONDS + 300', source)

    def test_account_key_is_process_wide(self):
        rt = Path('src/task/RoutineTask.py').read_text(encoding='utf-8')
        self.assertIn('set_current_account(account_key)', rt)
        sched = Path('src/schedule_runtime.py').read_text(encoding='utf-8')
        self.assertIn('def set_current_account(account):', sched)
        self.assertIn('def current_account():', sched)
        base = Path('src/task/BaseWWTask.py').read_text(encoding='utf-8')
        self.assertIn('schedule_runtime.current_account()', base)

    def test_monitor_icon_and_columns(self):
        source = Path('src/gui/TaskMonitorTab.py').read_text(encoding='utf-8')
        self.assertIn("icon = 'monitor'", source)
        self.assertIn('QHeaderView.ResizeMode.Stretch', source)
        self.assertIn('_button_tasks', source)
        self.assertNotIn('setProperty(', source)

    def test_liberation_shortcut_bounded(self):
        source = Path('src/combat/CombatCheck.py').read_text(encoding='utf-8')
        self.assertIn('LIBERATION_CHECK_GRACE = 8.0', source)
        check = next(n for n in ast.walk(ast.parse(source))
                     if isinstance(n, ast.FunctionDef) and n.name == 'do_check_in_combat')
        body = ast.unparse(check)
        self.assertIn('_last_liberation', body)
        self.assertIn('self.in_liberation = False', body)

    def test_exit_releases_held_inputs(self):
        source = Path('src/exit_cleanup.py').read_text(encoding='utf-8')
        self.assertIn('release_held_inputs', source)

    def test_fkey_watcher_stoppable(self):
        source = Path('src/combat/FKeyStart.py').read_text(encoding='utf-8')
        self.assertIn('self._stop = threading.Event()', source)
        self.assertIn('while not self._stop.is_set():', source)
        self.assertIn('def stop(self):', source)
        self.assertIn('def uninstall_self_f_filter():', source)
        exit_src = Path('src/exit_cleanup.py').read_text(encoding='utf-8')
        self.assertIn('f_key_watcher.stop()', exit_src)
        self.assertIn('uninstall_self_f_filter()', exit_src)

    def test_stamina_recovery_matches_daily_cap(self):
        source = Path('src/task/BaseWWTask.py').read_text(encoding='utf-8')
        self.assertIn('STAMINA_RECOVER_SECONDS = 360', source)
        self.assertIn('_stamina_wait_ledger', source)

    def test_exit_cleanup_installed(self):
        main = Path('main.py').read_text(encoding='utf-8')
        self.assertIn('from src.exit_cleanup import install as install_exit_cleanup', main)
        source = Path('src/exit_cleanup.py').read_text(encoding='utf-8')
        self.assertIn('app.aboutToQuit.connect(stop_all_manual_tasks)', source)
        self.assertIn('task.disable()', source)


if __name__ == "__main__":
    unittest.main()
