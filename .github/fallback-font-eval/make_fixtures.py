"""Write the non-embedded CJK fixtures used by tests/test_locale_fonts.py.

Usage: python make_fixtures.py OUT_DIR

The builders mirror tests/conftest.py without importing pytest, so the
benchmark environments need nothing beyond the wheels under comparison.
"""

from __future__ import annotations

import sys
from pathlib import Path


def build_raw_pdf(objects: dict[int, str], *, version: str = "1.7") -> bytes:
    out = bytearray(f"%PDF-{version}\n".encode())
    offsets: dict[int, int] = {}
    for number in range(1, len(objects) + 1):
        offsets[number] = len(out)
        out.extend(f"{number} 0 obj\n".encode())
        out.extend(objects[number].encode("latin-1"))
        out.extend(b"\nendobj\n")
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for number in range(1, len(objects) + 1):
        out.extend(f"{offsets[number]:010d} 00000 n \n".encode())
    out.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode())
    return bytes(out)


def mincho_japanese() -> bytes:
    """MS-Mincho via 90ms-RKSJ-H: Adobe-Japan1, serif slot."""
    text = "".join(f"\\{b:03o}" for b in "こんにちは日本語".encode("cp932"))
    stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET"
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 612 792] "
            "/Resources << /Font << /F1 3 0 R >> >> >>",
            3: "<< /Type /Font /Subtype /Type0 /BaseFont /MS-Mincho /Encoding /90ms-RKSJ-H "
            "/DescendantFonts [6 0 R] >>",
            4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            5: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
            6: "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /MS-Mincho"
            " /CIDSystemInfo << /Registry (Adobe) /Ordering (Japan1) /Supplement 6 >>"
            " /FontDescriptor 7 0 R /DW 1000 >>",
            7: "<< /Type /FontDescriptor /FontName /MS-Mincho /Flags 6 /FontBBox [0 -137 1000 859]"
            " /ItalicAngle 0 /Ascent 859 /Descent -140 /CapHeight 769 /StemV 78 >>",
        },
        version="1.4",
    )


def locale(text: str, base_font: str, encoding: str, ordering: str, supplement: int) -> bytes:
    """A Unicode-CMap PDF for one non-embedded Adobe CJK collection."""
    stream = f"BT /F1 24 Tf 72 720 Td <{text.encode('utf-16-be').hex().upper()}> Tj ET"
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [4 0 R] /Count 1 /MediaBox [0 0 612 792] "
            "/Resources << /Font << /F1 3 0 R >> >> >>",
            3: f"<< /Type /Font /Subtype /Type0 /BaseFont /{base_font} /Encoding /{encoding} "
            "/DescendantFonts [6 0 R] >>",
            4: "<< /Type /Page /Parent 2 0 R /Contents 5 0 R >>",
            5: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
            6: f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /{base_font} "
            f"/CIDSystemInfo << /Registry (Adobe) /Ordering ({ordering}) /Supplement {supplement} >> "
            "/FontDescriptor 7 0 R /DW 1000 >>",
            7: f"<< /Type /FontDescriptor /FontName /{base_font} /Flags 6 "
            "/FontBBox [0 -250 1000 1000] /ItalicAngle 0 /Ascent 880 "
            "/Descent -120 /CapHeight 700 /StemV 80 >>",
        },
        version="1.4",
    )


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    fixtures = {
        "cjk-ja-mincho.pdf": mincho_japanese(),
        "cjk-ko.pdf": locale("한국어", "HYSMyeongJo-Medium", "UniKS-UCS2-H", "Korea1", 2),
        "cjk-zh-cn.pdf": locale("简体中文", "STSong-Light", "UniGB-UCS2-H", "GB1", 5),
        "cjk-zh-tw.pdf": locale("繁體中文", "MSung-Light", "UniCNS-UCS2-H", "CNS1", 7),
    }
    for name, data in fixtures.items():
        (out / name).write_bytes(data)


if __name__ == "__main__":
    main()
