"""Run one logical pylopdf compatibility suite on native Python or Pyodide."""

from __future__ import annotations

import argparse
import dataclasses
import difflib
import gc
import hashlib
import json
import math
import platform
import statistics
import struct
import sys
import tempfile
import time
import warnings
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pylopdf import DocumentComplexity
    from pylopdf import Path as ContentPath

_ASSETS = (
    "tests/assets/real_world/pdf20-simple.pdf",
    "tests/assets/real_world/mhlw-doc.pdf",
    "tests/assets/real_world/f1040.pdf",
    "tests/assets/real_world/senate-expenditures.pdf",
    "tests/assets/real_world/bunka-kokugo-series-019-p4.pdf",
    "tests/assets/real_world/pdfium-type3.pdf",
    "tests/assets/encrypted/user-aes-256.pdf",
    "fonts/pylopdf-fonts-jp/src/pylopdf_fonts_jp/NotoSansJP-Regular.otf",
    "fonts/pylopdf-fonts-he/src/pylopdf_fonts_he/NotoSansHebrew-Variable.ttf",
)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_EXPECTED_TWO_PAGES = 2
_EXPECTED_MERGED_PAGES = 3
_SENATE_ROTATION = 90
_MIN_FORM_ITEMS = 400
# Keep required Unicode fixture data escaped so repository prose remains English.
_RIGHT_COLUMN = "\u53f3\u5074\u5217"
_LEFT_COLUMN = "\u5de6\u5074\u5217"
_CASE_LAW = "\u88c1\u5224\u4f8b"
_JAPANESE = "\u65e5\u672c\u8a9e"
_INVOICE = "\u8acb\u6c42\u66f8"
_HEBREW = "\u05e9\u05dc\u05d5\u05dd"


def _delete_page(document: Any, index: int) -> None:  # noqa: ANN401 - pylopdf imports lazily.
    del document.pages[index]


def _path_paints(path: ContentPath) -> str:
    """Name what a content path paints: "f", "s", or "fs", as pymupdf names it."""
    return ("f" if path.fill is not None else "") + ("s" if path.stroke is not None else "")


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


def _unlock_result(document: Any, password: str) -> str:  # noqa: ANN401 - pylopdf imports lazily.
    """Return the name of the access that ``password`` grants, or of the exception it raises."""
    try:
        return document.unlock(password).name
    except Exception as error:  # noqa: BLE001 - the public exception type is the result.
        return type(error).__name__


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
    pdf20_complexity: DocumentComplexity,
    bounded_scan_complexity: DocumentComplexity,
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
    deep_content = b"BT [" + b"[" * 100_000 + b"(X)" + b"]" * 100_000 + b"] TJ ET"
    nested_content = _build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R >>",
            4: f"<< /Length {len(deep_content)} >>\nstream\n".encode() + deep_content + b"\nendstream",
        }
    )
    deep_program = b"{" * 100_000 + b"}" * 100_000
    type4_header = f"<< /FunctionType 4 /Domain [0 1] /Range [0 1 0 1 0 1] /Length {len(deep_program)} >>"
    deep_functions = _build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << /Shading << /Sh1 5 0 R >> >> "
            "/Contents 4 0 R >>",
            4: b"<< /Length 7 >>\nstream\n/Sh1 sh\nendstream",
            5: "<< /ShadingType 2 /ColorSpace /DeviceRGB /Coords [0 0 100 0] /Function 6 0 R >>",
            6: f"{type4_header}\nstream\n".encode() + deep_program + b"\nendstream",
        }
    )
    chain_length = 2_000
    deep_chain = _build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << /ColorSpace << /Cs1 5 0 R >> >> "
            "/Contents 4 0 R >>",
            4: b"<< /Length 22 >>\nstream\n/Cs1 cs 0 sc 0 0 9 9 re f\nendstream",
            **{
                number: f"[/Indexed {number + 1} 0 R 1 <00 FF>]" if number < 4 + chain_length else "[/DeviceGray]"
                for number in range(5, 5 + chain_length)
            },
        }
    )
    deep_page = pylopdf.open(
        _build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 100 100] >>",
                3: "<< /Type /Page /Parent 2 0 R >>",
                4: "<< /Type /Page /Parent 2 0 R /PieceInfo " + "[" * 80 + "0" + "]" * 80 + " >>",
            }
        )
    )
    pattern_shading = _build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << /Shading << /Sh1 5 0 R >> >> "
            "/Contents 4 0 R >>",
            4: b"<< /Length 7 >>\nstream\n/Sh1 sh\nendstream",
            5: "<< /ShadingType 2 /ColorSpace /Pattern /Coords [0 0 100 0] "
            "/Function << /FunctionType 2 /Domain [0 1] /C0 [0] /C1 [1] /N 1 >> >>",
        }
    )

    def renderer_error(callback: Callable[[], object]) -> list[object]:
        try:
            callback()
        except pylopdf.PdfError as error:
            return [type(error).__name__, "malformed input" in str(error)]
        msg = "malformed rendering unexpectedly succeeded"
        raise RuntimeError(msg)

    unverifiable = _build_limited_content_pdf(b"opaque", "Crypt")
    cyclic = pylopdf.open(_build_reference_cycle_pdf(), limits=pylopdf.DocumentLimits(max_object_depth=16))
    return {
        "file_size": limit_code(
            lambda: pylopdf.open(pdf20_bytes, limits=pylopdf.DocumentLimits(max_file_size=len(pdf20_bytes) - 1))
        ),
        "page_count": limit_code(lambda: pylopdf.open(form_bytes, limits=pylopdf.DocumentLimits(max_pages=1))),
        "decompressed_size": limit_code(
            lambda: pylopdf.open(compressed_bomb, limits=pylopdf.DocumentLimits(max_decompressed_size=1024))
        ),
        "object_depth": limit_code(
            lambda: pylopdf.open(deep_object, limits=pylopdf.DocumentLimits(max_object_depth=4))
        ),
        "unverifiable": limit_code(
            lambda: pylopdf.open(unverifiable, limits=pylopdf.DocumentLimits(max_decompressed_size=1024))
        ),
        # hayro 0.7 overflowed its stack on this stream and hayro 0.8 truncates it; the guard refuses it first.
        "content_nesting": limit_code(pylopdf.open(nested_content).pages[0].text),
        # Neither recursion is a content stream, and hayro 0.7 and 0.8 overflow their stacks on both.
        "resource_nesting_program": limit_code(pylopdf.open(deep_functions).pages[0].text),
        "resource_nesting_chain": limit_code(pylopdf.open(deep_chain).pages[0].text),
        # hayro 0.8 reads no page dictionary nested more than 64 levels deep; only that page is refused.
        "page_object_depth": [limit_code(deep_page.pages[1].text), deep_page.pages[0].text()],
        # hayro 0.8 panics on a shading in a Pattern space (LaurenzV/hayro#1546); the unwind becomes PdfError.
        "renderer_panic": renderer_error(pylopdf.open(pattern_shading).pages[0].pixmap),
        "reference_cycle_pages": len(cyclic.pages),
        "pdf20_complexity": dataclasses.asdict(pdf20_complexity),
        "bounded_scan_complexity": dataclasses.asdict(bounded_scan_complexity),
    }


def _recovery_results(pdf20_bytes: bytes, form_bytes: bytes) -> dict[str, Any]:
    import pylopdf  # noqa: PLC0415

    def corrupt_startxref_keyword(data: bytes) -> bytes:
        position = data.rfind(b"startxref")
        return data[:position] + b"startxrex" + data[position + len(b"startxref") :]

    def shift_startxref(data: bytes, delta: int) -> bytes:
        position = data.rfind(b"startxref") + len(b"startxref")
        start = position + len(data[position:]) - len(data[position:].lstrip())
        end = start
        while data[end : end + 1].isdigit():
            end += 1
        return data[:start] + str(int(data[start:end]) + delta).encode() + data[end:]

    def recovered(data: bytes) -> dict[str, Any]:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            document = pylopdf.open(data)
            probe = pylopdf.probe(data)
        _require(document.is_repaired == probe.is_repaired, "metadata probe disagreed about recovery")
        resaved = pylopdf.open(document.to_bytes())
        _require(not resaved.is_repaired, "saving did not normalize recovered cross-reference data")
        return {
            "repaired": document.is_repaired,
            "pages": len(document.pages),
            "text_sha256": _digest(document.pages[0].text()),
            "warnings": sorted(
                {str(warning.message) for warning in caught if issubclass(warning.category, pylopdf.PylopdfWarning)}
            ),
        }

    def limit_code(callback: Callable[[], object]) -> str:
        try:
            callback()
        except pylopdf.LimitError as error:
            return error.code
        msg = "hostile recovery input unexpectedly opened"
        raise RuntimeError(msg)

    hostile = b"%PDF-1.4\n" + b"stream\n" * 16_384
    return {
        "offset_corrected": recovered(shift_startxref(pdf20_bytes, 12)),
        "rebuilt": recovered(corrupt_startxref_keyword(pdf20_bytes)),
        # lopdf 0.46 rebuilds files that only have cross-reference streams.
        "xref_stream_damage": recovered(corrupt_startxref_keyword(form_bytes)),
        "recovery_scan": [
            limit_code(lambda: pylopdf.open(hostile)),
            limit_code(lambda: pylopdf.probe(hostile)),
        ],
    }


