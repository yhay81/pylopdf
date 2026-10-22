"""XMP metadata, catalog properties, and document state."""

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


def test_xmp_is_none_without_a_metadata_stream() -> None:
    assert pylopdf.open(build_pdf(["plain"])).xmp is None
    # A /Metadata entry that is no stream reads as absent, as in pymupdf.
    assert pylopdf.open(_catalog_pdf("/Metadata 4 0 R", "<< /Type /Metadata >>")).xmp is None


def test_xmp_decodes_filters_invalid_utf8_and_nul_as_pymupdf() -> None:
    xmp = '<x:xmpmeta xmlns:x="adobe:ns:meta/">日本語</x:xmpmeta>'
    assert pylopdf.open(_xmp_pdf(xmp.encode(), compressed=True)).xmp == xmp
    damaged = pylopdf.open(_xmp_pdf(b"<a>\xff</a>\x00ignored"))
    assert damaged.xmp == "<a>�</a>"


@pytest.mark.parametrize(
    ("name", "length"),
    [
        ("bill-hr815.pdf", 5440),
        ("f1040.pdf", 4090),
        ("pdf20-simple.pdf", 2351),
        ("wdl6812-manuscript.pdf", 2004),
    ],
)
def test_real_world_xmp_matches_pymupdf_lengths(name: str, length: int) -> None:
    # pymupdf 1.28.2 returns text of the same lengths and digests for these files.
    xmp = pylopdf.open(REAL_WORLD / name).xmp
    assert xmp is not None
    assert len(xmp) == length
    assert "<x:xmpmeta" in xmp or "<?xpacket" in xmp


def test_real_world_file_without_xmp() -> None:
    assert pylopdf.open(REAL_WORLD / "usrguide.pdf").xmp is None


def test_xmp_read_is_bounded_on_every_decoding_layer() -> None:
    doc = pylopdf.open(_xmp_pdf(b"<x/>" + b" " * 2_000_000, compressed=True))
    with pytest.raises(LimitError) as caught:
        _ = doc.xmp
    assert caught.value.code == "xmp_metadata_size"
    with pytest.raises(LimitError) as caught:
        _ = doc.pdfa_claim
    assert caught.value.code == "xmp_metadata_size"
    # A larger packet reads through the stream itself.
    assert len(doc.catalog["Metadata"].read(max_size=None)) == 2_000_004


def test_assigning_xmp_adds_an_unfiltered_metadata_stream(tmp_path: Path) -> None:
    doc = pylopdf.open(build_pdf(["plain"]))
    xmp = "<x:xmpmeta>héllo</x:xmpmeta>"

    doc.xmp = xmp
    stream = doc.catalog["Metadata"]
    assert isinstance(stream, pylopdf.Stream)
    assert stream["Type"] == "Metadata"
    assert stream["Subtype"] == "XML"
    assert "Filter" not in stream
    assert doc.xmp == xmp

    # Compressing saves leave the XMP packet readable in the file.
    path = tmp_path / "xmp.pdf"
    doc.save(path)
    assert xmp.encode() in path.read_bytes()
    reopened = pylopdf.open(path)
    assert reopened.xmp == xmp
    assert reopened.text().strip() == "plain"


def test_assigning_xmp_rewrites_an_existing_stream_in_place() -> None:
    doc = pylopdf.open(_xmp_pdf(b"<old/>", compressed=True))
    xref = doc.catalog["Metadata"].xref

    doc.xmp = "<new/>"
    stream = doc.catalog["Metadata"]
    assert stream.xref == xref
    assert "Filter" not in stream
    assert stream["Type"] == "Metadata"
    assert stream.read(decode=False) == b"<new/>"
    assert pylopdf.open(doc.to_bytes()).xmp == "<new/>"


def test_assigned_xmp_is_bounded_before_any_change() -> None:
    doc = pylopdf.open(build_pdf(["plain"]))
    objects = len(doc.objects)
    with pytest.raises(LimitError) as caught:
        doc.xmp = "é" * 600_000  # 1.2 MB of UTF-8 in 600,000 characters
    assert caught.value.code == "xmp_metadata_input_size"
    assert len(doc.objects) == objects
    assert "Metadata" not in doc.catalog

    doc.xmp = "x" * (1 << 20)
    assert doc.xmp == "x" * (1 << 20)


