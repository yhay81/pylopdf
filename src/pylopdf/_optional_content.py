"""Optional content groups and membership dictionaries (ISO 32000-2 §8.11).

The functions here back :class:`Document`'s pymupdf-named optional-content
methods. They read and write the object graph through the low-level object
model, so every write invalidates rendering and extraction caches like any
other object edit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from pylopdf._objects import Array, Dictionary, Name, Stream, String

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pylopdf._document import Document
    from pylopdf._types import OcgInfo, OcmdInfo, VisibilityExpression

#: pymupdf's spelling of the four ``/P`` policies, keyed by lower case.
_POLICIES = {"anyon": "AnyOn", "allon": "AllOn", "anyoff": "AnyOff", "alloff": "AllOff"}

#: Visibility-expression operators, keyed by pymupdf's lower-case spelling.
_OPERATORS = {"and": "And", "or": "Or", "not": "Not"}

#: Nesting of visibility expressions that reads and writes accept.
_MAX_EXPRESSION_DEPTH = 32

#: ``/Parent`` levels that the inherited ``/Resources`` lookup follows.
_MAX_PARENT_DEPTH = 256


def _name(value: object) -> str | None:
    """Return the text of a name value, or ``None``."""
    return str(value) if isinstance(value, Name) else None


def _text(value: object) -> str:
    """Return the text of a string value, or ``""``."""
    return value.text if isinstance(value, String) else ""


def _identity(value: object) -> tuple[int, int] | None:
    """Return the ``(number, generation)`` of an indirect handle."""
    if isinstance(value, (Array, Dictionary)) and value.is_indirect:
        return (value.xref or 0, value.generation or 0)
    return None


def _properties(document: Document) -> Dictionary | None:
    """Return the catalog's ``/OCProperties`` dictionary."""
    properties = document.catalog.get("OCProperties")
    return properties if isinstance(properties, Dictionary) else None


def _references(value: object) -> list[tuple[int, int]]:
    """Return the identities of the indirect dictionaries in an array."""
    if not isinstance(value, Array):
        return []
    return [identity for item in value if (identity := _identity(item)) is not None]


def _inactive(properties: Dictionary, groups: list[tuple[int, int]]) -> set[tuple[int, int]]:
    """Return the groups that the default configuration turns off, as hayro reads it."""
    config = properties.get("D")
    if not isinstance(config, Dictionary):
        return set()
    inactive = set(groups) if _name(config.get("BaseState")) == "OFF" else set()
    inactive.difference_update(_references(config.get("ON")))
    inactive.update(_references(config.get("OFF")))
    return inactive


def get_ocgs(document: Document) -> dict[int, OcgInfo]:
    """Describe every group that ``/OCProperties /OCGs`` lists."""
    properties = _properties(document)
    if properties is None:
        return {}
    groups = properties.get("OCGs")
    if not isinstance(groups, Array):
        return {}
    identities = _references(groups)
    inactive = _inactive(properties, identities)
    result: dict[int, OcgInfo] = {}
    for group in groups:
        identity = _identity(group)
        if identity is None or not isinstance(group, Dictionary):
            continue
        intent = group.get("Intent")
        if isinstance(intent, Array):
            intents = [text for item in intent if (text := _name(item)) is not None]
        else:
            intents = [] if (text := _name(intent)) is None else [text]
        usage = group.get("Usage")
        creator = usage.get("CreatorInfo") if isinstance(usage, Dictionary) else None
        subtype = _name(creator.get("Subtype")) if isinstance(creator, Dictionary) else None
        result[identity[0]] = {
            "name": _text(group.get("Name")),
            "intent": intents,
            "on": identity not in inactive,
            "usage": subtype,
        }
    return result


def _array(container: Dictionary, key: str) -> Array:
    """Return the array under ``key``, writing an empty one when there is none."""
    value = container.get(key)
    if not isinstance(value, Array):
        container[key] = []
        value = container[key]
    return value


def _intents(intent: str | Sequence[str] | None) -> list[str]:
    """Return the ``/Intent`` names that ``add_ocg`` writes."""
    if intent is None or intent == "":
        return ["View"]
    if isinstance(intent, str):
        return [intent]
    intents = list(intent)
    if not all(isinstance(item, str) and item for item in intents):
        msg = f"intent must be a name or a sequence of names: {intent!r}"
        raise TypeError(msg)
    return intents


