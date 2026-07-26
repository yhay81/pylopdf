"""Tests for Document.embfile_* attachment APIs."""

from __future__ import annotations

import zlib

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf


def _compressed_embfile_pdf(
    payload: bytes,
    *,
    filter_name: bytes = b"/FlateDecode",
) -> bytes:
    """Build one attachment without asking pylopdf to decode it first."""
    encoded = zlib.compress(payload) if filter_name in {b"/FlateDecode", b"/Fl"} else payload
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /Names << /EmbeddedFiles 4 0 R >> >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: "<< /Names [(payload.bin) 5 0 R] >>",
            5: "<< /Type /Filespec /F (payload.bin) /EF << /F 6 0 R >> >>",
            6: (
                b"<< /Type /EmbeddedFile /Filter "
                + filter_name
                + b" /Length "
                + str(len(encoded)).encode()
                + b" >>\nstream\n"
                + encoded
                + b"\nendstream"
            ),
        }
    )


def test_embfile_add_get_roundtrip() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello"]))
    payload = b"\x00\x01binary\xff\xfe" * 100
    doc.embfile_add("invoice.xml", payload, filename="請求書データ.xml", desc="請求書の構造化データ")
    assert doc.embfile_names() == ["invoice.xml"]
    assert doc.embfile_get("invoice.xml") == payload

    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.embfile_names() == ["invoice.xml"]
    assert reopened.embfile_get("invoice.xml") == payload


def test_embfile_survives_compressed_and_garbage_save() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello"]))
    payload = b"repetitive " * 1000
    doc.embfile_add("data.txt", payload)
    data = doc.tobytes(garbage=True, deflate=True, object_streams=True)
    reopened = pylopdf.open(stream=data)
    assert reopened.embfile_get("data.txt") == payload


def test_embfile_multiple_names_sorted() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    doc.embfile_add("b.txt", b"B")
    doc.embfile_add("a.txt", b"A")
    assert doc.embfile_names() == ["a.txt", "b.txt"]
    assert doc.embfile_get("a.txt") == b"A"
    assert doc.embfile_get("b.txt") == b"B"


def test_inline_filespec_reads_do_not_mutate_document() -> None:
    """Read a valid inline FileSpec without creating orphan objects."""
    pdf = build_raw_pdf(
        {
            1: (
                "<< /Type /Catalog /Pages 2 0 R /Names << /EmbeddedFiles << "
                "/Names [(x.txt) << /Type /Filespec /F (x.txt) /EF << /F 4 0 R >> >>] >> >> >>"
            ),
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: b"<< /Type /EmbeddedFile /Length 3 >>\nstream\nabc\nendstream",
        }
    )
    doc = pylopdf.open(stream=pdf)
    before = len(doc.tobytes())

    for _ in range(4):
        assert doc.embfile_names() == ["x.txt"]
        assert doc.embfile_get("x.txt") == b"abc"

    assert len(doc.tobytes()) == before
    doc.embfile_add("y.txt", b"def")
    assert doc.embfile_names() == ["x.txt", "y.txt"]
    assert doc.embfile_get("x.txt") == b"abc"
    doc.embfile_del("x.txt")
    assert doc.embfile_names() == ["y.txt"]


def test_embfile_del_removes() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    doc.embfile_add("a.txt", b"A")
    doc.embfile_add("b.txt", b"B")
    doc.embfile_del("a.txt")
    assert doc.embfile_names() == ["b.txt"]
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.embfile_names() == ["b.txt"]


def test_embfile_errors() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    doc.embfile_add("a.txt", b"A")
    with pytest.raises(pylopdf.PdfError, match="already exists"):
        doc.embfile_add("a.txt", b"other")
    with pytest.raises(pylopdf.PdfError, match="not found"):
        doc.embfile_get("missing.txt")
    with pytest.raises(pylopdf.PdfError, match="not found"):
        doc.embfile_del("missing.txt")
    with pytest.raises(ValueError, match="name"):
        doc.embfile_add("", b"x")


def test_embfile_get_bounds_compressed_output_before_materializing_it() -> None:
    payload = b"attachment payload " * 100_000
    doc = pylopdf.open(stream=_compressed_embfile_pdf(payload))

    with pytest.raises(pylopdf.LimitError) as caught:
        doc.embfile_get("payload.bin", max_size=1024)
    assert caught.value.code == "embedded_file_size"
    assert "1024-byte" in str(caught.value)

    assert doc.embfile_get("payload.bin", max_size=len(payload)) == payload
    assert doc.embfile_get("payload.bin", max_size=None) == payload


