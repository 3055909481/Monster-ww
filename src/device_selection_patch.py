"""设备选择：默认 PC、顺序 PC → 云鸣潮 → 安卓、绝不自动切换。

框架 DeviceManager 有两处行为与本项目要求冲突：
  1. ``get_devices()`` 排序为 adb(0) → windows(1) → browser(2)，导致安卓排最前、
     云鸣潮排最后；
  2. ``set_preferred_device()`` 无参数调用时（启动 / 设备列表刷新）会挑
     "第一个或已连接"的设备并 start()，导致首次启动或插拔模拟器时莫名跳到
     云鸣潮登录或安卓设备。

本项目在 ``main.py`` 早期 monkeypatch：
  * 排序固定为 windows(PC)=0 → browser(云鸣潮)=1 → adb(安卓)=2；
  * 无参数的自动回退固定选 PC，不再按"已连接"抢设备；
  * 显式调用（用户手动点选 / 脚本 start_adb / start_browser）完全不改。

框架是 pip 包，不改源码；任何一步失败都静默降级为框架原行为。

!! 编译版关键约束（曾导致 v1.0.77 全线崩溃）!!
Cython 把模块级/嵌套函数编译成 builtin_function_or_method：
  * 无法 setattr 自定义属性 -> 幂等标记必须用模块级变量；
  * 更严重：builtin 不实现描述符协议，直接赋值给类后 ``instance.method()``
    不会自动传 self，报 "takes exactly 1 positional argument (0 given)"。
所以这里用 ``_BoundMethod`` 描述符包装，在 ``__get__`` 时用闭包把实例绑定
进去，保证实例调用正确拿到 self。
"""

import logging

logger = logging.getLogger(__name__)

_INSTALLED = False
_PATCHED = None
_original_set_preferred = None


class _BoundMethod:
    """描述符：把 (self, ...) 函数绑定成类实例方法，兼容 Cython 编译环境。

    普通 Python 函数天然是描述符，但 Cython 编译后变成 builtin 就失去该特性。
    这个类的 __get__ 在 ``instance.method`` 访问时返回一个闭包，闭包里已把
    instance 固定为第一个参数，因此 ``instance.method(x)`` 等价于
    ``func(instance, x)``。
    """

    def __init__(self, func):
        self.func = func

    def __get__(self, instance, owner=None):
        if instance is None:
            return self.func
        func = self.func

        def bound(*args, **kwargs):
            return func(instance, *args, **kwargs)

        return bound


def _sorted_devices(self):
    devices = list(self.device_dict.values())
    order = {'windows': 0, 'browser': 1, 'adb': 2}

    def sort_key(device):
        return order.get(device.get('device'), 3)

    return sorted(devices, key=sort_key)


def _set_preferred_device(self, imei=None, index=-1):
    """无参自动调用时锁定 PC；显式指定（用户手动）保持框架行为。"""
    auto = imei is None and index == -1
    if auto:
        # 已保存的首选若仍有效，尊重它（用户上次手动选的）
        saved = self.config.get('preferred')
        if saved and saved in self.device_dict:
            imei = saved
        else:
            # 未指定或失效：固定选 PC，绝不自动挑 adb/browser
            pc = next((d for d in self.device_dict.values()
                       if d.get('device') == 'windows'), None)
            if pc is None:
                logger.info('no PC device available; leaving preferred device unchanged')
                return
            imei = pc['imei']
            logger.info(f'auto device selection locked to PC: {imei}')

    # 以下与框架原逻辑一致（用 _original 完成实际切换与 start）
    return _original_set_preferred(self, imei, index)


def install():
    """包装 DeviceManager 的排序与默认选择。失败静默降级。"""
    global _INSTALLED, _PATCHED, _original_set_preferred
    if _INSTALLED:
        return
    try:
        from Mon.device.DeviceManager import DeviceManager
        # 幂等靠模块级 _PATCHED 比对，不用函数属性（Cython builtin 无法 setattr）。
        # 用 _BoundMethod 描述符而非裸函数：否则实例调用 get_devices() 时
        # self 丢失，v1.0.77 就是因此让设备下拉和任务全线崩溃。
        if _PATCHED is not None and getattr(DeviceManager, '__dict__', {}).get('get_devices') is _PATCHED:
            _INSTALLED = True
            return

        _original_set_preferred = DeviceManager.set_preferred_device

        patched_get = _BoundMethod(_sorted_devices)
        patched_set = _BoundMethod(_set_preferred_device)

        DeviceManager.get_devices = patched_get
        DeviceManager.set_preferred_device = patched_set
        _PATCHED = patched_get
        _INSTALLED = True
        logger.info('device selection patch installed (PC first, no auto switch)')
    except Exception as error:
        logger.warning('failed to install device selection patch: %s', error)
