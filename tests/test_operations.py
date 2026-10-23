"""Operator-level content: Page.operations and Operations.

Operations are tokenized as hayro tokenizes them and keep their bytes, so
reading and writing back every content stream of the corpus changes nothing,
and an edit rewrites only what it changes.
"""

from __future__ import annotations

import zlib
from pathlib import Path

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import InlineImage, Name, Operation, Operations, String

REAL_WORLD = Path(__file__).parent / "assets" / "real_world"
CORPUS = sorted(REAL_WORLD.glob("*.pdf"))


def _page(content: bytes, resources: str = "<< /Font << /F1 4 0 R >> >>", *, filtered: bool = False) -> bytes:
    data = zlib.compress(content) if filtered else content
    filter_entry = b" /Filter /FlateDecode" if filtered else b""
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 300 300] >>",
        3: f"<< /Type /Page /Parent 2 0 R /Resources {resources} /Contents 5 0 R >>",
        4: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        5: b"<< /Length %d%s >>\nstream\n%s\nendstream" % (len(data), filter_entry, data),
    }
    return build_raw_pdf(objects)


def _content_streams(doc: pylopdf.Document) -> list[bytes]:
    """Return the decoded data of every page content stream and Form XObject."""
    streams = []
    for xref in doc.objects:
        value = doc.objects[xref]
        if isinstance(value, pylopdf.Stream) and value.get("Subtype") == "Form":
            streams.append(value.read(max_size=None))
    for page in doc.pages:
        contents = page.obj.get("Contents")
        if isinstance(contents, pylopdf.Stream):
            streams.append(contents.read(max_size=None))
        elif isinstance(contents, pylopdf.Array):
            streams.extend(item.read(max_size=None) for item in contents if isinstance(item, pylopdf.Stream))
    return streams


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.name)
def test_every_corpus_content_stream_round_trips(path: Path) -> None:
    doc = pylopdf.open(path)
    streams = _content_streams(doc)
    assert streams
    for data in streams:
        operations = Operations.parse(data, max_operations=None, max_size=None)
        assert operations.to_bytes() == data


@pytest.mark.filterwarnings("ignore::pylopdf.PylopdfWarning")
@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.name)
def test_writing_back_every_corpus_page_keeps_text_and_pixels(path: Path) -> None:
    doc = pylopdf.open(path)
    pages = range(min(len(doc.pages), 3))
    before = [(doc.pages[pno].text(), bytes(doc.pages[pno].pixmap(dpi=36))) for pno in pages]
    for pno in pages:
        page = doc.pages[pno]
        operations = page.operations
        page.operations = operations
        assert page.operations == operations
    reopened = pylopdf.open(doc.to_bytes())
    after = [(reopened.pages[pno].text(), bytes(reopened.pages[pno].pixmap(dpi=36))) for pno in pages]
    assert after == before


def test_operations_hold_hayro_values() -> None:
    content = b"q 1 0 0 1 10.5 -2 cm BT /F1 12 Tf [(A) -120 <42>] TJ ET Q /P <</MCID 3 /Lang (en)>> BDC EMC"
    operations = Operations.parse(content)

    assert [operation.operator for operation in operations] == [
        "q", "cm", "BT", "Tf", "TJ", "ET", "Q", "BDC", "EMC",
    ]  # fmt: skip
    assert operations[1].operands == (1, 0, 0, 1, 10.5, -2)
    assert isinstance(operations[1].operands[0], int)
    assert isinstance(operations[1].operands[4], float)
    assert operations[3].operands == (Name("F1"), 12)
    assert isinstance(operations[3].operands[0], Name)
    assert operations[4].operands == ((String(b"A"), -120, String(b"B")),)
    assert operations[7].operands == (Name("P"), {"MCID": 3, "Lang": String(b"en")})
    assert operations.to_bytes() == content


def test_comments_and_white_space_stay_with_the_operation_that_follows() -> None:
    content = b"% header\nq\n  % inside\n  0 0 m\n\nQ\n% end\n"
    operations = Operations.parse(content)

    assert [operation.operator for operation in operations] == ["q", "m", "Q"]
    assert operations.tail == b"\n% end\n"
    assert operations.to_bytes() == content
    assert Operations([operations[0], operations[2]], tail=operations.tail).to_bytes() == b"% header\nq\n\nQ\n% end\n"


