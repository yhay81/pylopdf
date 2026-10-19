"""Validate the pymupdf parity manifest and report coverage per pymupdf class.

``api/pymupdf-parity.json`` holds one entry per pymupdf public API, keyed by
its pymupdf name: ``open`` or ``TEXT_DEHYPHENATE`` for a module attribute,
``PDF_ANNOT_*`` for module attributes grouped by a shared prefix, and
``Document.save`` for a member of one of the tracked classes. Each entry has
these fields:

- ``kind``: what the name is in pymupdf, as ``tools/pymupdf_inventory.py``
  reports it.
- ``status``: ``supported`` when pylopdf offers it under the same name with
  pymupdf's arguments and result shape; ``deviation`` when pylopdf offers the
  capability with a documented difference; ``planned`` when a ROADMAP.md
  foundation or milestone adds it; ``integration`` when an ecosystem tool
  covers it through a tested recipe; ``non-goal`` when the roadmap's non-goals
  exclude it.
- ``pylopdf``: the pylopdf public symbol that provides it or comes closest,
  spelled as in ``api/public-api.json`` (``Document.save``, ``LinkInfo.uri``),
  or ``null``.
- ``milestone``: the foundation (``F1``-``F8``) or milestone (``M1``-``M8``)
  that adds it or closes its deviation, or ``null``.
- ``note``: the difference, the plan, or the reason, in English.

The manifest records API names and statuses only. Never copy pymupdf code or
documentation text, which is AGPL-licensed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import TypeAlias, cast

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "api" / "pymupdf-parity.json"
PUBLIC_API_PATH = ROOT / "api" / "public-api.json"

SCHEMA = 1
STATUSES = ("supported", "deviation", "planned", "integration", "non-goal")
MILESTONES = tuple(f"F{number}" for number in range(1, 9)) + tuple(f"M{number}" for number in range(1, 9))
FIELDS = ("kind", "status", "pylopdf", "milestone", "note")
MODULE_SCOPE = "module"
MODULE_KINDS = frozenset({"alias", "class", "constant", "exception", "function", "group"})
MEMBER_KINDS = frozenset({"alias", "attribute", "class", "method", "property", "protocol"})
TRACKED_CLASSES = (
    "Document",
    "Page",
    "Annot",
    "Widget",
    "Link",
    "Outline",
    "Pixmap",
    "TextPage",
    "Shape",
    "TextWriter",
    "Font",
    "Point",
    "Rect",
    "IRect",
    "Quad",
    "Matrix",
    "Story",
    "DocumentWriter",
    "Archive",
    "DisplayList",
)
COVERED = ("supported", "deviation", "integration")
MODULE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*\*?")
MEMBER_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

JsonObject: TypeAlias = dict[str, object]
Entry: TypeAlias = dict[str, object]


def load_json(path: Path) -> JsonObject:
    """Load one JSON object from ``path``."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        msg = f"{path} must contain a JSON object"
        raise TypeError(msg)
    return cast("JsonObject", value)


def scope_of(key: str) -> str:
    """Return the tracked class that owns ``key``, or ``"module"``."""
    owner, separator, _ = key.partition(".")
    if separator or owner in TRACKED_CLASSES:
        return owner
    return MODULE_SCOPE


def sort_key(key: str) -> tuple[int, str]:
    """Order module entries first, then each tracked class with its members."""
    scope = scope_of(key)
    if scope == MODULE_SCOPE:
        return (0, key)
    rank = TRACKED_CLASSES.index(scope) + 1 if scope in TRACKED_CLASSES else len(TRACKED_CLASSES) + 1
    return (rank, "" if key == scope else key)


