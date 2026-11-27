"""Shared task-stop helper for the launcher-style tabs.

TaskMonitorTab already stops a task by disabling it, unpausing a paused run
and stopping the executor's current task (needed for nested run_task_by_class
calls where the child's own _enabled is never checked). The run/stop cards use
the same helper so "stop here" behaves identically to the monitor page.
"""


def stop_task(task, executor=None):
    """Stop ``task`` the same way TaskMonitorTab._stop_one does.

    Returns True when a stop was actually issued (useful for a toast).
    """
    if task is None:
        return False
    issued = False
    try:
        task.disable()
        issued = True
    except Exception:
        pass
    try:
        if getattr(task, 'running', False):
            task.unpause()
    except Exception:
        pass
    # Only chain to the current task when the clicked task isn't it: a nested
    # one-time task reports enabled/running but the executor checks the parent.
    current = getattr(executor, 'current_task', None) if executor is not None else None
    if current is not None and task is not current:
        try:
            executor.stop_current_task()
            issued = True
        except Exception:
            pass
    return issued
