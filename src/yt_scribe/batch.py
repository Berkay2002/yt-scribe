"""Batch input and output helpers for yt-scribe."""

from __future__ import annotations

from pathlib import Path

from youtube_transcript_api.proxies import GenericProxyConfig

from . import CliError
from .youtube import canonical_watch_url, fetch_playlist_video_ids, playlist_id_from_url


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