def test_assigned_xmp_repeats_the_input_bound_in_rust() -> None:
    doc = pylopdf.open(build_pdf(["plain"]))
    with pytest.raises(LimitError) as caught:
        doc._doc.set_xml_metadata(b"x" * 10, 9)  # noqa: SLF001 - the core repeats the bound.
    assert caught.value.code == "xmp_metadata_input_size"
    assert "Metadata" not in doc.catalog


def test_assigned_xmp_is_text_only() -> None:
    doc = pylopdf.open(build_pdf(["plain"]))
    with pytest.raises(TypeError, match="str"):
        doc.xmp = b"<x/>"  # ty: ignore[invalid-assignment]


def test_deleting_xmp_removes_the_catalog_entry() -> None:
    doc = pylopdf.open(_xmp_pdf(b"<old/>"))
    del doc.xmp
    assert "Metadata" not in doc.catalog
    assert doc.xmp is None
    del doc.xmp  # Removing absent metadata changes nothing.
    saved = pylopdf.open(doc.to_bytes())
    assert saved.xmp is None
    assert all(
        not (isinstance(value, pylopdf.Stream) and value.get("Type") == "Metadata") for value in saved.objects.values()
    )


def test_info_metadata_edits_leave_xmp_unchanged() -> None:
    doc = pylopdf.open(_xmp_pdf(b"<old/>"))
    doc.metadata["title"] = "New"
    doc.metadata = {"author": "Me"}
    assert doc.xmp == "<old/>"


# --- page mode and page layout ----------------------------------------------


def test_page_mode_and_layout_default_to_their_specified_values() -> None:
    doc = pylopdf.open(build_pdf(["plain"]))
    assert doc.page_mode is pylopdf.PageMode.USE_NONE
    assert doc.page_layout is pylopdf.PageLayout.SINGLE_PAGE
    # A value that is no name, or a name outside ISO 32000-2 Table 29, reads as unset.
    odd = pylopdf.open(_catalog_pdf("/PageMode (UseOutlines) /PageLayout 7"))
    assert odd.page_mode is pylopdf.PageMode.USE_NONE
    assert odd.page_layout is pylopdf.PageLayout.SINGLE_PAGE
    custom = pylopdf.open(_catalog_pdf("/PageMode /Custom /PageLayout 4 0 R", "/TwoColumnLeft"))
    assert custom.page_mode is pylopdf.PageMode.USE_NONE
    assert custom.page_layout is pylopdf.PageLayout.TWO_COLUMN_LEFT
    assert pylopdf.open(REAL_WORLD / "usrguide.pdf").page_mode is pylopdf.PageMode.USE_OUTLINES


@pytest.mark.parametrize("mode", [pylopdf.PageMode.FULL_SCREEN, pylopdf.PageMode.USE_OC, "UseAttachments", "UseNone"])
def test_assigning_page_mode_writes_the_name(mode: pylopdf.PageMode | str) -> None:
    doc = pylopdf.open(build_pdf(["plain"]))
    doc.page_mode = mode
    assert doc.catalog["PageMode"] == mode
    assert isinstance(doc.catalog["PageMode"], pylopdf.Name)
    assert pylopdf.open(doc.to_bytes()).page_mode == mode


@pytest.mark.parametrize("layout", [pylopdf.PageLayout.ONE_COLUMN, "TwoPageRight", "TwoColumnLeft"])
def test_assigning_page_layout_writes_the_name(layout: pylopdf.PageLayout | str) -> None:
    doc = pylopdf.open(build_pdf(["plain"]))
    doc.page_layout = layout
    assert pylopdf.open(doc.to_bytes()).page_layout == layout


@pytest.mark.parametrize("value", ["", "/", "Custom", "SinglePage", "useoc", "/UseOC", None, 1])
def test_page_mode_refuses_other_values(value: object) -> None:
    doc = pylopdf.open(build_pdf(["plain"]))
    with pytest.raises(ValueError, match="page_mode must be one of"):
        doc.page_mode = value  # ty: ignore[invalid-assignment]
    assert "PageMode" not in doc.catalog


