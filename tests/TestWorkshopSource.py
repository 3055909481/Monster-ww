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
