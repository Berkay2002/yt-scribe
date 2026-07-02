import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "yt_scribe.py"


def run_cli(*args, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        env=env,
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


def test_polish_help_exposes_agent_harness_selection():
    result = run_cli("polish", "--help")

    assert result.returncode == 0
    assert "--agent-harness" in result.stdout
    assert "opencode" in result.stdout


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


def test_config_command_persists_default_agent_harness(tmp_path):
    env = os.environ.copy()
    env["YT_SCRIBE_CONFIG"] = str(tmp_path / "config.json")

    result = run_cli("--json", "config", "set", "default-agent-harness", "opencode", env=env)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["config"]["default_agent_harness"] == "opencode"

    result = run_cli("--json", "config", env=env)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["config"]["default_agent_harness"] == "opencode"
    assert payload["config"]["effective_agent_harness"] == "opencode"


def test_install_harness_assets_writes_global_agent_files(tmp_path):
    env = os.environ.copy()
    env["YT_SCRIBE_AGENTS_SKILLS_DIR"] = str(tmp_path / "agent-skills")
    env["YT_SCRIBE_OPENCODE_AGENTS_DIR"] = str(tmp_path / "opencode-agents")

    result = run_cli("--json", "install-harness-assets", env=env)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert (
        tmp_path
        / "agent-skills"
        / "yt-scribe-transcript-polisher"
        / "SKILL.md"
    ).exists()
    assert (tmp_path / "opencode-agents" / "yt-scribe.md").exists()
    assert (
        tmp_path / "opencode-agents" / "yt-scribe-transcript-polisher.md"
    ).exists()
