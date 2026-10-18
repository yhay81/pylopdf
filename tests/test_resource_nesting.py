"""Colour spaces, shadings, and functions that nest too deeply for hayro.

hayro 0.7 and 0.8 build a colour space or a function by recursion over the objects it names, with no depth bound: an
Indexed space names its base, a Separation space its alternate, a stitching function every sub-function, and a Type 4
function parses nested braces by recursion. About 20,000 indirect objects that name each other, or 300,000 nested
braces, overflow the stack, and a stack overflow kills the process instead of raising, so those cases run in a
child interpreter. Every path that lets hayro interpret a page must refuse them with `LimitError` instead.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from conftest import build_raw_pdf
from test_content_nesting import FONT, FORM, OPERATIONS, _refusal, _stream, _type3_font

import pylopdf

pytestmark = pytest.mark.filterwarnings("ignore:text extraction skipped glyphs")

CODE = "resource_nesting_depth"
#: Deep enough to overflow the stack of any thread that pylopdf runs on.
CRASH_BRACES = 300_000
CRASH_CHAIN = 20_000
#: The deepest braces and chain that reach hayro's recursive builders.
BRACE_LIMIT = 64
CHAIN_LIMIT = 32
SOME_OPERATIONS = ["get_page_text", "get_pixmap", "render_page_svg"]


class Parts:
    """Number the objects of a one-page document as they are added.

    Objects 1 through 4 are the catalog, the page tree, the page, and its content stream; added objects follow.
    """

    def __init__(self, first: int = 5) -> None:
        """Start numbering at `first`."""
        self.objects: dict[int, str | bytes] = {}
        self._next = first

    def add(self, body: str | bytes) -> int:
        """Append an object and return its number."""
        number = self._next
        self.objects[number] = body
        self._next += 1
        return number

    def reserve(self, count: int) -> int:
        """Return the first of `count` consecutive numbers, which the caller sets in `objects`."""
        first = self._next
        self._next += count
        return first

    def build(self, resources: str, content: str, *, page: str = "", pages: str = "") -> bytes:
        """Assemble the document around the added objects."""
        return build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: f"<< /Type /Pages /Kids [3 0 R] /Count 1 {pages}>>",
                3: f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << {resources}>> "
                f"/Contents 4 0 R {page}>>",
                4: _stream(content),
                **self.objects,
            }
        )


# --- structures: the chains and programs that overflow hayro ---------------------------------------------------------

TYPE2 = "<< /FunctionType 2 /Domain [0 1] /C0 [0 0 0] /C1 [1 0.5 0] /N 1 >>"


def _valid_program(depth: int) -> str:
    """Write a program that nests `depth` levels of braces and still evaluates to three values."""
    return "{ " + "dup 0.5 gt { " * (depth - 1) + "dup " + "} if " * (depth - 1) + "dup dup }"


def type4_program(parts: Parts, depth: int) -> str:
    """Add a Type 4 function whose braces nest `depth` levels deep."""
    program = _valid_program(depth) if depth <= 1_000 else "{" * depth + "}" * depth
    attributes = "/FunctionType 4 /Domain [0 1] /Range [0 1 0 1 0 1] "
    return f"{parts.add(_stream(program, attributes))} 0 R"


def stitching_chain(parts: Parts, depth: int) -> str:
    """Stitching functions that each name the next one, ending in a Type 2 function: `depth` nodes."""
    first = parts.reserve(depth)
    for index in range(depth - 1):
        number = first + index
        parts.objects[number] = (
            f"<< /FunctionType 3 /Domain [0 1] /Functions [{number + 1} 0 R] /Bounds [] /Encode [0 1] >>"
        )
    parts.objects[first + depth - 1] = TYPE2
    return f"{first} 0 R"


def indexed_chain(parts: Parts, depth: int) -> str:
    """Indexed colour spaces that each take the next one as their base: `depth` nodes."""
    first = parts.reserve(depth)
    for index in range(depth):
        number = first + index
        base = f"{number + 1} 0 R" if index < depth - 1 else "/DeviceGray"
        parts.objects[number] = f"[/Indexed {base} 1 <00 FF>]"
    return f"{first} 0 R"


def separation_chain(parts: Parts, depth: int) -> str:
    """Separation colour spaces that each take the next one as their alternate: `depth` nodes in all.

    The last one names a Type 2 tint transform, which is a node of its own, so there are `depth - 1` spaces.
    """
    tint = parts.add(TYPE2)
    first = parts.reserve(depth - 1)
    for index in range(depth - 1):
        number = first + index
        alternate = f"{number + 1} 0 R" if index < depth - 2 else "/DeviceRGB"
        parts.objects[number] = f"[/Separation /Spot{index} {alternate} {tint} 0 R]"
    return f"{first} 0 R"


def devicen_chain(parts: Parts, depth: int) -> str:
    """DeviceN colour spaces that each take the next one as their alternate: `depth` nodes, as for Separation."""
    tint = parts.add(TYPE2.replace("/Domain [0 1]", "/Domain [0 1 0 1]"))
    first = parts.reserve(depth - 1)
    for index in range(depth - 1):
        number = first + index
        alternate = f"{number + 1} 0 R" if index < depth - 2 else "/DeviceRGB"
        parts.objects[number] = f"[/DeviceN [/A{index} /B{index}] {alternate} {tint} 0 R]"
    return f"{first} 0 R"


def icc_chain(parts: Parts, depth: int) -> str:
    """ICCBased spaces whose profile hayro cannot parse, so it falls back to the next space: `depth` nodes.

    Each space is an array and the profile stream it names, so `depth` must be even.
    """
    assert depth % 2 == 0
    first = parts.reserve(depth)
    for index in range(0, depth, 2):
        number = first + index
        last = index == depth - 2
        alternate = "/DeviceGray" if last else f"[/ICCBased {number + 3} 0 R]"
        parts.objects[number] = f"[/ICCBased {number + 1} 0 R]"
        parts.objects[number + 1] = _stream("not a profile", f"/N 1 /Alternate {alternate} ")
    return f"{first} 0 R"


def pattern_nesting(parts: Parts, depth: int) -> str:
    """Direct Pattern spaces inside one another: `depth` nodes, none of them an object of its own."""
    del parts
    return "[/Pattern " * depth + "/DeviceGray" + "]" * depth


def ring(parts: Parts, depth: int) -> str:
    """Stitching functions that name each other in a cycle of `depth` objects."""
    first = parts.reserve(depth)
    for index in range(depth):
        number = first + index
        following = first + (index + 1) % depth
        parts.objects[number] = (
            f"<< /FunctionType 3 /Domain [0 1] /Functions [{following} 0 R] /Bounds [] /Encode [0 1] >>"
        )
    return f"{first} 0 R"


def stitching_through_arrays(parts: Parts, levels: int) -> str:
    """Stitching functions that each name the next through a `/Functions` array that is an object of its own.

    There are `levels` functions and as many arrays, and a Type 2 function at the end: `2 * levels + 1` nodes.
    """
    first = parts.reserve(2 * levels + 1)
    for level in range(levels):
        function, listing = first + 2 * level, first + 2 * level + 1
        parts.objects[function] = (
            f"<< /FunctionType 3 /Domain [0 1] /Functions {listing} 0 R /Bounds [] /Encode [0 1] >>"
        )
        parts.objects[listing] = f"[{function + 2} 0 R]"
    parts.objects[first + 2 * levels] = TYPE2
    return f"{first} 0 R"


def chain_into(parts: Parts, length: int, target: str) -> str:
    """Stitching functions that each name the next one, the last naming `target`: `length` nodes above it."""
    first = parts.reserve(length)
    for index in range(length):
        number = first + index
        following = f"{number + 1} 0 R" if index < length - 1 else target
        parts.objects[number] = f"<< /FunctionType 3 /Domain [0 1] /Functions [{following}] /Bounds [] /Encode [0 1] >>"
    return f"{first} 0 R"


@dataclass(frozen=True)
class Structure:
    """How to build one kind of deep structure."""

    build: Callable[[Parts, int], str]
    #: Colour components a colour space delivers, so that content can set a colour in it.
    components: int = 1


FUNCTIONS = {
    "type4": Structure(type4_program),
    "stitching": Structure(stitching_chain),
    "ring": Structure(ring),
}
COLOUR_SPACES = {
    "indexed": Structure(indexed_chain),
    "separation": Structure(separation_chain),
    "devicen": Structure(devicen_chain, components=2),
    "icc": Structure(icc_chain),
    "pattern": Structure(pattern_nesting),
}


# --- entries: where hayro builds a function or a colour space -------------------------------------------------------

Entry = Callable[[Parts, str, int], tuple[str, str]]
#: Entries that put the function inside a colour space array, which is a level of its own above the chain.
WRAPPED = {
    "tint-transform",
    "indirect-tint-transform",
    "devicen-tint-transform",
    "image-tint-transform",
    "soft-mask-group-tint-transform",
}
SHADING = "/ShadingType 2 /ColorSpace /DeviceRGB /Coords [0 0 100 0] "
GROUP = FORM + "/Group << /S /Transparency /CS /DeviceGray >> "


def _fill(components: int) -> str:
    return " ".join(["0.5"] * components) + " sc 0 0 50 50 re f"


def _graphics_state(parts: Parts, entries: str) -> tuple[str, str]:
    state = parts.add(f"<< /Type /ExtGState {entries}>>")
    return f"/ExtGState << /GS0 {state} 0 R >> ", "/GS0 gs 0 0 50 50 re f"


def shading(parts: Parts, function: str, _: int) -> tuple[str, str]:
    number = parts.add(f"<< {SHADING}/Function {function} >>")
    return f"/Shading << /Sh1 {number} 0 R >> ", "/Sh1 sh"


def shading_functions(parts: Parts, function: str, _: int) -> tuple[str, str]:
    number = parts.add(f"<< {SHADING}/Function [{function} {function} {function}] >>")
    return f"/Shading << /Sh1 {number} 0 R >> ", "/Sh1 sh"


def mesh_shading(parts: Parts, function: str, _: int) -> tuple[str, str]:
    attributes = (
        f"/ShadingType 4 /ColorSpace /DeviceRGB /BitsPerCoordinate 8 /BitsPerComponent 8 /BitsPerFlag 8 "
        f"/Decode [0 100 0 100 0 1 0 1 0 1] /Function {function} "
    )
    number = parts.add(_stream(b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00", attributes))
    return f"/Shading << /Sh1 {number} 0 R >> ", "/Sh1 sh"


def shading_pattern(parts: Parts, function: str, _: int) -> tuple[str, str]:
    number = parts.add(f"<< /PatternType 2 /Shading << {SHADING}/Function {function} >> >>")
    return f"/Pattern << /P1 {number} 0 R >> ", "/Pattern cs /P1 scn 0 0 100 100 re f"


def direct_shading_pattern(parts: Parts, function: str, _: int) -> tuple[str, str]:
    del parts
    pattern = f"<< /PatternType 2 /Shading << {SHADING}/Function {function} >> >>"
    return f"/Pattern << /P1 {pattern} >> ", "/Pattern cs /P1 scn 0 0 100 100 re f"


def transfer(parts: Parts, function: str, _: int) -> tuple[str, str]:
    return _graphics_state(parts, f"/TR {function} ")


def transfer2(parts: Parts, function: str, _: int) -> tuple[str, str]:
    return _graphics_state(parts, f"/TR2 {function} ")


def transfer_array(parts: Parts, function: str, _: int) -> tuple[str, str]:
    return _graphics_state(parts, f"/TR [{function} {function} {function} {function}] ")


def direct_transfer(parts: Parts, function: str, _: int) -> tuple[str, str]:
    del parts
    return f"/ExtGState << /GS0 << /Type /ExtGState /TR {function} >> >> ", "/GS0 gs 0 0 50 50 re f"


def soft_mask_transfer(parts: Parts, function: str, _: int) -> tuple[str, str]:
    group = parts.add(_stream("0 0 m", GROUP))
    return _graphics_state(parts, f"/SMask << /Type /Mask /S /Luminosity /G {group} 0 R /TR {function} >> ")


def tint_transform(_parts: Parts, function: str, _: int) -> tuple[str, str]:
    return f"/ColorSpace << /Cs1 [/Separation /Spot /DeviceRGB {function}] >> ", "/Cs1 cs " + _fill(1)


def indirect_tint_transform(parts: Parts, function: str, _: int) -> tuple[str, str]:
    space = parts.add(f"[/Separation /Spot /DeviceRGB {function}]")
    return f"/ColorSpace << /Cs1 {space} 0 R >> ", "/Cs1 cs " + _fill(1)


def devicen_tint_transform(parts: Parts, function: str, _: int) -> tuple[str, str]:
    space = parts.add(f"[/DeviceN [/A /B] /DeviceRGB {function}]")
    return f"/ColorSpace << /Cs1 {space} 0 R >> ", "/Cs1 cs " + _fill(2)


def _image(parts: Parts, space: str, components: int) -> tuple[str, str]:
    image = parts.add(
        _stream(
            bytes(components),
            f"/Type /XObject /Subtype /Image /Width 1 /Height 1 /BitsPerComponent 8 /ColorSpace {space} ",
        )
    )
    return f"/XObject << /Im0 {image} 0 R >> ", "q 50 0 0 50 10 10 cm /Im0 Do Q"


def image_tint_transform(parts: Parts, function: str, _: int) -> tuple[str, str]:
    return _image(parts, f"[/Separation /Spot /DeviceRGB {function}]", 1)


def soft_mask_group_tint_transform(parts: Parts, function: str, _: int) -> tuple[str, str]:
    group = parts.add(
        _stream("0 0 m", FORM + f"/Group << /S /Transparency /CS [/Separation /Spot /DeviceRGB {function}] >> ")
    )
    return _graphics_state(parts, f"/SMask << /Type /Mask /S /Luminosity /G {group} 0 R >> ")


FUNCTION_ENTRIES: dict[str, Entry] = {
    "shading": shading,
    "shading-function-array": shading_functions,
    "mesh-shading": mesh_shading,
    "shading-pattern": shading_pattern,
    "direct-shading-pattern": direct_shading_pattern,
    "transfer": transfer,
    "transfer2": transfer2,
    "transfer-array": transfer_array,
    "direct-transfer": direct_transfer,
    "soft-mask-transfer": soft_mask_transfer,
    "tint-transform": tint_transform,
    "indirect-tint-transform": indirect_tint_transform,
    "devicen-tint-transform": devicen_tint_transform,
    "image-tint-transform": image_tint_transform,
    "soft-mask-group-tint-transform": soft_mask_group_tint_transform,
}


def colour_resource(parts: Parts, space: str, components: int) -> tuple[str, str]:
    del parts
    return f"/ColorSpace << /Cs1 {space} >> ", "/Cs1 cs " + _fill(components)


def _function_with_outputs(components: int) -> str:
    zeros, ones = " ".join(["0"] * components), " ".join(["1"] * components)
    return f"<< /FunctionType 2 /Domain [0 1] /C0 [{zeros}] /C1 [{ones}] /N 1 >>"


def shading_space(parts: Parts, space: str, components: int) -> tuple[str, str]:
    function = _function_with_outputs(components)
    number = parts.add(f"<< /ShadingType 2 /ColorSpace {space} /Coords [0 0 100 0] /Function {function} >>")
    return f"/Shading << /Sh1 {number} 0 R >> ", "/Sh1 sh"


def shading_pattern_space(parts: Parts, space: str, components: int) -> tuple[str, str]:
    function = _function_with_outputs(components)
    shading_dictionary = f"<< /ShadingType 2 /ColorSpace {space} /Coords [0 0 100 0] /Function {function} >>"
    number = parts.add(f"<< /PatternType 2 /Shading {shading_dictionary} >>")
    return f"/Pattern << /P1 {number} 0 R >> ", "/Pattern cs /P1 scn 0 0 100 100 re f"


def image_space(parts: Parts, space: str, components: int) -> tuple[str, str]:
    return _image(parts, space, components)


def soft_mask_group_space(parts: Parts, space: str, components: int) -> tuple[str, str]:
    del components
    group = parts.add(_stream("0 0 m", FORM + f"/Group << /S /Transparency /CS {space} >> "))
    return _graphics_state(parts, f"/SMask << /Type /Mask /S /Luminosity /G {group} 0 R >> ")


COLOUR_ENTRIES: dict[str, Entry] = {
    "resource": colour_resource,
    "shading-space": shading_space,
    "shading-pattern-space": shading_pattern_space,
    "image-space": image_space,
    "soft-mask-group-space": soft_mask_group_space,
}


# --- scopes: where the resources and the content that uses them live -------------------------------------------------


def page_scope(parts: Parts, resources: str, content: str) -> bytes:
    """List the resources in the page itself and draw there."""
    return parts.build(resources, content)


def inherited_scope(parts: Parts, resources: str, content: str) -> bytes:
    """List the resources only in the page tree node; the page inherits them."""
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: f"<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 300 300] /Resources << {resources}>> >>",
            3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
            4: _stream(content),
            **parts.objects,
        }
    )


def form_scope(parts: Parts, resources: str, content: str) -> bytes:
    form = parts.add(_stream(content, FORM + f"/Resources << {resources}>> "))
    return parts.build(f"/XObject << /Fm0 {form} 0 R >> ", "q /Fm0 Do Q")


def nested_form_scope(parts: Parts, resources: str, content: str) -> bytes:
    inner = parts.add(_stream(content, FORM + f"/Resources << {resources}>> "))
    outer = parts.add(_stream("q /Inner Do Q", FORM + f"/Resources << /XObject << /Inner {inner} 0 R >> >> "))
    return parts.build(f"/XObject << /Outer {outer} 0 R >> ", "q /Outer Do Q")


def annotation_scope(parts: Parts, resources: str, content: str, subtype: str = "/Subtype /FreeText") -> bytes:
    appearance = parts.add(
        _stream(content, f"/Type /XObject /Subtype /Form /BBox [0 0 190 50] /Resources << {resources}>> ")
    )
    annotation = parts.add(f"<< /Type /Annot {subtype} /Rect [10 150 200 200] /AP << /N {appearance} 0 R >> >>")
    return parts.build("", "0 0 m", page=f"/Annots [{annotation} 0 R] ")


def widget_scope(parts: Parts, resources: str, content: str) -> bytes:
    return annotation_scope(parts, resources, content, "/Subtype /Widget /FT /Tx /T (name) /V (value)")


def tiling_pattern_scope(parts: Parts, resources: str, content: str) -> bytes:
    attributes = "/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 /BBox [0 0 20 20] /XStep 20 /YStep 20 "
    pattern = parts.add(_stream(content, attributes + f"/Resources << {resources}>> "))
    return parts.build(f"/Pattern << /P1 {pattern} 0 R >> ", "/Pattern cs /P1 scn 0 0 200 200 re f")


def glyph_scope(parts: Parts, resources: str, content: str) -> bytes:
    """Reach the resources through the `/Resources` of a Type 3 font that the page selects."""
    procedure = parts.add(_stream("100 0 0 0 100 100 d1 " + content))
    font = parts.add(_type3_font(procedure, f"/Resources << {resources}>> "))
    return parts.build(f"/Font << /F3 {font} 0 R >> ", "BT /F3 12 Tf 20 100 Td (a) Tj ET")


def soft_mask_scope(parts: Parts, resources: str, content: str) -> bytes:
    group = parts.add(_stream(content, GROUP + f"/Resources << {resources}>> "))
    state = parts.add(f"<< /Type /ExtGState /SMask << /Type /Mask /S /Luminosity /G {group} 0 R >> >>")
    return parts.build(f"/ExtGState << /GS0 {state} 0 R >> ", "/GS0 gs 0 0 200 200 re f")


def listed_only_scope(parts: Parts, resources: str, content: str) -> bytes:
    """List the resources without drawing with them: hayro never builds them, but resolves names through them."""
    del content
    return parts.build(resources, "0 0 m")


SCOPES: dict[str, Callable[[Parts, str, str], bytes]] = {
    "page": page_scope,
    "inherited": inherited_scope,
    "form": form_scope,
    "nested-form": nested_form_scope,
    "annotation": annotation_scope,
    "widget": widget_scope,
    "tiling-pattern": tiling_pattern_scope,
    "glyph": glyph_scope,
    "soft-mask": soft_mask_scope,
    "listed-only": listed_only_scope,
}


def function_document(structure: str, entry: str, depth: int, scope: str = "page") -> bytes:
    parts = Parts()
    function = FUNCTIONS[structure].build(parts, depth)
    resources, content = FUNCTION_ENTRIES[entry](parts, function, 3)
    return SCOPES[scope](parts, resources, content)


def colour_document(structure: str, entry: str, depth: int, scope: str = "page") -> bytes:
    parts = Parts()
    description = COLOUR_SPACES[structure]
    space = description.build(parts, depth)
    resources, content = COLOUR_ENTRIES[entry](parts, space, description.components)
    return SCOPES[scope](parts, resources, content)


# Every case: a name for the report and a builder of the document at a given depth.
CASES: dict[str, Callable[..., bytes]] = {}
for _structure in ("type4", "stitching"):
    for _entry in FUNCTION_ENTRIES:
        CASES[f"{_structure}-{_entry}"] = partial(function_document, _structure, _entry)
for _structure in ("indexed", "separation", "devicen", "icc", "pattern"):
    for _entry in COLOUR_ENTRIES:
        CASES[f"{_structure}-{_entry}"] = partial(colour_document, _structure, _entry)

#: The depth at which each structure is refused: the smallest depth beyond its limit.
REFUSED = {
    "type4": BRACE_LIMIT + 1,
    "stitching": CHAIN_LIMIT + 1,
    "indexed": CHAIN_LIMIT + 1,
    "separation": CHAIN_LIMIT + 1,
    "devicen": CHAIN_LIMIT + 1,
    "icc": CHAIN_LIMIT + 2,
    "pattern": CHAIN_LIMIT + 1,
}
ACCEPTED = {
    "type4": BRACE_LIMIT,
    "stitching": CHAIN_LIMIT,
    "indexed": CHAIN_LIMIT,
    "separation": CHAIN_LIMIT,
    "devicen": CHAIN_LIMIT,
    "icc": CHAIN_LIMIT,
    "pattern": CHAIN_LIMIT,
}
CRASHING = {name: CRASH_BRACES if name == "type4" else CRASH_CHAIN for name in REFUSED}


def _structure_of(case: str) -> str:
    return case.split("-", 1)[0]


def _accepted(case: str) -> int:
    """Return the deepest structure that the check accepts in this case, net of a colour space around a function."""
    structure, entry = case.split("-", 1)
    return ACCEPTED[structure] - (1 if structure == "stitching" and entry in WRAPPED else 0)


@pytest.mark.parametrize("case", CASES)
def test_every_operation_refuses_a_chain_beyond_the_limit(case: str) -> None:
    """Check each operation in process at the smallest refused depth, where nothing can crash."""
    data = CASES[case](REFUSED[_structure_of(case)])
    for operation in OPERATIONS.values():
        doc = pylopdf.Document(stream=data)
        error = _refusal(partial(operation, doc))
        assert error.code == CODE
        assert isinstance(error, pylopdf.PdfError)
        assert error.args[0] == CODE


@pytest.mark.parametrize("case", CASES)
def test_a_chain_at_the_limit_is_accepted(case: str) -> None:
    """List the structure without drawing with it: hayro paints nested Separation spaces in exponential time."""
    doc = pylopdf.Document(stream=CASES[case](_accepted(case), "listed-only"))

    for operation in OPERATIONS.values():
        operation(doc)


@pytest.mark.parametrize("case", CASES)
def test_ordinary_structures_are_interpreted_by_every_operation(case: str) -> None:
    """The fixtures are valid: at a realistic depth every entry draws and every operation reads the page."""
    structure = _structure_of(case)
    doc = pylopdf.Document(stream=CASES[case](4 if structure == "icc" else 3))

    for operation in OPERATIONS.values():
        operation(doc)
    assert doc[0].get_pixmap().width == 300


@pytest.mark.parametrize(
    "case",
    [
        "type4-shading",
        "type4-transfer",
        "type4-tint-transform",
        "stitching-shading",
        "stitching-transfer",
        "indexed-resource",
        "indexed-image-space",
        "icc-resource",
        "pattern-resource",
    ],
)
def test_a_painted_chain_at_the_limit_is_interpreted(case: str) -> None:
    """Build and draw with the deepest structure that the check accepts, on the stack of the main thread."""
    doc = pylopdf.Document(stream=CASES[case](_accepted(case)))

    for operation in OPERATIONS.values():
        operation(doc)
    assert doc[0].get_pixmap().width == 300


@pytest.mark.parametrize("depth", [CHAIN_LIMIT + 1, 60, 90])
def test_directly_nested_spaces_are_a_chain_too(depth: int) -> None:
    """Spaces nested inside one another cost as many levels as spaces that reference each other."""
    doc = pylopdf.Document(stream=CASES["pattern-resource"](depth))

    assert _refusal(lambda: doc.get_page_text(0)).code == CODE
    assert _refusal(doc[0].get_pixmap).code == CODE


@pytest.mark.parametrize("structure", ["separation", "devicen"])
def test_a_built_chain_at_the_limit_is_interpreted(structure: str) -> None:
    """Select the deepest accepted space without painting: building it is cheap, converting a colour is not."""
    parts = Parts()
    space = COLOUR_SPACES[structure].build(parts, ACCEPTED[structure])
    doc = pylopdf.Document(stream=page_scope(parts, f"/ColorSpace << /Cs1 {space} >> ", "/Cs1 cs"))

    for operation in OPERATIONS.values():
        operation(doc)


# One child interpreter reads many documents, so that startup does not dominate. A crash ends the child and the test
# reports the document it died on.
MANY = """
import sys
import threading

