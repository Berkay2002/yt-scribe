#!/usr/bin/env python3
"""Fetch YouTube transcripts and polish them with Codex or OpenCode."""

from __future__ import annotations

import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VERSION = "0.1.0"
COMMAND_NAME = "yt-scribe"
DEFAULT_AGENT_HARNESS = "codex"
AGENT_HARNESSES = ("codex", "opencode")
CONFIG_ENV_VAR = "YT_SCRIBE_CONFIG"
CONFIG_FILENAME = "config.json"
DATA_DIR_ENV_VAR = "YT_SCRIBE_DATA_DIR"
AGENTS_SKILLS_DIR_ENV_VAR = "YT_SCRIBE_AGENTS_SKILLS_DIR"
HTTP_PROXY_ENV_VAR = "YT_SCRIBE_HTTP_PROXY"
HTTPS_PROXY_ENV_VAR = "YT_SCRIBE_HTTPS_PROXY"
DEFAULT_CACHE_DIR = Path(".yt-scribe") / "cache"
PROJECT_CONFIG = Path(".yt-scribe") / CONFIG_FILENAME
RUN_REGISTRY_FILENAME = "registry.json"
ANSI_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
DEEP_WORKFLOW_DURATION_THRESHOLD_SECONDS = 45 * 60
DEEP_CHUNK_TARGET_SECONDS = 10 * 60
DEEP_CHUNK_OVERLAP_SECONDS = 45
DEEP_CHUNK_MAX_CHARS = 15_000
RUN_WORKFLOWS = ("auto", "quick", "deep")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)

STYLE_INSTRUCTIONS = {
    "clean": (
        "Clean this YouTube transcript. Remove filler, repeated phrases, "
        "timestamps, caption artifacts, and obvious speech disfluencies. "
        "Preserve the speaker's meaning and order. Do not add facts that are "
        "not in the transcript. Return only the cleaned text."
    ),
    "notes": (
        "Turn this YouTube transcript into clear markdown notes. Preserve the "
        "meaning and order. Use concise headings and bullets where helpful. "
        "Remove filler and caption artifacts. Do not add facts that are not in "
        "the transcript."
    ),
    "summary": (
        "Summarize this YouTube transcript in plain markdown. Include the main "
        "ideas, key details, and any concrete action items. Do not add facts "
        "that are not in the transcript."
    ),
    "article": (
        "Rewrite this YouTube transcript as a readable article in markdown. "
        "Preserve the argument and sequence, remove filler, and avoid adding "
        "facts that are not in the transcript."
    ),
}
TIMESTAMP_GROUNDING_INSTRUCTION = (
    "Timestamp grounding is requested. When the transcript includes timestamp anchors "
    "such as [01:23], preserve useful anchors in the polished output so important "
    "claims can be traced back to the transcript. Do not invent timestamps, and do "
    "not add timestamp anchors for claims that are not supported by nearby transcript text."
)
TEMPLATE_INSTRUCTIONS = {
    "lecture": (
        "Use a lecture notes structure with concise section headings, key concepts, "
        "definitions, examples, and open questions when they are present in the transcript."
    ),
    "research": (
        "Use a research notes structure with claims, evidence, methods, limitations, "
        "and follow-up questions when they are present in the transcript."
    ),
    "meeting": (
        "Use a meeting-style structure with decisions, risks, action items, owners, "
        "and deadlines only when they are present in the transcript."
    ),
}
INNER_POLISHER_SKILL = "yt-scribe-transcript-polisher"
HARNESS_INSTRUCTIONS = {
    "codex": "harness/codex.md",
    "opencode": "harness/opencode.md",
}
TRANSCRIPT_DELIVERY = {
    "codex": "stdin",
    "opencode": "an attached transcript file",
}

