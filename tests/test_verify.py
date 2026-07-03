import json

from yt_scribe import verify


def test_verify_module_reports_supported_and_unsupported_claims():
    result = verify.verify_polished_output(
        "Alice shipped 42 widgets.\nBob shipped 99 widgets.",
        "[00:01] Alice shipped 42 widgets.",
    )

    assert result["summary"] == {
        "claims": 2,
        "supported": 1,
        "unsupported": 1,
        "uncertain": 0,
    }
    assert result["findings"][0]["transcript_anchor"] == "00:01"
    assert result["findings"][1]["unsupported_terms"] == ["Bob", "99"]


def test_verify_module_loads_transcript_json_segments(tmp_path):
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(
        json.dumps({"segments": [{"start": 1.0, "text": "Alice shipped 42 widgets."}]}),
        encoding="utf-8",
    )

    loaded = verify.load_verification_transcript(transcript_path)

    assert loaded["text"] == "Alice shipped 42 widgets."
    assert loaded["entries"][0]["anchor"] == "00:01"