sys.path.insert(0, sys.argv[1])
import pylopdf
from test_content_nesting import OPERATIONS


def attempt(name, operation, path, limits, password):
    doc = pylopdf.open(path, limits=limits, password=password or None)
    try:
        OPERATIONS[operation](doc)
    except pylopdf.LimitError as error:
        print(name, operation, error.code, flush=True)
    else:
        print(name, operation, "ok", flush=True)


for spec in sys.argv[2:]:
    name, path, mode, password, operations, stack = spec.split("|")
    limits = pylopdf.DocumentLimits.web() if mode == "web" else None
    for operation in operations.split(","):
        if stack == "0":
            attempt(name, operation, path, limits, password)
        else:
            # A thread with a small stack, such as musl gives by default.
            threading.stack_size(int(stack) * 1024)
            thread = threading.Thread(target=attempt, args=(name, operation, path, limits, password))
            thread.start()
            thread.join()
"""


@dataclass(frozen=True)
class Crash:
    """A hostile document and how the child interpreter opens it."""

    data: bytes
    mode: str = "default"
    password: str = ""
    #: Kibibytes of stack for the thread that runs each operation; zero runs it on the main thread.
    stack: int = 0


def _run_many(directory: Path, documents: dict[str, Crash], operations: Sequence[str]) -> dict[tuple[str, str], str]:
    specs = []
    for name, crash in documents.items():
        path = directory / f"{name}.pdf"
        path.write_bytes(crash.data)
        specs.append("|".join([name, str(path), crash.mode, crash.password, ",".join(operations), str(crash.stack)]))
    completed = subprocess.run(  # noqa: S603  # Run this interpreter on fixtures written by the test.
        [sys.executable, "-c", MANY, str(Path(__file__).parent), *specs],
        capture_output=True,
        check=False,
        text=True,
        timeout=600,
    )
    lines = [line.split() for line in completed.stdout.splitlines()]
    assert completed.returncode == 0, f"died after {lines[-1:]}: {completed.stderr[-400:]}"
    return {(name, operation): outcome for name, operation, outcome in lines}


def _expected(
    documents: dict[str, Crash], operations: Sequence[str], outcome: str = CODE
) -> dict[tuple[str, str], str]:
    return {(name, operation): outcome for name in documents for operation in operations}


#: One case for each way that hayro reaches a function or a colour space, for the child interpreter.
CRASH_CASES = [
    *(f"type4-{entry}" for entry in FUNCTION_ENTRIES),
    "stitching-shading",
    "stitching-shading-function-array",
    "stitching-mesh-shading",
    "stitching-transfer",
    "stitching-soft-mask-transfer",
    "stitching-tint-transform",
    "stitching-image-tint-transform",
    *(f"indexed-{entry}" for entry in COLOUR_ENTRIES),
    "separation-resource",
    "separation-image-space",
    "separation-soft-mask-group-space",
    "devicen-resource",
    "devicen-shading-space",
    "icc-resource",
    "icc-image-space",
]


def test_no_deep_structure_crashes_any_entry(tmp_path: Path) -> None:
    """Refuse structures that overflowed the stack of the interpreter before the check existed."""
    documents = {case: Crash(CASES[case](CRASHING[_structure_of(case)])) for case in CRASH_CASES}

    assert _run_many(tmp_path, documents, SOME_OPERATIONS) == _expected(documents, SOME_OPERATIONS)


@pytest.mark.parametrize("structure", ["type4", "stitching", "indexed"])
def test_no_operation_crashes_on_a_deep_structure(structure: str, tmp_path: Path) -> None:
    case = {"type4": "type4-shading", "stitching": "stitching-shading", "indexed": "indexed-resource"}[structure]
    documents = {structure: Crash(CASES[case](CRASHING[structure]))}

    assert _run_many(tmp_path, documents, list(OPERATIONS)) == _expected(documents, list(OPERATIONS))


def test_the_deepest_accepted_structures_fit_a_small_stack(tmp_path: Path) -> None:
    """Build and draw at the limit on a 256 KiB thread: the bounds exist to keep hayro's recursion inside a stack."""
    documents = {}
    for case in ("type4-shading", "stitching-shading", "indexed-resource", "icc-resource", "pattern-resource"):
        documents[case] = Crash(CASES[case](_accepted(case)), stack=256)
    for structure in ("separation", "devicen"):
        parts = Parts()
        space = COLOUR_SPACES[structure].build(parts, ACCEPTED[structure])
        data = page_scope(parts, f"/ColorSpace << /Cs1 {space} >> ", "/Cs1 cs")
        documents[f"{structure}-resource"] = Crash(data, stack=256)

    outcomes = _run_many(tmp_path, documents, SOME_OPERATIONS[:2])

    assert outcomes == _expected(documents, SOME_OPERATIONS[:2], "ok")


