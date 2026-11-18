"""Declarative daily routine orchestrator.

Users edit an ordered plan in ``configs/RoutinePlans.json``; this task walks the
list, injects each item's parameters into the existing task objects and reuses
their battle/navigation logic. A failing item is isolated so later items still
run. Multi-account users simply pick a different plan name per account.
"""

from copy import deepcopy

from Mon import Logger, TaskDisabledException

from src.task.BaseCombatTask import BaseCombatTask
from src.task.DailyTask import DailyTask
from src.task.FarmEchoTask import FarmEchoTask
from src.task.ForgeryTask import ForgeryTask
from src.task.SimulationTask import SimulationTask
from src.task.TacetTask import TacetTask
from src.task.WWOneTimeTask import WWOneTimeTask
from src.task.account.RuntimeTaskConfig import account_task_config
from src.task.routine import RoutineCatalog as catalog
from src.task.routine.RoutinePlans import (
    DEFAULT_PLAN_NAME, EDIT_PLANS, PLAN_SELECTOR, RoutinePlanStore, normalize_items,
)
from src.task.task_icons import task_icon

logger = Logger.get_logger(__name__)


class RoutineTask(WWOneTimeTask, BaseCombatTask):
    """Run a user-defined sequence of daily/stamina tasks."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = '🧩 Daily Routine Pipeline'
        self.icon = task_icon('daily')
        self.group_name = 'Daily Activities'
        self.group_icon = task_icon('category-daily')
        self.description = 'Run a fully customizable daily pipeline. Each account may select its own plan.'
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
            'text': 'Configure Routine',
            'callback': self.configure_plans,
        }
        self.add_exit_after_config()
        self.plan_name = DEFAULT_PLAN_NAME
        self.results = []

    # ------------------------------------------------------------------ GUI
    def configure_plans(self):
        from PySide6.QtWidgets import QApplication
        from src.gui.RoutinePlanDialog import RoutinePlanDialog

        dialog = RoutinePlanDialog(QApplication.activeWindow(), self)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

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
            self.log_info(self.tr('Routine plan has no enabled item; nothing to do.'), notify=True)
            return

        self.ensure_main(time_out=180)
        for item in selected:
            entry_id = item['id']
            self.info_set('当前流水线步骤', entry_id)
            try:
                self._run_entry(entry_id, item['params'])
            except TaskDisabledException:
                raise
            except Exception as error:
                self.results.append((entry_id, 'failed', str(error)))
                self.log_error(self.tr('Routine item failed: {id}').format(id=entry_id), error)
                self.screenshot(f'routine_fail_{entry_id}')
                self.ensure_main(time_out=180)
            else:
                self.results.append((entry_id, 'success', ''))
        self.info_set('流水线结果', self.results)
        self.log_info(self.tr('Daily routine finished: {success}/{total}').format(
            success=sum(status == 'success' for _, status, _ in self.results),
            total=len(self.results)), notify=True)

    def _run_entry(self, entry_id, params):
        params = deepcopy(params)
        self.log_info(self.tr('Run routine item: {id} {params}').format(id=entry_id, params=params))
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
        else:
            raise ValueError(self.tr('Unknown routine item: {id}').format(id=entry_id))
        self.ensure_main()

    def _farm_echo(self, config):
        task = self.get_task_by_class(FarmEchoTask)
        self.log_info(self.tr('Farm echo with: {config}').format(config=config))
        with account_task_config(task, config):
            task.run()
