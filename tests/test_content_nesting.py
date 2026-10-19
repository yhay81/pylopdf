"""Content streams that nest arrays or dictionaries too deeply for hayro.

hayro 0.7 reads arrays and dictionaries in content streams by unbounded
recursion (LaurenzV/hayro#1347). A stream with a few hundred thousand nested
`[` overflows the stack, and a stack overflow kills the process instead of
raising, so those cases run in a child interpreter. Every path that lets hayro
interpret a page must refuse such a stream with `LimitError` instead.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path

import pytest
from conftest import build_raw_pdf

import pylopdf

pytestmark = pytest.mark.filterwarnings("ignore:text extraction skipped glyphs")

CODE = "content_nesting_depth"
#: Deep enough to overflow the stack of any thread that pylopdf runs on.
CRASH_DEPTH = 300_000
#: The deepest nesting that reaches hayro's parser.
LIMIT = 64
FONT = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
FORM = "/Type /XObject /Subtype /Form /BBox [0 0 300 300] "
SHOW = "BT /F1 20 Tf 20 100 Td (PAGE TEXT) Tj ET"

#: Every public path that makes hayro interpret a page.
OPERATIONS: dict[str, Callable[[pylopdf.Document], object]] = {
    "get_page_text": lambda doc: doc.get_page_text(0),
    "get_text_words": lambda doc: doc[0].get_text("words"),
    "get_text_dict": lambda doc: doc[0].get_text("dict"),
    "get_text_rawdict": lambda doc: doc[0].get_text("rawdict"),
    "search_for": lambda doc: doc[0].search_for("PAGE"),
    "find_tables": lambda doc: doc[0].find_tables(),
    "get_drawings": lambda doc: doc[0].get_drawings(),
    "get_images": lambda doc: doc[0].get_images(),
    "get_pixmap": lambda doc: doc[0].get_pixmap(),
    "render_page": lambda doc: doc.render_page(0),
    "render_pages": lambda doc: doc.render_pages(workers=2),
    "render_page_svg": lambda doc: doc.render_page_svg(0),
    "to_markdown": lambda doc: doc.to_markdown(),
    "compress_images": lambda doc: doc.compress_images(),
}

# Each operation runs on its own document, so that one refusal cannot hide a
# crash in the next one. The child reuses this module's operation table.
CHILD = """
import os
import sys

sys.path.insert(0, sys.argv[1])
import pylopdf
from test_content_nesting import OPERATIONS

path, mode = sys.argv[2], sys.argv[3]
limits = pylopdf.DocumentLimits.web() if mode == "web" else None
password = os.environ.get("CONTENT_NESTING_PASSWORD")
for name in sys.argv[4:]:
    doc = pylopdf.open(path, limits=limits, password=password)
    try:
        OPERATIONS[name](doc)
    except pylopdf.LimitError as error:
        print(name, error.code, flush=True)
    else:
        print(name, "ok", flush=True)
