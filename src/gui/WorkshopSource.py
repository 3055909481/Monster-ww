"""Optional online refresh for the team workshop.

The build ships an offline snapshot in ``workshop_bundle/``. This module can
refresh the index from the upstream char-code repository on demand and cache
downloaded archives locally, so users get newer shared codes without a full
app rebuild. Every network operation is best-effort: on any failure the caller
keeps using the bundled snapshot.
"""

import json
import re
from pathlib import Path
from urllib.parse import quote

import requests

INDEX_URL = (
    "https://raw.githubusercontent.com/wk321425/coco-ww-char-code/main/teams.json"
)
ARCHIVE_BASE_URL = "https://raw.githubusercontent.com/wk321425/coco-ww-char-code/main"
MIRROR_INDEX_URL = (
    "https://cnb.cool/wk321425/coco-ww-char-code/-/git/raw/main/teams.json"
)
MIRROR_ARCHIVE_BASE_URL = "https://cnb.cool/wk321425/coco-ww-char-code/-/git/raw/main"

INDEX_TIMEOUT = 12
ARCHIVE_TIMEOUT = 30
MAX_INDEX_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
USER_AGENT = "Monster-ww-Workshop/1.0"

_SCENE_RE = re.compile(r"场景[:：]?\s*([^\n。;；]+)")
_DIFFICULTY_RE = re.compile(r"(?:复现)?难度[:：]?\s*([^\n。;；,，]+)")
_DIFFICULTY_WORDS = ("极低", "低", "中等", "中", "较高", "高", "极高")
_SCENE_HINTS = ("矩阵", "深塔", "深境", "聚落", "日常", "4c", "4c", "boss", "声骸", "无音", "讨伐", "副本")


def _valid_index(payload):
    return isinstance(payload, dict) and isinstance(payload.get("teams"), list)


def _get_limited(url, timeout, max_bytes):
    response = requests.get(url, timeout=timeout, stream=True,
                            headers={"User-Agent": USER_AGENT})
    try:
        response.raise_for_status()
        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                if int(declared) > max_bytes:
                    return None
            except (TypeError, ValueError):
                pass
        content = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            content.extend(chunk)
            if len(content) > max_bytes:
                return None
        return bytes(content)
    finally:
        response.close()


def parse_index(payload):
    """Flatten an upstream teams.json into the flat code entries the GUI uses."""
    if not _valid_index(payload):
        raise ValueError("invalid workshop index")
    codes = []
    for team_entry in payload["teams"]:
        if not isinstance(team_entry, dict):
            continue
        team_str = team_entry.get("team", "")
        members = team_entry.get("members", [])
        for code in team_entry.get("codes", []):
            if not isinstance(code, dict):
                continue
            item = dict(code)
            if not item.get("team"):
                item["team"] = team_str
            if not item.get("members"):
                item["members"] = members
            codes.append(item)
    return sorted(codes, key=lambda code: int(code.get("timestamp") or 0), reverse=True)


def merge_indexes(primary, supplemental):
    """合并两份 teams.json 索引，只增不减。

    ``primary`` 决定队伍顺序与整体元信息（含顶层 URL），``supplemental`` 只
    补充缺失的队伍/代码。队伍按 ``slug``（缺失时按 ``team``）匹配，代码按
    ``filename`` 去重；同 filename 取 ``timestamp`` 较新者，这样在线刷新拿
    到的更新、以及升级后离线包里的新版本，都能覆盖旧内容。

    用于两个场景：
    - 在线刷新：在线索引为主，补充内置快照里上游没有的代码；
    - 应用升级：本地缓存为主，补充新版本离线包里新增的代码。
    """
    if not _valid_index(primary):
        return supplemental
    if not _valid_index(supplemental):
        return primary

    result = {
        key: value for key, value in primary.items()
        if key not in ("teams", "totalTeams", "totalCodes")
    }
    result["teams"] = [dict(team) for team in primary["teams"] if isinstance(team, dict)]

    # Index by both slug and team name: the two payloads may use different
    # keys (e.g. one has slug, the other only team), and we must still match
    # the same team instead of adding it twice.
    team_by_key = {}
    for team in result["teams"]:
        for key in (team.get("slug"), team.get("team")):
            if key:
                team_by_key.setdefault(key, team)

    def code_time(code):
        try:
            return int(code.get("timestamp") or 0)
        except (TypeError, ValueError):
            return 0

    added_teams = 0
    added_codes = 0
    for team in supplemental.get("teams", []):
        if not isinstance(team, dict):
            continue
        target = None
        for key in (team.get("slug"), team.get("team")):
            if key and key in team_by_key:
                target = team_by_key[key]
                break
        if target is None:
            target = dict(team)
            target["codes"] = [dict(c) for c in team.get("codes", []) if isinstance(c, dict)]
            result["teams"].append(target)
            for key in (target.get("slug"), target.get("team")):
                if key:
                    team_by_key.setdefault(key, target)
            added_teams += 1
            added_codes += len(target["codes"])
            continue

        codes = target.setdefault("codes", [])
        existing = {}
        for index, code in enumerate(codes):
            if isinstance(code, dict) and code.get("filename"):
                existing[code["filename"]] = index
        for code in team.get("codes", []):
            if not isinstance(code, dict):
                continue
            filename = code.get("filename")
            if not filename:
                codes.append(dict(code))
                added_codes += 1
                continue
            index = existing.get(filename)
            if index is None:
                existing[filename] = len(codes)
                codes.append(dict(code))
                added_codes += 1
            elif code_time(code) > code_time(codes[index]):
                codes[index] = dict(code)

    result["totalTeams"] = len(result["teams"])
    result["totalCodes"] = sum(len(team.get("codes", [])) for team in result["teams"])
    for team in result["teams"]:
        team["totalCodes"] = len(team.get("codes", []))
    result["_mergeStats"] = {"addedTeams": added_teams, "addedCodes": added_codes}
    return result


