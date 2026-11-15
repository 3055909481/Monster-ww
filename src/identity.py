"""Runtime identity layer for the Monster-ww portable build.

The portable package must not expose the upstream project's identifiers in
its on-disk artifacts.  This module rebrands, at process start, everything the
Mon-script framework writes or registers:

* the framework log basename (``logs/<name>.log``),
* every log record, including exception text and formatted arguments,
* the Log page's default file and placeholder text,
* the custom-script folders created next to the executable,
* Win32 overlay window classes and the notification AppUserModelID.

It patches the *installed* Mon-script package at runtime instead of forking the
dependency again, so an incremental PyAppify update can pick it up without a
new Mon-script tag or another full dependency download.

The patches are deliberately defensive: a failure in any single patch is
logged and skipped, never allowed to prevent the application from starting.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

APP_SLUG = "monster-ww"
APP_TITLE = "Monster-ww"
LOG_BASENAME = "Monster-ww-runtime"
SCRIPTS_FOLDER = "Monster-ww_scripts"
IMPORT_FOLDER = "Monster-ww_drop"
OVERLAY_WINDOW_CLASS = "Monster-ww.Overlay"
NOTIFIER_APP_USER_MODEL_ID = "Monster-ww.Shell"

# Every spelling that identifies the upstream distribution. Longest matches
# win because the regex alternation is ordered by length.
_UPSTREAM_TOKENS = (
    "ok-wuthering-waves",
    "okwutheringwaves",
    "ok-ww-char-code",
    "ok-script",
    "ok_script",
    "ok-oldking",
    "ok-ww",
    "ok_ww",
    "okww",
)
_UPSTREAM_RE = re.compile(
    "|".join(re.escape(t) for t in sorted(_UPSTREAM_TOKENS, key=len, reverse=True)),
    re.IGNORECASE,
)


def sanitize_text(value):
    """Rewrite upstream branding inside any runtime string."""
    if not isinstance(value, str):
        return value
    lowered = value.lower()
    if not any(token in lowered for token in _UPSTREAM_TOKENS):
        return value
    return _UPSTREAM_RE.sub(APP_SLUG, value)


class _SanitizingFilter(logging.Filter):
    """Scrub upstream names from log records before any handler sees them."""

    def filter(self, record):
        try:
            message = record.getMessage()
        except Exception:
            return True
        sanitized = sanitize_text(message)
        if sanitized != message:
            record.msg = sanitized
            record.args = ()
        if record.exc_text:
            record.exc_text = sanitize_text(record.exc_text)
        return True


def _patch_logging():
    """Attach the filter before importing Mon, so import-time logs are clean."""
    for logger_name in ("Mon", "pyappify", ""):
        target = logging.getLogger(logger_name)
        if not any(isinstance(item, _SanitizingFilter) for item in target.filters):
            target.addFilter(_SanitizingFilter())


def _patch_logger_file():
    """Rename the framework log: config_logger(config, name='Mon-script')."""
    from Mon.util import logger as logger_module

    original = logger_module.config_logger
    if getattr(original, "_monster_ww_patched", False):
        return original

    def config_logger(config=None, name=LOG_BASENAME, _original=original):
        return _original(config, name)

    config_logger._monster_ww_patched = True
    logger_module.config_logger = config_logger
    return config_logger


def _patch_script_folders():
    """Replace the ok_tasks / ok_import folders created beside the exe."""
    from Mon.core import script_packager

    def tasks_folder():
        return os.path.join(os.getcwd(), SCRIPTS_FOLDER)

    def import_folder():
        return os.path.join(os.getcwd(), IMPORT_FOLDER)

    script_packager.OK_TASKS_FOLDER = SCRIPTS_FOLDER
    script_packager.OK_IMPORT_FOLDER = IMPORT_FOLDER
    script_packager.get_ok_tasks_folder = tasks_folder
    script_packager.get_ok_import_folder = import_folder

    # Several framework modules use ``from ... import get_ok_import_folder``.
    # Those module-level bindings were created before this patch, so refresh
    # any already-imported module that still points at the original callable.
    for module in list(sys.modules.values()):
        if module is None or not isinstance(getattr(module, "__dict__", None), dict):
            continue
        for attr, replacement in (
            ("get_ok_tasks_folder", tasks_folder),
            ("get_ok_import_folder", import_folder),
        ):
            current = module.__dict__.get(attr)
            if current is replacement or current is None:
                continue
            if getattr(current, "__module__", "") == "Mon.core.script_packager":
                try:
                    module.__dict__[attr] = replacement
                except Exception:
                    pass


def _patch_window_classes():
    from Mon.ui.overlay import win32_gdi

    win32_gdi._window_class_name = OVERLAY_WINDOW_CLASS


def _patch_notifications():
    from Mon.notification import system as notifier_module

    cls = notifier_module.WindowsSystemNotifier
    if getattr(cls.__init__, "_monster_ww_patched", False):
        return
    original_init = cls.__init__
    original_show = cls.show

    def __init__(self, app_name=APP_TITLE, app_icon=None, _original=original_init):
        _original(self, APP_TITLE, app_icon)
        self.app_id = NOTIFIER_APP_USER_MODEL_ID

    def show(self, title, message, error=False, _original=original_show):
        return _original(self, sanitize_text(title), sanitize_text(message), error)

    __init__._monster_ww_patched = True
    cls.__init__ = __init__
    cls.show = show


def _patch_log_page():
    from Mon.ui.qt.shell import log_page as page

    original_init = page.LogPage.__init__
    if getattr(original_init, "_monster_ww_patched", False):
        return
    original_t = page._t

    def __init__(self, log_file=None, parent=None, _original=original_init):
        default = Path.cwd() / "logs" / f"{LOG_BASENAME}.log"
        _original(self, log_file or default, parent)

    def _t(text, _original=original_t):
        # Translate with the original key first, then scrub the result.
        translated = sanitize_text(_original(text))
        return translated.replace("Mon-script.log", LOG_BASENAME + ".log")

    __init__._monster_ww_patched = True
    page.LogPage.__init__ = __init__
    page._t = _t


def apply():
    """Apply every runtime identity patch. Idempotent; safe to call twice."""
    _patch_logging()

    import Mon as mon_package

    if getattr(mon_package, "_monster_ww_identity_applied", False):
        return False
    mon_package._monster_ww_identity_applied = True

    patched_config_logger = None
    for patch in (
        _patch_logger_file,
        _patch_script_folders,
        _patch_window_classes,
        _patch_notifications,
        _patch_log_page,
    ):
        try:
            result = patch()
            if patch is _patch_logger_file:
                patched_config_logger = result
        except Exception as exc:
            logging.getLogger("Mon").warning(
                "identity patch %s skipped: %s", patch.__name__, exc
            )

    # Mon.__init__ resolves symbols through package globals first, and the
    # original config_logger is already bound there by a top-level import.
    if patched_config_logger is not None:
        try:
            mon_package.config_logger = patched_config_logger
        except Exception as exc:
            logging.getLogger("Mon").warning("identity export skipped: %s", exc)
    return True
