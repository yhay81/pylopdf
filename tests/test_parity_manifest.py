"""Regression tests for the pymupdf capability map."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "tools" / "check_parity.py"
MANIFEST = ROOT / "api" / "pymupdf-parity.json"
OPTIONAL_FIELDS = frozenset({"hint", "note"})


def _run_checker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed repository script under the active interpreter.
        [sys.executable, str(CHECKER), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_capability_map_is_classified_and_resolves() -> None:
    """Every pymupdf API has a status, and every available pylopdf symbol exists."""
    result = _run_checker()
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("pymupdf ")
    assert "planned by milestone: " in result.stdout


@pytest.mark.parametrize(
    ("key", "changes", "message"),
    [
        ("Document.load_page", {"pylopdf": "Document.no_such_method"}, "is not in api/public-api.json"),
        ("FileNotFoundError", {"pylopdf": "NoSuchBuiltinError"}, "is not in api/public-api.json"),
        ("Document.is_dirty", {"pylopdf": "Document.is_closed"}, "instead of the pymupdf name"),
        ("Document.version_count", {"pylopdf": "Document.undo"}, "only an available entry names a pylopdf symbol"),
        ("Document.version_count", {"milestone": "M7"}, "unknown milestone"),
        ("Document.version_count", {"milestone": None}, "a planned entry must name its milestone"),
        ("set_log", {"hint": None}, "needs a hint for migrating callers"),
        ("Document.version_count", {"status": None}, "unclassified"),
    ],
)
def test_capability_checker_rejects_broken_entries(
    tmp_path: Path,
    key: str,
    changes: dict[str, object],
    message: str,
) -> None:
    """The checker names the entry and the rule it breaks; None removes an optional field."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entry = manifest["entries"][key]
    for field, value in changes.items():
        if value is None and field in OPTIONAL_FIELDS:
            del entry[field]
        else:
            entry[field] = value
    broken = tmp_path / "pymupdf-parity.json"
    broken.write_text(json.dumps(manifest), encoding="utf-8")

    result = _run_checker("--manifest", str(broken))

    assert result.returncode == 1
    assert any(line.strip().startswith(f"{key}: ") and message in line for line in result.stderr.splitlines())
