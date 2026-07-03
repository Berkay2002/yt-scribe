"""Deep polishing workflow helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .. import CliError, ProgressReporter
from ..file_io import write_text
from ..runs import (
    read_json_file,
    update_deep_engine_metadata,
    utc_timestamp,
    write_bundle_metadata,
)
from ..transcripts import DEEP_CHUNK_MAX_CHARS
from .chunked import merge_chunk_instruction
from .harnesses import harness_label, run_agent_polish

__all__ = [
    "codex_csv_fanout_jobs",
    "deep_chunk_instruction",
    "deep_merge_input",
    "merge_chunk_instruction",
    "opencode_server_config",
    "opencode_server_prompt",
    "run_codex_csv_fanout_jobs",
    "run_codex_csv_fanout_engine",
    "run_deep_fallback_engine",
    "run_opencode_server_engine",
    "run_opencode_server_session",
    "verify_opencode_server_artifacts",
    "write_codex_csv_fanout_metadata",
    "write_opencode_server_metadata",
]


COMMAND_NAME = "yt-scribe"


def deep_chunk_instruction(base_instruction: str, chunk: dict[str, Any]) -> str:
    return (
        f"{base_instruction}\n\n"
        "Deep workflow chunk mode:\n"
        f"- Process only {chunk['id']} covering {chunk['start']} to {chunk['end']}.\n"
        "- Produce detailed notes for this chunk.\n"
        "- Preserve timestamp anchors that support important claims.\n"
        "- Do not infer from missing chunks."
    )


def deep_merge_input(notes: list[tuple[str, str]]) -> str:
    return "\n\n".join(f"## {chunk_id}\n\n{text.strip()}" for chunk_id, text in notes)


def run_deep_fallback_engine(
    *,
    bundle: dict[str, str],
    instruction: str,
    out_path: str,
    model: str | None,
    cwd: str | None,
    harness: str,
    resume: bool,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    manifest = read_json_file(bundle["chunk_manifest"])
    chunk_results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    notes: list[tuple[str, str]] = []
    resumed_chunks = 0

    for chunk in manifest.get("chunks", []):
        chunk_id = str(chunk["id"])
        text_path = Path(chunk["text_path"])
        note_path = Path(chunk["note_path"])
        if resume and note_path.is_file() and note_path.read_text(encoding="utf-8").strip():
            notes.append((chunk_id, note_path.read_text(encoding="utf-8")))
            resumed_chunks += 1
            chunk_results.append(
                {
                    "chunk_id": chunk_id,
                    "status": "skipped",
                    "note_path": str(note_path),
                    "reason": "resume_existing_note",
                }
            )
            continue

        if progress:
            progress.message(f"Deep chunk {chunk_id}: running {harness_label(harness)}")
        try:
            result = run_agent_polish(
                transcript_text=text_path.read_text(encoding="utf-8"),
                instruction=deep_chunk_instruction(instruction, chunk),
                out_path=str(note_path),
                model=model,
                cwd=cwd,
                harness=harness,
                progress=progress,
            )
        except CliError as exc:
            failure = {
                "chunk_id": chunk_id,
                "code": exc.code,
                "message": str(exc),
                "note_path": str(note_path),
            }
            failures.append(failure)
            chunk_results.append({"chunk_id": chunk_id, "status": "failed", **failure})
            continue

        notes.append((chunk_id, result["text"]))
        chunk_results.append(
            {
                "chunk_id": chunk_id,
                "status": "succeeded",
                "note_path": result["output_path"],
                "chars": result["chars"],
            }
        )

    if failures:
        update_deep_engine_metadata(
            bundle,
            status="incomplete",
            harness=harness,
            chunk_results=chunk_results,
            failures=failures,
            resumed_chunks=resumed_chunks,
            merge_status="skipped",
        )
        raise CliError(
            "Deep run incomplete; retry with --resume after fixing the failed chunks",
            "deep_run_incomplete",
            {
                "failed_chunks": failures,
                "resume_command": (
                    f'{COMMAND_NAME} run "<youtube-url>" --workflow deep '
                    f'--bundle-dir "{bundle["dir"]}" --resume'
                ),
            },
        )

    merge_result = run_agent_polish(
        transcript_text=deep_merge_input(notes),
        instruction=merge_chunk_instruction(instruction),
        out_path=out_path,
        model=model,
        cwd=cwd,
        harness=harness,
        progress=progress,
    )
    update_deep_engine_metadata(
        bundle,
        status="completed",
        harness=merge_result["harness"],
        chunk_results=chunk_results,
        failures=[],
        resumed_chunks=resumed_chunks,
        merge_status="merged",
        output_path=merge_result["output_path"],
    )
    merge_result["chunking"] = {
        "enabled": True,
        "engine": "managed_fallback",
        "chunk_chars": DEEP_CHUNK_MAX_CHARS,
        "chunks": len(manifest.get("chunks") or []),
        "merge_status": "merged",
        "chunk_artifact_dir": bundle["chunks_dir"],
        "resumed_chunks": resumed_chunks,
    }
    merge_result["bundle_status"] = "completed"
    return merge_result


def codex_csv_fanout_jobs(
    manifest: dict[str, Any],
    instruction: str,
) -> list[dict[str, Any]]:
    jobs = []
    for chunk in manifest.get("chunks", []):
        jobs.append(
            {
                "chunk_id": chunk["id"],
                "text_path": chunk["text_path"],
                "note_path": chunk["note_path"],
                "start": chunk["start"],
                "end": chunk["end"],
                "instruction": (
                    f"{instruction}\n\n"
                    "CSV fan-out worker task:\n"
                    f"- Read only {chunk['text_path']}.\n"
                    f"- Produce detailed notes only for {chunk['id']} "
                    f"covering {chunk['start']} to {chunk['end']}.\n"
                    "- Return only markdown notes for this chunk."
                ),
            }
        )
    return jobs


def run_codex_csv_fanout_jobs(
    jobs: list[dict[str, Any]],
    *,
    instruction: str,
    model: str | None,
    cwd: str | None,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    raise CliError(
        "Codex CSV fan-out is not available in this environment",
        "codex_csv_fanout_unavailable",
        {"jobs": len(jobs), "model": model, "cwd": cwd},
    )


def write_codex_csv_fanout_metadata(
    bundle: dict[str, str],
    *,
    status: str,
    fallback_used: bool,
    failures: list[dict[str, Any]],
    reason: str | None = None,
) -> None:
    metadata = read_json_file(bundle["metadata"])
    metadata["codex_csv_fanout"] = {
        "status": status,
        "fallback_used": fallback_used,
        "failures": failures,
        "reason": reason,
        "updated_at": utc_timestamp(),
    }
    write_bundle_metadata(bundle["metadata"], metadata)


def run_codex_csv_fanout_engine(
    *,
    bundle: dict[str, str],
    instruction: str,
    out_path: str,
    model: str | None,
    cwd: str | None,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    manifest = read_json_file(bundle["chunk_manifest"])
    jobs = codex_csv_fanout_jobs(manifest, instruction)
    fanout = run_codex_csv_fanout_jobs(
        jobs,
        instruction=instruction,
        model=model,
        cwd=cwd,
        progress=progress,
    )
    results = {
        str(result.get("chunk_id")): str(result.get("text") or "")
        for result in fanout.get("results", [])
        if result.get("chunk_id")
    }
    failures = list(fanout.get("failures") or [])
    notes: list[tuple[str, str]] = []
    chunk_results: list[dict[str, Any]] = []

    for chunk in manifest.get("chunks", []):
        chunk_id = str(chunk["id"])
        note_text = results.get(chunk_id, "").strip()
        note_path = Path(chunk["note_path"])
        if not note_text:
            failures.append({"chunk_id": chunk_id, "message": "worker returned no notes"})
            chunk_results.append({"chunk_id": chunk_id, "status": "missing"})
            continue
        note_text = note_text + "\n"
        write_text(str(note_path), note_text)
        notes.append((chunk_id, note_text))
        chunk_results.append(
            {
                "chunk_id": chunk_id,
                "status": "succeeded",
                "note_path": str(note_path),
                "chars": len(note_text),
            }
        )

    if failures:
        write_codex_csv_fanout_metadata(
            bundle,
            status="fallback",
            fallback_used=True,
            failures=failures,
            reason="codex_csv_fanout_incomplete",
        )
        raise CliError(
            "Codex CSV fan-out did not produce all required chunk notes",
            "codex_csv_fanout_incomplete",
            {"failures": failures},
        )

    merge_result = run_agent_polish(
        transcript_text=deep_merge_input(notes),
        instruction=merge_chunk_instruction(instruction),
        out_path=out_path,
        model=model,
        cwd=cwd,
        harness="codex",
        progress=progress,
    )
    metadata = read_json_file(bundle["metadata"])
    metadata["bundle_status"] = "completed"
    metadata["engine"] = {
        "name": "codex_csv_fanout",
        "status": "completed",
        "harness": "codex",
        "chunk_results": chunk_results,
        "failures": [],
        "merge_status": "merged",
        "output_path": merge_result["output_path"],
        "updated_at": utc_timestamp(),
    }
    metadata["codex_csv_fanout"] = {
        "status": "completed",
        "fallback_used": False,
        "failures": [],
        "reason": None,
        "updated_at": utc_timestamp(),
    }
    write_bundle_metadata(bundle["metadata"], metadata)
    merge_result["chunking"] = {
        "enabled": True,
        "engine": "codex_csv_fanout",
        "chunk_chars": DEEP_CHUNK_MAX_CHARS,
        "chunks": len(manifest.get("chunks") or []),
        "merge_status": "merged",
        "chunk_artifact_dir": bundle["chunks_dir"],
        "resumed_chunks": 0,
    }
    merge_result["bundle_status"] = "completed"
    return merge_result


def opencode_server_config() -> dict[str, Any]:
    username = os.environ.get("OPENCODE_SERVER_USERNAME")
    password = os.environ.get("OPENCODE_SERVER_PASSWORD")
    auth = None
    if username or password:
        auth = {"username": username, "password": password}
    return {
        "host": "127.0.0.1",
        "port": None,
        "cors": False,
        "auth": auth,
    }


def opencode_server_prompt(bundle: dict[str, str], instruction: str) -> str:
    return (
        "Use OpenCode server-backed deep orchestration for this yt-scribe bundle.\n\n"
        f"Bundle directory: {bundle['dir']}\n"
        f"Chunk manifest: {bundle['chunk_manifest']}\n"
        f"Expected final notes: {bundle['polished']}\n\n"
        "Process each chunk using bounded subagents or background subagents when available. "
        "Write every per-chunk note to the note paths in the manifest, then write final "
        "merged notes to the expected final notes path. Do not create project-local "
        ".opencode configuration for this one-off run.\n\n"
        f"Polishing instruction:\n{instruction}"
    )


def run_opencode_server_session(
    *,
    bundle: dict[str, str],
    prompt: str,
    server: dict[str, Any],
    model: str | None,
    cwd: str | None,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    raise CliError(
        "OpenCode server orchestration is not available in this environment",
        "opencode_server_unavailable",
        {"host": server["host"], "model": model, "cwd": cwd},
    )


def verify_opencode_server_artifacts(
    bundle: dict[str, str],
    manifest: dict[str, Any],
    out_path: str,
) -> list[str]:
    required = [out_path]
    required.extend(str(chunk["note_path"]) for chunk in manifest.get("chunks", []))
    return [
        str(Path(path).expanduser().resolve())
        for path in required
        if not Path(path).expanduser().is_file()
        or not Path(path).expanduser().read_text(encoding="utf-8").strip()
    ]


def write_opencode_server_metadata(
    bundle: dict[str, str],
    *,
    status: str,
    fallback_used: bool,
    reason: str | None,
    session: dict[str, Any] | None = None,
    missing: list[str] | None = None,
    server: dict[str, Any] | None = None,
) -> None:
    metadata = read_json_file(bundle["metadata"])
    metadata["opencode_server"] = {
        "status": status,
        "fallback_used": fallback_used,
        "reason": reason,
        "session": session,
        "missing": missing or [],
        "server": server,
        "updated_at": utc_timestamp(),
    }
    write_bundle_metadata(bundle["metadata"], metadata)


def run_opencode_server_engine(
    *,
    bundle: dict[str, str],
    instruction: str,
    out_path: str,
    model: str | None,
    cwd: str | None,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    manifest = read_json_file(bundle["chunk_manifest"])
    server = opencode_server_config()
    prompt = opencode_server_prompt(bundle, instruction)
    session = run_opencode_server_session(
        bundle=bundle,
        prompt=prompt,
        server=server,
        model=model,
        cwd=cwd,
        progress=progress,
    )
    if session.get("status") != "completed":
        write_opencode_server_metadata(
            bundle,
            status="fallback",
            fallback_used=True,
            reason="opencode_server_incomplete",
            session=session,
            server=server,
        )
        raise CliError(
            "OpenCode server orchestration did not complete",
            "opencode_server_incomplete",
            {"session": session},
        )

    missing = verify_opencode_server_artifacts(bundle, manifest, out_path)
    if missing:
        write_opencode_server_metadata(
            bundle,
            status="fallback",
            fallback_used=True,
            reason="opencode_server_incomplete",
            session=session,
            missing=missing,
            server=server,
        )
        raise CliError(
            "OpenCode server orchestration did not write required artifacts",
            "opencode_server_incomplete",
            {"missing": missing, "session": session},
        )

    final_text = Path(out_path).read_text(encoding="utf-8")
    metadata = read_json_file(bundle["metadata"])
    metadata["bundle_status"] = "completed"
    metadata["engine"] = {
        "name": "opencode_server",
        "status": "completed",
        "harness": "opencode",
        "session": session,
        "merge_status": "server_completed",
        "output_path": out_path,
        "updated_at": utc_timestamp(),
    }
    metadata["opencode_server"] = {
        "status": "completed",
        "fallback_used": False,
        "reason": None,
        "session": session,
        "missing": [],
        "server": server,
        "updated_at": utc_timestamp(),
    }
    write_bundle_metadata(bundle["metadata"], metadata)
    return {
        "output_path": out_path,
        "chars": len(final_text),
        "harness": "opencode",
        "text": final_text,
        "chunking": {
            "enabled": True,
            "engine": "opencode_server",
            "chunk_chars": DEEP_CHUNK_MAX_CHARS,
            "chunks": len(manifest.get("chunks") or []),
            "merge_status": "server_completed",
            "chunk_artifact_dir": bundle["chunks_dir"],
            "resumed_chunks": 0,
        },
        "bundle_status": "completed",
    }