"""


def _stream(body: str | bytes, entries: str = "") -> bytes:
    data = body.encode("latin-1") if isinstance(body, str) else body
    return f"<< {entries}/Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream"


def _arrays(depth: int) -> str:
    return "[" * depth + "(X)" + "]" * depth


def _dictionaries(depth: int) -> str:
    return "<< /A " * depth + "1" + " >>" * depth


def _show(operand: str) -> str:
    return f"BT /F1 20 Tf 20 100 Td {operand} TJ ET"


def _page(
    objects: dict[int, str | bytes],
    *,
    contents: str = "5 0 R",
    fonts: str = "/F1 4 0 R ",
    resources: str = "",
    extra: str = "",
) -> bytes:
    """Build a one-page PDF; the caller supplies objects 5 and up."""
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
            f"/Resources << /Font << {fonts}>> {resources}>> /Contents {contents} {extra}>>",
            4: FONT,
            **objects,
        }
    )


def _document(contents: Sequence[str | bytes], shared: Sequence[str | bytes] = (), resources: str = "") -> bytes:
    """Build pages that draw `contents`; `shared` objects follow and are `{0}`, `{1}`, ... in `resources`."""
    first_shared = 4 + 2 * len(contents)
    kids = " ".join(f"{4 + 2 * index} 0 R" for index in range(len(contents)))
    page_resources = resources.format(*range(first_shared, first_shared + len(shared)))
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {len(contents)} >>",
        3: FONT,
    }
    for index, content in enumerate(contents):
        objects[4 + 2 * index] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
            f"/Resources << /Font << /F1 3 0 R >> {page_resources}>> /Contents {5 + 2 * index} 0 R >>"
        )
        objects[5 + 2 * index] = _stream(content)
    for offset, shared_object in enumerate(shared):
        objects[first_shared + offset] = shared_object
    return build_raw_pdf(objects)


def _type3_font(procedure: int, resources: str = "") -> str:
    return (
        "<< /Type /Font /Subtype /Type3 /FontBBox [0 0 100 100] /FontMatrix [0.01 0 0 0.01 0 0] "
        f"/CharProcs << /a {procedure} 0 R >> /Encoding << /Type /Encoding /Differences [97 /a] >> "
        f"/FirstChar 97 /LastChar 97 /Widths [100] {resources}>>"
    )


def page_content(content: str | bytes) -> bytes:
    return _page({5: _stream(content)})


def page_content_array(*streams: str) -> bytes:
    objects: dict[int, str | bytes] = {5 + index: _stream(content) for index, content in enumerate(streams)}
    references = " ".join(f"{number} 0 R" for number in objects)
    return _page(objects, contents=f"[{references}]")


def form(content: str) -> bytes:
    return _page(
        {5: _stream(f"{SHOW} q /Fm0 Do Q"), 6: _stream(content, FORM + "/Resources << /Font << /F1 4 0 R >> >> ")},
        resources="/XObject << /Fm0 6 0 R >> ",
    )


def nested_form(content: str) -> bytes:
    return _page(
        {
            5: _stream("q /Outer Do Q"),
            6: _stream("q /Inner Do Q", FORM + "/Resources << /XObject << /Inner 7 0 R >> >> "),
            7: _stream(content, FORM + "/Resources << /Font << /F1 4 0 R >> >> "),
        },
        resources="/XObject << /Outer 6 0 R >> ",
    )


def annotation(content: str, subtype: str = "/Subtype /FreeText /Contents (note)") -> bytes:
    return _page(
        {
            5: _stream(SHOW),
            6: f"<< /Type /Annot {subtype} /Rect [10 150 200 200] /AP << /N 7 0 R >> >>",
            7: _stream(
                content, "/Type /XObject /Subtype /Form /BBox [0 0 190 50] /Resources << /Font << /F1 4 0 R >> >> "
            ),
        },
        extra="/Annots [6 0 R] ",
    )


def widget(content: str) -> bytes:
    return annotation(content, "/Subtype /Widget /FT /Tx /T (name) /V (value)")


def appearance_state(content: str, state: str = "/On", *, indirect: bool = False, holder: str = "") -> bytes:
    """Put `content` in one state of a check box's `/AP /N` dictionary, by default the one hayro draws.

    hayro draws the state that `/AS` names, or `/Off` when that is no stream, so `/AS /Gone` reaches
    `/Off`. `holder` puts `content` in the other state instead. With `indirect`, the state dictionary and
    `/AS` are objects of their own.
    """
    form = "/Type /XObject /Subtype /Form /BBox [0 0 190 50] /Resources << /Font << /F1 4 0 R >> >> "
    holder = holder or ("/Off" if state == "/Gone" else state)
    other = "/On" if holder == "/Off" else "/Off"
    states = f"<< {holder} 7 0 R {other} 8 0 R >>"
    objects: dict[int, str | bytes] = {
        5: _stream(SHOW),
        7: _stream(content, form),
        8: _stream("0 0 m", form),
    }
    if indirect:
        objects[9] = states
        objects[10] = state
        states, state = "9 0 R", "10 0 R"
    objects[6] = (
        f"<< /Type /Annot /Subtype /Widget /FT /Btn /T (box) /AS {state} /Rect [10 150 200 200] "
        f"/AP << /N {states} >> >>"
    )
    return _page(objects, extra="/Annots [6 0 R] ")


def tiling_pattern(content: str) -> bytes:
    pattern = (
        "/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 /BBox [0 0 20 20] /XStep 20 /YStep 20 "
        "/Resources << /Font << /F1 4 0 R >> >> "
    )
    return _page(
        {5: _stream(f"/Pattern cs /P1 scn 0 0 200 200 re f {SHOW}"), 6: _stream(content, pattern)},
        resources="/Pattern << /P1 6 0 R >> ",
    )


def glyph_procedure(content: str) -> bytes:
    return _page(
        {
            5: _stream("BT /F3 12 Tf 20 100 Td (a) Tj ET"),
            6: _type3_font(7),
            7: _stream("100 0 0 0 100 100 d1 " + content),
        },
        fonts="/F1 4 0 R /F3 6 0 R ",
    )


def soft_mask_group(content: str) -> bytes:
    group = FORM + "/Group << /S /Transparency /CS /DeviceGray >> /Resources << /Font << /F1 4 0 R >> >> "
    return _page(
        {
            5: _stream("/GS0 gs 0 0 200 200 re f"),
            6: "<< /Type /ExtGState /SMask << /Type /Mask /S /Luminosity /G 7 0 R >> >>",
            7: _stream(content, group),
        },
        resources="/ExtGState << /GS0 6 0 R >> ",
    )


def extgstate_font(content: str) -> bytes:
    """Reach a Type 3 font only through an ExtGState `/Font` entry."""
    return _page(
        {
            5: _stream("/GS0 gs BT 20 100 Td (a) Tj ET"),
            6: "<< /Type /ExtGState /Font [7 0 R 12] >>",
            7: _type3_font(8),
            8: _stream("100 0 0 0 100 100 d1 " + content),
        },
        resources="/ExtGState << /GS0 6 0 R >> ",
    )


def inherited_resources(content: str) -> bytes:
    """List the deep form only in the Pages node; the page inherits its resources."""
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 300 300] "
            "/Resources << /Font << /F1 4 0 R >> /XObject << /Fm0 6 0 R >> >> >>",
            3: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            4: FONT,
            5: _stream(f"{SHOW} q /Fm0 Do Q"),
            6: _stream(content, FORM + "/Resources << /Font << /F1 4 0 R >> >> "),
        }
    )


def pattern_in_form(content: str) -> bytes:
    pattern = "/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 /BBox [0 0 20 20] /XStep 20 /YStep 20 "
    return _page(
        {
            5: _stream(f"{SHOW} q /Fm0 Do Q"),
            6: _stream("/Pattern cs /P1 scn 0 0 200 200 re f", FORM + "/Resources << /Pattern << /P1 7 0 R >> >> "),
            7: _stream(content, pattern + "/Resources << /Font << /F1 4 0 R >> >> "),
        },
        resources="/XObject << /Fm0 6 0 R >> ",
    )


def form_in_glyph_resources(content: str) -> bytes:
    """Reach a form through the resources of a Type 3 font that the page selects."""
    font = _type3_font(7, "/Resources << /XObject << /Fm0 8 0 R >> >> ")
    return _page(
        {
            5: _stream("BT /F3 12 Tf 20 100 Td (a) Tj ET"),
            6: font,
            7: _stream("100 0 0 0 100 100 d1 q /Fm0 Do Q"),
            8: _stream(content, FORM + "/Resources << /Font << /F1 4 0 R >> >> "),
        },
        fonts="/F1 4 0 R /F3 6 0 R ",
    )


def soft_mask_in_form(content: str) -> bytes:
    group = FORM + "/Group << /S /Transparency /CS /DeviceGray >> /Resources << /Font << /F1 4 0 R >> >> "
    return _page(
        {
            5: _stream(f"{SHOW} q /Fm0 Do Q"),
            6: _stream("/GS0 gs 0 0 200 200 re f", FORM + "/Resources << /ExtGState << /GS0 7 0 R >> >> "),
            7: "<< /Type /ExtGState /SMask << /Type /Mask /S /Luminosity /G 8 0 R >> >>",
            8: _stream(content, group),
        },
        resources="/XObject << /Fm0 6 0 R >> ",
    )


def shared_category(leaf: str, content: str) -> bytes:
    """Serve one dictionary as every resource category; the leaf lives in the role named by `leaf`.

    Every role walks the dictionary differently, so one walk must not stand in for another.
    """
    group = FORM + "/Group << /S /Transparency /CS /DeviceGray >> /Resources << /Font << /F1 4 0 R >> >> "
    pattern = (
        "/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 /BBox [0 0 20 20] /XStep 20 /YStep 20 "
        "/Resources << /Font << /F1 4 0 R >> >> "
    )
    shallow = "0 0 m"
    drawing = {
        "form": "q /Fm0 Do Q",
        "pattern": "/Pattern cs /P1 scn 0 0 200 200 re f",
        "font": "BT /F3 12 Tf 20 100 Td (a) Tj ET",
        "extgstate": "/GS0 gs 0 0 200 200 re f",
    }[leaf]
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Contents 12 0 R "
            "/Resources << /XObject 5 0 R /Pattern 5 0 R /Font 5 0 R /ExtGState 5 0 R >> >>",
            4: FONT,
            5: "<< /F1 4 0 R /Fm0 6 0 R /P1 7 0 R /F3 8 0 R /GS0 10 0 R >>",
            6: _stream(content if leaf == "form" else shallow, FORM + "/Resources << /Font << /F1 4 0 R >> >> "),
            7: _stream(content if leaf == "pattern" else shallow, pattern),
            8: _type3_font(9),
            9: _stream("100 0 0 0 100 100 d1 " + (content if leaf == "font" else shallow)),
            10: "<< /Type /ExtGState /SMask << /Type /Mask /S /Luminosity /G 11 0 R >> >>",
            11: _stream(content if leaf == "extgstate" else shallow, group),
            12: _stream(drawing),
        }
    )


def resources_alias_category(content: str) -> bytes:
    """Make the page's /Font dictionary also the /Resources of a form that draws with a glyph font.

    The dictionary holds a font of its own and a /Font category of its own. Neither role may stand in
    for the other.
    """
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Contents 5 0 R "
            "/Resources << /Font 8 0 R /XObject << /Fm0 6 0 R >> >> >>",
            4: FONT,
            5: _stream("q /Fm0 Do Q"),
            6: _stream("BT /F3 12 Tf 20 100 Td (a) Tj ET", FORM + "/Resources 8 0 R "),
            7: _type3_font(9),
            8: "<< /F1 4 0 R /Font << /F3 7 0 R >> >>",
            9: _stream("100 0 0 0 100 100 d1 " + content),
        }
    )


def chain_cycle(content: str) -> bytes:
    """List a form in the dictionary that is also the page's /Font category and the form's /Pattern category.

    hayro refuses to follow a reference back into the chain of objects it is reading. Reached as a
    "font" through that dictionary, the form's /Pattern entry points back into the chain and does not
    resolve, although it resolves when the page draws the form. A walk that lets the first path decide
    never meets the pattern.
    """
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Contents 5 0 R "
            "/Resources << /Font 8 0 R /XObject << /Fm0 6 0 R >> >> >>",
            4: FONT,
            5: _stream("q /Fm0 Do Q"),
            6: _stream("/Pattern cs /P1 scn 0 0 200 200 re f", FORM + "/Resources << /Pattern 8 0 R >> "),
            7: _stream(
                content,
                "/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 /BBox [0 0 20 20] /XStep 20 /YStep 20 "
                "/Resources << /Font << /F1 4 0 R >> >> ",
            ),
            8: "<< /F1 4 0 R /P1 7 0 R /Fm0 6 0 R >>",
        }
    )


def _direct_fonts(deep: str, where: str, content: str) -> bytes:
    """Two direct Type 3 fonts inside one indirect object, `deep` being the one with the deep glyph.

    A direct dictionary reports the object number of the object that contains it, so a guard that
    identifies fonts that way sees one font. Which of the two it meets first depends on hash order,
    so the callers try both.
    """

    def font(name: str) -> str:
        return _type3_font(8 if name == deep else 7)

    fonts = f"/F1 4 0 R /GA {font('GA')} /GB {font('GB')}"
    page = "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Contents 5 0 R /Resources {} >>"
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        4: FONT,
        5: _stream(f"BT /{deep} 12 Tf 20 100 Td (a) Tj ET"),
        7: _stream("100 0 0 0 100 100 d1 0 0 m"),
        8: _stream("100 0 0 0 100 100 d1 " + content),
    }
    if where == "font-dictionary":
        objects[3] = page.format("<< /Font 6 0 R >>")
        objects[6] = f"<< {fonts} >>"
    elif where == "resources":
        objects[3] = page.format("6 0 R")
        objects[6] = f"<< /Font << {fonts} >> >>"
    else:
        # Each ExtGState selects its font directly, and both states sit in one indirect dictionary.
        states = " ".join(f"/S{name} << /Type /ExtGState /Font [{font(name)} 12] >>" for name in ("GA", "GB"))
        objects[3] = page.format("<< /ExtGState 6 0 R >>")
        objects[5] = _stream(f"/S{deep} gs BT 20 100 Td (a) Tj ET")
        objects[6] = f"<< {states} >>"
    return build_raw_pdf(objects)


#: Where hayro interprets content, with a document builder for each.
PLACEMENTS: dict[str, Callable[[int], bytes]] = {
    "page": lambda depth: page_content(_show(_arrays(depth))),
    "form": lambda depth: form(_show(_arrays(depth))),
    "nested-form": lambda depth: nested_form(_show(_arrays(depth))),
    "annotation": lambda depth: annotation(_show(_arrays(depth))),
    "widget": lambda depth: widget(_show(_arrays(depth))),
    "appearance-state": lambda depth: appearance_state(_show(_arrays(depth))),
    "appearance-off-state": lambda depth: appearance_state(_show(_arrays(depth)), "/Gone"),
    "indirect-appearance-states": lambda depth: appearance_state(_show(_arrays(depth)), indirect=True),
    "tiling-pattern": lambda depth: tiling_pattern(_show(_arrays(depth))),
    "glyph-procedure": lambda depth: glyph_procedure(_show(_arrays(depth))),
    "soft-mask-group": lambda depth: soft_mask_group(_show(_arrays(depth))),
    "extgstate-font": lambda depth: extgstate_font(_show(_arrays(depth))),
    "inherited-resources": lambda depth: inherited_resources(_show(_arrays(depth))),
    "pattern-in-form": lambda depth: pattern_in_form(_show(_arrays(depth))),
    "form-in-glyph-resources": lambda depth: form_in_glyph_resources(_show(_arrays(depth))),
    "soft-mask-in-form": lambda depth: soft_mask_in_form(_show(_arrays(depth))),
    "shared-category-form": lambda depth: shared_category("form", _show(_arrays(depth))),
    "shared-category-pattern": lambda depth: shared_category("pattern", _show(_arrays(depth))),
    "shared-category-font": lambda depth: shared_category("font", _show(_arrays(depth))),
    "shared-category-extgstate": lambda depth: shared_category("extgstate", _show(_arrays(depth))),
    "resources-alias-category": lambda depth: resources_alias_category(_show(_arrays(depth))),
    "cyclic-chain-resources": lambda depth: chain_cycle(_show(_arrays(depth))),
    "direct-fonts-a": lambda depth: _direct_fonts("GA", "font-dictionary", _show(_arrays(depth))),
    "direct-fonts-b": lambda depth: _direct_fonts("GB", "font-dictionary", _show(_arrays(depth))),
    "direct-fonts-in-resources-a": lambda depth: _direct_fonts("GA", "resources", _show(_arrays(depth))),
    "direct-fonts-in-resources-b": lambda depth: _direct_fonts("GB", "resources", _show(_arrays(depth))),
    "direct-fonts-in-graphics-states-a": lambda depth: _direct_fonts("GA", "graphics-states", _show(_arrays(depth))),
    "direct-fonts-in-graphics-states-b": lambda depth: _direct_fonts("GB", "graphics-states", _show(_arrays(depth))),
    "dictionary-operand": lambda depth: page_content(f"/P {_dictionaries(depth)} BDC EMC {SHOW}"),
    "dictionary-keys": lambda depth: page_content(f"/P {'<<' * depth} BDC EMC {SHOW}"),
    "inline-image-dictionary": lambda depth: page_content(
        f"q 10 0 0 10 0 0 cm BI /W 1 /H 1 /BPC 8 /CS /G /Foo {_arrays(depth)} ID \x00 EI Q {SHOW}"
    ),
}


def _run_child(
    path: Path, names: Sequence[str] = tuple(OPERATIONS), mode: str = "default", password: str | None = None
) -> dict[str, str]:
    environment = {**os.environ, **({} if password is None else {"CONTENT_NESTING_PASSWORD": password})}
    completed = subprocess.run(  # noqa: S603  # Run this interpreter on a fixture written by the test.
        [sys.executable, "-c", CHILD, str(Path(__file__).parent), str(path), mode, *names],
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr[-400:]
    return dict(line.split() for line in completed.stdout.splitlines())


def _refusal(call: Callable[[], object]) -> pylopdf.LimitError:
    with pytest.raises(pylopdf.LimitError) as caught:
        call()
    return caught.value


@pytest.mark.parametrize("placement", PLACEMENTS)
def test_no_nesting_depth_crashes_any_operation(placement: str, tmp_path: Path) -> None:
    """Refuse content nested 300,000 deep wherever hayro interprets it.

    hayro 0.7 killed the interpreter with SIGSEGV here, whatever the operation.
    """
    path = tmp_path / f"{placement}.pdf"
    path.write_bytes(PLACEMENTS[placement](CRASH_DEPTH))

    assert _run_child(path) == dict.fromkeys(OPERATIONS, CODE)


SAVED_FORMS: dict[str, Callable[[pylopdf.Document], bytes]] = {
    "compressed": lambda doc: doc.tobytes(deflate=True),
    "object-streams": lambda doc: doc.tobytes(deflate=True, object_streams=True),
    "encrypted": lambda doc: doc.tobytes(deflate=True, user_pw="secret"),
}


@pytest.mark.parametrize("saved", SAVED_FORMS)
@pytest.mark.parametrize("placement", ["page", "form", "annotation"])
def test_saved_variants_are_refused_too(placement: str, saved: str, tmp_path: Path) -> None:
    """Flate-compressed streams, object streams, and decrypted documents reach the same check."""
    path = tmp_path / f"{placement}-{saved}.pdf"
    doc = pylopdf.Document(stream=PLACEMENTS[placement](CRASH_DEPTH))
    path.write_bytes(SAVED_FORMS[saved](doc))
    assert path.stat().st_size < 100_000
    password = "secret" if saved == "encrypted" else None

    outcomes = _run_child(path, ["get_page_text", "get_pixmap"], password=password)

    assert outcomes == {"get_page_text": CODE, "get_pixmap": CODE}


def test_the_web_profile_does_not_change_the_refusal(tmp_path: Path) -> None:
    path = tmp_path / "deep.pdf"
    path.write_bytes(PLACEMENTS["page"](CRASH_DEPTH))

    outcomes = _run_child(path, ["get_page_text", "get_pixmap"], "web")

    assert outcomes == {"get_page_text": CODE, "get_pixmap": CODE}


@pytest.mark.parametrize("placement", PLACEMENTS)
def test_every_operation_refuses_content_beyond_the_limit(placement: str) -> None:
    """Check each operation in process at the smallest refused depth, where nothing can crash."""
    data = PLACEMENTS[placement](LIMIT + 1)
    for operation in OPERATIONS.values():
        doc = pylopdf.Document(stream=data)
        error = _refusal(partial(operation, doc))
        assert error.code == CODE
        assert isinstance(error, pylopdf.PdfError)
        assert error.args[0] == CODE


@pytest.mark.parametrize("placement", PLACEMENTS)
def test_content_at_the_limit_is_interpreted(placement: str) -> None:
    doc = pylopdf.Document(stream=PLACEMENTS[placement](LIMIT))

    for operation in OPERATIONS.values():
        operation(doc)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(lambda depth: _show(_arrays(depth)), id="arrays"),
        pytest.param(lambda depth: f"/P {_dictionaries(depth)} BDC EMC {SHOW}", id="dictionaries"),
        pytest.param(
            lambda depth: f"/P {'[<< /A ' * (depth // 2)}1{' >>]' * (depth // 2)} BDC EMC {SHOW}", id="alternating"
        ),
    ],
)
def test_the_limit_is_exactly_64_levels(payload: Callable[[int], str]) -> None:
    accepted = pylopdf.Document(stream=page_content(payload(LIMIT)))
    assert accepted.get_page_text(0) is not None

    refused = pylopdf.Document(stream=page_content(payload(LIMIT + 2)))
    assert _refusal(lambda: refused.get_page_text(0)).code == CODE


def test_refusals_name_where_the_content_is() -> None:
    page = pylopdf.Document(stream=PLACEMENTS["page"](LIMIT + 1))
    assert "content of page 1 nests arrays or dictionaries deeper than the 64-level limit" in str(
        _refusal(lambda: page.get_page_text(0))
    )

    nested = pylopdf.Document(stream=PLACEMENTS["nested-form"](LIMIT + 1))
    assert "content stream 7 0 R reachable from page 1" in str(_refusal(lambda: nested.get_page_text(0)))


def test_many_shallow_containers_are_not_nesting() -> None:
    arrays = " ".join(["[ (a) 1 ] TJ"] * 5000)
    dictionaries = " ".join(["/P << /A [1 2] /B << /C 3 >> >> BDC EMC"] * 2000)

    doc = pylopdf.Document(stream=page_content(f"{arrays} {dictionaries} {SHOW}"))

    assert "PAGE TEXT" in doc.get_page_text(0)


def test_brackets_inside_strings_comments_names_and_hex_do_not_nest() -> None:
    hidden = "(" + "[" * 5000 + ")"
    comment = "% " + "[<<" * 2000 + "\n"
    hex_string = "<" + "41 " * 2000 + ">"
    name = "/A" + "x" * 3000
    parentheses = "(" * 100_000 + ")" * 100_000

    doc = pylopdf.Document(
        stream=page_content(f"{hidden} pop {comment} {hex_string} pop {name} pop {parentheses} pop {SHOW}")
    )

    assert "PAGE TEXT" in doc.get_page_text(0)


def test_nesting_adds_up_across_the_streams_of_one_page() -> None:
    """Containers opened in one `/Contents` stream close in the next, as hayro joins them."""
    split = pylopdf.Document(
        stream=page_content_array("BT /F1 20 Tf 20 100 Td " + "[" * 40, "[" * 40 + "(X)" + "]" * 80 + " TJ ET")
    )
    assert _refusal(lambda: split.get_page_text(0)).code == CODE

    shallow = pylopdf.Document(
        stream=page_content_array("BT /F1 20 Tf 20 100 Td " + "[" * 30, "[" * 30 + "(X)" + "]" * 60 + " TJ ET")
    )
    assert shallow.get_page_text(0) is not None


def _inline_image(data: bytes, tail: str = SHOW) -> bytes:
    return b"q 20 0 0 20 10 10 cm BI /W 2 /H 2 /BPC 8 /CS /G ID " + data + b" EI Q " + tail.encode()


def test_binary_inline_image_data_is_not_tokenized() -> None:
    """Image data may hold any bytes, including unbalanced brackets and stray ends."""
    data = b"[" * 500 + b"(" + b"<<" * 200 + b"\x00%\xff" + b"EI \xfe\xff" + b"BI /W 1 " + b"]" * 3
    doc = pylopdf.Document(stream=page_content(_inline_image(data)))

    assert "PAGE TEXT" in doc.get_page_text(0)
    assert doc[0].get_pixmap().width == 300
    assert isinstance(doc[0].get_images(), list)


def test_ascii_hex_inline_image_is_not_tokenized() -> None:
    """ASCII-encoded image data is as opaque as binary data."""
    content = f"q 20 0 0 20 10 10 cm BI /W 2 /H 2 /BPC 8 /CS /G /F /AHx ID 00 ff 00 ff> EI Q {SHOW}"
    doc = pylopdf.Document(stream=page_content(content))

    assert "PAGE TEXT" in doc.get_page_text(0)


def test_two_inline_images_in_one_stream() -> None:
    content = _inline_image(b"[[\x00(", "") + b" " + _inline_image(b"\xff<<\x01]")
    doc = pylopdf.Document(stream=page_content(content))

    assert "PAGE TEXT" in doc.get_page_text(0)


#: Streams where hayro resumes tokenizing somewhere an `EI` search would not expect.
INLINE_IMAGE_EVASIONS = {
    # No `EI` at all: hayro parses the "image data" as content.
    "no-end": lambda payload: b"BI /W 1 /H 1 ID x " + payload,
    # The only `EI` is followed by a regular byte, so hayro resumes behind it.
    "end-with-regular-byte": lambda payload: b"BI /W 1 /H 1 ID \x00 EITJ " + payload,
    # hayro rejects the first end and resumes behind the last, inside what looks like a string.
    "rejected-end": lambda payload: b"BI /W 1 /H 1 ID \xff EI Q ((EI% foo\n " + payload,
    # An unterminated string after the first end hides the second one.
    "string-after-end": lambda payload: b"BI /W 1 /H 1 ID \xff EI (  EI " + payload,
    # The second image has no end, so hayro tokenizes its data.
    "second-image-without-end": lambda payload: b"BI /W 1 /H 1 ID x EI Q BI /W 1 /H 1 ID " + payload,
    # `ID` as a value does not end the dictionary; the real one comes later.
    "id-as-value": lambda payload: b"BI /W ID /H 1 ID \xff EI Q " + payload,
    # A lone sign or dot is the number 0, so it completes the value and the next `ID` ends the dictionary.
    "sign-as-value": lambda payload: b"BI /W 1 /H 1 /K - ID \xff EI (  EI " + payload,
    "dot-as-value": lambda payload: b"BI /W 1 /H 1 /K . ID \xff EI (  EI " + payload,
    # While it decides whether an `EI` ends the data, hayro looks for the bytes `BI` anywhere behind it and
    # reads a dictionary there, even inside a string or a comment that the content itself would hide.
    "search-reads-dictionary-in-string": lambda payload: b"BI /W 1 /H 1 ID \xff EI (BI /A " + payload,
    "search-reads-dictionary-in-comment": lambda payload: b"BI /W 1 /H 1 ID \xff EI %BI /A " + payload,
    "search-reads-nested-dictionaries": lambda payload: (
        b"BI /W 1 /H 1 ID \xff EI (BI /A " + payload.replace(b"[", b"<<")
    ),
    "search-after-binary-and-comment": lambda payload: (
        b"BI ID (\x00\x80\x01\x02\r\x80EI\n(--BI /BI " + payload.replace(b"[", b"<<")
    ),
}


@pytest.mark.parametrize("evasion", INLINE_IMAGE_EVASIONS)
def test_inline_image_ends_cannot_hide_nesting(evasion: str, tmp_path: Path) -> None:
    refused = pylopdf.Document(stream=page_content(INLINE_IMAGE_EVASIONS[evasion](b"[" * (LIMIT + 6))))
    assert _refusal(lambda: refused.get_page_text(0)).code == CODE

    path = tmp_path / "evasion.pdf"
    path.write_bytes(page_content(INLINE_IMAGE_EVASIONS[evasion](b"[" * CRASH_DEPTH)))
    assert _run_child(path, ["get_page_text", "get_pixmap"]) == {"get_page_text": CODE, "get_pixmap": CODE}


def _after_keyword(prefix: str, payload: bytes) -> bytes:
    """Put `prefix` before `BI`. The unterminated string hides the payload from a lexer that misses the image."""
    return prefix.encode() + b"BI /W 1 /H 1 ID \xff EI (  EI " + payload


@pytest.mark.parametrize("prefix", ["-", ".", "-.", "+.", "--", ".-", "-+.", "+.+."])
def test_signs_and_dots_do_not_hide_the_inline_image_keyword(prefix: str, tmp_path: Path) -> None:
    """A sign or dot that no digit follows is the number 0 to hayro, and `BI` is the next token.

    A `+` splits only when a dot follows it.
    """
    refused = pylopdf.Document(stream=page_content(_after_keyword(prefix, b"[" * (LIMIT + 6))))
    assert _refusal(lambda: refused.get_page_text(0)).code == CODE

    path = tmp_path / "prefix.pdf"
    path.write_bytes(page_content(_after_keyword(prefix, b"[" * CRASH_DEPTH)))
    assert _run_child(path, ["get_page_text", "get_pixmap"]) == {"get_page_text": CODE, "get_pixmap": CODE}


@pytest.mark.parametrize("prefix", ["+", "+-", "-+", ".+", "5-", "a-", "x.", "5.", "/-", "1-2"])
def test_longer_tokens_are_not_the_inline_image_keyword(prefix: str, tmp_path: Path) -> None:
    """With a digit or letter before it the run is one token, so no image starts and the string hides the nesting."""
    path = tmp_path / "token.pdf"
    path.write_bytes(page_content(_after_keyword(prefix, b"[" * CRASH_DEPTH)))

    assert _run_child(path, ["get_page_text", "get_pixmap"]) == {"get_page_text": "ok", "get_pixmap": "ok"}


def test_the_image_keyword_in_ordinary_text_is_harmless() -> None:
    """Prose after an inline image holds many `BI` pairs, and hayro stops reading each within a byte or two."""
    prose = "(BIG BIKE BIRD BIOGRAPHY BI/BI/BI BIID) Tj " * 100
    content = _inline_image(b"\x00\x01\x02\x03", "") + b" BT /F1 20 Tf 20 100 Td " + prose.encode() + b" ET"
    doc = pylopdf.Document(stream=page_content(content))

    assert "BIOGRAPHY" in doc.get_page_text(0)
    assert doc[0].get_pixmap().width == 300


def test_stream_objects_inside_content_are_refused() -> None:
    """A dictionary followed by `stream` makes hayro skip an opaque body."""
    doc = pylopdf.Document(stream=page_content(f"<< /Length 3 >> stream\nabc\nendstream {SHOW}"))

    error = _refusal(lambda: doc.get_page_text(0))

    assert error.code == CODE
    assert "embeds a stream object" in str(error)


def test_refusal_is_per_page() -> None:
    doc = pylopdf.Document(stream=_document([_show(_arrays(LIMIT + 1)), SHOW]))

    assert doc.get_page_text(1).strip() == "PAGE TEXT"
    assert _refusal(lambda: doc.get_page_text(0)).code == CODE
    assert doc.get_page_text(1).strip() == "PAGE TEXT"
    assert doc.render_pages(pages=[1])
    assert _refusal(doc.render_pages).code == CODE
    assert _refusal(doc.get_text).code == CODE
    assert "PAGE TEXT" in doc.get_text(pages=[1])


def test_a_refused_stream_is_never_remembered_as_clear() -> None:
    """Two pages share a deep form: neither may borrow the other's check."""
    deep = _stream(_show(_arrays(LIMIT + 1)), FORM + "/Resources << /Font << /F1 3 0 R >> >> ")
    doc = pylopdf.Document(
        stream=_document([f"{SHOW} /Fm0 Do", f"{SHOW} /Fm0 Do"], [deep], "/XObject << /Fm0 {0} 0 R >> ")
    )

    for _ in range(2):
        assert _refusal(lambda: doc.get_page_text(0)).code == CODE
        assert _refusal(lambda: doc.get_page_text(1)).code == CODE


