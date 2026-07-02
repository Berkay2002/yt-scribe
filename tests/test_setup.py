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
