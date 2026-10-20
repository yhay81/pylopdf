"""Regression tests for the reviewed public API surface."""

from __future__ import annotations

import json
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


def test_geometry_snapshot_keeps_inherited_coordinates_and_operators() -> None:
    """NamedTuple subclassing must not hide coordinate or operator contracts."""
    symbols = json.loads((ROOT / "api" / "public-api.json").read_text(encoding="utf-8"))["symbols"]
    for name, fields in (("Point", ("x", "y")), ("Matrix", ("a", "b", "c", "d", "e", "f"))):
        contract = symbols[name]
        assert contract["annotations"] == dict.fromkeys(fields, "float")
        assert all(contract["members"][field]["kind"] == "property" for field in fields)
        assert all(op in contract["members"] for op in ("__mul__", "__truediv__", "__bool__"))
