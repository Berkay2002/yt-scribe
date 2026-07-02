#!/usr/bin/env python3
"""MCP server entry point for yt-scribe."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import yt_scribe

COMMAND_NAME = "yt-scribe-mcp"
READ_ONLY_ENV_VAR = "YT_SCRIBE_MCP_READ_ONLY"
DEFAULT_TRANSPORT = "stdio"
DEFAULT_HTTP_HOST = "127.0.0.1"
DEFAULT_HTTP_PORT = 3000
HTTP_ENDPOINT_PATH = "/mcp"
LOCAL_HTTP_HOSTS = {"127.0.0.1", "localhost", "::1"}
SUPPORTED_TOOL_GROUPS = ("info", "inspect", "fetch", "polish", "run")
AGENT_TOOL_GROUPS = ("polish", "run")
SERVER_INSTRUCTIONS = (
    "Use yt-scribe for YouTube transcript workflows. Inspect captions before "
    "assuming a transcript exists. Fetch tools are read-only. Tools named "
    "agent_* may spawn Codex or OpenCode and can run longer; do not use them "
    "for read-only transcript access. Do not bypass private, disabled, or "
    "unavailable captions. Polished output must not add facts that are not in "
    "the transcript."
)


def error_payload(exc: yt_scribe.CliError) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": exc.code,
            "message": str(exc),
            **exc.details,
        },
    }


def truthy_env(value: str | None) -> bool:
    return bool(value and value.strip().lower() in {"1", "true", "yes", "on"})


def agent_tools_enabled(read_only: bool = False) -> bool:
    return not read_only and not truthy_env(os.environ.get(READ_ONLY_ENV_VAR))


def server_info(enable_agent_tools: bool = True) -> dict[str, Any]:
    """Return basic yt-scribe MCP server metadata."""
    tool_groups = [
        group
        for group in SUPPORTED_TOOL_GROUPS
        if enable_agent_tools or group not in AGENT_TOOL_GROUPS
    ]
    return {
        "package": "yt-scribe",
        "version": yt_scribe.VERSION,
        "default_transport": DEFAULT_TRANSPORT,
        "supported_tool_groups": tool_groups,
        "agent_tools_enabled": enable_agent_tools,
    }


def inspect_youtube_captions(url: str) -> dict[str, Any]:
    """Inspect caption availability for a YouTube URL or video ID."""
    try:
        return {"ok": True, "video": yt_scribe.inspect_video_payload(url)}
    except yt_scribe.CliError as exc:
        return error_payload(exc)


def fetch_youtube_transcript(
    url: str,
    language: str = "en",
    languages: list[str] | None = None,
    transcript_format: str = "text",
    timestamps: bool = False,
    cache_dir: str | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Fetch a YouTube transcript without starting an agent harness."""
    try:
        normalized_languages = yt_scribe.normalize_languages(languages, default=language)
        fetch_payload, _rendered = yt_scribe.fetch_transcript_payload(
            url,
            normalized_languages,
            transcript_format,
            Path(cache_dir).expanduser().resolve() if cache_dir else None,
            resume,
            timestamps=timestamps,
            include_transcript=True,
        )
        return {"ok": True, "fetch": fetch_payload}
    except yt_scribe.CliError as exc:
        return error_payload(exc)


def agent_polish_transcript(
    transcript: str,
    style: str = "notes",
    focus: str | None = None,
    instruction: str | None = None,
    timestamps: bool = False,
    agent_harness: str | None = None,
    model: str | None = None,
    max_chars: int = 0,
) -> dict[str, Any]:
    """Polish transcript text with the configured Codex or OpenCode agent harness."""
    try:
        focus_items = [focus] if focus else None
        payload, result = yt_scribe.polish_transcript_text_payload(
            transcript,
            style=style,
            focus=focus_items,
            instruction=instruction,
            timestamps=timestamps,
            agent_harness=agent_harness,
            model=model,
            max_chars=max_chars,
            out_path=None,
            progress=yt_scribe.ProgressReporter(False),
        )
        payload["text"] = result["text"]
        return {"ok": True, "polish": payload}
    except yt_scribe.CliError as exc:
        return error_payload(exc)


