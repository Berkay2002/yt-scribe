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

## Contents

- [Install](#install)
- [Quick Start](#quick-start)
- [Lifecycle](#lifecycle)
- [Commands](#commands)
- [AI-Friendly JSON](#ai-friendly-json)
- [MCP Server](#mcp-server)
- [Agent Harnesses](#agent-harnesses)
- [Plugin Skills](#plugin-skills)
- [Notes](#notes)
- [Advanced usage](#advanced-usage)

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

`yt-scribe setup` copies the shared skills to `~/.agents/skills`. This is needed when the globally installed CLI is used from projects that do not contain this repository's `.agents` folder.

`yt-scribe` uses `youtube-transcript-api` for caption access.

Install MCP support when you want an MCP client to call yt-scribe as native
tools:

```sh
python -m pip install --upgrade "yt-scribe[mcp] @ git+https://github.com/Berkay2002/yt-scribe.git"
```

From a checkout:

```sh
python -m pip install -e ".[dev,mcp]"
```

Maintainers can install test and lint tools with:

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

Use `--brief` when an agent or script only needs caption availability and
language codes:

```sh
yt-scribe inspect "<url>" --brief
```

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
yt-scribe polish transcript.txt --template lecture --out lecture-notes.md
yt-scribe polish transcript.txt --timestamps --out anchored-notes.md
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
yt-scribe run "<url>" --timestamps
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
yt-scribe config profile set research --style notes --template research --langs en,en-US
yt-scribe config profile get research
yt-scribe config profile remove research
```

Without config, `yt-scribe` uses Codex. A config default changes future `polish` and `run` commands unless a command passes `--agent-harness` explicitly.
Named profiles can also provide defaults for repeated workflows. `run`, `polish`,
and `batch` accept `--profile <name>`, and command-line flags override profile
values, including `--no-timestamps`, `--no-front-matter`, and
`--chunk-chars 0`. When a project has `.yt-scribe/config.json`, that config is
read as a local overlay unless `YT_SCRIBE_CONFIG` points somewhere explicit.

`install-skills`

Installs only the shared agent skills into `~/.agents/skills`. Most users should run `yt-scribe setup` instead.

```sh
yt-scribe install-skills
yt-scribe --json install-skills
```

`init-project`

Writes repo-local `yt-scribe` guidance under `.yt-scribe/` without editing root
project docs.

```sh
yt-scribe init-project
yt-scribe init-project --profile research
```

`verify <file>`

Compares polished output with a transcript artifact and reports conservative
supported, unsupported, and uncertain findings. It accepts raw text, timestamped
text, transcript JSON, or segment JSON.

```sh
yt-scribe verify notes.md --transcript transcript.json
yt-scribe --json verify notes.md --transcript transcript.txt
```

`raw <url>`

Read-only diagnostic escape hatch for inspecting the selected YouTube timedtext caption URL. Most users do not need this because normal transcript fetching uses `youtube-transcript-api`.

```sh
yt-scribe raw "<url>" --lang en
```

## Advanced Usage

These are opt-in workflow features for larger note-taking runs, repeated ingestion,
and agent automation. The normal `yt-scribe run "<url>"` path does not require them.

- `--langs`: ordered caption language fallback.
- `--front-matter`: factual metadata at the top of polished markdown.
- `--timestamps`: source anchors in polished output.
- `--template` and `--profile`: repeatable output structure and workflow defaults.
- `--cache-dir` and `--resume`: explicit transcript cache reuse.
- `--chunk-chars`: chunk-and-merge polishing for long transcripts.
- `--bundle-dir`: write transcript, polished output, and metadata together.
- `verify`: check polished output against a transcript artifact.
- `batch`: process URL lists or playlist URLs and write a manifest.

<details>
<summary>Examples for power users and agent workflows</summary>

Use ordered language fallback when caption languages vary across videos:

```sh
yt-scribe fetch "<youtube-url>" --langs en,en-US,en-GB,sv --out transcript.txt
yt-scribe run "<youtube-url>" --langs en,en-US,en-GB,sv
```

When no requested language matches, the error includes available language codes
and a concrete `--langs ...` retry suggestion.

Add factual front matter to polished markdown when the output will be indexed,
cited, or processed by another tool:

```sh
yt-scribe run "<youtube-url>" --front-matter --out notes.md
```

Use an explicit transcript cache when rerunning the same videos or recovering from
interrupted polish work:

```sh
yt-scribe fetch "<youtube-url>" --cache-dir .yt-scribe/cache --out transcript.txt
yt-scribe run "<youtube-url>" --resume --cache-dir .yt-scribe/cache --out notes.md
```

Process a plain text URL list and write a manifest for partial success tracking:

```sh
yt-scribe batch videos.txt --out-dir notes --manifest notes/manifest.json --resume
```

Batch input is one YouTube URL or video ID per line. Blank lines and lines starting
with `#` are ignored. Playlist URLs are expanded into ordinary batch items. The
manifest records succeeded, failed, and skipped items.

Use chunking when a transcript is too long for a dependable single polish pass:

```sh
yt-scribe run "<youtube-url>" --chunk-chars 60000 --out notes.md
yt-scribe batch videos.txt --out-dir notes --manifest notes/manifest.json --chunk-chars 60000
```

Keep a run's artifacts together:

```sh
yt-scribe run "<youtube-url>" --bundle-dir .yt-scribe/runs/VIDEO_ID
```

The bundle metadata records the transcript, polished output, chunking, front
matter data when enabled, and manifest or verification record slots when present.

</details>

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

## MCP Server

`yt-scribe-mcp` exposes yt-scribe to local MCP clients. The server is intended
for trusted local agents. It can fetch public caption tracks and, unless started
in read-only mode, expose tools that may spawn Codex or OpenCode for polishing.

STDIO is the default transport:

```sh
yt-scribe-mcp
```

Read-only STDIO hides the agent-backed polishing tools:

```sh
yt-scribe-mcp --read-only
```

The same read-only mode can be set with an environment variable:

```sh
YT_SCRIBE_MCP_READ_ONLY=1 yt-scribe-mcp
```

Local HTTP is explicit and binds to localhost by default:

```sh
yt-scribe-mcp --http
```

The endpoint is:

```text
http://127.0.0.1:3000/mcp
```

Use `--host` and `--port` only with `--http`:

```sh
yt-scribe-mcp --http --host 127.0.0.1 --port 3000
```

Binding HTTP to anything other than localhost prints a warning. The server has
no built-in authentication, so do not expose it to a network unless you have
added your own boundary and understand the risk.

The read-only tools are:

- `yt_scribe_info`: server metadata.
- `inspect_youtube_captions`: caption availability for a YouTube link or video ID.
- `fetch_youtube_transcript`: transcript fetching with ordered language fallback.

The agent-backed tools are clearly named:

- `agent_polish_transcript`: polish provided transcript text with Codex or OpenCode.
- `agent_fetch_and_polish_youtube`: fetch a transcript and polish it in one call.

All MCP tools return structured payloads with `ok: true` on success. Errors use
the same code vocabulary as the CLI where practical, such as
`invalid_youtube_url` and `no_captions`.

<details>
<summary>Generic STDIO MCP client config</summary>

Example config:

```json
{
  "mcpServers": {
    "yt-scribe": {
      "command": "yt-scribe-mcp",
      "args": []
    }
  }
}
```

</details>

<details>
<summary>Codex MCP config</summary>

Codex CLI and the Codex IDE extension share MCP config through
`~/.codex/config.toml`, or a trusted project-local `.codex/config.toml`.
Add yt-scribe over STDIO with the CLI:

```sh
codex mcp add yt-scribe -- yt-scribe-mcp
```

Or edit `config.toml`:

```toml
[mcp_servers.yt-scribe]
command = "yt-scribe-mcp"
startup_timeout_sec = 20
tool_timeout_sec = 300
```

For read-only Codex use:

```toml
[mcp_servers.yt-scribe]
command = "yt-scribe-mcp"
args = ["--read-only"]
startup_timeout_sec = 20
tool_timeout_sec = 120
```

For local HTTP, start `yt-scribe-mcp --http` yourself and configure Codex with:

```toml
[mcp_servers.yt-scribe]
url = "http://127.0.0.1:3000/mcp"
tool_timeout_sec = 300
```

In the Codex TUI, use `/mcp` to inspect active MCP servers.

</details>

<details>
<summary>OpenCode MCP config</summary>

OpenCode config uses the `mcp` object in `opencode.jsonc`. Local STDIO:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "yt-scribe": {
      "type": "local",
      "command": ["yt-scribe-mcp"],
      "enabled": true,
      "timeout": 300000
    }
  }
}
```

OpenCode read-only:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "yt-scribe": {
      "type": "local",
      "command": ["yt-scribe-mcp", "--read-only"],
      "enabled": true,
      "timeout": 120000
    }
  }
}
```

OpenCode HTTP mode:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "yt-scribe": {
      "type": "remote",
      "url": "http://127.0.0.1:3000/mcp",
      "enabled": true,
      "timeout": 300000
    }
  }
}
```

</details>

<details>
<summary>FastMCP config generator</summary>

The FastMCP installer can generate or install local MCP configs for clients it
supports, including Claude Desktop, Claude Code, Cursor, Gemini CLI, Goose,
`mcp-json`, and `stdio`. MCP clients run servers in isolated environments, so
declare dependencies explicitly when using `fastmcp install`.

For a local checkout, generate standard MCP JSON like this:

```sh
fastmcp install mcp-json yt_scribe_mcp.py:create_mcp_server -e . --server-name yt-scribe
```

For hosts not listed by `fastmcp install`, including clients that use a generic
MCP JSON file, use the standard STDIO config above or the generated `mcp-json`
output.

</details>

Debug with MCP Inspector:

```sh
npx @modelcontextprotocol/inspector yt-scribe-mcp
```

For HTTP mode, start the server first and point the inspector at
`http://127.0.0.1:3000/mcp`.

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

For the default prompt, `--style` selects the output mode and `--focus` appends
run-specific instructions. JSON output includes `instruction_mode` and
`instruction_sources` so agents can tell whether the run used only the selected
style, added custom focus instructions, or replaced the prompt.

There are two skills:

- `yt-scribe`: teaches an agent how to use the CLI correctly.
- `yt-scribe-transcript-polisher`: guides the agent started by the CLI to polish the fetched transcript.

The Codex plugin uses the root `skills/` tree. The CLI setup flow also keeps a
shared `.agents/skills` tree for installing these skills globally from the
command line. Each skill keeps Codex and OpenCode notes in separate files, so
each tool sees only the details it needs.

## Plugin Skills

This repository contains one Codex plugin skill:

- `skills/yt-scribe`: skill for using the CLI.
- `skills/yt-scribe/harness/codex.md`: Codex-specific CLI instructions.
- `skills/yt-scribe/harness/opencode.md`: OpenCode-specific CLI instructions.

The plugin skill explains how to run the CLI correctly. The transcript-polisher
skill is internal support for the agent started by the CLI after the transcript
has already been fetched, so it is not exposed through the plugin.

When the CLI is installed globally, project-local `.agents` folders are not automatically available in other projects. Run `yt-scribe setup` to install the skill files globally.

The plugin manifest lives in `.codex-plugin/` and points at `skills/`.

For Codex plugin installation, this repo includes `.agents/plugins/marketplace.json`.
The marketplace entry points at `https://github.com/Berkay2002/yt-scribe.git`
because the plugin root is the repository root.

## Notes

- A video must have captions available.
- `youtube-transcript-api` uses an undocumented YouTube web-client API, so YouTube can change or block behavior. If that backend is blocked, `yt-scribe` tries the public timedtext caption track exposed by the watch page.
- If YouTube blocks the IP running the command, use `--http-proxy` or `--https-proxy`, or set `YT_SCRIBE_HTTP_PROXY` / `YT_SCRIBE_HTTPS_PROXY`. A plain datacenter proxy may still be blocked by YouTube.
- `yt-scribe` does not bypass private, unavailable, or disabled captions.
- Long transcripts can be truncated deliberately with `--max-chars`.
