"""CLI adapter for yt-scribe."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import CliError, ProgressReporter
from .batch import (
    batch_output_path,
    default_polish_output_path,
    default_run_output_path,
    expand_batch_items,
    read_batch_urls,
)
from .config import (
    AGENT_HARNESSES,
    DEFAULT_AGENT_HARNESS,
    HTTP_PROXY_ENV_VAR,
    HTTPS_PROXY_ENV_VAR,
    VERSION,
    apply_profile,
    cache_dir_from_args,
    config_path,
    config_payload,
    doctor_payload,
    effective_agent_harness,
    get_profile,
    lifecycle_steps,
    profile_from_args,
    proxy_config_from_args,
    read_config,
    read_config_file,
    write_config,
)
from .polish import (
    STYLE_INSTRUCTIONS,
    TEMPLATE_INSTRUCTIONS,
    apply_output_prefix,
    chunking_disabled_payload,
    harness_label,
    limit_text,
    opencode_server_config,
    polish_transcript_text_payload,
    render_front_matter,
    resolve_instruction,
    run_agent_polish,
    run_chunked_agent_polish,
    run_codex_csv_fanout_engine,
    run_deep_fallback_engine,
    run_front_matter,
    run_front_matter_data,
    run_opencode_server_engine,
    selected_agent_harness,
    write_codex_csv_fanout_metadata,
    write_opencode_server_metadata,
)
from .runs import (
    ask_agent_instruction,
    bundle_paths,
    deep_next_commands,
    load_run_registry,
    read_json_file,
    rename_run,
    render_ask_context,
    resolve_run_selector,
    retrieve_run_context,
    run_record_for_deep_workflow,
    run_registry_path,
    update_run_record,
    write_bundle_metadata,
    write_deep_bundle_plan,
    write_text,
)
from .setup import init_project, install_skills, setup_payload
from .transcripts import (
    fetch_transcript_payload,
    load_or_fetch_transcript,
    render_timestamped_transcript,
    render_transcript,
    split_transcript_chunks,
)
from .verify import render_verification, verify_polished_file
from .youtube import (
    choose_track,
    extract_video_id,
    fetch_raw_caption_tracks,
    fetch_video_duration_seconds,
    fetch_video_title,
    http_get,
    inspect_video_payload,
    requested_languages,
    select_run_workflow,
    timedtext_url,
)

COMMAND_NAME = "yt-scribe"
RUN_WORKFLOWS = ("auto", "quick", "deep")


def emit(data: Any, as_json: bool, text: str | None = None) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif text is not None:
        print(text, end="" if text.endswith("\n") else "\n")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def emit_error(exc: CliError, as_json: bool) -> int:
    payload = {"ok": False, "error": {"code": exc.code, "message": str(exc), **exc.details}}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stdout)
    else:
        print(f"{COMMAND_NAME}: {exc}", file=sys.stderr)
        if exc.details.get("stderr_tail"):
            print(exc.details["stderr_tail"], file=sys.stderr)
    return 1


def handle_args(args: argparse.Namespace) -> int:
    if args.command in {"polish", "run", "batch"}:
        apply_profile(args)

    if args.command == "setup":
        payload = {"ok": True, "setup": setup_payload()}
        doctor = payload["setup"]["doctor"]
        harnesses = doctor["agent_harness"]["harnesses"]
        text = (
            f"Installed yt-scribe support files:\n"
            f"  agent skills: {payload['setup']['skills']['agents_skills_dir']}\n"
            f"Agent harnesses:\n"
            f"  default: {doctor['agent_harness']['default']}\n"
            f"  codex: {'found' if harnesses['codex']['available'] else 'not found'}\n"
            f"  opencode: {'found' if harnesses['opencode']['available'] else 'not found'}\n"
            f"Command on PATH: {'yes' if doctor['install']['resolved_command'] else 'no'}\n"
            f"Next:\n"
            f"  yt-scribe run \"<youtube-url>\"\n"
        )
        emit(payload, args.json, text)
        return 0

    if args.command == "install-skills":
        payload = {"ok": True, "skills": install_skills()}
        text = (
            f"Installed skills:\n"
            f"  agent skills: {payload['skills']['agents_skills_dir']}\n"
        )
        emit(payload, args.json, text)
        return 0

    if args.command == "init-project":
        result = init_project(args.dir, args.profile)
        payload = {"ok": True, "init_project": result}
        text = (
            "Initialized yt-scribe project guidance:\n"
            f"  dir: {result['dir']}\n"
            f"  guidance: {result['guidance_path']}\n"
            f"  config: {result['config_path']}\n"
        )
        emit(payload, args.json, text)
        return 0

    if args.command == "config":
        config = read_config_file(config_path())
        effective_config = read_config()
        if args.config_command == "set":
            config["default_agent_harness"] = args.value
            write_config(config)
            effective_config = read_config()
        elif args.config_command == "unset":
            config.pop("default_agent_harness", None)
            write_config(config)
            effective_config = read_config()
        elif args.config_command == "profile":
            profiles = config.setdefault("profiles", {})
            if args.profile_command == "set":
                profiles[args.name] = profile_from_args(args)
                write_config(config)
                effective_config = read_config()
            elif args.profile_command == "remove":
                profiles.pop(args.name, None)
                write_config(config)
                effective_config = read_config()
            elif args.profile_command == "get":
                profile = get_profile(effective_config, args.name)
                payload = {
                    "ok": True,
                    "profile": {"name": args.name, "values": profile},
                    "config": config_payload(effective_config),
                }
                emit(payload, args.json)
                return 0

        payload = {"ok": True, "config": config_payload(effective_config)}
        text = (
            f"config: {payload['config']['path']}\n"
            f"default_agent_harness: {payload['config']['default_agent_harness']}\n"
            f"effective_agent_harness: {payload['config']['effective_agent_harness']}\n"
        )
        emit(payload, args.json, text)
        return 0

    if args.command == "doctor":
        emit({"ok": True, "doctor": doctor_payload()}, args.json)
        return 0

    if args.command == "lifecycle":
        payload = {"ok": True, "lifecycle": lifecycle_steps()}
        text = "\n".join(
            f"{item['step']}: {item['command']}\n  {item['purpose']}"
            for item in lifecycle_steps()
        )
        text += "\n"
        emit(payload, args.json, text)
        return 0

    if args.command == "runs":
        if args.runs_command == "list":
            registry = load_run_registry()
            runs = registry["runs"]
            payload = {
                "ok": True,
                "runs": runs,
                "registry_path": str(run_registry_path()),
            }
            text = "\n".join(
                f"{run['name']}\n"
                f"  status: {run['status']}\n"
                f"  title: {run['title']}\n"
                f"  video: {run['video_id']}\n"
                f"  source: {run['source_url']}"
                for run in runs
            )
            emit(payload, args.json, (text + "\n") if text else "No managed runs.\n")
            return 0
        if args.runs_command == "open":
            run = resolve_run_selector(args.selector)
            payload = {"ok": True, "run": run}
            emit(payload, args.json, f"{run['bundle_path']}\n")
            return 0
        if args.runs_command == "rename":
            run = rename_run(args.selector, args.title)
            payload = {"ok": True, "run": run}
            emit(payload, args.json, f"{run['name']}: {run['bundle_path']}\n")
            return 0

    if args.command == "ask":
        run = resolve_run_selector(args.selector)
        if run.get("status") not in {None, "completed"}:
            raise CliError(f"Run is not completed: {run.get('name')}", "run_not_completed")
        question = " ".join(args.question).strip()
        retrieval = retrieve_run_context(run, question, top_k=args.top_k)
        context_text = render_ask_context(retrieval["hits"])
        answer = None
        agent_payload = None
        if args.agent and retrieval["has_hits"]:
            harness = args.agent_harness or effective_agent_harness()
            result = run_agent_polish(
                transcript_text=context_text,
                instruction=ask_agent_instruction(question),
                out_path=None,
                model=args.model,
                cwd=args.cd,
                harness=harness,
                progress=ProgressReporter(False),
            )
            answer = result["text"]
            agent_payload = {
                "agent_harness": result["harness"],
                "chars": result["chars"],
            }
        elif not retrieval["has_hits"]:
            answer = "No relevant context found for this question."
        elif args.show_context:
            answer = context_text

        payload = {
            "ok": True,
            "ask": {
                "run": {
                    "name": run.get("name"),
                    "video_id": run.get("video_id"),
                    "bundle_path": run.get("bundle_path"),
                },
                "question": question,
                "has_hits": retrieval["has_hits"],
                "hits": retrieval["hits"],
                "context": context_text if args.show_context else None,
                "answer": answer,
                "agent": agent_payload,
            },
        }
        text = answer or context_text
        emit(payload, args.json, text + "\n")
        return 0

    if args.command == "inspect":
        proxy_config = proxy_config_from_args(args)
        video = inspect_video_payload(args.url, proxy_config)
        tracks = video["tracks"]
        payload = {
            "ok": True,
            "video": {
                "id": video["id"],
                "url": video["url"],
                "duration_seconds": video["duration_seconds"],
                "tracks": tracks,
            },
        }
        if args.brief:
            payload = {
                "ok": True,
                "video": {
                    "id": video["id"],
                    "url": video["url"],
                    "duration_seconds": video["duration_seconds"],
                    "has_captions": video["has_captions"],
                    "caption_tracks": video["caption_tracks"],
                    "languages": video["languages"],
                    "manual_languages": video["manual_languages"],
                    "auto_generated_languages": video["auto_generated_languages"],
                },
            }
            language_text = ", ".join(payload["video"]["languages"]) or "none"
            track_word = "track" if len(tracks) == 1 else "tracks"
            duration_text = (
                f"duration: {video['duration_seconds']} seconds\n"
                if video["duration_seconds"] is not None
                else ""
            )
            text = (
                f"{video['url']}\n"
                f"{duration_text}"
                f"captions: {'yes' if tracks else 'no'} ({len(tracks)} {track_word})\n"
                f"languages: {language_text}\n"
            )
            emit(payload, args.json, text)
            return 0
        text = (
            f"{video['url']}\n"
            + (
                f"duration: {video['duration_seconds']} seconds\n"
                if video["duration_seconds"] is not None
                else ""
            )
            + "\n".join(
                f"- {track['language_code']}: {track['name']}"
                + (" (auto)" if track["auto_generated"] else "")
                for track in tracks
            )
            + "\n"
        )
        emit(payload, args.json, text)
        return 0

    if args.command == "fetch":
        fetch_payload, rendered = fetch_transcript_payload(
            args.url,
            requested_languages(args),
            args.format,
            cache_dir_from_args(args),
            args.resume,
            proxy_config_from_args(args),
            out_path=args.out,
        )
        payload = {"ok": True, "fetch": fetch_payload}
        if args.json:
            emit(payload, True)
        elif fetch_payload["output_path"]:
            emit(payload, False, f"Wrote transcript to {fetch_payload['output_path']}\n")
        else:
            emit(payload, False, rendered)
        return 0

    if args.command == "polish":
        progress = ProgressReporter(not args.json)
        progress.message(f"Reading transcript from {Path(args.file).expanduser()}")
        transcript_text = Path(args.file).expanduser().read_text(encoding="utf-8")
        limited_text = limit_text(transcript_text, args.max_chars)
        progress.message(f"Loaded transcript ({len(limited_text)} chars)")
        out_path = (
            args.out
            if args.stdout
            else args.out or str(default_polish_output_path(args.file, args.style))
        )
        payload_body, result = polish_transcript_text_payload(
            transcript_text,
            style=args.style,
            template=args.template,
            focus=args.focus,
            focus_file=args.focus_file,
            instruction=args.instruction,
            prompt_file=args.prompt_file,
            timestamps=args.timestamps,
            agent_harness=args.agent_harness,
            model=args.model,
            cwd=args.cd,
            max_chars=args.max_chars,
            out_path=out_path,
            input_path=str(Path(args.file).expanduser().resolve()),
            progress=progress,
        )
        payload = {"ok": True, "polish": payload_body}
        if args.json:
            emit(payload, True)
        elif result["output_path"]:
            emit(payload, False, f"Wrote polished transcript to {result['output_path']}\n")
        else:
            emit(payload, False, result["text"])
        return 0

    if args.command == "run":
        progress = ProgressReporter(not args.json)
        video_id = extract_video_id(args.url)
        proxy_config = proxy_config_from_args(args)
        duration_seconds = (
            fetch_video_duration_seconds(video_id, proxy_config)
            if args.workflow == "auto"
            else None
        )
        workflow = select_run_workflow(args.workflow, duration_seconds)
        progress.message(
            f"Workflow: {workflow['workflow']} ({workflow['workflow_reason']})"
        )
        progress.message(f"Fetching transcript for {video_id}")
        transcript, cache = load_or_fetch_transcript(
            args.url,
            requested_languages(args),
            cache_dir_from_args(args),
            args.resume,
            proxy_config,
        )
        segment_count = len(transcript["segments"])
        segment_word = "segment" if segment_count == 1 else "segments"
        progress.message(
            f"Fetched {segment_count} transcript {segment_word} "
            f"({len(transcript['text'])} chars)"
        )
        harness = selected_agent_harness(args)
        managed_run = None
        run_title = None
        bundle_dir = args.bundle_dir
        if workflow["workflow"] == "deep":
            run_title = fetch_video_title(video_id, proxy_config)
            managed_run = run_record_for_deep_workflow(
                video_id=transcript["video_id"],
                source_url=transcript["url"],
                title=run_title,
                workflow=str(workflow["workflow"]),
                harness=harness,
                bundle_dir=args.bundle_dir,
            )
            bundle_dir = str(managed_run["bundle_path"])
            progress.message(f"Managed run: {managed_run['name']}")
        bundle = bundle_paths(bundle_dir)
        deep_bundle = None
        if bundle and workflow["workflow"] == "deep":
            deep_bundle = write_deep_bundle_plan(
                transcript,
                bundle,
                workflow=workflow,
                harness=harness,
                title=run_title,
                managed_run=managed_run,
            )
            transcript_path = deep_bundle["transcript"]
            if args.transcript:
                rendered = render_transcript(transcript, args.transcript_format)
                write_text(args.transcript, rendered)
        else:
            transcript_target = args.transcript or (bundle["transcript"] if bundle else None)
            rendered = render_transcript(transcript, args.transcript_format)
            transcript_path = write_text(transcript_target, rendered)
        if transcript_path:
            progress.message(f"Wrote raw transcript to {transcript_path}")
        transcript_text = (
            render_timestamped_transcript(transcript) if args.timestamps else transcript["text"]
        )
        original_transcript_chars = len(transcript_text)
        transcript_text = limit_text(transcript_text, args.max_chars)
        if args.max_chars and original_transcript_chars > len(transcript_text):
            progress.message(f"Truncated transcript to {len(transcript_text)} chars")
        if deep_bundle and bundle:
            out_path = args.out or bundle["polished"]
        else:
            out_path = (
                args.out
                if args.stdout
                else args.out
                or (bundle["polished"] if bundle else None)
                or str(default_run_output_path(transcript["video_id"], args.style))
            )
        instruction = resolve_instruction(args, harness)
        progress.message(f"Using {harness_label(harness)}")
        if deep_bundle:
            try:
                if harness == "codex":
                    try:
                        result = run_codex_csv_fanout_engine(
                            bundle=bundle,
                            instruction=instruction.text,
                            out_path=out_path,
                            model=args.model,
                            cwd=args.cd,
                            progress=progress,
                        )
                    except CliError as exc:
                        write_codex_csv_fanout_metadata(
                            bundle,
                            status="fallback",
                            fallback_used=True,
                            failures=list(exc.details.get("failures") or []),
                            reason=exc.code,
                        )
                        result = run_deep_fallback_engine(
                            bundle=bundle,
                            instruction=instruction.text,
                            out_path=out_path,
                            model=args.model,
                            cwd=args.cd,
                            harness=harness,
                            resume=True,
                            progress=progress,
                        )
                elif harness == "opencode":
                    try:
                        result = run_opencode_server_engine(
                            bundle=bundle,
                            instruction=instruction.text,
                            out_path=out_path,
                            model=args.model,
                            cwd=args.cd,
                            progress=progress,
                        )
                    except CliError as exc:
                        write_opencode_server_metadata(
                            bundle,
                            status="fallback",
                            fallback_used=True,
                            reason=exc.code,
                            session=exc.details.get("session"),
                            missing=list(exc.details.get("missing") or []),
                            server=opencode_server_config(),
                        )
                        result = run_deep_fallback_engine(
                            bundle=bundle,
                            instruction=instruction.text,
                            out_path=out_path,
                            model=args.model,
                            cwd=args.cd,
                            harness=harness,
                            resume=True,
                            progress=progress,
                        )
                else:
                    result = run_deep_fallback_engine(
                        bundle=bundle,
                        instruction=instruction.text,
                        out_path=out_path,
                        model=args.model,
                        cwd=args.cd,
                        harness=harness,
                        resume=args.resume,
                        progress=progress,
                    )
            except CliError:
                if managed_run is not None:
                    managed_run["status"] = "incomplete"
                    update_run_record(managed_run)
                raise
        elif args.chunk_chars:
            chunks = split_transcript_chunks(transcript, args.chunk_chars, args.timestamps)
            result = run_chunked_agent_polish(
                chunks=chunks,
                instruction=instruction.text,
                out_path=out_path,
                model=args.model,
                cwd=args.cd,
                harness=harness,
                chunk_chars=args.chunk_chars,
                resume=args.resume,
                progress=progress,
            )
        else:
            result = run_agent_polish(
                transcript_text=transcript_text,
                instruction=instruction.text,
                out_path=out_path,
                model=args.model,
                cwd=args.cd,
                harness=harness,
                progress=progress,
            )
            result["chunking"] = chunking_disabled_payload()
        front_matter_data = None
        if args.front_matter:
            front_matter_data = run_front_matter_data(
                transcript,
                args.style,
                instruction,
                result["harness"],
            )
            result = apply_output_prefix(
                result,
                render_front_matter(front_matter_data),
            )
        if managed_run is not None:
            managed_run["status"] = "completed"
            managed_run["harness"] = result["harness"]
            managed_run = update_run_record(managed_run)
        payload = {
            "ok": True,
            "run": {
                "video_id": transcript["video_id"],
                "url": transcript["url"],
                **workflow,
                "language": transcript["language"],
                "requested_languages": transcript.get(
                    "requested_languages",
                    requested_languages(args),
                ),
                "segments": len(transcript["segments"]),
                "style": args.style,
                "instruction_mode": instruction.mode,
                "instruction_sources": instruction.sources,
                "timestamp_grounding": args.timestamps,
                "agent_harness": result["harness"],
                "transcript_path": transcript_path,
                "output_path": result["output_path"],
                "chars": result["chars"],
                "front_matter": args.front_matter,
                "front_matter_data": front_matter_data,
                "chunking": result["chunking"],
                "cache": cache,
                "bundle": None,
                "managed_run": managed_run,
                "next_commands": (
                    deep_next_commands(managed_run, bundle["dir"])
                    if deep_bundle and bundle
                    else []
                ),
            },
        }
        if bundle:
            existing_bundle_metadata = read_json_file(bundle["metadata"]) if deep_bundle else {}
            records = {
                "manifest": None,
                "verification": None,
            }
            if deep_bundle:
                records = {
                    "chunk_manifest": deep_bundle["chunk_manifest"],
                    "structural_verification": deep_bundle["structural_verification"],
                    "manifest": None,
                    "verification": None,
                }
            metadata_path = write_bundle_metadata(
                bundle["metadata"],
                {
                    "video_id": transcript["video_id"],
                    "url": transcript["url"],
                    "title": run_title,
                    **workflow,
                    "language": transcript["language"],
                    "requested_languages": transcript.get(
                        "requested_languages",
                        requested_languages(args),
                    ),
                    "track": transcript.get("track"),
                    "style": args.style,
                    "template": args.template,
                    "instruction_mode": instruction.mode,
                    "instruction_sources": instruction.sources,
                    "timestamp_grounding": args.timestamps,
                    "agent_harness": result["harness"],
                    "bundle_status": result.get(
                        "bundle_status",
                        "planned" if deep_bundle else "completed",
                    ),
                    "transcript_path": transcript_path,
                    "transcript_json_path": deep_bundle["transcript_json"] if deep_bundle else None,
                    "chunk_manifest_path": deep_bundle["chunk_manifest"] if deep_bundle else None,
                    "structural_verification_path": (
                        deep_bundle["structural_verification"] if deep_bundle else None
                    ),
                    "output_path": result["output_path"],
                    "front_matter_enabled": args.front_matter,
                    "front_matter_data": front_matter_data,
                    "chunking": result["chunking"],
                    "engine": existing_bundle_metadata.get("engine"),
                    "codex_csv_fanout": existing_bundle_metadata.get("codex_csv_fanout"),
                    "opencode_server": existing_bundle_metadata.get("opencode_server"),
                    "cache": cache,
                    "managed_run": managed_run,
                    "records": records,
                },
            )
            payload["run"]["bundle"] = {
                "dir": bundle["dir"],
                "transcript": transcript_path,
                "transcript_json": deep_bundle["transcript_json"] if deep_bundle else None,
                "chunks_dir": deep_bundle["chunks_dir"] if deep_bundle else None,
                "chunk_manifest": deep_bundle["chunk_manifest"] if deep_bundle else None,
                "structural_verification": (
                    deep_bundle["structural_verification"] if deep_bundle else None
                ),
                "polished": result["output_path"],
                "metadata": metadata_path,
                "records": records,
            }
        if args.json:
            emit(payload, True)
        elif args.stdout:
            emit(payload, False, result["text"])
        elif result["output_path"]:
            next_text = ""
            if payload["run"]["next_commands"]:
                next_text = "Next:\n" + "\n".join(
                    f"  {command}" for command in payload["run"]["next_commands"]
                ) + "\n"
            text = (
                f"Workflow: {workflow['workflow']} ({workflow['workflow_reason']})\n"
                f"Wrote polished transcript to {result['output_path']}\n"
                f"{next_text}"
            )
            emit(payload, False, text)
        else:
            emit(payload, False, result["text"])
        return 0

    if args.command == "batch":
        progress = ProgressReporter(not args.json)
        if not args.out_dir:
            raise CliError("batch requires --out-dir or a profile out_dir", "missing_out_dir")
        if not args.manifest:
            raise CliError("batch requires --manifest or a profile manifest", "missing_manifest")
        urls = read_batch_urls(args.file)
        out_dir = Path(args.out_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = Path(args.manifest).expanduser().resolve()
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        items: list[dict[str, Any]] = []
        languages = requested_languages(args)
        cache_dir = cache_dir_from_args(args)
        proxy_config = proxy_config_from_args(args)
        batch_inputs = expand_batch_items(urls, proxy_config)
        harness = selected_agent_harness(args)
        instruction = resolve_instruction(args, harness)

        for index, batch_input in enumerate(batch_inputs, start=1):
            url = batch_input["url"]
            source_metadata = {
                key: value
                for key, value in batch_input.items()
                if key in {"playlist_url", "playlist_id"}
            }
            progress.message(f"Batch item {index}/{len(batch_inputs)}: {url}")
            try:
                playlist_error = batch_input.get("playlist_error")
                if playlist_error:
                    raise CliError(
                        playlist_error.get("message") or "Playlist expansion failed",
                        playlist_error.get("code") or "playlist_error",
                        {
                            key: value
                            for key, value in playlist_error.items()
                            if key not in {"code", "message"}
                        },
                    )
                video_id = extract_video_id(url)
                output_path = batch_output_path(out_dir, video_id, args.style)
                if args.resume and output_path.is_file():
                    items.append(
                        {
                            "url": url,
                            **source_metadata,
                            "video_id": video_id,
                            "status": "skipped",
                            "output_path": str(output_path),
                            "reason": "output_exists",
                        }
                    )
                    continue

                transcript, cache = load_or_fetch_transcript(
                    url,
                    languages,
                    cache_dir,
                    args.resume,
                    proxy_config,
                )
                output_path = batch_output_path(out_dir, transcript["video_id"], args.style)
                if args.chunk_chars:
                    chunks = split_transcript_chunks(transcript, args.chunk_chars, args.timestamps)
                    result = run_chunked_agent_polish(
                        chunks=chunks,
                        instruction=instruction.text,
                        out_path=str(output_path),
                        model=args.model,
                        cwd=args.cd,
                        harness=harness,
                        chunk_chars=args.chunk_chars,
                        resume=args.resume,
                        progress=progress,
                    )
                else:
                    result = run_agent_polish(
                        transcript_text=limit_text(
                            render_timestamped_transcript(transcript)
                            if args.timestamps
                            else transcript["text"],
                            args.max_chars,
                        ),
                        instruction=instruction.text,
                        out_path=str(output_path),
                        model=args.model,
                        cwd=args.cd,
                        harness=harness,
                        progress=progress,
                    )
                    result["chunking"] = chunking_disabled_payload()
                if args.front_matter:
                    result = apply_output_prefix(
                        result,
                        run_front_matter(transcript, args.style, instruction, result["harness"]),
                    )
                items.append(
                    {
                        "url": transcript["url"],
                        **source_metadata,
                        "video_id": transcript["video_id"],
                        "status": "succeeded",
                        "language": transcript["language"],
                        "requested_languages": transcript.get("requested_languages", languages),
                        "agent_harness": result["harness"],
                        "output_path": result["output_path"],
                        "transcript_path": None,
                        "chars": result["chars"],
                        "timestamp_grounding": args.timestamps,
                        "chunking": result["chunking"],
                        "cache": cache,
                    }
                )
            except CliError as exc:
                items.append(
                    {
                        "url": url,
                        **source_metadata,
                        "status": "failed",
                        "error": {
                            "code": exc.code,
                            "message": str(exc),
                            **exc.details,
                        },
                    }
                )

        succeeded = sum(1 for item in items if item["status"] == "succeeded")
        failed = sum(1 for item in items if item["status"] == "failed")
        skipped = sum(1 for item in items if item["status"] == "skipped")
        manifest = {
            "ok": failed == 0,
            "source_path": str(Path(args.file).expanduser().resolve()),
            "out_dir": str(out_dir),
            "manifest_path": str(manifest_path),
            "style": args.style,
            "timestamp_grounding": args.timestamps,
            "chunking": {
                "enabled": bool(args.chunk_chars),
                "chunk_chars": args.chunk_chars,
            },
            "requested_languages": languages,
            "succeeded": succeeded,
            "failed": failed,
            "skipped": skipped,
            "items": items,
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        payload = {"ok": failed == 0, "batch": manifest}
        emit(payload, args.json, f"Wrote batch manifest to {manifest_path}\n")
        return 0 if failed == 0 else 1

    if args.command == "verify":
        polished_path = Path(args.file).expanduser().resolve()
        transcript_path = Path(args.transcript).expanduser().resolve()
        result = verify_polished_file(polished_path, transcript_path)
        ok = result["summary"]["unsupported"] == 0
        payload = {
            "ok": ok,
            "verify": {
                "polished_path": str(polished_path),
                "transcript_path": str(transcript_path),
                **result,
            },
        }
        emit(payload, args.json, render_verification(result))
        return 0 if ok else 1

    if args.command == "raw":
        video_id = extract_video_id(args.url)
        proxy_config = proxy_config_from_args(args)
        tracks = fetch_raw_caption_tracks(video_id, proxy_config)
        track = choose_track(tracks, args.lang)
        url = timedtext_url(track, args.format)
        if args.body:
            body = http_get(url, proxy_config).decode("utf-8", errors="replace")
            emit({"ok": True, "body": body}, args.json, body)
            return 0
        payload = {
            "ok": True,
            "raw": {
                "video_id": video_id,
                "selected_track": track.public_dict(),
                "url": url,
                "tracks": [track.public_dict() for track in tracks],
            },
        }
        emit(payload, args.json)
        return 0

    raise CliError("Unhandled command", "internal_error")


def add_polish_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--style",
        choices=sorted(STYLE_INSTRUCTIONS),
        default=None,
        help="polished output style, default: notes",
    )
    parser.add_argument(
        "--template",
        choices=sorted(TEMPLATE_INSTRUCTIONS),
        help="safe output structure to compose with the selected style",
    )
    parser.add_argument("--out", help="write polished output to this file")
    parser.add_argument(
        "--stdout",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="print polished output instead of writing a file",
    )
    parser.add_argument(
        "--focus",
        action="append",
        help=(
            "add custom instructions for this run; can be passed more than once "
            "and overrides --style where they conflict"
        ),
    )
    parser.add_argument(
        "--focus-file",
        action="append",
        help="read additional custom instructions from a file",
    )
    parser.add_argument(
        "--instruction",
        help="advanced: replace the entire built-in polishing prompt",
    )
    parser.add_argument(
        "--prompt-file",
        help="advanced: read the full replacement prompt from a file",
    )
    parser.add_argument(
        "--timestamps",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="ask the polisher to preserve useful timestamp anchors",
    )
    parser.add_argument(
        "--agent-harness",
        choices=AGENT_HARNESSES,
        help=(
            "agent harness for polishing; defaults to config "
            f"or {DEFAULT_AGENT_HARNESS}"
        ),
    )
    parser.add_argument("--model", help="optional agent model override")
    parser.add_argument("--profile", help="named workflow profile from config")
    parser.add_argument("--cd", help="working directory to pass to the agent harness")
    parser.add_argument(
        "--max-chars",
        type=int,
        default=0,
        help="truncate transcript before polish",
    )


def add_proxy_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--http-proxy",
        help=f"advanced: HTTP proxy URL for YouTube requests; env: {HTTP_PROXY_ENV_VAR}",
    )
    parser.add_argument(
        "--https-proxy",
        help=f"advanced: HTTPS proxy URL for YouTube requests; env: {HTTPS_PROXY_ENV_VAR}",
    )


def build_parser() -> argparse.ArgumentParser:
    epilog = """lifecycle:
  1. yt-scribe doctor
  2. yt-scribe inspect <youtube-url>
  3. yt-scribe fetch <youtube-url> --out transcript.txt
  4. yt-scribe polish transcript.txt --style notes --out notes.md
  5. yt-scribe run <youtube-url>

