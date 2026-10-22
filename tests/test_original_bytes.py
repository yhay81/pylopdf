"""Render and extract the same document before and after an unrelated edit.

An unedited document renders and extracts from its original bytes through
hayro, while edits and saves serialize lopdf's object graph. Loading keeps the
original bytes only when every reference reachable from the trailer resolves
to the same object in both views, and every view must list the page-tree
walker's pages, so malformed input no longer changes after any edit.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import zlib
from pathlib import Path

import pytest
from conftest import build_pdf, with_xref_entry_offset

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"
FONT = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"


def _stream(text: str, y: int = 100) -> str:
    content = f"BT /F1 20 Tf 20 {y} Td ({text}) Tj ET"
    return f"<< /Length {len(content)} >>\nstream\n{content}\nendstream"


def _pdf(bodies: dict[int, str | None], *, headers: dict[int, int] | None = None) -> bytes:
    """Build an xref-table PDF.

    A `None` body is a free entry, and `headers` writes another object number
    into the header of the object that an entry locates.
    """
    headers = headers or {}
    out = bytearray(b"%PDF-1.7\n")
    offsets: dict[int, int] = {}
    for number, body in sorted(bodies.items()):
        if body is None:
            continue
        offsets[number] = len(out)
        out += f"{headers.get(number, number)} 0 obj\n{body}\nendobj\n".encode("latin-1")
    size = max(bodies) + 1
    xref = len(out)
    out += f"xref\n0 {size}\n".encode()
    for number in range(size):
        out += f"{offsets[number]:010d} 00000 n \n".encode() if number in offsets else b"0000000000 65535 f \n"
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(out)


def _one_page(
    contents: str, objects: dict[int, str | None], resources: str = "", font: str = "4 0 R"
) -> dict[int, str | None]:
    """Return objects 1-4 of a one-page document plus `objects`."""
    return {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 300 300] "
        f"/Resources << /Font << /F1 {font} >> {resources}>> >>",
        3: f"<< /Type /Page /Parent 2 0 R /Contents {contents} >>",
        4: FONT,
        **objects,
    }


def _object_stream(members: dict[int, str]) -> str:
    """Return an uncompressed object stream holding `members`."""
    offsets: list[str] = []
    data = ""
    for number, body in members.items():
        offsets.append(f"{number} {len(data)}")
        data += f"{body}\n"
    header = " ".join(offsets) + "\n"
    payload = header + data
    return (
        f"<< /Type /ObjStm /N {len(members)} /First {len(header)} /Length {len(payload)} >>\n"
        f"stream\n{payload}\nendstream"
    )


def _keeps_original_bytes(data: bytes) -> bool:
    """Whether loading kept the original input for the first interpretation.

    A budget one byte below the input refuses the original bytes by their own
    size; a serialized snapshot either fits or fails with another message.
    """
    limits = pylopdf.DocumentLimits(max_interpretation_size=len(data) - 1)
    try:
        pylopdf.open(data, limits=limits).get_text()
    except pylopdf.LimitError as error:
        return "rendering and extraction source is" in str(error)
    return False


def _page_views(doc: pylopdf.Document) -> list[tuple[str, str]]:
    return [
        (
            doc.pages[index].get_text(),
            hashlib.sha256(
                doc.pages[index].get_pixmap(matrix=pylopdf.Matrix(0.25, 0.25), alpha=True).samples
            ).hexdigest(),
        )
        for index in range(len(doc.pages))
    ]


def _consistent_page_texts(data: bytes) -> list[list[str]]:
    """Return each page's lines after checking that an edit and a save change nothing."""
    doc = pylopdf.open(data)
    unedited = _page_views(doc)
    doc.set_metadata({"title": "Edited"})
    assert _page_views(doc) == unedited
    assert _page_views(pylopdf.open(doc.tobytes())) == unedited
    return [text.splitlines() for text, _ in unedited]


def _with_revision(data: bytes, number: int, body: str) -> bytes:
    """Append an incremental update that replaces one object."""
    previous = re.findall(rb"startxref\s+(\d+)", data)[-1].decode()
    size = re.findall(rb"/Size (\d+)", data)[-1].decode()
    out = bytearray(data + b"\n")
    offset = len(out)
    out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += (
        f"xref\n0 1\n0000000000 65535 f \n{number} 1\n{offset:010d} 00000 n \n"
        f"trailer\n<< /Size {size} /Root 1 0 R /Prev {previous} >>\nstartxref\n{xref}\n%%EOF"
    ).encode()
    return bytes(out)


