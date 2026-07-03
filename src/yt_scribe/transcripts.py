"""Transcript rendering, cache, and chunk planning for yt-scribe."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from youtube_transcript_api.proxies import GenericProxyConfig

from . import CliError
from .file_io import write_text
from .youtube import extract_video_id, fetch_transcript

DEEP_CHUNK_TARGET_SECONDS = 10 * 60
DEEP_CHUNK_OVERLAP_SECONDS = 45
DEEP_CHUNK_MAX_CHARS = 15_000


def srt_timestamp(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def timestamp_anchor(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, rest = divmod(total_seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours:02}:{minutes:02}:{secs:02}"
    return f"{minutes:02}:{secs:02}"


def render_timestamped_transcript(transcript: dict[str, Any]) -> str:
    lines: list[str] = []
    for segment in transcript.get("segments", []):
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        start = float(segment.get("start") or 0)
        lines.append(f"[{timestamp_anchor(start)}] {text}")
    return "\n".join(lines)


def split_transcript_chunks(
    transcript: dict[str, Any],
    chunk_chars: int,
    timestamps: bool,
) -> list[str]:
    if chunk_chars <= 0:
        return [render_timestamped_transcript(transcript) if timestamps else transcript["text"]]

    chunks: list[str] = []
    current: list[str] = []
    current_chars = 0
    for segment in transcript.get("segments", []):
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        if timestamps:
            start = float(segment.get("start") or 0)
            text = f"[{timestamp_anchor(start)}] {text}"
        projected = current_chars + len(text) + (1 if current else 0)
        if current and projected > chunk_chars:
            chunks.append("\n".join(current))
            current = [text]
            current_chars = len(text)
        else:
            current.append(text)
            current_chars = projected
    if current:
        chunks.append("\n".join(current))
    if not chunks:
        chunks.append("")
    return chunks


def render_transcript(transcript: dict[str, Any], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(transcript, ensure_ascii=False, indent=2)
    if output_format == "srt":
        blocks = []
        for index, segment in enumerate(transcript["segments"], start=1):
            start = float(segment["start"])
            end = start + float(segment.get("duration") or 0)
            blocks.append(
                f"{index}\n{srt_timestamp(start)} --> {srt_timestamp(end)}\n{segment['text']}"
            )
        return "\n\n".join(blocks) + "\n"
    return transcript["text"] + "\n"


def transcript_cache_path(cache_dir: Path, video_id: str, language: str) -> Path:
    safe_language = re.sub(r"[^A-Za-z0-9_.-]+", "_", language)
    return cache_dir / f"{video_id}-{safe_language}.json"


def write_transcript_cache(cache_dir: Path | str, transcript: dict[str, Any]) -> Path:
    cache_root = Path(cache_dir).expanduser().resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    path = transcript_cache_path(cache_root, transcript["video_id"], transcript["language"])
    path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_transcript_cache(
    cache_dir: Path | str,
    video_id: str,
    languages: list[str],
) -> tuple[dict[str, Any], Path] | None:
    cache_root = Path(cache_dir).expanduser().resolve()
    for language in languages:
        path = transcript_cache_path(cache_root, video_id, language)
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8")), path
    return None


def load_or_fetch_transcript(
    url_or_id: str,
    languages: list[str],
    cache_dir: Path | None,
    resume: bool,
    proxy_config: GenericProxyConfig | None = None,
) -> tuple[dict[str, Any], dict[str, str | None]]:
    video_id = extract_video_id(url_or_id)
    if cache_dir and resume:
        cached = read_transcript_cache(cache_dir, video_id, languages)
        if cached:
            transcript, path = cached
            return transcript, {"status": "hit", "path": str(path)}

    transcript = fetch_transcript(url_or_id, languages, proxy_config=proxy_config)
    if cache_dir:
        path = write_transcript_cache(cache_dir, transcript)
        status = "written" if not resume else "miss_written"
        return transcript, {"status": status, "path": str(path)}
    return transcript, {"status": "disabled", "path": None}


def fetch_transcript_payload(
    url_or_id: str,
    languages: list[str],
    output_format: str,
    cache_dir: Path | None = None,
    resume: bool = False,
    proxy_config: GenericProxyConfig | None = None,
    timestamps: bool = False,
    out_path: str | None = None,
    include_transcript: bool = False,
) -> tuple[dict[str, Any], str]:
    if output_format not in {"text", "json", "srt"}:
        raise CliError(
            f"Unsupported transcript format: {output_format}",
            "invalid_transcript_format",
            {"format": output_format},
        )

    transcript, cache = load_or_fetch_transcript(
        url_or_id,
        languages,
        cache_dir,
        resume,
        proxy_config,
    )
    rendered = (
        render_timestamped_transcript(transcript) + "\n"
        if timestamps and output_format == "text"
        else render_transcript(transcript, output_format)
    )
    output_path = write_text(out_path, rendered)
    fetch_payload = {
        "video_id": transcript["video_id"],
        "url": transcript["url"],
        "language": transcript["language"],
        "requested_languages": transcript["requested_languages"],
        "track": transcript["track"],
        "segments": len(transcript["segments"]),
        "chars": len(transcript["text"]),
        "source": transcript.get("source"),
        "format": output_format,
        "timestamped": timestamps and output_format == "text",
        "output_path": output_path,
        "cache": cache,
    }
    if include_transcript:
        fetch_payload["transcript"] = rendered
    return fetch_payload, rendered


def segment_start(segment: dict[str, Any]) -> float:
    return float(segment.get("start") or 0)


def segment_end(segment: dict[str, Any]) -> float:
    return segment_start(segment) + float(segment.get("duration") or 0)


def transcript_segment_line(segment: dict[str, Any]) -> str:
    return f"[{timestamp_anchor(segment_start(segment))}] {str(segment.get('text') or '').strip()}"


def plan_deep_chunks(
    transcript: dict[str, Any],
    *,
    target_seconds: int = DEEP_CHUNK_TARGET_SECONDS,
    max_chars: int = DEEP_CHUNK_MAX_CHARS,
    overlap_seconds: int = DEEP_CHUNK_OVERLAP_SECONDS,
) -> list[dict[str, Any]]:
    indexed_segments = [
        (index, segment)
        for index, segment in enumerate(transcript.get("segments") or [])
        if str(segment.get("text") or "").strip()
    ]
    if not indexed_segments:
        return []

    chunks: list[dict[str, Any]] = []
    start = 0
    while start < len(indexed_segments):
        end = start
        lines: list[str] = []
        char_count = 0
        chunk_start = segment_start(indexed_segments[start][1])
        while end < len(indexed_segments):
            _, segment = indexed_segments[end]
            line = transcript_segment_line(segment)
            projected_chars = char_count + len(line) + (1 if lines else 0)
            projected_span = segment_end(segment) - chunk_start
            if end > start and (
                projected_span > target_seconds or projected_chars > max_chars
            ):
                break
            lines.append(line)
            char_count = projected_chars
            end += 1

        if end <= start:
            _, segment = indexed_segments[start]
            lines = [transcript_segment_line(segment)]
            end = start + 1

        chunk_segments = indexed_segments[start:end]
        first_source_index = chunk_segments[0][0]
        last_source_index = chunk_segments[-1][0]
        start_seconds = segment_start(chunk_segments[0][1])
        end_seconds = segment_end(chunk_segments[-1][1])
        chunk_id = f"chunk-{len(chunks) + 1:03}"
        chunks.append(
            {
                "id": chunk_id,
                "index": len(chunks) + 1,
                "start_seconds": start_seconds,
                "end_seconds": end_seconds,
                "start": timestamp_anchor(start_seconds),
                "end": timestamp_anchor(end_seconds),
                "source_segments": {
                    "start": first_source_index,
                    "end": last_source_index + 1,
                },
                "chars": len("\n".join(lines)),
                "text": "\n".join(lines),
            }
        )

        if end >= len(indexed_segments):
            break

        overlap_threshold = max(chunk_start, end_seconds - overlap_seconds)
        next_start = end
        for candidate in range(start + 1, end):
            if segment_end(indexed_segments[candidate][1]) > overlap_threshold:
                next_start = candidate
                break
        start = next_start

    return chunks
