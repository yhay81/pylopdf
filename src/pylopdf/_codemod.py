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
import inspect
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, cast

import libcst as cst

import pylopdf
from pylopdf._hints import migration_hint
from pylopdf._migration_hints import HINTS, PYLOPDF_015_ONLY, PYLOPDF_015_REMOVED

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

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
ROTATION_MESSAGE = (
    "pylopdf extracts and inserts in display coordinates with the page rotation resolved; "
    "drop any rotation_matrix or derotation_matrix step."
)


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
    bound = args.bind(("simple",))
    if bound is None or bound.get("simple", "True") != "True":
        return None
    return f"[[entry.level, entry.title, entry.page + 1] for entry in {receiver}.get_outline()]"


def _set_toc(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    bound = args.bind(("toc",))
    if bound is None or set(bound) != {"toc"}:
        return None
    return f"{receiver}.set_outline([(entry[0], entry[1], entry[2] - 1) for entry in {bound['toc']}])"


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


def _save(migrator: _Migrator, receiver: str, args: _Arguments) -> str | None:
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
TEXT_RECORD_MESSAGES = {
    "words": "page.words() returns Word records with text, bbox, block, line, and index instead of tuples.",
    "blocks": "page.layout().blocks holds Block records with text, bbox, and lines instead of tuples.",
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
    if option in TEXT_RECORD_MESSAGES:
        migrator.todo(TEXT_RECORD_MESSAGES[option])
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


#: Calls rewritten wherever they appear.
CALL_RULES: dict[str, Callable[[_Migrator, str, _Arguments], str | None]] = {
    "get_toc": _get_toc,
    "set_toc": _set_toc,
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
    "set_language": _one_argument("language", "{receiver}.set_language_tag({value})"),
    "save": _save,
    "tobytes": _tobytes,
    "write": _tobytes,
    "authenticate": _unlock(note=True),
    "get_text": _get_text,
    "search_for": _search_for,
    "to_markdown": _renamed("markdown"),
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
}
#: Statement rules that assign to their receiver, which must therefore be a name or an attribute.
ASSIGNING_RULES = frozenset(
    {"include_rect", "include_point", "intersect", "transform", "normalize", "concat", "invert"}
    | {"prerotate", "prescale", "preshear", "pretranslate"}
)


#: Page properties that the API reset renamed, rewritten on names bound only to pages.
PAGE_RENAMES = {"number": "index", "parent": "document"}
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


#: Module calls whose arguments changed, keyed by their name in the module.
MODULE_CALL_RULES: dict[str, Callable[[_Migrator, str | None, str, _Arguments], str | None]] = {
    "open": _open_document,
    "Document": _open_document,
    "peek_metadata": _probe,
}


#: The fields of word and block records by their position in pymupdf's tuples.
RECORD_FIELDS = {
    "words": {0: "bbox.x0", 1: "bbox.y0", 2: "bbox.x1", 3: "bbox.y1", 4: "text", 5: "block", 6: "line", 7: "index"},
    "blocks": {0: "bbox.x0", 1: "bbox.y0", 2: "bbox.x1", 3: "bbox.y1", 4: "text"},
}
#: The variable name that a converted loop over unpacked tuples takes.
RECORD_NAMES = {"words": "word", "blocks": "block"}
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

    def __init__(self, documents: frozenset[str] | None = None) -> None:
        self.found = False
        #: Whether the module imports fitz or pymupdf, not only pylopdf.
        self.pymupdf = False
        #: Local names of the fitz, pymupdf, and pylopdf modules, before the rewrite.
        self.modules: set[str] = set()
        #: Local names imported from those modules, mapped to their names there.
        self.imported: dict[str, str] = {}
        #: The kinds of value that each name is bound to.
        self.bindings: dict[str, set[str]] = {}
        self._documents = documents

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
            member = self.member(node.func)
            if member in DOCUMENT_FACTORIES:
                return "document"
            if member in OTHER_FACTORIES:
                return "factory"
            function = node.func
            if isinstance(function, cst.Attribute) and (
                (function.attr.value in PAGE_METHODS and self.is_document(function.value))
                or (function.attr.value == "new" and self.is_pages(function.value))
            ):
                return "page"
        if (
            isinstance(node, cst.Subscript)
            and len(node.slice) == 1
            and isinstance(node.slice[0].slice, cst.Index)
            and self.is_pages(node.value)
        ):
            return "page"
        return "other"

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

    def __init__(self, scan: _Scanner, documents: frozenset[str], pages: frozenset[str]) -> None:
        super().__init__()
        self.rewrites = 0
        #: Local names bound to the package, after the rewrite.
        self.module_names: set[str] = set()
        #: Local names that ``import fitz`` or a renamed import bound, and what they read now.
        self.renamed: dict[str, str] = {}
        #: Names that ``from fitz import ...`` or ``from pylopdf import ...`` bound.
        self.imported: set[str] = set()
        self._scan = scan
        self._documents = documents
        self._pages = pages
        #: Names bound to a value that a module factory returned that is not a document.
        self._factories = scan.names("factory")
        self._todos: list[list[str]] = [[]]
        self._attribute_names: set[int] = set()
        self._call_functions: set[int] = set()
        self._statement_calls: set[int] = set()
        #: Module attributes that an enclosing call's rule replaces, which need no hint of their own.
        self._consumed: set[int] = set()

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

    def leave_SimpleStatementLine(
        self, original_node: cst.SimpleStatementLine, updated_node: cst.SimpleStatementLine
    ) -> cst.SimpleStatementLine:
        statement = original_node.body[0] if len(original_node.body) == 1 else None
        if (
            isinstance(statement, cst.Expr)
            and id(statement.value) in self._statement_calls
            and isinstance(updated := updated_node.body[0], cst.Expr)
            and isinstance(call := updated.value, cst.Call)
            and isinstance(function := call.func, cst.Attribute)
        ):
            name = function.attr.value
            arguments = _Arguments.of(call)
            replacement = None
            if arguments is not None and (
                name not in ASSIGNING_RULES or isinstance(function.value, (cst.Name, cst.Attribute))
            ):
                replacement = STATEMENT_RULES[name](self, _receiver(function.value), arguments)
            if replacement is None:
                self._member_todo(name)
            else:
                self.rewrites += 1
                new_statement = cst.parse_statement(replacement)
                assert isinstance(new_statement, cst.SimpleStatementLine)  # noqa: S101 - a rule renders one statement.
                updated_node = updated_node.with_changes(body=[new_statement.body[0]])
        result = self._pop(updated_node)
        assert isinstance(result, cst.SimpleStatementLine)  # noqa: S101 - _pop only adds comments.
        return result

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
        self._withdraw(TEXT_RECORD_MESSAGES[kind])
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
        """Return ``words`` or ``blocks`` for an iterable that a ``get_text`` rewrite just produced."""
        code = _code(iterable)
        for kind, suffix in (("words", ".words()"), ("blocks", ".layout().blocks")):
            if code.endswith(suffix) and TEXT_RECORD_MESSAGES[kind] in self._todos[-1]:
                return kind
        return None

    def _record_comprehension(self, node: _Comprehension) -> _Comprehension:
        """Read the fields of word and block records where a comprehension indexed pymupdf's tuples."""
        for_: cst.CompFor | None = node.for_in
        while for_ is not None:
            kind = self._record_kind(for_.iter)
            if kind is not None:
                converted = _record_loop(node, for_.target, kind)
                if converted is not None:
                    self._withdraw(TEXT_RECORD_MESSAGES[kind])
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

    def leave_ImportFrom(self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom) -> cst.ImportFrom:
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
        aliases = []
        for alias in updated_node.names:
            name = _code(alias.name)
            local = _code(alias.asname.name) if alias.asname is not None else name
            if name in MODULE_RENAMES:
                self.rewrites += 1
                renamed = MODULE_RENAMES[name]
                alias = alias.with_changes(name=cst.Name(renamed))  # noqa: PLW2901
                if alias.asname is None:
                    self.renamed[name] = renamed
                    local = renamed
            elif not hasattr(pylopdf, name):
                self.todo(migration_hint(name) or f"pylopdf does not provide pymupdf's {name}.")
            self.imported.add(local)
            aliases.append(alias)
        return updated_node.with_changes(names=aliases)

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

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.Name:
        if id(original_node) not in self._attribute_names and updated_node.value in self.renamed:
            return updated_node.with_changes(value=self.renamed[updated_node.value])
        return updated_node

    def leave_Subscript(self, original_node: cst.Subscript, updated_node: cst.Subscript) -> cst.Subscript:
        return updated_node.with_changes(value=self._pages_of(updated_node.value))

    def leave_Attribute(self, original_node: cst.Attribute, updated_node: cst.Attribute) -> cst.BaseExpression:
        name = updated_node.attr.value
        called = id(original_node) in self._call_functions
        if isinstance(original_node.value, cst.Name) and original_node.value.value in self._scan.modules:
            return self._module_attribute(original_node, updated_node, called=called)
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
            (isinstance(receiver, cst.Name) and receiver.value in self._factories)
            or (isinstance(receiver, cst.Call) and self._scan.member(receiver.func) in OTHER_FACTORIES)
        ):
            return cst.parse_expression(f"len({_receiver(updated_node.value)}.pages)")
        if name in {"needs_pass", "is_encrypted"}:
            return updated_node.with_changes(attr=cst.Name("needs_password"))
        if isinstance(receiver, cst.Name) and receiver.value in self._pages and name in PAGE_RENAMES:
            return updated_node.with_changes(attr=cst.Name(PAGE_RENAMES[name]))
        return None

    def _module_call(self, member: str, updated_node: cst.Call) -> cst.BaseExpression:
        """Rewrite a call of a module function whose arguments changed."""
        function = updated_node.func
        arguments = _Arguments.of(updated_node)
        prefix = f"{_code(function.value)}." if isinstance(function, cst.Attribute) else None
        rule = MODULE_CALL_RULES[member]
        replacement = None if arguments is None else rule(self, prefix, _code(function), arguments)
        if replacement is None:
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
        return self._method_call(function, updated_node)

    def _method_call(self, function: cst.Attribute, updated_node: cst.Call) -> cst.BaseExpression:
        """Rewrite a method call by the method's name."""
        name = function.attr.value
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
    pages = _Scanner(documents)
    module.visit(pages)
    migrator = _Migrator(scanner, documents, pages.names("page"))
    code = module.visit(migrator).code
    marker = f"# {TODO}: "
    todos = tuple(
        (number, line.strip()[len(marker) :])
        for number, line in enumerate(code.splitlines(), start=1)
        if line.strip().startswith(marker)
    )
    return Migration(code, migrator.rewrites, todos)
