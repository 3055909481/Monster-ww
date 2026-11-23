import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import config


class TestCalculatePcExePath(unittest.TestCase):

    def test_none_path_uses_most_recently_run_executable(self):
        expected = r"C:\Games\Wuthering Waves.exe"

        with patch.object(config, "_find_most_recently_run_pc_exe", return_value=expected) as find:
            with patch.object(config, "_find_pc_exe_from_registry") as find_registry:
                result = config.calculate_pc_exe_path(None)

        find.assert_called_once_with()
        find_registry.assert_not_called()
        self.assertEqual(expected, result)

    def test_none_path_falls_back_to_registry_lookup(self):
        expected = r"C:\Games\Wuthering Waves.exe"

        with patch.object(config, "_find_most_recently_run_pc_exe", return_value=None):
            with patch.object(config, "_find_pc_exe_from_registry", return_value=expected) as find:
                result = config.calculate_pc_exe_path(None)

        find.assert_called_once_with()
        self.assertEqual(expected, result)

    def test_none_path_returns_none_when_no_installation_is_found(self):
        with patch.object(config, "_find_most_recently_run_pc_exe", return_value=None):
            with patch.object(config, "_find_pc_exe_from_registry", return_value=None):
                result = config.calculate_pc_exe_path(None)

        self.assertIsNone(result)

    def test_running_path_still_derives_game_executable(self):
        running_path = (
            r"C:\Games\Wuthering Waves Game\Client\Binaries"
            r"\Win64\Client-Win64-Shipping.exe"
        )

        result = config.calculate_pc_exe_path(running_path)

        self.assertEqual(r"C:\Games\Wuthering Waves Game\Wuthering Waves.exe", result)

    def test_registered_launcher_path_finds_sibling_game_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            install_root = Path(temp)
            launcher = install_root / "Wuthering Waves bilibili" / "launcher.exe"
            game_exe = install_root / "Wuthering Waves Game" / "Wuthering Waves.exe"
            launcher.parent.mkdir()
            game_exe.parent.mkdir()
            game_exe.touch()

            result = config._find_pc_exe_near_registered_path(str(launcher))

        self.assertEqual(str(game_exe), result)


class TestGameServerPath(unittest.TestCase):

    def setUp(self):
        self._saved_override = config._server_override
        config.set_server_override(None)

    def tearDown(self):
        config._server_override = self._saved_override

    def _basic(self, server=None, cn='', global_path=''):
        return {
            config.SERVER_KEY: server or config.SERVER_CN,
            config.SERVER_PATH_KEYS[config.SERVER_CN]: cn,
            config.SERVER_PATH_KEYS[config.SERVER_GLOBAL]: global_path,
        }

    def _mock_basic(self, basic):
        og = SimpleNamespace(global_config=SimpleNamespace(get_config=lambda name: basic))
        return patch("Mon.og", og, create=True)

    def test_configured_path_wins_for_selected_server(self):
        import os
        with tempfile.NamedTemporaryFile(suffix='.exe', delete=False) as handle:
            path = handle.name
        try:
            basic = self._basic(config.SERVER_GLOBAL, cn='C:/missing/cn.exe', global_path=path)
            with self._mock_basic(basic):
                self.assertEqual(config.get_current_server(), config.SERVER_GLOBAL)
                self.assertEqual(config.calculate_pc_exe_path(), path)
        finally:
            os.unlink(path)

    def test_missing_configured_path_falls_back_to_detection(self):
        basic = self._basic(config.SERVER_CN, cn=r'C:\missing\cn.exe')
        with self._mock_basic(basic):
            with patch.object(config, "_find_most_recently_run_pc_exe", return_value='C:/auto.exe'):
                self.assertEqual(config.calculate_pc_exe_path(), 'C:/auto.exe')

    def test_account_override_switches_server_path(self):
        import os
        with tempfile.NamedTemporaryFile(suffix='.exe', delete=False) as handle:
            cn_path = handle.name
        try:
            basic = self._basic(config.SERVER_GLOBAL, cn=cn_path, global_path='C:/missing/global.exe')
            with self._mock_basic(basic):
                config.set_server_override(config.SERVER_CN)
                self.assertEqual(config.calculate_pc_exe_path(), cn_path)
                config.set_server_override(None)
                with patch.object(config, "_find_most_recently_run_pc_exe", return_value='C:/auto.exe'):
                    self.assertEqual(config.calculate_pc_exe_path(), 'C:/auto.exe')
        finally:
            os.unlink(cn_path)

    def test_scheduled_server_overrides_global(self):
        basic = self._basic(config.SERVER_CN)  # global = 国服
        with self._mock_basic(basic):
            with patch.object(sys, 'argv', ['main.py', '-t', '1', '--schedule-server', '国际服']):
                self.assertEqual(config.get_current_server(), config.SERVER_GLOBAL)

    def test_server_options_in_basic_config(self):
        cfg_type = config.basic_config_option.config_type
        self.assertEqual(cfg_type[config.SERVER_KEY]['type'], 'drop_down')
        self.assertEqual(cfg_type[config.SERVER_KEY]['options'], ['国服', '国际服'])
        for key in config.SERVER_PATH_KEYS.values():
            self.assertEqual(cfg_type[key]['type'], 'file_selector')


if __name__ == "__main__":
    unittest.main()
