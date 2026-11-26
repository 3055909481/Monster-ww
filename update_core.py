"""Standalone updater core: no GUI, no framework dependencies.

Imported by both the packaged app (to hand off an update) and the standalone
MonsterUpdate.exe (to actually download / verify / extract / install). Keeping
it at the repository root stops Cython from compiling it into a .pyd, so the
updater executable can ship as plain, testable Python.

Every long-running step takes a callback so the GUI can show a real percentage
instead of an indeterminate spinner.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

LOG = logging.getLogger("MonsterUpdate")

REPO = "3055909481/Monster-ww"
RELEASE_PAGE = f"https://github.com/{REPO}/releases"
USER_AGENT = "Monster-ww-updater"
APP_DIR_NAME = "Monster-ww"
EXE_NAME = "Monster-ww.exe"
UPDATER_NAME = "MonsterUpdate.exe"
SWAP_REQUEST_NAME = "swap-request"

# A tree counts as installed only when every entry below is there. A half
# copied tree can still hold the exe and then crash on first start (that is
# exactly how v1.0.39 shipped broken), so this is checked twice: before the
# swap and before anything is relaunched.
REQUIRED_FILES = (
    EXE_NAME,
    "version.txt",
    "assets/data/domain_catalog.json",
    "workshop_bundle/teams.json",
)


class UpdateError(RuntimeError):
    """User-facing failure; the GUI shows str(error) verbatim."""


class UpdateCancelled(UpdateError):
    def __init__(self):
        super().__init__("更新已取消")


# --------------------------------------------------------------------------
# Progress helpers
# --------------------------------------------------------------------------
def emit_progress(callback, phase, percent, detail=""):
    """Report (phase, 0-100, detail); never let a UI error break the update."""
    if callback is None:
        return
    try:
        callback(str(phase), max(0, min(100, float(percent))), str(detail))
    except Exception:
        LOG.exception("progress callback failed")


def _check_cancel(is_cancelled):
    if is_cancelled is not None and is_cancelled():
        raise UpdateCancelled()


# --------------------------------------------------------------------------
# Filesystem helpers
# --------------------------------------------------------------------------
def missing_files(app_dir):
    """Relative paths a runnable install must have; empty list means usable."""
    app_dir = Path(app_dir)
    return [name for name in REQUIRED_FILES if not (app_dir / name).is_file()]


def _sibling(app_dir, suffix):
    """``<app_dir><suffix>``: a sibling on the same volume, never a child."""
    return Path(str(Path(app_dir)) + suffix)


def extract_root(app_dir):
    """Directory the new tree is unpacked into.

    Unpacking next to the install (instead of %TEMP%) makes the final swap a
    rename: a 244 MB tree is copied exactly once, and a rename can never leave
    a half-written directory where the app used to be.
    """
    return _sibling(app_dir, ".new")


def _retry_os(action, attempts=10, delay=0.4):
    """Run an OS call, retrying transient locks (scanners touch fresh files)."""
    last = None
    for attempt in range(attempts):
        try:
            return action()
        except OSError as error:
            last = error
            if attempt < attempts - 1:
                time.sleep(delay)
    raise last


def _copy2_retry(src, dst):
    """``copy2`` that tolerates a scanner holding a freshly written file."""
    _retry_os(lambda: shutil.copyfile(src, dst))
    try:
        shutil.copystat(src, dst, follow_symlinks=False)
    except OSError as error:
        LOG.warning("cannot copy attributes of %s: %s", dst, error)


def _same_volume(path, directory):
    try:
        return os.stat(str(path)).st_dev == os.stat(str(directory)).st_dev
    except OSError:
        return False


def move_tree(src, dst):
    """Move a directory: a rename when possible, else a copy we can retry."""
    src, dst = Path(src), Path(dst)
    if _same_volume(src, dst.parent):
        _retry_os(lambda: os.rename(str(src), str(dst)))
        return
    LOG.info("cross-volume install: copying %s to %s", src, dst)
    shutil.copytree(str(src), str(dst), copy_function=_copy2_retry)
    # Dropping the source is cleanup, not part of the install: one locked file
    # in there (v1.0.39's failure) must not turn a finished copy into an error.
    shutil.rmtree(str(src), ignore_errors=True)


# --------------------------------------------------------------------------
# Network
# --------------------------------------------------------------------------
def asset_candidates(tag):
    from urllib.parse import quote

    base = f"https://github.com/{REPO}/releases/download/{quote(str(tag), safe='')}/"
    plain = str(tag).lstrip("v")
    names = (
        f"Monster-ww-{tag}-windows-x64.7z",
        f"Monster-ww-{tag}-windows-x64.zip",
        f"Monster-ww-win32-Portable-{plain}.7z",
    )
    return [{"name": name, "url": base + quote(name, safe="")} for name in names]


def _head_ok(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return 200 <= response.status < 400
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        return True  # Some CDNs reject HEAD; let GET decide.
    except Exception:
        return False


def _http_text(url, timeout=20):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        if error.code in (403, 429):
            raise UpdateError(
                f"GitHub 检查次数已达上限，请稍后再试或直接前往 {RELEASE_PAGE} 下载"
            ) from error
        raise UpdateError(f"下载失败：HTTP {error.code}") from error
    except Exception as error:
        raise UpdateError(f"网络错误：{error}") from error


def resolve_asset(tag, progress=None, is_cancelled=None):
    """Pick the release asset that actually exists."""
    _check_cancel(is_cancelled)
    emit_progress(progress, "正在查询更新包…", 2)
    candidates = asset_candidates(tag)
    for asset in candidates:
        _check_cancel(is_cancelled)
        if _head_ok(asset["url"]):
            LOG.info("using asset %s", asset["name"])
            return asset
    LOG.warning("no HEAD succeeded for %s; falling back to %s", tag, candidates[0]["name"])
    return candidates[0]


def expected_checksum(asset):
    try:
        text = _http_text(asset["url"] + ".sha256")
    except Exception as error:
        LOG.warning("no checksum for %s: %s", asset["name"], error)
        return None
    match = re.search(r"\b([0-9a-fA-F]{64})\b", text)
    return match.group(1) if match else None


def download(asset, destination, progress=None, is_cancelled=None, phase_base=5.0,
             phase_span=55.0):
    """Stream the archive to disk, reporting byte-level percentage."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(asset["url"], headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=120) as response, open(destination, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            read = 0
            while True:
                _check_cancel(is_cancelled)
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                read += len(chunk)
                if total:
                    pct = phase_base + phase_span * read / total
                    detail = f"{read / 1048576:.1f} / {total / 1048576:.1f} MB"
                else:
                    pct = phase_base + phase_span * 0.5
                    detail = f"已下载 {read / 1048576:.1f} MB"
                emit_progress(progress, "正在下载更新包…", pct, detail)
    except UpdateCancelled:
        raise
    except urllib.error.HTTPError as error:
        raise UpdateError(f"下载失败：HTTP {error.code}，请检查网络或前往 {RELEASE_PAGE} 手动下载") from error
    except Exception as error:
        raise UpdateError(f"下载失败：{error}") from error
    return digest.hexdigest()


