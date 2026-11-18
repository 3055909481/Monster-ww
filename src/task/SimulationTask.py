import re

from Mon import Logger
from src.task.DomainTask import DomainTask
from src.task.task_icons import task_icon

logger = Logger.get_logger(__name__)


class SimulationTask(DomainTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = '🧪 Simulation Challenge'
        self.icon = task_icon('simulation')
        self.group_name = 'Daily Activities'
        self.group_icon = task_icon('category-daily')
        self.description = 'Farms the selected Simulation Challenge. Must be able to teleport (F2).'
        self.support_schedule_task = True
        self.default_config = {
            'Region Selection': '瑾珑·梦州',
            'Material Selection': 'Shell Credit',
        }
        material_option_list = ['Resonator EXP', 'Weapon EXP', 'Shell Credit']
        region_option_list = ['瑾珑·梦州', '索拉里斯之极·拉海洛', '黎那汐塔', '瑾珑·今州']
        self.config_type['Material Selection'] = {'type': 'drop_down', 'options': material_option_list}
        self.config_type['Region Selection'] = {'type': 'drop_down', 'options': region_option_list}
        self.config_description = {
            'Region Selection': '瑾珑·梦州 / 索拉里斯之极·拉海洛 / 黎那汐塔 / 瑾珑·今州',
            'Material Selection': 'Resonator EXP / Weapon EXP / Shell Credit',
        }
        self.stamina_once = 40
        # F2 simulation book: 4 regions x 3 materials, grouped by region.
        self.structure = [3, 3, 3, 3]
        self.total_number = sum(self.structure)

    def run(self):
        super().run()
        self.make_sure_in_world()
        self.farm_simulation()

    def farm_simulation(self, daily=False, used_stamina=0, config=None, max_runs=0):
        if daily:
            must_use = 180 - used_stamina
        else:
            must_use = 0
        if config is None:
            config = self.config
        selection = config.get('Material Selection', 'Shell Credit')
        region = config.get('Region Selection', '瑾珑·梦州')

        def teleport_once():
            self.teleport_into_domain(selection, region)

        self.farm_domain_with_recovery_loop(must_use, teleport_once, max_runs=max_runs)

    def teleport_into_domain(self, selection, region='瑾珑·梦州'):
        from src.task.routine import RoutineCatalog as catalog

        serial = catalog.simulation_serial(region, selection)
        self.open_boss_book('moni')
        self.info_set('Target Simulation Challenge', f'{region} / {selection} #{serial - 1}')
        if serial > self.total_number:
            raise IndexError(self.tr('Index out of range, max is {total_number}').format(total_number=self.total_number))
        self.click_on_book_target(serial, self.total_number, self.structure)
        self.click(0.891, 0.910, after_sleep=1)
        self.click_team_challenge()
        self.wait_in_team_and_world(time_out=self.teleport_timeout)