@pytest.mark.parametrize(
    "name",
    ["f1040.pdf", "usrguide.pdf", "pdf20-simple.pdf", "bill-hr815.pdf", "patent-us223898.pdf"],
)
def test_well_formed_input_keeps_original_bytes(name: str) -> None:
    """Classic tables, cross-reference streams, and object streams all match."""
    assert _keeps_original_bytes((ASSETS / name).read_bytes())


def test_incremental_update_keeps_original_bytes() -> None:
    base = build_pdf(["Original text"])
    updated = _with_revision(base, 5, _stream("Updated text", 720))

    assert _keeps_original_bytes(updated)
    assert _consistent_page_texts(updated) == [["Updated text"]]


def test_object_under_a_mismatched_header_renders_from_the_graph() -> None:
    """Entry 6 locates `9 0 obj`, which the page draws.

    lopdf stores the object as 9; hayro read 9 as null until something read
    entry 6. The page used to gain the text after any edit.
    """
    data = _pdf(
        _one_page("[5 0 R 9 0 R]", {5: _stream("TARGET PAGE"), 6: _stream("NINE VIA ENTRY SIX", 200)}),
        headers={6: 9},
    )

    assert not _keeps_original_bytes(data)
    assert _consistent_page_texts(data) == [["NINE VIA ENTRY SIX", "TARGET PAGE"]]


def test_unreachable_object_under_a_mismatched_header_is_dropped() -> None:
    """Nothing reaches object 9 or entry 6, so both views read the page alike."""
    data = _pdf(
        _one_page("5 0 R", {5: _stream("TARGET PAGE"), 6: _stream("UNREACHED", 200)}),
        headers={6: 9},
    )

    assert _keeps_original_bytes(data)
    doc = pylopdf.open(data)
    assert b"UNREACHED" not in doc.tobytes()
    assert _consistent_page_texts(data) == [["TARGET PAGE"]]


@pytest.mark.parametrize(
    ("contents", "objects", "headers"),
    [
        pytest.param("[5 0 R 6 0 R]", {6: _stream("NINE", 200)}, {6: 9}, id="entry-with-another-header"),
        pytest.param("[5 0 R 6 0 R]", {6: "<< /Kind (unterminated >>"}, {}, id="unparseable"),
        pytest.param("[5 0 R 6 0 R]", {6: "<< /Kind 1.2.3 >>"}, {}, id="malformed-number"),
    ],
)
def test_reachable_entry_that_lopdf_could_not_load_renders_from_the_graph(
    contents: str, objects: dict[int, str | None], headers: dict[int, int]
) -> None:
    """Render from the graph when the page reaches an entry lopdf could not load.

    Reading the entry, hayro would parse it on its own or rebuild its whole
    table. Renderers stop at the first content entry that is not a stream, so
    the page shows only the stream before the missing object.
    """
    data = _pdf(_one_page(contents, {5: _stream("TARGET PAGE"), **objects}), headers=headers)

    assert not _keeps_original_bytes(data)
    assert _consistent_page_texts(data) == [["TARGET PAGE"]]


def test_form_with_a_bare_keyword_survives_edits_and_saves() -> None:
    """Load a form that spells `/Type XObject` with the value read as null, as hayro reads it."""
    page = "BT /F1 20 Tf 20 100 Td (PAGE TEXT) Tj ET q /Fm0 Do Q"
    form = "BT /F1 20 Tf 20 200 Td (FORM TEXT) Tj ET"
    data = _pdf(
        _one_page(
            "5 0 R",
            {
                5: f"<< /Length {len(page)} >>\nstream\n{page}\nendstream",
                6: "<< /Type XObject /Subtype /Form /BBox [0 0 300 300] "
                f"/Resources << /Font << /F1 4 0 R >> >> /Length {len(form)} >>\nstream\n{form}\nendstream",
            },
            resources="/XObject << /Fm0 6 0 R >> ",
        )
    )

    assert not _keeps_original_bytes(data)
    assert _consistent_page_texts(data) == [["FORM TEXT", "PAGE TEXT"]]
    doc = pylopdf.open(data)
    for index in range(3):
        doc.pages[0].insert_text((20, 30 + 20 * index), f"STAMP {index}")
    saved = doc.tobytes()
    assert re.search(rb"/Type null\s*/Subtype\s*/Form", saved)
    assert sorted(pylopdf.open(saved).pages[0].get_text().splitlines()) == [
        "FORM TEXT",
        "PAGE TEXT",
        "STAMP 0",
        "STAMP 1",
        "STAMP 2",
    ]


