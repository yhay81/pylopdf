"""Keep loaded object numbers low enough for new objects.

lopdf numbers new objects above the highest object a document has, without an
overflow check. A malformed file numbered near 2**32 made new objects wrap onto
object 0 and then the catalog, and every save visited each number up to the
highest one. Loading now moves objects numbered above 8,388,607, the ISO
32000-1 indirect-object limit, to follow the highest object at or below it.
"""

from __future__ import annotations

import re
import zlib
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import build_pdf

import pylopdf

LIMIT = 8_388_607
NEAR_U32_MAX = 4_294_967_294
FONT = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
NOTO_SANS_JP = (
    Path(__file__).parents[1] / "fonts" / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp" / "NotoSansJP-Regular.otf"
)


def _stream(text: str, y: int = 100) -> str:
    content = f"BT /F1 20 Tf 20 {y} Td ({text}) Tj ET"
    return f"<< /Length {len(content)} >>\nstream\n{content}\nendstream"


def _objects(
    contents: str,
    *,
    catalog: str = "",
    numbers: tuple[int, int, int, int, int] = (1, 2, 3, 4, 5),
) -> dict[int, str | bytes]:
    """Return a one-page document drawing `contents`, numbered by `numbers`."""
    catalog_id, pages_id, page_id, font_id, content_id = numbers
    return {
        catalog_id: f"<< /Type /Catalog /Pages {pages_id} 0 R {catalog} >>",
        pages_id: (
            f"<< /Type /Pages /Kids [{page_id} 0 R] /Count 1 /MediaBox [0 0 300 300] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>"
        ),
        page_id: f"<< /Type /Page /Parent {pages_id} 0 R /Contents {contents} >>",
        font_id: FONT,
        content_id: _stream("TARGET PAGE"),
    }


def _xref_table(offsets: dict[int, int]) -> bytes:
    """Return a cross-reference table with one subsection per run of numbers."""
    runs: list[list[int]] = []
    for number in sorted(offsets):
        if runs and number == runs[-1][-1] + 1:
            runs[-1].append(number)
        else:
            runs.append([number])
    table = bytearray(b"xref\n0 1\n0000000000 65535 f \n")
    for run in runs:
        table += f"{run[0]} {len(run)}\n".encode()
        table += b"".join(f"{offsets[number]:010d} 00000 n \n".encode() for number in run)
    return bytes(table)


