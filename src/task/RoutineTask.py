"""Declarative daily routine orchestrator.

Users edit an ordered plan in ``configs/RoutinePlans.json``; this task walks the
list, injects each item's parameters into the existing task objects and reuses
their battle/navigation logic. A failing item is isolated so later items still
run. Multi-account users simply pick a different plan name per account.
"""

from copy import deepcopy
from datetime import date

from Mon import Logger, TaskDisabledException

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
from src.task.WWOneTimeTask import WWOneTimeTask
from src.task.account.RuntimeTaskConfig import account_task_config
from src.task.routine import RoutineCatalog as catalog
from src.task.routine.RoutinePlans import (
    DEFAULT_PLAN_NAME, EDIT_PLANS, PLAN_SELECTOR, RoutinePlanStore,
    is_scheduled_today, normalize_items,
)
from src.task.task_icons import task_icon

logger = Logger.get_logger(__name__)


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
        if saved and callable(on_saved):
            on_saved()

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
            with account_task_config(task, {
                    'Region Selection': params.get('地区', catalog.simulation_regions()[0]),
                    'Material Selection': params.get('材料', 'Shell Credit'),
            }):
                task.farm_simulation(max_runs=int(params.get('次数', 0) or 0))
        elif entry_id == 'forgery':
            task = self.get_task_by_class(ForgeryTask)
            serial = catalog.forgery_serial(params.get('地区', catalog.forgery_region_names()[0]),
                                            params.get('武器类型', catalog.forgery_weapon_types()[0]))
            with account_task_config(task, {'Which Forgery Challenge to Farm': serial}):
                task.farm_forgery(max_runs=int(params.get('次数', 0) or 0))
        elif entry_id == 'tacet':
            task = self.get_task_by_class(TacetTask)
            serial = catalog.tacet_serial(params.get('无音区', catalog.tacet_zones()[0]))
            with account_task_config(task, {'Which Tacet Suppression to Farm': serial}):
                task.farm_tacet(max_runs=int(params.get('次数', 0) or 0))
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
