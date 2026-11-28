"""触发任务开关必须立即持久化，杜绝「界面开了、重启又关」。"""

import unittest
from unittest.mock import MagicMock, patch

from src.trigger_switch_patch import _persist, install


class FakeConfig(dict):
    """模拟框架 Config：__setitem__ 值变化时立即 save_file。"""

    def __init__(self, data):
        super().__init__(data)
        self.saves = 0

    def __setitem__(self, key, value):
        if self.get(key) != value:
            super().__setitem__(key, value)
            self.save_file()

    def save_file(self):
        self.saves += 1


class TestPersist(unittest.TestCase):

    def test_enable_writes_config_immediately(self):
        cfg = FakeConfig({'_enabled': False, 'Other': 1})
        task = MagicMock()
        task.config = cfg
        self.assertTrue(_persist(task, True))
        self.assertTrue(cfg['_enabled'])
        self.assertEqual(1, cfg.saves)  # 立即写盘
        self.assertEqual(1, cfg['Other'])  # 其它设置不动

    def test_disable_writes_config_immediately(self):
        cfg = FakeConfig({'_enabled': True})
        task = MagicMock()
        task.config = cfg
        self.assertTrue(_persist(task, False))
        self.assertFalse(cfg['_enabled'])
        self.assertEqual(1, cfg.saves)

    def test_no_rewrite_when_value_unchanged(self):
        cfg = FakeConfig({'_enabled': True})
        task = MagicMock()
        task.config = cfg
        self.assertTrue(_persist(task, True))
        self.assertEqual(0, cfg.saves)  # 值没变不写盘

    def test_missing_config_is_noop(self):
        task = MagicMock()
        task.config = None
        self.assertFalse(_persist(task, True))


class TestInstall(unittest.TestCase):

    def test_wraps_set_trigger_enabled(self):
        fake_module = MagicMock()
        original = lambda task, enabled: None
        fake_module.set_trigger_enabled = original
        with patch('importlib.import_module', return_value=fake_module):
            # 重置模块安装标志以测试包装
            import src.trigger_switch_patch as m
            m._INSTALLED = False
            m._PATCHED = None
            m.install()
        wrapped = fake_module.set_trigger_enabled
        self.assertIsNot(wrapped, original)  # 必须被换成包装函数
        self.assertIs(m._PATCHED, wrapped)  # 记录包装函数用于幂等

        cfg = FakeConfig({'_enabled': False})
        task = MagicMock()
        task.config = cfg
        wrapped(task, True)
        self.assertTrue(cfg['_enabled'])  # 包装后先落盘

    def test_double_install_is_noop(self):
        fake_module = MagicMock()
        original = lambda task, enabled: None
        fake_module.set_trigger_enabled = original
        import src.trigger_switch_patch as m
        m._INSTALLED = False
        m._PATCHED = None
        with patch('importlib.import_module', return_value=fake_module):
            m.install()
            first = fake_module.set_trigger_enabled
            m._INSTALLED = False  # 再次 install 靠 _PATCHED 比对避免二次包装
            m.install()
        self.assertIs(first, fake_module.set_trigger_enabled)


if __name__ == '__main__':
    unittest.main()
