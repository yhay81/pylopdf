"""Image objects: ``Document.images``, ``Page.resources``, and their handles."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from pylopdf._font_objects import FontObject, FormObject
from pylopdf._layers import Layer, LayerMembership, _layer_from_core, _layer_to_core
from pylopdf._limits import (
    _DEFAULT_MAX_IMAGE_INPUT_SIZE,
    _DEFAULT_MAX_IMAGE_PIXELS,
    _validate_optional_positive_int,
)
from pylopdf._objects import _object_number
from pylopdf.pylopdf_core import LimitError, Pixmap, StaleObjectError

if TYPE_CHECKING:
    from pylopdf._document import Document
    from pylopdf._objects import Stream
    from pylopdf._page import Page

#: The core's dictionary facts of one image: width, height, bits per
#: component, color space, alternate color space, filters, soft-mask xref,
#: mask xref, and whether the image is a stencil mask.
_Facts = tuple[int, int, int, str, str, list[str], int, int, bool]


@dataclass(frozen=True, slots=True, kw_only=True)
class EncodedImage:
    """The data of one image, as :meth:`ImageObject.extract` returns it."""

    format: Literal["jpeg", "jpx", "png"]
    """``"jpeg"`` or ``"jpx"`` when the image's DCT or JPEG 2000 data passes
    through unchanged, else ``"png"``."""
    data: bytes
    """The encoded image."""
    resolution: tuple[int, int]
    """The horizontal and vertical resolution in dots per inch: a JPEG's own
    density or a JP2 file's resolution box, else 96."""