def agent_fetch_and_polish_youtube(
    url: str,
    language: str = "en",
    languages: list[str] | None = None,
    style: str = "notes",
    focus: str | None = None,
    instruction: str | None = None,
    timestamps: bool = False,
    agent_harness: str | None = None,
    model: str | None = None,
    max_chars: int = 0,
    cache_dir: str | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    """Fetch a YouTube transcript and polish it with Codex or OpenCode."""
    try:
        normalized_languages = yt_scribe.normalize_languages(languages, default=language)
        focus_items = [focus] if focus else None
        payload, result = yt_scribe.run_youtube_polish_payload(
            url,
            languages=normalized_languages,
            cache_dir=Path(cache_dir).expanduser().resolve() if cache_dir else None,
            resume=resume,
            style=style,
            focus=focus_items,
            instruction=instruction,
            timestamps=timestamps,
            agent_harness=agent_harness,
            model=model,
            max_chars=max_chars,
            progress=yt_scribe.ProgressReporter(False),
        )
        payload["text"] = result["text"]
        return {"ok": True, "run": payload}
    except yt_scribe.CliError as exc:
        return error_payload(exc)


async def report_progress(
    ctx: Any,
    progress: float,
    total: float,
    message: str,
) -> None:
    if ctx is not None:
        await ctx.report_progress(progress=progress, total=total, message=message)


async def inspect_youtube_captions_with_progress(url: str, ctx: Any = None) -> dict[str, Any]:
    await report_progress(ctx, 0, 100, "Resolving YouTube video")
    await report_progress(ctx, 40, 100, "Inspecting caption tracks")
    result = inspect_youtube_captions(url)
    await report_progress(ctx, 100, 100, "Caption inspection complete")
    return result


async def fetch_youtube_transcript_with_progress(
    url: str,
    language: str = "en",
    languages: list[str] | None = None,
    transcript_format: str = "text",
    timestamps: bool = False,
    cache_dir: str | None = None,
    resume: bool = False,
    ctx: Any = None,
) -> dict[str, Any]:
    await report_progress(ctx, 0, 100, "Resolving YouTube video")
    await report_progress(ctx, 20, 100, "Fetching transcript")
    result = fetch_youtube_transcript(
        url=url,
        language=language,
        languages=languages,
        transcript_format=transcript_format,
        timestamps=timestamps,
        cache_dir=cache_dir,
        resume=resume,
    )
    await report_progress(ctx, 90, 100, "Rendering transcript output")
    await report_progress(ctx, 100, 100, "Transcript fetch complete")
    return result


async def agent_polish_transcript_with_progress(
    transcript: str,
    style: str = "notes",
    focus: str | None = None,
    instruction: str | None = None,
    timestamps: bool = False,
    agent_harness: str | None = None,
    model: str | None = None,
    max_chars: int = 0,
    ctx: Any = None,
) -> dict[str, Any]:
    await report_progress(ctx, 0, 100, "Preparing agent-backed polishing")
    await report_progress(ctx, 20, 100, "Starting agent harness")
    result = agent_polish_transcript(
        transcript=transcript,
        style=style,
        focus=focus,
        instruction=instruction,
        timestamps=timestamps,
        agent_harness=agent_harness,
        model=model,
        max_chars=max_chars,
    )
    await report_progress(ctx, 100, 100, "Agent-backed polishing complete")
    return result


async def agent_fetch_and_polish_youtube_with_progress(
    url: str,
    language: str = "en",
    languages: list[str] | None = None,
    style: str = "notes",
    focus: str | None = None,
    instruction: str | None = None,
    timestamps: bool = False,
    agent_harness: str | None = None,
    model: str | None = None,
    max_chars: int = 0,
    cache_dir: str | None = None,
    resume: bool = False,
    ctx: Any = None,
) -> dict[str, Any]:
    await report_progress(ctx, 0, 100, "Fetching transcript")
    await report_progress(ctx, 45, 100, "Starting agent-backed polishing")
    result = agent_fetch_and_polish_youtube(
        url=url,
        language=language,
        languages=languages,
        style=style,
        focus=focus,
        instruction=instruction,
        timestamps=timestamps,
        agent_harness=agent_harness,
        model=model,
        max_chars=max_chars,
        cache_dir=cache_dir,
        resume=resume,
    )
    await report_progress(ctx, 100, 100, "Fetch and polish complete")
    return result


def create_mcp_server(enable_agent_tools: bool | None = None) -> Any:
    """Create the FastMCP server.

    Import MCP lazily so the normal CLI install path does not require MCP dependencies.
    """
    try:
        from fastmcp import Context, FastMCP
        from fastmcp.dependencies import CurrentContext
        from mcp.types import ToolAnnotations
    except ImportError as exc:
        raise RuntimeError(
            "MCP support is not installed. Install it with `pip install 'yt-scribe[mcp]'`."
        ) from exc

    enable_agent_tools = (
        agent_tools_enabled() if enable_agent_tools is None else enable_agent_tools
    )
    mcp = FastMCP("yt-scribe", instructions=SERVER_INSTRUCTIONS)
    current_context = CurrentContext()

    @mcp.tool()
    def yt_scribe_info() -> dict[str, Any]:
        """Return package, version, transport, and MCP tool group information."""
        return server_info(enable_agent_tools)

    @mcp.tool(
        name="inspect_youtube_captions",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def inspect_youtube_captions_tool(
        url: str,
        ctx: Context = current_context,
    ) -> dict[str, Any]:
        """Inspect YouTube caption tracks without fetching transcript text."""
        return await inspect_youtube_captions_with_progress(url, ctx)

    @mcp.tool(
        name="fetch_youtube_transcript",
        annotations=ToolAnnotations(
            readOnlyHint=True,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    async def fetch_youtube_transcript_tool(
        url: str,
        language: str = "en",
        languages: list[str] | None = None,
        transcript_format: str = "text",
        timestamps: bool = False,
        cache_dir: str | None = None,
        resume: bool = False,
        ctx: Context = current_context,
    ) -> dict[str, Any]:
        """Fetch YouTube transcript text without starting Codex or OpenCode."""
        return await fetch_youtube_transcript_with_progress(
            url=url,
            language=language,
            languages=languages,
            transcript_format=transcript_format,
            timestamps=timestamps,
            cache_dir=cache_dir,
            resume=resume,
            ctx=ctx,
        )

    if enable_agent_tools:

        @mcp.tool(
            name="agent_polish_transcript",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=False,
                openWorldHint=True,
            ),
        )
        async def agent_polish_transcript_tool(
            transcript: str,
            style: str = "notes",
            focus: str | None = None,
            instruction: str | None = None,
            timestamps: bool = False,
            agent_harness: str | None = None,
            model: str | None = None,
            max_chars: int = 0,
            ctx: Context = current_context,
        ) -> dict[str, Any]:
            """Agent-backed: polish transcript text with Codex or OpenCode."""
            return await agent_polish_transcript_with_progress(
                transcript=transcript,
                style=style,
                focus=focus,
                instruction=instruction,
                timestamps=timestamps,
                agent_harness=agent_harness,
                model=model,
                max_chars=max_chars,
                ctx=ctx,
            )

        @mcp.tool(
            name="agent_fetch_and_polish_youtube",
            annotations=ToolAnnotations(
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=False,
                openWorldHint=True,
            ),
        )
        async def agent_fetch_and_polish_youtube_tool(
            url: str,
            language: str = "en",
            languages: list[str] | None = None,
            style: str = "notes",
            focus: str | None = None,
            instruction: str | None = None,
            timestamps: bool = False,
            agent_harness: str | None = None,
            model: str | None = None,
            max_chars: int = 0,
            cache_dir: str | None = None,
            resume: bool = False,
            ctx: Context = current_context,
        ) -> dict[str, Any]:
            """Agent-backed: fetch a YouTube transcript and polish it."""
            return await agent_fetch_and_polish_youtube_with_progress(
                url=url,
                language=language,
                languages=languages,
                style=style,
                focus=focus,
                instruction=instruction,
                timestamps=timestamps,
                agent_harness=agent_harness,
                model=model,
                max_chars=max_chars,
                cache_dir=cache_dir,
                resume=resume,
                ctx=ctx,
            )

    return mcp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=COMMAND_NAME,
        description="Run the local yt-scribe MCP server.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {yt_scribe.VERSION}",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help=f"run Streamable HTTP instead of STDIO; endpoint: {HTTP_ENDPOINT_PATH}",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help=(
            "do not expose agent-backed polish/run tools; also available with "
            f"{READ_ONLY_ENV_VAR}=1"
        ),
    )
    parser.add_argument(
        "--host",
        help=f"HTTP host to bind, default: {DEFAULT_HTTP_HOST}",
    )
    parser.add_argument(
        "--port",
        type=int,
        help=f"HTTP port to bind, default: {DEFAULT_HTTP_PORT}",
    )
    return parser


def validate_transport_args(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    if not args.http and (args.host or args.port):
        parser.error("Host and port arguments are only valid with --http.")


def warn_if_nonlocal_http(host: str) -> None:
    if host not in LOCAL_HTTP_HOSTS:
        print(
            "\n"
            f"WARNING: {COMMAND_NAME} is binding to a non-localhost interface "
            f"({host}).\n"
            "This exposes local transcript fetching and agent-backed polishing tools "
            "to any client that can reach this interface.\n"
            "The server has no built-in authentication. Only proceed if you "
            "understand the risk.\n",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_transport_args(parser, args)

    try:
        mcp = create_mcp_server(enable_agent_tools=agent_tools_enabled(args.read_only))
    except RuntimeError as exc:
        print(f"{COMMAND_NAME}: {exc}", file=sys.stderr)
        return 1

    if args.http:
        host = args.host or DEFAULT_HTTP_HOST
        port = args.port or DEFAULT_HTTP_PORT
        warn_if_nonlocal_http(host)
        mcp.run(transport="http", host=host, port=port)
    else:
        mcp.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
