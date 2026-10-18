"""Write smaller real-world documents: first pages, or several documents joined.

`SOURCE_PDF:PAGES[,PAGES...]` writes the first PAGES pages of SOURCE_PDF as
`sub-<stem>-p<PAGES>.pdf`. `NAME=SOURCE_PDF+SOURCE_PDF[+...]` writes the
documents one after the other as `mix-<NAME>.pdf`. Each output keeps the
objects its pages reach (fonts, images, annotations, form fields) and is
saved by lopdf through pylopdf with a cross-reference table, or with object
streams and a cross-reference stream when the page count ends in "s" or the
name in "-objstm".

Usage: python make_subsets.py OUT_DIR SPEC...
"""

from __future__ import annotations

import sys
from pathlib import Path

import pylopdf


def write(path: Path, doc: pylopdf.Document, object_streams: bool) -> None:
    out = doc.tobytes(garbage=True, object_streams=object_streams)
    path.write_bytes(out)
    with pylopdf.open(stream=out) as check:
        print(f"{path.name}: {len(out)} bytes, {check.page_count} pages")


def main() -> None:
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    for spec in sys.argv[2:]:
        if "=" in spec:
            name, _, sources = spec.partition("=")
            object_streams = name.endswith("-objstm")
            out = pylopdf.Document()
            for source in sources.split("+"):
                with pylopdf.open(stream=Path(source).read_bytes()) as src:
                    out.insert_pdf(src)
            write(out_dir / f"mix-{name}.pdf", out, object_streams)
            continue
        source, _, counts = spec.rpartition(":")
        data = Path(source).read_bytes()
        stem = Path(source).stem
        for count in counts.split(","):
            object_streams = count.endswith("s")
            pages = int(count.rstrip("s"))
            with pylopdf.open(stream=data) as doc:
                if pages > doc.page_count:
                    raise SystemExit(f"{source} has {doc.page_count} pages, not {pages}")
                doc.select(range(pages))
                suffix = "-objstm" if object_streams else ""
                write(out_dir / f"sub-{stem}-p{pages}{suffix}.pdf", doc, object_streams)


if __name__ == "__main__":
    main()
