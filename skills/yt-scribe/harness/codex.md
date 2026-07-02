# Codex Harness

Use this file when the outer agent is Codex or when the user wants the Codex polishing harness.

`polish` and `run` use Codex by default:

```powershell
yt-scribe --json run "<youtube-url>"
yt-scribe --json polish transcript.txt --style summary --out summary.md
```

With Codex, the CLI invokes `codex exec` in read-only, ephemeral mode and passes the transcript through stdin. The inner polishing prompt asks for the `yt-scribe-transcript-polisher` skill and its Codex harness instructions. The CLI writes final Codex output through `--output-last-message`, so prefer `--out` when the user expects a file.
