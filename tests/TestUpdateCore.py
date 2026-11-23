"""独立更新程序核心逻辑测试：进度回调、校验、解压、安装与回滚。"""
import hashlib
import importlib.util
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import update_core as c

HAVE_PY7ZR = importlib.util.find_spec('py7zr') is not None


class TestProgress(unittest.TestCase):

    def test_emit_progress_clamps_and_is_safe(self):
        seen = []
        c.emit_progress(lambda p, v, d: seen.append((p, v, d)), '下载', 150, 'x')
        self.assertEqual(seen, [('下载', 100.0, 'x')])
        c.emit_progress(None, '下载', 10)

    def test_broken_callback_does_not_raise(self):
        def broken(*_args):
            raise RuntimeError('ui gone')
        c.emit_progress(broken, '下载', 50)

    def test_cancel_is_checked(self):
        self.assertIsNone(c._check_cancel(lambda: False))
        with self.assertRaises(c.UpdateCancelled):
            c._check_cancel(lambda: True)


class TestAssets(unittest.TestCase):

    def test_asset_candidates_are_direct_links(self):
        assets = c.asset_candidates('v1.0.40')
        self.assertEqual(assets[0]['name'], 'Monster-ww-v1.0.40-windows-x64.7z')
        self.assertIn('/releases/download/v1.0.40/', assets[0]['url'])
        self.assertNotIn('api.github.com', assets[0]['url'])

    def test_resolve_asset_uses_available_head(self):
        with patch.object(c, '_head_ok', side_effect=[False, True, False]):
            asset = c.resolve_asset('v1.0.40')
        self.assertTrue(asset['name'].endswith('.zip'))

    def test_resolve_asset_reports_progress(self):
        seen = []
        with patch.object(c, '_head_ok', return_value=True):
            c.resolve_asset('v1.0.40', progress=lambda *a: seen.append(a))
        self.assertEqual(seen[0][0], '正在查询更新包…')


class TestDownloadAndVerify(unittest.TestCase):

    def test_download_reports_byte_progress_and_digest(self):
        payload = b'x' * (1024 * 512 + 7)

        class FakeResponse:
            headers = {'Content-Length': str(len(payload))}

            def __enter__(self):
                self._chunks = [payload[:262144], payload[262144:], b'']
                return self

            def __exit__(self, *args):
                return False

            def read(self, _size):
                return self._chunks.pop(0)

        seen = []
        with tempfile.TemporaryDirectory() as work:
            dest = Path(work) / 'pkg.7z'
            with patch.object(c.urllib.request, 'urlopen', return_value=FakeResponse()):
                digest = c.download({'url': 'https://example/x', 'name': 'pkg.7z'}, dest,
                                    progress=lambda *a: seen.append(a))
            self.assertTrue(dest.is_file())
            self.assertEqual(dest.read_bytes(), payload)
            self.assertEqual(digest, hashlib.sha256(payload).hexdigest())
        self.assertTrue(any('正在下载' in a[0] for a in seen))
        self.assertGreater(seen[-1][1], seen[0][1])
        self.assertIn('MB', seen[-1][2])

    def test_verify_mismatch_deletes_archive(self):
        with tempfile.TemporaryDirectory() as work:
            archive = Path(work) / 'bad.7z'
            archive.write_bytes(b'bad')
            with self.assertRaises(c.UpdateError) as ctx:
                c.verify(archive, 'a' * 64, 'b' * 64)
            self.assertIn('校验失败', str(ctx.exception))
            self.assertFalse(archive.exists())

    def test_verify_accepts_matching_checksum(self):
        with tempfile.TemporaryDirectory() as work:
            archive = Path(work) / 'ok.7z'
            archive.write_bytes(b'ok')
            seen = []
            c.verify(archive, 'a' * 64, 'A' * 64, progress=lambda *a: seen.append(a))
        self.assertEqual(seen[-1][0], '校验通过')

    def test_verify_without_checksum_is_reported_not_silent(self):
        """A missing .sha256 must be visible: skipping verification quietly is
        how a swapped download goes unnoticed."""
        with tempfile.TemporaryDirectory() as work:
            archive = Path(work) / 'ok.7z'
            archive.write_bytes(b'ok')
            seen = []
            c.verify(archive, None, 'a' * 64, progress=lambda *a: seen.append(a))
            self.assertTrue(archive.exists(), 'nothing to delete without a checksum')
        self.assertTrue(seen[-1][0].startswith('校验已跳过'), seen[-1])


