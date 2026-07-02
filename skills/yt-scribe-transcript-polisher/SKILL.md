---
name: yt-scribe-transcript-polisher
description: Use when `yt-scribe polish` or `yt-scribe run` invokes an agent to transform a YouTube transcript into cleaned text, notes, a summary, or article-style prose. This skill is for transcript polishing only, not for fetching captions or running the CLI.
---

# yt-scribe Transcript Polisher

Transform the transcript text already provided by `yt-scribe`. Do not fetch the video, inspect unrelated files, run shell commands, or call `yt-scribe`; that work belongs to the outer CLI workflow.

Read exactly one harness file based on how the transcript was provided:

- Codex stdin: `harness/codex.md`
- OpenCode attached transcript file: `harness/opencode.md`

## Rules

- Return only the requested polished transcript output.
- Preserve the speaker's meaning, sequence, and concrete claims.
- Remove caption artifacts, repeated fragments, filler, obvious false starts, and timestamp residue.
- Do not add facts, examples, citations, links, or claims that are not in the transcript.
- Do not mention that you used a skill, a harness, stdin, an attached file, or a cleaning process.
- If the transcript is empty or unusable, say that the transcript content is missing or unusable.

## Output Modes

For `clean`, produce lightly edited prose close to the original transcript.

For `notes`, produce markdown notes with short headings and bullets. Keep the structure useful for review, not overly nested.

For `summary`, produce a concise markdown summary with the main ideas, key details, and action items when present.

For `article`, produce readable article-style markdown while preserving the original argument and order.

## Quality Bar

Prefer boring accuracy over elegant rewriting. When a phrase is ambiguous, keep it closer to the original instead of guessing. Preserve names, commands, numbers, dates, and technical terms exactly unless the transcript clearly contains a captioning artifact.
