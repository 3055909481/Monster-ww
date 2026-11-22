"""In-app updater for the PyInstaller full-package build.

This module supplies the small subset of the ``pyappify`` launcher API that
the framework's AboutTab/UpdateCard expects, backed by this fork's GitHub
releases instead. Installing it (see ``install()``) makes the existing update
UI work: version picker, check button, automatic startup check and toast,
plus update-then-restart.

Version discovery uses the public ``releases.atom`` feed and download links
under ``/releases/download``. Neither is a GitHub REST API endpoint, so the
60-request/hour anonymous API limit no longer breaks update checks. A token
is never read from the environment or from Git credentials.

Full-package updates must replace files while the app is not running. The
flow is therefore two-phase:

1. ``update_to_version`` (in the running GUI) downloads and extracts the
   release into a staging directory, writes a small .cmd apply script, spawns
   it detached, and returns ``{"updated": True, "restart_required": True}``.
   The framework then emits ``communicate.quit`` and the app exits.
2. The detached script waits for the old PID to exit, swaps the app directory
   (old -> .old, staged -> app dir), and relaunches Monster-ww.exe. User data
   lives in %APPDATA% and is never touched.

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
RELEASES_ATOM = f"https://github.com/{REPO}/releases.atom"
RELEASE_PAGE = f"https://github.com/{REPO}/releases"
APP_DIR_NAME = "Monster-ww"
EXE_NAME = "Monster-ww.exe"
USER_AGENT = "Monster-ww-updater"

# Injected by install(): routes phase messages to the About/Update card's
# status label through a Qt signal (safe from the background worker thread).
_status_reporter = None


def _status(message):
    """Report a user-visible phase; never let UI issues break an update."""
    reporter = _status_reporter
    if reporter is not None:
        try:
            reporter(message)
        except Exception:
            pass
    LOG.info("update status: %s", message)


def _app_dir() -> Path:
    """Directory holding Monster-ww.exe (the packaged app root)."""
    exe = Path(sys.executable)
    # PyInstaller onedir runs the real exe, so argv[0] and executable agree.
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

            exe = str(_app_dir() / EXE_NAME)
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


def _http_text(url, timeout=20, accept=None):
    """Fetch a public GitHub page/feed as text. No credentials, no API quota."""
    import urllib.error
    import urllib.request

    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        if error.code in (403, 429):
            raise RuntimeError(
                f"GitHub 检查次数已达上限，请稍后再试或直接前往 {RELEASE_PAGE} 下载"
            ) from error
        raise


def _strip_html(value):
    import html

    text = html.unescape(value or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(?:p|div|li|h[1-6])>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _releases_from_atom(text, limit=30):
    """Parse GitHub's releases.atom into simple release dictionaries."""
    import xml.etree.ElementTree as ET

    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(text)
    releases = []
    for entry in root.findall("a:entry", ns):
        entry_id = (entry.findtext("a:id", default="", namespaces=ns) or "").strip()
        tag = entry_id.rsplit("/", 1)[-1] if entry_id else ""
        if not tag:
            continue
        releases.append({
            "tag": tag,
            "title": (entry.findtext("a:title", default=tag, namespaces=ns) or tag).strip(),
            "updated": (entry.findtext("a:updated", default="", namespaces=ns) or "").strip(),
            "body": _strip_html(entry.findtext("a:content", default="", namespaces=ns)),
        })
        if len(releases) >= limit:
            break
    return releases


def _is_prerelease(tag):
    parsed = _parse_version(tag)
    return bool(parsed and parsed[3] < 3)


def get_version_list(release_only=True, exit_event=None, number_versions=30, **_kwargs):
    """Return the framework's version-list shape, newest first."""
    _status("正在检查更新…")
    try:
        text = _http_text(RELEASES_ATOM, accept="application/atom+xml")
        releases = _releases_from_atom(text, limit=number_versions)
    except RuntimeError:
        raise  # already a user-facing message (e.g. rate limit hint)
    except Exception as error:
        LOG.warning("update check failed: %s", error)
        raise RuntimeError(f"无法检查更新：{error}") from error

    current = _current_version()
    current_parsed = _parse_version(current) or (-1, -1, -1, -1, -1)
    result = []
    for release in releases:
        tag = release["tag"]
        parsed = _parse_version(tag)
        if parsed is None:
            continue
        prerelease = _is_prerelease(tag)
        if release_only and prerelease:
            continue
        relation = ("newer" if parsed > current_parsed else
                    "current" if parsed == current_parsed else "older")
        result.append({
            "version": tag,
            "title": tag,
            "kind": "tag",
            "relation": relation,
            "update_note": [release["body"][:800]],
            "prerelease": prerelease,
        })
    _status(f"已获取 {len(result)} 个版本" if result else "未找到可用版本")
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


def _asset_candidates(tag):
    """Known asset names for a release tag, newest format first."""
    from urllib.parse import quote

    base = f"https://github.com/{REPO}/releases/download/{quote(tag, safe='')}/"
    plain = str(tag).lstrip("v")
    names = (
        f"Monster-ww-{tag}-windows-x64.7z",
        f"Monster-ww-{tag}-windows-x64.zip",
        f"Monster-ww-win32-Portable-{plain}.7z",
    )
    return [{"name": name, "url": base + quote(name, safe="")} for name in names]


