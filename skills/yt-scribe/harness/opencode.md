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

With OpenCode, the CLI invokes `opencode run` and attaches the transcript as a temp file. The inner polishing prompt asks for the `yt-scribe-transcript-polisher` skill and its OpenCode harness instructions. Prefer `--out` when the user expects a file.
