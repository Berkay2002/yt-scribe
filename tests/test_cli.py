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
    assert "--focus" in result.stdout
    assert "--timestamps" in result.stdout
    assert "opencode" in result.stdout


def test_inspect_help_exposes_brief_mode():
    result = run_cli("inspect", "--help")

    assert result.returncode == 0
    assert "--brief" in result.stdout


def test_verify_help_exposes_transcript_input():
    result = run_cli("verify", "--help")

    assert result.returncode == 0
    assert "--transcript" in result.stdout


def test_run_help_exposes_chunking_option():
    result = run_cli("run", "--help")

    assert result.returncode == 0
    assert "--workflow" in result.stdout
    assert "45 minutes" in result.stdout
    assert "--chunk-chars" in result.stdout
    assert "--bundle-dir" in result.stdout


def test_runs_help_exposes_run_management_commands():
    result = run_cli("runs", "--help")

    assert result.returncode == 0
    assert "list" in result.stdout
    assert "open" in result.stdout
    assert "rename" in result.stdout


def test_ask_help_exposes_context_and_agent_modes():
    result = run_cli("ask", "--help")

    assert result.returncode == 0
    assert "--show-context" in result.stdout
    assert "--agent" in result.stdout


def test_readme_documents_long_video_deep_workflow():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Long-video deep workflow" in readme
    assert "yt-scribe runs list" in readme
    assert "yt-scribe ask <run-name>" in readme
    assert "workflow_threshold_seconds" in readme
    assert "does not transcribe audio" in readme
    assert "does not bypass" in readme


def test_agent_skill_documents_long_video_workflow():
    skill = (ROOT / "skills" / "yt-scribe" / "SKILL.md").read_text(encoding="utf-8")
    codex = (ROOT / "skills" / "yt-scribe" / "harness" / "codex.md").read_text(
        encoding="utf-8"
    )
    opencode = (ROOT / "skills" / "yt-scribe" / "harness" / "opencode.md").read_text(
        encoding="utf-8"
    )

    assert "--workflow deep" in skill
    assert "ask <run-name>" in skill
    assert "Codex CSV fan-out" in codex
    assert "local server/session orchestration" in opencode


def test_init_project_help_exposes_project_directory():
    result = run_cli("init-project", "--help")

    assert result.returncode == 0
    assert "--dir" in result.stdout


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


def test_config_profile_set_and_get(tmp_path):
    env = os.environ.copy()
    env["YT_SCRIBE_CONFIG"] = str(tmp_path / "config.json")

    result = run_cli(
        "--json",
        "config",
        "profile",
        "set",
        "research",
        "--style",
        "summary",
        "--langs",
        "en,sv",
        "--template",
        "lecture",
        "--timestamps",
        "--cache-dir",
        ".yt-scribe/cache",
        "--resume",
        "--transcript-format",
        "json",
        "--out",
        "notes.md",
        "--bundle-dir",
        ".yt-scribe/runs/current",
        "--out-dir",
        "batch-notes",
        "--manifest",
        "batch-manifest.json",
        "--stdout",
        "--chunk-chars",
        "5000",
        env=env,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["config"]["profiles"]["research"]["style"] == "summary"
    assert payload["config"]["profiles"]["research"]["langs"] == ["en", "sv"]
    assert payload["config"]["profiles"]["research"]["cache_dir"] == ".yt-scribe/cache"
    assert payload["config"]["profiles"]["research"]["resume"] is True
    assert payload["config"]["profiles"]["research"]["transcript_format"] == "json"
    assert payload["config"]["profiles"]["research"]["out"] == "notes.md"
    assert payload["config"]["profiles"]["research"]["bundle_dir"] == ".yt-scribe/runs/current"
    assert payload["config"]["profiles"]["research"]["out_dir"] == "batch-notes"
    assert payload["config"]["profiles"]["research"]["manifest"] == "batch-manifest.json"
    assert payload["config"]["profiles"]["research"]["stdout"] is True
    assert payload["config"]["profiles"]["research"]["chunk_chars"] == 5000

    result = run_cli("--json", "config", "profile", "get", "research", env=env)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["profile"]["name"] == "research"
    assert payload["profile"]["values"]["template"] == "lecture"


def test_install_skills_writes_global_skill_files(tmp_path):
    env = os.environ.copy()
    env["YT_SCRIBE_AGENTS_SKILLS_DIR"] = str(tmp_path / "agent-skills")

    result = run_cli("--json", "install-skills", env=env)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert (tmp_path / "agent-skills" / "yt-scribe" / "SKILL.md").exists()
    assert (
        tmp_path
        / "agent-skills"
        / "yt-scribe-transcript-polisher"
        / "SKILL.md"
    ).exists()
    assert "opencode_agents_dir" not in payload["skills"]


def test_setup_installs_support_files_and_reports_next_command(tmp_path):
    env = os.environ.copy()
    env["PATH"] = ""
    env["YT_SCRIBE_AGENTS_SKILLS_DIR"] = str(tmp_path / "agent-skills")
    env["YT_SCRIBE_CONFIG"] = str(tmp_path / "config.json")

    result = run_cli("--json", "setup", env=env)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["setup"]["next"]["run"] == "yt-scribe run <youtube-url>"
    assert payload["setup"]["doctor"]["install"]["resolved_command"] is None
    assert (tmp_path / "agent-skills" / "yt-scribe" / "SKILL.md").exists()
    assert (
        tmp_path
        / "agent-skills"
        / "yt-scribe-transcript-polisher"
        / "SKILL.md"
    ).exists()
    assert "opencode_agents_dir" not in payload["setup"]["skills"]
