"""The typed low-level object model (roadmap F5): ``doc.objects``, the trailer, and the catalog."""

from __future__ import annotations

import zlib
from pathlib import Path

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf
from pylopdf import Array, Dictionary, Name, Stream, String

REAL_WORLD = Path(__file__).parent / "assets" / "real_world"


def _two_pages() -> pylopdf.Document:
    return pylopdf.open(stream=build_pdf(["first", "second"]))


def test_values_map_to_python_types() -> None:
    doc = pylopdf.open(
        stream=build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /Extra 3 0 R >>",
                2: "<< /Type /Pages /Kids [] /Count 0 >>",
                3: "[null true 7 -2.5 /Na#6De (lit) <414243> [1] << /K 1 >> 4 0 R 99 0 R]",
                4: "<< /Length 2 >>\nstream\nab\nendstream",
            }
        )
    )

    values = doc.objects[3]

    assert isinstance(values, Array)
    assert values[0] is None
    assert values[1] is True
    assert values[2] == 7
    assert isinstance(values[2], int)
    assert values[3] == pytest.approx(-2.5)
    assert values[4] == Name("Name")
    assert isinstance(values[4], Name)
    assert values[5] == String(b"lit")
    assert values[6] == b"ABC"
    assert isinstance(values[6], String)
    assert isinstance(values[7], Array)
    assert not values[7].is_indirect
    assert isinstance(values[8], Dictionary)
    assert values[8]["K"] == 1
    assert isinstance(values[9], Stream)
    assert values[9].xref == 4
    # ISO 32000-2 §7.3.10: a reference to a missing object reads as null.
    assert values[10] is None
    assert len(values) == 11


def test_trailer_catalog_and_page_objects() -> None:
    doc = _two_pages()

    assert doc.trailer.xref is None
    assert "Root" in doc.trailer
    assert doc.catalog == doc.trailer["Root"]
    assert doc.catalog.xref == 1
    assert doc.catalog["Type"] == "Catalog"
    assert doc.page_xref(0) == doc[0].xref == 4
    assert doc.page_xref(-1) == 6
    assert doc[1].obj == doc.objects[6]
    assert doc[1].obj["Type"] == Name("Page")
    assert doc.catalog["Pages"]["Kids"][1] == doc[1].obj


def test_objects_mapping() -> None:
    doc = _two_pages()

    numbers = list(doc.objects)

    assert numbers == sorted(numbers)
    assert len(doc.objects) == len(numbers)
    assert 1 in doc.objects
    assert 10_000 not in doc.objects
    assert "1" not in doc.objects
    with pytest.raises(KeyError):
        doc.objects[10_000]
    with pytest.raises(TypeError):
        doc.objects["1"]  # ty: ignore[invalid-argument-type]


def test_dictionary_writes_take_effect_immediately() -> None:
    doc = _two_pages()
    page = doc[0]

    page.obj["Rotate"] = 90
    page.obj["UserUnit"] = 1.5
    del page.obj["UserUnit"]

    assert page.rotation == 90
    assert "UserUnit" not in page.obj
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened[0].rotation == 90


def test_writes_invalidate_cached_text_and_rendering() -> None:
    doc = _two_pages()
    assert doc[0].get_text().strip() == "first"
    contents = doc[0].obj["Contents"]
    assert isinstance(contents, Stream)

    contents.write(b"BT /F1 12 Tf 72 720 Td (replaced) Tj ET")

    assert doc[0].get_text().strip() == "replaced"
    assert contents["Filter"] == "FlateDecode"
    assert contents.read() == b"BT /F1 12 Tf 72 720 Td (replaced) Tj ET"
    assert zlib.decompress(contents.read(decode=False)) == contents.read()


def test_stream_write_without_a_filter_stores_the_data() -> None:
    doc = _two_pages()
    contents = doc[0].obj["Contents"]

    contents.write(b"BT /F1 12 Tf 72 720 Td (plain) Tj ET", filter=None)

    assert "Filter" not in contents
    assert contents["Length"] == len(b"BT /F1 12 Tf 72 720 Td (plain) Tj ET")
    assert contents.read(decode=False) == contents.read()
    assert pylopdf.open(stream=doc.tobytes())[0].get_text().strip() == "plain"


def test_stream_read_is_bounded() -> None:
    doc = pylopdf.open()
    xref = doc.objects.add_stream(b"x" * 10_000)
    stream = doc.objects[xref]

    with pytest.raises(pylopdf.LimitError) as error:
        stream.read(max_size=100)
    assert error.value.code == "stream_data_size"
    with pytest.raises(pylopdf.LimitError):
        stream.read(decode=False, max_size=10)
    assert len(stream.read(max_size=None)) == 10_000
    with pytest.raises(ValueError, match="max_size"):
        stream.read(max_size=0)


