import asyncio
import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import tomllib

import yt_scribe as package
import yt_scribe_mcp
from yt_scribe import transcripts, youtube
from yt_scribe.polish import harnesses as polish_harnesses
from yt_scribe.polish import workflows as polish_workflows

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "yt_scribe_mcp.py"


def run_mcp_cli(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def run_mcp_module(*args):
    return subprocess.run(
        [sys.executable, "-m", "yt_scribe.mcp", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_mcp_script_metadata_exposes_console_entrypoint_and_extra():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["scripts"]["yt-scribe-mcp"] == "yt_scribe.mcp:main"
    assert "mcp" in project["optional-dependencies"]
    assert any(dep.startswith("fastmcp") for dep in project["optional-dependencies"]["mcp"])
    setuptools_config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["setuptools"]
    assert "yt_scribe_mcp" in setuptools_config["py-modules"]


def test_mcp_help_is_available_without_optional_dependencies():
    result = run_mcp_cli("--help")

    assert result.returncode == 0
    assert "Run the local yt-scribe MCP server." in result.stdout
    assert "--http" in result.stdout
    assert "--read-only" in result.stdout
    assert "/mcp" in result.stdout


def test_mcp_module_entrypoint_help_is_available_without_optional_dependencies():
    result = run_mcp_module("--help")

    assert result.returncode == 0
    assert "Run the local yt-scribe MCP server." in result.stdout
    assert "--http" in result.stdout


def test_readme_documents_mcp_client_configuration():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "python -m pip install -e \".[dev,mcp]\"" in readme
    assert "codex mcp add yt-scribe -- yt-scribe-mcp" in readme
    assert '"command": ["yt-scribe-mcp"]' in readme
    assert "npx @modelcontextprotocol/inspector yt-scribe-mcp" in readme


def test_mcp_info_payload_is_stable_without_optional_dependencies():
    assert yt_scribe_mcp.server_info() == {
        "package": "yt-scribe",
        "version": package.VERSION,
        "default_transport": "stdio",
        "supported_tool_groups": ["info", "inspect", "fetch", "polish", "run"],
        "agent_tools_enabled": True,
    }


def test_mcp_info_payload_can_hide_agent_tool_groups():
    payload = yt_scribe_mcp.server_info(enable_agent_tools=False)

    assert payload["agent_tools_enabled"] is False
    assert payload["supported_tool_groups"] == ["info", "inspect", "fetch"]


def test_mcp_inspect_returns_caption_metadata_without_network():
    tracks = [
        youtube.CaptionTrack("English", "en", "https://example.test/en"),
        youtube.CaptionTrack("Swedish", "sv", "https://example.test/sv", kind="asr"),
    ]

    with patch.object(youtube, "list_transcript_tracks", return_value=tracks):
        payload = yt_scribe_mcp.inspect_youtube_captions("dQw4w9WgXcQ")

    assert payload["ok"] is True
    assert payload["video"]["id"] == "dQw4w9WgXcQ"
    assert payload["video"]["caption_tracks"] == 2
    assert payload["video"]["languages"] == ["en", "sv"]
    assert payload["video"]["manual_languages"] == ["en"]
    assert payload["video"]["auto_generated_languages"] == ["sv"]


def test_mcp_inspect_preserves_invalid_url_error_code():
    payload = yt_scribe_mcp.inspect_youtube_captions("not-a-youtube-url")

    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_youtube_url"


def test_mcp_fetch_returns_rendered_transcript_and_metadata_without_network():
    transcript = {
        "video_id": "dQw4w9WgXcQ",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "language": "en-GB",
        "requested_languages": ["en", "en-GB"],
        "track": {"language_code": "en-GB"},
        "segments": [{"start": 1.0, "duration": 0.5, "text": "hello world"}],
        "text": "hello world",
        "source": "youtube_transcript_api",
    }

    with patch.object(
        transcripts,
        "load_or_fetch_transcript",
        return_value=(transcript, {"status": "disabled", "path": None}),
    ) as load:
        payload = yt_scribe_mcp.fetch_youtube_transcript(
            "dQw4w9WgXcQ",
            languages=["en", "en-GB"],
            timestamps=True,
        )

    assert payload["ok"] is True
    assert payload["fetch"]["language"] == "en-GB"
    assert payload["fetch"]["requested_languages"] == ["en", "en-GB"]
    assert payload["fetch"]["segments"] == 1
    assert payload["fetch"]["chars"] == len("hello world")
    assert payload["fetch"]["source"] == "youtube_transcript_api"
    assert payload["fetch"]["timestamped"] is True
    assert payload["fetch"]["transcript"] == "[00:01] hello world\n"
    assert load.call_args.args[1] == ["en", "en-GB"]


def test_mcp_fetch_preserves_transcript_error_code():
    with patch.object(
        transcripts,
        "load_or_fetch_transcript",
        side_effect=package.CliError("No caption tracks were found", "no_captions"),
    ):
        payload = yt_scribe_mcp.fetch_youtube_transcript("dQw4w9WgXcQ")

    assert payload["ok"] is False
    assert payload["error"]["code"] == "no_captions"


def test_mcp_fetch_reports_progress_when_context_is_available():
    transcript = {
        "video_id": "dQw4w9WgXcQ",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "language": "en",
        "requested_languages": ["en"],
        "track": {"language_code": "en"},
        "segments": [{"start": 1.0, "duration": 0.5, "text": "hello"}],
        "text": "hello",
        "source": "youtube_transcript_api",
    }
    progress_events = []

    class FakeContext:
        async def report_progress(self, progress, total, message):
            progress_events.append((progress, total, message))

    with patch.object(
        transcripts,
        "load_or_fetch_transcript",
        return_value=(transcript, {"status": "disabled", "path": None}),
    ):
        payload = asyncio.run(
            yt_scribe_mcp.fetch_youtube_transcript_with_progress(
                "dQw4w9WgXcQ",
                ctx=FakeContext(),
            )
        )

    assert payload["ok"] is True
    assert progress_events[0] == (0, 100, "Resolving YouTube video")
    assert progress_events[-1] == (100, 100, "Transcript fetch complete")


def test_mcp_agent_tools_can_be_disabled_by_read_only_flag(monkeypatch):
    monkeypatch.delenv(yt_scribe_mcp.READ_ONLY_ENV_VAR, raising=False)

    assert yt_scribe_mcp.agent_tools_enabled(read_only=True) is False
    assert yt_scribe_mcp.agent_tools_enabled(read_only=False) is True


def test_mcp_agent_tools_can_be_disabled_by_environment(monkeypatch):
    monkeypatch.setenv(yt_scribe_mcp.READ_ONLY_ENV_VAR, "1")

    assert yt_scribe_mcp.agent_tools_enabled(read_only=False) is False


def test_mcp_agent_polish_uses_existing_harness_boundary():
    with (
        patch.object(
            polish_harnesses,
            "run_agent_polish",
            return_value={
                "output_path": None,
                "chars": len("polished\n"),
                "harness": "codex",
                "text": "polished\n",
            },
        ),
        patch.object(
            polish_workflows,
            "run_agent_polish",
            return_value={
                "output_path": None,
                "chars": len("polished\n"),
                "harness": "codex",
                "text": "polished\n",
            },
        ) as run_agent,
    ):
        payload = yt_scribe_mcp.agent_polish_transcript(
            "raw transcript",
            focus="Keep only action items.",
            timestamps=True,
        )

    assert payload["ok"] is True
    assert payload["polish"]["style"] == "notes"
    assert payload["polish"]["instruction_mode"] == "custom"
    assert payload["polish"]["instruction_sources"] == ["--focus", "--timestamps"]
    assert payload["polish"]["timestamp_grounding"] is True
    assert payload["polish"]["agent_harness"] == "codex"
    assert payload["polish"]["text"] == "polished\n"
    assert run_agent.call_args.kwargs["transcript_text"] == "raw transcript"
    assert "Keep only action items." in run_agent.call_args.kwargs["instruction"]


def test_mcp_agent_run_fetches_and_polishes_without_live_harness():
    transcript = {
        "video_id": "dQw4w9WgXcQ",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "language": "en",
        "requested_languages": ["en"],
        "track": {"language_code": "en"},
        "segments": [{"start": 1.0, "duration": 0.5, "text": "hello"}],
        "text": "hello",
        "source": "youtube_transcript_api",
    }

    with (
        patch.object(
            polish_workflows,
            "load_or_fetch_transcript",
            return_value=(transcript, {"status": "disabled", "path": None}),
        ) as load,
        patch.object(
            polish_harnesses,
            "run_agent_polish",
            return_value={
                "output_path": None,
                "chars": len("notes\n"),
                "harness": "codex",
                "text": "notes\n",
            },
        ),
        patch.object(
            polish_workflows,
            "run_agent_polish",
            return_value={
                "output_path": None,
                "chars": len("notes\n"),
                "harness": "codex",
                "text": "notes\n",
            },
        ) as polish,
    ):
        payload = yt_scribe_mcp.agent_fetch_and_polish_youtube(
            "dQw4w9WgXcQ",
            languages=["en"],
            timestamps=True,
        )

    assert payload["ok"] is True
    assert payload["run"]["video_id"] == "dQw4w9WgXcQ"
    assert payload["run"]["segments"] == 1
    assert payload["run"]["timestamp_grounding"] is True
    assert payload["run"]["agent_harness"] == "codex"
    assert payload["run"]["text"] == "notes\n"
    assert load.call_args.args[1] == ["en"]
    assert polish.call_args.kwargs["transcript_text"] == "[00:01] hello"


@pytest.mark.skipif(
    importlib.util.find_spec("fastmcp") is None,
    reason="requires the optional mcp extra",
)
def test_mcp_server_exposes_expected_tools_through_fastmcp_client():
    from fastmcp import Client

    async def list_tool_names():
        async with Client(yt_scribe_mcp.create_mcp_server()) as client:
            tools = await client.list_tools()
            return {tool.name for tool in tools}

    assert asyncio.run(list_tool_names()) == {
        "yt_scribe_info",
        "inspect_youtube_captions",
        "fetch_youtube_transcript",
        "agent_polish_transcript",
        "agent_fetch_and_polish_youtube",
    }


def test_mcp_rejects_http_host_without_http_mode():
    result = run_mcp_cli("--host", "127.0.0.1")

    assert result.returncode == 2
    assert "Host and port arguments are only valid with --http." in result.stderr


def test_mcp_rejects_http_port_without_http_mode_even_when_zero():
    result = run_mcp_cli("--port", "0")

    assert result.returncode == 2
    assert "Host and port arguments are only valid with --http." in result.stderr


def test_mcp_default_transport_runs_stdio():
    calls = []

    class FakeServer:
        def run(self, **kwargs):
            calls.append(kwargs)

    with patch.object(yt_scribe_mcp, "create_mcp_server", return_value=FakeServer()):
        exit_code = yt_scribe_mcp.main([])

    assert exit_code == 0
    assert calls == [{}]


def test_mcp_http_transport_uses_localhost_default():
    calls = []

    class FakeServer:
        def run(self, **kwargs):
            calls.append(kwargs)

    with patch.object(yt_scribe_mcp, "create_mcp_server", return_value=FakeServer()):
        exit_code = yt_scribe_mcp.main(["--http"])

    assert exit_code == 0
    assert calls == [{"transport": "http", "host": "127.0.0.1", "port": 3000}]


def test_mcp_http_warns_on_nonlocal_host(capsys):
    calls = []

    class FakeServer:
        def run(self, **kwargs):
            calls.append(kwargs)

    with patch.object(yt_scribe_mcp, "create_mcp_server", return_value=FakeServer()):
        exit_code = yt_scribe_mcp.main(["--http", "--host", "0.0.0.0", "--port", "9999"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert calls == [{"transport": "http", "host": "0.0.0.0", "port": 9999}]
    assert "WARNING" in captured.err
    assert "non-localhost interface (0.0.0.0)" in captured.err