@pytest.mark.parametrize(
    ("body", "saved_entries"),
    [
        pytest.param(
            "<< /Length {length} /Note Foo /Array [Bar 1 -2.5 true] >>",
            rb"/Note null/Array\[null 1 -2\.5 true\]",
            id="bare-keywords",
        ),
        pytest.param(
            "<< /Length {length} /Note Foo /Text (a\\(b\\) \\101\\\n(nested)) /Hex <4142 3> /N#61me /x >>",
            rb"/Text\(a\(b\) A\(nested\)\)/Hex<414230>/Name/x",
            id="strings-and-names",
        ),
        pytest.param(
            "<< /Length 8 0 R /Note Foo >>",
            rb"/Note null",
            id="indirect-length",
        ),
        pytest.param(
            "<< /Length 999 % wrong\n /Note Foo >>",
            rb"/Note null",
            id="sole-endstream",
        ),
    ],
)
def test_skipped_stream_is_recovered(body: str, saved_entries: bytes) -> None:
    content = "BT /F1 20 Tf 20 100 Td (RECOVERED) Tj ET"
    data = _pdf(
        _one_page(
            "5 0 R",
            {
                5: body.format(length=len(content)) + f"\nstream\n{content}\nendstream",
                6: None,
                7: None,
                8: str(len(content)),
            },
        )
    )

    assert _consistent_page_texts(data) == [["RECOVERED"]]
    saved = pylopdf.open(data).tobytes()
    assert re.search(saved_entries, saved), saved
    assert saved.count(b"RECOVERED") == 1


def test_unreachable_recovered_object_keeps_original_bytes() -> None:
    data = _pdf(_one_page("5 0 R", {5: _stream("TARGET PAGE"), 6: "<< /Type Unused >>"}))

    assert _keeps_original_bytes(data)
    assert re.search(rb"6 0 obj\s*<</Type null>>", pylopdf.open(data).tobytes())


CONTENT = "BT /F1 20 Tf 20 100 Td (NO USABLE LENGTH) Tj ET"


@pytest.mark.parametrize(
    ("length", "objects"),
    [
        pytest.param("", {}, id="missing"),
        pytest.param("/Length /Unknown", {}, id="name"),
        pytest.param("/Length 8 0 R", {}, id="unlisted-object"),
        pytest.param("/Length 8 0 R", {8: "/NotANumber"}, id="object-without-a-number"),
        pytest.param("/Length 8 0 R", {8: f"{len(CONTENT)}.0"}, id="indirect-real"),
        pytest.param(f"/Length {len(CONTENT)}.", {}, id="real"),
        pytest.param(f"/Length {len(CONTENT) + 1}.5", {}, id="fractional-real"),
        pytest.param(f"/Length {len(CONTENT) - 9}.", {}, id="real-too-short"),
    ],
)
def test_stream_without_a_usable_length_keeps_the_data_that_hayro_reads(
    length: str, objects: dict[int, str | None]
) -> None:
    """Hold the data that hayro reads for a stream whose `/Length` lopdf cannot read while parsing.

    lopdf loaded such a stream without data, or took the declared number of bytes without looking for
    `endstream`, while hayro reads the declared bytes only when `endstream` follows them and otherwise
    searches for it. The page lost its text, or part of it, after any edit or save (pdf.js
    `issue1293r.pdf` has no `/Length` at all).
    """
    data = _pdf(_one_page("5 0 R", {5: f"<< {length} >>\nstream\n{CONTENT}\nendstream ", **objects}))

    assert _keeps_original_bytes(data)
    assert _consistent_page_texts(data) == [["NO USABLE LENGTH"]]
    saved = pylopdf.open(data).tobytes()
    assert saved.count(b"NO USABLE LENGTH") == 1
    assert re.search(rb"<</Length \d+>>stream\n", saved)


