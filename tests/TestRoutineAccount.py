"""计划任务可选账号绑定：存储 CRUD + run 时切号行为。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.task.routine.RoutinePlans import (
    DEFAULT_PLAN_NAME, PLAN_SELECTOR, RoutinePlanAccountStore,
)


class TestRoutinePlanAccountStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = RoutinePlanAccountStore(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_by_default(self):
        self.assertEqual(self.store.load(), {})
        self.assertIsNone(self.store.get(DEFAULT_PLAN_NAME))

    def test_set_get_remove(self):
        self.store.set('打本号A', 'user_a@example.com')
        self.assertEqual(self.store.get('打本号A'), 'user_a@example.com')
        self.store.set('打本号A', None)
        self.assertIsNone(self.store.get('打本号A'))

    def test_set_empty_key_removes_binding(self):
        self.store.set('计划', 'abc')
        self.store.set('计划', '   ')
        self.assertEqual(self.store.load(), {})

    def test_corrupt_file_is_ignored(self):
        self.store.path.write_text('not json', encoding='utf-8')
        self.assertEqual(self.store.load(), {})
        self.store.path.write_text('["not", "a", "dict"]', encoding='utf-8')
        self.assertEqual(self.store.load(), {})

    def test_atomic_file_is_valid_json(self):
        self.store.set('计划', '账号')
        data = json.loads(self.store.path.read_text(encoding='utf-8'))
        self.assertEqual(data, {'计划': '账号'})


class TestRoutineTaskAccountSwitch(unittest.TestCase):
    """run() 不依赖真实游戏：mock 掉 WWOneTimeTask.run / ensure_main / _run_entry。"""

    def _task(self, plan_name=DEFAULT_PLAN_NAME):
        from src.task.RoutineTask import RoutineTask
        task = RoutineTask.__new__(RoutineTask)
        task.config = {PLAN_SELECTOR: plan_name}
        task.results = []
        task._executor = Mock()
        task._executor.config_folder = self.tmp.name
        task.log_info = Mock()
        task.info_set = Mock()
        task.ensure_main = Mock()
        task._run_entry = Mock()
        return task

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # 一个启用的步骤即可，避免空计划提前 return
        from src.task.routine.RoutinePlans import RoutinePlanStore
        RoutinePlanStore(self.tmp.name).save({DEFAULT_PLAN_NAME: [
            {'id': 'forgery', 'uid': 'x', 'enabled': True, 'params': {}}]})

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_binding_never_switches_account(self):
        task = self._task()
        with patch('src.task.RoutineTask.WWOneTimeTask.run', Mock()), \
             patch.object(task, '_switch_to_login') as switch, \
             patch.object(task, '_login_target_account') as login:
            task.run()
        switch.assert_not_called()
        login.assert_not_called()
        task._run_entry.assert_called_once()

    def test_binding_logs_into_account_before_running(self):
        RoutinePlanAccountStore(self.tmp.name).set(DEFAULT_PLAN_NAME, 'target@example.com')
        task = self._task()
        order = []
        with patch('src.task.RoutineTask.WWOneTimeTask.run', Mock()), \
             patch.object(task, '_switch_to_login', lambda: order.append('switch')), \
             patch.object(task, '_login_target_account', lambda key: order.append(('login', key))), \
             patch.object(task, '_run_entry', lambda *a: order.append('run')):
            task.run()
        self.assertEqual(order, ['switch', ('login', 'target@example.com'), 'run'])


if __name__ == '__main__':
    unittest.main()
