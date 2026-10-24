"""Subsetting the embedded fonts of existing documents."""

from __future__ import annotations

from pathlib import Path

import pytest

import pylopdf

REAL_WORLD = Path(__file__).parent / "assets" / "real_world"
# In mhlw-doc.pdf one full YuMincho program (object 152, 733,908 bytes)
# serves a Type0 font (object 5, `/F1`) and a simple TrueType font (object 12,
# `/F2`).
TYPE0_FONT = 5
SIMPLE_FONT = 12
PROGRAM = 152


def _mhlw() -> pylopdf.Document:
    return pylopdf.open(REAL_WORLD / "mhlw-doc.pdf")


def _exclusive_program() -> pylopdf.Document:
    """Give the simple font a copy of the program, so the Type0 font has it alone."""
    doc = _mhlw()
    data = doc.objects[PROGRAM].read()
    copy = doc.objects.add_stream(data, {"Length1": len(data)})
    doc.objects[SIMPLE_FONT]["FontDescriptor"]["FontFile2"] = doc.objects[copy]
    return pylopdf.open(doc.to_bytes())


def _signature(doc: pylopdf.Document) -> list[tuple[str, bytes]]:
    return [(page.text(), bytes(page.pixmap(dpi=72))) for page in doc.pages]


def _add_text_stream(doc: pylopdf.Document, content: bytes) -> None:
    """Append a content stream to the first page."""
    page = doc.pages[0].obj
    added = doc.objects.add_stream(content, filter=None)
    page["Contents"] = [page["Contents"], doc.objects[added]]


@pytest.mark.filterwarnings("ignore::pylopdf.PylopdfWarning")
def test_subsetting_keeps_text_and_rendering_and_shrinks_the_file() -> None:
    doc = _exclusive_program()
    expected = _signature(doc)
    before = len(doc.to_bytes())

    result = doc.subset_fonts()

    # The Type0 font's program and the simple font's copy.
    assert result == pylopdf.FontSubsetResult(
        considered=2,
        subset=2,
        skipped=0,
        bytes_before=2 * 733_908,
        bytes_after=result.bytes_after,
        bytes_saved=2 * 733_908 - result.bytes_after,
    )
    assert result.bytes_after < 2 * 733_908 // 5
    assert _signature(doc) == expected
    saved = doc.to_bytes()
    assert len(saved) < before * 2 // 3
    with pylopdf.open(saved) as reopened:
        assert _signature(reopened) == expected
        cid_font = reopened.objects[TYPE0_FONT]["DescendantFonts"][0]
        assert isinstance(cid_font["CIDToGIDMap"], pylopdf.Stream)


@pytest.mark.filterwarnings("ignore::pylopdf.PylopdfWarning")
def test_subsetting_a_subset_again_changes_nothing() -> None:
    doc = _exclusive_program()
    doc.subset_fonts()
    saved = doc.to_bytes()

    again = pylopdf.open(saved).subset_fonts()

    assert again.subset == 0
    assert again.skipped == 2


@pytest.mark.filterwarnings("ignore::pylopdf.PylopdfWarning")
def test_a_program_shared_by_a_cid_font_and_a_simple_font_is_subset() -> None:
    doc = _mhlw()
    expected = _signature(doc)
    before = len(doc.to_bytes())

    result = doc.subset_fonts()

    assert (result.considered, result.subset, result.skipped) == (1, 1, 0)
    assert result.bytes_after < 733_908 // 5
    saved = doc.to_bytes()
    assert len(saved) < before // 3
    with pylopdf.open(saved) as reopened:
        assert _signature(reopened) == expected


@pytest.mark.parametrize("name", ["senate-expenditures.pdf", "nics-background-checks-2015-11.pdf"])
def test_simple_truetype_fonts_keep_their_codes(name: str) -> None:
    doc = pylopdf.open(REAL_WORLD / name)
    expected = _signature(doc)

    result = doc.subset_fonts()

    assert result.subset >= 1
    assert result.bytes_saved > 0
    with pylopdf.open(doc.to_bytes()) as reopened:
        assert _signature(reopened) == expected


@pytest.mark.filterwarnings("ignore::pylopdf.PylopdfWarning")
def test_a_simple_font_held_directly_keeps_its_program_whole() -> None:
    doc = _mhlw()
    resources = doc.pages[0].obj["Resources"]["Font"]
    # Copy the simple font into the resource dictionary, so no object holds it.
    direct = dict(doc.objects[SIMPLE_FONT].items())
    resources["F2"] = direct
    doc = pylopdf.open(doc.to_bytes())

    assert doc.subset_fonts().subset == 0


