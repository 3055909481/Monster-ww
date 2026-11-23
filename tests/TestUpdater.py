"""应用内更新器测试：版本比较、Atom 解析、直链构造、更新暂存。"""
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch as mock_patch

from src import updater as u

ATOM = '''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>tag:github.com,2008:Repository/1/v1.0.28</id>
    <title>v1.0.28</title>
    <updated>2026-11-22T11:57:38Z</updated>
    <content type="html">&lt;p&gt;fix A &amp;amp; B&lt;/p&gt;</content>
  </entry>
  <entry>
    <id>tag:github.com,2008:Repository/1/v1.0.27</id>
    <title>v1.0.27</title>
    <updated>2026-11-22T11:25:34Z</updated>
    <content type="html">&lt;p&gt;previous&lt;/p&gt;</content>
  </entry>
  <entry>
    <id>tag:github.com,2008:Repository/1/v2.0.0-beta.1</id>
    <title>v2.0.0-beta.1</title>
    <updated>2026-11-23T00:00:00Z</updated>
    <content type="html">&lt;p&gt;beta&lt;/p&gt;</content>
  </entry>
  <entry>
    <id>tag:github.com,2008:Repository/1/not-a-version</id>
    <title>not-a-version</title>
    <updated>2026-11-24T00:00:00Z</updated>
    <content type="html">&lt;p&gt;ignored&lt;/p&gt;</content>
  </entry>
</feed>
'''


class TestVersionComparison(unittest.TestCase):

    def test_numeric_versions(self):
        self.assertTrue(u.is_greater_version('v1.0.27', 'v1.0.26'))
        self.assertFalse(u.is_greater_version('v1.0.26', 'v1.0.27'))
        self.assertFalse(u.is_greater_version('v1.0.27', 'v1.0.27'))
        self.assertTrue(u.is_greater_version('v2.0.0', 'v1.9.9'))

    def test_prerelease_versions(self):
        self.assertTrue(u.is_greater_version('v1.0.27', 'v1.0.27-beta.1'))
        self.assertTrue(u.is_greater_version('v1.0.27-rc.1', 'v1.0.27-beta.2'))
        self.assertFalse(u.is_greater_version('v1.0.27-alpha.1', 'v1.0.27-beta.1'))

    def test_invalid_versions(self):
        self.assertFalse(u.is_greater_version('dev', 'v1.0.0'))
        self.assertFalse(u.is_greater_version('', 'v1.0.0'))


class TestAtomFeed(unittest.TestCase):

    def test_parses_entries_and_strips_html(self):
        releases = u._releases_from_atom(ATOM, limit=10)
        self.assertEqual(len(releases), 4)
        self.assertEqual(releases[0]['tag'], 'v1.0.28')
        self.assertEqual(releases[0]['body'], 'fix A & B')
        self.assertEqual(releases[0]['updated'], '2026-11-22T11:57:38Z')

    def test_filters_and_relations(self):
        with mock_patch.object(u, '_http_text', return_value=ATOM), \
             mock_patch.object(u, '_current_version', return_value='v1.0.27'):
            versions = u.get_version_list(release_only=True)
        self.assertEqual([v['version'] for v in versions], ['v1.0.28', 'v1.0.27'])
        self.assertEqual(versions[0]['relation'], 'newer')
        self.assertEqual(versions[1]['relation'], 'current')
        self.assertEqual(versions[0]['update_note'], ['fix A & B'])

    def test_skips_prerelease_when_release_only(self):
        with mock_patch.object(u, '_http_text', return_value=ATOM), \
             mock_patch.object(u, '_current_version', return_value='v1.0.27'):
            only = u.get_version_list(release_only=True)
            everything = u.get_version_list(release_only=False)
        self.assertNotIn('v2.0.0-beta.1', [v['version'] for v in only])
        self.assertIn('v2.0.0-beta.1', [v['version'] for v in everything])

    def test_uses_atom_url_without_api(self):
        with mock_patch.object(u, '_http_text', return_value=ATOM) as fetch, \
             mock_patch.object(u, '_current_version', return_value='v1.0.27'):
            u.get_version_list()
        self.assertEqual(fetch.call_args.args[0], u.RELEASES_ATOM)
        self.assertNotIn('api.github.com', u.RELEASES_ATOM)

    def test_reports_check_phase_to_ui(self):
        statuses = []
        with mock_patch.object(u, '_status', side_effect=statuses.append), \
             mock_patch.object(u, '_http_text', return_value=ATOM), \
             mock_patch.object(u, '_current_version', return_value='v1.0.27'):
            u.get_version_list()
        self.assertIn('正在检查更新…', statuses)
        self.assertTrue(any('2 个版本' in s for s in statuses))

    def test_network_failure_raises_readable_error(self):
        with mock_patch.object(u, '_http_text', side_effect=OSError('offline')):
            with self.assertRaises(RuntimeError) as ctx:
                u.get_version_list()
        self.assertIn('无法检查更新', str(ctx.exception))

    def test_rate_limit_message_passes_through(self):
        with mock_patch.object(u, '_http_text', side_effect=RuntimeError('GitHub 检查次数已达上限')):
            with self.assertRaises(RuntimeError) as ctx:
                u.get_version_list()
        self.assertIn('GitHub 检查次数已达上限', str(ctx.exception))

    def test_update_notes(self):
        versions = [{'version': 'v1.0.28', 'update_note': ['fix A', 'fix B']}]
        notes = u.calculate_update_notes(versions, 'v1.0.27', 'v1.0.28')
        self.assertEqual(notes, ['fix A', 'fix B'])