def test_shared_clear_streams_serve_every_page() -> None:
    shallow = _stream(_show(_arrays(3)), FORM + "/Resources << /Font << /F1 3 0 R >> >> ")
    doc = pylopdf.Document(stream=_document([f"{SHOW} /Fm0 Do"] * 3, [shallow], "/XObject << /Fm0 {0} 0 R >> "))

    assert all("PAGE TEXT" in doc.get_page_text(index) for index in range(3))
    assert doc.get_text().count("PAGE TEXT") == 3


def test_only_content_a_page_can_reach_is_checked() -> None:
    """A deep form that no resource dictionary lists is never decoded, so it cannot matter."""
    orphan = _stream(_show(_arrays(CRASH_DEPTH)), FORM)
    doc = pylopdf.Document(stream=_document([SHOW], [orphan]))

    assert "PAGE TEXT" in doc.get_page_text(0)


def test_image_data_is_not_content() -> None:
    """Only forms, patterns, glyph procedures, and appearances carry operators; image bytes are never scanned."""
    image = _stream(
        b"[" * CRASH_DEPTH,
        "/Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 ",
    )
    data = _document([f"q 50 0 0 50 10 10 cm /Im0 Do Q {SHOW}"], [image], "/XObject << /Im0 {0} 0 R >> ")
    doc = pylopdf.Document(stream=data)

    assert "PAGE TEXT" in doc.get_page_text(0)
    assert doc[0].get_pixmap().width == 300


