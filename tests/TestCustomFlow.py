"""Pure-Python tests for the custom task flow registry and store.

These tests import only the dependency-free FlowSteps module so they run
without Mon, PySide6 or any compiled native extension.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.task.routine.FlowSteps import (
    ACCOUNT_SOURCE_MANUAL, ACCOUNT_SOURCE_SELECT, DEFAULT_FLOW_NAME, ENTRIES,
    ENTRIES_BY_ID, FLOW_SELECTOR, ROUTINE_PLAN_KEY, ROUTINE_TASK_NAME,
    SERVER_CN, SERVER_FOLLOW, SERVER_GLOBAL, FlowStepStore,
    default_items, describe_params, entry_config_type, new_item, normalize_items,
    normalize_flows, resolve_flow_account, task_reference,
)


class TestFlowCatalog(unittest.TestCase):
    def test_expected_steps_exist(self):
        self.assertEqual(
            [entry.task_id for entry in ENTRIES],
            ['start_game', 'login', 'switch_account', 'run_task', 'exit_game'],
        )
        self.assertEqual(set(ENTRIES_BY_ID), {entry.task_id for entry in ENTRIES})

    def test_every_entry_has_metadata(self):
        for entry in ENTRIES:
            with self.subTest(entry=entry.task_id):
                self.assertTrue(entry.name)
                self.assertTrue(entry.description)
                self.assertTrue(entry.description.endswith('。'))
                self.assertTrue(entry.icon)
                self.assertIsInstance(entry.default_config, dict)
                # Every config key must have a matching type and description.
                self.assertEqual(set(entry.config_description), set(entry.default_config))
                self.assertEqual(set(entry.config_type), set(entry.default_config))

    def test_step_icons_are_valid_kit_icons(self):
        """The editor paints entry.icon via kit_icon(); an unknown name crashes startup.

        Read the real white-list from the framework's icons module so any future
        invalid icon is caught in CI instead of in the packaged smoke test.
        """
        import re
        candidates = []
        try:
            from Mon.ui.qt.kit import icons as kit_icons
            candidates.append(Path(kit_icons.__file__))
        except ImportError:
            pass
        vendored = Path('.mon-tmp/Mon/ui/qt/kit/icons.py')
        if vendored.is_file():
            candidates.append(vendored)
        self.assertTrue(candidates, 'Cannot locate Mon kit icons module')
        source = candidates[0].read_text(encoding='utf-8')
        whitelist = set(re.findall(r'"([a-z0-9-]+)"\s*:', source))
        self.assertTrue(whitelist, 'Could not parse kit icon whitelist')
        for entry in ENTRIES:
            with self.subTest(entry=entry.task_id):
                self.assertIn(entry.icon, whitelist)

    def test_new_item_and_normalize_keep_duplicates(self):
        first = new_item('login')
        second = new_item('login')
        self.assertNotEqual(first['uid'], second['uid'])
        items = normalize_items([first, {'id': 'run_task'}, second, {'id': 'unknown'}])
        self.assertEqual([item['id'] for item in items],
                         ['login', 'run_task', 'login'])
        for item in items:
            self.assertEqual(set(item), {'id', 'uid', 'enabled', 'params'})
            self.assertTrue(item['uid'])

    def test_normalize_fills_defaults_and_drops_unknown_params(self):
        items = normalize_items([{'id': 'start_game', 'params': {
            '服务器': SERVER_GLOBAL, '不存在': 'x'}}])
        self.assertEqual(items[0]['params'], {'服务器': SERVER_GLOBAL})

    def test_normalize_enabled_defaults_true(self):
        self.assertTrue(normalize_items([{'id': 'exit_game'}])[0]['enabled'])
        self.assertFalse(normalize_items([{'id': 'exit_game', 'enabled': False}])[0]['enabled'])

    def test_default_flow_covers_two_servers(self):
        items = default_items()
        starts = [item['params']['服务器'] for item in items if item['id'] == 'start_game']
        self.assertEqual(starts, [SERVER_CN, SERVER_GLOBAL])


class TestAccountAndTaskResolution(unittest.TestCase):
    def test_manual_account_wins(self):
        params = {'账号来源': ACCOUNT_SOURCE_MANUAL,
                  '选择账号': '1317520', '手动账号': ' abc@outlook.com '}
        self.assertEqual(resolve_flow_account(params), 'abc@outlook.com')

    def test_selected_account(self):
        params = {'账号来源': ACCOUNT_SOURCE_SELECT,
                  '选择账号': ' 1317520 ', '手动账号': 'ignored'}
        self.assertEqual(resolve_flow_account(params), '1317520')

    def test_empty_account(self):
        self.assertEqual(resolve_flow_account({}), '')
        self.assertEqual(resolve_flow_account(None), '')

    def test_legacy_account_key(self):
        self.assertEqual(resolve_flow_account({'账号': '999'}), '999')

    def test_task_reference(self):
        self.assertEqual(task_reference(ROUTINE_TASK_NAME),
                         ('src.task.RoutineTask', 'RoutineTask'))
        self.assertEqual(task_reference('梦幻游园（周常乐园）')[1], 'GardenTask')
        self.assertIsNone(task_reference('不存在'))


class TestDynamicConfigType(unittest.TestCase):
    def test_login_options_filled_from_accounts(self):
        config_type = entry_config_type('login', accounts=['1317520', 'abc@outlook.com'])
        self.assertEqual(config_type['选择账号']['type'], 'drop_down')
        self.assertEqual(config_type['选择账号']['options'],
                         ['1317520', 'abc@outlook.com'])

    def test_login_without_accounts_stays_editable(self):
        config_type = entry_config_type('login', accounts=[])
        self.assertEqual(config_type['选择账号']['type'], 'line_edit')

    def test_run_task_plan_options(self):
        config_type = entry_config_type('run_task', plans=['默认', '国际默认'])
        self.assertEqual(config_type[ROUTINE_PLAN_KEY]['type'], 'drop_down')
        self.assertEqual(config_type[ROUTINE_PLAN_KEY]['options'], ['默认', '国际默认'])

    def test_unknown_step_returns_empty(self):
        self.assertEqual(entry_config_type('nope'), {})

    def test_base_registry_has_no_dynamic_options(self):
        # The static registry must stay pure: no hardcoded account/task lists.
        self.assertEqual(ENTRIES_BY_ID['run_task'].config_type[ROUTINE_PLAN_KEY],
                         {'type': 'line_edit'})
        self.assertEqual(ENTRIES_BY_ID['login'].config_type['选择账号'],
                         {'type': 'line_edit'})


class TestDescribeParams(unittest.TestCase):
    def test_readable_pairs(self):
        entry = ENTRIES_BY_ID['start_game']
        self.assertEqual(describe_params(entry, {'服务器': SERVER_GLOBAL}),
                         ['服务器 国际服'])

    def test_hides_follow_and_empty(self):
        entry = ENTRIES_BY_ID['login']
        pairs = describe_params(entry, {'账号来源': ACCOUNT_SOURCE_SELECT,
                                        '选择账号': '1317520', '服务器': SERVER_FOLLOW})
        self.assertIn('选择账号 1317520', pairs)
        self.assertFalse(any('跟随全局' in pair for pair in pairs))


class TestFlowStore(unittest.TestCase):
    def test_load_creates_default(self):
        with tempfile.TemporaryDirectory() as folder:
            flows = FlowStepStore(folder).load()
            self.assertIn(DEFAULT_FLOW_NAME, flows)

    def test_save_and_reload(self):
        with tempfile.TemporaryDirectory() as folder:
            store = FlowStepStore(folder)
            flows = store.load()
            flows['全流程'] = default_items()
            saved = store.save(flows)
            self.assertIn('全流程', saved)
            loaded = store.load()
            self.assertEqual([item['id'] for item in loaded['全流程']],
                             [item['id'] for item in flows['全流程']])
            self.assertTrue((Path(folder) / 'CustomFlows.json').exists())

    def test_corrupt_file_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'CustomFlows.json'
            path.write_text('not json', encoding='utf-8')
            flows = FlowStepStore(folder).load()
            self.assertIn(DEFAULT_FLOW_NAME, flows)

    def test_save_writes_valid_utf8_json(self):
        with tempfile.TemporaryDirectory() as folder:
            store = FlowStepStore(folder)
            store.save({'中文流程': default_items()})
            data = json.loads((Path(folder) / 'CustomFlows.json').read_text(encoding='utf-8'))
            self.assertIn('中文流程', data)

    def test_no_temp_files_left(self):
        with tempfile.TemporaryDirectory() as folder:
            FlowStepStore(folder).save({'x': default_items()})
            leftovers = [p.name for p in Path(folder).glob('.CustomFlows-*')]
            self.assertEqual(leftovers, [])

    def test_normalize_flows_drops_empty_names(self):
        result = normalize_flows({'': default_items(), '   ': default_items(),
                                  '有效': default_items()})
        self.assertEqual(list(result), ['有效'])


class TestSourceGuards(unittest.TestCase):
    def test_task_uses_config_folder_not_hardcoded(self):
        source = Path('src/task/CustomFlowTask.py').read_text(encoding='utf-8')
        self.assertIn('FlowStepStore(self.executor.config_folder)', source)
        self.assertIn('flow_options_for(_Config.config_folder)', source)

    def test_routine_exposes_no_switch_entry(self):
        source = Path('src/task/RoutineTask.py').read_text(encoding='utf-8')
        self.assertIn('def run_selected_plan(self, plan_name):', source)
        # The shared entry must not perform its own account switch.
        start = source.index('def run_selected_plan(self, plan_name):')
        body = source[start:start + 400]
        self.assertNotIn('_login_target_account', body)

    def test_registration_wired(self):
        config_source = Path('config.py').read_text(encoding='utf-8')
        self.assertIn('["src.task.CustomFlowTask", "CustomFlowTask"]', config_source)
        self.assertIn('["src.gui.CustomFlowTab", "CustomFlowTab"]', config_source)

    def test_flow_reuses_routine_login_helpers(self):
        """The flow must delegate account selection to RoutineTask explicitly.

        CustomFlowTask does not inherit RoutineTask, so _login_target_account
        (copied from RoutineTask) needs its internal self._select_target_account
        call to resolve; verify the delegating method exists.
        """
        source = Path('src/task/CustomFlowTask.py').read_text(encoding='utf-8')
        self.assertIn('def _select_target_account(self, account_key):', source)
        self.assertIn('RoutineTask._select_target_account(self, account_key)', source)
        self.assertIn('RoutineTask._login_target_account(self, account_key)', source)

    def test_flow_uses_config_folder_for_all_stores(self):
        source = Path('src/task/CustomFlowTask.py').read_text(encoding='utf-8')
        self.assertIn('FlowStepStore(self.executor.config_folder)', source)
        self.assertIn('MultiAccountConfigStore(self.executor.config_folder)', source)
        self.assertNotIn("FlowStepStore('configs')", source)

    def test_schedule_patch_supports_custom_flow(self):
        source = Path('src/schedule_patch.py').read_text(encoding='utf-8')
        self.assertIn("'CustomFlowTask'", source)
        self.assertIn('_flows()', source)

    def test_start_client_reports_missing_path(self):
        source = Path('src/task/CustomFlowTask.py').read_text(encoding='utf-8')
        self.assertIn('client may be a different server', source)
        self.assertIn('Global Client Path', source)


class TestFlowStaminaWait(unittest.TestCase):
    """流级体力等待开关：与日常一条龙同款，任务级、对全部任务流生效。

    CustomFlowTask 依赖 Mon，本机无法 import，因此用源码静态检查锁定关键
    约定；CI 环境有 Mon 时可补充运行期验证。
    """

    def setUp(self):
        self.source = Path('src/task/CustomFlowTask.py').read_text(encoding='utf-8')

    def test_task_level_config_keys(self):
        self.assertIn("WAIT_STAMINA_KEY = '等待体力恢复'", self.source)
        self.assertIn("STAMINA_GAP_KEY = '体力等待缺口上限'", self.source)
        self.assertIn('self.default_config[WAIT_STAMINA_KEY] = False', self.source)
        self.assertIn('self.default_config[STAMINA_GAP_KEY] = 10', self.source)

    def test_only_stamina_consuming_tasks_are_covered(self):
        self.assertIn("STAMINA_TASK_CLASSES = frozenset({'SimulationTask', 'ForgeryTask', 'TacetTask'})",
                      self.source)
        # Non-stamina tasks must not be in the set (领奖/梦魇/游园/讨伐).
        self.assertNotIn('NightmareNestTask', self.source.split('STAMINA_TASK_CLASSES')[1].split('\n')[0])
        self.assertNotIn('GardenTask', self.source.split('STAMINA_TASK_CLASSES')[1].split('\n')[0])

    def test_injection_reuses_routine_attributes_and_always_assigns(self):
        self.assertIn('def _configure_stamina_wait(self, task):', self.source)
        self.assertIn('task._routine_wait_stamina = enabled', self.source)
        self.assertIn('task._routine_stamina_gap = int(self.config.get(STAMINA_GAP_KEY, 10) or 0)',
                      self.source)
        self.assertIn('task._routine_stamina_single_run = False', self.source)
        # Injection must happen in the non-routine (direct task) branch.
        self.assertIn('if class_name in STAMINA_TASK_CLASSES:', self.source)
        self.assertIn('self._configure_stamina_wait(task)', self.source)

    def test_routine_task_step_inherits_its_own_config(self):
        # 选「日常一条龙」的路径本就走 run_selected_plan，自动用 RoutineTask
        # 自己的体力开关；确认没有绕过它。
        self.assertIn('task.run_selected_plan(plan)', self.source)


if __name__ == '__main__':
    unittest.main()
