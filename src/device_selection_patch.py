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
"""

import logging

logger = logging.getLogger(__name__)

_INSTALLED = False


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


_original_set_preferred = None


def install():
    """包装 DeviceManager 的排序与默认选择。失败静默降级。"""
    global _INSTALLED, _original_set_preferred
    if _INSTALLED:
        return
    try:
        from Mon.device.DeviceManager import DeviceManager
        if getattr(DeviceManager.get_devices, '_device_patch_wrapped', False):
            _INSTALLED = True
            return

        original_get = DeviceManager.get_devices
        _original_set_preferred = DeviceManager.set_preferred_device

        def patched_get(self):
            return _sorted_devices(self)

        patched_get._device_patch_wrapped = True

        def patched_set(self, imei=None, index=-1):
            return _set_preferred_device(self, imei, index)

        patched_set._device_patch_wrapped = True

        DeviceManager.get_devices = patched_get
        DeviceManager.set_preferred_device = patched_set
        _INSTALLED = True
        logger.info('device selection patch installed (PC first, no auto switch)')
    except Exception as error:
        logger.warning('failed to install device selection patch: %s', error)
