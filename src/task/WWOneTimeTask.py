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
        try:
            from src.task.RoutineTask import RoutineTask
            # Reuse the proven login helpers against this task instance: they
            # only need generic task methods (ocr/click/sleep/ensure_main).
            RoutineTask._switch_to_login(self)
            RoutineTask._login_target_account(self, account_key)
        except Exception as error:
            logger.warning('scheduled account login failed for %s: %s', account_key, error)
