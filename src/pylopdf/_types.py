"""Public value types, type aliases, flags, and constants."""

from __future__ import annotations

import enum
from typing import Literal, TypeAlias, TypedDict


class TextAlign(enum.IntEnum):
    """Text alignment; the members are pymupdf's ``TEXT_ALIGN_*`` values."""

    LEFT = 0
    CENTER = 1
    RIGHT = 2
    JUSTIFY = 3


TEXT_ALIGN_LEFT = TextAlign.LEFT
TEXT_ALIGN_CENTER = TextAlign.CENTER
TEXT_ALIGN_RIGHT = TextAlign.RIGHT
TEXT_ALIGN_JUSTIFY = TextAlign.JUSTIFY


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


PDF_PERM_PRINT = Permissions.PRINT
PDF_PERM_MODIFY = Permissions.MODIFY
PDF_PERM_COPY = Permissions.COPY
PDF_PERM_ANNOTATE = Permissions.ANNOTATE
PDF_PERM_FORM = Permissions.FORM
PDF_PERM_ACCESSIBILITY = Permissions.ACCESSIBILITY
PDF_PERM_ASSEMBLE = Permissions.ASSEMBLE
PDF_PERM_PRINT_HQ = Permissions.PRINT_HQ


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


FormFieldType: TypeAlias = Literal[
    "text",
    "checkbox",
    "radio",
    "button",
    "combobox",
    "listbox",
    "signature",
    "unknown",
]


class FormFieldInfo(TypedDict):
    """One AcroForm field returned by :meth:`Document.get_form_fields`."""

    name: str
    type: FormFieldType
    value: str | None


class DocumentComplexity(TypedDict):
    """Cheap structural facts that require no stream decoding or rendering."""

    page_count: int
    object_count: int
    stream_count: int
    encoded_stream_bytes: int
    max_object_depth: int


# The package re-exports these names; reprs, pickles, and documentation name it.
for _public in (
    TextAlign,
    Permissions,
    Access,
    ImageCompressionResult,
    FormFieldInfo,
    DocumentComplexity,
):
    _public.__module__ = "pylopdf"
