#!/usr/bin/env python3
"""Fetch YouTube transcripts and polish them with Codex or OpenCode."""

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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig, InvalidProxyConfig

VERSION = "0.1.0"
COMMAND_NAME = "yt-scribe"
DEFAULT_AGENT_HARNESS = "codex"
AGENT_HARNESSES = ("codex", "opencode")
CONFIG_ENV_VAR = "YT_SCRIBE_CONFIG"
CONFIG_FILENAME = "config.json"
AGENTS_SKILLS_DIR_ENV_VAR = "YT_SCRIBE_AGENTS_SKILLS_DIR"
HTTP_PROXY_ENV_VAR = "YT_SCRIBE_HTTP_PROXY"
HTTPS_PROXY_ENV_VAR = "YT_SCRIBE_HTTPS_PROXY"
DEFAULT_CACHE_DIR = Path(".yt-scribe") / "cache"
PROJECT_CONFIG = Path(".yt-scribe") / CONFIG_FILENAME
ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)

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

EMBEDDED_SKILL_ASSETS = {
    ".agents/skills/yt-scribe/SKILL.md": """---
name: yt-scribe
description: Use when an agent needs to fetch a YouTube transcript, inspect
  available captions, save raw captions, or polish a transcript into notes,
  summaries, cleaned text, or article-style prose through the installed
  `yt-scribe` CLI.
---

# yt-scribe

Use the installed `yt-scribe` CLI for YouTube transcript workflows. This skill
teaches an agent how to use the CLI correctly.

Prefer `--json` when reading command output for analysis or chaining.

Read exactly one harness file for command details:

- Codex: `harness/codex.md`
- OpenCode: `harness/opencode.md`

The CLI is human-first. Its default path should be the same obvious command a person would run:

```sh
yt-scribe run "<youtube-url>"
```

## Start

Verify the command exists and the local harness setup is available:

```sh
yt-scribe --json doctor
```

If `yt-scribe` is missing, install and set it up from the public repository:

```sh
python -m pip install --upgrade git+https://github.com/Berkay2002/yt-scribe.git \\
  && python -m yt_scribe setup
```

From a checkout, run `sh ./install-local.sh` on Linux or macOS, or
`.\\install-local.ps1` on Windows. The local installers create the wrapper and
run setup.

## Workflow

For a new YouTube link:

```sh
yt-scribe --json inspect "<youtube-url>"
yt-scribe --json inspect "<youtube-url>" --brief
yt-scribe --json fetch "<youtube-url>" --lang en --out transcript.txt
yt-scribe --json polish transcript.txt --style notes --out notes.md
yt-scribe --json polish transcript.txt --focus "Focus on decisions and risks" --out notes.md
```

For the one-command path:

```sh
yt-scribe --json run "<youtube-url>"
yt-scribe --json run "<youtube-url>" --focus "Keep only action items"
yt-scribe --json run "<youtube-url>" --timestamps
yt-scribe --json run "<youtube-url>" --bundle-dir .yt-scribe/runs/VIDEO_ID
```

Use styles intentionally:

- `notes`: structured markdown notes.
- `summary`: concise summary with key ideas.
- `clean`: cleaned transcript text with filler removed.
- `article`: readable article-style prose.

Use `--focus "..."` or `--focus-file instructions.md` when the user wants
specific emphasis while keeping the normal harness prompt. Use `--instruction`
or `--prompt-file` only when the user needs to replace the whole polishing prompt.

Use `--timestamps` when the user needs polished output with source anchors. For
`run`, yt-scribe passes transcript segment start times to the polisher. For
`polish`, the input transcript should already contain useful timestamp anchors.

Use `verify` when the user needs a conservative transcript-backed check:

```sh
yt-scribe --json verify notes.md --transcript transcript.json
```

Use profiles and templates for repeated local conventions:

```sh
yt-scribe config profile set research --style notes --template research --langs en,en-US
yt-scribe --json run "<youtube-url>" --profile research
```

Use `--chunk-chars` only for long transcripts that need chunk-and-merge polishing.
Use `batch` for URL lists; playlist URLs in a batch file expand into normal batch
items.

## Safety

- Use `inspect` before assuming captions exist.
- Do not claim transcript availability until `fetch`, `inspect`, or `run` succeeds.
- Do not bypass private, disabled, or unavailable captions.
- Do not use `raw --body` unless high-level commands are insufficient.
- Do not run destructive shell commands as part of this workflow.
- Do not pass secrets in `--focus`, `--instruction`, or prompt files.
""",
    ".agents/skills/yt-scribe/harness/codex.md": """# Codex Harness

Use this file when Codex is running the yt-scribe CLI or when the user wants the
Codex polishing harness.

`polish` and `run` use Codex by default:

```sh
yt-scribe --json run "<youtube-url>"
yt-scribe --json polish transcript.txt --style summary --out summary.md
```

With Codex, the CLI invokes `codex exec` in read-only, ephemeral mode and passes
the transcript through stdin. The polishing prompt asks for the
`yt-scribe-transcript-polisher` skill from `.agents/skills` and its Codex
instructions. The CLI writes final Codex output through `--output-last-message`,
so prefer `--out` when the user expects a file.
""",
    ".agents/skills/yt-scribe/harness/opencode.md": """# OpenCode Harness

Use this file when OpenCode is running the yt-scribe CLI or when the user wants
the OpenCode polishing harness.

Select OpenCode per command:

```sh
yt-scribe --json run "<youtube-url>" --agent-harness opencode
yt-scribe --json polish transcript.txt --agent-harness opencode --style summary --out summary.md
```

Or persist it as the default:

```sh
yt-scribe config set default-agent-harness opencode
```

With OpenCode, the CLI invokes `opencode run` and attaches the transcript as a
temp file. The polishing prompt asks for the shared
`yt-scribe-transcript-polisher` skill from `.agents/skills` and its OpenCode
instructions. Prefer `--out` when the user expects a file.
""",
    ".agents/skills/yt-scribe/agents/openai.yaml": """interface:
  display_name: "YT Scribe"
  short_description: "Use yt-scribe to fetch and polish YouTube transcripts."
  default_prompt: "Turn this YouTube link into clean notes."
""",
    ".agents/skills/yt-scribe-transcript-polisher/SKILL.md": """---
name: yt-scribe-transcript-polisher
description: Use when `yt-scribe polish` or `yt-scribe run` invokes an agent to
  transform a YouTube transcript into cleaned text, notes, a summary, or
  article-style prose. This skill is for transcript polishing only, not for
  fetching captions or running the CLI.
---

# yt-scribe Transcript Polisher

Transform the transcript text already provided by `yt-scribe`. This skill is for the
agent started by the CLI after the transcript has already been fetched. Do not fetch
the video, inspect unrelated files, run shell commands, or call `yt-scribe`.

Read exactly one harness file based on how the transcript was provided:

- Codex stdin: `harness/codex.md`
- OpenCode attached transcript file: `harness/opencode.md`

## Rules

- Return only the requested polished transcript output.
- Preserve the speaker's meaning, sequence, and concrete claims.
- Honor custom user instructions passed by `yt-scribe`. When they conflict with
  the selected output mode, the custom instructions win unless they would require
  adding unsupported facts.
- Remove caption artifacts, repeated fragments, filler, obvious false starts,
  and timestamp residue unless timestamp grounding was requested.
- When timestamp grounding is requested, preserve useful timestamp anchors from
  the provided transcript near important claims. Do not invent timestamps.
- Do not add facts, examples, citations, links, or claims that are not in the transcript.
- Do not mention that you used a skill, a harness, stdin, an attached file, or a
  cleaning process.
- If the transcript is empty or unusable, say that the transcript content is
  missing or unusable.

## Output Modes

For `clean`, produce lightly edited prose close to the original transcript.

For `notes`, produce markdown notes with short headings and bullets. Keep the
structure useful for review, not overly nested.

For `summary`, produce a concise markdown summary with the main ideas, key details,
and action items when present.

For `article`, produce readable article-style markdown while preserving the original
argument and order.

## Quality Bar

Prefer boring accuracy over elegant rewriting. When a phrase is ambiguous, keep it
closer to the original instead of guessing. Preserve names, commands, numbers,
dates, and technical terms exactly unless the transcript clearly contains a
captioning artifact.
""",
    ".agents/skills/yt-scribe-transcript-polisher/harness/codex.md": """# Codex Harness

Use this file when `yt-scribe` invokes Codex through `codex exec`.

The transcript is provided through stdin, appended to the prompt as the content to
transform. Return only the polished transcript output.

Do not mention Codex, stdin, or the polishing process in the final answer.
""",
    ".agents/skills/yt-scribe-transcript-polisher/harness/opencode.md": """# OpenCode Harness

Use this file when `yt-scribe` invokes OpenCode through `opencode run`.

The transcript is attached as a temp transcript file. Read that attached transcript
as the content to transform. Return only the polished transcript output.

Do not mention OpenCode, the attached file, or the polishing process in the final answer.
""",
    ".agents/skills/yt-scribe-transcript-polisher/agents/openai.yaml": """interface:
  display_name: "YT Scribe Transcript Polisher"
  short_description: "Polish transcript text passed through yt-scribe."
  default_prompt: "Polish this transcript into clean notes."
""",
}


