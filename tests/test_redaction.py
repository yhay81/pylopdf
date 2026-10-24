"""Page.redact removes what areas cover, and hayro finds nothing there afterwards."""

from __future__ import annotations

import zlib
from pathlib import Path

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import Color, Rect, Redaction

REAL_WORLD = Path(__file__).parent / "assets" / "real_world"
HELVETICA = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"


def _stream(content: str | bytes, dictionary: str = "") -> bytes:
    data = content.encode("latin-1") if isinstance(content, str) else content
    return b"<< %s /Length %d >>\nstream\n%s\nendstream" % (dictionary.encode(), len(data), data)


def _page(
    content: str | bytes,
    resources: str = "<< /Font << /F1 4 0 R >> >>",
    *extra: str | bytes,
    page: str = "",
    pages: int = 1,
) -> bytes:
    """Pages of 300x300 points sharing `content`, Helvetica as /F1, extra objects from 6."""
    first_extra_page = 6 + len(extra)
    numbers = [3, *range(first_extra_page, first_extra_page + pages - 1)]
    kids = " ".join(f"{number} 0 R" for number in numbers)
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {pages} /MediaBox [0 0 300 300] >>",
        3: f"<< /Type /Page /Parent 2 0 R /Resources {resources} /Contents 5 0 R {page} >>",
        4: HELVETICA,
        5: _stream(content),
    }
    objects.update(enumerate(extra, start=6))
    for number in numbers[1:]:
        objects[number] = f"<< /Type /Page /Parent 2 0 R /Resources {resources} /Contents 5 0 R >>"
    return build_raw_pdf(objects)


def _word(page: pylopdf.Page, text: str) -> Rect:
    (word,) = [word for word in page.words() if word.text == text]
    return word.bbox


def _origins(page: pylopdf.Page) -> list[tuple[str, float, float]]:
    return [
        (char.text, round(char.origin.x, 3), round(char.origin.y, 3))
        for block in page.layout(chars=True).blocks
        for line in block.lines
        for span in line.spans
        for char in span.chars
        if not char.is_synthetic
    ]


def _assert_nothing_inside(page: pylopdf.Page, area: Rect) -> None:
    """Neither extraction nor the content model finds anything inside the area."""
    inner = Rect(area.x0 + 0.01, area.y0 + 0.01, area.x1 - 0.01, area.y1 - 0.01)
    assert _text_inside(page, inner) == ""
    for run in page.content().text_runs:
        for glyph in run.glyphs:
            if glyph.bbox is not None:
                assert not _meets(glyph.bbox, inner), glyph


def _text_inside(page: pylopdf.Page, area: Rect) -> str:
    return "".join(char.text for _, char in _chars(page) if _meets(char.bbox, area))


def _meets(a: Rect, b: Rect) -> bool:
    return a.x0 < b.x1 and a.x1 > b.x0 and a.y0 < b.y1 and a.y1 > b.y0


def test_text_in_an_area_goes_and_the_rest_stays_in_place() -> None:
    doc = pylopdf.open(_page("BT /F1 12 Tf 20 250 Td (Hello secret world) Tj 0 -20 Td (Second line) Tj ET"))
    page = doc.pages[0]
    before = _origins(page)
    area = _word(page, "secret")

    report = page.redact(area, fill=None)

    assert report.glyphs == 6
    assert report.areas == (area,)
    assert page.text().split() == ["Hello", "world", "Second", "line"]
    after = _origins(page)
    secret_start = "".join(entry[0] for entry in before).index("secret")
    assert after == before[:secret_start] + before[secret_start + 6 :]
    _assert_nothing_inside(page, area)
    reopened = pylopdf.open(doc.to_bytes())
    assert reopened.pages[0].text().split() == ["Hello", "world", "Second", "line"]