SCOPED = [
    ("type4", "shading", function_document),
    ("stitching", "shading", function_document),
    ("indexed", "resource", colour_document),
    ("icc", "resource", colour_document),
]


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize(("structure", "entry", "builder"), SCOPED)
def test_every_scope_that_lists_resources_is_checked(
    scope: str, structure: str, entry: str, builder: Callable[..., bytes]
) -> None:
    """Every place that resources can live leads the walk to the deep structure, drawn with or not."""
    refused = pylopdf.Document(stream=builder(structure, entry, REFUSED[structure], scope))
    assert _refusal(lambda: refused.get_page_text(0)).code == CODE
    assert _refusal(refused[0].get_pixmap).code == CODE

    accepted = pylopdf.Document(stream=builder(structure, entry, ACCEPTED[structure], scope))
    assert accepted.get_page_text(0) is not None
    assert accepted[0].get_pixmap().width == 300


def test_no_scope_crashes_any_operation(tmp_path: Path) -> None:
    documents = {
        f"{structure}-{entry}-{scope}": Crash(builder(structure, entry, CRASHING[structure], scope))
        for scope in SCOPES
        for structure, entry, builder in SCOPED[::2]
    }

    assert _run_many(tmp_path, documents, SOME_OPERATIONS[:2]) == _expected(documents, SOME_OPERATIONS[:2])


