"""Public value types, type aliases, and flags."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias

from pylopdf._hints import hinted_protocol


class Permissions(enum.IntFlag):
    """What an encrypted document permits the user password, combined with ``|``.

    :attr:`Document.permissions` reports them and ``Encryption(permissions=)``
    sets them. The values are the ``/P`` bits of ISO 32000-2 Table 22, and
    ``ALL`` combines every member.
    """

    PRINT = 1 << 2
    MODIFY = 1 << 3
    COPY = 1 << 4
    ANNOTATE = 1 << 5
    FORM = 1 << 8
    ACCESSIBILITY = 1 << 9
    ASSEMBLE = 1 << 10
    PRINT_HQ = 1 << 11
    ALL = PRINT | MODIFY | COPY | ANNOTATE | FORM | ACCESSIBILITY | ASSEMBLE | PRINT_HQ


class Access(enum.IntEnum):
    """What a password opened: the user's restricted access, or the owner's full access."""

    USER = 1
    OWNER = 2


#: Clockwise correction applied to rendered OCR input.
OcrRotation: TypeAlias = Literal[0, 90, 180, 270]
#: Script or locale used to select an optional bundled generation font.
FontLanguage: TypeAlias = Literal["ar", "he", "hi", "ja", "ko", "th", "zh-CN", "zh-TW"]
#: Locale slot used for non-embedded Adobe CJK font fallback.
CjkFontLanguage: TypeAlias = Literal["ja", "ko", "zh-CN", "zh-TW"]
#: The PostScript name of a Standard 14 font, which :meth:`Font.standard` takes.
StandardFontName: TypeAlias = Literal[
    "Courier",
    "Courier-Oblique",
    "Courier-Bold",
    "Courier-BoldOblique",
    "Helvetica",
    "Helvetica-Oblique",
    "Helvetica-Bold",
    "Helvetica-BoldOblique",
    "Times-Roman",
    "Times-Italic",
    "Times-Bold",
    "Times-BoldItalic",
    "Symbol",
    "ZapfDingbats",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class ImageCompressionResult:
    """What :meth:`Document.rewrite_images` did.

    The byte totals compare the encoded payloads of the rewritten images
    before and after, not the size of the saved document.
    """

    considered: int
    """The image objects that the document's pages place."""
    rewritten: int
    """The image objects whose smaller JPEG payload replaced their data."""
    skipped: int
    """The image objects left as they were: ``considered - rewritten``."""
    bytes_before: int
    """The encoded payload bytes of the rewritten images before the call."""
    bytes_after: int
    """Their encoded payload bytes after it."""
    bytes_saved: int
    """``bytes_before - bytes_after``."""

    if not TYPE_CHECKING:
        __getitem__ = hinted_protocol(
            "ImageCompressionResult is a record, not a dictionary: read result.rewritten, result.bytes_saved, and so on"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentComplexity:
    """Cheap structural facts that :attr:`Document.complexity` reads without decoding streams or rendering."""

    page_count: int
    """The number of pages."""
    object_count: int
    """The number of indirect objects."""
    stream_count: int
    """The number of stream objects."""
    encoded_stream_bytes: int
    """The encoded bytes of every stream together."""
    max_object_depth: int
    """The deepest nesting of arrays and dictionaries in any object."""

    if not TYPE_CHECKING:
        __getitem__ = hinted_protocol(
            "DocumentComplexity is a record, not a dictionary: read doc.complexity.page_count and so on"
        )


# The package re-exports these names; reprs, pickles, and documentation name it.
for _public in (
    Permissions,
    Access,
    ImageCompressionResult,
    DocumentComplexity,
):
    _public.__module__ = "pylopdf"
