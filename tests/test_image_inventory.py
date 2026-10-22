"""pymupdf's image inventory: get_images, get_image_info, and extract_image."""

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


def test_get_images_lists_image_xobjects_as_pymupdf_does() -> None:
    page = pylopdf.open(_inventory_pdf()).pages[0]

    images = page.get_images()

    assert images == [
        (5, 6, 2, 1, 8, "DeviceGray", "", "Im0", "FlateDecode"),
        (7, 0, 1, 1, 8, "ICCBased", "DeviceRGB", "Im1", "FlateDecode"),
        (9, 0, 1, 1, 8, "Indexed", "DeviceRGB", "Im2", "FlateDecode"),
    ]
    assert isinstance(images[0], pylopdf.ImageItem)
    assert images[0].xref == 5
    assert images[2].colorspace == "Indexed"


def test_get_images_full_adds_the_referencer() -> None:
    doc = pylopdf.open(_inventory_pdf())

    images = doc.pages[0].get_images(full=True)

    assert [(image.xref, image.referencer) for image in images] == [(5, 0), (7, 0), (9, 8)]
    assert isinstance(images[0], pylopdf.ImageItemFull)
    assert doc.pages[0].get_images(full=True) == images
    assert doc.pages[0].get_images() == doc.pages[0].get_images()


def test_get_images_is_empty_without_image_xobjects() -> None:
    assert pylopdf.open(build_pdf(["Text only"])).pages[0].get_images() == []


def test_get_image_info_describes_each_placement() -> None:
    page = pylopdf.open(_inventory_pdf()).pages[0]

    infos = page.get_image_info(xrefs=True)

    assert [info["number"] for info in infos] == [0, 1, 2]
    assert [info["xref"] for info in infos] == [5, 5, 9]
    first = infos[0]
    assert set(first) == {
        "number",
        "bbox",
        "transform",
        "width",
        "height",
        "colorspace",
        "cs-name",
        "xres",
        "yres",
        "bpc",
        "size",
        "has-mask",
        "xref",
    }
    assert first["bbox"] == pytest.approx(pylopdf.Rect(0, 90, 20, 100))
    assert pylopdf.Rect(0, 0, 1, 1) * first["transform"] == pytest.approx(first["bbox"])
    assert (first["width"], first["height"], first["colorspace"], first["cs-name"], first["bpc"]) == (
        2,
        1,
        1,
        "DeviceGray",
        8,
    )
    assert first["has-mask"] is True
    assert first["size"] > 0
    assert (first["xres"], first["yres"]) == (96, 96)
    assert infos[2]["cs-name"] == "Indexed"
    assert "xref" not in page.get_image_info()[0]


def test_get_image_info_hashes_raise_until_supported() -> None:
    with pytest.raises(pylopdf.PdfError, match="hashes"):
        pylopdf.open(_inventory_pdf()).pages[0].get_image_info(hashes=True)


def test_extract_image_returns_the_image_without_its_mask() -> None:
    doc = pylopdf.open(_inventory_pdf())

    image = doc.extract_image(5)

    assert image["ext"] == "png"
    assert image["image"].startswith(b"\x89PNG\r\n\x1a\n")
    assert (image["width"], image["height"], image["colorspace"], image["bpc"]) == (2, 1, 1, 8)
    assert (image["smask"], image["cs-name"]) == (6, "DeviceGray")
    assert (image["xres"], image["yres"]) == (96, 96)
    assert doc.extract_image(6)["smask"] == 0
    indexed = doc.extract_image(9)
    assert (indexed["colorspace"], indexed["cs-name"]) == (3, "Indexed")


def test_extract_image_passes_jpeg_through() -> None:
    doc = pylopdf.open(ASSETS / "wdl6812-manuscript.pdf")
    # The first image is Flate-wrapped JPEG data, which passes through as JPEG.
    xref = doc.pages[0].get_images()[0].xref

    image = doc.extract_image(xref)

    assert image["ext"] == "jpeg"
    assert image["image"].startswith(b"\xff\xd8\xff")
    assert image["colorspace"] in {1, 3}


@pytest.mark.parametrize("xref", [1, 4, 8, 999])
def test_extract_image_refuses_other_objects(xref: int) -> None:
    with pytest.raises(pylopdf.PdfError, match=f"xref {xref} is not"):
        pylopdf.open(_inventory_pdf()).extract_image(xref)


@pytest.mark.parametrize("xref", [0, -1, True, 1.5])
def test_extract_image_validates_the_xref(xref: object) -> None:
    with pytest.raises(ValueError, match="xref must be a positive integer"):
        pylopdf.open(_inventory_pdf()).extract_image(xref)  # ty: ignore[invalid-argument-type]


def test_extract_image_guards_deep_color_space_chains() -> None:
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

    with pytest.raises(pylopdf.LimitError) as caught:
        doc.extract_image(4)
    assert caught.value.code == "resource_nesting_depth"
