"""Document.sanitize removes scripts, actions, embedded files, metadata, hidden text, and comments."""

from __future__ import annotations

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import Rect

HELVETICA = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"


def _stream(content: str, dictionary: str = "") -> str:
    return f"<< {dictionary} /Length {len(content)} >>\nstream\n{content}\nendstream"


def _loaded() -> bytes:
    """Return a page that carries a little of everything sanitization removes."""
    content = "BT /F1 12 Tf 20 250 Td (Visible) Tj 3 Tr 0 -20 Td (Hidden layer) Tj 0 Tr 0 -20 Td (After) Tj ET"
    return build_raw_pdf(
        {
            1: (
                "<< /Type /Catalog /Pages 2 0 R /OpenAction 8 0 R /AA << /WC 9 0 R >>"
                " /Names << /JavaScript << /Names [(init) 8 0 R] >> /EmbeddedFiles << /Names [(a.txt) 10 0 R] >> >>"
                " /Metadata 12 0 R /Outlines 13 0 R /AF [10 0 R] >>"
            ),
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 300 300] >>",
            3: (
                "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R"
                " /Annots [6 0 R 7 0 R 15 0 R] /AA << /O 8 0 R >> /Thumb 14 0 R"
                " /PieceInfo << /App << /Private (x) >> >> >>"
            ),
            4: HELVETICA,
            5: _stream(content),
            6: "<< /Type /Annot /Subtype /Text /Rect [10 10 30 30] /Contents (A comment) >>",
            7: "<< /Type /Annot /Subtype /Link /Rect [40 40 80 60] /A 16 0 R >>",
            8: "<< /S /JavaScript /JS (app.alert('hi')) >>",
            9: "<< /S /Launch /F (calc.exe) >>",
            10: "<< /Type /Filespec /F (a.txt) /UF (a.txt) /EF << /F 11 0 R >> >>",
            11: _stream("secret attachment", "/Type /EmbeddedFile"),
            12: _stream("<x:xmpmeta/>", "/Type /Metadata /Subtype /XML"),
            13: "<< /Type /Outlines /First 17 0 R /Last 17 0 R /Count 1 >>",
            14: _stream("\x00\x00\x00", "/Width 1 /Height 1 /ColorSpace /DeviceRGB /BitsPerComponent 8"),
            15: ("<< /Type /Annot /Subtype /FileAttachment /Rect [100 10 120 30] /FS 10 0 R /Contents (attached) >>"),
            16: "<< /S /URI /URI (https://example.test/) /Next 8 0 R >>",
            17: "<< /Title (Run) /Parent 13 0 R /A 9 0 R >>",
        }
    )


def _with_info(data: bytes) -> bytes:
    doc = pylopdf.open(data)
    doc.metadata["author"] = "Ada"
    return doc.to_bytes()


def test_sanitize_removes_every_category() -> None:
    doc = pylopdf.open(_with_info(_loaded()))
    page = doc.pages[0]
    assert "Hidden" in page.text()

    report = doc.sanitize()

    assert report.scripts >= 2
    assert report.actions >= 2
    assert report.embedded_files == 2
    assert report.metadata >= 2
    assert report.hidden_glyphs == len("Hidden layer")
    assert report.comments == 1
    assert report.links == 0
    assert report.thumbnails == 1
    assert report.objects > 0
    catalog = doc.catalog
    assert "OpenAction" not in catalog
    assert "AA" not in catalog
    assert "Metadata" not in catalog
    assert "AF" not in catalog
    assert "JavaScript" not in catalog["Names"]
    assert "EmbeddedFiles" not in catalog["Names"]
    assert "Info" not in doc.trailer
    assert dict(doc.metadata) == {}
    assert len(doc.attachments) == 0
    page = doc.pages[0]
    assert page.text().split() == ["Visible", "After"]
    assert "Thumb" not in page.obj
    assert "PieceInfo" not in page.obj
    assert "AA" not in page.obj
    assert len(page.annotations) == 0
    (link,) = page.links
    # The URI stays; the script chained after it goes.
    assert link.target == "https://example.test/"
    assert "Next" not in link.obj["A"]
    # The outline item that launched a file is inert.
    assert doc.outline[0].destination is None
    assert "A" not in doc.outline[0].obj
    saved = doc.to_bytes()
    assert b"app.alert" not in saved
    assert b"calc.exe" not in saved
    assert b"secret attachment" not in pylopdf.open(saved).to_bytes(compress=False)


def test_each_category_is_optional() -> None:
    doc = pylopdf.open(_with_info(_loaded()))

    report = doc.sanitize(
        scripts=False,
        actions=False,
        embedded_files=False,
        metadata=False,
        hidden_text=False,
        comments=False,
        thumbnails=False,
    )

    assert (report.scripts, report.actions, report.embedded_files, report.metadata) == (0, 0, 0, 0)
    assert (report.hidden_glyphs, report.comments, report.thumbnails) == (0, 0, 0)
    assert "OpenAction" in doc.catalog
    assert len(doc.attachments) == 1
    assert doc.metadata["author"] == "Ada"
    assert "Hidden" in doc.pages[0].text()
    assert len(doc.pages[0].annotations) == 2


def test_scripts_alone_keep_other_actions() -> None:
    doc = pylopdf.open(_loaded())
    report = doc.sanitize(
        actions=False, embedded_files=False, metadata=False, hidden_text=False, comments=False, thumbnails=False
    )

    assert report.scripts >= 2
    assert "OpenAction" not in doc.catalog
    # The Launch trigger is no script and stays.
    assert "WC" in doc.catalog["AA"]
    assert doc.outline[0].obj["A"]["S"] == "Launch"


def test_links_and_form_values() -> None:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 300))
    page.links.add(Rect(10, 10, 50, 30), "https://example.test/")
    page.widgets.add(pylopdf.TextField("name", Rect(10, 100, 150, 120), value="Ada"))

    report = doc.sanitize(links=True, form_values=True)

    assert report.links == 1
    assert report.fields == 1
    assert len(page.links) == 0
    assert doc.form["name"].value in ("", None)


def test_sanitized_documents_save_in_full() -> None:
    doc = pylopdf.open(_loaded())
    assert doc.can_save_incrementally
    doc.sanitize()

    assert not doc.can_save_incrementally
    reopened = pylopdf.open(doc.to_bytes())
    assert reopened.pages[0].text().split() == ["Visible", "After"]


@pytest.mark.parametrize("option", ["scripts", "links", "form_values"])
def test_options_are_bools(option: str) -> None:
    doc = pylopdf.open(_loaded())
    with pytest.raises(TypeError):
        doc.sanitize(**{option: 1})  # ty: ignore[invalid-argument-type]
