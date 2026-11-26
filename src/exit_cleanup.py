"""Ensure every manually triggered task stops when the app exits.

The framework sets exit_event and destroys the executor on window close, but a
task waiting inside OCR/wait_until can outlive that. Disabling every manual
task up front makes the next framework check point abort it, and clearing the
current task lets the UI stop showing a stale run.
"""

import logging

logger = logging.getLogger(__name__)

_INSTALLED = False


def stop_all_manual_tasks():
    """Disable every one-time/trigger task and stop the current one."""
    try:
        from Mon import og
        executor = getattr(og, 'executor', None) or getattr(
            getattr(og, 'app', None), 'executor', None)
        if executor is None:
            return
        for task in list(executor.get_all_tasks()):
            try:
                if getattr(task, 'enabled', False) or getattr(task, 'running', False):
                    task.disable()
            except Exception as error:
                logger.warning('failed to disable %s on exit: %s', task, error)
        try:
            executor.stop_current_task()
        except Exception:
            pass
    except Exception as error:
        logger.warning('exit cleanup failed: %s', error)


def install():
    """Install the quit cleanup once; failures degrade to framework default."""
    global _INSTALLED
    if _INSTALLED:
        return
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return
        app.aboutToQuit.connect(stop_all_manual_tasks)
        _INSTALLED = True
        logger.info('exit cleanup installed')
    except Exception as error:
        logger.warning('failed to install exit cleanup: %s', error)
