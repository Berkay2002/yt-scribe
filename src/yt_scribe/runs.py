"""Managed run registry and deep bundle helpers for yt-scribe."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from . import CliError
from .transcripts import (
    DEEP_CHUNK_MAX_CHARS,
    DEEP_CHUNK_OVERLAP_SECONDS,
    DEEP_CHUNK_TARGET_SECONDS,
    plan_deep_chunks,
    render_timestamped_transcript,
)

COMMAND_NAME = "yt-scribe"
DATA_DIR_ENV_VAR = "YT_SCRIBE_DATA_DIR"
RUN_REGISTRY_FILENAME = "registry.json"


def write_text(path: str | None, text: str) -> str | None:
    if not path:
        return None
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return str(target)

def bundle_paths(bundle_dir: str | None) -> dict[str, str] | None:
    if not bundle_dir:
        return None
    root = Path(bundle_dir).expanduser().resolve()
    return {
        "dir": str(root),
        "transcript": str(root / "transcript.txt"),
        "transcript_json": str(root / "transcript.json"),
        "chunks_dir": str(root / "chunks"),
        "chunk_manifest": str(root / "chunk-manifest.json"),
        "polished": str(root / "polished.md"),
        "metadata": str(root / "metadata.json"),
        "structural_verification": str(root / "structural-verification.json"),
    }


def write_bundle_metadata(path: str, data: dict[str, Any]) -> str:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(target)


def write_json_file(path: str | Path, data: Any) -> str:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(target)


def write_deep_bundle_plan(
    transcript: dict[str, Any],
    bundle: dict[str, str],
    *,
    workflow: dict[str, int | str | None],
    harness: str,
    title: str | None,
    managed_run: dict[str, Any] | None,
) -> dict[str, Any]:
    root = Path(bundle["dir"])
    chunks_dir = Path(bundle["chunks_dir"])
    chunks_dir.mkdir(parents=True, exist_ok=True)

    transcript_json_path = write_json_file(bundle["transcript_json"], transcript)
    timestamped_transcript = render_timestamped_transcript(transcript) + "\n"
    transcript_text_path = write_text(bundle["transcript"], timestamped_transcript)
    chunks = plan_deep_chunks(transcript)
    manifest_chunks = []
    for chunk in chunks:
        text_path = chunks_dir / f"{chunk['id']}.txt"
        note_path = chunks_dir / f"{chunk['id']}-notes.md"
        write_text(str(text_path), chunk["text"] + "\n")
        manifest_chunks.append(
            {
                key: value
                for key, value in chunk.items()
                if key != "text"
            }
            | {
                "text_path": str(text_path.resolve()),
                "note_path": str(note_path.resolve()),
            }
        )

    manifest = {
        "version": 1,
        "video_id": transcript["video_id"],
        "url": transcript["url"],
        "chunking": {
            "target_seconds": DEEP_CHUNK_TARGET_SECONDS,
            "overlap_seconds": DEEP_CHUNK_OVERLAP_SECONDS,
            "max_chars": DEEP_CHUNK_MAX_CHARS,
        },
        "chunks": manifest_chunks,
    }
    manifest_path = write_json_file(bundle["chunk_manifest"], manifest)
    metadata = {
        "video_id": transcript["video_id"],
        "url": transcript["url"],
        "title": title,
        **workflow,
        "language": transcript["language"],
        "requested_languages": transcript.get("requested_languages"),
        "track": transcript.get("track"),
        "agent_harness": harness,
        "bundle_status": "planned",
        "managed_run": managed_run,
        "artifacts": {
            "transcript_json": transcript_json_path,
            "transcript": transcript_text_path,
            "chunk_manifest": manifest_path,
            "structural_verification": str(Path(bundle["structural_verification"]).resolve()),
            "chunks_dir": str(chunks_dir.resolve()),
        },
    }
    metadata_path = write_bundle_metadata(bundle["metadata"], metadata)
    verification = verify_deep_bundle_structure(bundle, manifest)
    verification_path = write_json_file(bundle["structural_verification"], verification)
    return {
        "dir": str(root),
        "transcript": transcript_text_path,
        "transcript_json": transcript_json_path,
        "chunk_manifest": manifest_path,
        "structural_verification": verification_path,
        "chunks_dir": str(chunks_dir.resolve()),
        "metadata": metadata_path,
        "manifest": manifest,
        "verification": verification,
    }


def verify_deep_bundle_structure(
    bundle: dict[str, str],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    required = [
        bundle["transcript_json"],
        bundle["transcript"],
        bundle["chunk_manifest"],
        bundle["metadata"],
    ]
    for chunk in manifest.get("chunks", []):
        required.append(str(chunk["text_path"]))
    missing = [
        str(Path(path).expanduser().resolve())
        for path in required
        if not Path(path).expanduser().is_file()
    ]
    return {
        "ok": not missing,
        "missing": missing,
        "checked_at": utc_timestamp(),
        "chunks": len(manifest.get("chunks") or []),
    }


def data_dir(path: str | Path | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()
    override = os.environ.get(DATA_DIR_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / COMMAND_NAME
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home).expanduser() / COMMAND_NAME
    return Path.home() / ".local" / "share" / COMMAND_NAME


def managed_runs_dir(root: str | Path | None = None) -> Path:
    return data_dir(root) / "runs"


def run_registry_path(root: str | Path | None = None) -> Path:
    return managed_runs_dir(root) / RUN_REGISTRY_FILENAME


def empty_run_registry() -> dict[str, Any]:
    return {"version": 1, "runs": []}


def load_run_registry(root: str | Path | None = None) -> dict[str, Any]:
    path = run_registry_path(root)
    if not path.is_file():
        return empty_run_registry()
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CliError(
            f"Could not parse run registry {path}: {exc}",
            "invalid_run_registry",
        ) from exc
    if not isinstance(registry, dict):
        raise CliError(f"Run registry {path} must contain a JSON object", "invalid_run_registry")
    runs = registry.get("runs")
    if not isinstance(runs, list):
        raise CliError("Run registry must contain a runs list", "invalid_run_registry")
    return {"version": int(registry.get("version") or 1), "runs": runs}


def save_run_registry(root: str | Path | None, registry: dict[str, Any]) -> Path:
    path = run_registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def slugify_run_name(value: str | None, fallback: str = "youtube-video") -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text or fallback


def unique_run_name(
    title: str | None,
    video_id: str,
    registry: dict[str, Any],
    exclude_run_id: str | None = None,
) -> str:
    base = f"{slugify_run_name(title)}-{video_id}"
    existing = {
        str(run.get("name"))
        for run in registry.get("runs", [])
        if run.get("run_id") != exclude_run_id
    }
    if base not in existing:
        return base
    for index in range(2, 10_000):
        candidate = f"{base}-{index}"
        if candidate not in existing:
            return candidate
    raise CliError(f"Could not find an available run name for {base}", "run_name_exhausted")


def run_record_for_deep_workflow(
    *,
    video_id: str,
    source_url: str,
    title: str | None,
    workflow: str,
    harness: str,
    bundle_dir: str | None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    registry = load_run_registry(root)
    name = unique_run_name(title, video_id, registry)
    managed = bundle_dir is None
    bundle_path = (
        managed_runs_dir(root) / name
        if managed
        else Path(bundle_dir).expanduser().resolve()
    )
    timestamp = utc_timestamp()
    record = {
        "run_id": f"{video_id}-{time.time_ns()}",
        "name": name,
        "title": title or name,
        "video_id": video_id,
        "source_url": source_url,
        "bundle_path": str(bundle_path),
        "managed": managed,
        "workflow": workflow,
        "harness": harness,
        "engine": "pending",
        "status": "running",
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    registry["runs"].append(record)
    save_run_registry(root, registry)
    return record


def update_run_record(record: dict[str, Any], root: str | Path | None = None) -> dict[str, Any]:
    registry = load_run_registry(root)
    runs = registry.get("runs", [])
    for index, item in enumerate(runs):
        if item.get("run_id") == record.get("run_id"):
            record["updated_at"] = utc_timestamp()
            runs[index] = record
            save_run_registry(root, registry)
            return record
    raise CliError(f"Run disappeared from registry: {record.get('run_id')}", "run_not_found")


def matching_runs(selector: str, registry: dict[str, Any]) -> list[dict[str, Any]]:
    exact = [
        run
        for run in registry.get("runs", [])
        if selector
        in {
            str(run.get("run_id") or ""),
            str(run.get("name") or ""),
            str(run.get("video_id") or ""),
        }
    ]
    if exact:
        return exact
    return [
        run
        for run in registry.get("runs", [])
        if str(run.get("run_id") or "").startswith(selector)
        or str(run.get("name") or "").startswith(selector)
        or str(run.get("video_id") or "").startswith(selector)
    ]


def resolve_run_selector(selector: str, root: str | Path | None = None) -> dict[str, Any]:
    registry = load_run_registry(root)
    matches = matching_runs(selector, registry)
    if not matches:
        raise CliError(f"No managed run matched: {selector}", "run_not_found")
    if len(matches) > 1:
        raise CliError(
            f"Run selector is ambiguous: {selector}",
            "ambiguous_run",
            {"matches": [run.get("name") for run in matches]},
        )
    return matches[0]


def rename_run(
    selector: str,
    title: str,
    root: str | Path | None = None,
) -> dict[str, Any]:
    registry = load_run_registry(root)
    matches = matching_runs(selector, registry)
    if not matches:
        raise CliError(f"No managed run matched: {selector}", "run_not_found")
    if len(matches) > 1:
        raise CliError(
            f"Run selector is ambiguous: {selector}",
            "ambiguous_run",
            {"matches": [run.get("name") for run in matches]},
        )
    record = matches[0]
    old_bundle_path = Path(str(record["bundle_path"]))
    new_name = unique_run_name(title, str(record["video_id"]), registry, record.get("run_id"))
    if record.get("managed"):
        new_bundle_path = old_bundle_path.parent / new_name
        if new_bundle_path.exists() and new_bundle_path != old_bundle_path:
            raise CliError(f"Run directory already exists: {new_bundle_path}", "run_path_exists")
        if old_bundle_path.exists() and new_bundle_path != old_bundle_path:
            old_bundle_path.rename(new_bundle_path)
        record["bundle_path"] = str(new_bundle_path)
    record["name"] = new_name
    record["title"] = title
    record["updated_at"] = utc_timestamp()
    for index, item in enumerate(registry["runs"]):
        if item.get("run_id") == record.get("run_id"):
            registry["runs"][index] = record
            break
    save_run_registry(root, registry)
    return record


RETRIEVAL_STOPWORDS = {
    "a",
    "about",
    "and",
    "are",
    "did",
    "does",
    "for",
    "how",
    "is",
    "of",
    "or",
    "the",
    "they",
    "to",
    "what",
    "with",
}


def retrieval_tokens(text: str) -> set[str]:
    tokens = set()
    for token in re.findall(r"[A-Za-z0-9]+", text.lower()):
        if token in RETRIEVAL_STOPWORDS or len(token) < 3:
            continue
        tokens.add(token)
        if token.endswith("s") and len(token) > 4:
            tokens.add(token[:-1])
    return tokens


def retrieval_score(query_tokens: set[str], text: str) -> int:
    return len(query_tokens & retrieval_tokens(text))


def split_markdown_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("#") and current:
            blocks.append("\n".join(current).strip())
            current = [line]
        elif line.strip():
            current.append(line)
        elif current:
            blocks.append("\n".join(current).strip())
            current = []
    if current:
        blocks.append("\n".join(current).strip())
    return [block for block in blocks if block]


def transcript_timestamp(line: str) -> str | None:
    match = re.match(r"\[([0-9:]+)\]\s*(.*)", line.strip())
    return match.group(1) if match else None


def retrieve_run_context(
    run: dict[str, Any],
    question: str,
    *,
    top_k: int = 5,
) -> dict[str, Any]:
    bundle_dir = Path(str(run["bundle_path"]))
    query_tokens = retrieval_tokens(question)
    hits: list[dict[str, Any]] = []

    outline_path = bundle_dir / "outline.md"
    if outline_path.is_file():
        for block in split_markdown_blocks(outline_path.read_text(encoding="utf-8")):
            score = retrieval_score(query_tokens, block)
            if score:
                hits.append(
                    {
                        "kind": "outline",
                        "score": score,
                        "path": str(outline_path),
                        "text": block,
                    }
                )

    chunks_dir = bundle_dir / "chunks"
    if chunks_dir.is_dir():
        for note_path in sorted(chunks_dir.glob("*-notes.md")):
            text = note_path.read_text(encoding="utf-8")
            score = retrieval_score(query_tokens, text)
            if score:
                hits.append(
                    {
                        "kind": "chunk_note",
                        "score": score,
                        "path": str(note_path),
                        "text": text.strip(),
                    }
                )

    transcript_path = bundle_dir / "transcript.txt"
    if transcript_path.is_file():
        transcript_lines = transcript_path.read_text(encoding="utf-8").splitlines()
        for line_number, line in enumerate(transcript_lines, 1):
            score = retrieval_score(query_tokens, line)
            if score:
                hits.append(
                    {
                        "kind": "transcript",
                        "score": score,
                        "path": str(transcript_path),
                        "line": line_number,
                        "timestamp": transcript_timestamp(line),
                        "text": line.strip(),
                    }
                )

    hits.sort(key=lambda hit: (-int(hit["score"]), str(hit["kind"]), str(hit.get("path"))))
    selected = hits[:top_k]
    return {
        "question": question,
        "query_tokens": sorted(query_tokens),
        "hits": selected,
        "has_hits": bool(selected),
    }


def render_ask_context(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return "No relevant context found for this question."
    parts = []
    for index, hit in enumerate(hits, start=1):
        timestamp = f" [{hit['timestamp']}]" if hit.get("timestamp") else ""
        parts.append(
            f"[{index}] {hit['kind']}{timestamp} score={hit['score']}\n{hit['text']}"
        )
    return "\n\n".join(parts)


def ask_agent_instruction(question: str) -> str:
    return (
        "Answer the question using only the retrieved context. "
        "If the context is insufficient, say what is missing. "
        "Do not use the full transcript or outside knowledge.\n\n"
        f"Question: {question}"
    )


def deep_next_commands(managed_run: dict[str, Any] | None, bundle_dir: str | None) -> list[str]:
    if managed_run:
        selector = str(managed_run["name"])
        bundle_path = str(managed_run["bundle_path"])
    else:
        selector = "<run-name>"
        bundle_path = str(Path(bundle_dir).expanduser().resolve()) if bundle_dir else "<bundle-dir>"
    return [
        f"{COMMAND_NAME} runs list",
        f"{COMMAND_NAME} runs open {selector}",
        f'{COMMAND_NAME} runs rename {selector} "<new name>"',
        f'{COMMAND_NAME} run "<youtube-url>" --workflow deep --bundle-dir "{bundle_path}" --resume',
        f'{COMMAND_NAME} ask {selector} "<question>" --show-context',
    ]


def read_json_file(path: str | Path) -> Any:
    return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))


def update_deep_engine_metadata(
    bundle: dict[str, str],
    *,
    status: str,
    harness: str,
    chunk_results: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    resumed_chunks: int,
    merge_status: str,
    output_path: str | None = None,
) -> dict[str, Any]:
    metadata = read_json_file(bundle["metadata"])
    metadata["bundle_status"] = "completed" if status == "completed" else "incomplete"
    metadata["engine"] = {
        "name": "managed_fallback",
        "status": status,
        "harness": harness,
        "chunk_results": chunk_results,
        "failures": failures,
        "resumed_chunks": resumed_chunks,
        "merge_status": merge_status,
        "output_path": output_path,
        "updated_at": utc_timestamp(),
    }
    write_bundle_metadata(bundle["metadata"], metadata)
    return metadata