def test_listing_a_deep_form_is_enough_to_refuse_the_page() -> None:
    """Names resolve through every enclosing resource dictionary, so unused entries count too."""
    deep = _stream(_show(_arrays(LIMIT + 1)), FORM + "/Resources << /Font << /F1 3 0 R >> >> ")
    doc = pylopdf.Document(stream=_document([SHOW], [deep], "/XObject << /Fm0 {0} 0 R >> "))

    assert _refusal(lambda: doc.get_page_text(0)).code == CODE


def test_every_appearance_state_is_checked() -> None:
    """Read every state of a check box, of which hayro draws one, without asking which."""
    drawn = pylopdf.Document(stream=appearance_state("0 0 1 rg 0 0 190 50 re f"))
    assert tuple(drawn[0].get_pixmap(clip=(20, 110, 40, 130)).samples[:3]) == (0, 0, 255)

    # `/AS` selects the shallow `/On`, the only state hayro draws, and `/Off` holds the deep content.
    doc = pylopdf.Document(stream=appearance_state(_show(_arrays(LIMIT + 1)), holder="/Off"))
    assert _refusal(lambda: doc.get_page_text(0)).code == CODE


def test_edits_drop_the_clearance_of_the_previous_snapshot() -> None:
    doc = pylopdf.Document(stream=_document([SHOW]))
    assert "PAGE TEXT" in doc.get_page_text(0)

    doc.insert_pdf(pylopdf.Document(stream=_document([_show(_arrays(LIMIT + 1))])))
    assert len(doc) == 2
    assert "PAGE TEXT" in doc.get_page_text(0)
    assert _refusal(lambda: doc.get_page_text(1)).code == CODE
    assert _refusal(lambda: doc.get_page_text(1)).code == CODE

    doc.delete_pages([1])
    assert "PAGE TEXT" in doc.get_page_text(0)
    assert doc.get_text().strip() == "PAGE TEXT"


def test_inserted_text_does_not_hide_deep_content() -> None:
    deep = _stream(_show(_arrays(LIMIT + 1)), FORM + "/Resources << /Font << /F1 3 0 R >> >> ")
    doc = pylopdf.Document(stream=_document([f"{SHOW} /Fm0 Do"], [deep], "/XObject << /Fm0 {0} 0 R >> "))
    assert _refusal(lambda: doc.get_page_text(0)).code == CODE

    doc[0].insert_text((20, 250), "ADDED")

    assert _refusal(lambda: doc.get_page_text(0)).code == CODE


def test_real_world_pages_pass_the_check() -> None:
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
