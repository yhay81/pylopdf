"""The options that the text views share: clip, clipping, ligatures, whitespace, and more."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest
from conftest import build_raw_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"
FONT = Path(__file__).resolve().parent.parent / "fonts" / "pylopdf-fonts-he" / "src" / "pylopdf_fonts_he"


def _to_unicode(mappings: dict[int, str]) -> str:
    """Return a ToUnicode CMap that maps one-byte codes to text."""
    entries = "\n".join(f"<{code:02X}> <{text.encode('utf-16-be').hex().upper()}>" for code, text in mappings.items())
    return (
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
        "/CMapName /Test def 1 begincodespacerange <00> <FF> endcodespacerange "
        f"{len(mappings)} beginbfchar\n{entries}\nendbfchar endcmap "
        "CMapName currentdict /CMap defineresource pop end end"
    )


def build_text_pdf(stream: str, *, to_unicode: dict[int, str] | None = None) -> bytes:
    """Build a 612 by 792 page that draws ``stream`` with Helvetica as /F1."""
    font = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica"
    objects: dict[int, bytes | str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        4: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
    }
    if to_unicode is None:
        objects[5] = font + " >>"
    else:
        cmap = _to_unicode(to_unicode)
        objects[5] = font + " /ToUnicode 6 0 R >>"
        objects[6] = f"<< /Length {len(cmap)} >>\nstream\n{cmap}\nendstream"
    return build_raw_pdf(objects)


def test_clip_keeps_the_text_inside_a_rectangle_in_reading_order() -> None:
    page = pylopdf.open(
        build_text_pdf(
            "BT /F1 20 Tf 72 700 Td (Top left) Tj ET BT /F1 20 Tf 400 700 Td (Top right) Tj ET "
            "BT /F1 20 Tf 72 600 Td (Lower left) Tj ET"
        )
    ).pages[0]

    assert page.text(clip=(60, 70, 300, 200)) == "Top left\nLower left\n"
    assert [word.text for word in page.words(clip=(380, 70, 612, 120))] == ["Top", "right"]
    # A glyph counts as inside when the center of its box is.
    assert page.text(clip=(60, 70, 100, 120)) == "To\n"
    assert page.text(clip=(0, 0, 10, 10)) == ""
    assert page.search("left", clip=(60, 150, 300, 200)) == page.search("left")[1:]


def test_glyphs_that_the_page_or_the_clip_paths_hide_are_left_out_unless_asked_for() -> None:
    page = pylopdf.open(
        build_text_pdf(
            "BT /F1 20 Tf 72 700 Td (Seen) Tj ET "
            "BT /F1 20 Tf 700 650 Td (Off page) Tj ET "
            "q 0 0 10 10 re W n BT /F1 20 Tf 72 600 Td (Masked) Tj ET Q "
            "q 3 Tr BT /F1 20 Tf 72 500 Td (Invisible) Tj ET Q"
        )
    ).pages[0]

    assert page.text() == "Seen\nInvisible\n"
    assert page.text(clipped=True) == "Seen\nOff page\nMasked\nInvisible\n"
    spans = [span for block in page.layout(clipped=True).blocks for line in block.lines for span in line.spans]
    assert {span.text: span.is_clipped for span in spans} == {
        "Seen": False,
        "Off page": True,
        "Masked": True,
        "Invisible": False,
    }
    # Each variant has its own interpretation, and the default one is intact.
    assert page.text() == "Seen\nInvisible\n"


def test_ligatures_and_whitespace_are_kept_unless_asked_otherwise() -> None:
    page = pylopdf.open(
        build_text_pdf(
            "BT /F1 20 Tf 72 700 Td (AB) Tj ET",
            to_unicode={0x41: "ﬁ", 0x42: " x"},
        )
    ).pages[0]

    assert page.text() == "ﬁ x\n"
    assert page.text(ligatures=False) == "fi x\n"
    assert page.text(whitespace=False) == "ﬁ x\n"
    assert page.text(ligatures=False, whitespace=False) == "fi x\n"
    chars = page.layout(chars=True, ligatures=False).blocks[0].lines[0].spans[0].chars
    # An expanded ligature is still one glyph, with one box.
    assert [char.text for char in chars] == ["fi", " x"]
    assert page.search("fi x", ligatures=False, whitespace=False) != []
    assert page.search("fi x") == []


def test_dehyphenation_joins_a_word_that_a_line_end_splits() -> None:
    page = pylopdf.open(
        build_text_pdf(
            "BT /F1 12 Tf 14 TL 72 700 Td (A long exam-) Tj T* (ple follows) Tj T* (Jean-) Tj T* (Pierre stays) Tj ET"
        )
    ).pages[0]

    assert page.text() == "A long exam-\nple follows\nJean-\nPierre stays\n"
    assert page.text(dehyphenate=True) == "A long example follows\nJean-\nPierre stays\n"
    words = page.words(dehyphenate=True)
    assert [word.text for word in words] == ["A", "long", "example", "follows", "Jean-", "Pierre", "stays"]
    example = words[2]
    first, second = page.words()[2], page.words()[3]
    assert (example.block, example.line, example.index) == (first.block, first.line, first.index)

    lines = page.layout(dehyphenate=True).blocks[0].lines
    # The lines keep their glyphs; the joined word belongs to the first line.
    assert [line.text for line in lines] == ["A long example", "follows", "Jean-", "Pierre stays"]
    assert [span.text for line in lines for span in line.spans] == [
        "A long exam",
        "ple follows",
        "Jean-",
        "Pierre stays",
    ]
    # The word's box spans "exam", without its hyphen, and "ple" on the next line.
    exam = pylopdf.Rect(first.bbox.x0, first.bbox.y0, lines[0].bbox.x1, first.bbox.y1)
    assert example.bbox == exam | second.bbox
    assert len(page.search("example", dehyphenate=True)) == 1
    assert page.search("example") == []


def test_dehyphenation_needs_the_next_line_in_the_same_block() -> None:
    page = pylopdf.open(build_text_pdf("BT /F1 12 Tf 72 700 Td (A long exam-) Tj 0 -200 Td (ple apart) Tj ET")).pages[0]

    assert page.text(dehyphenate=True) == page.text()


def test_unmapped_glyphs_are_skipped_or_stand_for_a_placeholder() -> None:
    font = pylopdf.Font.from_file(FONT / "NotoSansHebrew-Variable.ttf")
    source = pylopdf.Document()
    page = source.pages.new()
    with page.draw() as canvas:
        canvas.text("Abc", (72, 100), font=font, size=20)
    document = pylopdf.open(source.to_bytes())
    for xref in document.objects:
        value = document.objects[xref]
        if isinstance(value, pylopdf.Dictionary) and "ToUnicode" in value:
            del value["ToUnicode"]
    page = document.pages[0]

    with pytest.warns(pylopdf.PylopdfWarning, match="no Unicode mapping"):
        assert page.text() == ""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert page.text(unmapped="replacement") == "���\n"
        glyph_ids = page.text(unmapped="glyph_id")
    assert len(glyph_ids) == 4
    assert all(0 < ord(char) < 0x10000 for char in glyph_ids[:3])


def test_type3_glyphs_without_unicode_have_no_glyph_id() -> None:
    page = pylopdf.open(ASSETS / "pdfium-type3.pdf").pages[0]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pylopdf.PylopdfWarning)
        assert page.text(unmapped="glyph_id") == page.text(unmapped="replacement")
    assert set(page.text(unmapped="replacement")) <= {"�", " ", "\n"}


def test_exact_boxes_come_from_the_glyph_outlines() -> None:
    page = pylopdf.open(build_text_pdf("BT /F1 20 Tf 72 700 Td (Ax) Tj ET")).pages[0]

    loose = page.layout(chars=True).blocks[0].lines[0].spans[0].chars
    exact = page.layout(chars=True, exact_bboxes=True).blocks[0].lines[0].spans[0].chars

    runs = page.content(paths=False, images=False).text_runs
    assert [char.bbox for char in exact] == [glyph.bbox for glyph in runs[0].glyphs]
    for tight, wide in zip(exact, loose, strict=True):
        assert tight.bbox.height < wide.bbox.height
        x0, y0, x1, y1 = wide.bbox
        assert tight.bbox in pylopdf.Rect(x0 - 0.5, y0, x1 + 0.5, y1)
    assert page.words(exact_bboxes=True)[0].bbox == exact[0].bbox | exact[1].bbox
    assert page.search("x", exact_bboxes=True) == [exact[1].bbox]
    # The reading order and the words do not depend on the box option.
    assert [word.text for word in page.words(exact_bboxes=True)] == [word.text for word in page.words()]


def test_synthetic_spaces_are_chars_that_join_to_the_span_text() -> None:
    page = pylopdf.open(build_text_pdf("BT /F1 20 Tf 72 700 Td (A) Tj 20 0 Td (gap) Tj ET")).pages[0]

    (span,) = page.layout(chars=True).blocks[0].lines[0].spans
    assert span.text == "A gap"
    assert "".join(char.text for char in span.chars) == span.text
    space = span.chars[1]
    assert space.is_synthetic
    assert [char.is_synthetic for char in span.chars] == [False, True, False, False, False]
    assert space.bbox.x0 == pytest.approx(span.chars[0].bbox.x1)
    assert space.bbox.x1 == pytest.approx(span.chars[2].bbox.x0)
    assert space.origin.x == pytest.approx(span.chars[0].bbox.x1)
    raw = page.layout(chars=True).to_dict()["blocks"][0]["lines"][0]["spans"][0]["chars"]
    assert [char["synthetic"] for char in raw] == [False, True, False, False, False]


def test_document_text_applies_the_options_to_every_page() -> None:
    data = build_text_pdf("BT /F1 20 Tf 72 700 Td (AB) Tj ET", to_unicode={0x41: "ﬁ", 0x42: "x"})
    document = pylopdf.open(data)
    document.pages.extend(pylopdf.open(data).pages)

    assert document.text(ligatures=False) == "fix\nfix\n"
    assert document.text(clip=(0, 0, 10, 10)) == ""


def test_text_option_variants_count_once_against_the_text_budget() -> None:
    data = build_text_pdf("BT /F1 20 Tf 72 700 Td (Seen) Tj ET BT /F1 20 Tf 700 650 Td (Off) Tj ET")
    document = pylopdf.open(data, limits=pylopdf.DocumentLimits(max_text_glyphs=7))
    page = document.pages[0]

    assert page.text() == "Seen\n"
    # The variant that keeps clipped glyphs grows the page's admission to 7.
    assert page.text(clipped=True) == "Seen\nOff\n"
    assert page.text() == "Seen\n"
    tight = pylopdf.open(data, limits=pylopdf.DocumentLimits(max_text_glyphs=6))
    assert tight.pages[0].text() == "Seen\n"
    with pytest.raises(pylopdf.LimitError) as error:
        tight.pages[0].text(clipped=True)
    assert error.value.code == "text_glyph_count"


@pytest.mark.parametrize(
    ("options", "error", "message"),
    [
        ({"unmapped": "cid"}, ValueError, "unmapped must be"),
        ({"clipped": 1}, TypeError, "clipped must be True or False"),
        ({"ligatures": None}, TypeError, "ligatures must be True or False"),
        ({"clip": (10, 10, 5, 20)}, ValueError, "clip must be a finite rect"),
    ],
)
def test_text_options_are_validated(options: dict[str, object], error: type[Exception], message: str) -> None:
    page = pylopdf.open(build_text_pdf("BT /F1 20 Tf 72 700 Td (A) Tj ET")).pages[0]

    for view in (page.text, page.words, page.layout):
        with pytest.raises(error, match=message):
            view(**options)  # ty: ignore[invalid-argument-type]
    with pytest.raises(error, match=message):
        page.search("A", **options)  # ty: ignore[no-matching-overload]
    with pytest.raises(TypeError, match="exact_bboxes must be True or False"):
        page.words(exact_bboxes="yes")  # ty: ignore[invalid-argument-type]


def test_options_survive_a_save_and_reopen() -> None:
    data = build_text_pdf("BT /F1 12 Tf 14 TL 72 700 Td (exam-) Tj T* (ple) Tj ET")
    document = pylopdf.open(data)

    reopened = pylopdf.open(document.to_bytes())

    assert reopened.pages[0].text(dehyphenate=True) == document.pages[0].text(dehyphenate=True) == "example\n"


def test_dehyphenated_text_fits_its_exact_size_preflight() -> None:
    data = build_text_pdf("BT /F1 12 Tf 14 TL 72 700 Td (A long exam-) Tj T* (ple follows) Tj ET")
    document = pylopdf.open(data, limits=pylopdf.DocumentLimits(max_text_size=64))

    assert document.pages[0].text(dehyphenate=True) == "A long example follows\n"
    assert document.text(dehyphenate=True, ligatures=False) == "A long example follows\n"
