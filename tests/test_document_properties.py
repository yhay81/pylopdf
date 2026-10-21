"""XMP metadata and catalog document properties with pymupdf's meaning (M5)."""

from __future__ import annotations

import zlib
from pathlib import Path

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf
from pylopdf.pylopdf_core import LimitError

REAL_WORLD = Path(__file__).parent / "assets" / "real_world"
ENCRYPTED = Path(__file__).parent / "assets" / "encrypted"


def _catalog_pdf(catalog_entries: str = "", *extra: bytes | str) -> bytes:
    """Build one page whose catalog holds ``catalog_entries``; extra objects start at 4."""
    objects: dict[int, bytes | str] = {
        1: f"<< /Type /Catalog /Pages 2 0 R {catalog_entries} >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
    }
    objects.update(enumerate(extra, start=4))
    return build_raw_pdf(objects)


def _stream(data: bytes, entries: bytes = b"") -> bytes:
    return b"<< " + entries + b" /Length " + str(len(data)).encode() + b" >>\nstream\n" + data + b"\nendstream"


def _xmp_pdf(data: bytes, *, compressed: bool = False) -> bytes:
    payload = zlib.compress(data) if compressed else data
    entries = b"/Type /Metadata /Subtype /XML" + (b" /Filter /FlateDecode" if compressed else b"")
    return _catalog_pdf("/Metadata 4 0 R", _stream(payload, entries))


def _annotated_pdf(annots: str, *extra: bytes | str) -> bytes:
    objects: dict[int, bytes | str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Annots {annots} >>",
    }
    objects.update(enumerate(extra, start=4))
    return build_raw_pdf(objects)


# --- XMP metadata ---------------------------------------------------------


def test_xml_metadata_is_empty_without_a_metadata_stream() -> None:
    assert pylopdf.open(stream=build_pdf(["plain"])).get_xml_metadata() == ""
    # A /Metadata entry that is no stream reads as absent, as in pymupdf.
    assert pylopdf.open(stream=_catalog_pdf("/Metadata 4 0 R", "<< /Type /Metadata >>")).get_xml_metadata() == ""


def test_xml_metadata_decodes_filters_invalid_utf8_and_nul_as_pymupdf() -> None:
    xmp = '<x:xmpmeta xmlns:x="adobe:ns:meta/">日本語</x:xmpmeta>'
    assert pylopdf.open(stream=_xmp_pdf(xmp.encode(), compressed=True)).get_xml_metadata() == xmp
    damaged = pylopdf.open(stream=_xmp_pdf(b"<a>\xff</a>\x00ignored"))
    assert damaged.get_xml_metadata() == "<a>�</a>"


@pytest.mark.parametrize(
    ("name", "length"),
    [
        ("bill-hr815.pdf", 5440),
        ("f1040.pdf", 4090),
        ("pdf20-simple.pdf", 2351),
        ("wdl6812-manuscript.pdf", 2004),
        ("usrguide.pdf", 0),
    ],
)
def test_real_world_xml_metadata_matches_pymupdf_lengths(name: str, length: int) -> None:
    # pymupdf 1.28.2 returns text of the same lengths and digests for these files.
    xmp = pylopdf.open(REAL_WORLD / name).get_xml_metadata(max_size=None)
    assert len(xmp) == length
    if length:
        assert "<x:xmpmeta" in xmp or "<?xpacket" in xmp


def test_xml_metadata_read_is_bounded_on_every_decoding_layer() -> None:
    doc = pylopdf.open(stream=_xmp_pdf(b"<x/>" + b" " * 2_000_000, compressed=True))
    with pytest.raises(LimitError) as caught:
        doc.get_xml_metadata()
    assert caught.value.code == "xmp_metadata_size"
    assert len(doc.get_xml_metadata(max_size=None)) == 2_000_004
    with pytest.raises(ValueError, match="max_size"):
        doc.get_xml_metadata(max_size=0)