NEXT_OBJECT = _stream("NEXT OBJECT", 200)
# The declared bytes of object 5 then end where `endstream` follows in object 6.
SPANNING_LENGTH = len(f"{CONTENT}\n\nendobj\n6 0 obj\n{NEXT_OBJECT}") - len("\nendstream")


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(f"<< >>\nstream\n{CONTENT}\n", id="endstream-in-the-next-object"),
        pytest.param(f"<< >>\nstream \n{CONTENT}\nendstream", id="space-before-the-end-of-line"),
        pytest.param(f"<< /Length {SPANNING_LENGTH}.5 >>\nstream\n{CONTENT}\n", id="length-past-the-object"),
    ],
)
def test_stream_that_hayro_reads_past_its_object_renders_from_the_graph(body: str) -> None:
    """Render from the graph when hayro would read a stream's data past its object, or no stream.

    hayro searches for `endstream` to the end of the file, takes data only after an end-of-line
    marker that follows `stream` directly, and takes the declared bytes past the next object when
    `endstream` follows them there. lopdf holds no data for these streams, so the original page
    showed more than any edit or save kept, or less.
    """
    data = _pdf(_one_page("[5 0 R 6 0 R]", {5: body, 6: NEXT_OBJECT}))

    assert not _keeps_original_bytes(data)
    assert _consistent_page_texts(data) == [["NEXT OBJECT"]]


@pytest.mark.parametrize("declared", [len(CONTENT) - 9, len(CONTENT) + 9], ids=["short", "long"])
def test_stream_with_an_entry_pointing_into_its_data_is_recovered(declared: int) -> None:
    """Recover a page's stream whose wrong `/Length` hides `endstream` behind another entry's offset.

    Entry 6 points into the data of object 5, whose declared bytes `endstream` does not follow.
    lopdf searched for `endstream` only up to the next entry offset and skipped the object, as did
    the recovery parse, while hayro reads up to the first `endstream`: the page lost its text in
    every view (pdf.js `issue11678.pdf`, page 2). An entry offset where no object header starts
    no longer ends an object, and the recovery parse takes the first `endstream`, as hayro does,
    when object 6's `endstream` follows as well.
    """
    data = _pdf(
        _one_page(
            "[5 0 R 6 0 R]",
            {5: f"<< /Length {declared} >>\nstream\n{CONTENT}\nendstream", 6: NEXT_OBJECT},
        )
    )
    data = with_xref_entry_offset(data, 6, data.index(CONTENT.encode()) + 10)

    assert not _keeps_original_bytes(data)
    assert _consistent_page_texts(data) == [["NO USABLE LENGTH"]]
    saved = pylopdf.open(data).tobytes()
    assert saved.count(b"NO USABLE LENGTH") == 1
    assert b"NEXT OBJECT" not in saved


def test_stream_without_a_length_reads_past_an_entry_pointing_into_its_data() -> None:
    """Hold the data that hayro reads for a stream past an unreachable entry offset inside it.

    lopdf loads a stream without `/Length` with no data, and the stream used to count as one that
    hayro reads past its object because entry 6 points into its data. Nothing reaches entry 6, so
    hayro reads the same data from the original bytes.
    """
    data = _pdf(_one_page("5 0 R", {5: f"<< >>\nstream\n{CONTENT}\nendstream ", 6: "<< /Unused true >>"}))
    data = with_xref_entry_offset(data, 6, data.index(CONTENT.encode()) + 10)

    assert _keeps_original_bytes(data)
    assert _consistent_page_texts(data) == [["NO USABLE LENGTH"]]


