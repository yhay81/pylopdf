"""Tests for the content model returned by Page.content()."""

from __future__ import annotations

import dataclasses
import pickle
import warnings
from collections import Counter
from contextlib import nullcontext
from pathlib import Path

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import (
    BlendMode,
    ClosePath,
    Color,
    CurveTo,
    FillRule,
    GraphicsContext,
    ImageMask,
    LineCap,
    LineJoin,
    LineTo,
    MarkedContent,
    MoveTo,
    Point,
    Rect,
    ShadingPattern,
    ShadingType,
    SoftMask,
    SoftMaskType,
    TextRenderMode,
    TilingPattern,
)

CORPUS = sorted((Path(__file__).parent / "assets" / "real_world").glob("*.pdf"))


def stream_object(data: str, dictionary: str = "") -> str:
    """Return the text of a stream object holding ``data``."""
    return f"<< {dictionary} /Length {len(data.encode('latin-1'))} >>\nstream\n{data}\nendstream"


def page_with(
    content: str,
    *,
    resources: str = "",
    objects: dict[int, str] | None = None,
    rotate: int = 0,
) -> pylopdf.Page:
    """Open a 200 by 100 page that paints ``content``, with Helvetica as /F1.

    Extra objects are numbered from 6.
    """
    rotation = f" /Rotate {rotate}" if rotate else ""
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: (
                "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 100] "
                f"/Resources << /Font << /F1 5 0 R >> {resources} >> >>"
            ),
            3: f"<< /Type /Page /Parent 2 0 R /Contents 4 0 R{rotation} >>",
            4: stream_object(content),
            5: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            **(objects or {}),
        }
    )
    return pylopdf.open(pdf).pages[0]


def assert_color(paint: pylopdf.Paint | None, rgb: tuple[float, float, float], alpha: float = 1) -> None:
    """Check a color paint; hayro holds opacity in eight bits."""
    assert isinstance(paint, Color)
    assert paint.rgb == pytest.approx(rgb)
    assert paint.alpha == pytest.approx(alpha, abs=1 / 255)


def test_paths_carry_their_fill_stroke_and_line_style() -> None:
    page = page_with(
        "/GS1 gs 1 0 0 rg 0 0 1 RG 2 w 1 J 2 j [3 2] 1 d 10 10 40 20 re B\n0 1 0 rg 60 10 m 80 40 100 0 120 30 c f*",
        resources="/ExtGState << /GS1 6 0 R >>",
        objects={6: "<< /Type /ExtGState /ca 0.25 /CA 0.5 >>"},
    )
    content = page.content()
    assert len(content) == 2
    rectangle, curve = content.paths

    assert_color(rectangle.fill, (1, 0, 0), 0.25)
    assert rectangle.fill_rule is FillRule.NONZERO
    assert rectangle.stroke is not None
    assert_color(rectangle.stroke.paint, (0, 0, 1), 0.5)
    assert rectangle.stroke.width == pytest.approx(2)
    assert rectangle.stroke.cap is LineCap.ROUND
    assert rectangle.stroke.join is LineJoin.BEVEL
    assert rectangle.stroke.dashes == pytest.approx((3, 2))
    assert rectangle.stroke.dash_offset == pytest.approx(1)
    assert rectangle.bbox == pytest.approx(Rect(10, 70, 50, 90))
    assert [type(segment) for segment in rectangle.segments] == [MoveTo, LineTo, LineTo, LineTo, ClosePath]

    assert_color(curve.fill, (0, 1, 0), 0.25)
    assert curve.fill_rule is FillRule.EVEN_ODD
    assert curve.stroke is None
    start, cubic = curve.segments
    assert start == MoveTo(Point(60, 90))
    assert isinstance(cubic, CurveTo)
    assert cubic.to == pytest.approx(Point(120, 70))
    assert curve.bbox == pytest.approx(Rect(60, 70, 120, 90))


def test_one_fill_and_stroke_operator_is_one_path() -> None:
    (path,) = page_with("10 10 40 20 re B").content().paths
    assert path.fill is not None
    assert path.stroke is not None


