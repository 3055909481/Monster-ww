"""Resolve built-in character source, preferring the live checkout over snapshots.

In a source checkout the real ``src/char/*.py`` files exist and are the
authoritative source. In the compiled portable build those modules are ``.pyd``
files and ``inspect.getsourcefile`` cannot recover readable code, so the build
workflow ships snapshots under the top-level ``char_sources/`` directory. This
module lets callers ask for a class's source without caring which layout is
running.
"""

from pathlib import Path

CHAR_SOURCES_DIR = Path(__file__).resolve().parent.parent.parent / "char_sources"
CHAR_SOURCE_DIR = Path(__file__).resolve().parent


def char_source_path(class_name):
    """Prefer the actual source module over a possibly stale bundled snapshot."""
    candidate = CHAR_SOURCE_DIR / f"{class_name}.py"
    if candidate.is_file():
        return candidate
    candidate = CHAR_SOURCES_DIR / f"{class_name}.py"
    return candidate if candidate.is_file() else None


def read_char_source(class_name):
    path = char_source_path(class_name)
    if path is None:
        return None
    return path.read_text(encoding="utf-8")
