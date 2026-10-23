"""Validate the pymupdf capability map and report coverage per pymupdf class.

``api/pymupdf-parity.json`` records, for every public name of pymupdf, how
pylopdf covers the capability behind it, keyed by the pymupdf name: ``open``
or ``TEXT_DEHYPHENATE`` for a module attribute, ``PDF_ANNOT_*`` for module
attributes grouped by a shared prefix, and ``Document.save`` for a member of
one of the tracked classes. Every entry has these fields:

- ``kind``: what the name is in pymupdf, as ``tools/pymupdf_inventory.py``
  reports it.
- ``status``: ``available`` when pylopdf provides the capability today,
  ``planned`` when a ROADMAP.md milestone adds it, ``integration`` when an
  ecosystem tool covers it through a tested recipe, and ``non-goal`` when it
  is outside the roadmap's scope or an internal of pymupdf without a
  capability at the PDF level.
- ``relation``, for available entries only: ``same`` when pylopdf provides
  the capability under the pymupdf name, and ``replaced`` when a different
  pylopdf API provides it. Other entries have ``null``.
- ``pylopdf``: for an available entry, the pylopdf symbol, spelled as in
  ``api/public-api.json``; otherwise ``null``, because a milestone designs
  the API when it starts.
- ``milestone``: the ROADMAP.md milestone that adds a planned capability or
  completes an available one, as one of the slugs in ``MILESTONES``, or
  ``null``.

Optional fields, written only when set:

- ``hint``: one English sentence for a migrating caller, reused verbatim by
  AttributeError hints and the migration guide. Required for ``replaced`` and
  ``integration`` entries.
- ``note``: a maintainer note, such as unsupported arguments or the reason for
  a non-goal. A non-goal needs a hint or a note.

The map records API names and statuses only. Never copy pymupdf code or
documentation text, which is AGPL-licensed.
"""

from __future__ import annotations

import argparse
import builtins
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import TypeAlias, cast

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "api" / "pymupdf-parity.json"
PUBLIC_API_PATH = ROOT / "api" / "public-api.json"

SCHEMA = 3
STATUSES = ("available", "planned", "integration", "non-goal")
RELATIONS = ("same", "replaced")
# ROADMAP.md milestone slugs and the headings they stand for.
MILESTONES = {
    "content": "Content model",
    "canvas": "Canvas",
    "reset": "API reset",
    "verification": "Verification",
    "reading": "Reading",
    "rendering": "Rendering",
    "writing": "Writing",
    "interaction": "Interaction",
    "rewriting": "Rewriting",
    "structure": "Structure",
    "persistence": "Persistence",
    "conformance": "Conformance",
    "understanding": "Understanding",
    "completion": "Specification completion",
}
REQUIRED_FIELDS = ("kind", "status", "relation", "pylopdf", "milestone")
OPTIONAL_FIELDS = ("hint", "note")
FIELDS = ("kind", "status", "relation", "pylopdf", "milestone", "hint", "note")
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
    ("planned", None),
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
    errors.extend(
        f"{key}: {field} must be a non-empty string"
        for field in OPTIONAL_FIELDS
        if field in entry and not (isinstance(entry[field], str) and str(entry[field]).strip())
    )
    return errors


def _symbol_exists(symbol: str, symbols: set[str]) -> bool:
    """Return whether a symbol, or every name of a ``PREFIX*`` group, is public.

    A built-in exception, such as ``FileNotFoundError``, counts as public because
    pylopdf raises it in place of a pymupdf class of the same name.
    """
    if isinstance(getattr(builtins, symbol, None), type) and issubclass(getattr(builtins, symbol), BaseException):
        return True
    if symbol.endswith("*"):
        return any(name.startswith(symbol[:-1]) for name in symbols if "." not in name)
    return symbol in symbols


def _status_errors(key: str, entry: Entry, symbols: set[str]) -> list[str]:  # noqa: C901 - one rule per branch.
    errors = []
    status = entry.get("status")
    relation = entry.get("relation")
    symbol = entry.get("pylopdf")
    milestone = entry.get("milestone")
    if status == "available":
        if relation is None:
            errors.append(f"{key}: an available entry needs relation same or replaced")
        if symbol is None:
            errors.append(f"{key}: an available entry must name its pylopdf symbol")
        elif isinstance(symbol, str) and not _symbol_exists(symbol, symbols):
            errors.append(f"{key}: pylopdf symbol {symbol!r} is not in api/public-api.json")
        if relation == "same" and symbol is not None and not same_name(key, entry):
            errors.append(f"{key}: a same entry names {symbol!r} instead of the pymupdf name")
    elif status is not None:
        if relation is not None:
            errors.append(f"{key}: only an available entry has a relation")
        if symbol is not None:
            errors.append(f"{key}: only an available entry names a pylopdf symbol")
    if status == "planned" and milestone is None:
        errors.append(f"{key}: a planned entry must name its milestone")
    if status in {"integration", "non-goal"} and milestone is not None:
        errors.append(f"{key}: a {status} entry has no milestone")
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
    """Return status and relation counts per class and the planned capabilities per milestone."""
    entries = cast("dict[str, Entry]", manifest["entries"])
    per_scope: dict[str, Counter[tuple[str, str | None]]] = {}
    milestones: Counter[str] = Counter()
    for key, entry in entries.items():
        per_scope.setdefault(scope_of(key), Counter())[_column(entry)] += 1
        if entry["status"] == "planned":
            milestones[str(entry["milestone"])] += 1
    labels = ("total", "avail same", "avail repl", "planned", "integration", "non-goal", "coverage")
    lines = [f"pymupdf {manifest['pymupdf']} capability map, schema {manifest['schema']}: {len(entries)} entries", ""]
    lines.append(f"{'':<16}" + "".join(f"{label:>12}" for label in labels))
    totals: Counter[tuple[str, str | None]] = Counter()
    for scope in (MODULE_SCOPE, *TRACKED_CLASSES):
        counts = per_scope.get(scope, Counter())
        totals.update(counts)
        lines.append(_coverage_row(scope, counts))
    lines.append(_coverage_row("total", totals))
    lines.append("coverage = (available + integration) / (total - non-goal)")
    lines.append("")
    planned = ", ".join(f"{title} {milestones[slug]}" for slug, title in MILESTONES.items() if milestones[slug])
    lines.append(f"planned by milestone: {planned or 'none'}")
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
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH, help="capability map to check")
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
        sys.stderr.write(f"{len(errors)} capability map error(s):\n")
        sys.stderr.writelines(f"  {error}\n" for error in errors)
        return 1
    sys.stdout.write(coverage_report(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
