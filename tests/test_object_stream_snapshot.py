"""Read documents with very large object streams from their serialized graph.

hayro-syntax parses the member table of an object stream, the pairs of object
number and offset that head it, again for every lookup of a member. One
container of N members therefore costs N table entries per lookup, and a page
with thousands of annotations that are members of the same container is
quadratic twice: in hayro's interpretation and in the content guard's pass over
the same `/Annots` array. Serializing lopdf's graph writes every member as an
ordinary object, so a document with a container of thousands of members is read
from its serialization when that costs far less than rebuilding the tables, and
the first interpretation falls back to the original bytes when the
serialization is refused.
"""

from __future__ import annotations

import hashlib
import time
import zlib

import pytest

import pylopdf

# One container of 4,096 members is the smallest that moves hayro to the serialized graph.
THRESHOLD = 4_096
FONT = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"


def _object_stream_pdf(  # noqa: PLR0913 - each keyword shapes one part of the fixture.
    members: int,
    *,
    annotations: int = 0,
    containers: int = 1,
    text: str = "Hello",
    appearance: bool = False,
    poison_last: bool = False,
    padding: int = 0,
) -> bytes:
    """Build a page whose annotations are the first members of the object streams.

    `members` dictionaries are spread over `containers` object streams: `annotations` annotations, then inert
    fillers. With `appearance` every annotation draws one shared form, and `poison_last` gives the last one a form
    that nests 100 arrays, which the content guard refuses as soon as it has read every annotation. `padding` appends
    an uncompressed stream of that many bytes to make the file larger without adding members.
    """
    catalog, pages, page, font, content, form, poisoned, filler_stream, xref_stream = range(1, 10)
    first_container = 10
    first = first_container + containers
    total = max(members, annotations)
    last = first + total - 1
    size = -(-total // containers)
    refs = " ".join(f"{first + index} 0 R" for index in range(annotations))
    bodies: list[bytes] = []
    for index in range(total):
        if index < annotations:
            target = poisoned if poison_last and index == annotations - 1 else form
            ap = f" /AP << /N {target} 0 R >>" if appearance else ""
            bodies.append(f"<< /Type /Annot /Subtype /Square /Rect [10 10 20 20]{ap} >>".encode())
        else:
            bodies.append(f"<< /Filler {index} >>".encode())

    out = bytearray(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}

    def add(number: int, data: bytes) -> None:
        offsets[number] = len(out)
        out.extend(f"{number} 0 obj\n".encode() + data + b"\nendobj\n")

    def add_stream(number: int, entries: str, data: bytes) -> None:
        add(number, f"<< {entries} /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream")

    add(catalog, f"<< /Type /Catalog /Pages {pages} 0 R >>".encode())
    add(pages, f"<< /Type /Pages /Kids [{page} 0 R] /Count 1 >>".encode())
    add(
        page,
        f"<< /Type /Page /Parent {pages} 0 R /MediaBox [0 0 300 300] /Contents {content} 0 R "
        f"/Resources << /Font << /F1 {font} 0 R >> >> /Annots [{refs}] >>".encode(),
    )
    add(font, FONT.encode())
    add_stream(content, "", f"BT /F1 24 Tf 20 200 Td ({text}) Tj ET".encode())
    square = "/Type /XObject /Subtype /Form /BBox [0 0 10 10]"
    add_stream(form, square, b"0 0 10 10 re f")
    add_stream(poisoned, square, b"[" * 100 + b"]" * 100)
    add_stream(filler_stream, "", b"\x00" * padding)
    for container in range(containers):
        start = container * size
        header = bytearray()
        body = bytearray()
        for index in range(start, min(start + size, total)):
            header += f"{first + index} {len(body)} ".encode()
            body += bodies[index] + b"\n"
        count = len(header.split()) // 2
        add_stream(
            first_container + container,
            f"/Type /ObjStm /N {count} /First {len(header)} /Filter /FlateDecode",
            zlib.compress(bytes(header) + bytes(body)),
        )

    xref_at = len(out)
    offsets[xref_stream] = xref_at
    rows = bytearray(bytes([0]) + (0).to_bytes(4, "big") + (0xFFFF).to_bytes(3, "big"))
    for number in range(1, last + 1):
        if number in offsets:
            rows += bytes([1]) + offsets[number].to_bytes(4, "big") + (0).to_bytes(3, "big")
        else:
            index = number - first
            rows += (
                bytes([2]) + (first_container + index // size).to_bytes(4, "big") + (index % size).to_bytes(3, "big")
            )
    table = zlib.compress(bytes(rows))
    out.extend(
        f"{xref_stream} 0 obj\n<< /Type /XRef /Size {last + 1} /W [1 4 3] /Root {catalog} 0 R "
        f"/Filter /FlateDecode /Length {len(table)} >>\nstream\n".encode()
        + table
        + b"\nendstream\nendobj\n"
        + f"startxref\n{xref_at}\n%%EOF\n".encode()
    )
    return bytes(out)


def _source(data: bytes, **kwargs: object) -> str:
    """Open `data`, extract the first page, and report which input hayro parsed."""
    doc = pylopdf.open(data, **kwargs)  # ty: ignore[invalid-argument-type]
    doc.get_text(pages=[0])
    return doc._doc.snapshot_source()  # noqa: SLF001


def test_a_container_of_thousands_of_members_reads_from_the_serialized_graph() -> None:
    assert _source(_object_stream_pdf(THRESHOLD, annotations=20)) == "serialized"
    assert _source(_object_stream_pdf(3 * THRESHOLD, annotations=20)) == "serialized"


def test_a_smaller_container_keeps_the_original_bytes() -> None:
    assert _source(_object_stream_pdf(THRESHOLD - 1, annotations=20)) == "original"
    assert _source(_object_stream_pdf(100, annotations=20)) == "original"


def test_many_containers_of_hundreds_of_members_keep_the_original_bytes() -> None:
    """A hundred containers of 500 members rebuild as many table entries as one of 5,000, at no measurable cost."""
    data = _object_stream_pdf(50_000, annotations=20, containers=100)
    assert _source(data) == "original"


def test_a_file_larger_than_the_rebuilt_tables_keeps_the_original_bytes() -> None:
    """Serializing costs time per input byte, so a large file with a modest container stays as it is."""
    boundary = THRESHOLD**2 // 8
    base = len(_object_stream_pdf(THRESHOLD, annotations=20))
    assert _source(_object_stream_pdf(THRESHOLD, annotations=20, padding=boundary - base + 10_000)) == "original"
    assert _source(_object_stream_pdf(THRESHOLD, annotations=20, padding=boundary - base - 10_000)) == "serialized"


def test_nothing_is_read_before_the_first_interpretation_and_after_an_edit() -> None:
    doc = pylopdf.open(_object_stream_pdf(THRESHOLD, annotations=20))
    assert doc._doc.snapshot_source() == "none"  # noqa: SLF001
    doc.get_text(pages=[0])
    assert doc._doc.snapshot_source() == "serialized"  # noqa: SLF001
    doc.set_metadata({"title": "Edited"})
    assert doc._doc.snapshot_source() == "none"  # noqa: SLF001
    doc.get_text(pages=[0])
    assert doc._doc.snapshot_source() == "serialized"  # noqa: SLF001


def _views(doc: pylopdf.Document) -> tuple[str, str, str, str]:
    page = doc.pages[0]
    return (
        doc.get_text(),
        repr(page.get_text("words")),
        hashlib.sha256(page.get_pixmap(matrix=pylopdf.Matrix(0.5, 0.5), alpha=True).samples).hexdigest(),
        repr(page.get_drawings()),
    )


def test_the_serialized_graph_gives_the_views_of_the_original_bytes() -> None:
    """Both snapshots describe the same document, so every reader agrees on a page that draws text and annotations."""
    original = pylopdf.open(_object_stream_pdf(300, annotations=250, appearance=True))
    serialized = pylopdf.open(_object_stream_pdf(2 * THRESHOLD, annotations=250, appearance=True))
    assert _views(original) == _views(serialized)
    assert original._doc.snapshot_source() == "original"  # noqa: SLF001
    assert serialized._doc.snapshot_source() == "serialized"  # noqa: SLF001
    assert "Hello" in serialized.get_text()


def test_the_original_bytes_remain_when_the_serialization_exceeds_the_interpretation_limit() -> None:
    """The serialization writes every member as an ordinary object, which is larger than the compressed container."""
    data = _object_stream_pdf(2 * THRESHOLD, annotations=20)
    assert _source(data, limits=pylopdf.DocumentLimits(max_interpretation_size=len(data))) == "original"
    assert _source(data, limits=pylopdf.DocumentLimits(max_interpretation_size=4 * len(data))) == "original"
    assert _source(data, limits=pylopdf.DocumentLimits(max_interpretation_size=40 * len(data))) == "serialized"


def test_a_page_of_thousands_of_annotations_in_one_container_is_not_quadratic() -> None:
    """Rebuilding the member table for every annotation took 4 s here, for hayro and again for the content guard."""
    doc = pylopdf.open(_object_stream_pdf(12_000, annotations=12_000, appearance=True))
    started = time.perf_counter()
    doc.get_text(pages=[0])
    assert time.perf_counter() - started < 2
    assert doc._doc.snapshot_source() == "serialized"  # noqa: SLF001


def test_the_content_guard_reads_every_annotation_from_the_serialized_graph() -> None:
    """A refusal at the last annotation proves the guard read all of them, and costs nothing else.

    Poisoning the last appearance makes the guard refuse the page before hayro interprets anything, so the time to
    refuse is the time of the guard's pass over `/Annots` alone: 2 s here when every lookup rebuilds the table.
    """
    doc = pylopdf.open(_object_stream_pdf(12_000, annotations=12_000, appearance=True, poison_last=True))
    started = time.perf_counter()
    with pytest.raises(pylopdf.LimitError) as refusal:
        doc.get_text(pages=[0])
    assert time.perf_counter() - started < 1
    assert refusal.value.code == "content_nesting_depth"
    assert doc._doc.snapshot_source() == "serialized"  # noqa: SLF001