EMBEDDED_SKILL_ASSETS = {
    ".agents/skills/yt-scribe/SKILL.md": """---
name: yt-scribe
description: Use when an agent needs to fetch a YouTube transcript, inspect
  available captions, save raw captions, or polish a transcript into notes,
  summaries, cleaned text, or article-style prose through the installed
  `yt-scribe` CLI.
---

# yt-scribe

Use the installed `yt-scribe` CLI for YouTube transcript workflows. This skill
teaches an agent how to use the CLI correctly.

Prefer `--json` when reading command output for analysis or chaining.

If the current host exposes the `yt-scribe` MCP server, prefer MCP tools for
structured agent workflows:

- `inspect_youtube_captions` before assuming captions exist.
- `fetch_youtube_transcript` for read-only transcript access.
- `agent_polish_transcript` or `agent_fetch_and_polish_youtube` only when the
  user wants agent-backed polishing and accepts that Codex or OpenCode may run.

Use the CLI when MCP is not installed, when the user asks for a terminal command,
or when file-oriented workflows such as `--out`, `--bundle-dir`, `batch`, or
`verify` are the better fit.

Read exactly one harness file for command details:

- Codex: `harness/codex.md`
- OpenCode: `harness/opencode.md`

The CLI is human-first. Its default path should be the same obvious command a person would run:

```sh
yt-scribe run "<youtube-url>"
```

## Start

Verify the command exists and the local harness setup is available:

```sh
yt-scribe --json doctor
```

If `yt-scribe` is missing, install and set it up from the public repository:

```sh
python -m pip install --upgrade git+https://github.com/Berkay2002/yt-scribe.git \\
  && python -m yt_scribe setup
```

From a checkout, run `sh ./install-local.sh` on Linux or macOS, or
`.\\install-local.ps1` on Windows. The local installers create the wrapper and
run setup.

## Workflow

For a new YouTube link:

```sh
yt-scribe --json inspect "<youtube-url>"
yt-scribe --json inspect "<youtube-url>" --brief
yt-scribe --json fetch "<youtube-url>" --lang en --out transcript.txt
yt-scribe --json polish transcript.txt --style notes --out notes.md
yt-scribe --json polish transcript.txt --focus "Focus on decisions and risks" --out notes.md
```

For the one-command path:

```sh
yt-scribe --json run "<youtube-url>"
yt-scribe --json run "<youtube-url>" --workflow quick
yt-scribe --json run "<youtube-url>" --workflow deep
yt-scribe --json run "<youtube-url>" --focus "Keep only action items"
yt-scribe --json run "<youtube-url>" --timestamps
yt-scribe --json run "<youtube-url>" --bundle-dir .yt-scribe/runs/VIDEO_ID
```

`run` defaults to `--workflow auto`. Auto mode uses duration metadata when it is
available and selects the deep workflow for videos at least 45 minutes long.
Shorter videos keep quick behavior. If duration is missing, caption availability
is still checked separately and auto mode stays quick.

Use deep mode when a long video needs durable artifacts or follow-up questions.
Deep mode preserves exact transcript JSON, timestamped transcript text, chunk
files, per-chunk notes, merged final notes, metadata, and structural checks.
Default managed deep runs are stored outside the current project. Use
`--bundle-dir` only when the user wants artifacts in a specific directory.

Useful follow-up commands:

```sh
yt-scribe --json runs list
yt-scribe --json runs open <run-name>
yt-scribe --json runs rename <run-name> "Project vocabulary"
yt-scribe --json run "<youtube-url>" --workflow deep --bundle-dir "<bundle-dir>" --resume
yt-scribe --json ask <run-name> "What did they say about retrieval?" --show-context
yt-scribe --json ask <run-name> "What did they say about retrieval?" --agent
```

Use `ask --show-context` before `ask --agent` when the user wants to inspect
retrieved source snippets before spending agent tokens. Agent-backed `ask`
passes only retrieved outline, chunk-note, and transcript context to the harness.

Use styles intentionally:

- `notes`: structured markdown notes.
- `summary`: concise summary with key ideas.
- `clean`: cleaned transcript text with filler removed.
- `article`: readable article-style prose.

Use `--focus "..."` or `--focus-file instructions.md` when the user wants
specific emphasis while keeping the normal harness prompt. Use `--instruction`
or `--prompt-file` only when the user needs to replace the whole polishing prompt.

Use `--timestamps` when the user needs polished output with source anchors. For
`run`, yt-scribe passes transcript segment start times to the polisher. For
`polish`, the input transcript should already contain useful timestamp anchors.

Use `verify` when the user needs a conservative transcript-backed check:

```sh
yt-scribe --json verify notes.md --transcript transcript.json
```

Use profiles and templates for repeated local conventions:

```sh
yt-scribe config profile set research --style notes --template research --langs en,en-US
yt-scribe --json run "<youtube-url>" --profile research
```

Use `--chunk-chars` only for long transcripts that need chunk-and-merge polishing.
Use `batch` for URL lists; playlist URLs in a batch file expand into normal batch
items.

## Safety

- Use `inspect` before assuming captions exist.
- Do not claim transcript availability until `fetch`, `inspect`, or `run` succeeds.
- Do not bypass private, disabled, or unavailable captions.
- Do not use `raw --body` unless high-level commands are insufficient.
- Do not run destructive shell commands as part of this workflow.
- Do not pass secrets in `--focus`, `--instruction`, or prompt files.
""",
    ".agents/skills/yt-scribe/harness/codex.md": """# Codex Harness

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
""",
    ".agents/skills/yt-scribe/harness/opencode.md": """# OpenCode Harness

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
""",
    ".agents/skills/yt-scribe/agents/openai.yaml": """interface:
  display_name: "YT Scribe"
  short_description: "Use yt-scribe to fetch and polish YouTube transcripts."
  default_prompt: "Turn this YouTube link into clean notes."
""",
    ".agents/skills/yt-scribe-transcript-polisher/SKILL.md": """---
name: yt-scribe-transcript-polisher
description: Use when `yt-scribe polish` or `yt-scribe run` invokes an agent to
  transform a YouTube transcript into cleaned text, notes, a summary, or
  article-style prose. This skill is for transcript polishing only, not for
  fetching captions or running the CLI.
---

# yt-scribe Transcript Polisher

Transform the transcript text already provided by `yt-scribe`. This skill is for the
agent started by the CLI after the transcript has already been fetched. Do not fetch
the video, inspect unrelated files, run shell commands, or call `yt-scribe`.

Read exactly one harness file based on how the transcript was provided:

- Codex stdin: `harness/codex.md`
- OpenCode attached transcript file: `harness/opencode.md`

## Rules

- Return only the requested polished transcript output.
- Preserve the speaker's meaning, sequence, and concrete claims.
- Honor custom user instructions passed by `yt-scribe`. When they conflict with
  the selected output mode, the custom instructions win unless they would require
  adding unsupported facts.
- Remove caption artifacts, repeated fragments, filler, obvious false starts,
  and timestamp residue unless timestamp grounding was requested.
- When timestamp grounding is requested, preserve useful timestamp anchors from
  the provided transcript near important claims. Do not invent timestamps.
- Do not add facts, examples, citations, links, or claims that are not in the transcript.
- Do not mention that you used a skill, a harness, stdin, an attached file, or a
  cleaning process.
- If the transcript is empty or unusable, say that the transcript content is
  missing or unusable.

## Output Modes

For `clean`, produce lightly edited prose close to the original transcript.

For `notes`, produce markdown notes with short headings and bullets. Keep the
structure useful for review, not overly nested.

For `summary`, produce a concise markdown summary with the main ideas, key details,
and action items when present.

For `article`, produce readable article-style markdown while preserving the original
argument and order.

## Quality Bar

Prefer boring accuracy over elegant rewriting. When a phrase is ambiguous, keep it
closer to the original instead of guessing. Preserve names, commands, numbers,
dates, and technical terms exactly unless the transcript clearly contains a
captioning artifact.
""",
    ".agents/skills/yt-scribe-transcript-polisher/harness/codex.md": """# Codex Harness

Use this file when `yt-scribe` invokes Codex through `codex exec`.

The transcript is provided through stdin, appended to the prompt as the content to
transform. Return only the polished transcript output.

Do not mention Codex, stdin, or the polishing process in the final answer.
""",
    ".agents/skills/yt-scribe-transcript-polisher/harness/opencode.md": """# OpenCode Harness

Use this file when `yt-scribe` invokes OpenCode through `opencode run`.

The transcript is attached as a temp transcript file. Read that attached transcript
as the content to transform. Return only the polished transcript output.

Do not mention OpenCode, the attached file, or the polishing process in the final answer.
""",
    ".agents/skills/yt-scribe-transcript-polisher/agents/openai.yaml": """interface:
  display_name: "YT Scribe Transcript Polisher"
  short_description: "Polish transcript text passed through yt-scribe."
  default_prompt: "Polish this transcript into clean notes."
""",
}