SAVED: dict[str, dict[str, Any]] = {
    "compressed": {"deflate": True},
    "object-streams": {"deflate": True, "object_streams": True},
    "encrypted": {"deflate": True, "user_pw": "secret"},
}


def test_saved_variants_are_refused_too(tmp_path: Path) -> None:
    """Flate-compressed streams, object streams, and decrypted documents reach the same check."""
    documents = {}
    for case in ("type4-shading", "indexed-resource"):
        original = pylopdf.Document(stream=CASES[case](CRASHING[_structure_of(case)]))
        for saved, options in SAVED.items():
            documents[f"{case}-{saved}"] = Crash(
                original.tobytes(**options), password="secret" if saved == "encrypted" else ""
            )

    assert _run_many(tmp_path, documents, SOME_OPERATIONS[:2]) == _expected(documents, SOME_OPERATIONS[:2])


def test_the_web_profile_does_not_change_the_refusal(tmp_path: Path) -> None:
    documents = {
        "type4-shading": Crash(CASES["type4-shading"](CRASH_BRACES), "web"),
        "stitching-shading": Crash(CASES["stitching-shading"](CRASH_CHAIN), "web"),
    }

    assert _run_many(tmp_path, documents, SOME_OPERATIONS[:2]) == _expected(documents, SOME_OPERATIONS[:2])


