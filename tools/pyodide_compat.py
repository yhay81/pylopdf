"""Run one logical pylopdf compatibility suite on native Python or Pyodide."""

from __future__ import annotations

import argparse
import difflib
import gc
import hashlib
import json
import platform
import statistics
import struct
import sys
import tempfile
import time
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

_ASSETS = (
    "tests/assets/real_world/pdf20-simple.pdf",
    "tests/assets/real_world/mhlw-doc.pdf",
    "tests/assets/real_world/f1040.pdf",
    "tests/assets/real_world/senate-expenditures.pdf",
    "tests/assets/real_world/bunka-kokugo-series-019-p4.pdf",
    "tests/assets/encrypted/user-aes-256.pdf",
    "fonts/pylopdf-fonts-jp/src/pylopdf_fonts_jp/NotoSansJP-Regular.otf",
)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_EXPECTED_TWO_PAGES = 2
_EXPECTED_MERGED_PAGES = 3
_SENATE_ROTATION = 90
_MIN_FORM_DRAWINGS = 400
# Keep required Unicode fixture data escaped so repository prose remains English.
_RIGHT_COLUMN = "\u53f3\u5074\u5217"
_LEFT_COLUMN = "\u5de6\u5074\u5217"
_CASE_LAW = "\u88c1\u5224\u4f8b"
_JAPANESE = "\u65e5\u672c\u8a9e"


def _require(condition: bool, message: str) -> None:  # noqa: FBT001
    if not condition:
        raise RuntimeError(message)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _exception_name(callback: Callable[[], object]) -> str:
    try:
        callback()
    except Exception as error:  # noqa: BLE001 - the public exception type is the result.
        return type(error).__name__
    msg = "operation unexpectedly succeeded"
    raise RuntimeError(msg)


def _png_size(data: bytes) -> list[int]:
    _require(data.startswith(_PNG_SIGNATURE), "rendering did not return PNG data")
    return list(struct.unpack(">II", data[16:24]))


def _build_raw_pdf(objects: dict[int, bytes | str], *, version: str = "1.7") -> bytes:
    expected = list(range(1, len(objects) + 1))
    _require(sorted(objects) == expected, "object numbers must be consecutive")
    output = bytearray(f"%PDF-{version}\n".encode())
    offsets: dict[int, int] = {}
    for number in expected:
        value = objects[number]
        body = value.encode("latin-1") if isinstance(value, str) else value
        offsets[number] = len(output)
        output.extend(f"{number} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref_position = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for number in expected:
        output.extend(f"{offsets[number]:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_position}\n%%EOF".encode())
    return bytes(output)


def _build_vertical_cjk_pdf() -> bytes:
    def octal(text: str) -> str:
        return "".join(f"\\{byte:03o}" for byte in text.encode("cp932"))

    stream = (
        "BT /F2 18 Tf 40 750 Td (Heading) Tj ET\n"
        f"BT /F1 24 Tf 200 720 Td ({octal(_RIGHT_COLUMN)}) Tj ET\n"
        f"BT /F1 24 Tf 100 720 Td ({octal(_LEFT_COLUMN)}) Tj ET\n"
        "BT /F2 18 Tf 40 20 Td (Footer) Tj ET"
    )
    return _build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: (
                "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 612 792] "
                "/Resources << /Font << /F1 3 0 R /F2 8 0 R >> >> >>"
            ),
            3: (
                "<< /Type /Font /Subtype /Type0 /BaseFont /MS-Mincho /Encoding /90ms-RKSJ-V /DescendantFonts [6 0 R] >>"
            ),
            4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            5: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
            6: (
                "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /MS-Mincho "
                "/CIDSystemInfo << /Registry (Adobe) /Ordering (Japan1) /Supplement 6 >> "
                "/FontDescriptor 7 0 R /DW 1000 >>"
            ),
            7: (
                "<< /Type /FontDescriptor /FontName /MS-Mincho /Flags 6 "
                "/FontBBox [0 -137 1000 859] /ItalicAngle 0 /Ascent 859 "
                "/Descent -140 /CapHeight 769 /StemV 78 >>"
            ),
            8: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        }
    )


