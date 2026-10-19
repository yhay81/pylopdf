"""Validate the pymupdf parity manifest and report coverage per pymupdf class.

``api/pymupdf-parity.json`` holds one entry per pymupdf public API, keyed by
its pymupdf name: ``open`` or ``TEXT_DEHYPHENATE`` for a module attribute,
``PDF_ANNOT_*`` for module attributes grouped by a shared prefix, and
``Document.save`` for a member of one of the tracked classes. Every entry has
these fields:

- ``kind``: what the name is in pymupdf, as ``tools/pymupdf_inventory.py``
  reports it.
- ``status``: ``available`` when pylopdf provides the capability today,
  ``planned`` when a ROADMAP.md foundation or milestone adds it,
  ``integration`` when an ecosystem tool covers it through a tested recipe,
  and ``non-goal`` when the roadmap's non-goals exclude it.
- ``relation``, for available and planned entries: ``same`` when pylopdf
  provides the pymupdf name with pymupdf's meaning, possibly accepting only
  part of its arguments, and ``replaced`` when a different pylopdf API
  provides the capability and pylopdf does not provide the pymupdf name.
  Integration and non-goal entries have ``null``.
- ``pylopdf``: the pylopdf symbol, spelled as in ``api/public-api.json``. An
  available entry names a symbol that exists; a planned ``same`` entry repeats
  the pymupdf name; a planned ``replaced`` entry names the intended API.
- ``milestone``: the ROADMAP.md foundation (``F0``-``F8``) or milestone
  (``M0``-``M8``) that adds or completes the entry, or ``null``.

Optional fields, written only when set:

- ``provisional``: ``true`` on a planned ``replaced`` entry whose pylopdf name
  awaits the F0 API design review.
- ``hint``: one English sentence for a migrating caller, reused verbatim by
  AttributeError hints and the migration guide. Required for ``replaced`` and
  ``integration`` entries.
- ``reset``: one English sentence on what milestone M0 changes in an existing
  pylopdf API so that it matches pymupdf; the entry's milestone is then
  ``M0``. An available ``same`` entry whose pylopdf name differs from the
  pymupdf name needs one.
- ``note``: a maintainer note, such as unsupported arguments or the reason for
  a non-goal. A non-goal needs a hint or a note.

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

SCHEMA = 2
STATUSES = ("available", "planned", "integration", "non-goal")
RELATIONS = ("same", "replaced")
MILESTONES = tuple(f"F{number}" for number in range(9)) + tuple(f"M{number}" for number in range(9))
RESET_MILESTONE = "M0"
REQUIRED_FIELDS = ("kind", "status", "relation", "pylopdf", "milestone")
OPTIONAL_FIELDS = ("provisional", "hint", "reset", "note")
FIELDS = ("kind", "status", "relation", "pylopdf", "provisional", "milestone", "hint", "reset", "note")
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
COLUMNS = (
    ("available", "same"),
    ("available", "replaced"),
    ("planned", "same"),
    ("planned", "replaced"),
    ("integration", None),
    ("non-goal", None),
)
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
    """Serialize the manifest canonically: one entry per line, fields in a fixed order."""
    entries = cast("dict[str, Entry]", manifest["entries"])
    keys = sorted(entries, key=sort_key)
    lines = [
        "{",
        f'  "schema": {json.dumps(manifest["schema"])},',
        f'  "pymupdf": {json.dumps(manifest["pymupdf"])},',
        '  "entries": {',
    ]
    for index, key in enumerate(keys):
        entry = entries[key]
        ordered = {field: entry[field] for field in FIELDS if field in entry}
        ordered.update({field: value for field, value in entry.items() if field not in FIELDS})
        body = json.dumps(ordered, ensure_ascii=False)
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


def same_name(key: str, entry: Entry) -> bool:
    """Return whether the pylopdf symbol carries the pymupdf name.

    A protocol method such as ``Rect.__len__`` that a pylopdf class inherits,
    for example from ``tuple``, resolves to the class itself.
    """
    symbol = entry.get("pylopdf")
    return symbol == key or (entry.get("kind") == "protocol" and symbol == key.partition(".")[0])


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
    errors = [f"{key}: missing field {field!r}" for field in REQUIRED_FIELDS if field not in entry]
    errors.extend(f"{key}: unknown field {field!r}" for field in entry if field not in FIELDS)
    kinds = MODULE_KINDS if "." not in key else MEMBER_KINDS
    if entry.get("kind") not in kinds:
        errors.append(f"{key}: kind {entry.get('kind')!r} is not one of {', '.join(sorted(kinds))}")
    status = entry.get("status")
    if status is None:
        errors.append(f"{key}: unclassified; set status to one of {', '.join(STATUSES)}")
    elif status not in STATUSES:
        errors.append(f"{key}: unknown status {status!r}")
    if entry.get("relation") not in (*RELATIONS, None):
        errors.append(f"{key}: unknown relation {entry.get('relation')!r}")
    if not isinstance(entry.get("pylopdf"), (str, type(None))):
        errors.append(f"{key}: pylopdf must be a symbol name or null")
    milestone = entry.get("milestone")
    if milestone is not None and milestone not in MILESTONES:
        errors.append(f"{key}: unknown milestone {milestone!r}")
    if "provisional" in entry and entry["provisional"] is not True:
        errors.append(f"{key}: provisional is written only as true")
    errors.extend(
        f"{key}: {field} must be a non-empty string"
        for field in ("hint", "reset", "note")
        if field in entry and not (isinstance(entry[field], str) and str(entry[field]).strip())
    )
    return errors


def _status_errors(key: str, entry: Entry, symbols: set[str]) -> list[str]:  # noqa: C901 - one rule per branch.
    errors = []
    status = entry.get("status")
    relation = entry.get("relation")
    symbol = entry.get("pylopdf")
    milestone = entry.get("milestone")
    if status in {"available", "planned"} and relation is None:
        errors.append(f"{key}: an {status} entry needs relation same or replaced")
    if status in {"integration", "non-goal"} and relation is not None:
        errors.append(f"{key}: a {status} entry has no relation")
    if status in {"available", "planned"} and symbol is None:
        errors.append(f"{key}: an {status} entry must name its pylopdf symbol")
    if status == "available" and isinstance(symbol, str) and symbol not in symbols:
        errors.append(f"{key}: pylopdf symbol {symbol!r} is not in api/public-api.json")
    if status == "available" and relation == "same" and not same_name(key, entry) and "reset" not in entry:
        errors.append(f"{key}: a same entry named {symbol!r} needs a reset that gives it the pymupdf name")
    if status == "planned" and relation == "same" and symbol != key:
        errors.append(f"{key}: a planned same entry repeats the pymupdf name as its pylopdf symbol")
    if status == "planned" and milestone is None:
        errors.append(f"{key}: a planned entry must name its milestone")
    if status in {"integration", "non-goal"} and milestone is not None:
        errors.append(f"{key}: a {status} entry has no milestone")
    if "reset" in entry and milestone != RESET_MILESTONE:
        errors.append(f"{key}: an entry with a reset belongs to milestone {RESET_MILESTONE}")
    if "provisional" in entry and (status, relation) != ("planned", "replaced"):
        errors.append(f"{key}: only a planned replaced entry can be provisional")
    if (relation == "replaced" or status == "integration") and "hint" not in entry:
        errors.append(f"{key}: a {relation or status} entry needs a hint for migrating callers")
    if status == "non-goal" and "hint" not in entry and "note" not in entry:
        errors.append(f"{key}: a non-goal entry needs a hint or a note that gives its reason")
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


def _column(entry: Entry) -> tuple[str, str | None]:
    return (str(entry["status"]), cast("str | None", entry["relation"]))


def coverage_report(manifest: JsonObject) -> str:
    """Return status and relation counts per class, provisional names, and the M0 resets."""
    entries = cast("dict[str, Entry]", manifest["entries"])
    per_scope: dict[str, Counter[tuple[str, str | None]]] = {}
    milestones: Counter[str] = Counter()
    for key, entry in entries.items():
        per_scope.setdefault(scope_of(key), Counter())[_column(entry)] += 1
        if entry["status"] == "planned":
            milestones[str(entry["milestone"])] += 1
    labels = ("total", "avail same", "avail repl", "plan same", "plan repl", "integration", "non-goal", "coverage")
    lines = [f"pymupdf {manifest['pymupdf']} parity, schema {manifest['schema']}: {len(entries)} entries", ""]
    lines.append(f"{'':<16}" + "".join(f"{label:>12}" for label in labels))
    totals: Counter[tuple[str, str | None]] = Counter()
    for scope in (MODULE_SCOPE, *TRACKED_CLASSES):
        counts = per_scope.get(scope, Counter())
        totals.update(counts)
        lines.append(_coverage_row(scope, counts))
    lines.append(_coverage_row("total", totals))
    lines.append("coverage = (available + integration) / (total - non-goal)")
    lines.append("")
    planned = ", ".join(f"{milestone} {milestones[milestone]}" for milestone in MILESTONES if milestones[milestone])
    lines.append(f"planned by milestone: {planned or 'none'}")
    provisional = sum(1 for entry in entries.values() if entry.get("provisional"))
    lines.append(f"provisional pylopdf names awaiting the F0 review: {provisional}")
    # Entries that one change touches share its sentence, so each change is listed once.
    changes: dict[str, list[str]] = {}
    for key in sorted((key for key, entry in entries.items() if "reset" in entry), key=sort_key):
        changes.setdefault(str(entries[key]["reset"]), []).append(key)
    lines.append("")
    lines.append(f"M0 resets: {len(changes)} changes over {sum(len(keys) for keys in changes.values())} entries")
    for reset, keys in changes.items():
        lines.append(f"- {reset}")
        for key in keys:
            entry = entries[key]
            tense = "now" if entry["status"] == "available" else "becomes"
            symbol = "" if same_name(key, entry) else f" [{tense} {entry['pylopdf']}]"
            lines.append(f"    {key}{symbol}")
    return "\n".join(lines) + "\n"


def _coverage_row(label: str, counts: Counter[tuple[str, str | None]]) -> str:
    total = sum(counts.values())
    in_scope = total - counts[("non-goal", None)]
    covered = counts[("available", "same")] + counts[("available", "replaced")] + counts[("integration", None)]
    coverage = f"{100 * covered / in_scope:.1f}%" if in_scope else "-"
    cells = [str(total), *(str(counts[column]) for column in COLUMNS), coverage]
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
