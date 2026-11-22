"""In-app updater for the PyInstaller full-package build.

This module supplies the small subset of the ``pyappify`` launcher API that
the framework's AboutTab/UpdateCard expects, backed by this fork's GitHub
releases instead. Installing it (see ``install()``) makes the existing update
UI work: version picker, check button, automatic startup check and toast,
plus update-then-restart.

Full-package updates must replace files while the app is not running. The
flow is therefore two-phase:

1. ``update_to_version`` (in the running GUI) downloads and extracts the
   release into a staging directory, writes a small apply script, spawns it
   detached, and returns ``{"updated": True, "restart_required": True}``.
   The framework then emits ``communicate.quit`` and the app exits.
2. The detached apply script waits for the old PID to exit, swaps the app
   directory (old -> .old, staged -> app dir), and relaunches Monster-ww.exe.
   User data lives in %APPDATA% and is never touched.

Every network step is best-effort: failures are reported to the UI as a
string (UpdateCard shows them) and never crash the app. Checking is opt-in
via the user pressing the button or the framework's startup check; browsing
the workshop and normal runtime stay fully offline.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

LOG = logging.getLogger(__name__)

REPO = "3055909481/Monster-ww"
API_RELEASES = f"https://api.github.com/repos/{REPO}/releases"
ASSET_SUFFIX = "-windows-x64.7z"
APP_DIR_NAME = "Monster-ww"


def _app_dir() -> Path:
    """Directory holding Monster-ww.exe (the packaged app root)."""
    exe = Path(sys.executable)
    # PyInstaller onefile runs from a temp _MEI dir; sys.argv[0] is the real exe.
    candidate = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else exe
    if candidate.is_file():
        return candidate.parent
    return exe.parent


def _current_version() -> str:
    """Read the version stamped into the package at build time."""
    try:
        root = _app_dir()
        for path in (root / "version.txt", root.parent / "version.txt"):
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="ignore").strip()
                if text:
                    return text
    except OSError:
        pass
    module = sys.modules.get("pyappify")
    value = getattr(module, "app_version", None) if module else None
    if value:
        return str(value)
    # v1.0.27 and earlier shipped no version.txt; the version is in the exe's
    # VERSIONINFO ProductVersion, which works even for those older packages.
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            exe = str(_app_dir() / "Monster-ww.exe")
            size = ctypes.windll.version.GetFileVersionInfoSizeW(exe, None)
            if size:
                buf = ctypes.create_string_buffer(size)
                ctypes.windll.version.GetFileVersionInfoW(exe, None, size, buf)
                ln = wintypes.DWORD()
                trans = ctypes.c_void_p()
                ctypes.windll.version.VerQueryValueW(
                    buf, "\\VarFileInfo\\Translation", ctypes.byref(trans), ctypes.byref(ln))
                if ln.value:
                    arr = ctypes.cast(trans, ctypes.POINTER(wintypes.WORD))
                    root = f"\\StringFileInfo\\{arr[0]:04x}{arr[1]:04x}\\ProductVersion"
                    text = ctypes.c_wchar_p()
                    length = wintypes.DWORD()
                    if ctypes.windll.version.VerQueryValueW(
                            buf, root, ctypes.byref(text), ctypes.byref(length)) and text.value:
                        return str(text.value)
        except Exception:
            pass
    return "dev"


def _parse_version(text):
    """(major, minor, patch, stage_rank, stage_num) from a version string."""
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-.]?(alpha|beta|rc)[.\-]?(\d+))?", str(text))
    if not match:
        return None
    stage = {"": 3, "alpha": 0, "beta": 1, "rc": 2}.get(match.group(4) or "", 3)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)),
            stage, int(match.group(5) or 0))


def is_greater_version(left, right) -> bool:
    a, b = _parse_version(left), _parse_version(right)
    if a is None or b is None:
        return False
    return a > b


def _http_json(url, timeout=20):
    import json
    import urllib.request

    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "Monster-ww-updater",
    })
    token = os.environ.get("GITHUB_TOKEN") or ""
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_version_list(release_only=True, exit_event=None):
    """Return the framework's version-list shape, newest first."""
    try:
        releases = _http_json(f"{API_RELEASES}?per_page=30")
    except Exception as error:
        LOG.warning("update check failed: %s", error)
        raise RuntimeError(f"无法检查更新：{error}") from error
    current = _current_version()
    result = []
    for release in releases:
        tag = str(release.get("tag_name") or "")
        if not tag:
            continue
        prerelease = bool(release.get("prerelease"))
        if release_only and prerelease:
            continue
        assets = release.get("assets") or []
        if not any(str(a.get("name", "")).endswith(ASSET_SUFFIX) for a in assets):
            continue  # only releases carrying a portable package are updatable
        parsed = _parse_version(tag)
        if parsed is None:
            continue
        relation = "current" if tag == current else ("newer" if parsed > (_parse_version(current) or (-1,)) else "older")
        result.append({
            "version": tag,
            "title": tag,
            "kind": "tag",
            "relation": relation,
            "update_note": [str(release.get("body") or "").strip()[:800]],
            "prerelease": prerelease,
        })
    return result