def test_inline_images_keep_their_dictionary_and_data() -> None:
    data = bytes(range(256))[:12]
    content = b"q 12 0 0 1 0 0 cm BI /W 4 /H 1 /BPC 8 /CS /RGB ID " + data + b" EI Q"
    operations = Operations.parse(content)

    image = operations[2]
    assert isinstance(image, InlineImage)
    assert image.dictionary == {"W": 4, "H": 1, "BPC": 8, "CS": Name("RGB")}
    assert image.data == data
    assert operations[3].operator == "Q"
    assert operations.to_bytes() == content


def test_filtered_inline_images_are_read() -> None:
    # lopdf's strict parse refuses filtered inline images; hayro reads them.
    encoded = b'8;Z]"!!~>'
    content = b"BI /W 1 /H 1 /BPC 8 /CS /G /F /A85 ID " + encoded + b"\nEI Q"
    operations = Operations.parse(content)

    assert isinstance(operations[0], InlineImage)
    assert operations[0].dictionary["F"] == Name("A85")
    assert operations[1].operator == "Q"


def test_new_operations_are_written_from_their_values() -> None:
    operations = Operations([
        Operation("q"),
        Operation("cm", (1, 0, 0, 1, 0.25, -1e-7)),
        Operation("Tf", (Name("F 1#"), 9.5)),
        Operation("Tj", (String(b"a(b)\\c\n\xff"),)),
        Operation("TJ", ([String(b"x"), -250.0, True, None],)),
        Operation("BDC", (Name("Span"), {"ActualText": "é"})),
        InlineImage({"W": 1, "H": 1, "BPC": 8, "CS": Name("G")}, b"\x80"),
        Operation("Q"),
    ])  # fmt: skip
    data = operations.to_bytes()

    assert b"0.25 -0.0000001 cm" in data
    assert b"/F#201#23 9.5 Tf" in data
    assert b"(a\\(b\\)\\\\c\\012\\377) Tj" in data
    assert b"[(x) -250 true null] TJ" in data
    reread = Operations.parse(data)
    # A plain str is written as a text string, which reads back as a String.
    expected = [
        *operations[:4],
        Operation("TJ", ((String(b"x"), -250, True, None),)),
        Operation("BDC", (Name("Span"), {"ActualText": String(b"\xfe\xff\x00\xe9")})),
        *operations[6:],
    ]
    assert reread == Operations(expected)


def test_operations_are_values() -> None:
    operation = Operation("re", [0, 0, 10, 10])  # ty: ignore[invalid-argument-type]

    assert operation.operands == (0, 0, 10, 10)
    assert operation == Operation("re", (0, 0, 10, 10))
    assert hash(operation) == hash(Operation("re", (0, 0, 10, 10)))
    with pytest.raises(AttributeError):
        operation.operator = "m"  # ty: ignore[invalid-assignment]
    assert Operations.parse(b"0 0 m")[0] == Operation("m", (0, 0))