def test_page_layout_refuses_page_modes() -> None:
    doc = pylopdf.open(build_pdf(["plain"]))
    with pytest.raises(ValueError, match="page_layout must be one of"):
        doc.page_layout = "UseNone"
    with pytest.raises(pylopdf.PdfError, match="bad PageLayout"):
        doc._doc.set_catalog_name("PageLayout", "twopageright")  # noqa: SLF001
    with pytest.raises(pylopdf.PdfError, match="unsupported"):
        doc._doc.set_catalog_name("Pages", "SinglePage")  # noqa: SLF001
    assert "PageLayout" not in doc.catalog


# --- MarkInfo ----------------------------------------------------------------


def test_mark_info_reads_the_three_flags() -> None:
    assert pylopdf.open(build_pdf(["plain"])).mark_info is None
    assert pylopdf.open(_catalog_pdf("/MarkInfo true")).mark_info is None
    assert pylopdf.open(REAL_WORLD / "f1040.pdf").mark_info == pylopdf.MarkInfo(marked=True)
    indirect = pylopdf.open(_catalog_pdf("/MarkInfo 4 0 R", "<< /Suspects true /Marked 5 >>"))
    assert indirect.mark_info == pylopdf.MarkInfo(suspects=True)


def test_assigning_mark_info_writes_all_three_flags() -> None:
    doc = pylopdf.open(_catalog_pdf("/MarkInfo << /UserProperties true /Extra 1 >>"))
    flags = pylopdf.MarkInfo(marked=True, suspects=True)
    doc.mark_info = flags
    # Flags left false are written false, whatever the document held.
    assert doc.mark_info == flags
    assert sorted(doc.catalog["MarkInfo"]) == ["Marked", "Suspects", "UserProperties"]
    assert pylopdf.open(doc.to_bytes()).mark_info == flags


def test_mark_info_refuses_other_values_and_deletes() -> None:
    doc = pylopdf.open(REAL_WORLD / "f1040.pdf")
    with pytest.raises(TypeError, match="MarkInfo"):
        doc.mark_info = {"Marked": True}  # ty: ignore[invalid-assignment]
    with pytest.raises(TypeError, match="bool"):
        pylopdf.MarkInfo(marked=1)  # ty: ignore[invalid-argument-type]
    del doc.mark_info
    assert doc.mark_info is None
    del doc.mark_info  # Removing absent flags changes nothing.
    assert pylopdf.open(doc.to_bytes()).mark_info is None


# --- links and annotations -------------------------------------------------


def test_has_links_and_annots_on_documents_without_annotations() -> None:
    doc = pylopdf.open(build_pdf(["plain", "two"]))
    assert doc.has_links() is False
    assert doc.has_annots() is False


def test_has_links_and_annots_follow_pymupdf_subtypes() -> None:
    links = pylopdf.open(_annotated_pdf("[4 0 R null 9 0 R]", "<< /Type /Annot /Subtype /Link /Rect [0 0 1 1] >>"))
    assert (links.has_links(), links.has_annots()) == (True, False)

    widgets = pylopdf.open(_annotated_pdf("[<< /Subtype /Widget /Rect [0 0 1 1] >>]"))
    assert (widgets.has_links(), widgets.has_annots()) == (False, False)

    # An annotation without a subtype counts, as MuPDF reads it as unknown.
    untyped = pylopdf.open(_annotated_pdf("4 0 R", "[<< /Rect [0 0 1 1] >>]"))
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
    doc = pylopdf.open(build_pdf(["plain"]))
    doc.pages[0].insert_link(
        {"kind": pylopdf.LINK_URI, "from": pylopdf.Rect(10, 10, 50, 50), "uri": "https://example.com"}
    )
    assert (doc.has_links(), doc.has_annots()) == (True, False)
    doc.pages[0].add_highlight_annot(pylopdf.Rect(10, 10, 50, 50))
    assert (doc.has_links(), doc.has_annots()) == (True, True)


# --- document state ------------------------------------------------------