class CliError(Exception):
    def __init__(self, message: str, code: str = "error", details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass
class PolishInstruction:
    text: str
    mode: str
    sources: list[str]


class ProgressWait:
    def __init__(self, reporter: ProgressReporter, message: str, interval_seconds: int = 15):
        self.reporter = reporter
        self.message = message
        self.interval_seconds = interval_seconds
        self.started_at = 0.0
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def __enter__(self) -> ProgressWait:
        self.started_at = time.monotonic()
        self.reporter.message(self.message)
        if self.reporter.enabled:
            self.thread = threading.Thread(target=self._heartbeat, daemon=True)
            self.thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=0.2)

    def _heartbeat(self) -> None:
        while not self.stop_event.wait(self.interval_seconds):
            elapsed = int(time.monotonic() - self.started_at)
            self.reporter.message(f"{self.message} ({elapsed}s elapsed)")


class ProgressReporter:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def message(self, text: str) -> None:
        if self.enabled:
            print(f"{COMMAND_NAME}: {text}", file=sys.stderr, flush=True)

    def wait(self, message: str) -> ProgressWait:
        return ProgressWait(self, message)


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


def http_get(url: str, proxy_config: GenericProxyConfig | None = None) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        if proxy_config:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler(proxy_config.to_requests_dict())
            )
            response_context = opener.open(request, timeout=30)
        else:
            response_context = urllib.request.urlopen(request, timeout=30)
        with response_context as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise CliError(
                "YouTube returned HTTP 429 while fetching caption metadata. "
                "This usually means the current IP is rate-limited or blocked; "
                "retry later or use --https-proxy / YT_SCRIBE_HTTPS_PROXY.",
                "youtube_ip_blocked",
            ) from exc
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


def fetch_raw_caption_tracks(
    video_id: str,
    proxy_config: GenericProxyConfig | None = None,
) -> list[CaptionTrack]:
    page = http_get(canonical_watch_url(video_id), proxy_config).decode(
        "utf-8",
        errors="replace",
    )
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
    proxy_config: GenericProxyConfig | None = None,
) -> list[CaptionTrack]:
    api = api or YouTubeTranscriptApi(proxy_config=proxy_config)
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


