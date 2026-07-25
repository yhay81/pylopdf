"""Detect unreviewed changes to pylopdf's documented public API surface."""

from __future__ import annotations

import argparse
import difflib
import enum
import inspect
import json
import sys
import types
import typing
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ForwardRef, TypeAlias, cast, get_args, get_origin, is_typeddict

import pylopdf

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = ROOT / "api" / "public-api.json"
PUBLIC_DUNDERS = frozenset({"__enter__", "__exit__", "__getitem__", "__iter__", "__len__"})

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


def _public_type_name(value: type[object]) -> str:
    """Return a stable public spelling for one runtime type."""
    if value.__module__.startswith("pylopdf"):
        return f"pylopdf.{value.__name__}"
    if value.__module__ == "builtins":
        return value.__qualname__
    return f"{value.__module__}.{value.__qualname__}"


def _atomic_type_expression(value: object) -> str | None:
    """Normalize a non-parameterized annotation when possible."""
    result = None
    if isinstance(value, str):
        result = value
    elif isinstance(value, ForwardRef):
        result = value.__forward_arg__
    elif value is None or value is type(None):
        result = "None"
    elif value is Ellipsis:
        result = "..."
    elif value is typing.Any:
        result = "Any"
    elif isinstance(value, type):
        result = _public_type_name(value)
    return result


def _type_expression(value: object) -> str:
    """Normalize one annotation or runtime type alias across supported Python versions."""
    if value is inspect.Parameter.empty or value is inspect.Signature.empty:
        msg = "empty annotations do not have a type expression"
        raise ValueError(msg)

    origin = get_origin(value)
    arguments = get_args(value)
    if origin is typing.Literal:
        rendered = ", ".join(repr(argument) for argument in arguments)
        return f"Literal[{rendered}]"
    if origin in {typing.Union, types.UnionType}:
        return " | ".join(_type_expression(argument) for argument in arguments)
    if origin is not None:
        origin_name = _public_type_name(origin) if isinstance(origin, type) else str(origin).removeprefix("typing.")
        rendered = ", ".join(_type_expression(argument) for argument in arguments)
        return f"{origin_name}[{rendered}]"

    atomic = _atomic_type_expression(value)
    if atomic is not None:
        return atomic

    return str(value).removeprefix("typing.")


def _json_default(value: object) -> JsonValue:
    """Normalize a callable default without memory addresses or interpreter-specific reprs."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, enum.Enum):
        return {
            "enum": f"{type(value).__name__}.{value.name}",
            "value": _json_default(value.value),
        }
    if isinstance(value, tuple):
        return [_json_default(item) for item in value]
    if isinstance(value, type):
        return {"type": _public_type_name(value)}
    return {"repr": repr(value)}


def _signature(value: object) -> JsonObject | None:
    """Return a structured callable signature, or None when the runtime hides it."""
    try:
        signature = inspect.signature(cast("Callable[..., object]", value))
    except (TypeError, ValueError):
        return None

    parameters: list[JsonValue] = []
    for parameter in signature.parameters.values():
        entry: JsonObject = {
            "kind": parameter.kind.name.lower(),
            "name": parameter.name,
        }
        if parameter.annotation is not inspect.Parameter.empty:
            entry["annotation"] = _type_expression(parameter.annotation)
        if parameter.default is not inspect.Parameter.empty:
            entry["default"] = _json_default(parameter.default)
        parameters.append(entry)

    result: JsonObject = {"parameters": parameters}
    if signature.return_annotation is not inspect.Signature.empty:
        result["returns"] = _type_expression(signature.return_annotation)
    return result


def _property_contract(value: property) -> JsonObject:
    """Return the observable contract of one Python property."""
    result: JsonObject = {"kind": "property"}
    if value.fget is not None:
        signature = _signature(value.fget)
        if signature is not None and "returns" in signature:
            result["returns"] = signature["returns"]
    return result


def _member_contract(owner: type[object], name: str, raw_value: object) -> JsonObject:
    """Describe one public class member."""
    if isinstance(raw_value, property):
        return _property_contract(raw_value)
    if isinstance(raw_value, classmethod):
        return {
            "kind": "classmethod",
            "signature": _signature(getattr(owner, name)),
        }
    if isinstance(raw_value, staticmethod):
        return {
            "kind": "staticmethod",
            "signature": _signature(getattr(owner, name)),
        }
    if inspect.isroutine(raw_value):
        return {
            "kind": "method",
            "signature": _signature(getattr(owner, name)),
        }
    if inspect.isdatadescriptor(raw_value):
        return {"kind": "property"}
    return {
        "kind": "attribute",
        "value": _json_default(raw_value),
    }


def _class_members(value: type[object]) -> JsonObject:
    """Collect class-owned public members plus supported protocol methods."""
    members: JsonObject = {}
    for name, raw_value in sorted(vars(value).items()):
        if name.startswith("_") and name not in PUBLIC_DUNDERS:
            continue
        members[name] = _member_contract(value, name, raw_value)
    return members


def _class_annotations(value: type[object]) -> JsonObject:
    """Collect stable class-level annotations."""
    annotations = getattr(value, "__annotations__", {})
    return {name: _type_expression(annotation) for name, annotation in sorted(annotations.items())}


def _typed_dict_contract(value: type[object]) -> JsonObject:
    """Describe required, optional, and typed mapping keys."""
    return {
        "fields": _class_annotations(value),
        "kind": "typed-dict",
        "optional_keys": sorted(value.__optional_keys__),  # type: ignore[attr-defined]
        "required_keys": sorted(value.__required_keys__),  # type: ignore[attr-defined]
    }


def _enum_contract(value: type[enum.Enum]) -> JsonObject:
    """Describe an exported enumeration and all member values."""
    return {
        "kind": "enum",
        "members": {name: _json_default(member.value) for name, member in value.__members__.items()},
    }


def _exception_contract(value: type[BaseException]) -> JsonObject:
    """Describe an exported exception's direct inheritance and public attributes."""
    return {
        "bases": [_public_type_name(base) for base in value.__bases__],
        "kind": "exception",
        "members": _class_members(value),
    }