def test_deeply_nested_object_that_lopdf_skips_never_reaches_hayro(tmp_path: Path) -> None:
    """Keep a deep object that lopdf skips away from hayro's parser.

    hayro 0.7 parsed the original object until the stack overflowed
    (LaurenzV/hayro#1347); hayro 0.8 reads no object nested more than 64
    levels deep. lopdf skips objects nested more than 100 levels deep, so the
    page renders from the serialized graph. A child process contains a crash.
    """
    content = "q /GS0 gs BT /F1 20 Tf 20 100 Td (TARGET PAGE) Tj ET Q"
    nested = "[" * 300_000 + "]" * 300_000
    path = tmp_path / "deep.pdf"
    path.write_bytes(
        _pdf(
            _one_page(
                "5 0 R",
                {
                    5: f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
                    6: f"<< /Type /ExtGState /Deep {nested} >>",
                },
                resources="/ExtGState << /GS0 6 0 R >> ",
            )
        )
    )
    script = "import sys, pylopdf; print(pylopdf.open(sys.argv[1]).pages[0].get_text(), end='')"

    completed = subprocess.run(  # noqa: S603  # Run this interpreter on a fixture written above.
        [sys.executable, "-c", script, str(path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split() == ["TARGET", "PAGE"]


def test_unlisted_object_stream_member_that_the_page_uses_renders_from_the_graph() -> None:
    """Render from the graph when the page uses a member that no entry lists.

    lopdf adds the member, while hayro reads its number as null.
    """
    data = _pdf(_one_page("5 0 R", {5: _stream("TARGET PAGE"), 6: _object_stream({7: FONT})}, font="7 0 R"))

    assert not _keeps_original_bytes(data)
    assert _consistent_page_texts(data) == [["TARGET PAGE"]]


def test_unreachable_object_stream_debris_is_dropped_with_its_numbers() -> None:
    """A damaged object stream can decode members under arbitrary numbers.

    lopdf raised the next object number over them, and its cross-reference
    writer runs in time linear in that number.
    """
    data = _pdf(
        _one_page(
            "5 0 R",
            {5: _stream("TARGET PAGE"), 6: _object_stream({4_000_000_000: "<< /Debris true >>", 3_000_000: "42"})},
        )
    )

    assert _keeps_original_bytes(data)
    doc = pylopdf.open(data)
    doc.pages[0].insert_text((20, 40), "STAMP")
    saved = doc.tobytes()
    assert b"Debris" not in saved
    assert max(int(number) for number in re.findall(rb"(\d+) 0 obj", saved)) < 20
    assert sorted(pylopdf.open(saved).pages[0].get_text().splitlines()) == ["STAMP", "TARGET PAGE"]


# The page draws `(abc)`, which a font that maps those codes to other glyphs extracts as `xyz`.
MAPPED_FONT = (
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding << /Type /Encoding /Differences [97 /x /y /z] >> >>"
)


def _compressed_pdf(  # noqa: PLR0913 - each keyword shapes one part of the fixture.
    members: list[tuple[int, str]],
    *,
    font: str = "4 0 R",
    slots: dict[int, int] | None = None,
    declared: int | None = None,
    note: str = "",
    flate: bool = False,
    objects: dict[int, str] | None = None,
    headers: dict[int, int] | None = None,
) -> bytes:
    """Build a page whose `members` share object stream 6, located by a cross-reference stream.

    Each member's entry names its first index in `members` unless `slots` gives another one,
    `declared` replaces the stream's `/N`, `note` follows the stream's table before `/First`,
    `objects` adds uncompressed objects, and `headers` writes another number into the header of an
    uncompressed object.
    """
    slots = slots or {}
    headers = headers or {}
    page = {number: body for number, body in _one_page("5 0 R", {5: _stream("abc")}, font=font).items() if body}
    uncompressed = {number: body for number, body in page.items() if number != 4} | (objects or {})
    out = bytearray(b"%PDF-1.7\n")
    offsets: dict[int, int] = {}
    for number, body in sorted(uncompressed.items()):
        offsets[number] = len(out)
        out += f"{headers.get(number, number)} 0 obj\n{body}\nendobj\n".encode("latin-1")
    table = "".join(
        f"{number} {sum(len(body) + 1 for _, body in members[:index])} " for index, (number, _) in enumerate(members)
    )
    table += note
    payload = (table + "".join(f"{body}\n" for _, body in members)).encode("latin-1")
    entries = f"/Type /ObjStm /N {len(members) if declared is None else declared} /First {len(table)}"
    if flate:
        payload = zlib.compress(payload)
        entries += " /Filter /FlateDecode"
    offsets[6] = len(out)
    out += f"6 0 obj\n<< {entries} /Length {len(payload)} >>\nstream\n".encode() + payload + b"\nendstream\nendobj\n"
    first_slots = {}
    for index, (number, _) in enumerate(members):
        first_slots.setdefault(number, index)
    xref = max([*offsets, *first_slots]) + 1
    offsets[xref] = len(out)
    rows = bytearray()
    for number in range(xref + 1):
        if number in offsets:
            rows += b"\x01" + offsets[number].to_bytes(4, "big") + b"\x00\x00"
        elif number in first_slots:
            rows += b"\x02" + (6).to_bytes(4, "big") + slots.get(number, first_slots[number]).to_bytes(2, "big")
        else:
            rows += b"\x00\x00\x00\x00\x00\xff\xff"
    dictionary = f"<< /Type /XRef /Size {xref + 1} /W [1 4 2] /Root 1 0 R /Length {len(rows)} >>"
    out += (
        f"{xref} 0 obj\n{dictionary}\nstream\n".encode()
        + rows
        + f"\nendstream\nendobj\nstartxref\n{offsets[xref]}\n%%EOF\n".encode()
    )
    return bytes(out)


@pytest.mark.parametrize("flate", [False, True], ids=["uncompressed", "flate"])
def test_members_in_the_slots_that_their_entries_name_keep_original_bytes(*, flate: bool) -> None:
    data = _compressed_pdf([(4, FONT), (7, MAPPED_FONT)], flate=flate)

    assert _keeps_original_bytes(data)
    assert _consistent_page_texts(data) == [["abc"]]


@pytest.mark.parametrize("flate", [False, True], ids=["uncompressed", "flate"])
@pytest.mark.parametrize(
    ("members", "font", "slots", "declared", "text"),
    [
        pytest.param([(4, FONT), (7, MAPPED_FONT)], "4 0 R", {4: 1, 7: 0}, None, "abc", id="swapped-slots"),
        pytest.param([(4, MAPPED_FONT), (4, FONT)], "4 0 R", None, None, "abc", id="number-listed-twice"),
        pytest.param([(4, FONT), (7, MAPPED_FONT)], "7 0 R", None, 1, "xyz", id="slot-past-n"),
    ],
)
def test_member_that_hayro_reads_from_another_slot_renders_from_the_graph(  # noqa: PLR0913 - one value per column.
    *,
    members: list[tuple[int, str]],
    font: str,
    slots: dict[int, int] | None,
    declared: int | None,
    text: str,
    flate: bool,
) -> None:
    """Render from the graph when hayro reads a page's font from another slot of its object stream.

    hayro reads the member at the index that the entry names, whatever number the stream's table
    gives it there, and reads none past `/N`; lopdf stores every member under its number in the
    table, keeping the last of two. The page extracted `abc` before any edit or save and `xyz`
    after, or the reverse.
    """
    data = _compressed_pdf(members, font=font, slots=slots, declared=declared, flate=flate)

    assert not _keeps_original_bytes(data)
    assert _consistent_page_texts(data) == [[text]]


def test_comment_after_the_member_table_keeps_original_bytes() -> None:
    """Keep the original bytes when a comment follows the member table, as qpdf's QDF output writes.

    hayro reads only the `/N` pairs, and lopdf skips the words of the comment, so both read the same
    members (pdf.js `issue14165.pdf`).
    """
    data = _compressed_pdf([(4, FONT), (7, MAPPED_FONT)], note="%% Object stream: object 4, index 0\n")

    assert _keeps_original_bytes(data)
    assert _consistent_page_texts(data) == [["abc"]]


def test_comment_that_lopdf_reads_as_a_member_renders_from_the_graph() -> None:
    """Render from the graph when lopdf reads a pair from the words of a comment after the table.

    lopdf splits the bytes before `/First` into words and pairs them in order. This comment gives
    member 4 the offset of member 7, and lopdf keeps the last pair for a number, while hayro reads
    only the `/N` pairs.
    """
    data = _compressed_pdf([(4, FONT), (7, MAPPED_FONT)], note=f"% x 4 {len(FONT) + 1}\n")

    assert not _keeps_original_bytes(data)
    assert _consistent_page_texts(data) == [["xyz"]]


def test_unreachable_member_in_another_slot_keeps_original_bytes() -> None:
    data = _compressed_pdf(
        [(4, FONT), (7, MAPPED_FONT), (8, "<< /Unused 8 >>")], font="9 0 R", slots={4: 1, 7: 0}, objects={9: FONT}
    )

    assert _keeps_original_bytes(data)
    assert _consistent_page_texts(data) == [["abc"]]


def test_object_under_a_member_number_renders_from_the_graph() -> None:
    """Render from the graph when an uncompressed object's header names a page's compressed font.

    lopdf stores the object under the number in its header, in place of the member that hayro reads
    for that number.
    """
    data = _compressed_pdf([(4, FONT)], objects={9: MAPPED_FONT}, headers={9: 4})

    assert not _keeps_original_bytes(data)
    assert _consistent_page_texts(data) == [["xyz"]]


def _two_page_tree(kids: str, extra: dict[int, str | None]) -> dict[int, str | None]:
    """Return a page tree whose walker pages are objects 4 and 6."""
    return {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count 2 /MediaBox [0 0 300 300] /Resources << /Font << /F1 3 0 R >> >> >>",
        3: FONT,
        4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
        5: _stream("PAGE ONE"),
        6: "<< /Type /Page /Parent 2 0 R /Contents 7 0 R >>",
        7: _stream("PAGE TWO"),
        **extra,
    }


@pytest.mark.parametrize(
    ("kids", "extra"),
    [
        pytest.param("4 0 R 8 0 R 6 0 R", {8: None, 9: "<< >>"}, id="missing-object"),
        pytest.param("4 0 R null 6 0 R", {}, id="null"),
        pytest.param("4 0 R 8 0 R 6 0 R", {8: "42"}, id="integer-object"),
        pytest.param("4 0 R 8 0 R 6 0 R", {8: _stream("NOT A PAGE")}, id="stream"),
        pytest.param("4 0 R 8 0 R 6 0 R", {8: "<< /Contents 9 0 R >>", 9: _stream("UNTYPED")}, id="untyped"),
        pytest.param(
            "4 0 R 8 0 R 6 0 R",
            {8: "<< /Type /Template /Contents 9 0 R >>", 9: _stream("TEMPLATE")},
            id="other-type",
        ),
        pytest.param("4 0 R << /Type /Page /Contents 8 0 R >> 6 0 R", {8: _stream("DIRECT")}, id="direct"),
    ],
)
def test_every_view_lists_the_walker_pages(kids: str, extra: dict[int, str | None]) -> None:
    """Read the page-tree walker's pages in every view.

    hayro 0.7 stopped at the first kid that was not a dictionary, and hayro 0.7
    and 0.8 read untyped or direct kids as pages, which the walker skips. Page 2
    then could not be extracted or rendered, or showed another object.
    """
    data = _pdf(_two_page_tree(kids, extra))

    assert len(pylopdf.open(data).pages) == 2
    assert _consistent_page_texts(data) == [["PAGE ONE"], ["PAGE TWO"]]


def test_walker_pages_render_under_an_interpretation_limit() -> None:
    """A finite limit reloads the bounded serialization before flattening it."""
    data = _pdf(_two_page_tree("4 0 R 8 0 R 6 0 R", {8: None, 9: "<< >>"}))
    limits = pylopdf.DocumentLimits(max_interpretation_size=1024 * 1024)
    doc = pylopdf.open(data, limits=limits)

    assert [doc.pages[index].get_text().splitlines() for index in range(2)] == [["PAGE ONE"], ["PAGE TWO"]]
    assert doc.render_pages(alpha=True)[1] == doc.pages[1].get_pixmap(alpha=True).tobytes()


def test_walker_pages_render_without_changing_the_saved_tree() -> None:
    doc = pylopdf.open(_pdf(_two_page_tree("4 0 R 8 0 R 6 0 R", {8: "42"})))

    assert doc.pages[1].get_text().splitlines() == ["PAGE TWO"]
    assert re.search(rb"/Kids\s*\[4 0 R 8 0 R 6 0 R\]", doc.tobytes())


def test_rendered_walker_pages_keep_inherited_attributes() -> None:
    """Page one inherits a landscape box and a quarter turn from a nested node."""
    data = _pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [10 0 R 6 0 R] /Count 2 /MediaBox [0 0 300 300] "
            "/Resources << /Font << /F1 3 0 R >> >> >>",
            3: FONT,
            4: "<< /Type /Page /Parent 10 0 R /Contents 5 0 R >>",
            5: _stream("PAGE ONE"),
            6: "<< /Type /Page /Parent 2 0 R /Contents 7 0 R >>",
            7: _stream("PAGE TWO"),
            8: None,
            9: "<< >>",
            10: "<< /Type /Pages /Parent 2 0 R /Kids [8 0 R 4 0 R] /Count 1 /Rotate 90 /MediaBox [0 0 400 200] >>",
        }
    )

    assert _consistent_page_texts(data) == [["PAGE ONE"], ["PAGE TWO"]]
    doc = pylopdf.open(data)
    assert doc.pages[0].rotation == 90
    rotated = doc.pages[0].get_pixmap(alpha=True)
    assert (rotated.width, rotated.height) == (200, 400)
    square = doc.pages[1].get_pixmap(alpha=True)
    assert (square.width, square.height) == (300, 300)