def _head_ok(url):
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return 200 <= response.status < 400
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        # Some CDNs reject HEAD; let the GET decide instead.
        return True
    except Exception:
        return False


def _asset_for(release_tag):
    candidates = _asset_candidates(str(release_tag))
    for asset in candidates:
        if _head_ok(asset["url"]):
            return asset
    if candidates:
        # Network failures must surface as a real download error, not a
        # misleading "no package" message.
        LOG.warning("no asset HEAD succeeded for %s; trying %s", release_tag, candidates[0]["url"])
        return candidates[0]
    raise RuntimeError(f"版本 {release_tag} 没有可更新的安装包")


def _sha256_for(asset):
    try:
        text = _http_text(asset["url"] + ".sha256", timeout=20)
    except Exception as error:
        LOG.warning("no checksum for %s: %s", asset["name"], error)
        return None
    match = re.search(r"\b([0-9a-fA-F]{64})\b", text)
    return match.group(1) if match else None


def _download(url, destination, expected_sha256=None):
    import hashlib
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
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


def _extract(archive, target):
    suffix = archive.suffix.lower()
    if suffix == ".7z":
        try:
            import py7zr
        except ImportError as error:
            raise RuntimeError(
                "缺少 7z 解压组件 py7zr，请重新下载完整安装包后解压覆盖，不要在原目录上修补"
            ) from error
        with py7zr.SevenZipFile(archive, mode="r") as zf:
            zf.extractall(path=target)
    elif suffix == ".zip":
        import zipfile
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(path=target)
    else:
        raise RuntimeError(f"不支持的压缩包格式：{suffix}")


def update_to_version(version, exit_event=None, language=None):
    """Stage a full-package update and schedule the swap after we exit."""
    if os.name != "nt":
        raise RuntimeError("自动更新仅支持 Windows")

    import shutil

    _status(f"正在下载 {version} 更新包…")
    asset = _asset_for(str(version))
    staging_root = Path(tempfile.gettempdir()) / "Monster-ww-update"
    staging_root.mkdir(parents=True, exist_ok=True)
    archive = staging_root / asset["name"]
    LOG.info("downloading update %s -> %s", version, archive)
    _download(asset["url"], archive, _sha256_for(asset))

    _status("下载完成，正在解压更新包…")
    extracted_app = staging_root / APP_DIR_NAME
    if extracted_app.exists():
        shutil.rmtree(extracted_app, ignore_errors=True)
    _extract(archive, staging_root)
    if not (extracted_app / EXE_NAME).is_file():
        raise RuntimeError("更新包结构不正确（缺少 Monster-ww.exe）")

    _status("更新包已就绪，程序即将退出并自动替换文件、重新启动…")

    app_dir = _app_dir()
    # Keep the apply script outside staging: the script deletes staging at the
    # end, and Windows cannot delete a batch file that is still executing.
    apply_script = Path(tempfile.gettempdir()) / "Monster-ww-apply.cmd"
    apply_script.write_text(
        _APPLY_SCRIPT.format(
            app_dir=str(app_dir), staged=str(extracted_app),
            staging=str(staging_root), pid=os.getpid(),
            exe_name=EXE_NAME,
        ),
        encoding="utf-8",
    )
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(["cmd.exe", "/c", str(apply_script)], creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     cwd=str(staging_root))
    LOG.info("update staged; apply script launched, waiting for pid %s", os.getpid())
    return {"updated": True, "restart_required": True}


_APPLY_SCRIPT = '''@echo off
setlocal
set "APP_DIR={app_dir}"
set "STAGED={staged}"
set "STAGING={staging}"
set "OLD_PID={pid}"
set "EXE_NAME={exe_name}"

set /a TRIES=0
:wait
set /a TRIES+=1
if %TRIES% GTR 180 goto swap
tasklist /FI "PID eq %OLD_PID%" 2^>nul | findstr /I /C:"%OLD_PID%" >nul
if errorlevel 1 goto swap
ping -n 2 127.0.0.1 >nul
goto wait

:swap
if exist "%APP_DIR%.old" rmdir /S /Q "%APP_DIR%.old" >nul 2^>nul
if exist "%APP_DIR%" move /Y "%APP_DIR%" "%APP_DIR%.old" >nul
if errorlevel 1 goto rollback
move /Y "%STAGED%" "%APP_DIR%" >nul
if errorlevel 1 goto rollback
start "" /D "%APP_DIR%" "%APP_DIR%\\%EXE_NAME%"
if exist "%STAGING%" rmdir /S /Q "%STAGING%" >nul 2^>nul
goto done

:rollback
if exist "%APP_DIR%.old" if not exist "%APP_DIR%" move /Y "%APP_DIR%.old" "%APP_DIR%" >nul

:done
endlocal
'''


def install():
    """Patch the imported pyappify module with our GitHub-backed API."""
    try:
        import pyappify
    except ImportError:
        LOG.warning("pyappify not importable; in-app updater disabled")
        return False

    # Phase status must be delivered on the Qt GUI thread. identity.py stays
    # as source in the package (Cython cannot make Qt Signal a descriptor),
    # so install the bridge there and route our logs through it.
    global _status_reporter
    try:
        from src import identity
        identity.install_update_status_bridge()
        _status_reporter = identity.report_update_status
    except Exception as error:
        LOG.warning("update status bridge unavailable: %s", error)

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
