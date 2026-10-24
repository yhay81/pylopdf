"""Standard paper sizes for new pages: :class:`PaperSize`."""

from __future__ import annotations

import enum


class PaperSize(tuple[int, int], enum.Enum):  # noqa: SLOT001 - enum members carry their name and value.
    """A standard paper size as a ``(width, height)`` pair of points, portrait.

    A member is the pair itself, so ``doc.pages.new(PaperSize.A4)`` adds an
    A4 page; :attr:`landscape` turns it sideways. ISO sizes are the
    millimetre sizes of ISO 216 and ISO 269 rounded to whole points, as PDF
    producers round them.
    """

    A0 = (2384, 3370)
    """ISO 216 A0, 841 × 1189 mm."""
    A1 = (1684, 2384)
    """ISO 216 A1, 594 × 841 mm."""
    A2 = (1191, 1684)
    """ISO 216 A2, 420 × 594 mm."""
    A3 = (842, 1191)
    """ISO 216 A3, 297 × 420 mm."""
    A4 = (595, 842)
    """ISO 216 A4, 210 × 297 mm."""
    A5 = (420, 595)
    """ISO 216 A5, 148 × 210 mm."""
    A6 = (298, 420)
    """ISO 216 A6, 105 × 148 mm."""
    A7 = (210, 298)
    """ISO 216 A7, 74 × 105 mm."""
    A8 = (147, 210)
    """ISO 216 A8, 52 × 74 mm."""
    A9 = (105, 147)
    """ISO 216 A9, 37 × 52 mm."""
    A10 = (74, 105)
    """ISO 216 A10, 26 × 37 mm."""
    B0 = (2835, 4008)
    """ISO 216 B0, 1000 × 1414 mm."""
    B1 = (2004, 2835)
    """ISO 216 B1, 707 × 1000 mm."""
    B2 = (1417, 2004)
    """ISO 216 B2, 500 × 707 mm."""
    B3 = (1001, 1417)
    """ISO 216 B3, 353 × 500 mm."""
    B4 = (709, 1001)
    """ISO 216 B4, 250 × 353 mm."""
    B5 = (499, 709)
    """ISO 216 B5, 176 × 250 mm."""
    B6 = (354, 499)
    """ISO 216 B6, 125 × 176 mm."""
    B7 = (249, 354)
    """ISO 216 B7, 88 × 125 mm."""
    B8 = (176, 249)
    """ISO 216 B8, 62 × 88 mm."""
    B9 = (125, 176)
    """ISO 216 B9, 44 × 62 mm."""
    B10 = (88, 125)
    """ISO 216 B10, 31 × 44 mm."""
    C0 = (2599, 3677)
    """ISO 269 C0, 917 × 1297 mm."""
    C1 = (1837, 2599)
    """ISO 269 C1, 648 × 917 mm."""
    C2 = (1298, 1837)
    """ISO 269 C2, 458 × 648 mm."""
    C3 = (918, 1298)
    """ISO 269 C3, 324 × 458 mm."""
    C4 = (649, 918)
    """ISO 269 C4, 229 × 324 mm."""
    C5 = (459, 649)
    """ISO 269 C5, 162 × 229 mm."""
    C6 = (323, 459)
    """ISO 269 C6, 114 × 162 mm."""
    C7 = (230, 323)
    """ISO 269 C7, 81 × 114 mm."""
    C8 = (162, 230)
    """ISO 269 C8, 57 × 81 mm."""
    C9 = (113, 162)
    """ISO 269 C9, 40 × 57 mm."""
    C10 = (79, 113)
    """ISO 269 C10, 28 × 40 mm."""
    LETTER = (612, 792)
    """US Letter, 8.5 × 11 in."""
    LEGAL = (612, 1008)
    """US Legal, 8.5 × 14 in."""
    TABLOID = (792, 1224)
    """Tabloid, 11 × 17 in; its landscape form is Ledger."""
    EXECUTIVE = (522, 756)
    """Executive, 7.25 × 10.5 in."""
    HALF_LETTER = (396, 612)
    """Half Letter (Statement), 5.5 × 8.5 in."""

    @property
    def width(self) -> int:
        """Return the width in points."""
        return self[0]

    @property
    def height(self) -> int:
        """Return the height in points."""
        return self[1]

    @property
    def landscape(self) -> tuple[int, int]:
        """Return the size turned sideways, ``(height, width)``."""
        return (self[1], self[0])


# The package re-exports this name; reprs, pickles, and documentation name it.
PaperSize.__module__ = "pylopdf"
