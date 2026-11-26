"""User-defined task flow orchestrator.

Steps are edited in the GUI and stored in configs/CustomFlows.json. This task
walks the ordered list and drives the whole client lifecycle: start a server's
client, log into an account, run existing tasks, switch accounts and exit the
client. Each step is isolated so a failure does not abort the remaining steps,
while a user stop (TaskDisabledException) always propagates immediately.
"""

import importlib
import os
from contextlib import contextmanager

from Mon import Config as _Config, Logger, PostMessageInteraction, TaskDisabledException
from Mon.task.exceptions import WaitFailedException

from src.task.BaseCombatTask import BaseCombatTask
from src.task.BaseWWTask import LOGIN_TEXTS
from src.task.MouseResetTask import MouseResetTask
from src.task.RoutineTask import RoutineTask, plan_options_for as routine_plan_options
from src.task.WWOneTimeTask import WWOneTimeTask
from src.task.account.MultiAccountConfigStore import (
    MultiAccountConfigStore, account_key_from_ocr, display_account,
)
from src.task.account.RuntimeTaskConfig import isolated_task_configs
from src.task.routine.FlowSteps import (
    DEFAULT_FLOW_NAME, EDIT_FLOWS, ENTRIES_BY_ID, FLOW_SELECTOR,
    ROUTINE_PLAN_KEY, ROUTINE_TASK_NAME, SERVER_CN, SERVER_FOLLOW, SERVER_GLOBAL,
    FlowStepStore, describe_params, entry_config_type, normalize_items,
    resolve_flow_account, task_reference,
)
from src.task.task_icons import task_icon

logger = Logger.get_logger(__name__)


def flow_options_for(folder):
    """Flow names available in the selector, read from a given config folder."""
    return list(FlowStepStore(folder).load())


@contextmanager
def _no_scheduled_account():
    """Hide a scheduling CLI account from nested one-time tasks.

    A flow owns account/login steps itself. If a task inside the flow also ran
    WWOneTimeTask.run(), a lingering --schedule-account would make it switch
    accounts unexpectedly. Suppress that just for the nested call.
    """
    from src import schedule_runtime

    original = getattr(schedule_runtime, 'scheduled_account', None)
    try:
        schedule_runtime.scheduled_account = lambda: None
        yield
    finally:
        schedule_runtime.scheduled_account = original


