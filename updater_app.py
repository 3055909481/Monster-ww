"""Monster-ww standalone updater window.

Built as a separate one-file executable (MonsterUpdate.exe) and shipped next to
Monster-ww.exe. The main app only launches this with args; everything visible
to the user -- download %, verify, extract %, install %, restart -- lives here,
so an update never depends on the app's own UI still being alive.

GUI uses tkinter (Python standard library, no Qt plugin/DLL issues) and runs
the update in a worker thread, marshalling progress back to the Tk main loop.

The main app quits the moment the hand-off returns, so this window owns two
responsibilities beyond showing a progress bar:

* keep "cancel" available while only the download/extract runs, and hard-disable
  it once the directory swap starts (``swapping``);
* bring the app back if the update fails or is cancelled (``update_core.revive``),
  otherwise the user is left with no window at all: the app exited seconds ago.
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import queue
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path

import update_core
from update_core import RELEASE_PAGE, UpdateCancelled, UpdateError, run_full_update

LOG = logging.getLogger("MonsterUpdate")

_MUTEX_HANDLE = None


def _log_file_path():
    """%APPDATA%/Monster-ww/logs, next to the app's own data."""
    base = os.environ.get("APPDATA") or tempfile.gettempdir()
    return Path(base) / "Monster-ww" / "logs" / "MonsterUpdate.log"


def _prepare_logging():
    """Send logs to a file as well: a windowed PyInstaller build has no stderr.

    Without this, every failure a user reports is untraceable.
    """
    handlers = []
    log_file = None
    try:
        log_file = _log_file_path()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            log_file, maxBytes=1_000_000, backupCount=1, encoding="utf-8",
            delay=False))
    except Exception:
        log_file = None
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        handlers=handlers or [logging.NullHandler()],
                        force=True)
    return log_file


def _already_running():
    """True when another updater instance owns the single-instance mutex."""
    global _MUTEX_HANDLE
    if os.name != "nt":
        return False
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateMutexW(None, 1, "Local\\Monster-ww-updater-instance")
    if not handle:
        return False  # cannot create one: never block a real update
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return True
    _MUTEX_HANDLE = handle  # held for the process lifetime
    return False


def _notify(title, message):
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, title, 0x40)
            return
        except Exception:
            pass
    print(message)


