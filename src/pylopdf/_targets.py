"""Where outline items and links lead: destinations and actions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from pylopdf._geometry import Point

if TYPE_CHECKING:
    from collections.abc import Sequence

#: The core's form of a target: kind, one-based page of this document or
#: zero-based page of another, display point, zoom, URI or file, and name.
_TargetTuple = tuple[str, int | None, tuple[float, float] | None, float | None, str | None, str | None]


def _page_index(page: object, name: str) -> int:
    if isinstance(page, bool) or not isinstance(page, int):
        msg = f"{name} must be an int: {page!r}"
        raise TypeError(msg)
    if page < 0:
        msg = f"{name} must not be negative: {page}"
        raise ValueError(msg)
    return page


def _text(value: object, name: str) -> str:
    if not isinstance(value, str):
        msg = f"{name} must be a str: {value!r}"
        raise TypeError(msg)
    if not value:
        msg = f"{name} must not be empty"
        raise ValueError(msg)
    return value


@dataclass(frozen=True, slots=True, init=False)
class Destination:
    """A place in this document: a zero-based page, a point, and a zoom.

    ``point`` is the top left of the view and ``zoom`` its magnification.
    The point is in the page's display coordinates. Without a point or a
    zoom the viewer fits the page in the window; ``zoom=None`` keeps the
    viewer's zoom.
    """

    page: int
    point: Point | None
    zoom: float | None

    def __init__(self, page: int, point: Sequence[float] | None = None, zoom: float | None = None) -> None:
        """Validate the page, point, and zoom; ``point`` takes any pair of numbers."""
        _page_index(page, "page")
        if zoom is not None:
            if isinstance(zoom, bool) or not isinstance(zoom, (int, float)):
                msg = f"zoom must be a number: {zoom!r}"
                raise TypeError(msg)
            if not math.isfinite(zoom) or zoom < 0:
                msg = f"zoom must be finite and not negative: {zoom!r}"
                raise ValueError(msg)
            zoom = float(zoom)
        object.__setattr__(self, "page", page)
        object.__setattr__(self, "point", None if point is None else Point(point))
        object.__setattr__(self, "zoom", zoom)


@dataclass(frozen=True, slots=True)
class RemoteDestination:
    """A place in another PDF file: a zero-based page or a named destination."""

    file: str
    page: int = 0
    name: str | None = None
    """A named destination in that file, used instead of ``page``."""

    def __post_init__(self) -> None:
        """Validate the file, page, and name."""
        _text(self.file, "file")
        _page_index(self.page, "page")
        if self.name is not None:
            _text(self.name, "name")


@dataclass(frozen=True, slots=True)
class Launch:
    """Open a file with the application the viewer associates with it."""

    file: str

    def __post_init__(self) -> None:
        """Validate the file."""
        _text(self.file, "file")


@dataclass(frozen=True, slots=True)
class NamedAction:
    """A viewer action by name (ISO 32000-2 Table 213).

    The standard names are ``"NextPage"``, ``"PrevPage"``, ``"FirstPage"``,
    and ``"LastPage"``; viewers may know others.
    """

    name: str

    def __post_init__(self) -> None:
        """Validate the name."""
        _text(self.name, "name")


#: Where an outline item or a link leads: a place in this document or in
#: another file, a file to open, a viewer action, or a URI as a string.
Target: TypeAlias = Destination | RemoteDestination | Launch | NamedAction | str


def _target_from_core(target: _TargetTuple) -> Target | None:
    """Build a target record from the core's tuple."""
    kind, page, point, zoom, text, name = target
    match kind:
        case "goto" if page is not None:
            return Destination(page - 1, None if point is None else Point(point), zoom)
        case "uri" if text:
            return text
        case "gotor" if text:
            return RemoteDestination(text, page or 0, name or None)
        case "launch" if text:
            return Launch(text)
        case "named" if name:
            return NamedAction(name)
        case _:
            return None


def _target_to_core(target: object, page_count: int) -> _TargetTuple:
    """Return the core's tuple for a target, validating its page."""
    match target:
        case None:
            return ("none", None, None, None, None, None)
        case Destination(page=page, point=point, zoom=zoom):
            if page >= page_count:
                msg = f"destination page {page} is out of range (0..{page_count - 1})"
                raise ValueError(msg)
            return ("goto", page + 1, None if point is None else (point.x, point.y), zoom, None, None)
        case str():
            return ("uri", None, None, None, _text(target, "URI"), None)
        case RemoteDestination(file=file, page=page, name=name):
            return ("gotor", page, None, None, file, name)
        case Launch(file=file):
            return ("launch", None, None, None, file, None)
        case NamedAction(name=name):
            return ("named", None, None, None, None, name)
        case _:
            msg = (
                "a target is a Destination, RemoteDestination, Launch, NamedAction, URI string, or None: "
                f"{type(target).__name__}"
            )
            raise TypeError(msg)


# The package re-exports these names; reprs, pickles, and documentation name them.
for _public in (Destination, RemoteDestination, Launch, NamedAction):
    _public.__module__ = "pylopdf"
