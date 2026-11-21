"""Declarative daily routine orchestrator.

Users edit an ordered plan in ``configs/RoutinePlans.json``; this task walks the
list, injects each item's parameters into the existing task objects and reuses
their battle/navigation logic. A failing item is isolated so later items still
run. Multi-account users simply pick a different plan name per account.
"""

from copy import deepcopy
from datetime import date

import win32api

from Mon import Logger, PostMessageInteraction, TaskDisabledException
from Mon.task.exceptions import WaitFailedException

from src.task.BaseWWTask import LOGIN_TEXTS
from src.task.BaseCombatTask import BaseCombatTask
from src.task.DailyTask import DailyTask
from src.task.FarmEchoTask import FarmEchoTask
from src.task.ForgeryTask import ForgeryTask
from src.task.SimulationTask import SimulationTask
from src.task.TacetTask import TacetTask
from src.task.TrainingGuideTask import (
    BUDGET_KEY as GUIDE_BUDGET_KEY,
    ROLE_KEY as GUIDE_ROLE_KEY,
    CATEGORY_KEYS as GUIDE_CATEGORY_KEYS,
    TrainingGuideTask,
)
from src.task.MouseResetTask import MouseResetTask
from src.task.MultiAccountDailyTask import (
    combo_items, combo_selected_item, find_login_combo, select_combo_item,
)
from src.task.WWOneTimeTask import WWOneTimeTask
from src.task.account.MultiAccountConfigStore import account_key_from_ocr, display_account
from src.task.account.RuntimeTaskConfig import account_task_config
from src.task.routine import RoutineCatalog as catalog
from src.task.routine.RoutinePlans import (
    DEFAULT_PLAN_NAME, EDIT_PLANS, PLAN_SELECTOR, RoutinePlanAccountStore, RoutinePlanStore,
    is_scheduled_today, normalize_items,
)
from src.task.task_icons import task_icon

logger = Logger.get_logger(__name__)

WAIT_STAMINA_KEY = '等待体力恢复'
STAMINA_GAP_KEY = '体力等待缺口上限'


