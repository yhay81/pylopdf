"""An adversarial redaction corpus: PDFs that hide text and pixels where redaction tends to miss them.

Each case is a small PDF built here, so the corpus is redistributable. A case
names the secret strings that its areas must remove, the strings that must
stay, and explicit areas for what has no text (pixels and vector art).
``tests/test_redaction_corpus.py`` checks every case with pylopdf's own
extraction and the content model, and ``bench/redaction.py`` checks the
redacted files with pymupdf's extraction and MuPDF's rendering.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field

import pylopdf
from pylopdf import Rect

HELVETICA = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
COURIER = "<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>"
SIZE = 300


@dataclass(frozen=True)
class Case:
    """One adversarial document and what redacting it must achieve."""

    name: str
    data: bytes
    #: Strings whose glyphs the areas cover; each occurrence on page 0 becomes an area.
    secrets: tuple[str, ...] = ()
    #: Strings that must remain in page 0's text.
    kept: tuple[str, ...] = ()
    #: Further areas in display space on page 0, for pixels and vector art.
    areas: tuple[Rect, ...] = ()
    #: Options for ``Page.redact``.
    options: dict[str, object] = field(default_factory=dict)
    #: Strings that must remain on other pages.
    elsewhere: tuple[str, ...] = ()
    #: Whether to mark the areas with Redaction annotations instead of passing them.
    annotate: bool = False


def stream(content: str | bytes, dictionary: str = "", *, compress: bool = False) -> bytes:
    data = content.encode("latin-1") if isinstance(content, str) else content
    if compress:
        data = zlib.compress(data)
        dictionary += " /Filter /FlateDecode"
    return b"<< %s /Length %d >>\nstream\n%s\nendstream" % (dictionary.encode(), len(data), data)


def build(objects: dict[int, str | bytes]) -> bytes:
    """Serialize numbered objects 1..n with a cross-reference table; 1 is the catalog."""
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number in range(1, len(objects) + 1):
        offsets.append(len(out))
        body = objects[number]
        out += b"%d 0 obj\n" % number
        out += body.encode("latin-1") if isinstance(body, str) else body
        out += b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def page(
    content: str | bytes,
    resources: str = "<< /Font << /F1 4 0 R >> >>",
    *extra: str | bytes,
    page_entries: str = "",
    pages: int = 1,
) -> bytes:
    """Pages of 300x300 points sharing ``content``, Helvetica as /F1, extra objects from 6."""
    first_extra_page = 6 + len(extra)
    numbers = [3, *range(first_extra_page, first_extra_page + pages - 1)]
    kids = " ".join(f"{number} 0 R" for number in numbers)
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {pages} /MediaBox [0 0 {SIZE} {SIZE}] >>",
        3: f"<< /Type /Page /Parent 2 0 R /Resources {resources} /Contents 5 0 R {page_entries} >>",
        4: HELVETICA,
        5: stream(content),
    }
    objects.update(enumerate(extra, start=6))
    for number in numbers[1:]:
        objects[number] = f"<< /Type /Page /Parent 2 0 R /Resources {resources} /Contents 5 0 R >>"
    return build(objects)


def form(content: str, resources: str = "<< /Font << /F1 4 0 R >> >>", bbox: str = "0 0 300 300") -> bytes:
    return stream(content, f"/Type /XObject /Subtype /Form /BBox [{bbox}] /Resources {resources}")


def cid_font(*, vertical: bool = False, to_unicode: int = 7) -> str:
    encoding = "Identity-V" if vertical else "Identity-H"
    return (
        f"<< /Type /Font /Subtype /Type0 /BaseFont /CIDTest /Encoding /{encoding} /DescendantFonts"
        " [<< /Type /Font /Subtype /CIDFontType2 /BaseFont /CIDTest /CIDSystemInfo"
        " << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /DW 1000"
        " /FontDescriptor << /Type /FontDescriptor /FontName /CIDTest /Flags 4 /FontBBox [0 -120 1000 880]"
        f" /ItalicAngle 0 /Ascent 880 /Descent -120 /CapHeight 700 /StemV 80 >> >>] /ToUnicode {to_unicode} 0 R >>"
    )


def to_unicode(text: str) -> bytes:
    """Return a ToUnicode CMap that maps two-byte codes 1, 2, ... to the characters of ``text``."""
    lines = "".join(f"<{code:04X}> <{ord(char):04X}>\n" for code, char in enumerate(text, start=1))
    return stream(
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /T def\n"
        "1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
        f"{len(text)} beginbfchar\n{lines}endbfchar endcmap CMapName currentdict /CMap defineresource pop end end"
    )


def cid_codes(text: str, alphabet: str) -> str:
    return "<" + "".join(f"{alphabet.index(char) + 1:04X}" for char in text) + ">"


def type3_font() -> tuple[str, bytes, bytes]:
    """Return a Type 3 font whose glyphs for a to z are filled squares, with its ToUnicode CMap and glyph."""
    names = " ".join(f"/g{chr(code)}" for code in range(97, 123))
    procs = " ".join(f"/g{chr(code)} 9 0 R" for code in range(97, 123))
    font = (
        "<< /Type /Font /Subtype /Type3 /FontBBox [0 0 600 700] /FontMatrix [0.001 0 0 0.001 0 0]"
        f" /CharProcs << {procs} >> /Encoding << /Type /Encoding /Differences [97 {names}] >>"
        " /FirstChar 97 /LastChar 122 /Widths [" + " 600" * 26 + " ] /ToUnicode 8 0 R >>"
    )
    lines = "".join(f"<{code:02X}> <{code:04X}>\n" for code in range(97, 123))
    cmap = stream(
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /T def\n"
        "1 begincodespacerange <00> <FF> endcodespacerange\n"
        f"26 beginbfchar\n{lines}endbfchar endcmap CMapName currentdict /CMap defineresource pop end end"
    )
    glyph = stream("600 0 0 0 600 700 d1 50 0 500 700 re f")
    return font, cmap, glyph


def image(width: int, height: int, rgb: tuple[int, int, int], extra: str = "") -> bytes:
    pixels = bytes(rgb) * (width * height)
    return stream(
        pixels,
        f"/Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceRGB"
        f" /BitsPerComponent 8 {extra}",
        compress=True,
    )


def text(x: float, y: float, value: str, size: float = 12, font: str = "/F1") -> str:
    return f"BT {font} {size} Tf {x} {y} Td ({value}) Tj ET "


def cases() -> list[Case]:
    """Build every case of the corpus."""
    corpus: list[Case] = []
    add = corpus.append

    add(
        Case(
            "plain",
            page(text(20, 250, "Name: Ada SECRETA Lovelace") + text(20, 100, "Footer text")),
            secrets=("SECRETA",),
            kept=("Name:", "Lovelace", "Footer"),
        )
    )
    add(
        Case(
            "kerned-tj",
            page(
                "BT /F1 12 Tf 20 250 Td [(Acc) -40 (ount ) 120 (SEC) -80 (RET) 300 (B) 40 ( end)] TJ ET "
                + text(20, 100, "Kept")
            ),
            secrets=("SECRETB",),
            kept=("Acc", "end", "Kept"),
        )
    )
    add(
        Case(
            "tj-with-spacing",
            page("BT /F1 12 Tf 2 Tc 4 Tw 120 Tz 2 Ts 20 250 Td (pre SECRETC post) Tj ET " + text(20, 100, "Kept")),
            secrets=("SECRETC",),
            kept=("pre", "post", "Kept"),
        )
    )
    add(
        Case(
            "quote-operators",
            page("BT /F1 12 Tf 16 TL 20 250 Td (Line one) Tj (SECRETD) ' 3 1 (SECRETE) \" (Line four) ' ET"),
            secrets=("SECRETD", "SECRETE"),
            kept=("one", "four"),
        )
    )
    add(
        Case(
            "rotated-text-matrix",
            page("BT /F1 12 Tf 0.8660 0.5 -0.5 0.8660 60 120 Tm (Tilted SECRETF here) Tj ET " + text(20, 270, "Kept")),
            secrets=("SECRETF",),
            kept=("Kept",),
        )
    )
    add(
        Case(
            "shared-form-two-pages",
            page(
                "q 1 0 0 1 20 200 cm /Fm Do Q",
                "<< /XObject << /Fm 6 0 R >> /Font << /F1 4 0 R >> >>",
                form(text(0, 0, "Form SECRETG body")),
                pages=2,
            ),
            secrets=("SECRETG",),
            kept=("Form", "body"),
            elsewhere=("SECRETG",),
        )
    )
    add(
        Case(
            "form-drawn-twice",
            page(
                "q 1 0 0 1 20 220 cm /Fm Do Q q 1 0 0 1 20 80 cm /Fm Do Q " + text(20, 150, "SECRETH"),
                "<< /XObject << /Fm 6 0 R >> /Font << /F1 4 0 R >> >>",
                form(text(0, 0, "Twice drawn")),
            ),
            secrets=("SECRETH",),
            kept=("Twice",),
        )
    )
    add(
        Case(
            "nested-forms",
            page(
                "q 1 0 0 1 10 10 cm /Outer Do Q",
                "<< /XObject << /Outer 6 0 R >> >>",
                form("q 1 0 0 1 10 100 cm /Inner Do Q", "<< /XObject << /Inner 7 0 R >> >>"),
                form("q 1 0 0 1 10 100 cm /Leaf Do Q", "<< /XObject << /Leaf 8 0 R >> >>"),
                form(text(0, 0, "Deep SECRETI leaf")),
            ),
            secrets=("SECRETI",),
            kept=("Deep", "leaf"),
        )
    )
    add(
        Case(
            "form-without-resources",
            page(
                "q 1 0 0 1 20 200 cm /Fm Do Q",
                "<< /XObject << /Fm 6 0 R >> /Font << /F1 4 0 R >> >>",
                stream(text(0, 0, "Inherits SECRETJ fonts"), "/Type /XObject /Subtype /Form /BBox [0 0 300 300]"),
            ),
            secrets=("SECRETJ",),
            kept=("Inherits", "fonts"),
        )
    )
    for rotation in (90, 180, 270):
        add(
            Case(
                f"rotated-page-{rotation}",
                page(text(30, 200, "Turned SECRETK page") + text(30, 60, "Kept"), page_entries=f"/Rotate {rotation}"),
                secrets=("SECRETK",),
                kept=("Turned", "Kept"),
            )
        )
    add(
        Case(
            "cropped-page",
            page(text(30, 200, "Cropped SECRETL") + text(30, 60, "Kept"), page_entries="/CropBox [20 20 280 280]"),
            secrets=("SECRETL",),
            kept=("Cropped", "Kept"),
        )
    )
    alphabet = "機密情報公開文書"
    add(
        Case(
            "cid-horizontal",
            page(
                f"BT /F1 20 Tf 20 250 Td {cid_codes('公開機密情報文書', alphabet)} Tj ET",
                "<< /Font << /F1 6 0 R >> >>",
                cid_font(),
                to_unicode(alphabet),
            ),
            secrets=("機密情報",),
            kept=("公開", "文書"),
        )
    )
    add(
        Case(
            "cid-vertical",
            page(
                f"BT /F1 20 Tf 150 290 Td {cid_codes('公開機密情報文書', alphabet)} Tj ET",
                "<< /Font << /F1 6 0 R >> >>",
                cid_font(vertical=True),
                to_unicode(alphabet),
            ),
            secrets=("機密情報",),
            kept=("公開", "文書"),
        )
    )
    font, cmap, glyph = type3_font()
    add(
        Case(
            "type3",
            page(
                "BT /F1 20 Tf 20 250 Td (open hidden open) Tj ET",
                "<< /Font << /F1 6 0 R >> >>",
                font,
                "<< >>",
                cmap,
                glyph,
            ),
            secrets=("hidden",),
            kept=("open",),
        )
    )
    add(
        Case(
            "invisible-ocr-layer",
            page(
                "q 200 0 0 40 20 230 cm /Im Do Q BT 3 Tr /F1 12 Tf 25 240 Td (Scanned SECRETM words) Tj ET",
                "<< /Font << /F1 4 0 R >> /XObject << /Im 6 0 R >> >>",
                image(4, 1, (240, 240, 240)),
            ),
            secrets=("SECRETM",),
            kept=("Scanned", "words"),
        )
    )
    add(
        Case(
            "text-under-cover",
            page(text(20, 250, "Covered SECRETN") + "1 1 1 rg 0 230 300 40 re f " + text(20, 100, "Kept")),
            secrets=("SECRETN",),
            kept=("Kept",),
            options={"graphics": "keep"},
        )
    )
    add(
        Case(
            "white-and-tiny-text",
            page(
                "1 1 1 rg "
                + text(20, 250, "White SECRETO")
                + "0 g "
                + text(20, 200, "SECRETP", size=0.5)
                + text(20, 100, "Kept")
            ),
            secrets=("SECRETO", "SECRETP"),
            kept=("White", "Kept"),
        )
    )
    add(
        Case(
            "overprinted-twice",
            page(text(20, 250, "SECRETQ") + text(20, 250, "SECRETQ") + text(20, 100, "Kept")),
            secrets=("SECRETQ",),
            kept=("Kept",),
        )
    )
    add(
        Case(
            "clipped-out-text",
            page("q 0 0 10 10 re W n " + text(20, 250, "Clipped SECRETR") + "Q " + text(20, 100, "Kept")),
            secrets=("SECRETR",),
            kept=("Kept",),
        )
    )
    add(
        Case(
            "actual-text",
            page(
                "/Span << /ActualText (SECRETS) >> BDC " + text(20, 250, "xxxxxxx") + "EMC "
                "/Span /MC0 BDC " + text(20, 200, "yyyyyyy") + "EMC " + text(20, 100, "Kept"),
                "<< /Font << /F1 4 0 R >> /Properties << /MC0 << /ActualText (SECRETT) >> >> >>",
            ),
            secrets=("xxxxxxx", "yyyyyyy"),
            kept=("Kept",),
        )
    )
    add(
        Case(
            "after-hayro-stops",
            page(text(20, 250, "Visible SECRETU") + "1 2 3 4 5 6 7 8 9 10 11 sc " + text(20, 200, "SECRETV")),
            secrets=("SECRETU",),
            kept=("Visible",),
        )
    )
    add(
        Case(
            "courier-and-helvetica-overlap",
            page(
                text(20, 250, "SECRETW") + text(20, 250, "SECRETW", font="/F2") + text(20, 100, "Kept"),
                "<< /Font << /F1 4 0 R /F2 6 0 R >> >>",
                COURIER,
            ),
            secrets=("SECRETW",),
            kept=("Kept",),
        )
    )
    add(
        Case(
            "image-pixels",
            page(
                "q 100 0 0 100 100 100 cm /Im Do Q " + text(20, 30, "Kept"),
                "<< /Font << /F1 4 0 R >> /XObject << /Im 6 0 R >> >>",
                image(10, 10, (200, 30, 30)),
            ),
            areas=(Rect(110, 110, 150, 150),),
            kept=("Kept",),
        )
    )
    add(
        Case(
            "image-with-soft-mask",
            page(
                "q 100 0 0 100 100 100 cm /Im Do Q",
                "<< /XObject << /Im 6 0 R >> >>",
                image(10, 10, (30, 200, 30), "/SMask 7 0 R"),
                stream(
                    bytes([180]) * 100,
                    "/Type /XObject /Subtype /Image /Width 10 /Height 10 /ColorSpace /DeviceGray /BitsPerComponent 8",
                ),
            ),
            areas=(Rect(110, 110, 150, 150),),
        )
    )
    add(
        Case(
            "stencil-mask",
            page(
                "q 0 0 1 rg 100 0 0 100 100 100 cm /Im Do Q",
                "<< /XObject << /Im 6 0 R >> >>",
                stream(
                    bytes([0x00]) * 20,
                    "/Type /XObject /Subtype /Image /Width 10 /Height 10 /ImageMask true /BitsPerComponent 1",
                ),
            ),
            areas=(Rect(110, 110, 150, 150),),
        )
    )
    add(
        Case(
            "inline-image",
            page(b"q 100 0 0 100 100 100 cm BI /W 4 /H 4 /BPC 8 /CS /G ID " + bytes([90]) * 16 + b" EI Q"),
            areas=(Rect(110, 110, 150, 150),),
        )
    )
    add(
        Case(
            "inline-image-filtered",
            page(b"q 100 0 0 100 100 100 cm BI /W 4 /H 4 /BPC 8 /CS /G /F /AHx ID " + b"5A" * 16 + b"> EI Q"),
            areas=(Rect(110, 110, 150, 150),),
        )
    )
    add(
        Case(
            "vector-art",
            page("0 0 1 rg 120 120 30 30 re f 1 0 0 RG 4 w 20 20 m 280 20 l S " + text(20, 270, "Kept")),
            areas=(Rect(110, 140, 160, 190),),
            kept=("Kept",),
        )
    )
    add(
        Case(
            "pattern-text",
            page(
                "/Pattern cs /P0 scn 100 100 100 100 re f",
                "<< /Pattern << /P0 6 0 R >> >>",
                stream(
                    text(0, 10, "SECRETX", size=8),
                    "/Type /Pattern /PatternType 1 /PaintType 1 /TilingType 1 /BBox [0 0 60 20]"
                    " /XStep 60 /YStep 20 /Resources << /Font << /F1 4 0 R >> >>",
                ),
            ),
            areas=(Rect(90, 90, 210, 210),),
        )
    )
    add(
        Case(
            "annotation-appearance",
            build(
                {
                    1: "<< /Type /Catalog /Pages 2 0 R >>",
                    2: f"<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 {SIZE} {SIZE}] >>",
                    3: (
                        "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >>"
                        " /Contents 5 0 R /Annots [6 0 R] >>"
                    ),
                    4: HELVETICA,
                    5: stream(text(20, 100, "Kept")),
                    6: (
                        "<< /Type /Annot /Subtype /FreeText /Rect [20 240 200 280] /Contents (SECRETY)"
                        " /AP << /N 7 0 R >> >>"
                    ),
                    7: form(text(2, 10, "SECRETY"), bbox="0 0 180 40"),
                }
            ),
            secrets=("SECRETY",),
            kept=("Kept",),
        )
    )
    add(
        Case(
            "redaction-annotations",
            page(text(20, 250, "Mark SECRETZ here") + text(20, 100, "Kept")),
            secrets=("SECRETZ",),
            kept=("Mark", "here", "Kept"),
            annotate=True,
        )
    )
    return corpus


def secret_areas(page: pylopdf.Page, secrets: tuple[str, ...]) -> list[Rect]:
    """Return the union of the ink boxes of each occurrence of each secret, in display space.

    The content model lists glyphs in paint order, so a secret drawn by one
    text operation stays contiguous whatever its direction; glyphs without
    ink take the extraction box of the same glyph instead.
    """
    # One entry per character of each glyph's text, so that indexes into
    # the joined text find the glyph that drew a character.
    glyphs = [
        (char, glyph.bbox)
        for run in page.content(paths=False, images=False).text_runs
        for glyph in run.glyphs
        for char in glyph.text or ""
    ]
    text = "".join(char for char, _ in glyphs)
    areas: list[Rect] = []
    for secret in secrets:
        start = text.find(secret)
        while start >= 0:
            boxes = [box for _, box in glyphs[start : start + len(secret)] if box is not None]
            box = boxes[0]
            for other in boxes[1:]:
                box |= other
            areas.append(box)
            start = text.find(secret, start + 1)
    return areas


def redact(case: Case) -> tuple[pylopdf.Document, list[Rect], pylopdf.RedactionReport]:
    """Open a case, redact page 0, and return the document, the areas, and the report."""
    doc = pylopdf.open(case.data)
    page = doc.pages[0]
    areas = [*secret_areas(page, case.secrets), *case.areas]
    if case.annotate:
        for area in areas:
            page.annotations.add(pylopdf.Redaction(area))
        report = page.redact(**case.options)  # ty: ignore[invalid-argument-type]
    else:
        options = {"fill": None, **case.options}
        report = page.redact(areas, **options)  # ty: ignore[invalid-argument-type]
    return doc, areas, report
