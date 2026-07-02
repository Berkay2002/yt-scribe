---
name: yt-scribe
description: Use when an agent needs to fetch a YouTube transcript, inspect available captions, save raw captions, or polish a transcript into notes, summaries, cleaned text, or article-style prose through the installed `yt-scribe` CLI.
---

# yt-scribe

Use the installed `yt-scribe` CLI for YouTube transcript workflows. This skill teaches an agent how to use the CLI correctly.

Prefer `--json` when reading command output for analysis or chaining.

Read exactly one harness file for command details:

- Codex: `harness/codex.md`
- OpenCode: `harness/opencode.md`

The CLI is human-first. Its default path should be the same obvious command a person would run:

```sh
yt-scribe run "<youtube-url>"
```

## Start

Verify the command exists and the local harness setup is available:

```sh
yt-scribe --json doctor
```

If `yt-scribe` is missing, install it from the public repository. From a checkout, run `sh ./install-local.sh` on Linux or macOS, or `.\install-local.ps1` on Windows, if you want the wrapper on PATH.

## Workflow

For a new YouTube link:

```sh
yt-scribe --json inspect "<youtube-url>"
yt-scribe --json fetch "<youtube-url>" --lang en --out transcript.txt
yt-scribe --json polish transcript.txt --style notes --out notes.md
```

For the one-command path:

```sh
yt-scribe --json run "<youtube-url>"
```

Use styles intentionally:

- `notes`: structured markdown notes.
- `summary`: concise summary with key ideas.
- `clean`: cleaned transcript text with filler removed.
- `article`: readable article-style prose.

## Safety

- Use `inspect` before assuming captions exist.
- Do not claim transcript availability until `fetch`, `inspect`, or `run` succeeds.
- Do not bypass private, disabled, or unavailable captions.
- Do not use `raw --body` unless high-level commands are insufficient.
- Do not run destructive shell commands as part of this workflow.
- Do not pass secrets in `--instruction` or prompt files.