def _consistency_results() -> dict[str, Any]:
    """Read malformed input the same before and after an unrelated edit."""
    import pylopdf  # noqa: PLC0415

    def texts(data: bytes) -> list[list[str]]:
        document = pylopdf.open(data)
        unedited = [document.pages[index].text().splitlines() for index in range(len(document.pages))]
        document.metadata["title"] = "Edited"
        edited = [document.pages[index].text().splitlines() for index in range(len(document.pages))]
        _require(unedited == edited, "an unrelated edit changed extracted text")
        return unedited

    def stream(text: str) -> str:
        content = f"BT /F1 20 Tf 20 100 Td ({text}) Tj ET"
        return f"<< /Length {len(content)} >>\nstream\n{content}\nendstream"

    font = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    page = "BT /F1 20 Tf 20 200 Td (PAGE TEXT) Tj ET q /Fm0 Do Q"
    form = "BT /F1 20 Tf 20 100 Td (FORM TEXT) Tj ET"
    # lopdf cannot parse the bare keyword in `/Type XObject`; hayro reads null.
    bare_keyword = _build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 300 300] >>",
            3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R >> /XObject << /Fm0 6 0 R >> >> >>",
            4: f"<< /Length {len(page)} >>\nstream\n{page}\nendstream",
            5: font,
            6: f"<< /Type XObject /Subtype /Form /BBox [0 0 300 300] /Resources << /Font << /F1 5 0 R >> >> "
            f"/Length {len(form)} >>\nstream\n{form}\nendstream",
        }
    )
    # The page-tree walker skips kid 8, an integer; hayro 0.7 ended the page list there, and hayro 0.8 skips it.
    non_page_kid = _build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [4 0 R 8 0 R 6 0 R] /Count 2 /MediaBox [0 0 300 300] "
            "/Resources << /Font << /F1 3 0 R >> >> >>",
            3: font,
            4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            5: stream("PAGE ONE"),
            6: "<< /Type /Page /Parent 2 0 R /Contents 7 0 R >>",
            7: stream("PAGE TWO"),
            8: "42",
        }
    )
    return {"bare_keyword_form": texts(bare_keyword), "non_page_kid": texts(non_page_kid)}


def _fixture_results(root: Path) -> dict[str, Any]:  # noqa: PLR0915 - explicit compatibility scenarios.
    import pylopdf  # noqa: PLC0415 - list-assets must work without an installed extension.

    real_world = root / "tests/assets/real_world"

    pdf20_bytes = (real_world / "pdf20-simple.pdf").read_bytes()
    pdf20 = pylopdf.open(pdf20_bytes)
    pdf20_text = pdf20.pages[0].text()
    pdf20_markdown = pdf20.markdown()
    _require(len(pdf20.pages) == 1, "PDF 2.0 fixture has the wrong page count")
    _require("Hello World" in pdf20_text, "PDF 2.0 fixture lost Hello World")
    _require("Hello World" in pdf20_markdown, "PDF 2.0 Markdown lost Hello World")

    mhlw = pylopdf.open((real_world / "mhlw-doc.pdf").read_bytes())
    mhlw_text = [mhlw.pages[index].text() for index in range(len(mhlw.pages))]
    mhlw_markdown = mhlw.markdown()
    _require(len(mhlw.pages) == _EXPECTED_TWO_PAGES, "MHLW fixture has the wrong page count")
    _require(_CASE_LAW in mhlw_text[0], "MHLW fixture lost embedded Japanese text")
    _require(_CASE_LAW in mhlw_markdown, "MHLW Markdown lost embedded Japanese text")
    _require(mhlw.text() == "".join(mhlw_text), "batched get_text diverged from per-page extraction")

    form_bytes = (real_world / "f1040.pdf").read_bytes()
    form = pylopdf.open(form_bytes)
    form_text = form.pages[0].text()
    form_markdown = form.pages[0].markdown()
    form_tables = form.pages[0].tables()
    form_paths = form.pages[0].content(text_runs=False, images=False).paths
    _require(
        all(path.fill is not None or path.stroke is not None for path in form_paths),
        "a content path paints nothing",
    )
    _require(len(form.pages) == _EXPECTED_TWO_PAGES, "Form 1040 fixture has the wrong page count")
    _require("U.S. Individual Income Tax Return" in form_text, "Form 1040 text is incomplete")
    _require(len(form_tables) == 1, "Form 1040 bordered table was not detected")
    form_table = form_tables[0]
    _require((len(form_table.rows), len(form_table.rows[0])) == (2, 7), "Form 1040 table shape changed")
    _require("Full-time<br>student" in form_markdown, "Form 1040 table was not integrated into Markdown")
    _require(len(form_paths) >= _MIN_FORM_ITEMS, "Form 1040 vector extraction is incomplete")

    senate = pylopdf.open((real_world / "senate-expenditures.pdf").read_bytes())
    senate_lines = senate.pages[0].tables()
    senate_text = senate.pages[0].tables(strategy="text")
    _require(senate.pages[0].rotation == _SENATE_ROTATION, "Senate fixture lost its page rotation")
    _require(
        len(senate_lines) == 1 and (len(senate_lines[0].rows), len(senate_lines[0].rows[0])) == (2, 7),
        "Senate bordered header changed",
    )
    _require(
        len(senate_text) == 1 and (len(senate_text[0].rows), len(senate_text[0].rows[0])) == (10, 3),
        "Senate borderless table changed",
    )
    first_senate_row = list(senate_text[0].rows[0])
    _require(
        first_senate_row == ["BAIN, J MATTHEW", "DISTRICT DIRECTOR", "37,499.96"],
        "Senate rotated reading order changed",
    )

    scan_bytes = (real_world / "bunka-kokugo-series-019-p4.pdf").read_bytes()
    scan = pylopdf.open(scan_bytes)
    _require(len(scan.pages) == 1, "Japanese scan has the wrong page count")
    _require(scan.pages[0].text() == "", "image-only Japanese scan unexpectedly exposed text")
    bounded_scan = pylopdf.open(scan_bytes, limits=pylopdf.DocumentLimits.web())
    _require(len(bounded_scan.pages) == 1, "web limits rejected a representative scan")
    scan_inventory = scan.pages[0].resources.images
    _require(len(scan_inventory) >= 1, "the scan lists no image XObject")
    scan_placements = scan.pages[0].content(text_runs=False, paths=False).images
    _require(
        {image.xref for image in scan_placements} <= {item.xref for item in scan_inventory},
        "a drawn image is missing from the inventory",
    )
    scan_image = scan_inventory[0].extract()
    scan_pixmap = scan_inventory[0].to_pixmap()

    encrypted_bytes = (root / "tests/assets/encrypted/user-aes-256.pdf").read_bytes()
    encrypted = pylopdf.open(encrypted_bytes)
    encrypted_error = _exception_name(lambda: len(encrypted.pages))
    _require(encrypted.needs_password, "encrypted fixture did not require a password")
    _require(encrypted_error == "EncryptedDocumentError", "encrypted access returned the wrong exception")
    decrypted = pylopdf.open(encrypted_bytes, password="userpw")  # noqa: S106
    _require(len(decrypted.pages) == _EXPECTED_TWO_PAGES, "AES-256 fixture did not decrypt")
    _require("Encrypted page one" in decrypted.pages[0].text(), "decrypted text is incomplete")
    authentication_error = _exception_name(
        lambda: pylopdf.open(encrypted_bytes, password="wrong")  # noqa: S106
    )
    _require(authentication_error == "PasswordError", "wrong password returned the wrong exception")
    _require(pylopdf.Document().path is None, "an empty document reported a path")
    unlocked = [_unlock_result(decrypted, password) for password in ("wrong", "userpw", "ownerpw")]
    _require(unlocked == ["PasswordError", "USER", "OWNER"], "a decrypted document unlocked with the wrong access")
    encryption_description = decrypted.encryption_method
    _require(encryption_description == "Standard V5 R6 256-bit AES", "encryption_method described the wrong encryption")
    granted = pylopdf.Permissions.PRINT | pylopdf.Permissions.COPY
    reencrypted = decrypted.to_bytes(encryption=pylopdf.Encryption(user_password="user", permissions=granted))  # noqa: S106
    _require(pylopdf.open(reencrypted).needs_password, "Encryption did not encrypt")
    reopened_permissions = pylopdf.open(reencrypted, password="user").permissions  # noqa: S106
    _require(reopened_permissions == pylopdf.Permissions.ALL, "the owner password did not default to the user password")
    object_streams_error = _exception_name(
        lambda: decrypted.to_bytes(object_streams=True, encryption=pylopdf.Encryption())
    )
    _require(object_streams_error == "ValueError", "encryption with object streams was not refused")

    return {
        "pdf20": {
            "pages": len(pdf20.pages),
            "text_sha256": _digest(pdf20_text),
            "markdown_sha256": _digest(pdf20_markdown),
        },
        "mhlw_cjk": {
            "pages": len(mhlw.pages),
            "page_text_sha256": [_digest(text) for text in mhlw_text],
            "markdown_sha256": _digest(mhlw_markdown),
        },
        "form_table": {
            "pages": len(form.pages),
            "text_sha256": _digest(form_text),
            "markdown_sha256": _digest(form_markdown),
            "shape": [len(form_table.rows), len(form_table.rows[0])],
            "confidence": round(form_table.diagnostics.confidence, 6),
            "path_count": len(form_paths),
            "path_paints": sorted({_path_paints(path) for path in form_paths}),
        },
        "rotated_tables": {
            "rotation": senate.pages[0].rotation,
            "bordered_shape": [len(senate_lines[0].rows), len(senate_lines[0].rows[0])],
            "borderless_shape": [len(senate_text[0].rows), len(senate_text[0].rows[0])],
            "first_row": first_senate_row,
        },
        "image_only": {
            "pages": len(scan.pages),
            "text": scan.pages[0].text(),
            "inventory": [
                [image.xref, image.width, image.height, image.bits, image.colorspace, list(image.filters)]
                for image in scan_inventory
            ],
            "placements": len(scan_placements),
            "extracted": [scan_image.format, list(scan_image.resolution), hashlib.sha256(scan_image.data).hexdigest()],
            "pixmap": [scan_pixmap.width, scan_pixmap.height, scan_pixmap.channels],
        },
        "encryption": {
            "locked_exception": encrypted_error,
            "wrong_password_exception": authentication_error,
            "decrypted_pages": len(decrypted.pages),
            "unlock": unlocked,
            "metadata_encryption": encryption_description,
            "object_streams_exception": object_streams_error,
        },
        "limits": _limit_results(
            pdf20_bytes,
            form_bytes,
            pdf20.complexity,
            bounded_scan.complexity,
        ),
        "recovery": _recovery_results(pdf20_bytes, form_bytes),
        "consistency": _consistency_results(),
    }