def test_set_xml_metadata_adds_an_unfiltered_metadata_stream(tmp_path: Path) -> None:
    doc = pylopdf.open(stream=build_pdf(["plain"]))
    xmp = "<x:xmpmeta>héllo</x:xmpmeta>"

    assert doc.set_xml_metadata(xmp) is None
    stream = doc.catalog["Metadata"]
    assert isinstance(stream, pylopdf.Stream)
    assert stream["Type"] == "Metadata"
    assert stream["Subtype"] == "XML"
    assert "Filter" not in stream
    assert doc.get_xml_metadata() == xmp

    # Deflating streams leaves the XMP packet readable in the file.
    path = tmp_path / "xmp.pdf"
    doc.save(path, deflate=True)
    assert xmp.encode() in path.read_bytes()
    reopened = pylopdf.open(path)
    assert reopened.get_xml_metadata() == xmp
    assert reopened.get_text().strip() == "plain"


def test_set_xml_metadata_rewrites_an_existing_stream_in_place() -> None:
    doc = pylopdf.open(stream=_xmp_pdf(b"<old/>", compressed=True))
    xref = doc.catalog["Metadata"].xref

    doc.set_xml_metadata("<new/>")
    stream = doc.catalog["Metadata"]
    assert stream.xref == xref
    assert "Filter" not in stream
    assert stream["Type"] == "Metadata"
    assert stream.read(decode=False) == b"<new/>"
    assert pylopdf.open(stream=doc.tobytes(deflate=True)).get_xml_metadata() == "<new/>"


def test_set_xml_metadata_input_is_bounded_before_any_change() -> None:
    doc = pylopdf.open(stream=build_pdf(["plain"]))
    objects = len(doc.objects)
    with pytest.raises(LimitError) as caught:
        doc.set_xml_metadata("é" * 600_000)  # 1.2 MB of UTF-8 in 600,000 characters
    assert caught.value.code == "xmp_metadata_input_size"
    with pytest.raises(LimitError):
        doc.set_xml_metadata("x" * 100, max_size=99)
    assert len(doc.objects) == objects
    assert "Metadata" not in doc.catalog

    doc.set_xml_metadata("x" * 100, max_size=100)
    doc.set_xml_metadata("x" * 2_000_000, max_size=None)
    assert len(doc.get_xml_metadata(max_size=None)) == 2_000_000


def test_set_xml_metadata_repeats_the_input_bound_in_rust() -> None:
    doc = pylopdf.open(stream=build_pdf(["plain"]))
    with pytest.raises(LimitError) as caught:
        doc._doc.set_xml_metadata(b"x" * 10, 9)  # noqa: SLF001 - the core repeats the bound.
    assert caught.value.code == "xmp_metadata_input_size"
    assert "Metadata" not in doc.catalog


def test_set_xml_metadata_takes_text_only() -> None:
    doc = pylopdf.open(stream=build_pdf(["plain"]))
    with pytest.raises(TypeError, match="str"):
        doc.set_xml_metadata(b"<x/>")  # ty: ignore[invalid-argument-type]


def test_del_xml_metadata_removes_the_catalog_entry() -> None:
    doc = pylopdf.open(stream=_xmp_pdf(b"<old/>"))
    doc.del_xml_metadata()
    assert "Metadata" not in doc.catalog
    assert doc.get_xml_metadata() == ""
    doc.del_xml_metadata()  # Removing absent metadata changes nothing.
    saved = pylopdf.open(stream=doc.tobytes(garbage=1))
    assert saved.get_xml_metadata() == ""
    assert all(
        not (isinstance(value, pylopdf.Stream) and value.get("Type") == "Metadata") for value in saved.objects.values()
    )


def test_set_metadata_leaves_xmp_unchanged() -> None:
    doc = pylopdf.open(stream=_xmp_pdf(b"<old/>"))
    doc.set_metadata({"title": "New"})
    assert doc.get_xml_metadata() == "<old/>"


# --- page mode and page layout ----------------------------------------------


def test_page_mode_and_layout_default_to_pymupdf_values() -> None:
    doc = pylopdf.open(stream=build_pdf(["plain"]))
    assert doc.pagemode == "UseNone"
    assert doc.pagelayout == "SinglePage"
    # A value that is no name reads as unset, as in pymupdf.
    odd = pylopdf.open(stream=_catalog_pdf("/PageMode (UseOutlines) /PageLayout 7"))
    assert odd.pagemode == "UseNone"
    assert odd.pagelayout == "SinglePage"
    assert pylopdf.open(REAL_WORLD / "usrguide.pdf").pagemode == "UseOutlines"