def verify(destination, expected, actual, progress=None, is_cancelled=None):
    _check_cancel(is_cancelled)
    emit_progress(progress, "正在校验安装包…", 62, "SHA-256")
    if not expected:
        # Never pretend the package was verified: say so in the window's phase
        # label and in the log, so a tampered/mixed-up download is visible.
        LOG.warning("no SHA-256 published for %s; integrity check skipped",
                    Path(destination).name)
        emit_progress(progress, "校验已跳过（发布页未提供 SHA-256）", 65, "")
        return
    if actual.lower() != expected.lower():
        try:
            Path(destination).unlink(missing_ok=True)
        except OSError:
            pass
        raise UpdateError(
            f"安装包校验失败（期望 {expected[:12]}…，实际 {actual[:12]}…），文件已删除，请重试"
        )
    emit_progress(progress, "校验通过", 65, expected[:12] + "…")


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------
def extract(archive, target, progress=None, is_cancelled=None, phase_base=65.0,
            phase_span=20.0):
    """Extract the archive, reporting per-file progress via py7zr callback."""
    archive, target = Path(archive), Path(target)
    target.mkdir(parents=True, exist_ok=True)
    suffix = archive.suffix.lower()

    if suffix == ".7z":
        try:
            import py7zr
            from py7zr.callbacks import ExtractCallback
        except ImportError as error:
            raise UpdateError("更新程序缺少 7z 解压组件，请联系维护者") from error

        with py7zr.SevenZipFile(archive, mode="r") as zf:
            names = zf.getnames()
            total = max(1, len(names))
            state = {"done": 0}

            class _Reporter(ExtractCallback):
                # py7zr declares six abstract callbacks; a subclass that omits
                # any of them (report_postprocess included) raises TypeError at
                # construction time, so every callback must be implemented even
                # when it does nothing.
                def report_start_preparation(self):
                    pass

                def report_start(self, processing_file_path, processing_bytes):
                    pass

                def report_update(self, decompressed_bytes):
                    pass

                def report_end(self, processing_file_path, wrote_bytes):
                    state["done"] += 1
                    pct = phase_base + phase_span * state["done"] / total
                    emit_progress(progress, "正在解压更新包…", pct,
                                  f"{state['done']} / {total} 个文件")

                def report_warning(self, message):
                    LOG.warning("7z warning: %s", message)

                def report_postprocess(self):
                    pass

            emit_progress(progress, "正在解压更新包…", phase_base, f"共 {total} 个文件")
            zf.extractall(path=target, callback=_Reporter())
    elif suffix == ".zip":
        import zipfile

        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            total = max(1, len(names))
            for index, name in enumerate(names, 1):
                _check_cancel(is_cancelled)
                zf.extract(name, path=target)
                emit_progress(progress, "正在解压更新包…",
                              phase_base + phase_span * index / total,
                              f"{index} / {total} 个文件")
    else:
        raise UpdateError(f"不支持的压缩包格式：{suffix}")

    _check_cancel(is_cancelled)
    extracted_app = target / APP_DIR_NAME
    if not (extracted_app / EXE_NAME).is_file():
        raise UpdateError("更新包结构不正确（缺少 Monster-ww.exe），请重新下载完整安装包")
    return extracted_app