def _layout_results() -> dict[str, Any]:
    import pylopdf  # noqa: PLC0415

    vertical = pylopdf.open(_build_vertical_cjk_pdf()).pages[0]
    vertical_lines = [line for block in vertical.layout().to_dict()["blocks"] for line in block["lines"]]
    vertical_text = vertical.text().splitlines()
    _require(
        vertical_text == ["Heading", _RIGHT_COLUMN, _LEFT_COLUMN, "Footer"],
        "vertical CJK reading order changed",
    )
    writing_modes = [line["wmode"] for line in vertical_lines]
    _require(writing_modes == [0, 1, 1, 0], "vertical CJK writing modes changed")
    _require(len(vertical.search(_RIGHT_COLUMN)) == 1, "vertical CJK search failed")

    columns = pylopdf.open(_build_multicolumn_pdf()).pages[0]
    column_lines = columns.text().splitlines()
    expected_columns = [
        "A heading spanning both columns",
        "Left one",
        "Left two",
        "Right one",
        "Right two",
        "A footer spanning both columns",
    ]
    _require(column_lines == expected_columns, "multicolumn reading order changed")
    raw_columns = columns.layout(chars=True).to_dict()
    raw_lines = [
        "".join(char["c"] for span in line["spans"] for char in span["chars"])
        for block in raw_columns["blocks"]
        for line in block["lines"]
    ]
    _require(raw_lines == expected_columns, "rawdict mapped-glyph reading order changed")
    raw_origins = [
        [round(value, 3) for value in line["spans"][0]["chars"][0]["origin"]]
        for block in raw_columns["blocks"]
        for line in block["lines"]
    ]
    _require(
        raw_origins == [[20, 30], [40, 70], [40, 90], [200, 70], [200, 90], [20, 270]],
        "rawdict baseline origins changed",
    )
    raw_spans = [span for block in raw_columns["blocks"] for line in block["lines"] for span in line["spans"]]
    raw_chars = [char for span in raw_spans for char in span["chars"]]
    _require(all("text" not in span for span in raw_spans), "rawdict spans contain synthesized text")
    _require(
        all(len(char["bbox"]) == 4 and len(char["origin"]) == 2 for char in raw_chars),  # noqa: PLR2004 - tuple arities.
        "rawdict glyph geometry shape changed",
    )
    _require(
        all(math.isfinite(value) for char in raw_chars for value in (*char["bbox"], *char["origin"])),
        "rawdict glyph geometry is not finite",
    )
    limited_columns = pylopdf.open(_build_multicolumn_pdf(), limits=pylopdf.DocumentLimits(max_text_size=8))
    try:
        limited_columns.pages[0].text()
    except pylopdf.LimitError as error:
        text_limit_code = error.code
    else:
        msg = "text budget unexpectedly admitted the multicolumn fixture"
        raise RuntimeError(msg)

    return {
        "vertical_cjk": {
            "lines": vertical_text,
            "writing_modes": writing_modes,
            "markdown_sha256": _digest(vertical.markdown()),
        },
        "columns": {
            "lines": column_lines,
            "word_count": len(columns.words()),
        },
        "rawdict": {
            "lines": raw_lines,
            "glyph_count": len(raw_chars),
            "line_origins": raw_origins,
        },
        "text_limit_code": text_limit_code,
    }


def _font_results(page: Any, font: bytes) -> dict[str, Any]:  # noqa: ANN401 - pylopdf imports lazily.
    """Draw with a reusable Font and require its measurements to match the drawing."""
    import pylopdf  # noqa: PLC0415

    face = pylopdf.Font.from_bytes(font)
    text = _JAPANESE + "AVA"
    measured = face.text_width(text, 18)
    with page.draw() as canvas:
        canvas.text(text, (20, 115), font=face, size=18)
    drawn = [
        span
        for block in page.layout(chars=True).to_dict()["blocks"]
        for line in block["lines"]
        for span in line["spans"]
        if abs(span["origin"][1] - 115) < 1
    ]
    _require(len(drawn) == 1, "a Font run did not extract as one span")
    _require(
        abs(drawn[0]["chars"][-1]["bbox"][2] - (20 + measured)) < 0.01,  # noqa: PLR2004 - PDF real precision.
        "Font.text_width differs from the drawn advance",
    )
    # A textbox exactly as tall as text_height measures fits with nothing to spare.
    needed = face.text_height(_JAPANESE * 6, 10, width=140)
    with pylopdf.Document().pages.new((200, 200)).draw() as canvas:
        fit = canvas.textbox((0, 0, 140, needed), _JAPANESE * 6, font=face, size=10)
    _require(fit.spare_height == 0, "Font.text_height differs from the height a textbox needs")
    standard = pylopdf.Font.standard("Helvetica")
    return {
        "name": face.name,
        "weight": face.weight,
        "is_bold": face.is_bold,
        "is_serif": face.is_serif,
        "units_per_em": face.units_per_em,
        "ascender": face.ascender,
        "descender": face.descender,
        "text_width": measured,
        "text_height": needed,
        "glyphs": [face.has_glyph(character) for character in [*_JAPANESE, "\U0001f980"]],
        "standard": [
            standard.name,
            standard.text_width("AVA", 18),
            standard.has_glyph("A"),
            standard.has_glyph("\u3042"),
            standard.ascender,
            standard.descender,
        ],
    }