def test_kerned_tj_arrays_keep_their_adjustments() -> None:
    content = "BT /F1 12 Tf 20 250 Td [(Kee) -120 (p) 50 (Se) -300 (cret) 80 (Tail) 30 (End)] TJ ET"
    doc = pylopdf.open(_page(content))
    page = doc.pages[0]
    chars = [char for _, char in _chars(page)]
    assert "".join(char.text for char in chars) == "KeepSecretTailEnd"
    # The kerning pulls "T" into the box of the "t" before it.
    end = (chars[9].bbox.x0 + chars[9].bbox.x1) / 2
    area = Rect(chars[6].bbox.x0, chars[6].bbox.y0, end, chars[6].bbox.y1)
    before = [(char.text, round(char.origin.x, 3), round(char.origin.y, 3)) for char in chars]

    page.redact(area, fill=None)

    after = _origins(page)
    assert after == before[:6] + before[10:]


def _chars(page: pylopdf.Page) -> list[tuple[int, pylopdf.Char]]:
    chars = [
        char
        for block in page.layout(chars=True).blocks
        for line in block.lines
        for span in line.spans
        for char in span.chars
        if not char.is_synthetic
    ]
    return list(enumerate(chars))


def test_text_operators_keep_their_effects() -> None:
    content = "BT /F1 12 Tf 14 TL 20 250 Td (Line one) Tj (Two) ' 2 1 (Three gone) \" (Four) ' ET"
    doc = pylopdf.open(_page(content))
    page = doc.pages[0]
    before = _origins(page)
    page.redact([_word(page, "Two"), _word(page, "Three"), _word(page, "gone")], fill=None)

    assert page.text().split() == ["Line", "one", "Four"]
    # "Four" stays on its line, after T* and the spacing that \" set.
    assert _origins(page)[-4:] == before[-4:]


def test_a_shared_form_is_copied_for_the_placement_that_loses_text() -> None:
    form = _stream(
        "BT /F1 10 Tf 0 0 Td (Shared secret) Tj ET",
        "/Type /XObject /Subtype /Form /BBox [0 0 200 20] /Resources << /Font << /F1 4 0 R >> >>",
    )
    content = "q 1 0 0 1 20 250 cm /Fm Do Q q 1 0 0 1 20 100 cm /Fm Do Q"
    doc = pylopdf.open(_page(content, "<< /XObject << /Fm 6 0 R >> >>", form, pages=2))
    page = doc.pages[0]
    secrets = [word.bbox for word in page.words() if word.text == "secret"]
    assert len(secrets) == 2

    report = page.redact(secrets[0], fill=None)

    assert report.forms == 1
    assert page.text().split() == ["Shared", "Shared", "secret"]
    # The other page and the original form are untouched.
    assert doc.pages[1].text().split() == ["Shared", "secret", "Shared", "secret"]
    assert b"secret" in doc.objects[6].read()


def test_a_rotated_page_takes_display_coordinates() -> None:
    doc = pylopdf.open(_page("BT /F1 12 Tf 20 250 Td (Turned secret text) Tj ET", page="/Rotate 90"))
    page = doc.pages[0]
    area = _word(page, "secret")
    assert area.width < area.height

    page.redact(area)

    assert page.text().split() == ["Turned", "text"]
    _assert_nothing_inside(page, area)


def _cid_font(*, vertical: bool = False) -> str:
    encoding = "Identity-V" if vertical else "Identity-H"
    return (
        f"<< /Type /Font /Subtype /Type0 /BaseFont /CIDTest /Encoding /{encoding} /DescendantFonts"
        " [<< /Type /Font /Subtype /CIDFontType2 /BaseFont /CIDTest /CIDSystemInfo"
        " << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /DW 1000"
        " /FontDescriptor << /Type /FontDescriptor /FontName /CIDTest /Flags 4 /FontBBox [0 -120 1000 880]"
        " /ItalicAngle 0 /Ascent 880 /Descent -120 /CapHeight 700 /StemV 80 >> >>] /ToUnicode 7 0 R >>"
    )


