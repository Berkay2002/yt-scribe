import yt_scribe
from yt_scribe import batch, config, polish, runs, setup, transcripts, verify, youtube


def test_package_root_exposes_intentional_interface():
    assert yt_scribe.VERSION == "0.1.0"
    assert yt_scribe.CliError("message", "code").code == "code"
    assert yt_scribe.youtube is youtube
    assert yt_scribe.transcripts is transcripts
    assert yt_scribe.polish is polish
    assert yt_scribe.config is config
    assert yt_scribe.setup is setup
    assert yt_scribe.batch is batch
    assert yt_scribe.verify is verify
    assert yt_scribe.runs is runs


def test_package_root_does_not_export_accidental_workflow_helpers():
    assert not hasattr(yt_scribe, "fetch_transcript")
    assert not hasattr(yt_scribe, "run_agent_polish")
    assert not hasattr(yt_scribe, "build_parser")