def calculate_update_notes(versions, current_version, target_version, language=None):
    entry = next((v for v in versions if str(v.get("version")) == str(target_version)), None)
    if not entry:
        return []
    notes = entry.get("update_note") or []
    return [n for n in notes if n]


def entry_notes(entry, language=None):
    notes = entry.get("update_note") or []
    return notes if isinstance(notes, list) else [notes]


def _asset_for(release_tag):
    release = _http_json(f"{API_RELEASES}/tags/{release_tag}")
    for asset in release.get("assets") or []:
        if str(asset.get("name", "")).endswith(ASSET_SUFFIX):
            return asset
    raise RuntimeError(f"版本 {release_tag} 没有可更新的安装包")


def _download(url, destination, expected_sha256=None):
    import hashlib
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": "Monster-ww-updater"})
    with urllib.request.urlopen(request, timeout=120) as response, open(destination, "wb") as out:
        digest = hashlib.sha256()
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            out.write(chunk)
    actual = digest.hexdigest()
    if expected_sha256 and actual.lower() != expected_sha256.lower():
        raise RuntimeError(f"安装包校验失败：期望 {expected_sha256[:12]}…，实际 {actual[:12]}…")
    return actual


def update_to_version(version, exit_event=None, language=None):
    """Stage a full-package update and schedule the swap after we exit."""
    import py7zr

    target = str(version).lstrip("v")
    asset = _asset_for(str(version))
    url = asset["browser_download_url"]

    staging_root = Path(tempfile.gettempdir()) / "Monster-ww-update"
    staging_root.mkdir(parents=True, exist_ok=True)
    archive = staging_root / asset["name"]
    expected = asset.get("digest", "").split(":", 1)[-1] if asset.get("digest") else None
    LOG.info("downloading update %s -> %s", version, archive)
    _download(url, archive, expected)

    unpacked = staging_root / f"unpacked-{target}"
    if unpacked.exists():
        import shutil
        shutil.rmtree(unpacked, ignore_errors=True)
    with py7zr.SevenZipFile(archive, mode="r") as zf:
        zf.extractall(path=staging_root)
    extracted_app = staging_root / APP_DIR_NAME
    if not (extracted_app / "Monster-ww.exe").is_file():
        raise RuntimeError("更新包结构不正确（缺少 Monster-ww.exe）")

    app_dir = _app_dir()
    apply_script = staging_root / "apply_update.py"
    current_pid = os.getpid()
    apply_script.write_text(
        _APPLY_SCRIPT.format(
            app_dir=str(app_dir), staged=str(extracted_app),
            staging=str(staging_root), pid=current_pid,
            exe_name="Monster-ww.exe",
        ),
        encoding="utf-8",
    )
    python_exe = Path(sys.executable)
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    subprocess.Popen([str(python_exe), str(apply_script)], creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     cwd=str(staging_root))
    LOG.info("update staged; apply script launched, waiting for pid %s", current_pid)
    return {"updated": True, "restart_required": True}


_APPLY_SCRIPT = '''"""Applied after the main app exits: swap directories and relaunch."""
import os, shutil, subprocess, sys, time
app_dir = r"{app_dir}"
staged = r"{staged}"
staging = r"{staging}"
old_pid = {pid}
exe_name = "{exe_name}"

deadline = time.time() + 60
while time.time() < deadline:
    try:
        os.kill(old_pid, 0)
    except (ProcessLookupError, PermissionError):
        break
    time.sleep(0.3)

backup = app_dir + ".old"
try:
    if os.path.isdir(backup):
        shutil.rmtree(backup, ignore_errors=True)
    if os.path.isdir(app_dir):
        os.rename(app_dir, backup)
    os.rename(staged, app_dir)
except Exception:
    if os.path.isdir(backup) and not os.path.isdir(app_dir):
        os.rename(backup, app_dir)
    raise

exe = os.path.join(app_dir, exe_name)
subprocess.Popen([exe], cwd=app_dir, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
try:
    shutil.rmtree(staging, ignore_errors=True)
except Exception:
    pass
'''


def install():
    """Patch the imported pyappify module with our GitHub-backed API."""
    try:
        import pyappify
    except ImportError:
        LOG.warning("pyappify not importable; in-app updater disabled")
        return False
    version = _current_version()
    pyappify.app_version = version
    pyappify.get_version_list = get_version_list
    pyappify.update_to_version = update_to_version
    pyappify.is_greater_version = is_greater_version
    pyappify.calculate_update_notes = calculate_update_notes
    pyappify.entry_notes = entry_notes
    pyappify.is_local_checkout = lambda: False
    if getattr(pyappify, "_monster_ww_updater_installed", False) is True:
        return False
    pyappify._monster_ww_updater_installed = True
    LOG.info("in-app updater installed (version %s)", version)
    return True