class CliError(Exception):
    def __init__(self, message: str, code: str = "error", details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass
class PolishInstruction:
    text: str
    mode: str
    sources: list[str]


@dataclass
class PolishOptions:
    style: str = "notes"
    template: str | None = None
    focus: list[str] | None = None
    focus_file: list[str] | None = None
    instruction: str | None = None
    prompt_file: str | None = None
    timestamps: bool = False
    agent_harness: str | None = None
    model: str | None = None
    cd: str | None = None
    max_chars: int = 0


class ProgressWait:
    def __init__(self, reporter: ProgressReporter, message: str, interval_seconds: int = 15):
        self.reporter = reporter
        self.message = message
        self.interval_seconds = interval_seconds
        self.started_at = 0.0
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def __enter__(self) -> ProgressWait:
        self.started_at = time.monotonic()
        self.reporter.message(self.message)
        if self.reporter.enabled:
            self.thread = threading.Thread(target=self._heartbeat, daemon=True)
            self.thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=0.2)

    def _heartbeat(self) -> None:
        while not self.stop_event.wait(self.interval_seconds):
            elapsed = int(time.monotonic() - self.started_at)
            self.reporter.message(f"{self.message} ({elapsed}s elapsed)")


class ProgressReporter:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def message(self, text: str) -> None:
        if self.enabled:
            print(f"{COMMAND_NAME}: {text}", file=sys.stderr, flush=True)

    def wait(self, message: str) -> ProgressWait:
        return ProgressWait(self, message)