def test_embfile_get_applies_the_same_limit_to_uncompressed_content() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    doc.embfile_add("raw.bin", b"1234")
    with pytest.raises(pylopdf.LimitError) as caught:
        doc.embfile_get("raw.bin", max_size=3)
    assert caught.value.code == "embedded_file_size"
    assert doc.embfile_get("raw.bin", max_size=4) == b"1234"


def test_embfile_get_accepts_filter_abbreviations_and_rejects_decode_failures() -> None:
    payload = b"abbreviated Flate attachment"
    abbreviated = pylopdf.open(
        stream=_compressed_embfile_pdf(payload, filter_name=b"/Fl"),
    )
    assert abbreviated.embfile_get("payload.bin") == payload

    unsupported = pylopdf.open(
        stream=_compressed_embfile_pdf(b"encoded", filter_name=b"/DCTDecode"),
    )
    with pytest.raises(pylopdf.PdfError, match="failed to decode attachment"):
        unsupported.embfile_get("payload.bin")

    malformed = pylopdf.open(
        stream=_compressed_embfile_pdf(b"encoded", filter_name=b"123"),
    )
    with pytest.raises(pylopdf.PdfError, match="invalid Filter"):
        malformed.embfile_get("payload.bin")


@pytest.mark.parametrize("max_size", [True, 1.5, "1024"])
def test_embfile_get_rejects_non_integer_size_limits(max_size: object) -> None:
    doc = pylopdf.Document()
    doc.new_page()
    doc.embfile_add("x", b"x")
    with pytest.raises(TypeError, match="max_size"):
        doc.embfile_get("x", max_size=max_size)  # type: ignore[arg-type]


@pytest.mark.parametrize("max_size", [0, -1])
def test_embfile_get_rejects_non_positive_size_limits(max_size: int) -> None:
    doc = pylopdf.Document()
    doc.new_page()
    doc.embfile_add("x", b"x")
    with pytest.raises(ValueError, match="max_size"):
        doc.embfile_get("x", max_size=max_size)


def test_embfile_name_tree_reference_cycle_is_visited_once() -> None:
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /Names << /EmbeddedFiles 4 0 R >> >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: "<< /Names [(x.txt) 5 0 R] /Kids [4 0 R] >>",
            5: "<< /Type /Filespec /F (x.txt) /EF << /F 6 0 R >> >>",
            6: b"<< /Type /EmbeddedFile /Length 1 >>\nstream\nx\nendstream",
        }
    )
    doc = pylopdf.open(stream=pdf)
    assert doc.embfile_names() == ["x.txt"]
    assert doc.embfile_get("x.txt") == b"x"


def test_embfile_name_tree_rejects_excessive_depth() -> None:
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /Names << /EmbeddedFiles 4 0 R >> >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
    }
    for object_id in range(4, 36):
        objects[object_id] = f"<< /Kids [{object_id + 1} 0 R] >>"
    objects[36] = "<< /Names [(x.txt) 37 0 R] >>"
    objects[37] = "<< /Type /Filespec /F (x.txt) /EF << /F 38 0 R >> >>"
    objects[38] = b"<< /Type /EmbeddedFile /Length 1 >>\nstream\nx\nendstream"

    doc = pylopdf.open(stream=build_raw_pdf(objects))
    with pytest.raises(pylopdf.PdfError, match="32-level safety limit"):
        doc.embfile_names()


def test_embfile_name_tree_rejects_excessive_entries_without_partial_output() -> None:
    pairs = " ".join(f"(name-{index}) 5 0 R" for index in range(4097))
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /Names << /EmbeddedFiles 4 0 R >> >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: f"<< /Names [{pairs}] >>",
            5: "<< /Type /Filespec /F (x.txt) /EF << /F 6 0 R >> >>",
            6: b"<< /Type /EmbeddedFile /Length 1 >>\nstream\nx\nendstream",
        }
    )
    doc = pylopdf.open(stream=pdf)
    with pytest.raises(pylopdf.PdfError, match="4096-entry safety limit"):
        doc.embfile_names()
    with pytest.raises(pylopdf.PdfError, match="4096-entry safety limit"):
        doc.embfile_get("name-0")


def test_embfile_add_refuses_to_create_an_unreadable_oversized_tree() -> None:
    pairs = " ".join(f"(name-{index}) 5 0 R" for index in range(4096))
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /Names << /EmbeddedFiles 4 0 R >> >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: f"<< /Names [{pairs}] >>",
            5: "<< /Type /Filespec /F (x.txt) /EF << /F 6 0 R >> >>",
            6: b"<< /Type /EmbeddedFile /Length 1 >>\nstream\nx\nendstream",
        }
    )
    doc = pylopdf.open(stream=pdf)
    before = doc.tobytes()
    with pytest.raises(pylopdf.PdfError, match="4096-entry safety limit is reached"):
        doc.embfile_add("one-too-many", b"x")
    assert doc.tobytes() == before
