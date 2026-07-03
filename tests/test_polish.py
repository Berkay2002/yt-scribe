import argparse
import json

import pytest

import yt_scribe
from yt_scribe import polish


def test_polish_package_resolves_instruction_payload():
    args = argparse.Namespace(
        style="summary",
        template=None,
        focus=["Only list decisions."],
        focus_file=None,
        instruction=None,
        prompt_file=None,
        timestamps=True,
    )

    payload = polish.instruction_payload(args, "codex")

    assert payload["mode"] == "custom"
    assert payload["sources"] == ["--focus", "--timestamps"]
    assert "Only list decisions." in payload["text"]
    assert "Do not invent timestamps" in payload["text"]


def test_polish_package_reports_missing_focus_file_as_cli_error(tmp_path):
    args = argparse.Namespace(
        style="summary",
        template=None,
        focus=None,
        focus_file=[str(tmp_path / "missing.txt")],
        instruction=None,
        prompt_file=None,
        timestamps=False,
    )

    with pytest.raises(yt_scribe.CliError) as error:
        polish.resolve_instruction(args, "codex")

    assert error.value.code == "instruction_file_read_failed"
    assert error.value.details["source"] == "--focus-file"


def test_polish_package_output_prefix_updates_file_backed_text(tmp_path):
    output_path = tmp_path / "notes.md"
    output_path.write_text("body\n", encoding="utf-8")

    result = polish.apply_output_prefix(
        {
            "output_path": str(output_path),
            "chars": len("stale"),
            "harness": "codex",
            "text": "stale",
        },
        "front\n",
    )

    assert output_path.read_text(encoding="utf-8") == "front\nbody\n"
    assert result["text"] == "front\nbody\n"
    assert result["chars"] == len("front\nbody\n")


def test_polish_package_parses_harness_events():
    codex_event = {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}
    opencode_event = {"type": "text", "part": {"text": "done"}}

    assert polish.codex_final_text(codex_event) == "done"
    assert polish.opencode_final_text(opencode_event) == "done"
    assert polish.parse_opencode_run_output(json.dumps(opencode_event)) == "done"


def test_polish_package_exposes_chunked_helpers():
    instruction = polish.merge_chunk_instruction("Base instruction")

    assert polish.chunking_disabled_payload()["enabled"] is False
    assert "Merge these polished transcript chunks" in instruction
    assert "Base instruction" in instruction


def test_polish_package_exposes_deep_helpers():
    chunk = {"id": "chunk-001", "start": "00:00", "end": "10:00"}

    assert "Process only chunk-001" in polish.deep_chunk_instruction("Base", chunk)
    assert "## chunk-001" in polish.deep_merge_input([("chunk-001", "notes")])
    assert "Process each chunk" in polish.opencode_server_prompt(
        {
            "dir": "bundle",
            "chunk_manifest": "manifest.json",
            "polished": "polished.md",
        },
        "Base",
    )
