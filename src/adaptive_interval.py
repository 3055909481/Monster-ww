"""项目层补丁：空闲时降低触发轮询占用，战斗时保持原节奏。

框架 Mon-script 是 pip 包，不随本项目打包，因此不改框架源码。这里在
main.py 启动早期 monkeypatch ``TaskExecutor.trigger_sleep``：

- 战斗中：在配置的战斗范围内取值（默认 1~1ms，即固定 1ms = 框架原值），
  AutoCombatTask 自身的 0.1s trigger_interval 完全不动，战斗效率不受影响。
- 空闲时：在配置的空闲范围内取值（默认 25~35ms），降低每轮触发轮询的唤醒
  频率。范围取值天然避免「中心值 ± 浮动」撞到 0 的边界问题。

所有读取均有防御性 try/except，任何一步失败都静默降级为框架原行为。
战斗信号来自任务实例上的 ``_in_combat``（CombatCheck 切换战斗时维护，不受
scene.reset() 清空影响），scene.in_combat() 只作为兜底。

配置项已并入 config.py 的 ``basic_config_option``（Basic Options / Tasks 分组），
与框架的 Trigger Interval 同处一页；界面中文由 i18n/zh_CN 提供。
"""

import logging
import random

logger = logging.getLogger(__name__)

# 配置项已并入 Basic Options 的 Tasks 分组（与 Trigger Interval 同处）。
CONFIG_NAME = 'Basic Options'
KEY_ENABLED = 'Enable Adaptive Interval'
KEY_COMBAT_MIN = 'Combat Interval Min (ms)'
KEY_COMBAT_MAX = 'Combat Interval Max (ms)'
KEY_IDLE_MIN = 'Idle Interval Min (ms)'
KEY_IDLE_MAX = 'Idle Interval Max (ms)'

_INSTALLED = False

# 兜底默认：战斗固定 1ms = 框架原值；空闲 25~35ms 温和降占用。
_FALLBACK = {
    KEY_ENABLED: True,
    KEY_COMBAT_MIN: 1,
    KEY_COMBAT_MAX: 1,
    KEY_IDLE_MIN: 25,
    KEY_IDLE_MAX: 35,
}


def _in_combat(executor):
    """判断当前是否战斗中。读任务实例状态，不依赖会被 reset 的 scene。"""
    scene = getattr(executor, 'scene', None)
    try:
        if scene is not None and scene.in_combat() is True:
            return True
    except Exception:
        pass
    for pool_name in ('trigger_tasks', 'onetime_tasks'):
        for task in (getattr(executor, pool_name, None) or []):
            if getattr(task, '_in_combat', False) is True:
                return True
    return False


def _range_ms(base, key_min, key_max):
    """读一对 min/max 并归一化，返回 (low, high)，均为非负且 low <= high。"""
    try:
        low = float(base[key_min])
    except (TypeError, ValueError, KeyError):
        low = float(_FALLBACK[key_min])
    try:
        high = float(base[key_max])
    except (TypeError, ValueError, KeyError):
        high = float(_FALLBACK[key_max])
    low, high = max(0.0, low), max(0.0, high)
    return (low, high) if low <= high else (high, low)


def _resolve_interval_ms(executor):
    """返回 (启用了自适应?, 本次应休眠的毫秒数)。全程防御性降级。"""
    cfg = None
    try:
        gc = getattr(executor, 'global_config', None)
        if gc is not None:
            cfg = gc.get_config(CONFIG_NAME)
    except Exception:
        cfg = None

    # 配置不可用（框架未注册、读取异常）时降级为框架原行为，而不是套用
    # 兜底间隔——避免在未知环境下悄悄改变调度节奏。
    if cfg is None:
        return False, 0

    base = dict(_FALLBACK)
    for key in _FALLBACK:
        try:
            if key in cfg:
                base[key] = cfg[key]
        except Exception:
            pass

    if not base[KEY_ENABLED]:
        return False, 0

    keys = (KEY_COMBAT_MIN, KEY_COMBAT_MAX) if _in_combat(executor) else (KEY_IDLE_MIN, KEY_IDLE_MAX)
    low, high = _range_ms(base, *keys)
    return True, (low if low == high else random.uniform(low, high))


def _patched_trigger_sleep(self):
    try:
        adaptive, interval_ms = _resolve_interval_ms(self)
    except Exception:
        adaptive, interval_ms = False, 0

    if adaptive:
        if interval_ms > 0:
            self.sleep(interval_ms / 1000.0)
        return

    # 原行为：完全等价于框架 TaskExecutor.trigger_sleep
    interval = self.basic_options.get('Trigger Interval', 1)
    if interval:
        self.sleep(interval / 1000.0)


def install():
    """安装补丁，幂等。返回 True 表示本次实际安装。"""
    global _INSTALLED
    if _INSTALLED:
        return False
    try:
        from Mon.task.TaskExecutor import TaskExecutor
        TaskExecutor.trigger_sleep = _patched_trigger_sleep
        _INSTALLED = True
        return True
    except Exception as exc:
        logger.warning('adaptive interval patch skipped: %s', exc)
        return False
