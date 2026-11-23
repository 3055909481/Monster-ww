"""独立更新程序核心逻辑测试：进度回调、校验、解压、安装与回滚。"""
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import update_core as c


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


if __name__ == '__main__':
    unittest.main()