def test_is_closed() -> None:
    doc = pylopdf.open(build_pdf(["plain"]))
    assert doc.is_closed is False
    doc.close()
    assert doc.is_closed is True
    with pytest.raises(pylopdf.DocumentClosedError):
        _ = doc.pages
    for operation in (
        lambda: doc.xmp,
        doc.has_links,
        lambda: doc.page_mode,
        lambda: doc.mark_info,
        lambda: doc.metadata,
    ):
        with pytest.raises(pylopdf.DocumentClosedError):
            operation()


def test_path_is_the_file_the_document_came_from(tmp_path: Path) -> None:
    path = tmp_path / "named.pdf"
    path.write_bytes(build_pdf(["plain"]))
    assert pylopdf.open(str(path)).path == path
    by_path = pylopdf.open(path)
    assert by_path.path == path
    assert pylopdf.open(path.read_bytes()).path is None
    assert pylopdf.Document().path is None
    by_path.close()
    assert by_path.path == path


def test_encrypted_documents_refuse_property_access_until_authenticated() -> None:
    doc = pylopdf.open(ENCRYPTED / "user-aes-256.pdf")
    for operation in (lambda: doc.xmp, doc.has_annots, lambda: doc.page_layout, lambda: doc.language):
        with pytest.raises(pylopdf.EncryptedDocumentError):
            operation()
    assert doc.path == ENCRYPTED / "user-aes-256.pdf"


def test_property_edits_keep_page_views_valid() -> None:
    doc = pylopdf.open(build_pdf(["plain"]))
    page = doc.pages[0]
    doc.xmp = "<x/>"
    doc.page_mode = pylopdf.PageMode.USE_THUMBS
    doc.page_layout = pylopdf.PageLayout.ONE_COLUMN
    doc.mark_info = pylopdf.MarkInfo(marked=True)
    doc.language = "en"
    doc.metadata["title"] = "t"
    del doc.xmp
    assert page.text().strip() == "plain"


# --- language tags -----------------------------------------------------------


def test_language_reads_the_stored_tag_unchanged() -> None:
    assert pylopdf.open(build_pdf(["plain"])).language is None
    # pymupdf reads these as "en" and "zh-Hant"; pylopdf keeps the whole tag.
    assert pylopdf.open(_catalog_pdf("/Lang (en-GB)")).language == "en-GB"
    assert pylopdf.open(_catalog_pdf("/Lang 4 0 R", "(zh-HK)")).language == "zh-HK"
    utf16 = "<FEFF007A0068002D00540057>"  # "zh-TW" as a UTF-16BE text string
    assert pylopdf.open(_catalog_pdf(f"/Lang {utf16}")).language == "zh-TW"
    # A malformed stored value reads as stored; a value that is no string reads as absent.
    assert pylopdf.open(_catalog_pdf("/Lang (not a tag)")).language == "not a tag"
    assert pylopdf.open(_catalog_pdf("/Lang /en")).language is None


@pytest.mark.parametrize(
    "tag", ["zh-CN", "zh-TW", "zh-HK", "en-US", "pt-BR", "sr-Latn-RS", "ja", "x-private", "de-CH-1996"]
)
def test_assigning_language_keeps_the_complete_tag(tag: str) -> None:
    doc = pylopdf.open(build_pdf(["plain"]))
    doc.language = tag
    assert doc.language == tag
    lang = doc.catalog["Lang"]
    assert isinstance(lang, pylopdf.String)
    assert bytes(lang) == tag.encode()
    assert pylopdf.open(doc.to_bytes()).language == tag


def test_deleting_language_removes_lang() -> None:
    doc = pylopdf.open(_catalog_pdf("/Lang (en-US)"))
    del doc.language
    assert "Lang" not in doc.catalog
    assert doc.language is None
    del doc.language  # Removing an absent tag changes nothing.
    assert pylopdf.open(doc.to_bytes()).language is None


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
        None,
    ],
)
def test_language_refuses_malformed_tags(tag: object) -> None:
    doc = pylopdf.open(_catalog_pdf("/Lang (ja)"))
    with pytest.raises(pylopdf.PdfError, match="BCP 47"):
        doc.language = tag  # ty: ignore[invalid-assignment]
    assert doc.language == "ja"


