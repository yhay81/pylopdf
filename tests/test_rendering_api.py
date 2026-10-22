"""The rendering surface: Page.pixmap, Page.svg, Document.render, and Pixmap."""

from __future__ import annotations

import pytest
from conftest import build_pdf

import pylopdf

PNG_COLOR_TYPE_OFFSET = 25
PNG_RGB = 2
PNG_RGBA = 6


def _page() -> pylopdf.Page:
    return pylopdf.open(build_pdf(["Rendered"], page_size=(200, 100))).pages[0]


def test_pixmap_defaults_to_opaque_rgb_on_white() -> None:
    pixmap = _page().pixmap()

    assert (pixmap.width, pixmap.height) == (200, 100)
    assert (pixmap.n, pixmap.alpha, pixmap.stride) == (3, False, 600)
    assert pixmap.colorspace == pylopdf.csRGB
    assert len(pixmap.samples) == 200 * 100 * 3
    assert pixmap.samples[:3] == b"\xff\xff\xff"
    assert repr(pixmap) == "<Pixmap 200x100 rgb>"
    assert pixmap.tobytes()[PNG_COLOR_TYPE_OFFSET] == PNG_RGB
    view = memoryview(pixmap)
    assert view.nbytes == len(pixmap.samples)


def test_pixmap_with_alpha_keeps_transparency() -> None:
    pixmap = _page().pixmap(alpha=True)

    assert (pixmap.n, pixmap.alpha, pixmap.stride) == (4, True, 800)
    assert pixmap.samples[:4] == b"\x00\x00\x00\x00"
    assert pixmap.tobytes()[PNG_COLOR_TYPE_OFFSET] == PNG_RGBA


def test_background_paints_beneath_the_page() -> None:
    page = _page()

    assert page.pixmap(background=(255, 0, 0)).samples[:3] == b"\xff\x00\x00"
    assert page.pixmap(alpha=True, background=(0, 0, 255, 128)).samples[:4] == b"\x00\x00\xff\x80"
    with pytest.raises(ValueError, match="alpha=False needs an opaque background"):
        page.pixmap(background=(0, 0, 255, 128))


def test_matrix_scales_each_axis_and_excludes_dpi() -> None:
    page = _page()

    pixmap = page.pixmap(matrix=pylopdf.Matrix(2, 3))
    assert (pixmap.width, pixmap.height) == (400, 300)
    assert page.pixmap(matrix=(0.5, 0, 0, 0.5, 0, 0)).width == 100
    # A translation moves the pixmap's origin in pymupdf, not its pixels.
    assert page.pixmap(matrix=pylopdf.Matrix(1, 0, 0, 1, 30, 40)).samples == page.pixmap().samples
    assert page.pixmap(dpi=36).width == 100
    with pytest.raises(ValueError, match="dpi or matrix, not both"):
        page.pixmap(matrix=pylopdf.Matrix(4, 4), dpi=36)
    with pytest.raises(ValueError, match="dpi or matrix, not both"):
        pylopdf.open(build_pdf(["One"])).render(matrix=pylopdf.Matrix(4, 4), dpi=36)


@pytest.mark.parametrize(
    "matrix",
    [pylopdf.Matrix(90), pylopdf.Matrix(1, 0.5, 0, 1, 0, 0), pylopdf.Matrix(-1, 0, 0, 1, 0, 0), (1, 0, 0, 0, 0, 0)],
)
def test_matrices_that_do_not_only_scale_raise(matrix: object) -> None:
    with pytest.raises(pylopdf.PdfError, match="matrix must scale"):
        _page().pixmap(matrix=matrix)  # ty: ignore[invalid-argument-type]


def test_negative_indexes_render_the_same_page() -> None:
    doc = pylopdf.open(build_pdf(["One", "Two"]))

    assert doc.pages[-1].pixmap(dpi=36, alpha=True).samples == doc.pages[1].pixmap(dpi=36, alpha=True).samples


def test_svg_draws_text_as_paths() -> None:
    svg = _page().svg()

    assert svg.startswith("<svg")
    assert "<text" not in svg


def _annotated_page() -> pylopdf.Page:
    doc = pylopdf.open(build_pdf([""], page_size=(200, 100)))
    page = doc.pages[0]
    page.add_highlight_annot([(20, 20, 180, 80)], color=(1, 0, 0))
    return page


def test_annotations_false_leaves_annotation_appearances_out() -> None:
    page = _annotated_page()
    blank = _page().pixmap(annotations=False)

    with_annotations = page.pixmap()
    without = page.pixmap(annotations=False)

    assert with_annotations.samples != without.samples
    assert set(without.samples) == {255}
    assert len(without.samples) == len(blank.samples)
    assert page.svg(annotations=False) != page.svg()
    doc = page.document
    assert doc.render(annotations=False) == [without.tobytes()]
    assert doc.render() == [with_annotations.tobytes()]


@pytest.mark.parametrize("name", ["pixmap", "svg"])
def test_annotations_must_be_a_bool(name: str) -> None:
    with pytest.raises(TypeError, match="annotations must be a bool"):
        getattr(_page(), name)(annotations=1)
    with pytest.raises(TypeError, match="annotations must be a bool"):
        pylopdf.open(build_pdf(["One"])).render(annotations=None)  # ty: ignore[invalid-argument-type]


def test_render_follows_the_pixmap_options() -> None:
    doc = pylopdf.open(build_pdf(["One", "Two"]))

    rgb, rgba = doc.render([0], dpi=36) + doc.render([0], dpi=36, alpha=True)

    assert rgb[PNG_COLOR_TYPE_OFFSET] == PNG_RGB
    assert rgba[PNG_COLOR_TYPE_OFFSET] == PNG_RGBA
    assert rgb == doc.pages[0].pixmap(dpi=36).tobytes()
    assert rgba == doc.pages[0].pixmap(dpi=36, alpha=True).tobytes()


def test_old_names_are_removed() -> None:
    for name in ("render_page", "render_page_svg", "render_pages", "get_page_pixmap"):
        assert not hasattr(pylopdf.Document, name)
    for name in ("render", "render_svg", "get_pixmap", "get_svg_image"):
        assert not hasattr(pylopdf.Page, name)
    with pytest.raises(TypeError):
        _page().pixmap(2)  # ty: ignore[too-many-positional-arguments]
    with pytest.raises(TypeError):
        _page().svg(None)  # ty: ignore[too-many-positional-arguments]


def test_rgb_pixmaps_insert_without_a_soft_mask() -> None:
    source = _page().pixmap(dpi=36)
    doc = pylopdf.open(build_pdf(["Target"]))

    xref = doc.pages[0].insert_image((10, 10, 110, 60), pixmap=source)

    image = doc.objects[xref]
    assert image["ColorSpace"] == "DeviceRGB"
    assert "SMask" not in image
    with_alpha = doc.pages[0].insert_image((10, 70, 110, 120), pixmap=_page().pixmap(dpi=36, alpha=True))
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