# --------------------------------------------------------------------------
# Process wait / swap / relaunch
# --------------------------------------------------------------------------
def wait_for_exit(pid, timeout=90, progress=None, is_cancelled=None, percent=2.0):
    """Wait for the old app pid to exit without polling a console window."""
    if not pid:
        return
    if os.name != "nt":
        return
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    SYNCHRONIZE = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, int(pid))
    if not handle:
        return  # already gone
    try:
        start = time.monotonic()
        while True:
            _check_cancel(is_cancelled)
            # WAIT_OBJECT_0 = 0, WAIT_TIMEOUT = 0x102
            result = ctypes.windll.kernel32.WaitForSingleObject(handle, 500)
            if result == 0:
                return
            if result != 0x102:
                break
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                raise UpdateError(
                    "主程序未在 90 秒内退出，更新已中止；请手动关闭 Monster-ww 后重试")
            emit_progress(progress, "正在等待主程序退出…", percent,
                          f"已等待 {int(elapsed)} 秒")
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def process_alive(pid):
    """True while the given pid is still running (Windows only)."""
    if not pid or os.name != "nt":
        return False
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False  # already gone
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True  # cannot tell; assume it is still there
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _restore_backup(app_dir, backup):
    """Put the old version back, tolerating a lock on the new tree.

    Deleting the new tree can stall on a single file another process still
    holds (v1.0.39 turned this into a lost install), so the partial tree is
    renamed aside first and only deleted when setting it aside is impossible.
    Returns True only when the old version is actually in place again.
    """
    app_dir, backup = Path(app_dir), Path(backup)
    if not backup.exists():
        return False
    if app_dir.exists():
        failed = _sibling(app_dir, ".failed")
        try:
            shutil.rmtree(failed, ignore_errors=True)
            _retry_os(lambda: os.rename(str(app_dir), str(failed)))
            LOG.warning("partial install moved aside: %s", failed)
        except OSError as error:
            LOG.error("cannot move partial install %s aside: %s", app_dir, error)
            try:
                _retry_os(lambda: shutil.rmtree(str(app_dir)))
            except OSError as remove_error:
                LOG.error("cannot remove partial install %s: %s", app_dir, remove_error)
                return False
    try:
        _retry_os(lambda: os.rename(str(backup), str(app_dir)))
    except OSError as error:
        LOG.error("restore from %s failed: %s", backup, error)
        return False
    LOG.info("restored original install from %s", backup)
    return True


