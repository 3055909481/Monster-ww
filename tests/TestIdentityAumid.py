"""身份层测试：进程 AUMID、注册表单实例标记重定向，失败时静默降级。"""
import os
import tempfile
import unittest
from unittest.mock import Mock, MagicMock, patch as mock_patch

from src import identity as ident


class TestProcessAumid(unittest.TestCase):

    def test_sets_expected_aumid(self):
        fake_shell = Mock()
        fake_windll = Mock(shell32=fake_shell)
        with mock_patch.object(ident, 'os') as os_mod, \
             mock_patch('ctypes.windll', fake_windll, create=True):
            os_mod.name = 'nt'
            # os.name 是只读属性，上面 mock 整模块即可
            ident._patch_process_app_user_model_id()
        fake_shell.SetCurrentProcessExplicitAppUserModelID.assert_called_once_with(
            ident.NOTIFIER_APP_USER_MODEL_ID
        )

    def test_non_windows_skips(self):
        with mock_patch.object(ident, 'os') as os_mod:
            os_mod.name = 'posix'
            with mock_patch('ctypes.windll', Mock(), create=True) as windll:
                ident._patch_process_app_user_model_id()
                windll.shell32.SetCurrentProcessExplicitAppUserModelID.assert_not_called()

    def test_failure_degrades_silently(self):
        with mock_patch.object(ident, 'os') as os_mod:
            os_mod.name = 'nt'
            with mock_patch('ctypes.windll', Mock(shell32=Mock(
                    SetCurrentProcessExplicitAppUserModelID=Mock(
                        side_effect=Exception('boom')))), create=True):
                # 不应抛异常
                ident._patch_process_app_user_model_id()


class TestOkAlias(unittest.TestCase):

    def test_ok_imports_resolve_to_mon(self):
        """工坊代码残留的 from ok import 必须能解析（No module named 'ok' 修复）。"""
        import sys
        import Mon
        from src import identity as ident
        ident._alias_ok_package(Mon)
        self.assertIs(sys.modules.get('ok'), Mon)
        from ok import Box, Logger, color_range_to_bound, PostMessageInteraction
        from ok.task.exceptions import FinishedException
        from ok.device.interaction_methods.keys import vk_key_dict
        from Mon import Box as MonBox
        self.assertIs(Box, MonBox)
        self.assertTrue(callable(color_range_to_bound))
        self.assertTrue(vk_key_dict)

    def test_alias_is_idempotent(self):
        import sys
        import Mon
        from src import identity as ident
        original = sys.modules.get('ok')
        try:
            sys.modules.pop('ok', None)
            ident._alias_ok_package(Mon)
            first = sys.modules['ok']
            ident._alias_ok_package(Mon)
            self.assertIs(sys.modules['ok'], first)
        finally:
            if original is None:
                sys.modules.pop('ok', None)
            else:
                sys.modules['ok'] = original


class TestAumidDisplay(unittest.TestCase):

    def test_registers_display_name(self):
        import sys
        fake_key = MagicMock()
        fake_key.__enter__.return_value = fake_key
        fake_winreg = Mock(CreateKey=Mock(return_value=fake_key))
        fake_winreg.HKEY_CURRENT_USER = object()
        fake_winreg.REG_SZ = 1
        with mock_patch.object(ident, 'os') as os_mod, \
             mock_patch.dict(sys.modules, {'winreg': fake_winreg}):
            os_mod.name = 'nt'
            ident._register_aumid_display()
        # 代码调用的是 winreg.SetValueEx(key, name, ...)，第一个参数是 key，第二个是名字
        values = [c.args[1] for c in fake_winreg.SetValueEx.call_args_list]
        self.assertIn('DisplayName', values)

    def test_non_windows_skips(self):
        with mock_patch.object(ident, 'os') as os_mod, \
             mock_patch('winreg.CreateKey') as create:
            os_mod.name = 'posix'
            ident._register_aumid_display()
            create.assert_not_called()


