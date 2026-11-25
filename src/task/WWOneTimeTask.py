import logging

from Mon import BrowserInteraction, PostMessageInteraction
from src.task.MouseResetTask import MouseResetTask

logger = logging.getLogger(__name__)


class WWOneTimeTask:

    def run(self):
        # Reset interrupted rotations on the executor before startup can sleep.
        if hasattr(self, 'reset_combat_state'):
            self.reset_combat_state('onetime task start')
        mouse_reset_task = self.executor.get_task_by_class(MouseResetTask)
        mouse_reset_task.run()
        if isinstance(self.executor.interaction, PostMessageInteraction):
            self.executor.interaction.activate()
        self.sleep(0.5)
        self._apply_scheduled_account()

    def _apply_scheduled_account(self):
        """Log into the account chosen in the scheduled task, for any task.

        RoutineTask already handles its own account switching and multi-account
        tasks switch accounts per entry; skip those to avoid logging in twice.
        Manual runs carry no --schedule-account and are left untouched.
        """
        from src.schedule_runtime import scheduled_account
        account_key = scheduled_account()
        if not account_key or type(self).__name__ in ('RoutineTask', 'MultiAccountConfigTask'):
            return
        from src.task.RoutineTask import RoutineTask
        # Reuse the proven login helpers against this task instance: they
        # only need generic task methods (ocr/click/sleep/ensure_main).
        # A scheduled run must never proceed on the wrong account, so retry
        # once and then fail loudly instead of silently running on whoever is
        # currently logged in.
        from Mon.task.exceptions import TaskDisabledException
        last_error = None
        for attempt in range(2):
            try:
                RoutineTask._switch_to_login(self)
                RoutineTask._login_target_account(self, account_key)
                return
            except TaskDisabledException:
                raise  # 用户主动停止，不重试
            except Exception as error:
                last_error = error
                logger.warning('scheduled account login failed for %s (attempt %d/2): %s',
                               account_key, attempt + 1, error)
        raise RuntimeError(
            f'scheduled account login failed for {account_key}; aborting to avoid '
            f'running on the wrong account: {last_error}') from last_error