def add_ocg(  # noqa: PLR0913 - pymupdf's parameters.
    document: Document,
    name: str,
    *,
    config: int,
    on: object,
    intent: str | Sequence[str] | None,
    usage: str | None,
) -> int:
    """Create a group, list it, and return its object number."""
    if not isinstance(name, str):
        msg = f"name must be a str: {name!r}"
        raise TypeError(msg)
    intents = _intents(intent)
    if usage is not None and not isinstance(usage, str):
        msg = f"usage must be a str: {usage!r}"
        raise TypeError(msg)
    if not isinstance(config, int) or isinstance(config, bool):
        msg = f"config must be an int: {config!r}"
        raise TypeError(msg)
    properties = _properties(document)
    if config > -1:
        configs = properties.get("Configs") if properties is not None else None
        if not isinstance(configs, Array) or config >= len(configs) or not isinstance(configs[config], Dictionary):
            msg = "bad config number"
            raise ValueError(msg)
    number = document.objects.add(
        {
            "Type": Name("OCG"),
            "Name": name,
            "Intent": [Name(item) for item in intents],
            "Usage": {"CreatorInfo": {"Creator": "pylopdf", "Subtype": Name(usage or "Artwork")}},
        }
    )
    group = document.objects[number]
    if properties is None:
        document.catalog["OCProperties"] = {"OCGs": [], "D": {"ON": [], "OFF": [], "Order": [], "RBGroups": []}}
        properties = document.catalog["OCProperties"]
    _array(properties, "OCGs").append(group)
    if config > -1:
        target = properties["Configs"][config]
    else:
        target = properties.get("D")
        if not isinstance(target, Dictionary):
            properties["D"] = {}
            target = properties["D"]
    _array(target, "Order").append(group)
    _array(target, "ON" if on else "OFF").append(group)
    return number


def _xobject(document: Document, xref: int) -> Dictionary:
    """Return the image or Form XObject stored under ``xref``."""
    value = document.objects.get(xref) if isinstance(xref, int) and not isinstance(xref, bool) else None
    if not isinstance(value, Dictionary) or _name(value.get("Subtype")) not in {"Image", "Form"}:
        msg = f"bad object type at xref {xref}"
        raise ValueError(msg)
    return value


def _typed(document: Document, xref: object, kind: set[str]) -> Dictionary | None:
    """Return the dictionary under ``xref`` when its ``/Type`` is one of ``kind``."""
    if not isinstance(xref, int) or isinstance(xref, bool) or xref <= 0:
        return None
    value = document.objects.get(xref)
    if isinstance(value, Dictionary) and not isinstance(value, Stream) and _name(value.get("Type")) in kind:
        return value
    return None


def oc_reference(dictionary: Dictionary) -> int:
    """Return the object number that ``/OC`` refers to, or 0."""
    value = dictionary.get("OC")
    identity = _identity(value)
    return 0 if identity is None else identity[0]


def get_oc(document: Document, xref: int) -> int:
    """Return the group or membership dictionary of an image or Form XObject."""
    return oc_reference(_xobject(document, xref))


def set_oc(document: Document, xref: int, oc: int) -> None:
    """Attach the group or membership dictionary ``oc`` to an XObject, or detach with 0."""
    xobject = _xobject(document, xref)
    if oc == 0:
        if "OC" in xobject:
            del xobject["OC"]
        return
    target = _typed(document, oc, {"OCG", "OCMD"})
    if target is None:
        msg = f"bad object type at xref {oc}"
        raise ValueError(msg)
    xobject["OC"] = target


def _read_expression(value: object, depth: int) -> VisibilityExpression | int | None:
    """Return pymupdf's list form of a visibility expression."""
    identity = _identity(value)
    if identity is not None and isinstance(value, Dictionary):
        return identity[0]
    if not isinstance(value, Array) or depth > _MAX_EXPRESSION_DEPTH or len(value) == 0:
        return None
    operator = _name(value[0])
    if operator is None:
        return None
    result: VisibilityExpression = [operator.lower()]
    for item in value[1:]:
        operand = _read_expression(item, depth + 1)
        if operand is not None:
            result.append(operand)
    return result


