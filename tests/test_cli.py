import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "yt_scribe.py"


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_help_exposes_human_and_agent_lifecycle():
    result = run_cli("--help")

    assert result.returncode == 0
    assert "yt-scribe doctor" in result.stdout
    assert "yt-scribe inspect <youtube-url>" in result.stdout
    assert "Put --json before the command" in result.stdout


def test_lifecycle_prints_ordered_public_commands():
    result = run_cli("lifecycle")

    assert result.returncode == 0
    assert "check: yt-scribe doctor" in result.stdout
    assert "run: yt-scribe run <youtube-url>" in result.stdout


def test_lifecycle_json_is_stable_for_agents():
    result = run_cli("--json", "lifecycle")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert [step["step"] for step in payload["lifecycle"]] == [
        "check",
        "inspect",
        "fetch",
        "polish",
        "run",
    ]


def test_invalid_youtube_url_returns_machine_readable_error():
    result = run_cli("--json", "inspect", "not-a-youtube-url")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_youtube_url"
