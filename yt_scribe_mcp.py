#!/usr/bin/env python3
"""Compatibility wrapper for the package-backed yt-scribe MCP server."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

_module = importlib.import_module("yt_scribe.mcp")
sys.modules[__name__] = _module

if __name__ == "__main__":
    raise SystemExit(_module.main())
