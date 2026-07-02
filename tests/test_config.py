import argparse
import json

from yt_scribe import config


def test_config_module_merges_global_and_project_profiles(tmp_path, monkeypatch):
    global_config = tmp_path / "global.json"
    project_root = tmp_path / "project"
    project_config = project_root / ".yt-scribe" / "config.json"
    project_config.parent.mkdir(parents=True)
    global_config.write_text(
        json.dumps(
            {
                "default_agent_harness": "codex",
                "profiles": {"research": {"style": "notes"}},
            }
        ),
        encoding="utf-8",
    )
    project_config.write_text(
        json.dumps({"profiles": {"research": {"style": "summary", "langs": ["en", "sv"]}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(config.CONFIG_ENV_VAR, str(global_config))

    assert config.config_payload(config.read_config())["profiles"]["research"]["style"] == "notes"

    monkeypatch.delenv(config.CONFIG_ENV_VAR)
    monkeypatch.chdir(project_root)

    payload = config.config_payload(config.read_config())
    assert payload["project_path"] == str(project_config)
    assert payload["profiles"]["research"] == {"style": "summary", "langs": ["en", "sv"]}


def test_config_module_profile_from_args_normalizes_languages():
    args = argparse.Namespace(
        style="summary",
        template=None,
        agent_harness=None,
        cache_dir=None,
        transcript=None,
        transcript_format=None,
        out=None,
        bundle_dir=None,
        out_dir=None,
        manifest=None,
        langs="en, sv",
        focus=None,
        front_matter=None,
        timestamps=True,
        resume=None,
        stdout=None,
        chunk_chars=5000,
    )

    profile = config.profile_from_args(args)

    assert profile["langs"] == ["en", "sv"]
    assert profile["timestamps"] is True
    assert profile["chunk_chars"] == 5000
