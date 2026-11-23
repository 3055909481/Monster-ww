"""Runtime identity layer for the Monster-ww portable build.

The portable package must not expose the upstream project's identifiers in
its on-disk artifacts.  This module rebrands, at process start, everything the
Mon-script framework writes or registers:

* the framework log basename (``logs/<name>.log``),
* every log record, including exception text and formatted arguments,
* the Log page's default file and placeholder text,
* legacy custom-script folders (migrated to the framework's canonical names),
* Win32 overlay window classes and the notification AppUserModelID,
* the single-instance marker filename (product prefix, not the framework's).

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
from pathlib import Path

APP_SLUG = "monster-ww"
APP_TITLE = "Monster-ww"
LOG_BASENAME = "Monster-ww-runtime"
# These are canonical framework folder names, not upstream project branding,
# so no runtime patch is needed: discovery, editing and packaging all agree.
SCRIPTS_FOLDER = "ok_tasks"
IMPORT_FOLDER = "ok_import"
# Folders written by Monster-ww v19-v22. Migrated once at startup.
LEGACY_SCRIPTS_FOLDER = "Monster-ww_scripts"
LEGACY_IMPORT_FOLDER = "Monster-ww_drop"
OVERLAY_WINDOW_CLASS = "Monster-ww.Overlay"
NOTIFIER_APP_USER_MODEL_ID = "Monster-ww.Shell"
# 单实例标记：框架默认写 Mon-script-<digest>.pid，重定向为产品自有前缀，
# 避免与同机的其他 Mon 框架产品互杀。前缀只匹配框架名，绝不含上游 ok-ww 标识。
FRAMEWORK_MUTEX_PREFIX = "Mon-script-"
MUTEX_MARKER_PREFIX = "Monster-ww-script-"

# Every spelling that identifies the upstream distribution. Longest matches
# win because the regex alternation is ordered by length.
_UPSTREAM_TOKENS = (
    "ok-wuthering-waves",
    "okwutheringwaves",
    "ok-ww-char-code",
    "ok-script",
    "ok_script",
    "ok-oldking",
    "ok-mcp",
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
    # Thread names embed a suffix after the brand; keep it instead of dropping
    # it (ok-mcp -> monster-ww-mcp, not monster-ww).
    def replace(match):
        token = match.group(0)
        return f"{APP_SLUG}-mcp" if token.lower() == "ok-mcp" else APP_SLUG
    return _UPSTREAM_RE.sub(replace, value)


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
        # %(threadName)s is rendered from this attribute, not from msg/args,
        # so upstream thread names (e.g. ok-mcp) would otherwise leak into logs.
        if getattr(record, 'threadName', None):
            record.threadName = sanitize_text(record.threadName)
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


def _migrate_user_config_to_appdata():
    """Copy legacy in-app configs/ into %APPDATA%/Monster-ww/configs on first run.

    The full-update portable build ships no configs/ and config.py now points
    at the APPDATA copy, so existing users' accounts/schedules/history would
    otherwise be invisible. Copy (never move or delete) so the old data stays
    as a fallback; only copy files that do not yet exist in the destination.
    """
    try:
        base = os.environ.get("APPDATA")
        if not base:
            return
        dest = Path(base) / "Monster-ww" / "configs"
        source = Path.cwd() / "configs"
        if not source.is_dir():
            return
        dest.mkdir(parents=True, exist_ok=True)
        copied = 0
        for old in source.rglob("*"):
            if not old.is_file():
                continue
            target = dest / old.relative_to(source)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(old, target)
            copied += 1
        if copied:
            logging.getLogger("Mon").info(
                "identity: migrated %d config file(s) to %s", copied, dest
            )
    except Exception as exc:
        logging.getLogger("Mon").warning("identity config migration skipped: %s", exc)


def _migrate_script_folders():
    """Move legacy custom-script folders to the canonical framework names.

    Both trees are preflighted before anything is moved. A conflict aborts
    startup with a descriptive error instead of silently overwriting a file
    the user edited.
    """
    pairs = (
        (Path(LEGACY_SCRIPTS_FOLDER), Path(SCRIPTS_FOLDER)),
        (Path(LEGACY_IMPORT_FOLDER), Path(IMPORT_FOLDER)),
    )
    for old, new in pairs:
        if old.exists():
            _check_folder_merge(old, new)
    for old, new in pairs:
        if old.exists():
            _merge_script_folder(old, new)


def _check_folder_merge(source, destination):
    import filecmp

    if not destination.exists() and not destination.is_symlink():
        return
    if source.is_symlink() or destination.is_symlink():
        same = (
            source.is_symlink()
            and destination.is_symlink()
            and os.readlink(source) == os.readlink(destination)
        )
    elif source.is_dir() and destination.is_dir():
        for child in source.iterdir():
            _check_folder_merge(child, destination / child.name)
        return
    else:
        same = (
            source.is_file()
            and destination.is_file()
            and filecmp.cmp(source, destination, shallow=False)
        )
    if not same:
        raise FileExistsError(
            f"Cannot migrate scripts: {source} conflicts with {destination}. "
            "Resolve the difference manually; no file will be overwritten."
        )


def _merge_script_folder(source, destination):
    if not destination.exists() and not destination.is_symlink():
        source.rename(destination)
    elif source.is_dir() and not source.is_symlink():
        for child in source.iterdir():
            _merge_script_folder(child, destination / child.name)
        source.rmdir()
    else:
        # The preflight proved this entry is identical to the destination.
        source.unlink()


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


def _patch_process_app_user_model_id():
    """让 Windows 按应用身份分组任务栏，而不是归到 pythonw.exe / Python。

    便携包的 GUI 进程是 pythonw.exe，若不显式设置进程 AUMID，Windows 会用
    pythonw 的默认身份（显示「Python」+ Python 图标）分组任务栏窗口。必须在
    QApplication / 任何 UI 创建之前调用，因此放在 apply() 最开头。
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            NOTIFIER_APP_USER_MODEL_ID
        )
        logging.getLogger("Mon").info(
            "identity: process AUMID set to %s", NOTIFIER_APP_USER_MODEL_ID
        )
    except Exception as exc:
        logging.getLogger("Mon").warning(
            "identity: SetCurrentProcessExplicitAppUserModelID skipped: %s", exc
        )


