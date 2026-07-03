import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock, patch

import yt_scribe as package
from yt_scribe import batch, cli, config, polish, runs, transcripts, verify, youtube
from yt_scribe.polish import chunked as polish_chunked
from yt_scribe.polish import deep as polish_deep
from yt_scribe.polish import harnesses as polish_harnesses
from yt_scribe.polish import workflows as polish_workflows

PATCH_TARGETS = {
    "fetch_transcript": (youtube, transcripts),
    "fetch_playlist_video_ids": (youtube, batch),
    "fetch_video_duration_seconds": (youtube, cli),
    "fetch_video_title": (youtube, cli),
    "list_transcript_tracks": (youtube,),
    "fetch_raw_caption_tracks": (youtube, cli),
    "load_or_fetch_transcript": (transcripts, cli, polish_workflows),
    "run_agent_polish": (
        polish,
        cli,
        polish_harnesses,
        polish_chunked,
        polish_deep,
        polish_workflows,
    ),
    "run_codex_csv_fanout_jobs": (polish, polish_deep),
    "run_opencode_server_session": (polish, polish_deep),
}


@contextlib.contextmanager
def patch_cli_workflow(name, **mock_kwargs):
    replacement = Mock(**mock_kwargs)
    targets = PATCH_TARGETS[name]
    with contextlib.ExitStack() as stack:
        for target in targets:
            stack.enter_context(patch.object(target, name, replacement))
        yield replacement


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


class FakeMultiLangTranscriptApi:
    def list(self, video_id):
        assert video_id == "dQw4w9WgXcQ"
        return [
            FakeTrack(language_code="sv", language="Swedish"),
            FakeTrack(language_code="en-GB", language="English (United Kingdom)"),
        ]

    def fetch(self, video_id, languages):
        assert video_id == "dQw4w9WgXcQ"
        assert languages == ["en-GB"]
        return [FakeSnippet(text="fallback transcript", start=0.0, duration=1.0)]


class BlockedTranscriptApi:
    def list(self, video_id):
        raise RuntimeError("YouTube is blocking requests from your IP")

    def fetch(self, video_id, languages):
        raise AssertionError("fetch should not be called when listing fails")


class FetchBlockedTranscriptApi:
    def list(self, video_id):
        assert video_id == "dQw4w9WgXcQ"
        return [FakeTrack(language_code="en", language="English")]

    def fetch(self, video_id, languages):
        raise RuntimeError("YouTube is blocking requests from your IP")


