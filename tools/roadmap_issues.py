"""Render and synchronize the GitHub issues that track ROADMAP.md milestones.

Every milestone under ``## Milestones`` in ROADMAP.md, except Engine work,
has one open tracker issue titled ``Milestone: <name>`` with the
``tracker`` label. Its body has three parts:

- ``Scope``: the milestone's bullets as a checklist. A bullet that starts
  with ``Done:`` or ends with ``(done)`` starts checked.
- ``Acceptance``: one box per sentence of the milestone's acceptance
  paragraph.
- ``Capability map``: every entry of ``api/pymupdf-parity.json`` whose
  ``milestone`` is the milestone's slug, checked when the entry is
  ``available`` or ``integration``, grouped by pymupdf class.

People check the Scope and Acceptance boxes by hand. The capability block
sits between two HTML comments and belongs to this tool, so the tracker
closes only when the capability map has no planned entry for its milestone.

Commands:

- ``render SLUG`` prints the complete body of one tracker, for creating it.
- ``sync`` rewrites the capability block of every open tracker through the
  GitHub CLI and leaves the rest of each body as people edited it. With
  ``--dry-run`` it prints which trackers would change instead.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROADMAP = ROOT / "ROADMAP.md"
MANIFEST = ROOT / "api" / "pymupdf-parity.json"
ROADMAP_URL = "https://github.com/yhay81/pylopdf/blob/main/ROADMAP.md"

# The slugs of tools/check_parity.py, in ROADMAP.md order, with their phase.
MILESTONES = {
    "content": ("Content model", 1),
    "canvas": ("Canvas", 1),
    "reset": ("API reset", 1),
    "reading": ("Reading", 2),
    "rendering": ("Rendering", 2),
    "writing": ("Writing", 2),
    "interaction": ("Interaction", 2),
    "rewriting": ("Rewriting", 2),
    "structure": ("Structure", 2),
    "persistence": ("Persistence", 2),
    "understanding": ("Understanding", 2),
}
DONE_STATUSES = frozenset({"available", "integration"})
BLOCK_START = "<!-- roadmap-issues:capabilities:start -->"
BLOCK_END = "<!-- roadmap-issues:capabilities:end -->"
TITLE_PREFIX = "Milestone: "


def _anchor(name: str) -> str:
    return re.sub(r"[^a-z0-9 -]", "", name.lower()).replace(" ", "-")


def milestone_section(name: str, roadmap: str | None = None) -> str:
    """Return the text of one ``### name`` section under ``## Milestones``."""
    text = ROADMAP.read_text(encoding="utf-8") if roadmap is None else roadmap
    milestones = text.split("\n## Milestones\n", 1)[1].split("\n## ", 1)[0]
    match = re.search(rf"^### {re.escape(name)}\n(.*?)(?=^### |\Z)", milestones, re.MULTILINE | re.DOTALL)
    if match is None:
        msg = f"ROADMAP.md has no milestone named {name!r}"
        raise ValueError(msg)
    return match.group(1).strip("\n")


def _scope_items(section: str) -> tuple[list[str], list[str], str]:
    """Split a section into its intro lines, checklist items, and acceptance."""
    intro: list[str] = []
    items: list[list[str]] = []
    acceptance: list[str] = []
    mode = "intro"
    for line in section.splitlines():
        if line.startswith("- "):
            items.append([line])
            mode = "item"
        elif line.startswith("Acceptance:"):
            acceptance.append(line.removeprefix("Acceptance:").strip())
            mode = "acceptance"
        elif mode == "acceptance":
            if line.strip():
                acceptance.append(line.strip())
        elif mode == "item" and (line.startswith("  ") or not line.strip()):
            items[-1].append(line.rstrip())
        elif line.strip():
            intro.append(line)
            mode = "intro"
    rendered = []
    for item in items:
        body = " ".join(part.strip() for part in item if part)
        done = body.startswith("- Done:") or body.rstrip(".").endswith("(done)")
        rendered.append(("- [x] " if done else "- [ ] ") + _unwrap(item)[2:])
    return [_unwrap(intro)] if intro else [], rendered, " ".join(acceptance)


def _unwrap(lines: list[str]) -> str:
    """Join hard-wrapped lines, which GitHub issues render as line breaks."""
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        starts_block = not out or not stripped or not out[-1].strip() or stripped.startswith("- ")
        if starts_block:
            out.append(line.rstrip())
        else:
            out[-1] += " " + stripped
    while out and not out[-1]:
        out.pop()
    return "\n".join(out).replace("](#", f"]({ROADMAP_URL}#")


