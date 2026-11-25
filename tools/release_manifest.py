"""Generate the incremental-update manifest from the built portable tree.

Unlike coco-ww (which hashes git-tracked source), Monster-ww ships Cython
compiled ``.pyd`` modules with the ``.py`` sources stripped. The manifest must
therefore describe the *build output* (``dist/Monster-ww/``), not the source
checkout, so a file the updater downloads is byte-for-byte what a fresh install
gets.

Usage (in CI, after ``Package portable archive``)::

    python tools/release_manifest.py dist/Monster-ww out --version v1.0.44 \
        --commit <GITHUB_SHA>

Outputs ``out/update-manifest.json``. ``format``/``version``/``commit``/
``files`` match what ``pyappify/__init__.py`` validates. Build products are
published to the ``update-index`` branch at ``builds/<version>/<rel>``; the
``commit`` is the source revision for provenance only.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path, PurePosixPath

MANIFEST_FORMAT = 1


def build_manifest(root, version, commit):
    root = Path(root)
    if not root.is_dir():
        raise SystemExit(f"build tree not found: {root}")
    files = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        # Defensive: the updater rejects these prefixes too. There should be
        # no caches/logs in a clean build tree, but never publish one if there
        # is (they hold user data and machine-specific blobs).
        if PurePosixPath(rel).is_absolute() or ".." in PurePosixPath(rel).parts:
            raise SystemExit(f"unsafe build path: {rel}")
        head = rel.split("/", 1)[0]
        if head in ("cache", "logs", ".git"):
            continue
        data = path.read_bytes()
        files[rel] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    if not files:
        raise SystemExit("empty build tree")
    if not (root / "Monster-ww.exe").is_file():
        raise SystemExit("build tree has no Monster-ww.exe; refusing to publish a broken manifest")
    return {
        "format": MANIFEST_FORMAT,
        "version": version,
        "commit": commit,
        "files": files,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("build_dir")
    parser.add_argument("out_dir")
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args(argv)
    manifest = build_manifest(args.build_dir, args.version, args.commit)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "update-manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    print(f"manifest: {len(manifest['files'])} files, {path}")


if __name__ == "__main__":
    main()
