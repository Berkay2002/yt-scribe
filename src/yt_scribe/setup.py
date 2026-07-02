"""Setup and skill installation helpers for yt-scribe."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import EMBEDDED_SKILL_ASSETS

COMMAND_NAME = "yt-scribe"
AGENTS_SKILLS_DIR_ENV_VAR = "YT_SCRIBE_AGENTS_SKILLS_DIR"
ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

def command_path(name: str) -> str | None:
    return shutil.which(name)


def command_invocation(executable: str, *args: str) -> list[str]:
    suffix = Path(executable).suffix.lower()
    if sys.platform == "win32" and suffix in {".bat", ".cmd"}:
        return ["cmd", "/c", executable, *args]
    if sys.platform == "win32" and suffix == ".ps1":
        return [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            executable,
            *args,
        ]
    return [executable, *args]


def command_output(command: list[str]) -> str | None:
    executable = command_path(command[0]) if len(command) > 0 else None
    if executable:
        command = command_invocation(executable, *command[1:])
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = ANSI_PATTERN.sub("", result.stdout).strip()
    return output or None


def agents_skills_dir() -> Path:
    override = os.environ.get(AGENTS_SKILLS_DIR_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".agents" / "skills"


def source_asset_path(relative_path: str) -> Path | None:
    package_root = Path(__file__).resolve().parent
    repo_root = package_root.parents[1]
    for root in (repo_root, package_root):
        candidate = root / Path(relative_path)
        if candidate.is_file():
            return candidate
    return None


def asset_content(relative_path: str) -> str:
    source = source_asset_path(relative_path)
    if source:
        return source.read_text(encoding="utf-8")
    return EMBEDDED_SKILL_ASSETS[relative_path]


def skill_asset_targets() -> dict[str, Path]:
    skills_dir = agents_skills_dir()
    return {
        ".agents/skills/yt-scribe/SKILL.md": skills_dir / "yt-scribe" / "SKILL.md",
        ".agents/skills/yt-scribe/harness/codex.md": (
            skills_dir / "yt-scribe" / "harness" / "codex.md"
        ),
        ".agents/skills/yt-scribe/harness/opencode.md": (
            skills_dir / "yt-scribe" / "harness" / "opencode.md"
        ),
        ".agents/skills/yt-scribe/agents/openai.yaml": (
            skills_dir / "yt-scribe" / "agents" / "openai.yaml"
        ),
        ".agents/skills/yt-scribe-transcript-polisher/SKILL.md": (
            skills_dir / "yt-scribe-transcript-polisher" / "SKILL.md"
        ),
        ".agents/skills/yt-scribe-transcript-polisher/harness/codex.md": (
            skills_dir / "yt-scribe-transcript-polisher" / "harness" / "codex.md"
        ),
        ".agents/skills/yt-scribe-transcript-polisher/harness/opencode.md": (
            skills_dir / "yt-scribe-transcript-polisher" / "harness" / "opencode.md"
        ),
        ".agents/skills/yt-scribe-transcript-polisher/agents/openai.yaml": (
            skills_dir / "yt-scribe-transcript-polisher" / "agents" / "openai.yaml"
        ),
    }


def install_skills() -> dict[str, Any]:
    installed = []
    for relative_path, target in skill_asset_targets().items():
        target.parent.mkdir(parents=True, exist_ok=True)
        content = asset_content(relative_path)
        target.write_text(content, encoding="utf-8")
        installed.append(
            {
                "source": relative_path,
                "path": str(target),
                "bytes": len(content.encode("utf-8")),
            }
        )
    return {
        "agents_skills_dir": str(agents_skills_dir()),
        "installed": installed,
    }


def setup_payload() -> dict[str, Any]:
    from .config import doctor_payload

    return {
        "skills": install_skills(),
        "doctor": doctor_payload(),
        "next": {
            "run": "yt-scribe run <youtube-url>",
            "check": "yt-scribe doctor",
        },
    }


def init_project(project_dir: str | Path, profile: str | None = None) -> dict[str, Any]:
    root = Path(project_dir).expanduser().resolve()
    yt_scribe_dir = root / ".yt-scribe"
    yt_scribe_dir.mkdir(parents=True, exist_ok=True)
    guidance_path = yt_scribe_dir / "AGENTS.md"
    config_path = yt_scribe_dir / "config.json"
    guidance = (
        "# yt-scribe\n\n"
        "Use `yt-scribe` for YouTube transcript workflows in this project.\n\n"
        "- Prefer `yt-scribe run \"<youtube-url>\"` for one-command notes.\n"
        "- Use `yt-scribe inspect \"<youtube-url>\" --brief` before assuming captions exist.\n"
        "- Do not bypass private, disabled, unavailable, or missing caption tracks.\n"
        "- Do not add facts that are not in the transcript.\n"
    )
    guidance_path.write_text(guidance, encoding="utf-8")
    config = {"profiles": {}}
    if profile:
        config["profiles"][profile] = {
            "style": "notes",
            "template": "research",
            "front_matter": True,
        }
    config_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "dir": str(yt_scribe_dir),
        "guidance_path": str(guidance_path),
        "config_path": str(config_path),
    }


def skills_payload() -> dict[str, Any]:
    targets = skill_asset_targets()
    return {
        "agents_skills_dir": str(agents_skills_dir()),
        "targets": {
            relative_path: {
                "path": str(target),
                "installed": target.exists(),
            }
            for relative_path, target in targets.items()
        },
    }
