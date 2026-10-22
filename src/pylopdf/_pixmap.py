"""Color spaces and the rasterization options shared by page and document rendering."""

from __future__ import annotations

import enum
import math
from typing import TYPE_CHECKING

from pylopdf._geometry import Matrix
from pylopdf.pylopdf_core import PdfError

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Maximum R/G/B/A component value.
_COLOR_MAX = 255
#: Points per inch, the resolution of a scale factor of 1.
_POINTS_PER_INCH = 72.0
#: The highest JPEG quality.
_MAX_QUALITY = 100


class Colorspace(enum.StrEnum):
    """The color space of a :class:`Pixmap`'s samples."""

    GRAY = "gray"
    """One gray component per pixel."""
    RGB = "rgb"
    """Red, green, and blue components per pixel."""


def _render_gray(colorspace: Colorspace | str) -> bool:
    """Return whether ``colorspace`` asks for gray samples."""
    try:
        return Colorspace(colorspace) is Colorspace.GRAY
    except ValueError:
        msg = f"colorspace must be 'gray' or 'rgb': {colorspace!r}"
        raise ValueError(msg) from None


def _validate_encoding(format: str, quality: int, *, alpha: bool) -> None:  # noqa: A002 - the public name.
    """Refuse an unknown format, a quality outside 1 through 100, and JPEG with alpha."""
    if format not in ("png", "jpeg"):
        msg = f"format must be 'png' or 'jpeg': {format!r}"
        raise ValueError(msg)
    if isinstance(quality, bool) or not isinstance(quality, int):
        msg = f"quality must be an integer from 1 through 100: {quality!r}"
        raise TypeError(msg)
    if not 1 <= quality <= _MAX_QUALITY:
        msg = f"quality must be an integer from 1 through 100: {quality!r}"
        raise ValueError(msg)
    if format == "jpeg" and alpha:
        msg = "JPEG has no alpha channel; render without alpha or write PNG"
        raise ValueError(msg)


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


def _validate_bool(name: str, value: object) -> None:
    """Refuse a rendering flag that is not a bool."""
    if not isinstance(value, bool):
        msg = f"{name} must be a bool: {value!r}"
        raise TypeError(msg)


def _render_background(
    background: tuple[int, int, int] | tuple[int, int, int, int] | None,
    *,
    alpha: bool,
) -> tuple[int, int, int, int] | None:
    """Resolve the raster background: white without alpha."""
    _validate_bool("alpha", alpha)
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
    """Return the horizontal and vertical scale of ``dpi`` or of ``matrix``.

    The two are exclusive. Only scaling matrices are supported: rotation,
    shear, and flips raise ``PdfError``, and a translation does not move the
    rendered pixels.
    """
    if dpi is not None and matrix is not None:
        msg = "pass dpi or matrix, not both"
        raise ValueError(msg)
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


# The package re-exports this enum; reprs, pickles, and documentation name it.
Colorspace.__module__ = "pylopdf"