def _build_multicolumn_pdf() -> bytes:
    stream = (
        "BT /F1 18 Tf 20 270 Td (A heading spanning both columns) Tj ET\n"
        "BT /F1 12 Tf 40 230 Td (Left one) Tj ET\n"
        "BT /F1 12 Tf 40 210 Td (Left two) Tj ET\n"
        "BT /F1 12 Tf 200 230 Td (Right one) Tj ET\n"
        "BT /F1 12 Tf 200 210 Td (Right two) Tj ET\n"
        "BT /F1 18 Tf 20 30 Td (A footer spanning both columns) Tj ET"
    )
    return _build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: (
                "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 400 300] "
                "/Resources << /Font << /F1 3 0 R >> >> >>"
            ),
            3: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            5: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        }
    )


def _build_limited_content_pdf(content: bytes, filter_name: str) -> bytes:
    return _build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R >>",
            4: (f"<< /Length {len(content)} /Filter /{filter_name} >>\nstream\n".encode() + content + b"\nendstream"),
        }
    )


def _build_reference_cycle_pdf() -> bytes:
    return _build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /Cycle 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: "<< /Next 5 0 R >>",
            5: "<< /Next 4 0 R >>",
        }
    )


def _limit_results(
    pdf20_bytes: bytes,
    form_bytes: bytes,
    pdf20_complexity: dict[str, int],
    bounded_scan_complexity: dict[str, int],
) -> dict[str, Any]:
    import pylopdf  # noqa: PLC0415

    def limit_code(callback: Callable[[], object]) -> str:
        try:
            callback()
        except pylopdf.LimitError as error:
            return error.code
        msg = "limited operation unexpectedly succeeded"
        raise RuntimeError(msg)

    compressed_bomb = _build_limited_content_pdf(
        zlib.compress(b" " * 65_536),
        "FlateDecode",
    )
    deep_object = _build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: "[" * 12 + "0" + "]" * 12,
        }
    )
    unverifiable = _build_limited_content_pdf(b"opaque", "Crypt")
    cyclic = pylopdf.Document(
        stream=_build_reference_cycle_pdf(),
        limits=pylopdf.DocumentLimits(max_object_depth=16),
    )
    return {
        "file_size": limit_code(
            lambda: pylopdf.Document(
                stream=pdf20_bytes,
                limits=pylopdf.DocumentLimits(max_file_size=len(pdf20_bytes) - 1),
            )
        ),
        "page_count": limit_code(
            lambda: pylopdf.Document(
                stream=form_bytes,
                limits=pylopdf.DocumentLimits(max_pages=1),
            )
        ),
        "decompressed_size": limit_code(
            lambda: pylopdf.Document(
                stream=compressed_bomb,
                limits=pylopdf.DocumentLimits(max_decompressed_size=1024),
            )
        ),
        "object_depth": limit_code(
            lambda: pylopdf.Document(
                stream=deep_object,
                limits=pylopdf.DocumentLimits(max_object_depth=4),
            )
        ),
        "unverifiable": limit_code(
            lambda: pylopdf.Document(
                stream=unverifiable,
                limits=pylopdf.DocumentLimits(max_decompressed_size=1024),
            )
        ),
        "reference_cycle_pages": cyclic.page_count,
        "pdf20_complexity": pdf20_complexity,
        "bounded_scan_complexity": bounded_scan_complexity,
    }


