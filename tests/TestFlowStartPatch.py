"""自定义工作流自带启动游戏时，框架不应重复启动。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import src.flow_start_patch as fsp


class TestFlowStepDetection(unittest.TestCase):
    """判定「工作流首个启用步骤是 start_game」的逻辑。"""

    def test_start_game_first_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'CustomFlows.json').write_text(json.dumps({
                '默认流程': [
                    {'id': 'start_game', 'enabled': True, 'params': {'服务器': '国服'}},
                    {'id': 'login', 'enabled': True, 'params': {}},
                ]}, ensure_ascii=False), encoding='utf-8')

            class FakeCFT:
                pass

            task = FakeCFT()
            task.executor = MagicMock()
            task.executor.config_folder = folder
            task.config = {'Custom Flow': '默认流程'}

            with patch.dict('sys.modules', {
                    'src.task.CustomFlowTask': MagicMock(CustomFlowTask=FakeCFT)}):
                self.assertTrue(fsp._flow_starts_game(task))

    def test_start_game_not_first_not_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'CustomFlows.json').write_text(json.dumps({
                '默认流程': [
                    {'id': 'login', 'enabled': True, 'params': {}},
                    {'id': 'start_game', 'enabled': True, 'params': {}},
                ]}, ensure_ascii=False), encoding='utf-8')

            class FakeCFT:
                pass

            task = FakeCFT()
            task.executor = MagicMock()
            task.executor.config_folder = folder
            task.config = {'Custom Flow': '默认流程'}

            with patch.dict('sys.modules', {
                    'src.task.CustomFlowTask': MagicMock(CustomFlowTask=FakeCFT)}):
                self.assertFalse(fsp._flow_starts_game(task))

    def test_disabled_start_game_not_detected(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'CustomFlows.json').write_text(json.dumps({
                '默认流程': [
                    {'id': 'start_game', 'enabled': False, 'params': {}},
                    {'id': 'login', 'enabled': True, 'params': {}},
                ]}, ensure_ascii=False), encoding='utf-8')

            class FakeCFT:
                pass

            task = FakeCFT()
            task.executor = MagicMock()
            task.executor.config_folder = folder
            task.config = {'Custom Flow': '默认流程'}

            with patch.dict('sys.modules', {
                    'src.task.CustomFlowTask': MagicMock(CustomFlowTask=FakeCFT)}):
                self.assertFalse(fsp._flow_starts_game(task))


class TestStartDeviceSkip(unittest.TestCase):
    """跳过 / 透传框架 start_device 的行为。"""

    def test_flow_with_start_game_skips_framework_launch(self):
        class Controller:
            pass

        controller = Controller()
        controller._active_task = 'flow-task'
        with patch.object(fsp, '_flow_starts_game', return_value=True), \
             patch.object(fsp, '_original_start_device', return_value='should-not-run') as orig:
            result = fsp._start_device(controller)
        self.assertTrue(result)
        orig.assert_not_called()  # 框架启动被跳过

    def test_other_task_passes_through(self):
        class Controller:
            pass

        controller = Controller()
        controller._active_task = 'other-task'
        with patch.object(fsp, '_flow_starts_game', return_value=False), \
             patch.object(fsp, '_original_start_device', return_value='framework') as orig:
            result = fsp._start_device(controller)
        self.assertEqual('framework', result)
        orig.assert_called_once_with(controller)


class TestBoundMethod(unittest.TestCase):

    def test_binds_self_like_cython_builtin(self):
        """描述符必须在实例调用时注入 self（Cython builtin 无 __get__）。"""
        def original(self, flag):
            return ('called', self.name, flag)

        class Host:
            start_device = fsp._BoundMethod(original)

        host = Host()
        host.name = 'dev'
        self.assertEqual(('called', 'dev', True), host.start_device(True))


class TestInstall(unittest.TestCase):

    def test_install_wraps_start_device(self):
        class Controller:
            def start_device(self):
                return 'original'

        fsp._INSTALLED = False
        fsp._PATCHED = None
        with patch.dict('sys.modules', {
                'Mon': MagicMock(),
                'Mon.core': MagicMock(),
                'Mon.core.start_controller': MagicMock(StartController=Controller)}):
            fsp.install()
        self.assertTrue(fsp._INSTALLED)
        self.assertIsInstance(Controller.__dict__['start_device'], fsp._BoundMethod)


if __name__ == '__main__':
    unittest.main()
