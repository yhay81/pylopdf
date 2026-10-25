"""Image files: what a JPEG, PNG, or JPEG 2000 file states about itself."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, cast

from pylopdf._limits import _DEFAULT_MAX_IMAGE_INPUT_SIZE, _validate_optional_positive_int
from pylopdf.pylopdf_core import LimitError
from pylopdf.pylopdf_core import image_info as _image_info


@dataclass(frozen=True, slots=True)
class ImageInfo:
    """What an image file's header states, as :func:`image_info` returns it."""

    format: Literal["jpeg", "png", "jpx"]
    """``"jpeg"``, ``"png"``, or ``"jpx"`` for JPEG 2000, a JP2 file or a raw codestream."""
    width: int
    """The width in pixels as stored, before the orientation applies."""
    height: int
    """The height in pixels as stored, before the orientation applies."""
    components: int
    """Color components per pixel without alpha: 1 gray, 3 RGB, 4 CMYK; a palette's entries are RGB."""
    alpha: bool
    """Whether the image has an alpha channel or, for PNG, transparency."""
    bits: int
    """Bits per component as stored, 8 for a PNG palette's entries."""
    resolution: tuple[int, int]
    """Dots per inch across and down, as :meth:`Pixmap.from_bytes` reads them: 96 when the file states none."""
    orientation: int
    """The EXIF orientation, 1 through 8, that :meth:`Canvas.image` applies; 1 when the file states none.

    Orientations 5 through 8 turn the image a quarter, so its upright width
    is ``height``.
    """


def image_info(
    source: str | os.PathLike[str] | bytes | bytearray | memoryview,
    /,
    *,
    max_size: int | None = _DEFAULT_MAX_IMAGE_INPUT_SIZE,
) -> ImageInfo:
    """Read the header of a JPEG, PNG, or JPEG 2000 image, a path or encoded bytes, without decoding it.

    Input is limited to 64 MiB by default (``None`` for trusted input), as
    :meth:`Canvas.image` limits it, refused with :class:`LimitError` code
    ``image_input_size``; data in no supported format, or whose header does
    not read, raises :class:`PdfError`.
    """
    _validate_optional_positive_int("max_size", max_size)
    if isinstance(source, (bytes, bytearray, memoryview)):
        # Refuse oversized input before copying it for the core.
        size = source.nbytes if isinstance(source, memoryview) else len(source)
        if max_size is not None and size > max_size:
            msg = f"encoded image input is {size} bytes, exceeding the {max_size}-byte limit"
            code = "image_input_size"
            raise LimitError(code, msg)
        facts = _image_info(data=bytes(source))
    elif isinstance(source, (str, os.PathLike)):
        facts = _image_info(path=os.fspath(source), max_size=max_size)
    else:
        msg = f"source must be a path or bytes, not {type(source).__name__}"
        raise TypeError(msg)
    image_format, width, height, components, alpha, bits, resolution, orientation = facts
    return ImageInfo(
        format=cast("Literal['jpeg', 'png', 'jpx']", image_format),
        width=width,
        height=height,
        components=components,
        alpha=alpha,
        bits=bits,
        resolution=resolution,
        orientation=orientation,
    )


# The package re-exports these names; reprs, pickles, and documentation name them.
ImageInfo.__module__ = "pylopdf"
image_info.__module__ = "pylopdf"
