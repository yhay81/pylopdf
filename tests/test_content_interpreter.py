"""The redaction engine's content interpreter places glyphs where hayro does.

`_Document.content_glyphs` is a private hook: it interprets a page tokenized
as hayro tokenizes it and reports, per glyph, the display-space origin,
the character code, the advance in glyph space, the text rendering mode, the
code's location in its content stream, and whether the metrics were
approximate or the font a Helvetica fallback. Text extraction, which hayro
interprets, is the oracle for the positions.
"""

from __future__ import annotations

import collections
from pathlib import Path

import pytest
from conftest import build_raw_pdf

import pylopdf

REAL_WORLD = Path(__file__).parent / "assets" / "real_world"
TOLERANCE = 0.01


def _glyphs(doc: pylopdf.Document, pno: int = 0) -> list[tuple]:
    return doc._doc.content_glyphs(pno + 1)  # noqa: SLF001 - private content-engine hook.


def _origins(doc: pylopdf.Document, pno: int = 0) -> list[tuple[float, float]]:
    return [glyph[0] for glyph in _glyphs(doc, pno)]


def _hayro_origins(doc: pylopdf.Document, pno: int = 0) -> list[tuple[str, float, float]]:
    raw = doc.pages[pno].layout(chars=True, clipped=True).to_dict()
    return [
        (char["c"], char["origin"][0], char["origin"][1])
        for block in raw["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
        for char in span["chars"]
        if not char["synthetic"]
    ]


def _unmatched(doc: pylopdf.Document, pno: int = 0) -> list[tuple[str, float, float]]:
    """Return hayro's glyphs that the interpreter places nowhere near."""
    buckets: dict[tuple[int, int], list[tuple[float, float]]] = collections.defaultdict(list)
    for x, y in _origins(doc, pno):
        buckets[round(x), round(y)].append((x, y))
    missing = []
    for char, ox, oy in _hayro_origins(doc, pno):
        near = (
            point
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for point in buckets.get((round(ox) + dx, round(oy) + dy), ())
        )
        if not any(abs(x - ox) <= TOLERANCE and abs(y - oy) <= TOLERANCE for x, y in near):
            missing.append((char, ox, oy))
    return missing


def _page(content: str, resources: str = "<< /Font << /F1 4 0 R >> >>", *extra: str) -> bytes:
    """One 300x300 page with `content`, Helvetica as /F1, and extra objects from 6."""
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 300 300] >>",
        3: f"<< /Type /Page /Parent 2 0 R /Resources {resources} /Contents 5 0 R >>",
        4: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        5: f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
    }
    objects.update(enumerate(extra, start=6))
    return build_raw_pdf(objects)


def _stream(content: str, dictionary: str = "") -> str:
    return f"<< {dictionary} /Length {len(content)} >>\nstream\n{content}\nendstream"


@pytest.mark.parametrize("path", sorted(REAL_WORLD.glob("*.pdf")), ids=lambda path: path.name)
def test_every_glyph_hayro_extracts_has_an_interpreted_twin(path: Path) -> None:
    doc = pylopdf.open(path)
    for pno in range(len(doc.pages)):
        assert _unmatched(doc, pno) == [], f"page {pno}"
        glyphs = _glyphs(doc, pno)
        assert not any(glyph[5][0] for glyph in glyphs), "no approximate widths in the corpus"


def test_text_state_operators_move_the_pen_as_hayro_does() -> None:
    content = (
        "BT /F1 12 Tf 20 250 Td (AB) Tj"
        " 3 Tc (CD) Tj 0 Tc 5 Tw (E F) Tj 0 Tw"
        " 150 Tz (GH) Tj 100 Tz 4 Ts (IJ) Tj 0 Ts"
        " 14 TL T* (KL) Tj (MN) ' 2 1 (O P) \""
        " 0 -30 TD (QR) Tj T* (ST) Tj"
        " [(U) -500 (V) 250 (W)] TJ"
        " 1 0 0.5 1 30 100 Tm (XY) Tj"
        " ET"
    )
    doc = pylopdf.open(_page(content))

    assert _unmatched(doc) == []
    assert len(_hayro_origins(doc)) == len(_glyphs(doc)) == 27


