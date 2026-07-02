# OpenCode Harness

Use this file when OpenCode is running the yt-scribe CLI or when the user wants
the OpenCode polishing harness.

Select OpenCode per command:

```sh
yt-scribe --json run "<youtube-url>" --agent-harness opencode
yt-scribe --json polish transcript.txt --agent-harness opencode --style summary --out summary.md
```

Or persist it as the default:

```sh
yt-scribe config set default-agent-harness opencode
```

With OpenCode, the CLI invokes `opencode run` and attaches the transcript as a
temp file. The polishing prompt asks for the shared
`yt-scribe-transcript-polisher` skill from `.agents/skills` and its OpenCode
instructions. Prefer `--out` when the user expects a file.