def test_unsupported_filters_raise_instead_of_returning_encoded_bytes() -> None:
    doc = pylopdf.open()
    xref = doc.objects.add_stream(b"data", filter=None)
    doc.objects[xref]["Filter"] = Name("JBIG2Decode")

    with pytest.raises(pylopdf.PdfError):
        doc.objects[xref].read()
    assert doc.objects[xref].read(decode=False) == b"data"


def test_add_builds_a_new_page_from_raw_objects() -> None:
    doc = pylopdf.open()
    doc.new_page()
    font = doc.objects.add({"Type": Name("Font"), "Subtype": Name("Type1"), "BaseFont": Name("Helvetica")})
    content = doc.objects.add_stream(b"BT /F9 24 Tf 72 72 Td (raw) Tj ET")

    doc[0].obj["Resources"] = {"Font": {"F9": doc.objects[font]}}
    doc[0].obj["Contents"] = doc.objects[content]

    assert doc[0].get_text().strip() == "raw"
    # An indirect handle stored inside a container is a reference.
    assert doc[0].obj["Resources"]["Font"]["F9"].xref == font
    saved = doc.tobytes()
    assert b"/F9 " + str(font).encode() + b" 0 R" in saved
    assert pylopdf.open(stream=saved)[0].get_text().strip() == "raw"


def test_assigning_a_handle_at_top_level_copies_it() -> None:
    doc = _two_pages()
    copy = doc.objects.add(doc.catalog)

    assert copy != doc.catalog.xref
    assert doc.objects[copy]["Type"] == "Catalog"
    doc.objects[copy]["Type"] = Name("Changed")
    assert doc.catalog["Type"] == "Catalog"

    target = doc.objects.add(0)
    doc.objects[target] = doc.objects[copy]
    assert dict(doc.objects[target]) == dict(doc.objects[copy])


def test_direct_handles_are_copied() -> None:
    doc = _two_pages()
    media_box = doc.catalog["Pages"]["MediaBox"]
    assert isinstance(media_box, Array)
    assert not media_box.is_indirect

    doc[0].obj["MediaBox"] = media_box
    doc[0].obj["MediaBox"][2] = 300

    assert list(doc.catalog["Pages"]["MediaBox"]) == [0, 0, 612, 792]
    assert list(doc[0].obj["MediaBox"]) == [0, 0, 300, 792]


def test_scalar_objects_read_as_values_and_can_be_replaced() -> None:
    doc = pylopdf.open()
    xref = doc.objects.add(42)

    assert doc.objects[xref] == 42
    doc.objects[xref] = Name("Answer")
    assert doc.objects[xref] == Name("Answer")
    with pytest.raises(KeyError):
        doc.objects[10_000] = 1


def test_text_strings() -> None:
    doc = pylopdf.open()
    doc.trailer["Info"] = {"Title": "日本語のタイトル", "Author": "ASCII", "Raw": b"\x00\xff"}

    info = doc.trailer["Info"]

    assert doc.metadata["title"] == "日本語のタイトル"
    assert info["Title"].text == "日本語のタイトル"
    assert info["Title"].startswith(b"\xfe\xff")
    assert info["Author"] == b"ASCII"
    assert info["Raw"] == b"\x00\xff"
    assert String(b"\xef\xbb\xbfUTF-8").text == "UTF-8"
    assert String(b"caf\xe9").text == "café"


def test_names_round_trip_and_reject_a_leading_slash() -> None:
    doc = pylopdf.open()
    xref = doc.objects.add([Name("A#B"), Name("日本"), Name("a\udcff")])

    assert list(doc.objects[xref]) == [Name("A#B"), Name("日本"), Name("a\udcff")]
    with pytest.raises(ValueError, match="leading slash"):
        Name("/Type")
    with pytest.raises(ValueError, match="leading slash"):
        doc.catalog["/Type"]
    assert repr(Name("Type")) == "Name('Type')"


def test_a_str_value_is_a_text_string_not_a_name() -> None:
    doc = pylopdf.open()
    doc.catalog["PageMode"] = "UseOutlines"

    assert isinstance(doc.catalog["PageMode"], String)
    assert b"/PageMode(UseOutlines)" in doc.tobytes()


def test_array_editing() -> None:
    doc = pylopdf.open()
    array = doc.objects[doc.objects.add([1, 2, 3])]

    array.append(4)
    array.insert(0, 0)
    array.insert(-1, 3.5)
    array[1] = Name("one")
    del array[2]
    array.extend([5, 6])

    assert list(array) == [0, Name("one"), 3, 3.5, 4, 5, 6]
    assert array[-1] == 6
    assert array[1:3] == [Name("one"), 3]
    with pytest.raises(IndexError):
        array[100]
    with pytest.raises(TypeError):
        array[0:1] = [1]


