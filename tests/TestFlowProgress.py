"""任务监控页的工作流步骤进度解析（纯函数，不需要 Qt 运行时）。"""

import ast
import textwrap
import unittest
from pathlib import Path
from types import SimpleNamespace


def _load_flow_progress():
    """从 TaskMonitorTab.py 提取 _flow_progress 与 STATUS_LABELS，避免 import Qt。"""
    source = Path('src/gui/TaskMonitorTab.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    wanted = {'STATUS_LABELS': None, '_flow_progress': None}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in wanted:
                    wanted[target.id] = node
        elif isinstance(node, ast.FunctionDef) and node.name in wanted:
            wanted[node.name] = node
    module = ast.Module(body=[wanted[k] for k in ('STATUS_LABELS', '_flow_progress')
                              if wanted[k] is not None], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {}
    exec(compile(module, '<flow-progress>', 'exec'), namespace)
    return namespace['STATUS_LABELS'], namespace['_flow_progress']


class TestFlowProgress(unittest.TestCase):

    def setUp(self):
        self.labels, self.progress = _load_flow_progress()

    def test_no_task_returns_empty(self):
        self.assertEqual((None, '', '', []), self.progress(None))

    def test_task_without_step_status_returns_empty(self):
        task = SimpleNamespace(info={'任务流': '默认流程'})
        self.assertEqual((None, '', '', []), self.progress(task))

    def test_parses_step_status_rows(self):
        steps = [
            {'index': 1, 'label': '启动游戏（国服）', 'status': 'success', 'error': ''},
            {'index': 2, 'label': '登录账号', 'status': 'running', 'error': ''},
            {'index': 3, 'label': '日常一条龙', 'status': 'pending', 'error': ''},
        ]
        task = SimpleNamespace(info={
            '任务流': '默认流程', '当前步骤': '2/3 · 登录账号', '步骤状态': steps,
        })
        name, current, account, rows = self.progress(task)
        self.assertEqual('默认流程', name)
        self.assertEqual('2/3 · 登录账号', current)
        self.assertEqual('', account)
        self.assertEqual(3, len(rows))
        self.assertEqual((1, '启动游戏（国服）', 'success', ''), rows[0])
        self.assertEqual(('pending', 'running', 'success'),
                         (rows[2][2], rows[1][2], rows[0][2]))

    def test_failed_step_keeps_error(self):
        steps = [{
            'index': 5, 'label': '切号', 'status': 'failed', 'error': '账号未找到',
        }]
        task = SimpleNamespace(info={'步骤状态': steps})
        _, _, _, rows = self.progress(task)
        self.assertEqual('账号未找到', rows[0][3])

    def test_all_status_labels_present(self):
        for status in ('pending', 'running', 'success', 'failed'):
            self.assertIn(status, self.labels)
            self.assertTrue(self.labels[status])

    def test_monitor_has_flow_panel(self):
        source = Path('src/gui/TaskMonitorTab.py').read_text(encoding='utf-8')
        self.assertIn('self.flow_table = QTableWidget(body)', source)
        self.assertIn("self.info_set('步骤状态'", Path('src/task/CustomFlowTask.py').read_text(encoding='utf-8'))
        # 面板在工作流运行时填充
        self.assertIn('self._fill_flow(manual, current)', source)


if __name__ == '__main__':
    unittest.main()
