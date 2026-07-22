"""暗号化テストフィクスチャの生成スクリプト。

このディレクトリの *.pdf を再生成する（生成物はリポジトリに同梱済み。
再実行が必要なのはフィクスチャ構成を変えるときだけ）:

    uv run --with pypdf --with cryptography python tests/assets/encrypted/generate.py

パスワードはすべて user="userpw" / owner="ownerpw"（owneronly-* は user 空)。
"""

from __future__ import annotations

import io
from pathlib import Path

from pypdf import PdfReader, PdfWriter

BASE = Path(__file__).parent


def build_plain_pdf() -> bytes:
    """テキスト抽出を検証できる 2 ページの最小 PDF を組み立てる。"""
    page_texts = ["Encrypted page one", "Encrypted page two"]
    n = len(page_texts)
    objects: dict[int, str] = {}
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objects[1] = "<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {n} /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> >>"
    )
    objects[3] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    for i, text in enumerate(page_texts):
        stream = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET"
        objects[4 + 2 * i] = f"<< /Type /Page /Parent 2 0 R /Contents {5 + 2 * i} 0 R >>"
        objects[5 + 2 * i] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n{objects[num]}\nendobj\n".encode("latin-1")
    xref_pos = len(out)
    size = len(objects) + 1
    out += f"xref\n0 {size}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for num in sorted(objects):
        out += f"{offsets[num]:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode("ascii")
    return bytes(out)


def main() -> None:
    plain = build_plain_pdf()
    variants = [
        ("user-rc4-40.pdf", "userpw", "RC4-40"),
        ("user-rc4-128.pdf", "userpw", "RC4-128"),
        ("user-aes-128.pdf", "userpw", "AES-128"),
        ("user-aes-256.pdf", "userpw", "AES-256"),
        ("owneronly-aes-256.pdf", "", "AES-256"),
    ]
    for name, user_pw, algorithm in variants:
        reader = PdfReader(io.BytesIO(plain))
        writer = PdfWriter(clone_from=reader)
        writer.encrypt(user_password=user_pw, owner_password="ownerpw", algorithm=algorithm)
        with (BASE / name).open("wb") as f:
            writer.write(f)
        print(f"{name}: {(BASE / name).stat().st_size} bytes")


if __name__ == "__main__":
    main()
