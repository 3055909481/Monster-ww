"""顶部队伍条测试：识别逻辑、中文名映射、patch 幂等（纯逻辑，不实例化 Qt）。"""
import os
import unittest
from unittest.mock import Mock, patch as mock_patch

# CI 无显示会话下创建 QWidget/QPixmap 会在解释器退出时触发 Qt 析构 access
# violation（即使测试本身全过）。必须在 import PySide6 之前强制 offscreen。
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from src import team_overlay as to
from src.char.CharFactory import char_dict


def _char(cls_name, canonical=None):
    # 用真实类而非 Mock，保证 type(char).__name__ 可用于兜底分支
    CharCls = type(cls_name, (), {'char_name': None})
    c = CharCls()
    # 真实 char.char_name 存的是 Labels 枚举成员（见 CharFactory:181）。
    # 从 char_dict 反查该角色类的 canonical key，避免硬编码（Changli 是 chang_changli）。
    key = canonical
    if key is None:
        for dict_key, info in char_dict.items():
            if getattr(info.get('cls'), '__name__', None) == cls_name:
                key = info.get('canonical_name')
                break
        key = key or ('char_' + cls_name.lower())
    c.char_name = key
    return c


def _task(chars):
    t = Mock()
    t.chars = chars
    return t


def _executor(trigger=None, onetime=None):
    ex = Mock()
    ex.trigger_tasks = trigger if trigger is not None else []
    ex.onetime_tasks = onetime if onetime is not None else []
    return ex


class TestTeamFromTasks(unittest.TestCase):

    def test_no_tasks_returns_none(self):
        self.assertIsNone(to.team_from_tasks(_executor()))

    def test_incomplete_team_returns_none(self):
        ex = _executor(trigger=[_task([_char('Verina'), _char('Changli'), None])])
        self.assertIsNone(to.team_from_tasks(ex))

    def test_duplicate_chars_returns_none(self):
        ex = _executor(trigger=[_task([_char('Verina'), _char('Verina'), _char('Jinhsi')])])
        self.assertIsNone(to.team_from_tasks(ex))

    def test_full_team_from_trigger(self):
        ex = _executor(trigger=[_task([_char('Verina'), _char('Changli'), _char('Jinhsi')])])
        team = to.team_from_tasks(ex)
        self.assertIsNotNone(team)
        self.assertEqual([cls for cls, _ in team], ['Verina', 'Changli', 'Jinhsi'])

    def test_full_team_from_onetime(self):
        ex = _executor(onetime=[_task([_char('Verina'), _char('Changli'), _char('Jinhsi')])])
        team = to.team_from_tasks(ex)
        self.assertIsNotNone(team)
        self.assertEqual(len(team), 3)

    def test_skips_incomplete_task_finds_later_complete(self):
        ex = _executor(trigger=[
            _task([_char('A'), None, None]),
            _task([_char('Verina'), _char('Changli'), _char('Jinhsi')]),
        ])
        team = to.team_from_tasks(ex)
        self.assertIsNotNone(team)
        self.assertEqual([cls for cls, _ in team], ['Verina', 'Changli', 'Jinhsi'])

    def test_task_without_chars_is_skipped(self):
        t = Mock(spec=[])  # 没有 chars 属性
        ex = _executor(trigger=[t])
        self.assertIsNone(to.team_from_tasks(ex))


class TestClassName(unittest.TestCase):

    def test_maps_template_name_to_class_and_label(self):
        # 真实 char_dict 里 char_verina -> Verina；i18n 可能未加载，label 至少非空
        c = _char('Verina')
        cls_name, label = to._class_name(c)
        self.assertEqual(cls_name, 'Verina')
        self.assertTrue(label)

    def test_unknown_key_falls_back_to_instance_class(self):
        c = _char('CustomHero', 'nonexistent_key')
        cls_name, label = to._class_name(c)
        self.assertEqual(cls_name, 'CustomHero')
        self.assertTrue(label)


class TestTeamStripRefresh(unittest.TestCase):
    """TeamStrip 渲染状态：需 Qt offscreen 环境。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _strip(self, executor):
        strip = to.TeamStrip()
        original = strip.executor

        def fake_executor():
            return executor

        strip.__class__ = type('StripWithEx', (to.TeamStrip,), {'executor': property(lambda s: executor)})
        return strip

    def test_unrecognized_shows_placeholder_not_hidden(self):
        ex = _executor()  # 无任务 = 未识别
        strip = self._strip(ex)
        # 先模拟曾识别过，再刷新为未识别
        strip._last_signature = ('Verina', 'Changli', 'Jinhsi')
        for avatar in strip._avatars:
            avatar.set_char('Verina', '维里奈')
        strip.refresh()
        self.assertTrue(strip.isVisible() or not strip.isHidden(),
                        '未识别时不应隐藏队伍条')
        self.assertEqual(strip.name_label.text(), '未识别')
        for avatar in strip._avatars:
            self.assertIsNone(avatar._pixmap)
            self.assertEqual(avatar._text, '')

    def test_recognized_shows_team(self):
        members = [_char('Verina'), _char('Changli'), _char('Jinhsi')]
        ex = _executor(trigger=[_task(members)])
        strip = self._strip(ex)
        strip.refresh()
        # 测试环境未加载 app i18n，tr() 返回类名原文；真实运行会显示中文名
        text = strip.name_label.text()
        self.assertIn(' · ', text)  # 三人名以分隔符连接
        self.assertTrue(any(n in text for n in ('Verina', '维里奈')))
        self.assertTrue(any(n in text for n in ('Changli', '长离')))
        self.assertTrue(any(n in text for n in ('Jinhsi', '今汐')))
        self.assertEqual(strip._last_signature, ('Verina', 'Changli', 'Jinhsi'))

    def test_transition_back_to_unrecognized_clears(self):
        members = [_char('Verina'), _char('Changli'), _char('Jinhsi')]
        ex = _executor(trigger=[_task(members)])
        strip = self._strip(ex)
        strip.refresh()  # 先识别
        self.assertIsNotNone(strip._last_signature)
        ex.trigger_tasks = []  # 变为未识别
        strip.refresh()
        self.assertIsNone(strip._last_signature)
        self.assertEqual(strip.name_label.text(), '未识别')
        for avatar in strip._avatars:
            self.assertIsNone(avatar._pixmap)
            self.assertEqual(avatar._text, '')


class TestInstall(unittest.TestCase):

    def test_install_is_idempotent(self):
        class FakeRunBar:
            pass

        with mock_patch.object(to, '_INSTALLED', False), \
             mock_patch('Mon.ui.qt.shell.run_bar.RunBar', FakeRunBar, create=True):
            self.assertTrue(to.install())
            self.assertEqual(FakeRunBar.__init__, to._patched_run_bar_init)
            self.assertFalse(to.install())

    def test_install_failure_returns_false(self):
        with mock_patch.object(to, '_INSTALLED', False), \
             mock_patch('builtins.__import__', side_effect=Exception('boom')):
            self.assertFalse(to.install())


if __name__ == '__main__':
    unittest.main()