def _acceptance_items(acceptance: str) -> list[str]:
    """Give each criterion of an acceptance paragraph its own box."""
    items = []
    for part in re.split(r"(?<=[.;])\s+", acceptance.replace("](#", f"]({ROADMAP_URL}#")):
        criterion = re.sub(r"^and ", "", part.strip()).rstrip(";.")
        if criterion:
            items.append(f"- [ ] {criterion[0].upper()}{criterion[1:]}.")
    return items


def capability_block(slug: str, manifest: dict[str, object] | None = None) -> str:
    """Return the generated capability-map block of one milestone."""
    data = json.loads(MANIFEST.read_text(encoding="utf-8")) if manifest is None else manifest
    entries = data["entries"]
    if not isinstance(entries, dict):
        msg = "the capability map has no entries object"
        raise TypeError(msg)
    groups: dict[str, list[tuple[str, dict[str, object]]]] = defaultdict(list)
    for key, entry in entries.items():
        if entry.get("milestone") == slug:
            owner = key.split(".", 1)[0] if "." in key else "module"
            groups[owner].append((key, entry))
    total = sum(len(group) for group in groups.values())
    done = sum(entry["status"] in DONE_STATUSES for group in groups.values() for _, entry in group)
    lines = [
        BLOCK_START,
        (
            f"{done} of {total} pymupdf names of this milestone are covered. Generated by "
            "`tools/roadmap_issues.py sync` from `api/pymupdf-parity.json`; edit the map, not this block."
        ),
    ]
    for owner in sorted(groups, key=lambda name: (name != "module", name)):
        group = sorted(groups[owner])
        covered = sum(entry["status"] in DONE_STATUSES for _, entry in group)
        lines += ["", f"<details><summary>{owner} ({covered}/{len(group)})</summary>", ""]
        for key, entry in group:
            box = "x" if entry["status"] in DONE_STATUSES else " "
            target = f" → `{entry['pylopdf']}`" if entry.get("pylopdf") else ""
            lines.append(f"- [{box}] `{key}`{target}")
        lines += ["", "</details>"]
    lines.append(BLOCK_END)
    return "\n".join(lines)


def render(slug: str) -> str:
    """Return the complete body of one milestone tracker."""
    name, phase = MILESTONES[slug]
    intro, items, acceptance = _scope_items(milestone_section(name))
    header = (
        f"Tracker for the [{name}]({ROADMAP_URL}#{_anchor(name)}) milestone of phase {phase}. "
        "ROADMAP.md says what and why; this issue records progress. Split an item into a sub-issue "
        "when work on it starts, and close this tracker when every box is checked."
    )
    parts = [header]
    if intro:
        parts.append("\n".join(intro))
    parts += ["## Scope", "\n".join(items) or "_No items._"]
    parts += ["## Acceptance", "\n".join(_acceptance_items(acceptance)) or "_None stated._"]
    parts += ["## Capability map", capability_block(slug)]
    return "\n\n".join(parts) + "\n"


def replace_block(body: str, block: str) -> str:
    """Replace the generated block of a tracker body."""
    start, end = body.find(BLOCK_START), body.find(BLOCK_END)
    if start < 0 or end < start:
        msg = "the body has no capability block"
        raise ValueError(msg)
    return body[:start] + block + body[end + len(BLOCK_END) :]


def _gh(*args: str, stdin: str | None = None) -> str:
    result = subprocess.run(  # noqa: S603 - the GitHub CLI with fixed arguments.
        ["gh", *args],  # noqa: S607 - resolved from PATH like any developer tool.
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        input=stdin,
    )
    return result.stdout


def sync(*, dry_run: bool) -> int:
    """Rewrite the capability block of every open milestone tracker."""
    issues = json.loads(
        _gh("issue", "list", "--label", "tracker", "--state", "open", "--limit", "200", "--json", "number,title,body")
    )
    by_title = {issue["title"]: issue for issue in issues}
    changed = 0
    for slug, (name, _) in MILESTONES.items():
        issue = by_title.get(TITLE_PREFIX + name)
        if issue is None:
            sys.stderr.write(f"{name}: no open tracker\n")
            continue
        body = replace_block(issue["body"].replace("\r\n", "\n"), capability_block(slug))
        if body == issue["body"].replace("\r\n", "\n"):
            continue
        changed += 1
        sys.stdout.write(f"{name}: #{issue['number']} {'would change' if dry_run else 'updated'}\n")
        if not dry_run:
            _gh("issue", "edit", str(issue["number"]), "--body-file", "-", stdin=body)
    return changed


def main(argv: list[str] | None = None) -> int:
    """Run the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    render_parser = commands.add_parser("render", help="print the body of one milestone tracker")
    render_parser.add_argument("slug", choices=sorted(MILESTONES))
    sync_parser = commands.add_parser("sync", help="rewrite the capability block of every open tracker")
    sync_parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "render":
        sys.stdout.write(render(args.slug))
        return 0
    sync(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
