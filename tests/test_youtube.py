import json

import pytest

import yt_scribe
from yt_scribe import youtube


def test_youtube_module_extracts_common_video_urls():
    assert youtube.extract_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert (
        youtube.extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=12")
        == "dQw4w9WgXcQ"
    )
    assert youtube.canonical_watch_url("dQw4w9WgXcQ") == (
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    )


def test_youtube_module_parses_json3_captions():
    payload = json.dumps(
        {
            "events": [
                {
                    "tStartMs": 1000,
                    "dDurationMs": 500,
                    "segs": [{"utf8": "hello"}, {"utf8": " world"}],
                },
                {"tStartMs": 2000, "dDurationMs": 500, "segs": [{"utf8": "\n"}]},
            ]
        }
    ).encode()

    assert youtube.parse_json3_caption(payload) == [
        {"start": 1.0, "duration": 0.5, "text": "hello world"}
    ]


def test_youtube_module_extracts_playlist_video_ids(monkeypatch):
    monkeypatch.setattr(
        youtube,
        "http_get",
        lambda url, proxy_config=None: b'{"videoId":"dQw4w9WgXcQ"}{"videoId":"dQw4w9WgXcQ"}',
    )

    assert youtube.fetch_playlist_video_ids("https://www.youtube.com/playlist?list=PLabc") == [
        "dQw4w9WgXcQ"
    ]


def test_youtube_module_only_treats_youtube_hosts_as_playlists():
    assert youtube.playlist_id_from_url("https://www.youtube.com/playlist?list=PLabc") == "PLabc"
    assert youtube.playlist_id_from_url("https://example.com/watch?list=PLabc") is None
    assert youtube.playlist_id_from_url("file:///etc/passwd?list=PLabc") is None


def test_raw_caption_tracks_treats_invalid_caption_metadata_as_missing(monkeypatch):
    monkeypatch.setattr(
        youtube,
        "fetch_watch_player_response",
        lambda video_id, proxy_config=None: {"captions": None},
    )

    with pytest.raises(yt_scribe.CliError) as error:
        youtube.fetch_raw_caption_tracks("dQw4w9WgXcQ")

    assert error.value.code == "no_captions"