def _rotated_canvas_results(font: bytes) -> dict[str, Any]:
    """Draw paths and mixed-font text with one canvas on a rotated page and record what it placed."""
    import pylopdf  # noqa: PLC0415
    from pylopdf import Color, PathBuilder, Stroke  # noqa: PLC0415

    face = pylopdf.Font.from_bytes(font)
    document = pylopdf.Document()
    page = document.pages.new((300, 200))
    page.rotation = 90
    stroke = Stroke(paint=Color(0.2, 0.4, 0.6), width=1.5, dashes=(3, 2))
    fill = Color(0.9, 0.95, 1)
    with page.draw() as canvas:
        canvas.rect((20, 20, 180, 120), radius=16, fill=fill, stroke=stroke)
        canvas.circle((100, 160), 20, fill=fill, stroke=stroke)
        canvas.path(PathBuilder().sector((60, 250), 30, 0, 135), fill=fill, stroke=stroke)
        canvas.path(PathBuilder().zigzag((20, 280), (180, 280), breadth=4), stroke=stroke)
        canvas.text(_JAPANESE, (30, 50), font=face, size=14)
        canvas.text("Standard 14\nsecond line", (30, 80), size=10, fill=Color(0.5, 0.5, 0.5))
        canvas.text(_JAPANESE + "AVA", (30, 110), font=face, size=12, fill=Color(0, 1, 0))
        canvas.text("spaced\nlines", (120, 50), size=8, line_height=2)
        canvas.text(_JAPANESE + "\n" + _JAPANESE, (120, 80), font=face, size=8, line_height=1.5)
        fit = canvas.textbox((30, 180, 170, 260), _JAPANESE * 6, font=face, size=10)
        canvas.text("turned", (190, 20), size=8, rotate=90)
        turned = canvas.textbox((175, 120, 198, 260), _JAPANESE, font=face, size=8, rotate=270)
    paths = page.content().paths
    _require(len(paths) == 4, "a canvas did not draw one path per call")  # noqa: PLR2004 - four paths.
    data = document.to_bytes()
    _require(data.count(b"/FontFile") == 1, "a canvas embedded a font more than once")
    words = [[word.text, round(word.bbox.x0, 2), round(word.bbox.y0, 2)] for word in page.words()]
    return {
        "drawing": [[_path_paints(path), [round(value, 2) for value in path.bbox]] for path in paths],
        "spare": round(fit.spare_height, 4),
        "turned_spare": round(turned.spare_height, 4),
        "turned_directions": [
            [round(run.glyphs[0].matrix.a, 4), round(run.glyphs[0].matrix.b, 4)] for run in page.content().text_runs
        ][-2:],
        "words": words,
    }


def _variable_font_results(font: bytes) -> dict[str, Any]:
    """Select weights of a variable font from bytes and require the bold instance to draw its measured width.

    Noto Sans Hebrew's default instance is Thin, so an omitted weight and
    ``weight=400`` load different instances.
    """
    import pylopdf  # noqa: PLC0415

    as_given = pylopdf.Font.from_bytes(font)
    regular = pylopdf.Font.from_bytes(font, weight=400)
    bold = pylopdf.Font.from_bytes(font, weight=700)
    measured = bold.text_width(_HEBREW, 18)
    document = pylopdf.Document()
    page = document.pages.new((240, 80))
    with page.draw() as canvas:
        canvas.text(_HEBREW, (20, 50), font=bold, size=18)
    spans = [
        span
        for block in page.layout(chars=True).to_dict()["blocks"]
        for line in block["lines"]
        for span in line["spans"]
    ]
    _require(len(spans) == 1, "a variable-font instance did not extract as one span")
    _require(spans[0]["font"] == "NotoSansHebrew-Bold", "a variable-font instance was embedded under another name")
    _require(
        abs(max(char["bbox"][2] for char in spans[0]["chars"]) - (20 + measured)) < 0.01,  # noqa: PLR2004 - PDF real precision.
        "a variable-font instance's text_width differs from the drawn advance",
    )
    return {
        "faces": [[face.name, face.weight] for face in (as_given, regular, bold)],
        "advances": [face.text_width(_HEBREW, 1000) for face in (as_given, regular, bold)],
        "text_width": measured,
        "outside_axis": _exception_name(lambda: pylopdf.Font.from_bytes(font, weight=1000)),
        "form": _variable_font_form_results(bold),
    }


def _variable_font_form_results(bold: Any) -> dict[str, Any]:  # noqa: ANN401 - pylopdf imports lazily.
    """Fill a right-aligned text field and a centered comb field with a bold instance.

    Each widget's text area is inset 2 points, so the text field's area ends
    at x = 278 and the six comb positions, 256 / 6 points each, start at
    x = 22. Both values fit at the 12-point size that auto-fitting starts from.
    """
    import pylopdf  # noqa: PLC0415

    document = pylopdf.open(
        _build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [4 0 R 5 0 R] /DA (/Helv 0 Tf 0 g) >> >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 300 200] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots [4 0 R 5 0 R] >>",
                4: "<< /FT /Tx /Q 2 /T (name) /Type /Annot /Subtype /Widget /Rect [20 140 280 170] /P 3 0 R /F 4 >>",
                5: (
                    "<< /FT /Tx /Ff 16777216 /MaxLen 6 /Q 1 /T (code) /Type /Annot /Subtype /Widget"
                    " /Rect [20 80 280 110] /P 3 0 R /F 4 >>"
                ),
            },
        )
    )
    document.form.font = bold
    document.form["name"].value = _HEBREW
    document.form["code"].value = "A12"
    lines = [line for block in document.pages[0].layout(chars=True).to_dict()["blocks"] for line in block["lines"]]
    _require(len(lines) == 2, "the filled fields did not extract as two lines")  # noqa: PLR2004 - two fields.
    name, code = sorted(lines, key=lambda line: line["bbox"][1])
    fonts = sorted({span["font"] for line in lines for span in line["spans"]})
    _require(fonts == ["NotoSansHebrew-Bold"], "a form appearance embedded another instance")
    _require(
        abs(name["bbox"][0] - (278 - bold.text_width(_HEBREW, 12))) < 0.01,  # noqa: PLR2004 - PDF real precision.
        "a right-aligned form appearance did not end its text at the measured advance",
    )
    cell = 256 / 6
    origins = sorted(char["origin"][0] for span in code["spans"] for char in span["chars"])
    expected = [22 + (1 + index) * cell + (cell - bold.text_width(char, 12)) / 2 for index, char in enumerate("A12")]
    _require(
        len(origins) == len(expected) and all(abs(a - b) < 0.01 for a, b in zip(origins, expected, strict=True)),  # noqa: PLR2004 - PDF real precision.
        "comb positions differ from the measured advances",
    )
    reopened = pylopdf.open(document.to_bytes())
    return {
        "values": sorted([name, field.value] for name, field in reopened.form.items()),
        "fonts": fonts,
        "standard_font": _exception_name(lambda: setattr(document.form, "font", pylopdf.Font.standard("Helvetica"))),
    }


