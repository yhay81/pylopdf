"""XMP metadata and catalog document properties with pymupdf's meaning (M5)."""

from __future__ import annotations

import zlib
from collections.abc import Callable
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


# --- language tags -----------------------------------------------------------


def test_language_tag_reads_the_stored_tag_unchanged() -> None:
    assert pylopdf.open(stream=build_pdf(["plain"])).language_tag is None
    # pymupdf reads these as "en" and "zh-Hant"; pylopdf keeps the whole tag.
    assert pylopdf.open(stream=_catalog_pdf("/Lang (en-GB)")).language_tag == "en-GB"
    assert pylopdf.open(stream=_catalog_pdf("/Lang 4 0 R", "(zh-HK)")).language_tag == "zh-HK"
    utf16 = "<FEFF007A0068002D00540057>"  # "zh-TW" as a UTF-16BE text string
    assert pylopdf.open(stream=_catalog_pdf(f"/Lang {utf16}")).language_tag == "zh-TW"
    # A malformed stored value reads as stored; a value that is no string reads as absent.
    assert pylopdf.open(stream=_catalog_pdf("/Lang (not a tag)")).language_tag == "not a tag"
    assert pylopdf.open(stream=_catalog_pdf("/Lang /en")).language_tag is None


@pytest.mark.parametrize(
    "tag", ["zh-CN", "zh-TW", "zh-HK", "en-US", "pt-BR", "sr-Latn-RS", "ja", "x-private", "de-CH-1996"]
)
def test_set_language_tag_keeps_the_complete_tag(tag: str) -> None:
    doc = pylopdf.open(stream=build_pdf(["plain"]))
    assert doc.set_language_tag(tag) is None
    assert doc.language_tag == tag
    lang = doc.catalog["Lang"]
    assert isinstance(lang, pylopdf.String)
    assert bytes(lang) == tag.encode()
    assert pylopdf.open(stream=doc.tobytes()).language_tag == tag


def test_set_language_tag_none_removes_lang() -> None:
    doc = pylopdf.open(stream=_catalog_pdf("/Lang (en-US)"))
    doc.set_language_tag(None)
    assert "Lang" not in doc.catalog
    assert doc.language_tag is None
    doc.set_language_tag(None)  # Removing an absent tag changes nothing.
    assert pylopdf.open(stream=doc.tobytes()).language_tag is None


@pytest.mark.parametrize(
    "tag",
    [
        "",
        "-",
        "en-",
        "en--US",
        "1en",
        "en_US",
        "en US",
        "toolongtag",
        "en-123456789",
        "ja\n",
        "日本語",
        "a" + "-a" * 128,
        5,
    ],
)
def test_set_language_tag_rejects_malformed_tags(tag: object) -> None:
    doc = pylopdf.open(stream=_catalog_pdf("/Lang (ja)"))
    with pytest.raises(pylopdf.PdfError, match="BCP 47"):
        doc.set_language_tag(tag)  # ty: ignore[invalid-argument-type]
    assert doc.language_tag == "ja"


def test_set_language_tag_repeats_the_check_in_rust() -> None:
    doc = pylopdf.open(stream=build_pdf(["plain"]))
    for tag in ("en_US", "a" + "-a" * 128):
        with pytest.raises(pylopdf.PdfError, match="BCP 47"):
            doc._doc.set_language_tag(tag)  # noqa: SLF001 - the core repeats the check.
    doc._doc.set_language_tag("a" + "-a" * 127)  # noqa: SLF001 - 255 bytes is accepted.
    assert doc.language_tag == "a" + "-a" * 127


def test_pymupdf_language_names_raise_migration_hints() -> None:
    doc = pylopdf.open(stream=build_pdf(["plain"]))
    with pytest.raises(AttributeError, match="language_tag"):
        _ = doc.language  # ty: ignore[unresolved-attribute]
    with pytest.raises(AttributeError, match="set_language_tag"):
        doc.set_language("en-US")  # ty: ignore[unresolved-attribute]


# --- page numbers, unsaved changes, and permissions ----------------------------


def _labeled_doc() -> pylopdf.Document:
    doc = pylopdf.open(stream=build_pdf([f"Page {i}" for i in range(8)]))
    doc.set_page_labels(
        [
            {"startpage": 0, "style": "r"},
            {"startpage": 3, "style": "D"},
            {"startpage": 5, "style": "A", "prefix": "App-"},
            {"startpage": 7, "prefix": "X"},
        ]
    )
    return doc


# Each expected list is pymupdf 1.28.2's result for the same labels.
@pytest.mark.parametrize(
    ("label", "numbers"),
    [("i", [0]), ("iii", [2]), ("1", [3]), ("2", [4]), ("App-A", [5]), ("App-B", [6]), ("X", [7]), ("3", [])],
)
def test_get_page_numbers_matches_page_labels(label: str, numbers: list[int]) -> None:
    doc = _labeled_doc()
    assert doc.get_page_numbers(label) == numbers
    assert all(doc[pno].get_label() == label for pno in numbers)


