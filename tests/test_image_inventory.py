"""Image objects: doc.images, page.resources.images, drawn placements, extract, and to_pixmap."""

from __future__ import annotations

import zlib
from pathlib import Path

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"


def _image(width: int, height: int, data: bytes, extra: str = "", color_space: str = "/DeviceGray") -> bytes:
    compressed = zlib.compress(data)
    return (
        (
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            f"/ColorSpace {color_space} /BitsPerComponent 8 /Filter /FlateDecode {extra}"
            f"/Length {len(compressed)} >>\nstream\n"
        ).encode()
        + compressed
        + b"\nendstream"
    )


def _inventory_pdf() -> bytes:
    """Build a page that draws Im0 twice and a Form drawing Im2, and lists the unused Im1."""
    content = b"q 20 0 0 10 0 0 cm /Im0 Do Q q 20 0 0 10 30 0 cm /Im0 Do Q /Fm0 Do"
    form = b"q 4 0 0 4 0 20 cm /Im2 Do Q"
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R "
                "/Resources << /XObject << /Im0 5 0 R /Im1 7 0 R /Fm0 8 0 R >> >> >>"
            ),
            4: f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream",
            5: _image(2, 1, bytes([10, 20]), "/SMask 6 0 R "),
            6: _image(2, 1, bytes([255, 128])),
            7: _image(1, 1, bytes([1, 2, 3]), color_space="[/ICCBased 10 0 R]"),
            8: (
                "<< /Type /XObject /Subtype /Form /BBox [0 0 100 100] "
                f"/Resources << /XObject << /Im2 9 0 R /Im0 5 0 R >> >> /Length {len(form)} >>\nstream\n"
            ).encode()
            + form
            + b"\nendstream",
            9: _image(1, 1, bytes([0]), color_space="[/Indexed /DeviceRGB 0 <ff0000>]"),
            10: (b"<< /N 3 /Length 0 >>\nstream\n\nendstream"),
        }
    )


def test_resources_list_the_image_xobjects_that_the_page_reaches() -> None:
    page = pylopdf.open(_inventory_pdf()).pages[0]

    images = page.resources.images

    assert [image.xref for image in images] == [5, 7, 9]
    assert all(isinstance(image, pylopdf.ImageObject) for image in images)
    first, icc, indexed = images
    assert (first.width, first.height, first.bits, first.colorspace, first.filters) == (
        2,
        1,
        8,
        "DeviceGray",
        ("FlateDecode",),
    )
    assert first.soft_mask is not None
    assert first.soft_mask.xref == 6
    assert (first.mask, first.is_stencil, first.alternate_colorspace) == (None, False, None)
    assert (icc.colorspace, icc.alternate_colorspace) == ("ICCBased", "DeviceRGB")
    assert (indexed.colorspace, indexed.alternate_colorspace) == ("Indexed", "DeviceRGB")
    assert page.resources.images == images


def test_resources_list_no_images_without_image_xobjects() -> None:
    assert pylopdf.open(build_pdf(["Text only"])).pages[0].resources.images == ()


def test_document_images_map_every_image_xobject() -> None:
    doc = pylopdf.open(_inventory_pdf())

    assert list(doc.images) == [5, 6, 7, 9]
    assert len(doc.images) == 4
    assert 6 in doc.images
    assert 8 not in doc.images
    assert "5" not in doc.images
    assert doc.images[9] == doc.pages[0].resources.images[2]
    assert hash(doc.images[9]) == hash(doc.pages[0].resources.images[2])
    assert repr(doc.images[9]) == "<ImageObject 9>"
    assert doc.images[5].obj["Width"] == 2
    for xref in (1, 4, 8, 999):
        with pytest.raises(KeyError):
            doc.images[xref]
    with pytest.raises(TypeError):
        doc.images["5"]  # ty: ignore[invalid-argument-type]


def test_an_image_handle_goes_stale_when_its_object_changes() -> None:
    doc = pylopdf.open(_inventory_pdf())
    image = doc.images[7]
    doc.objects[7] = 1
    with pytest.raises(pylopdf.StaleObjectError, match="no longer an image"):
        _ = image.width
    renumbered = pylopdf.open(doc.to_bytes(renumber=True))
    first = renumbered.images[next(iter(renumbered.images))]
    renumbered.to_bytes(renumber=True)
    with pytest.raises(pylopdf.StaleObjectError, match="renumbered"):
        _ = first.width


def test_content_images_describe_each_placement() -> None:
    images = pylopdf.open(_inventory_pdf()).pages[0].content().images

    assert [image.xref for image in images] == [5, 5, 9]
    first = images[0]
    assert first.bbox == pytest.approx(pylopdf.Rect(0, 90, 20, 100))
    assert pylopdf.Rect(0, 0, 1, 1) * first.matrix == pytest.approx(first.bbox)
    assert (first.width, first.height, first.components, first.colorspace, first.bits) == (
        2,
        1,
        1,
        "DeviceGray",
        8,
    )
    assert first.mask is not None
    assert first.encoded_size > 0
    assert first.resolution == (96, 96)
    assert images[2].colorspace == "Indexed"


def test_extract_returns_the_image_without_its_mask() -> None:
    doc = pylopdf.open(_inventory_pdf())

    image = doc.images[5].extract()

    assert isinstance(image, pylopdf.EncodedImage)
    assert image.format == "png"
    assert image.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert image.resolution == (96, 96)
    assert doc.images[9].extract().format == "png"


def test_extract_passes_jpeg_through() -> None:
    doc = pylopdf.open(ASSETS / "wdl6812-manuscript.pdf")
    # The first image is Flate-wrapped JPEG data, which passes through as JPEG.
    image = doc.pages[0].resources.images[0]

    encoded = image.extract()

    assert image.filters == ("FlateDecode", "DCTDecode")
    assert encoded.format == "jpeg"
    assert encoded.data.startswith(b"\xff\xd8\xff")


def test_to_pixmap_renders_the_pixels_with_or_without_the_mask() -> None:
    doc = pylopdf.open(_inventory_pdf())
    image = doc.images[5]

    opaque = image.to_pixmap()
    masked = image.to_pixmap(alpha=True)

    assert (opaque.width, opaque.height, opaque.channels, opaque.alpha) == (2, 1, 3, False)
    assert bytes(opaque) == bytes([10, 10, 10, 20, 20, 20])
    assert (masked.width, masked.height, masked.channels, masked.alpha) == (2, 1, 4, True)
    assert bytes(masked)[3] == 255
    assert 120 <= bytes(masked)[7] <= 136
    assert bytes(doc.images[9].to_pixmap()) == bytes([255, 0, 0])
    with pytest.raises(TypeError):
        image.to_pixmap(alpha=1)  # ty: ignore[invalid-argument-type]


def test_extract_and_to_pixmap_guard_deep_color_space_chains() -> None:
    depth = 40
    objects: dict[int, bytes | str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] /Resources << /XObject << /Im0 4 0 R >> >> >>",
        4: _image(1, 1, bytes([0]), color_space="5 0 R"),
    }
    for index in range(depth):
        base = f"{6 + index} 0 R" if index + 1 < depth else "/DeviceRGB"
        objects[5 + index] = f"[/Indexed {base} 0 <000000>]"
    doc = pylopdf.open(build_raw_pdf(objects))

    for operation in (doc.images[4].extract, doc.images[4].to_pixmap):
        with pytest.raises(pylopdf.LimitError) as caught:
            operation()
        assert caught.value.code == "resource_nesting_depth"