def test_page_mode_reads_names_outside_the_standard_list() -> None:
    doc = pylopdf.open(stream=_catalog_pdf("/PageMode /Custom /PageLayout 4 0 R", "/TwoColumnLeft"))
    assert doc.pagemode == "Custom"
    assert doc.pagelayout == "TwoColumnLeft"


@pytest.mark.parametrize(
    ("value", "stored"),
    [("FullScreen", "FullScreen"), ("/useoc", "UseOC"), ("USEATTACHMENTS", "UseAttachments")],
)
def test_set_pagemode_matches_names_case_insensitively(value: str, stored: str) -> None:
    doc = pylopdf.open(stream=build_pdf(["plain"]))
    assert doc.set_pagemode(value) is True
    assert doc.catalog["PageMode"] == stored
    assert isinstance(doc.catalog["PageMode"], pylopdf.Name)
    assert pylopdf.open(stream=doc.tobytes()).pagemode == stored


@pytest.mark.parametrize(
    ("value", "stored"),
    [("OneColumn", "OneColumn"), ("/twopageright", "TwoPageRight"), ("twocolumnleft", "TwoColumnLeft")],
)
def test_set_pagelayout_matches_names_case_insensitively(value: str, stored: str) -> None:
    doc = pylopdf.open(stream=build_pdf(["plain"]))
    assert doc.set_pagelayout(value) is True
    assert pylopdf.open(stream=doc.tobytes()).pagelayout == stored


@pytest.mark.parametrize("value", ["", "/", "Custom", "SinglePage", None, 1])
def test_set_pagemode_rejects_other_values(value: object) -> None:
    doc = pylopdf.open(stream=build_pdf(["plain"]))
    with pytest.raises(pylopdf.PdfError, match="bad PageMode value"):
        doc.set_pagemode(value)  # ty: ignore[invalid-argument-type]
    assert "PageMode" not in doc.catalog


def test_set_pagelayout_rejects_page_modes() -> None:
    doc = pylopdf.open(stream=build_pdf(["plain"]))
    with pytest.raises(ValueError, match="bad PageLayout value"):
        doc.set_pagelayout("UseNone")
    with pytest.raises(pylopdf.PdfError, match="bad PageLayout"):
        doc._doc.set_catalog_name("PageLayout", "twopageright")  # noqa: SLF001
    with pytest.raises(pylopdf.PdfError, match="unsupported"):
        doc._doc.set_catalog_name("Pages", "SinglePage")  # noqa: SLF001
    assert "PageLayout" not in doc.catalog


# --- MarkInfo ----------------------------------------------------------------


def test_markinfo_reads_pymupdf_shape() -> None:
    assert pylopdf.open(stream=build_pdf(["plain"])).markinfo == {}
    assert pylopdf.open(stream=_catalog_pdf("/MarkInfo true")).markinfo == {}
    assert pylopdf.open(REAL_WORLD / "f1040.pdf").markinfo == {
        "Marked": True,
        "UserProperties": False,
        "Suspects": False,
    }
    indirect = pylopdf.open(stream=_catalog_pdf("/MarkInfo 4 0 R", "<< /Suspects true /Marked 5 >>"))
    assert indirect.markinfo == {"Marked": False, "UserProperties": False, "Suspects": True}


def test_set_markinfo_writes_all_three_flags() -> None:
    doc = pylopdf.open(stream=_catalog_pdf("/MarkInfo << /UserProperties true /Extra 1 >>"))
    assert doc.set_markinfo({"Marked": True, "Suspects": "TRUE"}) is True  # ty: ignore[invalid-argument-type]
    # Missing keys become false, whatever the document held, as in pymupdf.
    expected = {"Marked": True, "UserProperties": False, "Suspects": True}
    assert doc.markinfo == expected
    assert sorted(doc.catalog["MarkInfo"]) == ["Marked", "Suspects", "UserProperties"]
    assert pylopdf.open(stream=doc.tobytes()).markinfo == expected


def test_set_markinfo_ignores_empty_input_and_rejects_bad_entries() -> None:
    doc = pylopdf.open(stream=build_pdf(["plain"]))
    assert doc.set_markinfo({}) is False
    assert doc.set_markinfo(None) is False  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="bad MarkInfo key"):
        doc.set_markinfo({"Marked": True, "Tagged": True})  # ty: ignore[invalid-argument-type, invalid-key]
    with pytest.raises(ValueError, match="bad MarkInfo value"):
        doc.set_markinfo({"Marked": 1})  # ty: ignore[invalid-argument-type]
    assert doc.markinfo == {}


