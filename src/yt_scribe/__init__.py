"""Intentional package interface for yt-scribe."""

# ruff: noqa: I001

from __future__ import annotations

from ._legacy import (
    EMBEDDED_SKILL_ASSETS,
    CliError,
    PolishInstruction,
    PolishOptions,
    ProgressReporter,
    ProgressWait,
)
from . import batch, config, polish, runs, setup, transcripts, verify, youtube
from .config import VERSION
from .youtube import CaptionTrack

__all__ = [
    "CaptionTrack",
    "CliError",
    "EMBEDDED_SKILL_ASSETS",
    "PolishInstruction",
    "PolishOptions",
    "ProgressReporter",
    "ProgressWait",
    "VERSION",
    "batch",
    "config",
    "polish",
    "runs",
    "setup",
    "transcripts",
    "verify",
    "youtube",
]
