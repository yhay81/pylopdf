"""LibCST rules that rewrite pymupdf programs for pylopdf.

:mod:`pylopdf.migrate` is the command-line entry point. The rules rename the
``fitz`` and ``pymupdf`` imports, rewrite the calls whose pylopdf replacement
is mechanical (the one-based TOC, ``embfile_*``, the PDF-syntax ``xref_*``
readers, and the in-place geometry methods), and add a ``TODO(pylopdf)``
comment, with the migration hint from the parity manifest, above every other
statement that uses a pymupdf name pylopdf does not provide. Receivers are
not typed, so a rule applies to any attribute of that name; the rules use
names that are specific to pymupdf, and files that import neither ``fitz``
nor ``pymupdf`` are left alone.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING

import libcst as cst

import pylopdf
from pylopdf._hints import migration_hint
from pylopdf._migration_hints import HINTS

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

TODO = "TODO(pylopdf)"
PYMUPDF_MODULES = frozenset({"fitz", "pymupdf"})
#: Module attributes that become a builtin name.
MODULE_TO_BUILTIN = {"FileNotFoundError": "FileNotFoundError"}
#: Coordinate helpers that pylopdf's display-space results make redundant.
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
    "drop any rotation_matrix or derotation_matrix step (api/DESIGN.md G1)."
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
def _member_hints() -> dict[str, str]:
    """Map a pymupdf member name that no pylopdf class provides to its hint.

    A name that several pymupdf classes share takes the Document hint, then
    the Page hint, then the first in alphabetical order.
    """
    provided = _provided_members()
    priority = {"Document": 0, "Page": 1}
    ranked: dict[str, tuple[int, str]] = {}
    for key, message in HINTS.items():
        owner, _, member = key.partition(".")
        if not member or (member.startswith("__") and member.endswith("__")) or member in provided:
            continue
        rank = priority.get(owner, len(priority))
        current = ranked.get(member)
        if current is None or rank < current[0]:
            ranked[member] = (rank, message)
    return {member: message for member, (_, message) in ranked.items()}


def _code(node: cst.CSTNode) -> str:
    return cst.Module(body=[]).code_for_node(node)


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


def _copy_page(_: _Migrator, receiver: str, args: _Arguments) -> str | None:
    arguments = [*args.positional, *(f"{key}={value}" for key, value in args.keywords.items())]
    return f"{receiver}.fullcopy_page({', '.join(arguments)})"


#: Calls rewritten wherever they appear.
CALL_RULES: dict[str, Callable[[_Migrator, str, _Arguments], str | None]] = {
    "get_toc": _get_toc,
    "set_toc": _set_toc,
    "copy_page": _copy_page,
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


#: Calls rewritten only as a statement of their own: pymupdf's in-place
#: geometry methods become assignments of pylopdf's immutable results.
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
}


class _ImportScanner(cst.CSTVisitor):
    """Find whether a module imports pymupdf at all."""

    def __init__(self) -> None:
        self.found = False

    def visit_Import(self, node: cst.Import) -> None:
        self.found |= any(_code(alias.name).split(".")[0] in PYMUPDF_MODULES for alias in node.names)

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        if node.module is not None and _code(node.module).split(".")[0] in PYMUPDF_MODULES:
            self.found = True


class _Migrator(cst.CSTTransformer):
    """Rewrite one module; each hint comment attaches to the innermost enclosing statement."""

    def __init__(self) -> None:
        super().__init__()
        self.rewrites = 0
        #: Local names bound to the package, after the rewrite.
        self.module_names: set[str] = set()
        #: Local names that ``import fitz`` bound and that now read ``pylopdf``.
        self.renamed: dict[str, str] = {}
        #: Names that ``from fitz import ...`` bound.
        self.imported: set[str] = set()
        self._todos: list[list[str]] = [[]]
        self._attribute_names: set[int] = set()
        self._call_functions: set[int] = set()
        self._statement_calls: set[int] = set()

    # Hint comments ------------------------------------------------------

    def todo(self, message: str) -> None:
        if message not in self._todos[-1]:
            self._todos[-1].append(message)

    def _push(self) -> None:
        self._todos.append([])

    def _pop(self, node: cst.CSTNode) -> cst.CSTNode:
        messages = self._todos.pop()
        leading: Sequence[cst.EmptyLine] | None = getattr(node, "leading_lines", None)
        if not messages or leading is None:
            self._todos[-1].extend(message for message in messages if message not in self._todos[-1])
            return node
        comments = [cst.EmptyLine(comment=cst.Comment(f"# {TODO}: {message}")) for message in messages]
        return node.with_changes(leading_lines=[*leading, *comments])

    def matrix_name(self) -> str | None:
        """Return how this module can name ``pylopdf.Matrix``, or None."""
        if "Matrix" in self.imported:
            return "Matrix"
        if self.module_names:
            return f"{min(self.module_names)}.Matrix"
        return None

    def _member_todo(self, name: str) -> None:
        if name in ROTATION_NAMES:
            self.todo(ROTATION_MESSAGE)
            return
        hint = _member_hints().get(name)
        if hint is not None:
            self.todo(hint)

    # Statements -----------------------------------------------------------

    def visit_SimpleStatementLine(self, node: cst.SimpleStatementLine) -> None:
        self._push()
        if len(node.body) == 1 and isinstance(node.body[0], cst.Expr):
            call = node.body[0].value
            if (
                isinstance(call, cst.Call)
                and isinstance(call.func, cst.Attribute)
                and call.func.attr.value in STATEMENT_RULES
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
                name == "embfile_del" or isinstance(function.value, (cst.Name, cst.Attribute))
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

    # Imports ----------------------------------------------------------------

    def leave_Import(self, original_node: cst.Import, updated_node: cst.Import) -> cst.Import:
        names = []
        for alias in updated_node.names:
            dotted = _code(alias.name)
            if dotted in PYMUPDF_MODULES:
                self.rewrites += 1
                alias = alias.with_changes(name=cst.Name("pylopdf"))  # noqa: PLW2901
                if alias.asname is None:
                    self.renamed[dotted] = "pylopdf"
                    self.module_names.add("pylopdf")
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
        if module not in PYMUPDF_MODULES:
            if module.split(".")[0] in PYMUPDF_MODULES:
                self.todo(f"pylopdf has no {module} module; import from pylopdf instead.")
            return updated_node
        self.rewrites += 1
        if not isinstance(updated_node.names, cst.ImportStar):
            for alias in updated_node.names:
                name = _code(alias.name)
                local = _code(alias.asname.name) if alias.asname is not None else name
                self.imported.add(local)
                if not hasattr(pylopdf, name):
                    self.todo(migration_hint(name) or f"pylopdf does not provide pymupdf's {name}.")
        return updated_node.with_changes(module=cst.Name("pylopdf"))

    # Expressions ------------------------------------------------------------

    def visit_Attribute(self, node: cst.Attribute) -> None:
        self._attribute_names.add(id(node.attr))

    def visit_Call(self, node: cst.Call) -> None:
        self._call_functions.add(id(node.func))

    def leave_Name(self, original_node: cst.Name, updated_node: cst.Name) -> cst.Name:
        if id(original_node) not in self._attribute_names and updated_node.value in self.renamed:
            return updated_node.with_changes(value=self.renamed[updated_node.value])
        return updated_node

    def leave_Attribute(self, original_node: cst.Attribute, updated_node: cst.Attribute) -> cst.BaseExpression:
        name = updated_node.attr.value
        value = updated_node.value
        if isinstance(value, cst.Name) and value.value in self.module_names:
            if name in MODULE_TO_BUILTIN:
                self.rewrites += 1
                return cst.Name(MODULE_TO_BUILTIN[name])
            if not hasattr(pylopdf, name) and not (name.startswith("__") and name.endswith("__")):
                self.todo(migration_hint(name) or f"pylopdf does not provide pymupdf's {name}.")
            return updated_node
        if name in CALL_RULES or name in STATEMENT_RULES:
            if id(original_node) not in self._call_functions:
                self._member_todo(name)
            return updated_node
        self._member_todo(name)
        return updated_node

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.BaseExpression:
        function = updated_node.func
        if not isinstance(function, cst.Attribute) or id(original_node) in self._statement_calls:
            return updated_node
        name = function.attr.value
        if name in STATEMENT_RULES:
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
        self.rewrites += 1
        return cst.parse_expression(replacement)


def migrate_source(source: str) -> Migration:
    """Migrate one pymupdf program; a module that imports neither fitz nor pymupdf stays as it is."""
    module = cst.parse_module(source)
    scanner = _ImportScanner()
    module.visit(scanner)
    if not scanner.found:
        return Migration(source, 0, ())
    migrator = _Migrator()
    code = module.visit(migrator).code
    marker = f"# {TODO}: "
    todos = tuple(
        (number, line.strip()[len(marker) :])
        for number, line in enumerate(code.splitlines(), start=1)
        if line.strip().startswith(marker)
    )
    return Migration(code, migrator.rewrites, todos)
