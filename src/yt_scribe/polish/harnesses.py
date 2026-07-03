"""Agent harness execution and JSON event parsing."""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import CliError, ProgressReporter
from ..file_io import write_text
from ..setup import command_invocation, command_path

__all__ = [
    "append_tail",
    "codex_final_text",
    "codex_item_progress_message",
    "codex_progress_message",
    "drain_stream_tail",
    "harness_label",
    "opencode_final_text",
    "opencode_progress_message",
    "parse_opencode_run_output",
    "run_agent_polish",
    "run_codex_polish",
    "run_jsonl_process",
    "run_opencode_polish",
]


def append_tail(buffer: list[str], text: str, limit: int) -> None:
    buffer.append(text)
    joined = "".join(buffer)
    if len(joined) > limit:
        buffer[:] = [joined[-limit:]]


def drain_stream_tail(stream: Any, buffer: list[str], limit: int) -> None:
    if stream is None:
        return
    for line in stream:
        append_tail(buffer, line, limit)


def run_jsonl_process(
    command: list[str],
    progress: ProgressReporter | None,
    progress_message: Callable[[dict[str, Any]], str | None],
    final_text: Callable[[dict[str, Any]], str | None],
    stdin_text: str | None = None,
) -> dict[str, Any]:
    stdout_tail: list[str] = []
    stderr_tail: list[str] = []
    text_parts: list[str] = []
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE if stdin_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    stderr_thread = threading.Thread(
        target=drain_stream_tail,
        args=(process.stderr, stderr_tail, 4000),
        daemon=True,
    )
    stderr_thread.start()

    try:
        if stdin_text is not None and process.stdin:
            try:
                process.stdin.write(stdin_text)
                process.stdin.close()
            except OSError:
                pass

        if process.stdout:
            for line in process.stdout:
                append_tail(stdout_tail, line, 4000)
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                if progress:
                    message = progress_message(event)
                    if message:
                        progress.message(message)
                text = final_text(event)
                if text:
                    text_parts.append(text)
        returncode = process.wait()
    finally:
        stderr_thread.join(timeout=0.5)

    return {
        "returncode": returncode,
        "stdout_tail": "".join(stdout_tail),
        "stderr_tail": "".join(stderr_tail),
        "text": "".join(text_parts),
    }


def codex_item_progress_message(event_type: str, item: dict[str, Any]) -> str | None:
    item_type = item.get("type")
    action = "started" if event_type == "item.started" else "completed"
    if item_type == "reasoning":
        return f"Codex {action} reasoning"
    if item_type == "command_execution":
        command = str(item.get("command") or "").strip()
        return f"Codex {action} command: {command}" if command else f"Codex {action} command"
    if item_type == "agent_message":
        text = str(item.get("text") or "")
        if event_type == "item.completed":
            return f"Codex completed final message ({len(text)} chars)"
        return "Codex started final message"
    if item_type == "mcp_tool_call":
        name = str(item.get("name") or item.get("tool_name") or "").strip()
        return f"Codex {action} MCP tool: {name}" if name else f"Codex {action} MCP tool"
    if item_type == "web_search_call":
        return f"Codex {action} web search"
    if item_type == "file_change":
        return f"Codex {action} file change"
    if item_type == "plan_update":
        return f"Codex {action} plan update"
    if isinstance(item_type, str) and item_type:
        return f"Codex {action} {item_type.replace('_', ' ')}"
    return None


def codex_progress_message(event: dict[str, Any]) -> str | None:
    event_type = event.get("type")
    if event_type == "thread.started":
        return "Codex thread started"
    if event_type == "turn.started":
        return "Codex turn started"
    if event_type == "turn.failed":
        return "Codex turn failed"
    if event_type == "turn.completed":
        usage = event.get("usage")
        if isinstance(usage, dict) and isinstance(usage.get("output_tokens"), int):
            return f"Codex turn completed ({usage['output_tokens']} output tokens)"
        return "Codex turn completed"
    if event_type == "error":
        message = event.get("message") or event.get("error") or "unknown error"
        return f"Codex error: {message}"
    if event_type in {"item.started", "item.completed"}:
        item = event.get("item")
        if isinstance(item, dict):
            return codex_item_progress_message(str(event_type), item)
    return None


def codex_final_text(event: dict[str, Any]) -> str | None:
    if event.get("type") != "item.completed":
        return None
    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "agent_message":
        return None
    text = item.get("text")
    return text if isinstance(text, str) else None


def opencode_progress_message(event: dict[str, Any]) -> str | None:
    event_type = event.get("type")
    part = event.get("part")
    if event_type == "step_start":
        return "OpenCode started step"
    if event_type == "step_finish":
        return "OpenCode completed step"
    if event_type == "tool_use" and isinstance(part, dict):
        name = str(part.get("tool") or part.get("name") or "tool")
        state = part.get("state")
        status = state.get("status") if isinstance(state, dict) else None
        action = "completed" if status == "completed" else "updated"
        return f"OpenCode {action} tool: {name}"
    if event_type == "reasoning" and isinstance(part, dict):
        text = str(part.get("text") or "")
        return f"OpenCode completed reasoning ({len(text)} chars)"
    if event_type == "text" and isinstance(part, dict):
        text = str(part.get("text") or "")
        return f"OpenCode completed text output ({len(text)} chars)"
    if event_type == "error":
        message = event.get("error") or "unknown error"
        return f"OpenCode error: {message}"
    return None


