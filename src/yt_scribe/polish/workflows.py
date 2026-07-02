"""Polishing workflow payload helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from youtube_transcript_api.proxies import GenericProxyConfig

from .. import PolishInstruction, ProgressReporter
from ..runs import write_text
from ..transcripts import (
    load_or_fetch_transcript,
    render_timestamped_transcript,
    render_transcript,
)
from .chunked import chunking_disabled_payload
from .harnesses import harness_label, run_agent_polish
from .instructions import (
    limit_text,
    polish_options,
    resolve_instruction,
    selected_agent_harness,
)


def front_matter_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    text = str(value)
    if not text:
        return '""'
    if re.search(r"[:#\n\r\[\]{}]", text):
        return json.dumps(text, ensure_ascii=False)
    return text


def render_front_matter(data: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            if value:
                lines.extend(f"  - {front_matter_scalar(item)}" for item in value)
            else:
                lines.append("  []")
        else:
            lines.append(f"{key}: {front_matter_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def run_front_matter_data(
    transcript: dict[str, Any],
    style: str,
    instruction: PolishInstruction,
    harness: str,
) -> dict[str, Any]:
    track = transcript.get("track") or {}
    return {
        "video_id": transcript.get("video_id"),
        "url": transcript.get("url"),
        "language": transcript.get("language"),
        "caption_name": track.get("name"),
        "caption_auto_generated": track.get("auto_generated"),
        "segments": len(transcript.get("segments") or []),
        "transcript_chars": len(transcript.get("text") or ""),
        "style": style,
        "instruction_mode": instruction.mode,
        "instruction_sources": instruction.sources,
        "agent_harness": harness,
    }


def run_front_matter(
    transcript: dict[str, Any],
    style: str,
    instruction: PolishInstruction,
    harness: str,
) -> str:
    return render_front_matter(run_front_matter_data(transcript, style, instruction, harness))


def apply_output_prefix(result: dict[str, Any], prefix: str) -> dict[str, Any]:
    if not prefix:
        return result

    if result["output_path"]:
        path = Path(result["output_path"])
        final_text = prefix + path.read_text(encoding="utf-8")
        path.write_text(final_text, encoding="utf-8")
    else:
        final_text = prefix + result["text"]
        result = {**result, "text": final_text}

    return {**result, "chars": len(final_text)}


def polish_transcript_text_payload(
    transcript_text: str,
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
    cwd: str | None = None,
    max_chars: int = 0,
    out_path: str | None = None,
    input_path: str | None = None,
    progress: ProgressReporter | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    options = polish_options(
        style=style,
        template=template,
        focus=focus,
        focus_file=focus_file,
        instruction=instruction,
        prompt_file=prompt_file,
        timestamps=timestamps,
        agent_harness=agent_harness,
        model=model,
        cd=cwd,
        max_chars=max_chars,
    )
    transcript_text = limit_text(transcript_text, max_chars)
    harness = selected_agent_harness(options)
    resolved_instruction = resolve_instruction(options, harness)
    if progress:
        progress.message(f"Using {harness_label(harness)}")
    result = run_agent_polish(
        transcript_text=transcript_text,
        instruction=resolved_instruction.text,
        out_path=out_path,
        model=model,
        cwd=cwd,
        harness=harness,
        progress=progress,
    )
    payload = {
        "input_path": input_path,
        "style": options.style,
        "instruction_mode": resolved_instruction.mode,
        "instruction_sources": resolved_instruction.sources,
        "timestamp_grounding": timestamps,
        "agent_harness": result["harness"],
        "output_path": result["output_path"],
        "chars": result["chars"],
    }
    return payload, result


def run_youtube_polish_payload(
    url_or_id: str,
    *,
    languages: list[str],
    transcript_format: str = "text",
    cache_dir: Path | None = None,
    resume: bool = False,
    proxy_config: GenericProxyConfig | None = None,
    transcript_out_path: str | None = None,
    style: str | None = None,
    template: str | None = None,
    focus: list[str] | None = None,
    focus_file: list[str] | None = None,
    instruction: str | None = None,
    prompt_file: str | None = None,
    timestamps: bool = False,
    agent_harness: str | None = None,
    model: str | None = None,
    cwd: str | None = None,
    max_chars: int = 0,
    out_path: str | None = None,
    progress: ProgressReporter | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    transcript, cache = load_or_fetch_transcript(
        url_or_id,
        languages,
        cache_dir,
        resume,
        proxy_config,
    )
    rendered = render_transcript(transcript, transcript_format)
    transcript_path = write_text(transcript_out_path, rendered)
    transcript_text = (
        render_timestamped_transcript(transcript) if timestamps else transcript["text"]
    )
    polish_payload, result = polish_transcript_text_payload(
        transcript_text,
        style=style,
        template=template,
        focus=focus,
        focus_file=focus_file,
        instruction=instruction,
        prompt_file=prompt_file,
        timestamps=timestamps,
        agent_harness=agent_harness,
        model=model,
        cwd=cwd,
        max_chars=max_chars,
        out_path=out_path,
        progress=progress,
    )
    payload = {
        "video_id": transcript["video_id"],
        "url": transcript["url"],
        "language": transcript["language"],
        "requested_languages": transcript.get("requested_languages", languages),
        "segments": len(transcript["segments"]),
        "style": polish_payload["style"],
        "instruction_mode": polish_payload["instruction_mode"],
        "instruction_sources": polish_payload["instruction_sources"],
        "timestamp_grounding": timestamps,
        "agent_harness": result["harness"],
        "transcript_path": transcript_path,
        "output_path": result["output_path"],
        "chars": result["chars"],
        "front_matter": False,
        "chunking": chunking_disabled_payload(),
        "cache": cache,
        "bundle": None,
    }
    return payload, result
