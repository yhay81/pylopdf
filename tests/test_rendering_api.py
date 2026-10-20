"""pymupdf's rendering surface: get_pixmap, get_page_pixmap, get_svg_image, and Pixmap (M0)."""

from __future__ import annotations

import pytest
from conftest import build_pdf

import pylopdf

PNG_COLOR_TYPE_OFFSET = 25
PNG_RGB = 2
PNG_RGBA = 6


def _page() -> pylopdf.Page:
    return pylopdf.open(stream=build_pdf(["Rendered"], page_size=(200, 100)))[0]


def test_get_pixmap_defaults_to_opaque_rgb_on_white() -> None:
    pixmap = _page().get_pixmap()

    assert (pixmap.width, pixmap.height) == (200, 100)
    assert (pixmap.n, pixmap.alpha, pixmap.stride) == (3, False, 600)
    assert pixmap.colorspace == pylopdf.csRGB
    assert len(pixmap.samples) == 200 * 100 * 3
    assert pixmap.samples[:3] == b"\xff\xff\xff"
    assert repr(pixmap) == "<Pixmap 200x100 rgb>"
    assert pixmap.tobytes()[PNG_COLOR_TYPE_OFFSET] == PNG_RGB
    view = memoryview(pixmap)
    assert view.nbytes == len(pixmap.samples)


def test_get_pixmap_with_alpha_keeps_transparency() -> None:
    pixmap = _page().get_pixmap(alpha=True)

    assert (pixmap.n, pixmap.alpha, pixmap.stride) == (4, True, 800)
    assert pixmap.samples[:4] == b"\x00\x00\x00\x00"
    assert pixmap.tobytes()[PNG_COLOR_TYPE_OFFSET] == PNG_RGBA


def test_background_paints_beneath_the_page() -> None:
    page = _page()

    assert page.get_pixmap(background=(255, 0, 0)).samples[:3] == b"\xff\x00\x00"
    assert page.get_pixmap(alpha=True, background=(0, 0, 255, 128)).samples[:4] == b"\x00\x00\xff\x80"
    with pytest.raises(ValueError, match="alpha=False needs an opaque background"):
        page.get_pixmap(background=(0, 0, 255, 128))


def test_matrix_scales_each_axis_and_dpi_takes_precedence() -> None:
    page = _page()

    pixmap = page.get_pixmap(matrix=pylopdf.Matrix(2, 3))
    assert (pixmap.width, pixmap.height) == (400, 300)
    assert page.get_pixmap(matrix=(0.5, 0, 0, 0.5, 0, 0)).width == 100
    # A translation moves the pixmap's origin in pymupdf, not its pixels.
    assert page.get_pixmap(matrix=pylopdf.Matrix(1, 0, 0, 1, 30, 40)).samples == page.get_pixmap().samples
    assert page.get_pixmap(matrix=pylopdf.Matrix(4, 4), dpi=36).width == 100


@pytest.mark.parametrize(
    "matrix",
    [pylopdf.Matrix(90), pylopdf.Matrix(1, 0.5, 0, 1, 0, 0), pylopdf.Matrix(-1, 0, 0, 1, 0, 0), (1, 0, 0, 0, 0, 0)],
)
def test_matrices_that_do_not_only_scale_raise(matrix: object) -> None:
    with pytest.raises(pylopdf.PdfError, match="matrix must scale"):
        _page().get_pixmap(matrix=matrix)  # ty: ignore[invalid-argument-type]


def test_get_page_pixmap_is_the_page_pixmap() -> None:
    doc = pylopdf.open(stream=build_pdf(["One", "Two"]))

    assert doc.get_page_pixmap(1).samples == doc[1].get_pixmap().samples
    assert doc.get_page_pixmap(-1, dpi=36, alpha=True).samples == doc[1].get_pixmap(dpi=36, alpha=True).samples


def test_get_svg_image_renders_text_as_paths() -> None:
    page = _page()

    svg = page.get_svg_image()

    assert svg.startswith("<svg")
    assert page.get_svg_image(matrix=pylopdf.Identity) == svg
    with pytest.raises(pylopdf.PdfError, match="identity matrix"):
        page.get_svg_image(matrix=pylopdf.Matrix(2, 2))
    with pytest.raises(pylopdf.PdfError, match="text_as_path=False"):
        page.get_svg_image(text_as_path=False)


def test_render_pages_follows_get_pixmap_options() -> None:
    doc = pylopdf.open(stream=build_pdf(["One", "Two"]))

    rgb, rgba = doc.render_pages([0], dpi=36) + doc.render_pages([0], dpi=36, alpha=True)

    assert rgb[PNG_COLOR_TYPE_OFFSET] == PNG_RGB
    assert rgba[PNG_COLOR_TYPE_OFFSET] == PNG_RGBA
    assert rgb == doc[0].get_pixmap(dpi=36).tobytes()
    assert rgba == doc[0].get_pixmap(dpi=36, alpha=True).tobytes()


def test_synonyms_are_removed() -> None:
    for name in ("render_page", "render_page_svg"):
        assert not hasattr(pylopdf.Document, name)
    for name in ("render", "render_svg"):
        assert not hasattr(pylopdf.Page, name)
    with pytest.raises(TypeError):
        _page().get_pixmap(2)  # ty: ignore[too-many-positional-arguments]


def test_rgb_pixmaps_insert_without_a_soft_mask() -> None:
    source = _page().get_pixmap(dpi=36)
    doc = pylopdf.open(stream=build_pdf(["Target"]))

    xref = doc[0].insert_image((10, 10, 110, 60), pixmap=source)

    image = doc.objects[xref]
    assert image["ColorSpace"] == "DeviceRGB"
    assert "SMask" not in image
    with_alpha = doc[0].insert_image((10, 70, 110, 120), pixmap=_page().get_pixmap(dpi=36, alpha=True))
    assert with_alpha != xref
    assert "SMask" in doc.objects[with_alpha]


def test_colorspace_values() -> None:
    assert (pylopdf.csRGB.n, pylopdf.csRGB.name) == (3, "DeviceRGB")
    assert (pylopdf.csGRAY.n, pylopdf.csGRAY.name) == (1, "DeviceGray")
    assert (pylopdf.csCMYK.n, pylopdf.csCMYK.name) == (4, "DeviceCMYK")
    assert (pylopdf.CS_RGB, pylopdf.CS_GRAY, pylopdf.CS_CMYK) == (1, 2, 3)
    assert pylopdf.Colorspace(pylopdf.CS_GRAY) == pylopdf.csGRAY
    assert repr(pylopdf.csRGB) == "Colorspace(CS_RGB) - DeviceRGB"
    assert len({pylopdf.csRGB, pylopdf.Colorspace(pylopdf.CS_RGB)}) == 1
    with pytest.raises(pylopdf.PdfError):
        pylopdf.Colorspace(9)
    with pytest.raises(AttributeError):
        pylopdf.csRGB.n = 1  # ty: ignore[invalid-assignment]
