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


class MultiAccountStopLogTest(unittest.TestCase):
    """用户主动停止多账号时不应记成 ERROR / 弹运行出错截图。"""

    def test_run_task_by_class_swallows_stop_error_log(self):
        source = Path('src/task/MultiAccountDailyTask.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        method = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == 'run_task_by_class')
        body = ast.unparse(method)
        self.assertIn('except TaskDisabledException:', body)
        self.assertIn('raise', body)
        # The stop branch must not call log_error (which triggers the screenshot).
        try_node = next(n for n in ast.walk(method) if isinstance(n, ast.Try))
        stop_handler = next(n for n in try_node.handlers
                            if isinstance(n.type, ast.Name) and n.type.id == 'TaskDisabledException')
        self.assertNotIn('log_error', ast.unparse(stop_handler))


class PyAppifyLauncherTest(unittest.TestCase):
    """在线更新必须交给 PyAppify 启动器（上游 ok-ww 方案）。"""

    def test_config_disables_in_app_update_card(self):
        source = Path('config.py').read_text(encoding='utf-8')
        self.assertIn('pyappify.get_version_list = None', source)
        self.assertNotIn('_updater.install()', source)
        self.assertNotIn('incremental updater API unavailable', source)

    def test_no_incremental_backend(self):
        # coco 增量后端不应再随包发布, 否则会覆盖启动器内置模块。
        root = Path('.')
        self.assertFalse((root / 'pyappify' / '__init__.py').exists())
        self.assertFalse((root / 'src' / 'net_ssl.py').exists())
        self.assertFalse((root / 'src' / 'update_confirm.py').exists())

    def test_workflow_uses_pyappify_launcher(self):
        workflow = Path('.github/workflows/portable-release.yml').read_text(encoding='utf-8')
        self.assertIn('pyappify', workflow)
        self.assertIn('launcher', workflow)
        self.assertNotIn('PyInstaller', workflow)
        self.assertNotIn('Monster-ww.spec', workflow)


class ReviewRound3Test(unittest.TestCase):
    """三轮审查修复守卫。"""

    def test_domain_uses_ocr_rechallenge(self):
        source = Path('src/task/DomainTask.py').read_text(encoding='utf-8')
        self.assertIn('self.rechallenge(', source)
        self.assertNotIn("self.click(0.68, 0.84, after_sleep=1)", source)
        base = Path('src/task/BaseWWTask.py').read_text(encoding='utf-8')
        self.assertIn('def rechallenge(', base)
        self.assertIn('RECHALLENGE_TEXT', base)

    def test_self_f_note_wired(self):
        source = Path('src/task/BaseWWTask.py').read_text(encoding='utf-8')
        self.assertIn('def _note_self_f(', source)
        self.assertIn('f_key_watcher.note_self_key', source)
        self.assertIn('f_key_watcher.note_self_key_up', source)

    def test_login_timeout_exits(self):
        source = Path('src/task/BaseWWTask.py').read_text(encoding='utf-8')
        self.assertIn('login_screen_visible', source)
        self.assertIn("raise CannotFindException(self.tr('Timed out on the game login screen.'))", source)
        routine = Path('src/task/RoutineTask.py').read_text(encoding='utf-8')
        self.assertIn('except (_TaskDisabled, _CannotFind):', routine)

    def test_mon_script_sync_tag(self):
        # .mon-tmp 是本地 vendored 目录, 不随仓库提交, CI 上不存在;
        # 只校验已跟踪的 requirements.txt 指向新 tag。
        req = Path('requirements.txt').read_text(encoding='utf-8')
        self.assertIn('v3.0.15-mon1', req)
        self.assertNotIn('v3.0.10-mon1', req)


if __name__ == '__main__':
    unittest.main()