class YtScribeTests(unittest.TestCase):
    def setUp(self):
        self.duration_patcher = patch.object(
            cli,
            "fetch_video_duration_seconds",
            return_value=None,
        )
        self.duration_patcher.start()
        self.addCleanup(self.duration_patcher.stop)
        self.title_patcher = patch.object(
            cli,
            "fetch_video_title",
            return_value=None,
        )
        self.title_patcher.start()
        self.addCleanup(self.title_patcher.stop)

    def sample_transcript(self) -> dict[str, object]:
        return {
            "video_id": "dQw4w9WgXcQ",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "language": "en",
            "requested_languages": ["en"],
            "track": {"language_code": "en"},
            "segments": [{"text": "hello world"}],
            "text": "hello world",
        }

    def test_embedded_skill_assets_match_source_files(self):
        for relative_path, embedded in package.EMBEDDED_SKILL_ASSETS.items():
            with self.subTest(relative_path=relative_path):
                actual = Path(relative_path).read_text(encoding="utf-8")
                self.assertEqual(actual, embedded)

    def test_extract_video_id_from_common_urls(self):
        self.assertEqual(youtube.extract_video_id("dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(
            youtube.extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=12"),
            "dQw4w9WgXcQ",
        )
        self.assertEqual(
            youtube.extract_video_id("https://youtu.be/dQw4w9WgXcQ?si=abc"),
            "dQw4w9WgXcQ",
        )
        self.assertEqual(
            youtube.extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ"),
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
            youtube.parse_json3_caption(payload),
            [{"start": 1.0, "duration": 0.5, "text": "hello world"}],
        )

    def test_extract_video_duration_seconds(self):
        self.assertEqual(
            youtube.extract_video_duration_seconds(
                {"videoDetails": {"lengthSeconds": "2700"}}
            ),
            2700,
        )
        self.assertIsNone(youtube.extract_video_duration_seconds({}))
        self.assertIsNone(
            youtube.extract_video_duration_seconds(
                {"videoDetails": {"lengthSeconds": "not-a-number"}}
            )
        )
        self.assertIsNone(
            youtube.extract_video_duration_seconds(
                {"videoDetails": {"lengthSeconds": "-1"}}
            )
        )

    def test_srt_timestamp(self):
        self.assertEqual(transcripts.srt_timestamp(3661.234), "01:01:01,234")

    def test_timestamp_anchor_uses_short_format_until_hour_mark(self):
        self.assertEqual(transcripts.timestamp_anchor(61.2), "01:01")
        self.assertEqual(transcripts.timestamp_anchor(3661.2), "01:01:01")

    def test_render_timestamped_transcript_uses_segment_start_times(self):
        transcript = {
            "segments": [
                {"start": 1.2, "duration": 0.5, "text": "hello"},
                {"start": 62.0, "duration": 0.5, "text": "world"},
                {"start": 63.0, "duration": 0.5, "text": "   "},
            ],
        }

        self.assertEqual(
            transcripts.render_timestamped_transcript(transcript),
            "[00:01] hello\n[01:02] world",
        )

    def test_split_transcript_chunks_uses_segment_boundaries(self):
        transcript = {
            "segments": [
                {"start": 1.0, "text": "first point"},
                {"start": 2.0, "text": "second point"},
                {"start": 3.0, "text": "third point"},
            ],
        }

        self.assertEqual(
            transcripts.split_transcript_chunks(transcript, 25, timestamps=False),
            ["first point\nsecond point", "third point"],
        )

    def test_lifecycle_is_exposed(self):
        steps = [item["step"] for item in config.lifecycle_steps()]
        self.assertEqual(steps, ["check", "inspect", "fetch", "polish", "run"])

    def test_builtin_style_prompts_trigger_transcript_polisher_skill(self):
        for style in cli.STYLE_INSTRUCTIONS:
            instruction = polish.style_instruction(style, "codex")
            self.assertIn("yt-scribe-transcript-polisher", instruction)
            self.assertIn("harness/codex.md", instruction)
            self.assertIn("stdin", instruction)
            self.assertNotIn("opencode", instruction.lower())

    def test_opencode_style_prompts_use_opencode_harness_file(self):
        instruction = polish.style_instruction("notes", "opencode")

        self.assertIn("yt-scribe-transcript-polisher", instruction)
        self.assertIn("harness/opencode.md", instruction)
        self.assertIn("attached transcript file", instruction)
        self.assertNotIn("codex", instruction.lower())

    def test_focus_instructions_extend_builtin_prompt(self):
        args = argparse.Namespace(
            style="summary",
            template=None,
            focus=["Only list decisions and risks."],
            focus_file=None,
            instruction=None,
            prompt_file=None,
            timestamps=False,
        )

        instruction = polish.resolve_instruction(args, "codex")

        self.assertEqual(instruction.mode, "custom")
        self.assertEqual(instruction.sources, ["--focus"])
        self.assertIn("yt-scribe-transcript-polisher", instruction.text)
        self.assertIn("Summarize this YouTube transcript", instruction.text)
        self.assertIn("Custom user instructions", instruction.text)
        self.assertIn("Only list decisions and risks.", instruction.text)

    def test_timestamp_instructions_extend_builtin_prompt(self):
        args = argparse.Namespace(
            style="notes",
            template=None,
            focus=None,
            focus_file=None,
            instruction=None,
            prompt_file=None,
            timestamps=True,
        )

        instruction = polish.resolve_instruction(args, "codex")

        self.assertEqual(instruction.mode, "style")
        self.assertEqual(instruction.sources, ["--style", "--timestamps"])
        self.assertIn("timestamp anchors", instruction.text)
        self.assertIn("Do not invent timestamps", instruction.text)

    def test_template_instructions_compose_with_focus(self):
        args = argparse.Namespace(
            style="notes",
            template="lecture",
            focus=["Keep only decisions."],
            focus_file=None,
            instruction=None,
            prompt_file=None,
            timestamps=False,
        )

        instruction = polish.resolve_instruction(args, "codex")

        self.assertEqual(instruction.mode, "custom")
        self.assertEqual(instruction.sources, ["--template", "--focus"])
        self.assertIn("lecture notes", instruction.text)
        self.assertIn("Keep only decisions.", instruction.text)

    def test_replacement_instruction_conflicts_with_focus(self):
        args = argparse.Namespace(
            style="notes",
            template=None,
            focus=["extra"],
            focus_file=None,
            instruction="Replace everything.",
            prompt_file=None,
            timestamps=False,
        )

        with self.assertRaises(package.CliError) as error:
            polish.resolve_instruction(args, "codex")

        self.assertEqual(error.exception.code, "conflicting_instruction_options")

    def test_fetch_transcript_uses_transcript_api_backend(self):
        transcript = youtube.fetch_transcript("dQw4w9WgXcQ", "en", api=FakeTranscriptApi())

        self.assertEqual(transcript["source"], "youtube_transcript_api")
        self.assertEqual(transcript["text"], "hello world")
        self.assertEqual(transcript["segments"][0]["start"], 1.234)
        self.assertEqual(transcript["requested_languages"], ["en"])

    def test_fetch_transcript_uses_ordered_language_fallback(self):
        transcript = youtube.fetch_transcript(
            "dQw4w9WgXcQ",
            ["en", "en-GB", "sv"],
            api=FakeMultiLangTranscriptApi(),
        )

        self.assertEqual(transcript["language"], "en-GB")
        self.assertEqual(transcript["requested_languages"], ["en", "en-GB", "sv"])
        self.assertEqual(transcript["text"], "fallback transcript")

    def test_fetch_transcript_reports_no_matching_fallback_language(self):
        with self.assertRaises(package.CliError) as error:
            youtube.fetch_transcript(
                "dQw4w9WgXcQ",
                ["de", "fr"],
                api=FakeMultiLangTranscriptApi(),
            )

        self.assertEqual(error.exception.code, "language_not_available")
        self.assertEqual(error.exception.details["requested_languages"], ["de", "fr"])
        self.assertEqual(error.exception.details["available_languages"], ["sv", "en-GB"])
        self.assertEqual(
            error.exception.details["suggested_fallback"],
            "--langs de,fr,sv,en-GB",
        )
        self.assertIn("--langs de,fr,sv,en-GB", str(error.exception))

    def test_fetch_transcript_falls_back_to_raw_timedtext_when_api_list_is_blocked(self):
        def fake_http_get(url, proxy_config=None):
            if url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ":
                return (
                    b'var ytInitialPlayerResponse = {"captions":'
                    b'{"playerCaptionsTracklistRenderer":{"captionTracks":[{'
                    b'"name":{"simpleText":"English"},"languageCode":"en",'
                    b'"baseUrl":"https://example.test/timedtext?v=dQw4w9WgXcQ&lang=en",'
                    b'"isTranslatable":true'
                    b"}]}}};"
                )
            self.assertIn("fmt=json3", url)
            self.assertIsNone(proxy_config)
            return json.dumps(
                {
                    "events": [
                        {
                            "tStartMs": 1000,
                            "dDurationMs": 500,
                            "segs": [{"utf8": "raw"}, {"utf8": " transcript"}],
                        }
                    ]
                }
            ).encode()

        with patch.object(youtube, "http_get", side_effect=fake_http_get):
            transcript = youtube.fetch_transcript(
                "dQw4w9WgXcQ",
                "en",
                api=BlockedTranscriptApi(),
            )

        self.assertEqual(transcript["source"], "youtube_timedtext")
        self.assertEqual(transcript["text"], "raw transcript")
        self.assertEqual(transcript["segments"][0]["start"], 1.0)
        self.assertEqual(transcript["requested_languages"], ["en"])

    def test_fetch_transcript_falls_back_to_raw_timedtext_when_api_fetch_is_blocked(self):
        def fake_http_get(url, proxy_config=None):
            if url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ":
                return (
                    b'var ytInitialPlayerResponse = {"captions":'
                    b'{"playerCaptionsTracklistRenderer":{"captionTracks":[{'
                    b'"name":{"simpleText":"English"},"languageCode":"en",'
                    b'"baseUrl":"https://example.test/timedtext?v=dQw4w9WgXcQ&lang=en"'
                    b"}]}}};"
                )
            self.assertIsNone(proxy_config)
            return json.dumps(
                {
                    "events": [
                        {
                            "tStartMs": 2000,
                            "dDurationMs": 750,
                            "segs": [{"utf8": "fallback text"}],
                        }
                    ]
                }
            ).encode()

        with patch.object(youtube, "http_get", side_effect=fake_http_get):
            transcript = youtube.fetch_transcript(
                "dQw4w9WgXcQ",
                "en",
                api=FetchBlockedTranscriptApi(),
            )

        self.assertEqual(transcript["source"], "youtube_timedtext")
        self.assertEqual(transcript["text"], "fallback text")

    def test_proxy_config_from_args_uses_cli_flags_and_env_fallback(self):
        args = argparse.Namespace(http_proxy="http://cli-proxy", https_proxy=None)

        with patch.dict(
            os.environ,
            {
                "YT_SCRIBE_HTTP_PROXY": "",
                "YT_SCRIBE_HTTPS_PROXY": "http://env-proxy",
            },
        ):
            proxy_config = config.proxy_config_from_args(args)

        self.assertEqual(
            proxy_config.to_requests_dict(),
            {"http": "http://cli-proxy", "https": "http://env-proxy"},
        )

    def test_proxy_config_from_args_rejects_placeholder_port(self):
        args = argparse.Namespace(
            http_proxy=None,
            https_proxy="http://USERNAME:PASSWORD@HOST:PORT",
        )

        with self.assertRaises(package.CliError) as raised:
            config.proxy_config_from_args(args)

        self.assertEqual(raised.exception.code, "invalid_proxy_config")
        self.assertIn("numeric port", str(raised.exception))

    def test_http_get_reports_youtube_429_as_ip_block(self):
        error = urllib.error.HTTPError(
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=None,
        )

        with (
            patch.object(youtube.urllib.request, "urlopen", side_effect=error),
            self.assertRaises(package.CliError) as raised,
        ):
            youtube.http_get("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

        self.assertEqual(raised.exception.code, "youtube_ip_blocked")
        self.assertIn("YT_SCRIBE_HTTPS_PROXY", str(raised.exception))

    def test_default_run_output_path_is_human_first_and_unique(self):
        first = batch.default_run_output_path("dQw4w9WgXcQ", "notes")
        self.assertEqual(first.name, "yt-scribe-dQw4w9WgXcQ-notes.md")

    def test_default_polish_output_path_uses_input_stem(self):
        first = batch.default_polish_output_path("transcript.txt", "summary")
        self.assertEqual(first.name, "transcript-summary.md")

    def test_opencode_polish_uses_run_command_with_attached_transcript(self):
        calls = []

        class FakePipe:
            def __init__(self, lines: list[str] | None = None):
                self.lines = lines or []

            def __iter__(self):
                return iter(self.lines)

        class FakePopen:
            def __init__(self, command, **kwargs):
                calls.append((command, kwargs))
                transcript_path = Path(command[command.index("--file") + 1])
                self_outer.assertEqual(
                    transcript_path.read_text(encoding="utf-8"), "raw transcript"
                )
                self.stdin = None
                self.stdout = FakePipe(
                    [
                        json.dumps({"type": "step_start"}) + "\n",
                        json.dumps({"type": "text", "part": {"text": "polished text"}})
                        + "\n",
                        json.dumps({"type": "step_finish"}) + "\n",
                    ]
                )
                self.stderr = FakePipe()
                self.returncode = 0

            def wait(self) -> int:
                return self.returncode

        self_outer = self
        with (
            patch.object(polish_harnesses, "command_path", return_value="opencode"),
            patch.object(polish_harnesses.subprocess, "Popen", FakePopen),
        ):
            result = polish.run_agent_polish(
                "raw transcript",
                "Polish the transcript.",
                None,
                "anthropic/claude-sonnet-4-5",
                "C:\\work",
                "opencode",
            )

        self.assertEqual(result["text"], "polished text\n")
        self.assertEqual(result["harness"], "opencode")
        self.assertEqual(
            calls[0][0][:2],
            ["opencode", "run"],
        )
        self.assertEqual(calls[0][0][2], "Polish the transcript.")
        self.assertIn("--dir", calls[0][0])
        self.assertIn("--format", calls[0][0])
        self.assertIn("--thinking", calls[0][0])
        self.assertNotIn("--agent", calls[0][0])
        self.assertIn("--model", calls[0][0])

    def test_opencode_polish_streams_json_events_to_progress(self):
        calls = []

        class FakePipe:
            def __init__(self, lines: list[str] | None = None):
                self.lines = lines or []
                self.written = ""
                self.closed = False

            def write(self, text: str) -> None:
                self.written += text

            def close(self) -> None:
                self.closed = True

            def __iter__(self):
                return iter(self.lines)

        class FakePopen:
            def __init__(self, command, **kwargs):
                calls.append((command, kwargs))
                self.stdin = FakePipe()
                self.stdout = FakePipe(
                    [
                        json.dumps({"type": "step_start", "part": {"type": "step-start"}})
                        + "\n",
                        json.dumps(
                            {
                                "type": "tool_use",
                                "part": {
                                    "type": "tool",
                                    "tool": "read",
                                    "state": {"status": "completed"},
                                },
                            }
                        )
                        + "\n",
                        json.dumps(
                            {
                                "type": "reasoning",
                                "part": {"type": "reasoning", "text": "checking transcript"},
                            }
                        )
                        + "\n",
                        json.dumps(
                            {
                                "type": "text",
                                "part": {"type": "text", "text": "polished text"},
                            }
                        )
                        + "\n",
                    ]
                )
                self.stderr = FakePipe()
                self.returncode = 0

            def wait(self) -> int:
                return self.returncode

        stderr = io.StringIO()
        with (
            patch.object(polish_harnesses, "command_path", return_value="opencode"),
            patch.object(polish_harnesses.subprocess, "Popen", FakePopen),
            contextlib.redirect_stderr(stderr),
        ):
            result = polish.run_agent_polish(
                "raw transcript",
                "Polish the transcript.",
                None,
                None,
                None,
                "opencode",
                package.ProgressReporter(True),
            )

        self.assertEqual(result["text"], "polished text\n")
        self.assertIn("--format", calls[0][0])
        self.assertIn("json", calls[0][0])
        self.assertIn("--thinking", calls[0][0])
        progress = stderr.getvalue()
        self.assertIn("OpenCode started step", progress)
        self.assertIn("OpenCode completed tool: read", progress)
        self.assertIn("OpenCode completed reasoning", progress)
        self.assertIn("OpenCode completed text output (13 chars)", progress)

    def test_codex_polish_streams_json_events_to_progress(self):
        calls = []

        class FakePipe:
            def __init__(self, lines: list[str] | None = None):
                self.lines = lines or []
                self.written = ""
                self.closed = False

            def write(self, text: str) -> None:
                self.written += text

            def close(self) -> None:
                self.closed = True

            def __iter__(self):
                return iter(self.lines)

        class FakePopen:
            def __init__(self, command, **kwargs):
                calls.append((command, kwargs))
                final_path = Path(command[command.index("--output-last-message") + 1])
                final_path.write_text("polished text\n", encoding="utf-8")
                self.stdin = FakePipe()
                self.stdout = FakePipe(
                    [
                        json.dumps({"type": "thread.started", "thread_id": "thread-1"}) + "\n",
                        json.dumps(
                            {
                                "type": "item.started",
                                "item": {"id": "item-1", "type": "reasoning"},
                            }
                        )
                        + "\n",
                        json.dumps(
                            {
                                "type": "item.started",
                                "item": {
                                    "id": "item-2",
                                    "type": "command_execution",
                                    "command": "python -m pytest",
                                },
                            }
                        )
                        + "\n",
                        json.dumps(
                            {
                                "type": "item.completed",
                                "item": {
                                    "id": "item-3",
                                    "type": "agent_message",
                                    "text": "polished text",
                                },
                            }
                        )
                        + "\n",
                    ]
                )
                self.stderr = FakePipe()
                self.returncode = 0

            def wait(self) -> int:
                return self.returncode

        stderr = io.StringIO()
        with (
            patch.object(polish_harnesses, "command_path", return_value="codex"),
            patch.object(polish_harnesses.subprocess, "Popen", FakePopen),
            contextlib.redirect_stderr(stderr),
        ):
            result = polish.run_agent_polish(
                "raw transcript",
                "Polish the transcript.",
                None,
                None,
                None,
                "codex",
                package.ProgressReporter(True),
            )

        self.assertEqual(result["text"], "polished text\n")
        self.assertIn("--json", calls[0][0])
        progress = stderr.getvalue()
        self.assertIn("Codex thread started", progress)
        self.assertIn("Codex started reasoning", progress)
        self.assertIn("Codex started command: python -m pytest", progress)
        self.assertIn("Codex completed final message (13 chars)", progress)

    def test_doctor_reports_codex_and_opencode_harnesses(self):
        paths = {"codex": "C:\\bin\\codex.exe", "opencode": None}

        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch.dict(
                    os.environ,
                    {"YT_SCRIBE_CONFIG": str(Path(tmp_dir) / "config.json")},
                ),
                patch.object(config, "command_path", side_effect=lambda name: paths.get(name)),
                patch.object(config, "command_output", return_value="codex 1.0"),
            ):
                payload = config.doctor_payload()

        self.assertEqual(payload["agent_harness"]["default"], "codex")
        self.assertTrue(payload["agent_harness"]["harnesses"]["codex"]["available"])
        self.assertFalse(payload["agent_harness"]["harnesses"]["opencode"]["available"])

    def test_local_install_metadata_is_platform_specific(self):
        with patch.object(config.sys, "platform", "linux"):
            self.assertEqual(config.local_install_command(), "sh ./install-local.sh")
            self.assertEqual(config.install_bin_dir(), Path.home() / ".local" / "bin")

        with patch.object(config.sys, "platform", "darwin"):
            self.assertEqual(config.local_install_command(), "sh ./install-local.sh")
            self.assertEqual(config.install_bin_dir(), Path.home() / ".local" / "bin")

        with patch.object(config.sys, "platform", "win32"):
            self.assertEqual(config.local_install_command(), ".\\install-local.ps1")
            self.assertEqual(config.install_bin_dir(), Path.home() / ".local" / "bin")

    def test_doctor_payload_reports_platform_install_command(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch.dict(
                    os.environ,
                    {"YT_SCRIBE_CONFIG": str(Path(tmp_dir) / "config.json")},
                ),
                patch.object(config.sys, "platform", "darwin"),
                patch.object(config, "command_path", return_value=None),
            ):
                payload = config.doctor_payload()

        self.assertEqual(payload["install"]["local_install_command"], "sh ./install-local.sh")
        self.assertEqual(Path(payload["install"]["wrapper_dir"]), config.install_bin_dir())

    def test_selected_agent_harness_uses_config_unless_cli_overrides(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {"YT_SCRIBE_CONFIG": str(Path(tmp_dir) / "config.json")},
            ):
                config.write_config({"default_agent_harness": "opencode"})

                self.assertEqual(
                    polish.selected_agent_harness(
                        argparse.Namespace(agent_harness=None),
                    ),
                    "opencode",
                )
                self.assertEqual(
                    polish.selected_agent_harness(
                        argparse.Namespace(agent_harness="codex"),
                    ),
                    "codex",
                )

    def test_run_reports_progress_to_stderr_before_polishing(self):
        transcript = {
            "video_id": "dQw4w9WgXcQ",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "language": "en",
            "track": {"language_code": "en"},
            "segments": [{"text": "hello world"}],
            "text": "hello world",
        }
        args = cli.build_parser().parse_args(
            ["run", "dQw4w9WgXcQ", "--out", "notes.md"],
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            patch_cli_workflow("fetch_transcript", return_value=transcript),
            patch_cli_workflow(
                "run_agent_polish",
                return_value={
                    "output_path": "notes.md",
                    "chars": 14,
                    "harness": "codex",
                    "text": "polished text\n",
                },
            ) as run_agent,
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            exit_code = cli.handle_args(args)

        self.assertEqual(exit_code, 0)
        self.assertIn("Fetching transcript", stderr.getvalue())
        self.assertIn("Fetched 1 transcript segment", stderr.getvalue())
        self.assertIn("Using Codex", stderr.getvalue())
        self.assertIn("Wrote polished transcript to notes.md", stdout.getvalue())
        self.assertIsNotNone(run_agent.call_args.kwargs["progress"])
        self.assertEqual(run_agent.call_args.kwargs["transcript_text"], "hello world")

    def test_run_with_timestamps_passes_timestamped_transcript_and_reports_json(self):
        transcript = {
            "video_id": "dQw4w9WgXcQ",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "language": "en",
            "requested_languages": ["en"],
            "track": {"language_code": "en"},
            "segments": [
                {"start": 1.2, "duration": 0.5, "text": "hello world"},
                {"start": 62.0, "duration": 0.5, "text": "second point"},
            ],
            "text": "hello world\nsecond point",
        }
        args = cli.build_parser().parse_args(
            ["--json", "run", "dQw4w9WgXcQ", "--stdout", "--timestamps"],
        )
        stdout = io.StringIO()

        with (
            patch_cli_workflow("fetch_transcript", return_value=transcript),
            patch_cli_workflow(
                "run_agent_polish",
                return_value={
                    "output_path": None,
                    "chars": len("polished text\n"),
                    "harness": "codex",
                    "text": "polished text\n",
                },
            ) as polish,
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = cli.handle_args(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            polish.call_args.kwargs["transcript_text"],
            "[00:01] hello world\n[01:02] second point",
        )
        self.assertIsNone(polish.call_args.kwargs["out_path"])
        self.assertIn("timestamp anchors", polish.call_args.kwargs["instruction"])
        self.assertTrue(payload["run"]["timestamp_grounding"])
        self.assertIn("--timestamps", payload["run"]["instruction_sources"])

    def test_run_with_chunk_chars_polishes_chunks_then_merges(self):
        transcript = {
            "video_id": "dQw4w9WgXcQ",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "language": "en",
            "requested_languages": ["en"],
            "track": {"language_code": "en"},
            "segments": [
                {"start": 1.0, "duration": 0.5, "text": "first point"},
                {"start": 2.0, "duration": 0.5, "text": "second point"},
                {"start": 3.0, "duration": 0.5, "text": "third point"},
            ],
            "text": "first point\nsecond point\nthird point",
        }
        calls = []

        def fake_polish(**kwargs):
            calls.append(kwargs)
            if len(calls) < 3:
                return {
                    "output_path": None,
                    "chars": len(f"chunk {len(calls)}\n"),
                    "harness": "codex",
                    "text": f"chunk {len(calls)}\n",
                }
            self.assertIn("## Chunk 1", kwargs["transcript_text"])
            self.assertIn("## Chunk 2", kwargs["transcript_text"])
            return {
                "output_path": None,
                "chars": len("merged text\n"),
                "harness": "codex",
                "text": "merged text\n",
            }

        args = cli.build_parser().parse_args(
            ["--json", "run", "dQw4w9WgXcQ", "--stdout", "--chunk-chars", "25"],
        )
        stdout = io.StringIO()

        with (
            patch_cli_workflow("fetch_transcript", return_value=transcript),
            patch_cli_workflow("run_agent_polish", side_effect=fake_polish),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = cli.handle_args(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(calls), 3)
        self.assertEqual(payload["run"]["chunking"]["chunks"], 2)
        self.assertEqual(payload["run"]["chunking"]["merge_status"], "merged")

    def test_chunked_polish_reports_chunk_failures(self):
        def fake_polish(**kwargs):
            if "second" in kwargs["transcript_text"]:
                raise package.CliError("agent failed", "agent_failed")
            return {
                "output_path": None,
                "chars": len("chunk ok\n"),
                "harness": "codex",
                "text": "chunk ok\n",
            }

        with patch.object(polish_chunked, "run_agent_polish", side_effect=fake_polish):
            with self.assertRaises(package.CliError) as error:
                polish.run_chunked_agent_polish(
                    chunks=["first", "second"],
                    instruction="Polish.",
                    out_path=None,
                    model=None,
                    cwd=None,
                    harness="codex",
                    chunk_chars=25,
                    resume=False,
                    progress=None,
                )

        self.assertEqual(error.exception.code, "chunk_polish_failed")
        self.assertEqual(error.exception.details["chunk_index"], 2)

    def test_chunked_polish_resume_reuses_existing_chunk_artifact(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "notes.md"
            chunk_dir = polish.chunk_artifact_dir(str(output_path))
            assert chunk_dir is not None
            chunk_dir.mkdir()
            (chunk_dir / "chunk-001.md").write_text("existing chunk\n", encoding="utf-8")
            calls = []

            def fake_polish(**kwargs):
                calls.append(kwargs)
                if len(calls) == 1:
                    self.assertEqual(kwargs["transcript_text"], "second")
                    return {
                        "output_path": runs.write_text(
                            kwargs["out_path"],
                            "new chunk\n",
                        ),
                        "chars": len("new chunk\n"),
                        "harness": "codex",
                        "text": "new chunk\n",
                    }
                self.assertIn("existing chunk", kwargs["transcript_text"])
                self.assertIn("new chunk", kwargs["transcript_text"])
                return {
                    "output_path": runs.write_text(kwargs["out_path"], "merged\n"),
                    "chars": len("merged\n"),
                    "harness": "codex",
                    "text": "merged\n",
                }

            with patch.object(polish_chunked, "run_agent_polish", side_effect=fake_polish):
                result = polish.run_chunked_agent_polish(
                    chunks=["first", "second"],
                    instruction="Polish.",
                    out_path=str(output_path),
                    model=None,
                    cwd=None,
                    harness="codex",
                    chunk_chars=25,
                    resume=True,
                    progress=None,
                )

        self.assertEqual(len(calls), 2)
        self.assertEqual(result["chunking"]["resumed_chunks"], 1)
        self.assertEqual(result["chunking"]["merge_status"], "merged")

    def test_chunked_polish_resume_repolishes_empty_chunk_artifact(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "notes.md"
            chunk_dir = polish.chunk_artifact_dir(str(output_path))
            assert chunk_dir is not None
            chunk_dir.mkdir()
            (chunk_dir / "chunk-001.md").write_text("", encoding="utf-8")
            calls = []

            def fake_polish(**kwargs):
                calls.append(kwargs)
                if len(calls) == 1:
                    self.assertEqual(kwargs["transcript_text"], "first")
                    return {
                        "output_path": runs.write_text(kwargs["out_path"], "first chunk\n"),
                        "chars": len("first chunk\n"),
                        "harness": "codex",
                        "text": "first chunk\n",
                    }
                if len(calls) == 2:
                    self.assertEqual(kwargs["transcript_text"], "second")
                    return {
                        "output_path": runs.write_text(kwargs["out_path"], "second chunk\n"),
                        "chars": len("second chunk\n"),
                        "harness": "codex",
                        "text": "second chunk\n",
                    }
                self.assertIn("first chunk", kwargs["transcript_text"])
                self.assertIn("second chunk", kwargs["transcript_text"])
                return {
                    "output_path": runs.write_text(kwargs["out_path"], "merged\n"),
                    "chars": len("merged\n"),
                    "harness": "codex",
                    "text": "merged\n",
                }

            with patch.object(polish_chunked, "run_agent_polish", side_effect=fake_polish):
                result = polish.run_chunked_agent_polish(
                    chunks=["first", "second"],
                    instruction="Polish.",
                    out_path=str(output_path),
                    model=None,
                    cwd=None,
                    harness="codex",
                    chunk_chars=25,
                    resume=True,
                    progress=None,
                )

        self.assertEqual(len(calls), 3)
        self.assertEqual(result["chunking"]["resumed_chunks"], 0)
        self.assertEqual(result["chunking"]["merge_status"], "merged")

    def test_polish_with_timestamps_reports_json_and_prompt_contract(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            transcript_path = Path(tmp_dir) / "transcript.txt"
            transcript_path.write_text("[00:01] hello world\n", encoding="utf-8")
            ignored_output = Path(tmp_dir) / "ignored.md"
            args = cli.build_parser().parse_args(
                [
                    "--json",
                    "polish",
                    str(transcript_path),
                    "--out",
                    str(ignored_output),
                    "--stdout",
                    "--timestamps",
                ],
            )
            stdout = io.StringIO()

            with (
                patch_cli_workflow(
                    "run_agent_polish",
                    return_value={
                        "output_path": None,
                        "chars": len("polished text\n"),
                        "harness": "codex",
                        "text": "polished text\n",
                    },
                ) as polish,
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                exit_code = cli.handle_args(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(polish.call_args.kwargs["transcript_text"], "[00:01] hello world\n")
        self.assertIsNone(polish.call_args.kwargs["out_path"])
        self.assertFalse(ignored_output.exists())
        self.assertIn("timestamp anchors", polish.call_args.kwargs["instruction"])
        self.assertTrue(payload["polish"]["timestamp_grounding"])

    def test_run_front_matter_is_written_to_output_file(self):
        transcript = {
            "video_id": "dQw4w9WgXcQ",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "language": "en",
            "track": {
                "name": "English",
                "language_code": "en",
                "auto_generated": False,
                "is_translatable": True,
            },
            "segments": [{"text": "hello world"}],
            "text": "hello world",
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "notes.md"
            args = cli.build_parser().parse_args(
                [
                    "--json",
                    "run",
                    "dQw4w9WgXcQ",
                    "--out",
                    str(output_path),
                    "--front-matter",
                ],
            )
            stdout = io.StringIO()

            with (
                patch_cli_workflow("fetch_transcript", return_value=transcript),
                patch_cli_workflow(
                    "run_agent_polish",
                    side_effect=lambda **kwargs: {
                        "output_path": runs.write_text(kwargs["out_path"], "polished text\n"),
                        "chars": len("polished text\n"),
                        "harness": "codex",
                        "text": "polished text\n",
                    },
                ),
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = cli.handle_args(args)

            payload = json.loads(stdout.getvalue())
            content = output_path.read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["run"]["front_matter"])
        self.assertTrue(content.startswith("---\n"))
        self.assertIn("video_id: dQw4w9WgXcQ\n", content)
        self.assertIn("language: en\n", content)
        self.assertIn("caption_auto_generated: false\n", content)
        self.assertIn("agent_harness: codex\n", content)
        self.assertTrue(content.endswith("polished text\n"))

    def test_run_front_matter_is_printed_to_stdout(self):
        transcript = {
            "video_id": "dQw4w9WgXcQ",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "language": "en",
            "track": {
                "name": "English",
                "language_code": "en",
                "auto_generated": False,
                "is_translatable": True,
            },
            "segments": [{"text": "hello world"}],
            "text": "hello world",
        }
        args = cli.build_parser().parse_args(
            ["run", "dQw4w9WgXcQ", "--stdout", "--front-matter"],
        )
        stdout = io.StringIO()

        with (
            patch_cli_workflow("fetch_transcript", return_value=transcript),
            patch_cli_workflow(
                "run_agent_polish",
                return_value={
                    "output_path": None,
                    "chars": len("polished text\n"),
                    "harness": "codex",
                    "text": "polished text\n",
                },
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            exit_code = cli.handle_args(args)

        self.assertEqual(exit_code, 0)
        self.assertTrue(stdout.getvalue().startswith("---\n"))
        self.assertIn("video_id: dQw4w9WgXcQ\n", stdout.getvalue())
        self.assertTrue(stdout.getvalue().endswith("polished text\n"))

    def test_run_bundle_dir_writes_transcript_output_and_metadata(self):
        transcript = {
            "video_id": "dQw4w9WgXcQ",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "language": "en",
            "requested_languages": ["en"],
            "track": {"language_code": "en"},
            "segments": [{"start": 1.0, "duration": 0.5, "text": "hello world"}],
            "text": "hello world",
            "source": "youtube_transcript_api",
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_dir = Path(tmp_dir) / "bundle"
            args = cli.build_parser().parse_args(
                ["--json", "run", "dQw4w9WgXcQ", "--bundle-dir", str(bundle_dir)],
            )
            stdout = io.StringIO()

            with (
                patch_cli_workflow("fetch_transcript", return_value=transcript),
                patch_cli_workflow(
                    "run_agent_polish",
                    side_effect=lambda **kwargs: {
                        "output_path": runs.write_text(kwargs["out_path"], "polished\n"),
                        "chars": len("polished\n"),
                        "harness": "codex",
                        "text": "polished\n",
                    },
                ),
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = cli.handle_args(args)

            payload = json.loads(stdout.getvalue())
            metadata = json.loads((bundle_dir / "metadata.json").read_text(encoding="utf-8"))

            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["run"]["bundle"]["dir"], str(bundle_dir.resolve()))
            self.assertTrue((bundle_dir / "transcript.txt").is_file())
            self.assertTrue((bundle_dir / "polished.md").is_file())
            self.assertEqual(metadata["video_id"], "dQw4w9WgXcQ")
            self.assertEqual(metadata["instruction_sources"], ["--style"])

    def test_run_bundle_metadata_includes_front_matter_and_record_slots(self):
        transcript = {
            "video_id": "dQw4w9WgXcQ",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "language": "en",
            "requested_languages": ["en"],
            "track": {"language_code": "en", "name": "English", "auto_generated": False},
            "segments": [{"start": 1.0, "duration": 0.5, "text": "hello world"}],
            "text": "hello world",
            "source": "youtube_transcript_api",
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_dir = Path(tmp_dir) / "bundle"
            args = cli.build_parser().parse_args(
                [
                    "--json",
                    "run",
                    "dQw4w9WgXcQ",
                    "--bundle-dir",
                    str(bundle_dir),
                    "--front-matter",
                ],
            )
            stdout = io.StringIO()

            with (
                patch_cli_workflow("fetch_transcript", return_value=transcript),
                patch_cli_workflow(
                    "run_agent_polish",
                    side_effect=lambda **kwargs: {
                        "output_path": runs.write_text(kwargs["out_path"], "polished\n"),
                        "chars": len("polished\n"),
                        "harness": "codex",
                        "text": "polished\n",
                    },
                ),
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = cli.handle_args(args)

            payload = json.loads(stdout.getvalue())
            metadata = json.loads((bundle_dir / "metadata.json").read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["run"]["front_matter"])
        self.assertEqual(metadata["front_matter_data"]["video_id"], "dQw4w9WgXcQ")
        self.assertEqual(metadata["front_matter_data"]["caption_name"], "English")
        self.assertEqual(metadata["records"], {"manifest": None, "verification": None})
        self.assertEqual(
            payload["run"]["bundle"]["records"],
            {"manifest": None, "verification": None},
        )

    def test_run_json_reports_requested_language_fallbacks(self):
        transcript = {
            "video_id": "dQw4w9WgXcQ",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "language": "en-GB",
            "requested_languages": ["en", "en-GB"],
            "track": {"language_code": "en-GB"},
            "segments": [{"text": "hello world"}],
            "text": "hello world",
        }
        args = cli.build_parser().parse_args(
            ["--json", "run", "dQw4w9WgXcQ", "--langs", "en,en-GB", "--stdout"],
        )
        stdout = io.StringIO()

        with (
            patch_cli_workflow("fetch_transcript", return_value=transcript) as fetch,
            patch_cli_workflow(
                "run_agent_polish",
                return_value={
                    "output_path": None,
                    "chars": 14,
                    "harness": "codex",
                    "text": "polished text\n",
                },
            ),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = cli.handle_args(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(fetch.call_args.args[1], ["en", "en-GB"])
        self.assertEqual(payload["run"]["requested_languages"], ["en", "en-GB"])
        self.assertEqual(payload["run"]["language"], "en-GB")

    def test_run_auto_workflow_selects_quick_below_duration_threshold(self):
        transcript = self.sample_transcript()
        args = cli.build_parser().parse_args(
            ["--json", "run", "dQw4w9WgXcQ", "--stdout"],
        )
        stdout = io.StringIO()

        with (
            patch_cli_workflow("fetch_video_duration_seconds", return_value=2699),
            patch_cli_workflow("fetch_transcript", return_value=transcript),
            patch_cli_workflow(
                "run_agent_polish",
                return_value={
                    "output_path": None,
                    "chars": len("polished text\n"),
                    "harness": "codex",
                    "text": "polished text\n",
                },
            ),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = cli.handle_args(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["run"]["workflow"], "quick")
        self.assertEqual(payload["run"]["workflow_requested"], "auto")
        self.assertEqual(payload["run"]["workflow_reason"], "duration_below_threshold")
        self.assertEqual(payload["run"]["duration_seconds"], 2699)
        self.assertEqual(payload["run"]["workflow_threshold_seconds"], 2700)

    def test_run_auto_workflow_selects_deep_at_duration_threshold(self):
        transcript = self.sample_transcript()
        args = cli.build_parser().parse_args(
            ["--json", "run", "dQw4w9WgXcQ", "--stdout"],
        )
        stdout = io.StringIO()

        with (
            patch_cli_workflow("fetch_video_duration_seconds", return_value=2700),
            patch_cli_workflow("fetch_transcript", return_value=transcript),
            patch_cli_workflow(
                "run_agent_polish",
                return_value={
                    "output_path": None,
                    "chars": len("polished text\n"),
                    "harness": "codex",
                    "text": "polished text\n",
                },
            ),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = cli.handle_args(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["run"]["workflow"], "deep")
        self.assertEqual(payload["run"]["workflow_requested"], "auto")
        self.assertEqual(payload["run"]["workflow_reason"], "duration_at_or_above_threshold")
        self.assertEqual(payload["run"]["duration_seconds"], 2700)

    def test_run_explicit_workflow_overrides_auto_selection(self):
        transcript = self.sample_transcript()

        for workflow in ("quick", "deep"):
            with self.subTest(workflow=workflow):
                args = cli.build_parser().parse_args(
                    ["--json", "run", "dQw4w9WgXcQ", "--stdout", "--workflow", workflow],
                )
                stdout = io.StringIO()

                with (
                    patch_cli_workflow("fetch_video_duration_seconds") as fetch_duration,
                    patch_cli_workflow("fetch_transcript", return_value=transcript),
                    patch_cli_workflow(
                        "run_agent_polish",
                        return_value={
                            "output_path": None,
                            "chars": len("polished text\n"),
                            "harness": "codex",
                            "text": "polished text\n",
                        },
                    ),
                    contextlib.redirect_stdout(stdout),
                ):
                    exit_code = cli.handle_args(args)

                payload = json.loads(stdout.getvalue())
                self.assertEqual(exit_code, 0)
                self.assertEqual(payload["run"]["workflow"], workflow)
                self.assertEqual(payload["run"]["workflow_requested"], workflow)
                self.assertEqual(payload["run"]["workflow_reason"], f"explicit_{workflow}")
                self.assertIsNone(payload["run"]["duration_seconds"])
                fetch_duration.assert_not_called()

    def test_run_auto_workflow_uses_quick_when_duration_is_missing(self):
        transcript = self.sample_transcript()
        args = cli.build_parser().parse_args(
            ["--json", "run", "dQw4w9WgXcQ", "--stdout"],
        )
        stdout = io.StringIO()

        with (
            patch_cli_workflow("fetch_video_duration_seconds", return_value=None),
            patch_cli_workflow("fetch_transcript", return_value=transcript) as fetch,
            patch_cli_workflow(
                "run_agent_polish",
                return_value={
                    "output_path": None,
                    "chars": len("polished text\n"),
                    "harness": "codex",
                    "text": "polished text\n",
                },
            ),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = cli.handle_args(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["run"]["workflow"], "quick")
        self.assertEqual(payload["run"]["workflow_reason"], "duration_unknown")
        self.assertIsNone(payload["run"]["duration_seconds"])
        fetch.assert_called_once()

    def test_run_human_output_reports_workflow_when_writing_file(self):
        transcript = self.sample_transcript()

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "notes.md"
            args = cli.build_parser().parse_args(
                ["run", "dQw4w9WgXcQ", "--out", str(output_path)],
            )
            stdout = io.StringIO()

            with (
                patch_cli_workflow("fetch_video_duration_seconds", return_value=2700),
                patch_cli_workflow("fetch_transcript", return_value=transcript),
                patch_cli_workflow(
                    "run_agent_polish",
                    side_effect=lambda **kwargs: {
                        "output_path": runs.write_text(kwargs["out_path"], "polished\n"),
                        "chars": len("polished\n"),
                        "harness": "codex",
                        "text": "polished\n",
                    },
                ),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                exit_code = cli.handle_args(args)

        self.assertEqual(exit_code, 0)
        self.assertIn("Workflow: deep (duration_at_or_above_threshold)", stdout.getvalue())
        self.assertIn("Wrote polished transcript", stdout.getvalue())

    def test_deep_run_creates_managed_registry_outside_current_project(self):
        transcript = self.sample_transcript()

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_dir = root / "data"
            project_dir = root / "project"
            project_dir.mkdir()
            previous_cwd = Path.cwd()
            os.chdir(project_dir)
            try:
                args = cli.build_parser().parse_args(
                    ["--json", "run", "dQw4w9WgXcQ"],
                )
                stdout = io.StringIO()

                with (
                    patch.dict(os.environ, {runs.DATA_DIR_ENV_VAR: str(data_dir)}),
                    patch_cli_workflow("fetch_video_duration_seconds", return_value=2700),
                    patch_cli_workflow("fetch_video_title", return_value="Long Video Talk"),
                    patch_cli_workflow("fetch_transcript", return_value=transcript),
                    patch_cli_workflow(
                        "run_agent_polish",
                        side_effect=lambda **kwargs: {
                            "output_path": runs.write_text(kwargs["out_path"], "polished\n"),
                            "chars": len("polished\n"),
                            "harness": "codex",
                            "text": "polished\n",
                        },
                    ),
                    contextlib.redirect_stdout(stdout),
                ):
                    exit_code = cli.handle_args(args)
            finally:
                os.chdir(previous_cwd)

            payload = json.loads(stdout.getvalue())
            registry = runs.load_run_registry(data_dir)

        bundle_dir = Path(payload["run"]["bundle"]["dir"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["run"]["workflow"], "deep")
        self.assertEqual(payload["run"]["managed_run"]["name"], "long-video-talk-dQw4w9WgXcQ")
        self.assertEqual(payload["run"]["managed_run"]["video_id"], "dQw4w9WgXcQ")
        self.assertEqual(payload["run"]["managed_run"]["title"], "Long Video Talk")
        self.assertTrue(payload["run"]["managed_run"]["managed"])
        self.assertTrue(bundle_dir.is_relative_to(data_dir.resolve()))
        self.assertFalse(bundle_dir.is_relative_to(project_dir.resolve()))
        self.assertEqual(registry["runs"][0]["video_id"], "dQw4w9WgXcQ")
        self.assertEqual(registry["runs"][0]["bundle_path"], str(bundle_dir))

    def test_runs_list_open_and_rename_managed_run(self):
        transcript = self.sample_transcript()

        with tempfile.TemporaryDirectory() as tmp_dir:
            data_dir = Path(tmp_dir) / "data"
            run_args = cli.build_parser().parse_args(
                ["--json", "run", "dQw4w9WgXcQ", "--workflow", "deep"],
            )
            run_stdout = io.StringIO()
            with (
                patch.dict(os.environ, {runs.DATA_DIR_ENV_VAR: str(data_dir)}),
                patch_cli_workflow("fetch_video_title", return_value="Original Talk"),
                patch_cli_workflow("fetch_transcript", return_value=transcript),
                patch_cli_workflow(
                    "run_agent_polish",
                    side_effect=lambda **kwargs: {
                        "output_path": runs.write_text(kwargs["out_path"], "polished\n"),
                        "chars": len("polished\n"),
                        "harness": "codex",
                        "text": "polished\n",
                    },
                ),
                contextlib.redirect_stdout(run_stdout),
            ):
                self.assertEqual(cli.handle_args(run_args), 0)
            created = json.loads(run_stdout.getvalue())["run"]["managed_run"]

            list_args = cli.build_parser().parse_args(["--json", "runs", "list"])
            list_stdout = io.StringIO()
            with (
                patch.dict(os.environ, {runs.DATA_DIR_ENV_VAR: str(data_dir)}),
                contextlib.redirect_stdout(list_stdout),
            ):
                self.assertEqual(cli.handle_args(list_args), 0)

            open_args = cli.build_parser().parse_args(
                ["--json", "runs", "open", "original-talk"],
            )
            open_stdout = io.StringIO()
            with (
                patch.dict(os.environ, {runs.DATA_DIR_ENV_VAR: str(data_dir)}),
                contextlib.redirect_stdout(open_stdout),
            ):
                self.assertEqual(cli.handle_args(open_args), 0)

            rename_args = cli.build_parser().parse_args(
                ["--json", "runs", "rename", created["name"], "Project Vocabulary"],
            )
            rename_stdout = io.StringIO()
            with (
                patch.dict(os.environ, {runs.DATA_DIR_ENV_VAR: str(data_dir)}),
                contextlib.redirect_stdout(rename_stdout),
            ):
                self.assertEqual(cli.handle_args(rename_args), 0)

            list_payload = json.loads(list_stdout.getvalue())
            open_payload = json.loads(open_stdout.getvalue())
            rename_payload = json.loads(rename_stdout.getvalue())
            old_bundle_exists = Path(created["bundle_path"]).exists()
            renamed_bundle_is_dir = Path(rename_payload["run"]["bundle_path"]).is_dir()

        self.assertEqual(list_payload["runs"][0]["name"], "original-talk-dQw4w9WgXcQ")
        self.assertEqual(open_payload["run"]["bundle_path"], created["bundle_path"])
        self.assertEqual(rename_payload["run"]["name"], "project-vocabulary-dQw4w9WgXcQ")
        self.assertEqual(rename_payload["run"]["title"], "Project Vocabulary")
        self.assertFalse(old_bundle_exists)
        self.assertTrue(renamed_bundle_is_dir)

    def test_deep_run_name_collision_adds_suffix(self):
        transcript = self.sample_transcript()

        with tempfile.TemporaryDirectory() as tmp_dir:
            data_dir = Path(tmp_dir) / "data"
            names = []
            with patch.dict(os.environ, {runs.DATA_DIR_ENV_VAR: str(data_dir)}):
                for _ in range(2):
                    args = cli.build_parser().parse_args(
                        ["--json", "run", "dQw4w9WgXcQ", "--workflow", "deep"],
                    )
                    stdout = io.StringIO()
                    with (
                        patch_cli_workflow("fetch_video_title", return_value="Same Talk"),
                        patch_cli_workflow("fetch_transcript", return_value=transcript),
                        patch_cli_workflow(
                            "run_agent_polish",
                            side_effect=lambda **kwargs: {
                                "output_path": runs.write_text(
                                    kwargs["out_path"],
                                    "polished\n",
                                ),
                                "chars": len("polished\n"),
                                "harness": "codex",
                                "text": "polished\n",
                            },
                        ),
                        contextlib.redirect_stdout(stdout),
                    ):
                        self.assertEqual(cli.handle_args(args), 0)
                    names.append(json.loads(stdout.getvalue())["run"]["managed_run"]["name"])

        self.assertEqual(names, ["same-talk-dQw4w9WgXcQ", "same-talk-dQw4w9WgXcQ-2"])

    def test_deep_run_writes_bundle_artifacts_and_chunk_manifest(self):
        transcript = {
            **self.sample_transcript(),
            "segments": [
                {"start": 0.0, "duration": 120.0, "text": "intro"},
                {"start": 120.0, "duration": 120.0, "text": "background"},
                {"start": 240.0, "duration": 120.0, "text": "design"},
                {"start": 360.0, "duration": 120.0, "text": "tradeoffs"},
                {"start": 480.0, "duration": 120.0, "text": "demo"},
                {"start": 600.0, "duration": 120.0, "text": "wrap"},
            ],
            "text": "intro\nbackground\ndesign\ntradeoffs\ndemo\nwrap",
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_dir = Path(tmp_dir) / "bundle"
            args = cli.build_parser().parse_args(
                [
                    "--json",
                    "run",
                    "dQw4w9WgXcQ",
                    "--workflow",
                    "deep",
                    "--bundle-dir",
                    str(bundle_dir),
                ],
            )
            stdout = io.StringIO()

            with (
                patch_cli_workflow("fetch_video_title", return_value="Chunk Planning Talk"),
                patch_cli_workflow("fetch_transcript", return_value=transcript),
                patch_cli_workflow(
                    "run_agent_polish",
                    side_effect=lambda **kwargs: {
                        "output_path": runs.write_text(kwargs["out_path"], "polished\n"),
                        "chars": len("polished\n"),
                        "harness": "codex",
                        "text": "polished\n",
                    },
                ),
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(cli.handle_args(args), 0)

            payload = json.loads(stdout.getvalue())
            transcript_json = json.loads((bundle_dir / "transcript.json").read_text())
            transcript_text = (bundle_dir / "transcript.txt").read_text(encoding="utf-8")
            manifest = json.loads((bundle_dir / "chunk-manifest.json").read_text())
            metadata = json.loads((bundle_dir / "metadata.json").read_text())
            verification = json.loads((bundle_dir / "structural-verification.json").read_text())
            chunk_file_exists = (bundle_dir / "chunks" / "chunk-001.txt").is_file()

        self.assertEqual(transcript_json, transcript)
        self.assertIn("[00:00] intro", transcript_text)
        self.assertIn("[10:00] wrap", transcript_text)
        self.assertEqual(
            payload["run"]["bundle"]["transcript_json"],
            str(bundle_dir / "transcript.json"),
        )
        self.assertTrue(chunk_file_exists)
        self.assertEqual(manifest["chunks"][0]["id"], "chunk-001")
        self.assertEqual(manifest["chunks"][0]["source_segments"], {"start": 0, "end": 5})
        self.assertTrue(manifest["chunks"][0]["text_path"].endswith("chunk-001.txt"))
        self.assertTrue(manifest["chunks"][0]["note_path"].endswith("chunk-001-notes.md"))
        self.assertEqual(metadata["title"], "Chunk Planning Talk")
        self.assertEqual(metadata["bundle_status"], "completed")
        self.assertTrue(verification["ok"])
        self.assertEqual(verification["missing"], [])

    def test_deep_run_stdout_still_writes_final_bundle_artifact(self):
        transcript = self.deep_engine_transcript()

        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_dir = Path(tmp_dir) / "bundle"
            args = cli.build_parser().parse_args(
                [
                    "run",
                    "dQw4w9WgXcQ",
                    "--workflow",
                    "deep",
                    "--agent-harness",
                    "opencode",
                    "--bundle-dir",
                    str(bundle_dir),
                    "--stdout",
                ],
            )
            stdout = io.StringIO()

            def fake_polish(**kwargs):
                out_path = Path(kwargs["out_path"])
                text = (
                    "merged stdout notes\n"
                    if out_path.name == "polished.md"
                    else "chunk notes\n"
                )
                return {
                    "output_path": runs.write_text(kwargs["out_path"], text),
                    "chars": len(text),
                    "harness": "opencode",
                    "text": text,
                }

            with (
                patch_cli_workflow("fetch_video_title", return_value="Stdout Talk"),
                patch_cli_workflow("fetch_transcript", return_value=transcript),
                patch_cli_workflow("run_agent_polish", side_effect=fake_polish),
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(cli.handle_args(args), 0)

            self.assertEqual(stdout.getvalue(), "merged stdout notes\n")
            self.assertEqual(
                (bundle_dir / "polished.md").read_text(encoding="utf-8"),
                "merged stdout notes\n",
            )

    def test_deep_chunk_plan_uses_overlap_and_segment_boundaries(self):
        transcript = {
            "segments": [
                {"start": 0.0, "duration": 60.0, "text": "a"},
                {"start": 60.0, "duration": 60.0, "text": "b"},
                {"start": 120.0, "duration": 60.0, "text": "c"},
                {"start": 180.0, "duration": 60.0, "text": "d"},
            ]
        }

        chunks = transcripts.plan_deep_chunks(
            transcript,
            target_seconds=180,
            max_chars=10_000,
            overlap_seconds=60,
        )

        self.assertEqual(
            [chunk["source_segments"] for chunk in chunks],
            [{"start": 0, "end": 3}, {"start": 2, "end": 4}],
        )
        self.assertEqual(chunks[1]["start_seconds"], 120.0)

    def test_deep_chunk_plan_keeps_dense_segment_whole(self):
        transcript = {
            "segments": [
                {"start": 0.0, "duration": 30.0, "text": "x" * 100},
                {"start": 30.0, "duration": 30.0, "text": "after"},
            ]
        }

        chunks = transcripts.plan_deep_chunks(
            transcript,
            target_seconds=30,
            max_chars=10,
            overlap_seconds=5,
        )

        self.assertEqual(chunks[0]["source_segments"], {"start": 0, "end": 1})
        self.assertIn("x" * 100, chunks[0]["text"])

    def deep_engine_transcript(self) -> dict[str, object]:
        return {
            **self.sample_transcript(),
            "segments": [
                {"start": 0.0, "duration": 600.0, "text": "first long chunk"},
                {"start": 600.0, "duration": 600.0, "text": "second long chunk"},
            ],
            "text": "first long chunk\nsecond long chunk",
        }

    def test_deep_fallback_engine_runs_chunks_and_merge_for_harnesses(self):
        for harness in ("codex", "opencode"):
            with self.subTest(harness=harness), tempfile.TemporaryDirectory() as tmp_dir:
                bundle_dir = Path(tmp_dir) / harness
                transcript = self.deep_engine_transcript()
                args = cli.build_parser().parse_args(
                    [
                        "--json",
                        "run",
                        "dQw4w9WgXcQ",
                        "--workflow",
                        "deep",
                        "--agent-harness",
                        harness,
                        "--bundle-dir",
                        str(bundle_dir),
                    ],
                )
                stdout = io.StringIO()
                calls = []

                def fake_polish(_harness=harness, _calls=calls, **kwargs):
                    _calls.append(kwargs)
                    out_path = Path(kwargs["out_path"])
                    if out_path.name == "polished.md":
                        text = "merged final notes\n"
                    else:
                        text = f"notes for {out_path.stem}\n"
                    return {
                        "output_path": runs.write_text(kwargs["out_path"], text),
                        "chars": len(text),
                        "harness": _harness,
                        "text": text,
                    }

                with (
                    patch_cli_workflow("fetch_video_title", return_value="Fallback Talk"),
                    patch_cli_workflow("fetch_transcript", return_value=transcript),
                    patch_cli_workflow("run_agent_polish", side_effect=fake_polish),
                    contextlib.redirect_stdout(stdout),
                ):
                    self.assertEqual(cli.handle_args(args), 0)

                payload = json.loads(stdout.getvalue())
                metadata = json.loads((bundle_dir / "metadata.json").read_text())

                self.assertEqual(len(calls), 3)
                self.assertTrue((bundle_dir / "chunks" / "chunk-001-notes.md").is_file())
                self.assertTrue((bundle_dir / "chunks" / "chunk-002-notes.md").is_file())
                self.assertEqual((bundle_dir / "polished.md").read_text(), "merged final notes\n")
                self.assertEqual(payload["run"]["chunking"]["engine"], "managed_fallback")
                self.assertEqual(payload["run"]["chunking"]["merge_status"], "merged")
                self.assertEqual(metadata["bundle_status"], "completed")
                self.assertEqual(metadata["engine"]["status"], "completed")
                self.assertEqual(metadata["engine"]["harness"], harness)

    def test_deep_fallback_failure_records_incomplete_and_skips_merge(self):
        transcript = self.deep_engine_transcript()

        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_dir = Path(tmp_dir) / "bundle"
            args = [
                "--json",
                "run",
                "dQw4w9WgXcQ",
                "--workflow",
                "deep",
                "--bundle-dir",
                str(bundle_dir),
            ]
            stdout = io.StringIO()
            calls = []

            def fake_polish(**kwargs):
                calls.append(kwargs)
                out_path = Path(kwargs["out_path"])
                if out_path.name == "chunk-002-notes.md":
                    raise package.CliError("chunk failed", "agent_failed")
                text = "chunk one notes\n"
                return {
                    "output_path": runs.write_text(kwargs["out_path"], text),
                    "chars": len(text),
                    "harness": "codex",
                    "text": text,
                }

            with (
                patch_cli_workflow("fetch_video_title", return_value="Failure Talk"),
                patch_cli_workflow("fetch_transcript", return_value=transcript),
                patch_cli_workflow("run_agent_polish", side_effect=fake_polish),
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = cli.main(args)

            payload = json.loads(stdout.getvalue())
            metadata = json.loads((bundle_dir / "metadata.json").read_text())

        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["error"]["code"], "deep_run_incomplete")
        self.assertEqual(metadata["bundle_status"], "incomplete")
        self.assertEqual(metadata["engine"]["status"], "incomplete")
        self.assertEqual(metadata["engine"]["failures"][0]["chunk_id"], "chunk-002")
        self.assertFalse((bundle_dir / "polished.md").exists())
        self.assertEqual(
            [Path(call["out_path"]).name for call in calls],
            ["chunk-001-notes.md", "chunk-002-notes.md"],
        )

    def test_deep_fallback_resume_retries_missing_chunks_and_merges(self):
        transcript = self.deep_engine_transcript()

        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_dir = Path(tmp_dir) / "bundle"
            first_args = [
                "--json",
                "run",
                "dQw4w9WgXcQ",
                "--workflow",
                "deep",
                "--bundle-dir",
                str(bundle_dir),
            ]

            def failing_polish(**kwargs):
                out_path = Path(kwargs["out_path"])
                if out_path.name == "chunk-002-notes.md":
                    raise package.CliError("chunk failed", "agent_failed")
                text = "chunk one notes\n"
                return {
                    "output_path": runs.write_text(kwargs["out_path"], text),
                    "chars": len(text),
                    "harness": "codex",
                    "text": text,
                }

            with (
                patch_cli_workflow("fetch_video_title", return_value="Resume Talk"),
                patch_cli_workflow("fetch_transcript", return_value=transcript),
                patch_cli_workflow("run_agent_polish", side_effect=failing_polish),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(cli.main(first_args), 1)

            second_args = [
                "--json",
                "run",
                "dQw4w9WgXcQ",
                "--workflow",
                "deep",
                "--bundle-dir",
                str(bundle_dir),
                "--resume",
            ]
            second_stdout = io.StringIO()
            second_calls = []

            def completing_polish(**kwargs):
                second_calls.append(Path(kwargs["out_path"]).name)
                out_path = Path(kwargs["out_path"])
                text = "merged notes\n" if out_path.name == "polished.md" else "chunk two notes\n"
                return {
                    "output_path": runs.write_text(kwargs["out_path"], text),
                    "chars": len(text),
                    "harness": "codex",
                    "text": text,
                }

            with (
                patch_cli_workflow("fetch_video_title", return_value="Resume Talk"),
                patch_cli_workflow("fetch_transcript", return_value=transcript),
                patch_cli_workflow("run_agent_polish", side_effect=completing_polish),
                contextlib.redirect_stdout(second_stdout),
            ):
                parsed_args = cli.build_parser().parse_args(second_args)
                self.assertEqual(cli.handle_args(parsed_args), 0)

            payload = json.loads(second_stdout.getvalue())
            metadata = json.loads((bundle_dir / "metadata.json").read_text())

        self.assertEqual(second_calls, ["chunk-002-notes.md", "polished.md"])
        self.assertEqual(payload["run"]["chunking"]["resumed_chunks"], 1)
        self.assertEqual(metadata["bundle_status"], "completed")
        self.assertEqual(metadata["engine"]["status"], "completed")

    def test_codex_csv_fanout_writes_worker_results_and_merges(self):
        transcript = self.deep_engine_transcript()

        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_dir = Path(tmp_dir) / "bundle"
            args = cli.build_parser().parse_args(
                [
                    "--json",
                    "run",
                    "dQw4w9WgXcQ",
                    "--workflow",
                    "deep",
                    "--agent-harness",
                    "codex",
                    "--bundle-dir",
                    str(bundle_dir),
                ],
            )
            stdout = io.StringIO()
            merge_calls = []

            def fake_fanout(jobs, **kwargs):
                return {
                    "results": [
                        {
                            "chunk_id": job["chunk_id"],
                            "text": f"worker notes for {job['chunk_id']}\n",
                        }
                        for job in jobs
                    ],
                    "failures": [],
                }

            def fake_merge(**kwargs):
                merge_calls.append(kwargs)
                text = "merged fanout notes\n"
                return {
                    "output_path": runs.write_text(kwargs["out_path"], text),
                    "chars": len(text),
                    "harness": "codex",
                    "text": text,
                }

            with (
                patch_cli_workflow("fetch_video_title", return_value="Fanout Talk"),
                patch_cli_workflow("fetch_transcript", return_value=transcript),
                patch_cli_workflow("run_codex_csv_fanout_jobs", side_effect=fake_fanout),
                patch_cli_workflow("run_agent_polish", side_effect=fake_merge),
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(cli.handle_args(args), 0)

            payload = json.loads(stdout.getvalue())
            metadata = json.loads((bundle_dir / "metadata.json").read_text())
            chunk_one_notes = (bundle_dir / "chunks" / "chunk-001-notes.md").read_text()

        self.assertEqual(len(merge_calls), 1)
        self.assertEqual(chunk_one_notes, "worker notes for chunk-001\n")
        self.assertEqual(payload["run"]["chunking"]["engine"], "codex_csv_fanout")
        self.assertEqual(metadata["engine"]["name"], "codex_csv_fanout")
        self.assertEqual(metadata["codex_csv_fanout"]["status"], "completed")
        self.assertFalse(metadata["codex_csv_fanout"]["fallback_used"])

    def test_codex_csv_fanout_unavailable_falls_back_to_managed_engine(self):
        transcript = self.deep_engine_transcript()

        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_dir = Path(tmp_dir) / "bundle"
            args = cli.build_parser().parse_args(
                [
                    "--json",
                    "run",
                    "dQw4w9WgXcQ",
                    "--workflow",
                    "deep",
                    "--agent-harness",
                    "codex",
                    "--bundle-dir",
                    str(bundle_dir),
                ],
            )
            stdout = io.StringIO()
            calls = []

            def fake_polish(**kwargs):
                calls.append(Path(kwargs["out_path"]).name)
                out_path = Path(kwargs["out_path"])
                text = "merged notes\n" if out_path.name == "polished.md" else "chunk notes\n"
                return {
                    "output_path": runs.write_text(kwargs["out_path"], text),
                    "chars": len(text),
                    "harness": "codex",
                    "text": text,
                }

            with (
                patch_cli_workflow("fetch_video_title", return_value="Fallback Fanout Talk"),
                patch_cli_workflow("fetch_transcript", return_value=transcript),
                patch_cli_workflow("run_agent_polish", side_effect=fake_polish),
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(cli.handle_args(args), 0)

            payload = json.loads(stdout.getvalue())
            metadata = json.loads((bundle_dir / "metadata.json").read_text())

        self.assertEqual(calls, ["chunk-001-notes.md", "chunk-002-notes.md", "polished.md"])
        self.assertEqual(payload["run"]["chunking"]["engine"], "managed_fallback")
        self.assertEqual(metadata["codex_csv_fanout"]["status"], "fallback")
        self.assertEqual(metadata["codex_csv_fanout"]["reason"], "codex_csv_fanout_unavailable")
        self.assertTrue(metadata["codex_csv_fanout"]["fallback_used"])

    def test_incomplete_codex_csv_fanout_falls_back_without_losing_notes(self):
        transcript = self.deep_engine_transcript()

        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_dir = Path(tmp_dir) / "bundle"
            args = cli.build_parser().parse_args(
                [
                    "--json",
                    "run",
                    "dQw4w9WgXcQ",
                    "--workflow",
                    "deep",
                    "--agent-harness",
                    "codex",
                    "--bundle-dir",
                    str(bundle_dir),
                ],
            )
            stdout = io.StringIO()
            fallback_calls = []

            def partial_fanout(jobs, **kwargs):
                return {
                    "results": [{"chunk_id": jobs[0]["chunk_id"], "text": "fanout chunk one\n"}],
                    "failures": [{"chunk_id": jobs[1]["chunk_id"], "message": "worker failed"}],
                }

            def fake_polish(**kwargs):
                fallback_calls.append(Path(kwargs["out_path"]).name)
                out_path = Path(kwargs["out_path"])
                text = (
                    "merged notes\n"
                    if out_path.name == "polished.md"
                    else "fallback chunk two\n"
                )
                return {
                    "output_path": runs.write_text(kwargs["out_path"], text),
                    "chars": len(text),
                    "harness": "codex",
                    "text": text,
                }

            with (
                patch_cli_workflow("fetch_video_title", return_value="Partial Fanout Talk"),
                patch_cli_workflow("fetch_transcript", return_value=transcript),
                patch_cli_workflow("run_codex_csv_fanout_jobs", side_effect=partial_fanout),
                patch_cli_workflow("run_agent_polish", side_effect=fake_polish),
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(cli.handle_args(args), 0)

            payload = json.loads(stdout.getvalue())
            metadata = json.loads((bundle_dir / "metadata.json").read_text())
            chunk_one_notes = (bundle_dir / "chunks" / "chunk-001-notes.md").read_text()

        self.assertEqual(fallback_calls, ["chunk-002-notes.md", "polished.md"])
        self.assertEqual(chunk_one_notes, "fanout chunk one\n")
        self.assertEqual(payload["run"]["chunking"]["resumed_chunks"], 1)
        self.assertEqual(metadata["codex_csv_fanout"]["status"], "fallback")
        self.assertEqual(metadata["codex_csv_fanout"]["failures"][0]["chunk_id"], "chunk-002")

    def test_opencode_server_engine_uses_local_session_and_verifies_artifacts(self):
        transcript = self.deep_engine_transcript()

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            bundle_dir = root / "bundle"
            project_dir = root / "project"
            project_dir.mkdir()
            args = cli.build_parser().parse_args(
                [
                    "--json",
                    "run",
                    "dQw4w9WgXcQ",
                    "--workflow",
                    "deep",
                    "--agent-harness",
                    "opencode",
                    "--bundle-dir",
                    str(bundle_dir),
                    "--cd",
                    str(project_dir),
                ],
            )
            stdout = io.StringIO()
            captured = {}

            def fake_server(**kwargs):
                captured.update(kwargs)
                manifest = json.loads((bundle_dir / "chunk-manifest.json").read_text())
                for chunk in manifest["chunks"]:
                    Path(chunk["note_path"]).write_text(
                        f"server notes for {chunk['id']}\n",
                        encoding="utf-8",
                    )
                (bundle_dir / "polished.md").write_text("server final notes\n", encoding="utf-8")
                return {"status": "completed", "session_id": "session-1", "events": 3}

            with (
                patch.dict(
                    os.environ,
                    {
                        "OPENCODE_SERVER_USERNAME": "user",
                        "OPENCODE_SERVER_PASSWORD": "secret",
                    },
                ),
                patch_cli_workflow("fetch_video_title", return_value="OpenCode Talk"),
                patch_cli_workflow("fetch_transcript", return_value=transcript),
                patch_cli_workflow("run_opencode_server_session", side_effect=fake_server),
                patch_cli_workflow("run_agent_polish") as fallback,
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(cli.handle_args(args), 0)

            payload = json.loads(stdout.getvalue())
            metadata = json.loads((bundle_dir / "metadata.json").read_text())
            opencode_dir_exists = (project_dir / ".opencode").exists()

        fallback.assert_not_called()
        self.assertEqual(captured["server"]["host"], "127.0.0.1")
        self.assertFalse(captured["server"]["cors"])
        self.assertEqual(
            captured["server"]["auth"],
            {"username": "user", "password": "secret"},
        )
        self.assertIn("bundle", captured["prompt"])
        self.assertEqual(payload["run"]["chunking"]["engine"], "opencode_server")
        self.assertEqual(metadata["engine"]["name"], "opencode_server")
        self.assertEqual(metadata["opencode_server"]["status"], "completed")
        self.assertFalse(opencode_dir_exists)

    def test_opencode_server_missing_artifacts_falls_back_to_managed_engine(self):
        transcript = self.deep_engine_transcript()

        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_dir = Path(tmp_dir) / "bundle"
            args = cli.build_parser().parse_args(
                [
                    "--json",
                    "run",
                    "dQw4w9WgXcQ",
                    "--workflow",
                    "deep",
                    "--agent-harness",
                    "opencode",
                    "--bundle-dir",
                    str(bundle_dir),
                ],
            )
            stdout = io.StringIO()
            fallback_calls = []

            def fake_polish(**kwargs):
                fallback_calls.append(Path(kwargs["out_path"]).name)
                out_path = Path(kwargs["out_path"])
                text = "merged notes\n" if out_path.name == "polished.md" else "chunk notes\n"
                return {
                    "output_path": runs.write_text(kwargs["out_path"], text),
                    "chars": len(text),
                    "harness": "opencode",
                    "text": text,
                }

            with (
                patch_cli_workflow("fetch_video_title", return_value="OpenCode Fallback"),
                patch_cli_workflow("fetch_transcript", return_value=transcript),
                patch_cli_workflow(
                    "run_opencode_server_session",
                    return_value={"status": "completed", "session_id": "session-1"},
                ),
                patch_cli_workflow("run_agent_polish", side_effect=fake_polish),
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(cli.handle_args(args), 0)

            payload = json.loads(stdout.getvalue())
            metadata = json.loads((bundle_dir / "metadata.json").read_text())

        self.assertEqual(
            fallback_calls,
            ["chunk-001-notes.md", "chunk-002-notes.md", "polished.md"],
        )
        self.assertEqual(payload["run"]["chunking"]["engine"], "managed_fallback")
        self.assertEqual(metadata["opencode_server"]["status"], "fallback")
        self.assertEqual(metadata["opencode_server"]["reason"], "opencode_server_incomplete")

    def test_opencode_server_unavailable_falls_back_to_managed_engine(self):
        transcript = self.deep_engine_transcript()

        with tempfile.TemporaryDirectory() as tmp_dir:
            bundle_dir = Path(tmp_dir) / "bundle"
            args = cli.build_parser().parse_args(
                [
                    "--json",
                    "run",
                    "dQw4w9WgXcQ",
                    "--workflow",
                    "deep",
                    "--agent-harness",
                    "opencode",
                    "--bundle-dir",
                    str(bundle_dir),
                ],
            )
            stdout = io.StringIO()
            fallback_calls = []

            def fake_polish(**kwargs):
                fallback_calls.append(Path(kwargs["out_path"]).name)
                out_path = Path(kwargs["out_path"])
                text = "merged notes\n" if out_path.name == "polished.md" else "chunk notes\n"
                return {
                    "output_path": runs.write_text(kwargs["out_path"], text),
                    "chars": len(text),
                    "harness": "opencode",
                    "text": text,
                }

            with (
                patch_cli_workflow("fetch_video_title", return_value="OpenCode Unavailable"),
                patch_cli_workflow("fetch_transcript", return_value=transcript),
                patch_cli_workflow("run_agent_polish", side_effect=fake_polish),
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(cli.handle_args(args), 0)

            metadata = json.loads((bundle_dir / "metadata.json").read_text())

        self.assertEqual(
            fallback_calls,
            ["chunk-001-notes.md", "chunk-002-notes.md", "polished.md"],
        )
        self.assertEqual(metadata["opencode_server"]["status"], "fallback")
        self.assertEqual(metadata["opencode_server"]["reason"], "opencode_server_unavailable")

    def write_synthetic_completed_run(self, data_dir: Path) -> dict[str, str]:
        bundle_dir = data_dir / "runs" / "retrieval-talk-dQw4w9WgXcQ"
        chunks_dir = bundle_dir / "chunks"
        chunks_dir.mkdir(parents=True)
        (bundle_dir / "outline.md").write_text(
            "## Retrieval Architecture\nLexical ranking chooses bounded context.\n",
            encoding="utf-8",
        )
        (chunks_dir / "chunk-001-notes.md").write_text(
            "Chunk notes: fallback engines retry missing chunks safely.\n",
            encoding="utf-8",
        )
        (chunks_dir / "chunk-002-notes.md").write_text(
            "Chunk notes: pricing details were not discussed.\n",
            encoding="utf-8",
        )
        (bundle_dir / "transcript.txt").write_text(
            "[00:10] Alpha setup detail.\n"
            "[12:34] The timestamped retrieval snippet mentions exact source anchors.\n"
            "[20:00] Unrelated closing remarks.\n",
            encoding="utf-8",
        )
        runs.save_run_registry(
            data_dir,
            {
                "version": 1,
                "runs": [
                    {
                        "run_id": "run-1",
                        "name": "retrieval-talk-dQw4w9WgXcQ",
                        "title": "Retrieval Talk",
                        "video_id": "dQw4w9WgXcQ",
                        "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                        "bundle_path": str(bundle_dir),
                        "managed": True,
                        "workflow": "deep",
                        "harness": "codex",
                        "engine": "managed_fallback",
                        "status": "completed",
                        "created_at": "2026-07-02T00:00:00Z",
                        "updated_at": "2026-07-02T00:00:00Z",
                    }
                ],
            },
        )
        return {"bundle_dir": str(bundle_dir)}

    def test_ask_show_context_retrieves_outline_chunk_notes_and_transcript(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_dir = Path(tmp_dir) / "data"
            self.write_synthetic_completed_run(data_dir)
            args = cli.build_parser().parse_args(
                [
                    "--json",
                    "ask",
                    "retrieval-talk",
                    "How does retrieval retry chunks and cite timestamp anchors?",
                    "--show-context",
                ],
            )
            stdout = io.StringIO()

            with (
                patch.dict(os.environ, {runs.DATA_DIR_ENV_VAR: str(data_dir)}),
                patch_cli_workflow("run_agent_polish") as agent,
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(cli.handle_args(args), 0)

            payload = json.loads(stdout.getvalue())

        agent.assert_not_called()
        kinds = [hit["kind"] for hit in payload["ask"]["hits"]]
        self.assertIn("outline", kinds)
        self.assertIn("chunk_note", kinds)
        self.assertIn("transcript", kinds)
        transcript_hit = next(hit for hit in payload["ask"]["hits"] if hit["kind"] == "transcript")
        self.assertEqual(transcript_hit["timestamp"], "12:34")

    def test_ask_no_hit_is_honest_without_agent_call(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_dir = Path(tmp_dir) / "data"
            self.write_synthetic_completed_run(data_dir)
            args = cli.build_parser().parse_args(
                ["--json", "ask", "dQw4w9WgXcQ", "What did they say about quantum bananas?"],
            )
            stdout = io.StringIO()

            with (
                patch.dict(os.environ, {runs.DATA_DIR_ENV_VAR: str(data_dir)}),
                patch_cli_workflow("run_agent_polish") as agent,
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(cli.handle_args(args), 0)

            payload = json.loads(stdout.getvalue())

        agent.assert_not_called()
        self.assertFalse(payload["ask"]["has_hits"])
        self.assertIn("No relevant context", payload["ask"]["answer"])

    def test_ask_agent_prompt_uses_only_retrieved_context(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_dir = Path(tmp_dir) / "data"
            self.write_synthetic_completed_run(data_dir)
            args = cli.build_parser().parse_args(
                [
                    "--json",
                    "ask",
                    "retrieval-talk-dQw4w9WgXcQ",
                    "What source anchors support retrieval?",
                    "--agent",
                    "--agent-harness",
                    "codex",
                ],
            )
            stdout = io.StringIO()

            with (
                patch.dict(os.environ, {runs.DATA_DIR_ENV_VAR: str(data_dir)}),
                patch_cli_workflow(
                    "run_agent_polish",
                    return_value={
                        "output_path": None,
                        "chars": len("grounded answer\n"),
                        "harness": "codex",
                        "text": "grounded answer\n",
                    },
                ) as agent,
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(cli.handle_args(args), 0)

            payload = json.loads(stdout.getvalue())
            context_text = agent.call_args.kwargs["transcript_text"]
            instruction = agent.call_args.kwargs["instruction"]

        self.assertEqual(payload["ask"]["answer"], "grounded answer\n")
        self.assertIn("[12:34]", context_text)
        self.assertNotIn("Unrelated closing remarks", context_text)
        self.assertIn("Answer the question using only the retrieved context", instruction)

    def test_rename_custom_bundle_does_not_move_user_bundle_directory(self):
        transcript = self.sample_transcript()

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            data_dir = root / "data"
            custom_bundle = root / "custom-bundle"
            run_args = cli.build_parser().parse_args(
                [
                    "--json",
                    "run",
                    "dQw4w9WgXcQ",
                    "--workflow",
                    "deep",
                    "--bundle-dir",
                    str(custom_bundle),
                ],
            )
            run_stdout = io.StringIO()
            with (
                patch.dict(os.environ, {runs.DATA_DIR_ENV_VAR: str(data_dir)}),
                patch_cli_workflow("fetch_video_title", return_value="Custom Bundle Talk"),
                patch_cli_workflow("fetch_transcript", return_value=transcript),
                patch_cli_workflow(
                    "run_agent_polish",
                    side_effect=lambda **kwargs: {
                        "output_path": runs.write_text(kwargs["out_path"], "polished\n"),
                        "chars": len("polished\n"),
                        "harness": "codex",
                        "text": "polished\n",
                    },
                ),
                contextlib.redirect_stdout(run_stdout),
            ):
                self.assertEqual(cli.handle_args(run_args), 0)
            created = json.loads(run_stdout.getvalue())["run"]["managed_run"]

            rename_args = cli.build_parser().parse_args(
                ["--json", "runs", "rename", created["name"], "Renamed Talk"],
            )
            rename_stdout = io.StringIO()
            with (
                patch.dict(os.environ, {runs.DATA_DIR_ENV_VAR: str(data_dir)}),
                contextlib.redirect_stdout(rename_stdout),
            ):
                self.assertEqual(cli.handle_args(rename_args), 0)

            renamed = json.loads(rename_stdout.getvalue())["run"]
            custom_bundle_exists = custom_bundle.is_dir()

        self.assertFalse(created["managed"])
        self.assertEqual(created["bundle_path"], str(custom_bundle.resolve()))
        self.assertEqual(renamed["bundle_path"], str(custom_bundle.resolve()))
        self.assertTrue(custom_bundle_exists)
        self.assertEqual(renamed["name"], "renamed-talk-dQw4w9WgXcQ")

    def test_run_registry_rejects_missing_and_ambiguous_prefixes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_dir = Path(tmp_dir) / "data"
            runs.save_run_registry(
                data_dir,
                {
                    "version": 1,
                    "runs": [
                        {
                            "run_id": "first",
                            "name": "alpha-dQw4w9WgXcQ",
                            "title": "Alpha",
                            "video_id": "dQw4w9WgXcQ",
                            "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                            "bundle_path": str(data_dir / "alpha-dQw4w9WgXcQ"),
                            "managed": True,
                            "workflow": "deep",
                            "harness": "codex",
                            "engine": "pending",
                            "status": "completed",
                            "created_at": "2026-07-02T00:00:00Z",
                            "updated_at": "2026-07-02T00:00:00Z",
                        },
                        {
                            "run_id": "second",
                            "name": "alpine-aaaaaaaaaaa",
                            "title": "Alpine",
                            "video_id": "aaaaaaaaaaa",
                            "source_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
                            "bundle_path": str(data_dir / "alpine-aaaaaaaaaaa"),
                            "managed": True,
                            "workflow": "deep",
                            "harness": "codex",
                            "engine": "pending",
                            "status": "completed",
                            "created_at": "2026-07-02T00:00:00Z",
                            "updated_at": "2026-07-02T00:00:00Z",
                        },
                    ],
                },
            )

            with patch.dict(os.environ, {runs.DATA_DIR_ENV_VAR: str(data_dir)}):
                with self.assertRaises(package.CliError) as missing:
                    runs.resolve_run_selector("missing")
                with self.assertRaises(package.CliError) as ambiguous:
                    runs.resolve_run_selector("al")

        self.assertEqual(missing.exception.code, "run_not_found")
        self.assertEqual(ambiguous.exception.code, "ambiguous_run")

    def test_run_profile_applies_defaults_and_cli_style_override(self):
        transcript = {
            "video_id": "dQw4w9WgXcQ",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "language": "sv",
            "requested_languages": ["sv"],
            "track": {"language_code": "sv"},
            "segments": [{"start": 1.0, "duration": 0.5, "text": "hello world"}],
            "text": "hello world",
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {"YT_SCRIBE_CONFIG": str(Path(tmp_dir) / "config.json")},
            ):
                config.write_config(
                    {
                        "profiles": {
                            "research": {
                                "style": "summary",
                                "langs": ["sv"],
                                "template": "lecture",
                                "timestamps": True,
                            }
                        }
                    }
                )
                args = cli.build_parser().parse_args(
                    [
                        "--json",
                        "run",
                        "dQw4w9WgXcQ",
                        "--profile",
                        "research",
                        "--style",
                        "clean",
                        "--stdout",
                    ],
                )
                stdout = io.StringIO()

                with (
                    patch_cli_workflow("fetch_transcript", return_value=transcript) as fetch,
                    patch_cli_workflow(
                        "run_agent_polish",
                        return_value={
                            "output_path": None,
                            "chars": len("polished text\n"),
                            "harness": "codex",
                            "text": "polished text\n",
                        },
                    ) as polish,
                    contextlib.redirect_stdout(stdout),
                ):
                    exit_code = cli.handle_args(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(fetch.call_args.args[1], ["sv"])
        self.assertEqual(payload["run"]["style"], "clean")
        self.assertTrue(payload["run"]["timestamp_grounding"])
        self.assertIn("--template", payload["run"]["instruction_sources"])
        self.assertIn("[00:01] hello world", polish.call_args.kwargs["transcript_text"])

    def test_run_profile_reads_project_config_and_allows_boolean_overrides(self):
        transcript = {
            "video_id": "dQw4w9WgXcQ",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "language": "sv",
            "requested_languages": ["sv"],
            "track": {"language_code": "sv"},
            "segments": [{"start": 1.0, "duration": 0.5, "text": "hello world"}],
            "text": "hello world",
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            project_config_dir = root / ".yt-scribe"
            project_config_dir.mkdir()
            (project_config_dir / "config.json").write_text(
                json.dumps(
                    {
                        "profiles": {
                            "research": {
                                "style": "summary",
                                "langs": ["sv"],
                                "timestamps": True,
                                "front_matter": True,
                                "chunk_chars": 25,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            previous_cwd = Path.cwd()
            os.chdir(root)
            try:
                args = cli.build_parser().parse_args(
                    [
                        "--json",
                        "run",
                        "dQw4w9WgXcQ",
                        "--profile",
                        "research",
                        "--no-timestamps",
                        "--no-front-matter",
                        "--chunk-chars",
                        "0",
                        "--stdout",
                    ],
                )
                stdout = io.StringIO()

                with (
                    patch_cli_workflow("fetch_transcript", return_value=transcript) as fetch,
                    patch_cli_workflow(
                        "run_agent_polish",
                        return_value={
                            "output_path": None,
                            "chars": len("polished text\n"),
                            "harness": "codex",
                            "text": "polished text\n",
                        },
                    ) as polish,
                    contextlib.redirect_stdout(stdout),
                ):
                    exit_code = cli.handle_args(args)
            finally:
                os.chdir(previous_cwd)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(fetch.call_args.args[1], ["sv"])
        self.assertEqual(payload["run"]["style"], "summary")
        self.assertFalse(payload["run"]["timestamp_grounding"])
        self.assertFalse(payload["run"]["front_matter"])
        self.assertFalse(payload["run"]["chunking"]["enabled"])
        self.assertNotIn("[00:01]", polish.call_args.kwargs["transcript_text"])

    def test_inspect_brief_reports_small_caption_summary(self):
        args = cli.build_parser().parse_args(["--json", "inspect", "dQw4w9WgXcQ", "--brief"])
        stdout = io.StringIO()
        tracks = [
            youtube.CaptionTrack("English", "en", "https://example.test/en"),
            youtube.CaptionTrack("Swedish", "sv", "https://example.test/sv", kind="asr"),
        ]

        with (
            patch_cli_workflow("list_transcript_tracks", return_value=tracks),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = cli.handle_args(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["video"]["caption_tracks"], 2)
        self.assertEqual(payload["video"]["languages"], ["en", "sv"])
        self.assertEqual(payload["video"]["manual_languages"], ["en"])
        self.assertEqual(payload["video"]["auto_generated_languages"], ["sv"])

    def test_inspect_brief_reports_duration_when_available(self):
        args = cli.build_parser().parse_args(["--json", "inspect", "dQw4w9WgXcQ", "--brief"])
        stdout = io.StringIO()
        tracks = [youtube.CaptionTrack("English", "en", "https://example.test/en")]

        with (
            patch_cli_workflow("fetch_video_duration_seconds", return_value=123),
            patch_cli_workflow("list_transcript_tracks", return_value=tracks),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = cli.handle_args(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["video"]["duration_seconds"], 123)

    def test_inspect_keeps_caption_availability_when_duration_is_missing(self):
        args = cli.build_parser().parse_args(["--json", "inspect", "dQw4w9WgXcQ", "--brief"])
        stdout = io.StringIO()
        tracks = [youtube.CaptionTrack("English", "en", "https://example.test/en")]

        with (
            patch_cli_workflow("fetch_video_duration_seconds", return_value=None),
            patch_cli_workflow("list_transcript_tracks", return_value=tracks),
            contextlib.redirect_stdout(stdout),
        ):
            exit_code = cli.handle_args(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertIsNone(payload["video"]["duration_seconds"])
        self.assertTrue(payload["video"]["has_captions"])
        self.assertEqual(payload["video"]["caption_tracks"], 1)

    def test_verify_polished_output_classifies_findings_conservatively(self):
        transcript = (
            "[00:01] Alice shipped 42 widgets.\n"
            "[00:05] Strategy choices were discussed."
        )
        polished = (
            "- Alice shipped 42 widgets.\n"
            "- Bob shipped 99 widgets.\n"
            "- The strategy is risky."
        )

        result = verify.verify_polished_output(polished, transcript)

        self.assertEqual(
            [finding["status"] for finding in result["findings"]],
            ["supported", "unsupported", "uncertain"],
        )
        self.assertEqual(result["summary"]["unsupported"], 1)
        self.assertEqual(result["findings"][0]["transcript_anchor"], "00:01")
        self.assertEqual(result["findings"][0]["polished_location"]["line"], 1)
        self.assertEqual(result["findings"][1]["unsupported_terms"], ["Bob", "99"])

    def test_verify_command_reports_stable_json_findings(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            polished_path = root / "notes.md"
            transcript_path = root / "transcript.json"
            polished_path.write_text(
                "- Alice shipped 42 widgets.\n- Bob shipped 99 widgets.\n",
                encoding="utf-8",
            )
            transcript_path.write_text(
                json.dumps(
                    {
                        "segments": [
                            {
                                "start": 1.0,
                                "duration": 0.5,
                                "text": "Alice shipped 42 widgets.",
                            }
                        ],
                        "text": "Alice shipped 42 widgets.",
                    }
                ),
                encoding="utf-8",
            )
            args = cli.build_parser().parse_args(
                ["--json", "verify", str(polished_path), "--transcript", str(transcript_path)]
            )
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = cli.handle_args(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["verify"]["summary"]["unsupported"], 1)
        self.assertEqual(payload["verify"]["findings"][0]["status"], "supported")
        self.assertEqual(payload["verify"]["findings"][1]["status"], "unsupported")
        self.assertEqual(payload["verify"]["findings"][1]["polished_location"]["line"], 2)

    def test_verify_human_output_groups_findings_by_status(self):
        result = {
            "summary": {"claims": 3, "supported": 1, "unsupported": 1, "uncertain": 1},
            "findings": [
                {
                    "status": "supported",
                    "claim": "Alice shipped 42 widgets.",
                    "message": "supported",
                    "transcript_anchor": "00:01",
                    "polished_location": {"line": 1, "column": 3},
                },
                {
                    "status": "unsupported",
                    "claim": "Bob shipped 99 widgets.",
                    "message": "unsupported",
                    "transcript_anchor": None,
                    "polished_location": {"line": 2, "column": 3},
                },
                {
                    "status": "uncertain",
                    "claim": "The strategy is risky.",
                    "message": "uncertain",
                    "transcript_anchor": None,
                    "polished_location": {"line": 3, "column": 3},
                },
            ],
        }

        rendered = verify.render_verification(result)

        self.assertLess(rendered.index("unsupported:"), rendered.index("\nuncertain:"))
        self.assertLess(rendered.index("\nuncertain:"), rendered.index("\nsupported:"))
        self.assertIn("- line 2: Bob shipped 99 widgets.", rendered)

    def test_init_project_writes_local_guidance_only(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            args = cli.build_parser().parse_args(
                ["--json", "init-project", "--dir", tmp_dir, "--profile", "research"]
            )
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                exit_code = cli.handle_args(args)

            payload = json.loads(stdout.getvalue())
            project_dir = Path(tmp_dir) / ".yt-scribe"

            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["init_project"]["dir"], str(project_dir.resolve()))
            self.assertTrue((project_dir / "AGENTS.md").is_file())
            self.assertTrue((project_dir / "config.json").is_file())
            self.assertFalse((Path(tmp_dir) / "AGENTS.md").exists())

    def test_fetch_writes_transcript_cache_when_cache_dir_is_set(self):
        transcript = {
            "video_id": "dQw4w9WgXcQ",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "language": "en",
            "requested_languages": ["en"],
            "track": {"language_code": "en"},
            "segments": [{"text": "hello world"}],
            "text": "hello world",
            "source": "youtube_transcript_api",
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_dir = Path(tmp_dir) / "cache"
            args = cli.build_parser().parse_args(
                ["--json", "fetch", "dQw4w9WgXcQ", "--cache-dir", str(cache_dir)],
            )
            stdout = io.StringIO()

            with (
                patch_cli_workflow("fetch_transcript", return_value=transcript),
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = cli.handle_args(args)

            payload = json.loads(stdout.getvalue())
            cache_path = Path(payload["fetch"]["cache"]["path"])
            cached_text = json.loads(cache_path.read_text(encoding="utf-8"))["text"]

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["fetch"]["cache"]["status"], "written")
        self.assertTrue(cache_path.name.endswith(".json"))
        self.assertEqual(cached_text, "hello world")

    def test_run_resume_reuses_cached_transcript_without_fetching(self):
        transcript = {
            "video_id": "dQw4w9WgXcQ",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "language": "en",
            "requested_languages": ["en"],
            "track": {"language_code": "en"},
            "segments": [{"text": "cached transcript"}],
            "text": "cached transcript",
            "source": "youtube_transcript_api",
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_dir = Path(tmp_dir) / "cache"
            cache_path = transcripts.write_transcript_cache(cache_dir, transcript)
            args = cli.build_parser().parse_args(
                [
                    "--json",
                    "run",
                    "dQw4w9WgXcQ",
                    "--resume",
                    "--cache-dir",
                    str(cache_dir),
                    "--stdout",
                ],
            )
            stdout = io.StringIO()

            with (
                patch_cli_workflow("fetch_transcript") as fetch,
                patch_cli_workflow(
                    "run_agent_polish",
                    return_value={
                        "output_path": None,
                        "chars": len("polished cached transcript\n"),
                        "harness": "codex",
                        "text": "polished cached transcript\n",
                    },
                ) as polish,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = cli.handle_args(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        fetch.assert_not_called()
        self.assertEqual(polish.call_args.kwargs["transcript_text"], "cached transcript")
        self.assertEqual(payload["run"]["cache"], {"status": "hit", "path": str(cache_path)})

    def test_batch_writes_manifest_for_success_and_failure(self):
        def fake_load_or_fetch(url, languages, cache_dir, resume, proxy_config=None):
            self.assertIsNone(proxy_config)
            if url == "aaaaaaaaaaa":
                raise package.CliError("No caption tracks were found", "no_captions")
            return (
                {
                    "video_id": "dQw4w9WgXcQ",
                    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "language": "en",
                    "requested_languages": languages,
                    "track": {"language_code": "en"},
                    "segments": [{"text": "hello world"}],
                    "text": "hello world",
                    "source": "youtube_transcript_api",
                },
                {"status": "disabled", "path": None},
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            list_path = root / "videos.txt"
            out_dir = root / "notes"
            manifest_path = root / "manifest.json"
            list_path.write_text("dQw4w9WgXcQ\naaaaaaaaaaa\n", encoding="utf-8")
            args = cli.build_parser().parse_args(
                [
                    "--json",
                    "batch",
                    str(list_path),
                    "--out-dir",
                    str(out_dir),
                    "--manifest",
                    str(manifest_path),
                ],
            )
            stdout = io.StringIO()

            with (
                patch_cli_workflow("load_or_fetch_transcript", side_effect=fake_load_or_fetch),
                patch_cli_workflow(
                    "run_agent_polish",
                    side_effect=lambda **kwargs: {
                        "output_path": runs.write_text(kwargs["out_path"], "polished text\n"),
                        "chars": len("polished text\n"),
                        "harness": "codex",
                        "text": "polished text\n",
                    },
                ),
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = cli.handle_args(args)

            payload = json.loads(stdout.getvalue())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            output_exists = Path(manifest["items"][0]["output_path"]).is_file()

        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["batch"]["succeeded"], 1)
        self.assertEqual(payload["batch"]["failed"], 1)
        self.assertEqual(manifest["items"][0]["status"], "succeeded")
        self.assertTrue(output_exists)
        self.assertEqual(manifest["items"][1]["status"], "failed")
        self.assertEqual(manifest["items"][1]["error"]["code"], "no_captions")

    def test_batch_with_chunk_chars_reports_chunking_in_manifest(self):
        def fake_load_or_fetch(url, languages, cache_dir, resume, proxy_config=None):
            return (
                {
                    "video_id": "dQw4w9WgXcQ",
                    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "language": "en",
                    "requested_languages": languages,
                    "track": {"language_code": "en"},
                    "segments": [
                        {"start": 1.0, "duration": 0.5, "text": "first point"},
                        {"start": 2.0, "duration": 0.5, "text": "second point"},
                        {"start": 3.0, "duration": 0.5, "text": "third point"},
                    ],
                    "text": "first point\nsecond point\nthird point",
                    "source": "youtube_transcript_api",
                },
                {"status": "disabled", "path": None},
            )

        def fake_polish(**kwargs):
            if kwargs["out_path"] and kwargs["out_path"].endswith("notes.md"):
                return {
                    "output_path": runs.write_text(kwargs["out_path"], "merged\n"),
                    "chars": len("merged\n"),
                    "harness": "codex",
                    "text": "merged\n",
                }
            return {
                "output_path": runs.write_text(kwargs["out_path"], "chunk\n"),
                "chars": len("chunk\n"),
                "harness": "codex",
                "text": "chunk\n",
            }

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            list_path = root / "videos.txt"
            out_dir = root / "notes"
            manifest_path = root / "manifest.json"
            list_path.write_text("dQw4w9WgXcQ\n", encoding="utf-8")
            args = cli.build_parser().parse_args(
                [
                    "--json",
                    "batch",
                    str(list_path),
                    "--out-dir",
                    str(out_dir),
                    "--manifest",
                    str(manifest_path),
                    "--chunk-chars",
                    "25",
                ],
            )
            stdout = io.StringIO()

            with (
                patch_cli_workflow("load_or_fetch_transcript", side_effect=fake_load_or_fetch),
                patch_cli_workflow("run_agent_polish", side_effect=fake_polish),
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = cli.handle_args(args)

            payload = json.loads(stdout.getvalue())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertTrue(payload["batch"]["chunking"]["enabled"])
        self.assertEqual(manifest["items"][0]["chunking"]["chunks"], 2)
        self.assertEqual(manifest["items"][0]["chunking"]["merge_status"], "merged")

    def test_expand_batch_items_expands_playlist_urls(self):
        with patch.object(
            batch,
            "fetch_playlist_video_ids",
            return_value=["dQw4w9WgXcQ", "aaaaaaaaaaa"],
        ):
            items = batch.expand_batch_items(
                ["https://www.youtube.com/playlist?list=PLabc123"],
                proxy_config=None,
            )

        self.assertEqual(
            [item["url"] for item in items],
            [
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "https://www.youtube.com/watch?v=aaaaaaaaaaa",
            ],
        )
        self.assertEqual(items[0]["playlist_id"], "PLabc123")

    def test_batch_playlist_records_source_metadata(self):
        def fake_load_or_fetch(url, languages, cache_dir, resume, proxy_config=None):
            self.assertEqual(url, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            return (
                {
                    "video_id": "dQw4w9WgXcQ",
                    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "language": "en",
                    "requested_languages": languages,
                    "track": {"language_code": "en"},
                    "segments": [{"text": "hello world"}],
                    "text": "hello world",
                    "source": "youtube_transcript_api",
                },
                {"status": "disabled", "path": None},
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            list_path = root / "videos.txt"
            out_dir = root / "notes"
            manifest_path = root / "manifest.json"
            playlist_url = "https://www.youtube.com/playlist?list=PLabc123"
            list_path.write_text(f"{playlist_url}\n", encoding="utf-8")
            args = cli.build_parser().parse_args(
                [
                    "--json",
                    "batch",
                    str(list_path),
                    "--out-dir",
                    str(out_dir),
                    "--manifest",
                    str(manifest_path),
                ],
            )
            stdout = io.StringIO()

            with (
                patch_cli_workflow(
                    "fetch_playlist_video_ids",
                    return_value=["dQw4w9WgXcQ"],
                ),
                patch_cli_workflow("load_or_fetch_transcript", side_effect=fake_load_or_fetch),
                patch_cli_workflow(
                    "run_agent_polish",
                    side_effect=lambda **kwargs: {
                        "output_path": runs.write_text(kwargs["out_path"], "polished\n"),
                        "chars": len("polished\n"),
                        "harness": "codex",
                        "text": "polished\n",
                    },
                ),
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = cli.handle_args(args)

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(manifest["items"][0]["playlist_url"], playlist_url)
        self.assertEqual(manifest["items"][0]["playlist_id"], "PLabc123")

    def test_batch_playlist_expansion_failure_is_recorded_in_manifest(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            list_path = root / "videos.txt"
            out_dir = root / "notes"
            manifest_path = root / "manifest.json"
            playlist_url = "https://www.youtube.com/playlist?list=PLabc123"
            list_path.write_text(f"{playlist_url}\n", encoding="utf-8")
            args = cli.build_parser().parse_args(
                [
                    "--json",
                    "batch",
                    str(list_path),
                    "--out-dir",
                    str(out_dir),
                    "--manifest",
                    str(manifest_path),
                ],
            )
            stdout = io.StringIO()

            with (
                patch_cli_workflow(
                    "fetch_playlist_video_ids",
                    side_effect=package.CliError("No videos were found", "playlist_empty"),
                ),
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = cli.handle_args(args)

            payload = json.loads(stdout.getvalue())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(manifest["failed"], 1)
        self.assertEqual(manifest["items"][0]["url"], playlist_url)
        self.assertEqual(manifest["items"][0]["playlist_id"], "PLabc123")
        self.assertEqual(manifest["items"][0]["error"]["code"], "playlist_empty")

    def test_batch_resume_skips_existing_outputs(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            list_path = root / "videos.txt"
            out_dir = root / "notes"
            out_dir.mkdir()
            output_path = out_dir / "yt-scribe-dQw4w9WgXcQ-notes.md"
            output_path.write_text("existing notes\n", encoding="utf-8")
            manifest_path = root / "manifest.json"
            list_path.write_text("dQw4w9WgXcQ\n", encoding="utf-8")
            args = cli.build_parser().parse_args(
                [
                    "--json",
                    "batch",
                    str(list_path),
                    "--out-dir",
                    str(out_dir),
                    "--manifest",
                    str(manifest_path),
                    "--resume",
                ],
            )
            stdout = io.StringIO()

            with (
                patch_cli_workflow("load_or_fetch_transcript") as fetch,
                patch_cli_workflow("run_agent_polish") as polish,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = cli.handle_args(args)

            payload = json.loads(stdout.getvalue())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        fetch.assert_not_called()
        polish.assert_not_called()
        self.assertEqual(payload["batch"]["skipped"], 1)
        self.assertEqual(manifest["items"][0]["status"], "skipped")
        self.assertEqual(manifest["items"][0]["output_path"], str(output_path.resolve()))

    def test_polish_json_reports_custom_instruction_mode(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            transcript_path = Path(tmp_dir) / "transcript.txt"
            transcript_path.write_text("hello world", encoding="utf-8")
            args = cli.build_parser().parse_args(
                [
                    "--json",
                    "polish",
                    str(transcript_path),
                    "--focus",
                    "Keep only action items.",
                    "--stdout",
                ],
            )
            stdout = io.StringIO()

            with (
                patch_cli_workflow(
                    "run_agent_polish",
                    return_value={
                        "output_path": None,
                        "chars": 14,
                        "harness": "codex",
                        "text": "polished text\n",
                    },
                ) as run_agent,
                contextlib.redirect_stdout(stdout),
            ):
                exit_code = cli.handle_args(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["polish"]["instruction_mode"], "custom")
        self.assertEqual(payload["polish"]["instruction_sources"], ["--focus"])
        self.assertIn("Keep only action items.", run_agent.call_args.kwargs["instruction"])


if __name__ == "__main__":
    unittest.main()
