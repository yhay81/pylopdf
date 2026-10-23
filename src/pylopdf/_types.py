"""Public value types, type aliases, and flags."""

from __future__ import annotations

import enum
from typing import Literal, TypeAlias, TypedDict


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


class ImageCompressionResult(TypedDict):
    """Summary returned by :meth:`Document.rewrite_images`."""

    considered: int
    rewritten: int
    skipped: int
    bytes_before: int
    bytes_after: int
    bytes_saved: int


class DocumentComplexity(TypedDict):
    """Cheap structural facts that require no stream decoding or rendering."""

    page_count: int
    object_count: int
    stream_count: int
    encoded_stream_bytes: int
    max_object_depth: int


# The package re-exports these names; reprs, pickles, and documentation name it.
for _public in (
    Permissions,
    Access,
    ImageCompressionResult,
    DocumentComplexity,
):
    _public.__module__ = "pylopdf"