def _generation_results(root: Path) -> dict[str, Any]:  # noqa: PLR0915 - explicit compatibility scenarios.
    import pylopdf  # noqa: PLC0415

    font = (root / "fonts/pylopdf-fonts-jp/src/pylopdf_fonts_jp/NotoSansJP-Regular.otf").read_bytes()
    document = pylopdf.Document()
    _require(len(document.pages) == 0, "new document is not empty")
    first = document.pages.new((240, 180))
    with first.draw() as canvas:
        canvas.text("Generated on Wasm", (20, 35), size=14)
        canvas.text(_JAPANESE, (20, 75), font=pylopdf.Font.from_bytes(font), size=18)
    font_results = _font_results(first, font)
    first_pixmap = first.pixmap(matrix=pylopdf.Matrix(0.5, 0.5), alpha=True)
    _require((first_pixmap.width, first_pixmap.height) == (120, 90), "generated page raster size changed")
    _require(
        bytes(first.pixmap(matrix=pylopdf.Matrix(0.5, 0.5), alpha=True)) == bytes(first_pixmap),
        "a repeated render on the retained cache changed the raster",
    )
    pixmap_buffer = memoryview(first_pixmap)
    _require(bytes(pixmap_buffer) == bytes(first_pixmap), "the Pixmap buffer differs from its pixels")
    _require(bytes(pylopdf.Pixmap.from_array(first_pixmap)) == bytes(first_pixmap), "from_array changed the pixels")
    gray_pixmap = first.pixmap(matrix=pylopdf.Matrix(0.5, 0.5), colorspace="gray")

    second = document.pages.new((180, 240))
    stale_error = _exception_name(lambda: first.rect)
    _require(stale_error == "StalePageError", "structural edit returned the wrong stale-page exception")
    with second.draw() as canvas:
        spare_height = canvas.textbox(
            (20, 20, 160, 100),
            "Second page wraps text without native filesystem access.",
            size=12,
        ).spare_height
    _require(spare_height >= 0, "generated textbox unexpectedly overflowed")

    serialized = document.to_bytes()
    reopened = pylopdf.open(serialized)
    texts = [reopened.pages[index].text().strip() for index in range(len(reopened.pages))]
    _require(len(reopened.pages) == _EXPECTED_TWO_PAGES, "generated document has the wrong page count")
    _require("Generated on Wasm" in texts[0] and _JAPANESE in texts[0], "generated text did not round-trip")
    _require("Second page wraps text" in texts[1], "textbox text did not round-trip")

    serial_renders = reopened.render([1, 0, 1], matrix=pylopdf.Matrix(0.5, 0.5), alpha=True, workers=1)
    requested_parallel_renders = reopened.render([1, 0, 1], matrix=pylopdf.Matrix(0.5, 0.5), alpha=True, workers=4)
    _require(serial_renders == requested_parallel_renders, "workers=4 changed serial-fallback rendering")
    render_sizes = [_png_size(data) for data in requested_parallel_renders]
    _require(render_sizes == [[90, 120], [120, 90], [90, 120]], "batch rendering lost input order")

    merged = pylopdf.Document()
    merged.pages.extend(reopened.pages)
    merged.pages.select([1, 0, 1])
    merged_texts = [merged.pages[index].text().strip() for index in range(len(merged.pages))]
    _require(len(merged.pages) == _EXPECTED_MERGED_PAGES, "merge/select produced the wrong page count")
    _require("Second page wraps text" in merged_texts[0], "merge/select changed page order")
    _require("Generated on Wasm" in merged_texts[1], "merge/select lost generated content")

    selected = pylopdf.open(serialized)
    selected.pages.select([1])
    _require(len(selected.pages) == 1, "page selection did not produce a one-page document")
    _require("Second page wraps text" in selected.pages[0].text(), "page selection retained the wrong page")

    with tempfile.TemporaryDirectory(prefix="pylopdf-pyodide-") as directory:
        saved_path = Path(directory) / "roundtrip.pdf"
        reopened.save(saved_path)
        saved = pylopdf.open(saved_path)
        _require(len(saved.pages) == _EXPECTED_TWO_PAGES, "virtual-filesystem save did not round-trip")

    closed_page = reopened.pages[0]
    reopened.close()
    closed_error = _exception_name(lambda: closed_page.rect)
    _require(closed_error == "DocumentClosedError", "closed page returned the wrong exception")

    malformed_error = _exception_name(lambda: pylopdf.open(b"not a PDF"))
    _require(malformed_error == "MalformedPdfError", "malformed bytes returned the wrong exception")
    empty_error = _exception_name(lambda: pylopdf.open(b""))
    _require(empty_error == "MalformedPdfError", "empty bytes returned the wrong exception")
    recovery = pylopdf.open(serialized)
    _require(len(recovery.pages) == _EXPECTED_TWO_PAGES, "runtime did not recover after malformed input")

    return {
        "empty_pages": 0,
        "roundtrip_pages": len(document.pages),
        "font": font_results,
        "rotated_canvas": _rotated_canvas_results(font),
        "variable_font": _variable_font_results(
            (root / "fonts/pylopdf-fonts-he/src/pylopdf_fonts_he/NotoSansHebrew-Variable.ttf").read_bytes(),
        ),
        "text_sha256": [_digest(text) for text in texts],
        "first_pixmap": [first_pixmap.width, first_pixmap.height, first_pixmap.channels],
        "first_pixmap_buffer": [
            pixmap_buffer.readonly,
            pixmap_buffer.format,
            list(pixmap_buffer.shape or ()),
            pixmap_buffer.nbytes,
        ],
        "gray_pixmap": [
            list(memoryview(gray_pixmap).shape or ()),
            hashlib.sha256(bytes(gray_pixmap)).hexdigest(),
            hashlib.sha256(gray_pixmap.to_bytes("jpeg", quality=80)).hexdigest(),
        ],
        "batch_render_sizes": render_sizes,
        "merged_text_sha256": [_digest(text) for text in merged_texts],
        "selected_text_sha256": _digest(selected.pages[0].text()),
        "exceptions": {
            "stale": stale_error,
            "closed": closed_error,
            "malformed": malformed_error,
            "empty": empty_error,
        },
    }


def _form_page_deletion_results() -> dict[str, Any]:
    """Remove the form widgets and fields of pages that deletion and selection drop."""
    import pylopdf  # noqa: PLC0415

    widget = "/Type /Annot /Subtype /Widget /Rect [10 10 90 30]"
    data = _build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [5 0 R 8 0 R 9 0 R] /CO [6 0 R 8 0 R] >> >>",
            2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 100 100] >>",
            3: "<< /Type /Page /Parent 2 0 R /Annots [6 0 R 8 0 R] >>",
            4: "<< /Type /Page /Parent 2 0 R /Annots [7 0 R] >>",
            5: "<< /FT /Tx /T (person) /Kids [6 0 R 7 0 R] >>",
            6: f"<< /T (first) /V (Ada) /Parent 5 0 R {widget} /P 3 0 R >>",
            7: f"<< /T (last) /V (Lovelace) /Parent 5 0 R {widget} /P 3 0 R >>",
            8: f"<< /FT /Tx /T (total) /V (42) {widget} /P 3 0 R >>",
            9: "<< /FT /Tx /T (hidden) /V (kept) >>",
        }
    )

    def names(document: Any) -> list[str]:  # noqa: ANN401 - pylopdf imports lazily.
        return list(document.form)

    results: dict[str, Any] = {}
    edits = (("delete_first", lambda doc: _delete_page(doc, 0)), ("select_first", lambda doc: doc.pages.select([0])))
    for label, edit in edits:
        document = pylopdf.open(data)
        edit(document)
        saved = document.to_bytes()
        reopened = pylopdf.open(saved)
        _require(names(reopened) == names(document), f"{label} fields changed after a save")
        results[label] = {"fields": names(document), "widgets": saved.count(b"/Subtype/Widget")}
    _require(results["delete_first"]["fields"] == ["hidden", "person.last"], "delete kept fields of a deleted page")
    _require(results["select_first"]["fields"] == ["hidden", "person.first", "total"], "select dropped kept fields")
    return results


def _structure_page_deletion_results() -> dict[str, Any]:
    """Remove the structure content of pages that deletion and selection drop."""
    import pylopdf  # noqa: PLC0415

    data = _build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 5 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 100 100] >>",
            3: "<< /Type /Page /Parent 2 0 R /StructParents 0 >>",
            4: "<< /Type /Page /Parent 2 0 R /StructParents 1 /Annots [10 0 R] >>",
            5: "<< /Type /StructTreeRoot /K 6 0 R /ParentTree << /Nums [0 [7 0 R] 1 [8 0 R] 2 9 0 R] >> >>",
            6: "<< /S /Document /P 5 0 R /K [7 0 R 8 0 R 9 0 R] >>",
            7: "<< /S /P /P 6 0 R /Pg 3 0 R /K 0 >>",
            8: "<< /S /P /P 6 0 R /Pg 4 0 R /K [0 << /Type /MCR /MCID 1 >>] >>",
            9: "<< /S /Link /P 6 0 R /K << /Type /OBJR /Obj 10 0 R >> >>",
            10: "<< /Type /Annot /Subtype /Link /Rect [10 10 90 30] /P 4 0 R /StructParent 2 >>",
        }
    )

    results: dict[str, Any] = {}
    edits = (("delete_second", lambda doc: _delete_page(doc, 1)), ("select_first", lambda doc: doc.pages.select([0])))
    for label, edit in edits:
        document = pylopdf.open(data)
        edit(document)
        saved = document.to_bytes()
        results[label] = {
            "pages": saved.count(b"/Type/Page") - saved.count(b"/Type/Pages"),
            "elements": saved.count(b"/S/"),
            "annotations": saved.count(b"/Type/Annot"),
            "parent_tree": saved.count(b"/Nums[0[7 0 R]]"),
        }
    expected = {"pages": 1, "elements": 2, "annotations": 0, "parent_tree": 1}
    _require(results["delete_second"] == expected, "delete kept the structure of a deleted page")
    _require(results["select_first"] == expected, "select kept the structure of an excluded page")

    depth = 257
    deep: dict[int, str] = {
        1: "<< /Type /Catalog /Pages 2 0 R /StructTreeRoot 5 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 100 100] >>",
        3: "<< /Type /Page /Parent 2 0 R >>",
        4: "<< /Type /Page /Parent 2 0 R >>",
        5: "<< /Type /StructTreeRoot /K 6 0 R >>",
    }
    for level in range(depth):
        deep[6 + level] = f"<< /S /Div /K [{7 + level} 0 R] >>" if level < depth - 1 else "<< /S /P /Pg 4 0 R /K 0 >>"
    document = pylopdf.open(_build_raw_pdf(deep))
    try:
        del document.pages[1:2]
    except pylopdf.LimitError as error:
        results["deep_refusal"] = {"code": error.code, "pages": len(document.pages)}
    _require(
        results.get("deep_refusal") == {"code": "structure_tree_count", "pages": 2},
        "a structure tree beyond 256 levels was not refused",
    )

    return results


