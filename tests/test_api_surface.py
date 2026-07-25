"""Regression tests for the reviewed public API surface."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_public_api_matches_reviewed_snapshot() -> None:
    """Reject accidental changes to exports, signatures, mappings, and inheritance."""
    result = subprocess.run(  # noqa: S603 - fixed repository script under the active interpreter.
        [sys.executable, str(ROOT / "tools" / "check_api_surface.py")],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
