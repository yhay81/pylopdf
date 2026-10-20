"""Color spaces and the rasterization options shared by page and document rendering."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

from pylopdf._geometry import Matrix
from pylopdf.pylopdf_core import PdfError

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Maximum R/G/B/A component value.
_COLOR_MAX = 255
#: Points per inch, the resolution of a scale factor of 1.
_POINTS_PER_INCH = 72.0

#: pymupdf's color space codes.
CS_RGB: Final = 1
CS_GRAY: Final = 2
CS_CMYK: Final = 3

_COLORSPACE_DETAILS: dict[int, tuple[str, int, str]] = {
    CS_RGB: ("DeviceRGB", 3, "CS_RGB"),
    CS_GRAY: ("DeviceGray", 1, "CS_GRAY"),
    CS_CMYK: ("DeviceCMYK", 4, "CS_CMYK"),
}


class Colorspace:
    """A device color space, as pymupdf's ``Colorspace(CS_RGB)`` describes it.

    Values are immutable and compare equal when they name the same space.
    """

    __slots__ = ("_type",)
    _type: int

    def __init__(self, type: int, /) -> None:  # noqa: A002 - pymupdf's parameter name.
        """Create the color space for one of :data:`CS_RGB`, :data:`CS_GRAY`, or :data:`CS_CMYK`."""
        if isinstance(type, bool) or type not in _COLORSPACE_DETAILS:
            msg = f"Colorspace takes CS_RGB, CS_GRAY, or CS_CMYK: {type!r}"
            raise PdfError(msg)
        object.__setattr__(self, "_type", int(type))

    def __setattr__(self, name: str, value: object) -> None:
        """Refuse attribute changes; color spaces are values."""
        msg = "Colorspace values are immutable"
        raise AttributeError(msg)

    @property
    def name(self) -> str:
        """Return the PDF name of the space, such as ``"DeviceRGB"``."""
        return _COLORSPACE_DETAILS[self._type][0]

    @property
    def n(self) -> int:
        """Return the number of color components."""
        return _COLORSPACE_DETAILS[self._type][1]

    def __eq__(self, other: object) -> bool:
        """Compare color spaces by kind."""
        if not isinstance(other, Colorspace):
            return NotImplemented
        return self._type == other._type

    def __hash__(self) -> int:
        """Hash consistently with equality."""
        return hash(("Colorspace", self._type))

    def __repr__(self) -> str:
        """Return pymupdf's representation, such as ``Colorspace(CS_RGB) - DeviceRGB``."""
        name, _, constant = _COLORSPACE_DETAILS[self._type]
        return f"Colorspace({constant}) - {name}"


# pymupdf's names for the three device color spaces.
csRGB: Final = Colorspace(CS_RGB)  # noqa: N816
csGRAY: Final = Colorspace(CS_GRAY)  # noqa: N816
csCMYK: Final = Colorspace(CS_CMYK)  # noqa: N816


def _normalize_background(
    background: tuple[int, int, int] | tuple[int, int, int, int] | None,
) -> tuple[int, int, int, int] | None:
    """Validate a ``background`` and normalize it to an RGBA tuple."""
    if background is None:
        return None
    match background:
        case (r, g, b):
            rgba = (r, g, b, _COLOR_MAX)
        case (r, g, b, a):
            rgba = (r, g, b, a)
        case _:
            msg = f"background must be an (R, G, B) or (R, G, B, A) tuple: {background!r}"
            raise ValueError(msg)
    for value in rgba:
        if not isinstance(value, int) or not 0 <= value <= _COLOR_MAX:
            msg = f"each background component must be an integer in 0-{_COLOR_MAX}: {background!r}"
            raise ValueError(msg)
    return rgba


def _render_background(
    background: tuple[int, int, int] | tuple[int, int, int, int] | None,
    *,
    alpha: bool,
) -> tuple[int, int, int, int] | None:
    """Resolve the raster background: white without alpha, as in pymupdf."""
    if not isinstance(alpha, bool):
        msg = f"alpha must be a bool: {alpha!r}"
        raise TypeError(msg)
    rgba = _normalize_background(background)
    if alpha:
        return rgba
    if rgba is None:
        return (_COLOR_MAX, _COLOR_MAX, _COLOR_MAX, _COLOR_MAX)
    if rgba[3] != _COLOR_MAX:
        msg = f"alpha=False needs an opaque background: {background!r}"
        raise ValueError(msg)
    return rgba


def _render_scale(matrix: Matrix | Sequence[float] | None, dpi: float | None) -> tuple[float, float]:
    """Return the horizontal and vertical scale of ``matrix``, or of ``dpi``.

    As in pymupdf, ``dpi`` takes precedence over ``matrix``. Only scaling
    matrices are supported: rotation, shear, and flips raise ``PdfError``,
    and a translation does not move the rendered pixels.
    """
    if dpi is not None:
        if isinstance(dpi, bool) or not isinstance(dpi, (int, float)) or not math.isfinite(dpi) or dpi <= 0:
            msg = f"dpi must be a positive finite number: {dpi!r}"
            raise ValueError(msg)
        scale = float(dpi) / _POINTS_PER_INCH
        return (scale, scale)
    if matrix is None:
        return (1.0, 1.0)
    resolved = matrix if isinstance(matrix, Matrix) else Matrix(*matrix)
    if resolved.b != 0 or resolved.c != 0 or resolved.a <= 0 or resolved.d <= 0:
        msg = f"matrix must scale without rotating, shearing, or flipping: {tuple(resolved)!r}"
        raise PdfError(msg)
    return (float(resolved.a), float(resolved.d))


# The package re-exports these names; reprs, pickles, and documentation name them.
Colorspace.__module__ = "pylopdf"