def _fixture_results(root: Path) -> dict[str, Any]:  # noqa: PLR0915 - explicit compatibility scenarios.
    import pylopdf  # noqa: PLC0415 - list-assets must work without an installed extension.

    real_world = root / "tests/assets/real_world"

    pdf20_bytes = (real_world / "pdf20-simple.pdf").read_bytes()
    pdf20 = pylopdf.Document(stream=pdf20_bytes)
    pdf20_text = pdf20.get_page_text(0)
    pdf20_markdown = pdf20.to_markdown()
    _require(pdf20.page_count == 1, "PDF 2.0 fixture has the wrong page count")
    _require("Hello World" in pdf20_text, "PDF 2.0 fixture lost Hello World")
    _require("Hello World" in pdf20_markdown, "PDF 2.0 Markdown lost Hello World")

    mhlw = pylopdf.Document(stream=(real_world / "mhlw-doc.pdf").read_bytes())
    mhlw_text = [mhlw.get_page_text(index) for index in range(mhlw.page_count)]
    mhlw_markdown = mhlw.to_markdown()
    _require(mhlw.page_count == _EXPECTED_TWO_PAGES, "MHLW fixture has the wrong page count")
    _require(_CASE_LAW in mhlw_text[0], "MHLW fixture lost embedded Japanese text")
    _require(_CASE_LAW in mhlw_markdown, "MHLW Markdown lost embedded Japanese text")
    _require(mhlw.get_text() == "".join(mhlw_text), "batched get_text diverged from per-page extraction")

    form_bytes = (real_world / "f1040.pdf").read_bytes()
    form = pylopdf.Document(stream=form_bytes)
    form_text = form.get_page_text(0)
    form_markdown = form[0].to_markdown()
    form_tables = form[0].find_tables()
    form_drawings = form[0].get_drawings()
    _require(form.page_count == _EXPECTED_TWO_PAGES, "Form 1040 fixture has the wrong page count")
    _require("U.S. Individual Income Tax Return" in form_text, "Form 1040 text is incomplete")
    _require(len(form_tables) == 1, "Form 1040 bordered table was not detected")
    form_table = form_tables[0]
    _require((form_table.row_count, form_table.col_count) == (2, 7), "Form 1040 table shape changed")
    _require("Full-time<br>student" in form_markdown, "Form 1040 table was not integrated into Markdown")
    _require(len(form_drawings) >= _MIN_FORM_DRAWINGS, "Form 1040 vector extraction is incomplete")

    senate = pylopdf.Document(stream=(real_world / "senate-expenditures.pdf").read_bytes())
    senate_lines = senate[0].find_tables()
    senate_text = senate[0].find_tables(strategy="text")
    _require(senate[0].rotation == _SENATE_ROTATION, "Senate fixture lost its page rotation")
    _require(
        len(senate_lines) == 1 and (senate_lines[0].row_count, senate_lines[0].col_count) == (2, 7),
        "Senate bordered header changed",
    )
    _require(
        len(senate_text) == 1 and (senate_text[0].row_count, senate_text[0].col_count) == (10, 3),
        "Senate borderless table changed",
    )
    first_senate_row = senate_text[0].extract()[0]
    _require(
        first_senate_row == ["BAIN, J MATTHEW", "DISTRICT DIRECTOR", "37,499.96"],
        "Senate rotated reading order changed",
    )

    scan_bytes = (real_world / "bunka-kokugo-series-019-p4.pdf").read_bytes()
    scan = pylopdf.Document(stream=scan_bytes)
    _require(scan.page_count == 1, "Japanese scan has the wrong page count")
    _require(scan.get_page_text(0) == "", "image-only Japanese scan unexpectedly exposed text")
    bounded_scan = pylopdf.Document(stream=scan_bytes, limits=pylopdf.DocumentLimits.web())
    _require(bounded_scan.page_count == 1, "web limits rejected a representative scan")

    encrypted_bytes = (root / "tests/assets/encrypted/user-aes-256.pdf").read_bytes()
    encrypted = pylopdf.Document(stream=encrypted_bytes)
    encrypted_error = _exception_name(lambda: encrypted.page_count)
    _require(encrypted.is_encrypted and encrypted.needs_pass, "encrypted fixture did not require authentication")
    _require(encrypted_error == "EncryptedDocumentError", "encrypted access returned the wrong exception")
    decrypted = pylopdf.Document(stream=encrypted_bytes, password="userpw")  # noqa: S106
    _require(decrypted.page_count == _EXPECTED_TWO_PAGES, "AES-256 fixture did not decrypt")
    _require("Encrypted page one" in decrypted.get_page_text(0), "decrypted text is incomplete")
    authentication_error = _exception_name(
        lambda: pylopdf.Document(stream=encrypted_bytes, password="wrong")  # noqa: S106
    )
    _require(authentication_error == "PasswordError", "wrong password returned the wrong exception")

    return {
        "pdf20": {
            "pages": pdf20.page_count,
            "text_sha256": _digest(pdf20_text),
            "markdown_sha256": _digest(pdf20_markdown),
        },
        "mhlw_cjk": {
            "pages": mhlw.page_count,
            "page_text_sha256": [_digest(text) for text in mhlw_text],
            "markdown_sha256": _digest(mhlw_markdown),
        },
        "form_table": {
            "pages": form.page_count,
            "text_sha256": _digest(form_text),
            "markdown_sha256": _digest(form_markdown),
            "shape": [form_table.row_count, form_table.col_count],
            "confidence": round(form_table.confidence, 6),
            "drawing_count": len(form_drawings),
            "drawing_types": sorted({drawing["type"] for drawing in form_drawings}),
        },
        "rotated_tables": {
            "rotation": senate[0].rotation,
            "bordered_shape": [senate_lines[0].row_count, senate_lines[0].col_count],
            "borderless_shape": [senate_text[0].row_count, senate_text[0].col_count],
            "first_row": first_senate_row,
        },
        "image_only": {"pages": scan.page_count, "text": scan.get_page_text(0)},
        "encryption": {
            "locked_exception": encrypted_error,
            "wrong_password_exception": authentication_error,
            "decrypted_pages": decrypted.page_count,
        },
        "limits": _limit_results(
            pdf20_bytes,
            form_bytes,
            pdf20.complexity,
            bounded_scan.complexity,
        ),
    }