class TestAssets(unittest.TestCase):

    def test_asset_candidates_are_direct_download_links(self):
        assets = u._asset_candidates('v1.0.28')
        self.assertEqual(assets[0]['name'], 'Monster-ww-v1.0.28-windows-x64.7z')
        self.assertTrue(assets[0]['url'].startswith('https://github.com/'))
        self.assertIn('/releases/download/v1.0.28/', assets[0]['url'])
        self.assertNotIn('api.github.com', assets[0]['url'])
        self.assertEqual(assets[1]['name'], 'Monster-ww-v1.0.28-windows-x64.zip')

    def test_asset_for_prefers_available_head(self):
        assets = u._asset_candidates('v1.0.28')
        with mock_patch.object(u, '_head_ok', side_effect=[False, True, False]):
            chosen = u._asset_for('v1.0.28')
        self.assertEqual(chosen, assets[1])

    def test_sha256_for(self):
        digest = 'a' * 64
        with mock_patch.object(u, '_http_text', return_value=f'{digest}  Monster-ww.7z\n'):
            self.assertEqual(u._sha256_for({'name': 'Monster-ww.7z', 'url': 'https://example/x'}), digest)

    def test_extract_7z_without_py7zr_has_readable_error(self):
        with mock_patch.dict(sys.modules, {'py7zr': None}):
            with self.assertRaises(RuntimeError) as ctx:
                u._extract(Path('dummy.7z'), Path('out'))
        self.assertIn('py7zr', str(ctx.exception))
        self.assertIn('重新下载完整安装包', str(ctx.exception))

    def test_extract_zip(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            archive = root / 'app.zip'
            with zipfile.ZipFile(archive, 'w') as zf:
                zf.writestr('Monster-ww/Monster-ww.exe', 'fake')
            target = root / 'out'
            target.mkdir()
            u._extract(archive, target)
            self.assertTrue((target / 'Monster-ww' / 'Monster-ww.exe').is_file())


class TestInstall(unittest.TestCase):

    def test_install_sets_expected_api(self):
        from types import SimpleNamespace
        fake = SimpleNamespace(app_version=None)
        with mock_patch.dict(sys.modules, {'pyappify': fake}):
            with mock_patch.object(u, '_current_version', return_value='v1.0.27'):
                result = u.install()
        self.assertTrue(result)
        self.assertEqual(fake.app_version, 'v1.0.27')
        self.assertIs(fake.get_version_list, u.get_version_list)
        self.assertIs(fake.update_to_version, u.update_to_version)
        self.assertIs(fake.is_greater_version, u.is_greater_version)

    def test_install_is_idempotent(self):
        from types import SimpleNamespace
        fake = SimpleNamespace(app_version=None)
        with mock_patch.dict(sys.modules, {'pyappify': fake, 'src.identity': None}):
            with mock_patch.object(u, '_current_version', return_value='v1.0.27'):
                self.assertTrue(u.install())
                fake.get_version_list = Mock()  # would be overwritten if re-installed
                self.assertFalse(u.install())
                self.assertIs(fake.get_version_list, u.get_version_list)

    def test_status_reporter_never_raises(self):
        def broken(_message):
            raise RuntimeError('ui gone')
        with mock_patch.object(u, '_status_reporter', broken):
            u._status('正在下载…')  # must not propagate


class TestApplyScript(unittest.TestCase):

    def test_apply_script_uses_cmd_and_relaunches(self):
        script = u._APPLY_SCRIPT.format(app_dir='C:/app', staged='C:/staged',
                                        staging='C:/tmp', pid=123, exe_name='Monster-ww.exe')
        self.assertIn('@echo off', script)
        self.assertIn('set "OLD_PID=123"', script)
        self.assertIn('tasklist /FI "PID eq %OLD_PID%"', script)
        self.assertIn('move /Y "%STAGED%" "%APP_DIR%"', script)
        self.assertIn('start "" /D "%APP_DIR%"', script)
        self.assertIn('Monster-ww.exe', script)

    def test_apply_script_escapes_backslash_for_cmd(self):
        script = u._APPLY_SCRIPT.format(app_dir='C:/app', staged='C:/staged',
                                        staging='C:/tmp', pid=123, exe_name='Monster-ww.exe')
        self.assertIn('%APP_DIR%\\%EXE_NAME%', script)


class TestApplyLaunch(unittest.TestCase):

    def test_apply_script_runs_with_hidden_window(self):
        """The apply cmd must not flash a findstr console while waiting for exit."""
        import subprocess as sp
        root = Path(tempfile.mkdtemp())
        app = root / 'app'
        app.mkdir()
        staging = root / 'staging'

        def fake_extract(_archive, target):
            exe_dir = target / u.APP_DIR_NAME
            exe_dir.mkdir(parents=True, exist_ok=True)
            (exe_dir / u.EXE_NAME).write_bytes(b'')

        popen = Mock()
        with mock_patch.object(u, '_asset_for', return_value={'name': 'x.7z', 'url': 'u'}), \
             mock_patch.object(u, '_sha256_for', return_value=None), \
             mock_patch.object(u, '_download'), \
             mock_patch.object(u, '_extract', side_effect=fake_extract), \
             mock_patch.object(u, '_app_dir', return_value=app), \
             mock_patch.object(u.tempfile, 'gettempdir', return_value=str(staging)), \
             mock_patch.object(u.subprocess, 'Popen', popen):
            u.update_to_version('v9.9.9')
        kwargs = popen.call_args.kwargs
        flags = kwargs['creationflags']
        self.assertTrue(flags & sp.CREATE_NO_WINDOW,
                        'apply script must use CREATE_NO_WINDOW')
        self.assertFalse(flags & sp.DETACHED_PROCESS,
                         'DETACHED_PROCESS still allocates a console for cmd.exe')


if __name__ == '__main__':
    unittest.main()