def _class_contract(value: type[object]) -> JsonObject:
    """Describe one ordinary, dataclass, or named-tuple public class."""
    if is_typeddict(value):
        return _typed_dict_contract(value)
    if issubclass(value, enum.Enum):
        return _enum_contract(value)
    if issubclass(value, BaseException):
        return _exception_contract(value)

    result: JsonObject = {
        "kind": "class",
        "members": _class_members(value),
        "signature": _signature(value),
    }
    annotations = _class_annotations(value)
    if annotations:
        result["annotations"] = annotations
    if is_dataclass(value):
        result["dataclass_fields"] = [field.name for field in fields(value)]
    named_tuple_fields = getattr(value, "_fields", None)
    if isinstance(named_tuple_fields, tuple):
        result["named_tuple_fields"] = list(named_tuple_fields)
    return result


def _export_contract(name: str, value: object) -> JsonObject:
    """Describe one name exported through pylopdf.__all__."""
    annotation = pylopdf.__annotations__.get(name)
    if annotation == "TypeAlias" or annotation is TypeAlias:
        return {
            "kind": "type-alias",
            "target": _type_expression(value),
        }
    if inspect.isclass(value):
        return _class_contract(value)
    if inspect.isfunction(value) or inspect.isbuiltin(value):
        return {
            "kind": "function",
            "signature": _signature(value),
        }
    return {
        "kind": "constant",
        "value": _json_default(value),
    }


def collect_public_api() -> JsonObject:
    """Collect the review baseline for pylopdf's documented public API."""
    exports = sorted(pylopdf.__all__)
    export_values: list[JsonValue] = []
    export_values.extend(exports)
    version_parts = pylopdf.__version__.split(".")
    return {
        "api_line": ".".join(version_parts[:2]),
        "exports": export_values,
        "schema": 1,
        "symbols": {name: _export_contract(name, getattr(pylopdf, name)) for name in exports},
    }


def _serialized(value: JsonObject) -> str:
    """Serialize a snapshot deterministically."""
    return f"{json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)}\n"


def load_snapshot() -> JsonObject:
    """Load the checked-in public API snapshot."""
    value = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        msg = f"public API snapshot must be a JSON object: {SNAPSHOT_PATH}"
        raise TypeError(msg)
    return typing.cast("JsonObject", value)


def snapshot_diff(expected: JsonObject, actual: JsonObject) -> str:
    """Return a readable unified diff between two public API snapshots."""
    return "".join(
        difflib.unified_diff(
            _serialized(expected).splitlines(keepends=True),
            _serialized(actual).splitlines(keepends=True),
            fromfile=str(SNAPSHOT_PATH),
            tofile="current pylopdf runtime",
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update",
        action="store_true",
        help="replace the checked-in snapshot with the current reviewed API",
    )
    return parser.parse_args()


def main() -> int:
    """Check or intentionally refresh the checked-in API baseline."""
    args = _parse_args()
    current = collect_public_api()
    if args.update:
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(_serialized(current), encoding="utf-8")
        sys.stdout.write(f"Updated public API snapshot: {SNAPSHOT_PATH}\n")
        return 0

    expected = load_snapshot()
    if expected == current:
        sys.stdout.write("Public API surface matches the reviewed snapshot.\n")
        return 0

    sys.stderr.write(
        "Public API surface changed. Review compatibility, then run "
        "`uv run python tools/check_api_surface.py --update` if intentional.\n"
    )
    sys.stderr.write(snapshot_diff(expected, current))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
