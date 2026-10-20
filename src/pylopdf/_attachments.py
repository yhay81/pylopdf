"""Document attachments: the ``Document.attachments`` mapping and its handles."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING

from pylopdf._limits import (
    _validate_cumulative_utf8_input,
    _validate_optional_positive_int,
    _validate_utf8_text_input,
)
from pylopdf.pylopdf_core import LimitError, StaleObjectError

if TYPE_CHECKING:
    from pylopdf._document import Document

_DEFAULT_MAX_EMBEDDED_FILE_SIZE = 64 * 1024 * 1024
_MAX_EMBEDDED_FILE_INPUT_TEXT_SIZE = 1024 * 1024


def _validate_name(name: object) -> str:
    """Check a lookup name's type and its 1 MiB bound before tree traversal."""
    if not isinstance(name, str):
        msg = f"name must be a string: {type(name).__name__}"
        raise TypeError(msg)
    _validate_utf8_text_input(
        name,
        _MAX_EMBEDDED_FILE_INPUT_TEXT_SIZE,
        limit_code="embedded_file_input_size",
        input_label="attachment lookup name",
    )
    return name


def _validate_data(data: bytes, max_size: int | None) -> bytes:
    """Check attachment data against ``max_size`` before copying it."""
    _validate_optional_positive_int("max_size", max_size)
    if max_size is not None and len(data) > max_size:
        limit_code = "embedded_file_size"
        msg = f"attachment input is {len(data)} bytes, exceeding the {max_size}-byte limit"
        raise LimitError(limit_code, msg)
    return bytes(data)


class Attachment:
    """A handle on one embedded file of a document, as ``doc.attachments[name]`` returns it.

    Properties read the document on every access. A handle raises
    :class:`StaleObjectError` once its attachment has been deleted.
    """

    __slots__ = ("_document", "_name")

    def __init__(self, document: Document, name: str) -> None:
        """Create a handle; :class:`Attachments` does this."""
        self._document = document
        self._name = name

    def _info(self) -> tuple[str, str, int, str, str]:
        """Return the core's current metadata of this attachment."""
        self._document._ensure_open()
        info = self._document._doc.embfile_info(self._name)
        if info is None:
            msg = f"the attachment {self._name!r} no longer exists"
            raise StaleObjectError(msg)
        return info

    @property
    def name(self) -> str:
        """Return the key under which the document lists the attachment."""
        return self._name

    @property
    def filename(self) -> str:
        """Return the viewer-facing file name (``/UF``, else ``/F``), or ``""``."""
        return self._info()[0]

    @property
    def description(self) -> str:
        """Return the description (``/Desc``), or ``""``."""
        return self._info()[1]

    @property
    def size(self) -> int:
        """Return the uncompressed size that the file declares (``/Params /Size``), or -1, as pymupdf does."""
        return self._info()[2]

    @property
    def creation_date(self) -> str:
        """Return the raw PDF creation date string (``/Params /CreationDate``), or ``""``."""
        return self._info()[3]

    @property
    def mod_date(self) -> str:
        """Return the raw PDF modification date string (``/Params /ModDate``), or ``""``."""
        return self._info()[4]

    def read(self, *, max_size: int | None = _DEFAULT_MAX_EMBEDDED_FILE_SIZE) -> bytes:
        """Return the attachment's contents, as pymupdf's ``embfile_get`` does.

        ``max_size`` bounds every decoding layer and defaults to 64 MiB. Pass a
        larger positive integer for a known large attachment, or ``None`` only
        when intentionally accepting unbounded materialization. Oversized
        content raises :class:`LimitError` with code ``embedded_file_size``.
        """
        self._info()
        _validate_optional_positive_int("max_size", max_size)
        return self._document._doc.embfile_get(self._name, max_size)

    def write(self, data: bytes, /, *, max_size: int | None = _DEFAULT_MAX_EMBEDDED_FILE_SIZE) -> None:
        """Replace the attachment's contents, as pymupdf's ``embfile_upd`` does with ``buffer_``.

        The name, file name, description, and dates stay; ``size`` becomes the
        new length and a stale ``/CheckSum`` is dropped. ``max_size`` bounds
        ``data`` and defaults to 64 MiB, checked before the data is copied.
        """
        self._info()
        payload = _validate_data(data, max_size)
        self._document._doc.embfile_upd(self._name, payload, max_size)

    def __eq__(self, other: object) -> bool:
        """Compare handles by document and name."""
        if not isinstance(other, Attachment):
            return NotImplemented
        return self._document is other._document and self._name == other._name

    def __hash__(self) -> int:
        """Hash consistently with equality."""
        return hash((id(self._document), self._name))

    def __repr__(self) -> str:
        """Return ``Attachment('name')``."""
        return f"Attachment({self._name!r})"


