"""List pymupdf's public API and compare it with the capability map.

This development tool needs pymupdf, which is AGPL-licensed and comes only from
the ``bench`` dependency group::

    uv sync --group bench
    uv run python tools/pymupdf_inventory.py           # per-class summary
    uv run python tools/pymupdf_inventory.py --json    # every name and its kind
    uv run python tools/pymupdf_inventory.py --check   # compare with the manifest
    uv run python tools/pymupdf_inventory.py --update  # add new names, drop removed ones

CI does not install pymupdf; it runs ``tools/check_parity.py`` instead. The
inventory records names and kinds only, never pymupdf code or documentation
text. ``--update`` adds new names as unclassified entries, which
``tools/check_parity.py`` rejects until someone classifies them.

pymupdf has no ``__all__``, so the inventory takes as public:

- every ``pymupdf`` module attribute whose name has no leading underscore,
  except imported modules, typing constructs, and classes or functions that
  another package defines. Names that start with a prefix in
  ``MODULE_GROUPS`` collapse into one ``PREFIX*`` entry;
- for each tracked class, the attributes its own namespace defines, the
  Python protocol methods in ``PROTOCOL_METHODS``, and the instance
  attributes of a sample object built in memory.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import io
import json
import sys
from collections import Counter
from typing import TYPE_CHECKING, cast

from check_parity import MANIFEST_PATH, SCHEMA, TRACKED_CLASSES, load_json, scope_of, serialize, sort_key

if TYPE_CHECKING:
    import types

    from check_parity import Entry, JsonObject

# Public functions, properties included, that each class body defines in
# pymupdf 1.28.0, as counted on 2026-10-19. The inventory
# also lists aliases, attributes, protocol methods, and instance attributes.
BASELINE_METHOD_COUNTS = {
    "Document": 141,
    "Page": 115,
    "Annot": 53,
    "Pixmap": 40,
    "Rect": 22,
    "Font": 20,
    "Shape": 17,
    "TextPage": 16,
    "Story": 16,
    "Link": 11,
    "Outline": 11,
    "Matrix": 7,
    "Quad": 7,
    "Widget": 5,
    "TextWriter": 5,
    "Archive": 4,
    "DisplayList": 4,
    "DocumentWriter": 3,
}
# Dunder methods that implement Python protocols a caller uses directly:
# containers, context managers, truth testing, and arithmetic operators.
PROTOCOL_METHODS = frozenset(
    {
        "__abs__",
        "__add__",
        "__and__",
        "__bool__",
        "__contains__",
        "__delitem__",
        "__enter__",
        "__exit__",
        "__getitem__",
        "__invert__",
        "__iter__",
        "__len__",
        "__mul__",
        "__neg__",
        "__or__",
        "__pos__",
        "__setitem__",
        "__sub__",
        "__truediv__",
    },
)
# Module attribute families that share one classification. The longest
# matching prefix wins.
MODULE_GROUPS = (
    "CS_",
    "Check",
    "Conversion",
    "EMPTY_",
    "FZ_RECOMPRESS_",
    "FZ_SUBSAMPLE_",
    "INFINITE_",
    "JM_",
    "LINK_FLAG_",
    "MSG_",
    "PDF_ALERT_",
    "PDF_ANNOT_",
    "PDF_ANNOT_IS_",
    "PDF_ANNOT_IT_",
    "PDF_ANNOT_LE_",
    "PDF_ANNOT_Q_",
    "PDF_BM_",
    "PDF_BORDER_",
    "PDF_BTN_FIELD_IS_",
    "PDF_CH_FIELD_IS_",
    "PDF_CLEAN_",
    "PDF_DOCUMENT_EVENT_",
    "PDF_ENCRYPT_",
    "PDF_ENUM_",
    "PDF_FD_",
    "PDF_FIELD_IS_",
    "PDF_LAYER_UI_",
    "PDF_LEXBUF_",
    "PDF_MAX_",
    "PDF_OC_",
    "PDF_PAGE_LABEL_",
    "PDF_PERM_",
    "PDF_REDACT_",
    "PDF_SIGNATURE_",
    "PDF_SIMPLE_ENCODING_",
    "PDF_STRUCT_",
    "PDF_TOK_",
    "PDF_TX_FIELD_IS_",
    "PDF_WIDGET_TX_FORMAT_",
    "PDF_WIDGET_TYPE_",
    "PDF_ZUGFERD_",
    "Py",
    "STAMP_",
    "SigFlag_",
    "TEXTFLAGS_",
    "TEXT_ENCODING_",
    "TEXT_FONT_",
    "TEXT_OUTPUT_",
    "UCDN_SCRIPT_",
    "Version",
    "annot_",
    "colors_",
    "dictkey_",
    "g_",
    "get_env_",
    "jm_",
    "mupdf_",
    "paper_",
    "pymupdf_",
    "recover_",
    "sRGB_to_",
    "swig_",
    "trace_device_",
    "util_",
)
TYPING_MODULES = frozenset({"typing", "typing_extensions", "__future__"})


def _load_pymupdf() -> types.ModuleType:
    try:
        return importlib.import_module("pymupdf")
    except ImportError:
        sys.stderr.write("pymupdf is not installed; run `uv sync --group bench` first.\n")
        raise


def _sample_objects(pymupdf: types.ModuleType) -> dict[str, object]:
    """Build one in-memory instance of every tracked class."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(0, 0, 10, 10), "uri": "https://example.com"})
    page = doc.reload_page(page)
    doc.set_toc([[1, "Title", 1]])
    samples = {
        "Document": doc,
        "Page": page,
        "Annot": page.add_text_annot((20, 20), "note"),
        "Widget": pymupdf.Widget(),
        "Link": page.first_link,
        "Outline": doc.outline,
        "Pixmap": pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1, 1), False),  # noqa: FBT003 - pymupdf API
        "TextPage": page.get_textpage(),
        "Shape": page.new_shape(),
        "TextWriter": pymupdf.TextWriter(page.rect),
        "Font": pymupdf.Font("helv"),
        "Point": pymupdf.Point(1, 2),
        "Rect": pymupdf.Rect(0, 0, 1, 1),
        "IRect": pymupdf.IRect(0, 0, 1, 1),
        "Quad": pymupdf.Rect(0, 0, 1, 1).quad,
        "Matrix": pymupdf.Matrix(1, 0, 0, 1, 0, 0),
        "Story": pymupdf.Story(),
        "DocumentWriter": pymupdf.DocumentWriter(io.BytesIO()),
        "Archive": pymupdf.Archive(),
        "DisplayList": page.get_displaylist(),
    }
    for name, sample in samples.items():
        if type(sample) is not getattr(pymupdf, name):
            msg = f"could not build a sample pymupdf.{name}; got {type(sample).__name__}"
            raise TypeError(msg)
    return samples