class RoutineTask(WWOneTimeTask, BaseCombatTask):
    """Run a user-defined sequence of daily/stamina tasks."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = '🧩 日常一条龙'
        self.icon = task_icon('daily')
        self.group_name = '日常活动'
        self.group_icon = task_icon('category-daily')
        self.description = '高度自定义的日常流水线，每个账号可选择自己的计划。'
        self.support_schedule_task = True
        self.default_config[PLAN_SELECTOR] = DEFAULT_PLAN_NAME
        self.config_description[PLAN_SELECTOR] = 'Plan name saved in configs/RoutinePlans.json.'
        self.config_type[PLAN_SELECTOR] = {
            'type': 'drop_down',
            'options': list(RoutinePlanStore('configs').load()),
        }
        self.default_config[EDIT_PLANS] = False
        self.config_type[EDIT_PLANS] = {
            'type': 'button',
            'text': '配置流水线',
            'callback': self.configure_plans,
        }
        # 体力不足时可等待自然恢复，尽量耗尽当天体力；次数为 0 的副本等待后补刷一轮即结束。
        self.default_config[WAIT_STAMINA_KEY] = False
        self.config_description[WAIT_STAMINA_KEY] = (
            'Wait for small natural stamina shortfalls to use up today\'s stamina. '
            'For unlimited (count=0) domains, one more run is done after the wait and then the domain ends.')
        self.default_config[STAMINA_GAP_KEY] = 10
        self.config_description[STAMINA_GAP_KEY] = (
            'Only wait when current stamina is short by at most this many points; '
            'a larger shortfall is skipped to avoid long waits.')
        self.add_exit_after_config()
        self.plan_name = DEFAULT_PLAN_NAME
        self.results = []

    # ------------------------------------------------------------------ GUI
    def configure_plans(self, on_saved=None):
        from PySide6.QtWidgets import QApplication
        from src.gui.RoutinePlanDialog import RoutinePlanDialog

        dialog = RoutinePlanDialog(QApplication.activeWindow(), self)
        try:
            saved = dialog.exec()
        finally:
            dialog.deleteLater()
        if not saved:
            return
        if callable(on_saved):
            on_saved()
        else:
            # 任务设置页的「配置流水线」按钮会把 clicked 的 bool 传进来，
            # on_saved 因此不可调用。此时主动刷新自定义页的下拉框，避免
            # 删除/新建计划后外层仍显示旧列表。
            self._refresh_routine_tabs()

    @staticmethod
    def _refresh_routine_tabs():
        from PySide6.QtWidgets import QApplication
        try:
            from src.gui.RoutineTab import RoutineTab
        except ImportError:
            return
        for widget in QApplication.allWidgets():
            if isinstance(widget, RoutineTab) and hasattr(widget, '_render'):
                widget._render()

    def configure_training_guide(self, on_saved=None):
        """Open the training-guide planner (material shortage -> runs)."""
        from PySide6.QtWidgets import QApplication
        from src.gui.TrainingGuideDialog import TrainingGuideDialog

        dialog = TrainingGuideDialog(QApplication.activeWindow(), self)
        try:
            saved = dialog.exec()
        finally:
            dialog.deleteLater()
        if saved and callable(on_saved):
            on_saved()

    def plan_options(self):
        return list(RoutinePlanStore(self.executor.config_folder).load())

    # ------------------------------------------------- optional account login
    # These mirror MultiAccountConfigTask's login helpers but stay local so the
    # routine task never depends on the multi-account task's private state.

    def click(self, x=-1, y=-1, *args, **kwargs):
        # Native login ComboBox needs a real cursor hover before background clicks.
        if (getattr(self, '_login_cursor_active', False)
                and isinstance(x, (int, float)) and isinstance(y, (int, float))
                and x >= 1 and y >= 1
                and isinstance(self.executor.interaction, PostMessageInteraction)):
            position = self.executor.interaction.capture.get_abs_cords(x, y)
            win32api.SetCursorPos((int(position[0]), int(position[1])))
            self.sleep(0.2)
        return super().click(x, y, *args, **kwargs)

    def _login_combo(self):
        return find_login_combo(self.hwnd.hwnd if self.hwnd else 0)

    def _switch_to_login(self):
        self.log_info(self.tr('Switching back to login screen'))
        self.send_key('esc', after_sleep=1.5)
        self.wait_feature('esc_setting')
        self.click_relative(0.04, 0.96, after_sleep=1)
        self.click_confirm(timeout=10)
        self.wait_until(lambda: self._login_combo() or self._at_login_screen(),
                        time_out=60, settle_time=2, raise_if_not_found=True)
        self.log_info(self.tr('Back at login screen'))

    def _at_login_screen(self):
        texts = self.ocr()
        if not self.find_boxes(texts, LOGIN_TEXTS):
            return None
        boxes = [box for box in texts if '*' in box.name or '@' in box.name]
        return max(boxes, key=lambda box: box.y + box.height / 2) if boxes else None

    def _select_target_account(self, account_key):
        combo = self._login_combo()
        if combo:
            items = combo_items(combo)
            matches = [index for index, text in enumerate(items)
                       if account_key_from_ocr(text) == account_key]
            if matches and select_combo_item(combo, matches[-1]):
                self.sleep(1)
                if account_key_from_ocr(combo_selected_item(combo)) == account_key:
                    return
            raise WaitFailedException(
                self.tr('Account {account} not found in the login list').format(
                    account=display_account(account_key)))
        # OCR fallback for accounts that do not expose a native ComboBox.
        previous = getattr(self, '_ocr_account_selection', False)
        try:
            self._ocr_account_selection = True
            for attempt in range(5):
                self.sleep(1)
                box = self._at_login_screen()
                if box is None:
                    continue
                self.click(box, after_sleep=2)
                self.sleep(1)
                matches = [b for b in self.ocr()
                           if account_key_from_ocr(b.name) == account_key]
                if matches:
                    self.click(max(matches, key=lambda b: b.y + b.height / 2), after_sleep=2)
                    self.sleep(1)
                    return
            raise WaitFailedException(
                self.tr('Account {account} not found in the login list').format(
                    account=display_account(account_key)))
        finally:
            self._ocr_account_selection = previous

    def _login_target_account(self, account_key):
        was_enabled = False
        mouse_reset = self.executor.get_task_by_class(MouseResetTask)
        if mouse_reset:
            was_enabled = mouse_reset.enabled
            if was_enabled:
                mouse_reset.disable()
        try:
            self._select_target_account(account_key)
            previous = getattr(self, '_login_cursor_active', False)
            try:
                self._login_cursor_active = True
                self.sleep(4)
                login = self.find_boxes(self.ocr(), boundary=self.box_of_screen(0.3, 0.3, 0.7, 0.8),
                                        match=LOGIN_TEXTS)
                if login:
                    self.click(login, after_sleep=3)
                else:
                    self.click_relative(0.5, 0.568, hcenter=True, vcenter=True, after_sleep=3)
                self.logged_in = False
                self.ensure_main(time_out=180)
                self.log_info(self.tr('{account} logged in successfully').format(
                    account=display_account(account_key)))
            finally:
                self._login_cursor_active = previous
        finally:
            if was_enabled and mouse_reset:
                mouse_reset.enable()

    # ------------------------------------------------------------------ run
    def run(self):
        WWOneTimeTask.run(self)
        self.results = []
        store = RoutinePlanStore(self.executor.config_folder)
        plans = store.load()
        self.plan_name = self.config.get(PLAN_SELECTOR, DEFAULT_PLAN_NAME)
        items = normalize_items(plans.get(self.plan_name, plans[DEFAULT_PLAN_NAME]))
        selected = [item for item in items if item['enabled']]
        if not selected:
            self.log_info(self.tr('流水线没有启用的步骤，无需执行。'), notify=True)
            return

        # Optional per-plan account: log out and log into the configured account
        # before running. No binding means the current account is used as before.
        account_key = RoutinePlanAccountStore(self.executor.config_folder).get(self.plan_name)
        if account_key:
            self.info_set('执行账号', display_account(account_key))
            self._switch_to_login()
            self._login_target_account(account_key)

        self.ensure_main(time_out=180)
        today = date.today()
        for item in selected:
            entry_id = item['id']
            if not is_scheduled_today(item['params'], today):
                self.log_info(self.tr('步骤「{id}」今日非运行日，跳过。').format(id=entry_id))
                self.results.append((entry_id, 'skipped', ''))
                continue
            self.info_set('当前流水线步骤', entry_id)
            try:
                self._run_entry(entry_id, item['params'])
            except TaskDisabledException:
                raise
            except Exception as error:
                self.results.append((entry_id, 'failed', str(error)))
                self.log_error(self.tr('流水线步骤失败：{id}').format(id=entry_id), error)
                self.screenshot(f'routine_fail_{entry_id}')
                self.ensure_main(time_out=180)
            else:
                self.results.append((entry_id, 'success', ''))
        self.info_set('流水线结果', self.results)
        acted = [row for row in self.results if row[1] != 'skipped']
        self.log_info(self.tr('日常流水线完成：{success}/{total}，跳过 {skipped} 项。').format(
            success=sum(status == 'success' for _, status, _ in acted),
            total=len(acted),
            skipped=sum(status == 'skipped' for _, status, _ in self.results)), notify=True)

    def _configure_stamina_wait(self, task, max_runs):
        """Tell a domain task whether to wait for stamina recovery when short.

        Finite counts (max_runs>0) wait so the configured runs can finish.
        Unlimited counts (max_runs=0) also wait, but only for a single run: after
        recovery one run is performed and the domain ends, so an idle account
        still finishes with stamina instead of burning it all immediately.
        """
        enabled = bool(self.config.get(WAIT_STAMINA_KEY, False))
        task._routine_wait_stamina = enabled
        task._routine_stamina_gap = int(self.config.get(STAMINA_GAP_KEY, 10) or 0)
        task._routine_stamina_single_run = enabled and max_runs <= 0

    def _run_entry(self, entry_id, params):
        params = deepcopy(params)
        self.log_info(self.tr('执行流水线步骤：{id} {params}').format(id=entry_id, params=params))
        if entry_id == 'claim_daily':
            daily = self.get_task_by_class(DailyTask)
            daily.claim_daily()
            daily.claim_mail()
        elif entry_id == 'battle_pass':
            self.get_task_by_class(DailyTask).claim_battle_pass()
        elif entry_id == 'weekly_garden':
            self.get_task_by_class(DailyTask).check_weekly_garden()
        elif entry_id == 'simulation':
            task = self.get_task_by_class(SimulationTask)
            max_runs = int(params.get('次数', 0) or 0)
            self._configure_stamina_wait(task, max_runs)
            with account_task_config(task, {
                    'Region Selection': params.get('地区', catalog.simulation_regions()[0]),
                    'Material Selection': params.get('材料', 'Shell Credit'),
            }):
                task.farm_simulation(max_runs=max_runs)
        elif entry_id == 'forgery':
            task = self.get_task_by_class(ForgeryTask)
            max_runs = int(params.get('次数', 0) or 0)
            self._configure_stamina_wait(task, max_runs)
            serial = catalog.forgery_serial(params.get('地区', catalog.forgery_region_names()[0]),
                                            params.get('武器类型', catalog.forgery_weapon_types()[0]))
            with account_task_config(task, {'Which Forgery Challenge to Farm': serial}):
                task.farm_forgery(max_runs=max_runs)
        elif entry_id == 'tacet':
            task = self.get_task_by_class(TacetTask)
            max_runs = int(params.get('次数', 0) or 0)
            self._configure_stamina_wait(task, max_runs)
            serial = catalog.tacet_serial(params.get('无音区', catalog.tacet_zones()[0]))
            with account_task_config(task, {'Which Tacet Suppression to Farm': serial}):
                task.farm_tacet(max_runs=max_runs)
        elif entry_id == 'boss_challenge':
            boss = params.get('BOSS', catalog.boss_challenges()[0])
            self._farm_echo({
                'Teleport to Boss': 'Boss Challenge',
                'Which Boss Challenge to Teleport': catalog.boss_challenge_serial(boss),
                'Boss': catalog.boss_challenge_profile(boss),
                'Repeat Farm Count': max(1, int(params.get('次数', 1) or 1)),
            })
        elif entry_id == 'weekly_boss':
            boss = params.get('周本', catalog.weekly_display_names()[0])
            self._farm_echo({
                'Teleport to Boss': 'Weekly Challenge',
                'Which Weekly Boss to Teleport': catalog.weekly_serial(boss),
                'Boss': 'Other',
                'Repeat Farm Count': min(3, max(1, int(params.get('次数', 3) or 3))),
            })
        elif entry_id == 'training_guide':
            guide = self.get_task_by_class(TrainingGuideTask)
            guide_config = {
                GUIDE_ROLE_KEY: params.get('目标角色', ''),
                GUIDE_BUDGET_KEY: int(params.get('体力预算', 240) or 0),
            }
            for domain, key in GUIDE_CATEGORY_KEYS.items():
                default = domain != 'weekly_boss'
                guide_config[key] = bool(params.get(key, default))
            with account_task_config(guide, guide_config):
                guide.run()
        else:
            raise ValueError(self.tr('未知流水线步骤：{id}').format(id=entry_id))
        self.ensure_main()

    def _farm_echo(self, config):
        task = self.get_task_by_class(FarmEchoTask)
        self.log_info(self.tr('刷取声骸，配置：{config}').format(config=config))
        with account_task_config(task, config):
            task.run()