def test_get_page_numbers_finds_every_page_or_only_the_first() -> None:
    doc = pylopdf.open(stream=build_pdf(["a", "b", "c"]))
    doc.set_page_labels([{"startpage": 0, "prefix": "cover"}])
    assert doc.get_page_numbers("cover") == [0, 1, 2]
    assert doc.get_page_numbers("cover", True) == [0]
    assert doc.get_page_numbers("cover", only_one=True) == [0]


def test_get_page_numbers_without_labels_or_label_is_empty() -> None:
    assert pylopdf.open(stream=build_pdf(["a"])).get_page_numbers("1") == []
    assert _labeled_doc().get_page_numbers("") == []


def test_is_dirty_starts_false_for_opened_documents_and_true_for_new_ones() -> None:
    assert not pylopdf.open(stream=build_pdf(["a"])).is_dirty
    assert not pylopdf.open(REAL_WORLD / "usrguide.pdf").is_dirty
    assert pylopdf.open().is_dirty


def test_reads_leave_is_dirty_false() -> None:
    doc = pylopdf.open(REAL_WORLD / "pdfium-links-highlights-annots.pdf")
    page = doc[0]
    page.get_text()
    page.get_text("dict")
    page.get_pixmap()
    page.get_links()
    page.get_drawings()
    page.search_for("a")
    doc.to_markdown()
    doc.get_outline()
    doc.get_xml_metadata()
    _ = (doc.metadata, doc.pagemode, doc.markinfo, doc.has_annots(), doc.permissions)
    _ = list(doc.objects)
    assert not doc.is_dirty


@pytest.mark.parametrize(
    "edit",
    [
        lambda doc: doc.set_metadata({"title": "t"}),
        lambda doc: doc.set_metadata(doc.metadata),
        lambda doc: doc[0].set_rotation(90),
        lambda doc: doc[0].insert_text((72, 72), "x"),
        lambda doc: doc.select([0]),
        lambda doc: doc.set_pagemode("UseOutlines"),
        lambda doc: doc.set_xml_metadata("<x/>"),
        lambda doc: doc.new_page(),
    ],
)
def test_every_edit_sets_is_dirty_until_the_document_closes(edit: Callable[[pylopdf.Document], object]) -> None:
    doc = pylopdf.open(stream=build_pdf(["a"]))
    edit(doc)
    assert doc.is_dirty
    # pymupdf never resets it, not even after an incremental save.
    doc.tobytes()
    assert doc.is_dirty


def test_save_options_do_not_set_is_dirty(tmp_path: Path) -> None:
    doc = pylopdf.open(REAL_WORLD / "usrguide.pdf")
    doc.tobytes(garbage=4, deflate=True)
    doc.save(tmp_path / "out.pdf", garbage=3, use_objstms=True)
    assert not doc.is_dirty


def test_is_dirty_is_false_after_authentication() -> None:
    doc = pylopdf.open(ENCRYPTED / "user-aes-256.pdf")
    assert doc.authenticate("userpw") == 2
    assert not doc.is_dirty


def test_unencrypted_documents_have_every_permission() -> None:
    assert pylopdf.open(stream=build_pdf(["a"])).permissions == -4
    assert pylopdf.open().permissions == -4


def _restricted(user_pw: str, permissions: int) -> bytes:
    doc = pylopdf.open(stream=build_pdf(["a"]))
    return doc.tobytes(
        encryption=pylopdf.PDF_ENCRYPT_AES_256, user_pw=user_pw, owner_pw="boss", permissions=permissions
    )


def test_permissions_follow_the_password_that_granted_access() -> None:
    # pymupdf 1.28.2 reads these values for the same documents.
    data = _restricted("user", pylopdf.Permissions.PRINT | pylopdf.Permissions.COPY)
    doc = pylopdf.open(stream=data)
    assert doc.permissions == 0
    assert doc.authenticate("user") == 2
    assert doc.permissions == -3884
    assert doc.permissions & pylopdf.Permissions.PRINT
    assert not doc.permissions & pylopdf.Permissions.MODIFY
    assert doc.authenticate("boss") == 4
    assert doc.permissions == -4
    assert pylopdf.open(stream=data, password="user").permissions == -3884
    assert pylopdf.open(stream=data, password="boss").permissions == -4


def test_permissions_of_a_document_with_an_empty_user_password() -> None:
    doc = pylopdf.open(stream=_restricted("", pylopdf.Permissions.PRINT))
    assert doc.permissions == -3900
    assert doc.authenticate("wrong") == 0
    assert doc.permissions == -3900
    assert doc.authenticate("boss") == 4
    assert doc.permissions == -4
    assert doc.authenticate("") == 2
    assert doc.permissions == -3900


@pytest.mark.parametrize("name", ["user-rc4-40.pdf", "user-rc4-128.pdf", "user-aes-128.pdf", "user-aes-256.pdf"])
def test_permissions_of_the_encrypted_assets_match_pymupdf(name: str) -> None:
    doc = pylopdf.open(ENCRYPTED / name)
    assert doc.permissions == 0
    doc.authenticate("userpw")
    assert doc.permissions == -4


def test_document_state_properties_raise_after_close() -> None:
    doc = pylopdf.open(stream=build_pdf(["a"]))
    doc.close()
    for read in (lambda: doc.is_dirty, lambda: doc.permissions):
        with pytest.raises(pylopdf.DocumentClosedError):
            read()
