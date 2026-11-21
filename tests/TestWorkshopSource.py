"""Tests for the optional online workshop refresh logic (no network/GUI needed)."""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

# Stub requests so the pure-logic module imports without the runtime dep.
# The CI build has the real package; these tests only exercise our logic.
if "requests" not in sys.modules:
    _requests = types.ModuleType("requests")
    _requests.RequestException = type("RequestException", (Exception,), {})
    sys.modules["requests"] = _requests

from src.gui.WorkshopSource import (
    archive_urls,
    download_archive,
    extract_difficulty,
    extract_scene,
    load_index_cache,
    merge_indexes,
    parse_index,
    save_index_cache,
)


def _sample_index():
    return {
        "repository": "wk321425/coco-ww-char-code",
        "totalTeams": 1,
        "totalCodes": 2,
        "teams": [{
            "team": "Baizhi, Verina, Phrolova",
            "members": ["Baizhi", "Verina", "Phrolova"],
            "codes": [
                {"filename": "a.zip", "name": "A", "team": "Baizhi, Verina, Phrolova",
                 "description": "场景：矩阵/深塔\n复现难度：中等", "timestamp": 2,
                 "rawUrl": "https://example.com/a.zip"},
                {"filename": "b.zip", "name": "B", "timestamp": 1,
                 "description": "场景未测试\n难度：随便", "downloadUrl": "https://example.com/b.zip"},
            ],
        }],
    }


class TestWorkshopSource(unittest.TestCase):
    def test_parse_index_flattens_and_sorts(self):
        codes = parse_index(_sample_index())
        self.assertEqual([c["filename"] for c in codes], ["a.zip", "b.zip"])
        # team/members inherited from the team entry when missing on the code
        self.assertEqual(codes[1]["team"], "Baizhi, Verina, Phrolova")
        self.assertEqual(codes[1]["members"], ["Baizhi", "Verina", "Phrolova"])

    def test_parse_index_rejects_invalid_payload(self):
        for payload in ({}, {"teams": "x"}, [], None):
            with self.assertRaises(ValueError):
                parse_index(payload)

    def test_scene_and_difficulty_extraction(self):
        codes = parse_index(_sample_index())
        self.assertEqual(extract_scene(codes[0]), "矩阵/深塔")
        self.assertEqual(extract_difficulty(codes[0]), "中等")
        self.assertEqual(extract_scene(codes[1]), "")  # "未测试" -> blank
        self.assertEqual(extract_difficulty(codes[1]), "")  # not a standard tier

    def test_archive_urls_include_upstream_and_entry_urls(self):
        codes = parse_index(_sample_index())
        urls = archive_urls(codes[0])
        self.assertTrue(any("coco-ww-char-code" in u for u in urls))
        self.assertIn("https://example.com/a.zip", urls)

    def test_archive_urls_rejects_empty_filename(self):
        self.assertEqual(archive_urls({}), [])

    def test_index_cache_roundtrip(self):
        payload = _sample_index()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "nested" / "teams.json"
            save_index_cache(payload, path)
            loaded = load_index_cache(path)
            self.assertEqual(loaded, payload)

    def test_index_cache_missing_or_invalid(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "teams.json"
            self.assertIsNone(load_index_cache(path))
            path.write_text("not json", encoding="utf-8")
            self.assertIsNone(load_index_cache(path))
            path.write_text("{}", encoding="utf-8")
            self.assertIsNone(load_index_cache(path))

    def test_merge_keeps_union_of_codes(self):
        """刷新/升级后代码只增不减：两边独有都保留。"""
        primary = _sample_index()  # a.zip, b.zip
        supplemental = {
            "teams": [{
                "slug": "Baizhi_Verina_Phrolova", "team": "Baizhi, Verina, Phrolova",
                "codes": [
                    {"filename": "b.zip", "timestamp": 1},
                    {"filename": "c.zip", "timestamp": 3},
                ],
            }]
        }
        merged = merge_indexes(primary, supplemental)
        names = {c["filename"] for c in parse_index(merged)}
        self.assertEqual(names, {"a.zip", "b.zip", "c.zip"})
        self.assertEqual(merged["totalCodes"], 3)

    def test_merge_newer_timestamp_wins(self):
        """同 filename 取较新内容（在线更新 / 升级新版本）。"""
        primary = _sample_index()  # a.zip timestamp=2
        supplemental = {
            "teams": [{
                "slug": "Baizhi_Verina_Phrolova", "team": "Baizhi, Verina, Phrolova",
                "codes": [{"filename": "a.zip", "name": "A-v3", "timestamp": 9}],
            }]
        }
        merged = merge_indexes(primary, supplemental)
        a = next(c for c in parse_index(merged) if c["filename"] == "a.zip")
        self.assertEqual(a["name"], "A-v3")
        self.assertEqual(merged["totalCodes"], 2)  # 去重，不重复计数

    def test_merge_adds_new_team(self):
        """补充方有新队伍时整队加入。"""
        supplemental = {
            "teams": [{
                "slug": "NewTeam", "team": "A, B, C", "members": ["A", "B", "C"],
                "codes": [{"filename": "n.zip", "timestamp": 5}],
            }]
        }
        merged = merge_indexes(_sample_index(), supplemental)
        self.assertEqual(merged["totalTeams"], 2)
        self.assertEqual(merged["totalCodes"], 3)
        self.assertIn("n.zip", {c["filename"] for c in parse_index(merged)})

    def test_merge_upgrade_scenario(self):
        """升级：旧缓存 + 新离线包，新代码增量进来且不缩水。"""
        cached = {  # 旧版本缓存：只有 a.zip
            "teams": [{
                "slug": "Baizhi_Verina_Phrolova", "team": "Baizhi, Verina, Phrolova",
                "codes": [{"filename": "a.zip", "timestamp": 2}],
            }]
        }
        new_bundle = _sample_index()  # 新离线包：a.zip + b.zip (+ c.zip 更新版)
        new_bundle["teams"][0]["codes"].append({"filename": "c.zip", "timestamp": 10})
        merged = merge_indexes(cached, new_bundle)
        names = {c["filename"] for c in parse_index(merged)}
        self.assertEqual(names, {"a.zip", "b.zip", "c.zip"})

    def test_merge_invalid_payloads(self):
        good = _sample_index()
        self.assertEqual(merge_indexes({}, good), good)
        self.assertEqual(merge_indexes(good, {}), good)

    def test_download_archive_validates_zip_magic_and_caches(self):
        class _Resp:
            ok = True
            headers = {}
            def iter_content(self, chunk_size):
                yield b"PK" + b"\x03\x04" + b"payload"
            def close(self):
                pass

        class _Session:
            def get(self, url, **kwargs):
                return _Resp()

        codes = parse_index(_sample_index())
        with tempfile.TemporaryDirectory() as folder:
            result = download_archive(codes[0], folder, session=_Session())
            self.assertIsNotNone(result)
            self.assertEqual(result.name, "a.zip")
            self.assertTrue(result.is_file())

    def test_download_archive_rejects_non_zip(self):
        class _Resp:
            ok = True
            headers = {}
            def iter_content(self, chunk_size):
                yield b"not a zip"
            def close(self):
                pass

        class _Session:
            def get(self, url, **kwargs):
                return _Resp()

        codes = parse_index(_sample_index())
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(download_archive(codes[0], folder, session=_Session()))


if __name__ == "__main__":
    unittest.main()
