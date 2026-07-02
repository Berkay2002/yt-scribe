---
name: yt-scribe
description: Use when an agent needs to fetch a YouTube transcript, inspect
  available captions, save raw captions, or polish a transcript into notes,
  summaries, cleaned text, or article-style prose through the installed
  `yt-scribe` CLI.
---

# yt-scribe

Use the installed `yt-scribe` CLI for YouTube transcript workflows. This skill
teaches an agent how to use the CLI correctly.

Prefer `--json` when reading command output for analysis or chaining.

If the current host exposes the `yt-scribe` MCP server, prefer MCP tools for
structured agent workflows:

- `inspect_youtube_captions` before assuming captions exist.
- `fetch_youtube_transcript` for read-only transcript access.
- `agent_polish_transcript` or `agent_fetch_and_polish_youtube` only when the
  user wants agent-backed polishing and accepts that Codex or OpenCode may run.

Use the CLI when MCP is not installed, when the user asks for a terminal command,
or when file-oriented workflows such as `--out`, `--bundle-dir`, `batch`, or
`verify` are the better fit.

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

If `yt-scribe` is missing, install and set it up from the public repository:

```sh
python -m pip install --upgrade git+https://github.com/Berkay2002/yt-scribe.git \
  && python -m yt_scribe setup
```

From a checkout, run `sh ./install-local.sh` on Linux or macOS, or
`.\install-local.ps1` on Windows. The local installers create the wrapper and
run setup.

## Workflow

For a new YouTube link:

```sh
yt-scribe --json inspect "<youtube-url>"
yt-scribe --json inspect "<youtube-url>" --brief
yt-scribe --json fetch "<youtube-url>" --lang en --out transcript.txt
yt-scribe --json polish transcript.txt --style notes --out notes.md
yt-scribe --json polish transcript.txt --focus "Focus on decisions and risks" --out notes.md
```

For the one-command path:

```sh
yt-scribe --json run "<youtube-url>"
yt-scribe --json run "<youtube-url>" --workflow quick
yt-scribe --json run "<youtube-url>" --workflow deep
yt-scribe --json run "<youtube-url>" --focus "Keep only action items"
yt-scribe --json run "<youtube-url>" --timestamps
yt-scribe --json run "<youtube-url>" --bundle-dir .yt-scribe/runs/VIDEO_ID
```

`run` defaults to `--workflow auto`. Auto mode uses duration metadata when it is
available and selects the deep workflow for videos at least 45 minutes long.
Shorter videos keep quick behavior. If duration is missing, caption availability
is still checked separately and auto mode stays quick.

Use deep mode when a long video needs durable artifacts or follow-up questions.
Deep mode preserves exact transcript JSON, timestamped transcript text, chunk
files, per-chunk notes, merged final notes, metadata, and structural checks.
Default managed deep runs are stored outside the current project. Use
`--bundle-dir` only when the user wants artifacts in a specific directory.

Useful follow-up commands:

```sh
yt-scribe --json runs list
yt-scribe --json runs open <run-name>
yt-scribe --json runs rename <run-name> "Project vocabulary"
yt-scribe --json run "<youtube-url>" --workflow deep --bundle-dir "<bundle-dir>" --resume
yt-scribe --json ask <run-name> "What did they say about retrieval?" --show-context
yt-scribe --json ask <run-name> "What did they say about retrieval?" --agent
```

Use `ask --show-context` before `ask --agent` when the user wants to inspect
retrieved source snippets before spending agent tokens. Agent-backed `ask`
passes only retrieved outline, chunk-note, and transcript context to the harness.

Use styles intentionally:

- `notes`: structured markdown notes.
- `summary`: concise summary with key ideas.
- `clean`: cleaned transcript text with filler removed.
- `article`: readable article-style prose.

Use `--focus "..."` or `--focus-file instructions.md` when the user wants
specific emphasis while keeping the normal harness prompt. Use `--instruction`
or `--prompt-file` only when the user needs to replace the whole polishing prompt.

Use `--timestamps` when the user needs polished output with source anchors. For
`run`, yt-scribe passes transcript segment start times to the polisher. For
`polish`, the input transcript should already contain useful timestamp anchors.

Use `verify` when the user needs a conservative transcript-backed check:

```sh
yt-scribe --json verify notes.md --transcript transcript.json
```

Use profiles and templates for repeated local conventions:

```sh
yt-scribe config profile set research --style notes --template research --langs en,en-US
yt-scribe --json run "<youtube-url>" --profile research
```

Use `--chunk-chars` only for long transcripts that need chunk-and-merge polishing.
Use `batch` for URL lists; playlist URLs in a batch file expand into normal batch
items.

## Safety

- Use `inspect` before assuming captions exist.
- Do not claim transcript availability until `fetch`, `inspect`, or `run` succeeds.
- Do not bypass private, disabled, or unavailable captions.
- Do not use `raw --body` unless high-level commands are insufficient.
- Do not run destructive shell commands as part of this workflow.
- Do not pass secrets in `--focus`, `--instruction`, or prompt files.