def normalize_languages(languages: str | list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(languages, str):
        candidates = [languages]
    else:
        candidates = list(languages)

    normalized: list[str] = []
    for candidate in candidates:
        normalized.extend(part.strip() for part in str(candidate).split(","))
    return [language for language in normalized if language]


def requested_languages(args: argparse.Namespace) -> list[str]:
    return normalize_languages(args.langs or args.lang)


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


def choose_track_from_languages(
    tracks: list[CaptionTrack],
    languages: str | list[str] | tuple[str, ...],
) -> tuple[CaptionTrack, list[str]]:
    requested = normalize_languages(languages)
    if not requested:
        raise CliError("At least one caption language is required", "language_required")

    last_error: CliError | None = None
    for language in requested:
        try:
            return choose_track(tracks, language), requested
        except CliError as exc:
            last_error = exc

    available = [track.language_code for track in tracks]
    message = (
        "No caption track matched requested languages "
        f"{', '.join(requested)}. Available: {', '.join(available)}"
    )
    raise CliError(
        message,
        "language_not_available",
        {
            "requested_languages": requested,
            "available_languages": available,
            **(last_error.details if last_error else {}),
        },
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


def fetch_timedtext_segments(
    track: CaptionTrack,
    proxy_config: GenericProxyConfig | None = None,
) -> list[dict[str, Any]]:
    try:
        payload = http_get(timedtext_url(track, "json3"), proxy_config)
        return parse_json3_caption(payload)
    except (CliError, json.JSONDecodeError, TypeError, ValueError):
        payload = http_get(timedtext_url(track, "srv3"), proxy_config)
        return parse_xml_caption(payload)


def transcript_payload(
    video_id: str,
    track: CaptionTrack,
    languages: list[str],
    segments: list[dict[str, Any]],
    source: str,
) -> dict[str, Any]:
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
        "requested_languages": languages,
        "track": track.public_dict(),
        "segments": segments,
        "text": text,
        "source": source,
    }


def fetch_raw_transcript(
    video_id: str,
    lang: str | list[str],
    proxy_config: GenericProxyConfig | None = None,
) -> dict[str, Any]:
    tracks = fetch_raw_caption_tracks(video_id, proxy_config)
    track, languages = choose_track_from_languages(tracks, lang)
    segments = fetch_timedtext_segments(track, proxy_config)
    return transcript_payload(video_id, track, languages, segments, "youtube_timedtext")


def fetch_transcript(
    url_or_id: str,
    lang: str | list[str],
    api: YouTubeTranscriptApi | None = None,
    proxy_config: GenericProxyConfig | None = None,
) -> dict[str, Any]:
    video_id = extract_video_id(url_or_id)
    api = api or YouTubeTranscriptApi(proxy_config=proxy_config)
    try:
        tracks = list_transcript_tracks(video_id, api, proxy_config)
    except CliError:
        return fetch_raw_transcript(video_id, lang, proxy_config)

    track, languages = choose_track_from_languages(tracks, lang)

    try:
        fetched = api.fetch(video_id, languages=[track.language_code])
    except Exception as exc:
        try:
            return fetch_raw_transcript(video_id, lang, proxy_config)
        except CliError:
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

    return transcript_payload(video_id, track, languages, segments, "youtube_transcript_api")


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


def chunk_artifact_dir(out_path: str | None) -> Path | None:
    if not out_path:
        return None
    return Path(f"{out_path}.chunks").expanduser().resolve()


def chunk_output_path(directory: Path, index: int) -> Path:
    return directory / f"chunk-{index:03}.md"


def merge_chunk_instruction(instruction: str) -> str:
    return (
        "Merge these polished transcript chunks into one coherent polished output. "
        "Remove duplicate headings introduced by chunking, preserve the original order, "
        "and do not add facts that are not present in the chunk outputs.\n\n"
        "Original polishing instruction:\n"
        f"{instruction}"
    )


def chunking_disabled_payload() -> dict[str, Any]:
    return {
        "enabled": False,
        "chunk_chars": 0,
        "chunks": 1,
        "merge_status": "not_needed",
        "chunk_artifact_dir": None,
        "resumed_chunks": 0,
    }


def run_chunked_agent_polish(
    chunks: list[str],
    instruction: str,
    out_path: str | None,
    model: str | None,
    cwd: str | None,
    harness: str,
    chunk_chars: int,
    resume: bool,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    if len(chunks) <= 1:
        result = run_agent_polish(
            transcript_text=chunks[0] if chunks else "",
            instruction=instruction,
            out_path=out_path,
            model=model,
            cwd=cwd,
            harness=harness,
            progress=progress,
        )
        result["chunking"] = {
            "enabled": True,
            "chunk_chars": chunk_chars,
            "chunks": len(chunks),
            "merge_status": "not_needed",
            "chunk_artifact_dir": None,
            "resumed_chunks": 0,
        }
        return result

    artifact_dir = chunk_artifact_dir(out_path)
    if artifact_dir:
        artifact_dir.mkdir(parents=True, exist_ok=True)

    polished_chunks: list[str] = []
    resumed_chunks = 0
    for index, chunk in enumerate(chunks, start=1):
        chunk_path = chunk_output_path(artifact_dir, index) if artifact_dir else None
        if resume and chunk_path and chunk_path.is_file():
            polished_chunks.append(chunk_path.read_text(encoding="utf-8"))
            resumed_chunks += 1
            continue
        try:
            result = run_agent_polish(
                transcript_text=chunk,
                instruction=instruction,
                out_path=str(chunk_path) if chunk_path else None,
                model=model,
                cwd=cwd,
                harness=harness,
                progress=progress,
            )
        except CliError as exc:
            raise CliError(
                f"Chunk {index} polish failed: {exc}",
                "chunk_polish_failed",
                {"chunk_index": index, "error_code": exc.code},
            ) from exc
        polished_chunks.append(result["text"])

    merge_input = "\n\n".join(
        f"## Chunk {index}\n\n{chunk.strip()}"
        for index, chunk in enumerate(polished_chunks, start=1)
    )
    try:
        result = run_agent_polish(
            transcript_text=merge_input,
            instruction=merge_chunk_instruction(instruction),
            out_path=out_path,
            model=model,
            cwd=cwd,
            harness=harness,
            progress=progress,
        )
    except CliError as exc:
        raise CliError(
            f"Chunk merge failed: {exc}",
            "chunk_merge_failed",
            {"chunks": len(chunks), "error_code": exc.code},
        ) from exc

    result["chunking"] = {
        "enabled": True,
        "chunk_chars": chunk_chars,
        "chunks": len(chunks),
        "merge_status": "merged",
        "chunk_artifact_dir": str(artifact_dir) if artifact_dir else None,
        "resumed_chunks": resumed_chunks,
    }
    return result


VERIFY_STOPWORDS = {
    "about",
    "after",
    "also",
    "and",
    "are",
    "but",
    "for",
    "from",
    "has",
    "have",
    "into",
    "that",
    "the",
    "this",
    "was",
    "were",
    "with",
}


def normalize_verification_text(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def strip_markdown_marker(line: str) -> str:
    return re.sub(r"^\s{0,3}(?:[-*+]\s+|\d+[.)]\s+|#{1,6}\s+)", "", line).strip()


def split_polished_claims(text: str) -> list[str]:
    claims: list[str] = []
    for line in text.splitlines():
        claim = strip_markdown_marker(line)
        if not claim:
            continue
        parts = re.split(r"(?<=[.!?])\s+", claim)
        claims.extend(part.strip() for part in parts if part.strip())
    return claims


def verification_terms(text: str) -> list[str]:
    terms = re.findall(r"[A-Za-z][A-Za-z'-]*|\d+(?:[.,:/-]\d+)*", text)
    return [
        term
        for term in terms
        if normalize_verification_text(term) not in VERIFY_STOPWORDS
    ]


def risky_verification_terms(text: str) -> list[str]:
    names = re.findall(r"\b[A-Z][a-zA-Z]*(?:\s+[A-Z][a-zA-Z]*)*\b", text)
    numbers = re.findall(r"\b\d+(?:[.,:/-]\d+)*\b", text)
    terms = []
    for term in [*names, *numbers]:
        normalized = normalize_verification_text(term)
        if normalized and normalized not in VERIFY_STOPWORDS and term not in terms:
            terms.append(term)
    return terms


def transcript_entries_from_text(text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = re.match(r"^\[(\d{2}:\d{2}(?::\d{2})?)\]\s*(.+)$", stripped)
        if match:
            anchor, entry_text = match.groups()
        else:
            anchor, entry_text = "", stripped
        entries.append(
            {
                "anchor": anchor,
                "text": entry_text,
                "normalized": normalize_verification_text(entry_text),
            }
        )
    return entries


def transcript_entries_from_segments(segments: list[Any]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        anchor = timestamp_anchor(float(segment.get("start") or 0))
        entries.append(
            {
                "anchor": anchor,
                "text": text,
                "normalized": normalize_verification_text(text),
            }
        )
    return entries


def load_verification_transcript(path: str | Path) -> dict[str, Any]:
    text = Path(path).expanduser().read_text(encoding="utf-8")
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            segments = parsed.get("segments")
            if isinstance(segments, list):
                entries = transcript_entries_from_segments(segments)
                transcript_text = "\n".join(entry["text"] for entry in entries)
                return {"text": transcript_text, "entries": entries}
            if isinstance(parsed.get("text"), str):
                transcript_text = parsed["text"]
                return {
                    "text": transcript_text,
                    "entries": transcript_entries_from_text(transcript_text),
                }
        if isinstance(parsed, list):
            entries = transcript_entries_from_segments(parsed)
            transcript_text = "\n".join(entry["text"] for entry in entries)
            return {"text": transcript_text, "entries": entries}
    return {"text": text, "entries": transcript_entries_from_text(text)}


def find_transcript_anchor(
    claim_terms: list[str],
    transcript_entries: list[dict[str, str]],
) -> str | None:
    normalized_terms = [normalize_verification_text(term) for term in claim_terms]
    normalized_terms = [term for term in normalized_terms if term]
    best_entry: dict[str, str] | None = None
    best_score = 0
    for entry in transcript_entries:
        entry_text = entry["normalized"]
        score = sum(1 for term in normalized_terms if term in entry_text)
        if score > best_score:
            best_entry = entry
            best_score = score
    if best_entry and best_entry["anchor"]:
        return best_entry["anchor"]
    return None


def verify_claim(
    claim: str,
    transcript_text: str,
    transcript_entries: list[dict[str, str]],
    index: int,
) -> dict[str, Any]:
    transcript_normalized = normalize_verification_text(transcript_text)
    claim_normalized = normalize_verification_text(claim)
    terms = verification_terms(claim)
    unsupported_terms = [
        term
        for term in risky_verification_terms(claim)
        if normalize_verification_text(term) not in transcript_normalized
    ]
    anchor = find_transcript_anchor(terms, transcript_entries)

    if claim_normalized and claim_normalized in transcript_normalized:
        status = "supported"
        severity = "info"
        message = "The claim text appears in the transcript."
    elif unsupported_terms:
        status = "unsupported"
        severity = "high"
        message = "The claim contains names or numbers not found in the transcript."
    else:
        normalized_terms = [normalize_verification_text(term) for term in terms]
        normalized_terms = [term for term in normalized_terms if term]
        supported_terms = [term for term in normalized_terms if term in transcript_normalized]
        if normalized_terms and len(supported_terms) == len(normalized_terms):
            status = "supported"
            severity = "info"
            message = "The claim's key terms appear in the transcript."
        elif normalized_terms and not supported_terms and len(normalized_terms) >= 3:
            status = "unsupported"
            severity = "medium"
            message = "No key terms from the claim were found in the transcript."
        else:
            status = "uncertain"
            severity = "medium"
            message = "The claim could not be confirmed or rejected deterministically."

    return {
        "index": index,
        "status": status,
        "severity": severity,
        "claim": claim,
        "message": message,
        "unsupported_terms": unsupported_terms,
        "transcript_anchor": anchor,
    }


def verify_polished_output(polished_text: str, transcript_text: str) -> dict[str, Any]:
    transcript_entries = transcript_entries_from_text(transcript_text)
    findings = [
        verify_claim(claim, transcript_text, transcript_entries, index)
        for index, claim in enumerate(split_polished_claims(polished_text), start=1)
    ]
    summary = {
        "claims": len(findings),
        "supported": sum(1 for finding in findings if finding["status"] == "supported"),
        "unsupported": sum(1 for finding in findings if finding["status"] == "unsupported"),
        "uncertain": sum(1 for finding in findings if finding["status"] == "uncertain"),
    }
    return {"summary": summary, "findings": findings}


def verify_polished_file(polished_path: str | Path, transcript_path: str | Path) -> dict[str, Any]:
    polished_text = Path(polished_path).expanduser().read_text(encoding="utf-8")
    transcript = load_verification_transcript(transcript_path)
    findings = [
        verify_claim(claim, transcript["text"], transcript["entries"], index)
        for index, claim in enumerate(split_polished_claims(polished_text), start=1)
    ]
    summary = {
        "claims": len(findings),
        "supported": sum(1 for finding in findings if finding["status"] == "supported"),
        "unsupported": sum(1 for finding in findings if finding["status"] == "unsupported"),
        "uncertain": sum(1 for finding in findings if finding["status"] == "uncertain"),
    }
    return {"summary": summary, "findings": findings}


def render_verification(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines = [
        (
            f"claims: {summary['claims']}, supported: {summary['supported']}, "
            f"unsupported: {summary['unsupported']}, uncertain: {summary['uncertain']}"
        )
    ]
    for finding in result["findings"]:
        anchor = f" [{finding['transcript_anchor']}]" if finding["transcript_anchor"] else ""
        lines.append(
            f"- {finding['status']}{anchor}: {finding['claim']}\n  {finding['message']}"
        )
    return "\n".join(lines) + "\n"


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


def cache_dir_from_args(args: argparse.Namespace) -> Path | None:
    if getattr(args, "cache_dir", None):
        return Path(args.cache_dir).expanduser().resolve()
    if getattr(args, "resume", False):
        return DEFAULT_CACHE_DIR.resolve()
    return None


def validate_proxy_url(value: str, source: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if not parsed.scheme or not parsed.netloc or not parsed.hostname:
        raise CliError(
            f"{source} must be a full proxy URL like http://user:pass@host:8080",
            "invalid_proxy_config",
        )
    try:
        _ = parsed.port
    except ValueError as exc:
        raise CliError(
            f"{source} has an invalid port. Use a numeric port, not a placeholder.",
            "invalid_proxy_config",
        ) from exc
    return value


def proxy_config_from_args(args: argparse.Namespace) -> GenericProxyConfig | None:
    http_proxy = getattr(args, "http_proxy", None) or os.environ.get(HTTP_PROXY_ENV_VAR)
    https_proxy = getattr(args, "https_proxy", None) or os.environ.get(HTTPS_PROXY_ENV_VAR)
    if not http_proxy and not https_proxy:
        return None
    if http_proxy:
        http_proxy = validate_proxy_url(http_proxy, "--http-proxy")
    if https_proxy:
        https_proxy = validate_proxy_url(https_proxy, "--https-proxy")
    try:
        return GenericProxyConfig(http_url=http_proxy, https_url=https_proxy)
    except InvalidProxyConfig as exc:
        raise CliError(str(exc), "invalid_proxy_config") from exc


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


def write_text(path: str | None, text: str) -> str | None:
    if not path:
        return None
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return str(target)


def bundle_paths(bundle_dir: str | None) -> dict[str, str] | None:
    if not bundle_dir:
        return None
    root = Path(bundle_dir).expanduser().resolve()
    return {
        "dir": str(root),
        "transcript": str(root / "transcript.txt"),
        "polished": str(root / "polished.md"),
        "metadata": str(root / "metadata.json"),
    }


def write_bundle_metadata(path: str, data: dict[str, Any]) -> str:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(target)


def init_project(project_dir: str | Path, profile: str | None = None) -> dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    yt_scribe_dir = root / ".yt-scribe"
    yt_scribe_dir.mkdir(parents=True, exist_ok=True)
    guidance_path = yt_scribe_dir / "AGENTS.md"
    config_path = yt_scribe_dir / "config.json"
    guidance = (
        "# yt-scribe\n\n"
        "Use `yt-scribe` for YouTube transcript workflows in this project.\n\n"
        "- Prefer `yt-scribe run \"<youtube-url>\"` for one-command notes.\n"
        "- Use `yt-scribe inspect \"<youtube-url>\" --brief` before assuming captions exist.\n"
        "- Do not bypass private, disabled, unavailable, or missing caption tracks.\n"
        "- Do not add facts that are not in the transcript.\n"
    )
    guidance_path.write_text(guidance, encoding="utf-8")
    config = {"profiles": {}}
    if profile:
        config["profiles"][profile] = {
            "style": "notes",
            "template": "research",
            "front_matter": True,
        }
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "dir": str(yt_scribe_dir),
        "guidance_path": str(guidance_path),
        "config_path": str(config_path),
    }


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


def batch_output_path(out_dir: str | Path, video_id: str, style: str) -> Path:
    return Path(out_dir).expanduser().resolve() / f"yt-scribe-{video_id}-{style}.md"


def read_batch_urls(path: str | Path) -> list[str]:
    source = Path(path).expanduser()
    urls = []
    for line in source.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            urls.append(stripped)
    return urls


def playlist_id_from_url(value: str) -> str | None:
    parsed = urllib.parse.urlparse(value)
    query = urllib.parse.parse_qs(parsed.query)
    playlist_ids = query.get("list") or []
    if playlist_ids:
        return playlist_ids[0]
    return None


def fetch_playlist_video_ids(
    playlist_url: str,
    proxy_config: GenericProxyConfig | None = None,
) -> list[str]:
    payload = http_get(playlist_url, proxy_config).decode("utf-8", errors="replace")
    seen: set[str] = set()
    video_ids: list[str] = []
    for video_id in re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', payload):
        if video_id not in seen:
            seen.add(video_id)
            video_ids.append(video_id)
    if not video_ids:
        raise CliError("No videos were found in this playlist", "playlist_empty")
    return video_ids


def expand_batch_items(
    urls: list[str],
    proxy_config: GenericProxyConfig | None,
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for url in urls:
        playlist_id = playlist_id_from_url(url)
        if not playlist_id:
            items.append({"url": url})
            continue
        try:
            video_ids = fetch_playlist_video_ids(url, proxy_config)
        except CliError as exc:
            items.append(
                {
                    "url": url,
                    "playlist_url": url,
                    "playlist_id": playlist_id,
                    "playlist_error": {
                        "code": exc.code,
                        "message": str(exc),
                        **exc.details,
                    },
                }
            )
            continue
        for video_id in video_ids:
            items.append(
                {
                    "url": canonical_watch_url(video_id),
                    "playlist_url": url,
                    "playlist_id": playlist_id,
                }
            )
    return items


def command_path(name: str) -> str | None:
    return shutil.which(name)


def command_invocation(executable: str, *args: str) -> list[str]:
    suffix = Path(executable).suffix.lower()
    if sys.platform == "win32" and suffix in {".bat", ".cmd"}:
        return ["cmd", "/c", executable, *args]
    if sys.platform == "win32" and suffix == ".ps1":
        return [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            executable,
            *args,
        ]
    return [executable, *args]


def command_output(command: list[str]) -> str | None:
    executable = command_path(command[0]) if len(command) > 0 else None
    if executable:
        command = command_invocation(executable, *command[1:])
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
    output = ANSI_PATTERN.sub("", result.stdout).strip()
    return output or None


def agents_skills_dir() -> Path:
    override = os.environ.get(AGENTS_SKILLS_DIR_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".agents" / "skills"


def source_asset_path(relative_path: str) -> Path | None:
    candidate = Path(__file__).resolve().parent / Path(relative_path)
    return candidate if candidate.is_file() else None


def asset_content(relative_path: str) -> str:
    source = source_asset_path(relative_path)
    if source:
        return source.read_text(encoding="utf-8")
    return EMBEDDED_SKILL_ASSETS[relative_path]


def skill_asset_targets() -> dict[str, Path]:
    skills_dir = agents_skills_dir()
    return {
        ".agents/skills/yt-scribe/SKILL.md": skills_dir / "yt-scribe" / "SKILL.md",
        ".agents/skills/yt-scribe/harness/codex.md": (
            skills_dir / "yt-scribe" / "harness" / "codex.md"
        ),
        ".agents/skills/yt-scribe/harness/opencode.md": (
            skills_dir / "yt-scribe" / "harness" / "opencode.md"
        ),
        ".agents/skills/yt-scribe/agents/openai.yaml": (
            skills_dir / "yt-scribe" / "agents" / "openai.yaml"
        ),
        ".agents/skills/yt-scribe-transcript-polisher/SKILL.md": (
            skills_dir / "yt-scribe-transcript-polisher" / "SKILL.md"
        ),
        ".agents/skills/yt-scribe-transcript-polisher/harness/codex.md": (
            skills_dir / "yt-scribe-transcript-polisher" / "harness" / "codex.md"
        ),
        ".agents/skills/yt-scribe-transcript-polisher/harness/opencode.md": (
            skills_dir / "yt-scribe-transcript-polisher" / "harness" / "opencode.md"
        ),
        ".agents/skills/yt-scribe-transcript-polisher/agents/openai.yaml": (
            skills_dir / "yt-scribe-transcript-polisher" / "agents" / "openai.yaml"
        ),
    }


def install_skills() -> dict[str, Any]:
    installed = []
    for relative_path, target in skill_asset_targets().items():
        target.parent.mkdir(parents=True, exist_ok=True)
        content = asset_content(relative_path)
        target.write_text(content, encoding="utf-8")
        installed.append(
            {
                "source": relative_path,
                "path": str(target),
                "bytes": len(content.encode("utf-8")),
            }
        )
    return {
        "agents_skills_dir": str(agents_skills_dir()),
        "installed": installed,
    }


def setup_payload() -> dict[str, Any]:
    return {
        "skills": install_skills(),
        "doctor": doctor_payload(),
        "next": {
            "run": "yt-scribe run <youtube-url>",
            "check": "yt-scribe doctor",
        },
    }


def skills_payload() -> dict[str, Any]:
    targets = skill_asset_targets()
    return {
        "agents_skills_dir": str(agents_skills_dir()),
        "targets": {
            relative_path: {
                "path": str(target),
                "installed": target.exists(),
            }
            for relative_path, target in targets.items()
        },
    }


def config_path() -> Path:
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / COMMAND_NAME / CONFIG_FILENAME
    return Path.home() / ".config" / COMMAND_NAME / CONFIG_FILENAME


def project_config_path(start: str | Path | None = None) -> Path | None:
    if os.environ.get(CONFIG_ENV_VAR):
        return None
    current = Path.cwd() if start is None else Path(start).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        candidate = directory / PROJECT_CONFIG
        if candidate.is_file():
            return candidate
    return None


def read_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CliError(f"Could not parse config file {path}: {exc}", "invalid_config") from exc
    if not isinstance(loaded, dict):
        raise CliError(f"Config file {path} must contain a JSON object", "invalid_config")
    harness = loaded.get("default_agent_harness")
    if harness is not None and harness not in AGENT_HARNESSES:
        raise CliError(
            f"Unsupported default_agent_harness in config: {harness}",
            "invalid_config",
        )
    profiles = loaded.get("profiles")
    if profiles is not None and not isinstance(profiles, dict):
        raise CliError("Config profiles must be a JSON object", "invalid_config")
    return loaded


def merge_configs(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = {**base, **overlay}
    profiles = {
        **(base.get("profiles") or {}),
        **(overlay.get("profiles") or {}),
    }
    if profiles:
        merged["profiles"] = profiles
    elif "profiles" in merged:
        merged.pop("profiles")
    return merged


def read_config() -> dict[str, Any]:
    config = read_config_file(config_path())
    local_path = project_config_path()
    if local_path:
        config = merge_configs(config, read_config_file(local_path))
    return config


def write_config(config: dict[str, Any]) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def effective_agent_harness(config: dict[str, Any] | None = None) -> str:
    config = read_config() if config is None else config
    return config.get("default_agent_harness") or DEFAULT_AGENT_HARNESS


def config_payload(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = read_config() if config is None else config
    local_path = project_config_path()
    return {
        "path": str(local_path or config_path()),
        "global_path": str(config_path()),
        "project_path": str(local_path) if local_path else None,
        "default_agent_harness": config.get("default_agent_harness"),
        "effective_agent_harness": effective_agent_harness(config),
        "profiles": config.get("profiles") or {},
    }


def normalize_profile_languages(value: str | list[str] | tuple[str, ...]) -> list[str]:
    return normalize_languages(value)


def profile_from_args(args: argparse.Namespace) -> dict[str, Any]:
    profile: dict[str, Any] = {}
    for key in (
        "style",
        "template",
        "agent_harness",
        "cache_dir",
        "transcript",
        "transcript_format",
        "out",
        "bundle_dir",
        "out_dir",
        "manifest",
    ):
        value = getattr(args, key, None)
        if value:
            profile[key] = value
    if getattr(args, "langs", None):
        profile["langs"] = normalize_profile_languages(args.langs)
    if getattr(args, "focus", None):
        profile["focus"] = args.focus
    for key in ("front_matter", "timestamps", "resume", "stdout"):
        value = getattr(args, key, None)
        if value is not None:
            profile[key] = bool(value)
    if getattr(args, "chunk_chars", None) is not None:
        profile["chunk_chars"] = args.chunk_chars
    return profile


def get_profile(config: dict[str, Any], name: str) -> dict[str, Any]:
    profiles = config.get("profiles") or {}
    profile = profiles.get(name)
    if not isinstance(profile, dict):
        raise CliError(f"Profile not found: {name}", "profile_not_found")
    return profile


def apply_profile(args: argparse.Namespace) -> None:
    profile_name = getattr(args, "profile", None)
    profile = get_profile(read_config(), profile_name) if profile_name else {}
    if getattr(args, "style", None) is None:
        args.style = profile.get("style") or "notes"
    if getattr(args, "template", None) is None:
        args.template = profile.get("template")
    if not getattr(args, "focus", None) and profile.get("focus"):
        args.focus = list(profile["focus"])
    if hasattr(args, "langs") and getattr(args, "langs", None) is None and profile.get("langs"):
        args.langs = ",".join(profile["langs"])
    for key in (
        "cache_dir",
        "transcript",
        "transcript_format",
        "out",
        "bundle_dir",
        "out_dir",
        "manifest",
    ):
        if hasattr(args, key) and getattr(args, key, None) is None and profile.get(key):
            setattr(args, key, profile[key])
    if (
        hasattr(args, "agent_harness")
        and getattr(args, "agent_harness", None) is None
        and profile.get("agent_harness")
    ):
        args.agent_harness = profile["agent_harness"]
    for key in ("front_matter", "timestamps", "resume", "stdout"):
        if hasattr(args, key) and getattr(args, key, None) is None:
            setattr(args, key, bool(profile.get(key, False)))
    if hasattr(args, "chunk_chars") and getattr(args, "chunk_chars", None) is None:
        args.chunk_chars = int(profile.get("chunk_chars", 0) or 0)
    if hasattr(args, "transcript_format") and getattr(args, "transcript_format", None) is None:
        args.transcript_format = "text"


def agent_harness_status() -> dict[str, Any]:
    codex_path = command_path("codex")
    opencode_path = command_path("opencode")
    return {
        "default": effective_agent_harness(),
        "built_in_default": DEFAULT_AGENT_HARNESS,
        "harnesses": {
            "codex": {
                "available": codex_path is not None,
                "path": codex_path,
                "version": command_output(["codex", "--version"]) if codex_path else None,
                "auth_status": command_output(["codex", "login", "status"]) if codex_path else None,
                "command": "codex exec",
            },
            "opencode": {
                "available": opencode_path is not None,
                "path": opencode_path,
                "version": command_output(["opencode", "--version"]) if opencode_path else None,
                "auth_status": command_output(["opencode", "auth", "list"])
                if opencode_path
                else None,
                "command": "opencode run",
            },
        },
    }


def install_bin_dir() -> Path:
    return Path.home() / ".local" / "bin"


def local_install_command() -> str:
    if sys.platform == "win32":
        return ".\\install-local.ps1"
    return "sh ./install-local.sh"


def doctor_payload() -> dict[str, Any]:
    install_dir = str(install_bin_dir())
    path_parts = [
        str(Path(part).resolve())
        for part in os.environ.get("PATH", "").split(os.pathsep)
        if part
    ]
    harness_status = agent_harness_status()
    codex_status = harness_status["harnesses"]["codex"]
    opencode_status = harness_status["harnesses"]["opencode"]
    return {
        "command": COMMAND_NAME,
        "version": VERSION,
        "python": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
        },
        "codex": {
            "available": codex_status["available"],
            "path": codex_status["path"],
            "version": codex_status["version"],
            "auth_status": codex_status["auth_status"],
            "auth": "reuses saved Codex CLI authentication",
        },
        "opencode": {
            "available": opencode_status["available"],
            "path": opencode_status["path"],
            "version": opencode_status["version"],
            "auth_status": opencode_status["auth_status"],
            "auth": "uses the configured OpenCode auth providers",
        },
        "agent_harness": harness_status,
        "youtube": {
            "method": "youtube-transcript-api",
            "yt_dlp_required": False,
        },
        "install": {
            "wrapper_dir": install_dir,
            "wrapper_dir_on_path": str(Path(install_dir).resolve()) in path_parts,
            "resolved_command": command_path(COMMAND_NAME),
            "local_install_command": local_install_command(),
        },
        "config": config_payload(),
        "skills": skills_payload(),
        "lifecycle": lifecycle_steps(),
    }


def lifecycle_steps() -> list[dict[str, str]]:
    return [
        {
            "step": "check",
            "command": "yt-scribe doctor",
            "purpose": "Verify Python, agent harness, config, and PATH setup.",
        },
        {
            "step": "inspect",
            "command": "yt-scribe inspect <youtube-url>",
            "purpose": "Resolve the video and list available caption tracks.",
        },
        {
            "step": "fetch",
            "command": "yt-scribe fetch <youtube-url> --out transcript.txt",
            "purpose": "Save the raw transcript without using an agent harness.",
        },
        {
            "step": "polish",
            "command": "yt-scribe polish transcript.txt --style notes --out notes.md",
            "purpose": "Use the configured agent harness on an existing transcript file.",
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
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    codex = command_path("codex")
    if not codex:
        raise CliError("codex was not found on PATH", "codex_missing")

    with tempfile.TemporaryDirectory(prefix="yt-scribe-") as tmp_dir:
        final_path = Path(tmp_dir) / "final.md"
        command = command_invocation(
            codex,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--output-last-message",
            str(final_path),
        )
        if cwd:
            command.extend(["--cd", str(Path(cwd).expanduser().resolve())])
        if model:
            command.extend(["--model", model])
        command.append(instruction)

        if progress:
            with progress.wait("Running Codex polisher"):
                result = subprocess.run(
                    command,
                    input=transcript_text,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=False,
                )
        else:
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
            "harness": "codex",
            "codex_stderr_tail": result.stderr[-2000:],
            "text": polished,
        }


def run_opencode_polish(
    transcript_text: str,
    instruction: str,
    out_path: str | None,
    model: str | None,
    cwd: str | None,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    opencode = command_path("opencode")
    if not opencode:
        raise CliError("opencode was not found on PATH", "opencode_missing")

    with tempfile.TemporaryDirectory(prefix="yt-scribe-") as tmp_dir:
        transcript_path = Path(tmp_dir) / "transcript.txt"
        transcript_path.write_text(transcript_text, encoding="utf-8")
        command = command_invocation(
            opencode,
            "run",
            instruction,
            "--file",
            str(transcript_path),
            "--format",
            "json",
        )
        if cwd:
            command.extend(["--dir", str(Path(cwd).expanduser().resolve())])
        if model:
            command.extend(["--model", model])

        if progress:
            with progress.wait("Running OpenCode polisher"):
                result = subprocess.run(
                    command,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=False,
                )
        else:
            result = subprocess.run(
                command,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
        if result.returncode != 0:
            raise CliError(
                "opencode run failed",
                "opencode_run_failed",
                {
                    "returncode": result.returncode,
                    "stderr_tail": result.stderr[-4000:],
                },
            )

        polished = parse_opencode_run_output(result.stdout).strip() + "\n"
        written = write_text(out_path, polished)
        return {
            "output_path": written,
            "chars": len(polished),
            "harness": "opencode",
            "opencode_stderr_tail": result.stderr[-2000:],
            "text": polished,
        }


def parse_opencode_run_output(output: str) -> str:
    text_parts: list[str] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = event.get("part")
        if event.get("type") == "text" and isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str):
                text_parts.append(text)
    if text_parts:
        return "".join(text_parts)
    return output


def run_agent_polish(
    transcript_text: str,
    instruction: str,
    out_path: str | None,
    model: str | None,
    cwd: str | None,
    harness: str,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    if harness == "codex":
        return run_codex_polish(transcript_text, instruction, out_path, model, cwd, progress)
    if harness == "opencode":
        return run_opencode_polish(transcript_text, instruction, out_path, model, cwd, progress)
    raise CliError(f"Unsupported agent harness: {harness}", "unsupported_agent_harness")


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


def style_instruction(style: str, harness: str) -> str:
    harness_file = HARNESS_INSTRUCTIONS[harness]
    delivery = TRANSCRIPT_DELIVERY[harness]
    return (
        f"Use the {INNER_POLISHER_SKILL} skill if it is available. "
        f"For this run, follow its {harness_file} instructions. "
        f"{STYLE_INSTRUCTIONS[style]} "
        f"The transcript is provided by yt-scribe on {delivery}."
    )


def custom_instruction_parts(args: argparse.Namespace) -> tuple[list[str], list[str]]:
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
        content = Path(focus_file).expanduser().read_text(encoding="utf-8").strip()
        if content:
            parts.append(content)
            sources.append("--focus-file")

    return parts, sources


def resolve_instruction(args: argparse.Namespace, harness: str) -> PolishInstruction:
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
        text = Path(args.prompt_file).expanduser().read_text(encoding="utf-8").strip()
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


def read_instruction(args: argparse.Namespace, harness: str) -> str:
    return resolve_instruction(args, harness).text


def limit_text(text: str, max_chars: int) -> str:
    if max_chars and len(text) > max_chars:
        return text[:max_chars]
    return text


def selected_agent_harness(args: argparse.Namespace) -> str:
    return args.agent_harness or effective_agent_harness()


def harness_label(harness: str) -> str:
    return {"codex": "Codex", "opencode": "OpenCode"}.get(harness, harness)


def handle_args(args: argparse.Namespace) -> int:
    if args.command in {"polish", "run", "batch"}:
        apply_profile(args)

    if args.command == "setup":
        payload = {"ok": True, "setup": setup_payload()}
        doctor = payload["setup"]["doctor"]
        harnesses = doctor["agent_harness"]["harnesses"]
        text = (
            f"Installed yt-scribe support files:\n"
            f"  agent skills: {payload['setup']['skills']['agents_skills_dir']}\n"
            f"Agent harnesses:\n"
            f"  default: {doctor['agent_harness']['default']}\n"
            f"  codex: {'found' if harnesses['codex']['available'] else 'not found'}\n"
            f"  opencode: {'found' if harnesses['opencode']['available'] else 'not found'}\n"
            f"Command on PATH: {'yes' if doctor['install']['resolved_command'] else 'no'}\n"
            f"Next:\n"
            f"  yt-scribe run \"<youtube-url>\"\n"
        )
        emit(payload, args.json, text)
        return 0

    if args.command == "install-skills":
        payload = {"ok": True, "skills": install_skills()}
        text = (
            f"Installed skills:\n"
            f"  agent skills: {payload['skills']['agents_skills_dir']}\n"
        )
        emit(payload, args.json, text)
        return 0

    if args.command == "init-project":
        result = init_project(args.dir, args.profile)
        payload = {"ok": True, "init_project": result}
        text = (
            "Initialized yt-scribe project guidance:\n"
            f"  dir: {result['dir']}\n"
            f"  guidance: {result['guidance_path']}\n"
            f"  config: {result['config_path']}\n"
        )
        emit(payload, args.json, text)
        return 0

    if args.command == "config":
        config = read_config_file(config_path())
        effective_config = read_config()
        if args.config_command == "set":
            config["default_agent_harness"] = args.value
            write_config(config)
            effective_config = read_config()
        elif args.config_command == "unset":
            config.pop("default_agent_harness", None)
            write_config(config)
            effective_config = read_config()
        elif args.config_command == "profile":
            profiles = config.setdefault("profiles", {})
            if args.profile_command == "set":
                profiles[args.name] = profile_from_args(args)
                write_config(config)
                effective_config = read_config()
            elif args.profile_command == "remove":
                profiles.pop(args.name, None)
                write_config(config)
                effective_config = read_config()
            elif args.profile_command == "get":
                profile = get_profile(effective_config, args.name)
                payload = {
                    "ok": True,
                    "profile": {"name": args.name, "values": profile},
                    "config": config_payload(effective_config),
                }
                emit(payload, args.json)
                return 0

        payload = {"ok": True, "config": config_payload(effective_config)}
        text = (
            f"config: {payload['config']['path']}\n"
            f"default_agent_harness: {payload['config']['default_agent_harness']}\n"
            f"effective_agent_harness: {payload['config']['effective_agent_harness']}\n"
        )
        emit(payload, args.json, text)
        return 0

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
        proxy_config = proxy_config_from_args(args)
        try:
            tracks = list_transcript_tracks(video_id, proxy_config=proxy_config)
        except CliError:
            tracks = fetch_raw_caption_tracks(video_id, proxy_config)
        payload = {
            "ok": True,
            "video": {
                "id": video_id,
                "url": canonical_watch_url(video_id),
                "tracks": [track.public_dict() for track in tracks],
            },
        }
        if args.brief:
            manual_languages = [
                track.language_code for track in tracks if not track.auto_generated
            ]
            auto_generated_languages = [
                track.language_code for track in tracks if track.auto_generated
            ]
            payload = {
                "ok": True,
                "video": {
                    "id": video_id,
                    "url": canonical_watch_url(video_id),
                    "has_captions": bool(tracks),
                    "caption_tracks": len(tracks),
                    "languages": [track.language_code for track in tracks],
                    "manual_languages": manual_languages,
                    "auto_generated_languages": auto_generated_languages,
                },
            }
            language_text = ", ".join(payload["video"]["languages"]) or "none"
            track_word = "track" if len(tracks) == 1 else "tracks"
            text = (
                f"{canonical_watch_url(video_id)}\n"
                f"captions: {'yes' if tracks else 'no'} ({len(tracks)} {track_word})\n"
                f"languages: {language_text}\n"
            )
            emit(payload, args.json, text)
            return 0
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
        transcript, cache = load_or_fetch_transcript(
            args.url,
            requested_languages(args),
            cache_dir_from_args(args),
            args.resume,
            proxy_config_from_args(args),
        )
        rendered = render_transcript(transcript, args.format)
        output_path = write_text(args.out, rendered)
        payload = {
            "ok": True,
            "fetch": {
                "video_id": transcript["video_id"],
                "url": transcript["url"],
                "language": transcript["language"],
                "requested_languages": transcript["requested_languages"],
                "track": transcript["track"],
                "segments": len(transcript["segments"]),
                "chars": len(transcript["text"]),
                "format": args.format,
                "output_path": output_path,
                "cache": cache,
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
        progress = ProgressReporter(not args.json)
        progress.message(f"Reading transcript from {Path(args.file).expanduser()}")
        transcript_text = Path(args.file).expanduser().read_text(encoding="utf-8")
        transcript_text = limit_text(transcript_text, args.max_chars)
        progress.message(f"Loaded transcript ({len(transcript_text)} chars)")
        out_path = (
            args.out
            if args.stdout
            else args.out or str(default_polish_output_path(args.file, args.style))
        )
        harness = selected_agent_harness(args)
        instruction = resolve_instruction(args, harness)
        progress.message(f"Using {harness_label(harness)}")
        result = run_agent_polish(
            transcript_text=transcript_text,
            instruction=instruction.text,
            out_path=out_path,
            model=args.model,
            cwd=args.cd,
            harness=harness,
            progress=progress,
        )
        payload = {
            "ok": True,
            "polish": {
                "input_path": str(Path(args.file).expanduser().resolve()),
                "style": args.style,
                "instruction_mode": instruction.mode,
                "instruction_sources": instruction.sources,
                "timestamp_grounding": args.timestamps,
                "agent_harness": result["harness"],
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
        progress = ProgressReporter(not args.json)
        progress.message(f"Fetching transcript for {extract_video_id(args.url)}")
        transcript, cache = load_or_fetch_transcript(
            args.url,
            requested_languages(args),
            cache_dir_from_args(args),
            args.resume,
            proxy_config_from_args(args),
        )
        segment_count = len(transcript["segments"])
        segment_word = "segment" if segment_count == 1 else "segments"
        progress.message(
            f"Fetched {segment_count} transcript {segment_word} "
            f"({len(transcript['text'])} chars)"
        )
        bundle = bundle_paths(args.bundle_dir)
        transcript_target = args.transcript or (bundle["transcript"] if bundle else None)
        rendered = render_transcript(transcript, args.transcript_format)
        transcript_path = write_text(transcript_target, rendered)
        if transcript_path:
            progress.message(f"Wrote raw transcript to {transcript_path}")
        transcript_text = (
            render_timestamped_transcript(transcript) if args.timestamps else transcript["text"]
        )
        original_transcript_chars = len(transcript_text)
        transcript_text = limit_text(transcript_text, args.max_chars)
        if args.max_chars and original_transcript_chars > len(transcript_text):
            progress.message(f"Truncated transcript to {len(transcript_text)} chars")
        out_path = (
            args.out
            if args.stdout
            else args.out
            or (bundle["polished"] if bundle else None)
            or str(default_run_output_path(transcript["video_id"], args.style))
        )
        harness = selected_agent_harness(args)
        instruction = resolve_instruction(args, harness)
        progress.message(f"Using {harness_label(harness)}")
        if args.chunk_chars:
            chunks = split_transcript_chunks(transcript, args.chunk_chars, args.timestamps)
            result = run_chunked_agent_polish(
                chunks=chunks,
                instruction=instruction.text,
                out_path=out_path,
                model=args.model,
                cwd=args.cd,
                harness=harness,
                chunk_chars=args.chunk_chars,
                resume=args.resume,
                progress=progress,
            )
        else:
            result = run_agent_polish(
                transcript_text=transcript_text,
                instruction=instruction.text,
                out_path=out_path,
                model=args.model,
                cwd=args.cd,
                harness=harness,
                progress=progress,
            )
            result["chunking"] = chunking_disabled_payload()
        front_matter_data = None
        if args.front_matter:
            front_matter_data = run_front_matter_data(
                transcript,
                args.style,
                instruction,
                result["harness"],
            )
            result = apply_output_prefix(
                result,
                render_front_matter(front_matter_data),
            )
        payload = {
            "ok": True,
            "run": {
                "video_id": transcript["video_id"],
                "url": transcript["url"],
                "language": transcript["language"],
                "requested_languages": transcript.get(
                    "requested_languages",
                    requested_languages(args),
                ),
                "segments": len(transcript["segments"]),
                "style": args.style,
                "instruction_mode": instruction.mode,
                "instruction_sources": instruction.sources,
                "timestamp_grounding": args.timestamps,
                "agent_harness": result["harness"],
                "transcript_path": transcript_path,
                "output_path": result["output_path"],
                "chars": result["chars"],
                "front_matter": args.front_matter,
                "front_matter_data": front_matter_data,
                "chunking": result["chunking"],
                "cache": cache,
                "bundle": None,
            },
        }
        if bundle:
            metadata_path = write_bundle_metadata(
                bundle["metadata"],
                {
                    "video_id": transcript["video_id"],
                    "url": transcript["url"],
                    "language": transcript["language"],
                    "requested_languages": transcript.get(
                        "requested_languages",
                        requested_languages(args),
                    ),
                    "style": args.style,
                    "template": args.template,
                    "instruction_mode": instruction.mode,
                    "instruction_sources": instruction.sources,
                    "timestamp_grounding": args.timestamps,
                    "agent_harness": result["harness"],
                    "transcript_path": transcript_path,
                    "output_path": result["output_path"],
                    "front_matter_enabled": args.front_matter,
                    "front_matter_data": front_matter_data,
                    "chunking": result["chunking"],
                    "cache": cache,
                    "records": {
                        "manifest": None,
                        "verification": None,
                    },
                },
            )
            payload["run"]["bundle"] = {
                "dir": bundle["dir"],
                "transcript": transcript_path,
                "polished": result["output_path"],
                "metadata": metadata_path,
                "records": {
                    "manifest": None,
                    "verification": None,
                },
            }
        if args.json:
            emit(payload, True)
        elif result["output_path"]:
            emit(payload, False, f"Wrote polished transcript to {result['output_path']}\n")
        else:
            emit(payload, False, result["text"])
        return 0

    if args.command == "batch":
        progress = ProgressReporter(not args.json)
        if not args.out_dir:
            raise CliError("batch requires --out-dir or a profile out_dir", "missing_out_dir")
        if not args.manifest:
            raise CliError("batch requires --manifest or a profile manifest", "missing_manifest")
        urls = read_batch_urls(args.file)
        out_dir = Path(args.out_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = Path(args.manifest).expanduser().resolve()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        items: list[dict[str, Any]] = []
        languages = requested_languages(args)
        cache_dir = cache_dir_from_args(args)
        proxy_config = proxy_config_from_args(args)
        batch_inputs = expand_batch_items(urls, proxy_config)
        harness = selected_agent_harness(args)
        instruction = resolve_instruction(args, harness)

        for index, batch_input in enumerate(batch_inputs, start=1):
            url = batch_input["url"]
            source_metadata = {
                key: value
                for key, value in batch_input.items()
                if key in {"playlist_url", "playlist_id"}
            }
            progress.message(f"Batch item {index}/{len(batch_inputs)}: {url}")
            try:
                playlist_error = batch_input.get("playlist_error")
                if playlist_error:
                    raise CliError(
                        playlist_error.get("message") or "Playlist expansion failed",
                        playlist_error.get("code") or "playlist_error",
                        {
                            key: value
                            for key, value in playlist_error.items()
                            if key not in {"code", "message"}
                        },
                    )
                video_id = extract_video_id(url)
                output_path = batch_output_path(out_dir, video_id, args.style)
                if args.resume and output_path.is_file():
                    items.append(
                        {
                            "url": url,
                            **source_metadata,
                            "video_id": video_id,
                            "status": "skipped",
                            "output_path": str(output_path),
                            "reason": "output_exists",
                        }
                    )
                    continue

                transcript, cache = load_or_fetch_transcript(
                    url,
                    languages,
                    cache_dir,
                    args.resume,
                    proxy_config,
                )
                output_path = batch_output_path(out_dir, transcript["video_id"], args.style)
                if args.chunk_chars:
                    chunks = split_transcript_chunks(transcript, args.chunk_chars, args.timestamps)
                    result = run_chunked_agent_polish(
                        chunks=chunks,
                        instruction=instruction.text,
                        out_path=str(output_path),
                        model=args.model,
                        cwd=args.cd,
                        harness=harness,
                        chunk_chars=args.chunk_chars,
                        resume=args.resume,
                        progress=progress,
                    )
                else:
                    result = run_agent_polish(
                        transcript_text=limit_text(
                            render_timestamped_transcript(transcript)
                            if args.timestamps
                            else transcript["text"],
                            args.max_chars,
                        ),
                        instruction=instruction.text,
                        out_path=str(output_path),
                        model=args.model,
                        cwd=args.cd,
                        harness=harness,
                        progress=progress,
                    )
                    result["chunking"] = chunking_disabled_payload()
                if args.front_matter:
                    result = apply_output_prefix(
                        result,
                        run_front_matter(transcript, args.style, instruction, result["harness"]),
                    )
                items.append(
                    {
                        "url": transcript["url"],
                        **source_metadata,
                        "video_id": transcript["video_id"],
                        "status": "succeeded",
                        "language": transcript["language"],
                        "requested_languages": transcript.get("requested_languages", languages),
                        "agent_harness": result["harness"],
                        "output_path": result["output_path"],
                        "transcript_path": None,
                        "chars": result["chars"],
                        "timestamp_grounding": args.timestamps,
                        "chunking": result["chunking"],
                        "cache": cache,
                    }
                )
            except CliError as exc:
                items.append(
                    {
                        "url": url,
                        **source_metadata,
                        "status": "failed",
                        "error": {
                            "code": exc.code,
                            "message": str(exc),
                            **exc.details,
                        },
                    }
                )

        succeeded = sum(1 for item in items if item["status"] == "succeeded")
        failed = sum(1 for item in items if item["status"] == "failed")
        skipped = sum(1 for item in items if item["status"] == "skipped")
        manifest = {
            "ok": failed == 0,
            "source_path": str(Path(args.file).expanduser().resolve()),
            "out_dir": str(out_dir),
            "manifest_path": str(manifest_path),
            "style": args.style,
            "timestamp_grounding": args.timestamps,
            "chunking": {
                "enabled": bool(args.chunk_chars),
                "chunk_chars": args.chunk_chars,
            },
            "requested_languages": languages,
            "succeeded": succeeded,
            "failed": failed,
            "skipped": skipped,
            "items": items,
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        payload = {"ok": failed == 0, "batch": manifest}
        emit(payload, args.json, f"Wrote batch manifest to {manifest_path}\n")
        return 0 if failed == 0 else 1

    if args.command == "verify":
        polished_path = Path(args.file).expanduser().resolve()
        transcript_path = Path(args.transcript).expanduser().resolve()
        result = verify_polished_file(polished_path, transcript_path)
        ok = result["summary"]["unsupported"] == 0
        payload = {
            "ok": ok,
            "verify": {
                "polished_path": str(polished_path),
                "transcript_path": str(transcript_path),
                **result,
            },
        }
        emit(payload, args.json, render_verification(result))
        return 0 if ok else 1

    if args.command == "raw":
        video_id = extract_video_id(args.url)
        proxy_config = proxy_config_from_args(args)
        tracks = fetch_raw_caption_tracks(video_id, proxy_config)
        track = choose_track(tracks, args.lang)
        url = timedtext_url(track, args.format)
        if args.body:
            body = http_get(url, proxy_config).decode("utf-8", errors="replace")
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
        default=None,
        help="polished output style, default: notes",
    )
    parser.add_argument(
        "--template",
        choices=sorted(TEMPLATE_INSTRUCTIONS),
        help="safe output structure to compose with the selected style",
    )
    parser.add_argument("--out", help="write polished output to this file")
    parser.add_argument(
        "--stdout",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="print polished output instead of writing a file",
    )
    parser.add_argument(
        "--focus",
        action="append",
        help=(
            "add custom instructions for this run; can be passed more than once "
            "and overrides --style where they conflict"
        ),
    )
    parser.add_argument(
        "--focus-file",
        action="append",
        help="read additional custom instructions from a file",
    )
    parser.add_argument(
        "--instruction",
        help="advanced: replace the entire built-in polishing prompt",
    )
    parser.add_argument(
        "--prompt-file",
        help="advanced: read the full replacement prompt from a file",
    )
    parser.add_argument(
        "--timestamps",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="ask the polisher to preserve useful timestamp anchors",
    )
    parser.add_argument(
        "--agent-harness",
        choices=AGENT_HARNESSES,
        help=(
            "agent harness for polishing; defaults to config "
            f"or {DEFAULT_AGENT_HARNESS}"
        ),
    )
    parser.add_argument("--model", help="optional agent model override")
    parser.add_argument("--profile", help="named workflow profile from config")
    parser.add_argument("--cd", help="working directory to pass to the agent harness")
    parser.add_argument(
        "--max-chars",
        type=int,
        default=0,
        help="truncate transcript before polish",
    )


def add_proxy_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--http-proxy",
        help=f"advanced: HTTP proxy URL for YouTube requests; env: {HTTP_PROXY_ENV_VAR}",
    )
    parser.add_argument(
        "--https-proxy",
        help=f"advanced: HTTPS proxy URL for YouTube requests; env: {HTTPS_PROXY_ENV_VAR}",
    )


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
        description="Human-first CLI for turning YouTube links into agent-polished notes.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", help="emit stable JSON output")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="command")

    config_parser = subparsers.add_parser("config", help="show or edit yt-scribe config")
    config_subparsers = config_parser.add_subparsers(dest="config_command", metavar="command")
    config_set_parser = config_subparsers.add_parser("set", help="set a config value")
    config_set_parser.add_argument(
        "key",
        choices=["default-agent-harness"],
        help="config key to set",
    )
    config_set_parser.add_argument("value", choices=AGENT_HARNESSES)
    config_unset_parser = config_subparsers.add_parser("unset", help="clear a config value")
    config_unset_parser.add_argument(
        "key",
        choices=["default-agent-harness"],
        help="config key to clear",
    )
    config_profile_parser = config_subparsers.add_parser(
        "profile",
        help="create, inspect, or remove named workflow profiles",
    )
    profile_subparsers = config_profile_parser.add_subparsers(
        dest="profile_command",
        required=True,
        metavar="command",
    )
    profile_set_parser = profile_subparsers.add_parser("set", help="create or replace a profile")
    profile_set_parser.add_argument("name", help="profile name")
    profile_set_parser.add_argument("--style", choices=sorted(STYLE_INSTRUCTIONS))
    profile_set_parser.add_argument("--langs", help="ordered comma-separated caption languages")
    profile_set_parser.add_argument("--focus", action="append", help="profile focus instruction")
    profile_set_parser.add_argument("--template", choices=sorted(TEMPLATE_INSTRUCTIONS))
    profile_set_parser.add_argument(
        "--front-matter",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    profile_set_parser.add_argument(
        "--timestamps",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    profile_set_parser.add_argument("--cache-dir", help="advanced: transcript cache directory")
    profile_set_parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="advanced: reuse cached or existing outputs where supported",
    )
    profile_set_parser.add_argument("--transcript", help="run: also write transcript here")
    profile_set_parser.add_argument(
        "--transcript-format",
        choices=["text", "json", "srt"],
        help="run: raw transcript format",
    )
    profile_set_parser.add_argument("--out", help="polish/run: write polished output here")
    profile_set_parser.add_argument(
        "--stdout",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="polish/run: print polished output instead of writing a file",
    )
    profile_set_parser.add_argument("--bundle-dir", help="run: bundle output directory")
    profile_set_parser.add_argument("--out-dir", help="batch: directory for polished outputs")
    profile_set_parser.add_argument("--manifest", help="batch: manifest JSON path")
    profile_set_parser.add_argument("--chunk-chars", type=int)
    profile_set_parser.add_argument("--agent-harness", choices=AGENT_HARNESSES)
    profile_get_parser = profile_subparsers.add_parser("get", help="show a profile")
    profile_get_parser.add_argument("name", help="profile name")
    profile_remove_parser = profile_subparsers.add_parser("remove", help="remove a profile")
    profile_remove_parser.add_argument("name", help="profile name")

    subparsers.add_parser("doctor", help="check Python, agent harnesses, skills, and PATH setup")
    subparsers.add_parser(
        "setup",
        help="install support files and print the next command",
    )
    subparsers.add_parser(
        "install-skills",
        help="install global yt-scribe agent skills",
    )
    init_project_parser = subparsers.add_parser(
        "init-project",
        help="write local yt-scribe project guidance",
    )
    init_project_parser.add_argument(
        "--dir",
        default=".",
        help="project directory to initialize, default: current directory",
    )
    init_project_parser.add_argument(
        "--profile",
        help="optional starter profile name for .yt-scribe/config.json",
    )
    subparsers.add_parser("lifecycle", help="print the recommended workflow")

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="resolve a video and list caption tracks",
    )
    inspect_parser.add_argument("url", help="YouTube URL or 11-character video ID")
    inspect_parser.add_argument(
        "--brief",
        action="store_true",
        help="print the smallest useful caption availability summary",
    )
    add_proxy_flags(inspect_parser)

    fetch_parser = subparsers.add_parser("fetch", help="download the transcript without an agent")
    fetch_parser.add_argument("url", help="YouTube URL or 11-character video ID")
    fetch_parser.add_argument("--lang", default="en", help="caption language code, default: en")
    fetch_parser.add_argument(
        "--langs",
        help="ordered comma-separated caption language fallback list",
    )
    fetch_parser.add_argument("--cache-dir", help="advanced: transcript cache directory")
    fetch_parser.add_argument(
        "--resume",
        action="store_true",
        help="advanced: reuse a cached transcript when available",
    )
    fetch_parser.add_argument("--format", choices=["text", "json", "srt"], default="text")
    fetch_parser.add_argument("--out", help="write transcript to this file")
    add_proxy_flags(fetch_parser)

    polish_parser = subparsers.add_parser(
        "polish",
        help="polish an existing transcript with the configured agent",
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
    run_parser.add_argument(
        "--langs",
        help="ordered comma-separated caption language fallback list",
    )
    run_parser.add_argument("--cache-dir", help="advanced: transcript cache directory")
    run_parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="advanced: reuse a cached transcript when available",
    )
    run_parser.add_argument("--transcript", help="also write the raw transcript to this file")
    run_parser.add_argument("--transcript-format", choices=["text", "json", "srt"], default=None)
    run_parser.add_argument(
        "--bundle-dir",
        help="advanced: write transcript, polished output, and metadata under this directory",
    )
    run_parser.add_argument(
        "--front-matter",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="prepend factual YAML front matter to polished markdown output",
    )
    run_parser.add_argument(
        "--chunk-chars",
        type=int,
        default=None,
        help="advanced: polish transcript chunks of roughly this many characters",
    )
    add_proxy_flags(run_parser)
    add_polish_flags(run_parser)

    batch_parser = subparsers.add_parser(
        "batch",
        help="advanced: process a plain text list of YouTube URLs",
    )
    batch_parser.add_argument("file", help="plain text file with one YouTube URL or ID per line")
    batch_parser.add_argument("--out-dir", help="directory for polished outputs")
    batch_parser.add_argument("--manifest", help="write batch manifest JSON here")
    batch_parser.add_argument("--lang", default="en", help="caption language code, default: en")
    batch_parser.add_argument(
        "--langs",
        help="ordered comma-separated caption language fallback list",
    )
    batch_parser.add_argument("--cache-dir", help="advanced: transcript cache directory")
    batch_parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="advanced: skip existing outputs and reuse cached transcripts",
    )
    batch_parser.add_argument(
        "--front-matter",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="prepend factual YAML front matter to polished markdown output",
    )
    batch_parser.add_argument(
        "--chunk-chars",
        type=int,
        default=None,
        help="advanced: polish transcript chunks of roughly this many characters",
    )
    add_proxy_flags(batch_parser)
    add_polish_flags(batch_parser)

    verify_parser = subparsers.add_parser(
        "verify",
        help="compare polished output with a transcript artifact",
    )
    verify_parser.add_argument("file", help="polished output file to verify")
    verify_parser.add_argument(
        "--transcript",
        required=True,
        help="raw transcript text, timestamped text, or transcript JSON artifact",
    )

    raw_parser = subparsers.add_parser("raw", help="read-only timedtext escape hatch")
    raw_parser.add_argument("url", help="YouTube URL or 11-character video ID")
    raw_parser.add_argument("--lang", default="en")
    raw_parser.add_argument("--format", choices=["json3", "srv3"], default="json3")
    add_proxy_flags(raw_parser)
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