class TestExtract(unittest.TestCase):

    def test_zip_extract_reports_file_progress(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            archive = root / 'app.zip'
            with zipfile.ZipFile(archive, 'w') as zf:
                zf.writestr('Monster-ww/Monster-ww.exe', 'fake')
                zf.writestr('Monster-ww/version.txt', 'v9')
            seen = []
            extracted = c.extract(archive, root / 'out',
                                  progress=lambda *a: seen.append(a))
            self.assertTrue((extracted / 'Monster-ww.exe').is_file())
        self.assertTrue(any('正在解压' in a[0] for a in seen))
        self.assertIn('2 / 2 个文件', seen[-1][2])

    def test_missing_exe_is_rejected(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            archive = root / 'app.zip'
            with zipfile.ZipFile(archive, 'w') as zf:
                zf.writestr('Monster-ww/readme.txt', 'x')
            with self.assertRaises(c.UpdateError) as ctx:
                c.extract(archive, root / 'out')
            self.assertIn('缺少 Monster-ww.exe', str(ctx.exception))

    def test_unknown_suffix_is_rejected(self):
        with self.assertRaises(c.UpdateError) as ctx:
            c.extract(Path('x.rar'), Path('out'))
        self.assertIn('不支持', str(ctx.exception))

    @unittest.skipUnless(HAVE_PY7ZR, 'py7zr not installed')
    def test_7z_extract_reports_file_progress(self):
        """Regression: the py7zr callback subclass omitted an abstract method,
        so instantiating it raised TypeError and every .7z update (the only
        asset shape published) failed at the extraction step."""
        import py7zr

        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            app = root / 'build' / 'Monster-ww'
            app.mkdir(parents=True)
            (app / 'Monster-ww.exe').write_text('fake')
            (app / 'version.txt').write_text('v9')
            (app / 'lib').mkdir()
            (app / 'lib' / 'a.dll').write_text('dll')
            archive = root / 'app.7z'
            with py7zr.SevenZipFile(archive, 'w') as zf:
                zf.writeall(str(app), 'Monster-ww')
            seen = []
            extracted = c.extract(archive, root / 'out',
                                  progress=lambda *a: seen.append(a))
            self.assertTrue((extracted / 'Monster-ww.exe').is_file())
            self.assertTrue((extracted / 'lib' / 'a.dll').is_file())
        self.assertTrue(any('正在解压' in a[0] for a in seen))
        self.assertGreater(len(seen), 1, 'callback never reported any file')
        self.assertIn('个文件', seen[-1][2])


class TestInstall(unittest.TestCase):

    def test_install_swaps_directories(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            app = root / 'Monster-ww'
            app.mkdir()
            (app / 'Monster-ww.exe').write_text('old')
            staged = root / 'staged' / 'Monster-ww'
            staged.mkdir(parents=True)
            (staged / 'Monster-ww.exe').write_text('new')
            seen = []
            backup = c.install(staged, app, progress=lambda *a: seen.append(a))
            self.assertEqual((app / 'Monster-ww.exe').read_text(), 'new')
            self.assertTrue(backup.exists())
            self.assertEqual((backup / 'Monster-ww.exe').read_text(), 'old')
        self.assertEqual(seen[-1][0], '安装完成')

    def test_install_rolls_back_on_failure(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            app = root / 'Monster-ww'
            app.mkdir()
            (app / 'Monster-ww.exe').write_text('old')
            staged = root / 'staged' / 'Monster-ww'
            staged.mkdir(parents=True)
            (staged / 'Monster-ww.exe').write_text('new')
            with patch.object(c.shutil, 'move', side_effect=OSError('locked')):
                with self.assertRaises(c.UpdateError) as ctx:
                    c.install(staged, app)
            self.assertIn('已恢复原版本', str(ctx.exception))
            self.assertEqual((app / 'Monster-ww.exe').read_text(), 'old')
            self.assertFalse((Path(str(app) + '.old')).exists())

    def test_install_discards_partial_new_tree_before_restoring(self):
        """A cross-volume move is copy+delete: when it dies halfway the new
        directory exists but is incomplete. Rolling back must delete it first,
        otherwise the user keeps a broken mix while being told it was restored."""
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            app = root / 'Monster-ww'
            app.mkdir()
            (app / 'Monster-ww.exe').write_text('old')
            (app / 'old.dll').write_text('old')
            staged = root / 'staged' / 'Monster-ww'
            staged.mkdir(parents=True)
            (staged / 'Monster-ww.exe').write_text('new')

            def half_move(src, dst, *args, **kwargs):
                Path(dst).mkdir(parents=True, exist_ok=True)
                (Path(dst) / 'Monster-ww.exe').write_text('new')  # partial copy
                raise OSError('disk full')

            with patch.object(c.shutil, 'move', side_effect=half_move):
                with self.assertRaises(c.UpdateError) as ctx:
                    c.install(staged, app)
            self.assertIn('已恢复原版本', str(ctx.exception))
            self.assertEqual((app / 'Monster-ww.exe').read_text(), 'old')
            self.assertTrue((app / 'old.dll').is_file(),
                            'the half-written new tree must be removed')
            self.assertFalse((Path(str(app) + '.old')).exists())

    def test_install_does_not_claim_restore_when_rename_never_happened(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            app = root / 'Monster-ww'
            app.mkdir()
            (app / 'Monster-ww.exe').write_text('old')
            staged = root / 'staged' / 'Monster-ww'
            staged.mkdir(parents=True)
            (staged / 'Monster-ww.exe').write_text('new')
            with patch.object(Path, 'rename', side_effect=OSError('in use')), \
                 patch.object(c.time, 'sleep', return_value=None):
                with self.assertRaises(c.UpdateError) as ctx:
                    c.install(staged, app)
            self.assertIn('原版本未改动', str(ctx.exception))
            self.assertEqual((app / 'Monster-ww.exe').read_text(), 'old')


class TestHandoff(unittest.TestCase):

    def test_updater_arguments(self):
        args = c.updater_arguments('v1.0.40', 'C:/app', 4242)
        self.assertEqual(args, ['--version', 'v1.0.40', '--app-dir', 'C:/app',
                                '--pid', '4242', '--exe', 'Monster-ww.exe'])

    def test_launch_updater_copies_to_temp_and_uses_no_console(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            app = root / 'Monster-ww'
            app.mkdir()
            (app / c.UPDATER_NAME).write_bytes(b'fake-updater')
            temp = root / 'temp'
            temp.mkdir()
            popen = Mock()
            with patch.object(c.tempfile, 'gettempdir', return_value=str(temp)), \
                 patch.object(c.subprocess, 'Popen', popen), \
                 patch.object(c.os, 'name', 'nt'):
                launched = c.launch_updater(app / c.UPDATER_NAME, 'v9', app, 1)
            self.assertEqual(launched.parent.name, 'Monster-ww-updater')
            self.assertEqual(launched.read_bytes(), b'fake-updater')
            args = popen.call_args.args[0]
            self.assertIn('--version', args)
            self.assertEqual(popen.call_args.kwargs['creationflags'],
                             c.subprocess.CREATE_NEW_PROCESS_GROUP)

    def test_launch_updater_survives_a_locked_temp_copy(self):
        """A previous updater window still holding the fixed temp exe must not
        break the hand-off: fall back to a per-run file name."""
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            app = root / 'Monster-ww'
            app.mkdir()
            (app / c.UPDATER_NAME).write_bytes(b'fake-updater')
            temp = root / 'temp'
            temp.mkdir()
            real_copy = c.shutil.copy2
            calls = []

            def flaky_copy(src, dst, *args, **kwargs):
                calls.append(Path(dst))
                if len(calls) == 1:
                    raise PermissionError('file is in use')
                return real_copy(src, dst)

            with patch.object(c.shutil, 'copy2', side_effect=flaky_copy), \
                 patch.object(c.tempfile, 'gettempdir', return_value=str(temp)), \
                 patch.object(c.subprocess, 'Popen', Mock()), \
                 patch.object(c.os, 'name', 'nt'):
                launched = c.launch_updater(app / c.UPDATER_NAME, 'v9', app, 1)
            self.assertNotEqual(launched.name, c.UPDATER_NAME)
            self.assertEqual(launched.read_bytes(), b'fake-updater')


class TestProcessRecovery(unittest.TestCase):

    @unittest.skipUnless(os.name == 'nt', 'Windows-only process probing')
    def test_process_alive_detects_current_process(self):
        self.assertTrue(c.process_alive(os.getpid()))
        self.assertFalse(c.process_alive(0))

    def test_revive_restores_backup_when_new_tree_is_unusable(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            app = root / 'Monster-ww'
            app.mkdir()
            (app / 'half-copied.dll').write_text('new')  # no exe: unusable
            backup = Path(str(app) + '.old')
            backup.mkdir()
            (backup / 'Monster-ww.exe').write_text('old')
            launched = []
            with patch.object(c, 'relaunch',
                              side_effect=lambda d, e=c.EXE_NAME: launched.append(d)):
                self.assertTrue(c.revive(app))
            self.assertEqual(len(launched), 1)
            self.assertEqual((app / 'Monster-ww.exe').read_text(), 'old')

    def test_revive_reports_failure_when_nothing_is_runnable(self):
        with tempfile.TemporaryDirectory() as work:
            app = Path(work) / 'Monster-ww'
            app.mkdir()
            self.assertFalse(c.revive(app))


if __name__ == '__main__':
    unittest.main()