def test_graphics_state_saves_the_text_state_and_cm_applies() -> None:
    content = (
        "q 2 0 0 2 10 10 cm BT /F1 10 Tf 5 100 Td (in) Tj ET Q"
        " BT /F1 10 Tf 5 100 Td q 3 Tc (sa) Tj Q (ved) Tj ET"
        " 0 1 -1 0 290 10 cm BT /F1 10 Tf 0 0 Td (rot) Tj ET"
    )
    doc = pylopdf.open(_page(content))

    assert _unmatched(doc) == []
    assert len(_glyphs(doc)) == 10


def test_glyph_sources_point_at_the_code_bytes() -> None:
    content = 'BT /F1 12 Tf 20 250 Td (ab) Tj [(c) -100 (de)] TJ 1 2 (f) " ET'
    doc = pylopdf.open(_page(content))

    sources = [glyph[4] for glyph in _glyphs(doc)]

    # Operations: BT 0, Tf 1, Td 2, Tj 3, TJ 4, " 5.
    assert sources == [
        (0, 3, 0, 0, 1),
        (0, 3, 0, 1, 1),
        (0, 4, 0, 0, 1),
        (0, 4, 2, 0, 1),
        (0, 4, 2, 1, 1),
        (0, 5, 2, 0, 1),
    ]
    assert [glyph[1] for glyph in _glyphs(doc)] == [ord(c) for c in "abcdef"]


def test_standard_fonts_without_widths_use_afm_widths() -> None:
    content = "BT /F1 10 Tf 10 100 Td (Wi) Tj /F2 10 Tf (Wi) Tj ET"
    resources = "<< /Font << /F1 4 0 R /F2 6 0 R >> >>"
    courier = "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"
    doc = pylopdf.open(_page(content, resources, courier))

    advances = [glyph[2][0] for glyph in _glyphs(doc)]

    assert advances == [944.0, 222.0, 600.0, 600.0]
    assert _unmatched(doc) == []


@pytest.mark.parametrize(
    ("encoding", "text"),
    [
        # Code 0 is `.notdef` (250 units), an unmapped WinAnsi code `bullet`,
        # and 0xA0 a space.
        ("/WinAnsiEncoding", r"A\000B\201C\240D"),
        # MacRoman's own `eacute`, then Mac OS Roman's `notequal`, which the
        # PDF table lacks.
        ("/MacRomanEncoding", r"A\216B\255C\000D"),
        ("/StandardEncoding", r"A\000B\047C\140D"),
    ],
)
def test_standard_fonts_name_codes_as_hayro_does(encoding: str, text: str) -> None:
    content = f"BT /F2 20 Tf 10 100 Td ({text}) Tj ET"
    resources = "<< /Font << /F2 6 0 R >> >>"
    font = f"<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman /Encoding {encoding} >>"
    doc = pylopdf.open(_page(content, resources, font))

    assert [char for char, _x, _y in _hayro_origins(doc) if char in "ABCD"] == list("ABCD")
    assert _unmatched(doc) == []


def test_symbol_fonts_ignore_their_base_encoding() -> None:
    content = "BT /F2 20 Tf 10 100 Td (ab\\000c) Tj /F1 20 Tf (X) Tj ET"
    resources = "<< /Font << /F1 4 0 R /F2 6 0 R >> >>"
    symbol = "<< /Type /Font /Subtype /Type1 /BaseFont /Symbol /Encoding /WinAnsiEncoding >>"
    doc = pylopdf.open(_page(content, resources, symbol))

    assert _unmatched(doc) == []
    assert "X" in [char for char, _x, _y in _hayro_origins(doc)]