def _function_of(value: object) -> object:
    """Return the function behind a method, static or class method, or property."""
    if isinstance(value, (staticmethod, classmethod)):
        return value.__func__
    if isinstance(value, property):
        return value.fget
    return value


def _member_kind(name: str, value: object, originals: dict[int, str]) -> str:
    if name in PROTOCOL_METHODS:
        return "protocol"
    original = originals.get(id(_function_of(value)))
    if original is not None and original != name:
        return "alias"
    if inspect.isclass(value):
        return "class"
    if isinstance(value, (staticmethod, classmethod)) or inspect.isroutine(value):
        return "method"
    if isinstance(value, property):
        return "property"
    return "attribute"


def _class_members(cls: type[object], sample: object) -> tuple[dict[str, str], dict[str, str], int]:
    """Return member kinds, alias targets, and the count of functions the class body defines."""
    namespace = {
        name: value for name, value in vars(cls).items() if not name.startswith("_") or name in PROTOCOL_METHODS
    }
    # A function belongs to the member whose name it carries; any other member
    # that holds the same function or property is an alias.
    originals: dict[int, str] = {}
    for name, value in namespace.items():
        function = _function_of(value)
        if getattr(function, "__name__", None) == name:
            originals[id(function)] = name
    kinds: dict[str, str] = {}
    targets: dict[str, str] = {}
    defined = 0
    for name, value in namespace.items():
        kind = _member_kind(name, value, originals)
        kinds[name] = kind
        if kind == "alias":
            targets[name] = originals[id(_function_of(value))]
        qualname = getattr(_function_of(value), "__qualname__", None)
        if kind in {"method", "property"} and qualname == f"{cls.__name__}.{name}":
            defined += 1
    instance_attributes = getattr(sample, "__dict__", {})
    for name in instance_attributes:
        if not name.startswith("_") and name not in kinds:
            kinds[name] = "attribute"
    return kinds, targets, defined


def _module_kind(name: str, value: object) -> str | None:
    """Return the kind of one module attribute, or None when it is not pymupdf API."""
    if inspect.ismodule(value) or type(value).__module__ in TYPING_MODULES:
        return None
    if inspect.isclass(value) or inspect.isroutine(value):
        module = getattr(value, "__module__", None) or ""
        if module != "pymupdf" and not module.startswith("pymupdf."):
            return None
        if getattr(value, "__name__", name) != name:
            return "alias"
        if inspect.isclass(value):
            return "exception" if issubclass(value, BaseException) else "class"
        return "function"
    return "constant"


def _group_of(name: str) -> str | None:
    prefixes = [prefix for prefix in MODULE_GROUPS if name.startswith(prefix)]
    return f"{max(prefixes, key=len)}*" if prefixes else None