def serialize(manifest: JsonObject) -> str:
    """Serialize the manifest canonically: one entry per line in a fixed order."""
    entries = cast("dict[str, Entry]", manifest["entries"])
    keys = sorted(entries, key=sort_key)
    lines = [
        "{",
        f'  "schema": {json.dumps(manifest["schema"])},',
        f'  "pymupdf": {json.dumps(manifest["pymupdf"])},',
        '  "entries": {',
    ]
    for index, key in enumerate(keys):
        body = json.dumps({field: entries[key].get(field) for field in FIELDS}, ensure_ascii=False)
        separator = "," if index < len(keys) - 1 else ""
        lines.append(f"    {json.dumps(key, ensure_ascii=False)}: {body}{separator}")
    lines.extend(["  }", "}"])
    return "\n".join(lines) + "\n"


def public_symbols(snapshot: JsonObject) -> set[str]:
    """Return every top-level and member symbol recorded in the public API snapshot."""
    symbols = cast("dict[str, JsonObject]", snapshot["symbols"])
    names = set(symbols)
    for name, contract in symbols.items():
        member_names: set[str] = set()
        for field in ("members", "annotations", "fields"):
            value = contract.get(field)
            if isinstance(value, dict):
                member_names.update(cast("dict[str, object]", value))
        for field in ("named_tuple_fields", "dataclass_fields"):
            value = contract.get(field)
            if isinstance(value, list):
                member_names.update(str(item) for item in cast("list[object]", value))
        names.update(f"{name}.{member}" for member in member_names)
    return names


def _key_errors(key: str, entries: dict[str, Entry]) -> list[str]:
    owner, separator, member = key.partition(".")
    if not separator:
        return [] if MODULE_NAME.fullmatch(key) else [f"{key}: malformed module name"]
    if owner not in TRACKED_CLASSES:
        return [f"{key}: members are recorded only for tracked classes"]
    if not MEMBER_NAME.fullmatch(member):
        return [f"{key}: malformed member name"]
    if owner not in entries:
        return [f"{key}: the class entry {owner!r} is missing"]
    return []


def _field_errors(key: str, entry: Entry) -> list[str]:
    errors = []
    if set(entry) != set(FIELDS):
        errors.append(f"{key}: fields must be exactly {', '.join(FIELDS)}")
    kinds = MODULE_KINDS if "." not in key else MEMBER_KINDS
    if entry.get("kind") not in kinds:
        errors.append(f"{key}: kind {entry.get('kind')!r} is not one of {', '.join(sorted(kinds))}")
    status = entry.get("status")
    if status is None:
        errors.append(f"{key}: unclassified; set status to one of {', '.join(STATUSES)}")
    elif status not in STATUSES:
        errors.append(f"{key}: unknown status {status!r}")
    if not isinstance(entry.get("pylopdf"), (str, type(None))):
        errors.append(f"{key}: pylopdf must be a symbol name or null")
    milestone = entry.get("milestone")
    if milestone is not None and milestone not in MILESTONES:
        errors.append(f"{key}: unknown milestone {milestone!r}")
    if not isinstance(entry.get("note"), str):
        errors.append(f"{key}: note must be a string")
    return errors


def _status_errors(key: str, entry: Entry, symbols: set[str]) -> list[str]:
    errors = []
    status = entry.get("status")
    symbol = entry.get("pylopdf")
    milestone = entry.get("milestone")
    note = entry.get("note")
    if isinstance(symbol, str) and symbol not in symbols:
        errors.append(f"{key}: pylopdf symbol {symbol!r} is not in api/public-api.json")
    if status in {"supported", "deviation"} and symbol is None:
        errors.append(f"{key}: a {status} entry must name its pylopdf symbol")
    if status == "planned" and milestone is None:
        errors.append(f"{key}: a planned entry must name its milestone")
    if status in {"supported", "integration", "non-goal"} and milestone is not None:
        errors.append(f"{key}: a {status} entry has no milestone")
    if status in {"deviation", "integration", "non-goal"} and not (isinstance(note, str) and note.strip()):
        errors.append(f"{key}: a {status} entry must explain itself in its note")
    return errors


