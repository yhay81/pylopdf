"""Regression tests for the ROADMAP.md milestone tracker bodies."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("roadmap_issues", ROOT / "tools" / "roadmap_issues.py")
assert _SPEC is not None
assert _SPEC.loader is not None
roadmap_issues = importlib.util.module_from_spec(_SPEC)
sys.modules["roadmap_issues"] = roadmap_issues
_SPEC.loader.exec_module(roadmap_issues)

_PARITY_SPEC = importlib.util.spec_from_file_location("check_parity", ROOT / "tools" / "check_parity.py")
assert _PARITY_SPEC is not None
assert _PARITY_SPEC.loader is not None
check_parity = importlib.util.module_from_spec(_PARITY_SPEC)
sys.modules["check_parity"] = check_parity
_PARITY_SPEC.loader.exec_module(check_parity)


def test_trackers_cover_every_capability_map_milestone() -> None:
    """Every milestone slug of the capability map has a tracker, with the same name."""
    names = {slug: name for slug, (name, _) in roadmap_issues.MILESTONES.items()}
    assert names == check_parity.MILESTONES


@pytest.mark.parametrize("slug", sorted(roadmap_issues.MILESTONES))
def test_tracker_lists_every_entry_of_its_milestone(slug: str) -> None:
    """A tracker can close only when the capability map has no planned entry for it."""
    body = roadmap_issues.render(slug)
    entries = json.loads((ROOT / "api" / "pymupdf-parity.json").read_text(encoding="utf-8"))["entries"]
    for key, entry in entries.items():
        if entry["milestone"] != slug:
            continue
        box = "x" if entry["status"] in roadmap_issues.DONE_STATUSES else " "
        assert f"- [{box}] `{key}`" in body
    assert "## Scope\n\n- [" in body
    assert "## Acceptance\n\n- [ ] " in body
    assert "](#" not in body


def test_sync_replaces_only_the_generated_block() -> None:
    """People's checks outside the block survive a sync."""
    body = roadmap_issues.render("reading").replace("- [ ] ", "- [x] ", 1)
    stale = body.replace("- [ ] `", "- [x] `")
    synced = roadmap_issues.replace_block(stale, roadmap_issues.capability_block("reading"))
    assert synced == body
    with pytest.raises(ValueError, match="no capability block"):
        roadmap_issues.replace_block("no block", "")
