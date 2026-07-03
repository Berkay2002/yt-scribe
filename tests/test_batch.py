from pathlib import Path

from yt_scribe import batch


def test_batch_module_reads_url_lists_and_ignores_comments(tmp_path):
    path = tmp_path / "videos.txt"
    path.write_text("\n# comment\ndQw4w9WgXcQ\n\nhttps://youtu.be/aaaaaaaaaaa\n", encoding="utf-8")

    assert batch.read_batch_urls(path) == ["dQw4w9WgXcQ", "https://youtu.be/aaaaaaaaaaa"]


def test_batch_module_expands_playlist_items(monkeypatch):
    monkeypatch.setattr(
        batch,
        "fetch_playlist_video_ids",
        lambda url, proxy_config: ["dQw4w9WgXcQ"],
    )

    items = batch.expand_batch_items(["https://www.youtube.com/playlist?list=PLabc123"], None)

    assert items == [
        {
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "playlist_url": "https://www.youtube.com/playlist?list=PLabc123",
            "playlist_id": "PLabc123",
        }
    ]


def test_batch_module_does_not_fetch_non_youtube_playlist_like_urls(monkeypatch):
    def fail_fetch(url, proxy_config):
        raise AssertionError(f"unexpected playlist fetch: {url}")

    monkeypatch.setattr(batch, "fetch_playlist_video_ids", fail_fetch)

    items = batch.expand_batch_items(["https://example.com/watch?list=PLabc123"], None)

    assert items == [{"url": "https://example.com/watch?list=PLabc123"}]


def test_batch_module_finds_next_available_output_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("yt-scribe-dQw4w9WgXcQ-notes.md").write_text("existing", encoding="utf-8")

    assert batch.default_run_output_path("dQw4w9WgXcQ", "notes").name == (
        "yt-scribe-dQw4w9WgXcQ-notes-2.md"
    )