def _register_aumid_display():
    """在 HKCU 注册 AUMID 的显示名与图标，稳定任务栏分组标题。"""
    if os.name != "nt":
        return
    try:
        import winreg
        icon = str(Path.cwd() / "icons" / "icon.ico")
        key = winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            rf"Software\Classes\AppUserModelId\{NOTIFIER_APP_USER_MODEL_ID}",
        )
        with key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_TITLE)
            if Path(icon).is_file():
                winreg.SetValueEx(key, "IconLocation", 0, winreg.REG_SZ, f"{icon},0")
    except Exception as exc:
        logging.getLogger("Mon").warning(
            "identity: AUMID display registration skipped: %s", exc
        )


def _alias_ok_package(mon_package):
    """让上游 ok-* 的角色代码在 Mon 框架上可导入（No module named 'ok'）。

    工坊离线包里有第三方角色代码沿用 ``from ok import Box/Logger/...`` 与
    ``from ok.task.exceptions import ...``。本项目框架是 Mon 包，不改写第三方
    代码（会破坏其 sha256），改为在 sys.modules 注册 ok -> Mon 别名，保持
    Unicode 路径与校验不变。只补 Mon 里确实存在的子模块，缺失的交给原解析。
    """
    import sys
    try:
        sys.modules.setdefault("ok", mon_package)
        for submodule in (
            "task", "task.exceptions", "device", "device.interaction_methods",
            "device.interaction_methods.keys", "util", "ocr", "feature",
        ):
            mon_name = f"Mon.{submodule}"
            if mon_name in sys.modules:
                sys.modules.setdefault(f"ok.{submodule}", sys.modules[mon_name])
    except Exception as exc:
        logging.getLogger("Mon").warning("identity ok-alias skipped: %s", exc)


def _mutex_owner_file(path):
    """框架 marker 路径 -> 产品自有 marker 路径；非 marker 路径原样返回。"""
    if not isinstance(path, str):
        return path
    name = os.path.basename(path)
    if not name.startswith(FRAMEWORK_MUTEX_PREFIX) or not name.endswith(".pid"):
        return path
    digest = name[len(FRAMEWORK_MUTEX_PREFIX):-len(".pid")]
    return os.path.join(os.path.dirname(path), f"{MUTEX_MARKER_PREFIX}{digest}.pid")


