#!/usr/bin/env python3
"""Compatibility wrapper for the package-backed yt-scribe CLI."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"


def _prepend_src() -> None:
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))


def _load_package() -> ModuleType:
    package_dir = SRC / "yt_scribe"
    spec = importlib.util.spec_from_file_location(
        "yt_scribe",
        package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)],
    )
    if spec is None or spec.loader is None:
        raise ImportError("Could not load src/yt_scribe package")
    module = importlib.util.module_from_spec(spec)
    sys.modules["yt_scribe"] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    _prepend_src()
    raise SystemExit(importlib.import_module("yt_scribe.cli").main())

sys.modules[__name__] = _load_package()
