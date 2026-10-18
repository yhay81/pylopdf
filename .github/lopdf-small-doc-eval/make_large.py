"""Write a large many-object PDF by concatenating a real-world document.

Usage: python make_large.py SOURCE_PDF COPIES OUT_PDF
"""

from __future__ import annotations

import sys
from pathlib import Path

import pylopdf


def main() -> None:
    source = Path(sys.argv[1]).read_bytes()
    copies = int(sys.argv[2])
    out = pylopdf.Document()
    for _ in range(copies):
        with pylopdf.open(stream=source) as src:
            out.insert_pdf(src)
    data = out.tobytes()
    Path(sys.argv[3]).write_bytes(data)
    with pylopdf.open(stream=data) as check:
        print(f"{len(data)} bytes, {check.page_count} pages, {check.complexity['object_count']} objects")


if __name__ == "__main__":
    main()
