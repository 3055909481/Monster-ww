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
import sys
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
    if expected and actual.lower() != expected.lower():
        try:
            Path(destination).unlink(missing_ok=True)
        except OSError:
            pass
        raise UpdateError(
            f"安装包校验失败（期望 {expected[:12]}…，实际 {actual[:12]}…），文件已删除，请重试"
        )
    emit_progress(progress, "校验通过", 65, expected[:12] + "…" if expected else "未提供校验值")


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


def install(extracted_app, app_dir, progress=None, is_cancelled=None):
    """Back up, swap directories, roll back on failure."""
    extracted_app, app_dir = Path(extracted_app), Path(app_dir)
    _check_cancel(is_cancelled)
    emit_progress(progress, "正在安装更新…", 88, "备份当前版本")
    backup = Path(str(app_dir) + ".old")
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    try:
        # The old process may have just exited; Windows can still hold a
        # transient handle on its directory. Retry the rename briefly.
        for attempt in range(10):
            try:
                if app_dir.exists():
                    app_dir.rename(backup)
                break
            except OSError:
                if attempt == 9:
                    raise
                time.sleep(0.5)
                emit_progress(progress, "正在安装更新…", 88,
                              f"等待文件释放（{attempt + 1}/10）")
        _check_cancel(is_cancelled)
        emit_progress(progress, "正在安装更新…", 94, "写入新版本")
        shutil.move(str(extracted_app), str(app_dir))
    except UpdateCancelled:
        if not app_dir.exists() and backup.exists():
            backup.rename(app_dir)
        raise
    except Exception as error:
        # Roll back so the user is never left with no runnable app.
        if not app_dir.exists() and backup.exists():
            try:
                backup.rename(app_dir)
            except Exception as rollback_error:
                LOG.error("rollback failed: %s", rollback_error)
        raise UpdateError(
            f"安装失败，已恢复原版本：{error}。请关闭杀毒软件或手动解压覆盖后重试"
        ) from error
    emit_progress(progress, "安装完成", 98, "清理旧版本")
    # Old backup is disposable; remove it in the background on next run only.
    return backup


def relaunch(app_dir, exe_name=EXE_NAME):
    exe = Path(app_dir) / exe_name
    if not exe.is_file():
        raise UpdateError(f"未找到 {exe_name}，请手动启动 {app_dir}")
    subprocess.Popen([str(exe)], cwd=str(app_dir), close_fds=True)
    return exe


def staging_root():
    return Path(tempfile.gettempdir()) / "Monster-ww-update"


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
    except Exception as error:
        raise UpdateError(f"无法启动更新程序：{error}") from error

    args = [str(temp_exe), *updater_arguments(version, app_dir, pid, exe_name)]
    try:
        if os.name == "nt":
            # The updater is a GUI window app; no console, new process group
            # so it survives the parent's shutdown.
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(args, creationflags=flags, cwd=str(temp_dir),
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
        else:
            subprocess.Popen(args, cwd=str(temp_dir), start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
    except Exception as error:
        raise UpdateError(f"无法启动更新程序：{error}") from error
    return temp_exe


def run_full_update(version, app_dir, pid, exe_name=EXE_NAME, progress=None,
                    is_cancelled=None, auto_relaunch=True):
    """Full standalone update flow. Progress callback: (phase, percent, detail)."""
    app_dir = Path(app_dir)
    _check_cancel(is_cancelled)
    emit_progress(progress, "准备更新…", 1, str(version))

    asset = resolve_asset(version, progress, is_cancelled)
    staging = staging_root()
    staging.mkdir(parents=True, exist_ok=True)
    archive = staging / asset["name"]

    actual = download(asset, archive, progress, is_cancelled)
    checksum = expected_checksum(asset)
    verify(archive, checksum, actual, progress, is_cancelled)

    extracted = staging / APP_DIR_NAME
    if extracted.exists():
        shutil.rmtree(extracted, ignore_errors=True)
    extracted_app = extract(archive, staging, progress, is_cancelled)

    # Only the directory swap needs the app gone; downloading and extracting
    # happen in a separate staging folder, so progress starts immediately.
    wait_for_exit(pid, progress=progress, is_cancelled=is_cancelled, percent=85.0)
    emit_progress(progress, "主程序已退出，开始安装…", 86, "")

    backup = install(extracted_app, app_dir, progress, is_cancelled)
    if auto_relaunch:
        emit_progress(progress, "正在重新启动…", 99, EXE_NAME)
        relaunch(app_dir, exe_name)
    emit_progress(progress, "更新完成", 100, str(version))

    # Best-effort cleanup; failures are harmless and must not fail the update.
    try:
        shutil.rmtree(backup, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)
    except Exception:
        pass
    return True