def _save_option_results() -> dict[str, Any]:
    """Compress, prune, merge identical objects, and compact numbering with the save options."""
    import pylopdf  # noqa: PLC0415

    data = _build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 200 200] >>",
            3: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R /Resources << /Font << /F1 7 0 R >> >> >>",
            4: "<< /Type /Page /Parent 2 0 R /Contents 6 0 R /Resources << /Font << /F1 8 0 R >> >> >>",
            5: "<< /Length 31 >>\nstream\nBT /F1 12 Tf 20 100 Td (A) Tj ET\nendstream",
            6: "<< /Length 31 >>\nstream\nBT /F1 12 Tf 20 100 Td (A) Tj ET\nendstream",
            7: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            8: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            9: "(UNREACHABLE)",
        }
    )
    options: dict[str, dict[str, bool]] = {
        "plain": {"compress": False, "prune": False},
        "default": {},
        "renumber": {"renumber": True},
        "dedupe": {"dedupe": True, "renumber": True},
    }
    results: dict[str, Any] = {}
    for name, option in options.items():
        document = pylopdf.open(data)
        saved = document.to_bytes(**option)
        reopened = pylopdf.open(saved)
        _require(
            [page.text().strip() for page in reopened.pages] == ["A", "A"],
            f"saving with {option} changed the text",
        )
        results[name] = {
            "objects": saved.count(b" 0 obj"),
            "fonts": saved.count(b"/BaseFont/Helvetica"),
            "pages": saved.count(b"/Type/Page") - saved.count(b"/Type/Pages"),
        }
    _require(results["plain"]["objects"] == 9, "prune=False removed objects")  # noqa: PLR2004 - the input's objects.
    _require(results["dedupe"] == {"objects": 6, "fonts": 1, "pages": 2}, "dedupe did not merge duplicates")
    return results


def _geometry_results() -> dict[str, Any]:
    """Check immutable affine values and their use by the PDF drawing API."""
    from pylopdf import Color, Document, LineTo, Matrix, MoveTo, Point, Stroke, open  # noqa: A004, PLC0415

    transform = Matrix(2, 3) * Matrix(1, 0, 0, 1, 20, 30)
    point = Point(10, 20)
    mapped = point * transform
    _require(mapped == (40, 90), "affine composition order changed")
    _require(mapped / transform == point, "inverse point transformation changed")
    _require(Point(1, 0) * Matrix(90) == (0, 1), "right-angle rotation changed")
    _require(not Matrix() and not Point(), "zero geometry must be false")
    _require(hash(point) == hash((10.0, 20.0)), "point hashing lost tuple semantics")
    _require(~Matrix(1, 2, 2, 4, 5, 6) == Matrix(), "singular inverse must be zero")
    try:
        point / Matrix()
    except ZeroDivisionError:
        pass
    else:
        msg = "division by a singular matrix did not raise"
        raise RuntimeError(msg)
    with Document() as document:
        page_size = 200
        page = document.pages.new((page_size, page_size))
        with page.draw() as canvas:
            canvas.line(mapped, mapped + Point(40, 0), stroke=Stroke(paint=Color(0, 0, 0), width=2))
            canvas.text("Affine", mapped + Point(0, 30))
        saved = document.to_bytes()
    with open(saved) as reopened:
        text = reopened.pages[0].text().strip()
        _require(text == "Affine", "transformed text failed to survive save/reopen")
        line = reopened.pages[0].content().paths[0].segments
        _require(line == (MoveTo(Point(40, 90)), LineTo(Point(80, 90))), "transformed line changed")
        _require(reopened.pages[0].pixmap(alpha=True).width == page_size, "transformed drawing failed to render")
    rectangles = _rectangle_results()
    return {
        "matrix": list(transform),
        "point": list(mapped),
        "inverse_point": list(mapped / transform),
        "text": text,
        **rectangles,
    }


def _rectangle_results() -> dict[str, Any]:  # noqa: PLR0915 - explicit compatibility scenarios.
    """Check Rect, IRect, and Quad operators and their use with page geometry."""
    from pylopdf import Document, Font, Highlight, IRect, Matrix, Point, Quad, Rect  # noqa: PLC0415

    rect = Rect(1, 2, 3, 4)
    _require(rect | Point(10, 10) == (1, 2, 10, 10), "rectangle union with a point changed")
    _require(rect & Rect(2, 3, 5, 5) == (2, 3, 3, 4), "rectangle intersection changed")
    _require(rect * Matrix(90) == (-4, 1, -2, 3), "rectangle rotation changed")
    _require(Rect(3, 4, 1, 2).width == 0, "rectangle width must not be negative")
    _require(Point(1, 2) in rect and Point(3, 4) not in rect, "point containment changed")
    _require((Rect.INFINITE * Matrix(30)).is_infinite, "infinite rectangle changed under rotation")
    _require(IRect(1.5, 2.5, 3.5, 4.5) == (1, 2, 4, 5), "integer rectangle rounding changed")
    _require(Rect(-0.5, -1.5, 2.0001, 3.9999).round() == (-1, -2, 2, 4), "rectangle rounding changed")
    quad = rect.quad * Matrix(90)
    _require(quad.is_rectangular and quad.rect == rect * Matrix(90), "rotated quad changed")
    _require(Point(-3, 2) in quad, "quad containment changed")
    _require(Quad(quad) == quad and abs(rect.quad) == abs(rect), "quad round trip changed")
    with Document() as document:
        page = document.pages.new((300, 200))
        page.rotation = 90
        box = page.rect * Matrix(0.5, 0.5) + 20
        with page.draw() as canvas:
            canvas.text("Boxed", box.tl + Point(10, 30))
        (hit,) = page.search("Boxed")
        boxed = document.pages.new((600, 800))
        boxed.boxes.crop = (100, 450, 500, 750)
        boxed.boxes.trim = (110, 460, 490, 740)
        cropbox = list(boxed.boxes.crop)
        crop_rect = list(boxed.rect)
        display_matrix = list(boxed.user_to_display)
        trimmed = list(Rect(boxed.boxes.trim) * boxed.user_to_display)
        boxed.boxes.media = (0, 0, 200, 300)
        reset_cropbox = list(boxed.boxes.crop)
        with boxed.draw() as canvas:
            canvas.image((10, 10, 60, 60), document.pages[0].pixmap(matrix=Matrix(0.1, 0.1), alpha=True))
            canvas.page((60, 60, 160, 160), document.pages[0])
        (image,) = boxed.content(text_runs=False, paths=False).images
        placed = [document.objects[image.xref]["Subtype"], [round(value, 2) for value in image.bbox], boxed.text()]
        highlight = boxed.annotations.add(Highlight(Rect(20, 20, 80, 40), contents="note"))
        boxed.links.add(Rect(20, 50, 80, 70), "https://e.test/")
        annotations = [(annotation.type.name, annotation.contents) for annotation in boxed.annotations]
        _require(highlight.xref > 0 and highlight in boxed.annotations, "the highlight returned no handle")
        with boxed.draw() as canvas:
            canvas.text("Full name", (20, 250), font=Font.standard("Times-Bold"))
        full_name = "/BaseFont/Times-Bold" in document.to_bytes().decode("latin-1")
    _require(isinstance(hit, Rect) and hit in box and hit.intersects(box), "search hit left its box")
    _require(cropbox == [100, 450, 500, 750] and crop_rect == [0, 0, 400, 300], "the CropBox changed")
    _require(display_matrix == [1, 0, 0, -1, -100, 750], "user_to_display changed")
    _require(trimmed == [10, 10, 390, 290], "the TrimBox did not map to display space")
    _require(reset_cropbox == [0, 0, 200, 300], "assigning the MediaBox kept the CropBox")
    # The 20x30 pixmap of the rotated first page keeps its aspect ratio in the square.
    _require(placed == ["Image", [18.33, 10, 51.67, 60], "Boxed\n"], "the canvas did not place the image and the page")
    _require(annotations == [("HIGHLIGHT", "note")], "annotation handles changed")
    _require(full_name, "Font.standard did not select its Standard 14 font")
    return {
        "cropbox": cropbox,
        "display_matrix": display_matrix,
        "reset_cropbox": reset_cropbox,
        "rect_union": list(rect | Rect(0, 0, 2, 2)),
        "rect_torect": list(rect.torect(Rect(0, 0, 10, 10))),
        "quad_rect": list(quad.rect),
        "box": list(box),
    }


