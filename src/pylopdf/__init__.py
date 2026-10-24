"""PDF editing and rendering backed by Rust.

:func:`open` opens a document and :func:`create` writes a new one. lopdf
handles editing, hayro rendering and extraction, and krilla generated text and
new documents; all are pure Rust under permissive licenses.
"""

from __future__ import annotations

# typing.get_type_hints() evaluates a public class's annotations in the module
# that the class reports, which is this package, so the Literal annotations of
# Table and TableDiagnostics resolve through this import.
from typing import (
    TYPE_CHECKING,
    Literal,  # noqa: F401 - read by typing.get_type_hints(); see above.
    NoReturn,
    TypeAlias,
)

from pylopdf import _annotations, _content, _form, _types
from pylopdf._annotations import (
    Annotation,
    AnnotationFile,
    AnnotationFlags,
    Annotations,
    AnnotationType,
    AttachmentIcon,
    Border,
    BorderStyle,
    Caret,
    Circle,
    FileAttachment,
    FreeText,
    Highlight,
    Ink,
    LineAnnotation,
    LineEnding,
    Note,
    NoteIcon,
    Polygon,
    PolyLine,
    Redaction,
    Square,
    Squiggly,
    Stamp,
    StampIcon,
    StrikeOut,
    Underline,
)
from pylopdf._attachments import Attachment, Attachments
from pylopdf._boxes import PageBoxes
from pylopdf._canvas import Canvas, ListNumbering, PathBuilder, TableHeaderScope, TagRole, TextFit
from pylopdf._content import (
    BlendMode,
    Clip,
    ClosePath,
    Color,
    Content,
    CurveTo,
    FillRule,
    FontRef,
    Glyph,
    GraphicsContext,
    Image,
    ImageMask,
    LineCap,
    LineJoin,
    LineTo,
    MarkedContent,
    MoveTo,
    Path,
    ShadingPattern,
    ShadingType,
    SoftMask,
    SoftMaskType,
    Stroke,
    TextRenderMode,
    TextRun,
    TilingPattern,
    TransparencyGroup,
)
from pylopdf._create import Builder, Standard, create
from pylopdf._document import Document, Encryption, Probe, open, probe  # noqa: A004 - the package's open().
from pylopdf._errors import (
    DocumentClosedError,
    EncryptedDocumentError,
    MalformedPdfError,
    PylopdfWarning,
    StalePageError,
)
from pylopdf._fallback_fonts import FallbackFont, FallbackFonts
from pylopdf._fonts import Font
from pylopdf._form import (
    CheckBox,
    ComboBox,
    Field,
    FieldChoice,
    FieldFlags,
    FieldType,
    Form,
    ListBox,
    PushButton,
    RadioButton,
    Scripts,
    ScriptTrigger,
    SignatureField,
    SignatureFlags,
    TextField,
)
from pylopdf._geometry import IRect, Matrix, Point, Quad, Rect
from pylopdf._hints import missing_attribute
from pylopdf._history import History
from pylopdf._images import EncodedImage, ImageObject, Images, Resources
from pylopdf._layers import (
    Layer,
    LayerBaseState,
    LayerConfiguration,
    LayerConfigurations,
    LayerExpression,
    LayerMembership,
    LayerOrder,
    Layers,
    VisibilityPolicy,
)
from pylopdf._limits import DocumentLimits
from pylopdf._links import Destinations, Link, Links
from pylopdf._metadata import MarkInfo, Metadata, PageLayout, PageMode, PdfaClaim, parse_pdf_date, pdf_date
from pylopdf._objects import Array, Dictionary, Name, Objects, Stream, String
from pylopdf._ocr import OcrEngine, OcrWord
from pylopdf._operations import InlineImage, Operation, Operations
from pylopdf._outline import Outline, OutlineEntry, OutlineItem
from pylopdf._page import Page
from pylopdf._page_labels import PageLabelRange, PageLabelStyle
from pylopdf._pages import Pages
from pylopdf._paper import PaperSize
from pylopdf._pixmap import Colorspace
from pylopdf._redaction import RedactionReport
from pylopdf._sanitize import SanitizationReport
from pylopdf._tables import Table, TableDiagnostics
from pylopdf._targets import Destination, Launch, NamedAction, RemoteDestination, Target
from pylopdf._text import Block, Char, Layout, Line, Span, Unmapped, Word
from pylopdf._types import (
    Access,
    DocumentComplexity,
    ImageCompressionResult,
    Permissions,
)
from pylopdf._widgets import Widget, Widgets
from pylopdf.pylopdf_core import LimitError, OcrError, PasswordError, PdfError, Pixmap, StaleObjectError

