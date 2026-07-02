import argparse
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

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

    def test_builtin_style_prompts_trigger_transcript_polisher_skill(self):
        for style in yt_scribe.STYLE_INSTRUCTIONS:
            instruction = yt_scribe.style_instruction(style, "codex")
            self.assertIn("yt-scribe-transcript-polisher", instruction)
            self.assertIn("harness/codex.md", instruction)
            self.assertIn("stdin", instruction)
            self.assertNotIn("opencode", instruction.lower())

    def test_opencode_style_prompts_use_opencode_harness_file(self):
        instruction = yt_scribe.style_instruction("notes", "opencode")

        self.assertIn("yt-scribe-transcript-polisher", instruction)
        self.assertIn("harness/opencode.md", instruction)
        self.assertIn("attached transcript file", instruction)
        self.assertNotIn("codex", instruction.lower())

    def test_focus_instructions_extend_builtin_prompt(self):
        args = argparse.Namespace(
            style="summary",
            focus=["Only list decisions and risks."],
            focus_file=None,
            instruction=None,
            prompt_file=None,
        )

        instruction = yt_scribe.resolve_instruction(args, "codex")

        self.assertEqual(instruction.mode, "custom")
        self.assertEqual(instruction.sources, ["--focus"])
        self.assertIn("yt-scribe-transcript-polisher", instruction.text)
        self.assertIn("Summarize this YouTube transcript", instruction.text)
        self.assertIn("Custom user instructions", instruction.text)
        self.assertIn("Only list decisions and risks.", instruction.text)

    def test_replacement_instruction_conflicts_with_focus(self):
        args = argparse.Namespace(
            style="notes",
            focus=["extra"],
            focus_file=None,
            instruction="Replace everything.",
            prompt_file=None,
        )

        with self.assertRaises(yt_scribe.CliError) as error:
            yt_scribe.resolve_instruction(args, "codex")

        self.assertEqual(error.exception.code, "conflicting_instruction_options")

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

    def test_opencode_polish_uses_run_command_with_attached_transcript(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append((command, kwargs))
            transcript_path = Path(command[command.index("--file") + 1])
            self.assertEqual(transcript_path.read_text(encoding="utf-8"), "raw transcript")
            stdout = "\n".join(
                [
                    json.dumps({"type": "step_start"}),
                    json.dumps({"type": "text", "part": {"text": "polished text"}}),
                    json.dumps({"type": "step_finish"}),
                ]
            )
            return subprocess.CompletedProcess(command, 0, stdout, "")

        with (
            patch.object(yt_scribe, "command_path", return_value="opencode"),
            patch.object(yt_scribe, "opencode_agent_available", return_value=True),
            patch.object(yt_scribe.subprocess, "run", side_effect=fake_run),
        ):
            result = yt_scribe.run_agent_polish(
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
        self.assertEqual(
            calls[0][0][calls[0][0].index("--agent") + 1],
            "yt-scribe-transcript-polisher",
        )
        self.assertIn("--model", calls[0][0])

    def test_doctor_reports_codex_and_opencode_harnesses(self):
        paths = {"codex": "C:\\bin\\codex.exe", "opencode": None}

        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch.dict(
                    os.environ,
                    {"YT_SCRIBE_CONFIG": str(Path(tmp_dir) / "config.json")},
                ),
                patch.object(yt_scribe, "command_path", side_effect=lambda name: paths.get(name)),
                patch.object(yt_scribe, "command_output", return_value="codex 1.0"),
            ):
                payload = yt_scribe.doctor_payload()

        self.assertEqual(payload["agent_harness"]["default"], "codex")
        self.assertTrue(payload["agent_harness"]["harnesses"]["codex"]["available"])
        self.assertFalse(payload["agent_harness"]["harnesses"]["opencode"]["available"])

    def test_local_install_metadata_is_platform_specific(self):
        with patch.object(yt_scribe.sys, "platform", "linux"):
            self.assertEqual(yt_scribe.local_install_command(), "sh ./install-local.sh")
            self.assertEqual(yt_scribe.install_bin_dir(), Path.home() / ".local" / "bin")

        with patch.object(yt_scribe.sys, "platform", "darwin"):
            self.assertEqual(yt_scribe.local_install_command(), "sh ./install-local.sh")
            self.assertEqual(yt_scribe.install_bin_dir(), Path.home() / ".local" / "bin")

        with patch.object(yt_scribe.sys, "platform", "win32"):
            self.assertEqual(yt_scribe.local_install_command(), ".\\install-local.ps1")
            self.assertEqual(yt_scribe.install_bin_dir(), Path.home() / ".local" / "bin")

    def test_doctor_payload_reports_platform_install_command(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch.dict(
                    os.environ,
                    {"YT_SCRIBE_CONFIG": str(Path(tmp_dir) / "config.json")},
                ),
                patch.object(yt_scribe.sys, "platform", "darwin"),
                patch.object(yt_scribe, "command_path", return_value=None),
            ):
                payload = yt_scribe.doctor_payload()

        self.assertEqual(payload["install"]["local_install_command"], "sh ./install-local.sh")
        self.assertEqual(Path(payload["install"]["wrapper_dir"]), yt_scribe.install_bin_dir())

    def test_selected_agent_harness_uses_config_unless_cli_overrides(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {"YT_SCRIBE_CONFIG": str(Path(tmp_dir) / "config.json")},
            ):
                yt_scribe.write_config({"default_agent_harness": "opencode"})

                self.assertEqual(
                    yt_scribe.selected_agent_harness(
                        argparse.Namespace(agent_harness=None),
                    ),
                    "opencode",
                )
                self.assertEqual(
                    yt_scribe.selected_agent_harness(
                        argparse.Namespace(agent_harness="codex"),
                    ),
                    "codex",
                )

    def test_opencode_agent_available_checks_global_agent_dir(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            agent_dir = Path(tmp_dir) / "opencode-agents"
            agent_dir.mkdir()
            (agent_dir / "yt-scribe-transcript-polisher.md").write_text(
                "agent",
                encoding="utf-8",
            )

            with patch.dict(
                os.environ,
                {"YT_SCRIBE_OPENCODE_AGENTS_DIR": str(agent_dir)},
            ):
                self.assertTrue(
                    yt_scribe.opencode_agent_available(
                        "yt-scribe-transcript-polisher",
                        cwd=tmp_dir,
                    )
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
        args = yt_scribe.build_parser().parse_args(
            ["run", "dQw4w9WgXcQ", "--out", "notes.md"],
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            patch.object(yt_scribe, "fetch_transcript", return_value=transcript),
            patch.object(
                yt_scribe,
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
            exit_code = yt_scribe.handle_args(args)

        self.assertEqual(exit_code, 0)
        self.assertIn("Fetching transcript", stderr.getvalue())
        self.assertIn("Fetched 1 transcript segment", stderr.getvalue())
        self.assertIn("Using Codex", stderr.getvalue())
        self.assertIn("Wrote polished transcript to notes.md", stdout.getvalue())
        self.assertIsNotNone(run_agent.call_args.kwargs["progress"])

    def test_polish_json_reports_custom_instruction_mode(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            transcript_path = Path(tmp_dir) / "transcript.txt"
            transcript_path.write_text("hello world", encoding="utf-8")
            args = yt_scribe.build_parser().parse_args(
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
                patch.object(
                    yt_scribe,
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
                exit_code = yt_scribe.handle_args(args)

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["polish"]["instruction_mode"], "custom")
        self.assertEqual(payload["polish"]["instruction_sources"], ["--focus"])
        self.assertIn("Keep only action items.", run_agent.call_args.kwargs["instruction"])


if __name__ == "__main__":
    unittest.main()