@pytest.mark.parametrize("structure", ["type4", "stitching", "indexed", "separation", "devicen", "icc", "pattern"])
def test_the_limits_are_exact(structure: str) -> None:
    case = {
        "type4": "type4-shading",
        "stitching": "stitching-shading",
        "indexed": "indexed-resource",
        "separation": "separation-resource",
        "devicen": "devicen-resource",
        "icc": "icc-resource",
        "pattern": "pattern-resource",
    }[structure]
    accepted = pylopdf.Document(stream=CASES[case](ACCEPTED[structure], "listed-only"))
    assert accepted.get_page_text(0) is not None

    refused = pylopdf.Document(stream=CASES[case](REFUSED[structure], "listed-only"))
    assert _refusal(lambda: refused.get_page_text(0)).code == CODE


def test_refusals_name_where_the_structure_is() -> None:
    chain = pylopdf.Document(stream=CASES["stitching-shading"](CHAIN_LIMIT + 1))
    message = str(_refusal(lambda: chain.get_page_text(0)))
    assert "chain of colour spaces and functions that starts at" in message
    assert "reachable from page 1 is deeper than the 32-level limit" in message

    program = pylopdf.Document(stream=CASES["type4-shading"](BRACE_LIMIT + 1))
    assert "Type 4 function 5 0 R reachable from page 1 nests procedures deeper than the 64-level limit" in str(
        _refusal(lambda: program.get_page_text(0))
    )


# --- what the walk must not refuse -------------------------------------------------------------------------------


def test_a_cycle_costs_one_level_per_object_and_is_not_infinite() -> None:
    """Hayro cuts a chain where it comes back to itself: a short cycle renders and a long one is a long chain."""
    for length in (1, 2, 3, CHAIN_LIMIT):
        parts = Parts()
        resources, content = shading(parts, ring(parts, length), 3)
        doc = pylopdf.Document(stream=page_scope(parts, resources, content))
        assert doc[0].get_pixmap().width == 300
        assert doc.get_page_text(0) is not None

    parts = Parts()
    resources, content = shading(parts, ring(parts, CHAIN_LIMIT + 1), 3)
    doc = pylopdf.Document(stream=page_scope(parts, resources, content))
    assert _refusal(lambda: doc.get_page_text(0)).code == CODE


def test_a_colour_space_that_names_itself_renders() -> None:
    parts = Parts()
    number = parts.add("[/Indexed 5 0 R 1 <00 FF>]")
    resources, content = colour_resource(parts, f"{number} 0 R", 1)
    doc = pylopdf.Document(stream=page_scope(parts, resources, content))

    assert doc[0].get_pixmap().width == 300


def test_a_long_cycle_is_refused_without_crashing(tmp_path: Path) -> None:
    parts = Parts()
    resources, content = shading(parts, ring(parts, CRASH_CHAIN), 3)
    documents = {"ring": Crash(page_scope(parts, resources, content))}

    assert _run_many(tmp_path, documents, SOME_OPERATIONS[:2]) == _expected(documents, SOME_OPERATIONS[:2])


def _two_pages(parts: Parts, first: str, second: str) -> bytes:
    """Build two pages whose resources are `first` and `second`; neither draws anything."""

    def page(resources: str) -> str:
        return f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << {resources}>> /Contents 4 0 R >>"

    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
            3: page(first),
            4: _stream("0 0 m"),
            5: page(second),
            **parts.objects,
        }
    )


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
@pytest.mark.parametrize(
    ("above", "refused"),
    [
        ((20, 5), ()),
        ((22, 22), ()),
        ((23, 5), (0,)),
        ((5, 23), (1,)),
    ],
)
def test_roots_that_share_a_cycle_are_measured_alike_in_any_order(
    above: tuple[int, int], refused: tuple[int, ...], order: tuple[int, int]
) -> None:
    """A ring of ten costs ten levels below any chain that names it, whichever page is checked first."""
    parts = Parts(first=6)
    member = ring(parts, 10)
    resources = [
        f"/Shading << /Sh1 << {SHADING}/Function {chain_into(parts, length, member)} >> >> " for length in above
    ]
    doc = pylopdf.Document(stream=_two_pages(parts, *resources))

    for page in order:
        if page in refused:
            assert _refusal(partial(doc.get_page_text, page)).code == CODE
        else:
            assert doc.get_page_text(page) is not None


# --- what the walk must not repeat -------------------------------------------------------------------------------
#
# A document can name one large object from thousands of places, and the check lists every entry of every resource
# dictionary whether or not content draws it, so reading the object for each entry would multiply their sizes: a file
# of a few hundred kilobytes would keep the first interpretation busy for minutes, where hayro, which never touches the
# unused entries, needs milliseconds.


def _shared_hub(parts: Parts, *, distinct: bool) -> str:
    """Ten thousand colour space entries that name one array of forty thousand elements, which names itself."""
    hub = parts.reserve(1)
    parts.objects[hub] = f"[/Indexed {hub} 0 R 1 <00 FF> " + "0 " * 40_000 + "]"
    if distinct:
        entries = " ".join(f"/Cs{index} [/Indexed {hub} 0 R 1 <00 FF>]" for index in range(10_000))
    else:
        entries = " ".join(f"/Cs{index} {hub} 0 R" for index in range(10_000))
    return f"/ColorSpace << {entries} >> "


