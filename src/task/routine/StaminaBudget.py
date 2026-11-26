"""Daily stamina-wait budget for custom flow steps.

A natural day for Wuthering Waves resets at 04:00 local time, not midnight.
The game grants at most 240 natural stamina per day, so waiting for recovery
past that amount is pointless (nothing more regenerates until the next reset).

This module keeps a small JSON ledger keyed by ``(day, account)`` so a flow can
wait for stamina without over-waiting, and stays dependency-free (no Mon / Qt)
so it can be unit tested directly.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

DAILY_STAMINA_CAP = 240
RESET_HOUR = 4
LEDGER_NAME = 'StaminaWaitLedger.json'


def reset_day(now=None):
    """Return the game-day date; before 04:00 belongs to the previous day.

    ``now`` may be a ``datetime`` or ``None`` (uses local time). Returning a
    ``date`` makes the ledger key stable across restarts and DST-safe enough
    for a 4-hour boundary.
    """
    moment = now or datetime.now()
    if moment.hour < RESET_HOUR:
        moment = moment - timedelta(days=1)
    return moment.date()


def day_key(now=None, account=''):
    """Ledger key combining game-day and account (multi-account isolation)."""
    return f'{reset_day(now).isoformat()}|{str(account or "").strip()}'


class StaminaWaitLedger:
    """Atomic JSON ledger of stamina gained by waiting, per game-day/account."""

    def __init__(self, folder='configs'):
        self.path = Path(folder) / LEDGER_NAME

    def load(self):
        try:
            with self.path.open(encoding='utf-8') as stream:
                data = json.load(stream)
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        # Drop entries from past game-days so the file stays small.
        today = reset_day().isoformat()
        return {key: value for key, value in data.items()
                if isinstance(key, str) and key.split('|', 1)[0] == today
                and isinstance(value, (int, float))}

    def waited(self, now=None, account=''):
        """How much stamina this account has already waited for today."""
        return float(self.load().get(day_key(now, account), 0) or 0)

    def remaining(self, now=None, account=''):
        """Remaining wait budget for today (never negative)."""
        return max(0.0, DAILY_STAMINA_CAP - self.waited(now, account))

    def add(self, amount, now=None, account=''):
        """Record ``amount`` of stamina gained by waiting and return the total."""
        amount = max(0.0, float(amount or 0))
        data = self.load()
        key = day_key(now, account)
        value = min(DAILY_STAMINA_CAP, float(data.get(key, 0) or 0) + amount)
        data[key] = value
        self._save(data)
        return value

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                             dir=self.path.parent,
                                             prefix='.StaminaWaitLedger-', suffix='.tmp',
                                             delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()


def plan_stamina_wait(current, target, remaining_budget, need=60):
    """Decide how much stamina to wait for, capped by the daily budget.

    Returns ``(should_wait, effective_target)``:
      * ``should_wait`` False when already at target or no budget left.
      * ``effective_target`` never exceeds current + remaining budget, so the
        step cannot wait beyond today's 240 natural stamina allowance.
      * The wait must still cover at least one run (``need``) to be useful; a
        smaller gap than that is pointless and returns False.
    """
    if current < 0 or target < 0 or remaining_budget <= 0:
        return False, current
    if current >= target:
        return False, current
    gap = target - current
    effective_gap = min(gap, float(remaining_budget))
    if effective_gap < max(1, int(need)):
        return False, current
    return True, current + effective_gap