def collect_inventory() -> JsonObject:
    """Collect every public pymupdf API name with its kind."""
    pymupdf = _load_pymupdf()
    entries: dict[str, str] = {}
    groups: dict[str, list[str]] = {}
    for name in sorted(vars(pymupdf)):
        if name.startswith("_"):
            continue
        kind = _module_kind(name, getattr(pymupdf, name))
        if kind is None:
            continue
        group = None if name in TRACKED_CLASSES else _group_of(name)
        if group is None:
            entries[name] = kind
        else:
            entries[group] = "group"
            groups.setdefault(group, []).append(name)
    aliases: dict[str, str] = {}
    defined: dict[str, int] = {}
    samples = _sample_objects(pymupdf)
    for class_name in TRACKED_CLASSES:
        kinds, targets, defined[class_name] = _class_members(getattr(pymupdf, class_name), samples[class_name])
        entries.update({f"{class_name}.{member}": kind for member, kind in kinds.items()})
        aliases.update({f"{class_name}.{member}": f"{class_name}.{target}" for member, target in targets.items()})
    return {
        "pymupdf": str(pymupdf.__version__),
        "entries": dict(sorted(entries.items(), key=lambda item: sort_key(item[0]))),
        "aliases": aliases,
        "groups": groups,
        "defined_methods": defined,
    }


def summary(inventory: JsonObject) -> str:
    """Return per-class counts, with the 2026-10-19 baseline beside them."""
    entries = cast("dict[str, str]", inventory["entries"])
    defined = cast("dict[str, int]", inventory["defined_methods"])
    groups = cast("dict[str, list[str]]", inventory["groups"])
    per_scope: dict[str, Counter[str]] = {}
    for key, kind in entries.items():
        per_scope.setdefault(scope_of(key), Counter())[kind] += 1
    lines = [f"pymupdf {inventory['pymupdf']}: {len(entries)} API entries"]
    module_counts = per_scope.get("module", Counter())
    grouped = sum(len(names) for names in groups.values())
    lines.append(f"  module: {sum(module_counts.values())} entries; {len(groups)} groups cover {grouped} names")
    for class_name in TRACKED_CLASSES:
        counts = per_scope.get(class_name, Counter())
        kinds = ", ".join(f"{kind} {count}" for kind, count in sorted(counts.items()))
        expected = BASELINE_METHOD_COUNTS.get(class_name)
        baseline = "" if expected is None else f" (2026-10-19: {expected})"
        lines.append(
            f"  {class_name}: {sum(counts.values())} entries; {defined[class_name]} methods "
            f"defined in the class body{baseline}; {kinds}",
        )
    return "\n".join(lines) + "\n"


def compare(inventory: JsonObject, manifest: JsonObject) -> tuple[list[str], list[str], list[str]]:
    """Return names missing from the manifest, stale manifest names, and kind changes."""
    current = cast("dict[str, str]", inventory["entries"])
    recorded = cast("dict[str, Entry]", manifest["entries"])
    missing = [key for key in current if key not in recorded]
    stale = [key for key in recorded if key not in current]
    changed = [key for key in current if key in recorded and recorded[key].get("kind") != current[key]]
    return missing, stale, changed


def update(inventory: JsonObject, manifest: JsonObject | None) -> JsonObject:
    """Return the manifest with new names unclassified, removed names dropped, and kinds refreshed."""
    current = cast("dict[str, str]", inventory["entries"])
    recorded = {} if manifest is None else cast("dict[str, Entry]", manifest["entries"])
    entries: dict[str, Entry] = {}
    for key, kind in current.items():
        entry = recorded.get(key) or {"status": None, "relation": None, "pylopdf": None, "milestone": None}
        entries[key] = {**entry, "kind": kind}
    return {"schema": SCHEMA, "pymupdf": inventory["pymupdf"], "entries": entries}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", 1)[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true", help="print the complete inventory as JSON")
    mode.add_argument("--check", action="store_true", help="compare the inventory with the capability map")
    mode.add_argument("--update", action="store_true", help="synchronize the manifest's names with the inventory")
    return parser.parse_args()


def main() -> int:
    """Run the requested inventory action and return a process exit status."""
    args = _parse_args()
    inventory = collect_inventory()
    if args.json:
        sys.stdout.write(json.dumps(inventory, indent=2) + "\n")
        return 0
    if not (args.check or args.update):
        sys.stdout.write(summary(inventory))
        return 0
    manifest = load_json(MANIFEST_PATH) if MANIFEST_PATH.exists() else None
    if args.update:
        updated = update(inventory, manifest)
        MANIFEST_PATH.write_text(serialize(updated), encoding="utf-8")
        added = sum(1 for entry in cast("dict[str, Entry]", updated["entries"]).values() if entry["status"] is None)
        sys.stdout.write(f"Wrote {MANIFEST_PATH} with {added} unclassified entries.\n")
        return 0
    if manifest is None:
        sys.stderr.write(f"{MANIFEST_PATH} does not exist; run with --update first.\n")
        return 1
    if manifest["pymupdf"] != inventory["pymupdf"]:
        sys.stdout.write(
            f"note: the manifest records pymupdf {manifest['pymupdf']}; installed {inventory['pymupdf']}\n"
        )
    missing, stale, changed = compare(inventory, manifest)
    for label, keys in (
        ("missing from the manifest", missing),
        ("no longer in pymupdf", stale),
        ("kind changed", changed),
    ):
        if keys:
            sys.stdout.write(f"{label} ({len(keys)}):\n")
            sys.stdout.writelines(f"  {key}\n" for key in keys)
    if missing or stale or changed:
        return 1
    sys.stdout.write("The capability map lists exactly the installed pymupdf public API.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
