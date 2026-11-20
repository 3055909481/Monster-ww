"""按 F 开战：物理 F 触发与「吸收声骸不算开战」判定。"""

import sys
import time
import types
import unittest
from unittest.mock import Mock, patch

# FKeyStart 里的 ctypes.windll 在 Linux/CI 无 GUI 时不可用，注入一个假 user32。
_ctypes = sys.modules.get('ctypes')
if _ctypes is not None and not hasattr(_ctypes, 'windll'):
    _user32 = types.SimpleNamespace(GetAsyncKeyState=lambda vk: 0)
    _ctypes.windll = types.SimpleNamespace(user32=_user32)

from src.combat.FKeyStart import FKeyWatcher
from src.task.AutoCombatTask import ABSORB_PROMPT_WINDOW, AutoCombatTask

F_START_KEY = AutoCombatTask.F_START_KEY


class TestFKeyWatcher(unittest.TestCase):
    def test_only_rising_edge_counts(self):
        watcher = FKeyWatcher()
        watcher.is_game_foreground = lambda: True
        watcher._self_until = 0.0
        self.assertTrue(watcher.on_sample(True, False))  # returns current key state
        self.assertGreater(watcher.last_press, 0)
        first = watcher.last_press
        watcher.on_sample(True, True)  # still held -> no new press
        self.assertEqual(watcher.last_press, first)

    def test_self_press_is_ignored(self):
        watcher = FKeyWatcher()
        watcher.is_game_foreground = lambda: True
        watcher.note_self_key('f', down_time=1.0)
        watcher.on_sample(True, False)
        self.assertLess(watcher.last_press, 0)  # ignored, still unset

    def test_background_game_ignored(self):
        watcher = FKeyWatcher()
        watcher.is_game_foreground = lambda: False
        watcher._self_until = 0.0
        watcher.on_sample(True, False)
        self.assertLess(watcher.last_press, 0)


class _Harness:
    """只提供 should_start_combat_by_key 需要的接口。"""
    F_START_KEY = F_START_KEY
    F_DELAY_KEY = 'F 之后等待(秒)'
    F_GRACE_KEY = '没锁到目标最多撑(秒)'

    def __init__(self, config):
        self.config = config
        self._f_consumed = -1.0
        self._absorb_prompt_at = -1.0
        self._f_prompt_box = None
        self._f_prompt_read_at = 0.0
        self._f_prompt_is_absorb = False
        self.log_info = Mock()

    f_start_enabled = property(lambda self: bool(self.config.get(self.F_START_KEY, True)))
    f_start_delay = property(lambda self: float(self.config.get(self.F_DELAY_KEY, 0) or 0))
    last_f_press = lambda self: self._pressed
    is_open_world_auto_combat = lambda self: self._open_world
    load_chars = lambda self: self._chars_loaded
    note_f_prompt = lambda self: None
    pressed_to_absorb = AutoCombatTask.pressed_to_absorb
    should_start_combat_by_key = AutoCombatTask.should_start_combat_by_key


def _harness(pressed, absorb_at=-1.0, open_world=False, enabled=True, loaded=True):
    h = _Harness({F_START_KEY: enabled, 'F 之后等待(秒)': 0.0})
    h._pressed = pressed
    h._absorb_prompt_at = absorb_at
    h._open_world = open_world
    h._chars_loaded = loaded
    return h


class TestShouldStartCombat(unittest.TestCase):
    def test_recent_physical_f_starts_combat(self):
        h = _harness(time.time())
        self.assertTrue(h.should_start_combat_by_key())

    def test_absorb_echo_press_is_ignored(self):
        now = time.time()
        h = _harness(now, absorb_at=now - 0.2)
        self.assertFalse(h.should_start_combat_by_key())

    def test_absorb_outside_window_does_not_block(self):
        now = time.time()
        h = _harness(now, absorb_at=now - ABSORB_PROMPT_WINDOW - 2)
        self.assertTrue(h.should_start_combat_by_key())

    def test_open_world_f_ignored(self):
        h = _harness(time.time(), open_world=True)
        self.assertFalse(h.should_start_combat_by_key())

    def test_disabled_task_never_starts(self):
        h = _harness(time.time(), enabled=False)
        self.assertFalse(h.should_start_combat_by_key())

    def test_stale_press_expires(self):
        h = _harness(time.time() - 60)
        self.assertFalse(h.should_start_combat_by_key())

    def test_press_is_consumed_once(self):
        h = _harness(time.time())
        self.assertTrue(h.should_start_combat_by_key())
        self.assertFalse(h.should_start_combat_by_key())  # already consumed


if __name__ == '__main__':
    unittest.main()
