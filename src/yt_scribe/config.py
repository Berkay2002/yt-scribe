"""Configuration, profiles, proxy, and doctor payloads for yt-scribe."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
from pathlib import Path
from typing import Any

from youtube_transcript_api.proxies import GenericProxyConfig, InvalidProxyConfig

from . import CliError
from .setup import command_output, command_path, skills_payload
from .youtube import normalize_languages

VERSION = "0.1.0"
COMMAND_NAME = "yt-scribe"
DEFAULT_AGENT_HARNESS = "codex"
AGENT_HARNESSES = ("codex", "opencode")
CONFIG_ENV_VAR = "YT_SCRIBE_CONFIG"
CONFIG_FILENAME = "config.json"
HTTP_PROXY_ENV_VAR = "YT_SCRIBE_HTTP_PROXY"
HTTPS_PROXY_ENV_VAR = "YT_SCRIBE_HTTPS_PROXY"
DEFAULT_CACHE_DIR = Path(".yt-scribe") / "cache"
PROJECT_CONFIG = Path(".yt-scribe") / CONFIG_FILENAME

def cache_dir_from_args(args: argparse.Namespace) -> Path | None:
    if getattr(args, "cache_dir", None):
        return Path(args.cache_dir).expanduser().resolve()
    if getattr(args, "resume", False):
        return DEFAULT_CACHE_DIR.resolve()
    return None


def validate_proxy_url(value: str, source: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if not parsed.scheme or not parsed.netloc or not parsed.hostname:
        raise CliError(
            f"{source} must be a full proxy URL like http://user:pass@host:8080",
            "invalid_proxy_config",
        )
    try:
        _ = parsed.port
    except ValueError as exc:
        raise CliError(
            f"{source} has an invalid port. Use a numeric port, not a placeholder.",
            "invalid_proxy_config",
        ) from exc
    return value


def proxy_config_from_args(args: argparse.Namespace) -> GenericProxyConfig | None:
    http_proxy = getattr(args, "http_proxy", None) or os.environ.get(HTTP_PROXY_ENV_VAR)
    https_proxy = getattr(args, "https_proxy", None) or os.environ.get(HTTPS_PROXY_ENV_VAR)
    if not http_proxy and not https_proxy:
        return None
    if http_proxy:
        http_proxy = validate_proxy_url(http_proxy, "--http-proxy")
    if https_proxy:
        https_proxy = validate_proxy_url(https_proxy, "--https-proxy")
    try:
        return GenericProxyConfig(http_url=http_proxy, https_url=https_proxy)
    except InvalidProxyConfig as exc:
        raise CliError(str(exc), "invalid_proxy_config") from exc
def config_path() -> Path:
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / COMMAND_NAME / CONFIG_FILENAME
    return Path.home() / ".config" / COMMAND_NAME / CONFIG_FILENAME


def project_config_path(start: str | Path | None = None) -> Path | None:
    if os.environ.get(CONFIG_ENV_VAR):
        return None
    current = Path.cwd() if start is None else Path(start).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        candidate = directory / PROJECT_CONFIG
        if candidate.is_file():
            return candidate
    return None


def read_config_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CliError(f"Could not parse config file {path}: {exc}", "invalid_config") from exc
    if not isinstance(loaded, dict):
        raise CliError(f"Config file {path} must contain a JSON object", "invalid_config")
    harness = loaded.get("default_agent_harness")
    if harness is not None and harness not in AGENT_HARNESSES:
        raise CliError(
            f"Unsupported default_agent_harness in config: {harness}",
            "invalid_config",
        )
    profiles = loaded.get("profiles")
    if profiles is not None and not isinstance(profiles, dict):
        raise CliError("Config profiles must be a JSON object", "invalid_config")
    return loaded


def merge_configs(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = {**base, **overlay}
    profiles = {
        **(base.get("profiles") or {}),
        **(overlay.get("profiles") or {}),
    }
    if profiles:
        merged["profiles"] = profiles
    elif "profiles" in merged:
        merged.pop("profiles")
    return merged


def read_config() -> dict[str, Any]:
    config = read_config_file(config_path())
    local_path = project_config_path()
    if local_path:
        config = merge_configs(config, read_config_file(local_path))
    return config


def write_config(config: dict[str, Any]) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def effective_agent_harness(config: dict[str, Any] | None = None) -> str:
    config = read_config() if config is None else config
    return config.get("default_agent_harness") or DEFAULT_AGENT_HARNESS


def config_payload(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = read_config() if config is None else config
    local_path = project_config_path()
    return {
        "path": str(local_path or config_path()),
        "global_path": str(config_path()),
        "project_path": str(local_path) if local_path else None,
        "default_agent_harness": config.get("default_agent_harness"),
        "effective_agent_harness": effective_agent_harness(config),
        "profiles": config.get("profiles") or {},
    }


def normalize_profile_languages(value: str | list[str] | tuple[str, ...]) -> list[str]:
    return normalize_languages(value)


def profile_from_args(args: argparse.Namespace) -> dict[str, Any]:
    profile: dict[str, Any] = {}
    for key in (
        "style",
        "template",
        "agent_harness",
        "cache_dir",
        "transcript",
        "transcript_format",
        "out",
        "bundle_dir",
        "out_dir",
        "manifest",
    ):
        value = getattr(args, key, None)
        if value:
            profile[key] = value
    if getattr(args, "langs", None):
        profile["langs"] = normalize_profile_languages(args.langs)
    if getattr(args, "focus", None):
        profile["focus"] = args.focus
    for key in ("front_matter", "timestamps", "resume", "stdout"):
        value = getattr(args, key, None)
        if value is not None:
            profile[key] = bool(value)
    if getattr(args, "chunk_chars", None) is not None:
        profile["chunk_chars"] = args.chunk_chars
    return profile


def get_profile(config: dict[str, Any], name: str) -> dict[str, Any]:
    profiles = config.get("profiles") or {}
    profile = profiles.get(name)
    if not isinstance(profile, dict):
        raise CliError(f"Profile not found: {name}", "profile_not_found")
    return profile


def apply_profile(args: argparse.Namespace) -> None:
    profile_name = getattr(args, "profile", None)
    profile = get_profile(read_config(), profile_name) if profile_name else {}
    if getattr(args, "style", None) is None:
        args.style = profile.get("style") or "notes"
    if getattr(args, "template", None) is None:
        args.template = profile.get("template")
    if not getattr(args, "focus", None) and profile.get("focus"):
        args.focus = list(profile["focus"])
    if hasattr(args, "langs") and getattr(args, "langs", None) is None and profile.get("langs"):
        args.langs = ",".join(profile["langs"])
    for key in (
        "cache_dir",
        "transcript",
        "transcript_format",
        "out",
        "bundle_dir",
        "out_dir",
        "manifest",
    ):
        if hasattr(args, key) and getattr(args, key, None) is None and profile.get(key):
            setattr(args, key, profile[key])
    if (
        hasattr(args, "agent_harness")
        and getattr(args, "agent_harness", None) is None
        and profile.get("agent_harness")
    ):
        args.agent_harness = profile["agent_harness"]
    for key in ("front_matter", "timestamps", "resume", "stdout"):
        if hasattr(args, key) and getattr(args, key, None) is None:
            setattr(args, key, bool(profile.get(key, False)))
    if hasattr(args, "chunk_chars") and getattr(args, "chunk_chars", None) is None:
        args.chunk_chars = int(profile.get("chunk_chars", 0) or 0)
    if hasattr(args, "transcript_format") and getattr(args, "transcript_format", None) is None:
        args.transcript_format = "text"


def agent_harness_status() -> dict[str, Any]:
    codex_path = command_path("codex")
    opencode_path = command_path("opencode")
    return {
        "default": effective_agent_harness(),
        "built_in_default": DEFAULT_AGENT_HARNESS,
        "harnesses": {
            "codex": {
                "available": codex_path is not None,
                "path": codex_path,
                "version": command_output(["codex", "--version"]) if codex_path else None,
                "auth_status": command_output(["codex", "login", "status"]) if codex_path else None,
                "command": "codex exec",
            },
            "opencode": {
                "available": opencode_path is not None,
                "path": opencode_path,
                "version": command_output(["opencode", "--version"]) if opencode_path else None,
                "auth_status": command_output(["opencode", "auth", "list"])
                if opencode_path
                else None,
                "command": "opencode run",
            },
        },
    }


def install_bin_dir() -> Path:
    return Path.home() / ".local" / "bin"


def local_install_command() -> str:
    if sys.platform == "win32":
        return ".\\install-local.ps1"
    return "sh ./install-local.sh"


def doctor_payload() -> dict[str, Any]:
    install_dir = str(install_bin_dir())
    path_parts = [
        str(Path(part).resolve())
        for part in os.environ.get("PATH", "").split(os.pathsep)
        if part
    ]
    harness_status = agent_harness_status()
    codex_status = harness_status["harnesses"]["codex"]
    opencode_status = harness_status["harnesses"]["opencode"]
    return {
        "command": COMMAND_NAME,
        "version": VERSION,
        "python": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
        },
        "codex": {
            "available": codex_status["available"],
            "path": codex_status["path"],
            "version": codex_status["version"],
            "auth_status": codex_status["auth_status"],
            "auth": "reuses saved Codex CLI authentication",
        },
        "opencode": {
            "available": opencode_status["available"],
            "path": opencode_status["path"],
            "version": opencode_status["version"],
            "auth_status": opencode_status["auth_status"],
            "auth": "uses the configured OpenCode auth providers",
        },
        "agent_harness": harness_status,
        "youtube": {
            "method": "youtube-transcript-api",
            "yt_dlp_required": False,
        },
        "install": {
            "wrapper_dir": install_dir,
            "wrapper_dir_on_path": str(Path(install_dir).resolve()) in path_parts,
            "resolved_command": command_path(COMMAND_NAME),
            "local_install_command": local_install_command(),
        },
        "config": config_payload(),
        "skills": skills_payload(),
        "lifecycle": lifecycle_steps(),
    }

def lifecycle_steps() -> list[dict[str, str]]:
    return [
        {
            "step": "check",
            "command": "yt-scribe doctor",
            "purpose": "Verify Python, agent harness, config, and PATH setup.",
        },
        {
            "step": "inspect",
            "command": "yt-scribe inspect <youtube-url>",
            "purpose": "Resolve the video and list available caption tracks.",
        },
        {
            "step": "fetch",
            "command": "yt-scribe fetch <youtube-url> --out transcript.txt",
            "purpose": "Save the raw transcript without using an agent harness.",
        },
        {
            "step": "polish",
            "command": "yt-scribe polish transcript.txt --style notes --out notes.md",
            "purpose": "Use the configured agent harness on an existing transcript file.",
        },
        {
            "step": "run",
            "command": "yt-scribe run <youtube-url>",
            "purpose": "Fetch and polish into a notes markdown file.",
        },
    ]