@pytest.mark.filterwarnings("ignore::pylopdf.PylopdfWarning")
def test_a_font_in_the_form_default_resources_is_left_alone() -> None:
    doc = _exclusive_program()
    font = doc.objects[TYPE0_FONT]
    doc.catalog["AcroForm"] = {"Fields": [], "DR": {"Font": {"F1": font}}}
    before = doc.objects[PROGRAM].read()

    # Only the simple font's copy of the program is subset.
    assert doc.subset_fonts().subset == 1

    assert doc.objects[PROGRAM].read() == before


@pytest.mark.filterwarnings("ignore::pylopdf.PylopdfWarning")
def test_a_form_draws_with_the_font_it_inherits() -> None:
    doc = _exclusive_program()
    # CID 0x1000 is a glyph that no page draws.
    form = doc.objects.add_stream(
        b"BT 72 500 Td <1000> Tj ET",
        {"Type": pylopdf.Name("XObject"), "Subtype": pylopdf.Name("Form"), "BBox": [0, 0, 600, 800]},
        filter=None,
    )
    resources = doc.pages[0].obj["Resources"]
    resources["XObject"] = {"Inherits": doc.objects[form]}
    _add_text_stream(doc, b"q /F1 40 Tf /Inherits Do Q")
    doc = pylopdf.open(doc.to_bytes())
    expected = _signature(doc)

    assert doc.subset_fonts().subset == 2

    assert _signature(doc) == expected


@pytest.mark.filterwarnings("ignore::pylopdf.PylopdfWarning")
def test_a_stream_that_does_not_decode_keeps_its_fonts_whole() -> None:
    doc = _exclusive_program()
    # pylopdf cannot decode a content stream behind an image filter.
    broken = doc.objects.add_stream(
        b"BT <1000> Tj ET",
        {"Type": pylopdf.Name("XObject"), "Subtype": pylopdf.Name("Form"), "BBox": [0, 0, 10, 10]},
        filter=None,
    )
    doc.objects[broken]["Filter"] = pylopdf.Name("JBIG2Decode")
    doc.pages[0].obj["Resources"]["XObject"] = {"Broken": doc.objects[broken]}
    _add_text_stream(doc, b"q /F1 12 Tf /Broken Do Q")
    before = doc.objects[PROGRAM].read()

    doc.subset_fonts()

    assert doc.objects[PROGRAM].read() == before


@pytest.mark.filterwarnings("ignore::pylopdf.PylopdfWarning")
def test_an_explicit_cid_to_gid_map_is_rewritten() -> None:
    doc = _exclusive_program()
    cid_font = doc.objects[TYPE0_FONT]["DescendantFonts"][0]
    identity = b"".join(cid.to_bytes(2, "big") for cid in range(20_000))
    cid_font["CIDToGIDMap"] = doc.objects[doc.objects.add_stream(identity)]
    doc = pylopdf.open(doc.to_bytes())
    expected = _signature(doc)

    assert doc.subset_fonts().subset == 2

    assert _signature(doc) == expected
    rewritten = doc.objects[TYPE0_FONT]["DescendantFonts"][0]["CIDToGIDMap"].read()
    assert len(rewritten) < len(identity)


@pytest.mark.filterwarnings("ignore::pylopdf.PylopdfWarning")
def test_subsetting_is_saved_incrementally() -> None:
    original = _exclusive_program().to_bytes()
    doc = pylopdf.open(original)
    expected = _signature(doc)

    doc.subset_fonts()
    saved = doc.to_bytes(incremental=True)

    assert saved.startswith(original)
    with pylopdf.open(saved) as reopened:
        assert _signature(reopened) == expected


def test_a_document_without_embedded_cid_fonts_considers_nothing() -> None:
    doc = pylopdf.open(REAL_WORLD / "usrguide.pdf")

    assert doc.subset_fonts() == pylopdf.FontSubsetResult(
        considered=0, subset=0, skipped=0, bytes_before=0, bytes_after=0, bytes_saved=0
    )


def test_pikepdf_reads_a_subset_document() -> None:
    pikepdf = pytest.importorskip("pikepdf", reason="interop group (pikepdf) is not installed")
    doc = _exclusive_program()
    doc.subset_fonts()

    import io  # noqa: PLC0415

    with pikepdf.open(io.BytesIO(doc.to_bytes())) as pdf:
        assert pdf.check_pdf_syntax() == []
