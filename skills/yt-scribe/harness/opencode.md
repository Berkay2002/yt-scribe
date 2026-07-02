# OpenCode Harness

Use this file when the outer agent is OpenCode or when the user wants the OpenCode polishing harness.

Select OpenCode per command:

```powershell
yt-scribe --json run "<youtube-url>" --agent-harness opencode
yt-scribe --json polish transcript.txt --agent-harness opencode --style summary --out summary.md
```

Or persist it as the default:

```powershell
yt-scribe config set default-agent-harness opencode
```

With OpenCode, the CLI invokes `opencode run` and attaches the transcript as a temp file. When `.opencode/agents/yt-scribe-transcript-polisher.md` is discoverable, the CLI passes `--agent yt-scribe-transcript-polisher`. The inner polishing skill lives in `.agents/skills/yt-scribe-transcript-polisher`. Prefer `--out` when the user expects a file.
