"""Page boxes: ``Page.boxes`` in PDF user space."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pylopdf._geometry import Rect
from pylopdf._limits import _validate_rect

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pylopdf._page import Page

#: hayro-syntax's MediaBox for a page without one: A4 at the origin.
_DEFAULT_MEDIABOX = Rect(0.0, 0.0, 210.0 * 72.0 / 25.4, 297.0 * 72.0 / 25.4)

_StoredBox = tuple[float, float, float, float]


def _ordered(box: _StoredBox) -> Rect:
    """Return a stored box with ``x0 <= x1`` and ``y0 <= y1``, as MuPDF reads it."""
    x0, y0, x1, y1 = box
    return Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


class PageBoxes:
    """The boxes of one page in PDF user space, as ``page.boxes`` returns them.

    ``media`` is the MediaBox, A4 at the origin when the page has none;
    ``crop`` defaults to the MediaBox, and ``bleed``, ``trim``, and ``art``
    default to the CropBox (ISO 32000-2 §14.11.2). Boxes read as stored,
    with their corners ordered and unclipped; the visible area in display
    space is ``page.rect``. The view reads the page on every access.

    Assigning ``media`` removes the other boxes, as pymupdf's
    ``set_mediabox`` does, so the whole new MediaBox shows. Assigning another
    box requires a rectangle with area inside the MediaBox, and ``del``
    removes a box so that it takes its default again.
    """

    __slots__ = ("_page",)

    def __init__(self, page: Page) -> None:
        """Wrap a page; use ``Page.boxes``."""
        self._page = page

    def _read(self) -> tuple[Rect, Rect, Rect, Rect, Rect]:
        page = self._page
        media, crop, bleed, trim, art = page._document._doc.page_boxes(page._page_number())
        media_box = _DEFAULT_MEDIABOX if media is None else _ordered(media)
        crop_box = media_box if crop is None else _ordered(crop)

        def other(box: _StoredBox | None) -> Rect:
            return crop_box if box is None else _ordered(box)

        return media_box, crop_box, other(bleed), other(trim), other(art)

    def _set(self, key: str, rect: Sequence[float]) -> None:
        x0, y0, x1, y1 = _validate_rect(rect, name=key)
        media = self.media
        if not (media.x0 <= x0 and media.y0 <= y0 and x1 <= media.x1 and y1 <= media.y1):
            msg = f"{key} must lie inside the MediaBox {media!r}: {rect!r}"
            raise ValueError(msg)
        page = self._page
        page._document._doc.set_page_box(page._page_number(), key, x0, y0, x1, y1)

    def _delete(self, key: str) -> None:
        page = self._page
        page._document._doc.remove_page_box(page._page_number(), key)

    @property
    def media(self) -> Rect:
        """Return the MediaBox, the extent of the page."""
        return self._read()[0]

    @media.setter
    def media(self, rect: Sequence[float]) -> None:
        x0, y0, x1, y1 = _validate_rect(rect, name="MediaBox")
        page = self._page
        page._document._doc.set_mediabox(page._page_number(), x0, y0, x1, y1)

    @property
    def crop(self) -> Rect:
        """Return the CropBox, the region that viewers show and print."""
        return self._read()[1]

    @crop.setter
    def crop(self, rect: Sequence[float]) -> None:
        self._set("CropBox", rect)

    @crop.deleter
    def crop(self) -> None:
        self._delete("CropBox")

    @property
    def bleed(self) -> Rect:
        """Return the BleedBox, the region that production clips to."""
        return self._read()[2]

    @bleed.setter
    def bleed(self, rect: Sequence[float]) -> None:
        self._set("BleedBox", rect)

    @bleed.deleter
    def bleed(self) -> None:
        self._delete("BleedBox")

    @property
    def trim(self) -> Rect:
        """Return the TrimBox, the finished page after trimming."""
        return self._read()[3]

    @trim.setter
    def trim(self, rect: Sequence[float]) -> None:
        self._set("TrimBox", rect)

    @trim.deleter
    def trim(self) -> None:
        self._delete("TrimBox")

    @property
    def art(self) -> Rect:
        """Return the ArtBox, the page's meaningful content."""
        return self._read()[4]

    @art.setter
    def art(self, rect: Sequence[float]) -> None:
        self._set("ArtBox", rect)

    @art.deleter
    def art(self) -> None:
        self._delete("ArtBox")

    def __repr__(self) -> str:
        """Return ``PageBoxes(media=..., crop=..., bleed=..., trim=..., art=...)``."""
        media, crop, bleed, trim, art = self._read()
        return f"PageBoxes(media={media!r}, crop={crop!r}, bleed={bleed!r}, trim={trim!r}, art={art!r})"


# The package re-exports this name; reprs and documentation name it.
PageBoxes.__module__ = "pylopdf"