class UpdaterWindow:
    def __init__(self, version, app_dir, pid, exe_name, log_file=None):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.version = version
        self.app_dir = Path(app_dir)
        self.pid = pid
        self.exe_name = exe_name
        self.log_file = log_file
        self.queue = queue.Queue()
        self.cancelled = threading.Event()
        self.worker = None
        self.done = False
        # Set once files are about to be replaced; cancelling after that point
        # cannot be honoured safely.
        self.swapping = False
        self._last_phase = None
        self._log_bucket = -1

        self.root = tk.Tk()
        self.root.title("Monster-ww 更新程序")
        self.root.geometry("560x330")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # Float above the desktop just long enough to be noticed, then behave
        # normally so the user can reach a browser for a manual download.
        try:
            self.root.attributes("-topmost", True)
            self.root.after(1500, self._drop_topmost)
        except Exception:
            pass

        pad = {"padx": 18}
        tk.Label(self.root, text="Monster-ww 更新", font=("Microsoft YaHei UI", 15, "bold")).pack(
            pady=(18, 2), **pad)
        tk.Label(self.root, text=f"正在更新到 {version}", font=("Microsoft YaHei UI", 10),
                 fg="#555").pack(pady=(0, 14), **pad)

        self.phase = tk.StringVar(value="准备更新…")
        tk.Label(self.root, textvariable=self.phase, font=("Microsoft YaHei UI", 10),
                 anchor="w").pack(fill="x", **pad)

        self.bar = ttk.Progressbar(self.root, orient="horizontal", length=524,
                                   mode="determinate", maximum=100)
        self.bar.pack(pady=(6, 4), **pad)

        self.percent = tk.StringVar(value="0%")
        self.detail = tk.StringVar(value="")
        info = tk.Frame(self.root)
        info.pack(fill="x", **pad)
        tk.Label(info, textvariable=self.percent, font=("Microsoft YaHei UI", 9),
                 width=6, anchor="w").pack(side="left")
        tk.Label(info, textvariable=self.detail, font=("Microsoft YaHei UI", 9),
                 fg="#666", anchor="e").pack(side="right")

        self.log = tk.Text(self.root, height=6, state="disabled",
                           font=("Consolas", 9), bg="#f7f7f7", relief="flat")
        self.log.pack(fill="both", expand=True, padx=18, pady=(10, 6))

        buttons = tk.Frame(self.root)
        buttons.pack(fill="x", pady=(0, 14), **pad)
        self.open_button = tk.Button(buttons, text="手动下载", width=12,
                                     command=self._open_release)
        self.open_button.pack(side="left")
        self.action_button = tk.Button(buttons, text="取消", width=12,
                                       command=self._on_action)
        self.action_button.pack(side="right")

        self._append_log(f"目标版本 {version}")
        self._append_log(f"安装目录 {self.app_dir}")
        if self.log_file:
            self._append_log(f"日志文件 {self.log_file}")

    # ------------------------------------------------------------------ UI
    def _drop_topmost(self):
        try:
            self.root.attributes("-topmost", False)
        except Exception:
            pass

    def _append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_action(self):
        if self.done:
            self.root.destroy()
            return
        if self.swapping:
            # Files are being replaced; a cancel here could leave a mix of
            # versions on disk, so keep the button disabled and explain instead.
            self._append_log("已开始替换文件，无法取消。")
            return
        if self.worker and self.worker.is_alive() and not self.cancelled.is_set():
            self.cancelled.set()
            self.phase.set("正在取消…")
            self.action_button.configure(state="disabled")

    def _on_close(self):
        if self.done:
            self.root.destroy()
            return
        self._on_action()

    def _open_release(self):
        try:
            webbrowser.open(RELEASE_PAGE)
        except Exception:
            pass

    def _progress(self, phase, percent, detail):
        self.queue.put(("progress", phase, percent, detail))

    def _apply_progress(self, phase, percent, detail):
        self.phase.set(phase)
        self.bar["value"] = percent
        self.percent.set(f"{percent:.0f}%")
        self.detail.set(detail)
        if not self.swapping and phase.startswith(("正在安装更新", "主程序已退出")):
            self.swapping = True
            try:
                self.action_button.configure(state="disabled")
            except Exception:
                pass
        if phase != self._last_phase:
            self._last_phase = phase
            self._log_bucket = int(percent // 10)
            self._append_log(f"{phase} {detail}".strip())
            return
        # A 244 MB package reports ~1000 download chunks and ~1000 files; log
        # one line per 10% instead of flooding the 6-line panel.
        if not detail or not phase.startswith(("正在下载", "正在解压")):
            return
        bucket = int(percent // 10)
        if bucket > self._log_bucket:
            self._log_bucket = bucket
            self._append_log(f"{phase} {detail}")

    def _poll(self):
        try:
            while True:
                item = self.queue.get_nowait()
                kind = item[0]
                if kind == "progress":
                    _, phase, percent, detail = item
                    self._apply_progress(phase, percent, detail)
                elif kind == "done":
                    self.done = True
                    self.phase.set("更新完成，程序已重新启动")
                    self.bar["value"] = 100
                    self.percent.set("100%")
                    self.detail.set(self.version)
                    self.action_button.configure(state="normal", text="关闭")
                    self._append_log("更新完成。")
                    self.root.after(4000, self._auto_close)
                elif kind == "error":
                    self.done = True
                    message, revived = item[1], item[2]
                    self.phase.set("更新失败")
                    self.action_button.configure(state="normal", text="关闭")
                    self._append_log("错误：" + message)
                    if revived:
                        self.detail.set("已重新启动原版本")
                        self._append_log("已重新启动原版本，可稍后重试。")
                    else:
                        self.detail.set("请手动启动或重新下载")
                elif kind == "cancelled":
                    self.done = True
                    revived = item[1]
                    self.phase.set("更新已取消")
                    self.action_button.configure(state="normal", text="关闭")
                    if revived:
                        self.detail.set("已重新启动原版本")
                        self._append_log("已取消，已重新启动原版本。")
                    else:
                        self.detail.set("请手动启动程序")
        except queue.Empty:
            pass
        try:
            self.root.after(120, self._poll)
        except Exception:
            pass

    def _auto_close(self):
        try:
            if self.root.winfo_exists():
                self.root.destroy()
        except Exception:
            pass

    # -------------------------------------------------------------- worker
    def _recover(self):
        """Start the app again after a failed/cancelled update.

        The app exits right after hand-off, so a failure here would leave the
        user with nothing on screen. Never start a second instance: skip when
        the original process is still alive.
        """
        if self.pid and update_core.process_alive(self.pid):
            return False
        try:
            return update_core.revive(self.app_dir, self.exe_name)
        except Exception:
            LOG.exception("restart after failed update failed")
            return False

    def _run(self):
        def cancelled():
            return self.cancelled.is_set()

        try:
            run_full_update(self.version, self.app_dir, self.pid, self.exe_name,
                            progress=self._progress, is_cancelled=cancelled)
            self.queue.put(("done",))
        except UpdateCancelled:
            self.queue.put(("cancelled", self._recover()))
        except UpdateError as error:
            self.queue.put(("error", str(error), self._recover()))
        except Exception as error:
            LOG.exception("update failed")
            self.queue.put(("error",
                            f"更新出错（{type(error).__name__}: {error}），详情见日志",
                            self._recover()))

    def start(self):
        self.worker = threading.Thread(target=self._run, name="MonsterUpdate-worker", daemon=True)
        self.worker.start()
        self._poll()
        self.root.mainloop()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Monster-ww standalone updater")
    parser.add_argument("--version", required=True)
    parser.add_argument("--app-dir", required=True)
    parser.add_argument("--pid", default="0")
    parser.add_argument("--exe", default="Monster-ww.exe")
    args = parser.parse_args(argv)

    log_file = _prepare_logging()
    if _already_running():
        LOG.warning("another updater instance is running; refusing to start")
        _notify("Monster-ww 更新程序", "已有一个更新窗口在运行，请先关闭它或等待它结束后再试。")
        return 2

    LOG.info("updater start: version=%s app_dir=%s pid=%s log=%s",
             args.version, args.app_dir, args.pid, log_file)
    try:
        pid = int(args.pid)
    except ValueError:
        pid = 0
    UpdaterWindow(args.version, args.app_dir, pid, args.exe, log_file).start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
