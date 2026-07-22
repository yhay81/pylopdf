"""Pixmap レンダリング（Page.get_pixmap）と警告連携のテスト。"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest
from conftest import build_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"


def test_pixmap_dimensions_and_samples(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(stream=one_page_pdf)
    pix = doc[0].get_pixmap()
    assert isinstance(pix, pylopdf.Pixmap)
    assert pix.width == 612
    assert pix.height == 792
    assert pix.n == 4
    assert pix.stride == pix.width * 4
    assert len(pix.samples) == pix.width * pix.height * 4


def test_pixmap_scale_and_dpi(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(stream=one_page_pdf)
    assert doc[0].get_pixmap(scale=2.0).width == 612 * 2
    assert doc[0].get_pixmap(dpi=144).width == 612 * 2
    with pytest.raises(ValueError, match="同時に指定"):
        doc[0].get_pixmap(scale=2.0, dpi=144)


def test_pixmap_tobytes_is_png(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(stream=one_page_pdf)
    pix = doc[0].get_pixmap()
    assert pix.tobytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_pixmap_background(one_page_pdf: bytes) -> None:
    """背景色を指定すると不透明ピクセルになる（既定は透明背景）。"""
    doc = pylopdf.open(stream=one_page_pdf)
    transparent = doc[0].get_pixmap()
    white = doc[0].get_pixmap(background=(255, 255, 255))
    assert transparent.samples[3] == 0
    assert white.samples[:4] == b"\xff\xff\xff\xff"


def test_pixmap_matches_png_rendering(one_page_pdf: bytes) -> None:
    """Pixmap と render_page の PNG は同じ画素になる。"""
    doc = pylopdf.open(stream=one_page_pdf)
    assert doc[0].get_pixmap().tobytes() == doc.render_page(0)


def build_broken_image_pdf() -> bytes:
    """壊れた DCT 画像を描画する 1 ページ PDF（ImageDecodeFailure を誘発する）。"""
    stream = "q 100 0 0 100 100 600 cm /Im0 Do Q"
    broken_jpeg = "\xff\xd8\xff"  # SOI だけの壊れた JPEG（latin-1 エンコードで実バイトになる）
    objects: dict[int, str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 612 792] >>",
        3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R /Resources << /XObject << /Im0 5 0 R >> >> >>",
        4: f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        5: "<< /Type /XObject /Subtype /Image /Width 8 /Height 8 /ColorSpace /DeviceRGB"
        " /BitsPerComponent 8 /Filter /DCTDecode /Length 3 >>\nstream\n" + broken_jpeg + "\nendstream",
    }
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n{objects[num]}\nendobj\n".encode("latin-1")
    xref_pos = len(out)
    size = len(objects) + 1
    out += f"xref\n0 {size}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for num in sorted(objects):
        out += f"{offsets[num]:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode("ascii")
    return bytes(out)


def test_broken_image_emits_warning() -> None:
    doc = pylopdf.open(stream=build_broken_image_pdf())
    with pytest.warns(pylopdf.PylopdfWarning, match="デコード"):
        doc.render_page(0)


def test_clean_render_emits_no_warning(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(stream=one_page_pdf)
    with warnings.catch_warnings():
        warnings.simplefilter("error", pylopdf.PylopdfWarning)
        doc.render_page(0)
        doc.get_page_text(0)


def test_warnings_do_not_leak_between_operations() -> None:
    """壊れた PDF の警告が、次のクリーンな操作に持ち越されない。"""
    broken = pylopdf.open(stream=build_broken_image_pdf())
    with pytest.warns(pylopdf.PylopdfWarning):
        broken.render_page(0)
    with warnings.catch_warnings():
        warnings.simplefilter("error", pylopdf.PylopdfWarning)
        broken.get_page_text(0)


def test_corpus_render_has_no_warnings() -> None:
    """コーパスの通常 PDF はレンダリングしても警告を出さない。"""
    doc = pylopdf.open(ASSETS / "usrguide.pdf")
    with warnings.catch_warnings():
        warnings.simplefilter("error", pylopdf.PylopdfWarning)
        doc[0].get_pixmap(scale=0.5)


def test_multi_page_pixmap() -> None:
    doc = pylopdf.open(stream=build_pdf(["One", "Two"], page_size=(200, 100)))
    pix = doc[1].get_pixmap()
    assert (pix.width, pix.height) == (200, 100)