def test_a_fill_and_a_stroke_in_other_contexts_stay_two_paths() -> None:
    content = page_with("10 10 40 20 re f /Artifact BMC 10 10 40 20 re S EMC").content()
    fill, stroke = content.paths
    assert (fill.fill is not None, fill.stroke) == (True, None)
    assert (stroke.fill, stroke.stroke is not None) == (None, True)
    assert stroke.context.marked_content == (MarkedContent(tag="Artifact", mcid=None),)


def test_text_runs_report_glyphs_with_outline_boxes() -> None:
    content = page_with("BT /F1 24 Tf 20 40 Td (H i) Tj ET").content()
    (run,) = content.text_runs
    assert run.text == "H i"
    assert run.render_mode is TextRenderMode.FILL
    assert run.fill == Color(0, 0, 0, 1)
    assert run.stroke is None
    assert run.size == pytest.approx(24)
    assert not run.font.is_type3

    letter, space, dot = run.glyphs
    assert letter.text == "H"
    assert letter.glyph_id is not None
    assert letter.origin == pytest.approx(Point(20, 60))
    assert letter.size == pytest.approx(24)
    assert letter.matrix == pytest.approx((24, 0, 0, -24, 20, 60))
    # Helvetica's H stands on the baseline, reaches its cap height of 0.718 em,
    # and advances 0.722 em; the box comes from the outline, not from metrics.
    assert letter.advance is not None
    assert letter.advance == pytest.approx(0.722 * 24, abs=0.05)
    assert letter.bbox is not None
    assert letter.bbox.y1 == pytest.approx(60, abs=0.3)
    assert letter.bbox.y0 == pytest.approx(60 - 0.718 * 24, abs=0.5)
    assert letter.origin.x < letter.bbox.x0 < letter.bbox.x1 < letter.origin.x + letter.advance

    assert space.text == " "
    assert space.bbox is None
    assert space.origin.x == pytest.approx(letter.origin.x + letter.advance)
    assert dot.bbox is not None
    assert run.bbox == letter.bbox | dot.bbox


def test_text_render_modes_follow_the_painting() -> None:
    content = page_with(
        "BT /F1 12 Tf 1 0 0 RG 0 0 1 rg 10 50 Td 2 Tr (A) Tj 1 Tr (B) Tj 3 Tr (C) Tj 7 Tr (D) Tj 0 Tr (E) Tj ET"
    ).content()
    runs = content.text_runs
    assert [(run.text, run.render_mode) for run in runs] == [
        ("A", TextRenderMode.FILL_STROKE),
        ("B", TextRenderMode.STROKE),
        ("C", TextRenderMode.INVISIBLE),
        ("E", TextRenderMode.FILL),
    ]
    fill_stroke, stroke, invisible, _ = runs
    assert fill_stroke.fill == Color(0, 0, 1, 1)
    assert fill_stroke.stroke is not None
    assert fill_stroke.stroke.paint == Color(1, 0, 0, 1)
    assert stroke.fill is None
    assert stroke.stroke is not None
    assert (invisible.fill, invisible.stroke) == (None, None)
    assert invisible.glyphs[0].bbox is not None


def test_text_painted_with_fill_and_stroke_extracts_once() -> None:
    page = page_with("BT /F1 12 Tf 10 50 Td 2 Tr (Hello) Tj 0 Tr 0 -20 Td (World) Tj ET")
    assert page.text() == "Hello\nWorld\n"
    assert [word.text for word in page.words()] == ["Hello", "World"]
    (run, _) = page.content().text_runs
    assert run.render_mode is TextRenderMode.FILL_STROKE


def test_a_stroke_of_other_glyphs_is_read_as_its_own_text() -> None:
    page = page_with("BT /F1 12 Tf 10 50 Td (Hello) Tj 1 Tr 0 -20 Td (Hello) Tj ET")
    assert page.text() == "Hello\nHello\n"
    assert [run.render_mode for run in page.content().text_runs] == [TextRenderMode.FILL, TextRenderMode.STROKE]


