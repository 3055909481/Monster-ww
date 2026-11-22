"""应用内更新器测试：版本比较、版本列表解析、GitHub 交互、更新暂存。"""
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch as mock_patch

from src import updater as u


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


class TestVersionList(unittest.TestCase):

    def _release(self, tag, assets=True, prerelease=False):
        return {
            'tag_name': tag,
            'prerelease': prerelease,
            'body': f'release notes for {tag}',
            'assets': ([{'name': f'Monster-ww-{tag}-windows-x64.7z',
                         'browser_download_url': f'https://example.com/{tag}.7z',
                         'digest': 'sha256:abc'}]
                       if assets else []),
        }

    def test_filters_and_relations(self):
        releases = [self._release('v1.0.28'), self._release('v1.0.27'),
                    self._release('v1.0.29', assets=False)]
        with mock_patch.object(u, '_http_json', return_value=releases), \
             mock_patch.object(u, '_current_version', return_value='v1.0.27'):
            versions = u.get_version_list(release_only=True)
        self.assertEqual([v['version'] for v in versions], ['v1.0.28', 'v1.0.27'])
        self.assertEqual(versions[0]['relation'], 'newer')
        self.assertEqual(versions[1]['relation'], 'current')

    def test_skips_prerelease_when_release_only(self):
        releases = [self._release('v1.0.28'), self._release('v2.0.0-beta.1', prerelease=True)]
        with mock_patch.object(u, '_http_json', return_value=releases), \
             mock_patch.object(u, '_current_version', return_value='v1.0.27'):
            only = u.get_version_list(release_only=True)
            all_versions = u.get_version_list(release_only=False)
        self.assertEqual([v['version'] for v in only], ['v1.0.28'])
        self.assertIn('v2.0.0-beta.1', [v['version'] for v in all_versions])

    def test_network_failure_raises_readable_error(self):
        with mock_patch.object(u, '_http_json', side_effect=OSError('offline')):
            with self.assertRaises(RuntimeError) as ctx:
                u.get_version_list()
        self.assertIn('无法检查更新', str(ctx.exception))

    def test_update_notes(self):
        versions = [{'version': 'v1.0.28', 'update_note': ['fix A', 'fix B']}]
        notes = u.calculate_update_notes(versions, 'v1.0.27', 'v1.0.28')
        self.assertEqual(notes, ['fix A', 'fix B'])


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
        with mock_patch.dict(sys.modules, {'pyappify': fake}):
            with mock_patch.object(u, '_current_version', return_value='v1.0.27'):
                self.assertTrue(u.install())
                fake.get_version_list = Mock()  # would be overwritten if re-installed
                self.assertFalse(u.install())
                self.assertIs(fake.get_version_list, u.get_version_list)


class TestApplyScript(unittest.TestCase):

    def test_apply_script_waits_and_relaunches(self):
        script = u._APPLY_SCRIPT.format(app_dir='C:/app', staged='C:/staged',
                                        staging='C:/tmp', pid=123, exe_name='Monster-ww.exe')
        self.assertIn('old_pid = 123', script)
        self.assertIn('os.kill(old_pid, 0)', script)
        self.assertIn('Monster-ww.exe', script)
        self.assertIn('os.rename(app_dir, backup)', script)
        self.assertIn('os.rename(staged, app_dir)', script)
        compile(script, 'apply_update.py', 'exec')


if __name__ == '__main__':
    unittest.main()
