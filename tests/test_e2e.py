import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "yt_scribe.py"
VIDEO_URL = "https://www.youtube.com/watch?v=5AkdMangfNk&t=340s"


def run_with_caption_retry(command):
    result = None
    for attempt in range(3):
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=600,
            check=False,
        )
        if result.returncode == 0:
            return result
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return result
        if payload.get("error", {}).get("code") != "caption_fetch_failed":
            return result
        if attempt < 2:
            time.sleep(5)
    return result


@pytest.mark.e2e
@pytest.mark.skipif(
    os.environ.get("YT_SCRIBE_RUN_E2E") != "1",
    reason="set YT_SCRIBE_RUN_E2E=1 to run real YouTube and agent harness e2e tests",
)
@pytest.mark.parametrize("harness", ["codex", "opencode"])
def test_run_fetches_real_video_and_polishes_with_real_agent(tmp_path, harness):
    if shutil.which(harness) is None:
        pytest.skip(f"{harness} is not installed")

    transcript_path = tmp_path / f"{harness}-transcript.txt"
    output_path = tmp_path / f"{harness}-clean.md"
    result = run_with_caption_retry(
        [
            sys.executable,
            str(SCRIPT),
            "--json",
            "run",
            VIDEO_URL,
            "--agent-harness",
            harness,
            "--style",
            "clean",
            "--max-chars",
            "1800",
            "--transcript",
            str(transcript_path),
            "--out",
            str(output_path),
        ]
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["run"]["agent_harness"] == harness
    assert payload["run"]["output_path"] == str(output_path)

    transcript = transcript_path.read_text(encoding="utf-8")
    polished = output_path.read_text(encoding="utf-8")
    assert len(transcript) > 500
    assert len(polished) > 100
    assert "yt-scribe-transcript-polisher" not in polished
    assert not polished.lstrip().startswith('{"type"')