def test_language_repeats_the_check_in_rust() -> None:
    doc = pylopdf.open(build_pdf(["plain"]))
    for tag in ("en_US", "a" + "-a" * 128):
        with pytest.raises(pylopdf.PdfError, match="BCP 47"):
            doc._doc.set_language_tag(tag)  # noqa: SLF001 - the core repeats the check.
    doc._doc.set_language_tag("a" + "-a" * 127)  # noqa: SLF001 - 255 bytes is accepted.
    assert doc.language == "a" + "-a" * 127


def test_removed_property_names_raise_migration_hints() -> None:
    doc = pylopdf.open(build_pdf(["plain"]))
    with pytest.raises(AttributeError, match=r"doc\.language"):
        doc.set_language("en-US")  # ty: ignore[unresolved-attribute]
    with pytest.raises(AttributeError, match=r"doc\.page_mode"):
        _ = doc.pagemode  # ty: ignore[unresolved-attribute]
    with pytest.raises(AttributeError, match=r"doc\.xmp"):
        doc.get_xml_metadata()  # ty: ignore[unresolved-attribute]
    with pytest.raises(AttributeError, match=r"doc\.metadata"):
        doc.set_metadata({})  # ty: ignore[unresolved-attribute]


# --- page numbers, unsaved changes, and permissions ----------------------------


def _labeled_doc() -> pylopdf.Document:
    doc = pylopdf.open(build_pdf([f"Page {i}" for i in range(8)]))
    doc.page_labels = [
        pylopdf.PageLabelRange(0, "r"),
        pylopdf.PageLabelRange(3, "D"),
        pylopdf.PageLabelRange(5, "A", "App-"),
        pylopdf.PageLabelRange(7, None, "X"),
    ]
    return doc


# Each expected list is pymupdf 1.28.2's result for the same labels.
@pytest.mark.parametrize(
    ("label", "numbers"),
    [("i", [0]), ("iii", [2]), ("1", [3]), ("2", [4]), ("App-A", [5]), ("App-B", [6]), ("X", [7]), ("3", [])],
)
def test_get_page_numbers_matches_page_labels(label: str, numbers: list[int]) -> None:
    doc = _labeled_doc()
    assert doc.pages.labeled(label) == numbers
    assert all(doc.pages[pno].label == label for pno in numbers)


def test_get_page_numbers_finds_every_page_or_only_the_first() -> None:
    doc = pylopdf.open(build_pdf(["a", "b", "c"]))
    doc.page_labels = [pylopdf.PageLabelRange(0, None, "cover")]
    assert doc.pages.labeled("cover") == [0, 1, 2]
    assert doc.pages.labeled("cover")[:1] == [0]
    assert doc.pages.labeled("cover")[:1] == [0]


def test_get_page_numbers_without_labels_or_label_is_empty() -> None:
    assert pylopdf.open(build_pdf(["a"])).pages.labeled("1") == []
    assert _labeled_doc().pages.labeled("") == []


def test_is_dirty_starts_false_for_opened_documents_and_true_for_new_ones() -> None:
    assert not pylopdf.open(build_pdf(["a"])).is_dirty
    assert not pylopdf.open(REAL_WORLD / "usrguide.pdf").is_dirty
    assert pylopdf.Document().is_dirty


def test_reads_leave_is_dirty_false() -> None:
    doc = pylopdf.open(REAL_WORLD / "pdfium-links-highlights-annots.pdf")
    page = doc.pages[0]
    page.text()
    page.layout().to_dict()
    page.pixmap()
    page.get_links()
    page.content()
    page.search("a")
    doc.markdown()
    _ = (doc.outline.entries(), doc.page_labels, page.label)
    _ = (doc.xmp, dict(doc.metadata), doc.page_mode, doc.mark_info, doc.language, doc.pdfa_claim)
    _ = (doc.has_annots(), doc.permissions, doc.version)
    _ = list(doc.objects)
    assert not doc.is_dirty


