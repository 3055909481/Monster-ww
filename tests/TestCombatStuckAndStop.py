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
        # Must check the top-level task the user actually starts/stops, not
        # this hidden child's own `enabled` (always False as a run_task_by_class
        # child) — otherwise the first click of every run aborts as "stopped".
        self.assertIn("getattr(executor, 'current_task', None)", self.source)
        self.assertIn("getattr(current, 'enabled', True) is False", self.source)
        self.assertNotIn("getattr(self, 'enabled', True) is False", self.source)
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
        # Exit cleanup must abort only the running task via the in-memory flag,
        # never call disable() (TriggerTask.disable persists config['_enabled']=False,
        # which made every trigger task come back disabled on next launch).
        self.assertIn('task._enabled = False', source)
        self.assertNotIn('task.disable()', source)

    def test_install_defers_when_no_qapplication_yet(self):
        """main.py calls install() before OK() creates QApplication.

        install() must defer via init_app_config instead of silently returning
        (the old bug: cleanup never registered, trigger tasks stayed enabled
        at exit and the framework wrote config['_enabled']=False).
        """
        source = Path('src/exit_cleanup.py').read_text(encoding='utf-8')
        self.assertIn('from Mon.ui.qt.util import app as app_util', source)
        self.assertIn('app_util.init_app_config = patched', source)
        # 幂等靠模块级 _PATCHED，不用函数属性：Cython 编译后函数是 builtin，
        # setattr 会抛异常导致 install 失败、退出清理从未注册。
        self.assertIn('global _INSTALLED, _PATCHED', source)
        self.assertIn('_PATCHED = patched', source)
        self.assertNotIn('patched._exit_cleanup_wrapped = True', source)
        # The deferred path must still hook aboutToQuit when the app is created.
        self.assertIn('_connect(qapp)', source)
        # main.py must install before OK() so the wrapper is in place in time.
        main = Path('main.py').read_text(encoding='utf-8')
        self.assertLess(main.index('install_exit_cleanup()'), main.index('ok = OK(config)'))


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

    def test_workflow_auto_releases_on_version_tag(self):
        """推 v* tag 自动发布；归一化 inputs 默认值供 push 事件使用。"""
        workflow = Path('.github/workflows/portable-release.yml').read_text(encoding='utf-8')
        # push tag 触发器
        self.assertIn('  push:', workflow)
        self.assertIn("      - 'v*'", workflow)
        # 版本解析用 push 的 tag 名
        self.assertIn("PUSH_TAG: ${{ github.ref_type == 'tag' && github.ref_name || '' }}", workflow)
        self.assertIn("$isPushTag = ($env:EVENT_NAME -eq 'push'", workflow)
        # inputs 归一化（push 无 inputs），且没有自引用
        self.assertIn("PKG_FORMAT: ${{ inputs.package_format || '7z' }}", workflow)
        self.assertIn("LAUNCHER: ${{ inputs.launcher || 'updater' }}", workflow)
        self.assertIn("PUBLISH_RELEASE: ${{ github.event_name == 'push' || inputs.publish_release }}", workflow)
        # 下游用归一化变量，而不是裸 inputs.*
        self.assertIn('package_zip_format: ${{ env.PKG_FORMAT }}', workflow)
        self.assertIn('if: ${{ env.LAUNCHER == \'updater\' }}', workflow)
        self.assertIn('if: ${{ env.PUBLISH_RELEASE', workflow)
        self.assertNotIn('inputs.package_format }}', workflow.split('jobs:')[1])

    def test_pushed_tag_keeps_exact_version(self):
        """推 v* tag 必须用该 tag 的精确版本，不能被自动升级。

        旧逻辑把 checkout 里已存在的 tag 也算进"最新 tag"，导致 v1.0.77
        被误判为"不比最新新"而升到 v2.0.0，tag 推送永远跳大版本。
        """
        workflow = Path('.github/workflows/portable-release.yml').read_text(encoding='utf-8')
        self.assertIn("$isPushTag = ($env:EVENT_NAME -eq 'push'", workflow)
        self.assertIn('if (-not $newer -and -not $isPushTag)', workflow)
        self.assertIn('using it as-is (no version bump)', workflow)
        # collision 处理（会改名）必须跳过 push tag
        self.assertIn('if (-not $isPushTag) {\n          $used = @(', workflow)

    def test_full_installer_is_manual_only(self):
        """完整安装包仅手动触发：避免与便携版在同一 tag 上双流并跑。"""
        build = Path('.github/workflows/build.yml').read_text(encoding='utf-8')
        self.assertIn('  workflow_dispatch:', build)
        self.assertIn("      version:", build)
        # 不能有 push tag 触发器
        self.assertNotIn("    tags:\n      - 'v*'", build)
        # 手动触发时 github.ref_name 是分支名，版本必须来自 inputs.version
        self.assertIn("RELEASE_TAG: ${{ inputs.version }}", build)
        self.assertNotIn('RELEASE_TAG: ${{ github.ref_name }}', build)
        self.assertNotIn('${GITHUB_REF_NAME#v}', build)


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


class ExitCleanupConfigTest(unittest.TestCase):
    """退出清理不得把触发任务的启用状态写进配置(否则下次启动全关)。"""

    def test_exit_abort_does_not_call_disable_or_write_config(self):
        from unittest.mock import Mock, patch
        import importlib.util

        # Load exit_cleanup without an installed framework/app: stop_all_manual_tasks
        # only needs `from Mon import og` at call time, which we patch.
        spec = importlib.util.spec_from_file_location('_exit_cleanup', 'src/exit_cleanup.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        running = Mock()
        running._enabled = True
        running.enabled = True
        running.running = True
        running.release_held_inputs = Mock()
        running.unpause = Mock()
        # A trigger task that is enabled but NOT running must be left alone.
        idle = Mock()
        idle._enabled = True
        idle.enabled = True
        idle.running = False

        executor = Mock()
        executor.get_all_tasks.return_value = [running, idle]
        executor.current_task = running
        og = Mock()
        og.executor = executor
        og.app = None

        with patch.dict('sys.modules', {'Mon': Mock(og=og)}):
            module.stop_all_manual_tasks()

        # Running task aborted in memory only; disable() (which persists config) never used.
        self.assertFalse(running._enabled)
        running.disable.assert_not_called()
        running.release_held_inputs.assert_called_once()
        running.unpause.assert_called_once()
        # Idle trigger task untouched - its enabled preference must survive restart.
        self.assertTrue(idle._enabled)
        idle.disable.assert_not_called()


if __name__ == '__main__':
    unittest.main()
