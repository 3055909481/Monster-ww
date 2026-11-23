"""应用内更新检查器测试：版本比较、Atom 解析、交接给独立更新程序。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch as mock_patch

from src import updater as u

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

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
        self.assertTrue(u.is_greater_version('v1.0.28', 'v1.0.27'))
        self.assertFalse(u.is_greater_version('v1.0.27', 'v1.0.28'))

    def test_prerelease_versions(self):
        self.assertTrue(u.is_greater_version('v1.0.28', 'v1.0.28-beta.1'))
        self.assertTrue(u._is_prerelease('v2.0.0-beta.1'))
        self.assertFalse(u._is_prerelease('v1.0.28'))

    def test_invalid_versions(self):
        self.assertFalse(u.is_greater_version('dev', 'v1.0.27'))
        self.assertIsNone(u._parse_version('not-a-version'))


class TestAtomFeed(unittest.TestCase):

    def test_parses_entries_and_strips_html(self):
        with mock_patch.object(u, '_http_text', return_value=ATOM), \
             mock_patch.object(u, '_current_version', return_value='v1.0.27'):
            versions = u.get_version_list()
        self.assertEqual([v['version'] for v in versions], ['v1.0.28', 'v1.0.27'])
        self.assertIn('fix A', versions[0]['update_note'][0])

    def test_filters_and_relations(self):
        with mock_patch.object(u, '_http_text', return_value=ATOM), \
             mock_patch.object(u, '_current_version', return_value='v1.0.27'):
            versions = u.get_version_list()
        self.assertEqual(versions[0]['relation'], 'newer')
        self.assertEqual(versions[1]['relation'], 'current')

    def test_skips_prerelease_when_release_only(self):
        with mock_patch.object(u, '_http_text', return_value=ATOM), \
             mock_patch.object(u, '_current_version', return_value='v1.0.27'):
            all_versions = u.get_version_list(release_only=False)
        self.assertIn('v2.0.0-beta.1', [v['version'] for v in all_versions])

    def test_uses_atom_url_without_api(self):
        with mock_patch.object(u, '_http_text', return_value=ATOM) as fetch, \
             mock_patch.object(u, '_current_version', return_value='v1.0.27'):
            u.get_version_list()
        self.assertIn('releases.atom', fetch.call_args.args[0])
        self.assertNotIn('api.github.com', fetch.call_args.args[0])

    def test_network_failure_raises_readable_error(self):
        with mock_patch.object(u, '_http_text', side_effect=OSError('offline')):
            with self.assertRaises(RuntimeError) as ctx:
                u.get_version_list()
        self.assertIn('无法检查更新', str(ctx.exception))

    def test_rate_limit_message_passes_through(self):
        limited = RuntimeError('GitHub 检查次数已达上限')
        with mock_patch.object(u, '_http_text', side_effect=limited):
            with self.assertRaises(RuntimeError) as ctx:
                u.get_version_list()
        self.assertIn('检查次数已达上限', str(ctx.exception))

    def test_update_notes(self):
        versions = [{'version': 'v1.0.28', 'update_note': ['note A', '']},
                    {'version': 'v1.0.27', 'update_note': ['note B']}]
        self.assertEqual(u.calculate_update_notes(versions, 'v1.0.27', 'v1.0.28'), ['note A'])
        self.assertEqual(u.entry_notes(versions[0]), ['note A', ''])
        self.assertEqual(u.calculate_update_notes(versions, 'v1.0.27', 'v9'), [])


class TestInstall(unittest.TestCase):

    def test_install_sets_expected_api(self):
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
        fake = SimpleNamespace(app_version=None)
        with mock_patch.dict(sys.modules, {'pyappify': fake}):
            with mock_patch.object(u, '_current_version', return_value='v1.0.27'):
                self.assertTrue(u.install())
                fake.get_version_list = Mock()
                self.assertFalse(u.install())
                self.assertIs(fake.get_version_list, u.get_version_list)


class TestUpdateHandoff(unittest.TestCase):
    """Updating must only launch the standalone GUI updater, nothing else."""

    def test_launches_standalone_updater_with_args(self):
        app_dir = Path(tempfile.mkdtemp())
        (app_dir / 'MonsterUpdate.exe').write_bytes(b'fake')
        popen = Mock()
        with mock_patch.object(u, '_app_dir', return_value=app_dir), \
             mock_patch.object(u.os, 'name', 'nt'), \
             mock_patch('update_core.subprocess.Popen', popen), \
             mock_patch('update_core.tempfile.gettempdir',
                        return_value=str(app_dir / 'temp')), \
             mock_patch.object(u.os, 'getpid', return_value=4242):
            result = u.update_to_version('v9.9.9')
        self.assertEqual(result, {'updated': True, 'restart_required': True})
        args = popen.call_args.args[0]
        self.assertTrue(str(args[0]).endswith('MonsterUpdate.exe'))
        self.assertIn('--version', args)
        self.assertIn('v9.9.9', args)
        self.assertIn('--app-dir', args)
        self.assertIn(str(app_dir), args)
        self.assertIn('--pid', args)
        self.assertIn('4242', args)
        # The updater is copied out of app_dir so it can replace its own file.
        self.assertNotEqual(str(args[0]), str(app_dir / 'MonsterUpdate.exe'))

    def test_missing_updater_has_readable_error(self):
        app_dir = Path(tempfile.mkdtemp())
        with mock_patch.object(u, '_app_dir', return_value=app_dir), \
             mock_patch.object(u.os, 'name', 'nt'):
            with self.assertRaises(RuntimeError) as ctx:
                u.update_to_version('v9.9.9')
        self.assertIn('MonsterUpdate.exe', str(ctx.exception))
        self.assertIn('手动下载', str(ctx.exception))

    def test_non_windows_is_rejected(self):
        with mock_patch.object(u.os, 'name', 'posix'):
            with self.assertRaises(RuntimeError) as ctx:
                u.update_to_version('v9.9.9')
        self.assertIn('Windows', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