class TestMutexOwnerFile(unittest.TestCase):
    """单实例标记只改文件名，且绝不出现上游 ok-ww 标识。"""

    def setUp(self):
        from Mon.util import process as mon_process
        self.mp = mon_process
        self.orig_retain = mon_process._retain_mutex
        self.orig_terminate = mon_process._terminate_previous_instances
        self.orig_flag = getattr(mon_process, '_monster_ww_mutex_patched', None)
        self.addCleanup(self._restore)

    def _restore(self):
        self.mp._retain_mutex = self.orig_retain
        self.mp._terminate_previous_instances = self.orig_terminate
        if self.orig_flag is None:
            if hasattr(self.mp, '_monster_ww_mutex_patched'):
                del self.mp._monster_ww_mutex_patched
        else:
            self.mp._monster_ww_mutex_patched = self.orig_flag

    def _unmark(self):
        if hasattr(self.mp, '_monster_ww_mutex_patched'):
            del self.mp._monster_ww_mutex_patched

    def test_renames_framework_marker(self):
        src = os.path.join(tempfile.gettempdir(), 'Mon-script-abc123.pid')
        dst = ident._mutex_owner_file(src)
        self.assertEqual(os.path.basename(dst), 'Monster-ww-script-abc123.pid')
        self.assertEqual(os.path.dirname(dst), os.path.dirname(src))

    def test_other_files_untouched(self):
        for name in ('Mon-script-abc.txt', 'Mon-other-abc.pid', 'random.pid'):
            path = os.path.join(tempfile.gettempdir(), name)
            self.assertEqual(ident._mutex_owner_file(path), path)

    def test_no_upstream_branding(self):
        self.assertNotIn('ok-script', ident.MUTEX_MARKER_PREFIX)
        self.assertNotIn('ok-script', ident.FRAMEWORK_MUTEX_PREFIX)
        dst = ident._mutex_owner_file(
            os.path.join(tempfile.gettempdir(), 'Mon-script-abc.pid'))
        self.assertNotIn('ok', os.path.basename(dst).lower())

    def test_patch_redirects_retain(self):
        self._unmark()
        target = os.path.join(tempfile.gettempdir(), 'Mon-script-abc.pid')
        with mock_patch.object(self.mp, '_retain_mutex') as fake:
            self.assertTrue(ident._patch_mutex_owner_file())
            self.mp._retain_mutex('HANDLE', target)
        fake.assert_called_once_with('HANDLE', ident._mutex_owner_file(target), gui=False)

    def test_terminate_falls_back_to_legacy_marker(self):
        self._unmark()
        with tempfile.TemporaryDirectory() as d:
            old = os.path.join(d, 'Mon-script-abc.pid')
            with open(old, 'w', encoding='ascii') as f:
                f.write('{}')
            with mock_patch.object(self.mp, '_terminate_previous_instances') as fake:
                ident._patch_mutex_owner_file()
                self.mp._terminate_previous_instances(old)
        fake.assert_called_once_with(old, replace_console=False)

    def test_terminate_prefers_new_marker(self):
        self._unmark()
        with tempfile.TemporaryDirectory() as d:
            old = os.path.join(d, 'Mon-script-abc.pid')
            new = os.path.join(d, 'Monster-ww-script-abc.pid')
            for path in (old, new):
                with open(path, 'w', encoding='ascii') as f:
                    f.write('{}')
            with mock_patch.object(self.mp, '_terminate_previous_instances') as fake:
                ident._patch_mutex_owner_file()
                self.mp._terminate_previous_instances(old)
        fake.assert_called_once_with(new, replace_console=False)

    def test_patch_is_idempotent(self):
        self._unmark()
        self.assertTrue(ident._patch_mutex_owner_file())
        retain_fn = self.mp._retain_mutex
        self.assertFalse(ident._patch_mutex_owner_file())
        self.assertIs(self.mp._retain_mutex, retain_fn)


if __name__ == '__main__':
    unittest.main()