# --- links and annotations -------------------------------------------------


def test_has_links_and_annots_on_documents_without_annotations() -> None:
    doc = pylopdf.open(stream=build_pdf(["plain", "two"]))
    assert doc.has_links() is False
    assert doc.has_annots() is False


def test_has_links_and_annots_follow_pymupdf_subtypes() -> None:
    links = pylopdf.open(
        stream=_annotated_pdf("[4 0 R null 9 0 R]", "<< /Type /Annot /Subtype /Link /Rect [0 0 1 1] >>")
    )
    assert (links.has_links(), links.has_annots()) == (True, False)

    widgets = pylopdf.open(stream=_annotated_pdf("[<< /Subtype /Widget /Rect [0 0 1 1] >>]"))
    assert (widgets.has_links(), widgets.has_annots()) == (False, False)

    # An annotation without a subtype counts, as MuPDF reads it as unknown.
    untyped = pylopdf.open(stream=_annotated_pdf("4 0 R", "[<< /Rect [0 0 1 1] >>]"))
    assert (untyped.has_links(), untyped.has_annots()) == (False, True)


def test_has_links_and_annots_on_real_world_files() -> None:
    # pymupdf 1.28.2 reports the same values for these files.
    annotated = pylopdf.open(REAL_WORLD / "pdfium-links-highlights-annots.pdf")
    assert (annotated.has_links(), annotated.has_annots()) == (True, True)
    manual = pylopdf.open(REAL_WORLD / "usrguide.pdf")
    assert (manual.has_links(), manual.has_annots()) == (True, False)
    form = pylopdf.open(REAL_WORLD / "f1040.pdf")
    assert (form.has_links(), form.has_annots()) == (False, False)


def test_has_annots_sees_created_annotations() -> None:
    doc = pylopdf.open(stream=build_pdf(["plain"]))
    doc[0].insert_link({"kind": pylopdf.LINK_URI, "from": pylopdf.Rect(10, 10, 50, 50), "uri": "https://example.com"})
    assert (doc.has_links(), doc.has_annots()) == (True, False)
    doc[0].add_highlight_annot(pylopdf.Rect(10, 10, 50, 50))
    assert (doc.has_links(), doc.has_annots()) == (True, True)


# --- document state ------------------------------------------------------


def test_is_closed_and_is_pdf() -> None:
    doc = pylopdf.open(stream=build_pdf(["plain"]))
    assert doc.is_pdf is True
    assert doc.is_closed is False
    doc.close()
    assert doc.is_closed is True
    with pytest.raises(pylopdf.DocumentClosedError):
        _ = doc.is_pdf
    for operation in (doc.get_xml_metadata, doc.has_links, lambda: doc.pagemode, lambda: doc.markinfo):
        with pytest.raises(pylopdf.DocumentClosedError):
            operation()


def test_name_is_the_filename_argument(tmp_path: Path) -> None:
    path = tmp_path / "named.pdf"
    path.write_bytes(build_pdf(["plain"]))
    assert pylopdf.open(str(path)).name == str(path)
    by_path = pylopdf.open(path)
    assert by_path.name == str(path)
    assert pylopdf.open(stream=path.read_bytes()).name is None
    assert pylopdf.open().name is None
    # pymupdf keeps a file type passed in place of a filename.
    assert pylopdf.open("pdf", path.read_bytes()).name == "pdf"
    by_path.close()
    assert by_path.name is None


def test_encrypted_documents_refuse_property_access_until_authenticated() -> None:
    doc = pylopdf.open(ENCRYPTED / "user-aes-256.pdf")
    for operation in (doc.get_xml_metadata, doc.has_annots, lambda: doc.pagelayout):
        with pytest.raises(pylopdf.EncryptedDocumentError):
            operation()
    assert doc.name == str(ENCRYPTED / "user-aes-256.pdf")


def test_property_edits_keep_page_views_valid() -> None:
    doc = pylopdf.open(stream=build_pdf(["plain"]))
    page = doc[0]
    doc.set_xml_metadata("<x/>")
    doc.set_pagemode("UseThumbs")
    doc.set_pagelayout("OneColumn")
    doc.set_markinfo({"Marked": True})
    doc.del_xml_metadata()
    assert page.get_text().strip() == "plain"
