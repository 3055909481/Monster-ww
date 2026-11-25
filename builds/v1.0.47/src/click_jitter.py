"""项目层补丁：给点击坐标加极小范围抖动，提升按钮命中率。

背景：部分按钮存在 hover 态或像素级边缘，固定坐标连点时偶发失效。
这里在 app 侧 monkeypatch ``Task.click``，在调用原方法前对绝对坐标加
一个小范围随机偏移，抖动的中心仍是原坐标（不引入系统性偏移）。

框架是 pip 包不随本项目打包，因此不改框架源码；本模块须保持源码
（Cython 编译后函数无法绑定 self），已加入 setup.py 与 workflow 白名单。

所有点击（绝对坐标 / click_relative 换算 / click_box 框内点）最终都汇聚到
Task.click(x, y)，因此只需 patch 这一处。相对坐标在 click_relative 里已换算
成绝对像素后才进 click，不会二次误加比例偏移。
"""

import logging
import random

logger = logging.getLogger(__name__)

_INSTALLED = False
_ORIGINAL_CLICK = None

# 配置项（并入 Basic Options）
KEY_ENABLED = 'Enable Click Jitter'
KEY_RADIUS = 'Click Jitter Radius (px)'
CONFIG_NAME = 'Basic Options'

# 配置缺失/异常时的兜底：默认关闭，绝不因 patch 本身改变既有点击行为
_FALLBACK = {
    KEY_ENABLED: True,
    KEY_RADIUS: 2,
}
_MAX_RADIUS = 8  # 上限保护：防止误配成大偏移导致点歪按钮


def _jitter_radius(executor):
    """返回本次点击的抖动半径（px）；0 表示不抖动。配置异常一律返回 0。"""
    try:
        gc = getattr(executor, 'global_config', None)
        cfg = gc.get_config(CONFIG_NAME) if gc is not None else None
        if not cfg:
            return 0
        if not cfg.get(KEY_ENABLED, False):
            return 0
        radius = int(cfg.get(KEY_RADIUS, _FALLBACK[KEY_RADIUS]))
    except Exception:
        return 0
    return max(0, min(_MAX_RADIUS, radius))


def _patched_click(self, x=-1, y=-1, *args, **kwargs):
    radius = _jitter_radius(getattr(self, 'executor', None))
    if radius and isinstance(x, (int, float)) and isinstance(y, (int, float)) \
            and not (0 < x < 1 or 0 < y < 1):
        x = int(round(x + random.uniform(-radius, radius)))
        y = int(round(y + random.uniform(-radius, radius)))
    return _ORIGINAL_CLICK(self, x, y, *args, **kwargs)


def install():
    """安装补丁，幂等。返回 True 表示本次实际安装。"""
    global _INSTALLED, _ORIGINAL_CLICK
    if _INSTALLED:
        return False
    try:
        from Mon.task.task import BaseTask
        _ORIGINAL_CLICK = BaseTask.click
        BaseTask.click = _patched_click
        _INSTALLED = True
        return True
    except Exception as exc:
        logger.warning('click jitter patch skipped: %s', exc)
        return False
