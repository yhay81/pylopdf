"""Page オブジェクト（doc[i]）と回転・ページボックスのテスト。"""

from __future__ import annotations

import pytest
from conftest import png_size

import pylopdf


def test_getitem_and_iteration(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    page = doc[1]
    assert isinstance(page, pylopdf.Page)
    assert page.number == 1
    assert page.parent is doc
    assert [p.number for p in doc] == [0, 1, 2]
    assert doc.load_page(2).number == 2


def test_negative_index(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    assert doc[-1].number == 2
    assert "Page three" in doc.get_page_text(-1)
    with pytest.raises(IndexError):
        _ = doc[-4]


def test_page_get_text_and_render(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    page = doc[1]
    assert "Page two" in page.get_text()
    assert page.render() == doc.render_page(1)
    assert page.render_svg() == doc.render_page_svg(1)


def test_stale_page_after_structure_change(three_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=three_page_pdf)
    page = doc[2]
    doc.delete_page(0)
    with pytest.raises(pylopdf.StalePageError, match="取得し直して"):
        _ = page.get_text()
    # 取り直せば使える（旧 2 ページ目は削除で 1 ページ目に繰り上がる）
    assert "Page three" in doc[1].get_text()


def test_page_on_closed_document(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    page = doc[0]
    doc.close()
    with pytest.raises(pylopdf.DocumentClosedError):
        _ = page.rotation


def test_mediabox_inherited_from_parent(one_page_pdf: bytes) -> None:
    # conftest の build_pdf は MediaBox を親 Pages 側に置いている（継承解決の検証）
    doc = pylopdf.Document(stream=one_page_pdf)
    assert doc[0].mediabox == pylopdf.Rect(0.0, 0.0, 612.0, 792.0)
    assert doc[0].cropbox == doc[0].mediabox  # CropBox 無し → MediaBox と同値
    assert doc[0].rect.width == 612.0
    assert doc[0].rect.height == 792.0


def test_set_mediabox_roundtrip(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    doc[0].set_mediabox((0, 0, 300, 400))
    assert doc[0].mediabox == pylopdf.Rect(0.0, 0.0, 300.0, 400.0)
    # 保存後も維持され、レンダリングサイズにも反映される
    reopened = pylopdf.Document(stream=doc.tobytes())
    assert reopened[0].mediabox == pylopdf.Rect(0.0, 0.0, 300.0, 400.0)
    assert png_size(reopened.render_page(0)) == (300, 400)


def test_set_cropbox(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    doc[0].set_cropbox((10, 10, 310, 410))
    assert doc[0].cropbox == pylopdf.Rect(10.0, 10.0, 310.0, 410.0)
    assert doc[0].mediabox == pylopdf.Rect(0.0, 0.0, 612.0, 792.0)  # MediaBox は不変
    # レンダリングは CropBox サイズになる
    assert png_size(doc.render_page(0)) == (300, 400)


@pytest.mark.parametrize(
    "rect",
    [(0, 0, -10, 400), (0, 0, 612, 0), (0, 0, float("nan"), 400), (0, 0, 612), "abcd"],
)
def test_set_box_invalid(one_page_pdf: bytes, rect: object) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(ValueError, match="MediaBox"):
        doc[0].set_mediabox(rect)  # type: ignore[arg-type]


def test_rotation_roundtrip_and_render(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    page = doc[0]
    assert page.rotation == 0
    page.set_rotation(90)
    assert page.rotation == 90
    # 回転はレンダリングの縦横に反映される（612x792 → 792x612）
    assert png_size(doc.render_page(0)) == (792, 612)
    page.set_rotation(-90)
    assert page.rotation == 270
    reopened = pylopdf.Document(stream=doc.tobytes())
    assert reopened[0].rotation == 270


def test_rotation_invalid(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    with pytest.raises(ValueError, match="90 の倍数"):
        doc[0].set_rotation(45)


def test_rect_swaps_for_rotation(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    doc[0].set_rotation(90)
    r = doc[0].rect
    assert (r.width, r.height) == (792.0, 612.0)
    assert doc[0].mediabox.width == 612.0  # mediabox 自体は回転の影響を受けない


def test_rect_helpers() -> None:
    r = pylopdf.Rect(10.0, 20.0, 110.0, 220.0)
    assert r.width == 100.0
    assert r.height == 200.0
    x0, y0, x1, y1 = r  # タプルとして展開できる
    assert (x0, y0, x1, y1) == (10.0, 20.0, 110.0, 220.0)


def test_page_repr(one_page_pdf: bytes) -> None:
    doc = pylopdf.Document(stream=one_page_pdf)
    assert repr(doc[0]) == "<Page 0 of <pylopdf.Document>>"