def get_ocmd(document: Document, xref: int) -> OcmdInfo:
    """Describe the membership dictionary under ``xref``."""
    if not isinstance(xref, int) or isinstance(xref, bool) or xref not in document.objects:
        msg = "bad xref"
        raise ValueError(msg)
    ocmd = _typed(document, xref, {"OCMD"})
    if ocmd is None:
        msg = "bad object type"
        raise ValueError(msg)
    groups = ocmd.get("OCGs")
    if isinstance(groups, Array):
        ocgs: list[int] | None = [identity[0] for identity in _references(groups)]
    elif (identity := _identity(groups)) is not None:
        ocgs = [identity[0]]
    else:
        ocgs = None
    expression = ocmd.get("VE")
    ve = cast("VisibilityExpression | None", _read_expression(expression, 1)) if isinstance(expression, Array) else None
    return {"xref": xref, "ocgs": ocgs, "policy": _name(ocmd.get("P")), "ve": ve}


def _write_expression(document: Document, ve: object, groups: set[int], depth: int) -> list[Any]:
    """Validate pymupdf's list form of a visibility expression and return its PDF array."""
    if not isinstance(ve, (list, tuple)) or len(ve) < 2 or not isinstance(ve[0], str):  # noqa: PLR2004 - an operator and an operand.
        msg = f"bad 've' format: {ve}"
        raise ValueError(msg)
    if depth > _MAX_EXPRESSION_DEPTH:
        msg = f"'ve' nests deeper than {_MAX_EXPRESSION_DEPTH} levels"
        raise ValueError(msg)
    operator = _OPERATORS.get(ve[0].lower())
    if operator is None:
        msg = f"bad operand: {ve[0]}"
        raise ValueError(msg)
    if operator == "Not" and len(ve) != 2:  # noqa: PLR2004 - /Not takes one operand.
        msg = f"bad 've' format: {ve}"
        raise ValueError(msg)
    result: list[Any] = [Name(operator)]
    for item in ve[1:]:
        if isinstance(item, int) and not isinstance(item, bool):
            if item not in groups:
                msg = f"bad OCG {item}"
                raise ValueError(msg)
            result.append(document.objects[item])
        else:
            result.append(_write_expression(document, item, groups, depth + 1))
    return result


def set_ocmd(
    document: Document,
    xref: int,
    ocgs: Sequence[int] | None,
    policy: str | None,
    ve: VisibilityExpression | None,
) -> int:
    """Create or replace a membership dictionary and return its object number."""
    groups = set(get_ocgs(document))
    ocmd: dict[str, Any] = {"Type": Name("OCMD")}
    if ocgs:
        unknown = set(ocgs).difference(groups)
        if unknown:
            msg = f"bad OCGs: {unknown}"
            raise ValueError(msg)
        ocmd["OCGs"] = [document.objects[group] for group in ocgs]
    if policy:
        spelled = _POLICIES.get(str(policy).lower())
        if spelled is None:
            msg = f"bad policy: {policy}"
            raise ValueError(msg)
        ocmd["P"] = Name(spelled)
    if ve:
        ocmd["VE"] = _write_expression(document, ve, groups, 1)
    if xref == 0:
        return document.objects.add(ocmd)
    if _typed(document, xref, {"OCMD"}) is None:
        msg = "bad xref or not an OCMD"
        raise ValueError(msg)
    document.objects[xref] = ocmd
    return xref


def page_oc_items(page: Dictionary) -> list[tuple[str, int, str]]:
    """List the groups and membership dictionaries in a page's ``/Resources /Properties``."""
    resources = None
    node: Any = page
    seen: set[tuple[int, int] | None] = set()
    for _ in range(_MAX_PARENT_DEPTH):
        if not isinstance(node, Dictionary) or _identity(node) in seen:
            break
        seen.add(_identity(node))
        resources = node.get("Resources")
        if resources is not None:
            break
        node = node.get("Parent")
    if not isinstance(resources, Dictionary):
        return []
    properties = resources.get("Properties")
    if not isinstance(properties, Dictionary):
        return []
    items = []
    for name in properties:
        value = properties[name]
        identity = _identity(value)
        if identity is None or not isinstance(value, Dictionary):
            continue
        kind = _name(value.get("Type"))
        if kind in {"OCG", "OCMD"}:
            items.append((str(name), identity[0], kind.lower()))
    return items
