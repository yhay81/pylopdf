"""Tests for annotation appearances, limits, and the /Annots edits that annotations and links share.

Created Highlights and render-only existing text markup appearances are checked
through rendered pixels.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import cast

import pytest
from conftest import annotation_subtypes, build_pdf, build_raw_pdf

import pylopdf

YELLOWISH_BLUE_MAX = 210
WHITE_MIN = 240


def _region_pixels(page: pylopdf.Page, rect: pylopdf.Rect) -> list[tuple[int, int, int]]:
    """Return RGB pixels inside ``rect`` after rendering on white."""
    pix = page.pixmap(background=(255, 255, 255), alpha=True)
    out = []
    for y in range(int(rect.y0), min(int(rect.y1), pix.height)):
        for x in range(int(rect.x0), min(int(rect.x1), pix.width)):
            offset = y * pix.stride + x * 4
            r, g, b = bytes(pix)[offset : offset + 3]
            out.append((r, g, b))
    return out


def _has_yellowish(pixels: list[tuple[int, int, int]]) -> bool:
    """Return whether the sample contains yellowish pixels."""
    return any(r > WHITE_MIN and g > WHITE_MIN and b < YELLOWISH_BLUE_MAX for r, g, b in pixels)


def _has_dominant_color(pixels: list[tuple[int, int, int]], channel: int) -> bool:
    """Return whether one RGB channel visibly dominates in the sample."""
    return any(
        pixel[channel] > 220 and all(pixel[index] < 180 for index in range(3) if index != channel) for pixel in pixels
    )


def test_search_and_highlight() -> None:
    doc = pylopdf.open(build_pdf(["Hello PDF"]))
    page = doc.pages[0]
    hits = page.search("Hello")
    assert hits
    page.annotations.add(pylopdf.Highlight(hits))

    annots = list(page.annotations)
    assert len(annots) == 1
    annot = annots[0]
    assert annot.type is pylopdf.AnnotationType.HIGHLIGHT
    hit = hits[0]
    # The annotation covers the hit; allow small f32 storage differences.
    assert annot.rect.x0 <= hit.x0 + 0.01
    assert annot.rect.x1 >= hit.x1 - 0.01
    # The appearance stream makes the highlight visible in Hayro.
    assert _has_yellowish(_region_pixels(page, hit))
    # Multiply blending preserves the black text pixels.
    assert any(r < 128 for r, _, _ in _region_pixels(page, hit))


def test_highlight_survives_save_roundtrip() -> None:
    doc = pylopdf.open(build_pdf(["Hello PDF"]))
    hits = doc.pages[0].search("PDF")
    doc.pages[0].annotations.add(pylopdf.Highlight(hits, contents="review note"))
    reopened = pylopdf.open(doc.to_bytes())
    annots = list(reopened.pages[0].annotations)
    assert len(annots) == 1
    assert annots[0].type is pylopdf.AnnotationType.HIGHLIGHT
    assert annots[0].contents == "review note"
    assert _has_yellowish(_region_pixels(reopened.pages[0], hits[0]))


def test_highlight_multiple_rects_in_one_annot() -> None:
    doc = pylopdf.open(build_pdf(["Hello PDF"]))
    page = doc.pages[0]
    rects = [pylopdf.Rect(10, 10, 40, 20), pylopdf.Rect(60, 40, 90, 50)]
    page.annotations.add(pylopdf.Highlight(rects))
    annots = list(page.annotations)
    assert len(annots) == 1
    # The bounding rectangle covers both input rectangles.
    assert annots[0].rect.x0 <= 10
    assert annots[0].rect.x1 >= 90
    assert annots[0].rect.y1 >= 50
    assert _has_yellowish(_region_pixels(page, rects[0]))
    assert _has_yellowish(_region_pixels(page, rects[1]))


def test_highlight_single_rect_argument() -> None:
    doc = pylopdf.Document()
    doc.pages.new((100, 100))
    page = doc.pages[0]
    page.annotations.add(pylopdf.Highlight((20, 20, 80, 40)))
    assert page.annotations[0].type is pylopdf.AnnotationType.HIGHLIGHT
    assert _has_yellowish(_region_pixels(page, pylopdf.Rect(20, 20, 80, 40)))


def test_highlight_on_rotated_page_uses_display_coordinates() -> None:
    doc = pylopdf.Document()
    doc.pages.new((100, 200))
    page = doc.pages[0]
    page.rotation = 90  # Display coordinates are 200 x 100.
    target = pylopdf.Rect(120, 30, 180, 70)
    page.annotations.add(pylopdf.Highlight(target))
    assert _has_yellowish(_region_pixels(page, target))
    assert not _has_yellowish(_region_pixels(page, pylopdf.Rect(10, 10, 60, 60)))
    # Readback also uses display coordinates.
    annot_rect = page.annotations[0].rect
    assert abs(annot_rect.x0 - target.x0) < 1
    assert abs(annot_rect.y1 - target.y1) < 1


def test_missing_highlight_appearance_rejects_malformed_opacity() -> None:
    """Do not invent an appearance when an authored opacity is malformed."""
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R /Annots [5 0 R] >>"),
            4: "<< /Length 0 >>\nstream\n\nendstream",
            5: (
                "<< /Type /Annot /Subtype /Highlight /Rect [10 10 30 20] "
                "/QuadPoints [10 20 30 20 10 10 30 10] /C [1 1 0] /CA /Invalid >>"
            ),
        }
    )
    page = pylopdf.open(pdf).pages[0]

    assert not _has_yellowish(_region_pixels(page, page.rect))


@pytest.mark.parametrize("max_interpretation_size", [None, 1024 * 1024])
def test_missing_text_markup_appearances_render_without_mutating_source(
    max_interpretation_size: int | None,
) -> None:
    """Render Underline, StrikeOut, and Squiggly dictionaries without `/AP`."""
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R /Annots [5 0 R 6 0 R 7 0 R] >>"),
            4: "<< /Length 0 >>\nstream\n\nendstream",
            5: (
                "<< /Type /Annot /Subtype /Underline /Rect [10 70 40 80] "
                "/QuadPoints [10 80 40 80 10 70 40 70] /C [1 0 0] >>"
            ),
            6: (
                "<< /Type /Annot /Subtype /StrikeOut /Rect [10 50 40 60] "
                "/QuadPoints [10 60 40 60 10 50 40 50] /C [0 1 0] >>"
            ),
            7: (
                "<< /Type /Annot /Subtype /Squiggly /Rect [10 30 40 40] "
                "/QuadPoints [10 40 40 40 10 30 40 30] /C [0 0 1] >>"
            ),
        }
    )
    document = pylopdf.open(
        pdf,
        limits=pylopdf.DocumentLimits(
            max_interpretation_size=max_interpretation_size,
        ),
    )
    page = document.pages[0]

    assert [annotation.type for annotation in page.annotations] == [
        pylopdf.AnnotationType.UNDERLINE,
        pylopdf.AnnotationType.STRIKE_OUT,
        pylopdf.AnnotationType.SQUIGGLY,
    ]
    assert _has_dominant_color(_region_pixels(page, pylopdf.Rect(8, 18, 43, 32)), 0)
    assert _has_dominant_color(_region_pixels(page, pylopdf.Rect(8, 38, 43, 52)), 1)
    assert _has_dominant_color(_region_pixels(page, pylopdf.Rect(8, 58, 43, 72)), 2)
    assert b"/AP" not in document.to_bytes()


def test_missing_underline_appearance_follows_slanted_quad() -> None:
    """Keep the lower QuadPoints edge instead of flattening the underline."""
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R /Annots [5 0 R] >>"),
            4: "<< /Length 0 >>\nstream\n\nendstream",
            5: (
                "<< /Type /Annot /Subtype /Underline /Rect [10 60 40 80] "
                "/QuadPoints [10 80 40 70 10 70 40 60] /C [1 0 0] >>"
            ),
        }
    )
    page = pylopdf.open(pdf).pages[0]

    assert _has_dominant_color(_region_pixels(page, pylopdf.Rect(8, 26, 16, 34)), 0)
    assert _has_dominant_color(_region_pixels(page, pylopdf.Rect(34, 35, 43, 43)), 0)


def test_missing_text_markup_quad_budget_is_atomic() -> None:
    """Do not synthesize a partial set when one annotation holds more quads than the engine draws."""
    excessive_quads = "10 20 30 20 10 10 30 10 " * 4097
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R /Annots [5 0 R 6 0 R] >>"),
            4: "<< /Length 0 >>\nstream\n\nendstream",
            5: (
                "<< /Type /Annot /Subtype /Highlight /Rect [10 10 30 20] "
                "/QuadPoints [10 20 30 20 10 10 30 10] /C [1 1 0] >>"
            ),
            6: (
                f"<< /Type /Annot /Subtype /Highlight /Rect [10 10 30 20] /QuadPoints [{excessive_quads}] /C [1 1 0] >>"
            ),
        }
    )
    page = pylopdf.open(pdf).pages[0]

    assert not _has_yellowish(_region_pixels(page, page.rect))


def test_missing_text_markup_segment_budget_is_atomic() -> None:
    """Do not amplify an extreme Squiggly width or synthesize its peer."""
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R /Annots [5 0 R 6 0 R] >>"),
            4: "<< /Length 0 >>\nstream\n\nendstream",
            5: (
                "<< /Type /Annot /Subtype /Highlight /Rect [10 10 30 20] "
                "/QuadPoints [10 20 30 20 10 10 30 10] /C [1 1 0] >>"
            ),
            6: (
                "<< /Type /Annot /Subtype /Squiggly /Rect [0 30 200000 40] "
                "/QuadPoints [0 40 200000 40 0 30 200000 30] /C [1 0 0] >>"
            ),
        }
    )
    page = pylopdf.open(pdf).pages[0]

    assert not _has_yellowish(_region_pixels(page, page.rect))


def _squiggles(count: int) -> pylopdf.Page:
    """Open a page with a highlight and `count` squiggly lines of 65,535 zigzag points each, all without appearances."""
    squiggles = {
        6 + index: (
            f"<< /Type /Annot /Subtype /Squiggly /Rect [0 {index} 131068 {index + 1}] "
            f"/QuadPoints [0 {index + 1} 131068 {index + 1} 0 {index} 131068 {index}] /C [1 0 0] >>"
        )
        for index in range(count)
    }
    references = " ".join(f"{number} 0 R" for number in range(5, 6 + count))
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R /Annots [{references}] >>",
            4: "<< /Length 0 >>\nstream\n\nendstream",
            5: (
                "<< /Type /Annot /Subtype /Highlight /Rect [10 10 30 20] "
                "/QuadPoints [10 20 30 20 10 10 30 10] /C [1 1 0] >>"
            ),
            **squiggles,
        }
    )
    return pylopdf.open(pdf).pages[0]


def test_missing_appearance_point_budget_is_atomic() -> None:
    """Do not synthesize a partial set above the aggregate point budget of a rendering snapshot."""
    page = _squiggles(5)
    assert not _has_yellowish(_region_pixels(page, page.rect))
    # Four of them fit in the budget, and their peer is drawn.
    page = _squiggles(4)
    assert _has_yellowish(_region_pixels(page, page.rect))


def test_insert_link_reads_back() -> None:
    doc = pylopdf.open(build_pdf(["Visit example"]))
    page = doc.pages[0]
    hits = page.search("example")
    page.links.add(hits[0], "https://example.com/")
    reopened = pylopdf.open(doc.to_bytes())
    assert annotation_subtypes(reopened.pages[0]) == ["Link"]
    assert reopened.pages[0].links[0].target == "https://example.com/"


def test_copy_page_detaches_shared_indirect_annots_array() -> None:
    """Detach a shared Annots array before annotating a copied page."""
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Annots 4 0 R >>",
            4: "[5 0 R]",
            5: (
                "<< /Type /Annot /Subtype /Link /Rect [1 1 10 10] /Border [0 0 0] "
                "/A << /S /URI /URI (https://a.example) >> >>"
            ),
        }
    )
    doc = pylopdf.open(pdf)
    doc.pages.extend(doc.pages[0])

    doc.pages[1].links.add((20, 20, 30, 30), "https://b.example")

    assert [len(annotation_subtypes(page)) for page in doc.pages] == [1, 2]
    assert [link.target for link in doc.pages[0].links] == ["https://a.example"]


def test_unique_indirect_annots_append_reaches_exact_limit_in_place() -> None:
    existing = "5 0 R " * 4095
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Annots 4 0 R >>",
            4: f"[{existing}]",
            5: (
                "<< /Type /Annot /Subtype /Link /Rect [1 1 10 10] /Border [0 0 0] "
                "/A << /S /URI /URI (https://a.example) >> >>"
            ),
        }
    )
    doc = pylopdf.open(pdf)

    doc.pages[0].links.add((20, 20, 30, 30), "https://b.example")

    assert len(annotation_subtypes(doc.pages[0])) == 4096
    assert b"/Annots 4 0 R" in doc.to_bytes()


def test_annotation_add_rejects_malformed_indirect_array_atomically() -> None:
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Annots 4 0 R >>",
            4: "<< /NotAnArray true >>",
        }
    )
    doc = pylopdf.open(pdf)
    before = doc.to_bytes()

    with pytest.raises(pylopdf.PdfError):
        doc.pages[0].links.add((20, 20, 30, 30), "https://example.com")
    with pytest.raises(pylopdf.PdfError):
        doc.pages[0].annotations.add(pylopdf.Highlight((20, 20, 30, 30)))

    assert doc.to_bytes() == before


def test_annots_empty_on_fresh_page() -> None:
    doc = pylopdf.Document()
    doc.pages.new()
    assert len(doc.pages[0].annotations) == 0


def test_annots_rejects_bad_input() -> None:
    doc = pylopdf.Document()
    doc.pages.new()
    page = doc.pages[0]
    with pytest.raises(ValueError, match="at least one rectangle"):
        page.annotations.add(pylopdf.Highlight([]))
    with pytest.raises(ValueError, match="opacity"):
        page.annotations.add(pylopdf.Highlight((10, 10, 50, 20), opacity=0))
    with pytest.raises(ValueError, match="URI"):
        page.links.add((10, 10, 50, 20), "")


def test_annotation_reads_reject_excessive_array_without_partial_output() -> None:
    annots = "5 0 R " * 4097
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Annots [{annots}] >>",
            4: "<< >>",
            5: ("<< /Type /Annot /Subtype /Link /Rect [1 1 10 10] /A << /S /URI /URI (https://example.com) >> >>"),
        }
    )
    page = pylopdf.open(pdf).pages[0]
    with pytest.raises(pylopdf.PdfError, match="4096-entry safety limit"):
        list(page.annotations)
    with pytest.raises(pylopdf.PdfError, match="4096-entry safety limit"):
        list(page.links)


def test_annotation_reads_reject_excessive_metadata_text() -> None:
    content = "x" * (1024 * 1024 + 1)
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Annots [5 0 R] >>",
            4: "<< >>",
            5: f"<< /Type /Annot /Subtype /Text /Rect [1 1 10 10] /Contents ({content}) >>",
        }
    )
    page = pylopdf.open(pdf).pages[0]
    with pytest.raises(pylopdf.PdfError, match=r"Contents text.*1048576-byte safety limit"):
        list(page.annotations)


def test_link_reads_reject_excessive_uri_text() -> None:
    uri = "x" * (1024 * 1024 + 1)
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Annots [5 0 R] >>",
            4: "<< >>",
            5: f"<< /Type /Annot /Subtype /Link /Rect [1 1 10 10] /A << /S /URI /URI ({uri}) >> >>",
        }
    )
    page = pylopdf.open(pdf).pages[0]
    # Annotation reads leave links to page.links, which reads the action.
    assert len(page.annotations) == 0
    with pytest.raises(pylopdf.PdfError, match=r"URI text.*1048576-byte safety limit"):
        list(page.links)


def test_annotation_add_refuses_over_limit_page_atomically() -> None:
    annots = "5 0 R " * 4096
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Annots [{annots}] >>",
            4: "<< >>",
            5: "<< /Type /Annot /Subtype /Text /Rect [1 1 10 10] >>",
        }
    )
    doc = pylopdf.open(pdf)
    before = doc.to_bytes()
    with pytest.raises(pylopdf.PdfError, match="4096-entry safety limit"):
        doc.pages[0].links.add((10, 10, 20, 20), "https://example.com")
    with pytest.raises(pylopdf.PdfError, match="4096-entry safety limit"):
        doc.pages[0].annotations.add(pylopdf.Highlight((10, 10, 20, 20)))
    assert doc.to_bytes() == before


def test_annotation_add_bounds_inputs_atomically() -> None:
    doc = pylopdf.open(build_pdf([""]))
    before = doc.to_bytes()

    with pytest.raises(pylopdf.LimitError, match=r"content.*1048576-byte") as content_limit:
        doc.pages[0].annotations.add(pylopdf.Highlight((10, 10, 20, 20), contents="x" * (1024 * 1024 + 1)))
    assert content_limit.value.code == "annotation_input_size"
    with pytest.raises(pylopdf.LimitError, match=r"link subtype and target.*1048576-byte") as uri_limit:
        doc.pages[0].links.add((10, 10, 20, 20), "x" * (1024 * 1024 + 1))
    assert uri_limit.value.code == "annotation_input_size"
    with pytest.raises(ValueError, match="4096 rectangles"):
        doc.pages[0].annotations.add(pylopdf.Highlight([(10, 10, 20, 20)] * 4097))
    assert doc.to_bytes() == before


def test_highlight_input_stops_before_unbounded_materialization() -> None:
    doc = pylopdf.open(build_pdf([""]))

    def excessive_rects() -> Iterator[tuple[int, int, int, int]]:
        yield from [(10, 10, 20, 20)] * 4097
        msg = "the bounded reader consumed beyond item 4,097"
        raise AssertionError(msg)

    with pytest.raises(ValueError, match="4096 rectangles"):
        doc.pages[0].annotations.add(pylopdf.Highlight(cast("Sequence[Sequence[float]]", excessive_rects())))

    def unreadable_rects() -> Iterator[tuple[int, int, int, int]]:
        msg = "oversized text must fail before rectangle iteration"
        raise AssertionError(msg)
        yield (10, 10, 20, 20)

    with pytest.raises(pylopdf.LimitError) as content_limit:
        pylopdf.Highlight(cast("Sequence[Sequence[float]]", unreadable_rects()), contents="x" * (1024 * 1024 + 1))
    assert content_limit.value.code == "annotation_input_size"


def test_annotation_add_at_metadata_boundary_remains_readable() -> None:
    # The subtype, the contents, and the creation and modification dates.
    content = "x" * (1024 * 1024 - len("Highlight") - 2 * len("D:20260101000000Z"))
    highlight_doc = pylopdf.open(build_pdf([""]))
    highlight_doc.pages[0].annotations.add(pylopdf.Highlight((10, 10, 20, 20), contents=content))
    assert highlight_doc.pages[0].annotations[0].contents == content

    uri = "x" * (1024 * 1024 - len("Link"))
    link_doc = pylopdf.open(build_pdf([""]))
    link_doc.pages[0].links.add((10, 10, 20, 20), uri)
    assert link_doc.pages[0].links[0].target == uri
    assert link_doc.pages[0].links[0].target == uri
