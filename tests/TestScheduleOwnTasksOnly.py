"""计划任务只列出当前应用的任务，不再混入其他 ok-* 应用（Mon-script fork 改动）。

防止框架升级回退到上游"列出所有 ok-* 目录"的行为（旧版 OK-WW 残留删不掉）。
"""

import unittest
from unittest.mock import Mock

from Mon.util.windows_schedule import WindowsScheduleManager


class TestOwnScheduleTasksOnly(unittest.TestCase):
    def _manager(self):
        mgr = WindowsScheduleManager.__new__(WindowsScheduleManager)
        mgr.SCHEDULE_ROOT_PATH = "\\Monster-ww"
        mgr.SCHEDULE_SERVICE = Mock()
        return mgr

    def test_only_returns_own_root(self):
        mgr = self._manager()
        self.assertEqual(["\\Monster-ww"], mgr._get_ok_schedule_root_paths_via_com())
        # 不应访问 COM 去枚举其他目录
        mgr.SCHEDULE_SERVICE.GetFolder.assert_not_called()

    def test_should_include_rejects_other_ok_apps(self):
        mgr = self._manager()
        self.assertTrue(mgr._should_include_task_path("\\Monster-ww\\日常任务"))
        self.assertFalse(mgr._should_include_task_path("\\OK-WW\\多账号每日任务"))
        self.assertFalse(mgr._should_include_task_path("\\ok-genshin\\某任务"))


if __name__ == '__main__':
    unittest.main()