def _to_unicode(count: int) -> bytes:
    lines = "".join(f"<{code:04X}> <{0x3041 + code:04X}>\n" for code in range(1, count + 1))
    cmap = (
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /T def\n"
        "1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
        f"{count} beginbfchar\n{lines}endbfchar endcmap CMapName currentdict /CMap defineresource pop end end"
    )
    return _stream(cmap)


@pytest.mark.parametrize("vertical", [False, True], ids=["horizontal", "vertical"])
def test_cid_fonts_lose_whole_two_byte_codes(vertical: bool) -> None:  # noqa: FBT001
    codes = "".join(f"{code:04X}" for code in range(1, 7))
    move = "150 280 Td" if vertical else "20 250 Td"
    doc = pylopdf.open(
        _page(
            f"BT /F1 20 Tf {move} <{codes}> Tj ET",
            "<< /Font << /F1 6 0 R >> >>",
            _cid_font(vertical=vertical),
            _to_unicode(6),
        )
    )
    page = doc.pages[0]
    chars = [char for _, char in _chars(page)]
    assert "".join(char.text for char in chars) == "あぃいぅうぇ"
    before = [(char.text, round(char.origin.x, 3), round(char.origin.y, 3)) for char in chars]
    # Extraction's boxes of vertical glyphs sit 1.6 points off their ink.
    joined = chars[2].bbox | chars[3].bbox
    area = Rect(joined.x0 + 3, joined.y0 + 3, joined.x1 - 3, joined.y1 - 3)

    page.redact(area, fill=None)

    after = [(char.text, round(char.origin.x, 3), round(char.origin.y, 3)) for _, char in _chars(page)]
    assert after == [before[0], before[1], before[4], before[5]]


def test_type3_glyphs_are_removed() -> None:
    font = (
        "<< /Type /Font /Subtype /Type3 /FontBBox [0 0 1000 1000] /FontMatrix [0.001 0 0 0.001 0 0]"
        " /CharProcs << /a 8 0 R >> /Encoding << /Type /Encoding /Differences [97 /a] >>"
        " /FirstChar 97 /LastChar 97 /Widths [1000] /ToUnicode 7 0 R >>"
    )
    to_unicode = _stream(
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /T def\n"
        "1 begincodespacerange <00> <FF> endcodespacerange\n1 beginbfchar\n<61> <0041>\nendbfchar"
        " endcmap CMapName currentdict /CMap defineresource pop end end"
    )
    glyph = _stream("1000 0 0 0 1000 1000 d1 0 0 1000 1000 re f")
    doc = pylopdf.open(
        _page("BT /F1 20 Tf 20 250 Td (aaaa) Tj ET", "<< /Font << /F1 6 0 R >> >>", font, to_unicode, glyph)
    )
    page = doc.pages[0]
    chars = [char for _, char in _chars(page)]
    assert len(chars) == 4
    second = chars[1].origin

    report = page.redact(Rect(second.x + 2, 30, second.x + 18, 60), fill=None)

    assert report.glyphs == 1
    assert [round(char.origin.x, 3) for _, char in _chars(page)] == [
        round(chars[index].origin.x, 3) for index in (0, 2, 3)
    ]


def test_invisible_ocr_text_is_removed() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 300))
    page.apply_ocr(
        words=[
            pylopdf.OcrWord(text="hidden", bbox=Rect(20, 20, 80, 40), confidence=1.0),
            pylopdf.OcrWord(text="kept", bbox=Rect(100, 20, 150, 40), confidence=1.0),
        ]
    )
    assert page.text().split() == ["hidden", "kept"]

    page.redact(Rect(15, 15, 85, 45))

    assert page.text().split() == ["kept"]