def test_items_carry_clips_blend_modes_and_marked_content() -> None:
    page = page_with(
        "q 10 10 80 80 re W n /GS1 gs /Span << /MCID 7 >> BDC 1 0 0 rg 20 20 30 30 re f EMC Q "
        "0 0 1 rg 100 10 20 20 re f",
        resources="/ExtGState << /GS1 6 0 R >>",
        objects={6: "<< /Type /ExtGState /BM /Multiply /ca 0.5 >>"},
    )
    clipped, plain = page.content().paths

    assert clipped.blend_mode is BlendMode.MULTIPLY
    assert_color(clipped.fill, (1, 0, 0), 0.5)
    (clip,) = clipped.context.clips
    assert clip.bbox == pytest.approx(Rect(10, 10, 90, 90))
    assert clip.fill_rule is FillRule.NONZERO
    assert clipped.context.clip_bbox == pytest.approx(Rect(10, 10, 90, 90))
    assert clipped.context.marked_content == (MarkedContent(tag="Span", mcid=7),)
    assert clipped.context.groups == ()
    assert clipped.context.opacity == 1

    assert plain.blend_mode is BlendMode.NORMAL
    assert plain.context == GraphicsContext(clips=(), groups=(), marked_content=())
    assert plain.context.clip_bbox is None


def test_transparency_groups_carry_opacity_and_soft_masks() -> None:
    group = "/Type /XObject /Subtype /Form /BBox [0 0 200 100] /Group << /S /Transparency >>"
    page = page_with(
        "/GS1 gs /Fm Do",
        resources="/ExtGState << /GS1 6 0 R >> /XObject << /Fm 7 0 R >>",
        objects={
            6: "<< /Type /ExtGState /ca 0.25 /SMask << /S /Alpha /G 8 0 R >> >>",
            7: stream_object("0 1 0 rg 10 10 50 50 re f", group),
            # hayro reads a soft mask only when its group names a color space.
            8: stream_object("0 0 200 100 re f", group.replace(">>", "/CS /DeviceGray >>")),
        },
    )
    (path,) = page.content().paths
    outer = path.context.groups[0]
    assert outer.opacity == pytest.approx(0.25)
    assert outer.soft_mask == SoftMask(xref=8, subtype=SoftMaskType.ALPHA)
    assert outer.blend_mode is BlendMode.NORMAL
    assert path.context.opacity == pytest.approx(0.25)
    assert path.fill == Color(0, 1, 0, 1)


def test_items_share_the_context_they_were_painted_in() -> None:
    content = page_with("10 10 10 10 re f 30 10 10 10 re f /P BMC 50 10 10 10 re f EMC").content()
    first, second, third = content.paths
    assert first.context is second.context
    assert third.context is not first.context


def test_shadings_and_tiling_patterns_are_paints() -> None:
    tiling = "/PatternType 1 /PaintType 1 /TilingType 1 /BBox [0 0 10 10] /XStep 10 /YStep 12 /Resources << >>"
    page = page_with(
        "q 20 10 60 60 re W n /Sh sh Q /Pattern cs /P1 scn 120 10 40 40 re f",
        resources="/Shading << /Sh 6 0 R >> /Pattern << /P1 7 0 R >>",
        objects={
            6: (
                "<< /ShadingType 2 /ColorSpace /DeviceRGB /Coords [20 0 80 0] /Extend [true true] "
                "/Function << /FunctionType 2 /Domain [0 1] /C0 [1 0 0] /C1 [0 0 1] /N 1 >> >>"
            ),
            7: stream_object("0 0 5 5 re f", tiling),
        },
    )
    shaded, tiled = page.content().paths

    assert isinstance(shaded.fill, ShadingPattern)
    assert shaded.fill.shading_type is ShadingType.AXIAL
    assert shaded.fill.opacity == pytest.approx(1)
    (clip,) = shaded.context.clips
    assert shaded.bbox == pytest.approx(clip.bbox)
    assert shaded.bbox == pytest.approx(Rect(20, 30, 80, 90))

    assert isinstance(tiled.fill, TilingPattern)
    assert tiled.fill.bbox == Rect(0, 0, 10, 10)
    assert (tiled.fill.x_step, tiled.fill.y_step) == (10, 12)
    assert tiled.bbox == pytest.approx(Rect(120, 50, 160, 90))


