"""In-app update *checker* for the PyInstaller full-package build.

This module supplies the small subset of the ``pyappify`` launcher API that
the framework's AboutTab/UpdateCard expects, backed by this fork's GitHub
releases instead: version picker, check button, automatic startup check and
toast. Version discovery uses the public ``releases.atom`` feed, never the
GitHub REST API, so anonymous rate limits cannot break update checks.

Installing an update is delegated to the standalone MonsterUpdate.exe window
(see ``update_core.py`` / ``updater_app.py``): ``update_to_version`` just
launches it with the target version and our PID, then lets the framework quit.
That updater shows download/verify/extract/install/restart progress on its own
window, which stays alive while this process exits and replaces its files.

User data lives in %APPDATA% and is never touched by the swap.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

LOG = logging.getLogger(__name__)

REPO = "3055909481/Monster-ww"
RELEASES_ATOM = f"https://github.com/{REPO}/releases.atom"
RELEASE_PAGE = f"https://github.com/{REPO}/releases"
EXE_NAME = "Monster-ww.exe"
USER_AGENT = "Monster-ww-updater"


def _app_dir() -> Path:
    """Directory holding Monster-ww.exe (the packaged app root)."""
    exe = Path(sys.executable)
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


def update_to_version(version, exit_event=None, language=None):
    """Hand off to the standalone updater window, then let the app quit.

    All visible progress (download %, verify, extract %, install, restart) is
    rendered by MonsterUpdate.exe, which survives this process exiting. Doing
    the work in-app never worked: the framework emits quit immediately after
    this returns, so no progress UI could be shown during download/extract.
    """
    if os.name != "nt":
        raise RuntimeError("自动更新仅支持 Windows")

    import update_core

    app_dir = _app_dir()
    updater_exe = update_core.updater_path(app_dir)
    LOG.info("launching standalone updater %s for %s", updater_exe, version)
    update_core.launch_updater(updater_exe, version, app_dir, os.getpid(), EXE_NAME)
    return {"updated": True, "restart_required": True}


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