def test_marked_content_replacement_text_goes_with_its_glyphs() -> None:
    content = "/Span << /ActualText (secret) /Lang (en) >> BDC BT /F1 12 Tf 20 250 Td (secret) Tj ET EMC"
    doc = pylopdf.open(_page(content))
    page = doc.pages[0]

    page.redact(_word(page, "secret"))

    data = page.operations.to_bytes()
    assert b"ActualText" not in data
    assert b"/Lang" in data


def test_paths_touched_or_covered() -> None:
    content = "0 0 1 rg 20 20 50 50 re f 100 20 50 50 re f 1 0 0 RG 20 200 m 280 200 l S"
    doc = pylopdf.open(_page(content))
    area = Rect(10, 220, 90, 290)  # covers the first square in display space
    report = doc.pages[0].redact(area, graphics="covered", fill=None)
    assert report.paths == 1
    assert len(doc.pages[0].content().paths) == 2

    doc = pylopdf.open(_page(content))
    report = doc.pages[0].redact(Rect(10, 90, 30, 110), graphics="touched", fill=None)
    assert report.paths == 1
    assert len(doc.pages[0].content().paths) == 2

    doc = pylopdf.open(_page(content))
    report = doc.pages[0].redact(area, graphics="keep", fill=None)
    assert report.paths == 0
    assert len(doc.pages[0].content().paths) == 3


def test_a_clipping_path_keeps_clipping() -> None:
    content = "q 20 20 100 100 re W f 0 1 0 rg 0 0 300 300 re f Q"
    doc = pylopdf.open(_page(content))
    page = doc.pages[0]
    page.redact(Rect(25, 185, 30, 190), fill=None)

    # The green page fill went too, since it touches the area; the clip
    # stays and no longer paints.
    assert page.content().paths == ()
    assert b"W n" in page.operations.to_bytes().replace(b"\n", b" ")


def _image(width: int = 4, height: int = 4) -> bytes:
    pixels = bytes([255, 0, 0] * (width * height))
    return _stream(
        zlib.compress(pixels),
        f"/Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceRGB"
        " /BitsPerComponent 8 /Filter /FlateDecode",
    )


def test_image_pixels_inside_an_area_are_blanked() -> None:
    content = "q 100 0 0 100 50 150 cm /Im Do Q"
    doc = pylopdf.open(_page(content, "<< /XObject << /Im 6 0 R >> >>", _image(4, 4), pages=2))
    page = doc.pages[0]
    # The left half of the image in display space.
    report = page.redact(Rect(50, 50, 100, 150), fill=None)

    assert report.blanked_images == 1
    (image,) = page.content().images
    assert image.xref != 6
    pixmap = doc.images[image.xref].to_pixmap()
    samples = bytes(pixmap)
    row = samples[: 4 * 3]
    assert row == bytes([0, 0, 0] * 2 + [255, 0, 0] * 2)
    # The other page keeps the original image.
    assert doc.pages[1].content().images[0].xref == 6


def test_images_can_be_removed() -> None:
    content = "q 100 0 0 100 50 150 cm /Im Do Q q 10 0 0 10 200 200 cm BI /W 1 /H 1 /BPC 8 /CS /G ID \x80 EI Q"
    doc = pylopdf.open(_page(content, "<< /XObject << /Im 6 0 R >> >>", _image()))
    page = doc.pages[0]

    report = page.redact([Rect(60, 60, 70, 70), Rect(200, 90, 205, 95)], images="remove", fill=None)

    assert report.images == 2
    assert page.content().images == ()


def test_inline_image_pixels_are_blanked() -> None:
    content = "q 20 0 0 10 200 200 cm BI /W 2 /H 1 /BPC 8 /CS /G ID \xff\xff EI Q"
    doc = pylopdf.open(_page(content))
    page = doc.pages[0]

    report = page.redact(Rect(200, 90, 210, 100), fill=None)

    assert report.blanked_images == 1
    (image,) = page.content().images
    assert bytes(doc.images[image.xref].to_pixmap())[:6] == bytes([0, 0, 0, 255, 255, 255])


