"""Pure-Python tests for the custom task flow registry and store.

These tests import only the dependency-free FlowSteps module so they run
without Mon, PySide6 or any compiled native extension.
"""

import ast
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
            ['start_game', 'login', 'switch_account', 'run_task',
             'wait_stamina', 'exit_game', 'exit_app'],
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


class TestFlowSteps(unittest.TestCase):
    """新步骤与状态判断的源码守卫。CustomFlowTask 依赖 Mon 无法本地 import。"""

    def setUp(self):
        self.source = Path('src/task/CustomFlowTask.py').read_text(encoding='utf-8')

    def test_wait_stamina_and_exit_app_steps_registered(self):
        steps = Path('src/task/routine/FlowSteps.py').read_text(encoding='utf-8')
        self.assertIn("'wait_stamina', '等待体力恢复'", steps)
        self.assertIn("'exit_app', '退出程序'", steps)

    def test_no_task_level_stamina_switch(self):
        # 用户要求：开关不动（日常一条龙那份），体力等待改为流程步骤。
        self.assertNotIn('WAIT_STAMINA_KEY', self.source)
        self.assertNotIn('STAMINA_GAP_KEY', self.source)

    def test_wait_step_honours_daily_budget(self):
        self.assertIn('def _wait_stamina_step(self, params):', self.source)
        self.assertIn('StaminaWaitLedger(self.executor.config_folder)', self.source)
        self.assertIn('plan_stamina_wait(', self.source)
        self.assertIn('ledger.add(', self.source)
        self.assertIn('self._current_account_key()', self.source)

    def test_exit_app_step_quits_framework(self):
        self.assertIn('def _exit_app_step(self):', self.source)
        self.assertIn('from Mon.core.events import communicate', self.source)
        self.assertIn('communicate.quit.emit()', self.source)

    def test_game_state_detection_for_login(self):
        self.assertIn('def _game_state(self):', self.source)
        self.assertIn("state = self._game_state()", self.source)
        self.assertIn("if state == 'world':", self.source)
        self.assertIn('self._switch_to_login()', self.source)

    def test_login_shows_account_list_before_selecting(self):
        """登录前必须先确保账号列表可见，否则会卡在"上次登录账号"直达页。"""
        self.assertIn('def _ensure_account_list_visible(self, account_key):', self.source)
        self.assertIn('Labels.switch_account', self.source)
        # 登录步骤在选账号前调用该方法
        login_pos = self.source.index('def _login_step(')
        ensure_pos = self.source.index('self._ensure_account_list_visible(account_key)')
        target_pos = self.source.index('self._login_target_account(account_key)', login_pos)
        self.assertLess(login_pos, ensure_pos)
        self.assertLess(ensure_pos, target_pos)
        # 目标账号已在列表里就直接返回，避免每次都点切换账号
        self.assertIn('if any(account_key_from_ocr(text) == account_key for text in items):\n                return', self.source)

    def test_step_status_tracked_for_ui(self):
        """每步状态必须实时写进 info，供任务监控页展示进度。"""
        self.assertIn("self.step_status = [", self.source)
        self.assertIn("'status': 'pending'", self.source)
        self.assertIn("self.info_set('步骤状态', self.step_status)", self.source)
        self.assertIn("entry['status'] = 'running'", self.source)  # 执行中
        self.assertIn("entry['status'] = 'failed'", self.source)
        self.assertIn("entry['status'] = 'success'", self.source)
        self.assertIn("entry['error'] = str(error)", self.source)  # 失败原因
        # running 必须在 _run_step 之前置位；失败/成功在之后
        run_pos = self.source.index('self._run_step(step_id, params)')
        self.assertLess(self.source.index("entry['status'] = 'running'"), run_pos)
        self.assertLess(run_pos, self.source.index("entry['status'] = 'success'"))
        self.assertIn('self._login_target_account(account_key)', self.source)

    def test_start_client_reuses_running_client(self):
        self.assertIn('client is already running; reusing it', self.source)
        self.assertIn('if not same_client:', self.source)
        self.assertIn('self.start_device()', self.source)

    def test_only_pc_client_windows_are_matched(self):
        """Cloud-game browser windows must never be auto-selected as the game."""
        src = Path('config.py').read_text(encoding='utf-8')
        tree = ast.parse(src)
        config = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                      and any(getattr(t, 'id', None) == 'config' for t in n.targets))
        top_classes = []
        for node in ast.walk(config):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if (isinstance(key, ast.Constant) and key.value == 'top_hwnd_class'
                            and isinstance(value, ast.List)):
                        top_classes.extend(ast.unparse(e) for e in value.elts)
        self.assertTrue(top_classes)
        self.assertFalse([v for v in top_classes if 'Cef' in v or 'Chrome' in v])

        # 云鸣潮(browser)作为可选设备保留（与上游一致），但默认设备与切换由
        # device_selection_patch 控制：默认 PC、不自动切换到云鸣潮/安卓。
        browser_keys = [key.value for node in ast.walk(config) if isinstance(node, ast.Dict)
                        for key in node.keys if isinstance(key, ast.Constant)
                        and isinstance(key.value, str) and key.value == 'browser']
        self.assertTrue(browser_keys, 'cloud game (browser) device must stay registered')
        patch_src = Path('src/device_selection_patch.py').read_text(encoding='utf-8')
        self.assertIn("'windows': 0", patch_src)
        self.assertIn("'browser': 1", patch_src)
        self.assertIn("'adb': 2", patch_src)


if __name__ == '__main__':
    unittest.main()