def _rel_files(root):
    """Relative paths of every file below root (POSIX separators, sorted)."""
    root = Path(root)
    return sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file())


def _install_filewise(new_tree, app_dir, backup, progress=None, is_cancelled=None):
    """Install file by file when the whole directory cannot be renamed.

    Windows refuses to rename a directory while *any* handle below it is open
    (an OpenVINO blob held by a scanner/driver, a stale process, ...). That made
    the v1.0.39 update fail even though almost every file was replaceable.
    Mirroring coco-ww's approach: move each old file to the backup, place each
    new file with os.replace, and journal every step so a failure restores the
    exact previous state. A single locked file now fails alone and rolls back,
    instead of blocking the rename of the entire install.
    """
    new_tree, app_dir, backup = Path(new_tree), Path(app_dir), Path(backup)
    journal = []  # (rel, had_old, placed_new)
    emit_progress(progress, "正在安装更新…", 90, "逐个文件替换（目录被占用）")
    try:
        # 1) Retire old files into the backup tree.
        old_files = _rel_files(app_dir) if app_dir.exists() else []
        total = max(1, len(old_files) + len(_rel_files(new_tree)))
        for index, rel in enumerate(old_files):
            _check_cancel(is_cancelled)
            src, dst = app_dir / rel, backup / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            _retry_os(lambda s=src, d=dst: os.replace(str(s), str(d)))
            journal.append([rel, True, False])
            if index % 10 == 0:
                emit_progress(progress, "正在安装更新…", 90 + int(4 * index / total),
                              f"备份旧文件 {index + 1}/{len(old_files)}")

        # 2) Place the new files.
        new_files = _rel_files(new_tree)
        for index, rel in enumerate(new_files):
            _check_cancel(is_cancelled)
            src, dst = new_tree / rel, app_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            existing = rel in old_files
            if existing:
                os.replace(str(src), str(dst))
                # Find the journal row we just retired and mark it placed.
                for row in journal:
                    if row[0] == rel and row[2] is False:
                        row[2] = True
                        break
            else:
                _retry_os(lambda s=src, d=dst: os.replace(str(s), str(d)))
                journal.append([rel, False, True])
            if index % 10 == 0:
                emit_progress(progress, "正在安装更新…", 94 + int(4 * index / total),
                              f"写入新文件 {index + 1}/{len(new_files)}")
    except Exception:
        # Roll back in reverse order: new files back to the tree, old files home.
        # A backup file that cannot be restored is a lost install, so surface it
        # instead of silently leaving a half-updated app that starts and crashes.
        rollback_errors = []
        for rel, had_old, placed_new in reversed(journal):
            try:
                if placed_new:
                    (app_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                    _retry_os(lambda s=app_dir / rel, d=new_tree / rel: os.replace(str(s), str(d)),
                              attempts=5)
                if had_old:
                    (backup / rel).parent.mkdir(parents=True, exist_ok=True)
                    _retry_os(lambda s=backup / rel, d=app_dir / rel: os.replace(str(s), str(d)),
                              attempts=5)
            except OSError as error:
                rollback_errors.append(f"{rel}: {error}")
        if rollback_errors:
            raise UpdateError(
                "安装失败且部分文件未能还原：" + "；".join(rollback_errors[:5]) +
                f"。原版本备份在 {backup}，请勿关闭程序并重试") from None
        raise
    # Remove directories left empty in the old install (files deleted upstream).
    for folder in sorted((p for p in app_dir.rglob('*') if p.is_dir()),
                         key=lambda p: len(p.parts), reverse=True):
        try:
            folder.rmdir()
        except OSError:
            pass
    return app_dir


def install(extracted_app, app_dir, progress=None, is_cancelled=None):
    """Swap the new tree in, keeping the old one recoverable.

    Fast path: rename the complete tree (one atomic operation). If Windows
    refuses because a file below the directory is locked, fall back to a
    journaled per-file replace so one stubborn handle cannot block the update.
    """
    extracted_app, app_dir = Path(extracted_app), Path(app_dir)
    _check_cancel(is_cancelled)

    # Never touch the live install with a tree that cannot start on its own.
    missing = missing_files(extracted_app)
    if missing:
        raise UpdateError(
            "更新包不完整（缺少 " + "、".join(missing) + "），原版本未改动，请重新下载")

    emit_progress(progress, "正在安装更新…", 88, "备份当前版本")
    backup = _sibling(app_dir, ".old")
    shutil.rmtree(backup, ignore_errors=True)
    shutil.rmtree(_sibling(app_dir, ".failed"), ignore_errors=True)
    moved_old = False
    filewise = False
    try:
        if app_dir.exists():
            # The old process may have just exited; Windows can still hold a
            # transient handle on its directory. Retry the rename briefly.
            for attempt in range(10):
                try:
                    os.rename(str(app_dir), str(backup))
                    moved_old = True
                    break
                except OSError as error:
                    if attempt == 9:
                        # Whole-directory rename is blocked by a persistent
                        # handle; degrade to per-file replacement instead of
                        # giving up (the v1.0.39 failure mode).
                        LOG.warning("directory rename blocked (%s); using filewise install", error)
                        filewise = True
                        break
                    time.sleep(0.5)
                    emit_progress(progress, "正在安装更新…", 88,
                                  f"等待文件释放（{attempt + 1}/10）")
        _check_cancel(is_cancelled)
        emit_progress(progress, "正在安装更新…", 94, "写入新版本")
        if filewise:
            backup.mkdir(parents=True, exist_ok=True)
            # _install_filewise rolls its own journal back on failure; the only
            # case needing the outer restore is when the whole old tree was
            # moved aside, which a per-file install never does by itself.
            _install_filewise(extracted_app, app_dir, backup, progress, is_cancelled)
            moved_old = False  # per-file path restored itself or left app intact
        else:
            move_tree(extracted_app, app_dir)
        missing = missing_files(app_dir)
        if missing:
            raise UpdateError("新版本缺少文件（" + "、".join(missing) + "）")
    except UpdateCancelled:
        if moved_old and not _restore_backup(app_dir, backup):
            raise UpdateError(
                f"已取消，但原版本恢复失败；原版本完整保存在 {backup}，请手动改回 {app_dir}"
            ) from None
        raise
    except Exception as error:
        if not moved_old:
            # The rename never happened, so the running install is untouched.
            raise UpdateError(
                f"安装失败，原版本未改动：{error}。请确认没有其他程序占用 {app_dir} 后重试"
            ) from error
        if _restore_backup(app_dir, backup):
            raise UpdateError(
                f"安装失败，已恢复原版本：{error}。可稍后重试，或手动下载完整安装包覆盖"
            ) from error
        raise UpdateError(
            f"安装失败，且自动恢复原版本也失败：{error}。"
            f"原版本完整保存在 {backup}，请手动将其改名为 {app_dir} 后重试"
        ) from error
    emit_progress(progress, "安装完成", 98, "清理旧版本")
    return backup


def _detached_flags():
    """Windows creation flags that keep a spawned process out of the parent's
    console/job, so it survives when the updater or the old app exits (as in
    coco-ww's _spawn_helper). Falls back to a new process group where a flag is
    unavailable on this Windows build."""
    if os.name != "nt":
        return 0
    flags = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
    for name in ('DETACHED_PROCESS', 'CREATE_BREAKAWAY_FROM_JOB'):
        extra = getattr(subprocess, name, 0)
        if extra:
            flags |= extra
    return flags


def _spawn_detached(args, cwd):
    """Spawn a process that outlives its parent, with a safe fallback."""
    if os.name != "nt":
        return subprocess.Popen(args, cwd=str(cwd), start_new_session=True,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, close_fds=True)
    try:
        return subprocess.Popen(args, cwd=str(cwd), creationflags=_detached_flags(),
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, close_fds=True)
    except OSError:
        # Some job objects refuse breakaway; still try a plain new group.
        return subprocess.Popen(
            args, cwd=str(cwd), creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True)


def relaunch(app_dir, exe_name=EXE_NAME):
    exe = Path(app_dir) / exe_name
    if not exe.is_file():
        raise UpdateError(f"未找到 {exe_name}，请手动启动 {app_dir}")
    _spawn_detached([str(exe)], app_dir)
    return exe


def revive(app_dir, exe_name=EXE_NAME):
    """Restart *something runnable* after a failed/cancelled update.

    The app may have quit already (it is asked to quit right before the swap),
    so a failure can leave the user with nothing on screen. Put the backup
    back when what is on disk is not a complete install, then start it.
    Returns True on success.
    """
    app_dir = Path(app_dir)
    backup = _sibling(app_dir, ".old")
    if missing_files(app_dir) and backup.is_dir() and not missing_files(backup):
        LOG.warning("install in %s is incomplete; restoring %s", app_dir, backup)
        _restore_backup(app_dir, backup)
    if missing_files(app_dir) or not (app_dir / exe_name).is_file():
        LOG.error("nothing runnable in %s", app_dir)
        return False
    try:
        relaunch(app_dir, exe_name)
    except (UpdateError, OSError) as error:
        LOG.error("restart after failed update failed: %s", error)
        return False
    return True


def staging_root():
    return Path(tempfile.gettempdir()) / "Monster-ww-update"


def swap_request_path():
    """Flag the running app polls: once it appears, the app quits by itself.

    The app must survive while the package downloads, so it is not killed at
    hand-off any more; this file is the "close now, your files are about to be
    replaced" signal.
    """
    return staging_root() / SWAP_REQUEST_NAME


def request_swap():
    path = swap_request_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("1", encoding="utf-8")
        LOG.info("swap requested through %s", path)
    except OSError as error:
        LOG.warning("cannot write swap request %s: %s", path, error)


def clear_swap_request():
    try:
        swap_request_path().unlink()
    except OSError:
        pass


def updater_path(app_dir):
    """Path of the standalone updater shipped alongside the app."""
    return Path(app_dir) / UPDATER_NAME


def updater_arguments(version, app_dir, pid, exe_name=EXE_NAME):
    return ["--version", str(version), "--app-dir", str(app_dir),
            "--pid", str(pid), "--exe", exe_name]


def launch_updater(updater_exe, version, app_dir, pid, exe_name=EXE_NAME):
    """Hand off the update to the standalone GUI updater, then let the app quit."""
    updater_exe = Path(updater_exe)
    if not updater_exe.is_file():
        raise UpdateError(
            f"未找到独立更新程序 {UPDATER_NAME}，请前往 {RELEASE_PAGE} 手动下载完整安装包")
    # The updater lives inside app_dir, which the swap replaces. Copy it to a
    # temp location first so it can replace its own original freely.
    temp_dir = Path(tempfile.gettempdir()) / "Monster-ww-updater"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_exe = temp_dir / UPDATER_NAME
    try:
        shutil.copy2(updater_exe, temp_exe)
    except OSError:
        # A previous updater window is still open and holds this exact file;
        # fall back to a per-run name instead of failing the whole update.
        temp_exe = temp_dir / f"MonsterUpdate-{os.getpid()}-{int(time.time())}.exe"
        try:
            shutil.copy2(updater_exe, temp_exe)
        except Exception as error:
            raise UpdateError(f"无法启动更新程序：{error}") from error

    args = [str(temp_exe), *updater_arguments(version, app_dir, pid, exe_name)]
    try:
        # Detached from the parent's console/job so the updater survives the
        # app's shutdown (coco-ww _spawn_helper pattern).
        _spawn_detached(args, temp_dir)
    except Exception as error:
        raise UpdateError(f"无法启动更新程序：{error}") from error
    return temp_exe


def run_full_update(version, app_dir, pid, exe_name=EXE_NAME, progress=None,
                    is_cancelled=None, auto_relaunch=True):
    """Full standalone update flow. Progress callback: (phase, percent, detail)."""
    app_dir = Path(app_dir)
    _check_cancel(is_cancelled)
    emit_progress(progress, "准备更新…", 1, str(version))

    staging = staging_root()
    staging.mkdir(parents=True, exist_ok=True)
    new_root = extract_root(app_dir)
    backup = None
    succeeded = False
    try:
        asset = resolve_asset(version, progress, is_cancelled)
        archive = staging / asset["name"]

        actual = download(asset, archive, progress, is_cancelled)
        checksum = expected_checksum(asset)
        verify(archive, checksum, actual, progress, is_cancelled)

        # Unpack next to the install: the swap then becomes a rename of a
        # complete tree instead of a second 244 MB copy of it.
        shutil.rmtree(new_root, ignore_errors=True)
        new_root.mkdir(parents=True, exist_ok=True)
        extracted_app = extract(archive, new_root, progress, is_cancelled)

        missing = missing_files(extracted_app)
        if missing:
            raise UpdateError(
                "更新包不完整（缺少 " + "、".join(missing) + "），请重新下载完整安装包")

        # Downloading and unpacking happen while the app keeps running; only
        # the swap itself needs it gone, so ask for the quit right here.
        emit_progress(progress, "正在请求主程序退出…", 85, "")
        request_swap()
        wait_for_exit(pid, progress=progress, is_cancelled=is_cancelled, percent=85.5)
        emit_progress(progress, "主程序已退出，开始安装…", 86, "")

        backup = install(extracted_app, app_dir, progress, is_cancelled)
        if auto_relaunch:
            emit_progress(progress, "正在重新启动…", 99, EXE_NAME)
            relaunch(app_dir, exe_name)
        succeeded = True
        emit_progress(progress, "更新完成", 100, str(version))
        return True
    finally:
        # The app quits on this flag, so it must never outlive the attempt that
        # set it: a stale flag would shut the app down on its next start.
        clear_swap_request()
        # Never leak a ~250 MB archive or a half-unpacked tree, even on failure:
        # the next attempt re-downloads anyway. Cleanup is strictly best effort
        # - one locked file here must not be reported as a failed install, which
        # is what broke v1.0.39. The .old backup is kept unless the whole update
        # succeeded, so a failed install stays recoverable through revive() or a
        # manual rename.
        shutil.rmtree(new_root, ignore_errors=True)
        if succeeded and backup is not None:
            shutil.rmtree(backup, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