def opencode_final_text(event: dict[str, Any]) -> str | None:
    if event.get("type") != "text":
        return None
    part = event.get("part")
    if not isinstance(part, dict):
        return None
    text = part.get("text")
    return text if isinstance(text, str) else None


def run_codex_polish(
    transcript_text: str,
    instruction: str,
    out_path: str | None,
    model: str | None,
    cwd: str | None,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    codex = command_path("codex")
    if not codex:
        raise CliError("codex was not found on PATH", "codex_missing")

    with tempfile.TemporaryDirectory(prefix="yt-scribe-") as tmp_dir:
        final_path = Path(tmp_dir) / "final.md"
        command = command_invocation(
            codex,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--json",
            "--output-last-message",
            str(final_path),
        )
        if cwd:
            command.extend(["--cd", str(Path(cwd).expanduser().resolve())])
        if model:
            command.extend(["--model", model])
        command.append(instruction)

        if progress:
            with progress.wait("Running Codex polisher"):
                result = run_jsonl_process(
                    command,
                    progress,
                    codex_progress_message,
                    codex_final_text,
                    stdin_text=transcript_text,
                )
        else:
            result = run_jsonl_process(
                command,
                progress,
                codex_progress_message,
                codex_final_text,
                stdin_text=transcript_text,
            )
        if result["returncode"] != 0:
            raise CliError(
                "codex exec failed",
                "codex_exec_failed",
                {
                    "returncode": result["returncode"],
                    "stderr_tail": result["stderr_tail"],
                    "stdout_tail": result["stdout_tail"],
                },
            )

        polished = final_path.read_text(encoding="utf-8") if final_path.exists() else result["text"]
        polished = polished.strip() + "\n"
        written = write_text(out_path, polished)
        return {
            "output_path": written,
            "chars": len(polished),
            "harness": "codex",
            "codex_stderr_tail": result["stderr_tail"][-2000:],
            "codex_stdout_tail": result["stdout_tail"][-2000:],
            "text": polished,
        }


def run_opencode_polish(
    transcript_text: str,
    instruction: str,
    out_path: str | None,
    model: str | None,
    cwd: str | None,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    opencode = command_path("opencode")
    if not opencode:
        raise CliError("opencode was not found on PATH", "opencode_missing")

    with tempfile.TemporaryDirectory(prefix="yt-scribe-") as tmp_dir:
        transcript_path = Path(tmp_dir) / "transcript.txt"
        transcript_path.write_text(transcript_text, encoding="utf-8")
        command = command_invocation(
            opencode,
            "run",
            instruction,
            "--file",
            str(transcript_path),
            "--format",
            "json",
            "--thinking",
        )
        if cwd:
            command.extend(["--dir", str(Path(cwd).expanduser().resolve())])
        if model:
            command.extend(["--model", model])

        if progress:
            with progress.wait("Running OpenCode polisher"):
                result = run_jsonl_process(
                    command,
                    progress,
                    opencode_progress_message,
                    opencode_final_text,
                )
        else:
            result = run_jsonl_process(
                command,
                progress,
                opencode_progress_message,
                opencode_final_text,
            )
        if result["returncode"] != 0:
            raise CliError(
                "opencode run failed",
                "opencode_run_failed",
                {
                    "returncode": result["returncode"],
                    "stderr_tail": result["stderr_tail"],
                    "stdout_tail": result["stdout_tail"],
                },
            )

        polished_text = result["text"] or parse_opencode_run_output(result["stdout_tail"])
        polished = polished_text.strip() + "\n"
        written = write_text(out_path, polished)
        return {
            "output_path": written,
            "chars": len(polished),
            "harness": "opencode",
            "opencode_stderr_tail": result["stderr_tail"][-2000:],
            "opencode_stdout_tail": result["stdout_tail"][-2000:],
            "text": polished,
        }


def parse_opencode_run_output(output: str) -> str:
    text_parts: list[str] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = event.get("part")
        if event.get("type") == "text" and isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str):
                text_parts.append(text)
    if text_parts:
        return "".join(text_parts)
    return output


def run_agent_polish(
    transcript_text: str,
    instruction: str,
    out_path: str | None,
    model: str | None,
    cwd: str | None,
    harness: str,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    if harness == "codex":
        return run_codex_polish(transcript_text, instruction, out_path, model, cwd, progress)
    if harness == "opencode":
        return run_opencode_polish(transcript_text, instruction, out_path, model, cwd, progress)
    raise CliError(f"Unsupported agent harness: {harness}", "unsupported_agent_harness")


def harness_label(harness: str) -> str:
    return {"codex": "Codex", "opencode": "OpenCode"}.get(harness, harness)
