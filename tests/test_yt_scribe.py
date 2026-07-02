import json
import unittest
from dataclasses import dataclass

import yt_scribe


@dataclass
class FakeTrack:
    language_code: str
    language: str
    is_generated: bool = False
    is_translatable: bool = True


@dataclass
class FakeSnippet:
    text: str
    start: float
    duration: float


class FakeTranscriptApi:
    def list(self, video_id):
        assert video_id == "dQw4w9WgXcQ"
        return [
            FakeTrack(language_code="en", language="English"),
            FakeTrack(language_code="en", language="English (auto-generated)", is_generated=True),
        ]

    def fetch(self, video_id, languages):
        assert video_id == "dQw4w9WgXcQ"
        assert languages == ["en"]
        return [
            FakeSnippet(text="hello\nworld", start=1.2345, duration=2.0),
            FakeSnippet(text=" ", start=3.0, duration=1.0),
        ]


class YtScribeTests(unittest.TestCase):
    def test_extract_video_id_from_common_urls(self):
        self.assertEqual(yt_scribe.extract_video_id("dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(
            yt_scribe.extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=12"),
            "dQw4w9WgXcQ",
        )
        self.assertEqual(
            yt_scribe.extract_video_id("https://youtu.be/dQw4w9WgXcQ?si=abc"),
            "dQw4w9WgXcQ",
        )
        self.assertEqual(
            yt_scribe.extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_parse_json3_caption(self):
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
        self.assertEqual(
            yt_scribe.parse_json3_caption(payload),
            [{"start": 1.0, "duration": 0.5, "text": "hello world"}],
        )

    def test_srt_timestamp(self):
        self.assertEqual(yt_scribe.srt_timestamp(3661.234), "01:01:01,234")

    def test_lifecycle_is_exposed(self):
        steps = [item["step"] for item in yt_scribe.lifecycle_steps()]
        self.assertEqual(steps, ["check", "inspect", "fetch", "polish", "run"])

    def test_builtin_style_prompts_trigger_inner_polisher_skill(self):
        for instruction in yt_scribe.STYLE_INSTRUCTIONS.values():
            self.assertIn("yt-scribe-transcript-polisher", instruction)
            self.assertIn("stdin", instruction)

    def test_fetch_transcript_uses_transcript_api_backend(self):
        transcript = yt_scribe.fetch_transcript("dQw4w9WgXcQ", "en", api=FakeTranscriptApi())

        self.assertEqual(transcript["source"], "youtube_transcript_api")
        self.assertEqual(transcript["text"], "hello world")
        self.assertEqual(transcript["segments"][0]["start"], 1.234)

    def test_default_run_output_path_is_human_first_and_unique(self):
        first = yt_scribe.default_run_output_path("dQw4w9WgXcQ", "notes")
        self.assertEqual(first.name, "yt-scribe-dQw4w9WgXcQ-notes.md")

    def test_default_polish_output_path_uses_input_stem(self):
        first = yt_scribe.default_polish_output_path("transcript.txt", "summary")
        self.assertEqual(first.name, "transcript-summary.md")


if __name__ == "__main__":
    unittest.main()
