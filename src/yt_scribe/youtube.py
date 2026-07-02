"""YouTube source handling for yt-scribe."""

from __future__ import annotations

import argparse
import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig

from . import CliError

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)
DEEP_WORKFLOW_DURATION_THRESHOLD_SECONDS = 45 * 60

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


def fetch_watch_player_response(
    video_id: str,
    proxy_config: GenericProxyConfig | None = None,
) -> dict[str, Any]:
    page = http_get(canonical_watch_url(video_id), proxy_config).decode(
        "utf-8",
        errors="replace",
    )
    return extract_json_object(page, "ytInitialPlayerResponse")


def extract_video_duration_seconds(player_response: dict[str, Any]) -> int | None:
    details = player_response.get("videoDetails")
    if not isinstance(details, dict):
        return None
    raw_duration = details.get("lengthSeconds")
    if isinstance(raw_duration, bool) or raw_duration is None:
        return None
    try:
        duration = int(str(raw_duration))
    except ValueError:
        return None
    return duration if duration >= 0 else None


def extract_video_title(player_response: dict[str, Any]) -> str | None:
    details = player_response.get("videoDetails")
    if not isinstance(details, dict):
        return None
    title = str(details.get("title") or "").strip()
    return title or None


def fetch_video_duration_seconds(
    video_id: str,
    proxy_config: GenericProxyConfig | None = None,
) -> int | None:
    try:
        player_response = fetch_watch_player_response(video_id, proxy_config)
    except (CliError, json.JSONDecodeError):
        return None
    return extract_video_duration_seconds(player_response)


def fetch_video_title(
    video_id: str,
    proxy_config: GenericProxyConfig | None = None,
) -> str | None:
    try:
        player_response = fetch_watch_player_response(video_id, proxy_config)
    except (CliError, json.JSONDecodeError):
        return None
    return extract_video_title(player_response)


def select_run_workflow(
    requested_workflow: str,
    duration_seconds: int | None,
) -> dict[str, int | str | None]:
    if requested_workflow == "quick":
        selected = "quick"
        reason = "explicit_quick"
    elif requested_workflow == "deep":
        selected = "deep"
        reason = "explicit_deep"
    elif duration_seconds is None:
        selected = "quick"
        reason = "duration_unknown"
    elif duration_seconds >= DEEP_WORKFLOW_DURATION_THRESHOLD_SECONDS:
        selected = "deep"
        reason = "duration_at_or_above_threshold"
    else:
        selected = "quick"
        reason = "duration_below_threshold"

    return {
        "workflow": selected,
        "workflow_requested": requested_workflow,
        "workflow_reason": reason,
        "duration_seconds": duration_seconds,
        "workflow_threshold_seconds": DEEP_WORKFLOW_DURATION_THRESHOLD_SECONDS,
    }


def inspect_video_payload(
    url_or_id: str,
    proxy_config: GenericProxyConfig | None = None,
) -> dict[str, Any]:
    video_id = extract_video_id(url_or_id)
    duration_seconds = fetch_video_duration_seconds(video_id, proxy_config)
    try:
        tracks = list_transcript_tracks(video_id, proxy_config=proxy_config)
    except CliError:
        tracks = fetch_raw_caption_tracks(video_id, proxy_config)

    manual_languages = [track.language_code for track in tracks if not track.auto_generated]
    auto_generated_languages = [track.language_code for track in tracks if track.auto_generated]
    return {
        "id": video_id,
        "url": canonical_watch_url(video_id),
        "duration_seconds": duration_seconds,
        "has_captions": bool(tracks),
        "caption_tracks": len(tracks),
        "languages": [track.language_code for track in tracks],
        "manual_languages": manual_languages,
        "auto_generated_languages": auto_generated_languages,
        "tracks": [track.public_dict() for track in tracks],
    }


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
    player = fetch_watch_player_response(video_id, proxy_config)
    captions = player.get("captions")
    renderer = (
        captions.get("playerCaptionsTracklistRenderer", {})
        if isinstance(captions, dict)
        else {}
    )
    raw_tracks = renderer.get("captionTracks") if isinstance(renderer, dict) else []
    raw_tracks = raw_tracks if isinstance(raw_tracks, list) else []
    tracks = [
        CaptionTrack(
            name=caption_name(track),
            language_code=str(track.get("languageCode") or ""),
            base_url=str(track.get("baseUrl") or ""),
            kind=track.get("kind"),
            is_translatable=bool(track.get("isTranslatable")),
        )
        for track in raw_tracks
        if isinstance(track, dict)
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


def normalize_languages(
    languages: str | list[str] | tuple[str, ...] | None,
    default: str = "en",
) -> list[str]:
    if isinstance(languages, str):
        candidates = [languages]
    elif languages:
        candidates = list(languages)
    else:
        candidates = [default]

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
    suggested_languages = ",".join([*requested, *available])
    suggested_fallback = f"--langs {suggested_languages}" if suggested_languages else None
    message = (
        "No caption track matched requested languages "
        f"{', '.join(requested)}. Available: {', '.join(available)}"
    )
    if suggested_fallback:
        message += f". Try: {suggested_fallback}"
    raise CliError(
        message,
        "language_not_available",
        {
            "requested_languages": requested,
            "available_languages": available,
            "suggested_fallback": suggested_fallback,
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