__version__ = "0.15.0"
__all__ = [
    "Access",
    "Annotation",
    "AnnotationFile",
    "AnnotationFlags",
    "AnnotationSpecification",
    "AnnotationType",
    "Annotations",
    "Array",
    "Attachment",
    "AttachmentIcon",
    "Attachments",
    "BlendMode",
    "Block",
    "Border",
    "BorderStyle",
    "Builder",
    "Canvas",
    "Caret",
    "Char",
    "CheckBox",
    "Circle",
    "CjkFontLanguage",
    "Clip",
    "ClosePath",
    "Color",
    "Colorspace",
    "ComboBox",
    "Content",
    "ContentItem",
    "CurveTo",
    "Destination",
    "Destinations",
    "Dictionary",
    "Document",
    "DocumentClosedError",
    "DocumentComplexity",
    "DocumentLimits",
    "EncodedImage",
    "EncryptedDocumentError",
    "Encryption",
    "FallbackFont",
    "FallbackFonts",
    "Field",
    "FieldChoice",
    "FieldFlags",
    "FieldSpecification",
    "FieldType",
    "FileAttachment",
    "FillRule",
    "Font",
    "FontLanguage",
    "FontRef",
    "Form",
    "FreeText",
    "Glyph",
    "GraphicsContext",
    "Highlight",
    "History",
    "IRect",
    "Image",
    "ImageCompressionResult",
    "ImageMask",
    "ImageObject",
    "Images",
    "Ink",
    "InlineImage",
    "Launch",
    "Layer",
    "LayerBaseState",
    "LayerConfiguration",
    "LayerConfigurations",
    "LayerExpression",
    "LayerMembership",
    "LayerOrder",
    "Layers",
    "Layout",
    "LimitError",
    "Line",
    "LineAnnotation",
    "LineCap",
    "LineEnding",
    "LineJoin",
    "LineTo",
    "Link",
    "Links",
    "ListBox",
    "ListNumbering",
    "MalformedPdfError",
    "MarkInfo",
    "MarkedContent",
    "Matrix",
    "Metadata",
    "MoveTo",
    "Name",
    "NamedAction",
    "Note",
    "NoteIcon",
    "Objects",
    "OcrEngine",
    "OcrError",
    "OcrRotation",
    "OcrWord",
    "Operation",
    "Operations",
    "Outline",
    "OutlineEntry",
    "OutlineItem",
    "Page",
    "PageBoxes",
    "PageLabelRange",
    "PageLabelStyle",
    "PageLayout",
    "PageMode",
    "Pages",
    "Paint",
    "PaperSize",
    "PasswordError",
    "Path",
    "PathBuilder",
    "PdfError",
    "PdfaClaim",
    "Permissions",
    "Point",
    "PolyLine",
    "Polygon",
    "Probe",
    "PushButton",
    "Quad",
    "RadioButton",
    "Rect",
    "Redaction",
    "RedactionReport",
    "RemoteDestination",
    "Resources",
    "SanitizationReport",
    "ScriptTrigger",
    "Scripts",
    "Segment",
    "ShadingPattern",
    "ShadingType",
    "SignatureField",
    "SignatureFlags",
    "SoftMask",
    "SoftMaskType",
    "Span",
    "Square",
    "Squiggly",
    "StaleObjectError",
    "StalePageError",
    "Stamp",
    "StampIcon",
    "Standard",
    "StandardFontName",
    "Stream",
    "StrikeOut",
    "String",
    "Stroke",
    "Table",
    "TableDiagnostics",
    "TableHeaderScope",
    "TagRole",
    "Target",
    "TextField",
    "TextFit",
    "TextRenderMode",
    "TextRun",
    "TilingPattern",
    "TransparencyGroup",
    "Underline",
    "Unmapped",
    "VisibilityPolicy",
    "Widget",
    "Widgets",
    "Word",
    "create",
    "open",
    "parse_pdf_date",
    "pdf_date",
    "probe",
]
__all__ += ["Pixmap", "PylopdfWarning"]

# Declare the type aliases again so that this module's __annotations__ marks
# them as TypeAlias, which tools/check_api_surface.py and other runtime readers
# use to tell an alias from a constant.
OcrRotation: TypeAlias = _types.OcrRotation
FontLanguage: TypeAlias = _types.FontLanguage
CjkFontLanguage: TypeAlias = _types.CjkFontLanguage
StandardFontName: TypeAlias = _types.StandardFontName
ContentItem: TypeAlias = _content.ContentItem
Paint: TypeAlias = _content.Paint
Segment: TypeAlias = _content.Segment
AnnotationSpecification: TypeAlias = _annotations.AnnotationSpecification
FieldSpecification: TypeAlias = _form.FieldSpecification


if not TYPE_CHECKING:

    def __getattr__(name: str) -> NoReturn:
        """Raise AttributeError for a missing name, naming pylopdf's replacement for a pymupdf name."""
        import sys  # noqa: PLC0415 - only a failed lookup reaches this.

        raise missing_attribute(None, sys.modules[__name__], name)