class Attachments(Mapping[str, Attachment]):
    """The embedded files of a document, keyed by name, as ``doc.attachments`` returns them.

    This live view replaces pymupdf's ``embfile_*`` methods: iterate it for
    the sorted names, index it for an :class:`Attachment`, ``add`` a file, and
    ``del`` one. The EmbeddedFiles name tree is read under its documented
    limits on every access.
    """

    __slots__ = ("_document",)

    def __init__(self, document: Document) -> None:
        """Create the view; :attr:`Document.attachments` does this."""
        self._document = document

    def _names(self) -> list[str]:
        self._document._ensure_open()
        return self._document._doc.embfile_names()

    def __getitem__(self, name: str) -> Attachment:
        """Return the handle of the attachment called ``name``; raise :class:`KeyError` when absent."""
        _validate_name(name)
        self._document._ensure_open()
        if self._document._doc.embfile_info(name) is None:
            raise KeyError(name)
        return Attachment(self._document, name)

    def __iter__(self) -> Iterator[str]:
        """Iterate the attachment names in sorted order."""
        return iter(self._names())

    def __len__(self) -> int:
        """Return the number of attachments, as pymupdf's ``embfile_count`` does."""
        return len(self._names())

    def __contains__(self, name: object) -> bool:
        """Return whether an attachment is called ``name``."""
        if not isinstance(name, str):
            return False
        _validate_name(name)
        self._document._ensure_open()
        return self._document._doc.embfile_info(name) is not None

    def __delitem__(self, name: str) -> None:
        """Delete the attachment called ``name``; raise :class:`KeyError` when absent."""
        if name not in self:
            raise KeyError(name)
        self._document._doc.embfile_del(name)

    def add(
        self,
        name: str,
        data: bytes,
        /,
        *,
        filename: str | None = None,
        description: str | None = None,
        max_size: int | None = _DEFAULT_MAX_EMBEDDED_FILE_SIZE,
    ) -> Attachment:
        """Add an attachment and return its handle, as pymupdf's ``embfile_add`` does.

        ``name`` is the unique key; adding an existing name raises
        :class:`PdfError`. ``filename`` is the viewer-facing file name and
        defaults to ``name``; ``description`` is a description. Both support
        Unicode through UTF-16BE ``UF``/``Desc``. ``max_size`` bounds the data
        and defaults to 64 MiB, matching :meth:`Attachment.read`; ``None``
        explicitly accepts unbounded input. The aggregate text input is capped
        at 1 MiB before data copying; refusals use
        ``embedded_file_input_size``. Existing inline FileSpecs are cloned only
        after bounded shape validation. This can build invoice-plus-XML
        structures such as ZUGFeRD/Factur-X.
        """
        self._document._ensure_open()
        if not isinstance(name, str):
            msg = f"name must be a string: {type(name).__name__}"
            raise TypeError(msg)
        if not name:
            msg = "name must be at least 1 character"
            raise ValueError(msg)
        if filename is not None and not isinstance(filename, str):
            msg = f"filename must be a string or None: {type(filename).__name__}"
            raise TypeError(msg)
        if description is not None and not isinstance(description, str):
            msg = f"description must be a string or None: {type(description).__name__}"
            raise TypeError(msg)
        input_text = [name, name if filename is None else filename]
        if description is not None:
            input_text.append(description)
        _validate_cumulative_utf8_input(
            input_text,
            _MAX_EMBEDDED_FILE_INPUT_TEXT_SIZE,
            limit_code="embedded_file_input_size",
            input_label="attachment name, filename, and description",
        )
        payload = _validate_data(data, max_size)
        self._document._doc.embfile_add(name, payload, filename, description, max_size)
        return Attachment(self._document, name)

    def __repr__(self) -> str:
        """Return the names, such as ``Attachments(['a.xml', 'b.txt'])``."""
        return f"Attachments({self._names()!r})"


# The package re-exports these names; reprs, pickles, and documentation name them.
for _public in (Attachment, Attachments):
    _public.__module__ = "pylopdf"