def _numbered_pdf(objects: dict[int, str | bytes], *, root: int = 1, headers: dict[int, str] | None = None) -> bytes:
    """Build an xref-table PDF whose entries keep the given numbers.

    `headers` overrides the `number generation` that an entry's object header
    declares; lopdf stores the object under the header's ID.
    """
    headers = headers or {}
    out = bytearray(b"%PDF-1.7\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        value = objects[number]
        offsets[number] = len(out)
        out += f"{headers.get(number, f'{number} 0')} obj\n".encode()
        out += value.encode("latin-1") if isinstance(value, str) else value
        out += b"\nendobj\n"
    xref_pos = len(out)
    out += _xref_table(offsets)
    out += f"trailer\n<< /Size {max(offsets) + 1} /Root {root} 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode()
    return bytes(out)


def _renumber_raw(data: bytes, renumbered: dict[int, int]) -> bytes:
    """Rename objects in pylopdf output and rebuild its cross-reference table.

    Only object headers and the exact array and `/Encrypt` references change,
    so encrypted stream bytes stay untouched.
    """
    for old, new in renumbered.items():
        data = data.replace(f"\n{old} 0 obj\n".encode(), f"\n{new} 0 obj\n".encode(), 1)
        data = data.replace(f" {old} 0 R]".encode(), f" {new} 0 R]".encode())
        data = data.replace(f"/Encrypt {old} 0 R".encode(), f"/Encrypt {new} 0 R".encode())
    body_end = data.rindex(b"\nxref\n") + 1
    trailer = re.search(rb"trailer\n(<<.*>>)\nstartxref", data, re.DOTALL)
    assert trailer is not None
    offsets = {int(match[1]): match.start() + 1 for match in re.finditer(rb"\n(\d+) 0 obj\n", data[:body_end])}
    dictionary = re.sub(rb"/Size \d+", f"/Size {max(offsets) + 1}".encode(), trailer[1])
    return (
        data[:body_end] + _xref_table(offsets) + b"trailer\n" + dictionary + f"\nstartxref\n{body_end}\n%%EOF".encode()
    )


def _with_reloaded(doc: pylopdf.Document) -> tuple[pylopdf.Document, pylopdf.Document]:
    return doc, pylopdf.Document(stream=doc.tobytes())


def _probe_document() -> pylopdf.Document:
    """Return the five-object target plus a marker object numbered near 2**32."""
    objects = _objects("5 0 R", catalog=f"/PylopdfProbe {NEAR_U32_MAX} 0 R")
    objects[NEAR_U32_MAX] = "<< /PylopdfMarker (BIG) >>"
    return pylopdf.Document(stream=_numbered_pdf(objects))


_ALLOCATIONS: dict[str, Callable[[pylopdf.Document], object]] = {
    "insert_text": lambda doc: doc[0].insert_text((20, 250), "Stamp"),
    "insert_text_embedded_font": lambda doc: doc[0].insert_text((20, 250), "Stamp", fontfile=NOTO_SANS_JP),
    "insert_image": lambda doc: doc[0].insert_image((10, 10, 50, 50), pixmap=doc[0].get_pixmap(scale=0.05)),
    "add_highlight_annot": lambda doc: doc[0].add_highlight_annot([(10, 10, 100, 30)]),
    "add_link_annot": lambda doc: doc[0].add_link_annot((10, 10, 100, 30), "https://example.com/"),
    "insert_ocr_text_layer": lambda doc: doc[0].insert_ocr_text_layer([(10, 10, 100, 30, "Word")]),
    "insert_pdf": lambda doc: doc.insert_pdf(pylopdf.Document(stream=build_pdf(["Source"]))),
    "show_pdf_page": lambda doc: doc[0].show_pdf_page(
        (0, 0, 100, 100), pylopdf.Document(stream=build_pdf(["Source"])), 0
    ),
    "new_page": lambda doc: doc.new_page(),
    "copy_page": lambda doc: doc.copy_page(0),
    "set_toc": lambda doc: doc.set_toc([[1, "Start", 1]]),
    "set_metadata": lambda doc: doc.set_metadata({"title": "Edited"}),
    "embfile_add": lambda doc: doc.embfile_add(f"note{len(doc.embfile_names())}.txt", b"note"),
}


@pytest.mark.parametrize("operation", list(_ALLOCATIONS))
def test_new_objects_cannot_wrap_onto_the_catalog(operation: str) -> None:
    """Object 4294967294 left one number before allocation wrapped past u32.

    The following objects took numbers 0 and 1, replacing the catalog, so the
    document raised `PdfError` on every later read. Now the marker object is
    number 6 and new objects follow it.
    """
    doc = _probe_document()

    for _ in range(2):
        _ALLOCATIONS[operation](doc)

    saved = doc.tobytes()
    assert re.search(rb"/PylopdfProbe 6 0 R", saved)
    assert re.search(rb"\n6 0 obj\n<</PylopdfMarker\(BIG\)>>", saved)
    for current in _with_reloaded(doc):
        assert "TARGET PAGE" in current.get_page_text(0).splitlines()


def test_references_above_the_highest_kept_object_follow_it_or_become_null() -> None:
    """Objects 10000000 and 4294967294 take 8 and 9 after object 7.

    Missing numbers up to 7 stay. Above it, a reference names a moved object or
    nothing, so 8, 8388607, and 4294967295 become null.
    """
    objects = _objects(f"[5 0 R 6 0 R 7 0 R 8 0 R 8388607 0 R 10000000 0 R {NEAR_U32_MAX} 0 R 4294967295 0 R]")
    objects[7] = _stream("SEVEN", 200)
    objects[10_000_000] = _stream("TEN MILLION", 150)
    objects[NEAR_U32_MAX] = _stream("TOP", 250)

    saved = pylopdf.Document(stream=_numbered_pdf(objects)).tobytes()

    assert re.search(rb"/Contents\[5 0 R 6 0 R 7 0 R null null 8 0 R 9 0 R null\]", saved)
    assert re.search(rb"\n8 0 obj\n<</Length \d+>>stream\nBT /F1 20 Tf 20 150 Td \(TEN MILLION\)", saved)
    assert re.search(rb"/Size 10\b", saved)


def test_moved_objects_keep_their_generation() -> None:
    """Cross-reference entry 6 locates an object whose header says 10000000 1.

    Both objects numbered 10000000 move after object 5 with their generations,
    and the reference to the absent generation 2 becomes null.
    """
    objects = _objects("[5 0 R 10000000 0 R 10000000 1 R 10000000 2 R]")
    objects[6] = _stream("GEN ONE", 200)
    objects[10_000_000] = _stream("GEN ZERO", 150)
    doc = pylopdf.Document(stream=_numbered_pdf(objects, headers={6: "10000000 1"}))

    saved = doc.tobytes()

    assert re.search(rb"/Contents\[5 0 R 6 0 R 7 1 R null\]", saved)
    assert re.search(rb"\n7 1 obj\n", saved)
    for current in _with_reloaded(doc):
        assert current.get_page_text(0).splitlines() == ["GEN ONE", "GEN ZERO", "TARGET PAGE"]


def test_cross_reference_entry_above_the_limit_without_its_object_lowers_max_id() -> None:
    """Entry 4294967294 locates object 6's header, so nothing moves.

    The in-use entry alone set `max_id` to 4294967294. It drops to object 6,
    the reference to the entry's number becomes null, and new objects follow.
    """
    objects = _objects("[5 0 R 6 0 R]", catalog=f"/PylopdfProbe {NEAR_U32_MAX} 0 R")
    objects[NEAR_U32_MAX] = _stream("SIX", 200)
    doc = pylopdf.Document(stream=_numbered_pdf(objects, headers={NEAR_U32_MAX: "6 0"}))
    stamps = ["STAMP ONE", "STAMP TWO"]

    for index, stamp in enumerate(stamps):
        doc[0].insert_text((20, 220 + 40 * index), stamp)

    saved = doc.tobytes()
    assert re.search(rb"/PylopdfProbe null", saved)
    size = re.search(rb"/Size (\d+)", saved)
    assert size is not None
    assert int(size[1]) < 30
    for current in _with_reloaded(doc):
        assert Counter(current.get_page_text(0).splitlines()) == Counter([*stamps, "SIX", "TARGET PAGE"])


def test_document_numbered_past_i32_reads_and_saves() -> None:
    """Every object moves to 1 through 5, including the trailer's `/Root`.

    hayro reads object numbers as i32, so it could not read this page at its
    original numbers, and every save visited four billion numbers.
    """
    numbers = (4_000_000_000, 4_000_000_001, 4_000_000_002, 4_000_000_003, 4_000_000_004)
    objects = _objects(f"{numbers[4]} 0 R", numbers=numbers)
    doc = pylopdf.Document(stream=_numbered_pdf(objects, root=numbers[0]))

    assert doc.get_page_text(0).split() == ["TARGET", "PAGE"]
    saved = doc.tobytes()
    assert re.search(rb"/Root 1 0 R", saved)
    assert not re.search(rb"\d{10} \d+ (?:obj|R)", saved)


_SAVES: dict[str, tuple[Callable[[pylopdf.Document], bytes], str | None]] = {
    "default": (lambda doc: doc.tobytes(), None),
    "garbage": (lambda doc: doc.tobytes(garbage=True), None),
    "object-streams": (lambda doc: doc.tobytes(use_objstms=True), None),
    "encrypted": (
        lambda doc: doc.tobytes(encryption=pylopdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner"),
        "user",
    ),
}


@pytest.mark.parametrize("mode", list(_SAVES))
def test_document_above_the_limit_saves_in_every_mode(mode: str) -> None:
    """Object streams and encryption allocate objects while saving.

    Both took numbers past u32, so the output failed to reopen; every save
    also visited each number up to 4294967294.
    """
    save, password = _SAVES[mode]

    saved = save(_probe_document())

    reopened = pylopdf.Document(stream=saved, password=password)
    assert reopened.get_page_text(0).split() == ["TARGET", "PAGE"]
    plain = reopened.tobytes()
    assert re.search(rb"/PylopdfProbe 6 0 R", plain)
    assert re.search(rb"\n6 0 obj\n<</PylopdfMarker\(BIG\)>>", plain)


def test_image_above_the_limit_is_compressed() -> None:
    """Image compression maps the renderer's image IDs back to editable objects.

    hayro reads number 10000000 from the original bytes, but loading moved the
    image to object 5, so rendering must read the renumbered document.
    """
    width = height = 128
    state = 0x12345678
    samples = bytearray()
    for _ in range(width * height * 3):
        state = (1_664_525 * state + 1_013_904_223) & 0xFFFFFFFF
        samples.append(state >> 24)
    encoded = zlib.compress(bytes(samples))
    image = (
        (
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceRGB "
            f"/BitsPerComponent 8 /Filter /FlateDecode /Length {len(encoded)} >>\nstream\n"
        ).encode()
        + encoded
        + b"\nendstream"
    )
    commands = "q 72 0 0 72 0 0 cm /Im0 Do Q"
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 100 100] >>",
        3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R /Resources << /XObject << /Im0 10000000 0 R >> >> >>",
        4: f"<< /Length {len(commands)} >>\nstream\n{commands}\nendstream",
        10_000_000: image,
    }
    doc = pylopdf.Document(stream=_numbered_pdf(objects))

    result = doc.compress_images(dpi=64, quality=65)

    assert (result["considered"], result["rewritten"]) == (1, 1)
    for current in _with_reloaded(doc):
        compressed = current[0].get_images()[0]
        assert (compressed["ext"], compressed["width"], compressed["height"]) == ("jpeg", 64, 64)