@pytest.mark.parametrize(
    "edit",
    [
        lambda doc: doc.metadata.update(title="t"),
        lambda doc: setattr(doc, "metadata", {**doc.metadata, "author": "a"}),
        lambda doc: doc.pages[0].set_rotation(90),
        lambda doc: doc.pages[0].insert_text((72, 72), "x"),
        lambda doc: doc.pages.select([0]),
        lambda doc: setattr(doc, "page_mode", "UseOutlines"),
        lambda doc: setattr(doc, "xmp", "<x/>"),
        lambda doc: doc.pages.new(),
    ],
)
def test_every_edit_sets_is_dirty_until_the_document_closes(edit: Callable[[pylopdf.Document], object]) -> None:
    doc = pylopdf.open(build_pdf(["a"]))
    edit(doc)
    assert doc.is_dirty
    # pymupdf never resets it, not even after an incremental save.
    doc.to_bytes()
    assert doc.is_dirty


def test_save_options_do_not_set_is_dirty(tmp_path: Path) -> None:
    doc = pylopdf.open(REAL_WORLD / "usrguide.pdf")
    doc.to_bytes(dedupe=True, renumber=True)
    doc.save(tmp_path / "out.pdf", dedupe=True, renumber=True, object_streams=True)
    assert not doc.is_dirty


def test_is_dirty_is_false_after_unlocking() -> None:
    doc = pylopdf.open(ENCRYPTED / "user-aes-256.pdf")
    assert doc.unlock("userpw") is pylopdf.Access.USER
    assert not doc.is_dirty


def test_unencrypted_documents_have_every_permission() -> None:
    assert pylopdf.open(build_pdf(["a"])).permissions == pylopdf.Permissions.ALL
    assert pylopdf.Document().permissions == pylopdf.Permissions.ALL


def _restricted(user_password: str, permissions: pylopdf.Permissions) -> bytes:
    encryption = pylopdf.Encryption(user_password=user_password, owner_password="boss", permissions=permissions)
    return pylopdf.open(build_pdf(["a"])).to_bytes(encryption=encryption)


def test_permissions_follow_the_password_that_unlocked_the_document() -> None:
    granted = pylopdf.Permissions.PRINT | pylopdf.Permissions.COPY
    data = _restricted("user", granted)
    doc = pylopdf.open(data)
    assert doc.permissions == pylopdf.Permissions(0)
    assert doc.unlock("user") is pylopdf.Access.USER
    assert doc.permissions == granted
    assert type(doc.permissions) is pylopdf.Permissions
    assert pylopdf.Permissions.PRINT in doc.permissions
    assert pylopdf.Permissions.MODIFY not in doc.permissions
    assert doc.unlock("boss") is pylopdf.Access.OWNER
    assert doc.permissions == pylopdf.Permissions.ALL
    assert pylopdf.open(data, password="user").permissions == granted
    assert pylopdf.open(data, password="boss").permissions == pylopdf.Permissions.ALL


def test_permissions_of_a_document_with_an_empty_user_password() -> None:
    doc = pylopdf.open(_restricted("", pylopdf.Permissions.PRINT))
    assert doc.permissions == pylopdf.Permissions.PRINT
    with pytest.raises(pylopdf.PasswordError):
        doc.unlock("wrong")
    assert doc.permissions == pylopdf.Permissions.PRINT
    assert doc.unlock("boss") is pylopdf.Access.OWNER
    assert doc.permissions == pylopdf.Permissions.ALL
    assert doc.unlock("") is pylopdf.Access.USER
    assert doc.permissions == pylopdf.Permissions.PRINT


def test_a_matching_owner_password_counts_when_the_document_needs_none() -> None:
    data = _restricted("", pylopdf.Permissions.PRINT)
    assert pylopdf.open(data, password="boss").permissions == pylopdf.Permissions.ALL
    assert pylopdf.open(data, password="wrong").permissions == pylopdf.Permissions.PRINT


@pytest.mark.parametrize("name", ["user-rc4-40.pdf", "user-rc4-128.pdf", "user-aes-128.pdf", "user-aes-256.pdf"])
def test_permissions_of_the_encrypted_assets(name: str) -> None:
    doc = pylopdf.open(ENCRYPTED / name)
    assert doc.permissions == pylopdf.Permissions(0)
    doc.unlock("userpw")
    assert doc.permissions == pylopdf.Permissions.ALL


def test_document_state_properties_raise_after_close() -> None:
    doc = pylopdf.open(build_pdf(["a"]))
    doc.close()
    for read in (lambda: doc.is_dirty, lambda: doc.permissions):
        with pytest.raises(pylopdf.DocumentClosedError):
            read()
