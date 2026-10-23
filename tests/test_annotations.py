"""page.annotations: Annotation handles, Highlight specifications, and the annotation enums."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import cast

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf
from pylopdf import AnnotationFlags, AnnotationType, Color, Highlight, Rect


def _annotated() -> pylopdf.Document:
    text = (
        "<< /Type /Annot /Subtype /Text /Rect [10 10 30 30] /Contents (Note) /Name /Comment "
        "/T (Ann) /CreationDate (D:20260101) /M (D:20260102) /Subj (Review) /NM (n-1) "
        "/F 4 /C [1 0 0] /IC [0 0 1] /CA 0.5 /Popup 5 0 R >>"
    )
    return pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: (
                    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Annots [4 0 R 5 0 R "
                    "<< /Type /Annot /Subtype /Link /Rect [0 0 5 5] >> "
                    "<< /Type /Annot /Subtype /Widget /Rect [0 0 5 5] >> "
                    "<< /Type /Annot /Subtype /FreeText /IT /FreeTextCallout /Rect [50 50 80 80] /F 2 "
                    "/C [0.5] /IC [0 0 0 1] >> << /Type /Annot /Rect [1 1 2 2] >>] >>"
                ),
                4: text,
                5: "<< /Type /Annot /Subtype /Popup /Rect [100 10 190 60] /Parent 4 0 R >>",
            }
        )
    )


def test_annotations_are_typed_handles() -> None:
    page = _annotated().pages[0]

    text, free_text, untyped = page.annotations

    assert isinstance(text, pylopdf.Annotation)
    assert (text.type, free_text.type, untyped.type) == (
        AnnotationType.TEXT,
        AnnotationType.FREE_TEXT,
        AnnotationType.UNKNOWN,
    )
    assert text.rect == Rect(10, 70, 30, 90)
    assert (text.contents, text.author, text.subject, text.name, text.icon) == (
        "Note",
        "Ann",
        "Review",
        "n-1",
        "Comment",
    )
    assert (text.created, text.modified, text.intent) == ("D:20260101", "D:20260102", None)
    assert free_text.intent == "FreeTextCallout"
    assert (text.color, text.interior_color) == (Color(1, 0, 0), Color(0, 0, 1))
    assert (free_text.color, free_text.interior_color) == (Color(0.5, 0.5, 0.5), Color(0, 0, 0))
    assert untyped.color is untyped.contents is None
    assert (text.opacity, free_text.opacity) == (0.5, 1.0)
    assert text.flags == AnnotationFlags.PRINT
    assert AnnotationFlags.HIDDEN in free_text.flags
    assert (text.xref, free_text.xref) == (4, 0)
    assert text.page == page
    assert text.obj["Subtype"] == "Text"
    assert free_text.obj["IT"] == "FreeTextCallout"
    assert repr(text) == "Annotation(type=AnnotationType.TEXT, rect=Rect(x0=10.0, y0=70.0, x1=30.0, y1=90.0))"
    with pytest.raises(AttributeError):
        text.rect = Rect(0, 0, 1, 1)  # ty: ignore[invalid-assignment]


def test_links_widgets_and_popups_are_not_listed() -> None:
    page = _annotated().pages[0]

    assert [annotation.type for annotation in page.annotations] == [
        AnnotationType.TEXT,
        AnnotationType.FREE_TEXT,
        AnnotationType.UNKNOWN,
    ]
    assert len(page.links) == 1


def test_handles_compare_by_annotation() -> None:
    page = _annotated().pages[0]

    first, second = list(page.annotations), list(page.annotations)

    assert first == second
    assert len(set(first) | set(second)) == 3
    assert first[0] != first[1]


def test_handles_go_stale() -> None:
    doc = _annotated()
    doc.pages.new()
    text, free_text, _ = doc.pages[0].annotations

    del doc.pages[0].obj["Annots"][0]
    with pytest.raises(pylopdf.StaleObjectError):
        _ = text.rect
    assert repr(text) == "<stale Annotation>"
    assert free_text.xref == 0

    # Removing an annotation stores direct ones as objects, so a handle that
    # found one by its position goes stale instead of naming another.
    del doc.pages[0].annotations[1]
    with pytest.raises(pylopdf.StaleObjectError):
        _ = free_text.type
    assert all(annotation.xref > 0 for annotation in doc.pages[0].annotations)

    remaining = doc.pages[0].annotations[0]
    del doc.pages[1]
    with pytest.raises(pylopdf.StalePageError):
        _ = remaining.type


def test_deleting_an_annotation_removes_its_popup() -> None:
    doc = _annotated()
    page = doc.pages[0]
    free_text = page.annotations[1]

    del page.annotations[0]

    assert [annotation.type for annotation in page.annotations] == [AnnotationType.FREE_TEXT, AnnotationType.UNKNOWN]
    assert page.annotations[0].intent == "FreeTextCallout"
    with pytest.raises(pylopdf.StaleObjectError):
        _ = free_text.intent
    assert [entry.get("Subtype") for entry in page.obj["Annots"]] == ["Link", "Widget", "FreeText", None]
    reopened = pylopdf.open(doc.to_bytes()).pages[0]
    assert len(reopened.annotations) == 2
    assert 4 not in reopened.document.objects
    assert 5 not in reopened.document.objects

    del page.annotations[:]
    assert len(page.annotations) == 0
    assert len(page.links) == 1


def test_adding_a_highlight_returns_its_handle() -> None:
    doc = pylopdf.open(build_pdf(["Highlight me"]))
    page = doc.pages[0]
    hit = page.search("Highlight")[0]

    annotation = page.annotations.add(Highlight(hit, contents="note"))

    assert annotation.type is AnnotationType.HIGHLIGHT
    assert (annotation.contents, annotation.color, annotation.flags) == ("note", Color(1, 1, 0), AnnotationFlags.PRINT)
    assert annotation.opacity == pytest.approx(0.4)
    assert annotation.xref > 0
    assert annotation in page.annotations
    assert annotation.rect == pytest.approx(hit, abs=1e-3)
    from_quad = page.annotations.add(Highlight(hit.quad, color=Color(0, 1, 0), opacity=1))
    assert from_quad.rect == pytest.approx(annotation.rect, abs=1e-3)
    assert (from_quad.color, from_quad.opacity) == (Color(0, 1, 0), 1.0)
    several = page.annotations.add(Highlight([hit.quad, Rect(10, 10, 20, 20)]))
    assert len(several.obj["QuadPoints"]) == 16
    reopened = pylopdf.open(doc.to_bytes()).pages[0]
    assert [annotation.contents for annotation in reopened.annotations] == ["note", None, None]


@pytest.mark.parametrize(
    ("arguments", "error", "match"),
    [
        (((),), ValueError, "at least one rectangle"),
        (((10, 10, 5, 20),), ValueError, "x0 < x1"),
        (("rect",), TypeError, "rects must be a rectangle"),
        (((10, 10, 20, 20), {"color": (1, 0, 0)}), TypeError, "color must be a Color"),
        (((10, 10, 20, 20), {"color": Color(1, 0, 0, 0.5)}), ValueError, "no transparency"),
        (((10, 10, 20, 20), {"opacity": 0}), ValueError, "greater than 0"),
        (((10, 10, 20, 20), {"opacity": True}), TypeError, "opacity must be a number"),
        (((10, 10, 20, 20), {"contents": 5}), TypeError, "contents must be a str"),
        (([(10, 10, 20, 20)] * 4097,), ValueError, "4096 rectangles"),
    ],
)
def test_highlight_specifications_validate_their_fields(
    arguments: tuple[object, ...], error: type[Exception], match: str
) -> None:
    rects, *rest = arguments
    keywords = cast("dict[str, object]", rest[0] if rest else {})

    with pytest.raises(error, match=match):
        Highlight(rects, **keywords)  # ty: ignore[invalid-argument-type]


def test_highlight_bounds_contents_before_reading_rectangles() -> None:
    def unreadable() -> Iterator[tuple[int, int, int, int]]:
        msg = "oversized contents must fail before rectangle iteration"
        raise AssertionError(msg)
        yield (10, 10, 20, 20)

    with pytest.raises(pylopdf.LimitError) as caught:
        Highlight(cast("Sequence[Sequence[float]]", unreadable()), contents="x" * (1024 * 1024 + 1))
    assert caught.value.code == "annotation_input_size"

    def excessive() -> Iterator[tuple[int, int, int, int]]:
        yield from [(10, 10, 20, 20)] * 4097
        msg = "the bounded reader consumed beyond item 4,097"
        raise AssertionError(msg)

    with pytest.raises(ValueError, match="4096 rectangles"):
        Highlight(cast("Sequence[Sequence[float]]", excessive()))


def test_add_takes_specifications_only() -> None:
    page = pylopdf.open(build_pdf(["x"])).pages[0]

    with pytest.raises(TypeError, match="takes a Highlight specification"):
        page.annotations.add((10, 10, 20, 20))  # ty: ignore[invalid-argument-type]
    assert not hasattr(page, "annots")
    assert not hasattr(page, "add_highlight_annot")


def test_annotation_constants_match_pymupdf() -> None:
    assert (pylopdf.PDF_ANNOT_TEXT, pylopdf.PDF_ANNOT_WIDGET, pylopdf.PDF_ANNOT_3D) == (0, 21, 26)
    assert (pylopdf.PDF_ANNOT_UNKNOWN, pylopdf.PDF_ANNOT_PROJECTION) == (-1, 27)
    assert pylopdf.PDF_ANNOT_3D is AnnotationType.THREE_D
    assert pylopdf.PDF_ANNOT_IS_LOCKED_CONTENTS == 512
    assert AnnotationFlags(6) == pylopdf.PDF_ANNOT_IS_PRINT | pylopdf.PDF_ANNOT_IS_HIDDEN
    for name in ("Annot", "AnnotType", "AnnotFlags"):
        assert not hasattr(pylopdf, name)