def test_reordering_kids_invalidates_pages() -> None:
    doc = _two_pages()
    page = doc[0]
    kids = doc.catalog["Pages"]["Kids"]

    kids[0], kids[1] = kids[1], kids[0]

    with pytest.raises(pylopdf.StalePageError):
        page.get_text()
    assert doc[0].get_text().strip() == "second"


def test_writes_that_keep_the_page_order_keep_pages() -> None:
    doc = _two_pages()
    page = doc[1]

    doc.catalog["PageLayout"] = Name("TwoColumnLeft")
    doc.objects.add({"Unused": True})

    assert page.get_text().strip() == "second"


def test_handles_go_stale_when_objects_are_renumbered() -> None:
    doc = _two_pages()
    pages = doc.catalog["Pages"]
    doc.objects.add({"Unreferenced": True})

    doc.tobytes(garbage=1)
    assert pages["Count"] == 2

    doc.tobytes(garbage=2)
    with pytest.raises(pylopdf.StaleObjectError):
        pages["Count"]
    assert doc.catalog["Pages"]["Count"] == 2


def test_handles_to_removed_objects_go_stale() -> None:
    doc = _two_pages()
    second = doc[1].obj

    doc.delete_page(1)

    with pytest.raises(pylopdf.StaleObjectError):
        second["Type"]


def test_direct_handles_go_stale_when_their_container_is_replaced() -> None:
    doc = _two_pages()
    resources = doc.catalog["Pages"]["Resources"]
    assert not resources.is_indirect

    doc.catalog["Pages"]["Resources"] = 0

    with pytest.raises(pylopdf.StaleObjectError):
        len(resources)


def test_handles_from_another_document_are_refused() -> None:
    doc = _two_pages()
    other = _two_pages()

    with pytest.raises(ValueError, match="another document"):
        doc.catalog["Other"] = other.catalog


def test_unsupported_values_and_depth_are_refused() -> None:
    doc = pylopdf.open()
    nested: list[object] = []
    deep = nested
    for _ in range(40):
        inner: list[object] = []
        deep.append(inner)
        deep = inner

    with pytest.raises(TypeError, match="cannot write"):
        doc.catalog["Bad"] = object()
    with pytest.raises(ValueError, match="finite"):
        doc.catalog["Bad"] = float("nan")
    with pytest.raises(pylopdf.LimitError) as error:
        doc.catalog["Deep"] = nested
    assert error.value.code == "object_value_depth"
    assert "Bad" not in doc.catalog
    assert "Deep" not in doc.catalog
    cycle: list[object] = []
    cycle.append(cycle)
    with pytest.raises(pylopdf.LimitError):
        doc.catalog["Cycle"] = cycle


def test_handles_compare_and_hash_by_address() -> None:
    doc = _two_pages()

    assert doc.catalog == doc.objects[1]
    assert hash(doc.catalog) == hash(doc.objects[1])
    assert doc.catalog["Pages"]["Kids"] == doc.objects[2]["Kids"]
    assert doc.catalog != doc.objects[2]
    assert repr(doc.objects[2]["Kids"]) == "<Array 2 0 R/Kids>"
    assert repr(doc.trailer) == "<Dictionary trailer>"


def test_closed_and_encrypted_documents_are_refused() -> None:
    doc = _two_pages()
    catalog = doc.catalog
    encrypted = pylopdf.open(stream=doc.tobytes(encryption=pylopdf.PDF_ENCRYPT_AES_256, user_pw="secret"))
    doc.close()

    with pytest.raises(pylopdf.DocumentClosedError):
        catalog["Type"]
    with pytest.raises(pylopdf.EncryptedDocumentError):
        encrypted.objects  # noqa: B018 - property access is the operation under test.


def test_objects_are_readable_after_authentication() -> None:
    encrypted = pylopdf.open(
        stream=_two_pages().tobytes(encryption=pylopdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner")
    )
    with pytest.raises(pylopdf.EncryptedDocumentError):
        encrypted.catalog  # noqa: B018 - property access is the operation under test.

    assert encrypted.authenticate("user") == 2
    assert encrypted.catalog["Pages"]["Count"] == 2


def test_real_world_object_graph_reads() -> None:
    doc = pylopdf.open(REAL_WORLD / "f1040.pdf")
    fonts = 0
    for xref in doc.objects:
        value = doc.objects[xref]
        if isinstance(value, Dictionary) and value.get("Type") == "Font":
            fonts += 1
            assert isinstance(value["BaseFont"], Name)
    assert fonts > 0
    form = doc.catalog["AcroForm"]
    assert len(form["Fields"]) == len({field.xref for field in form["Fields"]})
