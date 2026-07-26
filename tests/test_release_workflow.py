from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _ROOT / ".github" / "workflows" / "release.yml"
_NATIVE_PLATFORMS = [
    ("ubuntu-latest", "x86_64-unknown-linux-gnu"),
    ("ubuntu-24.04-arm", "aarch64-unknown-linux-gnu"),
    ("macos-latest", "aarch64-apple-darwin"),
    ("macos-15-intel", "x86_64-apple-darwin"),
    ("windows-latest", "x86_64-pc-windows-msvc"),
]
_MATRIX_ENTRY = re.compile(r"^\s+- \{ runner: ([^,]+), target: ([^ }]+) \}$", re.MULTILINE)


def _job(workflow: str, name: str) -> str:
    start = workflow.index(f"  {name}:")
    match = re.search(r"^  [a-z][a-z0-9-]*:$", workflow[start + 1 :], re.MULTILINE)
    if match is None:
        return workflow[start:]
    return workflow[start : start + 1 + match.start()]


def test_every_native_release_wheel_runs_on_its_own_architecture() -> None:
    workflow = _WORKFLOW.read_text(encoding="utf-8")

    for job_name in ("build-wheels", "build-free-threaded-wheels"):
        job = _job(workflow, job_name)
        assert _MATRIX_ENTRY.findall(job) == _NATIVE_PLATFORMS
        assert "matrix.platform.smoke" not in job
        assert "python tools/smoke_artifact.py dist" in job