def _content_isolation_results() -> dict[str, Any]:
    """Draw on content that leaves q and Q unbalanced, counted as hayro parses it."""
    import pylopdf  # noqa: PLC0415

    contents = {
        "open": b"2 0 0 2 0 0 cm q 0 0 1 rg 0 0 5 5 re f",
        "excess": b"Q Q 2 0 0 2 0 0 cm 0 0 1 rg 0 0 5 5 re f",
        "inline_image": b"q 2 0 0 2 0 0 cm q BI /W 4 /H 1 /BPC 8 /CS /G ID Q Q EI",
        "comment": b"q 2 0 0 2 0 0 cm q % no line end",
    }
    point = (20.0, 40.0)
    results: dict[str, Any] = {}
    for label, content in contents.items():
        data = _build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Rotate 90 /Contents [4 0 R] >>",
                4: f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream",
            }
        )
        document = pylopdf.open(data)
        with document.pages[0].draw() as canvas:
            canvas.text("Probe", point, size=10)
        words = [word for word in document.pages[0].words() if word.text == "Probe"]
        _require(len(words) == 1, f"{label} content lost the appended text")
        x0, y0, x1, y1 = words[0].bbox
        _require(round(x0, 1) == point[0] and y0 < point[1] < y1, f"{label} content moved the appended text")
        results[label] = [round(value, 2) for value in (x0, y0, x1, y1)]
    return results


def _ocr_layer_results() -> dict[str, Any]:
    """Add another engine's words as an invisible layer, the OCR path that Wasm offers."""
    import pylopdf  # noqa: PLC0415

    document = pylopdf.Document()
    document.pages.new((300, 200))
    page = document.pages[0]
    words = [
        pylopdf.OcrWord(text="Invoice", bbox=pylopdf.Rect(40, 40, 120, 56), confidence=0.98),
        pylopdf.OcrWord(text=_INVOICE, bbox=pylopdf.Rect(40, 70, 110, 90), confidence=0.95),
    ]
    added = page.apply_ocr(words=words)
    _require(added == words, "the OCR layer did not take every word")
    _require(page.apply_ocr(words=words) == [], "a page with text did not skip a second layer")
    hits = page.search(_INVOICE)
    _require(len(hits) == 1, "the OCR layer is not searchable")
    reopened = pylopdf.open(document.to_bytes())
    return {
        "text": reopened.pages[0].text(),
        "hit": [round(value, 1) for value in hits[0]],
        "content_items": len(reopened.pages[0].content().items),
    }


def _object_model_results() -> dict[str, Any]:
    """Read and write the object graph through the typed low-level object model."""
    import pylopdf  # noqa: PLC0415

    data = _build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 200] >>",
            3: "<< /Type /Page /Parent 2 0 R >>",
        }
    )
    document = pylopdf.open(data)
    font = document.objects.add(
        {
            "Type": pylopdf.Name("Font"),
            "Subtype": pylopdf.Name("Type1"),
            "BaseFont": pylopdf.Name("Helvetica"),
        }
    )
    content = document.objects.add_stream(b"BT /F9 12 Tf 20 100 Td (Raw) Tj ET")
    page = document.pages[0].obj
    page["Resources"] = {"Font": {"F9": document.objects[font]}}
    page["Contents"] = document.objects[content]
    document.trailer["Info"] = {"Title": "Grüße ∑"}
    _require(document.pages[0].text().strip() == "Raw", "object model content was not drawn")
    _require(document.metadata["title"] == "Grüße ∑", "object model Info text changed")
    stream = document.objects[content]
    _require(stream.read() == b"BT /F9 12 Tf 20 100 Td (Raw) Tj ET", "stream data did not round-trip")
    saved = pylopdf.open(document.to_bytes(renumber=True))
    catalog = saved.catalog
    return {
        "objects": list(saved.objects),
        "catalog_keys": sorted(catalog),
        "page_keys": sorted(saved.pages[0].obj),
        "text": saved.pages[0].text().strip(),
        "filter": str(saved.pages[0].obj["Contents"]["Filter"]),
        "title": saved.trailer["Info"]["Title"].text,
        "stale": _exception_name(lambda: stream["Length"] if document.to_bytes(renumber=True) else None),
    }


def _document_property_results(root: Path) -> dict[str, Any]:
    """Read and write XMP metadata and the catalog's viewer and marking properties."""
    import pylopdf  # noqa: PLC0415

    form = pylopdf.open(root / "tests/assets/real_world/f1040.pdf")
    xmp = form.xmp
    _require(xmp is not None and xmp.startswith("<?xpacket"), "real-world XMP did not decode")
    document = pylopdf.open(
        _build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 200] >>",
                3: "<< /Type /Page /Parent 2 0 R >>",
            }
        )
    )
    packet = '<x:xmpmeta xmlns:x="adobe:ns:meta/">Grüße ∑</x:xmpmeta>'
    document.xmp = packet
    document.page_mode = pylopdf.PageMode.USE_OUTLINES
    document.page_layout = "TwoColumnRight"
    document.mark_info = pylopdf.MarkInfo(marked=True)
    document.language = "zh-TW"
    document.metadata.update(title="Grüße", created="D:20260102030405Z", trapped="True")
    document.pages.new((200, 200))
    document.outline = [
        pylopdf.OutlineEntry(1, "\u7b2c 1 \u7ae0", pylopdf.Destination(0, (10, 20), 1.5), is_open=True),
        pylopdf.OutlineEntry(2, "Web", "https://example.com/", color=pylopdf.Color(1, 0, 0), bold=True),
        pylopdf.OutlineEntry(2, "Next", pylopdf.NamedAction("NextPage")),
        pylopdf.OutlineEntry(1, "Other", pylopdf.RemoteDestination("other.pdf", 2)),
    ]
    renamed = document.outline[0].children[1]
    renamed.title = "Weiter"
    renamed.italic = True
    document.outline[0].is_open = False
    page = document.pages[0]
    for target in (
        "https://example.com/",
        pylopdf.Destination(1, (10, 20), 2.0),
        pylopdf.RemoteDestination("other.pdf", name="chapter"),
        pylopdf.Launch("notes.txt"),
        pylopdf.NamedAction("PrevPage"),
    ):
        page.links.add((10, 10, 60, 30), target)
    page.links[1].rect = (20, 40, 80, 60)
    page.links[0].target = pylopdf.Destination(0)
    del page.links[3]
    document.page_labels = [pylopdf.PageLabelRange(0, "r"), pylopdf.PageLabelRange(1, prefix="A-")]
    data = document.to_bytes()
    _require(packet.encode() in data, "saving compressed the XMP packet")
    saved = pylopdf.open(data)
    return {
        "form_xmp": [len(xmp), _digest(xmp)],
        "form_mark_info": repr(form.mark_info),
        "form_pdfa_claim": repr(form.pdfa_claim),
        "form_annotations": [any(page.links for page in form.pages), any(page.annotations for page in form.pages)],
        "xmp": saved.xmp,
        "page_mode": str(saved.page_mode),
        "page_layout": str(saved.page_layout),
        "mark_info": repr(saved.mark_info),
        "language": saved.language,
        "outline": [
            [
                entry.level,
                entry.title,
                repr(entry.destination),
                repr(entry.color),
                entry.bold,
                entry.italic,
                entry.is_open,
            ]
            for entry in saved.outline.entries()
        ],
        "outline_count": int(saved.catalog["Outlines"]["Count"]),
        "links": [[list(link.rect), repr(link.target)] for link in saved.pages[0].links],
        "page_labels": [page.label for page in saved.pages],
        "metadata": dict(saved.metadata),
        "version": saved.version,
        "xmp_limit": _exception_name(lambda: setattr(saved, "xmp", "x" * ((1 << 20) + 1))),
        "path": [form.path is not None, saved.path is None, saved.is_closed],
    }


