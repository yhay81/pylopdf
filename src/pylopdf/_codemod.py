"""LibCST rules that rewrite pymupdf programs, and pylopdf 0.15 programs, for pylopdf.

:mod:`pylopdf.migrate` is the command-line entry point. The rules rename the
``fitz`` and ``pymupdf`` imports, rewrite the calls whose pylopdf replacement
is mechanical (opening documents, the page operations that ``doc.pages``
replaced, the one-based TOC, ``embfile_*``, the PDF-syntax ``xref_*``
readers, and the in-place geometry methods), and add a ``TODO(pylopdf)``
comment, with the migration hint from the capability map, above every other
statement that uses a name pylopdf does not provide. Receivers are not
typed, so a rule applies to any attribute of that name; the rules use names
that are specific to pymupdf or to pylopdf 0.15. A name bound only to
documents (``doc = fitz.open(...)``) is also indexed, measured, and iterated
through ``doc.pages``, and a name bound only to pages (``page = doc[0]`` or a
loop over a document's pages) has its ``number`` and ``parent`` renamed.
Files that import neither ``fitz``, ``pymupdf``, nor ``pylopdf`` are left
alone.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import re
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, cast, get_args

import libcst as cst

import pylopdf
from pylopdf._hints import migration_hint
from pylopdf._metadata import METADATA_KEYS
from pylopdf._migration_hints import HINTS, PYLOPDF_015_ONLY, PYLOPDF_015_REMOVED

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

TODO = "TODO(pylopdf)"
PYMUPDF_MODULES = frozenset({"fitz", "pymupdf"})
PACKAGE = "pylopdf"
#: Module attributes that become a builtin name.
MODULE_TO_BUILTIN = {"FileNotFoundError": "FileNotFoundError"}
#: Module attributes that the API reset renamed, pymupdf's and pylopdf 0.15's alike.
MODULE_RENAMES = {
    "FileDataError": "MalformedPdfError",
    "EmptyFileError": "MalformedPdfError",
    "peek_metadata": "probe",
}
#: pymupdf module attributes that pylopdf names otherwise; pylopdf's own ``Outline`` is the item sequence.
PYMUPDF_MODULE_RENAMES = {"Outline": "OutlineItem", "Annot": "Annotation"}
#: pymupdf's geometry constructors, called without arguments, and the class attributes that replace them.
GEOMETRY_CONSTANTS = {
    "IdentityMatrix": "Matrix.IDENTITY",
    **{
        f"{kind}_{shape.upper()}": f"{shape}.{kind}"
        for kind in ("EMPTY", "INFINITE")
        for shape in ("Rect", "IRect", "Quad")
    },
}
#: Module constants that became enum members or class attributes.
MODULE_MEMBER_RENAMES = {
    "csRGB": "Colorspace.RGB",
    "csGRAY": "Colorspace.GRAY",
    "CS_RGB": "Colorspace.RGB",
    "CS_GRAY": "Colorspace.GRAY",
    **{
        f"PDF_ANNOT_{member.name}": f"AnnotationType.{member.name}"
        for member in pylopdf.AnnotationType
        if member is not pylopdf.AnnotationType.THREE_D
    },
    "PDF_ANNOT_3D": "AnnotationType.THREE_D",
    **{f"PDF_ANNOT_IS_{member.name}": f"AnnotationFlags.{member.name}" for member in pylopdf.AnnotationFlags},
    **{f"PDF_PERM_{member.name}": f"Permissions.{member.name}" for member in pylopdf.Permissions},
    **{
        f"STAMP_{''.join(part.capitalize() for part in member.name.split('_'))}": f"StampIcon.{member.name}"
        for member in pylopdf.StampIcon
    },
    **{
        f"PDF_ANNOT_LE_{member.name.replace('REVERSED_', 'R_')}": f"LineEnding.{member.name}"
        for member in pylopdf.LineEnding
    },
    **{
        f"PDF_BM_{''.join(part.capitalize() for part in member.name.split('_'))}": f"BlendMode.{member.name}"
        for member in pylopdf.BlendMode
    },
    "Identity": "Matrix.IDENTITY",
    "planish_line": "Matrix.planish",
    "FZ_MIN_INF_RECT": "Rect.INFINITE.x0",
    "FZ_MAX_INF_RECT": "Rect.INFINITE.x1",
    **GEOMETRY_CONSTANTS,
}
#: Document properties that the API reset renamed; only pymupdf's and pylopdf 0.15's Document had them.
DOCUMENT_PROPERTY_RENAMES = {
    "pagemode": "page_mode",
    "pagelayout": "page_layout",
    "language_tag": "language",
    "markinfo": "mark_info",
}
#: Pixmap attributes that the buffer protocol replaced, and what reads the same pixels.
PIXMAP_SAMPLES = {"samples": "bytes({receiver})", "samples_mv": "memoryview({receiver})"}
#: Builtins that iterate their arguments, whose document arguments become ``doc.pages``.
ITERATING_BUILTINS = frozenset(
    {"all", "any", "enumerate", "filter", "frozenset", "iter", "list", "map"}
    | {"reversed", "set", "sorted", "tuple", "zip"}
)
#: Module calls whose result is a document, a value that is no document, or neither.
DOCUMENT_FACTORIES = frozenset({"open", "Document"})
OTHER_FACTORIES = frozenset({"probe", "peek_metadata", "create"})
#: Document methods whose result is a page.
PAGE_METHODS = frozenset({"load_page", "new_page", "reload_page"})
#: Statements with headers or bodies of their own; each collects its own hints.
COMPOUND_STATEMENTS = (
    cst.If,
    cst.For,
    cst.While,
    cst.With,
    cst.Try,
    cst.TryStar,
    cst.FunctionDef,
    cst.ClassDef,
    cst.ExceptHandler,
    cst.Match,
)
ROTATION_NAMES = frozenset({"rotation_matrix", "derotation_matrix"})
#: pymupdf's page boxes besides the MediaBox, and the ``page.boxes`` attribute of each.
PAGE_BOX_NAMES = {"cropbox": "crop", "artbox": "art", "bleedbox": "bleed", "trimbox": "trim"}
PAGE_BOX_MESSAGE = (
    "pymupdf's cropbox, artbox, bleedbox, and trimbox flip y against the top of the MediaBox; page.boxes "
    "reads and takes PDF user space as stored, so each y there is page.boxes.media.y1 - y here."
)
ROTATION_MESSAGE = (
    "pylopdf extracts and inserts in display coordinates with the page rotation resolved; "
    "drop any rotation_matrix or derotation_matrix step, and map PDF user space with page.user_to_display."
)
#: pymupdf's short Standard 14 aliases and the PostScript name of each.
STANDARD_FONT_ALIASES = {
    "helv": "Helvetica",
    "hebo": "Helvetica-Bold",
    "heit": "Helvetica-Oblique",
    "hebi": "Helvetica-BoldOblique",
    "cour": "Courier",
    "cobo": "Courier-Bold",
    "coit": "Courier-Oblique",
    "cobi": "Courier-BoldOblique",
    "tiro": "Times-Roman",
    "tibo": "Times-Bold",
    "tiit": "Times-Italic",
    "tibi": "Times-BoldItalic",
    "symb": "Symbol",
    "zadb": "ZapfDingbats",
}
FONT_MESSAGE = (
    "Font.standard(name) takes a Standard 14 PostScript name, Font.from_file(path) and Font.from_bytes(data) "
    "load an OpenType face, and Font.installed(language=..., family=...) selects the face of an installed font "
    "extra; pymupdf's other built-in fonts, script=, ordering=, and embed= are not available."
)
HAS_GLYPH_MESSAGE = (
    "Font.has_glyph(char) takes a one-character string and returns whether the face has a glyph for it, "
    "not the glyph number."
)
#: pymupdf's line caps and joins, by number, as ``LineCap`` and ``LineJoin`` members.
LINE_CAPS = ("BUTT", "ROUND", "SQUARE")
LINE_JOINS = ("MITER", "ROUND", "BEVEL")
#: A pymupdf dash pattern such as ``"[3 4] 0"``: the lengths and the phase.
DASH_PATTERN = re.compile(r"\s*\[\s*((?:\d+(?:\.\d*)?\s*)*)\]\s*(\d+(?:\.\d*)?)?\s*")
#: The styling options of pymupdf's ``Page.draw_*`` methods.
DRAWING_STYLE = (
    "color",
    "fill",
    "dashes",
    "width",
    "lineCap",
    "lineJoin",
    "overlay",
    "closePath",
    "stroke_opacity",
    "fill_opacity",
    "morph",
    "oc",
    "radius",
)
#: Each ``Page.draw_*`` method that the canvas draws alike: its geometry parameters, and the canvas
#: call with its geometry, which ``{builder}`` and ``{close}`` complete.
PAGE_DRAWINGS: dict[str, tuple[tuple[str, ...], str]] = {
    "draw_line": (("p1", "p2"), "line({p1}, {p2}"),
    "draw_polyline": (("points",), "polygon({points}{close}"),
    "draw_rect": (("rect",), "rect({rect}"),
    "draw_circle": (("center", "radius"), "circle({center}, {radius}"),
    "draw_oval": (("tetra",), "ellipse({tetra}"),
    "draw_bezier": (("p1", "p2", "p3", "p4"), "bezier({p1}, {p2}, {p3}, {p4}"),
    "draw_squiggle": (("p1", "p2", "breadth"), "path({builder}().wave({p1}, {p2}, breadth={breadth})"),
    "draw_zigzag": (("p1", "p2", "breadth"), "path({builder}().zigzag({p1}, {p2}, breadth={breadth})"),
}
#: The drawing methods whose canvas call takes a fill.
FILLED_DRAWINGS = frozenset({"draw_polyline", "draw_rect", "draw_circle", "draw_oval"})
#: The ``align`` option of the canvas's textbox for each ``TEXT_ALIGN_*`` value.
TEXT_ALIGNMENTS = ("left", "center", "right", "justify")
#: pymupdf's and pylopdf 0.15's ``insert_text`` parameters: the point and the text, then the options.
TEXT_PARAMETERS = (
    "point",
    "text",
    "fontsize",
    "lineheight",
    "fontname",
    "fontfile",
    "set_simple",
    "encoding",
    "color",
    "fill",
    "render_mode",
    "border_width",
    "miter_limit",
    "rotate",
    "morph",
    "stroke_opacity",
    "fill_opacity",
    "overlay",
    "oc",
    "font",
    "fontbuffer",
    "fontindex",
    "font_language",
    "max_font_size",
    "max_text_size",
)
#: pymupdf's and pylopdf 0.15's ``insert_textbox`` parameters, in the same way.
TEXTBOX_PARAMETERS = (
    "rect",
    "buffer",
    "fontsize",
    "lineheight",
    "fontname",
    "fontfile",
    "set_simple",
    "encoding",
    "color",
    "fill",
    "expandtabs",
    "align",
    "rotate",
    "render_mode",
    "border_width",
    "miter_limit",
    "morph",
    "stroke_opacity",
    "fill_opacity",
    "oc",
    "overlay",
    "font",
    "fontbuffer",
    "fontindex",
    "font_language",
    "max_font_size",
    "max_text_size",
    "text",
)
#: pymupdf's ``insert_image`` parameters, of which only the rectangle is positional, and pylopdf 0.15's limits.
IMAGE_PARAMETERS = (
    "rect",
    "alpha",
    "filename",
    "height",
    "keep_proportion",
    "mask",
    "oc",
    "overlay",
    "pixmap",
    "rotate",
    "stream",
    "width",
    "xref",
    "max_size",
    "max_pixels",
)
#: ``insert_image`` options whose value changes nothing.
IMAGE_DEFAULTS = {
    "alpha": frozenset({"-1"}),
    "height": frozenset({"0"}),
    "width": frozenset({"0"}),
    "mask": frozenset({"None"}),
    "oc": frozenset({"0"}),
    "xref": frozenset({"0"}),
    "rotate": frozenset({"0"}),
    "keep_proportion": frozenset({"True"}),
    "overlay": frozenset({"True"}),
    "filename": frozenset({"None"}),
    "stream": frozenset({"None"}),
    "pixmap": frozenset({"None"}),
}
#: pymupdf's ``show_pdf_page`` parameters; pylopdf 0.15 named ``docsrc`` ``src``.
PAGE_PLACEMENT_PARAMETERS = ("rect", "docsrc", "pno", "keep_proportion", "overlay", "oc", "rotate", "clip")
#: ``show_pdf_page`` options whose value changes nothing.
PAGE_PLACEMENT_DEFAULTS = {
    "pno": frozenset({"0"}),
    "keep_proportion": frozenset({"True"}),
    "overlay": frozenset({"True"}),
    "oc": frozenset({"0"}),
    "rotate": frozenset({"0"}),
    "clip": frozenset({"None"}),
}
#: Text options whose value changes nothing, and the options that the canvas has no counterpart for.
TEXT_DEFAULTS = {
    "set_simple": frozenset({"0", "False"}),
    "encoding": frozenset({"0"}),
    "render_mode": frozenset({"0"}),
    "border_width": frozenset({"0.05", "1"}),
    "miter_limit": frozenset({"1"}),
    "rotate": frozenset({"0"}),
    "morph": frozenset({"None"}),
    "stroke_opacity": frozenset({"1", "1.0"}),
    "oc": frozenset({"0"}),
    "fill": frozenset({"None"}),
    "fontindex": frozenset({"0"}),
    "expandtabs": frozenset({"1"}),
    "fontname": frozenset({"'helv'", '"helv"'}),
    "overlay": frozenset({"True", "1"}),
}


@dataclass(frozen=True)
class Migration:
    """The outcome of migrating one source file."""

    code: str
    #: Mechanical rewrites applied, imports included.
    rewrites: int
    #: ``(line, message)`` for every TODO comment the migration added.
    todos: tuple[tuple[int, str], ...]


@cache
def _provided_members() -> frozenset[str]:
    """Return every attribute name of pylopdf's public classes and of the builtin types."""
    names: set[str] = set()
    for export in pylopdf.__all__:
        value = getattr(pylopdf, export)
        if inspect.isclass(value):
            names.update(dir(value))
    for kind in (dict, list, tuple, set, frozenset, str, bytes, bytearray, int, float, object):
        names.update(dir(kind))
    return frozenset(names)


@cache
def _member_hints(pymupdf: bool) -> dict[str, str]:  # noqa: FBT001 - cached per kind of program.
    """Map a pymupdf or pylopdf 0.15 member name that no pylopdf class provides to its hint.

    A name that several classes share takes the Document hint, then the Page
    hint, then the first in alphabetical order. A pymupdf program skips the
    names that only pylopdf 0.15 had.
    """
    provided = _provided_members()
    priority = {"Document": 0, "Page": 1}
    ranked: dict[str, tuple[int, str]] = {}
    for key, message in HINTS.items():
        owner, _, member = key.partition(".")
        if not member or (member.startswith("__") and member.endswith("__")) or member in provided:
            continue
        if pymupdf and key in PYLOPDF_015_ONLY:
            continue
        rank = priority.get(owner, len(priority))
        current = ranked.get(member)
        if current is None or rank < current[0]:
            ranked[member] = (rank, message)
    return {member: message for member, (_, message) in ranked.items()}


@cache
def _removed_015_members() -> frozenset[str]:
    """Return the member names of pylopdf 0.15.0's public classes that pylopdf no longer provides."""
    return frozenset(name.partition(".")[2] for name in PYLOPDF_015_REMOVED if "." in name)


def _code(node: cst.CSTNode) -> str:
    return cst.Module(body=[]).code_for_node(node)


def _same_code(replacement: str, node: cst.CSTNode) -> bool:
    """Return whether ``replacement`` is the expression ``node`` apart from layout and comments."""
    return ast.dump(ast.parse(replacement, mode="eval")) == ast.dump(ast.parse(_code(node).strip(), mode="eval"))


def _repeatable(node: cst.BaseExpression) -> bool:
    """Return whether ``node`` names the same page when evaluated again.

    That is a name, or attributes and subscripts by names and literals of one;
    a call such as ``doc.pages.new()`` makes a new page each time.
    """
    while isinstance(node, (cst.Attribute, cst.Subscript)):
        if isinstance(node, cst.Subscript) and not all(
            isinstance(element.slice, cst.Index)
            and isinstance(element.slice.value, (cst.Name, cst.Integer, cst.SimpleString))
            for element in node.slice
        ):
            return False
        node = node.value
    return isinstance(node, cst.Name)


def _calls(node: cst.CSTNode) -> Iterator[cst.Call]:
    """Yield every call in ``node``, outermost first."""
    if isinstance(node, cst.Call):
        yield node
    for child in node.children:
        yield from _calls(child)


def _receiver(node: cst.BaseExpression) -> str:
    """Render a receiver, parenthesized unless it is a name, attribute, call, or subscript."""
    text = _code(node)
    if isinstance(node, (cst.Name, cst.Attribute, cst.Call, cst.Subscript)):
        return text
    return f"({text})"


@dataclass(frozen=True)
class _Arguments:
    """A call's arguments, refused when it unpacks ``*args`` or ``**kwargs``."""

    positional: tuple[str, ...]
    keywords: dict[str, str]

    @classmethod
    def of(cls, call: cst.Call) -> _Arguments | None:
        positional: list[str] = []
        keywords: dict[str, str] = {}
        for arg in call.args:
            if arg.star:
                return None
            if arg.keyword is None:
                if keywords:
                    return None
                positional.append(_code(arg.value))
            else:
                keywords[arg.keyword.value] = _code(arg.value)
        return cls(tuple(positional), keywords)

    def bind(self, names: Sequence[str]) -> dict[str, str] | None:
        """Bind to pymupdf's parameter names, or None for unknown or duplicate arguments."""
        if len(self.positional) > len(names):
            return None
        bound = dict(zip(names, self.positional, strict=False))
        for key, value in self.keywords.items():
            if key not in names or key in bound:
                return None
            bound[key] = value
        return bound


