"""Polishing instruction resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import CliError, PolishInstruction, PolishOptions
from ..config import effective_agent_harness

STYLE_INSTRUCTIONS = {
    "clean": (
        "Clean this YouTube transcript. Remove filler, repeated phrases, "
        "timestamps, caption artifacts, and obvious speech disfluencies. "
        "Preserve the speaker's meaning and order. Do not add facts that are "
        "not in the transcript. Return only the cleaned text."
    ),
    "notes": (
        "Turn this YouTube transcript into clear markdown notes. Preserve the "
        "meaning and order. Use concise headings and bullets where helpful. "
        "Remove filler and caption artifacts. Do not add facts that are not in "
        "the transcript."
    ),
    "summary": (
        "Summarize this YouTube transcript in plain markdown. Include the main "
        "ideas, key details, and any concrete action items. Do not add facts "
        "that are not in the transcript."
    ),
    "article": (
        "Rewrite this YouTube transcript as a readable article in markdown. "
        "Preserve the argument and sequence, remove filler, and avoid adding "
        "facts that are not in the transcript."
    ),
}
TIMESTAMP_GROUNDING_INSTRUCTION = (
    "Timestamp grounding is requested. When the transcript includes timestamp anchors "
    "such as [01:23], preserve useful anchors in the polished output so important "
    "claims can be traced back to the transcript. Do not invent timestamps, and do "
    "not add timestamp anchors for claims that are not supported by nearby transcript text."
)
TEMPLATE_INSTRUCTIONS = {
    "lecture": (
        "Use a lecture notes structure with concise section headings, key concepts, "
        "definitions, examples, and open questions when they are present in the transcript."
    ),
    "research": (
        "Use a research notes structure with claims, evidence, methods, limitations, "
        "and follow-up questions when they are present in the transcript."
    ),
    "meeting": (
        "Use a meeting-style structure with decisions, risks, action items, owners, "
        "and deadlines only when they are present in the transcript."
    ),
}
INNER_POLISHER_SKILL = "yt-scribe-transcript-polisher"
HARNESS_INSTRUCTIONS = {
    "codex": "harness/codex.md",
    "opencode": "harness/opencode.md",
}
TRANSCRIPT_DELIVERY = {
    "codex": "stdin",
    "opencode": "an attached transcript file",
}

__all__ = [
    "HARNESS_INSTRUCTIONS",
    "INNER_POLISHER_SKILL",
    "PolishInstruction",
    "PolishOptions",
    "STYLE_INSTRUCTIONS",
    "TEMPLATE_INSTRUCTIONS",
    "TIMESTAMP_GROUNDING_INSTRUCTION",
    "TRANSCRIPT_DELIVERY",
    "custom_instruction_parts",
    "limit_text",
    "polish_options",
    "read_instruction",
    "resolve_instruction",
    "selected_agent_harness",
    "style_instruction",
]


def style_instruction(style: str, harness: str) -> str:
    harness_file = HARNESS_INSTRUCTIONS[harness]
    delivery = TRANSCRIPT_DELIVERY[harness]
    return (
        f"Use the {INNER_POLISHER_SKILL} skill if it is available. "
        f"For this run, follow its {harness_file} instructions. "
        f"{STYLE_INSTRUCTIONS[style]} "
        f"The transcript is provided by yt-scribe on {delivery}."
    )


def custom_instruction_parts(
    args: Any,
) -> tuple[list[str], list[str]]:
    parts: list[str] = []
    sources: list[str] = []

    template = getattr(args, "template", None)
    if template:
        parts.append(TEMPLATE_INSTRUCTIONS[template])
        sources.append("--template")

    for focus in getattr(args, "focus", None) or []:
        focus = focus.strip()
        if focus:
            parts.append(focus)
            sources.append("--focus")

    for focus_file in getattr(args, "focus_file", None) or []:
        content = read_instruction_file(focus_file, "--focus-file").strip()
        if content:
            parts.append(content)
            sources.append("--focus-file")

    return parts, sources


def read_instruction_file(path: str, source: str) -> str:
    expanded = Path(path).expanduser()
    try:
        return expanded.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(
            f"Could not read {source} {expanded}: {exc}",
            "instruction_file_read_failed",
            {"path": str(expanded), "source": source},
        ) from exc


def resolve_instruction(
    args: Any,
    harness: str,
) -> PolishInstruction:
    timestamp_sources = ["--timestamps"] if getattr(args, "timestamps", False) else []
    replacement_sources = [
        source
        for source, value in (
            ("--instruction", getattr(args, "instruction", None)),
            ("--prompt-file", getattr(args, "prompt_file", None)),
        )
        if value
    ]
    custom_parts, custom_sources = custom_instruction_parts(args)

    if len(replacement_sources) > 1:
        raise CliError(
            "Use only one full prompt replacement option: --instruction or --prompt-file",
            "conflicting_instruction_options",
        )
    if replacement_sources and custom_parts:
        raise CliError(
            "Use --focus/--focus-file to extend the built-in prompt, or "
            "--instruction/--prompt-file to replace it, not both.",
            "conflicting_instruction_options",
        )

    if getattr(args, "prompt_file", None):
        text = read_instruction_file(args.prompt_file, "--prompt-file").strip()
        if timestamp_sources:
            text += f"\n\n{TIMESTAMP_GROUNDING_INSTRUCTION}"
        return PolishInstruction(
            text=text,
            mode="replace",
            sources=["--prompt-file", *timestamp_sources],
        )
    if getattr(args, "instruction", None):
        text = args.instruction.strip()
        if timestamp_sources:
            text += f"\n\n{TIMESTAMP_GROUNDING_INSTRUCTION}"
        return PolishInstruction(
            text=text,
            mode="replace",
            sources=["--instruction", *timestamp_sources],
        )

    text = style_instruction(getattr(args, "style", "notes"), harness)
    if timestamp_sources:
        text += f"\n\n{TIMESTAMP_GROUNDING_INSTRUCTION}"
    if not custom_parts:
        return PolishInstruction(text=text, mode="style", sources=["--style", *timestamp_sources])

    custom_text = "\n\n".join(custom_parts)
    text += (
        "\n\nCustom user instructions for this run:\n"
        "Follow these instructions even when they narrow or change the selected "
        "style. They do not allow adding facts that are not in the transcript.\n\n"
        f"{custom_text}"
    )
    return PolishInstruction(
        text=text,
        mode="custom",
        sources=[*custom_sources, *timestamp_sources],
    )


def read_instruction(args: Any, harness: str) -> str:
    return resolve_instruction(args, harness).text


def limit_text(text: str, max_chars: int) -> str:
    if max_chars and len(text) > max_chars:
        return text[:max_chars]
    return text


def selected_agent_harness(args: Any) -> str:
    return args.agent_harness or effective_agent_harness()


def polish_options(
    *,
    style: str | None = None,
    template: str | None = None,
    focus: list[str] | None = None,
    focus_file: list[str] | None = None,
    instruction: str | None = None,
    prompt_file: str | None = None,
    timestamps: bool = False,
    agent_harness: str | None = None,
    model: str | None = None,
    cd: str | None = None,
    max_chars: int = 0,
) -> PolishOptions:
    return PolishOptions(
        style=style or "notes",
        template=template,
        focus=focus,
        focus_file=focus_file,
        instruction=instruction,
        prompt_file=prompt_file,
        timestamps=timestamps,
        agent_harness=agent_harness,
        model=model,
        cd=cd,
        max_chars=max_chars,
    )


def instruction_payload(args: Any, harness: str) -> dict[str, Any]:
    instruction = resolve_instruction(args, harness)
    return {
        "text": instruction.text,
        "mode": instruction.mode,
        "sources": instruction.sources,
    }
