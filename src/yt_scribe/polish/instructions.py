"""Polishing instruction resolution."""

from __future__ import annotations

from typing import Any

from .._legacy import (
    PolishInstruction,
    PolishOptions,
    custom_instruction_parts,
    limit_text,
    polish_options,
    read_instruction,
    resolve_instruction,
    selected_agent_harness,
    style_instruction,
)

__all__ = [
    "PolishInstruction",
    "PolishOptions",
    "custom_instruction_parts",
    "limit_text",
    "polish_options",
    "read_instruction",
    "resolve_instruction",
    "selected_agent_harness",
    "style_instruction",
]


def instruction_payload(args: Any, harness: str) -> dict[str, Any]:
    instruction = resolve_instruction(args, harness)
    return {
        "text": instruction.text,
        "mode": instruction.mode,
        "sources": instruction.sources,
    }
