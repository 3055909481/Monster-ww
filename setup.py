"""Compile the runtime sources into native extensions for source obfuscation.

The portable build ships compiled modules only: ``src/**/*.py`` and
``config.py`` become ``.pyd`` files so the distributable carries no readable
application logic. Two modules deliberately stay as source:

* ``__init__.py`` — package discovery needs it importable before the compiled
  modules exist, and it holds no logic worth hiding.
* ``identity.py`` — its runtime monkeypatches assign nested ``def`` functions
  onto framework classes. Cython turns those into non-descriptor builtins that
  cannot bind ``self`` once assigned, so it must remain pure Python.
* ``schedule_patch.py`` — same reason: it monkeypatches framework dialog/manager
  methods with plain functions that must bind ``self`` as real descriptors.

The build workflow strips every ``.py`` that has a matching ``.pyd`` from the
slim release tree. See build_char_sources.py for the character-source snapshot
that keeps "edit built-in character code" working in the compiled build.
"""

import os
import pathlib

import setuptools
from Cython.Build import cythonize
from setuptools import Extension

os.environ["PYTHONIOENCODING"] = "utf-8"

ROOT = pathlib.Path(__file__).resolve().parent
EXCLUDE_FILES = {"__init__.py", "identity.py", "schedule_patch.py", "adaptive_interval.py", "team_overlay.py"}


def collect_python_extensions(base_dir):
    """One Extension per .py module under base_dir, plus root config.py."""
    extensions = []
    for path in sorted(pathlib.Path(base_dir).rglob("*.py")):
        if path.name in EXCLUDE_FILES:
            continue
        module = ".".join(path.with_suffix("").parts)
        extensions.append(Extension(name=module, language="c++", sources=[str(path)]))
    config_py = ROOT / "config.py"
    if config_py.is_file():
        extensions.append(Extension(name="config", language="c++", sources=["config.py"]))
    print(f"collected {len(extensions)} modules to compile")
    return extensions


def main():
    extensions = collect_python_extensions("src")

    # Cython's nthreads uses multiprocessing. On Windows the spawned workers
    # re-import this file, so setup() must be guarded by __main__ or the
    # workers recursively start new build pools (RuntimeError: bootstrapping).
    setuptools.setup(
        name="Monster-ww",
        version="0.0.1",
        author="Monster-ww maintainers",
        author_email="",
        description="Monster-ww automation runtime",
        url="https://github.com/3055909481/Monster-ww",
        packages=setuptools.find_packages(),
        include_package_data=True,
        classifiers=[
            "Programming Language :: Python :: 3",
            "Intended Audience :: Developers",
            "License :: OSI Approved :: MIT License",
            "Operating System :: Microsoft :: Windows",
        ],
        ext_modules=cythonize(
            extensions,
            compiler_directives={
                "language_level": "3",
                "embedsignature": False,
                "binding": False,
            },
            # Parallelize the .py -> .cpp translation stage. The C++ compilation
            # stage is parallelized separately via build_ext --parallel in CI.
            nthreads=int(os.environ.get("CYTHON_NTHREADS", "4")),
        ),
    )


if __name__ == "__main__":
    main()
