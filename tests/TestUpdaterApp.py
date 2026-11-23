"""独立更新窗口的非 GUI 逻辑测试：单实例、日志落地、失败重启、日志节流。

tkinter 只在 UpdaterWindow.__init__ 里导入，所以这些用例不需要显示环境。
"""
import logging
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import updater_app
import update_core


class TestSingleInstance(unittest.TestCase):

    @unittest.skipUnless(os.name == 'nt', 'named mutex is Windows-only')
    def test_second_updater_is_rejected(self):
        updater_app._MUTEX_HANDLE = None
        self.assertFalse(updater_app._already_running())
        self.assertTrue(updater_app._already_running(),
                        'a second instance must see the mutex already held')


class TestLogging(unittest.TestCase):

    def test_log_file_is_created_under_appdata(self):
        with tempfile.TemporaryDirectory() as work:
            with patch.dict(os.environ, {'APPDATA': work}):
                log_file = updater_app._prepare_logging()
                updater_app.LOG.warning('probe line')
                for handler in logging.getLogger().handlers:
                    handler.flush()
            self.assertIsNotNone(log_file, 'windowed builds have no stderr to fall back on')
            self.assertTrue(log_file.is_file())
            self.assertIn('probe line', log_file.read_text(encoding='utf-8'))
            self.assertEqual(log_file.parent, Path(work) / 'Monster-ww' / 'logs')
            # Release the file before TemporaryDirectory tries to delete it.
            for handler in list(logging.getLogger().handlers):
                logging.getLogger().removeHandler(handler)
                handler.close()


class TestRecoverAfterFailure(unittest.TestCase):
    """The app is asked to quit right before the swap, so a failure can leave
    the user with nothing on screen - but never while it is still running."""

    def _window_stub(self, pid):
        return SimpleNamespace(pid=pid, app_dir=Path('C:/app/Monster-ww'),
                               exe_name='Monster-ww.exe')

    def test_skips_revive_while_the_app_is_still_alive(self):
        stub = self._window_stub(os.getpid())
        revive = Mock()
        with patch.object(updater_app.update_core, 'revive', revive):
            self.assertFalse(updater_app.UpdaterWindow._recover(stub))
        revive.assert_not_called()

    def test_relaunches_when_the_app_is_gone(self):
        stub = self._window_stub(0)
        with patch.object(updater_app.update_core, 'process_alive', return_value=False), \
             patch.object(updater_app.update_core, 'revive', return_value=True) as revive:
            self.assertTrue(updater_app.UpdaterWindow._recover(stub))
        revive.assert_called_once()

    def test_revive_failure_is_not_fatal(self):
        stub = self._window_stub(0)
        with patch.object(updater_app.update_core, 'process_alive', return_value=False), \
             patch.object(updater_app.update_core, 'revive',
                          side_effect=update_core.UpdateError('boom')):
            self.assertFalse(updater_app.UpdaterWindow._recover(stub))


class TestProgressLogThrottle(unittest.TestCase):

    def _stub(self):
        stub = SimpleNamespace(phase=Mock(), bar={}, percent=Mock(),
                               detail=Mock(), swapping=False, action_button=Mock(),
                               _last_phase=None, _log_bucket=-1,
                               lines=[])
        stub._append_log = stub.lines.append
        return stub

    def test_download_log_is_throttled(self):
        stub = self._stub()
        for pct in range(5, 61, 5):
            updater_app.UpdaterWindow._apply_progress(
                stub, '正在下载更新包…', float(pct), f'{pct} MB')
        self.assertLessEqual(len(stub.lines), 8,
                             'a ~1000-chunk download must not fill the log panel')
        self.assertTrue(stub.lines[0].startswith('正在下载更新包…'))
        self.assertEqual(stub.percent.set.call_args.args[0], '60%')

    def test_phase_change_is_always_logged(self):
        stub = self._stub()
        updater_app.UpdaterWindow._apply_progress(stub, '正在校验安装包…', 62.0, 'SHA-256')
        updater_app.UpdaterWindow._apply_progress(stub, '校验通过', 65.0, 'abc…')
        self.assertEqual(len(stub.lines), 2)


class TestCancelGate(unittest.TestCase):
    """Cancel must work during download/extract but never during the swap."""

    def _stub(self, swapping):
        return SimpleNamespace(
            done=False, swapping=swapping, cancelled=threading.Event(),
            worker=Mock(is_alive=Mock(return_value=True)),
            phase=Mock(), action_button=Mock(), root=Mock(),
            lines=[], _append_log=lambda text: self._append(text))

    def setUp(self):
        self._lines = []

    def _append(self, text):
        self._lines.append(text)

    def test_cancel_is_honoured_before_the_swap(self):
        stub = self._stub(swapping=False)
        updater_app.UpdaterWindow._on_action(stub)
        self.assertTrue(stub.cancelled.is_set())

    def test_cancel_is_refused_during_the_swap(self):
        stub = self._stub(swapping=True)
        updater_app.UpdaterWindow._on_action(stub)
        self.assertFalse(stub.cancelled.is_set())
        self.assertIn('无法取消', self._lines[-1])


class TestSwapPhaseDetection(unittest.TestCase):

    def test_install_phase_disables_cancel(self):
        stub = SimpleNamespace(phase=Mock(), bar={}, percent=Mock(), detail=Mock(),
                               swapping=False, action_button=Mock(),
                               _last_phase=None, _log_bucket=-1, lines=[])
        stub._append_log = stub.lines.append
        updater_app.UpdaterWindow._apply_progress(stub, '正在下载更新包…', 40.0, '10 MB')
        self.assertFalse(stub.swapping, 'download must stay cancellable')
        updater_app.UpdaterWindow._apply_progress(stub, '主程序已退出，开始安装…', 86.0, '')
        self.assertTrue(stub.swapping)
        stub.action_button.configure.assert_called_once_with(state='disabled')


if __name__ == '__main__':
    unittest.main()