def test_soft_masks_are_merged_into_the_blanked_copy() -> None:
    mask = _stream(
        bytes([0, 255, 255, 0]),
        "/Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceGray /BitsPerComponent 8",
    )
    image = _stream(
        bytes([0, 0, 255] * 4),
        "/Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceRGB /BitsPerComponent 8 /SMask 7 0 R",
    )
    doc = pylopdf.open(_page("q 100 0 0 100 100 100 cm /Im Do Q", "<< /XObject << /Im 6 0 R >> >>", image, mask))
    page = doc.pages[0]

    page.redact(Rect(100, 100, 150, 150), fill=None)

    (placed,) = page.content().images
    pixmap = doc.images[placed.xref].to_pixmap(alpha=True)
    samples = bytes(pixmap)
    # The top-left pixel is blanked and opaque; the others keep their alpha.
    assert samples[0:4] == bytes([0, 0, 0, 255])
    assert samples[4:8] == bytes([0, 0, 255, 255])
    assert samples[15] == 0


def test_overlapping_annotations_and_widgets_go() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 300))
    page.annotations.add(pylopdf.Square(Rect(20, 20, 60, 60)))
    page.annotations.add(pylopdf.Square(Rect(200, 200, 260, 260)))
    page.links.add(Rect(20, 100, 80, 120), "https://example.test/secret")
    page.widgets.add(pylopdf.TextField("ssn", Rect(20, 150, 120, 170), value="123-45-6789"))

    report = page.redact([Rect(10, 10, 130, 175)])

    assert report.annotations == 3
    assert len(page.annotations) == 1
    assert len(page.links) == 0
    assert len(page.widgets) == 0
    assert "ssn" not in doc.form


def test_redaction_annotations_become_fills_with_overlay_text() -> None:
    doc = pylopdf.open(_page("BT /F1 12 Tf 20 250 Td (Hello secret world) Tj ET"))
    page = doc.pages[0]
    area = _word(page, "secret")
    page.annotations.add(Redaction(area, fill=Color(0, 0, 1), overlay_text="X"))

    report = page.redact()

    # The annotation stores its rectangle in single precision.
    assert len(report.areas) == 1
    assert tuple(report.areas[0]) == pytest.approx(tuple(area), abs=1e-3)
    assert report.glyphs == 6
    assert len(page.annotations) == 0
    assert sorted(page.text().split()) == ["Hello", "X", "world"]
    fills = [path for path in page.content().paths if path.fill == Color(0, 0, 1)]
    assert len(fills) == 1
    assert tuple(fills[0].bbox) == pytest.approx(tuple(area), abs=0.01)


def test_a_page_without_redaction_annotations_is_unchanged() -> None:
    data = _page("BT /F1 12 Tf 20 250 Td (Hello) Tj ET")
    doc = pylopdf.open(data)
    report = doc.pages[0].redact()

    assert report.areas == ()
    assert report.glyphs == 0
    assert doc.pages[0].text().split() == ["Hello"]


def test_content_after_what_hayro_reads_is_dropped() -> None:
    content = (
        "BT /F1 12 Tf 20 250 Td (Visible secret) Tj ET 1 2 3 4 5 6 7 8 9 10 11 sc BT /F1 12 Tf 20 200 Td (Hidden) Tj ET"
    )
    doc = pylopdf.open(_page(content))
    page = doc.pages[0]
    page.redact(_word(page, "secret"))

    assert b"Hidden" not in page.operations.to_bytes()
    assert page.operations.tail.strip() == b""