def test_images_report_their_object_mask_and_placement() -> None:
    source = pylopdf.Document()
    source.pages.new((2, 1))
    pixmap = source.pages[0].get_pixmap(background=(10, 20, 30, 40), alpha=True)
    doc = pylopdf.Document()
    doc.pages.new((200, 100))
    xref = doc.pages[0].insert_image((10, 20, 50, 40), pixmap=pixmap)

    (image,) = doc.pages[0].content().images
    assert image.xref == xref
    assert (image.width, image.height) == (2, 1)
    assert (image.colorspace, image.components, image.bits) == ("DeviceRGB", 3, 8)
    assert image.mask is ImageMask.SOFT
    assert not image.is_stencil
    assert image.bbox == pytest.approx(Rect(10, 20, 50, 40))
    assert image.matrix == pytest.approx((40, 0, 0, 20, 10, 20))
    assert image.context.groups[-1].opacity == 1


def test_stencil_masks_are_images_without_an_object() -> None:
    page = page_with("q 0 0 1 rg 20 0 0 20 10 10 cm BI /W 2 /H 2 /IM true /BPC 1 ID \x00\x00 EI Q")
    (image,) = page.content().images
    assert image.is_stencil
    assert image.xref == 0
    assert image.colorspace == ""
    assert image.bbox == pytest.approx(Rect(10, 70, 30, 90))


def test_type3_glyphs_take_their_boxes_from_the_procedure() -> None:
    page = pylopdf.open(CORPUS[0].parent / "pdfium-type3.pdf").pages[0]
    with pytest.warns(pylopdf.PylopdfWarning):
        page.text()
    runs = page.content().text_runs
    assert runs
    for run in runs:
        assert run.font.is_type3
        for glyph in run.glyphs:
            assert glyph.glyph_id is None
            assert glyph.text is None
            assert glyph.advance is None
            assert glyph.bbox is not None
            assert glyph.bbox in run.bbox


def test_content_uses_rotated_display_coordinates() -> None:
    page = page_with("10 10 40 20 re f BT /F1 10 Tf 10 50 Td (A) Tj ET", rotate=90)
    (path,) = page.content().paths
    assert path.bbox == pytest.approx(Rect(10, 10, 30, 50))
    (run,) = page.content().text_runs
    a, b, c, d, _, _ = run.glyphs[0].matrix
    assert (a, b, c, d) == pytest.approx((0, 10, 10, 0))


def test_clip_keeps_the_items_that_meet_it() -> None:
    page = page_with("10 10 20 20 re f 150 10 20 20 re f")
    assert len(page.content()) == 2
    (kept,) = page.content(clip=(0, 0, 100, 100))
    assert kept.bbox.x0 == pytest.approx(10)
    assert len(page.content(clip=(30, 0, 150, 100))) == 2
    assert len(page.content(clip=(31, 0, 149, 100))) == 0


def test_annotations_can_be_left_out() -> None:
    page = pylopdf.open(CORPUS[0].parent / "pdfium-links-highlights-annots.pdf").pages[0]
    with_annotations = page.content()
    without = page.content(annotations=False)
    assert len(without) < len(with_annotations)
    assert with_annotations[: len(without)] == without.items


def test_max_elements_bounds_the_interpretation() -> None:
    page = page_with("10 10 20 20 re f")
    with pytest.raises(pylopdf.LimitError) as caught:
        page.content(max_elements=3)
    assert caught.value.code == "content_element_count"
    assert len(page.content(max_elements=None)) == 1

    # 90,000 strokes of two segments each pass the default of 262,144.
    dense = page_with(" ".join(f"{index % 190} 10 m {index % 190 + 5} 20 l S" for index in range(90_000)))
    with pytest.raises(pylopdf.LimitError, match="262144-element"):
        dense.content()


def _smallest_budget(page: pylopdf.Page) -> int:
    budget = 1
    while True:
        try:
            page.content(max_elements=budget)
        except pylopdf.LimitError:
            budget += 1
        else:
            return budget


@pytest.mark.parametrize("operator", ["S", "B"])
def test_max_elements_counts_dash_lengths(operator: str) -> None:
    # A dash array repeated by every stroke must not multiply memory, so each
    # stroke charges its dash lengths along with its segments.
    dashes = " ".join(["1"] * 100)
    plain = page_with(f"0 0 m 10 10 l {operator}")
    dashed = page_with(f"[{dashes}] 0 d 0 0 m 10 10 l {operator}")
    assert _smallest_budget(dashed) == _smallest_budget(plain) + 100
    (path,) = dashed.content().paths
    assert path.stroke is not None
    assert len(path.stroke.dashes) == 100


