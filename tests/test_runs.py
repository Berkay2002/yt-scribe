from pathlib import Path

from yt_scribe import runs


def test_runs_module_creates_unique_managed_run_records(tmp_path):
    first = runs.run_record_for_deep_workflow(
        video_id="dQw4w9WgXcQ",
        source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="Research Talk",
        workflow="deep",
        harness="codex",
        bundle_dir=None,
        root=tmp_path,
    )
    second = runs.run_record_for_deep_workflow(
        video_id="dQw4w9WgXcQ",
        source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="Research Talk",
        workflow="deep",
        harness="codex",
        bundle_dir=None,
        root=tmp_path,
    )

    assert first["name"] == "research-talk-dQw4w9WgXcQ"
    assert second["name"] == "research-talk-dQw4w9WgXcQ-2"
    assert runs.resolve_run_selector(first["name"], tmp_path)["run_id"] == first["run_id"]


def test_runs_module_retrieves_context_from_bundle_artifacts(tmp_path):
    bundle = tmp_path / "run"
    chunks = bundle / "chunks"
    chunks.mkdir(parents=True)
    (bundle / "outline.md").write_text("# Retrieval\nHybrid search details.", encoding="utf-8")
    (chunks / "chunk-001-notes.md").write_text("Dense retrieval notes.", encoding="utf-8")
    (bundle / "transcript.txt").write_text(
        "[00:01] Retrieval combines sparse and dense.",
        encoding="utf-8",
    )
    run = {"bundle_path": str(bundle)}

    context = runs.retrieve_run_context(run, "retrieval dense", top_k=2)
    rendered = runs.render_ask_context(context["hits"])

    assert context["has_hits"] is True
    assert len(context["hits"]) == 2
    assert "retrieval" in rendered.lower()


def test_runs_module_writes_deep_bundle_plan(tmp_path):
    bundle = runs.bundle_paths(str(tmp_path / "bundle"))
    transcript = {
        "video_id": "dQw4w9WgXcQ",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "language": "en",
        "requested_languages": ["en"],
        "track": {"language_code": "en"},
        "segments": [{"start": 1.0, "duration": 0.5, "text": "hello world"}],
        "text": "hello world",
    }

    assert bundle is not None
    plan = runs.write_deep_bundle_plan(
        transcript,
        bundle,
        workflow={"workflow": "deep", "workflow_requested": "deep"},
        harness="codex",
        title="Talk",
        managed_run=None,
    )

    assert Path(plan["chunk_manifest"]).is_file()
    assert plan["verification"]["ok"] is True