@pytest.mark.parametrize(
    ("operator", "operands", "error"),
    [
        ("", (), ValueError),
        ("T j", (), ValueError),
        ("/Tj", (), ValueError),
        ("BI", (), ValueError),
        (b"Tj", (), TypeError),
        ("Tj", "text", TypeError),
        ("re", (float("nan"),), ValueError),
        ("Do", (object(),), TypeError),
        ("BDC", ({1: 2},), TypeError),
    ],
)
def test_operations_refuse_what_cannot_be_written(operator: object, operands: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        Operation(operator, operands)  # ty: ignore[invalid-argument-type]


def test_deep_operands_are_refused() -> None:
    value: object = 1
    for _ in range(65):
        value = [value]
    with pytest.raises(pylopdf.LimitError) as error:
        Operation("Do", (value,))
    assert error.value.code == "object_value_depth"


def test_content_after_what_hayro_reads_stays_in_the_tail() -> None:
    # hayro reads at most ten operands and ignores the rest of the stream.
    content = b"q 1 2 3 4 5 6 7 8 9 10 11 sc Q"
    operations = Operations.parse(content)

    assert [operation.operator for operation in operations] == ["q"]
    assert operations.tail == b" 1 2 3 4 5 6 7 8 9 10 11 sc Q"
    assert operations.to_bytes() == content


def test_parse_limits() -> None:
    with pytest.raises(pylopdf.LimitError) as error:
        Operations.parse(b"q Q q Q", max_operations=3)
    assert error.value.code == "content_operation_count"
    assert len(Operations.parse(b"q Q q Q", max_operations=4)) == 4
    with pytest.raises(pylopdf.LimitError) as error:
        Operations.parse(b"q Q", max_size=2)
    assert error.value.code == "content_input_size"
    with pytest.raises(ValueError, match="max_size"):
        Operations.parse(b"q", max_size=0)


def test_assigning_operations_edits_the_page() -> None:
    doc = pylopdf.open(_page(b"BT /F1 12 Tf 20 250 Td (Keep) Tj 0 -20 Td (Drop) Tj ET", filtered=True))
    page = doc.pages[0]
    operations = page.operations
    page.operations = [operation for operation in operations if operation.operands != (String(b"Drop"),)]

    assert page.text().split() == ["Keep"]
    assert page.obj["Contents"].read() == b"BT /F1 12 Tf 20 250 Td (Keep) Tj 0 -20 Td ET"
    reopened = pylopdf.open(doc.to_bytes())
    assert reopened.pages[0].text().split() == ["Keep"]


def test_new_operations_draw_on_the_page() -> None:
    doc = pylopdf.open(_page(b""))
    page = doc.pages[0]
    page.operations = [
        Operation("BT"),
        Operation("Tf", (Name("F1"), 20)),
        Operation("Td", (50, 150)),
        Operation("Tj", (String(b"Drawn"),)),
        Operation("ET"),
    ]

    assert page.text().strip() == "Drawn"


def test_page_operations_join_streams_as_hayro_does() -> None:
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 300 300] >>",
        3: "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /Contents [5 0 R 6 0 R 7 0 R] >>",
        4: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        5: "<< /Length 21 >>\nstream\nBT /F1 12 Tf 9 9 Td (\nendstream",
        6: "<< /Length 7 >>\nstream\nA) Tj ET\nendstream",
        # hayro stops at the first entry that is not a stream.
        7: "<< /Type /NotAStream >>",
    }
    doc = pylopdf.open(build_raw_pdf(objects))
    operations = doc.pages[0].operations

    assert operations.to_bytes() == b"BT /F1 12 Tf 9 9 Td ( A) Tj ET "
    assert operations[3] == Operation("Tj", (String(b" A"),))


def test_assigning_a_shared_content_stream_leaves_other_pages() -> None:
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 /MediaBox [0 0 300 300] >>",
        3: "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        4: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        5: "<< /Length 33 >>\nstream\nBT /F1 12 Tf 20 250 Td (Hi) Tj ET\nendstream",
        6: "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    }
    doc = pylopdf.open(build_raw_pdf(objects))
    doc.pages[0].operations = []

    assert doc.pages[0].text().strip() == ""
    assert doc.pages[1].text().strip() == "Hi"


def test_page_operations_refuse_wrong_values() -> None:
    doc = pylopdf.open(_page(b"q Q"))
    with pytest.raises(TypeError):
        doc.pages[0].operations = b"q Q"  # ty: ignore[invalid-assignment]
    with pytest.raises(TypeError):
        doc.pages[0].operations = [Operation("q"), "Q"]  # ty: ignore[invalid-assignment]
    assert doc.pages[0].operations.to_bytes() == b"q Q"


def test_a_stale_page_refuses_operations() -> None:
    doc = pylopdf.open(_page(b"q Q"))
    page = doc.pages[0]
    del doc.pages[0:0]
    doc.pages.insert(0, doc.pages[0])
    with pytest.raises(pylopdf.StalePageError):
        page.operations  # noqa: B018 - the property raises.
