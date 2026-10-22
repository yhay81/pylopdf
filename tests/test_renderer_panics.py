"""Renderer panics that pylopdf turns into `PdfError`.

hayro 0.8 panics when colour values reach a conversion with a different number of components than the colour space
that converts them (LaurenzV/hayro#1546): a Separation or DeviceN tint transform with the wrong number of outputs, or a
shading in a Pattern space. hayro 0.7 drew the same pages. A panic that reached PyO3 would raise `PanicException`,
which derives from `BaseException` and escapes `except Exception`, so every operation that lets hayro interpret a page
catches it and raises `PdfError` instead. When a hayro upgrade fixes these pages, the cases below render and the
assertions that expect an error must change.
"""

from __future__ import annotations

import pytest
from conftest import build_raw_pdf
from test_content_nesting import OPERATIONS, SHOW

import pylopdf

MESSAGE = "the renderer failed on malformed input"
ONE_OUTPUT = "<< /FunctionType 2 /Domain [0 1] /C0 [0] /C1 [1] /N 1 >>"
SEPARATION = f"[/Separation /Spot /DeviceRGB {ONE_OUTPUT}]"
DEVICEN = "[/DeviceN [/A /B] /DeviceRGB << /FunctionType 2 /Domain [0 1 0 1] /C0 [0] /C1 [1] /N 1 >>]"
FONT = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"


def _stream(data: str, entries: str = "") -> str:
    return f"<< {entries}/Length {len(data)} >>\nstream\n{data}\nendstream"


def _pages(*pages: tuple[str, str], extra: tuple[str, ...] = ()) -> bytes:
    """Build one page per `(resources, content)`; `extra` objects follow, numbered from `4 + 2 * len(pages)`."""
    kids = " ".join(f"{4 + 2 * index} 0 R" for index in range(len(pages)))
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>",
        3: FONT,
    }
    for index, (resources, content) in enumerate(pages):
        objects[4 + 2 * index] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
            f"/Resources << /Font << /F1 3 0 R >> {resources}>> /Contents {5 + 2 * index} 0 R >>"
        )
        objects[5 + 2 * index] = _stream(f"{SHOW} {content}")
    for offset, body in enumerate(extra):
        objects[4 + 2 * len(pages) + offset] = body
    return build_raw_pdf(objects)


def _shading(space: str) -> tuple[str, str]:
    return (
        f"/Shading << /Sh1 << /ShadingType 2 /ColorSpace {space} /Coords [0 0 100 0] /Function {ONE_OUTPUT} >> >> ",
        "/Sh1 sh",
    )


def _image_in_separation() -> bytes:
    image = _stream(
        "\x80", f"/Type /XObject /Subtype /Image /Width 1 /Height 1 /BitsPerComponent 8 /ColorSpace {SEPARATION} "
    )
    return _pages(("/XObject << /Im0 6 0 R >> ", "q 50 0 0 50 10 10 cm /Im0 Do Q"), extra=(image,))


#: The pages of LaurenzV/hayro#1546, each with ordinary text that extraction still reads.
CASES: dict[str, bytes] = {
    "separation-one-output": _pages((f"/ColorSpace << /Cs1 {SEPARATION} >> ", "/Cs1 cs 0.5 sc 0 0 50 50 re f")),
    "separation-three-or-four-outputs": _pages(
        ("/ColorSpace << /Cs1 [/Separation /Spot /DeviceRGB 6 0 R] >> ", "/Cs1 cs 0.75 sc 0 0 50 50 re f"),
        extra=(_stream("{ dup 0.5 gt { dup } if dup dup }", "/FunctionType 4 /Domain [0 1] /Range [0 1 0 1 0 1] "),),
    ),
    "devicen-one-output": _pages((f"/ColorSpace << /Cs1 {DEVICEN} >> ", "/Cs1 cs 0.5 0.5 sc 0 0 50 50 re f")),
    "image-in-separation": _image_in_separation(),
    "shading-in-pattern": _pages(_shading("/Pattern")),
    "shading-in-pattern-gray": _pages(_shading("[/Pattern /DeviceGray]")),
}


def _render_all(doc: pylopdf.Document) -> None:
    doc.render(alpha=True, workers=2)


@pytest.mark.parametrize("case", CASES)
def test_a_renderer_panic_raises_pdf_error(case: str) -> None:
    """No operation lets a panic through: each one completes or raises `PdfError`, and rendering raises."""
    errors: dict[str, pylopdf.PdfError] = {}
    for name, operation in OPERATIONS.items():
        doc = pylopdf.open(CASES[case])
        try:
            operation(doc)
        except pylopdf.PdfError as error:
            errors[name] = error
    assert {"get_pixmap", "render_page", "render_pages", "render_page_svg"} <= errors.keys()
    for failure in errors.values():
        assert not isinstance(failure, pylopdf.LimitError)
        assert MESSAGE in str(failure)

    doc = pylopdf.open(CASES[case])
    with pytest.raises(pylopdf.PdfError, match=MESSAGE) as caught:
        doc.pages[0].pixmap(alpha=True)
    assert "copy_from_slice" in str(caught.value)
    assert "PAGE TEXT" in doc.pages[0].text()


def test_the_document_stays_usable_after_a_panic() -> None:
    """A panic drops the snapshot that hayro may have left half-built, so later calls start over."""
    resources, content = _shading("/Pattern")
    doc = pylopdf.open(_pages((resources, content), ("", "0 0 1 rg 0 0 100 100 re f"), ("", "")))

    with pytest.raises(pylopdf.PdfError, match=MESSAGE):
        doc.pages[0].pixmap(alpha=True)
    assert doc.pages[1].pixmap(alpha=True).width == 300
    assert "PAGE TEXT" in doc.pages[0].text()
    with pytest.raises(pylopdf.PdfError, match=MESSAGE):
        doc.pages[0].pixmap(alpha=True).to_bytes()
    assert len(doc.render([1, 2], alpha=True, workers=2)) == 2

    # A worker thread of the parallel renderer panics as well.
    with pytest.raises(pylopdf.PdfError, match=MESSAGE):
        _render_all(doc)

    del doc.pages[0:1]
    assert len(doc.render(alpha=True, workers=2)) == 2


def test_a_contained_panic_leaves_edits_in_place() -> None:
    """Containment drops derived state only: the edited document is still the one that saves."""
    resources, content = _shading("/Pattern")
    doc = pylopdf.open(_pages((resources, content), ("", "")))
    doc.pages[1].insert_text((20, 250), "ADDED")

    with pytest.raises(pylopdf.PdfError, match=MESSAGE):
        doc.pages[0].pixmap(alpha=True)

    assert "ADDED" in doc.pages[1].text()
    assert "ADDED" in pylopdf.open(doc.to_bytes()).pages[1].text()