def test_text_paints_no_paths() -> None:
    assert page_with("BT /F1 12 Tf 10 50 Td (Text only) Tj ET").content().paths == ()


@pytest.mark.parametrize(
    ("options", "error"),
    [
        ({"max_elements": 0}, ValueError),
        ({"max_elements": True}, TypeError),
        ({"annotations": 1}, TypeError),
        ({"text_runs": 0}, TypeError),
        ({"paths": None}, TypeError),
        ({"images": "no"}, TypeError),
        ({"clip": (0, 0, 1)}, ValueError),
    ],
)
def test_content_validates_its_options(options: dict[str, object], error: type[Exception]) -> None:
    page = page_with("10 10 20 20 re f")
    with pytest.raises(error):
        page.content(**options)  # ty: ignore[invalid-argument-type]


def test_content_is_an_immutable_sequence() -> None:
    page = page_with("1 0 0 rg 10 10 20 20 re f BT /F1 12 Tf 10 50 Td (Hi) Tj ET")
    content = page.content()
    path, run = content
    assert content[0] is path
    assert content[-1] is run
    assert content[0:1] == (path,)
    assert content.paths == (path,)
    assert content.text_runs == (run,)
    assert content.images == ()
    assert content == page.content()
    assert hash(content) == hash(page.content())
    assert pickle.loads(pickle.dumps(content)) == content  # noqa: S301 - a value pickled here.
    assert repr(path).startswith("Path(segments=(MoveTo(to=Point(")
    with pytest.raises(dataclasses.FrozenInstanceError):
        content.items = ()  # ty: ignore[invalid-assignment]
    with pytest.raises(dataclasses.FrozenInstanceError):
        path.bbox = Rect()  # ty: ignore[invalid-assignment]


def test_content_reflects_edits() -> None:
    doc = pylopdf.Document()
    doc.pages.new((200, 100))
    page = doc.pages[0]
    assert len(page.content()) == 0
    page.draw_rect((10, 10, 50, 30), color=(1, 0, 0))
    (path,) = page.content().paths
    assert path.stroke is not None
    assert path.stroke.paint == Color(1, 0, 0, 1)
    assert path.bbox == pytest.approx(Rect(10, 10, 50, 30))


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.name)
def test_selected_kinds_equal_the_full_content_filtered(path: Path) -> None:
    doc = pylopdf.open(path)
    for page in doc.pages[:3]:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", pylopdf.PylopdfWarning)
            full = page.content()
            for text_runs, paths, images in (
                (False, True, True),
                (True, False, True),
                (True, True, False),
                (False, True, False),
            ):
                kept = tuple(
                    item
                    for item in full
                    if (text_runs or not isinstance(item, pylopdf.TextRun))
                    and (paths or not isinstance(item, pylopdf.Path))
                    and (images or not isinstance(item, pylopdf.Image))
                )
                selected = page.content(text_runs=text_runs, paths=paths, images=images)
                assert selected.items == kept, (text_runs, paths, images)


def test_no_kinds_give_empty_content() -> None:
    page = page_with("10 10 20 20 re f BT /F1 12 Tf 10 50 Td (Text) Tj ET")
    assert page.content(text_runs=False, paths=False, images=False).items == ()


@pytest.mark.parametrize("path", CORPUS, ids=lambda path: path.name)
def test_glyph_origins_agree_with_the_layout(path: Path) -> None:
    doc = pylopdf.open(path)
    for page in doc.pages:
        content = page.content()
        expected = pytest.warns(pylopdf.PylopdfWarning) if path.name == "pdfium-type3.pdf" else nullcontext()
        with expected:
            layout = page.layout(chars=True)
        characters = Counter(
            (char.text, round(char.origin.x, 3), round(char.origin.y, 3))
            for block in layout.blocks
            for line in block.lines
            for span in line.spans
            for char in span.chars
            if char.text.strip()
        )
        glyphs = Counter(
            (glyph.text, round(glyph.origin.x, 3), round(glyph.origin.y, 3))
            for run in content.text_runs
            for glyph in run.glyphs
            if glyph.text is not None and glyph.text.strip()
        )
        assert glyphs == characters
