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
instructions. The CLI streams OpenCode JSON events as human progress and reads
the final output from text events. It uses OpenCode's `--thinking` flag for
reasoning event summaries. Prefer `--out` when the user expects a file.

For long videos, `yt-scribe run` defaults to `--workflow auto` and selects deep
mode at 45 minutes when duration metadata is available. Select OpenCode with
`--agent-harness opencode` or config. OpenCode deep mode first tries
local server/session orchestration using local-only defaults, then falls back to
managed per-chunk OpenCode calls if server orchestration is unavailable or does
not produce the expected bundle artifacts. It does not write project-local
`.opencode/` config for a one-off deep run.

After a deep run, use `yt-scribe --json runs list`, `yt-scribe --json runs open
<run-name>`, and `yt-scribe --json ask <run-name> "<question>" --show-context`
to inspect reusable artifacts before asking an agent.
