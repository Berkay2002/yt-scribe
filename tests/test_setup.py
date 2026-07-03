import pytest

import yt_scribe as package
from yt_scribe import setup


def test_setup_module_finds_root_skill_assets():
    path = setup.source_asset_path(".agents/skills/yt-scribe/SKILL.md")

    assert path is not None
    assert path.parts[-3:] == ("skills", "yt-scribe", "SKILL.md")


def test_setup_module_installs_skill_assets_to_configured_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(setup.AGENTS_SKILLS_DIR_ENV_VAR, str(tmp_path / "agent-skills"))

    payload = setup.install_skills()

    assert payload["agents_skills_dir"] == str(tmp_path / "agent-skills")
    assert (tmp_path / "agent-skills" / "yt-scribe" / "SKILL.md").is_file()
    assert (
        tmp_path / "agent-skills" / "yt-scribe-transcript-polisher" / "SKILL.md"
    ).is_file()


def test_setup_module_reports_missing_embedded_assets_as_cli_error(monkeypatch):
    monkeypatch.setattr(setup, "source_asset_path", lambda relative_path: None)

    with pytest.raises(package.CliError) as error:
        setup.asset_content("missing/SKILL.md")

    assert error.value.code == "embedded_asset_missing"
    assert error.value.details["path"] == "missing/SKILL.md"
