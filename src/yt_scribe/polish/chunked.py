"""Chunk-and-merge polishing workflow helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import CliError, ProgressReporter
from .harnesses import run_agent_polish

__all__ = [
    "chunk_artifact_dir",
    "chunk_output_path",
    "chunking_disabled_payload",
    "merge_chunk_instruction",
    "run_chunked_agent_polish",
]


def chunk_artifact_dir(out_path: str | None) -> Path | None:
    if not out_path:
        return None
    return Path(f"{out_path}.chunks").expanduser().resolve()


def chunk_output_path(directory: Path, index: int) -> Path:
    return directory / f"chunk-{index:03}.md"


def merge_chunk_instruction(instruction: str) -> str:
    return (
        "Merge these polished transcript chunks into one coherent polished output. "
        "Remove duplicate headings introduced by chunking, preserve the original order, "
        "and do not add facts that are not present in the chunk outputs.\n\n"
        "Original polishing instruction:\n"
        f"{instruction}"
    )


def chunking_disabled_payload() -> dict[str, Any]:
    return {
        "enabled": False,
        "chunk_chars": 0,
        "chunks": 1,
        "merge_status": "not_needed",
        "chunk_artifact_dir": None,
        "resumed_chunks": 0,
    }


def run_chunked_agent_polish(
    chunks: list[str],
    instruction: str,
    out_path: str | None,
    model: str | None,
    cwd: str | None,
    harness: str,
    chunk_chars: int,
    resume: bool,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    if len(chunks) <= 1:
        result = run_agent_polish(
            transcript_text=chunks[0] if chunks else "",
            instruction=instruction,
            out_path=out_path,
            model=model,
            cwd=cwd,
            harness=harness,
            progress=progress,
        )
        result["chunking"] = {
            "enabled": True,
            "chunk_chars": chunk_chars,
            "chunks": len(chunks),
            "merge_status": "not_needed",
            "chunk_artifact_dir": None,
            "resumed_chunks": 0,
        }
        return result

    artifact_dir = chunk_artifact_dir(out_path)
    if artifact_dir:
        artifact_dir.mkdir(parents=True, exist_ok=True)

    polished_chunks: list[str] = []
    resumed_chunks = 0
    for index, chunk in enumerate(chunks, start=1):
        chunk_path = chunk_output_path(artifact_dir, index) if artifact_dir else None
        if resume and chunk_path and chunk_path.is_file():
            existing_text = chunk_path.read_text(encoding="utf-8")
            if existing_text.strip():
                polished_chunks.append(existing_text)
                resumed_chunks += 1
                continue
        try:
            result = run_agent_polish(
                transcript_text=chunk,
                instruction=instruction,
                out_path=str(chunk_path) if chunk_path else None,
                model=model,
                cwd=cwd,
                harness=harness,
                progress=progress,
            )
        except CliError as exc:
            raise CliError(
                f"Chunk {index} polish failed: {exc}",
                "chunk_polish_failed",
                {"chunk_index": index, "error_code": exc.code},
            ) from exc
        polished_chunks.append(result["text"])

    merge_input = "\n\n".join(
        f"## Chunk {index}\n\n{chunk.strip()}"
        for index, chunk in enumerate(polished_chunks, start=1)
    )
    try:
        result = run_agent_polish(
            transcript_text=merge_input,
            instruction=merge_chunk_instruction(instruction),
            out_path=out_path,
            model=model,
            cwd=cwd,
            harness=harness,
            progress=progress,
        )
    except CliError as exc:
        raise CliError(
            f"Chunk merge failed: {exc}",
            "chunk_merge_failed",
            {"chunks": len(chunks), "error_code": exc.code},
        ) from exc

    result["chunking"] = {
        "enabled": True,
        "chunk_chars": chunk_chars,
        "chunks": len(chunks),
        "merge_status": "merged",
        "chunk_artifact_dir": str(artifact_dir) if artifact_dir else None,
        "resumed_chunks": resumed_chunks,
    }
    return result