class ImageObject:
    """A handle on one image XObject of a document, as ``doc.images[xref]`` returns it.

    Properties read the document on every access. A handle raises
    :class:`StaleObjectError` once its object is no longer an image or the
    document's objects were renumbered.
    """

    __slots__ = ("_document", "_epoch", "_xref")

    def __init__(self, document: Document, xref: int) -> None:
        """Create a handle; :class:`Images` and :class:`Resources` do this."""
        self._document = document
        self._epoch = document._object_epoch
        self._xref = xref

    def _facts(self) -> _Facts:
        """Return the core's current facts of this image."""
        document = self._document
        document._ensure_open()
        if self._epoch != document._object_epoch:
            msg = f"image {self._xref} was invalidated when the document's objects were renumbered or reloaded"
            raise StaleObjectError(msg)
        facts = document._doc.image_facts(self._xref)
        if facts is None:
            msg = f"object {self._xref} is no longer an image"
            raise StaleObjectError(msg)
        return facts

    def _related(self, xref: int) -> ImageObject | None:
        return None if xref == 0 else ImageObject(self._document, xref)

    @property
    def xref(self) -> int:
        """Return the image's object number."""
        return self._xref

    @property
    def obj(self) -> Stream:
        """Return the image's stream in the low-level object model."""
        self._facts()
        return cast("Stream", self._document.objects[self._xref])

    @property
    def layer(self) -> Layer | LayerMembership | None:
        """Return the optional-content group or membership that shows or hides the image (``/OC``), or ``None``.

        Assigning a :class:`Layer` or a :class:`LayerMembership` sets it, and
        ``del`` removes it; rendering and extraction then show the image only
        while its group is visible, wherever it is placed.
        """
        self._facts()
        return _layer_from_core(self._document, self._document._doc.xobject_layer(self._xref))

    @layer.setter
    def layer(self, layer: Layer | LayerMembership | None) -> None:
        self._facts()
        value = _layer_to_core(self._document, layer)
        self._document._doc.set_xobject_layer(self._xref, value)

    @layer.deleter
    def layer(self) -> None:
        self.layer = None

    @property
    def width(self) -> int:
        """Return the width in pixels (``/Width``)."""
        return self._facts()[0]

    @property
    def height(self) -> int:
        """Return the height in pixels (``/Height``)."""
        return self._facts()[1]

    @property
    def bits(self) -> int:
        """Return the bits per component (``/BitsPerComponent``), or 0 when the dictionary omits it."""
        return self._facts()[2]

    @property
    def colorspace(self) -> str | None:
        """Return the color space's name or family, such as ``"DeviceRGB"`` or ``"ICCBased"``.

        ``None`` when the dictionary names none, as for a stencil mask or JPX
        data that carries its own.
        """
        return self._facts()[3] or None

    @property
    def alternate_colorspace(self) -> str | None:
        """Return the base or alternate color space of an Indexed, ICCBased, Separation, or DeviceN space."""
        return self._facts()[4] or None

    @property
    def filters(self) -> tuple[str, ...]:
        """Return the names of the stream's filters in decoding order, such as ``("DCTDecode",)``."""
        return tuple(self._facts()[5])

    @property
    def soft_mask(self) -> ImageObject | None:
        """Return the image's soft mask (``/SMask``), or ``None``."""
        return self._related(self._facts()[6])

    @property
    def mask(self) -> ImageObject | None:
        """Return the stencil mask image of ``/Mask``, or ``None``; a color-key array is not an image."""
        return self._related(self._facts()[7])

    @property
    def is_stencil(self) -> bool:
        """Return whether the image is a stencil mask (``/ImageMask true``)."""
        return self._facts()[8]

    def extract(self) -> EncodedImage:
        """Return the image's data without its masks.

        DCT data passes through unchanged as JPEG and data whose only filter
        is JPXDecode as JPEG 2000 (``"jpx"``, a JP2 file or a raw
        codestream); other data, including JBIG2, CCITT, and Flate, is
        decoded and returned as PNG. Decoding runs
        under the same content and colour-space guards and resource limits as
        rendering.
        """
        self._facts()
        image_format, resolution, data = self._document._doc.extract_image(self._xref)
        self._document._emit_warnings()
        return EncodedImage(
            format=cast("Literal['jpeg', 'jpx', 'png']", image_format),
            data=data,
            resolution=resolution,
        )

    def to_pixmap(self, *, alpha: bool = False) -> Pixmap:
        """Render the image at its pixel size.

        The result is opaque RGB of the image without its masks, or with
        ``alpha=True`` straight-alpha RGBA in which the soft mask or mask
        sets the transparency. Rendering runs under the same guards and
        limits as page rendering.
        """
        if not isinstance(alpha, bool):
            msg = f"alpha must be a bool: {alpha!r}"
            raise TypeError(msg)
        self._facts()
        pixmap = self._document._doc.image_pixmap(self._xref, alpha)
        self._document._emit_warnings()
        return pixmap

    def replace(
        self,
        source: str | os.PathLike[str] | bytes | bytearray | memoryview | Pixmap,
        /,
        *,
        max_size: int | None = _DEFAULT_MAX_IMAGE_INPUT_SIZE,
        max_pixels: int | None = _DEFAULT_MAX_IMAGE_PIXELS,
    ) -> None:
        """Replace the image with a JPEG, PNG, or JPEG 2000 file, encoded bytes, or a :class:`Pixmap`.

        The new image keeps this object number, so every placement of the
        image, on any page, in Form XObjects, and in annotation appearances,
        draws it, stretched into the placement as the old image was; the
        placements keep their matrices, so the new data's EXIF orientation
        does not apply. Its
        layer (``/OC``) and its place in the structure tree
        (``/StructParent``) stay; its masks and the rest of its dictionary
        are those of the new image, and the old masks go when the document
        is saved unless something else uses them. JPEG and JPEG 2000 data
        is stored as it is, JPEG 2000 alpha included, and PNG data and
        Pixmaps with Flate and a soft mask for their alpha. Input is limited as :meth:`Canvas.image` limits it, refused
        with :class:`LimitError` code ``image_input_size`` or
        ``image_pixel_count``, and nothing changes on any refusal.
        """
        _validate_optional_positive_int("max_size", max_size)
        _validate_optional_positive_int("max_pixels", max_pixels)
        self._facts()
        core = self._document._doc
        if isinstance(source, Pixmap):
            core.replace_image(self._xref, pixmap=source)
        elif isinstance(source, (bytes, bytearray, memoryview)):
            # Refuse oversized input before copying it for the core, which checks again.
            size = source.nbytes if isinstance(source, memoryview) else len(source)
            if max_size is not None and size > max_size:
                msg = f"encoded image input is {size} bytes, exceeding the {max_size}-byte limit"
                code = "image_input_size"
                raise LimitError(code, msg)
            core.replace_image(self._xref, data=bytes(source), max_size=max_size, max_pixels=max_pixels)
        elif isinstance(source, (str, os.PathLike)):
            core.replace_image(self._xref, path=os.fspath(source), max_size=max_size, max_pixels=max_pixels)
        else:
            msg = f"source must be a path, bytes, or a Pixmap, not {type(source).__name__}"
            raise TypeError(msg)

    def __eq__(self, other: object) -> bool:
        """Compare the document, the object numbering, and the object number."""
        if not isinstance(other, ImageObject):
            return NotImplemented
        return self._document is other._document and self._epoch == other._epoch and self._xref == other._xref

    def __hash__(self) -> int:
        """Hash the document, the object numbering, and the object number."""
        return hash((id(self._document), self._epoch, self._xref))

    def __repr__(self) -> str:
        """Return ``<ImageObject 12>``."""
        return f"<ImageObject {self._xref}>"


