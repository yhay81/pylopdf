"""Low-level behavior tests for the pylopdf_core._Document Rust binding."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from pylopdf.pylopdf_core import LimitError, _Document


def _oversized_png_header() -> bytes:
    ihdr = struct.pack(">IIBBBBB", 8_001, 8_000, 8, 2, 0, 0, 0)
    body = b"IHDR" + ihdr
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(ihdr)) + body + struct.pack(">I", zlib.crc32(body))


def test_new_document_is_empty() -> None:
    doc = _Document()
    assert doc.page_count() == 0
    assert doc.version() == "1.7"


def test_new_document_saves_valid_minimum_structure() -> None:
    data = _Document().save_bytes()
    assert b"/Root" in data
    assert b"/Type/Catalog" in data
    assert b"/Type/Pages" in data
    assert b"/Count 0" in data
    assert _Document.load_bytes(data).page_count() == 0


def test_load_bytes_and_page_count(three_page_pdf: bytes) -> None:
    doc = _Document.load_bytes(three_page_pdf)
    assert doc.page_count() == 3


def test_interpretation_size_limit_is_repeated_in_core(one_page_pdf: bytes) -> None:
    exact = _Document.load_bytes(
        one_page_pdf,
        max_interpretation_size=len(one_page_pdf),
    )
    assert exact.extract_text([1])

    bounded = _Document.load_bytes(
        one_page_pdf,
        max_interpretation_size=len(one_page_pdf) - 1,
    )
    with pytest.raises(LimitError) as source_error:
        bounded.extract_text([1])
    assert source_error.value.args[0] == "interpretation_size"

    edited_probe = _Document()
    edited_probe.new_page(None, 595.0, 842.0)
    snapshot_size = len(edited_probe.save_bytes())
    edited = _Document(None, snapshot_size - 1)
    edited.new_page(None, 595.0, 842.0)
    with pytest.raises(LimitError) as edited_error:
        edited.extract_text([1])
    assert edited_error.value.args[0] == "interpretation_size"

    with pytest.raises(ValueError, match="max_interpretation_size"):
        _Document(None, 0)


def test_load_metadata_file_size_limit(tmp_path: Path, three_page_pdf: bytes) -> None:
    path = tmp_path / "three-pages.pdf"
    path.write_bytes(three_page_pdf)
    limit = len(three_page_pdf)

    assert _Document.load_metadata(str(path), None, limit)[1] == 3
    assert _Document.load_metadata_bytes(three_page_pdf, None, limit)[1] == 3

    with pytest.raises(LimitError) as path_error:
        _Document.load_metadata(str(path), None, limit - 1)
    with pytest.raises(LimitError) as stream_error:
        _Document.load_metadata_bytes(three_page_pdf, None, limit - 1)
    assert path_error.value.args[0] == "file_size"
    assert stream_error.value.args[0] == "file_size"

    with pytest.raises(ValueError, match="max_file_size"):
        _Document.load_metadata_bytes(three_page_pdf, None, 0)


def test_save_bytes_roundtrip(three_page_pdf: bytes) -> None:
    doc = _Document.load_bytes(three_page_pdf)
    data = doc.save_bytes()
    assert data.startswith(b"%PDF-")
    reloaded = _Document.load_bytes(data)
    assert reloaded.page_count() == 3


def test_save_bytes_paths_are_bounded_in_core(one_page_pdf: bytes) -> None:
    plain = _Document.load_bytes(one_page_pdf)
    with pytest.raises(LimitError) as plain_limit:
        plain.save_bytes(16)
    assert plain_limit.value.args[0] == "pdf_output_size"

    modern = _Document.load_bytes(one_page_pdf)
    with pytest.raises(LimitError) as modern_limit:
        modern.save_bytes_with_object_streams(16)
    assert modern_limit.value.args[0] == "pdf_output_size"

    encrypted = _Document.load_bytes(one_page_pdf)
    with pytest.raises(LimitError) as encrypted_limit:
        encrypted.save_bytes_encrypted("", "owner", 0, bytes(32), 16)
    assert encrypted_limit.value.args[0] == "pdf_output_size"


def test_save_and_load_file(tmp_path, one_page_pdf: bytes) -> None:  # noqa: ANN001
    path = tmp_path / "out.pdf"
    doc = _Document.load_bytes(one_page_pdf)
    doc.save(str(path))
    reloaded = _Document.load(str(path))
    assert reloaded.page_count() == 1


def test_extract_text(three_page_pdf: bytes) -> None:
    doc = _Document.load_bytes(three_page_pdf)
    assert "Page one" in doc.extract_text([1])
    assert "Page three" in doc.extract_text([3])


def test_delete_pages(three_page_pdf: bytes) -> None:
    doc = _Document.load_bytes(three_page_pdf)
    doc.delete_pages([2])
    assert doc.page_count() == 2
    remaining = doc.extract_text([1, 2])
    assert "Page two" not in remaining


def test_metadata_set_and_get(one_page_pdf: bytes) -> None:
    doc = _Document.load_bytes(one_page_pdf)
    assert doc.get_metadata() == {}
    doc.set_metadata("Title", "My Title")
    doc.set_metadata("Author", "Alice")
    assert doc.get_metadata() == {"Title": "My Title", "Author": "Alice"}
    # An empty value removes the metadata key.
    doc.set_metadata("Author", "")
    assert doc.get_metadata() == {"Title": "My Title"}


def test_metadata_unicode_roundtrip(one_page_pdf: bytes) -> None:
    doc = _Document.load_bytes(one_page_pdf)
    doc.set_metadata("Title", "日本語のタイトル")
    # Save and reload through UTF-16BE.
    reloaded = _Document.load_bytes(doc.save_bytes())
    assert reloaded.get_metadata()["Title"] == "日本語のタイトル"


def test_metadata_pdfdocencoding(one_page_pdf: bytes) -> None:
    """Decode a BOM-less PDF string as PDFDocEncoding."""
    raw = one_page_pdf.replace(
        b"trailer\n<< /Size 6 /Root 1 0 R >>",
        b"trailer\n<< /Size 6 /Root 1 0 R /Info << /Title <80> >> >>",
    )
    doc = _Document.load_bytes(raw)
    # PDFDocEncoding byte 0x80 is bullet (U+2022).
    assert doc.get_metadata()["Title"] == "•"


def test_merge(one_page_pdf: bytes, three_page_pdf: bytes) -> None:
    doc = _Document.load_bytes(one_page_pdf)
    other = _Document.load_bytes(three_page_pdf)
    doc.merge(other)
    assert doc.page_count() == 4
    # Preserve a merged document through save and reload.
    reloaded = _Document.load_bytes(doc.save_bytes())
    assert reloaded.page_count() == 4
    all_text = reloaded.extract_text([1, 2, 3, 4])
    for expected in ["Hello PDF", "Page one", "Page two", "Page three"]:
        assert expected in all_text


def test_structural_batches_are_bounded_in_core(one_page_pdf: bytes) -> None:
    too_many = [1] * 4097

    deleting = _Document.load_bytes(one_page_pdf)
    with pytest.raises(ValueError, match="4096 page entries"):
        deleting.delete_pages(too_many)
    assert deleting.page_count() == 1

    selecting = _Document.load_bytes(one_page_pdf)
    with pytest.raises(ValueError, match="4096 page entries"):
        selecting.select(too_many)
    assert selecting.page_count() == 1

    merging = _Document.load_bytes(one_page_pdf)
    source = _Document.load_bytes(one_page_pdf)
    with pytest.raises(ValueError, match="4096 page entries"):
        merging.merge_pages(source, too_many, None)
    assert merging.page_count() == 1


def test_text_replacement_is_bounded_in_core(one_page_pdf: bytes) -> None:
    doc = _Document.load_bytes(one_page_pdf)
    before = doc.save_bytes()

    with pytest.raises(LimitError) as input_limit:
        doc.replace_text_on_page(1, "PDF", "x" * 4094, None, 64 * 1024 * 1024)
    assert input_limit.value.args[0] == "replacement_input_size"
    with pytest.raises(LimitError) as output_limit:
        doc.replace_text_on_page(1, "PDF", "Cat", None, 16)
    assert output_limit.value.args[0] == "replacement_output_size"
    with pytest.raises(ValueError, match="exactly one character"):
        doc.replace_text_on_page(1, "PDF", "Cat", "??", 64 * 1024 * 1024)
    assert doc.save_bytes() == before


def test_single_render_output_limit_is_repeated_in_core(one_page_pdf: bytes) -> None:
    doc = _Document.load_bytes(one_page_pdf)

    with pytest.raises(LimitError) as caught:
        doc.render_page_png(1, 1.0, None, 1)
    assert caught.value.args[0] == "render_output_size"
    with pytest.raises(ValueError, match="max_output_size"):
        doc.render_page_png(1, 1.0, None, 0)


def test_image_insertion_limits_are_repeated_in_core(one_page_pdf: bytes, tmp_path: Path) -> None:
    doc = _Document.load_bytes(one_page_pdf)
    before = doc.save_bytes()
    rect = (0.0, 0.0, 10.0, 10.0)

    with pytest.raises(LimitError) as input_limit:
        doc.insert_image(1, rect, b"too large", 0, True, True, 1, 64_000_000)
    assert input_limit.value.args[0] == "image_input_size"

    with pytest.raises(LimitError) as pixel_limit:
        doc.insert_image(1, rect, _oversized_png_header(), 0, True, True, 64 * 1024 * 1024, 64_000_000)
    assert pixel_limit.value.args[0] == "image_pixel_count"

    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"too large")
    with pytest.raises(LimitError) as file_limit:
        doc.insert_image_file(1, rect, str(image_path), 0, True, True, 1, 64_000_000)
    assert file_limit.value.args[0] == "image_input_size"
    assert doc.save_bytes() == before


def test_font_input_limits_are_repeated_in_core(one_page_pdf: bytes, tmp_path: Path) -> None:
    doc = _Document.load_bytes(one_page_pdf)
    before = doc.save_bytes()
    color = (0.0, 0.0, 0.0)

    calls = [
        lambda: doc.set_fallback_font("sans", b"too large", 0, 1),
        lambda: doc.set_form_field("missing", "x", b"too large", 0, 1),
        lambda: doc.insert_embedded_text(1, (10.0, 20.0), ["x"], b"too large", 0, 11.0, color, True, 1),
        lambda: doc.insert_embedded_textbox(
            1,
            (10.0, 10.0, 100.0, 80.0),
            "x",
            b"too large",
            0,
            11.0,
            1.2,
            0,
            color,
            True,
            1,
        ),
    ]
    for call in calls:
        with pytest.raises(LimitError) as caught:
            call()
        assert caught.value.args[0] == "font_input_size"

    font_path = tmp_path / "font.otf"
    font_path.write_bytes(b"too large")
    with pytest.raises(LimitError) as file_limit:
        doc.set_fallback_font_file("sans", str(font_path), 0, 1)
    assert file_limit.value.args[0] == "font_input_size"
    assert doc.save_bytes() == before


def test_generated_text_input_limits_are_repeated_in_core(one_page_pdf: bytes) -> None:
    doc = _Document.load_bytes(one_page_pdf)
    before = doc.save_bytes()
    color = (0.0, 0.0, 0.0)
    point = (10.0, 20.0)
    rect = (10.0, 10.0, 100.0, 80.0)

    calls = [
        lambda: doc.insert_page_text(1, point, [b"1234"], "Helvetica", True, 11.0, color, True, 3),
        lambda: doc.insert_page_textbox(1, rect, "1234", "Helvetica", True, 11.0, 1.2, 0, color, True, 3),
        lambda: doc.insert_embedded_text(1, point, ["1234"], b"bad font", 0, 11.0, color, True, None, 3),
        lambda: doc.insert_embedded_text_file(
            1,
            point,
            ["1234"],
            "missing-font.otf",
            0,
            11.0,
            color,
            True,
            None,
            3,
        ),
        lambda: doc.insert_embedded_textbox(
            1,
            rect,
            "1234",
            b"bad font",
            0,
            11.0,
            1.2,
            0,
            color,
            True,
            None,
            3,
        ),
        lambda: doc.insert_embedded_textbox_file(
            1,
            rect,
            "1234",
            "missing-font.otf",
            0,
            11.0,
            1.2,
            0,
            color,
            True,
            None,
            3,
        ),
    ]
    for call in calls:
        with pytest.raises(LimitError) as caught:
            call()
        assert caught.value.args[0] == "text_input_size"
        assert doc.save_bytes() == before

    with pytest.raises(ValueError, match="max_text_size"):
        doc.insert_page_text(1, point, [b"x"], "Helvetica", True, 11.0, color, True, 0)

    doc.insert_page_text(1, point, [b"1234"], "Helvetica", True, 11.0, color, True, 4)
    assert doc.save_bytes() != before


def test_search_limits_are_repeated_in_core(one_page_pdf: bytes) -> None:
    doc = _Document.load_bytes(one_page_pdf)

    assert doc.search_page(1, "é" * 2048, 1) == []
    with pytest.raises(LimitError) as input_limit:
        doc.search_page(1, "é" * 2049, 1)
    assert input_limit.value.args[0] == "search_input_size"

    with pytest.raises(LimitError) as hit_limit:
        doc.search_page(1, "l", 1)
    assert hit_limit.value.args[0] == "search_hit_count"
    assert len(doc.search_page(1, "l", 2)) == 2
    assert len(doc.search_page(1, "l", None)) == 2

    with pytest.raises(ValueError, match="max_hits"):
        doc.search_page(1, "l", 0)
    with pytest.raises(ValueError, match="needle"):
        doc.search_page(1, "", 1)


def test_password_input_limits_are_repeated_in_core(one_page_pdf: bytes) -> None:
    exact = "é" * 63 + "a"
    oversized = "é" * 64
    key = bytes(range(32))

    assert _Document.load_bytes(one_page_pdf, exact).page_count() == 1
    assert _Document.load_metadata_bytes(one_page_pdf, exact)[1] == 1

    for load_call in (
        lambda: _Document.load_bytes(one_page_pdf, oversized),
        lambda: _Document.load_metadata_bytes(one_page_pdf, oversized),
    ):
        with pytest.raises(LimitError) as caught:
            load_call()
        assert caught.value.args[0] == "password_input_size"

    doc = _Document.load_bytes(one_page_pdf)
    for save_call in (
        lambda: doc.save_bytes_encrypted(oversized, "owner", 0, key),
        lambda: doc.save_bytes_encrypted("user", oversized, 0, key),
    ):
        with pytest.raises(LimitError) as caught:
            save_call()
        assert caught.value.args[0] == "password_input_size"

    encrypted = doc.save_bytes_encrypted("user", "owner", 0, key)
    locked = _Document.load_bytes(encrypted)
    with pytest.raises(LimitError) as user_limit:
        locked.authenticate_user_password(oversized)
    assert user_limit.value.args[0] == "password_input_size"
    with pytest.raises(LimitError) as owner_limit:
        locked.authenticate_owner_password(oversized)
    assert owner_limit.value.args[0] == "password_input_size"


def test_embedded_file_input_limit_is_repeated_in_core(one_page_pdf: bytes) -> None:
    doc = _Document.load_bytes(one_page_pdf)
    payload = b"1234"
    doc.embfile_add("exact.bin", payload, None, None, len(payload))

    before = doc.save_bytes()
    with pytest.raises(LimitError) as caught:
        doc.embfile_add("too-large.bin", payload, None, None, len(payload) - 1)
    assert caught.value.args[0] == "embedded_file_size"
    assert doc.save_bytes() == before

    with pytest.raises(ValueError, match="max_size"):
        doc.embfile_add("zero.bin", payload, None, None, 0)
    doc.embfile_add("unbounded.bin", payload, None, None, None)
    assert doc.embfile_get("unbounded.bin", len(payload)) == payload


def test_merge_into_empty(three_page_pdf: bytes) -> None:
    doc = _Document()
    doc.merge(_Document.load_bytes(three_page_pdf))
    assert doc.page_count() == 3
    reloaded = _Document.load_bytes(doc.save_bytes())
    assert reloaded.page_count() == 3


def test_merge_empty_then_nonempty(three_page_pdf: bytes) -> None:
    """Preserve max_id and the page tree after merging an empty document."""
    doc = _Document()
    doc.merge(_Document())
    doc.merge(_Document.load_bytes(three_page_pdf))
    assert doc.page_count() == 3
    assert _Document.load_bytes(doc.save_bytes()).page_count() == 3


def test_merge_repairs_incorrect_page_count(one_page_pdf: bytes) -> None:
    """Normalize an invalid input Count to the actual merged page count."""
    broken_count = one_page_pdf.replace(b"/Count 1", b"/Count 9")
    doc = _Document.load_bytes(broken_count)
    assert doc.page_count() == 1

    doc.merge(_Document.load_bytes(one_page_pdf))
    data = doc.save_bytes()

    assert doc.page_count() == 2
    assert b"/Count 2" in data
    assert b"/Count 10" not in data
