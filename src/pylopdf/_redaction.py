"""Redaction: removing what areas of a page cover.

``Page.redact`` applies the page's redaction annotations, or areas given
directly, and returns a :class:`RedactionReport`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pylopdf._annotations import _optional_color, _validate_highlight_rects
from pylopdf._geometry import Rect

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from pylopdf._content import Color
    from pylopdf._geometry import Quad
    from pylopdf._page import Page

_TEXT_MODES = ("remove", "keep")
_IMAGE_MODES = ("pixels", "remove", "keep")
_GRAPHICS_MODES = ("touched", "covered", "keep")


@dataclass(frozen=True, slots=True, kw_only=True)
class RedactionReport:
    """What :meth:`Page.redact` removed.

    ``areas`` are the applied areas in display space. ``glyphs`` counts the
    glyphs removed, ``images`` the image placements removed, and
    ``blanked_images`` the placements whose covered pixels were blanked in a
    new copy of the image. ``paths`` and ``shadings`` count the vector
    graphics removed, ``annotations`` the annotations and form widgets that
    overlapped an area (the applied redaction annotations not included), and
    ``forms`` the Form XObject placements that were copied so that other
    placements keep their content.
    """

    areas: tuple[Rect, ...]
    glyphs: int
    images: int
    blanked_images: int
    paths: int
    shadings: int
    annotations: int
    forms: int


def _choice(value: object, name: str, choices: tuple[str, ...]) -> str:
    if value not in choices:
        allowed = ", ".join(repr(choice) for choice in choices)
        msg = f"{name} must be one of {allowed}: {value!r}"
        raise ValueError(msg)
    return str(value)


def redact(  # noqa: PLR0913 - the redaction's options.
    page: Page,
    areas: Sequence[float] | Quad | Iterable[Sequence[float] | Quad] | None,
    *,
    text: Literal["remove", "keep"],
    images: Literal["pixels", "remove", "keep"],
    graphics: Literal["touched", "covered", "keep"],
    fill: Color | None,
) -> RedactionReport:
    """Validate the options and apply the redaction; see :meth:`Page.redact`."""
    text = _choice(text, "text", _TEXT_MODES)  # ty: ignore[invalid-assignment]
    images = _choice(images, "images", _IMAGE_MODES)  # ty: ignore[invalid-assignment]
    graphics = _choice(graphics, "graphics", _GRAPHICS_MODES)  # ty: ignore[invalid-assignment]
    fill = _optional_color(fill, "fill")
    payload = None
    if areas is not None:
        rects = _validate_highlight_rects(areas)
        color = None if fill is None else fill.rgb
        payload = [((rect.x0, rect.y0, rect.x1, rect.y1), color) for rect in rects]
    return _apply(page, payload, text=text == "remove", images=images, graphics=graphics, clip=None)


def clip_to_rect(page: Page, rect: Sequence[float]) -> RedactionReport:
    """Remove what lies outside ``rect``; see :meth:`Page.clip_to_rect`."""
    (kept,) = _validate_highlight_rects(rect)
    bounds = page.rect
    kept = kept & bounds
    if kept.is_empty:
        kept = Rect(bounds.x0, bounds.y0, bounds.x0, bounds.y0)
    bands = [
        Rect(bounds.x0, bounds.y0, bounds.x1, kept.y0),
        Rect(bounds.x0, kept.y1, bounds.x1, bounds.y1),
        Rect(bounds.x0, kept.y0, kept.x0, kept.y1),
        Rect(kept.x1, kept.y0, bounds.x1, kept.y1),
    ]
    payload = [((band.x0, band.y0, band.x1, band.y1), None) for band in bands if band.width > 0 and band.height > 0]
    return _apply(
        page, payload, text=True, images="pixels", graphics="touched", clip=(kept.x0, kept.y0, kept.x1, kept.y1)
    )


def _apply(  # noqa: PLR0913 - the core's arguments.
    page: Page,
    payload: list[tuple[tuple[float, float, float, float], tuple[float, float, float] | None]] | None,
    *,
    text: bool,
    images: str,
    graphics: str,
    clip: tuple[float, float, float, float] | None,
) -> RedactionReport:
    document = page._document  # noqa: SLF001 - the page's own document.
    raw = document._doc.redact_page(  # noqa: SLF001
        page._page_number(),  # noqa: SLF001
        payload,
        text,
        images,
        graphics,
        clip,
    )
    document._emit_warnings()  # noqa: SLF001
    applied, glyphs, removed_images, blanked, paths, shadings, annotations, forms = raw
    return RedactionReport(
        areas=tuple(Rect(*rect) for rect in applied),
        glyphs=glyphs,
        images=removed_images,
        blanked_images=blanked,
        paths=paths,
        shadings=shadings,
        annotations=annotations,
        forms=forms,
    )


RedactionReport.__module__ = "pylopdf"