def _shared_functions_array(parts: Parts) -> str:
    """Three thousand stitching functions that name one array of ten thousand sub-functions."""
    function = parts.add(TYPE2)
    listing = parts.add("[" + f"{function} 0 R " * 10_000 + "]")
    stitching = [
        parts.add(f"<< /FunctionType 3 /Domain [0 1] /Functions {listing} 0 R /Bounds [] /Encode [0 1] >>")
        for _ in range(3_000)
    ]
    spaces = " ".join(
        f"/Cs{index} [/Separation /Spot /DeviceRGB {number} 0 R]" for index, number in enumerate(stitching)
    )
    return f"/ColorSpace << {spaces} >> "


def _shared_shading_functions(parts: Parts) -> str:
    """Three thousand shadings whose `/Function` is one array of ten thousand functions."""
    function = parts.add(TYPE2)
    listing = parts.add("[" + f"{function} 0 R " * 10_000 + "]")
    shadings = " ".join(f"/Sh{index} << {SHADING}/Function {listing} 0 R >>" for index in range(3_000))
    return f"/Shading << {shadings} >> "


def _padding(entries: int) -> str:
    return " ".join(f"/Pad{index} 0" for index in range(entries))


def _shared_shading(parts: Parts) -> str:
    """Three thousand `/Shading` entries that name one shading dictionary of fifty thousand entries."""
    function = parts.add(TYPE2)
    shading = parts.add(f"<< {SHADING}/Function {function} 0 R {_padding(50_000)} >>")
    entries = " ".join(f"/Sh{index} {shading} 0 R" for index in range(3_000))
    return f"/Shading << {entries} >> "


def _shared_pattern(parts: Parts) -> str:
    """Three thousand `/Pattern` entries that name one shading pattern of fifty thousand entries."""
    function = parts.add(TYPE2)
    pattern = parts.add(f"<< /PatternType 2 /Shading << {SHADING}/Function {function} 0 R >> {_padding(50_000)} >>")
    entries = " ".join(f"/P{index} {pattern} 0 R" for index in range(3_000))
    return f"/Pattern << {entries} >> "


def _shared_soft_mask(parts: Parts) -> str:
    """Three thousand graphics states that name one soft-mask dictionary of fifty thousand entries."""
    group = parts.add(_stream("0 0 m", GROUP))
    mask = parts.add(f"<< /Type /Mask /S /Luminosity /G {group} 0 R {_padding(50_000)} >>")
    states = [parts.add(f"<< /Type /ExtGState /SMask {mask} 0 R >>") for _ in range(3_000)]
    entries = " ".join(f"/GS{index} {number} 0 R" for index, number in enumerate(states))
    return f"/ExtGState << {entries} >> "


def _shared_profile(parts: Parts) -> str:
    """Three thousand ICCBased spaces that name one profile whose dictionary lists 150,000 numbers."""
    profile = parts.add(_stream("", "/N 3 /Alternate /DeviceRGB /Padding [" + "0 " * 150_000 + "] "))
    spaces = " ".join(f"/Cs{index} [/ICCBased {profile} 0 R]" for index in range(3_000))
    return f"/ColorSpace << {spaces} >> "


def _shared_string(parts: Parts) -> str:
    """Three thousand Indexed spaces whose lookup table is one string of a megabyte."""
    table = parts.add("<" + "00" * 1_000_000 + ">")
    spaces = " ".join(f"/Cs{index} [/Indexed /DeviceRGB 0 {table} 0 R]" for index in range(3_000))
    return f"/ColorSpace << {spaces} >> "


SHARED_OBJECTS: dict[str, Callable[[Parts], str]] = {
    "hub-same-entry": partial(_shared_hub, distinct=False),
    "hub-distinct-entries": partial(_shared_hub, distinct=True),
    "functions-array": _shared_functions_array,
    "shading-functions-array": _shared_shading_functions,
    "shading": _shared_shading,
    "shading-pattern": _shared_pattern,
    "soft-mask": _shared_soft_mask,
    "profile": _shared_profile,
    "string": _shared_string,
}


@pytest.mark.parametrize("name", SHARED_OBJECTS)
def test_an_object_that_many_entries_name_is_read_once(name: str) -> None:
    parts = Parts()
    resources = SHARED_OBJECTS[name](parts)
    doc = pylopdf.Document(stream=page_scope(parts, resources, "0 0 m"))

    started = time.perf_counter()
    assert doc.get_page_text(0) is not None
    # Reading the object for every entry takes seconds to tens of seconds; reading it once takes milliseconds.
    assert time.perf_counter() - started < 1.5


def test_shared_sub_graphs_are_measured_once() -> None:
    """Thirty levels that each name the next one twice have a billion paths and a depth of thirty."""
    parts = Parts()
    first = parts.reserve(30)
    for index in range(30):
        number = first + index
        if index < 29:
            parts.objects[number] = (
                f"<< /FunctionType 3 /Domain [0 1] /Functions [{number + 1} 0 R {number + 1} 0 R] "
                "/Bounds [0.5] /Encode [0 1 0 1] >>"
            )
        else:
            parts.objects[number] = TYPE2
    # Listed but never drawn, so hayro never walks the billion paths; the check must not either.
    shading_object = parts.add(f"<< {SHADING}/Function {first} 0 R >>")
    data = page_scope(parts, f"/Shading << /Sh1 {shading_object} 0 R >> ", "0 0 m")
    doc = pylopdf.Document(stream=data)

    started = time.perf_counter()
    assert doc.get_page_text(0) is not None
    assert time.perf_counter() - started < 5

    # Deeper than the limit and shared: refused at once.
    parts = Parts()
    first = parts.reserve(60)
    for index in range(60):
        number = first + index
        if index < 59:
            parts.objects[number] = (
                f"<< /FunctionType 3 /Domain [0 1] /Functions [{number + 1} 0 R {number + 1} 0 R] "
                "/Bounds [0.5] /Encode [0 1 0 1] >>"
            )
        else:
            parts.objects[number] = TYPE2
    deep = pylopdf.Document(
        stream=page_scope(parts, f"/Shading << /Sh1 << {SHADING}/Function {first} 0 R >> >> ", "0 0 m")
    )
    assert _refusal(deep.get_text).code == CODE


# --- what the content walk must not repeat -------------------------------------------------------------------------
#
# The content walk lists the entries of the same dictionaries, and follows every reference it meets to a form, pattern,
# font, appearance, or glyph procedure, so parsing the object for each entry that names it multiplies their sizes in
# the same way. Each shape names one large object from thousands of places, through a different kind of reference.

TILING = "/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 /BBox [0 0 20 20] /XStep 20 /YStep 20 "
TYPE3 = (
    "/Type /Font /Subtype /Type3 /FontBBox [0 0 100 100] /FontMatrix [0.01 0 0 0.01 0 0] "
    "/Encoding << /Type /Encoding /Differences [97 /g0] >> /FirstChar 97 /LastChar 97 /Widths [100] "
)
#: How many entries name the large object, and how many keys it has.
ENTRIES = 3_000
KEYS = 50_000


def _large() -> str:
    """Return dictionary entries that nothing reads, closed by a space so that `/Length` can follow."""
    return _padding(KEYS) + " "


def _entries(prefix: str, numbers: Sequence[int]) -> str:
    """Resource entries `/{prefix}0`, `/{prefix}1`, and so on that name the objects `numbers`."""
    return " ".join(f"/{prefix}{index} {number} 0 R" for index, number in enumerate(numbers))


def _hidden_annotation(entries: str) -> str:
    """Build an annotation that hayro does not draw, so that only the walk reads its entries.

    hayro reads the appearance of every annotation it draws, which would add the same cost to the first page text.
    """
    return f"<< /Type /Annot /Subtype /FreeText /Rect [0 0 10 10] /F 2 {entries}>>"


def _annotations(numbers: Sequence[int]) -> str:
    """Return the page entry that lists the annotations `numbers`."""
    return "/Annots [" + " ".join(f"{number} 0 R" for number in numbers) + "] "


