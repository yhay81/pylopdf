"""非埋め込み CJK フォントの fallback レンダリングのテスト。

フォント実体はリポジトリ同梱の fonts/pylopdf-fonts-cjk/（Noto Sans/Serif JP）を使う。
自動検出のテストだけは pylopdf_fonts_cjk がインストール済みのときに実行される
（`uv sync --all-extras` で入る。CI は常に実行）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import build_nonembedded_cjk_pdf

import pylopdf

FONTS_DIR = Path(__file__).parents[1] / "fonts" / "pylopdf-fonts-cjk" / "src" / "pylopdf_fonts_cjk"
SANS = FONTS_DIR / "NotoSansJP-Regular.otf"
SERIF = FONTS_DIR / "NotoSerifJP-Regular.otf"


def render_blank_baseline() -> bytes:
    """代替フォントなし（自動検出も無効）の描画結果 = 文字が出ない基準画像。"""
    doc = pylopdf.open(stream=build_nonembedded_cjk_pdf())
    doc.set_fallback_font(None)
    return doc.render_page(0, 1.0)


def test_without_fallback_renders_blank() -> None:
    """代替フォントがなければ文字は描画されない（従来挙動の確認）。"""
    png = render_blank_baseline()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_fallback_font_from_path_renders_text() -> None:
    doc = pylopdf.open(stream=build_nonembedded_cjk_pdf())
    doc.set_fallback_font(SANS)
    png = doc.render_page(0, 1.0)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    # 文字が描画されればピクセルが増えて PNG は blank と一致しなくなる
    assert png != render_blank_baseline()
    assert len(png) > len(render_blank_baseline())


def test_fallback_font_from_bytes_renders_text() -> None:
    doc = pylopdf.open(stream=build_nonembedded_cjk_pdf())
    doc.set_fallback_font(SANS.read_bytes())
    assert doc.render_page(0, 1.0) != render_blank_baseline()


def test_serif_slot_used_for_mincho() -> None:
    """MS-Mincho（明朝系）の PDF は serif スロットのフォントで描画される。"""
    doc = pylopdf.open(stream=build_nonembedded_cjk_pdf())
    doc.set_fallback_font(SERIF, kind="serif")
    assert doc.render_page(0, 1.0) != render_blank_baseline()


def test_svg_rendering_includes_glyphs() -> None:
    blank_doc = pylopdf.open(stream=build_nonembedded_cjk_pdf())
    blank_doc.set_fallback_font(None)
    blank_svg = blank_doc.render_page_svg(0)

    doc = pylopdf.open(stream=build_nonembedded_cjk_pdf())
    doc.set_fallback_font(SANS)
    svg = doc.render_page_svg(0)
    assert svg.startswith("<svg")
    assert len(svg) > len(blank_svg)


def test_invalid_kind_raises() -> None:
    doc = pylopdf.open(stream=build_nonembedded_cjk_pdf())
    with pytest.raises(ValueError, match="kind"):
        doc.set_fallback_font(SANS, kind="bold")


def test_latin_rendering_unaffected_by_fallback(one_page_pdf: bytes) -> None:
    """代替フォントを設定してもラテン文字 PDF の描画は変わらない。"""
    plain = pylopdf.open(stream=one_page_pdf)
    plain.set_fallback_font(None)
    baseline = plain.render_page(0, 1.0)

    with_font = pylopdf.open(stream=one_page_pdf)
    with_font.set_fallback_font(SANS)
    assert with_font.render_page(0, 1.0) == baseline


def test_auto_discovery_via_extra() -> None:
    """pylopdf[cjk] が入っていれば、何も設定しなくても CJK が描画される。"""
    pytest.importorskip("pylopdf_fonts_cjk")
    doc = pylopdf.open(stream=build_nonembedded_cjk_pdf())
    png = doc.render_page(0, 1.0)
    assert png != render_blank_baseline()


@pytest.mark.xfail(
    reason="lopdf は定義済み CMap（90ms-RKSJ-H）のデコードに未対応で invalid character encoding になる",
    strict=True,
)
def test_nonembedded_cjk_extract_text_known_limit() -> None:
    doc = pylopdf.open(stream=build_nonembedded_cjk_pdf())
    assert "こんにちは" in doc.get_page_text(0)
