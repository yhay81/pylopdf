"""Groups of nearby paths, as pymupdf's cluster_drawings finds them."""

from __future__ import annotations

import pytest
from test_text_options import build_text_pdf

import pylopdf


def _content(stream: str) -> pylopdf.Content:
    return pylopdf.open(build_text_pdf(stream)).pages[0].content(text_runs=False, images=False)


def test_nearby_paths_form_one_cluster_and_distant_ones_another() -> None:
    content = _content(
        "10 700 20 20 re f 32 700 20 20 re f "  # 2 points apart: one group
        "300 700 20 20 re f "  # far away
        "10 600 100 10 re f 50 595 4 4 re f"  # a small box 1 point below the bar
    )

    clusters = content.path_clusters()

    assert clusters == [
        pylopdf.Rect(10, 72, 52, 92),
        pylopdf.Rect(300, 72, 320, 92),
        pylopdf.Rect(10, 182, 110, 197),
    ]
    assert content.path_clusters(x_tolerance=0, y_tolerance=0)[:2] == [
        pylopdf.Rect(10, 72, 30, 92),
        pylopdf.Rect(32, 72, 52, 92),
    ]


def test_a_cluster_grows_to_hold_what_its_box_reaches() -> None:
    # The two bars do not come near each other, but the box of the first and
    # the corner joins them, as a growing cluster does.
    content = _content("10 700 100 2 re f 108 600 2 102 re f 10 600 2 2 re f")

    assert content.path_clusters() == [pylopdf.Rect(10, 90, 110, 192)]


def test_strokes_count_with_their_width_and_areas_without_ink_are_left_out() -> None:
    content = _content("4 w 10 700 m 100 700 l S 0 w 10 600 m 100 600 l S")

    # The 4-point stroke widens the line by 2 points on every side.
    assert content.path_clusters() == [pylopdf.Rect(8, 90, 102, 94)]


def test_path_clusters_validate_their_tolerances() -> None:
    content = _content("10 700 20 20 re f")

    for value in (-1, float("inf"), True, "3"):
        with pytest.raises(ValueError, match="x_tolerance must be"):
            content.path_clusters(x_tolerance=value)  # ty: ignore[invalid-argument-type]
    assert _content("").path_clusters() == []