class CustomFlowTask(WWOneTimeTask, BaseCombatTask):
    """Run a user-defined sequence of client/account/task steps."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = '🔀 自定义任务流'
        self.icon = task_icon('multi-account')
        self.group_name = '日常活动'
        self.group_icon = task_icon('category-daily')
        self.description = '自定义完整执行流程：启动指定服务器、登录账号、执行任务、切换账号、退出客户端。'
        self.support_schedule_task = True
        self.default_config[FLOW_SELECTOR] = DEFAULT_FLOW_NAME
        self.config_description[FLOW_SELECTOR] = '任务流名称，保存在 configs/CustomFlows.json。'
        self.config_type[FLOW_SELECTOR] = {
            'type': 'drop_down',
            'options': flow_options_for(_Config.config_folder),
        }
        self.default_config[EDIT_FLOWS] = False
        self.config_type[EDIT_FLOWS] = {
            'type': 'button',
            'text': '配置任务流',
            'callback': self.configure_flows,
        }
        self.add_exit_after_config()
        self.flow_name = DEFAULT_FLOW_NAME
        self.results = []
        self.current_server = None

    # ------------------------------------------------------------------ GUI
    def configure_flows(self, on_saved=None):
        from PySide6.QtWidgets import QApplication
        from src.gui.CustomFlowDialog import CustomFlowDialog

        dialog = CustomFlowDialog(QApplication.activeWindow(), self)
        try:
            saved = dialog.exec()
        finally:
            dialog.deleteLater()
        if not saved:
            return
        if callable(on_saved):
            on_saved()
        else:
            self._refresh_flow_tabs()

    @staticmethod
    def _refresh_flow_tabs():
        from PySide6.QtWidgets import QApplication
        try:
            from src.gui.CustomFlowTab import CustomFlowTab
        except ImportError:
            return
        for widget in QApplication.allWidgets():
            if isinstance(widget, CustomFlowTab) and hasattr(widget, '_render'):
                widget._render()

    def flow_options(self):
        return flow_options_for(self.executor.config_folder)

    def configured_accounts(self):
        store = MultiAccountConfigStore(self.executor.config_folder)
        try:
            accounts = store.load()
        except (OSError, ValueError):
            return []
        return [account['account_key'] for account in accounts
                if isinstance(account, dict) and account.get('account_key')]

    def routine_plans(self):
        return routine_plan_options(self.executor.config_folder)

    def config_type_for(self, step_id):
        return entry_config_type(
            step_id,
            accounts=self.configured_accounts(),
            plans=self.routine_plans(),
        )

    # -------------------------------------------------------- login helpers
    def click(self, x=-1, y=-1, *args, **kwargs):
        # Native login ComboBox needs a real cursor hover before background clicks.
        if (getattr(self, '_login_cursor_active', False)
                and isinstance(x, (int, float)) and isinstance(y, (int, float))
                and x >= 1 and y >= 1
                and isinstance(self.executor.interaction, PostMessageInteraction)):
            position = self.executor.interaction.capture.get_abs_cords(x, y)
            import win32api
            win32api.SetCursorPos((int(position[0]), int(position[1])))
            self.sleep(0.2)
        return super().click(x, y, *args, **kwargs)

    def _login_combo(self):
        from src.task.MultiAccountDailyTask import find_login_combo
        return find_login_combo(self.hwnd.hwnd if self.hwnd else 0)

    def _at_login_screen(self):
        texts = self.ocr()
        if not self.find_boxes(texts, LOGIN_TEXTS):
            return None
        boxes = [box for box in texts if '*' in box.name or '@' in box.name]
        return max(boxes, key=lambda box: box.y + box.height / 2) if boxes else None

    def _switch_to_login(self):
        self.log_info(self.tr('Switching back to login screen'))
        self.send_key('esc', after_sleep=1.5)
        self.wait_feature('esc_setting')
        self.click_relative(0.04, 0.96, after_sleep=1)
        self.click_confirm(timeout=10)
        self.wait_until(lambda: self._login_combo() or self._at_login_screen(),
                        time_out=60, settle_time=2, raise_if_not_found=True)
        self.log_info(self.tr('Back at login screen'))

    def _select_target_account(self, account_key):
        # RoutineTask._login_target_account() calls self._select_target_account,
        # so delegate explicitly (this task does not inherit from RoutineTask).
        return RoutineTask._select_target_account(self, account_key)

    def _login_target_account(self, account_key):
        self.log_info(self.tr('Logging in: {account}').format(
            account=display_account(account_key)))
        RoutineTask._login_target_account(self, account_key)

    # -------------------------------------------------------- server / client
    def _apply_server(self, server):
        """Make ``configured_client_path`` resolve to the requested server."""
        from config import set_server_override
        if server == SERVER_FOLLOW:
            return
        set_server_override(server)
        self.current_server = server

    def _start_client(self, server):
        """Start the requested server's client, replacing a running one if needed.

        The framework normally starts a game before the task runs. When the flow
        asks for a different server (or restarts after an exit), kill the old
        client, refresh the device list so it reads disconnected, then launch.
        """
        from config import configured_client_path, set_server_override
        from Mon import og

        if server in (SERVER_FOLLOW, None, ''):
            server = SERVER_CN
        set_server_override(server)
        self.current_server = server

        device_manager = getattr(og, 'device_manager', None)
        if device_manager is None:
            raise RuntimeError(self.tr('Device manager is not ready; cannot start the client.'))

        target_path = configured_client_path(server)
        hwnd_window = getattr(device_manager, 'hwnd_window', None)
        running_path = getattr(hwnd_window, 'exe_full_path', None) if hwnd_window else None

        # With a configured path we can tell whether the running client is the
        # requested server and replace it if not. Without one, the framework
        # falls back to auto-detection (single-client setups); just ensure a
        # client is running and let it resolve the exe itself.
        same_client = False
        if target_path:
            same_client = bool(
                running_path
                and os.path.normcase(os.path.abspath(running_path))
                == os.path.normcase(os.path.abspath(target_path))
            )
            if not same_client:
                self.log_info(self.tr('Starting {server} client: {path}').format(
                    server=server, path=target_path))
                device_manager.stop_hwnd()
                self.sleep(3)
                device_manager.do_refresh(True)
            else:
                # The requested client is already running. Reusing it avoids
                # a needless restart; the following login step detects whether
                # it is on the login screen or inside the game world.
                self.log_info(self.tr('{server} client is already running; reusing it.').format(
                    server=server))
        elif running_path:
            self.log_warning(self.tr(
                'No client path configured for {server}; the already-running client may be a different server. '
                'Set "{path_key}" in Basic Options to switch servers reliably.'
            ).format(server=server, path_key='Global Client Path' if server == SERVER_GLOBAL
                     else 'CN Client Path'))
        else:
            self.log_info(self.tr('No client path configured for {server}; using auto-detection.').format(
                server=server))

        if not same_client:
            try:
                self.start_device()
            except Exception as error:
                raise RuntimeError(self.tr('Failed to start the {server} client: {error}').format(
                    server=server, error=error)) from error
        self._wait_for_login_or_main()

    def _wait_for_login_or_main(self, time_out=180):
        """After a client launch, wait until a login screen or game world appears."""
        def ready():
            texts = self.ocr()
            if self._login_combo() or self.find_boxes(texts, LOGIN_TEXTS):
                return True
            if self.in_team_and_world():
                self.logged_in = True
                return True
            return False

        if not self.wait_until(ready, time_out=time_out, settle_time=2, raise_if_not_found=False):
            raise WaitFailedException(self.tr('Timed out waiting for the client login screen or game world.'))
        self.sleep(1)

    def _stop_client(self):
        from Mon import og
        device_manager = getattr(og, 'device_manager', None)
        if device_manager is None:
            return
        hwnd_window = getattr(device_manager, 'hwnd_window', None)
        if not hwnd_window:
            self.log_info(self.tr('No running client to exit.'))
            return
        self.log_info(self.tr('Exiting current client'))
        device_manager.stop_hwnd()
        self.sleep(3)
        device_manager.do_refresh(True)
        self.logged_in = False
        self.current_server = None

    # ------------------------------------------------------------------ run
    def run(self):
        WWOneTimeTask.run(self)
        from src.schedule_runtime import scheduled_plan

        store = FlowStepStore(self.executor.config_folder)
        flows = store.load()
        scheduled = scheduled_plan()
        self.flow_name = scheduled if scheduled and scheduled in flows \
            else self.config.get(FLOW_SELECTOR, DEFAULT_FLOW_NAME)
        if self.flow_name not in flows:
            self.flow_name = DEFAULT_FLOW_NAME

        steps = normalize_items(flows[self.flow_name])
        selected = [step for step in steps if step['enabled']]
        if not selected:
            self.log_info(self.tr('任务流没有启用的步骤，无需执行。'), notify=True)
            return

        self.info_set('任务流', self.flow_name)
        self.results = []
        total = len(selected)
        for index, step in enumerate(selected, 1):
            step_id = step['id']
            params = step['params']
            label = self._step_label(step_id, params)
            self.info_set('当前步骤', f'{index}/{total} · {label}')
            try:
                self._run_step(step_id, params)
            except TaskDisabledException:
                raise
            except Exception as error:
                self.results.append((label, 'failed', str(error)))
                self.log_error(self.tr('任务流步骤失败：{step}').format(step=label), error)
                self.screenshot(f'custom_flow_fail_{step_id}')
                self.log_warning(self.tr('Continuing with the next step after a failure.'))
            else:
                self.results.append((label, 'success', ''))

        self.info_set('任务流结果', self.results)
        succeeded = sum(status == 'success' for _, status, _ in self.results)
        self.log_info(self.tr('任务流完成：{success}/{total}').format(
            success=succeeded, total=total), notify=True)

    def _step_label(self, step_id, params):
        entry = ENTRIES_BY_ID.get(step_id)
        entry_name = self.tr(entry.name) if entry else step_id
        pairs = describe_params(entry, params) if entry else []
        detail = ' · '.join(pairs)
        return f'{entry_name}（{detail}）' if detail else entry_name

    def _run_step(self, step_id, params):
        self.log_info(self.tr('执行步骤：{params}').format(params=params))
        if step_id == 'start_game':
            self._start_client(params.get('服务器', SERVER_CN))
        elif step_id in ('login', 'switch_account'):
            self._login_step(params, from_game=step_id == 'switch_account')
        elif step_id == 'run_task':
            self._run_task_step(params)
        elif step_id == 'wait_stamina':
            self._wait_stamina_step(params)
        elif step_id == 'exit_game':
            self._stop_client()
        elif step_id == 'exit_app':
            self._exit_app_step()
        else:
            raise ValueError(self.tr('未知任务流步骤：{step}').format(step=step_id))

    def _wait_stamina_step(self, params):
        """Wait for natural stamina recovery, capped by today's 240 allowance.

        The ledger is keyed by account so multi-account flows do not share a
        budget. A step never waits past 04:00 reset because the ledger rolls
        over by game-day, and never waits more than 240 natural stamina in a
        single game-day.
        """
        from src.task.routine.StaminaBudget import (
            DAILY_STAMINA_CAP, StaminaWaitLedger, plan_stamina_wait,
        )

        account = self._current_account_key()
        target = max(0, int(params.get('目标体力', 160) or 0))
        need = max(0, int(params.get('单次消耗', 60) or 0))
        ledger = StaminaWaitLedger(self.executor.config_folder)

        current, _, _ = self._read_stamina_reliable()
        if current < 0:
            self.log_warning(self.tr('Cannot read stamina; skip the wait step.'))
            return
        remaining = ledger.remaining(account=account)
        should_wait, effective_target = plan_stamina_wait(
            current, min(target, DAILY_STAMINA_CAP), remaining, need=need)
        if not should_wait:
            self.log_info(self.tr(
                'Stamina wait skipped: current {current}, target {target}, today left {left}.'
            ).format(current=current, target=target, left=remaining))
            return

        gained = max(0, int(effective_target - current))
        self.log_info(self.tr(
            'Waiting for stamina {current} -> {target} (today budget left {left}).'
        ).format(current=current, target=effective_target, left=remaining))

        # Natural recovery is ~1 per 4 min; cap the wall-clock wait to avoid
        # an all-night stall if the OCR or the game misbehaves.
        time_out = min(max(gained * self.STAMINA_RECOVER_SECONDS, 60), self.STAMINA_WAIT_HARD_CAP)

        def reached():
            value, _, _ = self._read_stamina_reliable()
            return value >= effective_target

        recovered = self.wait_until(reached, time_out=time_out, settle_time=0,
                                    raise_if_not_found=False)
        if recovered:
            ledger.add(gained, account=account)
            self.log_info(self.tr('Stamina wait finished at {target}.').format(
                target=effective_target))
        else:
            # Only credit what actually recovered, so a timeout cannot burn
            # the whole daily allowance.
            after, _, _ = self._read_stamina_reliable()
            actual = max(0, int(after - current)) if after > 0 else 0
            if actual:
                ledger.add(actual, account=account)
            self.log_warning(self.tr('Stamina wait timed out; continue with remaining steps.'))

    def _current_account_key(self):
        """Best-effort account key for per-account stamina budgeting."""
        try:
            from src import schedule_runtime
            account = schedule_runtime.scheduled_account()
            if account:
                return str(account).strip()
        except Exception:
            pass
        try:
            return str(self.config.get('Account') or self.info.get('account') or '').strip()
        except Exception:
            return ''

    def _exit_app_step(self):
        """Close the game and quit the app without waiting for flow completion.

        This is an explicit step, so the user can place it exactly where the
        flow should end instead of relying on the global Exit After Task flag.
        """
        from Mon.core.events import communicate

        self._stop_client()
        self.log_info(self.tr('Exiting Monster-ww'))
        communicate.quit.emit()

    def _game_state(self):
        """Detect whether the client sits on the login screen or in the world.

        A flow may start on an already-running client that auto-logged into the
        previous account. Both login and switch-account steps must cope with
        that state instead of assuming a freshly launched client shows login.
        """
        try:
            texts = self.ocr()
            if self._login_combo() or self.find_boxes(texts, LOGIN_TEXTS):
                return 'login'
            if self.in_team_and_world():
                return 'world'
        except Exception as error:
            logger.warning('game state detection failed: %s', error)
        return 'unknown'

    def _login_step(self, params, from_game=False):
        server = params.get('服务器', SERVER_FOLLOW)
        if server in (SERVER_CN, SERVER_GLOBAL):
            self._apply_server(server)

        account_key = resolve_flow_account(params)
        if not account_key:
            raise ValueError(self.tr('登录步骤未指定账号；请选择已有账号或手动填写账号 key。'))

        state = self._game_state()
        self.log_info(self.tr('Login step detected game state: {state}').format(state=state))
        if state == 'world':
            # Already inside the game with the previous account: go back to the
            # login screen, then pick the requested account explicitly.
            self.logged_in = False
            self.ensure_main(time_out=180)
            self._switch_to_login()
        elif state == 'unknown':
            # Not sure which screen we are on. Rely on the shared helper to
            # bring us to a known state (it tolerates already being at login).
            if from_game:
                self.logged_in = False
                self.ensure_main(time_out=180)
                self._switch_to_login()
        # state == 'login': select the account directly.
        self._login_target_account(account_key)

    def _run_task_step(self, params):
        task_name = params.get('任务', ROUTINE_TASK_NAME)
        reference = task_reference(task_name)
        if not reference:
            raise ValueError(self.tr('未知任务：{task}').format(task=task_name))
        module_name, class_name = reference
        module = importlib.import_module(module_name)
        task_class = getattr(module, class_name)
        task = self.executor.get_task_by_class(task_class)
        if task is None:
            raise ValueError(self.tr('任务未启用或未注册：{task}').format(task=task_name))

        with _no_scheduled_account(), isolated_task_configs(self.executor.get_all_tasks()):
            if class_name == 'RoutineTask':
                # The routine plan is stored under RoutineTask's own selector,
                # not the flow selector. Prefer the step's explicit plan.
                from src.task.routine.RoutinePlans import PLAN_SELECTOR, DEFAULT_PLAN_NAME
                plan = params.get(ROUTINE_PLAN_KEY) or task.config.get(PLAN_SELECTOR, DEFAULT_PLAN_NAME)
                plans = routine_plan_options(self.executor.config_folder)
                if plan not in plans:
                    plan = DEFAULT_PLAN_NAME
                task.run_selected_plan(plan)
            else:
                self.run_task_by_class(task_class)
        self.ensure_main(time_out=180)
