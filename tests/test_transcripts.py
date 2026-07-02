import json

from yt_scribe import transcripts


def test_transcripts_module_renders_timestamped_text_and_srt():
    transcript = {
        "text": "hello\nworld",
        "segments": [
            {"start": 1.2, "duration": 0.5, "text": "hello"},
            {"start": 62.0, "duration": 0.5, "text": "world"},
        ],
    }

    assert transcripts.render_timestamped_transcript(transcript) == "[00:01] hello\n[01:02] world"
    assert "00:00:01,200 --> 00:00:01,700" in transcripts.render_transcript(transcript, "srt")
    assert json.loads(transcripts.render_transcript(transcript, "json"))["text"] == "hello\nworld"


def test_transcripts_module_writes_and_reads_cache(tmp_path):
    transcript = {
        "video_id": "dQw4w9WgXcQ",
        "language": "en-GB",
        "text": "cached transcript",
    }

    path = transcripts.write_transcript_cache(tmp_path, transcript)
    cached = transcripts.read_transcript_cache(tmp_path, "dQw4w9WgXcQ", ["en", "en-GB"])

    assert path.name == "dQw4w9WgXcQ-en-GB.json"
    assert cached == (transcript, path)


def test_transcripts_module_splits_chunks_on_segment_boundaries():
    transcript = {
        "segments": [
            {"start": 1.0, "text": "first point"},
            {"start": 2.0, "text": "second point"},
            {"start": 3.0, "text": "third point"},
        ],
        "text": "first point\nsecond point\nthird point",
    }

    assert transcripts.split_transcript_chunks(transcript, 25, timestamps=False) == [
        "first point\nsecond point",
        "third point",
    ]
