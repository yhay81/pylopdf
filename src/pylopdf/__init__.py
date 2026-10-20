"""PDF editing and rendering backed by Rust.

The :class:`Document` API follows pymupdf conventions. lopdf handles editing
and hayro handles rendering; both are pure Rust under permissive licenses.
"""

from __future__ import annotations

# typing.get_type_hints() evaluates a public class's annotations in the module
# that the class reports, which is this package, so the Literal annotations of
# Table, TableDiagnostics, and TableFinder resolve through this import.
from typing import (
    Literal,  # noqa: F401 - read by typing.get_type_hints(); see above.
    TypeAlias,
)

from pylopdf import _types
from pylopdf._document import Document, open, peek_metadata  # noqa: A004 - pymupdf's open().
from pylopdf._errors import DocumentClosedError, EncryptedDocumentError, PylopdfWarning, StalePageError
from pylopdf._fonts import Font
from pylopdf._geometry import Matrix
from pylopdf._limits import DocumentLimits
from pylopdf._objects import Array, Dictionary, Name, Objects, Stream, String
from pylopdf._ocr import OcrEngine
from pylopdf._page import Page
from pylopdf._shape import Shape
from pylopdf._tables import Table, TableDiagnostics, TableFinder
from pylopdf._types import (
    LINK_GOTO,
    LINK_GOTOR,
    LINK_LAUNCH,
    LINK_NAMED,
    LINK_NONE,
    LINK_URI,
    TEXT_ALIGN_CENTER,
    TEXT_ALIGN_JUSTIFY,
    TEXT_ALIGN_LEFT,
    TEXT_ALIGN_RIGHT,
    AnnotationInfo,
    DocumentComplexity,
    DocumentMetadata,
    DrawingInfo,
    FormFieldInfo,
    ImageCompressionResult,
    ImageInfo,
    LinkInfo,
    MetadataProbe,
    MetadataUpdate,
    OcrWord,
    PageLabelInfo,
    PageLabelSpec,
    Permissions,
    Point,
    RawTextBlock,
    RawTextLine,
    RawTextPage,
    RawTextSpan,
    Rect,
    TextBlock,
    TextChar,
    TextLine,
    TextPage,
    TextSpan,
)
from pylopdf.pylopdf_core import LimitError, OcrError, PasswordError, PdfError, Pixmap, StaleObjectError

__version__ = "0.14.0"
__all__ = [
    "LINK_GOTO",
    "LINK_GOTOR",
    "LINK_LAUNCH",
    "LINK_NAMED",
    "LINK_NONE",
    "LINK_URI",
    "TEXT_ALIGN_CENTER",
    "TEXT_ALIGN_JUSTIFY",
    "TEXT_ALIGN_LEFT",
    "TEXT_ALIGN_RIGHT",
    "AnnotationInfo",
    "Array",
    "BlockEntry",
    "CjkFontLanguage",
    "Dictionary",
    "Document",
    "DocumentClosedError",
    "DocumentComplexity",
    "DocumentLimits",
    "DocumentMetadata",
    "DrawingInfo",
    "DrawingItem",
    "EncryptedDocumentError",
    "Font",
    "FontLanguage",
    "FormFieldInfo",
    "FormFieldType",
    "ImageCompressionResult",
    "ImageInfo",
    "LimitError",
    "LinkInfo",
    "Matrix",
    "MetadataProbe",
    "MetadataUpdate",
    "Name",
    "Objects",
    "OcrEngine",
    "OcrError",
    "OcrRotation",
    "OcrWord",
    "Page",
    "PageLabelInfo",
    "PageLabelSpec",
    "PasswordError",
    "PdfError",
    "Permissions",
    "Point",
    "RawTextBlock",
    "RawTextLine",
    "RawTextPage",
    "RawTextSpan",
    "Rect",
    "Shape",
    "StaleObjectError",
    "StalePageError",
    "Stream",
    "String",
    "Table",
    "TableDiagnostics",
    "TableFinder",
    "TextBlock",
    "TextChar",
    "TextLine",
    "TextPage",
    "TextSpan",
    "WordEntry",
    "open",
    "peek_metadata",
]
__all__ += ["Pixmap", "PylopdfWarning"]

# Declare the type aliases again so that this module's __annotations__ marks
# them as TypeAlias, which tools/check_api_surface.py and other runtime readers
# use to tell an alias from a constant.
WordEntry: TypeAlias = _types.WordEntry
BlockEntry: TypeAlias = _types.BlockEntry
OcrRotation: TypeAlias = _types.OcrRotation
FontLanguage: TypeAlias = _types.FontLanguage
CjkFontLanguage: TypeAlias = _types.CjkFontLanguage
DrawingItem: TypeAlias = _types.DrawingItem
FormFieldType: TypeAlias = _types.FormFieldType
