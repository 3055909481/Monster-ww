"""配置统一到 %APPDATA% 的一次性迁移：结构核对 + 旧配置清理 + 用户数据保护。

所有测试只用临时目录，绝不触碰真实 %APPDATA%/Monster-ww/configs。
"""

import json
import tempfile
import unittest
from pathlib import Path

from src.config_migration import (
    MARKER, PROTECTED_CONFIGS, active_task_names,
    migrate_legacy_configs, prune_obsolete_task_configs,
)


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


class TestMigrateLegacyConfigs(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.legacy = self.root / 'legacy_configs'
        self.target = self.root / 'appdata_configs'
        self.legacy.mkdir()
        self.target.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_existing_appdata_config_never_overwritten(self):
        write_json(self.legacy / 'AutoCombatTask.json', {
            '_enabled': True, 'Auto Target': True, '按 F 开始战斗': True,
        })
        appdata = {'_enabled': False, 'Auto Target': False, 'my_custom': 'keep'}
        write_json(self.target / 'AutoCombatTask.json', appdata)
        count = migrate_legacy_configs(self.legacy, self.target)
        self.assertEqual(0, count)  # APPDATA 已有，不迁移、不覆盖
        self.assertEqual(appdata, read_json(self.target / 'AutoCombatTask.json'))

    def test_new_file_copied_whole(self):
        write_json(self.legacy / 'NewTask.json', {'_enabled': True, 'x': 1})
        count = migrate_legacy_configs(self.legacy, self.target)
        self.assertEqual(1, count)
        self.assertEqual({'_enabled': True, 'x': 1},
                         read_json(self.target / 'NewTask.json'))

    def test_marker_prevents_second_run(self):
        write_json(self.legacy / 'AutoCombatTask.json', {'_enabled': True})
        migrate_legacy_configs(self.legacy, self.target)
        self.assertTrue((self.target / MARKER).exists())
        # 之后改旧配置，不应再被迁入
        write_json(self.legacy / 'AutoCombatTask.json', {'_enabled': False})
        count = migrate_legacy_configs(self.legacy, self.target)
        self.assertEqual(0, count)

    def test_same_directory_is_noop(self):
        write_json(self.legacy / 'X.json', {'a': 1})
        self.assertEqual(0, migrate_legacy_configs(self.legacy, self.legacy))

    def test_missing_directory_is_noop(self):
        self.assertEqual(0, migrate_legacy_configs(self.root / 'none', self.target))


class TestPruneObsoleteConfigs(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.target = self.root / 'appdata_configs'
        self.target.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_obsolete_task_config_moved_to_backup(self):
        write_json(self.target / 'FarmMapTask.json', {'a': 1})
        prune_obsolete_task_configs(self.target, active_names={'AutoCombatTask'})
        self.assertFalse((self.target / 'FarmMapTask.json').exists())
        self.assertTrue((self.target / 'obsolete_backup' / 'FarmMapTask.json').exists())

    def test_active_task_config_untouched(self):
        write_json(self.target / 'AutoCombatTask.json', {'_enabled': True})
        prune_obsolete_task_configs(self.target, active_names={'AutoCombatTask'})
        self.assertTrue((self.target / 'AutoCombatTask.json').exists())

    def test_user_data_configs_never_touched(self):
        # 账号 / 自定义工作流 / 日常一条龙 / 团队 等用户数据
        user_files = ['MultiAccountConfig.json', 'CustomFlows.json', 'RoutinePlans.json',
                      'MultiAccountConfigTask.json', 'CustomFlowTask.json', 'RoutineTask.json']
        active = {'AutoCombatTask'}  # 故意不含它们，验证保护名单生效
        for name in user_files:
            write_json(self.target / name, {'user': 'data'})
        prune_obsolete_task_configs(self.target, active_names=active)
        for name in user_files:
            self.assertTrue((self.target / name).exists(), name)
            self.assertFalse((self.target / 'obsolete_backup' / name).exists(), name)

    def test_empty_active_names_skips_cleanup(self):
        write_json(self.target / 'FarmMapTask.json', {'a': 1})
        prune_obsolete_task_configs(self.target, active_names=set())
        self.assertTrue((self.target / 'FarmMapTask.json').exists())

    def test_protected_set_covers_user_data(self):
        self.assertIn('MultiAccountConfig', PROTECTED_CONFIGS)
        self.assertIn('CustomFlows', PROTECTED_CONFIGS)
        self.assertIn('RoutinePlans', PROTECTED_CONFIGS)

    def test_active_task_names_reads_from_config(self):
        names = active_task_names()
        self.assertIn('AutoCombatTask', names)
        self.assertIn('DailyTask', names)
        self.assertIn('MultiAccountConfigTask', names)


if __name__ == '__main__':
    unittest.main()