def _patch_mutex_owner_file():
    """重定向单实例标记文件名，与其他 Mon 框架产品隔离。

    只包装 Mon.util.process 里接收 owner_file 的两个函数，不替换全局
    os.path.join——xjtu 那种写法改的是共享的 os.path 模块对象，会波及
    无关代码。互斥量名本身（工作目录的 md5）保持不变，跨版本仍能检测
    已有实例；升级期若新标记尚不存在，接管时回退读取框架旧标记。
    """
    from Mon.util import process as mon_process

    if getattr(mon_process, "_monster_ww_mutex_patched", False):
        return False

    original_retain = mon_process._retain_mutex
    original_terminate = mon_process._terminate_previous_instances

    def retain(handle, owner_file, gui=False, _original=original_retain):
        return _original(handle, _mutex_owner_file(owner_file), gui=gui)

    def terminate(owner_file, replace_console=False, _original=original_terminate):
        target = _mutex_owner_file(owner_file)
        if not os.path.exists(target) and os.path.exists(owner_file):
            # 升级期兼容：旧版本实例留下的是框架名 marker，仍要能识别并接管。
            target = owner_file
        return _original(target, replace_console=replace_console)

    mon_process._retain_mutex = retain
    mon_process._terminate_previous_instances = terminate
    mon_process._monster_ww_mutex_patched = True
    return True


def install_update_status_bridge():
    """Route updater phase messages to UpdateCard's status label on the GUI thread."""
    global _STATUS_BRIDGE
    if _STATUS_BRIDGE is not None:
        return True
    try:
        from PySide6.QtCore import QObject, Signal, Slot
        from PySide6.QtWidgets import QApplication, QWidget
    except Exception as exc:
        logging.getLogger("Mon").warning("update status bridge skipped: %s", exc)
        return False

    class _StatusBridge(QObject):
        status = Signal(str)

        @Slot(str)
        def apply(self, message):
            app = QApplication.instance()
            if app is None:
                return
            # UpdateCard is a child widget nested deep inside the About tab,
            # never a top-level window. Walk each window's full child tree,
            # de-duplicated, so the phase text actually reaches its label.
            seen = set()
            for top in app.topLevelWidgets():
                for widget in [top, *top.findChildren(QWidget)]:
                    if id(widget) in seen:
                        continue
                    seen.add(id(widget))
                    try:
                        setter = getattr(widget, "_set_status", None)
                        if callable(setter) and hasattr(widget, "version_combo"):
                            setter(message)
                            # The framework only spins the progress ring while
                            # checking versions, not while downloading. Show it
                            # during the download/extract phases so users can
                            # tell the update is actually working.
                            spinner = getattr(widget, "_set_check_loading", None)
                            if callable(spinner):
                                done = any(k in message for k in ("就绪", "退出", "失败", "错误"))
                                spinner(not done)
                    except Exception:
                        pass

    # Created on the GUI thread; AutoConnection therefore queues worker
    # emissions onto the event loop before touching any widget.
    _STATUS_BRIDGE = _StatusBridge()
    _STATUS_BRIDGE.status.connect(_STATUS_BRIDGE.apply)
    return True


_STATUS_BRIDGE = None


def report_update_status(message):
    """Thread-safe: emit from the updater worker, painted on the GUI thread."""
    bridge = _STATUS_BRIDGE
    if bridge is None:
        return
    try:
        bridge.status.emit(str(message))
    except Exception:
        pass


def apply():
    """Migrate legacy folders and apply runtime identity patches. Idempotent."""
    # 进程级 AUMID 必须最先设置（早于任何 Mon 导入 / QApplication），否则
    # 任务栏窗口会按 pythonw.exe 归到「Python」分组。
    _patch_process_app_user_model_id()
    _register_aumid_display()
    # Run before importing Mon: the framework creates ok_tasks/ok_import on
    # import, which would make a later migration look like a conflict.
    _migrate_user_config_to_appdata()
    _migrate_script_folders()
    _patch_logging()

    import Mon as mon_package

    # ok -> Mon 别名必须早于任何工坊角色代码导入（CustomCharLoader 在任务运行时用）
    _alias_ok_package(mon_package)

    if getattr(mon_package, "_monster_ww_identity_applied", False):
        return False
    mon_package._monster_ww_identity_applied = True

    patched_config_logger = None
    for patch in (
        _patch_logger_file,
        _patch_mutex_owner_file,
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
