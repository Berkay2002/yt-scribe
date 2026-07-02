<p align="center">
  <img src="assets/logo.svg" width="128" alt="yt-scribe logo">
</p>

# yt-scribe

Turn a YouTube link into a transcript, then ask an agent harness to polish it into readable notes, summaries, cleaned text, or article-style prose.

`yt-scribe` is human-first software that can also be driven by coding agents:

- You can run one obvious command and get a useful notes file.
- Agents can use the same CLI with stable JSON, explicit lifecycle steps, and composable commands.

Use it yourself from the terminal, or let Codex use it as an agent tool.

It uses public YouTube caption tracks when they are available through `youtube-transcript-api`. It does not download video or audio. Polishing is done locally through Codex by default, and can use OpenCode when selected.

## Install

From GitHub:

```powershell
pip install git+https://github.com/Berkay2002/yt-scribe.git
```

For local development or immediate use from a checkout:

```powershell
.\install-local.ps1
```

Check your setup:

```powershell
yt-scribe doctor
```

`yt-scribe` uses `youtube-transcript-api` for caption access. Maintainers can install test and lint tools with:

```powershell
pip install -e .[dev]
```

Run normal tests:

```powershell
python -m pytest
python -m ruff check .
```

Run the real YouTube and agent harness e2e test:

```powershell
$env:YT_SCRIBE_RUN_E2E = "1"
python -m pytest tests/test_e2e.py -q -s
```

The e2e test fetches a real transcript and runs both Codex and OpenCode when they are installed. It is opt-in because it uses network access and live agent calls.

## Quick Start

Fetch and polish a video in one command:

```powershell
yt-scribe run "https://www.youtube.com/watch?v=VIDEO_ID"
```

By default this creates `yt-scribe-VIDEO_ID-notes.md` in the current directory.

Keep the raw transcript too:

```powershell
yt-scribe run "https://www.youtube.com/watch?v=VIDEO_ID" --transcript transcript.txt
```

Download only the transcript:

```powershell
yt-scribe fetch "https://www.youtube.com/watch?v=VIDEO_ID" --out transcript.txt
```

Polish an existing transcript:

```powershell
yt-scribe polish transcript.txt --style summary --out summary.md
```

## Lifecycle

`yt-scribe` is easiest to understand as a small pipeline:

```powershell
yt-scribe doctor
yt-scribe inspect "<youtube-url>"
yt-scribe fetch "<youtube-url>" --out transcript.txt
yt-scribe polish transcript.txt --style notes --out notes.md
yt-scribe run "<youtube-url>"
```

Run this any time to print the same lifecycle from the CLI:

```powershell
yt-scribe lifecycle
```

## Commands

`doctor`

Checks Python, agent harness availability, PATH installation, config, and the expected lifecycle.

`inspect <url>`

Resolves the YouTube video and lists caption tracks.

`fetch <url>`

Downloads the transcript without calling Codex.

Useful options:

```powershell
yt-scribe fetch "<url>" --lang en --format text --out transcript.txt
yt-scribe fetch "<url>" --format srt --out captions.srt
yt-scribe fetch "<url>" --format json --out transcript.json
```

`polish <file>`

Uses the configured agent harness to polish an existing transcript. The built-in default is Codex.

```powershell
yt-scribe polish transcript.txt --style clean --out clean.txt
yt-scribe polish transcript.txt --style notes --out notes.md
yt-scribe polish transcript.txt --style summary --out summary.md
yt-scribe polish transcript.txt --style article --out article.md
yt-scribe polish transcript.txt --agent-harness opencode --out notes.md
```

`run <url>`

Fetches the transcript and polishes it in one command.

```powershell
yt-scribe run "<url>"
yt-scribe run "<url>" --style summary
yt-scribe run "<url>" --stdout
yt-scribe run "<url>" --transcript transcript.txt --out notes.md
yt-scribe run "<url>" --agent-harness opencode
```

`config`

Shows or edits the persisted yt-scribe config.

```powershell
yt-scribe config
yt-scribe config set default-agent-harness opencode
yt-scribe config unset default-agent-harness
```

Without config, `yt-scribe` uses Codex. A config default changes future `polish` and `run` commands unless a command passes `--agent-harness` explicitly.

`raw <url>`

Read-only diagnostic escape hatch for inspecting the selected YouTube timedtext caption URL. Most users do not need this because normal transcript fetching uses `youtube-transcript-api`.

```powershell
yt-scribe raw "<url>" --lang en
```

## AI-Friendly JSON

Put `--json` before the command:

```powershell
yt-scribe --json doctor
yt-scribe --json inspect "<url>"
yt-scribe --json fetch "<url>" --out transcript.txt
yt-scribe --json run "<url>"
```

Successful commands return:

```json
{
  "ok": true,
  "fetch": {
    "video_id": "VIDEO_ID",
    "output_path": "C:\\path\\transcript.txt"
  }
}
```

Errors return:

```json
{
  "ok": false,
  "error": {
    "code": "no_captions",
    "message": "No caption tracks were found for this video"
  }
}
```

## Agent Harnesses

Codex is the built-in default. `polish` and `run` call:

```text
codex exec --ephemeral --skip-git-repo-check --sandbox read-only --output-last-message <temp-file> "<instruction>"
```

The transcript is passed through stdin. Codex progress stays separate from the final output, and the final message is read from the file written by `--output-last-message`.

OpenCode is available when `opencode` is on PATH and selected with `--agent-harness opencode` or config. `yt-scribe` calls:

```text
opencode run "<instruction>" --file <temp-transcript-file> --format json
```

The final text is read from OpenCode JSON events. Run `yt-scribe doctor` to check whether `codex` and `opencode` are available and whether their auth commands report usable local configuration.

If the optional plugin skills are installed, the inner agent uses `yt-scribe-transcript-polisher` for the transcript rewrite. The skill has harness-specific notes so Codex and OpenCode do not need to read each other's command details.

## Plugin Skills

This repository also contains plugin skills:

- `skills/yt-scribe`: for an outer agent that wants to use the CLI.
- `skills/yt-scribe/harness/codex.md`: Codex-specific outer CLI instructions.
- `skills/yt-scribe/harness/opencode.md`: OpenCode-specific outer CLI instructions.
- `skills/yt-scribe-transcript-polisher`: for the inner agent that receives transcript text and rewrites it.
- `skills/yt-scribe-transcript-polisher/harness/codex.md`: Codex stdin polishing instructions.
- `skills/yt-scribe-transcript-polisher/harness/opencode.md`: OpenCode attached-file polishing instructions.

The split matters. The outer agent fetches, saves, and chains commands. The inner agent only transforms transcript text and should not fetch videos or run shell commands.

The local personal plugin scaffold is created at:

```text
C:\Users\berka\plugins\yt-scribe
```

## Notes

- A video must have captions available.
- `youtube-transcript-api` uses an undocumented YouTube web-client API, so YouTube can change or block behavior.
- If YouTube blocks the IP running the command, the underlying library may raise request-blocking errors. The upstream project documents proxy support for those cases.
- `yt-scribe` does not bypass private, unavailable, or disabled captions.
- Long transcripts can be truncated deliberately with `--max-chars`.
