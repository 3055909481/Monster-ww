"""在线更新确认框补丁：验证用户取消时不更新、确认后才更新。"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src import update_confirm


def make_card(version='v1.0.46', notes=None):
    card = SimpleNamespace()
    entry = {'version': version, 'title': version}
    if notes is not None:
        entry['update_note'] = notes
    card.versions = [entry]
    combo = Mock()
    combo.currentIndex.return_value = 0
    card.version_combo = combo
    return card


class TestUpdateConfirm(unittest.TestCase):

    def test_cancel_does_not_call_original(self):
        original = Mock()
        with patch.object(update_confirm, '_ORIGINAL', original), \
             patch.object(update_confirm, '_confirm', return_value=False):
            card = make_card()
            update_confirm.update_to_selected_version(card)
        original.assert_not_called()

    def test_confirm_calls_original(self):
        original = Mock(return_value='done')
        with patch.object(update_confirm, '_ORIGINAL', original), \
             patch.object(update_confirm, '_confirm', return_value=True):
            card = make_card()
            result = update_confirm.update_to_selected_version(card)
        original.assert_called_once_with(card)
        self.assertEqual('done', result)

    def test_confirm_dialog_shows_version_and_notes(self):
        with patch.object(update_confirm, '_ORIGINAL', Mock()), \
             patch.object(update_confirm, '_confirm', return_value=True) as confirm:
            card = make_card('v2.0.0', ['新增功能 A', '修复问题 B'])
            update_confirm.update_to_selected_version(card)
        args = confirm.call_args.args
        self.assertIn('v2.0.0', args[2])
        self.assertIn('新增功能 A', args[2])
        self.assertIn('修复问题 B', args[2])

    def test_missing_original_aborts_safely(self):
        with patch.object(update_confirm, '_ORIGINAL', None), \
             patch.object(update_confirm, '_confirm', return_value=True):
            # 不应抛异常
            update_confirm.update_to_selected_version(make_card())

    def test_selected_version_info_handles_missing_entry(self):
        card = make_card()
        card.version_combo.currentIndex.return_value = -1
        title, notes = update_confirm._selected_version_info(card)
        self.assertEqual(('', ''), (title, notes))

    def test_qt_clicked_bool_is_accepted(self):
        """Qt's clicked(bool) signal may pass a checked flag to the slot."""
        original = Mock(return_value='done')
        with patch.object(update_confirm, '_ORIGINAL', original), \
             patch.object(update_confirm, '_confirm', return_value=True):
            card = make_card()
            result = update_confirm.update_to_selected_version(card, False)
        original.assert_called_once_with(card)
        self.assertEqual('done', result)

    def test_stays_pure_python_when_packaged(self):
        """Cython cyfunctions cannot bind self when assigned onto framework classes.

        This patch is installed as UpdateCard.update_to_selected_version, so it
        MUST be excluded from .pyd compilation and shipped as source.
        """
        from pathlib import Path

        setup_source = Path('setup.py').read_text(encoding='utf-8')
        self.assertIn('"update_confirm.py"', setup_source)
        spec_source = Path('Monster-ww.spec').read_text(encoding='utf-8')
        self.assertIn("'update_confirm.py'", spec_source)
        workflow = Path('.github/workflows/portable-release.yml').read_text(encoding='utf-8')
        self.assertIn("'update_confirm.py'", workflow)

    def test_install_patches_update_card_once(self):
        fake_card_class = type('FakeUpdateCard', (), {'update_to_selected_version': lambda self: None})
        fake_module = SimpleNamespace(UpdateCard=fake_card_class)
        import sys
        with patch.dict(sys.modules, {'Mon.ui.qt.about.UpdateCard': fake_module}), \
             patch.object(update_confirm, '_INSTALLED', False), \
             patch.object(update_confirm, '_ORIGINAL', None):
            update_confirm.install()
            self.assertIs(update_confirm.update_to_selected_version,
                          fake_card_class.update_to_selected_version)
            self.assertTrue(update_confirm._INSTALLED)
            # 重复安装不重复包装
            current = fake_card_class.update_to_selected_version
            update_confirm.install()
            self.assertIs(current, fake_card_class.update_to_selected_version)


class LauncherUpdateSwitchTest(unittest.TestCase):
    """Online update must stay on the original standalone-launcher updater."""

    def test_config_installs_github_release_updater(self):
        source = Path('config.py').read_text(encoding='utf-8')
        self.assertIn('from src import updater as _updater', source)
        self.assertIn('_updater.install()', source)
        # The coco-style file-level incremental updater was reverted; guard
        # against it silently coming back via the old API check.
        self.assertNotIn('incremental updater API unavailable', source)


if __name__ == '__main__':
    unittest.main()