def test_objects_up_to_the_limit_keep_their_numbers() -> None:
    """Object 8388607 is within the limit, and 8388608 already follows it.

    The unallocated 8388609 still becomes null.
    """
    objects = _objects(f"[5 0 R {LIMIT} 0 R {LIMIT + 1} 0 R {LIMIT + 2} 0 R]")
    objects[LIMIT] = _stream("AT LIMIT", 200)
    objects[LIMIT + 1] = _stream("PAST LIMIT", 150)
    doc = pylopdf.Document(stream=_numbered_pdf(objects))

    saved = doc.tobytes()

    assert re.search(rb"/Contents\[5 0 R 8388607 0 R 8388608 0 R null\]", saved)
    assert re.search(rb"\n8388608 0 obj\n", saved)
    assert re.search(rb"/Size 8388609\b", saved)
    assert doc.get_page_text(0).splitlines() == ["AT LIMIT", "PAST LIMIT", "TARGET PAGE"]


@pytest.mark.parametrize("unlock", ["password", "authenticate"])
def test_encrypted_document_above_the_limit_opens_and_edits(unlock: str) -> None:
    """The content stream and the encryption dictionary sit above the limit.

    AES-256 keys do not depend on object numbers, so pylopdf's encrypted output
    still decrypts after its objects are renamed past the limit, and loading
    with the password moves them back.
    """
    objects = _objects("[5 0 R 6 0 R]")
    objects[6] = _stream("SECRET", 200)
    encrypted = pylopdf.Document(stream=_numbered_pdf(objects)).tobytes(
        encryption=pylopdf.PDF_ENCRYPT_AES_256, user_pw="user", owner_pw="owner"
    )
    encrypt_id = re.search(rb"/Encrypt (\d+) 0 R", encrypted)
    assert encrypt_id is not None
    patched = _renumber_raw(encrypted, {6: NEAR_U32_MAX, int(encrypt_id[1]): NEAR_U32_MAX - 1})
    assert patched.count(f"{NEAR_U32_MAX} 0 R".encode()) == 1
    if unlock == "password":
        doc = pylopdf.Document(stream=patched, password="user")
    else:
        doc = pylopdf.Document(stream=patched)
        assert doc.needs_pass
        assert doc.authenticate("user") == 2

    doc[0].insert_text((20, 250), "STAMP")

    for current in _with_reloaded(doc):
        assert Counter(current.get_page_text(0).splitlines()) == Counter(["SECRET", "STAMP", "TARGET PAGE"])
