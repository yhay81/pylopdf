"""Resource-policy tests for untrusted PDF processing."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf

REAL_WORLD = Path(__file__).parent / "assets" / "real_world"


def _error_code(error: pylopdf.LimitError) -> str:
    """Read the stable resource identifier."""
    return error.code


def test_document_limits_validate_values() -> None:
    assert pylopdf.DocumentLimits().max_pages is None
    with pytest.raises(TypeError, match="max_pages"):
        pylopdf.DocumentLimits(max_pages=True)
    with pytest.raises(TypeError, match="max_pages"):
        pylopdf.DocumentLimits(max_pages=1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="max_pages"):
        pylopdf.DocumentLimits(max_pages=0)
    with pytest.raises(ValueError, match="cannot both"):
        pylopdf.Document(
            stream=build_pdf(["one"]),
            max_decompressed_size=1024,
            limits=pylopdf.DocumentLimits(max_pages=1),
        )


def test_web_profile_has_bounded_worker_defaults() -> None:
    limits = pylopdf.DocumentLimits.web()
    assert limits.max_file_size == 10 * 1024 * 1024
    assert limits.max_pages == 200
    assert limits.max_page_content_size == 10 * 1024 * 1024
    assert limits.max_text_size == 1024 * 1024


def test_limit_error_is_machine_readable_pdf_error() -> None:
    data = build_pdf(["one"])
    with pytest.raises(pylopdf.LimitError) as caught:
        pylopdf.Document(
            stream=data,
            limits=pylopdf.DocumentLimits(max_file_size=len(data) - 1),
        )
    assert isinstance(caught.value, pylopdf.PdfError)
    assert _error_code(caught.value) == "file_size"
    assert caught.value.args[0] == "file_size"
    assert str(caught.value).startswith("PDF input is")


def test_file_size_limit_precedes_path_read(tmp_path: Path) -> None:
    path = tmp_path / "input.pdf"
    data = build_pdf(["one"])
    path.write_bytes(data)
    with pytest.raises(pylopdf.LimitError) as caught:
        pylopdf.open(
            path,
            limits=pylopdf.DocumentLimits(max_file_size=len(data) - 1),
        )
    assert _error_code(caught.value) == "file_size"


def test_page_and_object_limits() -> None:
    data = build_pdf(["one", "two", "three"])
    with pytest.raises(pylopdf.LimitError) as pages:
        pylopdf.open(stream=data, limits=pylopdf.DocumentLimits(max_pages=2))
    assert _error_code(pages.value) == "page_count"

    with pytest.raises(pylopdf.LimitError) as objects:
        pylopdf.open(stream=data, limits=pylopdf.DocumentLimits(max_objects=4))
    assert _error_code(objects.value) == "object_count"


def test_direct_object_depth_limit_ignores_reference_cycles() -> None:
    nested = "[" * 8 + "0" + "]" * 8
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: nested,
            5: "<< /Next 6 0 R >>",
            6: "<< /Next 5 0 R >>",
        }
    )
    with pytest.raises(pylopdf.LimitError) as depth:
        pylopdf.open(stream=pdf, limits=pylopdf.DocumentLimits(max_object_depth=4))
    assert _error_code(depth.value) == "object_depth"

    doc = pylopdf.open(stream=pdf, limits=pylopdf.DocumentLimits(max_object_depth=16))
    assert doc.page_count == 1


def test_page_content_and_general_stream_limits_are_independent() -> None:
    page_content = b" " * 200
    unused = b"x" * 200
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R >>",
            4: b"<< /Length 200 >>\nstream\n" + page_content + b"\nendstream",
            5: b"<< /Length 200 >>\nstream\n" + unused + b"\nendstream",
        }
    )
    with pytest.raises(pylopdf.LimitError) as content:
        pylopdf.open(
            stream=pdf,
            limits=pylopdf.DocumentLimits(max_page_content_size=100),
        )
    assert _error_code(content.value) == "page_content_size"

    doc = pylopdf.open(
        stream=pdf,
        limits=pylopdf.DocumentLimits(
            max_page_content_size=200,
            max_decompressed_size=200,
        ),
    )
    assert doc.page_count == 1


def test_cumulative_decompressed_limit() -> None:
    stream = b"x" * 60
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: b"<< /Length 60 >>\nstream\n" + stream + b"\nendstream",
            5: b"<< /Length 60 >>\nstream\n" + stream + b"\nendstream",
        }
    )
    with pytest.raises(pylopdf.LimitError) as total:
        pylopdf.open(
            stream=pdf,
            limits=pylopdf.DocumentLimits(max_total_decompressed_size=100),
        )
    assert _error_code(total.value) == "total_decompressed_size"


def test_eager_object_stream_uses_cumulative_limit_code() -> None:
    with pytest.raises(pylopdf.LimitError) as total:
        pylopdf.open(
            REAL_WORLD / "f1040.pdf",
            limits=pylopdf.DocumentLimits(max_total_decompressed_size=100),
        )
    assert _error_code(total.value) == "total_decompressed_size"


def test_text_budget_is_cumulative_across_interpreted_pages() -> None:
    doc = pylopdf.open(
        stream=build_pdf(["ABC", "DEF", ""]),
        limits=pylopdf.DocumentLimits(max_text_size=5),
    )
    assert "ABC" in doc[0].get_text()
    with pytest.raises(pylopdf.LimitError) as text:
        doc[1].get_text()
    assert _error_code(text.value) == "text_size"
    # A failed text-heavy page does not consume the remaining budget.
    assert doc[2].get_text() == ""


def test_text_budget_remains_active_on_generated_documents() -> None:
    doc = pylopdf.Document(limits=pylopdf.DocumentLimits(max_text_size=2))
    page = doc.new_page()
    page.insert_text((20, 20), "ABC")
    with pytest.raises(pylopdf.LimitError) as text:
        page.get_text()
    assert _error_code(text.value) == "text_size"


def test_limits_revalidate_after_authentication() -> None:
    encrypted = (Path(__file__).parent / "assets" / "encrypted" / "user-aes-256.pdf").read_bytes()
    doc = pylopdf.open(
        stream=encrypted,
        limits=pylopdf.DocumentLimits(max_pages=1),
    )
    assert doc.is_encrypted
    with pytest.raises(pylopdf.LimitError) as pages:
        doc.authenticate("userpw")
    assert _error_code(pages.value) == "page_count"


def test_complexity_requires_no_stream_decoding() -> None:
    data = build_pdf(["one"])
    doc = pylopdf.open(stream=data)
    facts = doc.complexity
    assert facts["page_count"] == 1
    assert facts["object_count"] == 5
    assert facts["stream_count"] == 1
    assert facts["encoded_stream_bytes"] > 0
    assert facts["max_object_depth"] >= 2


@pytest.mark.parametrize("name", ["f1040.pdf", "bunka-kokugo-series-019-p4.pdf"])
def test_web_profile_accepts_representative_vector_and_scan_pdfs(name: str) -> None:
    doc = pylopdf.open(REAL_WORLD / name, limits=pylopdf.DocumentLimits.web())
    assert doc.page_count >= 1


def test_malformed_input_stays_inside_pdf_error_hierarchy() -> None:
    with pytest.raises(pylopdf.PdfError):
        pylopdf.open(
            stream=b"%PDF-1.7\nnot a document",
            limits=pylopdf.DocumentLimits.web(),
        )