def _content_model_results(root: Path) -> dict[str, Any]:
    """Read a form page and a Type 3 page through the content model."""
    import pylopdf  # noqa: PLC0415

    def rounded(items: Sequence[Any]) -> list[list[float]]:
        return [[round(value, 2) for value in item.bbox] for item in items]

    form = pylopdf.open(root / "tests/assets/real_world/f1040.pdf").pages[0].content()
    runs = form.text_runs
    glyphs = [glyph for run in runs for glyph in run.glyphs]
    _require(len(runs) > _MIN_FORM_ITEMS, "Form 1040 content has too few text runs")
    _require(all(glyph.bbox is None or glyph.bbox.is_valid for glyph in glyphs), "a glyph box is invalid")
    marked = sorted({mark.tag for run in runs for mark in run.context.marked_content})
    type3 = pylopdf.open(root / "tests/assets/real_world/pdfium-type3.pdf").pages[0].content()
    return {
        "form_counts": [len(runs), len(form.paths), len(form.images)],
        "form_text": _digest("".join(run.text for run in runs)),
        "form_boxes": _digest(repr(rounded(runs) + rounded(form.paths))),
        "form_marked_content": marked,
        "type3": [
            [run.font.is_type3, [round(value, 2) for value in run.bbox], [glyph.text for glyph in run.glyphs]]
            for run in type3.text_runs
        ],
    }


def _canvas_results(root: Path) -> dict[str, Any]:
    """Draw paths, text, an image, a page, and scopes with a canvas and read them back."""
    import pylopdf  # noqa: PLC0415
    from pylopdf import Color, Matrix, PathBuilder, Stroke  # noqa: PLC0415

    font = pylopdf.Font.from_bytes(
        (root / "fonts/pylopdf-fonts-jp/src/pylopdf_fonts_jp/NotoSansJP-Regular.otf").read_bytes()
    )
    source = pylopdf.open(root / "tests/assets/real_world/pdf20-simple.pdf")
    document = pylopdf.Document()
    page = document.pages.new((300, 200))
    swatch = pylopdf.Document().pages.new((4, 2)).pixmap()
    with page.draw() as canvas:
        canvas.rect((10, 10, 110, 60), radius=6, fill=Color(1, 0, 0), stroke=Stroke(paint=Color(0, 0, 1), width=2))
        canvas.text("Canvas", (20, 90), size=12)
        canvas.text(_JAPANESE, (20, 120), font=font, size=14, fill=Color(0, 0, 0, 0.5))
        with canvas.clip(PathBuilder().rect((150, 0, 300, 100))), canvas.opacity(0.5):
            canvas.circle((200, 50), 40, fill=Color(0, 0.5, 0))
        with canvas.transform(Matrix(1, 0, 0, 1, 150, 100)):
            canvas.image((0, 0, 40, 20), swatch)
            canvas.page((50, 0, 150, 100), source.pages[0])
        clipped = source.pages[0].rect & pylopdf.Rect(0, 0, 200, 150)
        canvas.page((220, 140, 290, 195), source.pages[0], clip=clipped, rotate=90)
        fit = canvas.textbox((10, 140, 140, 190), "Wrapped text " * 20, size=8)
        height = font.text_height(_JAPANESE * 3, 10, width=60)
        exact = canvas.textbox((150, 100, 210, 100 + height), _JAPANESE * 3, font=font, size=10)
        with canvas.mask() as mask:
            mask.rect((250, 0, 275, 50), fill=Color(1, 1, 1))
            canvas.rect((250, 0, 300, 50), fill=Color(0, 0, 1))
    _require(not fit.fits, "the overflowing textbox drew")
    pixmap = page.pixmap()
    samples = bytes(pixmap)

    def blue(x: int, y: int) -> bool:
        offset = y * pixmap.stride + x * 3
        return samples[offset : offset + 3] == b"\x00\x00\xff"

    _require(blue(262, 25) and not blue(287, 25), "a mask scope did not mask its drawing")
    _require(exact.spare_height == 0, "a textbox of the measured height did not fit exactly")
    content = page.content()
    return {
        "kinds": [type(item).__name__ for item in content],
        "boxes": [[round(value, 1) for value in item.bbox] for item in content][:6],
        "all_boxes": [[round(value, 1) for value in item.bbox] for item in content],
        "opacities": [round(float(item.context.opacity), 2) for item in content][:6],
        "text": _digest(page.text()),
        "spare_height": round(fit.spare_height, 2),
        "text_height": round(height, 3),
        "mask": [str(item.soft_mask.subtype) for item in content.paths if item.soft_mask is not None],
    }


def _create_results(root: Path) -> dict[str, Any]:
    """Write a tagged PDF/UA document with pylopdf.create() and read it back."""
    import datetime as dt  # noqa: PLC0415

    import pylopdf  # noqa: PLC0415
    from pylopdf import Color, ListNumbering, TagRole  # noqa: PLC0415

    font_path = root / "fonts/pylopdf-fonts-jp/src/pylopdf_fonts_jp/NotoSansJP-Regular.otf"
    font = pylopdf.Font.from_bytes(font_path.read_bytes())
    created = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC)
    builder = pylopdf.create(standard="pdf/ua-1", language="en", title="Contract", created=created)
    with builder.page((300, 200)) as canvas:
        canvas.bookmark("Start")
        with canvas.tag(TagRole.HEADING_1, title="Start"):
            canvas.text("Start", (20, 40), font=font, size=16)
        with canvas.tag(TagRole.PARAGRAPH):
            canvas.text(_JAPANESE, (20, 80), font=font)
        with canvas.tag(TagRole.FIGURE, alt="A red square"), canvas.opacity(0.5):
            canvas.rect((20, 100, 80, 160), fill=Color(1, 0, 0))
        with canvas.tag(TagRole.LIST, numbering=ListNumbering.DECIMAL), canvas.tag(TagRole.LIST_ITEM):
            with canvas.tag(TagRole.LABEL):
                canvas.text("1.", (100, 180), font=font)
            with canvas.tag(TagRole.LIST_BODY):
                canvas.text("Item", (120, 180), font=font)
    first = builder.to_bytes()
    _require(first == builder.to_bytes(), "a fixed creation date did not make the output reproducible")
    document = builder.finish()
    content = document.pages[0].content()
    return {
        "pages": len(document.pages),
        "kinds": [type(item).__name__ for item in content],
        "marks": [[mark.tag for mark in item.context.marked_content] for item in content],
        "outline": [[entry.level, entry.title, entry.page] for entry in document.outline.entries()],
        "text": _digest(document.pages[0].text()),
        "struct_tree": str(document.catalog["StructTreeRoot"]["Type"]),
        "list_numbering": _list_numberings(document.catalog["StructTreeRoot"]),
        "ua_claim": "pdfuaid" in (document.xmp or ""),
    }


def _list_numberings(root: Any) -> list[str]:  # noqa: ANN401 - a low-level dictionary.
    """Return the ListNumbering attribute of each list element in the structure tree, depth first."""
    import pylopdf  # noqa: PLC0415

    found: list[str] = []
    stack = [root["K"]]
    while stack:
        node = stack.pop()
        if isinstance(node, pylopdf.Array):
            stack.extend(reversed(list(node)))
        elif isinstance(node, pylopdf.Dictionary) and "S" in node:
            if node["S"] == "L":
                attributes = node.get("A")
                items = list(attributes) if isinstance(attributes, pylopdf.Array) else [attributes]
                found.extend(
                    str(item["ListNumbering"])
                    for item in items
                    if isinstance(item, pylopdf.Dictionary) and "ListNumbering" in item
                )
            if "K" in node:
                stack.append(node["K"])
    return found


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
        "geometry": _geometry_results(),
        "form_page_deletion": _form_page_deletion_results(),
        "structure_page_deletion": _structure_page_deletion_results(),
        "save_options": _save_option_results(),
        "content_isolation": _content_isolation_results(),
        "ocr_layer": _ocr_layer_results(),
        "object_model": _object_model_results(),
        "document_properties": _document_property_results(root),
        "content_model": _content_model_results(root),
        "canvas": _canvas_results(root),
        "create": _create_results(root),
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
        pylopdf.open(data, limits=limits).close()

    def bounded_extract() -> None:
        with pylopdf.open(form, limits=limits) as document:
            document.pages[0].text()

    def reject_file_size() -> None:
        try:
            pylopdf.open(pdf20, limits=pylopdf.DocumentLimits(max_file_size=len(pdf20) - 1))
        except pylopdf.LimitError as error:
            _require(error.code == "file_size", "file rejection returned the wrong code")
            return
        msg = "file-size rejection unexpectedly succeeded"
        raise RuntimeError(msg)

    def reject_page_count() -> None:
        try:
            pylopdf.open(form, limits=pylopdf.DocumentLimits(max_pages=1))
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
    # The baseline arrives as JSON, so a tuple or a non-string key would make
    # the Wasm results unequal to it while both serialize identically, and the
    # comparison would fail with an empty diff.
    _require(
        json.loads(json.dumps(results)) == results,
        "compatibility results must be plain JSON values, with lists instead of tuples",
    )
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
