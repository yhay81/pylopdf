"""Line, Polygon, PolyLine, and Ink annotations, their points, and their line endings."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import Border, Color, Ink, LineAnnotation, LineEnding, Point, Polygon, PolyLine

BLUE = Color(0.0, 0.0, 1.0)
YELLOW = Color(1.0, 1.0, 0.0)
GREEN = Color(0.0, 1.0, 0.0)
WHITE = (255, 255, 255)


def _pixel(pixmap: pylopdf.Pixmap, x: float, y: float) -> tuple[int, int, int]:
    offset = int(y) * pixmap.stride + int(x) * pixmap.channels
    red, green, blue = bytes(pixmap)[offset : offset + 3]
    return red, green, blue


def _flat(points: object) -> tuple[float, ...]:
    """Flatten points, or strokes of points, for pytest.approx."""
    if isinstance(points, Point):
        return tuple(points)
    return tuple(value for item in cast("tuple[object, ...]", points) for value in _flat(item))


def _page(rotation: int = 0) -> pylopdf.Page:
    page = pylopdf.Document().pages.new((300, 300))
    page.rotation = rotation
    return page


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_lines_polygons_and_ink_render_where_their_points_are(rotation: int) -> None:
    page = _page(rotation)

    line = page.annotations.add(
        LineAnnotation(
            (20, 20),
            (150, 20),
            color=BLUE,
            interior_color=YELLOW,
            border=Border(2),
            line_endings=(LineEnding.CLOSED_ARROW, LineEnding.CIRCLE),
        )
    )
    polygon = page.annotations.add(Polygon([(20, 60), (120, 60), (70, 140)], interior_color=GREEN))
    polyline = page.annotations.add(PolyLine([Point(150, 60), (250, 60), (250, 140)], border=Border(2)))
    ink = page.annotations.add(Ink([[(160, 160), (200, 180), (240, 160)], [(270, 170)]], border=Border(4)))

    assert [annotation.type.name for annotation in page.annotations] == ["LINE", "POLYGON", "POLY_LINE", "INK"]
    assert _flat(line.vertices) == pytest.approx(_flat((Point(20, 20), Point(150, 20))), abs=1e-3)
    assert line.line_endings == (LineEnding.CLOSED_ARROW, LineEnding.CIRCLE)
    assert _flat(polygon.vertices) == pytest.approx(_flat((Point(20, 60), Point(120, 60), Point(70, 140))), abs=1e-3)
    assert polygon.line_endings is None
    assert polyline.line_endings == (LineEnding.NONE, LineEnding.NONE)
    strokes = ((Point(160, 160), Point(200, 180), Point(240, 160)), (Point(270, 170),))
    assert _flat(ink.strokes) == pytest.approx(_flat(strokes), abs=1e-3)
    assert ink.vertices is None
    assert line.strokes is None
    # The rectangle holds the line, its stroke, and its endings.
    assert line.rect.contains((14, 14, 156, 26))

    pixmap = page.pixmap(dpi=72)
    assert _pixel(pixmap, 80, 20) == (0, 0, 255)
    assert _pixel(pixmap, 152, 20) == (255, 255, 0)  # inside the end circle
    assert _pixel(pixmap, 70, 90) == (0, 255, 0)
    assert _pixel(pixmap, 70, 50) == WHITE
    assert _pixel(pixmap, 250, 100) == (255, 0, 0)
    assert _pixel(pixmap, 200, 179) == (255, 0, 0)
    assert _pixel(pixmap, 270, 170) == (255, 0, 0)  # a one-point stroke draws a dot


@pytest.mark.parametrize("ending", list(LineEnding))
def test_every_line_ending_draws_at_the_end(ending: LineEnding) -> None:
    page = _page()

    line = page.annotations.add(
        LineAnnotation((40, 100), (200, 100), border=Border(2), interior_color=BLUE, line_endings=(ending, ending))
    )

    assert line.line_endings == (ending, ending)
    pixmap = page.pixmap(dpi=72)
    # Every ending but none reaches across the line beside its end.
    marked = any(_pixel(pixmap, x, y) != WHITE for x in range(186, 212) for y in (94, 95, 105, 106))
    assert marked is (ending is not LineEnding.NONE)


def test_line_annotations_survive_save_and_reopen() -> None:
    page = _page(90)
    page.annotations.add(
        PolyLine(
            [(10, 10), (100, 40), (60, 120)],
            color=BLUE,
            border=Border(3, dashes=(4, 2)),
            line_endings=(LineEnding.SQUARE, LineEnding.REVERSED_CLOSED_ARROW),
            opacity=0.8,
            contents="route",
        )
    )
    page.annotations.add(Ink([[(20, 150), (60, 170)]], color=GREEN, border=Border(4)))

    reopened = pylopdf.open(page.document.to_bytes()).pages[0]

    polyline, ink = reopened.annotations
    assert _flat(polyline.vertices) == pytest.approx(_flat((Point(10, 10), Point(100, 40), Point(60, 120))), abs=1e-3)
    assert polyline.line_endings == (LineEnding.SQUARE, LineEnding.REVERSED_CLOSED_ARROW)
    assert polyline.border == Border(3, dashes=(4, 2))
    assert polyline.color == BLUE
    assert polyline.opacity == pytest.approx(0.8)
    assert polyline.contents == "route"
    assert list(polyline.obj["LE"]) == ["Square", "RClosedArrow"]
    assert _flat(ink.strokes) == pytest.approx(_flat(((Point(20, 150), Point(60, 170)),)), abs=1e-3)
    assert ink.color == GREEN
    assert "IC" not in ink.obj
    assert _pixel(reopened.pixmap(dpi=72), 40, 160) == (0, 255, 0)


def test_specifications_validate_their_points() -> None:
    assert LineAnnotation((0, 0), Point(1, 1)).line_endings == (LineEnding.NONE, LineEnding.NONE)
    assert LineAnnotation((0, 0), (1, 1), line_endings=cast("Any", ("slash", "butt"))).line_endings == (
        LineEnding.SLASH,
        LineEnding.BUTT,
    )
    with pytest.raises(TypeError, match="a point"):
        LineAnnotation(cast("Any", (0, 0, 0)), (1, 1))
    with pytest.raises(TypeError, match="a point"):
        LineAnnotation(cast("Any", "ab"), (1, 1))
    with pytest.raises(ValueError, match="finite"):
        LineAnnotation((0, float("nan")), (1, 1))
    with pytest.raises(TypeError, match="line_endings"):
        LineAnnotation((0, 0), (1, 1), line_endings=cast("Any", LineEnding.SLASH))
    with pytest.raises(ValueError, match="'wave'"):
        LineAnnotation((0, 0), (1, 1), line_endings=cast("Any", ("wave", "none")))
    with pytest.raises(ValueError, match="at least 2"):
        Polygon([(0, 0)])
    with pytest.raises(TypeError, match="sequence of points"):
        PolyLine(cast("Any", 5))
    with pytest.raises(ValueError, match="at least one stroke"):
        Ink([])
    with pytest.raises(ValueError, match="at least 1 points"):
        Ink([[]])
    with pytest.raises(TypeError, match="sequence of point sequences"):
        Ink(cast("Any", "x"))
    assert not hasattr(Ink([[(0, 0)]]), "interior_color")


def test_points_stop_at_the_limit_without_reading_further() -> None:
    def endless() -> Iterator[tuple[float, float]]:
        while True:
            yield (1.0, 2.0)

    with pytest.raises(pylopdf.LimitError) as raised:
        PolyLine(endless())
    assert raised.value.code == "annotation_point_count"
    with pytest.raises(pylopdf.LimitError):
        Ink([[(0.0, 0.0)] * 40000, [(0.0, 0.0)] * 30000])
    assert len(Polygon([(0.0, 0.0)] * 65536).vertices) == 65536


def test_the_core_repeats_the_point_checks() -> None:
    doc = pylopdf.Document()
    doc.pages.new((100, 100))
    core = doc._doc  # noqa: SLF001

    for spec, message in [
        ({"subtype": "Line", "points": [[(0.0, 0.0)]]}, "two points"),
        ({"subtype": "Polygon", "points": [[(0.0, 0.0)], [(1.0, 1.0)]]}, "two or more vertices"),
        ({"subtype": "Ink", "points": [[]]}, "one or more strokes"),
        ({"subtype": "Line", "rect": (0.0, 0.0, 1.0, 1.0)}, "takes points"),
        ({"subtype": "Square", "points": [[(0.0, 0.0)]]}, "takes a rectangle"),
        ({"subtype": "Line", "points": [[(0.0, 0.0), (float("inf"), 1.0)]]}, "finite"),
        ({"subtype": "Polygon", "points": [[(0.0, 0.0), (1.0, 1.0)]], "line_endings": ("None", "None")}, "no line"),
        ({"subtype": "Line", "points": [[(0.0, 0.0), (1.0, 1.0)]], "line_endings": ("Wave", "None")}, "unknown"),
    ]:
        with pytest.raises(pylopdf.PdfError, match=message):
            core.add_annotation(1, spec)
    with pytest.raises(pylopdf.LimitError) as raised:
        core.add_annotation(1, {"subtype": "Ink", "points": [[(0.0, 0.0)] * 65537]})
    assert raised.value.code == "annotation_point_count"
    assert "Annots" not in doc.pages[0].obj


def _without_appearance(entries: str) -> pylopdf.Document:
    return pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Annots [4 0 R] >>",
                4: f"<< /Type /Annot {entries} >>",
            }
        )
    )


@pytest.mark.parametrize(
    ("entries", "point"),
    [
        ("/Subtype /Line /Rect [0 0 200 100] /L [10 50 190 50] /C [0 0 1] /BS << /W 4 >>", (100, 50)),
        ("/Subtype /PolyLine /Rect [0 0 200 100] /Vertices [10 10 100 10 100 90] /C [0 0 1] /BS << /W 2 >>", (100, 50)),
        ("/Subtype /Polygon /Rect [0 0 200 100] /Vertices [10 10 190 10 100 90] /IC [0 0 1]", (100, 70)),
        ("/Subtype /Ink /Rect [0 0 200 100] /InkList [[10 50 190 50]] /C [0 0 1] /Border [0 0 4]", (100, 50)),
    ],
)
def test_rendering_draws_missing_line_appearances(entries: str, point: tuple[int, int]) -> None:
    doc = _without_appearance(entries)
    before = doc.to_bytes()

    assert _pixel(doc.pages[0].pixmap(dpi=72), *point) == (0, 0, 255)
    assert doc.to_bytes() == before


@pytest.mark.parametrize(
    "entries",
    [
        "/Subtype /Line /Rect [0 0 200 100] /L [10 50 190] /C [0 0 1]",
        "/Subtype /PolyLine /Rect [0 0 200 100] /Vertices [10 10 100] /C [0 0 1]",
        "/Subtype /Polygon /Rect [0 0 200 100] /Vertices (none) /IC [0 0 1]",
        "/Subtype /Ink /Rect [0 0 200 100] /InkList [[10 50 190 50] 7] /C [0 0 1]",
        "/Subtype /Line /Rect [0 0 200 100] /L [10 50 10 50] /C [0 0 1] /LE [/Circle /Circle] /IC [0 0 1]",
    ],
)
def test_malformed_geometry_reads_as_absent(entries: str) -> None:
    doc = _without_appearance(entries)

    (annotation,) = doc.pages[0].annotations
    if "/L [10 50 10 50]" in entries:
        # A line of no length draws its round endings and nothing else.
        assert annotation.vertices == (Point(10, 50), Point(10, 50))
        return
    assert annotation.vertices is None
    assert annotation.strokes is None
    assert bytes(doc.pages[0].pixmap(dpi=72)) == b"\xff" * (200 * 100 * 3)


def test_unknown_line_endings_read_as_none() -> None:
    doc = _without_appearance("/Subtype /Line /Rect [0 0 200 100] /L [10 50 190 50] /LE [/Wave]")

    assert doc.pages[0].annotations[0].line_endings == (LineEnding.NONE, LineEnding.NONE)


def test_reading_too_many_points_raises() -> None:
    numbers = " ".join("1" for _ in range(2 * 65537))
    doc = _without_appearance(f"/Subtype /Polygon /Rect [0 0 200 100] /Vertices [{numbers}]")

    with pytest.raises(pylopdf.LimitError) as raised:
        _ = doc.pages[0].annotations[0].vertices
    assert raised.value.code == "annotation_point_count"
    # Rendering leaves it undrawn.
    assert bytes(doc.pages[0].pixmap(dpi=72)) == b"\xff" * (200 * 100 * 3)
