"""Regression tests for the pymupdf parity manifest."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "tools" / "check_parity.py"
MANIFEST = ROOT / "api" / "pymupdf-parity.json"


def _run_checker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed repository script under the active interpreter.
        [sys.executable, str(CHECKER), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_parity_manifest_is_classified_and_resolves() -> None:
    """Every pymupdf API has a status, and every pylopdf symbol it names exists."""
    result = _run_checker()
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0].startswith("pymupdf ")


@pytest.mark.parametrize(
    ("key", "changes", "message"),
    [
        ("Document.authenticate", {"pylopdf": "Document.no_such_method"}, "is not in api/public-api.json"),
        ("Document.xref_object", {"milestone": None}, "a planned entry must name its milestone"),
        ("Document.journal_undo", {"status": None}, "unclassified"),
    ],
)
def test_parity_checker_rejects_broken_entries(
    tmp_path: Path,
    key: str,
    changes: dict[str, object],
    message: str,
) -> None:
    """The checker fails on a missing symbol, a missing milestone, and an unclassified entry."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["entries"][key].update(changes)
    broken = tmp_path / "pymupdf-parity.json"
    broken.write_text(json.dumps(manifest), encoding="utf-8")

    result = _run_checker("--manifest", str(broken))

    assert result.returncode == 1
    assert any(line.strip().startswith(f"{key}: ") and message in line for line in result.stderr.splitlines())