def _layout_results() -> dict[str, Any]:
    import pylopdf  # noqa: PLC0415

    vertical = pylopdf.Document(stream=_build_vertical_cjk_pdf())[0]
    vertical_lines = [line for block in vertical.get_text("dict")["blocks"] for line in block["lines"]]
    vertical_text = vertical.get_text().splitlines()
    _require(
        vertical_text == ["Heading", _RIGHT_COLUMN, _LEFT_COLUMN, "Footer"],
        "vertical CJK reading order changed",
    )
    writing_modes = [line["wmode"] for line in vertical_lines]
    _require(writing_modes == [0, 1, 1, 0], "vertical CJK writing modes changed")
    _require(len(vertical.search_for(_RIGHT_COLUMN)) == 1, "vertical CJK search failed")

    columns = pylopdf.Document(stream=_build_multicolumn_pdf())[0]
    column_lines = columns.get_text().splitlines()
    expected_columns = [
        "A heading spanning both columns",
        "Left one",
        "Left two",
        "Right one",
        "Right two",
        "A footer spanning both columns",
    ]
    _require(column_lines == expected_columns, "multicolumn reading order changed")
    limited_columns = pylopdf.Document(
        stream=_build_multicolumn_pdf(),
        limits=pylopdf.DocumentLimits(max_text_size=8),
    )
    try:
        limited_columns[0].get_text()
    except pylopdf.LimitError as error:
        text_limit_code = error.code
    else:
        msg = "text budget unexpectedly admitted the multicolumn fixture"
        raise RuntimeError(msg)

    return {
        "vertical_cjk": {
            "lines": vertical_text,
            "writing_modes": writing_modes,
            "markdown_sha256": _digest(vertical.to_markdown()),
        },
        "columns": {
            "lines": column_lines,
            "word_count": len(columns.get_text("words")),
        },
        "text_limit_code": text_limit_code,
    }