class Images(Mapping[int, ImageObject]):
    """The image XObjects of a document, keyed by object number, as ``doc.images`` returns them."""

    __slots__ = ("_document",)

    def __init__(self, document: Document) -> None:
        """Wrap a document; use ``Document.images``."""
        self._document = document

    def __getitem__(self, xref: int) -> ImageObject:
        """Return the image stored under ``xref``; another object raises :class:`KeyError`."""
        number = _object_number(xref)
        self._document._ensure_open()
        if self._document._doc.image_facts(number) is None:
            raise KeyError(xref)
        return ImageObject(self._document, number)

    def __iter__(self) -> Iterator[int]:
        """Iterate over the object numbers of the images in ascending order."""
        self._document._ensure_open()
        return iter(self._document._doc.image_xrefs())

    def __len__(self) -> int:
        """Return the number of image XObjects."""
        self._document._ensure_open()
        return len(self._document._doc.image_xrefs())

    def __contains__(self, xref: object) -> bool:
        """Return whether ``xref`` is an image XObject."""
        if not isinstance(xref, int) or isinstance(xref, bool) or xref < 0:
            return False
        self._document._ensure_open()
        return self._document._doc.image_facts(xref) is not None


class Resources:
    """What a page's resources reach, as ``page.resources`` returns it."""

    __slots__ = ("_page",)

    def __init__(self, page: Page) -> None:
        """Wrap a page; use ``Page.resources``."""
        self._page = page

    @property
    def images(self) -> tuple[ImageObject, ...]:
        """Return the image XObjects that the page's resources list, once each.

        The page's inherited resources and, depth first, those of the Form
        XObjects they list are read in resource order. Inline images and
        images that only annotations draw are not listed, and an image need
        not be drawn to be listed; ``page.content(text_runs=False,
        paths=False).images`` lists drawn placements. A page that lists more
        than 4,096 images or 4,096 Form XObjects raises :class:`LimitError`
        with code ``image_inventory_count``.
        """
        page = self._page
        document = page.document
        xrefs = document._doc.page_image_inventory(page._page_number())
        return tuple(ImageObject(document, xref) for xref in xrefs)

    @property
    def layers(self) -> dict[str, Layer | LayerMembership]:
        """Return the groups and memberships that the page's ``/Properties`` names, by resource name.

        Marked content names them as ``/OC /Name BDC``; the mapping is a
        snapshot. Property lists that are no optional content are left out.
        """
        page = self._page
        document = page.document
        return {
            name: value
            for name, entry in document._doc.page_layers(page._page_number())
            if (value := _layer_from_core(document, entry)) is not None
        }

    @property
    def fonts(self) -> tuple[FontObject, ...]:
        """Return the font dictionaries that the page's resources list, once each.

        The page's inherited resources and, depth first, those of the Form
        XObjects they list are read in resource order, as for
        :attr:`images`. Fonts stored directly in a resource dictionary rather
        than as objects of their own, and fonts that only annotation
        appearances or Type 3 glyph procedures use, are not listed. A page
        that lists more than 4,096 fonts or 4,096 Form XObjects raises
        :class:`LimitError` with code ``font_inventory_count``.
        """
        page = self._page
        document = page.document
        xrefs = document._doc.page_font_inventory(page._page_number())
        return tuple(FontObject(document, xref) for xref in xrefs)

    @property
    def forms(self) -> tuple[FormObject, ...]:
        """Return the Form XObjects that the page's resources reach, once each, depth first.

        A page that nests more than 4,096 Form XObjects raises
        :class:`LimitError` with code ``form_inventory_count``.
        """
        page = self._page
        document = page.document
        xrefs = document._doc.page_form_inventory(page._page_number())
        return tuple(FormObject(document, xref) for xref in xrefs)


# The package re-exports these names; reprs, pickles, and documentation name it.
for _public in (EncodedImage, ImageObject, Images, Resources):
    _public.__module__ = "pylopdf"
