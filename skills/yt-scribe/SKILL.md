---
name: yt-scribe
description: Use when Codex needs to fetch a YouTube transcript, inspect available captions, save raw captions, or polish a transcript into notes, summaries, cleaned text, or article-style prose through the installed `yt-scribe` CLI and its configured agent harness.
---

# yt-scribe

Use the installed `yt-scribe` CLI for YouTube transcript workflows. This skill is for the outer Codex agent that wants to run the CLI. Codex is the default polishing harness; OpenCode can be selected when installed.

Prefer `--json` when reading command output for analysis or chaining.

The CLI is human-first. Its default path should be the same obvious command a person would run:

```powershell
yt-scribe run "<youtube-url>"
```

## Start

Verify the command exists and the local agent harness setup is available:

```powershell
yt-scribe --json doctor
```

If `yt-scribe` is missing, check whether the repo exists at `C:\Users\berka\code\clis\yt-scribe` and run its `install-local.ps1`.

## Workflow

For a new YouTube link:

```powershell
yt-scribe --json inspect "<youtube-url>"
yt-scribe --json fetch "<youtube-url>" --lang en --out transcript.txt
yt-scribe --json polish transcript.txt --style notes --out notes.md
```

For the one-command path:

```powershell
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

## Codex Behavior

`polish` and `run` use Codex by default. With Codex, they invoke `codex exec` in read-only, ephemeral mode and pass the transcript through stdin. The CLI prompts the inner agent to use `yt-scribe-transcript-polisher` when that skill is installed. The CLI writes final Codex output through `--output-last-message`, so prefer `--out` when the user expects a file.

OpenCode can be selected per command:

```powershell
yt-scribe --json polish transcript.txt --agent-harness opencode --out notes.md
```

Or persisted as the default:

```powershell
yt-scribe config set default-agent-harness opencode
```

## Examples

```powershell
yt-scribe --json inspect "https://www.youtube.com/watch?v=VIDEO_ID"
```

```powershell
yt-scribe --json run "https://www.youtube.com/watch?v=VIDEO_ID"
```

```powershell
yt-scribe --json polish transcript.txt --style summary --out summary.md
```