def test_widths_arrays_and_missing_width_apply_to_simple_fonts() -> None:
    content = "BT /F2 10 Tf 10 100 Td (ABCD) Tj ET"
    resources = "<< /Font << /F2 6 0 R >> >>"
    font = (
        "<< /Type /Font /Subtype /TrueType /BaseFont /Custom /FirstChar 66 /LastChar 67"
        " /Widths [500 700] /FontDescriptor 7 0 R >>"
    )
    descriptor = "<< /Type /FontDescriptor /FontName /Custom /Flags 32 /MissingWidth 333 >>"
    doc = pylopdf.open(_page(content, resources, font, descriptor))

    assert [glyph[2][0] for glyph in _glyphs(doc)] == [333.0, 500.0, 700.0, 333.0]


def test_form_xobjects_nest_with_their_matrix_and_resources() -> None:
    inner = _stream("BT /F1 8 Tf 0 0 Td (in) Tj ET", "/Type /XObject /Subtype /Form /BBox [0 0 100 100]")
    outer = _stream(
        "BT /F1 8 Tf 0 20 Td (out) Tj ET /Inner Do",
        "/Type /XObject /Subtype /Form /BBox [0 0 100 100] /Matrix [1 0 0 1 5 5]"
        " /Resources << /XObject << /Inner 7 0 R >> /Font << /F1 4 0 R >> >>",
    )
    content = "q 1 0 0 1 50 50 cm /Outer Do Q q 2 0 0 2 100 150 cm /Outer Do Q"
    resources = "<< /Font << /F1 4 0 R >> /XObject << /Outer 6 0 R >> >>"
    doc = pylopdf.open(_page(content, resources, outer, inner))

    glyphs = _glyphs(doc)

    assert _unmatched(doc) == []
    assert len(glyphs) == 10
    # The inner form inherits the outer form's resources, and sources name
    # the form that holds each code.
    assert [glyph[4][0] for glyph in glyphs] == [6, 6, 6, 7, 7] * 2


def test_composite_fonts_read_codes_and_cid_widths() -> None:
    content = "BT /F2 20 Tf 10 200 Td <00010002000300ff> Tj ET"
    resources = "<< /Font << /F2 6 0 R >> >>"
    font = "<< /Type /Font /Subtype /Type0 /BaseFont /Custom /Encoding /Identity-H /DescendantFonts [7 0 R] >>"
    descendant = (
        "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Custom"
        " /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >>"
        " /DW 600 /W [1 [500 700] 3 255 900] >>"
    )
    doc = pylopdf.open(_page(content, resources, font, descendant))

    glyphs = _glyphs(doc)

    assert [glyph[1] for glyph in glyphs] == [1, 2, 3, 255]
    assert [glyph[2][0] for glyph in glyphs] == [500.0, 700.0, 900.0, 900.0]
    assert [glyph[4][3:] for glyph in glyphs] == [(0, 2), (2, 2), (4, 2), (6, 2)]
    xs = [glyph[0][0] for glyph in glyphs]
    assert xs == pytest.approx([10.0, 20.0, 34.0, 52.0])


def test_vertical_composite_fonts_advance_down_with_origin_displacement() -> None:
    content = "BT /F2 10 Tf 100 200 Td <00010002> Tj ET"
    resources = "<< /Font << /F2 6 0 R >> >>"
    font = "<< /Type /Font /Subtype /Type0 /BaseFont /Custom /Encoding /Identity-V /DescendantFonts [7 0 R] >>"
    descendant = (
        "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Custom"
        " /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >>"
        " /DW 1000 /W2 [2 [-800 400 900]] >>"
    )
    doc = pylopdf.open(_page(content, resources, font, descendant))

    glyphs = _glyphs(doc)

    # CID 1 takes /DW2's default [880 -1000] and half the horizontal width;
    # CID 2 its /W2 entry. Display y grows downward.
    assert [glyph[2] for glyph in glyphs] == [(0.0, -1000.0), (0.0, -800.0)]
    (x1, y1), (x2, y2) = (glyph[0] for glyph in glyphs)
    assert (x1, y1) == pytest.approx((95.0, 300 - 191.2))
    assert (x2, y2) == pytest.approx((96.0, 300 - 191.0 + 10.0))


