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

Install from GitHub and set up the agent support files:

```sh
python -m pip install --upgrade git+https://github.com/Berkay2002/yt-scribe.git && python -m yt_scribe setup
```

On Windows PowerShell:

```powershell
py -m pip install --upgrade git+https://github.com/Berkay2002/yt-scribe.git; py -m yt_scribe setup
```

Then use it:

```sh
yt-scribe run "https://www.youtube.com/watch?v=VIDEO_ID"
```

Or let an agent install it for you. Open Codex or OpenCode and paste this:

```text
Install yt-scribe on this machine: run python -m pip install --upgrade git+https://github.com/Berkay2002/yt-scribe.git && python -m yt_scribe setup, then run python -m yt_scribe --json doctor and tell me whether the yt-scribe command is on PATH and which agent harnesses are available. If yt-scribe is not on PATH, tell me the python -m yt_scribe fallback command to use. Then ask whether I want to add yt-scribe guidance to the current project's AGENTS.md so teammates and future agents use it consistently. If I say yes, add a yt-scribe section that says to use yt-scribe for YouTube transcript workflows, prefer yt-scribe run "<youtube-url>" for one-command transcript-to-notes work, use yt-scribe --json inspect "<youtube-url>" before assuming captions exist, do not bypass private or disabled captions, and do not add facts that are not in the transcript.
```

For local development or immediate use from a checkout, run the local installer. It installs the package in editable mode, creates the wrapper, and runs `yt-scribe setup` for you.

```sh
sh ./install-local.sh
```

On Windows PowerShell:

```powershell
.\install-local.ps1
```

If the `yt-scribe` command is not on PATH after a `pip` install, use the module form:

```sh
python -m yt_scribe run "https://www.youtube.com/watch?v=VIDEO_ID"
```

You can re-run setup or inspect the environment later:

```sh
yt-scribe setup
yt-scribe doctor
```

`yt-scribe setup` copies the transcript-polisher skill to `~/.agents/skills/yt-scribe-transcript-polisher` and the OpenCode agents to OpenCode's global agents directory. This is needed when the globally installed CLI is used from projects that do not contain this repository's `.agents` or `.opencode` folders.

`yt-scribe` uses `youtube-transcript-api` for caption access. Maintainers can install test and lint tools with:

```sh
pip install -e ".[dev]"
```

Run normal tests:

```sh
python -m pytest
python -m ruff check .
```

Run the real YouTube and agent harness e2e test:

```sh
YT_SCRIBE_RUN_E2E=1 python -m pytest tests/test_e2e.py -q -s
```

On Windows PowerShell:

```powershell
$env:YT_SCRIBE_RUN_E2E = "1"; python -m pytest tests/test_e2e.py -q -s
```

The e2e test fetches a real transcript and runs both Codex and OpenCode when they are installed. It is opt-in because it uses network access and live agent calls.

## Quick Start

Fetch and polish a video in one command:

```sh
yt-scribe run "https://www.youtube.com/watch?v=VIDEO_ID"
```

By default this creates `yt-scribe-VIDEO_ID-notes.md` in the current directory.

Keep the raw transcript too:

```sh
yt-scribe run "https://www.youtube.com/watch?v=VIDEO_ID" --transcript transcript.txt
```

Download only the transcript:

```sh
yt-scribe fetch "https://www.youtube.com/watch?v=VIDEO_ID" --out transcript.txt
```

Polish an existing transcript:

```sh
yt-scribe polish transcript.txt --style summary --out summary.md
```

Tell the polisher what to focus on:

```sh
yt-scribe run "https://www.youtube.com/watch?v=VIDEO_ID" --focus "Keep only decisions, risks, and action items"
```

## Lifecycle

`yt-scribe` is easiest to understand as a small pipeline:

```sh
yt-scribe doctor
yt-scribe inspect "<youtube-url>"
yt-scribe fetch "<youtube-url>" --out transcript.txt
yt-scribe polish transcript.txt --style notes --out notes.md
yt-scribe run "<youtube-url>"
```

Run this any time to print the same lifecycle from the CLI:

```sh
yt-scribe lifecycle
```

## Commands

`doctor`

Checks Python, agent harness availability, PATH installation, config, and the expected lifecycle.

`setup`

Installs the global agent support files and prints the next command to run.

```sh
yt-scribe setup
yt-scribe --json setup
```

`inspect <url>`

Resolves the YouTube video and lists caption tracks.

`fetch <url>`

Downloads the transcript without calling Codex.

Useful options:

```sh
yt-scribe fetch "<url>" --lang en --format text --out transcript.txt
yt-scribe fetch "<url>" --format srt --out captions.srt
yt-scribe fetch "<url>" --format json --out transcript.json
```

`polish <file>`

