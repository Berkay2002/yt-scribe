#!/usr/bin/env python3
"""Compatibility module for the package-backed yt-scribe MCP server."""

from __future__ import annotations

import importlib
import sys

_module = importlib.import_module("yt_scribe.mcp")
sys.modules[__name__] = _module

if __name__ == "__main__":
    raise SystemExit(_module.main())
