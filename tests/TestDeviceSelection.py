"""设备选择：默认 PC、顺序 PC→云鸣潮→安卓、仅手动切换。"""

import inspect
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import src.device_selection_patch as dsp


class FakeDeviceManager:
    """最小的 DeviceManager 替身，带真实 patch 需要的所有属性。"""

    def __init__(self, devices):
        self.device_dict = {d['imei']: d for d in devices}
        self.config = {'preferred': ''}
        self.started = []
        self.calls = []

    def start(self):
        self.started.append(True)


def pc(imei='pc'):
    return {'imei': imei, 'device': 'windows'}


def browser(imei='browser'):
    return {'imei': imei, 'device': 'browser'}


def adb(imei='adb'):
    return {'imei': imei, 'device': 'adb', 'connected': True}


class TestSortOrder(unittest.TestCase):

    def test_pc_cloud_android_order(self):
        mgr = FakeDeviceManager([adb(), browser(), pc()])
        result = dsp._sorted_devices(mgr)
        self.assertEqual(['pc', 'browser', 'adb'], [d['imei'] for d in result])

    def test_unknown_type_goes_last(self):
        mgr = FakeDeviceManager([adb(), {'imei': 'x', 'device': 'other'}, pc(), browser()])
        result = dsp._sorted_devices(mgr)
        self.assertEqual(['pc', 'browser', 'adb', 'x'], [d['imei'] for d in result])

    def test_framework_default_order_is_reversed(self):
        """守卫：框架原序确实是 adb 优先（不然 patch 就多余）。"""
        src = Path('.mon-tmp/Mon/device/DeviceManager.py').read_text(encoding='utf-8')
        body = src[src.index('    def get_devices(self):'):src.index('    def _replace_pc_devices')]
        self.assertLess(body.index("'adb'"), body.index("'windows'"))
        self.assertLess(body.index("'windows'"), body.index("'browser'"))


class TestDefaultSelection(unittest.TestCase):

    def setUp(self):
        # 让 patch 里的 _original_set_preferred 走一个记录型替身。
        # 第一参数是 DeviceManager 实例，所以用闭包外的 recorded 列表，
        # 不要误读成测试实例的 self.calls。
        recorded = self.calls = []
        dsp._original_set_preferred = lambda mgr, imei, index: recorded.append((imei, index))

    def test_auto_picks_pc_not_connected_adb(self):
        mgr = FakeDeviceManager([adb(), browser(), pc()])
        dsp._set_preferred_device(mgr)
        self.assertEqual(('pc', -1), self.calls[-1])

    def test_auto_respects_saved_manual_choice(self):
        mgr = FakeDeviceManager([adb(), browser(), pc()])
        mgr.config['preferred'] = 'adb'  # 用户上次手动选的
        dsp._set_preferred_device(mgr)
        self.assertEqual(('adb', -1), self.calls[-1])

    def test_explicit_imei_passed_through_untouched(self):
        mgr = FakeDeviceManager([adb(), browser(), pc()])
        dsp._set_preferred_device(mgr, 'browser')
        self.assertEqual(('browser', -1), self.calls[-1])

    def test_explicit_index_passed_through_untouched(self):
        mgr = FakeDeviceManager([adb(), browser(), pc()])
        dsp._set_preferred_device(mgr, index=2)
        self.assertEqual((None, 2), self.calls[-1])

    def test_no_pc_leaves_unchanged(self):
        mgr = FakeDeviceManager([adb(), browser()])
        dsp._set_preferred_device(mgr)
        self.assertEqual([], self.calls)


class TestInstall(unittest.TestCase):

    def test_patches_both_methods(self):
        fake_dm = MagicMock()
        fake_dm.get_devices = lambda self: []
        fake_dm.set_preferred_device = lambda self, imei=None, index=-1: None
        dsp._INSTALLED = False
        with patch.dict('sys.modules', {'Mon': MagicMock(),
                                        'Mon.device': MagicMock(),
                                        'Mon.device.DeviceManager': MagicMock(DeviceManager=fake_dm)}):
            dsp.install()
        self.assertTrue(getattr(fake_dm.get_devices, '_device_patch_wrapped', False))
        self.assertTrue(getattr(fake_dm.set_preferred_device, '_device_patch_wrapped', False))

    def test_browser_enabled_in_config(self):
        src = Path('config.py').read_text(encoding='utf-8')
        self.assertIn("    'browser': {", src)
        self.assertIn("'url': 'https://mc.kurogames.com/cloud/#/'", src)


if __name__ == '__main__':
    unittest.main()
