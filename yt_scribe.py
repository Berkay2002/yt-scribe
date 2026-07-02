#!/usr/bin/env python3
"""Fetch YouTube transcripts and polish them with codex exec."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from youtube_transcript_api import YouTubeTranscriptApi

VERSION = "0.1.0"
COMMAND_NAME = "yt-scribe"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)

STYLE_INSTRUCTIONS = {
    "clean": (
        "Use the yt-scribe-transcript-polisher skill if it is available. "
        "Clean this YouTube transcript. Remove filler, repeated phrases, "
        "timestamps, caption artifacts, and obvious speech disfluencies. "
        "Preserve the speaker's meaning and order. Do not add facts that are "
        "not in the transcript. Return only the cleaned text. The transcript "
        "is provided on stdin."
    ),
    "notes": (
        "Use the yt-scribe-transcript-polisher skill if it is available. "
        "Turn this YouTube transcript into clear markdown notes. Preserve the "
        "meaning and order. Use concise headings and bullets where helpful. "
        "Remove filler and caption artifacts. Do not add facts that are not in "
        "the transcript. The transcript is provided on stdin."
    ),
    "summary": (
        "Use the yt-scribe-transcript-polisher skill if it is available. "
        "Summarize this YouTube transcript in plain markdown. Include the main "
        "ideas, key details, and any concrete action items. Do not add facts "
        "that are not in the transcript. The transcript is provided on stdin."
    ),
    "article": (
        "Use the yt-scribe-transcript-polisher skill if it is available. "
        "Rewrite this YouTube transcript as a readable article in markdown. "
        "Preserve the argument and sequence, remove filler, and avoid adding "
        "facts that are not in the transcript. The transcript is provided on stdin."
    ),
}


class CliError(Exception):
    def __init__(self, message: str, code: str = "error", details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass
class CaptionTrack:
    name: str
    language_code: str
    base_url: str
    kind: str | None = None
    is_translatable: bool = False

    @property
    def auto_generated(self) -> bool:
        return self.kind == "asr"

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "language_code": self.language_code,
            "auto_generated": self.auto_generated,
            "is_translatable": self.is_translatable,
        }


def http_get(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise CliError(f"HTTP {exc.code} while fetching {url}", "http_error") from exc
    except urllib.error.URLError as exc:
        message = f"Network error while fetching {url}: {exc.reason}"
        raise CliError(message, "network_error") from exc


def extract_video_id(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value

    parsed = urllib.parse.urlparse(value)
    host = parsed.netloc.lower().removeprefix("www.")
    query = urllib.parse.parse_qs(parsed.query)

    if host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path == "/watch" and query.get("v"):
            return query["v"][0]
        if parsed.path.startswith("/shorts/"):
            candidate = parsed.path.split("/")[2]
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
                return candidate
        if parsed.path.startswith("/embed/"):
            candidate = parsed.path.split("/")[2]
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
                return candidate

    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/")[0]
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
            return candidate

    raise CliError(f"Could not resolve a YouTube video ID from: {value}", "invalid_youtube_url")


def canonical_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def extract_json_object(text: str, marker: str) -> dict[str, Any]:
    marker_index = text.find(marker)
    if marker_index < 0:
        raise CliError(f"Could not find {marker} in YouTube page", "caption_metadata_missing")

    start = text.find("{", marker_index)
    if start < 0:
        raise CliError(f"Could not find JSON object after {marker}", "caption_metadata_missing")

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : index + 1])

    raise CliError(f"Could not parse JSON object after {marker}", "caption_metadata_parse_failed")


def caption_name(track: dict[str, Any]) -> str:
    name = track.get("name", {})
    if isinstance(name, dict):
        runs = name.get("runs")
        if isinstance(runs, list):
            return "".join(str(run.get("text", "")) for run in runs).strip()
        simple = name.get("simpleText")
        if simple:
            return str(simple)
    return str(track.get("languageCode") or "unknown")


def fetch_raw_caption_tracks(video_id: str) -> list[CaptionTrack]:
    page = http_get(canonical_watch_url(video_id)).decode("utf-8", errors="replace")
    player = extract_json_object(page, "ytInitialPlayerResponse")
    renderer = player.get("captions", {}).get("playerCaptionsTracklistRenderer", {})
    raw_tracks = renderer.get("captionTracks") or []
    tracks = [
        CaptionTrack(
            name=caption_name(track),
            language_code=str(track.get("languageCode") or ""),
            base_url=str(track.get("baseUrl") or ""),
            kind=track.get("kind"),
            is_translatable=bool(track.get("isTranslatable")),
        )
        for track in raw_tracks
        if track.get("baseUrl")
    ]
    if not tracks:
        raise CliError("No caption tracks were found for this video", "no_captions")
    return tracks


def list_transcript_tracks(
    video_id: str,
    api: YouTubeTranscriptApi | None = None,
) -> list[CaptionTrack]:
    api = api or YouTubeTranscriptApi()
    try:
        transcript_list = api.list(video_id)
    except Exception as exc:
        message = f"Could not list captions for this video: {exc}"
        raise CliError(message, "caption_list_failed") from exc

    tracks = [
        CaptionTrack(
            name=str(getattr(track, "language", "") or getattr(track, "language_code", "")),
            language_code=str(getattr(track, "language_code", "")),
            base_url="",
            kind="asr" if getattr(track, "is_generated", False) else None,
            is_translatable=bool(getattr(track, "is_translatable", False)),
        )
        for track in transcript_list
    ]
    if not tracks:
        raise CliError("No caption tracks were found for this video", "no_captions")
    return tracks


def choose_track(tracks: list[CaptionTrack], lang: str) -> CaptionTrack:
    lang = lang.lower()
    exact = [track for track in tracks if track.language_code.lower() == lang]
    if exact:
        return sorted(exact, key=lambda track: track.auto_generated)[0]

    requested_prefix = lang.split("-")[0]
    prefix = [
        track
        for track in tracks
        if track.language_code.lower().split("-")[0] == requested_prefix
    ]
    if prefix:
        return sorted(prefix, key=lambda track: track.auto_generated)[0]

    raise CliError(
        f"No caption track matched language '{lang}'. Available: "
        + ", ".join(track.language_code for track in tracks),
        "language_not_available",
        {"available_languages": [track.language_code for track in tracks]},
    )


def timedtext_url(track: CaptionTrack, fmt: str = "json3") -> str:
    parsed = urllib.parse.urlparse(track.base_url)
    query = urllib.parse.parse_qs(parsed.query)
    query["fmt"] = [fmt]
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query, doseq=True)))


def parse_json3_caption(payload: bytes) -> list[dict[str, Any]]:
    data = json.loads(payload.decode("utf-8", errors="replace"))
    events = data.get("events") or []
    segments: list[dict[str, Any]] = []
    for event in events:
        text = "".join(seg.get("utf8", "") for seg in event.get("segs") or [])
        text = html.unescape(text).replace("\n", " ").strip()
        if not text:
            continue
        segments.append(
            {
                "start": round(float(event.get("tStartMs", 0)) / 1000.0, 3),
                "duration": round(float(event.get("dDurationMs", 0)) / 1000.0, 3),
                "text": text,
            }
        )
    return segments


def parse_xml_caption(payload: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(payload.decode("utf-8", errors="replace"))
    segments: list[dict[str, Any]] = []
    for node in root.findall(".//text"):
        raw_text = "".join(node.itertext())
        text = html.unescape(raw_text).replace("\n", " ").strip()
        if not text:
            continue
        segments.append(
            {
                "start": round(float(node.attrib.get("start", "0")), 3),
                "duration": round(float(node.attrib.get("dur", "0")), 3),
                "text": text,
            }
        )
    return segments


def fetch_transcript(
    url_or_id: str,
    lang: str,
    api: YouTubeTranscriptApi | None = None,
) -> dict[str, Any]:
    video_id = extract_video_id(url_or_id)
    api = api or YouTubeTranscriptApi()
    tracks = list_transcript_tracks(video_id, api)
    track = choose_track(tracks, lang)

    try:
        fetched = api.fetch(video_id, languages=[track.language_code])
    except Exception as exc:
        message = f"Could not fetch captions for this video: {exc}"
        raise CliError(message, "caption_fetch_failed") from exc

    segments = [
        {
            "start": round(float(item.start), 3),
            "duration": round(float(item.duration), 3),
            "text": str(item.text).replace("\n", " ").strip(),
        }
        for item in fetched
        if str(item.text).strip()
    ]

    text = "\n".join(segment["text"] for segment in segments)
    if not text.strip():
        raise CliError(
            "Caption track was found, but it did not contain transcript text",
            "empty_transcript",
        )

    return {
        "video_id": video_id,
        "url": canonical_watch_url(video_id),
        "language": track.language_code,
        "track": track.public_dict(),
        "segments": segments,
        "text": text,
        "source": "youtube_transcript_api",
    }


def srt_timestamp(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


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


def write_text(path: str | None, text: str) -> str | None:
    if not path:
        return None
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return str(target)


def next_available_path(path: Path) -> Path:
    path = path.expanduser()
    if not path.exists():
        return path

    for index in range(2, 10_000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate

    raise CliError(f"Could not find an available filename near {path}", "output_path_exhausted")


def default_run_output_path(video_id: str, style: str) -> Path:
    return next_available_path(Path(f"yt-scribe-{video_id}-{style}.md"))


def default_polish_output_path(input_path: str, style: str) -> Path:
    source = Path(input_path).expanduser()
    return next_available_path(Path(f"{source.stem}-{style}.md"))


def command_path(name: str) -> str | None:
    return shutil.which(name)


def command_output(command: list[str]) -> str | None:
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None


def doctor_payload() -> dict[str, Any]:
    install_dir = str(Path.home() / ".local" / "bin")
    path_parts = [
        str(Path(part).resolve())
        for part in os.environ.get("PATH", "").split(os.pathsep)
        if part
    ]
    codex_path = command_path("codex")
    return {
        "command": COMMAND_NAME,
        "version": VERSION,
        "python": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
        },
        "codex": {
            "available": codex_path is not None,
            "path": codex_path,
            "version": command_output(["codex", "--version"]) if codex_path else None,
            "auth": "reuses saved Codex CLI authentication",
        },
        "youtube": {
            "method": "youtube-transcript-api",
            "yt_dlp_required": False,
        },
        "install": {
            "wrapper_dir": install_dir,
            "wrapper_dir_on_path": str(Path(install_dir).resolve()) in path_parts,
            "resolved_command": command_path(COMMAND_NAME),
        },
        "lifecycle": lifecycle_steps(),
    }


def lifecycle_steps() -> list[dict[str, str]]:
    return [
        {
            "step": "check",
            "command": "yt-scribe doctor",
            "purpose": "Verify Python, Codex CLI, and PATH setup.",
        },
        {
            "step": "inspect",
            "command": "yt-scribe inspect <youtube-url>",
            "purpose": "Resolve the video and list available caption tracks.",
        },
        {
            "step": "fetch",
            "command": "yt-scribe fetch <youtube-url> --out transcript.txt",
            "purpose": "Save the raw transcript without using Codex.",
        },
        {
            "step": "polish",
            "command": "yt-scribe polish transcript.txt --style notes --out notes.md",
            "purpose": "Use codex exec on an existing transcript file.",
        },
        {
            "step": "run",
            "command": "yt-scribe run <youtube-url>",
            "purpose": "Fetch and polish into a notes markdown file.",
        },
    ]


def run_codex_polish(
    transcript_text: str,
    instruction: str,
    out_path: str | None,
    model: str | None,
    cwd: str | None,
) -> dict[str, Any]:
    codex = command_path("codex")
    if not codex:
        raise CliError("codex was not found on PATH", "codex_missing")

    with tempfile.TemporaryDirectory(prefix="yt-scribe-") as tmp_dir:
        final_path = Path(tmp_dir) / "final.md"
        command = [
            codex,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--output-last-message",
            str(final_path),
        ]
        if cwd:
            command.extend(["--cd", str(Path(cwd).expanduser().resolve())])
        if model:
            command.extend(["--model", model])
        command.append(instruction)

        result = subprocess.run(
            command,
            input=transcript_text,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise CliError(
                "codex exec failed",
                "codex_exec_failed",
                {
                    "returncode": result.returncode,
                    "stderr_tail": result.stderr[-4000:],
                },
            )

        polished = final_path.read_text(encoding="utf-8") if final_path.exists() else result.stdout
        polished = polished.strip() + "\n"
        written = write_text(out_path, polished)
        return {
            "output_path": written,
            "chars": len(polished),
            "codex_stderr_tail": result.stderr[-2000:],
            "text": polished,
        }


def emit(data: Any, as_json: bool, text: str | None = None) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif text is not None:
        print(text, end="" if text.endswith("\n") else "\n")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def emit_error(exc: CliError, as_json: bool) -> int:
    payload = {"ok": False, "error": {"code": exc.code, "message": str(exc), **exc.details}}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stdout)
    else:
        print(f"{COMMAND_NAME}: {exc}", file=sys.stderr)
        if exc.details.get("stderr_tail"):
            print(exc.details["stderr_tail"], file=sys.stderr)
    return 1


def read_instruction(args: argparse.Namespace) -> str:
    if getattr(args, "prompt_file", None):
        return Path(args.prompt_file).expanduser().read_text(encoding="utf-8").strip()
    if getattr(args, "instruction", None):
        return args.instruction
    return STYLE_INSTRUCTIONS[getattr(args, "style", "notes")]


def limit_text(text: str, max_chars: int) -> str:
    if max_chars and len(text) > max_chars:
        return text[:max_chars]
    return text


def handle_args(args: argparse.Namespace) -> int:
    if args.command == "doctor":
        emit({"ok": True, "doctor": doctor_payload()}, args.json)
        return 0

    if args.command == "lifecycle":
        payload = {"ok": True, "lifecycle": lifecycle_steps()}
        text = "\n".join(
            f"{item['step']}: {item['command']}\n  {item['purpose']}"
            for item in lifecycle_steps()
        )
        text += "\n"
        emit(payload, args.json, text)
        return 0

    if args.command == "inspect":
        video_id = extract_video_id(args.url)
        tracks = list_transcript_tracks(video_id)
        payload = {
            "ok": True,
            "video": {
                "id": video_id,
                "url": canonical_watch_url(video_id),
                "tracks": [track.public_dict() for track in tracks],
            },
        }
        text = (
            f"{canonical_watch_url(video_id)}\n"
            + "\n".join(
                f"- {track.language_code}: {track.name}"
                + (" (auto)" if track.auto_generated else "")
                for track in tracks
            )
            + "\n"
        )
        emit(payload, args.json, text)
        return 0

    if args.command == "fetch":
        transcript = fetch_transcript(args.url, args.lang)
        rendered = render_transcript(transcript, args.format)
        output_path = write_text(args.out, rendered)
        payload = {
            "ok": True,
            "fetch": {
                "video_id": transcript["video_id"],
                "url": transcript["url"],
                "language": transcript["language"],
                "track": transcript["track"],
                "segments": len(transcript["segments"]),
                "chars": len(transcript["text"]),
                "format": args.format,
                "output_path": output_path,
            },
        }
        if args.json:
            emit(payload, True)
        elif output_path:
            emit(payload, False, f"Wrote transcript to {output_path}\n")
        else:
            emit(payload, False, rendered)
        return 0

    if args.command == "polish":
        transcript_text = Path(args.file).expanduser().read_text(encoding="utf-8")
        transcript_text = limit_text(transcript_text, args.max_chars)
        out_path = (
            args.out
            if args.stdout
            else args.out or str(default_polish_output_path(args.file, args.style))
        )
        result = run_codex_polish(
            transcript_text,
            read_instruction(args),
            out_path,
            args.model,
            args.cd,
        )
        payload = {
            "ok": True,
            "polish": {
                "input_path": str(Path(args.file).expanduser().resolve()),
                "style": args.style,
                "output_path": result["output_path"],
                "chars": result["chars"],
            },
        }
        if args.json:
            emit(payload, True)
        elif result["output_path"]:
            emit(payload, False, f"Wrote polished transcript to {result['output_path']}\n")
        else:
            emit(payload, False, result["text"])
        return 0

    if args.command == "run":
        transcript = fetch_transcript(args.url, args.lang)
        rendered = render_transcript(transcript, args.transcript_format)
        transcript_path = write_text(args.transcript, rendered)
        transcript_text = limit_text(transcript["text"], args.max_chars)
        out_path = (
            args.out
            if args.stdout
            else args.out or str(default_run_output_path(transcript["video_id"], args.style))
        )
        result = run_codex_polish(
            transcript_text,
            read_instruction(args),
            out_path,
            args.model,
            args.cd,
        )
        payload = {
            "ok": True,
            "run": {
                "video_id": transcript["video_id"],
                "url": transcript["url"],
                "language": transcript["language"],
                "segments": len(transcript["segments"]),
                "style": args.style,
                "transcript_path": transcript_path,
                "output_path": result["output_path"],
                "chars": result["chars"],
            },
        }
        if args.json:
            emit(payload, True)
        elif result["output_path"]:
            emit(payload, False, f"Wrote polished transcript to {result['output_path']}\n")
        else:
            emit(payload, False, result["text"])
        return 0

    if args.command == "raw":
        video_id = extract_video_id(args.url)
        tracks = fetch_raw_caption_tracks(video_id)
        track = choose_track(tracks, args.lang)
        url = timedtext_url(track, args.format)
        if args.body:
            body = http_get(url).decode("utf-8", errors="replace")
            emit({"ok": True, "body": body}, args.json, body)
            return 0
        payload = {
            "ok": True,
            "raw": {
                "video_id": video_id,
                "selected_track": track.public_dict(),
                "url": url,
                "tracks": [track.public_dict() for track in tracks],
            },
        }
        emit(payload, args.json)
        return 0

    raise CliError("Unhandled command", "internal_error")


def add_polish_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--style",
        choices=sorted(STYLE_INSTRUCTIONS),
        default="notes",
        help="Codex output style, default: notes",
    )
    parser.add_argument("--out", help="write polished output to this file")
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="print polished output instead of writing a file",
    )
    parser.add_argument("--instruction", help="replace the built-in Codex prompt")
    parser.add_argument("--prompt-file", help="read the Codex prompt from a file")
    parser.add_argument("--model", help="optional codex exec model override")
    parser.add_argument("--cd", help="working directory to pass to codex exec")
    parser.add_argument("--max-chars", type=int, default=0, help="truncate transcript before Codex")


def build_parser() -> argparse.ArgumentParser:
    epilog = """lifecycle:
  1. yt-scribe doctor
  2. yt-scribe inspect <youtube-url>
  3. yt-scribe fetch <youtube-url> --out transcript.txt
  4. yt-scribe polish transcript.txt --style notes --out notes.md
  5. yt-scribe run <youtube-url>

