"""注釈 API（Page.annots / add_highlight_annot / add_link_annot）のテスト。

ハイライトは外観ストリーム（AP）を生成するため、hayro のレンダリングにも
現れる。見た目の正しさはレンダリング画素で検証する。
"""

from __future__ import annotations

import pytest
from conftest import build_pdf

import pylopdf

YELLOWISH_BLUE_MAX = 210
WHITE_MIN = 240


def _region_pixels(page: pylopdf.Page, rect: pylopdf.Rect) -> list[tuple[int, int, int]]:
    """白背景でレンダリングした rect 内の RGB 画素列を返す。"""
    pix = page.get_pixmap(background=(255, 255, 255))
    out = []
    for y in range(int(rect.y0), min(int(rect.y1), pix.height)):
        for x in range(int(rect.x0), min(int(rect.x1), pix.width)):
            offset = y * pix.stride + x * 4
            r, g, b = pix.samples[offset : offset + 3]
            out.append((r, g, b))
    return out


def _has_yellowish(pixels: list[tuple[int, int, int]]) -> bool:
    """黄色系（赤緑が高く青が下がった）画素があるか。"""
    return any(r > WHITE_MIN and g > WHITE_MIN and b < YELLOWISH_BLUE_MAX for r, g, b in pixels)


def test_search_and_highlight() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello PDF"]))
    page = doc[0]
    hits = page.search_for("Hello")
    assert hits
    page.add_highlight_annot(hits)

    annots = page.annots()
    assert len(annots) == 1
    annot = annots[0]
    assert annot["type"] == "Highlight"
    assert annot["uri"] is None
    hit = hits[0]
    # 注釈の rect は検索ヒットを覆う（座標は f32 格納なので僅かな丸めを許容）
    assert annot["rect"].x0 <= hit.x0 + 0.01
    assert annot["rect"].x1 >= hit.x1 - 0.01
    # AP により hayro のレンダリングでも黄色く見える
    assert _has_yellowish(_region_pixels(page, hit))
    # テキスト（黒画素）は Multiply ブレンドで潰れず残る
    assert any(r < 128 for r, _, _ in _region_pixels(page, hit))


def test_highlight_survives_save_roundtrip() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello PDF"]))
    hits = doc[0].search_for("PDF")
    doc[0].add_highlight_annot(hits, content="review note")
    reopened = pylopdf.open(stream=doc.tobytes())
    annots = reopened[0].annots()
    assert len(annots) == 1
    assert annots[0]["type"] == "Highlight"
    assert annots[0]["contents"] == "review note"
    assert _has_yellowish(_region_pixels(reopened[0], hits[0]))


def test_highlight_multiple_rects_in_one_annot() -> None:
    doc = pylopdf.open(stream=build_pdf(["Hello PDF"]))
    page = doc[0]
    rects = [pylopdf.Rect(10, 10, 40, 20), pylopdf.Rect(60, 40, 90, 50)]
    page.add_highlight_annot(rects)
    annots = page.annots()
    assert len(annots) == 1
    # 外接矩形は両方を覆う
    assert annots[0]["rect"].x0 <= 10
    assert annots[0]["rect"].x1 >= 90
    assert annots[0]["rect"].y1 >= 50
    assert _has_yellowish(_region_pixels(page, rects[0]))
    assert _has_yellowish(_region_pixels(page, rects[1]))


def test_highlight_single_rect_argument() -> None:
    doc = pylopdf.Document()
    doc.new_page(width=100, height=100)
    page = doc[0]
    page.add_highlight_annot((20, 20, 80, 40))
    assert page.annots()[0]["type"] == "Highlight"
    assert _has_yellowish(_region_pixels(page, pylopdf.Rect(20, 20, 80, 40)))


def test_highlight_on_rotated_page_uses_display_coordinates() -> None:
    doc = pylopdf.Document()
    doc.new_page(width=100, height=200)
    page = doc[0]
    page.set_rotation(90)  # 表示は 200x100
    target = pylopdf.Rect(120, 30, 180, 70)
    page.add_highlight_annot(target)
    assert _has_yellowish(_region_pixels(page, target))
    assert not _has_yellowish(_region_pixels(page, pylopdf.Rect(10, 10, 60, 60)))
    # 読み取りも表示座標で返る
    annot_rect = page.annots()[0]["rect"]
    assert abs(annot_rect.x0 - target.x0) < 1
    assert abs(annot_rect.y1 - target.y1) < 1


def test_add_link_annot_reads_back() -> None:
    doc = pylopdf.open(stream=build_pdf(["Visit example"]))
    page = doc[0]
    hits = page.search_for("example")
    page.add_link_annot(hits[0], "https://example.com/")
    reopened = pylopdf.open(stream=doc.tobytes())
    annots = reopened[0].annots()
    assert len(annots) == 1
    assert annots[0]["type"] == "Link"
    assert annots[0]["uri"] == "https://example.com/"


def test_annots_empty_on_fresh_page() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    assert doc[0].annots() == []


def test_annots_rejects_bad_input() -> None:
    doc = pylopdf.Document()
    doc.new_page()
    page = doc[0]
    with pytest.raises(ValueError, match="rects"):
        page.add_highlight_annot([])
    with pytest.raises(ValueError, match="opacity"):
        page.add_highlight_annot((10, 10, 50, 20), opacity=0)
    with pytest.raises(ValueError, match="uri"):
        page.add_link_annot((10, 10, 50, 20), "")
