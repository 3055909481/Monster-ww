"""Monster-ww standalone updater window.

Built as a separate one-file executable (MonsterUpdate.exe) and shipped next to
Monster-ww.exe. The main app only launches this with args; everything visible
to the user -- download %, verify, extract %, install %, restart -- lives here,
so an update never depends on the app's own UI still being alive.

GUI uses tkinter (Python standard library, no Qt plugin/DLL issues) and runs
the update in a worker thread, marshalling progress back to the Tk main loop.
"""
from __future__ import annotations

import argparse
import logging
import queue
import sys
import threading
import traceback
import webbrowser
from pathlib import Path

from update_core import (RELEASE_PAGE, UpdateCancelled, UpdateError,
                         run_full_update)

LOG = logging.getLogger("MonsterUpdate")


class UpdaterWindow:
    def __init__(self, version, app_dir, pid, exe_name):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.version = version
        self.app_dir = Path(app_dir)
        self.pid = pid
        self.exe_name = exe_name
        self.queue = queue.Queue()
        self.cancelled = threading.Event()
        self.worker = None
        self.done = False

        self.root = tk.Tk()
        self.root.title("Monster-ww 更新程序")
        self.root.geometry("560x330")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        try:
            self.root.attributes("-topmost", True)
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

    # ------------------------------------------------------------------ UI
    def _append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_action(self):
        if self.done:
            self.root.destroy()
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

    def _poll(self):
        try:
            while True:
                item = self.queue.get_nowait()
                kind = item[0]
                if kind == "progress":
                    _, phase, percent, detail = item
                    self.phase.set(phase)
                    self.bar["value"] = percent
                    self.percent.set(f"{percent:.0f}%")
                    self.detail.set(detail)
                    if detail and phase.startswith(("正在下载", "正在解压")):
                        self._append_log(f"{phase} {detail}")
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
                    message = item[1]
                    self.phase.set("更新失败")
                    self.action_button.configure(state="normal", text="关闭")
                    self._append_log("错误：" + message)
                    self.detail.set("可点「手动下载」自行更新")
                elif kind == "cancelled":
                    self.done = True
                    self.phase.set("更新已取消")
                    self.action_button.configure(state="normal", text="关闭")
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _auto_close(self):
        if self.root.winfo_exists():
            self.root.destroy()

    # -------------------------------------------------------------- worker
    def _run(self):
        def cancelled():
            return self.cancelled.is_set()

        try:
            run_full_update(self.version, self.app_dir, self.pid, self.exe_name,
                            progress=self._progress, is_cancelled=cancelled)
            self.queue.put(("done",))
        except UpdateCancelled:
            self.queue.put(("cancelled",))
        except UpdateError as error:
            self.queue.put(("error", str(error)))
        except Exception:
            LOG.exception("update failed")
            self.queue.put(("error", "更新出错：" + traceback.format_exc(limit=3)))

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

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        pid = int(args.pid)
    except ValueError:
        pid = 0
    UpdaterWindow(args.version, args.app_dir, pid, args.exe).start()
    return 0


if __name__ == "__main__":
    sys.exit(main())
