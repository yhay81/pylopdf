"""Tests for Page.get_links annotation reads and destination resolution.

Resolve named destinations through a /Names tree in the real-world
pdfTeX/hyperref usrguide.pdf, and direct /Dest arrays in a minimal fixture.
"""

from __future__ import annotations

from pathlib import Path

from conftest import build_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"


def _build_direct_dest_fixture() -> bytes:
    """Build a minimal two-page PDF with one direct /Dest array link."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Annots [5 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
        b"<< /Type /Annot /Subtype /Link /Rect [10 10 100 30] /Dest [4 0 R /XYZ 5 195 null] >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n").encode()
    return bytes(out)


def test_direct_dest_array() -> None:
    """Resolve a direct /Dest array to a page number and target point."""
    doc = pylopdf.open(stream=_build_direct_dest_fixture())
    links = doc[0].get_links()
    assert len(links) == 1
    link = links[0]
    assert link["kind"] == pylopdf.LINK_GOTO
    assert link["page"] == 1
    # Display coordinates for crop [0,0,200,200] without rotation: (x, 200-y).
    assert link["from"] == pylopdf.Rect(10.0, 170.0, 100.0, 190.0)
    assert link["to"] == pylopdf.Point(5.0, 5.0)
    assert "zoom" not in link  # A null value means no zoom.
    assert "nameddest" not in link
    assert doc[1].get_links() == []
    doc.close()


def test_uri_link_roundtrip() -> None:
    """Read back a URI link created with add_link_annot."""
    doc = pylopdf.open(stream=build_pdf(["Hello link"]))
    page = doc[0]
    rect = (10.0, 20.0, 110.0, 40.0)
    page.add_link_annot(rect, "https://example.com/")
    links = page.get_links()
    assert len(links) == 1
    link = links[0]
    assert link["kind"] == pylopdf.LINK_URI
    assert link["uri"] == "https://example.com/"
    got = link["from"]
    assert (got.x0, got.y0, got.x1, got.y1) == rect
    doc.close()


def test_usrguide_named_destinations() -> None:
    """Resolve every named destination in a pdfTeX/hyperref PDF."""
    doc = pylopdf.open(ASSETS / "usrguide.pdf")
    goto = []
    uri = []
    for page_number in range(doc.page_count):
        for link in doc[page_number].get_links():
            if link["kind"] == pylopdf.LINK_GOTO:
                goto.append(link)
            elif link["kind"] == pylopdf.LINK_URI:
                uri.append(link)
    assert len(goto) == 40
    assert len(uri) == 2
    # Resolve all entries through the two-level /Names tree.
    assert all(link["page"] >= 0 for link in goto)
    assert all(link["nameddest"] for link in goto)
    assert all(isinstance(link.get("to"), pylopdf.Point) for link in goto)
    first = goto[0]
    assert first["nameddest"] == "section.1"
    assert first["page"] == 1
    assert all(link["uri"].startswith(("http://", "https://")) for link in uri)
    doc.close()