Uses the configured agent harness to polish an existing transcript. The built-in default is Codex.

```sh
yt-scribe polish transcript.txt --style clean --out clean.txt
yt-scribe polish transcript.txt --style notes --out notes.md
yt-scribe polish transcript.txt --style summary --out summary.md
yt-scribe polish transcript.txt --style article --out article.md
yt-scribe polish transcript.txt --focus "Focus on concrete takeaways" --out notes.md
yt-scribe polish transcript.txt --focus-file instructions.md --out notes.md
yt-scribe polish transcript.txt --agent-harness opencode --out notes.md
```

Use `--focus` or `--focus-file` for custom instructions that should keep the
normal transcript-polisher prompt. Custom focus instructions override `--style`
where they conflict, but they do not allow the agent to add facts that are not in
the transcript. `--instruction` and `--prompt-file` are advanced options that
replace the whole polishing prompt.

`run <url>`

Fetches the transcript and polishes it in one command.

```sh
yt-scribe run "<url>"
yt-scribe run "<url>" --style summary
yt-scribe run "<url>" --focus "Extract only action items and owner names"
yt-scribe run "<url>" --stdout
yt-scribe run "<url>" --transcript transcript.txt --out notes.md
yt-scribe run "<url>" --agent-harness opencode
```

For normal human runs, fetch and polish progress is written to stderr. The final
path or polished text stays on stdout. `--json` suppresses progress and keeps
stdout machine-readable.

`config`

Shows or edits the persisted yt-scribe config.

```sh
yt-scribe config
yt-scribe config set default-agent-harness opencode
yt-scribe config unset default-agent-harness
```

Without config, `yt-scribe` uses Codex. A config default changes future `polish` and `run` commands unless a command passes `--agent-harness` explicitly.

`install-skills`

Installs only the transcript-polisher skill and OpenCode agents into global user-level locations. Most users should run `yt-scribe setup` instead.

```sh
yt-scribe install-skills
yt-scribe --json install-skills
```

`raw <url>`

Read-only diagnostic escape hatch for inspecting the selected YouTube timedtext caption URL. Most users do not need this because normal transcript fetching uses `youtube-transcript-api`.

```sh
yt-scribe raw "<url>" --lang en
```

## AI-Friendly JSON

Put `--json` before the command:

```sh
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
    "output_path": "/path/to/transcript.txt"
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

When the project-local OpenCode agent is available, `yt-scribe` also passes:

```text
--agent yt-scribe-transcript-polisher
```

The final text is read from OpenCode JSON events. Run `yt-scribe doctor` to check whether `codex` and `opencode` are available and whether their auth commands report usable local configuration.

For the default prompt, `--style` selects the output mode and `--focus` appends
run-specific instructions. JSON output includes `instruction_mode` and
`instruction_sources` so agents can tell whether the run used only the selected
style, added custom focus instructions, or replaced the prompt.

There are two skills:

- `yt-scribe`: teaches an agent how to use the CLI correctly.
- `yt-scribe-transcript-polisher`: guides the agent started by the CLI to polish the fetched transcript.

Each skill keeps Codex and OpenCode notes in separate files, so each tool sees only the details it needs.

## Plugin Skills

This repository also contains plugin skills:

- `.opencode/agents/yt-scribe.md`: OpenCode agent for using the CLI.
- `.opencode/agents/yt-scribe-transcript-polisher.md`: OpenCode agent for polishing transcripts.
- `skills/yt-scribe`: plugin skill for using the CLI.
- `skills/yt-scribe/harness/codex.md`: Codex-specific CLI instructions.
- `skills/yt-scribe/harness/opencode.md`: OpenCode-specific CLI instructions.
- `.agents/skills/yt-scribe-transcript-polisher`: skill for transcript rewriting.
- `.agents/skills/yt-scribe-transcript-polisher/harness/codex.md`: Codex stdin polishing instructions.
- `.agents/skills/yt-scribe-transcript-polisher/harness/opencode.md`: OpenCode attached-file polishing instructions.

The split matters. One skill explains how to run the CLI correctly. The transcript-polisher skill is only for the agent started by the CLI after the transcript has already been fetched.

When the CLI is installed globally, project-local `.agents` and `.opencode` folders are not automatically available in other projects. Run `yt-scribe setup` to install the skill files globally.

The plugin files live in this repository under `.codex-plugin/` and `skills/`.

## Notes

- A video must have captions available.
- `youtube-transcript-api` uses an undocumented YouTube web-client API, so YouTube can change or block behavior.
- If YouTube blocks the IP running the command, the underlying library may raise request-blocking errors. The upstream project documents proxy support for those cases.
- `yt-scribe` does not bypass private, unavailable, or disabled captions.
- Long transcripts can be truncated deliberately with `--max-chars`.