def test_type3_fonts_scale_widths_by_the_font_matrix() -> None:
    content = "BT /F3 10 Tf 10 100 Td (ab) Tj ET"
    resources = "<< /Font << /F3 6 0 R >> >>"
    font = (
        "<< /Type /Font /Subtype /Type3 /FontBBox [0 0 100 100] /FontMatrix [0.01 0 0 0.01 0 0]"
        " /CharProcs << /a 7 0 R /b 7 0 R >> /Encoding << /Differences [97 /a /b] >>"
        " /FirstChar 97 /LastChar 98 /Widths [50 80] /Resources << >> >>"
    )
    procedure = _stream("50 0 d0 0 0 50 50 re f")
    doc = pylopdf.open(_page(content, resources, font, procedure))

    glyphs = _glyphs(doc)

    # hayro scales Type3 widths in single precision, so 80 × 0.01 is not
    # exactly 0.8.
    assert [glyph[2][0] for glyph in glyphs] == pytest.approx([500.0, 800.0], abs=1e-3)
    assert [glyph[0][0] for glyph in glyphs] == pytest.approx([10.0, 15.0])


def test_missing_fonts_fall_back_to_helvetica_like_hayro() -> None:
    content = "BT /Nope 10 Tf 10 100 Td (Hi) Tj ET BT 10 50 Td (No) Tj ET"
    doc = pylopdf.open(_page(content))

    glyphs = _glyphs(doc)

    assert [glyph[5] for glyph in glyphs] == [(False, True)] * 4
    assert [glyph[2][0] for glyph in glyphs] == [722.0, 222.0, 722.0, 556.0]
    assert _unmatched(doc) == []


def test_invisible_and_clipping_text_is_recorded_with_its_mode() -> None:
    content = "BT /F1 10 Tf 3 Tr 10 100 Td (ocr) Tj 7 Tr (clip) Tj 9 Tr (x) Tj ET"
    doc = pylopdf.open(_page(content))

    assert [glyph[3] for glyph in _glyphs(doc)] == [3, 3, 3, 7, 7, 7, 7, 0]


def test_forms_nest_to_hayros_depth_and_no_deeper() -> None:
    depth = 52
    forms = []
    for level in range(depth):
        number = 6 + level
        draw = f" /F{number + 1} Do" if level + 1 < depth else ""
        forms.append(
            _stream(
                f"BT /F1 5 Tf 0 0 Td (x) Tj ET{draw}",
                f"/Type /XObject /Subtype /Form /BBox [0 0 10 10]"
                f" /Resources << /Font << /F1 4 0 R >> /XObject << /F{number + 1} {number + 1} 0 R >> >>",
            )
        )
    resources = "<< /Font << /F1 4 0 R >> /XObject << /F6 6 0 R >> >>"
    doc = pylopdf.open(_page("/F6 Do", resources, *forms))

    assert len(_glyphs(doc)) == 50


def test_forms_that_draw_themselves_twice_hit_the_operation_bound() -> None:
    form = _stream(
        "q Q " * 1000 + "/Self Do /Self Do",
        "/Type /XObject /Subtype /Form /BBox [0 0 10 10] /Resources << /XObject << /Self 6 0 R >> >>",
    )
    doc = pylopdf.open(_page("/Self Do", "<< /XObject << /Self 6 0 R >> >>", form))

    with pytest.raises(pylopdf.LimitError) as error:
        _glyphs(doc)
    assert error.value.code == "content_operation_count"


def test_forms_that_draw_themselves_twice_hit_the_item_bound() -> None:
    # Every placement of a form is a container of its own.
    form = _stream(
        "/Self Do /Self Do",
        "/Type /XObject /Subtype /Form /BBox [0 0 10 10] /Resources << /XObject << /Self 6 0 R >> >>",
    )
    doc = pylopdf.open(_page("/Self Do", "<< /XObject << /Self 6 0 R >> >>", form))

    with pytest.raises(pylopdf.LimitError) as error:
        _glyphs(doc)
    assert error.value.code == "content_item_count"