def _large_form(parts: Parts) -> bytes:
    """Three thousand `/XObject` entries that name one form of fifty thousand keys."""
    form = parts.add(_stream("", FORM + _large()))
    return page_scope(parts, f"/XObject << {_entries('Fm', [form] * ENTRIES)} >> ", "0 0 m")


def _large_dictionary_as_x_object(parts: Parts) -> bytes:
    """Three thousand `/XObject` entries that name one dictionary of fifty thousand keys, which is no stream."""
    dictionary = parts.add(f"<< {_padding(KEYS)} >>")
    return page_scope(parts, f"/XObject << {_entries('Fm', [dictionary] * ENTRIES)} >> ", "0 0 m")


def _large_tiling_pattern(parts: Parts) -> bytes:
    """Three thousand `/Pattern` entries that name one tiling pattern of fifty thousand keys."""
    pattern = parts.add(_stream("", TILING + _large()))
    return page_scope(parts, f"/Pattern << {_entries('P', [pattern] * ENTRIES)} >> ", "0 0 m")


def _large_font(parts: Parts) -> bytes:
    """Three thousand `/Font` entries that name one font of fifty thousand keys."""
    font = parts.add(f"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica {_padding(KEYS)} >>")
    return page_scope(parts, f"/Font << {_entries('F', [font] * ENTRIES)} >> ", "0 0 m")


def _large_appearance(parts: Parts) -> bytes:
    """Three thousand hidden annotations whose `/N` appearance is one form of fifty thousand keys."""
    form = parts.add(_stream("", FORM + _large()))
    annotations = [parts.add(_hidden_annotation(f"/AP << /N {form} 0 R >> ")) for _ in range(ENTRIES)]
    return parts.build("", "0 0 m", page=_annotations(annotations))


def _large_appearance_dictionary(parts: Parts) -> bytes:
    """Three thousand hidden annotations that name one `/AP` dictionary of fifty thousand keys."""
    form = parts.add(_stream("", FORM))
    appearances = parts.add(f"<< /N {form} 0 R {_padding(KEYS)} >>")
    annotations = [parts.add(_hidden_annotation(f"/AP {appearances} 0 R ")) for _ in range(ENTRIES)]
    return parts.build("", "0 0 m", page=_annotations(annotations))


def _large_glyph_procedure(parts: Parts) -> bytes:
    """Build a Type 3 font whose three thousand `/CharProcs` entries name one procedure of fifty thousand keys."""
    procedure = parts.add(_stream("100 0 0 0 100 100 d1", _large()))
    font = parts.add(f"<< {TYPE3}/CharProcs << {_entries('g', [procedure] * ENTRIES)} >> >>")
    return page_scope(parts, f"/Font << /F3 {font} 0 R >> ", "0 0 m")


def _large_glyph_procedures(parts: Parts) -> bytes:
    """Three thousand Type 3 fonts that name one `/CharProcs` dictionary of fifty thousand keys."""
    procedure = parts.add(_stream("100 0 0 0 100 100 d1", ""))
    procedures = parts.add(f"<< /g0 {procedure} 0 R {_padding(KEYS)} >>")
    fonts = [parts.add(f"<< {TYPE3}/CharProcs {procedures} 0 R >>") for _ in range(ENTRIES)]
    return page_scope(parts, f"/Font << {_entries('F', fonts)} >> ", "0 0 m")


def _large_resources(parts: Parts) -> bytes:
    """Three thousand forms that name one `/Resources` dictionary of fifty thousand keys."""
    resources = parts.add(f"<< {_padding(KEYS)} >>")
    forms = [parts.add(_stream("", FORM + f"/Resources {resources} 0 R ")) for _ in range(ENTRIES)]
    return page_scope(parts, f"/XObject << {_entries('Fm', forms)} >> ", "0 0 m")


def _large_glyph_resources(parts: Parts) -> bytes:
    """Three thousand Type 3 fonts that name one `/Resources` dictionary of fifty thousand keys."""
    resources = parts.add(f"<< {_padding(KEYS)} >>")
    fonts = [parts.add(f"<< {TYPE3}/Resources {resources} 0 R >>") for _ in range(ENTRIES)]
    return page_scope(parts, f"/Font << {_entries('F', fonts)} >> ", "0 0 m")


def _large_category(parts: Parts) -> bytes:
    """Three thousand forms whose resources name one `/Font` dictionary of fifty thousand keys."""
    fonts = parts.add(f"<< {_padding(KEYS)} >>")
    forms = [parts.add(_stream("", FORM + f"/Resources << /Font {fonts} 0 R >> ")) for _ in range(ENTRIES)]
    return page_scope(parts, f"/XObject << {_entries('Fm', forms)} >> ", "0 0 m")


def _large_soft_mask_group(parts: Parts) -> bytes:
    """Three thousand graphics states whose soft masks name one group of fifty thousand keys."""
    group = parts.add(_stream("0 0 m", GROUP + _large()))
    mask = f"/SMask << /Type /Mask /S /Luminosity /G {group} 0 R >> "
    states = [parts.add(f"<< /Type /ExtGState {mask}>>") for _ in range(ENTRIES)]
    return page_scope(parts, f"/ExtGState << {_entries('GS', states)} >> ", "0 0 m")


def _large_group_attributes(parts: Parts) -> bytes:
    """Three thousand soft-mask groups that name one `/Group` dictionary of fifty thousand keys."""
    attributes = parts.add(f"<< /S /Transparency /CS /DeviceGray {_padding(KEYS)} >>")
    states = []
    for _ in range(ENTRIES):
        group = parts.add(_stream("0 0 m", FORM + f"/Group {attributes} 0 R "))
        states.append(parts.add(f"<< /Type /ExtGState /SMask << /Type /Mask /S /Luminosity /G {group} 0 R >> >>"))
    return page_scope(parts, f"/ExtGState << {_entries('GS', states)} >> ", "0 0 m")


def _large_font_selection(parts: Parts) -> bytes:
    """Four thousand graphics states that name one `/Font` array of 120,000 elements."""
    font = parts.add(FONT)
    selection = parts.add(f"[{font} 0 R 12 " + "0 " * 120_000 + "]")
    states = [parts.add(f"<< /Type /ExtGState /Font {selection} 0 R >>") for _ in range(4_000)]
    return page_scope(parts, f"/ExtGState << {_entries('GS', states)} >> ", "0 0 m")


def _large_selected_font(parts: Parts) -> bytes:
    """Three thousand graphics states whose `/Font` arrays select one font of fifty thousand keys."""
    font = parts.add(f"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica {_padding(KEYS)} >>")
    states = [parts.add(f"<< /Type /ExtGState /Font [{font} 0 R 12] >>") for _ in range(ENTRIES)]
    return page_scope(parts, f"/ExtGState << {_entries('GS', states)} >> ", "0 0 m")


SHARED_CONTENT: dict[str, Callable[[Parts], bytes]] = {
    "form": _large_form,
    "x-object-without-stream": _large_dictionary_as_x_object,
    "tiling-pattern": _large_tiling_pattern,
    "font": _large_font,
    "appearance": _large_appearance,
    "appearance-dictionary": _large_appearance_dictionary,
    "glyph-procedure": _large_glyph_procedure,
    "glyph-procedures": _large_glyph_procedures,
    "resources": _large_resources,
    "glyph-resources": _large_glyph_resources,
    "category": _large_category,
    "soft-mask-group": _large_soft_mask_group,
    "group-attributes": _large_group_attributes,
    "font-selection": _large_font_selection,
    "selected-font": _large_selected_font,
}


@pytest.mark.parametrize("name", SHARED_CONTENT)
def test_a_content_object_that_many_entries_name_is_read_once(name: str) -> None:
    doc = pylopdf.Document(stream=SHARED_CONTENT[name](Parts()))

    started = time.perf_counter()
    assert doc.get_page_text(0) is not None
    # Parsing the object for every entry takes seconds to tens of seconds; parsing it once takes milliseconds.
    assert time.perf_counter() - started < 1.5