def fetch_latest_codes(index_url=INDEX_URL, mirror_url=MIRROR_INDEX_URL):
    """Fetch and parse the newest index. Falls back to the mirror. Raises on total failure."""
    errors = []
    for url in (index_url, mirror_url):
        if not url:
            continue
        try:
            raw = _get_limited(url, INDEX_TIMEOUT, MAX_INDEX_BYTES)
            if raw is None:
                raise ValueError("index too large")
            payload = json.loads(raw.decode("utf-8"))
            codes = parse_index(payload)
            if not codes:
                raise ValueError("empty workshop index")
            return codes, payload, url
        except (requests.RequestException, ValueError, UnicodeDecodeError) as error:
            errors.append(f"{url}: {error}")
    raise RuntimeError("; ".join(errors) or "workshop index unavailable")


def save_index_cache(payload, cache_path):
    """Persist the fetched index so the workshop can use it offline afterwards."""
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def load_index_cache(cache_path):
    """Load a previously cached index; returns None when absent or invalid."""
    path = Path(cache_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if _valid_index(payload) else None


def archive_urls(code):
    """Candidate download URLs for a code entry, primary source first."""
    filename = Path(str(code.get("filename") or "")).name
    if not filename:
        return []
    quoted = quote(filename)
    urls = []
    for base in (ARCHIVE_BASE_URL, MIRROR_ARCHIVE_BASE_URL):
        urls.append(f"{base}/codes/{quoted}")
    for key in ("rawUrl", "downloadUrl"):
        value = str(code.get(key) or "").strip()
        if value.startswith("https://") and value not in urls:
            urls.append(value)
    return urls


def download_archive(code, cache_dir, session=None):
    """Download a code ZIP into ``cache_dir``. Returns the cached path or None."""
    filename = Path(str(code.get("filename") or "")).name
    if not filename or "/" in filename or "\\" in filename:
        return None
    client = session or requests
    folder = Path(cache_dir)
    target = folder / filename
    if target.is_file() and target.stat().st_size > 0:
        return target
    folder.mkdir(parents=True, exist_ok=True)
    for url in archive_urls(code):
        try:
            response = client.get(url, timeout=ARCHIVE_TIMEOUT, stream=True,
                                  headers={"User-Agent": USER_AGENT})
        except requests.RequestException:
            continue
        try:
            if not response.ok:
                continue
            content = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                content.extend(chunk)
                if len(content) > MAX_ARCHIVE_BYTES:
                    break
            if not content or len(content) > MAX_ARCHIVE_BYTES:
                continue
            if content[:2] != b"PK":
                continue
            temporary = folder / (filename + ".part")
            temporary.write_bytes(bytes(content))
            temporary.replace(target)
            return target
        except (requests.RequestException, OSError):
            continue
        finally:
            response.close()
    return None


def extract_scene(code):
    """Best-effort scene hint from the free-form description.

    Descriptions are free text; only return a value that actually mentions a
    known scene keyword, otherwise leave the cell blank rather than show noise.
    """
    match = _SCENE_RE.search(str(code.get("description") or ""))
    if not match:
        return ""
    value = match.group(1).strip()
    lowered = value.casefold()
    if "未测试" in value or not any(hint in lowered for hint in _SCENE_HINTS):
        return ""
    return value[:40]


def extract_difficulty(code):
    """Best-effort difficulty tier from the free-form description."""
    match = _DIFFICULTY_RE.search(str(code.get("description") or ""))
    if not match:
        return ""
    value = match.group(1).strip()
    for word in _DIFFICULTY_WORDS:
        if value.startswith(word):
            return word
    return ""