def test_content_stops_where_hayro_stops_reading() -> None:
    # hayro cannot read the unterminated string and reads nothing after it.
    doc = pylopdf.open(_page("BT /F1 10 Tf 72 700 Td (A) Tj (unterminated Tj ET"))

    assert [glyph[1] for glyph in _glyphs(doc)] == [ord("A")]


def _items(doc: pylopdf.Document, pno: int = 0) -> tuple[list, list, list, list, list]:
    return doc._doc.content_items(pno + 1)  # noqa: SLF001 - private content-engine hook.


def _close(a: tuple[float, ...], b: tuple[float, ...], tolerance: float = TOLERANCE) -> bool:
    return all(abs(x - y) <= tolerance for x, y in zip(a, b, strict=True))


@pytest.mark.filterwarnings("ignore::pylopdf.PylopdfWarning")
@pytest.mark.parametrize("path", sorted(REAL_WORLD.glob("*.pdf")), ids=lambda path: path.name)
def test_every_path_and_image_hayro_paints_has_an_interpreted_twin(path: Path) -> None:
    doc = pylopdf.open(path)
    for pno in range(min(len(doc.pages), 8)):
        paths, images, _, _, containers = _items(doc, pno)
        # Rendering synthesizes appearances that the document does not hold.
        content = doc.pages[pno].content(text_runs=False, annotations=False)
        boxes = [path[3] for path in paths]
        for item in content.paths:
            # An `sh` is a path of the content model, and a shading here.
            if isinstance(item.fill, pylopdf.ShadingPattern) and item.stroke is None:
                continue
            assert any(_close(tuple(item.bbox), box, 0.05) for box in boxes), (pno, item.bbox)
        image_boxes = [image[3] for image in images]
        for item in content.images:
            assert any(_close(tuple(item.bbox), box, 0.05) for box in image_boxes), (pno, item.bbox)
        for number, parent, _ in containers[1:]:
            assert number > 0
            assert parent is None or parent[0] < len(containers)


def test_paths_name_the_operations_that_build_and_paint_them() -> None:
    content = "q 2 w 10 10 m 50 10 l S 0 0 1 rg 60 60 20 20 re f 100 100 m 120 100 l 110 130 l h B Q"
    doc = pylopdf.open(_page(content))
    paths, _, _, _, _ = _items(doc)

    assert [(path[1], path[2], path[5]) for path in paths] == [
        (2, 4, (False, True, False)),
        (6, 7, (True, False, False)),
        (8, 12, (True, True, False)),
    ]
    # Display space puts y downward on the 300-point page.
    assert _close(paths[0][3], (10, 290, 50, 290))
    assert _close(paths[1][3], (60, 220, 80, 240))
    # A stroke covers half its width beyond the geometry, diagonally for its caps.
    width = 2 / 2 * 2**0.5
    assert _close(paths[0][4], (10 - width, 290 - width, 50 + width, 290 + width))


def test_clipping_paths_narrow_what_shadings_and_images_paint() -> None:
    content = "q 50 50 100 100 re W n /Sh sh 10 0 0 10 60 60 cm /Im Do Q /Sh sh"
    resources = (
        "<< /Shading << /Sh << /ShadingType 2 /ColorSpace /DeviceGray /Coords [0 0 1 0]"
        " /Function << /FunctionType 2 /Domain [0 1] /C0 [0] /C1 [1] /N 1 >> >> >>"
        " /XObject << /Im 6 0 R >> >>"
    )
    image = _stream(
        "\x80", "/Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8"
    )
    doc = pylopdf.open(_page(content, resources, image))
    paths, images, shadings, _, _ = _items(doc)

    assert paths == []
    assert [(shading[1], shading[2]) for shading in shadings] == [(4, (50.0, 150.0, 150.0, 250.0)), (8, None)]
    assert [(image[1], image[2]) for image in images] == [(6, 6)]
    assert _close(images[0][3], (60, 230, 70, 240))
    assert images[0][4] == (50.0, 150.0, 150.0, 250.0)