def validate(manifest: JsonObject, symbols: set[str]) -> list[str]:
    """Return every rule violation in ``manifest``."""
    errors = []
    if set(manifest) != {"schema", "pymupdf", "entries"}:
        errors.append("the manifest must have exactly the keys schema, pymupdf, and entries")
    if manifest.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    if not isinstance(manifest.get("pymupdf"), str):
        errors.append("pymupdf must name the inventoried pymupdf version")
    entries = manifest.get("entries")
    if not isinstance(entries, dict) or not entries:
        errors.append("entries must be a non-empty object")
        return errors
    typed_entries = cast("dict[str, Entry]", entries)
    for key, entry in typed_entries.items():
        if not isinstance(entry, dict):
            errors.append(f"{key}: an entry must be an object")
            continue
        errors.extend(_key_errors(key, typed_entries))
        errors.extend(_field_errors(key, entry))
        errors.extend(_status_errors(key, entry, symbols))
    errors.extend(f"{name}: the tracked class has no entry" for name in TRACKED_CLASSES if name not in typed_entries)
    return errors


def coverage_report(manifest: JsonObject) -> str:
    """Return a per-class status table and the planned entries per milestone."""
    entries = cast("dict[str, Entry]", manifest["entries"])
    per_scope: dict[str, Counter[str]] = {}
    milestones: Counter[str] = Counter()
    for key, entry in entries.items():
        status = str(entry["status"])
        per_scope.setdefault(scope_of(key), Counter())[status] += 1
        if status == "planned":
            milestones[str(entry["milestone"])] += 1
    columns = ("total", *STATUSES, "coverage")
    header = f"{'pymupdf ' + str(manifest['pymupdf']):<16}" + "".join(f"{column:>12}" for column in columns)
    lines = [header]
    totals: Counter[str] = Counter()
    for scope in (MODULE_SCOPE, *TRACKED_CLASSES):
        counts = per_scope.get(scope, Counter())
        totals.update(counts)
        lines.append(_coverage_row(scope, counts))
    lines.append(_coverage_row("total", totals))
    lines.append("")
    lines.append("coverage = (supported + deviation + integration) / (total - non-goal)")
    planned = ", ".join(f"{milestone} {milestones[milestone]}" for milestone in MILESTONES if milestones[milestone])
    lines.append(f"planned by milestone: {planned or 'none'}")
    return "\n".join(lines) + "\n"


def _coverage_row(label: str, counts: Counter[str]) -> str:
    total = sum(counts.values())
    in_scope = total - counts["non-goal"]
    covered = sum(counts[status] for status in COVERED)
    coverage = f"{100 * covered / in_scope:.1f}%" if in_scope else "-"
    cells = [str(total), *(str(counts[status]) for status in STATUSES), coverage]
    return f"{label:<16}" + "".join(f"{cell:>12}" for cell in cells)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH, help="parity manifest to check")
    parser.add_argument("--api", type=Path, default=PUBLIC_API_PATH, help="public API snapshot to resolve against")
    parser.add_argument(
        "--format",
        action="store_true",
        help="rewrite a valid manifest in canonical order and formatting",
    )
    return parser.parse_args()


def main() -> int:
    """Check the manifest, print coverage, and return a process exit status."""
    args = _parse_args()
    manifest = load_json(args.manifest)
    errors = validate(manifest, public_symbols(load_json(args.api)))
    if not errors:
        canonical = serialize(manifest)
        if args.format:
            args.manifest.write_text(canonical, encoding="utf-8")
        elif args.manifest.read_text(encoding="utf-8") != canonical:
            errors.append(
                f"{args.manifest} is not canonically formatted; run `uv run python tools/check_parity.py --format`",
            )
    if errors:
        sys.stderr.write(f"{len(errors)} parity manifest error(s):\n")
        sys.stderr.writelines(f"  {error}\n" for error in errors)
        return 1
    sys.stdout.write(coverage_report(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
