# Codex Harness

Use this file when Codex is running the yt-scribe CLI or when the user wants the
Codex polishing harness.

`polish` and `run` use Codex by default:

```sh
yt-scribe --json run "<youtube-url>"
yt-scribe --json polish transcript.txt --style summary --out summary.md
```

With Codex, the CLI invokes `codex exec` in read-only, ephemeral mode and passes
the transcript through stdin. The polishing prompt asks for the
`yt-scribe-transcript-polisher` skill from `.agents/skills` and its Codex
instructions. The CLI streams Codex JSON events as human progress and writes
final Codex output through `--output-last-message`, so prefer `--out` when the
user expects a file.

For long videos, `yt-scribe run` defaults to `--workflow auto` and selects deep
mode at 45 minutes when duration metadata is available. Codex is the default
deep harness. Deep mode tries Codex CSV fan-out for one transcript chunk per
worker when that seam is available, then falls back to managed per-chunk Codex
calls. The output contract is the same either way: exact transcript JSON,
timestamped transcript text, chunk files, per-chunk notes, merged final notes,
metadata, and structural verification.

After a deep run, use `yt-scribe --json runs list`, `yt-scribe --json runs open
<run-name>`, and `yt-scribe --json ask <run-name> "<question>" --show-context`
to inspect reusable artifacts before asking an agent.