def _get_toc(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``get_toc()`` to the ``[level, title, page]`` lists it returned, with one-based pages."""
    bound = args.bind(("simple",))
    if bound is None or bound.get("simple", "True") != "True":
        return None
    return (
        "[[entry.level, entry.title, -1 if entry.page is None else entry.page + 1] "
        f"for entry in {receiver}.outline.entries()]"
    )


def _set_toc(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``set_toc(toc)``, whose pages are one-based, to an assignment of outline entries."""
    bound = args.bind(("toc",))
    entry = migrator.class_name("OutlineEntry")
    destination = migrator.class_name("Destination")
    if bound is None or set(bound) != {"toc"} or entry is None or destination is None:
        return None
    return (
        f"{receiver}.outline = [{entry}(item[0], item[1], {destination}(item[2] - 1) if item[2] > 0 else None) "
        f"for item in {bound['toc']}]"
    )


def _set_page_labels(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``set_page_labels(labels)`` dictionaries to page-label ranges."""
    bound = args.bind(("labels",))
    label_range = migrator.class_name("PageLabelRange")
    if bound is None or set(bound) != {"labels"} or label_range is None:
        return None
    return (
        f'{receiver}.page_labels = [{label_range}(rule["startpage"], rule.get("style") or None, '
        f'rule.get("prefix", ""), rule.get("firstpagenum", 1)) for rule in {bound["labels"]}]'
    )


def _no_arguments(template: str) -> Callable[[_Migrator, str, _Arguments], str | None]:
    def rule(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
        if args.positional or args.keywords:
            return None
        return template.format(receiver=receiver)

    return rule


def _one_argument(name: str, template: str) -> Callable[[_Migrator, str, _Arguments], str | None]:
    def rule(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
        bound = args.bind((name,))
        if bound is None or set(bound) != {name}:
            return None
        return template.format(receiver=receiver, value=bound[name])

    return rule


def _embfile_get(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    bound = args.bind(("item",))
    if bound is None or set(bound) != {"item"} or bound["item"].lstrip("-").isdigit():
        return None
    return f"{receiver}.attachments[{bound['item']}].read()"


def _embfile_add(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    bound = args.bind(("name", "buffer_", "filename", "ufilename", "desc"))
    if bound is None or not {"name", "buffer_"} <= set(bound):
        return None
    if "filename" in bound and "ufilename" in bound:
        return None
    parts = [bound["name"], bound["buffer_"]]
    filename = bound.get("ufilename", bound.get("filename"))
    if filename is not None:
        parts.append(f"filename={filename}")
    if "desc" in bound:
        parts.append(f"description={bound['desc']}")
    return f"{receiver}.attachments.add({', '.join(parts)})"


def _embfile_upd(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    bound = args.bind(("item", "buffer_", "filename", "ufilename", "desc"))
    if bound is None or set(bound) != {"item", "buffer_"} or bound["item"].lstrip("-").isdigit():
        return None
    return f"{receiver}.attachments[{bound['item']}].write({bound['buffer_']})"


def _update_stream(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    bound = args.bind(("xref", "stream"))
    if bound is None or set(bound) != {"xref", "stream"}:
        return None
    return f"{receiver}.objects[{bound['xref']}].write({bound['stream']})"


def _int_literal(text: str) -> int | None:
    """Return the value of an integer literal, possibly negated, or None."""
    try:
        node = cst.parse_expression(text)
    except cst.ParserSyntaxError:
        return None
    sign = 1
    if isinstance(node, cst.UnaryOperation) and isinstance(node.operator, cst.Minus):
        sign, node = -1, node.expression
    if isinstance(node, cst.Integer):
        return sign * int(node.evaluated_value)
    return None


def _wrapped(text: str) -> str:
    """Parenthesize an expression's code unless it is a name, attribute, call, or subscript."""
    return _receiver(cst.parse_expression(text))


def _page_range(source: str, first: str | None, last: str | None) -> str | None:
    """Render ``insert_pdf``'s inclusive page range of ``source`` as a slice of its pages."""
    pages = f"{_wrapped(source)}.pages"
    start = 0 if first is None else _int_literal(first)
    stop = -1 if last is None else _int_literal(last)
    if start is None or stop is None:
        return None
    start = max(start, 0)
    if stop < 0:
        return pages if start == 0 else f"{pages}[{start}:]"
    if start <= stop:
        return f"{pages}[{start}:{stop + 1}]"
    return f"{pages}[{stop}:{start + 1}][::-1]"


def _insert_pdf(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    bound = args.bind(
        (
            "docsrc",
            "from_page",
            "to_page",
            "start_at",
            "rotate",
            "links",
            "annots",
            "widgets",
            "join_duplicates",
            "show_progress",
            "final",
        )
    )
    if bound is None or "docsrc" not in bound:
        return None
    defaults = {"rotate": "-1", "links": "True", "annots": "True", "widgets": "True", "join_duplicates": "False"}
    if any(bound.get(key, value) != value for key, value in defaults.items()):
        return None
    pages = _page_range(bound["docsrc"], bound.get("from_page"), bound.get("to_page"))
    if pages is None:
        return None
    start = _int_literal(bound.get("start_at", "-1"))
    if start is None:
        return None
    if start < 0:
        return f"{receiver}.pages.extend({pages})"
    return f"{receiver}.pages.insert({start}, {pages})"


def _new_page(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    bound = args.bind(("pno", "width", "height"))
    if bound is None:
        return None
    parts = []
    if "width" in bound or "height" in bound:
        parts.append(f"({bound.get('width', '595')}, {bound.get('height', '842')})")
    index = _int_literal(bound.get("pno", "-1"))
    if index is None:
        return None
    if index != -1:
        parts.append(f"index={index}")
    return f"{receiver}.pages.new({', '.join(parts)})"


def _copy_page(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    bound = args.bind(("pno", "to"))
    if bound is None or "pno" not in bound:
        return None
    page = f"{receiver}.pages[{bound['pno']}]"
    to = _int_literal(bound.get("to", "-1"))
    if to is None:
        return None
    if to == -1:
        return f"{receiver}.pages.extend({page})"
    return f"{receiver}.pages.insert({to}, {page})"


def _move_page(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    bound = args.bind(("pno", "to"))
    if bound is None or "pno" not in bound:
        return None
    index = _int_literal(bound["pno"])
    to = _int_literal(bound.get("to", "-1"))
    if index is None or to is None or index < 0:
        return None
    # pymupdf inserts before page ``to``; pylopdf names the index the page ends at.
    destination = -1 if to == -1 else (to - 1 if to > index else to)
    return f"{receiver}.pages.move({index}, {destination})"


def _select(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    # A receiver named pages can already be Pages, unless it is a known document.
    if (
        (receiver.rsplit(".", 1)[-1] == "pages" and not migrator.is_document(receiver))
        or len(args.positional) != 1
        or args.keywords
    ):
        return None
    return f"{receiver}.pages.select({args.positional[0]})"


def _load_page(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    bound = args.bind(("page_id",))
    if bound is None:
        return None
    return f"{receiver}.pages[{bound.get('page_id', '0')}]"


def _page_method(method: str, names: tuple[str, ...]) -> Callable[[_Migrator, str, _Arguments], str | None]:
    """Rewrite ``doc.<name>(pno, ...)`` to ``doc.pages[pno].<method>(...)``, keeping the other arguments.

    When the page method has a rule of its own, such as ``get_text``, that
    rule rewrites the page call in turn.
    """

    def rule(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
        bound = args.bind(("pno", *names))
        if bound is None or "pno" not in bound:
            return None
        rest = _Arguments(args.positional[1:], {key: value for key, value in args.keywords.items() if key != "pno"})
        page = f"{receiver}.pages[{bound['pno']}]"
        if method in CALL_RULES:
            return CALL_RULES[method](migrator, page, rest)
        return _call(page, method, rest)

    return rule


def _get_page_numbers(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    bound = args.bind(("label", "only_one"))
    if bound is None or "label" not in bound:
        return None
    only_one = bound.get("only_one", "False")
    if only_one not in {"True", "False"}:
        return None
    suffix = "[:1]" if only_one == "True" else ""
    return f"{receiver}.pages.labeled({bound['label']}){suffix}"


def _pages_call(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    bound = args.bind(("start", "stop", "step"))
    if bound is None:
        return None
    if not bound:
        return f"{receiver}.pages"
    parts = [bound.get(key, "") for key in ("start", "stop", "step")]
    if parts[2] == "":
        parts.pop()
    return f"{receiver}.pages[{':'.join('' if part == 'None' else part for part in parts)}]"


#: pymupdf's ``Document.tobytes`` parameters in order; ``save`` adds ``filename``,
#: ``incremental``, and ``preserve_metadata``, and pylopdf 0.15 took
#: ``object_streams`` as a keyword.
TOBYTES_PARAMETERS = (
    "garbage",
    "clean",
    "deflate",
    "deflate_images",
    "deflate_fonts",
    "ascii",
    "expand",
    "linear",
    "no_new_id",
    "appearance",
    "pretty",
    "encryption",
    "permissions",
    "owner_pw",
    "user_pw",
    "use_objstms",
    "compression_effort",
)
SAVE_PARAMETERS = (
    "filename",
    *TOBYTES_PARAMETERS[:6],
    "incremental",
    *TOBYTES_PARAMETERS[6:15],
    "preserve_metadata",
    *TOBYTES_PARAMETERS[15:],
)
#: Save options of pymupdf that pylopdf leaves out; their default values change nothing.
_UNSUPPORTED_SAVE_OPTIONS = ("clean", "incremental", "ascii", "expand", "linear", "no_new_id", "appearance", "pretty")
_FALSE = frozenset({"False", "0", "None"})
_TRUE = frozenset({"True", "1"})
SAVE_MESSAGE = (
    "save() and to_bytes() take compress, prune, dedupe, renumber, object_streams, and "
    "encryption=Encryption(user_password=..., owner_password=..., permissions=...); "
    "pylopdf writes AES-256 encryption only and has no clean, incremental, linear, or ascii option."
)
UNLOCK_MESSAGE = (
    "unlock() returns Access.USER or Access.OWNER and raises PasswordError for a wrong password, "
    "where authenticate() returned 0, 1, 2, 4, or 6."
)


#: pymupdf's ``PDF_ENCRYPT_*`` values by their member names.
ENCRYPTION_METHODS = {0: "KEEP", 1: "NONE", 2: "RC4_40", 3: "RC4_128", 4: "AES_128", 5: "AES_256", 6: "UNKNOWN"}


def _encryption_method(text: str) -> str | None:
    """Return the method that an ``encryption=`` argument names in pymupdf's terms, or None for another value.

    ``fitz.PDF_ENCRYPT_AES_256``, ``EncryptionMethod.AES_256``, and ``5`` all
    name ``AES_256``; an ``Encryption(...)`` value or ``None`` names none.
    """
    parts = text.split(".")
    if parts[-1].startswith("PDF_ENCRYPT_"):
        return parts[-1].removeprefix("PDF_ENCRYPT_")
    if len(parts) > 1 and parts[-2] == "EncryptionMethod":
        return parts[-1]
    value = _int_literal(text)
    return None if value is None else ENCRYPTION_METHODS.get(value, "UNKNOWN")


def _encryption_option(migrator: _Migrator, bound: dict[str, str]) -> str | None:
    """Return the ``encryption=`` option, ``""`` for none, or ``None`` when it cannot be migrated.

    pymupdf encrypts only with ``encryption=PDF_ENCRYPT_AES_256`` and ignores
    the passwords otherwise; pylopdf 0.15 encrypted whenever a password was
    given. An unrecognized value can already use pylopdf's ``Encryption``
    API; a pymupdf module needs a manual review of such a value.
    """
    passwords = {"user_pw", "owner_pw", "permissions"} & set(bound)
    value = bound.get("encryption")
    method = None if value is None else _encryption_method(value)
    if value is not None and method is None:
        return None if passwords or migrator.pymupdf() else f"encryption={value}"
    if method is None:
        encrypts = not migrator.pymupdf() and any(bound.get(key, "None") != "None" for key in ("user_pw", "owner_pw"))
    elif method in {"AES_256", "NONE"}:
        encrypts = method == "AES_256"
    else:
        return None
    if not encrypts:
        return ""
    name = migrator.class_name("Encryption")
    if name is None:
        return None
    fields = (("user_pw", "user_password"), ("owner_pw", "owner_password"), ("permissions", "permissions"))
    arguments = [
        f"{field}={bound[key]}"
        for key, field in fields
        if key in bound and not (key in {"user_pw", "owner_pw"} and bound[key] == "None")
    ]
    return f"encryption={name}({', '.join(arguments)})"


#: The save options of each of pymupdf's cumulative garbage levels, which pylopdf 0.15 took too.
GARBAGE_LEVELS = {
    0: ["prune=False"],
    1: [],
    2: ["renumber=True"],
    3: ["dedupe=True", "renumber=True"],
    4: ["dedupe=True", "renumber=True"],
}


def _object_options(bound: dict[str, str]) -> list[str] | None:
    """Translate ``garbage``, ``deflate``, and the object-stream option, or return None."""
    options = []
    if "garbage" in bound:
        level = {"False": 0, "True": 1}.get(bound["garbage"], _int_literal(bound["garbage"]))
        if level not in GARBAGE_LEVELS:
            return None
        options.extend(GARBAGE_LEVELS[level])
    deflate = bound.get("deflate", "True")
    if deflate not in _FALSE | _TRUE:
        return None
    if deflate in _FALSE:
        options.append("compress=False")
    if "use_objstms" in bound and "object_streams" in bound:
        return None
    object_streams = bound.get("use_objstms", bound.get("object_streams", "False"))
    if object_streams not in _FALSE | _TRUE:
        return None
    if object_streams in _TRUE:
        options.append("object_streams=True")
    return options


def _save_options(migrator: _Migrator, bound: dict[str, str]) -> list[str] | None:
    """Translate pymupdf's and pylopdf 0.15's save options, or return None when one cannot be migrated.

    An option that the call gives keeps its effect, and an omitted one takes
    pylopdf's default: ``garbage=0`` becomes ``prune=False``, while a call
    without ``garbage`` prunes.
    """
    ignored = all(bound.get(key, "False") in _FALSE | _TRUE for key in ("deflate_images", "deflate_fonts"))
    unsupported = any(bound.get(key, "False") not in _FALSE for key in _UNSUPPORTED_SAVE_OPTIONS)
    options = _object_options(bound)
    encryption = _encryption_option(migrator, bound)
    if not ignored or unsupported or bound.get("preserve_metadata", "1") not in _TRUE:
        return None
    if options is None or encryption is None:
        return None
    return [*options, encryption] if encryption else options


#: pymupdf's Pixmap output names and the formats that pylopdf writes.
PIXMAP_FORMATS = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg"}


def _pixmap_keywords(args: _Arguments, names: Sequence[str]) -> list[str] | None:
    """Translate pymupdf's ``output`` and ``jpg_quality`` to ``format`` and ``quality``, or None."""
    bound = args.bind(names)
    if bound is None or not ({"output", "jpg_quality"} & set(bound)):
        return None
    keywords = []
    if bound.get("output", "None") != "None":
        try:
            output = ast.literal_eval(bound["output"])
        except (ValueError, SyntaxError):
            return None
        if not isinstance(output, str) or output.lower() not in PIXMAP_FORMATS:
            return None
        keywords.append(f'format="{PIXMAP_FORMATS[output.lower()]}"')
    if "jpg_quality" in bound:
        keywords.append(f"quality={bound['jpg_quality']}")
    return keywords


def _save(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    if not migrator.is_document(receiver):
        pixmap = _pixmap_keywords(args, ("filename", "output", "jpg_quality"))
        if pixmap is not None and args.positional:
            return f"{receiver}.save({', '.join([args.positional[0], *pixmap])})"
    bound = args.bind((*SAVE_PARAMETERS, "object_streams"))
    if bound is None or "filename" not in bound or (len(args.positional) > 1 and not migrator.is_document(receiver)):
        # Another class's save, such as Pixmap.save(filename, output), which keeps its own rules.
        return None
    options = _save_options(migrator, bound)
    if options is None:
        migrator.todo(SAVE_MESSAGE)
        return None
    return f"{receiver}.save({', '.join([bound['filename'], *options])})"


def _tobytes(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    if not args.positional and not args.keywords:
        # Document.to_bytes() and Pixmap.to_bytes() alike.
        return f"{receiver}.to_bytes()"
    if not migrator.is_document(receiver):
        pixmap = _pixmap_keywords(args, ("output", "jpg_quality"))
        if pixmap is not None:
            return f"{receiver}.to_bytes({', '.join(keyword.removeprefix('format=') for keyword in pixmap)})"
    bound = args.bind((*TOBYTES_PARAMETERS, "object_streams", "max_size"))
    if bound is None or not (set(args.keywords) - {"max_size"} or migrator.is_document(receiver)):
        # Another class's tobytes, such as Pixmap.tobytes(output), which keeps its own rules.
        return None
    options = _save_options(migrator, bound)
    if options is None:
        migrator.todo(SAVE_MESSAGE)
        return None
    if "max_size" in bound:
        options.append(f"max_size={bound['max_size']}")
    return f"{receiver}.to_bytes({', '.join(options)})"


def _unlock(note: bool) -> Callable[[_Migrator, str, _Arguments], str | None]:  # noqa: FBT001 - a rule factory.
    def rule(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
        bound = args.bind(("password",))
        if bound is None or set(bound) != {"password"}:
            return None
        if note:
            migrator.todo(UNLOCK_MESSAGE)
        return f"{receiver}.unlock({bound['password']})"

    return rule


#: ``get_text`` options and their replacements; ``words`` and ``blocks`` return records now.
TEXT_OPTIONS = {
    "text": "{receiver}.text()",
    "words": "{receiver}.words()",
    "blocks": "{receiver}.layout().blocks",
    "dict": "{receiver}.layout().to_dict()",
    "rawdict": "{receiver}.layout(chars=True).to_dict()",
}
RECORD_MESSAGES = {
    "words": "page.words() returns Word records with text, bbox, block, line, and index instead of tuples.",
    "blocks": "page.layout().blocks holds Block records with text, bbox, and lines instead of tuples.",
    "images": (
        "page.resources.images returns ImageObject handles instead of tuples: read image.xref, width, height, "
        "bits, colorspace, alternate_colorspace, filters, soft_mask, mask, and is_stencil."
    ),
}


def _string_literal(text: str) -> str | None:
    """Return the value of a string literal, or None for another expression."""
    try:
        node = cst.parse_expression(text)
    except cst.ParserSyntaxError:
        return None
    if isinstance(node, cst.SimpleString):
        value = node.evaluated_value
        return value if isinstance(value, str) else None
    return None


def _call(receiver: str, method: str, args: _Arguments) -> str:
    """Render a method call with the arguments of another call."""
    rendered = [*args.positional, *(f"{key}={value}" for key, value in args.keywords.items())]
    return f"{receiver}.{method}({', '.join(rendered)})"


def _renamed(method: str) -> Callable[[_Migrator, str, _Arguments], str | None]:
    """Return a rule that renames a method and keeps its arguments."""

    def rule(_: _Migrator, receiver: str, args: _Arguments) -> str:
        return _call(receiver, method, args)

    return rule


def _get_text(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite pymupdf's ``Page.get_text(option)`` and pylopdf 0.15's ``Document.get_text(pages)``."""
    if "pages" in args.keywords or (
        args.positional and _string_literal(args.positional[0]) is None and migrator.is_document(receiver)
    ):
        bound = args.bind(("pages",))
        return None if bound is None else _call(receiver, "text", args)
    bound = args.bind(("option", "clip", "flags", "textpage", "sort", "delimiters", "tolerance"))
    if bound is None or set(bound) - {"option", "sort"}:
        return None
    option = _string_literal(bound.get("option", '"text"'))
    if option not in TEXT_OPTIONS:
        return None
    if option in RECORD_MESSAGES:
        migrator.todo(RECORD_MESSAGES[option])
    return TEXT_OPTIONS[option].format(receiver=receiver)


def _search_for(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``search_for(needle, ...)`` to ``search(text, ...)``; clip and quads come with Reading."""
    keywords = {"needle" if key == "text" else key: value for key, value in args.keywords.items()}
    bound = _Arguments(args.positional, keywords).bind(
        ("needle", "clip", "quads", "flags", "textpage", "hit_max", "max_hits")
    )
    if bound is None or "needle" not in bound or {"clip", "flags", "textpage"} & set(bound):
        return None
    if bound.get("quads", "False") not in _FALSE or ("hit_max" in bound and "max_hits" in bound):
        return None
    arguments = [bound["needle"]]
    if "hit_max" in bound or "max_hits" in bound:
        arguments.append(f"max_hits={bound.get('max_hits', bound.get('hit_max'))}")
    return f"{receiver}.search({', '.join(arguments)})"


#: The strategies of ``find_tables`` that ``tables`` takes as they are.
TABLE_STRATEGIES = frozenset({"lines", "text"})
OCR_RECORD_MESSAGE = "page.ocr() returns OcrWord records; read word.text, word.bbox, and word.confidence."


def _find_tables(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``find_tables(clip=..., strategy=...)``; pymupdf's tuning keywords come with Understanding."""
    bound = args.bind(("clip", "strategy"))
    if bound is None:
        return None
    arguments = []
    strategy = bound.get("strategy")
    if strategy is not None and strategy != "None":
        if _string_literal(strategy) not in TABLE_STRATEGIES:
            return None
        arguments.append(f"strategy={strategy}")
    if "clip" in bound:
        arguments.append(f"clip={bound['clip']}")
    return f"{receiver}.tables({', '.join(arguments)})"


def _get_text_ocr(migrator: _Migrator, receiver: str, args: _Arguments) -> str:
    """Rewrite pylopdf 0.15's ``get_text_ocr(...)`` to ``ocr(...)``, whose words are records now."""
    migrator.todo(OCR_RECORD_MESSAGE)
    return _call(receiver, "ocr", args)


PATH_RECORD_MESSAGE = (
    "page.content(...).paths returns Path records instead of dictionaries: read path.bbox, path.segments, "
    "path.fill, path.fill_rule, and path.stroke, whose paint, width, cap, join, and dashes style the stroke."
)
EXTENDED_PATH_MESSAGE = (
    "page.content(...).paths returns Path records instead of dictionaries; each path's context holds the clips, "
    "transparency groups, and marked content around it, which extended=True listed as entries of their own."
)
IMAGE_RECORD_MESSAGE = (
    "page.content(...).images returns Image records instead of dictionaries: read image.xref, bbox, matrix, "
    "width, height, bits, colorspace, components, mask, encoded_size, and resolution."
)


def _get_drawings(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``get_drawings(extended=...)`` and ``get_cdrawings(extended=...)`` to the content model."""
    bound = args.bind(("extended", "callback", "method"))
    if bound is None or {"callback", "method"} & set(bound):
        return None
    extended = bound.get("extended", "False")
    if extended not in _FALSE | _TRUE:
        return None
    migrator.todo(EXTENDED_PATH_MESSAGE if extended in _TRUE else PATH_RECORD_MESSAGE)
    return f"{receiver}.content(text_runs=False, images=False).paths"


def _get_image_info(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``get_image_info(xrefs=...)``; image digests have no counterpart yet."""
    bound = args.bind(("hashes", "xrefs"))
    if bound is None or bound.get("hashes", "False") not in _FALSE:
        return None
    migrator.todo(IMAGE_RECORD_MESSAGE)
    return f"{receiver}.content(text_runs=False, paths=False).images"


EXTRACTED_IMAGE_MESSAGE = (
    "doc.images[xref].extract() returns an EncodedImage instead of a dictionary: read .format, .data, and "
    ".resolution; the size, color space, and masks are properties of doc.images[xref]."
)
#: ``extract_image`` dictionary keys and what reads them from ``doc.images[xref]``.
EXTRACTED_IMAGE_KEYS = {
    "ext": ".extract().format",
    "image": ".extract().data",
    "width": ".width",
    "height": ".height",
    "bpc": ".bits",
    "cs-name": ".colorspace",
    "xres": ".extract().resolution[0]",
    "yres": ".extract().resolution[1]",
}


def _get_images(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``get_images(full=...)`` to ``resources.images``, whose items are handles."""
    bound = args.bind(("full",))
    if bound is None or bound.get("full", "False") not in _FALSE | _TRUE:
        return None
    migrator.todo(RECORD_MESSAGES["images"])
    return f"{receiver}.resources.images"


def _extract_image(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``extract_image(xref)`` to ``images[xref].extract()``, which returns a record."""
    bound = args.bind(("xref",))
    if bound is None or "xref" not in bound:
        return None
    migrator.todo(EXTRACTED_IMAGE_MESSAGE)
    return f"{receiver}.images[{bound['xref']}].extract()"


#: The RGB color space, which ``pixmap()`` renders by default, as module constants become enum members.
_RGB_COLORSPACES = frozenset({"pylopdf.Colorspace.RGB", "Colorspace.RGB", "csRGB"})
#: pymupdf's identity matrix, the only one that ``svg()`` draws with.
_IDENTITY_MATRICES = frozenset(
    {"pymupdf.Identity", "fitz.Identity", "pylopdf.Identity", "Identity", "pylopdf.Matrix.IDENTITY", "Matrix.IDENTITY"}
)


def _render_keywords(args: _Arguments, names: Sequence[str]) -> list[str] | None:
    """Return a rendering call's keywords with dpi winning over matrix, as pymupdf and 0.15 did."""
    bound = args.bind(names)
    if bound is None:
        return None
    dropped = set()
    if bound.get("colorspace") in _RGB_COLORSPACES:
        dropped.add("colorspace")
    if bound.get("dpi") == "None":
        dropped.add("dpi")
    elif "dpi" in bound:
        dropped.add("matrix")
    return [
        f"{'annotations' if key == 'annots' else key}={value}" for key, value in bound.items() if key not in dropped
    ]


def _get_pixmap(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``get_pixmap(...)`` to ``pixmap(...)``, which takes dpi or matrix and calls annots annotations."""
    if args.positional:
        return None
    keywords = _render_keywords(args, ("matrix", "dpi", "colorspace", "clip", "alpha", "annots", "background"))
    return None if keywords is None else f"{receiver}.pixmap({', '.join(keywords)})"


def _get_svg_image(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``get_svg_image()`` to ``svg()``, which draws text as paths at 72 dpi."""
    bound = args.bind(("matrix", "text_as_path"))
    if bound is None or bound.get("matrix", "Identity") not in _IDENTITY_MATRICES:
        return None
    if bound.get("text_as_path", "True") not in _TRUE:
        return None
    return f"{receiver}.svg()"


def _render_pages(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite pylopdf 0.15's ``render_pages(...)`` to ``render(...)``."""
    names = ("pages", "matrix", "dpi", "alpha", "background", "workers", "max_size")
    if args.bind(names) is None:
        return None
    keywords = _render_keywords(_Arguments((), args.keywords), names)
    return None if keywords is None else f"{receiver}.render({', '.join([*args.positional, *keywords])})"


def _render_page(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite pylopdf 0.15's ``render_page(pno, scale, ...)``, which returned PNG bytes that kept transparency."""
    bound = args.bind(("pno", "scale", "dpi", "background", "max_size"))
    if bound is None or "pno" not in bound:
        return None
    keywords = []
    if bound.get("dpi", "None") != "None":
        keywords.append(f"dpi={bound['dpi']}")
    elif "scale" in bound:
        scale = bound["scale"]
        try:
            keywords.append(f"dpi={float(scale) * 72:g}")
        except ValueError:
            keywords.append(f"dpi=72 * ({scale})")
    keywords.append("alpha=True")
    if "background" in bound:
        keywords.append(f"background={bound['background']}")
    size = f"max_size={bound['max_size']}" if "max_size" in bound else ""
    return f"{receiver}.pages[{bound['pno']}].pixmap({', '.join(keywords)}).to_bytes({size})"


#: pymupdf's and pylopdf 0.15's metadata keys that ``doc.metadata`` renamed.
METADATA_KEY_RENAMES = {"creationDate": "created", "modDate": "modified"}
#: pymupdf's metadata entries that are properties now, and what reads them.
METADATA_FACTS = {"format": 'f"PDF {{{receiver}.version}}"', "encryption": "{receiver}.encryption_method"}
#: Keys whose value pymupdf's ``set_metadata`` ignored.
METADATA_READ_ONLY = frozenset(METADATA_FACTS)
#: The literals that pymupdf's ``set_metadata`` reads as removing an entry.
_FALSY_LITERALS = frozenset({'""', "''", "None", "False", "0", '"none"', "'none'", '"null"', "'null'"})
#: The catalog names that page_mode and page_layout take, which pymupdf matched case-insensitively.
PAGE_MODE_NAMES = ("UseNone", "UseOutlines", "UseThumbs", "FullScreen", "UseOC", "UseAttachments")
PAGE_LAYOUT_NAMES = ("SinglePage", "OneColumn", "TwoColumnLeft", "TwoColumnRight", "TwoPageLeft", "TwoPageRight")
MARK_INFO_MESSAGE = (
    "doc.mark_info returns a MarkInfo record with marked, user_properties, and suspects, or None; "
    "assign a MarkInfo to replace it."
)
OUTLINE_MESSAGE = (
    "pymupdf's doc.outline is the first top-level item; pylopdf's doc.outline is the sequence of top-level "
    "OutlineItem handles, whose children replace down and next, and walk() visits every item."
)
PDFA_MESSAGE = "doc.pdfa_claim returns a PdfaClaim record with part and conformance, or None, instead of a tuple."


def _set_metadata(_: _Migrator, receiver: str, args: _Arguments) -> str | None:  # noqa: PLR0911
    """Rewrite ``set_metadata({...})`` with a literal dictionary to ``metadata.update()`` or ``clear()``.

    A value that removed an entry, or any other argument, keeps the call for
    the member hint, because the new mapping removes entries with ``del``.
    """
    bound = args.bind(("m",))
    if bound is None or "m" not in bound:
        return None
    try:
        node = cst.parse_expression(bound["m"])
    except cst.ParserSyntaxError:
        return None
    if not isinstance(node, cst.Dict):
        return None
    if not node.elements:
        return f"{receiver}.metadata.clear()"
    entries = []
    for element in node.elements:
        if not isinstance(element, cst.DictElement):
            return None
        key = _string_literal(_code(element.key))
        value = _code(element.value)
        if key is None or value in _FALSY_LITERALS:
            return None
        if key in METADATA_READ_ONLY:
            continue
        entries.append(f'"{METADATA_KEY_RENAMES.get(key, key)}": {value}')
    return f"{receiver}.metadata.update({{{', '.join(entries)}}})"


def _get_xml_metadata(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``get_xml_metadata()``, which returned ``""`` without a packet, to ``xmp``."""
    if args.positional or args.keywords:
        return None
    return f'({receiver}.xmp or "")'


def _get_pdfa_claim(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite pylopdf 0.15's ``get_pdfa_claim()`` to the ``pdfa_claim`` record."""
    if args.positional or args.keywords:
        return None
    migrator.todo(PDFA_MESSAGE)
    return f"{receiver}.pdfa_claim"


def _assign_property(prop: str, parameter: str) -> Callable[[_Migrator, str, _Arguments], str | None]:
    """Rewrite a setter call to an assignment of ``prop``."""

    def rule(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
        bound = args.bind((parameter,))
        if bound is None or set(bound) != {parameter}:
            return None
        return f"{receiver}.{prop} = {bound[parameter]}"

    return rule


def _set_rotation(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite a page's ``set_rotation(rotation)``; pymupdf's annotations have one too."""
    bound = args.bind(("rotation",))
    if bound is None or set(bound) != {"rotation"} or not migrator.is_page(receiver):
        return None
    return f"{receiver}.rotation = {bound['rotation']}"


def _pymupdf_only(
    rule: Callable[[_Migrator, str, _Arguments], str | None],
) -> Callable[[_Migrator, str, _Arguments], str | None]:
    """Apply an annotation setter rule only in modules that import pymupdf, whose annotations had the method."""

    def guarded(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
        return rule(migrator, receiver, args) if migrator.pymupdf() else None

    return guarded


def _set_colors(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite an annotation's ``set_colors(stroke=...)`` or ``set_colors(fill=...)`` to one assignment."""
    bound = args.bind(("colors", "stroke", "fill"))
    if bound is None or len(bound) != 1 or "colors" in bound:
        return None
    ((keyword, value),) = bound.items()
    prop = "color" if keyword == "stroke" else "interior_color"
    if value in ("None", "[]", "()"):
        return f"del {receiver}.{prop}"
    color = _color_record(migrator, value)
    return None if color is None else f"{receiver}.{prop} = {color}"


def _set_line_ends(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite an annotation's ``set_line_ends(start, end)``."""
    bound = args.bind(("start", "end"))
    if bound is None or set(bound) != {"start", "end"}:
        return None
    return f"{receiver}.line_endings = ({bound['start']}, {bound['end']})"


def _set_info(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite an annotation's ``set_info(content=...)``, ``title=``, or ``subject=`` with one keyword."""
    bound = args.bind(("info", "content", "title", "creationDate", "modDate", "subject"))
    properties = {"content": "contents", "title": "author", "subject": "subject"}
    if bound is None or len(bound) != 1 or next(iter(bound)) not in properties:
        return None
    ((keyword, value),) = bound.items()
    return f"{receiver}.{properties[keyword]} = {value}"


def _set_border(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite an annotation's ``set_border(width=..., dashes=...)`` to a ``Border``."""
    bound = args.bind(("border", "width", "dashes", "style", "clouds"))
    border = migrator.class_name("Border")
    if bound is None or not bound or set(bound) - {"width", "dashes"} or border is None:
        return None
    arguments = [bound["width"]] if "width" in bound else []
    if "dashes" in bound:
        arguments.append(f"dashes={bound['dashes']}")
    return f"{receiver}.border = {border}({', '.join(arguments)})"


def _set_form_field(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite pylopdf 0.15's ``set_form_field(name, value)`` without font options."""
    bound = args.bind(("name", "value"))
    if bound is None or set(bound) != {"name", "value"}:
        return None
    return f"{receiver}.form[{bound['name']}].value = {bound['value']}"


def _set_fallback_font(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite pylopdf 0.15's ``set_fallback_font(None)``, which turned fallback rendering off."""
    bound = args.bind(("font",))
    if bound != {"font": "None"}:
        return None
    return f"{receiver}.fallback_fonts = {{}}"


ANNOTATION_RECORD_MESSAGE = (
    "page.annotations lists Annotation handles with type, rect, contents, author, subject, color, "
    "interior_color, opacity, and flags in place of a type tuple and info and colors dictionaries; "
    "links are in page.links."
)
ANNOTATION_HANDLE_MESSAGE = (
    "pylopdf's Annotation handles have typed properties in place of info, colors, and next; "
    "annotation.type is an AnnotationType member."
)


def _annots(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``annots(types=...)`` to the page's annotation handles."""
    bound = args.bind(("types",))
    if bound is None:
        return None
    migrator.todo(ANNOTATION_RECORD_MESSAGE)
    types = bound.get("types", "None")
    if types == "None":
        return f"list({receiver}.annotations)"
    return f"[annotation for annotation in {receiver}.annotations if annotation.type in {types}]"


def _color_record(migrator: _Migrator, text: str) -> str | None:
    """Return a ``Color`` for an RGB tuple expression, unpacking a tuple literal."""
    color = migrator.class_name("Color")
    if color is None:
        return None
    node = cst.parse_expression(text)
    if isinstance(node, (cst.Tuple, cst.List)) and all(isinstance(e, cst.Element) for e in node.elements):
        return f"{color}({', '.join(_code(element.value) for element in node.elements)})"
    return f"{color}(*{text})"


def _add_text_markup_annot(specification: str) -> Callable[[_Migrator, str, _Arguments], str | None]:
    """Rewrite ``add_highlight_annot(quads, ...)`` and its siblings to ``annotations.add(...)``."""

    def rule(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
        return _add_markup(migrator, receiver, args, specification)

    return rule


def _add_markup(migrator: _Migrator, receiver: str, args: _Arguments, specification: str) -> str | None:
    """Rewrite one text-markup call to ``annotations.add(Highlight(...))`` or a sibling."""
    keywords = dict(args.keywords)
    if len(args.positional) > 1 or set(keywords) - {"quads", "rects", "color", "opacity", "content"}:
        return None
    area = args.positional[0] if args.positional else keywords.get("quads", keywords.get("rects"))
    highlight = migrator.class_name(specification)
    if area is None or highlight is None:
        return None
    arguments = [area]
    if "color" in keywords:
        color = _color_record(migrator, keywords["color"])
        if color is None:
            return None
        arguments.append(f"color={color}")
    if "opacity" in keywords:
        arguments.append(f"opacity={keywords['opacity']}")
    if "content" in keywords:
        arguments.append(f"contents={keywords['content']}")
    return f"{receiver}.annotations.add({highlight}({', '.join(arguments)}))"


def _add_shape_annot(
    specification: str, parameters: tuple[str, ...] = ("rect",)
) -> Callable[[_Migrator, str, _Arguments], str | None]:
    """Rewrite ``add_rect_annot(rect)`` and the other shape methods to ``annotations.add(...)``."""

    def rule(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
        bound = args.bind(parameters)
        name = migrator.class_name(specification)
        if bound is None or set(bound) != set(parameters) or name is None:
            return None
        arguments = ", ".join(bound[parameter] for parameter in parameters)
        return f"{receiver}.annotations.add({name}({arguments}))"

    return rule


#: The NoteIcon member of each icon name that add_text_annot takes.
_NOTE_ICONS = {
    '"Comment"': "COMMENT",
    '"Help"': "HELP",
    '"Insert"': "INSERT",
    '"Key"': "KEY",
    '"NewParagraph"': "NEW_PARAGRAPH",
    '"Note"': "NOTE",
    '"Paragraph"': "PARAGRAPH",
}


def _add_text_annot(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``add_text_annot(point, text, icon=...)`` to ``annotations.add(Note(...))``."""
    bound = args.bind(("point", "text", "icon"))
    note = migrator.class_name("Note")
    if bound is None or not {"point", "text"} <= set(bound) or note is None:
        return None
    arguments = [bound["point"], f"contents={bound['text']}"]
    if "icon" in bound:
        member = _NOTE_ICONS.get(bound["icon"].replace("'", '"'))
        icons = migrator.class_name("NoteIcon")
        if member is None or icons is None:
            return None
        arguments.append(f"icon={icons}.{member}")
    return f"{receiver}.annotations.add({note}({', '.join(arguments)}))"


def _add_freetext_annot(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``add_freetext_annot(rect, text, ...)`` to ``annotations.add(FreeText(...))``."""
    bound = args.bind(("rect", "text", "fontsize", "fontname", "text_color", "fill_color"))
    free_text = migrator.class_name("FreeText")
    if bound is None or not {"rect", "text"} <= set(bound) or "fontname" in bound or free_text is None:
        return None
    arguments = [bound["rect"], bound["text"]]
    if "fontsize" in bound:
        arguments.append(f"size={bound['fontsize']}")
    for keyword, field in (("text_color", "text_color"), ("fill_color", "color")):
        if keyword in bound:
            color = _color_record(migrator, bound[keyword])
            if color is None:
                return None
            arguments.append(f"{field}={color}")
    return f"{receiver}.annotations.add({free_text}({', '.join(arguments)}))"


#: The StampIcon member of each of pymupdf's STAMP_* codes, in their order.
_STAMP_ICONS = (
    "APPROVED",
    "AS_IS",
    "CONFIDENTIAL",
    "DEPARTMENTAL",
    "EXPERIMENTAL",
    "EXPIRED",
    "FINAL",
    "FOR_COMMENT",
    "FOR_PUBLIC_RELEASE",
    "NOT_APPROVED",
    "NOT_FOR_PUBLIC_RELEASE",
    "SOLD",
    "TOP_SECRET",
    "DRAFT",
)


def _add_stamp_annot(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``add_stamp_annot(rect, stamp=N)`` to ``annotations.add(Stamp(...))``."""
    bound = args.bind(("rect", "stamp"))
    stamp = migrator.class_name("Stamp")
    if bound is None or "rect" not in bound or stamp is None:
        return None
    arguments = [bound["rect"]]
    if "stamp" in bound:
        code = bound["stamp"].rsplit(".", 1)[-1]
        if code in _STAMP_ICONS:
            code = f"STAMP_{''.join(part.capitalize() for part in code.split('_'))}"
        names = {f"STAMP_{''.join(part.capitalize() for part in member.split('_'))}": member for member in _STAMP_ICONS}
        member = _STAMP_ICONS[int(code)] if code.isdigit() and int(code) < len(_STAMP_ICONS) else names.get(code)
        icons = migrator.class_name("StampIcon")
        if member is None or icons is None:
            return None
        arguments.append(f"icon={icons}.{member}")
    return f"{receiver}.annotations.add({stamp}({', '.join(arguments)}))"


def _add_file_annot(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``add_file_annot(point, buffer, filename, desc=...)`` to ``annotations.add(FileAttachment(...))``."""
    bound = args.bind(("point", "buffer_", "filename", "ufilename", "desc"))
    attachment = migrator.class_name("FileAttachment")
    if bound is None or not {"point", "buffer_", "filename"} <= set(bound) or attachment is None:
        return None
    arguments = [bound["point"], bound["buffer_"], f"filename={bound.get('ufilename', bound['filename'])}"]
    if "desc" in bound:
        arguments.append(f"description={bound['desc']}")
    return f"{receiver}.annotations.add({attachment}({', '.join(arguments)}))"


def _add_redact_annot(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``add_redact_annot(quad, text=..., fill=..., text_color=...)`` to ``annotations.add(Redaction(...))``."""
    bound = args.bind(("quad", "text", "fontname", "fontsize", "align", "fill", "text_color", "cross_out"))
    redaction = migrator.class_name("Redaction")
    if bound is None or "quad" not in bound or {"fontname", "align", "cross_out"} & set(bound) or redaction is None:
        return None
    arguments = [bound["quad"]]
    if "text" in bound:
        arguments.append(f"overlay_text={bound['text']}")
    if "fontsize" in bound:
        arguments.append(f"size={bound['fontsize']}")
    for keyword in ("fill", "text_color"):
        if keyword in bound:
            if bound[keyword] in ("None", "False"):
                arguments.append(f"{keyword}=None")
                continue
            color = _color_record(migrator, bound[keyword])
            if color is None:
                return None
            arguments.append(f"{keyword}={color}")
    if "fill" not in bound:
        # pymupdf fills with white; Redaction fills with black by default.
        arguments.append(f"fill={migrator.class_name('Color')}(1, 1, 1)")
    return f"{receiver}.annotations.add({redaction}({', '.join(arguments)}))"


def _delete_annot(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``delete_annot(annot)`` as a statement to ``del`` of its index."""
    bound = args.bind(("annot",))
    if bound is None or set(bound) != {"annot"}:
        return None
    annotations = f"{receiver}.annotations"
    return f"del {annotations}[{annotations}.index({bound['annot']})]"


LINK_RECORD_MESSAGE = (
    "page.links lists Link handles instead of dictionaries: read link.rect and link.target, a URI string, "
    "Destination, RemoteDestination, Launch, NamedAction, or None; assigning either edits the link."
)
LINK_HANDLE_MESSAGE = (
    "pylopdf's Link handles have rect and target, a URI string, Destination, RemoteDestination, Launch, "
    "NamedAction, or None, in place of pymupdf's uri, dest, is_external, and next."
)
LINK_KINDS_MESSAGE = "page.links lists every link; test the type of link.target instead of passing kinds."
#: The key of each kind of link dictionary that the target takes first, and
#: the others with the keyword the target takes them as.
LINK_ARGUMENTS: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "GOTO": ("page", (("to", "point"), ("zoom", "zoom"))),
    "GOTOR": ("file", (("page", "page"), ("nameddest", "name"))),
    "LAUNCH": ("file", ()),
    "NAMED": ("name", ()),
}
#: The keys that each kind of pymupdf link dictionary may hold besides ``kind`` and ``from``.
LINK_KEYS = {
    "URI": frozenset({"uri"}),
    "GOTO": frozenset({"page", "to", "zoom"}),
    "GOTOR": frozenset({"file", "page", "nameddest"}),
    "LAUNCH": frozenset({"file"}),
    "NAMED": frozenset({"name"}),
}


def _get_links(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``get_links()`` to the page's link handles."""
    if args.positional or args.keywords:
        return None
    migrator.todo(LINK_RECORD_MESSAGE)
    return f"list({receiver}.links)"


def _links_call(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite pymupdf's ``page.links(kinds=None)`` generator to the ``page.links`` sequence."""
    if not migrator.pymupdf():
        return None
    bound = args.bind(("kinds",))
    if bound is None or bound.get("kinds", "None") != "None":
        migrator.todo(LINK_KINDS_MESSAGE)
        return None
    migrator.todo(LINK_HANDLE_MESSAGE)
    return f"iter({receiver}.links)"


def _first_link(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite pymupdf's ``load_links()`` to the first link handle."""
    if args.positional or args.keywords:
        return None
    migrator.todo(LINK_HANDLE_MESSAGE)
    return f"next(iter({receiver}.links), None)"


def _dict_literal(text: str) -> dict[str, str] | None:
    """Return the entries of a dictionary literal whose keys are string literals, or None."""
    node = cst.parse_expression(text)
    if not isinstance(node, cst.Dict):
        return None
    entries: dict[str, str] = {}
    for element in node.elements:
        if not isinstance(element, cst.DictElement):
            return None
        key = _string_literal(_code(element.key))
        if key is None or key in entries:
            return None
        entries[key] = _code(element.value)
    return entries


def _link_kind(text: str) -> str | None:
    """Return ``URI``, ``GOTO``, and so on for a ``LINK_*`` or ``LinkKind.*`` expression, or None."""
    name = text.rpartition(".")[2]
    kind = name.removeprefix("LINK_") if name.startswith("LINK_") else name
    if text.rpartition(".")[0].endswith("LinkKind") or name.startswith("LINK_"):
        return kind if kind in LINK_KEYS else None
    return None


def _link_target(migrator: _Migrator, kind: str, entries: dict[str, str]) -> str | None:
    """Return the target expression of a pymupdf link dictionary of ``kind``, or None."""
    if kind == "URI":
        return entries.get("uri")
    names = {"GOTO": "Destination", "GOTOR": "RemoteDestination", "LAUNCH": "Launch", "NAMED": "NamedAction"}
    record = migrator.class_name(names[kind])
    if record is None:
        return None
    required, keywords = LINK_ARGUMENTS[kind]
    if required not in entries:
        return None
    arguments = [entries[required], *(f"{keyword}={entries[key]}" for key, keyword in keywords if key in entries)]
    return f"{record}({', '.join(arguments)})"


def _add_link_annot(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite pylopdf 0.15's ``add_link_annot(rect, uri)``."""
    bound = args.bind(("rect", "uri"))
    if bound is None or set(bound) != {"rect", "uri"}:
        return None
    return f"{receiver}.links.add({bound['rect']}, {bound['uri']})"


def _insert_link(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``insert_link({...})`` with a dictionary literal to ``links.add(rect, target)``."""
    bound = args.bind(("lnk", "mark"))
    if bound is None or "lnk" not in bound or bound.get("mark", "True") not in _TRUE:
        return None
    entries = _dict_literal(bound["lnk"])
    if entries is None or "from" not in entries or "kind" not in entries:
        return None
    kind = _link_kind(entries["kind"])
    if kind is None or set(entries) - {"kind", "from", "xref", "id"} - LINK_KEYS[kind]:
        return None
    target = _link_target(migrator, kind, entries)
    return None if target is None else f"{receiver}.links.add({entries['from']}, {target})"


def _set_page_box(attribute: str) -> Callable[[_Migrator, str, _Arguments], str | None]:
    """Rewrite ``set_mediabox(rect)`` and its siblings to an assignment of a ``page.boxes`` attribute."""

    def rule(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
        bound = args.bind(("rect",))
        if bound is None or set(bound) != {"rect"}:
            return None
        if attribute != "media" and migrator.pymupdf():
            migrator.todo(PAGE_BOX_MESSAGE)
        return f"{receiver}.boxes.{attribute} = {bound['rect']}"

    return rule


def _delete_property(prop: str) -> Callable[[_Migrator, str, _Arguments], str | None]:
    """Rewrite a call that removes an entry to ``del`` of ``prop``."""

    def rule(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
        return None if args.positional or args.keywords else f"del {receiver}.{prop}"

    return rule


def _set_language(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``set_language(tag)`` and 0.15's ``set_language_tag(tag)``; ``None`` removes the tag."""
    bound = args.bind(("language",))
    if bound is None or set(bound) != {"language"}:
        return None
    if bound["language"] == "None":
        return f"del {receiver}.language"
    return f"{receiver}.language = {bound['language']}"


def _set_catalog_name(prop: str, names: Sequence[str]) -> Callable[[_Migrator, str, _Arguments], str | None]:
    """Rewrite ``set_pagemode(name)`` and ``set_pagelayout(name)``, matching a literal as pymupdf did."""

    def rule(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
        if len(args.positional) != 1 or args.keywords:
            return None
        literal = _string_literal(args.positional[0])
        if literal is None:
            return None
        matches = [name for name in names if name.lower() == literal.removeprefix("/").lower()]
        return f'{receiver}.{prop} = "{matches[0]}"' if matches else None

    return rule


def _text_length(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``Font.text_length(text, fontsize=11)`` to ``text_width(text, size)``, whose size has no default."""
    bound = args.bind(("text", "fontsize", "language", "script", "wmode", "small_caps", "max_text_size"))
    if bound is None or "text" not in bound:
        return None
    defaults = {"language": ("None",), "script": ("0",), "wmode": ("0",), "small_caps": ("0", "False")}
    if any(bound.get(key, values[0]) not in values for key, values in defaults.items()):
        return None
    arguments = [bound["text"], bound.get("fontsize", "11")]
    if "max_text_size" in bound:
        arguments.append(f"max_text_size={bound['max_text_size']}")
    return f"{receiver}.text_width({', '.join(arguments)})"


def _has_glyph(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite ``has_glyph(ord(char))`` and ``has_glyph(code)`` to take the character itself."""
    bound = args.bind(("chr", "language", "script", "fallback", "small_caps"))
    if bound is None or set(bound) != {"chr"}:
        migrator.todo(HAS_GLYPH_MESSAGE)
        return None
    value = bound["chr"]
    node = cst.parse_expression(value)
    if (
        isinstance(node, cst.Call)
        and _code(node.func) == "ord"
        and len(node.args) == 1
        and node.args[0].keyword is None
        and not node.args[0].star
    ):
        character = _code(node.args[0].value)
    elif _int_literal(value) is not None:
        character = f"chr({value})"
    elif _string_literal(value) is not None:
        return None
    else:
        migrator.todo(HAS_GLYPH_MESSAGE)
        return None
    return f"{receiver}.has_glyph({character})"


#: Calls rewritten wherever they appear.
CALL_RULES: dict[str, Callable[[_Migrator, str, _Arguments], str | None]] = {
    "get_toc": _get_toc,
    "get_page_labels": _no_arguments(
        '[{{"startpage": label.start, "style": label.style or "", "prefix": label.prefix, '
        '"firstpagenum": label.first}} for label in {receiver}.page_labels]'
    ),
    "get_label": _no_arguments("{receiver}.label"),
    "get_form_fields": _no_arguments(
        '[{{"name": name, "type": str(field.type), "value": field.value}} for name, field in {receiver}.form.items()]'
    ),
    "copy_page": _copy_page,
    "fullcopy_page": _copy_page,
    "move_page": _move_page,
    "insert_pdf": _insert_pdf,
    "new_page": _new_page,
    "select": _select,
    "load_page": _load_page,
    "page_xref": _one_argument("pno", "{receiver}.pages[{value}].xref"),
    "get_page_numbers": _get_page_numbers,
    "get_page_text": _page_method("get_text", ("option",)),
    "get_page_pixmap": _page_method(
        "get_pixmap", ("matrix", "dpi", "colorspace", "clip", "alpha", "annots", "background")
    ),
    "get_page_images": _page_method("get_images", ("full",)),
    "render_page": _render_page,
    "render_page_svg": _page_method("svg", ("max_size",)),
    "pages": _pages_call,
    "embfile_names": _no_arguments("list({receiver}.attachments)"),
    "embfile_count": _no_arguments("len({receiver}.attachments)"),
    "embfile_get": _embfile_get,
    "embfile_add": _embfile_add,
    "embfile_upd": _embfile_upd,
    "pdf_catalog": _no_arguments("{receiver}.catalog.xref"),
    "xref_length": _no_arguments("max({receiver}.objects) + 1"),
    "xref_stream": _one_argument("xref", "{receiver}.objects[{value}].read()"),
    "xref_stream_raw": _one_argument("xref", "{receiver}.objects[{value}].read(decode=False)"),
    "update_stream": _update_stream,
    "save": _save,
    "tobytes": _tobytes,
    "write": _tobytes,
    "authenticate": _unlock(note=True),
    "get_text": _get_text,
    "search_for": _search_for,
    "to_markdown": _renamed("markdown"),
    "find_tables": _find_tables,
    "get_text_ocr": _get_text_ocr,
    "get_drawings": _get_drawings,
    "get_cdrawings": _get_drawings,
    "get_image_info": _get_image_info,
    "get_images": _get_images,
    "extract_image": _extract_image,
    "get_pixmap": _get_pixmap,
    "get_svg_image": _get_svg_image,
    "render_pages": _render_pages,
    "render_svg": _renamed("svg"),
    "set_metadata": _set_metadata,
    "get_xml_metadata": _get_xml_metadata,
    "get_pdfa_claim": _get_pdfa_claim,
    "get_links": _get_links,
    "links": _links_call,
    "load_links": _first_link,
    "insert_link": _insert_link,
    "add_link_annot": _add_link_annot,
    "has_links": _no_arguments("any(page.links for page in {receiver}.pages)"),
    "annots": _annots,
    "add_highlight_annot": _add_text_markup_annot("Highlight"),
    "add_underline_annot": _add_text_markup_annot("Underline"),
    "add_strikeout_annot": _add_text_markup_annot("StrikeOut"),
    "add_squiggly_annot": _add_text_markup_annot("Squiggly"),
    "add_text_annot": _add_text_annot,
    "add_redact_annot": _add_redact_annot,
    "add_file_annot": _add_file_annot,
    "add_stamp_annot": _add_stamp_annot,
    "add_caret_annot": _add_shape_annot("Caret", ("point",)),
    "add_freetext_annot": _add_freetext_annot,
    "add_rect_annot": _add_shape_annot("Square"),
    "add_circle_annot": _add_shape_annot("Circle"),
    "add_line_annot": _add_shape_annot("LineAnnotation", ("p1", "p2")),
    "add_polygon_annot": _add_shape_annot("Polygon", ("points",)),
    "add_polyline_annot": _add_shape_annot("PolyLine", ("points",)),
    "add_ink_annot": _add_shape_annot("Ink", ("handwriting",)),
    "has_annots": _no_arguments("any(page.annotations for page in {receiver}.pages)"),
    "text_length": _text_length,
    "has_glyph": _has_glyph,
}


def _in_place(operator: str) -> Callable[[_Migrator, str, _Arguments], str | None]:
    def rule(_: _Migrator, target: str, args: _Arguments) -> str | None:
        if len(args.positional) != 1 or args.keywords:
            return None
        return f"{target} = {target} {operator} {args.positional[0]}"

    return rule


def _normalize(_: _Migrator, target: str, args: _Arguments) -> str | None:
    if args.positional or args.keywords:
        return None
    return f"{target} = {target}.normalized()"


def _concat(_: _Migrator, target: str, args: _Arguments) -> str | None:
    if len(args.positional) != 2 or args.keywords:  # noqa: PLR2004 - pymupdf's two matrices.
        return None
    return f"{target} = {args.positional[0]} * {args.positional[1]}"


def _invert(_: _Migrator, target: str, args: _Arguments) -> str | None:
    if args.keywords or len(args.positional) > 1:
        return None
    source = args.positional[0] if args.positional else target
    return f"{target} = ~{source}"


def _premultiply(count: int, template: str) -> Callable[[_Migrator, str, _Arguments], str | None]:
    def rule(migrator: _Migrator, target: str, args: _Arguments) -> str | None:
        matrix = migrator.matrix_name()
        if matrix is None or len(args.positional) != count or args.keywords:
            return None
        factor = template.format(*args.positional)
        return f"{target} = {matrix}({factor}) * {target}"

    return rule


def _embfile_del(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    bound = args.bind(("item",))
    if bound is None or set(bound) != {"item"} or bound["item"].lstrip("-").isdigit():
        return None
    return f"del {receiver}.attachments[{bound['item']}]"


def _delete_page(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    bound = args.bind(("pno",))
    if bound is None:
        return None
    return f"del {receiver}.pages[{bound.get('pno', '-1')}]"


def _deleted_indexes(text: str) -> list[int] | None:
    """Return the indexes that a literal list, tuple, set, or ``range`` of literals names."""
    node = cst.parse_expression(text)
    if isinstance(node, (cst.List, cst.Tuple, cst.Set)):
        values = [_int_literal(_code(element.value)) for element in node.elements]
        if any(value is None or value < 0 for value in values):
            return None
        return sorted({value for value in values if value is not None})
    # range(stop) and range(start, stop) of literals.
    if isinstance(node, cst.Call) and _code(node.func) == "range" and len(node.args) in {1, 2}:
        bounds = [_int_literal(_code(arg.value)) for arg in node.args]
        valid = (
            value is not None and value >= 0 and not arg.keyword and not arg.star
            for value, arg in zip(bounds, node.args, strict=True)
        )
        if not all(valid):
            return None
        start, stop = (0, bounds[0]) if len(bounds) == 1 else bounds
        return list(range(start or 0, stop or 0))
    return None


def _deleted_slice(args: _Arguments) -> str | None:
    """Return the slice of pages that pymupdf's ``from_page``/``to_page`` forms delete, or None."""
    if args.keywords:
        if args.positional or set(args.keywords) - {"from_page", "to_page"}:
            return None
        first = _int_literal(args.keywords.get("from_page", "-1"))
        last = _int_literal(args.keywords.get("to_page", "-1"))
        if first is None or last is None or first < -1 or last < -1 or (first == -1 and last != -1):
            return None
        if first == -1:
            return "-1"
        return f"{first}:" if last == -1 else f"{first}:{last + 1}"
    first, last = (_int_literal(text) for text in args.positional)
    if first is None or last is None or min(first, last) < 0:
        return None
    return f"{min(first, last)}:{max(first, last) + 1}"


def _delete_pages(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    pages = f"{receiver}.pages"
    if args.keywords or len(args.positional) == 2:  # noqa: PLR2004 - pymupdf's (from_page, to_page) form.
        selected = _deleted_slice(args)
        return None if selected is None else f"del {pages}[{selected}]"
    if len(args.positional) != 1:
        return None
    single = _int_literal(args.positional[0])
    if single is not None:
        return f"del {pages}[{single}]"
    indexes = _deleted_indexes(args.positional[0])
    if not indexes:
        return None
    if indexes == list(range(indexes[0], indexes[-1] + 1)):
        return f"del {pages}[{indexes[0]}:{indexes[-1] + 1}]"
    kept = "{" + ", ".join(map(str, indexes)) + "}"
    return f"{pages}.select(index for index in range(len({pages})) if index not in {kept})"


def _drawing_paint(migrator: _Migrator, color: str, opacity: str | None) -> str | None:
    """Return the ``Color`` of a pymupdf gray or RGB colour and an opacity, or None for CMYK."""
    record = migrator.class_name("Color")
    if record is None:
        return None
    alpha = [] if opacity is None or opacity in {"1", "1.0"} else [opacity]
    node = cst.parse_expression(color)
    if isinstance(node, (cst.Tuple, cst.List)):
        if not all(isinstance(element, cst.Element) for element in node.elements):
            return None
        components = [_code(element.value) for element in node.elements]
        if len(components) == 1:
            components *= 3
        if len(components) != 3:  # noqa: PLR2004 - red, green, and blue.
            return None
        return f"{record}({', '.join([*components, *alpha])})"
    if isinstance(node, (cst.Integer, cst.Float)):
        return f"{record}({', '.join([color] * 3 + alpha)})"
    return f"{record}(*{color}{''.join(f', {value}' for value in alpha)})"


def _line_style(migrator: _Migrator, value: str, names: tuple[str, ...], record: str) -> str | None:
    """Return the ``LineCap`` or ``LineJoin`` member of pymupdf's number, "" for the default, or None."""
    number = _int_literal(value)
    if number is None or not 0 <= number < len(names):
        return None
    enum = migrator.class_name(record)
    return "" if number == 0 else None if enum is None else f"{enum}.{names[number]}"


def _dash_keywords(value: str) -> list[str] | None:
    """Return the ``dashes`` and ``dash_offset`` keywords of a literal pymupdf dash pattern, or None."""
    literal = _string_literal(value)
    match = None if literal is None else DASH_PATTERN.fullmatch(literal)
    if match is None:
        return None
    lengths = match.group(1).split()
    keywords = [f"dashes=({', '.join(lengths)}{',' if len(lengths) == 1 else ''})"] if lengths else []
    offset = match.group(2)
    if offset is not None and float(offset) != 0:
        keywords.append(f"dash_offset={offset}")
    return keywords


def _drawing_stroke(migrator: _Migrator, options: dict[str, str]) -> str | None:
    """Return the canvas ``stroke`` of pymupdf's drawing options, "" for none, or None when it cannot."""
    color = options.get("color", "(0,)")
    if color == "None":
        return ""
    paint = _drawing_paint(migrator, color, options.get("stroke_opacity"))
    width = options.get("width", "1")
    if paint is None or width in {"0", "0.0"}:
        return None
    keywords = [] if width in {"1", "1.0"} else [f"width={width}"]
    for option, names, record, keyword in (
        ("lineCap", LINE_CAPS, "LineCap", "cap"),
        ("lineJoin", LINE_JOINS, "LineJoin", "join"),
    ):
        member = _line_style(migrator, options.get(option, "0"), names, record)
        if member is None:
            return None
        if member:
            keywords.append(f"{keyword}={member}")
    if options.get("dashes", "None") != "None":
        dashes = _dash_keywords(options["dashes"])
        if dashes is None:
            return None
        keywords.extend(dashes)
    stroke = migrator.class_name("Stroke")
    if not keywords:
        return paint
    return None if stroke is None else f"{stroke}(paint={paint}, {', '.join(keywords)})"


def _drawing_options(method: str, args: _Arguments) -> tuple[dict[str, str], dict[str, str]] | None:
    """Return the geometry and the styling options of a ``Page.draw_*`` call that a canvas draws alike."""
    geometry = PAGE_DRAWINGS[method][0]
    bound = args.bind((*geometry, *DRAWING_STYLE))
    if bound is None or len(args.positional) > len(geometry):
        return None
    values = {"breadth": "2", **{key: value for key, value in bound.items() if key in geometry}}
    options = {key: value for key, value in bound.items() if key not in geometry}
    close = options.get("closePath", "False")
    unsupported = (
        any(name not in values for name in geometry)
        or options.get("morph", "None") != "None"
        or options.get("oc", "0") != "0"
        or (method == "draw_rect" and options.get("radius", "None") != "None")
        or close not in _FALSE | _TRUE
        or (method == "draw_bezier" and close in _TRUE)
        or options.get("overlay", "True") not in _FALSE | _TRUE
    )
    return None if unsupported else (values, options)


def _drawing_keywords(migrator: _Migrator, method: str, options: dict[str, str]) -> list[str] | None:
    """Return the ``fill`` and ``stroke`` keywords of a canvas call, or None when they cannot be written."""
    keywords = []
    fill = options.get("fill", "None")
    if fill != "None":
        paint = _drawing_paint(migrator, fill, options.get("fill_opacity"))
        if method not in FILLED_DRAWINGS or paint is None:
            return None
        keywords.append(f"fill={paint}")
    stroke = _drawing_stroke(migrator, options)
    if stroke is None or (not stroke and fill == "None"):
        return None
    # A canvas path without a fill or a stroke is stroked with a 1-point black line, pymupdf's default.
    if stroke and (fill != "None" or stroke != _drawing_paint(migrator, "(0,)", None)):
        keywords.append(f"stroke={stroke}")
    return keywords


def _page_drawing(method: str) -> Callable[[_Migrator, str, _Arguments], str | None]:
    """Rewrite a ``Page.draw_*`` statement to a canvas that draws the same path."""
    template = PAGE_DRAWINGS[method][1]

    def rule(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
        drawing = None if migrator.is_shape(receiver) else _drawing_options(method, args)
        if drawing is None:
            return None
        values, options = drawing
        keywords = _drawing_keywords(migrator, method, options)
        builder = migrator.class_name("PathBuilder")
        if keywords is None or builder is None:
            return None
        close = "" if options.get("closePath", "False") in _TRUE else ", close=False"
        call = template.format(**values, builder=builder, close=close)
        name = migrator.canvas_name()
        draw = "draw(underlay=True)" if options.get("overlay", "True") in _FALSE else "draw()"
        arguments = "".join(f", {keyword}" for keyword in keywords)
        return f"with {receiver}.{draw} as {name}:\n    {name}.{call}{arguments})\n"

    return rule


def _text_options(args: _Arguments, names: tuple[str, ...]) -> dict[str, str] | None:
    """Return the options of an ``insert_text`` or ``insert_textbox`` call that change what it draws."""
    bound = args.bind(names)
    if bound is None or len(args.positional) > 2:  # noqa: PLR2004 - the position and the text.
        return None
    options = {key: value for key, value in bound.items() if value not in TEXT_DEFAULTS.get(key, ())}
    unsupported = {"set_simple", "encoding", "render_mode", "border_width", "miter_limit", "morph"}
    if unsupported & set(options) or {"stroke_opacity", "oc", "fill"} & set(options):
        return None
    return options


def _text_font(migrator: _Migrator, options: dict[str, str]) -> str | None:
    """Return the canvas ``font=`` of the font options of ``insert_text``, "" for the default, or None."""
    font = migrator.class_name("Font")
    sources = [key for key in ("font", "fontfile", "fontbuffer", "font_language") if options.get(key, "None") != "None"]
    loading = [key for key in ("fontindex", "max_font_size") if key in options]
    if font is None or len(sources) > 1 or (loading and sources not in (["fontfile"], ["fontbuffer"])):
        return None
    if sources == ["font"]:
        return options["font"]
    if sources in (["fontfile"], ["fontbuffer"]):
        keywords = [f"{FONT_KEYWORDS[key]}={options[key]}" for key in loading]
        method = "from_file" if sources == ["fontfile"] else "from_bytes"
        return f"{font}.{method}({', '.join([options[sources[0]], *keywords])})"
    name = _standard_font(options.get("fontname", '"helv"'))
    if sources == ["font_language"]:
        serif = ", serif=True" if name is not None and name.startswith("Times") else ""
        return None if name is None else f"{font}.installed(language={options['font_language']}{serif})"
    if name is None:
        return None
    return "" if name == "Helvetica" else f'{font}.standard("{name}")'


def _text_keywords(migrator: _Migrator, options: dict[str, str]) -> list[str] | None:
    """Return the canvas keywords of the font, size, colour, and line options of a text call."""
    font = _text_font(migrator, options)
    keywords = [] if not font else [f"font={font}"]
    if "fontsize" in options:
        keywords.append(f"size={options['fontsize']}")
    color = options.get("color", "None")
    alpha = options.get("fill_opacity")
    if color != "None" or alpha is not None:
        paint = _drawing_paint(migrator, "(0,)" if color == "None" else color, alpha)
        if paint is None:
            return None
        keywords.append(f"fill={paint}")
    if "lineheight" in options:
        keywords.append(f"line_height={options['lineheight']}")
    if "rotate" in options and (rotate := _canvas_rotation(migrator, options["rotate"])) != "0":
        keywords.append(f"rotate={rotate}")
    if "max_text_size" in options:
        keywords.append(f"max_text_size={options['max_text_size']}")
    return None if font is None else keywords


def _canvas_rotation(migrator: _Migrator, rotate: str) -> str:
    """Return the canvas ``rotate=`` of a pymupdf angle, which turns anticlockwise where the canvas turns clockwise."""
    if not migrator.pymupdf():
        return rotate
    angle = _int_literal(rotate)
    return f"-{_receiver(cst.parse_expression(rotate))}" if angle is None else str(-angle % 360)


def _canvas_block(migrator: _Migrator, receiver: str, options: dict[str, str], body: Callable[[str], str]) -> str:
    """Return a ``with`` block that draws ``body(name)``, a call of the canvas ``name``, on ``receiver``."""
    name = migrator.canvas_name()
    draw = "draw(underlay=True)" if options.get("overlay", "True") in _FALSE else "draw()"
    return f"with {receiver}.{draw} as {name}:\n    {body(name)}\n"


def _insert_text(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite an ``insert_text`` statement to a canvas that draws the same lines."""
    options = _text_options(args, TEXT_PARAMETERS)
    if options is None or "point" not in options or "text" not in options:
        return None
    if options.get("overlay", "True") not in _FALSE | _TRUE:
        return None
    keywords = _text_keywords(migrator, options)
    if keywords is None:
        return None
    text = cst.parse_expression(options["text"])
    if isinstance(text, (cst.List, cst.Tuple)):
        # The canvas takes one string and splits it into lines.
        options["text"] = f'"\\n".join({options["text"]})'
    arguments = ", ".join([options["text"], options["point"], *keywords])
    return _canvas_block(migrator, receiver, options, lambda canvas: f"{canvas}.text({arguments})")


def _insert_textbox(target: str | None) -> Callable[[_Migrator, str, _Arguments], str | None]:
    """Rewrite an ``insert_textbox`` statement, or an assignment of its spare height, to a canvas textbox."""

    def rule(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
        options = _text_options(args, TEXTBOX_PARAMETERS)
        if options is None or "rect" not in options or ("buffer" in options) == ("text" in options):
            return None
        if options.get("overlay", "True") not in _FALSE | _TRUE:
            return None
        keywords = _text_keywords(migrator, options)
        if keywords is None:
            return None
        text = options.get("buffer", options.get("text", ""))
        if "expandtabs" in options:
            text = f"({text}).expandtabs({options['expandtabs']})"
        if "align" in options:
            literal = _int_literal(options["align"])
            name = options["align"].rpartition(".")[2]
            index = (
                literal
                if literal is not None
                else next((i for i, value in enumerate(TEXT_ALIGNMENTS) if name.endswith(value.upper())), None)
            )
            if index is None or not 0 <= index < len(TEXT_ALIGNMENTS):
                return None
            if index:
                keywords.insert(0, f'align="{TEXT_ALIGNMENTS[index]}"')
        arguments = ", ".join([options["rect"], text, *keywords])

        def body(canvas: str) -> str:
            call = f"{canvas}.textbox({arguments})"
            return call if target is None else f"{target} = {call}.spare_height"

        return _canvas_block(migrator, receiver, options, body)

    return rule


def _insert_image(target: str | None) -> Callable[[_Migrator, str, _Arguments], str | None]:
    """Rewrite an ``insert_image`` statement to a canvas that draws the same image.

    An assignment of the xref it returned becomes the canvas block and an
    assignment of the xref that the page's content reports for the image,
    the last one it draws or, below the content, the first; that reads the
    page again, so the page must be a name, attribute, or subscript. pymupdf
    turns the image anticlockwise and the canvas, like pylopdf 0.15,
    clockwise, so an angle from pymupdf is negated.
    """

    def rule(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
        block = _image_block(migrator, receiver, args)
        if block is None or target is None:
            return block
        if not _repeatable(cst.parse_expression(receiver)):
            return None
        index = 0 if args.keywords.get("overlay", "True") in _FALSE else -1
        images = f"{receiver}.content(text_runs=False, paths=False, annotations=False).images"
        return f"{block}{target} = {images}[{index}].xref\n"

    return rule


def _image_block(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Return the canvas block that draws the image of an ``insert_image`` call, or None."""
    bound = args.bind(IMAGE_PARAMETERS)
    if bound is None or len(args.positional) > 1 or "rect" not in bound:
        return None
    options = {key: value for key, value in bound.items() if value not in IMAGE_DEFAULTS.get(key, ())}
    sources = [key for key in ("filename", "stream", "pixmap") if key in options]
    unsupported = {"alpha", "height", "width", "mask", "oc", "xref"} & set(options)
    if len(sources) != 1 or unsupported or options.get("overlay", "True") not in _FALSE | _TRUE:
        return None
    keywords = []
    if "keep_proportion" in options:
        keywords.append(f"keep_aspect={options['keep_proportion']}")
    if "rotate" in options and (rotate := _canvas_rotation(migrator, options["rotate"])) != "0":
        keywords.append(f"rotate={rotate}")
    keywords.extend(f"{key}={options[key]}" for key in ("max_size", "max_pixels") if key in options)
    arguments = ", ".join([options["rect"], options[sources[0]], *keywords])
    return _canvas_block(migrator, receiver, options, lambda canvas: f"{canvas}.image({arguments})")


def _show_pdf_page(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
    """Rewrite a ``show_pdf_page`` statement to a canvas that draws the same page."""
    bound = args.bind(PAGE_PLACEMENT_PARAMETERS)
    if bound is None and "src" in args.keywords:
        keywords = {"docsrc" if key == "src" else key: value for key, value in args.keywords.items()}
        bound = _Arguments(args.positional, keywords).bind(PAGE_PLACEMENT_PARAMETERS)
    if bound is None or "rect" not in bound or "docsrc" not in bound:
        return None
    options = {key: value for key, value in bound.items() if value not in PAGE_PLACEMENT_DEFAULTS.get(key, ())}
    if "oc" in options or options.get("overlay", "True") not in _FALSE | _TRUE:
        return None
    source = f"{_receiver(cst.parse_expression(options['docsrc']))}.pages[{options.get('pno', '0')}]"
    keywords = [] if "clip" not in options else [f"clip={options['clip']}"]
    if "keep_proportion" in options:
        keywords.append(f"keep_aspect={options['keep_proportion']}")
    if "rotate" in options and (rotate := _canvas_rotation(migrator, options["rotate"])) != "0":
        keywords.append(f"rotate={rotate}")
    arguments = ", ".join([options["rect"], source, *keywords])
    return _canvas_block(migrator, receiver, options, lambda canvas: f"{canvas}.page({arguments})")


#: Calls rewritten only as a statement of their own: pymupdf's in-place
#: geometry methods become assignments of pylopdf's immutable results, and
#: page deletions become ``del`` statements.
STATEMENT_RULES: dict[str, Callable[[_Migrator, str, _Arguments], str | None]] = {
    "include_rect": _in_place("|"),
    "include_point": _in_place("|"),
    "intersect": _in_place("&"),
    "transform": _in_place("*"),
    "normalize": _normalize,
    "concat": _concat,
    "invert": _invert,
    "prerotate": _premultiply(1, "{}"),
    "prescale": _premultiply(2, "{}, {}"),
    "preshear": _premultiply(2, "{}, {}, 1"),
    "pretranslate": _premultiply(2, "1, 0, 0, 1, {}, {}"),
    "embfile_del": _embfile_del,
    "delete_page": _delete_page,
    "delete_pages": _delete_pages,
    "authenticate": _unlock(note=False),
    "set_xml_metadata": _assign_property("xmp", "metadata"),
    "del_xml_metadata": _delete_property("xmp"),
    "set_language": _set_language,
    "set_language_tag": _set_language,
    "set_pagemode": _set_catalog_name("page_mode", PAGE_MODE_NAMES),
    "set_pagelayout": _set_catalog_name("page_layout", PAGE_LAYOUT_NAMES),
    "set_toc": _set_toc,
    "set_page_labels": _set_page_labels,
    "set_fallback_font": _set_fallback_font,
    "set_form_field": _set_form_field,
    "set_rotation": _set_rotation,
    "set_opacity": _pymupdf_only(_assign_property("opacity", "opacity")),
    "set_flags": _pymupdf_only(_assign_property("flags", "flags")),
    "set_blendmode": _pymupdf_only(_assign_property("blend_mode", "blend_mode")),
    "set_rect": _pymupdf_only(_assign_property("rect", "rect")),
    "set_colors": _pymupdf_only(_set_colors),
    "set_line_ends": _pymupdf_only(_set_line_ends),
    "set_info": _pymupdf_only(_set_info),
    "set_border": _pymupdf_only(_set_border),
    "set_mediabox": _set_page_box("media"),
    "set_cropbox": _set_page_box("crop"),
    "set_artbox": _set_page_box("art"),
    "set_bleedbox": _set_page_box("bleed"),
    "set_trimbox": _set_page_box("trim"),
    "delete_annot": _delete_annot,
    **{method: _page_drawing(method) for method in PAGE_DRAWINGS},
    "insert_text": _insert_text,
    "insert_textbox": _insert_textbox(None),
    "insert_image": _insert_image(None),
    "show_pdf_page": _show_pdf_page,
}
#: Calls rewritten when one name or attribute is assigned their result.
ASSIGNMENT_RULES: dict[str, Callable[[str], Callable[[_Migrator, str, _Arguments], str | None]]] = {
    "insert_textbox": _insert_textbox,
    "insert_image": _insert_image,
}
#: Statement rules that assign to their receiver, which must therefore be a name or an attribute.
ASSIGNING_RULES = frozenset(
    {"include_rect", "include_point", "intersect", "transform", "normalize", "concat", "invert"}
    | {"prerotate", "prescale", "preshear", "pretranslate"}
)


#: Page properties that the API reset renamed, rewritten on names bound only to pages.
PAGE_RENAMES = {"number": "index", "parent": "document"}
#: Members of a ``find_tables`` result and of its tables that have no mechanical replacement.
TABLE_FINDER_MEMBERS = frozenset({"cells", "clip", "page", "strategy"})
TABLE_UNDERSTANDING_MEMBERS = frozenset({"header", "to_pandas"})
TABLE_UNDERSTANDING_MESSAGE = (
    "pymupdf's Table.header and Table.to_pandas() come with the Understanding milestone; read table.rows meanwhile."
)
#: Table attributes of pymupdf and pylopdf 0.15 and what reads them now.
TABLE_RENAMES = {
    "row_count": "len({table}.rows)",
    "col_count": "len({table}.rows[0])",
    "confidence": "{table}.diagnostics.confidence",
    "strategy": "{table}.diagnostics.strategy",
}
PROBE_MESSAGE = (
    "probe() returns a Probe record with page_count, version, needs_password, is_repaired, and metadata, "
    "whose keys are title, author, subject, keywords, creator, producer, created, and modified."
)


def _imported_as(migrator: _Migrator, name: str) -> str | None:
    """Return the local name under which the module imported ``name``, or None."""
    for local, original in migrator._scan.imported.items():  # noqa: SLF001 - the migrator's own scan.
        if original == name:
            return MODULE_RENAMES.get(local, local) if local == original else local
    return None


def _open_source(bound: dict[str, str]) -> tuple[str | None, bool]:
    """Return the source argument of an ``open`` call, or None, and whether the call is supported."""
    filename = None if bound.get("filename") == "None" else bound.get("filename")
    stream = None if bound.get("stream") == "None" else bound.get("stream")
    filetype = bound.get("filetype")
    if stream is not None and filename is not None:
        if filetype is not None:
            return None, False
        filetype, filename = filename, None
    if filetype is not None and filetype.strip("'\"").lower() != "pdf":
        return None, False
    return (stream if stream is not None else filename), True


def _open_document(migrator: _Migrator, prefix: str | None, _: str, args: _Arguments) -> str | None:
    """Rewrite pymupdf's and pylopdf 0.15's ``open``/``Document`` to ``open(source)`` or ``Document()``."""
    bound = args.bind(("filename", "stream", "filetype", "password", "limits", "max_decompressed_size"))
    if bound is None:
        return None
    source, supported = _open_source(bound)
    keywords = [f"password={bound['password']}"] if "password" in bound else []
    limits = bound.get("limits")
    if "max_decompressed_size" in bound:
        limits_class = f"{prefix}DocumentLimits" if prefix else _imported_as(migrator, "DocumentLimits")
        supported &= limits is None and limits_class is not None
        limits = f"{limits_class}(max_decompressed_size={bound['max_decompressed_size']})"
    if limits is not None:
        keywords.append(f"limits={limits}")
    if source is None:
        supported &= "password" not in bound
        function = f"{prefix}Document" if prefix else _imported_as(migrator, "Document")
        arguments = keywords
    else:
        function = f"{prefix}open" if prefix else _imported_as(migrator, "open")
        arguments = [source, *keywords]
    if not supported or function is None:
        return None
    return f"{function}({', '.join(arguments)})"


def _probe(migrator: _Migrator, prefix: str | None, function: str, args: _Arguments) -> str | None:
    """Rewrite pylopdf 0.15's ``peek_metadata`` to ``probe(source)``."""
    bound = args.bind(("filename", "stream", "password", "max_file_size"))
    if bound is None or ("filename" in bound) == ("stream" in bound):
        return None
    source = bound.get("stream", bound.get("filename"))
    keywords = [f"{key}={bound[key]}" for key in ("password", "max_file_size") if key in bound]
    migrator.todo(PROBE_MESSAGE)
    name = f"{prefix}probe" if prefix else function
    return f"{name}({', '.join([str(source), *keywords])})"


def _standard_font(text: str) -> str | None:
    """Return the PostScript name of a string literal that names a Standard 14 font, or None."""
    literal = _string_literal(text)
    if literal is None:
        return None
    names = {name.lower(): name for name in get_args(pylopdf.StandardFontName)}
    return STANDARD_FONT_ALIASES.get(literal.lower()) or names.get(literal.lower())


#: pymupdf's ``Font`` parameters in order, then pylopdf 0.15's keyword-only ones.
FONT_PARAMETERS = (
    "fontname",
    "fontfile",
    "fontbuffer",
    "script",
    "language",
    "ordering",
    "is_bold",
    "is_italic",
    "is_serif",
    "embed",
    "weight",
    "fontindex",
    "family",
    "allow_regular_fallback",
    "max_font_size",
)
#: Font arguments whose value changes nothing.
FONT_DEFAULTS = {
    "script": frozenset({"0"}),
    "ordering": frozenset({"-1"}),
    "embed": frozenset({"1", "True"}),
    "is_bold": _FALSE,
    "is_italic": _FALSE,
    "is_serif": _FALSE,
    "fontindex": frozenset({"0"}),
    "allow_regular_fallback": _FALSE,
}
#: Font loading arguments that pylopdf names otherwise.
FONT_KEYWORDS = {"fontindex": "index", "weight": "weight", "max_font_size": "max_size"}
#: The arguments that name a face, of which a Font call passes one.
FONT_SOURCES = ("fontname", "fontfile", "fontbuffer", "language", "family")
#: pymupdf's options for its own built-in fonts, which pylopdf does not have.
UNSUPPORTED_FONT_OPTIONS = frozenset({"script", "ordering", "embed", "allow_regular_fallback"})


def _font_options(args: _Arguments) -> dict[str, str] | None:
    """Return the arguments of a ``Font`` call that change the face, or None when pylopdf cannot load it."""
    bound = args.bind(FONT_PARAMETERS)
    if bound is None:
        return None
    # None selects the default of every argument but max_font_size, whose None lifts the bound.
    options = {
        key: value
        for key, value in bound.items()
        if (value != "None" or key == "max_font_size") and value not in FONT_DEFAULTS.get(key, ())
    }
    if len([key for key in FONT_SOURCES if key in options]) != 1 or UNSUPPORTED_FONT_OPTIONS & set(options):
        return None
    return options


def _installed_font(function: str, source: str, options: dict[str, str]) -> str | None:
    """Rewrite pylopdf 0.15's ``Font(language=...)`` or ``Font(family=...)`` to ``Font.installed``."""
    if set(options) - {source, "is_bold", "is_italic", "is_serif", "weight"}:
        return None
    if ("is_serif" in options and source == "family") or (
        "is_bold" in options and ("weight" in options or options["is_bold"] not in _TRUE)
    ):
        return None
    keywords = [f"{source}={options[source]}"]
    if "is_serif" in options:
        keywords.append(f"serif={options['is_serif']}")
    weight = "700" if "is_bold" in options else options.get("weight")
    if weight is not None:
        keywords.append(f"weight={weight}")
    if "is_italic" in options:
        keywords.append(f"italic={options['is_italic']}")
    return f"{function}.installed({', '.join(keywords)})"


def _font(migrator: _Migrator, _: str | None, function: str, args: _Arguments) -> str | None:
    """Rewrite pymupdf's and pylopdf 0.15's ``Font(...)`` to the factory that loads the same face."""
    options = _font_options(args)
    replacement = None
    if options is not None:
        source = next(key for key in FONT_SOURCES if key in options)
        if source == "fontname":
            name = _standard_font(options["fontname"])
            if name is not None and set(options) == {"fontname"}:
                replacement = f'{function}.standard("{name}")'
        elif source in {"fontfile", "fontbuffer"}:
            if not set(options) - {source, *FONT_KEYWORDS}:
                keywords = [f"{FONT_KEYWORDS[key]}={options[key]}" for key in FONT_KEYWORDS if key in options]
                method = "from_file" if source == "fontfile" else "from_bytes"
                replacement = f"{function}.{method}({', '.join([options[source], *keywords])})"
        elif not migrator.pymupdf():
            # pymupdf's language= is a hint for shaping; pylopdf 0.15's selects an installed font extra.
            replacement = _installed_font(function, source, options)
    if replacement is None:
        migrator.todo(FONT_MESSAGE)
    return replacement


def _get_text_length(migrator: _Migrator, prefix: str | None, _: str, args: _Arguments) -> str | None:
    """Rewrite pymupdf's ``get_text_length(text, fontname, fontsize)`` to a Standard 14 font's ``text_width``."""
    bound = args.bind(("text", "fontname", "fontsize", "encoding"))
    if bound is None or "text" not in bound or bound.get("encoding", "0") != "0":
        return None
    name = _standard_font(bound.get("fontname", '"helv"'))
    font = f"{prefix}Font" if prefix else migrator.class_name("Font")
    if name is None or font is None:
        return None
    return f'{font}.standard("{name}").text_width({bound["text"]}, {bound.get("fontsize", "11")})'


def _geometry_constant(_: _Migrator, __: str | None, function: str, args: _Arguments) -> str | None:
    """Rewrite a call of ``EMPTY_RECT()`` or ``IdentityMatrix()``, already renamed, to the class attribute."""
    return None if args.positional or args.keywords else function


#: Module calls whose arguments changed, keyed by their name in the module.
MODULE_CALL_RULES: dict[str, Callable[[_Migrator, str | None, str, _Arguments], str | None]] = {
    "open": _open_document,
    "Document": _open_document,
    "peek_metadata": _probe,
    "Font": _font,
    "get_text_length": _get_text_length,
    **dict.fromkeys(GEOMETRY_CONSTANTS, _geometry_constant),
}


#: The fields of the records that replaced pylopdf 0.15's dictionaries, by the kind of value the scan finds.
RECORD_KEYS = {
    "complexity": frozenset(field.name for field in dataclasses.fields(pylopdf.DocumentComplexity)),
    "image_rewrite": frozenset(field.name for field in dataclasses.fields(pylopdf.ImageCompressionResult)),
}
#: The fields of word and block records by their position in pymupdf's tuples.
RECORD_FIELDS = {
    "words": {0: "bbox.x0", 1: "bbox.y0", 2: "bbox.x1", 3: "bbox.y1", 4: "text", 5: "block", 6: "line", 7: "index"},
    "blocks": {0: "bbox.x0", 1: "bbox.y0", 2: "bbox.x1", 3: "bbox.y1", 4: "text"},
    "images": {0: "xref", 2: "width", 3: "height", 4: "bits", 5: "colorspace", 6: "alternate_colorspace"},
}
#: The variable name that a converted loop over unpacked tuples takes.
RECORD_NAMES = {"words": "word", "blocks": "block", "images": "image"}
_Comprehension = cst.ListComp | cst.SetComp | cst.DictComp | cst.GeneratorExp


def _tuple_field(node: cst.Subscript, fields: dict[int, str]) -> str | None:
    """Return the record field that ``node``, an index into a pymupdf tuple, reads, or None."""
    if len(node.slice) != 1:
        return None
    element = node.slice[0].slice
    if isinstance(element, cst.Index):
        index = _int_literal(_code(element.value))
        return None if index is None else fields.get(index)
    if (
        isinstance(element, cst.Slice)
        and element.lower is None
        and element.step is None
        and element.upper is not None
        and _int_literal(_code(element.upper)) == 4  # noqa: PLR2004 - the four box coordinates.
    ):
        return "bbox"
    return None


class _IndexedRecord(cst.CSTTransformer):
    """Turn ``variable[i]`` into the record field at position ``i``, noting any other use of ``variable``."""

    def __init__(self, variable: str, fields: dict[int, str], skip: set[int]) -> None:
        super().__init__()
        self.variable = variable
        self.fields = fields
        self.skip = skip
        self.handled: set[int] = set()
        self.unsupported = False

    def visit_Subscript(self, node: cst.Subscript) -> None:
        if (
            isinstance(node.value, cst.Name)
            and node.value.value == self.variable
            and _tuple_field(node, self.fields) is not None
        ):
            self.handled.add(id(node.value))

    def visit_Name(self, node: cst.Name) -> None:
        if node.value == self.variable and id(node) not in self.handled and id(node) not in self.skip:
            self.unsupported = True

    def leave_Subscript(self, original_node: cst.Subscript, updated_node: cst.Subscript) -> cst.BaseExpression:
        if id(original_node.value) in self.handled:
            return cst.parse_expression(f"{self.variable}.{_tuple_field(original_node, self.fields)}")
        return updated_node


class _UnpackedRecord(cst.CSTTransformer):
    """Turn the names that unpacked a pymupdf tuple into fields of one record variable."""

    def __init__(self, replacements: dict[str, str | None]) -> None:
        super().__init__()
        self.replacements = replacements
        self.names: set[int] = set()
        self.unsupported = False

    def visit_Attribute(self, node: cst.Attribute) -> None:
        self.names.add(id(node.attr))

    def visit_Arg(self, node: cst.Arg) -> None:
        if node.keyword is not None:
            self.names.add(id(node.keyword))

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.BaseExpression:
        if id(original_node) in self.names or original_node.value not in self.replacements:
            return updated_node
        replacement = self.replacements[original_node.value]
        if replacement is None:
            self.unsupported = True
            return updated_node
        return cst.parse_expression(replacement)


def _record_loop(node: cst.CSTNode, target: cst.BaseAssignTargetExpression, kind: str) -> cst.CSTNode | None:
    """Rewrite a loop over pymupdf's word or block tuples to read record fields, or return None.

    ``node`` is a comprehension or a ``for`` statement whose ``target`` takes
    each item. A name target must only be indexed by tuple positions that
    the record has; a tuple target may name the positions that the record has
    and ignore the rest with ``_`` names or a starred name.
    """
    if isinstance(target, cst.Name):
        indexed = _IndexedRecord(target.value, RECORD_FIELDS[kind], {id(target)})
        converted = node.visit(indexed)
        if indexed.unsupported or not indexed.handled or not isinstance(converted, cst.CSTNode):
            return None
        return converted
    if isinstance(target, cst.Tuple):
        return _unpacked_loop(node, target, kind)
    return None


def _unpacked_loop(node: cst.CSTNode, target: cst.Tuple, kind: str) -> cst.CSTNode | None:
    """Rewrite a loop that unpacks pymupdf's tuples to take one record, or return None."""
    fields = RECORD_FIELDS[kind]
    taken = {name.value for name in _names(node)}
    variable = next((name for name in (RECORD_NAMES[kind], "item", "record") if name not in taken), None)
    replacements: dict[str, str | None] = {}
    for position, element in enumerate(target.elements):
        value = element.value
        ignored = isinstance(value, cst.Name) and value.value.startswith("_")
        if isinstance(element, cst.StarredElement):
            if position != len(target.elements) - 1 or not ignored:
                return None
        elif not isinstance(value, cst.Name):
            return None
        elif not ignored:
            field = fields.get(position)
            replacements[value.value] = None if field is None else f"{variable}.{field}"
    if variable is None:
        return None
    unpacked = _UnpackedRecord(replacements)
    converted = node.deep_replace(target, cst.Name(variable)).visit(unpacked)
    return None if unpacked.unsupported or not isinstance(converted, cst.CSTNode) else converted


def _names(node: cst.CSTNode) -> list[cst.Name]:
    """Return every name node in ``node``."""
    collected: list[cst.Name] = []

    class _Collect(cst.CSTVisitor):
        def visit_Name(self, node: cst.Name) -> None:
            collected.append(node)

    node.visit(_Collect())
    return collected


class _Scanner(cst.CSTVisitor):
    """Find the module's pymupdf and pylopdf imports and the names bound to documents and pages.

    A name is a document name when every binding of it in the module assigns
    a document (``fitz.open(...)``, ``Document()``, or a ``with`` target), and
    a page name when every binding assigns a page of a document name; the
    scan runs once for documents and once more for pages.
    """

    def __init__(self, documents: frozenset[str] | None = None, tables: frozenset[str] | None = None) -> None:
        self.found = False
        #: Whether the module imports fitz or pymupdf, not only pylopdf.
        self.pymupdf = False
        #: Local names of the fitz, pymupdf, and pylopdf modules, before the rewrite.
        self.modules: set[str] = set()
        #: Local names imported from fitz or pymupdf, whose class names may differ from pylopdf's.
        self.pymupdf_modules: set[str] = set()
        #: Local names imported from those modules, mapped to their names there.
        self.imported: dict[str, str] = {}
        #: The kinds of value that each name is bound to.
        self.bindings: dict[str, set[str]] = {}
        self._documents = documents
        self._tables = tables

    def names(self, kind: str) -> frozenset[str]:
        """Return the names that only values of ``kind`` are bound to."""
        return frozenset(name for name, kinds in self.bindings.items() if kinds == {kind})

    def member(self, node: cst.BaseExpression) -> str | None:
        """Return the module member that an expression names, such as ``open`` for ``fitz.open``."""
        if isinstance(node, cst.Attribute) and isinstance(node.value, cst.Name) and node.value.value in self.modules:
            return node.attr.value
        if isinstance(node, cst.Name):
            return self.imported.get(node.value)
        return None

    def is_document(self, node: cst.BaseExpression) -> bool:
        return self._documents is not None and isinstance(node, cst.Name) and node.value in self._documents

    def is_tables(self, node: cst.BaseExpression) -> bool:
        """Return whether ``node`` is a ``find_tables`` result: the call, its ``tables``, or a name of one."""
        if isinstance(node, cst.Attribute) and node.attr.value == "tables":
            node = node.value
        if isinstance(node, cst.Call):
            return isinstance(node.func, cst.Attribute) and node.func.attr.value == "find_tables"
        return self._tables is not None and isinstance(node, cst.Name) and node.value in self._tables

    def is_pages(self, node: cst.BaseExpression) -> bool:
        """Return whether ``node`` is a document name or its ``pages``, possibly sliced."""
        if isinstance(node, cst.Subscript) and all(isinstance(element.slice, cst.Slice) for element in node.slice):
            node = node.value
        if isinstance(node, cst.Attribute) and node.attr.value == "pages":
            node = node.value
        return self.is_document(node)

    def kind(self, node: cst.BaseExpression | None) -> str:
        """Classify the value bound to a name."""
        if isinstance(node, cst.Call):
            return self._call_kind(node)
        if isinstance(node, cst.Attribute) and node.attr.value == "complexity":
            return "complexity"
        if isinstance(node, cst.Subscript) and len(node.slice) == 1 and isinstance(node.slice[0].slice, cst.Index):
            if self.is_pages(node.value):
                return "page"
            if self.is_tables(node.value):
                return "table"
        return "other"

    def _call_kind(self, node: cst.Call) -> str:
        """Classify the value that a call returns."""
        member = self.member(node.func)
        if member in DOCUMENT_FACTORIES:
            return "document"
        if member in OTHER_FACTORIES:
            return "factory"
        function = node.func
        if not isinstance(function, cst.Attribute):
            return "other"
        name = function.attr.value
        if (name in PAGE_METHODS and self.is_document(function.value)) or (
            name == "new" and self.is_pages(function.value)
        ):
            return "page"
        if name == "draw" and all(arg.keyword is not None and arg.keyword.value == "underlay" for arg in node.args):
            return "canvas"
        return {
            "new_shape": "shape",
            "find_tables": "tables",
            "rewrite_images": "image_rewrite",
            "compress_images": "image_rewrite",
        }.get(name, "other")

    def bind(self, target: cst.BaseExpression | cst.BaseAssignTargetExpression, kind: str) -> None:
        if isinstance(target, cst.Name):
            self.bindings.setdefault(target.value, set()).add(kind)
        elif isinstance(target, (cst.Tuple, cst.List)):
            for element in target.elements:
                self.bind(element.value, "other")
        elif isinstance(target, cst.StarredElement):
            self.bind(target.value, "other")

    def _bind_loop(self, target: cst.BaseAssignTargetExpression, iterable: cst.BaseExpression) -> None:
        if self.is_pages(iterable):
            self.bind(target, "page")
            return
        if self.is_tables(iterable):
            self.bind(target, "table")
            return
        if (
            isinstance(iterable, cst.Call)
            and _code(iterable.func) == "enumerate"
            and iterable.args
            and self.is_pages(iterable.args[0].value)
            and isinstance(target, cst.Tuple)
            and len(target.elements) == 2  # noqa: PLR2004 - (index, page).
        ):
            self.bind(target.elements[0].value, "other")
            self.bind(target.elements[1].value, "page")
            return
        self.bind(target, "other")

    def visit_Import(self, node: cst.Import) -> None:
        for alias in node.names:
            dotted = _code(alias.name)
            root = dotted.split(".")[0]
            if root in PYMUPDF_MODULES or root == PACKAGE:
                self.found = True
                self.pymupdf |= root in PYMUPDF_MODULES
            if dotted in PYMUPDF_MODULES or dotted == PACKAGE:
                local = _code(alias.asname.name) if alias.asname is not None else dotted
                self.modules.add(local)
                if dotted in PYMUPDF_MODULES:
                    self.pymupdf_modules.add(local)
                self.bind(cst.Name(local), "module")

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        if node.module is None or node.relative:
            return
        module = _code(node.module)
        if module.split(".")[0] not in (*PYMUPDF_MODULES, PACKAGE):
            return
        self.found = True
        self.pymupdf |= module.split(".")[0] in PYMUPDF_MODULES
        if module not in (*PYMUPDF_MODULES, PACKAGE) or isinstance(node.names, cst.ImportStar):
            return
        for alias in node.names:
            name = _code(alias.name)
            self.imported[_code(alias.asname.name) if alias.asname is not None else name] = name

    def visit_Assign(self, node: cst.Assign) -> None:
        kind = self.kind(node.value)
        for target in node.targets:
            self.bind(target.target, kind)

    def visit_AnnAssign(self, node: cst.AnnAssign) -> None:
        self.bind(node.target, self.kind(node.value))

    def visit_AugAssign(self, node: cst.AugAssign) -> None:
        self.bind(node.target, "other")

    def visit_NamedExpr(self, node: cst.NamedExpr) -> None:
        self.bind(node.target, self.kind(node.value))

    def visit_WithItem(self, node: cst.WithItem) -> None:
        if node.asname is not None:
            self.bind(node.asname.name, self.kind(node.item))

    def visit_For(self, node: cst.For) -> None:
        self._bind_loop(node.target, node.iter)

    def visit_CompFor(self, node: cst.CompFor) -> None:
        self._bind_loop(node.target, node.iter)

    def visit_Param(self, node: cst.Param) -> None:
        self.bind(node.name, "other")

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        self.bind(node.name, "other")

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        self.bind(node.name, "other")

    def visit_ExceptHandler(self, node: cst.ExceptHandler) -> None:
        if node.name is not None:
            self.bind(node.name.name, "other")


class _Migrator(cst.CSTTransformer):
    """Rewrite one module; each hint comment attaches to the innermost enclosing statement."""

    def __init__(self, scan: _Scanner, documents: frozenset[str], items: _Scanner) -> None:
        super().__init__()
        self.rewrites = 0
        #: Local names bound to the package, after the rewrite.
        self.module_names: set[str] = set()
        #: Local names that ``import fitz`` or a renamed import bound, and what they read now.
        self.renamed: dict[str, str] = {}
        #: Local names of imported module constants and the enum members that replace them.
        self.member_renamed: dict[str, str] = {}
        #: Names that ``from fitz import ...`` or ``from pylopdf import ...`` bound.
        self.imported: set[str] = set()
        self._scan = scan
        self._documents = documents
        #: The second scan, which knows the names bound to pages, ``find_tables`` results, and tables.
        self._items = items
        self._pages = items.names("page")
        self._tables = items.names("table")
        #: Names bound to a value that a module factory returned that is not a document.
        self._factories = scan.names("factory")
        #: Names bound to pylopdf 0.15's dictionaries that became records, by the kind of record.
        self._records = {name: kind for kind in RECORD_KEYS for name in scan.names(kind)}
        #: Names bound to pymupdf's and pylopdf 0.15's ``Shape``, whose drawing methods have no rule.
        self._shapes = scan.names("shape")
        self._todos: list[list[str]] = [[]]
        self._attribute_names: set[int] = set()
        self._call_functions: set[int] = set()
        self._statement_calls: set[int] = set()
        #: Module attributes that an enclosing call's rule replaces, which need no hint of their own.
        self._consumed: set[int] = set()
        #: Subscripts that are assigned or deleted, which keep their form.
        self._targets: set[int] = set()
        #: Canvas blocks that statement rules wrote, by identity; consecutive ones on one page are joined.
        self._canvas_blocks: dict[int, cst.With] = {}
        #: The names of the canvas blocks that enclose the statement being rewritten.
        self._open_canvases: list[str] = []

    # Hint comments ------------------------------------------------------

    def todo(self, message: str) -> None:
        # One entry per emission, so that a rewrite that makes one unnecessary withdraws only its own.
        self._todos[-1].append(message)

    def _withdraw(self, message: str) -> None:
        self._todos[-1].remove(message)

    def _push(self) -> None:
        self._todos.append([])

    def _pop(self, node: cst.CSTNode) -> cst.CSTNode:
        messages = self._todos.pop()
        leading: Sequence[cst.EmptyLine] | None = getattr(node, "leading_lines", None)
        if not messages or leading is None:
            self._todos[-1].extend(messages)
            return node
        comments = [cst.EmptyLine(comment=cst.Comment(f"# {TODO}: {message}")) for message in dict.fromkeys(messages)]
        return node.with_changes(leading_lines=[*leading, *comments])

    def module_name(self) -> str | None:
        """Return how this module names the package, or None."""
        return min(self.module_names) if self.module_names else None

    def class_name(self, name: str) -> str | None:
        """Return how this module can name the package's class ``name``, or None."""
        if name in self.imported:
            return name
        module = self.module_name()
        return None if module is None else f"{module}.{name}"

    def matrix_name(self) -> str | None:
        """Return how this module can name ``pylopdf.Matrix``, or None."""
        return self.class_name("Matrix")

    def pymupdf(self) -> bool:
        """Return whether the module imports pymupdf rather than pylopdf 0.15."""
        return self._scan.pymupdf

    def is_page(self, receiver: str) -> bool:
        """Return whether ``receiver`` is a name bound only to pages or an item of a document's ``pages``."""
        expression = cst.parse_expression(receiver)
        if isinstance(expression, cst.Name):
            return expression.value in self._pages
        return (
            isinstance(expression, cst.Subscript)
            and isinstance(expression.value, cst.Attribute)
            and expression.value.attr.value == "pages"
        )

    def is_shape(self, receiver: str) -> bool:
        """Return whether ``receiver`` is a ``Shape``: a name bound to one, or a ``new_shape()`` call."""
        expression = cst.parse_expression(receiver)
        if isinstance(expression, cst.Name):
            return expression.value in self._shapes
        return (
            isinstance(expression, cst.Call)
            and isinstance(expression.func, cst.Attribute)
            and expression.func.attr.value == "new_shape"
        )

    def canvas_name(self) -> str:
        """Return the name that a rewrite binds a canvas to: ``canvas`` where that is free.

        The module must bind the name to canvases only, and no enclosing canvas
        block may use it, which the new block would rebind.
        """
        name = "canvas"
        while self._scan.bindings.get(name, {"canvas"}) != {"canvas"} or name in self._open_canvases:
            name = f"page_{name}"
        return name

    def is_document(self, receiver: str) -> bool:
        """Return whether ``receiver`` is a known document name or document factory call."""
        if receiver in self._documents:
            return True
        expression = cst.parse_expression(receiver)
        if isinstance(expression, cst.Name):
            return expression.value in self._documents
        if not isinstance(expression, cst.Call):
            return False
        function = expression.func
        if isinstance(function, cst.Attribute) and isinstance(function.value, cst.Name):
            return function.value.value in self.module_names and function.attr.value in DOCUMENT_FACTORIES
        return isinstance(function, cst.Name) and self._scan.imported.get(function.value) in DOCUMENT_FACTORIES

    def _member_todo(self, name: str) -> None:
        if not self._scan.pymupdf and name not in _removed_015_members():
            # A pylopdf program can only miss what pylopdf 0.15 had; other names, such as re.search, are not ours.
            return
        if name in ROTATION_NAMES:
            self.todo(ROTATION_MESSAGE)
            return
        hint = _member_hints(self._scan.pymupdf).get(name)
        if hint is not None:
            self.todo(hint)

    def _is_document(self, node: cst.BaseExpression) -> bool:
        return isinstance(node, cst.Name) and node.value in self._documents

    def _is_table(self, node: cst.BaseExpression) -> bool:
        """Return whether ``node`` is one table of a ``find_tables`` result."""
        if isinstance(node, cst.Name):
            return node.value in self._tables
        return (
            isinstance(node, cst.Subscript)
            and len(node.slice) == 1
            and isinstance(node.slice[0].slice, cst.Index)
            and self._items.is_tables(node.value)
        )

    def _pages_of(self, node: cst.BaseExpression) -> cst.BaseExpression:
        """Return ``node.pages`` for a document name, and ``node`` otherwise."""
        if self._is_document(node):
            self.rewrites += 1
            return cst.Attribute(value=node, attr=cst.Name("pages"))
        return node

    # Statements -----------------------------------------------------------

    def visit_SimpleStatementLine(self, node: cst.SimpleStatementLine) -> None:
        self._push()
        if len(node.body) == 1 and isinstance(node.body[0], cst.Expr):
            call = node.body[0].value
            if (
                isinstance(call, cst.Call)
                and isinstance(call.func, cst.Attribute)
                and call.func.attr.value in STATEMENT_RULES
                and (self._scan.pymupdf or call.func.attr.value not in ASSIGNING_RULES)
            ):
                self._statement_calls.add(id(call))
        elif (assigned := self._assigned_call(node)) is not None:
            self._statement_calls.add(id(assigned))

    @staticmethod
    def _assigned_call(node: cst.SimpleStatementLine) -> cst.Call | None:
        """Return the call of an assignment rule whose result one name or attribute is assigned, or None."""
        if len(node.body) != 1 or not isinstance(statement := node.body[0], cst.Assign):
            return None
        call = statement.value
        if (
            len(statement.targets) == 1
            and isinstance(statement.targets[0].target, (cst.Name, cst.Attribute))
            and isinstance(call, cst.Call)
            and isinstance(call.func, cst.Attribute)
            and call.func.attr.value in ASSIGNMENT_RULES
        ):
            return call
        return None

    def leave_SimpleStatementLine(
        self, original_node: cst.SimpleStatementLine, updated_node: cst.SimpleStatementLine
    ) -> cst.SimpleStatementLine | cst.BaseCompoundStatement | cst.FlattenSentinel[cst.BaseStatement]:
        statement = original_node.body[0] if len(original_node.body) == 1 else None
        target = None
        if isinstance(statement, cst.Assign) and isinstance(updated_node.body[0], cst.Assign):
            target = _code(updated_node.body[0].targets[0].target)
        if (
            isinstance(statement, (cst.Expr, cst.Assign))
            and id(statement.value) in self._statement_calls
            and isinstance(updated := updated_node.body[0], (cst.Expr, cst.Assign))
            and isinstance(call := updated.value, cst.Call)
            and isinstance(function := call.func, cst.Attribute)
        ):
            name = function.attr.value
            arguments = _Arguments.of(call)
            replacement = None
            rule = STATEMENT_RULES[name] if target is None else ASSIGNMENT_RULES[name](target)
            if arguments is not None and (
                name not in ASSIGNING_RULES or isinstance(function.value, (cst.Name, cst.Attribute))
            ):
                replacement = rule(self, _receiver(function.value), arguments)
            if replacement is None:
                self._member_todo(name)
            else:
                self.rewrites += 1
                first, *rest = cst.parse_module(replacement).body
                if rest or not isinstance(first, cst.SimpleStatementLine):
                    # A canvas block replaces the statement and keeps its comments.
                    # An assignment of what it placed may follow it, and reads the
                    # page as this block alone leaves it, so no other block joins it.
                    compound = self._pop(first.with_changes(leading_lines=updated_node.leading_lines))
                    if isinstance(compound, cst.With) and not rest:
                        self._canvas_blocks[id(compound)] = compound
                    if rest:
                        return cst.FlattenSentinel([cast("cst.BaseStatement", compound), *rest])
                    return cast("cst.BaseCompoundStatement", compound)
                updated_node = updated_node.with_changes(body=[first.body[0]])
        result = self._pop(updated_node)
        assert isinstance(result, cst.SimpleStatementLine)  # noqa: S101 - _pop only adds comments.
        return result

    def visit_With(self, node: cst.With) -> None:
        self._open_canvases.extend(
            item.asname.name.value
            for item in node.items
            if item.asname is not None
            and isinstance(item.asname.name, cst.Name)
            and self._scan.kind(item.item) == "canvas"
        )

    def leave_With(self, original_node: cst.With, updated_node: cst.With) -> cst.With:
        for item in original_node.items:
            if (
                item.asname is not None
                and isinstance(item.asname.name, cst.Name)
                and self._scan.kind(item.item) == "canvas"
            ):
                self._open_canvases.pop()
        return updated_node

    def leave_IndentedBlock(
        self, original_node: cst.IndentedBlock, updated_node: cst.IndentedBlock
    ) -> cst.IndentedBlock:
        return updated_node.with_changes(body=self._join_canvas_blocks(updated_node.body))

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        return updated_node.with_changes(body=self._join_canvas_blocks(updated_node.body))

    def _join_canvas_blocks(self, body: Sequence[cst.BaseStatement]) -> list[cst.BaseStatement]:
        """Join consecutive canvas blocks that rules wrote for one page, so that one canvas draws them all.

        A joined block commits once, after its last call, so a later call
        must not be able to read what an earlier one drew: its arguments may
        hold names, attributes, literals, and the package's own constructors,
        but no other call, and it places no page, which could be this one.
        Blank lines and comments keep blocks apart.
        """
        joined: list[cst.BaseStatement] = []
        for statement in body:
            previous = joined[-1] if joined else None
            if (
                isinstance(previous, cst.With)
                and isinstance(statement, cst.With)
                and id(previous) in self._canvas_blocks
                and id(statement) in self._canvas_blocks
                and not statement.leading_lines
                and _code(previous.items[0]) == _code(statement.items[0])
                and isinstance(header := previous.items[0].item, cst.Call)
                and isinstance(header.func, cst.Attribute)
                and _repeatable(header.func.value)
                and isinstance(previous.body, cst.IndentedBlock)
                and isinstance(statement.body, cst.IndentedBlock)
                and self._reads_no_page(statement)
            ):
                block = previous.with_changes(
                    body=previous.body.with_changes(body=[*previous.body.body, *statement.body.body])
                )
                self._canvas_blocks[id(block)] = block
                joined[-1] = block
            else:
                joined.append(statement)
        return joined

    def _reads_no_page(self, block: cst.With) -> bool:
        """Return whether ``block`` calls only its canvas but ``page``, string methods, and the package's names."""
        alias = block.items[0].asname
        canvas = alias.name if alias is not None else None
        for call in _calls(block.body):
            function = call.func
            if isinstance(function, cst.Attribute) and function.attr.value == "page":
                return False
            root = function.value if isinstance(function, cst.Attribute) else function
            while isinstance(root, cst.Attribute):
                root = root.value
            if isinstance(root, cst.SimpleString) and root is not function:
                continue
            if not isinstance(root, cst.Name) or not (
                (isinstance(canvas, cst.Name) and root.value == canvas.value and root is not function)
                or root.value in self.module_names
                or (root is function and root.value in self.imported)
            ):
                return False
        return True

    def on_visit(self, node: cst.CSTNode) -> bool:
        """Open a hint list for every compound statement, whose header can use pymupdf names."""
        if isinstance(node, COMPOUND_STATEMENTS):
            self._push()
        return super().on_visit(node)

    def on_leave(
        self, original_node: cst.CSTNode, updated_node: cst.CSTNode
    ) -> cst.CSTNode | cst.RemovalSentinel | cst.FlattenSentinel[cst.CSTNode]:
        """Attach a compound statement's hints above it."""
        result = super().on_leave(original_node, updated_node)
        if isinstance(original_node, COMPOUND_STATEMENTS) and isinstance(result, cst.CSTNode):
            return self._pop(result)
        return result

    def leave_For(self, original_node: cst.For, updated_node: cst.For) -> cst.For:
        updated_node = updated_node.with_changes(iter=self._pages_of(updated_node.iter))
        kind = self._record_kind(updated_node.iter)
        if kind is None:
            return updated_node
        converted = _record_loop(updated_node, updated_node.target, kind)
        if converted is None:
            return updated_node
        self._withdraw(RECORD_MESSAGES[kind])
        assert isinstance(converted, cst.For)  # noqa: S101 - the loop stays a loop.
        return converted

    def leave_ListComp(self, original_node: cst.ListComp, updated_node: cst.ListComp) -> cst.BaseExpression:
        return self._record_comprehension(updated_node)

    def leave_SetComp(self, original_node: cst.SetComp, updated_node: cst.SetComp) -> cst.BaseExpression:
        return self._record_comprehension(updated_node)

    def leave_DictComp(self, original_node: cst.DictComp, updated_node: cst.DictComp) -> cst.BaseExpression:
        return self._record_comprehension(updated_node)

    def leave_GeneratorExp(self, original_node: cst.GeneratorExp, updated_node: cst.GeneratorExp) -> cst.BaseExpression:
        return self._record_comprehension(updated_node)

    def _record_kind(self, iterable: cst.BaseExpression) -> str | None:
        """Return ``words``, ``blocks``, or ``images`` for an iterable that a rewrite just produced."""
        code = _code(iterable)
        for kind, suffix in (("words", ".words()"), ("blocks", ".layout().blocks"), ("images", ".resources.images")):
            if code.endswith(suffix) and RECORD_MESSAGES[kind] in self._todos[-1]:
                return kind
        return None

    def _record_comprehension(self, node: _Comprehension) -> _Comprehension:
        """Read the fields of records where a comprehension indexed pymupdf's tuples."""
        for_: cst.CompFor | None = node.for_in
        while for_ is not None:
            kind = self._record_kind(for_.iter)
            if kind is not None:
                converted = _record_loop(node, for_.target, kind)
                if converted is not None:
                    self._withdraw(RECORD_MESSAGES[kind])
                    return cast("_Comprehension", converted)
            for_ = for_.inner_for_in
        return node

    def leave_CompFor(self, original_node: cst.CompFor, updated_node: cst.CompFor) -> cst.CompFor:
        return updated_node.with_changes(iter=self._pages_of(updated_node.iter))

    # Imports ----------------------------------------------------------------

    def leave_Import(self, original_node: cst.Import, updated_node: cst.Import) -> cst.Import:
        names = []
        for alias in updated_node.names:
            dotted = _code(alias.name)
            if dotted in PYMUPDF_MODULES or dotted == PACKAGE:
                if dotted != PACKAGE:
                    self.rewrites += 1
                    alias = alias.with_changes(name=cst.Name(PACKAGE))  # noqa: PLW2901
                if alias.asname is None:
                    self.renamed[dotted] = PACKAGE
                    self.module_names.add(PACKAGE)
                else:
                    local = alias.asname.name
                    assert isinstance(local, cst.Name)  # noqa: S101 - import aliases are names.
                    self.module_names.add(local.value)
            elif dotted.split(".")[0] in PYMUPDF_MODULES:
                self.todo(f"pylopdf has no {dotted} module; import pylopdf instead.")
            names.append(alias)
        return updated_node.with_changes(names=names)

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> cst.ImportFrom | cst.RemovalSentinel:
        if original_node.module is None or original_node.relative:
            return updated_node
        # The original name: a preceding ``import fitz`` already renamed this one.
        module = _code(original_node.module)
        if module not in (*PYMUPDF_MODULES, PACKAGE):
            if module.split(".")[0] in PYMUPDF_MODULES:
                self.todo(f"pylopdf has no {module} module; import from pylopdf instead.")
            return updated_node
        if module != PACKAGE:
            self.rewrites += 1
            updated_node = updated_node.with_changes(module=cst.Name(PACKAGE))
        if isinstance(updated_node.names, cst.ImportStar):
            return updated_node
        listed = {_code(alias.name) for alias in updated_node.names if alias.asname is None}
        aliases = [
            kept for alias in updated_node.names if (kept := self._import_alias(alias, module, listed)) is not None
        ]
        if len(aliases) < len(updated_node.names):
            if not aliases:
                # Another statement imports the enum already.
                return cst.RemoveFromParent()
            if updated_node.lpar is None:
                aliases[-1] = aliases[-1].with_changes(comma=cst.MaybeSentinel.DEFAULT)
        return updated_node.with_changes(names=aliases)

    def _import_alias(self, alias: cst.ImportAlias, module: str, listed: set[str]) -> cst.ImportAlias | None:
        """Rewrite one name of a ``from ... import``, or return None when the statement no longer needs it."""
        name = _code(alias.name)
        local = _code(alias.asname.name) if alias.asname is not None else name
        renames = PYMUPDF_MODULE_RENAMES if module in PYMUPDF_MODULES else {}
        if name in MODULE_RENAMES or name in renames:
            self.rewrites += 1
            renamed = MODULE_RENAMES.get(name) or renames[name]
            alias = alias.with_changes(name=cst.Name(renamed))
            if alias.asname is None:
                self.renamed[name] = renamed
                local = renamed
        elif name in MODULE_MEMBER_RENAMES:
            # The constant's uses read the member of its enum, which the statement imports in its place.
            self.rewrites += 1
            enum_name = MODULE_MEMBER_RENAMES[name].split(".")[0]
            self.member_renamed[local] = MODULE_MEMBER_RENAMES[name]
            if enum_name in self.imported or enum_name in listed or self._scan.imported.get(enum_name) == enum_name:
                return None
            local = enum_name
            alias = cst.ImportAlias(name=cst.Name(enum_name))
        elif not hasattr(pylopdf, name):
            self.todo(migration_hint(name) or f"pylopdf does not provide pymupdf's {name}.")
        self.imported.add(local)
        return alias

    # Expressions ------------------------------------------------------------

    def visit_Attribute(self, node: cst.Attribute) -> None:
        self._attribute_names.add(id(node.attr))

    def visit_Call(self, node: cst.Call) -> None:
        self._call_functions.add(id(node.func))
        if isinstance(node.func, cst.Attribute) and node.func.attr.value in {"save", "tobytes", "write"}:
            # The save rules turn a PDF_ENCRYPT_* argument into Encryption(...), or mark the call.
            self._consumed.update(
                id(arg.value) for arg in node.args if arg.keyword and arg.keyword.value == "encryption"
            )
        if isinstance(node.func, cst.Attribute) and node.func.attr.value == "insert_textbox":
            # The insert_textbox rule turns a TEXT_ALIGN_* argument into an align option, or marks the call.
            for arg in node.args:
                part = arg.value if arg.keyword is not None and arg.keyword.value == "align" else None
                while isinstance(part, cst.Attribute):
                    self._consumed.add(id(part))
                    part = part.value
        if isinstance(node.func, cst.Attribute) and node.func.attr.value == "insert_link" and node.args:
            # The insert_link rule turns the kind of a link dictionary literal into a target, or marks the call.
            link = node.args[0].value
            kinds = [
                element.value
                for element in (link.elements if isinstance(link, cst.Dict) else ())
                if isinstance(element, cst.DictElement) and _string_literal(_code(element.key)) == "kind"
            ]
            for kind in kinds:
                # Both fitz.LINK_URI and pylopdf.LinkKind.URI.
                part = kind
                while isinstance(part, cst.Attribute):
                    self._consumed.add(id(part))
                    part = part.value

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.BaseExpression:
        if id(original_node) not in self._attribute_names and updated_node.value in self.renamed:
            return updated_node.with_changes(value=self.renamed[updated_node.value])
        if id(original_node) not in self._attribute_names and updated_node.value in self.member_renamed:
            return cst.parse_expression(self.member_renamed[updated_node.value])
        return updated_node

    def visit_AssignTarget(self, node: cst.AssignTarget) -> None:
        self._targets.add(id(node.target))

    def visit_AugAssign(self, node: cst.AugAssign) -> None:
        self._targets.add(id(node.target))

    def visit_Del(self, node: cst.Del) -> None:
        self._targets.add(id(node.target))

    def leave_Subscript(self, original_node: cst.Subscript, updated_node: cst.Subscript) -> cst.BaseExpression:
        updated_node = updated_node.with_changes(value=self._pages_of(updated_node.value))
        if id(original_node) not in self._targets:
            metadata = self._metadata_key(updated_node)
            if metadata is not None:
                self.rewrites += 1
                return metadata
            field = self._record_key(original_node)
            if field is not None:
                self.rewrites += 1
                return cst.Attribute(value=updated_node.value, attr=cst.Name(field))
        return self._extracted_image_key(updated_node)

    def _record_key(self, node: cst.Subscript) -> str | None:
        """Return the field that a key of pylopdf 0.15's complexity or ``rewrite_images`` dictionary reads, or None."""
        value = node.value
        kind = self._records.get(value.value) if isinstance(value, cst.Name) else self._scan.kind(value)
        if kind not in RECORD_KEYS or len(node.slice) != 1 or not isinstance(node.slice[0].slice, cst.Index):
            return None
        key = _string_literal(_code(node.slice[0].slice.value))
        return key if key in RECORD_KEYS[kind] else None

    def _metadata_key(self, node: cst.Subscript) -> cst.BaseExpression | None:
        """Read a key of pymupdf's or pylopdf 0.15's metadata dictionary from ``doc.metadata``.

        Those dictionaries held every key, ``""`` when absent, so a read
        becomes ``get(key, "")``; in a module that imports only pylopdf, only
        the keys that changed are rewritten.
        """
        code = _code(node.value)
        if not code.endswith(".metadata") or len(node.slice) != 1 or not isinstance(node.slice[0].slice, cst.Index):
            return None
        key = _string_literal(_code(node.slice[0].slice.value))
        return None if key is None else self._metadata_read(code, key, None)

    def _metadata_read(self, code: str, key: str, default: str | None) -> cst.BaseExpression | None:
        """Return what reads ``key`` of the metadata at ``code``, or None to keep the read."""
        if key in METADATA_FACTS:
            return cst.parse_expression(METADATA_FACTS[key].format(receiver=code.removesuffix(".metadata")))
        if key not in METADATA_KEY_RENAMES and not (self._scan.pymupdf and key in METADATA_KEYS and default is None):
            return None
        return cst.parse_expression(f'{code}.get("{METADATA_KEY_RENAMES.get(key, key)}", {default or chr(34) * 2})')

    def _extracted_image_key(self, node: cst.Subscript) -> cst.BaseExpression:
        """Read a key of ``extract_image``'s dictionary from the image handle or its record."""
        code = _code(node.value)
        if (
            not code.endswith(".extract()")
            or EXTRACTED_IMAGE_MESSAGE not in self._todos[-1]
            or len(node.slice) != 1
            or not isinstance(node.slice[0].slice, cst.Index)
        ):
            return node
        key = _string_literal(_code(node.slice[0].slice.value))
        if key not in EXTRACTED_IMAGE_KEYS:
            return node
        self._withdraw(EXTRACTED_IMAGE_MESSAGE)
        return cst.parse_expression(code.removesuffix(".extract()") + EXTRACTED_IMAGE_KEYS[key])

    def leave_Attribute(self, original_node: cst.Attribute, updated_node: cst.Attribute) -> cst.BaseExpression:
        name = updated_node.attr.value
        called = id(original_node) in self._call_functions
        if isinstance(original_node.value, cst.Name) and original_node.value.value in self._scan.modules:
            return self._module_attribute(original_node, updated_node, called=called)
        table_hint = self._table_member_hint(original_node)
        if table_hint is not None:
            self.todo(table_hint)
            return updated_node
        if not called:
            replacement = self._attribute_rule(original_node, updated_node)
            if replacement is not None:
                self.rewrites += 1
                return replacement
        if name in CALL_RULES or name in STATEMENT_RULES:
            if not called:
                self._member_todo(name)
            return updated_node
        self._member_todo(name)
        return updated_node

    def _table_member_hint(self, node: cst.Attribute) -> str | None:
        """Return the hint for a table member without a mechanical replacement, or None."""
        name = node.attr.value
        if name in TABLE_FINDER_MEMBERS and self._items.is_tables(node.value):
            return migration_hint(f"TableFinder.{name}")
        if self._is_table(node.value):
            if name == "page":
                return migration_hint("Table.page")
            if name in TABLE_UNDERSTANDING_MEMBERS:
                return TABLE_UNDERSTANDING_MESSAGE
        return None

    def _module_attribute(
        self, original_node: cst.Attribute, updated_node: cst.Attribute, *, called: bool
    ) -> cst.BaseExpression:
        """Rewrite a module attribute, such as ``fitz.FileDataError``, or mark one that pylopdf lacks."""
        name = updated_node.attr.value
        if name in MODULE_TO_BUILTIN:
            self.rewrites += 1
            return cst.Name(MODULE_TO_BUILTIN[name])
        if name in MODULE_RENAMES and not (called and name in MODULE_CALL_RULES):
            self.rewrites += 1
            return updated_node.with_changes(attr=cst.Name(MODULE_RENAMES[name]))
        if (
            name in PYMUPDF_MODULE_RENAMES
            and isinstance(original_node.value, cst.Name)
            and original_node.value.value in self._scan.pymupdf_modules
        ):
            self.rewrites += 1
            return updated_node.with_changes(attr=cst.Name(PYMUPDF_MODULE_RENAMES[name]))
        if name in MODULE_MEMBER_RENAMES:
            self.rewrites += 1
            return cst.parse_expression(f"{_code(updated_node.value)}.{MODULE_MEMBER_RENAMES[name]}")
        if (
            not (called and name in MODULE_CALL_RULES)
            and id(original_node) not in self._consumed
            and not hasattr(pylopdf, name)
            and not (name.startswith("__") and name.endswith("__"))
        ):
            self.todo(migration_hint(name) or f"pylopdf does not provide pymupdf's {name}.")
        return updated_node

    def _attribute_rule(self, original_node: cst.Attribute, updated_node: cst.Attribute) -> cst.BaseExpression | None:
        """Rewrite a renamed property that is read rather than called."""
        name = original_node.attr.value
        receiver = original_node.value
        if name == "page_count" and not (
            (isinstance(receiver, cst.Name) and (receiver.value in self._factories or receiver.value in self._records))
            or (isinstance(receiver, cst.Call) and self._scan.member(receiver.func) in OTHER_FACTORIES)
            or self._scan.kind(receiver) == "complexity"
        ):
            return cst.parse_expression(f"len({_receiver(updated_node.value)}.pages)")
        if name in {"needs_pass", "is_encrypted"}:
            return updated_node.with_changes(attr=cst.Name("needs_password"))
        if isinstance(receiver, cst.Name) and receiver.value in self._pages and name in PAGE_RENAMES:
            return updated_node.with_changes(attr=cst.Name(PAGE_RENAMES[name]))
        if name == "tables" and self._items.is_tables(receiver):
            # page.tables() returns the list that find_tables(...).tables held.
            return updated_node.value
        if self._is_table(receiver) and name in TABLE_RENAMES:
            return cst.parse_expression(TABLE_RENAMES[name].format(table=_receiver(updated_node.value)))
        return self._property_rule(name, updated_node)

    def _property_rule(self, name: str, updated_node: cst.Attribute) -> cst.BaseExpression | None:
        """Rewrite a document, page, or pixmap property that the API reset renamed, whatever its receiver."""
        if name == "outline" and self._scan.pymupdf:
            self.todo(OUTLINE_MESSAGE)
            return None
        if name == "mediabox" or name in PAGE_BOX_NAMES:
            if name != "mediabox" and self._scan.pymupdf:
                self.todo(PAGE_BOX_MESSAGE)
            return cst.parse_expression(f"{_code(updated_node.value)}.boxes.{PAGE_BOX_NAMES.get(name, 'media')}")
        if name in {"first_link", "first_annot"} and self._scan.pymupdf:
            self.todo(LINK_HANDLE_MESSAGE if name == "first_link" else ANNOTATION_HANDLE_MESSAGE)
            collection = "links" if name == "first_link" else "annotations"
            return cst.parse_expression(f"next(iter({_code(updated_node.value)}.{collection}), None)")
        if name in DOCUMENT_PROPERTY_RENAMES:
            if name == "markinfo":
                self.todo(MARK_INFO_MESSAGE)
            return updated_node.with_changes(attr=cst.Name(DOCUMENT_PROPERTY_RENAMES[name]))
        if name in PIXMAP_SAMPLES:
            # Only pymupdf's and pylopdf 0.15's Pixmap has these names; the buffer protocol replaces them.
            return cst.parse_expression(PIXMAP_SAMPLES[name].format(receiver=_code(updated_node.value)))
        return None

    def _module_call(self, member: str, updated_node: cst.Call) -> cst.BaseExpression:
        """Rewrite a call of a module function whose arguments changed."""
        function = updated_node.func
        arguments = _Arguments.of(updated_node)
        prefix = f"{_code(function.value)}." if isinstance(function, cst.Attribute) else None
        rule = MODULE_CALL_RULES[member]
        noted = len(self._todos[-1])
        replacement = None if arguments is None else rule(self, prefix, _code(function), arguments)
        if replacement is None:
            if len(self._todos[-1]) == noted:
                self.todo(
                    migration_hint(member)
                    or f"pylopdf.{MODULE_RENAMES.get(member, member)} takes other arguments; see the API reference."
                )
            return updated_node
        if _same_code(replacement, updated_node):
            return updated_node
        self.rewrites += 1
        return cst.parse_expression(replacement)

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.BaseExpression:
        function = updated_node.func
        member = self._scan.member(original_node.func)
        if member in MODULE_CALL_RULES:
            return self._module_call(member, updated_node)
        if isinstance(function, cst.Name) and function.value in {"len", *ITERATING_BUILTINS}:
            if function.value == "len" and len(updated_node.args) != 1:
                return updated_node
            return updated_node.with_changes(
                args=[arg.with_changes(value=self._pages_of(arg.value)) for arg in updated_node.args]
            )
        if not isinstance(function, cst.Attribute) or id(original_node) in self._statement_calls:
            return updated_node
        original_function = original_node.func
        if (
            function.attr.value == "extract"
            and not updated_node.args
            and isinstance(original_function, cst.Attribute)
            and self._is_table(original_function.value)
        ):
            self.rewrites += 1
            return cst.Attribute(value=function.value, attr=cst.Name("rows"))
        return self._method_call(function, updated_node)

    def _method_call(self, function: cst.Attribute, updated_node: cst.Call) -> cst.BaseExpression:
        """Rewrite a method call by the method's name."""
        name = function.attr.value
        if name == "get" and _code(function.value).endswith(".metadata"):
            arguments = _Arguments.of(updated_node)
            key = None if arguments is None or not arguments.positional else _string_literal(arguments.positional[0])
            if arguments is not None and key is not None and not arguments.keywords:
                default = arguments.positional[1] if len(arguments.positional) > 1 else None
                replacement = self._metadata_read(_code(function.value), key, default)
                if replacement is not None:
                    self.rewrites += 1
                    return replacement
        if name in STATEMENT_RULES and name not in CALL_RULES and (self._scan.pymupdf or name not in ASSIGNING_RULES):
            self._member_todo(name)
            return updated_node
        rule = CALL_RULES.get(name)
        if rule is None:
            return updated_node
        arguments = _Arguments.of(updated_node)
        replacement = None if arguments is None else rule(self, _receiver(function.value), arguments)
        if replacement is None:
            self._member_todo(name)
            return updated_node
        if _same_code(replacement, updated_node):
            return updated_node
        self.rewrites += 1
        return cst.parse_expression(replacement)


def migrate_source(source: str) -> Migration:
    """Migrate one pymupdf or pylopdf 0.15 program; other modules stay as they are."""
    module = cst.parse_module(source)
    scanner = _Scanner()
    module.visit(scanner)
    if not scanner.found:
        return Migration(source, 0, ())
    documents = scanner.names("document")
    items = _Scanner(documents, scanner.names("tables"))
    module.visit(items)
    migrator = _Migrator(scanner, documents, items)
    code = module.visit(migrator).code
    marker = f"# {TODO}: "
    todos = tuple(
        (number, line.strip()[len(marker) :])
        for number, line in enumerate(code.splitlines(), start=1)
        if line.strip().startswith(marker)
    )
    return Migration(code, migrator.rewrites, todos)