ai contract:
  Put --json before the command for stable machine-readable output.
  Use raw only as a read-only escape hatch when inspect or fetch is not enough.
"""
    parser = argparse.ArgumentParser(
        prog=COMMAND_NAME,
        description="Human-first CLI for turning YouTube links into agent-polished notes.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--json", action="store_true", help="emit stable JSON output")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="command")

    config_parser = subparsers.add_parser("config", help="show or edit yt-scribe config")
    config_subparsers = config_parser.add_subparsers(dest="config_command", metavar="command")
    config_set_parser = config_subparsers.add_parser("set", help="set a config value")
    config_set_parser.add_argument(
        "key",
        choices=["default-agent-harness"],
        help="config key to set",
    )
    config_set_parser.add_argument("value", choices=AGENT_HARNESSES)
    config_unset_parser = config_subparsers.add_parser("unset", help="clear a config value")
    config_unset_parser.add_argument(
        "key",
        choices=["default-agent-harness"],
        help="config key to clear",
    )
    config_profile_parser = config_subparsers.add_parser(
        "profile",
        help="create, inspect, or remove named workflow profiles",
    )
    profile_subparsers = config_profile_parser.add_subparsers(
        dest="profile_command",
        required=True,
        metavar="command",
    )
    profile_set_parser = profile_subparsers.add_parser("set", help="create or replace a profile")
    profile_set_parser.add_argument("name", help="profile name")
    profile_set_parser.add_argument("--style", choices=sorted(STYLE_INSTRUCTIONS))
    profile_set_parser.add_argument("--langs", help="ordered comma-separated caption languages")
    profile_set_parser.add_argument("--focus", action="append", help="profile focus instruction")
    profile_set_parser.add_argument("--template", choices=sorted(TEMPLATE_INSTRUCTIONS))
    profile_set_parser.add_argument(
        "--front-matter",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    profile_set_parser.add_argument(
        "--timestamps",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    profile_set_parser.add_argument("--cache-dir", help="advanced: transcript cache directory")
    profile_set_parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="advanced: reuse cached or existing outputs where supported",
    )
    profile_set_parser.add_argument("--transcript", help="run: also write transcript here")
    profile_set_parser.add_argument(
        "--transcript-format",
        choices=["text", "json", "srt"],
        help="run: raw transcript format",
    )
    profile_set_parser.add_argument("--out", help="polish/run: write polished output here")
    profile_set_parser.add_argument(
        "--stdout",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="polish/run: print polished output instead of writing a file",
    )
    profile_set_parser.add_argument("--bundle-dir", help="run: bundle output directory")
    profile_set_parser.add_argument("--out-dir", help="batch: directory for polished outputs")
    profile_set_parser.add_argument("--manifest", help="batch: manifest JSON path")
    profile_set_parser.add_argument("--chunk-chars", type=int)
    profile_set_parser.add_argument("--agent-harness", choices=AGENT_HARNESSES)
    profile_get_parser = profile_subparsers.add_parser("get", help="show a profile")
    profile_get_parser.add_argument("name", help="profile name")
    profile_remove_parser = profile_subparsers.add_parser("remove", help="remove a profile")
    profile_remove_parser.add_argument("name", help="profile name")

    subparsers.add_parser("doctor", help="check Python, agent harnesses, skills, and PATH setup")
    subparsers.add_parser(
        "setup",
        help="install support files and print the next command",
    )
    subparsers.add_parser(
        "install-skills",
        help="install global yt-scribe agent skills",
    )
    init_project_parser = subparsers.add_parser(
        "init-project",
        help="write local yt-scribe project guidance",
    )
    init_project_parser.add_argument(
        "--dir",
        default=".",
        help="project directory to initialize, default: current directory",
    )
    init_project_parser.add_argument(
        "--profile",
        help="optional starter profile name for .yt-scribe/config.json",
    )
    subparsers.add_parser("lifecycle", help="print the recommended workflow")

    runs_parser = subparsers.add_parser("runs", help="manage saved deep workflow runs")
    runs_subparsers = runs_parser.add_subparsers(
        dest="runs_command",
        required=True,
        metavar="command",
    )
    runs_subparsers.add_parser("list", help="list saved deep workflow runs")
    runs_open_parser = runs_subparsers.add_parser("open", help="print a run bundle path")
    runs_open_parser.add_argument("selector", help="run name, video ID, or unambiguous prefix")
    runs_rename_parser = runs_subparsers.add_parser("rename", help="rename a saved run")
    runs_rename_parser.add_argument("selector", help="run name, video ID, or unambiguous prefix")
    runs_rename_parser.add_argument("title", help="new display title or semantic name")

    ask_parser = subparsers.add_parser(
        "ask",
        help="ask questions against a completed deep run",
        description=(
            "Retrieve relevant outline, chunk-note, and transcript context from a "
            "completed deep run before optionally invoking an agent."
        ),
    )
    ask_parser.add_argument("selector", help="run name, video ID, or unambiguous prefix")
    ask_parser.add_argument("question", nargs="+", help="question to ask against the run")
    ask_parser.add_argument(
        "--show-context",
        action="store_true",
        help="print retrieved context without invoking an agent",
    )
    ask_parser.add_argument(
        "--agent",
        action="store_true",
        help="answer with the configured agent harness using only retrieved context",
    )
    ask_parser.add_argument("--agent-harness", choices=AGENT_HARNESSES)
    ask_parser.add_argument("--model", help="optional agent model override")
    ask_parser.add_argument("--cd", help="working directory to pass to the agent harness")
    ask_parser.add_argument("--top-k", type=int, default=5, help="number of context hits")

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="resolve a video and list caption tracks",
    )
    inspect_parser.add_argument("url", help="YouTube URL or 11-character video ID")
    inspect_parser.add_argument(
        "--brief",
        action="store_true",
        help="print the smallest useful caption availability summary",
    )
    add_proxy_flags(inspect_parser)

    fetch_parser = subparsers.add_parser("fetch", help="download the transcript without an agent")
    fetch_parser.add_argument("url", help="YouTube URL or 11-character video ID")
    fetch_parser.add_argument("--lang", default="en", help="caption language code, default: en")
    fetch_parser.add_argument(
        "--langs",
        help="ordered comma-separated caption language fallback list",
    )
    fetch_parser.add_argument("--cache-dir", help="advanced: transcript cache directory")
    fetch_parser.add_argument(
        "--resume",
        action="store_true",
        help="advanced: reuse a cached transcript when available",
    )
    fetch_parser.add_argument("--format", choices=["text", "json", "srt"], default="text")
    fetch_parser.add_argument("--out", help="write transcript to this file")
    add_proxy_flags(fetch_parser)

    polish_parser = subparsers.add_parser(
        "polish",
        help="polish an existing transcript with the configured agent",
    )
    polish_parser.add_argument("file", help="transcript file to polish")
    add_polish_flags(polish_parser)

    run_parser = subparsers.add_parser(
        "run",
        help="fetch and polish in one command",
        description=(
            "Fetch a YouTube transcript and write notes to "
            "yt-scribe-<video-id>-notes.md by default. Workflow auto-selection "
            "uses the deep bundle workflow for videos at least 45 minutes long."
        ),
    )
    run_parser.add_argument("url", help="YouTube URL or 11-character video ID")
    run_parser.add_argument("--lang", default="en", help="caption language code, default: en")
    run_parser.add_argument(
        "--langs",
        help="ordered comma-separated caption language fallback list",
    )
    run_parser.add_argument("--cache-dir", help="advanced: transcript cache directory")
    run_parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="advanced: reuse a cached transcript when available",
    )
    run_parser.add_argument("--transcript", help="also write the raw transcript to this file")
    run_parser.add_argument("--transcript-format", choices=["text", "json", "srt"], default=None)
    run_parser.add_argument(
        "--workflow",
        choices=RUN_WORKFLOWS,
        default="auto",
        help="auto selects deep at 45 minutes; quick and deep override selection",
    )
    run_parser.add_argument(
        "--bundle-dir",
        help="advanced: write transcript, polished output, and metadata under this directory",
    )
    run_parser.add_argument(
        "--front-matter",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="prepend factual YAML front matter to polished markdown output",
    )
    run_parser.add_argument(
        "--chunk-chars",
        type=int,
        default=None,
        help="advanced: polish transcript chunks of roughly this many characters",
    )
    add_proxy_flags(run_parser)
    add_polish_flags(run_parser)

    batch_parser = subparsers.add_parser(
        "batch",
        help="advanced: process a plain text list of YouTube URLs",
    )
    batch_parser.add_argument("file", help="plain text file with one YouTube URL or ID per line")
    batch_parser.add_argument("--out-dir", help="directory for polished outputs")
    batch_parser.add_argument("--manifest", help="write batch manifest JSON here")
    batch_parser.add_argument("--lang", default="en", help="caption language code, default: en")
    batch_parser.add_argument(
        "--langs",
        help="ordered comma-separated caption language fallback list",
    )
    batch_parser.add_argument("--cache-dir", help="advanced: transcript cache directory")
    batch_parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="advanced: skip existing outputs and reuse cached transcripts",
    )
    batch_parser.add_argument(
        "--front-matter",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="prepend factual YAML front matter to polished markdown output",
    )
    batch_parser.add_argument(
        "--chunk-chars",
        type=int,
        default=None,
        help="advanced: polish transcript chunks of roughly this many characters",
    )
    add_proxy_flags(batch_parser)
    add_polish_flags(batch_parser)

    verify_parser = subparsers.add_parser(
        "verify",
        help="compare polished output with a transcript artifact",
    )
    verify_parser.add_argument("file", help="polished output file to verify")
    verify_parser.add_argument(
        "--transcript",
        required=True,
        help="raw transcript text, timestamped text, or transcript JSON artifact",
    )

    raw_parser = subparsers.add_parser("raw", help="read-only timedtext escape hatch")
    raw_parser.add_argument("url", help="YouTube URL or 11-character video ID")
    raw_parser.add_argument("--lang", default="en")
    raw_parser.add_argument("--format", choices=["json3", "srv3"], default="json3")
    add_proxy_flags(raw_parser)
    raw_parser.add_argument(
        "--body",
        action="store_true",
        help="print the raw timedtext response body",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return handle_args(args)
    except CliError as exc:
        return emit_error(exc, getattr(args, "json", False))


__all__ = ["build_parser", "handle_args", "main"]
