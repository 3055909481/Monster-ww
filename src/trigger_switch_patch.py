"""确保触发任务开关的每次切换都立即、可靠地写入配置文件。

框架的 ``set_trigger_enabled`` 用异步线程调用 ``task.enable()``，而
``BaseTask.enable()`` 在 ``_enabled`` 已为 True 时会跳过整段逻辑；更糟的是
线程里的任何异常都会被吞掉，界面开关看起来变了，配置文件却没变，重启后
开关又回到旧值。用户反馈的「手动开启后重启又关闭」正是这个现象。

本项目在 ``main.py`` 早期用本模块包装 ``tasks_page.set_trigger_enabled``：
先把 ``config['_enabled']`` 同步写入（Config.__setitem__ 会立即持久化），
再交给框架原函数完成调度。这样无论框架内部发生什么，用户点下的状态都会
落盘。关闭同理（框架 disable 本身就是同步写盘，这里只做幂等保护）。
"""

import logging

logger = logging.getLogger(__name__)

_INSTALLED = False


def _persist(task, enabled):
    """同步把开关状态写进任务配置。返回是否成功。"""
    config = getattr(task, 'config', None)
    if config is None or '_enabled' not in config:
        return False
    try:
        if bool(config.get('_enabled')) != bool(enabled):
            config['_enabled'] = bool(enabled)  # Config.__setitem__ 立即 save_file
        return True
    except Exception as error:
        logger.warning('persist trigger switch failed for %s: %s', task, error)
        return False


def install():
    """包装 tasks_page.set_trigger_enabled，保证开关状态落盘。失败静默降级。"""
    global _INSTALLED
    if _INSTALLED:
        return
    try:
        import importlib
        tasks_page = importlib.import_module('Mon.ui.qt.tasks.tasks_page')
        original = tasks_page.set_trigger_enabled
        if getattr(original, '_trigger_switch_wrapped', False):
            _INSTALLED = True
            return

        def patched(task, enabled):
            _persist(task, enabled)
            return original(task, enabled)

        patched._trigger_switch_wrapped = True
        tasks_page.set_trigger_enabled = patched
        _INSTALLED = True
        logger.info('trigger switch persistence installed')
    except Exception as error:
        logger.warning('failed to install trigger switch patch: %s', error)
