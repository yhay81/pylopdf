"""AttributeError messages that name pylopdf's replacement for a pymupdf name.

The classes that pymupdf programs use most, and the package itself, define
``__getattr__``, which Python calls only after normal lookup failed, so a
successful lookup costs nothing. The hints load on the first miss.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from collections.abc import Callable


def migration_hint(name: str) -> str | None:
    """Return the migration message for a pymupdf name, such as ``Document.get_toc``, or None."""
    from pylopdf._migration_hints import HINTS  # noqa: PLC0415 - loaded on the first missing name.

    hint = HINTS.get(name)
    if hint is not None:
        return hint
    # The longest prefix wins, so PDF_ANNOT_IS_HIDDEN takes the hint of PDF_ANNOT_IS_* rather than PDF_ANNOT_*.
    groups = [key for key in HINTS if key.endswith("*") and name.startswith(key[:-1])]
    return HINTS[max(groups, key=len)] if groups else None


def missing_attribute(owner: str | None, obj: object, name: str) -> AttributeError:
    """Build the AttributeError for a failed lookup, adding a migration hint when one exists.

    ``owner`` is the class name of an instance, or None for the package.
    Dunder names, which protocols probe constantly, never consult the hints.
    """
    if owner is None:
        message = f"module 'pylopdf' has no attribute {name!r}"
        key = name
    else:
        message = f"{owner!r} object has no attribute {name!r}"
        key = f"{owner}.{name}"
    if not (name.startswith("__") and name.endswith("__")):
        hint = migration_hint(key)
        if hint is not None:
            message = f"{message}. {hint}"
    return AttributeError(message, name=name, obj=obj)


def hinted_getattr(self: object, name: str) -> NoReturn:
    """Raise the AttributeError of a failed instance lookup, with a migration hint.

    Classes assign this as ``__getattr__`` outside ``TYPE_CHECKING``, so type
    checkers still reject unknown attributes.
    """
    raise missing_attribute(type(self).__name__, self, name)


def hinted_protocol(message: str) -> Callable[..., NoReturn]:
    """Return a protocol method, such as ``__getitem__``, that raises ``TypeError`` with a migration hint.

    Classes assign it outside ``TYPE_CHECKING`` for a protocol that the API
    reset moved to another object, so type checkers and the public API
    snapshot, which skips methods marked ``__pylopdf_hint__``, never see it.
    """

    def method(_self: object, *_args: object) -> NoReturn:
        raise TypeError(message)

    method.__pylopdf_hint__ = True  # ty: ignore[unresolved-attribute]
    return method
