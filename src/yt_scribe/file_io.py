"""Shared file-writing helpers for yt-scribe."""

from __future__ import annotations

from pathlib import Path


def write_text(path: str | None, text: str) -> str | None:
    if not path:
        return None
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return str(target)
