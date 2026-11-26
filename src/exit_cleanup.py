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
            # Release held keys/buttons BEFORE disabling: send_key_up on the
            # task calls executor.reset_scene() -> check_enabled(), which raises
            # TaskDisabledException once _enabled is False and drops the first
            # key (set iteration order then randomly leaks one held key, e.g. W).
            try:
                release = getattr(task, 'release_held_inputs', None)
                if callable(release):
                    release()
            except Exception as error:
                logger.warning('failed to release held inputs for %s: %s', task, error)
            try:
                if getattr(task, 'enabled', False) or getattr(task, 'running', False):
                    task.disable()
            except Exception as error:
                logger.warning('failed to disable %s on exit: %s', task, error)
        try:
            executor.stop_current_task()
        except Exception:
            pass
        # Stop the always-on F-key watcher thread and restore the global
        # GetAsyncKeyState hook it installs; otherwise the process exits with a
        # live polling thread and a patched system function.
        try:
            from src.combat.FKeyStart import f_key_watcher, uninstall_self_f_filter
            f_key_watcher.stop()
            uninstall_self_f_filter()
        except Exception as error:
            logger.warning('F-key watcher cleanup failed: %s', error)
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
