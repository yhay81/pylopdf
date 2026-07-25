"""Tests for vector drawing extraction."""

from __future__ import annotations

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf


def build_drawing_pdf(*, rotation: int = 0) -> bytes:
    """Build fill-stroke and even-odd cubic paths with styled paint."""
    stream = (
        "/GS1 gs 1 0 0 rg 0 0 1 RG 2 w 1 J 2 j [3 2] 1 d 10 10 40 20 re B\n0 1 0 rg 60 10 m 80 40 100 0 120 30 c f*"
    )
    rotate = f" /Rotate {rotation}" if rotation else ""
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: (
                "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 100] "
                "/Resources << /ExtGState << /GS1 5 0 R >> >> >>"
            ),
            3: f"<< /Type /Page /Parent 2 0 R /Contents 4 0 R{rotate} >>",
            4: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
            5: "<< /Type /ExtGState /ca 0.25 /CA 0.5 >>",
        }
    )


def test_get_drawings_returns_styled_paths() -> None:
    drawings = pylopdf.open(stream=build_drawing_pdf())[0].get_drawings()
    assert len(drawings) == 2

    rectangle = drawings[0]
    assert rectangle["type"] == "fs"
    assert rectangle["rect"] == pytest.approx(pylopdf.Rect(10, 70, 50, 90))
    assert rectangle["closePath"]
    assert len(rectangle["items"]) == 4
    assert all(item[0] == "l" for item in rectangle["items"])
    assert rectangle["color"] == pytest.approx((0, 0, 1))
    assert rectangle["fill"] == pytest.approx((1, 0, 0))
    assert rectangle["stroke_opacity"] == pytest.approx(0.5, abs=1 / 255)
    assert rectangle["fill_opacity"] == pytest.approx(0.25, abs=1 / 255)
    assert rectangle["even_odd"] is False
    assert rectangle["width"] == pytest.approx(2)
    assert rectangle["lineCap"] == (1, 1, 1)
    assert rectangle["lineJoin"] == 2
    assert rectangle["dashes"] == "[3 2] 1"

    curve = drawings[1]
    assert curve["type"] == "f"
    assert not curve["closePath"]
    assert [item[0] for item in curve["items"]] == ["c"]
    assert curve["color"] is None
    assert curve["fill"] == pytest.approx((0, 1, 0))
    assert curve["fill_opacity"] == pytest.approx(0.25, abs=1 / 255)
    assert curve["even_odd"] is True
    assert curve["width"] is None
    assert curve["lineCap"] is None
    assert curve["lineJoin"] is None
    assert curve["dashes"] is None


def test_get_drawings_uses_rotated_display_coordinates() -> None:
    drawing = pylopdf.open(stream=build_drawing_pdf(rotation=90))[0].get_drawings()[0]
    assert drawing["rect"] == pytest.approx(pylopdf.Rect(10, 10, 30, 50))


def test_get_drawings_ignores_text() -> None:
    assert pylopdf.open(stream=build_pdf(["Text only"]))[0].get_drawings() == []


def test_get_drawings_rejects_excessive_path_count() -> None:
    stream = "\n".join("0 0 m 1 1 l S" for _ in range(8193))
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 10 10] >>",
            3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
            4: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        }
    )

    with pytest.raises(pylopdf.PdfError, match="8192-path safety limit"):
        pylopdf.open(stream=pdf)[0].get_drawings()


def test_get_drawings_rejects_excessive_command_count() -> None:
    stream = "0 0 m\n" + "1 1 l\n" * 131_073 + "S"
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 10 10] >>",
            3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
            4: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        }
    )

    with pytest.raises(pylopdf.PdfError, match="131072-command safety limit"):
        pylopdf.open(stream=pdf)[0].get_drawings()
