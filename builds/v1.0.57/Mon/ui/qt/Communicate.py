"""Compatibility export for the framework-neutral application event bus."""

from Mon.core.events import EventBus as Communicate
from Mon.core.events import communicate

__all__ = ["Communicate", "communicate"]