def _generation_results(root: Path) -> dict[str, Any]:
    import pylopdf  # noqa: PLC0415

    font = (root / "fonts/pylopdf-fonts-jp/src/pylopdf_fonts_jp/NotoSansJP-Regular.otf").read_bytes()
    document = pylopdf.Document()
    _require(document.page_count == 0, "new document is not empty")
    first = document.new_page(width=240, height=180)
    first.insert_text((20, 35), "Generated on Wasm", fontsize=14)
    first.insert_text((20, 75), _JAPANESE, fontsize=18, fontbuffer=font)
    first_pixmap = first.get_pixmap(0.5)
    _require((first_pixmap.width, first_pixmap.height) == (120, 90), "generated page raster size changed")

    second = document.new_page(width=180, height=240)
    stale_error = _exception_name(lambda: first.rect)
    _require(stale_error == "StalePageError", "structural edit returned the wrong stale-page exception")
    spare_height = second.insert_textbox(
        (20, 20, 160, 100),
        "Second page wraps text without native filesystem access.",
        fontsize=12,
    )
    _require(spare_height >= 0, "generated textbox unexpectedly overflowed")

    serialized = document.tobytes()
    reopened = pylopdf.Document(stream=serialized)
    texts = [reopened.get_page_text(index).strip() for index in range(reopened.page_count)]
    _require(reopened.page_count == _EXPECTED_TWO_PAGES, "generated document has the wrong page count")
    _require("Generated on Wasm" in texts[0] and _JAPANESE in texts[0], "generated text did not round-trip")
    _require("Second page wraps text" in texts[1], "textbox text did not round-trip")

    serial_renders = reopened.render_pages([1, 0, 1], scale=0.5, workers=1)
    requested_parallel_renders = reopened.render_pages([1, 0, 1], scale=0.5, workers=4)
    _require(serial_renders == requested_parallel_renders, "workers=4 changed serial-fallback rendering")
    render_sizes = [_png_size(data) for data in requested_parallel_renders]
    _require(render_sizes == [[90, 120], [120, 90], [90, 120]], "batch rendering lost input order")

    merged = pylopdf.Document()
    merged.insert_pdf(reopened)
    merged.select([1, 0, 1])
    merged_texts = [merged.get_page_text(index).strip() for index in range(merged.page_count)]
    _require(merged.page_count == _EXPECTED_MERGED_PAGES, "merge/select produced the wrong page count")
    _require("Second page wraps text" in merged_texts[0], "merge/select changed page order")
    _require("Generated on Wasm" in merged_texts[1], "merge/select lost generated content")

    selected = pylopdf.Document(stream=serialized)
    selected.select([1])
    _require(selected.page_count == 1, "page selection did not produce a one-page document")
    _require("Second page wraps text" in selected.get_page_text(0), "page selection retained the wrong page")

    with tempfile.TemporaryDirectory(prefix="pylopdf-pyodide-") as directory:
        saved_path = Path(directory) / "roundtrip.pdf"
        reopened.save(saved_path)
        saved = pylopdf.Document(saved_path)
        _require(saved.page_count == _EXPECTED_TWO_PAGES, "virtual-filesystem save did not round-trip")

    closed_page = reopened[0]
    reopened.close()
    closed_error = _exception_name(lambda: closed_page.rect)
    _require(closed_error == "DocumentClosedError", "closed page returned the wrong exception")

    malformed_error = _exception_name(lambda: pylopdf.Document(stream=b"not a PDF"))
    _require(malformed_error == "PdfError", "malformed bytes returned the wrong exception")
    recovery = pylopdf.Document(stream=serialized)
    _require(recovery.page_count == _EXPECTED_TWO_PAGES, "runtime did not recover after malformed input")

    return {
        "empty_pages": 0,
        "roundtrip_pages": document.page_count,
        "text_sha256": [_digest(text) for text in texts],
        "first_pixmap": [first_pixmap.width, first_pixmap.height, first_pixmap.n],
        "batch_render_sizes": render_sizes,
        "merged_text_sha256": [_digest(text) for text in merged_texts],
        "selected_text_sha256": _digest(selected.get_page_text(0)),
        "exceptions": {
            "stale": stale_error,
            "closed": closed_error,
            "malformed": malformed_error,
        },
    }


def collect_results(root: Path) -> dict[str, Any]:
    """Run compatibility assertions and return stable native/Wasm comparison data."""
    import pylopdf  # noqa: PLC0415

    root = root.resolve()
    for relative_path in _ASSETS:
        path = root / relative_path
        _require(path.is_file(), f"compatibility asset is missing: {path}")
    return {
        "schema": 2,
        "pylopdf_version": pylopdf.__version__,
        "fixtures": _fixture_results(root),
        "layout": _layout_results(),
        "generation": _generation_results(root),
        "threads": {
            "render_pages_workers_4": "serial on Emscripten; bounded rayon pool on native",
        },
    }


def _median_ms(callback: Callable[[], object], repeats: int) -> float:
    callback()
    samples = []
    for _ in range(repeats):
        gc.collect()
        start = time.perf_counter()
        callback()
        samples.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(samples), 3)