def _image_and_pattern(depth: int) -> bytes:
    """Name one stream as a tiling pattern of the page and as an image in the resources of a form.

    The walk queues the stream as content before it meets the image entry, which must still measure the colour space.
    """
    parts = Parts()
    space = indexed_chain(parts, depth)
    both = parts.add(
        _stream(
            b"\x00",
            f"/Type /XObject /Subtype /Image /Width 1 /Height 1 /BitsPerComponent 8 /ColorSpace {space} "
            "/PatternType 1 /PaintType 1 /TilingType 1 /BBox [0 0 20 20] /XStep 20 /YStep 20 ",
        )
    )
    form = parts.add(
        _stream("q 50 0 0 50 10 10 cm /Im0 Do Q", FORM + f"/Resources << /XObject << /Im0 {both} 0 R >> >> ")
    )
    return parts.build(f"/XObject << /Fm0 {form} 0 R >> /Pattern << /P1 {both} 0 R >> ", "q /Fm0 Do Q")


def test_a_stream_that_is_a_pattern_and_an_image_is_measured_as_an_image() -> None:
    """Content that was cleared in one role says nothing about the colour space another role builds."""
    accepted = pylopdf.Document(stream=_image_and_pattern(ACCEPTED["indexed"]))
    assert accepted[0].get_pixmap().width == 300

    refused = pylopdf.Document(stream=_image_and_pattern(REFUSED["indexed"]))
    assert _refusal(refused.get_text).code == CODE
    assert _refusal(refused[0].get_pixmap).code == CODE


def test_a_stream_that_is_a_pattern_and_an_image_does_not_crash(tmp_path: Path) -> None:
    documents = {"pattern-and-image": Crash(_image_and_pattern(CRASH_CHAIN))}

    assert _run_many(tmp_path, documents, SOME_OPERATIONS[:2]) == _expected(documents, SOME_OPERATIONS[:2])


def test_wide_stitching_functions_are_not_deep() -> None:
    """A gradient with thousands of pieces is one level of functions, however wide."""
    parts = Parts()
    pieces = 3000
    first = parts.reserve(pieces)
    for index in range(pieces):
        parts.objects[first + index] = TYPE2
    functions = " ".join(f"{first + index} 0 R" for index in range(pieces))
    bounds = " ".join(f"{(index + 1) / pieces:.6f}" for index in range(pieces - 1))
    encode = " ".join(["0 1"] * pieces)
    gradient = parts.add(
        f"<< /FunctionType 3 /Domain [0 1] /Functions [{functions}] /Bounds [{bounds}] /Encode [{encode}] >>"
    )
    resources, content = shading(parts, f"{gradient} 0 R", 3)
    doc = pylopdf.Document(stream=page_scope(parts, resources, content))

    assert doc[0].get_pixmap().width == 300


@pytest.mark.parametrize("levels", [15, 16])
def test_an_indirect_functions_array_is_a_level_of_its_own(levels: int) -> None:
    """A `/Functions` array that is an object of its own is a node, so each level of the chain is two."""
    parts = Parts()
    resources, _ = shading(parts, stitching_through_arrays(parts, levels), 3)
    doc = pylopdf.Document(stream=page_scope(parts, resources, "0 0 m"))

    # Fifteen levels are 31 nodes with the Type 2 function at the end, sixteen are 33.
    if levels > 15:
        assert _refusal(partial(doc.get_page_text, 0)).code == CODE
    else:
        assert doc.get_page_text(0) is not None


def test_a_chain_through_indirect_functions_arrays_is_refused_without_crashing(tmp_path: Path) -> None:
    parts = Parts()
    resources, _ = shading(parts, stitching_through_arrays(parts, CRASH_CHAIN // 2), 3)
    documents = {"arrays": Crash(page_scope(parts, resources, "0 0 m"))}

    assert _run_many(tmp_path, documents, SOME_OPERATIONS[:2]) == _expected(documents, SOME_OPERATIONS[:2])


def test_one_object_in_two_roles_is_measured_in_both() -> None:
    """The same dictionary is a function in one place and a colour space in another."""
    parts = Parts()
    chain = stitching_chain(parts, CHAIN_LIMIT + 1)
    resources, content = colour_resource(parts, chain, 1)

    doc = pylopdf.Document(stream=page_scope(parts, resources, content))

    assert _refusal(lambda: doc.get_page_text(0)).code == CODE


def test_dictionaries_listed_as_both_colour_spaces_and_shadings_are_checked_in_both() -> None:
    parts = Parts()
    deep = indexed_chain(parts, CHAIN_LIMIT + 1)
    shading_object = parts.add(f"<< {SHADING}/Function {TYPE2} >>")
    both = parts.add(f"<< /Cs1 {deep} /Sh1 {shading_object} 0 R >>")
    data = page_scope(parts, f"/ColorSpace {both} 0 R /Shading {both} 0 R ", "0 0 m")

    assert _refusal(pylopdf.Document(stream=data).get_text).code == CODE


def test_every_listed_resource_counts_even_when_unused() -> None:
    parts = Parts()
    resources, _ = colour_resource(parts, indexed_chain(parts, CHAIN_LIMIT + 1), 1)
    doc = pylopdf.Document(stream=page_scope(parts, resources, "0 0 m"))

    assert _refusal(lambda: doc.get_page_text(0)).code == CODE


def test_refusal_is_per_page() -> None:
    parts = Parts(first=7)
    deep = indexed_chain(parts, CHAIN_LIMIT + 1)
    resources = "/Font << /F1 4 0 R >> "
    doc = pylopdf.Document(
        stream=build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
                f"/Resources << {resources}>> /Contents 6 0 R >>",
                4: FONT,
                5: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
                f"/Resources << {resources}/ColorSpace << /Cs1 {deep} >> >> /Contents 6 0 R >>",
                6: _stream("BT /F1 20 Tf 20 100 Td (PAGE TEXT) Tj ET"),
                **parts.objects,
            }
        )
    )

    assert doc.get_page_text(0).strip() == "PAGE TEXT"
    assert _refusal(lambda: doc.get_page_text(1)).code == CODE
    assert doc.get_page_text(0).strip() == "PAGE TEXT"
    assert doc.render_pages(pages=[0])
    assert _refusal(doc.render_pages).code == CODE
    assert _refusal(doc.get_text).code == CODE
    assert "PAGE TEXT" in doc.get_text(pages=[0])


def test_edits_drop_the_clearance_of_the_previous_snapshot() -> None:
    parts = Parts()
    resources, content = colour_resource(parts, indexed_chain(parts, CHAIN_LIMIT + 1), 1)
    deep = pylopdf.Document(stream=page_scope(parts, resources, content))
    parts = Parts()
    resources, content = colour_resource(parts, indexed_chain(parts, 3), 1)
    doc = pylopdf.Document(stream=page_scope(parts, resources, content))
    assert doc.get_page_text(0) is not None

    doc.insert_pdf(deep)
    assert len(doc) == 2
    assert doc.get_page_text(0) is not None
    assert _refusal(lambda: doc.get_page_text(1)).code == CODE
    assert _refusal(lambda: doc.get_page_text(1)).code == CODE

    doc.delete_pages([1])
    assert doc.get_page_text(0) is not None
    assert doc.get_text() is not None


def test_real_world_pages_pass_the_check() -> None:
    """Shadings, Separation spaces, soft masks, and Type 3 fonts of real files stay far below the limits."""
    root = Path(__file__).parent / "assets" / "real_world"
    for name in (
        "pdf20-simple.pdf",
        "usrguide.pdf",
        "pdfium-smask-blend.pdf",
        "pdfium-type3.pdf",
        "pdfium-links-highlights-annots.pdf",
    ):
        doc = pylopdf.open(root / name)
        assert doc.get_text() is not None
        assert doc.render_pages(pages=[0])
        doc.compress_images()