def test_a_refused_redaction_leaves_the_document_unchanged() -> None:
    # A zero font size with character spacing moves the pen, which no TJ
    # adjustment can reproduce.
    content = "BT /F1 0 Tf 5 Tc 20 250 Td (secret) Tj ET BT /F1 12 Tf 20 200 Td (Other) Tj ET"
    data = _page(content)
    doc = pylopdf.open(data)
    page = doc.pages[0]
    before = page.operations.to_bytes()

    with pytest.raises(pylopdf.PdfError, match="font size 0"):
        page.redact(Rect(0, 0, 300, 300))

    assert page.operations.to_bytes() == before
    assert doc.to_bytes() == pylopdf.open(data).to_bytes()


@pytest.mark.parametrize(
    ("options", "error"),
    [
        ({"text": "erase"}, ValueError),
        ({"images": "blur"}, ValueError),
        ({"graphics": "all"}, ValueError),
        ({"fill": (0, 0, 0)}, TypeError),
    ],
)
def test_options_are_checked(options: dict[str, object], error: type[Exception]) -> None:
    doc = pylopdf.open(_page("BT /F1 12 Tf 20 250 Td (Hello) Tj ET"))
    with pytest.raises(error):
        doc.pages[0].redact(Rect(0, 0, 10, 10), **options)  # ty: ignore[invalid-argument-type]


def test_too_many_areas_are_refused() -> None:
    doc = pylopdf.open(_page("BT /F1 12 Tf 20 250 Td (Hello) Tj ET"))
    with pytest.raises(ValueError, match="4096"):
        doc.pages[0].redact([Rect(0, 0, 1, 1)] * 4097)


@pytest.mark.filterwarnings("ignore::pylopdf.PylopdfWarning")
@pytest.mark.parametrize("path", sorted(REAL_WORLD.glob("*.pdf")), ids=lambda path: path.name)
def test_redacting_the_middle_of_every_corpus_page_leaves_nothing_there(path: Path) -> None:
    doc = pylopdf.open(path)
    for pno in range(min(len(doc.pages), 2)):
        page = doc.pages[pno]
        rect = page.rect
        area = Rect(
            rect.x0 + rect.width * 0.25,
            rect.y0 + rect.height * 0.25,
            rect.x0 + rect.width * 0.75,
            rect.y0 + rect.height * 0.5,
        )
        outside = Rect(rect.x0, rect.y0 + rect.height * 0.8, rect.x1, rect.y1)
        kept = _text_inside(page, outside)
        page.redact(area)
        _assert_nothing_inside(page, area)
        assert _text_inside(page, outside) == kept
    pylopdf.open(doc.to_bytes())


def test_a_redacted_document_cannot_be_saved_incrementally() -> None:
    doc = pylopdf.open(_page("BT /F1 12 Tf 20 250 Td (Hello secret world) Tj ET"))
    assert doc.can_save_incrementally
    page = doc.pages[0]
    page.redact(_word(page, "secret"))

    # The original bytes still hold the removed text.
    assert not doc.can_save_incrementally
    with pytest.raises(pylopdf.PdfError):
        doc.to_bytes(incremental=True)
    saved = pylopdf.open(doc.to_bytes())
    streams = [saved.objects[xref] for xref in saved.objects]
    assert not any(b"secret" in stream.read() for stream in streams if isinstance(stream, pylopdf.Stream))


def test_a_redaction_with_nothing_to_apply_keeps_incremental_saves() -> None:
    doc = pylopdf.open(_page("BT /F1 12 Tf 20 250 Td (Hello) Tj ET"))
    doc.pages[0].redact()

    assert doc.can_save_incrementally


def test_a_noop_redaction_keeps_direct_annotations_and_clean_state() -> None:
    doc = pylopdf.open(
        _page(
            "BT /F1 12 Tf 20 250 Td (Hello) Tj ET",
            page="/Annots [<< /Type /Annot /Subtype /Square /Rect [10 10 30 30] >>]",
        )
    )
    page = doc.pages[0]
    assert page.annotations[0].xref == 0

    report = page.redact()

    assert report.areas == ()
    assert not doc.is_dirty
    assert page.annotations[0].xref == 0
    assert doc.can_save_incrementally