def _peak_rss_bytes() -> int | None:
    if sys.platform == "emscripten":
        return None
    try:
        import resource  # noqa: PLC0415
    except ImportError:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def benchmark_limits(root: Path, repeats: int = 5) -> dict[str, Any]:
    """Measure bounded open/extract/rejection costs on native Python or Wasm."""
    import pylopdf  # noqa: PLC0415

    root = root.resolve()
    real_world = root / "tests/assets/real_world"
    form = (real_world / "f1040.pdf").read_bytes()
    scan = (real_world / "bunka-kokugo-series-019-p4.pdf").read_bytes()
    pdf20 = (real_world / "pdf20-simple.pdf").read_bytes()
    limits = pylopdf.DocumentLimits.web()

    def bounded_open(data: bytes) -> None:
        pylopdf.Document(stream=data, limits=limits).close()

    def bounded_extract() -> None:
        with pylopdf.Document(stream=form, limits=limits) as document:
            document.get_page_text(0)

    def reject_file_size() -> None:
        try:
            pylopdf.Document(
                stream=pdf20,
                limits=pylopdf.DocumentLimits(max_file_size=len(pdf20) - 1),
            )
        except pylopdf.LimitError as error:
            _require(error.code == "file_size", "file rejection returned the wrong code")
            return
        msg = "file-size rejection unexpectedly succeeded"
        raise RuntimeError(msg)

    def reject_page_count() -> None:
        try:
            pylopdf.Document(
                stream=form,
                limits=pylopdf.DocumentLimits(max_pages=1),
            )
        except pylopdf.LimitError as error:
            _require(error.code == "page_count", "page rejection returned the wrong code")
            return
        msg = "page-count rejection unexpectedly succeeded"
        raise RuntimeError(msg)

    timings = {
        "open_form": _median_ms(lambda: bounded_open(form), repeats),
        "open_scan": _median_ms(lambda: bounded_open(scan), repeats),
        "open_and_extract_form_page_0": _median_ms(bounded_extract, repeats),
        "reject_file_size": _median_ms(reject_file_size, repeats),
        "reject_page_count": _median_ms(reject_page_count, repeats),
    }
    return {
        "schema": 1,
        "runtime": sys.platform,
        "python": platform.python_version(),
        "repeats": repeats,
        "median_ms": timings,
        "process_peak_rss_bytes": _peak_rss_bytes(),
        "input_bytes": {
            "f1040.pdf": len(form),
            "bunka-kokugo-series-019-p4.pdf": len(scan),
            "pdf20-simple.pdf": len(pdf20),
        },
    }


def _compare_results(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    if actual == expected:
        return
    expected_text = json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True).splitlines()
    actual_text = json.dumps(actual, ensure_ascii=False, indent=2, sort_keys=True).splitlines()
    difference = "\n".join(
        difflib.unified_diff(expected_text, actual_text, fromfile="native", tofile="wasm", lineterm="")
    )
    msg = f"native and Wasm compatibility results differ:\n{difference}"
    raise RuntimeError(msg)


def run_suite(root: Path, baseline: Path | None = None) -> str:
    """Run the suite, optionally compare a native baseline, and return JSON."""
    results = collect_results(root)
    if baseline is not None:
        expected = json.loads(baseline.read_text(encoding="utf-8"))
        _compare_results(results, expected)
    return json.dumps(results, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main() -> None:
    """List required assets or run the native compatibility suite."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-assets", action="store_true")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--benchmark-output", type=Path)
    parser.add_argument("--benchmark-only", action="store_true")
    args = parser.parse_args()
    if args.list_assets:
        sys.stdout.write(json.dumps(_ASSETS))
        return
    if args.root is None:
        parser.error("--root is required unless --list-assets is used")
    if args.benchmark_only:
        if args.benchmark_output is None:
            parser.error("--benchmark-only requires --benchmark-output")
        benchmark = json.dumps(
            benchmark_limits(args.root),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        args.benchmark_output.parent.mkdir(parents=True, exist_ok=True)
        args.benchmark_output.write_text(f"{benchmark}\n", encoding="utf-8")
        return
    result = run_suite(args.root, args.baseline)
    if args.output is None:
        sys.stdout.write(f"{result}\n")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{result}\n", encoding="utf-8")
    if args.benchmark_output is not None:
        benchmark = json.dumps(
            benchmark_limits(args.root),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        args.benchmark_output.parent.mkdir(parents=True, exist_ok=True)
        args.benchmark_output.write_text(f"{benchmark}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
