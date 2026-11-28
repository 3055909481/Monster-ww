"""避免自定义工作流与框架预启动重复启动游戏。

框架的 ``StartController.start()`` 在把任务交给执行器之前，会先执行自己的
``start_device()``：按全局配置的 exe 路径启动游戏、等窗口、查 HDR。但用户的
自定义工作流（CustomFlowTask）第一步通常就是 ``start_game``（带明确服务器参数，
且会自己关闭另一个服务器的客户端）。两者叠加导致：

  * 游戏被启动两次（框架一次 + 工作流一次）；
  * 工作流任务要等框架那套启动完成后才进入执行器，状态栏因此迟迟不显示运行中。

本项目在 ``main.py`` 早期 monkeypatch ``StartController.start_device``：当本次
启动的任务是 CustomFlowTask 且其选中工作流的首个启用步骤是 start_game 时，
跳过框架预启动，让工作流全权控制游戏启停（含服务器选择）。其它任务完全走
框架原行为。

!! 编译版约束（与 device_selection_patch 同样的两个坑）!!
Cython 把函数编译成 builtin：无法 setattr 自定义属性（幂等用模块级变量），
也不实现描述符协议（用 _BoundMethod 保证实例调用拿到 self）。
"""

import logging

logger = logging.getLogger(__name__)

_INSTALLED = False
_PATCHED = None
_original_start_device = None


class _BoundMethod:
    """描述符：把 (self, ...) 函数绑定为实例方法，兼容 Cython 编译环境。"""

    def __init__(self, func):
        self.func = func

    def __get__(self, instance, owner=None):
        if instance is None:
            return self.func
        func = self.func

        def bound(*args, **kwargs):
            return func(instance, *args, **kwargs)

        return bound


def _flow_starts_game(task):
    """任务是否为「首个启用步骤就是 start_game」的自定义工作流。"""
    try:
        from src.task.CustomFlowTask import CustomFlowTask
        if not isinstance(task, CustomFlowTask):
            return False
        from src.task.routine.FlowSteps import (
            DEFAULT_FLOW_NAME, FLOW_SELECTOR, FlowStepStore, normalize_items,
        )
        executor = getattr(task, 'executor', None)
        folder = getattr(executor, 'config_folder', None)
        if not folder:
            return False
        flows = FlowStepStore(folder).load()
        name = task.config.get(FLOW_SELECTOR, DEFAULT_FLOW_NAME)
        steps = normalize_items(flows.get(name, []))
        enabled = [s for s in steps if s.get('enabled')]
        return bool(enabled) and enabled[0].get('id') == 'start_game'
    except Exception as error:
        logger.debug('flow starts-game detection failed: %s', error)
        return False


def _start_device(self, *args, **kwargs):
    task = self.__dict__.get('_active_task')
    if _flow_starts_game(task):
        logger.info('custom flow owns game start; skipping framework start_device')
        # start_device 成功时会发 starting_emulator(True, None, 0) 通知 UI 收起
        # 启动进度条；这里必须同样发出，否则弹窗会一直转圈。
        try:
            from Mon import communicate
            communicate.starting_emulator.emit(True, None, 0)
        except Exception:
            pass
        return True
    return _original_start_device(self, *args, **kwargs)


def install():
    """包装 StartController.start_device。失败静默降级为框架原行为。"""
    global _INSTALLED, _PATCHED, _original_start_device
    if _INSTALLED:
        return
    try:
        from Mon.core.start_controller import StartController
        if _PATCHED is not None and getattr(StartController, '__dict__', {}).get('start_device') is _PATCHED:
            _INSTALLED = True
            return
        _original_start_device = StartController.start_device
        patched = _BoundMethod(_start_device)
        StartController.start_device = patched
        _PATCHED = patched
        _INSTALLED = True
        logger.info('flow start patch installed (no duplicate game launch)')
    except Exception as error:
        logger.warning('failed to install flow start patch: %s', error)
