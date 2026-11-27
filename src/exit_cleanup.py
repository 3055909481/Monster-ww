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
    """Abort any running task on exit without changing saved task settings.

    Only the in-memory ``_enabled`` flag is cleared for the task that is
    actually running, so the framework's next ``check_enabled()`` aborts it.
    ``Task.disable()`` is deliberately NOT used here: for TriggerTask it also
    writes ``config['_enabled'] = False`` and persists the file, which made
    every trigger task come back disabled on the next launch ("残留配置").
    Trigger tasks that are merely enabled (not running) need no action at all
    - process exit stops their scheduling and must not erase the user's
    preference to have them auto-enabled next time.
    """
    try:
        from Mon import og
        executor = getattr(og, 'executor', None) or getattr(
            getattr(og, 'app', None), 'executor', None)
        if executor is None:
            return
        current = getattr(executor, 'current_task', None)
        for task in list(executor.get_all_tasks()):
            # Release held keys/buttons BEFORE clearing the flag: send_key_up
            # calls reset_scene() -> check_enabled(), which raises once
            # _enabled is False and would otherwise drop a held key (e.g. W).
            if task is not current:
                continue
            try:
                release = getattr(task, 'release_held_inputs', None)
                if callable(release):
                    release()
            except Exception as error:
                logger.warning('failed to release held inputs for %s: %s', task, error)
            # In-memory only: no config write, so the setting survives restart.
            try:
                task._enabled = False
            except Exception as error:
                logger.warning('failed to stop %s on exit: %s', task, error)
        try:
            if current is not None:
                current.unpause()  # wake any wait_until so it hits check_enabled
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


def _connect(app):
    app.aboutToQuit.connect(stop_all_manual_tasks)


def install():
    """Install the quit cleanup once; failures degrade to framework default.

    ``main.py`` calls this before ``OK(config)`` creates the QApplication, so
    ``QApplication.instance()`` is still None here. Defer the connection by
    wrapping ``init_app_config`` (which the framework imports lazily inside
    ``App.__init__``) and hook aboutToQuit the moment the app exists.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            _connect(app)
            _INSTALLED = True
            logger.info('exit cleanup installed')
            return
        from Mon.ui.qt.util import app as app_util
        original = app_util.init_app_config
        if getattr(original, '_exit_cleanup_wrapped', False):
            return

        def patched(*args, **kwargs):
            result = original(*args, **kwargs)
            qapp = result[0]
            _connect(qapp)
            return result

        patched._exit_cleanup_wrapped = True
        app_util.init_app_config = patched
        _INSTALLED = True
        logger.info('exit cleanup deferred to QApplication creation')
    except Exception as error:
        logger.warning('failed to install exit cleanup: %s', error)