ai contract:
  Put --json before the command for stable machine-readable output.
  Use raw only as a read-only escape hatch when inspect or fetch is not enough.
"""
    parser = argparse.ArgumentParser(
        prog=COMMAND_NAME,
        description="Human-first CLI for turning YouTube links into Codex-polished notes.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", help="emit stable JSON output")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="command")

    subparsers.add_parser("doctor", help="check Codex, Python, and PATH setup")
    subparsers.add_parser("lifecycle", help="print the recommended workflow")

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="resolve a video and list caption tracks",
    )
    inspect_parser.add_argument("url", help="YouTube URL or 11-character video ID")

    fetch_parser = subparsers.add_parser("fetch", help="download the transcript without Codex")
    fetch_parser.add_argument("url", help="YouTube URL or 11-character video ID")
    fetch_parser.add_argument("--lang", default="en", help="caption language code, default: en")
    fetch_parser.add_argument("--format", choices=["text", "json", "srt"], default="text")
    fetch_parser.add_argument("--out", help="write transcript to this file")

    polish_parser = subparsers.add_parser(
        "polish",
        help="polish an existing transcript with codex exec",
    )
    polish_parser.add_argument("file", help="transcript file to polish")
    add_polish_flags(polish_parser)

    run_parser = subparsers.add_parser(
        "run",
        help="fetch and polish in one command",
        description=(
            "Fetch a YouTube transcript and write notes to "
            "yt-scribe-<video-id>-notes.md by default."
        ),
    )
    run_parser.add_argument("url", help="YouTube URL or 11-character video ID")
    run_parser.add_argument("--lang", default="en", help="caption language code, default: en")
    run_parser.add_argument("--transcript", help="also write the raw transcript to this file")
    run_parser.add_argument("--transcript-format", choices=["text", "json", "srt"], default="text")
    add_polish_flags(run_parser)

    raw_parser = subparsers.add_parser("raw", help="read-only timedtext escape hatch")
    raw_parser.add_argument("url", help="YouTube URL or 11-character video ID")
    raw_parser.add_argument("--lang", default="en")
    raw_parser.add_argument("--format", choices=["json3", "srv3"], default="json3")
    raw_parser.add_argument(
        "--body",
        action="store_true",
        help="print the raw timedtext response body",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return handle_args(args)
    except CliError as exc:
        return emit_error(exc, getattr(args, "json", False))


if __name__ == "__main__":
    raise SystemExit(main())
