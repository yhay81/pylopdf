"""Every case of the adversarial redaction corpus leaks nothing.

After redaction, neither text extraction nor the content model finds a
secret anywhere on the page, no stream of the saved file holds it, nothing
that hayro paints has ink inside an area, and a rendering of each area shows
only blank or blanked pixels. ``bench/redaction.py`` repeats the check with
pymupdf.
"""

from __future__ import annotations

import pytest
from redaction_corpus import Case, cases, redact

import pylopdf
from pylopdf import Rect

CASES = cases()


def _inset(area: Rect, by: float) -> Rect:
    return Rect(area.x0 + by, area.y0 + by, area.x1 - by, area.y1 - by)


def _meets(a: Rect, b: Rect) -> bool:
    return a.x0 < b.x1 and a.x1 > b.x0 and a.y0 < b.y1 and a.y1 > b.y0


@pytest.mark.filterwarnings("ignore::pylopdf.PylopdfWarning")
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_redaction_leaks_nothing(case: Case) -> None:  # noqa: C901, PLR0912 - one check after another.
    doc, areas, report = redact(case)
    assert areas, "every case redacts something"
    assert report.areas
    saved = pylopdf.open(doc.to_bytes())
    page = saved.pages[0]

    text = page.text()
    # The content model lists glyphs in paint order, which rotated and
    # vertical lines keep together.
    painted = "".join(glyph.text or "" for run in page.content().text_runs for glyph in run.glyphs)
    for secret in case.secrets:
        assert secret not in text
        assert secret not in text.replace("\n", "").replace(" ", "")
        assert secret not in painted
    for kept in case.kept:
        assert kept in painted.replace(" ", "")
    for other in case.elsewhere:
        assert any(other in saved.pages[index].text() for index in range(1, len(saved.pages)))

    # No stream of the file holds an ASCII secret, in content or replacement text.
    on_page = "".join(char for char in "".join(case.secrets) if char.isascii())
    if on_page and not case.elsewhere:
        for xref in saved.objects:
            value = saved.objects[xref]
            if isinstance(value, pylopdf.Stream):
                data = value.read(max_size=None)
                for secret in case.secrets:
                    if secret.isascii():
                        assert secret.encode() not in data, (xref, secret)

    # Nothing hayro paints has ink inside an area.
    inner = [_inset(area, 0.05) for area in areas]
    content = page.content()
    for run in content.text_runs:
        for glyph in run.glyphs:
            if glyph.bbox is not None:
                assert not any(_meets(glyph.bbox, area) for area in inner), glyph.text
    if case.options.get("graphics", "touched") == "touched":
        fills = {id(path) for path in content.paths if case.annotate}
        for path in content.paths:
            if id(path) not in fills:
                assert not any(_meets(path.bbox, area) for area in inner), path.bbox

    # A rendering of each area holds only white, or the black of blanked pixels.
    if not case.annotate:
        pixmap = page.pixmap(dpi=72)
        samples = bytes(pixmap)
        for area in areas:
            box = _inset(area, 1.5)
            for y in range(max(0, int(box.y0)), min(pixmap.height, int(box.y1) + 1)):
                for x in range(max(0, int(box.x0)), min(pixmap.width, int(box.x1) + 1)):
                    pixel = samples[(y * pixmap.width + x) * 3 : (y * pixmap.width + x) * 3 + 3]
                    assert pixel in (b"\xff\xff\xff", b"\x00\x00\x00"), (x, y, pixel)


def test_the_corpus_covers_its_classes() -> None:
    names = {case.name for case in CASES}
    for required in (
        "kerned-tj",
        "shared-form-two-pages",
        "rotated-page-90",
        "cid-horizontal",
        "cid-vertical",
        "type3",
        "overprinted-twice",
        "inline-image",
        "image-with-soft-mask",
        "stencil-mask",
        "invisible-ocr-layer",
    ):
        assert required in names
