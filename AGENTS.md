# Repository Guidelines

## Project Structure & Module Organization

This is a small Python CLI package. Package code lives under `src/yt_scribe/`. The `yt-scribe` console script routes through `src/yt_scribe/cli.py`, and the MCP server routes through `src/yt_scribe/mcp.py`. Root `yt_scribe.py` and `yt_scribe_mcp.py` remain thin compatibility wrappers for direct script use and legacy imports. Tests live in `tests/`, with CLI behavior in `tests/test_cli.py`, MCP behavior in `tests/test_mcp.py`, focused module coverage in files such as `tests/test_youtube.py`, and opt-in live integration coverage in `tests/test_e2e.py`.

Packaged support files are split by purpose: `skills/` contains the Codex plugin skills, `.agents/skills/` contains the shared setup-source copies used by the CLI install flow, `assets/` contains logo files, and `.codex-plugin/` holds plugin metadata. Install helpers are `install-local.sh` and `install-local.ps1`.

## Build, Test, and Development Commands

- `pip install -e ".[dev]"`: install the package in editable mode with pytest and ruff.
- `python -m yt_scribe doctor`: inspect local CLI, config, PATH, and harness availability.
- `python -m yt_scribe run "<youtube-url>"`: fetch a transcript and polish it through the default harness.
- `python -m pytest`: run the normal test suite.
- `python -m ruff check .`: run lint checks.
- `YT_SCRIBE_RUN_E2E=1 python -m pytest tests/test_e2e.py -q -s`: run live YouTube and agent harness tests. Use PowerShell syntax on Windows: `$env:YT_SCRIBE_RUN_E2E = "1"; python -m pytest tests/test_e2e.py -q -s`.

## Coding Style & Naming Conventions

Target Python 3.10+. Ruff is configured with a 100-character line length and rules `E`, `F`, `I`, `UP`, and `B`. Keep imports sorted by ruff. Use clear snake_case for functions, variables, and test names. Prefer explicit dataclasses or small helpers when they make CLI state easier to read, but avoid adding abstractions around one-off command handling.

## Testing Guidelines

Add or update tests for every behavior change. Prefer narrow unit tests in `tests/test_yt_scribe.py` for parser, formatting, config, and helper logic. Use `tests/test_cli.py` when stdout, stderr, exit codes, or JSON stability matters. Keep network and live agent checks behind the `e2e` marker and `YT_SCRIBE_RUN_E2E=1`.

## Commit & Pull Request Guidelines

Recent history uses short imperative subjects, for example `Add OpenCode harness support` and `Refine polish flag help and install guidance`. Keep commits focused and describe the user-visible behavior changed.

Pull requests should include a concise description, the commands run, and any limitations such as skipped e2e tests. Include screenshots only for asset or documentation rendering changes.

## Security & Configuration Tips

Do not bypass private, disabled, or unavailable captions. Keep generated transcripts and polished outputs out of commits unless they are deliberate fixtures. Use temporary config paths such as `YT_SCRIBE_CONFIG` in tests so local user settings are not modified.

## Agent skills

Engineering skills use the local markdown issue tracker in `.scratch/`.
When a skill says to publish a PRD, issue, or plan to the issue tracker, create
or update files under `.scratch/<feature-slug>/`; do not create GitHub issues
unless the user explicitly asks for that.

Codex plugin skills live under the root `skills/` directory. The plugin manifest
points at that tree. The CLI setup flow keeps shared setup-source copies under
`.agents/skills/` and installs them globally for use from other projects. Do not
reintroduce `.opencode/agents/` as a separate source tree.

The plugin exposes one skill:

- `skills/yt-scribe`: teaches agents how to use the CLI.

The transcript-polisher skill is internal CLI support under `.agents/skills/`;
do not copy it into the plugin `skills/` tree.

Keep embedded skill assets in `src/yt_scribe/__init__.py` in sync with the files
under `skills/` and `.agents/skills/` when changing setup, plugin, or install
behavior.

## Advanced Workflow Features

Some CLI features are for repeated or automated workflows, not the default happy
path. Keep the default README path focused on `inspect`, `fetch`, `polish`, and
`run`. If documenting advanced features publicly, put them in a collapsed or
clearly labeled advanced section.

Advanced or opt-in features include:

- `--langs` for ordered caption language fallback.
- `--front-matter` for metadata-rich markdown outputs.
- `--cache-dir` and `--resume` for explicit transcript cache reuse.
- `--timestamps` for timestamp-grounded polished output.
- `--template` and `--profile` for repeated output conventions.
- `--chunk-chars` for chunk-and-merge polishing of long transcripts.
- `--bundle-dir` for grouped transcript, polished output, and metadata artifacts.
- `verify` for conservative transcript-backed checks of polished output.
- `init-project` for repo-local `.yt-scribe/` guidance.
- `batch` for plain-text URL lists and manifest-based partial success.
  Batch inputs may include playlist URLs, which expand into ordinary batch items.

Planning artifacts such as PRDs, issue breakdowns, and brainstorming notes belong
outside the public release surface. The local `docs/` folder is ignored for that
purpose.

## Documentation Freshness

After substantial changes to CLI behavior, packaging, setup/install flow, agent
skills, harness behavior, workflow expectations, or domain language, run the
private project skill at `.agents/skills/docs-freshness/` after the implementation
work is committed and the working tree is clean. Check README.md, AGENTS.md,
CONTEXT.md, local planning docs, CLI help text, and embedded skill assets for
stale claims. After docs edits and verification, the skill overwrites its local
`state.md` with only the baseline commit hash.

Keep public docs public and local docs local. In this checkout, AGENTS.md,
CONTEXT.md, docs/, and .scratch/ are private local guidance unless the user
explicitly asks to publish them.
