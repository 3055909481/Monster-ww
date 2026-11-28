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
        """守卫：框架原序确实是 adb 优先（不然 patch 就多余）。

        编译后的 CI 环境没有 .mon-tmp 源码，只有开发 checkout 才有；
        这种环境下跳过即可，patch 行为由其它测试覆盖。
        """
        source = Path('.mon-tmp/Mon/device/DeviceManager.py')
        if not source.exists():
            self.skipTest('framework source not present in compiled build')
        src = source.read_text(encoding='utf-8')
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
        # 用真实类（不是 MagicMock）验证描述符绑定：MagicMock 的属性访问不走
        # __get__，会掩盖 v1.0.77 的 "self 丢失" 崩溃。
        class DM:
            def get_devices(self):
                return []

            def set_preferred_device(self, imei=None, index=-1):
                return None

        dsp._INSTALLED = False
        dsp._PATCHED = None
        with patch.dict('sys.modules', {'Mon': MagicMock(),
                                        'Mon.device': MagicMock(),
                                        'Mon.device.DeviceManager': MagicMock(DeviceManager=DM)}):
            dsp.install()
        self.assertTrue(dsp._INSTALLED)
        self.assertIsInstance(DM.__dict__['get_devices'], dsp._BoundMethod)

        # 关键：实例调用（不显式传 self）必须正常工作且 self 正确绑定
        mgr = FakeDeviceManager([adb(), browser(), pc()])
        dm = DM()
        dm.device_dict = mgr.device_dict
        dm.config = mgr.config
        self.assertEqual(['pc', 'browser', 'adb'],
                         [d['imei'] for d in dm.get_devices()])

    def test_bound_method_binds_self_like_cython_builtin(self):
        """守卫：Cython 把函数编译成无 __get__ 的 builtin 时，实例调用不能丢 self。

        v1.0.77 崩溃根因就是裸函数被编译后 ``instance.get_devices()`` 不传
        self，报 "takes exactly 1 positional argument (0 given)"。
        """
        def bare_get(self):
            return list(self.device_dict)

        class FakeBuiltin:
            """模拟 Cython 编译产物：函数没有 __get__，不自动绑定。"""
            def __init__(self, fn):
                self.fn = fn

            def __call__(self, *args, **kwargs):
                return self.fn(*args, **kwargs)

        bound = dsp._BoundMethod(FakeBuiltin(bare_get))

        class Host:
            get_devices = bound

        host = Host()
        host.device_dict = {'a': 1, 'b': 2}
        # 实例调用，self 必须由描述符闭包注入
        self.assertEqual(['a', 'b'], host.get_devices())

    def test_browser_enabled_in_config(self):
        src = Path('config.py').read_text(encoding='utf-8')
        self.assertIn("    'browser': {", src)
        self.assertIn("'url': 'https://mc.kurogames.com/cloud/#/'", src)


if __name__ == '__main__':
    unittest.main()