def test_inline_images_and_marked_content_are_recorded() -> None:
    content = "/P << /MCID 0 >> BDC q 20 0 0 10 5 5 cm BI /W 1 /H 1 /BPC 8 /CS /G ID \x80 EI Q EMC /Artifact BMC"
    doc = pylopdf.open(_page(content))
    _, images, _, marked, _ = _items(doc)

    assert [(image[0], image[1], image[2]) for image in images] == [(0, 3, 0)]
    assert _close(images[0][3], (5, 285, 25, 295))
    assert marked == [(0, 0, 5), (0, 6, None)]


def test_each_placement_of_a_form_is_its_own_container() -> None:
    form = _stream(
        "0 0 10 10 re f BT /F1 5 Tf (A) Tj ET",
        "/Type /XObject /Subtype /Form /BBox [0 0 10 10] /Resources << /Font << /F1 4 0 R >> >>",
    )
    content = "q 1 0 0 1 20 20 cm /Fm Do Q q 1 0 0 1 100 20 cm /Fm Do Q"
    doc = pylopdf.open(_page(content, "<< /XObject << /Fm 6 0 R >> >>", form))
    paths, _, _, _, containers = _items(doc)

    assert containers == [(0, None, 0), (6, (0, 2), 0), (6, (0, 6), 0)]
    assert [path[0] for path in paths] == [6, 6]
    assert _close(paths[0][3], (20, 270, 30, 280))
    assert _close(paths[1][3], (100, 270, 110, 280))
    glyphs = _glyphs(doc)
    assert [glyph[4][0] for glyph in glyphs] == [6, 6]


def test_graphics_states_widen_strokes() -> None:
    content = "/Wide gs 10 10 m 50 10 l S"
    doc = pylopdf.open(_page(content, "<< /ExtGState << /Wide << /LW 10 >> >> >>"))
    paths, _, _, _, _ = _items(doc)

    half = 10 / 2 * 2**0.5
    assert _close(paths[0][4], (10 - half, 290 - half, 50 + half, 290 + half))


def test_glyph_compensation_restores_the_advance() -> None:
    # Helvetica's A is 667 units wide; Tc and Tw add 2 and 3 points at 10 points.
    content = "BT /F1 10 Tf 2 Tc 3 Tw 150 Tz 20 250 Td (A ) Tj 0 0 Td ET"
    doc = pylopdf.open(_page(content))
    glyphs = _glyphs(doc)

    assert [round(glyph[8], 3) for glyph in glyphs] == [-(667 + 200), -(278 + 500)]
    # A box spans the advance and the font's extent.
    x0, y0, x1, y1 = glyphs[0][7]
    assert _close((x0, x1), (20, 20 + 6.67 * 1.5))
    assert y0 < 50 - 7 < 50 < y1


def test_vertical_glyph_compensation() -> None:
    font = (
        "<< /Type /Font /Subtype /Type0 /BaseFont /V /Encoding /Identity-V /DescendantFonts"
        " [<< /Type /Font /Subtype /CIDFontType2 /BaseFont /V /CIDSystemInfo"
        " << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /DW 1000 >>] >>"
    )
    content = "BT /F1 10 Tf 100 250 Td <00010002> Tj ET"
    doc = pylopdf.open(_page(content, "<< /Font << /F1 6 0 R >> >>", font))
    glyphs = _glyphs(doc)

    # DW2 defaults to a -1000 vertical advance, which a TJ number of 1000 reverses.
    assert [glyph[8] for glyph in glyphs] == [1000.0, 1000.0]


def test_a_zero_font_size_has_no_compensation() -> None:
    doc = pylopdf.open(_page("BT /F1 0 Tf 20 250 Td (A) Tj ET"))

    assert [glyph[8] for glyph in _glyphs(doc)] == [None]
