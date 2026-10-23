"""Documents, opening and metadata probing, and atomic saving."""

from __future__ import annotations

import functools
import math
import os
import re
import secrets
import stat
import sys
import warnings as _warnings
from collections import Counter
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, TypeGuard, cast

from pylopdf import _fonts, _markdown, _text
from pylopdf._attachments import Attachments
from pylopdf._errors import DocumentClosedError, EncryptedDocumentError, PylopdfWarning
from pylopdf._fallback_fonts import (
    FallbackFont,
    FallbackFonts,
    _apply_fallback_font,
    _replace_fallback_fonts,
    _reset_fallback_fonts,
)
from pylopdf._fonts import (
    _font_input_limit_error,
)
from pylopdf._form import Form
from pylopdf._hints import hinted_getattr, hinted_protocol
from pylopdf._images import Images
from pylopdf._limits import (
    _DEFAULT_MAX_FONT_INPUT_SIZE,
    _DEFAULT_MAX_MARKDOWN_SIZE,
    DocumentLimits,
    _document_limit_args,
    _validate_cumulative_pdf_text_input,
    _validate_cumulative_utf8_input,
    _validate_optional_positive_int,
    _validate_utf8_text_input,
)
from pylopdf._metadata import METADATA_KEYS, MarkInfo, Metadata, PageLayout, PageMode, PdfaClaim, _metadata_value
from pylopdf._objects import Dictionary, Name, Objects, String
from pylopdf._outline import Outline, OutlineEntry, _write_outline
from pylopdf._page import Page
from pylopdf._page_labels import PageLabelRange, _read_page_labels, _write_page_labels
from pylopdf._pages import Pages
from pylopdf._pixmap import (
    Colorspace,
    _render_background,
    _render_gray,
    _render_scale,
    _validate_bool,
    _validate_encoding,
)
from pylopdf._tables import (
    _bounded_markdown_page_output,
    _markdown_output_limit_error,
    _markdown_page_tables,
    _markdown_table_output,
    _MarkdownBudget,
)
from pylopdf._types import Access, Permissions
from pylopdf.pylopdf_core import LimitError, PasswordError, PdfError, _Document

if sys.platform == "win32":
    from pylopdf.pylopdf_core import _copy_windows_dacl

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from types import TracebackType
    from typing import Any, Self

    from pylopdf._fonts import Font
    from pylopdf._geometry import Matrix
    from pylopdf._types import (
        CjkFontLanguage,
        DocumentComplexity,
        ImageCompressionResult,
    )


_DEFAULT_MAX_XMP_METADATA_SIZE = 1024 * 1024
_DEFAULT_MAX_RENDER_BATCH_SIZE = 512 * 1024 * 1024
_DEFAULT_MAX_PDF_OUTPUT_SIZE = 512 * 1024 * 1024
_MAX_METADATA_INPUT_TEXT_SIZE = 1024 * 1024
_MAX_PASSWORD_INPUT_BYTES = 127
# Explicit page iterables may repeat pages up to the larger of the page count
# and this floor, so repetition never costs more than one full document pass.
_MIN_PAGE_ENTRY_LIMIT = 4096
_TEMPORARY_FILE_ATTEMPTS = 100

_IMAGE_COMPRESSION_MIN_DPI = 36.0
_IMAGE_COMPRESSION_MAX_DPI = 2400.0
_IMAGE_COMPRESSION_MAX_QUALITY = 100
_MAX_RENDER_WORKERS = 64


def _resolve_page_index(pno: int, count: int) -> int:
    """Resolve negative indexing against one page-count snapshot."""
    normalized = pno + count if pno < 0 else pno
    if not 0 <= normalized < count:
        msg = f"page number {pno} is out of range (0..{count - 1})"
        raise IndexError(msg)
    return normalized


def _temporary_sibling_path(target: Path) -> Path:
    """Create one same-directory output path with normal umask permissions."""
    for _ in range(_TEMPORARY_FILE_ATTEMPTS):
        candidate = target.parent / f".pylopdf-{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        except FileExistsError:
            continue
        os.close(descriptor)
        return candidate
    msg = f"failed to create a unique temporary output beside {target}"
    raise FileExistsError(msg)


def _save_target_path(requested: Path) -> Path:
    """Support Windows long paths before checking or resolving a final symlink."""
    target = requested
    if sys.platform == "win32":
        # Normalize before adding the verbatim prefix: it does not interpret
        # relative components or forward slashes. Keep caller text in errors.
        absolute = os.path.abspath(requested)  # noqa: PTH100 - lexical normalization must precede filesystem access.
        if not absolute.startswith(("\\\\?\\", "\\\\.\\")):
            absolute = "\\\\?\\UNC\\" + absolute[2:] if absolute.startswith("\\\\") else "\\\\?\\" + absolute
        target = Path(absolute)
    return target.resolve(strict=False) if target.is_symlink() else target


def _remove_temporary_output(temporary: Path) -> None:
    """Best-effort cleanup, including a Windows sibling made read-only."""
    if sys.platform == "win32":
        with suppress(OSError):
            temporary.chmod(stat.S_IWRITE)
    with suppress(OSError):
        temporary.unlink()


def _atomic_save_file(
    filename: str | os.PathLike[str],
    writer: Callable[[str], None],
) -> None:
    """Write a sibling file completely before atomically replacing the target."""
    requested = Path(filename)
    try:
        target = _save_target_path(requested)
        try:
            target_metadata = target.stat()
        except FileNotFoundError:
            target_mode = None
        else:
            target_mode = stat.S_IMODE(target_metadata.st_mode) if stat.S_ISREG(target_metadata.st_mode) else None
        temporary = _temporary_sibling_path(target)
    except (OSError, RuntimeError) as exc:
        msg = f"failed to save {requested}: {exc}"
        raise PdfError(msg) from exc
    replaced = False
    try:
        if sys.platform == "win32" and target_mode is not None:
            try:
                # Apply access restrictions before any PDF data is written.
                _copy_windows_dacl(target, temporary)
            except OSError as exc:
                msg = f"failed to save {requested}: {exc}"
                raise PdfError(msg) from exc
        try:
            writer(str(temporary))
        except PdfError as exc:
            message = str(exc).replace(str(temporary), str(requested))
            raise PdfError(message) from exc
        try:
            if target_mode is not None:
                temporary.chmod(target_mode)
            temporary.replace(target)
        except OSError as exc:
            msg = f"failed to save {requested}: {exc}"
            raise PdfError(msg) from exc
        replaced = True
    finally:
        if not replaced:
            _remove_temporary_output(temporary)


def _validate_password_input(password: str | None, label: str = "password") -> None:
    """Bound password KDF input before crossing the Python/Rust boundary."""
    if password is not None:
        _validate_utf8_text_input(
            password,
            _MAX_PASSWORD_INPUT_BYTES,
            limit_code="password_input_size",
            input_label=label,
        )


_MAX_LANGUAGE_TAG_BYTES = 256
_LANGUAGE_TAG = re.compile(r"[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*", re.ASCII)


def _is_int(value: object) -> TypeGuard[int]:
    """Return whether ``value`` is an integer other than a bool."""
    return isinstance(value, int) and not isinstance(value, bool)


def _matched_access(password: str, *, user: bool, owner: bool) -> Access | None:
    """Return what a password opens, as MuPDF decides it, or ``None``.

    An empty owner password counts only when the user password is empty too.
    """
    if owner and (password or user):
        return Access.OWNER
    if user:
        return Access.USER
    return None


def _owner_password(encryption: Encryption) -> str:
    """Return the owner password of ``encryption``, which defaults to the user password."""
    return encryption.user_password if encryption.owner_password is None else encryption.owner_password


def _catalog_enum(kind: type[PageMode | PageLayout], value: object, name: str) -> str:
    """Return the catalog name of a page mode or layout, given as a member or its exact value."""
    try:
        return str(kind(value))
    except ValueError:
        msg = f"{name} must be one of {[member.value for member in kind]}: {value!r}"
        raise ValueError(msg) from None


def _write_metadata(document: Document, values: Mapping[str, str]) -> None:
    """Write validated Info values in one edit, where ``""`` removes an entry.

    The values share the 1 MiB input and encoded-text limits
    (``LimitError.code == "metadata_input_size"``), checked before any
    change.
    """
    document._ensure_open()
    present = [value for value in values.values() if value]
    _validate_cumulative_utf8_input(
        present,
        _MAX_METADATA_INPUT_TEXT_SIZE,
        limit_code="metadata_input_size",
        input_label="Info metadata input",
    )
    _validate_cumulative_pdf_text_input(
        present,
        _MAX_METADATA_INPUT_TEXT_SIZE,
        limit_code="metadata_input_size",
        input_label="encoded Info metadata input",
    )
    document._doc.set_metadata_batch([(METADATA_KEYS[key], value) for key, value in values.items()])


def _is_language_tag(tag: object) -> bool:
    """Return whether ``tag`` has the shape of a BCP 47 language tag of at most 256 bytes."""
    return isinstance(tag, str) and len(tag) <= _MAX_LANGUAGE_TAG_BYTES and _LANGUAGE_TAG.fullmatch(tag) is not None


def _rewrite_dpi(name: str, value: object) -> float:
    """Validate one ``rewrite_images`` resolution: a finite number from 36 through 2400."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        msg = f"{name} must be a finite number from 36 through 2400"
        raise PdfError(msg)
    dpi = float(value)
    if not math.isfinite(dpi) or not _IMAGE_COMPRESSION_MIN_DPI <= dpi <= _IMAGE_COMPRESSION_MAX_DPI:
        msg = f"{name} must be a finite number from 36 through 2400"
        raise PdfError(msg)
    return dpi


class Document:
    """A PDF document.

    :func:`open` opens an existing document, and ``Document()`` creates an
    empty one. :attr:`pages` holds the pages. Documents are context managers
    that close on exit.
    """

    if not TYPE_CHECKING:
        __getattr__ = hinted_getattr
        __getitem__ = hinted_protocol("'Document' object is not subscriptable; use doc.pages[index]")
        __iter__ = hinted_protocol("'Document' object is not iterable; iterate over doc.pages")

    def __init__(self, *, limits: DocumentLimits | None = None) -> None:
        """Create an empty document; :func:`open` opens an existing one.

        ``limits`` sets the interpretation and text budgets for the pages that
        the document receives later.
        """
        self._setup(None, None, None, limits)

    def _setup(
        self,
        path: str | None,
        data: bytes | None,
        password: str | None,
        limits: DocumentLimits | None,
    ) -> None:
        """Load ``path`` or ``data``, or start an empty document, and set up the state."""
        _validate_password_input(password)
        if limits is not None and not isinstance(limits, DocumentLimits):
            msg = f"limits must be a DocumentLimits instance or None: {limits!r}"
            raise TypeError(msg)
        resolved_limits = limits or DocumentLimits()
        limit_args = _document_limit_args(resolved_limits)
        self._limits = resolved_limits
        if data is not None:
            doc = _Document.load_bytes(data, None, *limit_args)
            if doc.is_encrypted() and password is not None:
                doc = _Document.load_bytes(data, password, *limit_args)
        elif path is not None:
            doc = _Document.load(path, None, *limit_args)
            if doc.is_encrypted() and password is not None:
                doc = _Document.load(path, password, *limit_args)
        else:
            doc = _Document(
                resolved_limits.max_text_size,
                resolved_limits.max_interpretation_size,
                resolved_limits.max_text_glyphs,
            )
        self._doc = doc
        self._closed = False
        self._path = None if path is None else Path(path)
        # Whether the core's CJK fallback slots hold the configured fonts, and
        # that configuration: None for the installed font extras, else each
        # configured language's FallbackFont, or None for its installed extra.
        self._fallback_configured = False
        self._fallback_state: dict[CjkFontLanguage, FallbackFont | None] | None = None
        # The face that form fills draw with; None selects one per value.
        self._form_font: Font | None = None
        # Structural generation; changes invalidate existing Page views.
        self._generation = 0
        # Object-numbering epoch; renumbering or reloading the objects
        # invalidates existing low-level object handles.
        self._object_epoch = 0
        # The edit revision without unsaved changes; a new document starts
        # with changes, as in pymupdf.
        self._clean_revision = None if data is None and path is None else doc.edit_revision()
        # The access that the password which unlocked the document grants,
        # which decides its permissions; None while it is locked or
        # unencrypted. A password that a document with an empty user password
        # did not need still counts when it matches.
        access = None if password is None else self._original_access(password)
        self._access = access or self._original_access("")
        # Retain input only while an undecrypted document may need reopening.
        self._source_path = path if self._doc.is_encrypted() else None
        self._source_bytes = data if self._doc.is_encrypted() else None
        self._emit_warnings()

    @property
    def needs_password(self) -> bool:
        """Return whether the document is locked until :meth:`unlock` receives its password."""
        self._ensure_not_closed()
        return self._doc.is_encrypted()

    @property
    def path(self) -> Path | None:
        """Return the file that the document was opened from, or ``None``."""
        return self._path

    @property
    def pages(self) -> Pages:
        """Return the pages, a live sequence of :class:`Page` views.

        ``doc.pages[i]`` takes negative indexes and slices, and
        :class:`Pages` adds, copies, deletes, selects, and moves pages.
        """
        self._ensure_not_closed()
        return Pages(self)

    @property
    def is_repaired(self) -> bool:
        """Return whether opening recovered damaged cross-reference data."""
        self._ensure_not_closed()
        return self._doc.is_repaired()

    @property
    def is_closed(self) -> bool:
        """Return whether :meth:`close` was called."""
        return self._closed

    def _original_access(self, password: str) -> Access | None:
        """Return the access that a password grants to an unlocked encrypted document, else ``None``."""
        if self._doc.is_encrypted() or not self._doc.was_encrypted():
            return None
        user, owner = self._doc.check_original_password(password)
        return _matched_access(password, user=user, owner=owner)

    @property
    def is_dirty(self) -> bool:
        """Return whether the document was edited since it was opened.

        As in pymupdf, every edit counts, even one that leaves the content as
        it was; a document created empty starts edited; and saving never
        resets it. Save options that change the in-memory document, such as
        ``compress`` and ``prune``, do not count as edits.
        """
        self._ensure_not_closed()
        return self._clean_revision != self._doc.edit_revision()

    @property
    def permissions(self) -> Permissions:
        """Return what the document permits, as :class:`Permissions` flags.

        A locked document permits nothing. An unencrypted document, or one
        unlocked with its owner password, permits everything,
        :attr:`Permissions.ALL`. Otherwise the flags are those that the
        encryption grants the user password. Test one with ``in``, as in
        ``Permissions.PRINT in doc.permissions``.
        """
        self._ensure_not_closed()
        if self._doc.is_encrypted():
            return Permissions(0)
        if self._access is Access.OWNER:
            return Permissions.ALL
        value = self._doc.encryption_permissions()
        return Permissions.ALL if value is None else Permissions(value & Permissions.ALL)

    def unlock(self, password: str) -> Access:
        """Unlock the document with a password and return the access that it grants.

        The owner password grants :attr:`Access.OWNER`, every right whatever
        the document's permissions, and the user password :attr:`Access.USER`,
        the rights of :attr:`permissions`. A password that matches neither
        raises :class:`PasswordError`. A document that opened without a
        password because its user password is empty can still be unlocked
        with its owner password; as in MuPDF, an empty owner password counts
        only when the user password is empty too. An unencrypted document
        grants :attr:`Access.OWNER` to any password. Passwords are capped at
        127 UTF-8 bytes. Unlocking a locked document reads it again, so
        handles to its objects go stale.
        """
        self._ensure_not_closed()
        _validate_password_input(password)
        if not self._doc.is_encrypted():
            if not self._doc.was_encrypted():
                return Access.OWNER
            access = self._original_access(password)
            if access is None:
                msg = "the password matches neither the user nor the owner password"
                raise PasswordError(msg)
            self._access = access
            return access
        access = _matched_access(
            password,
            user=self._doc.authenticate_user_password(password),
            owner=self._doc.authenticate_owner_password(password),
        )
        if access is None:
            msg = "the password matches neither the user nor the owner password"
            raise PasswordError(msg)
        # Read the document again with the password so that objects inside
        # object streams are readable.
        limit_args = _document_limit_args(self._limits)
        if self._source_path is not None:
            self._doc = _Document.load(self._source_path, password, *limit_args)
        elif self._source_bytes is not None:
            self._doc = _Document.load_bytes(self._source_bytes, password, *limit_args)
        self._source_path = None
        self._source_bytes = None
        # The new core document's fallback slots are empty; rendering fills them again.
        self._fallback_configured = False
        self._clean_revision = self._doc.edit_revision()
        self._access = access
        self._object_epoch += 1
        self._emit_warnings()
        return access

    @property
    def limits(self) -> DocumentLimits:
        """Return the immutable resource policy configured at open time."""
        self._ensure_not_closed()
        return self._limits

    @property
    def complexity(self) -> DocumentComplexity:
        """Return cheap structural facts without decoding streams or rendering."""
        self._ensure_not_closed()
        pages, objects, streams, encoded_bytes, depth = self._doc.complexity()
        return {
            "page_count": pages,
            "object_count": objects,
            "stream_count": streams,
            "encoded_stream_bytes": encoded_bytes,
            "max_object_depth": depth,
        }

    @property
    def objects(self) -> Objects:
        """Return the indirect objects, keyed by object number.

        This typed object model replaces pymupdf's ``xref_*`` methods:
        ``doc.objects[xref]`` reads an object, assignment replaces it, and
        :meth:`Objects.add` creates one. Writes take effect immediately and
        invalidate rendering and extraction caches; a write that changes the
        page order also invalidates existing :class:`Page` views. Objects are
        raw PDF: keeping them semantically valid is the caller's
        responsibility.
        """
        self._ensure_open()
        return Objects(self)

    @property
    def trailer(self) -> Dictionary:
        """Return the trailer dictionary as a live handle."""
        self._ensure_open()
        return Dictionary(self, None)

    @property
    def catalog(self) -> Dictionary:
        """Return the document catalog (the trailer's ``/Root``) as a live handle."""
        root = self.trailer.get("Root")
        if not isinstance(root, Dictionary):
            msg = "the trailer has no /Root catalog dictionary"
            raise PdfError(msg)
        return root

    def _bump_generation(self) -> None:
        """Record a structural change and invalidate existing page views."""
        self._generation += 1

    @property
    def metadata(self) -> Metadata:
        """Return the Info dictionary as a live :class:`Metadata` mapping.

        Assigning a mapping replaces every entry in one edit, and an empty
        mapping removes the Info dictionary.
        """
        self._ensure_open()
        return Metadata(self)

    @metadata.setter
    def metadata(self, values: Mapping[str, str]) -> None:
        self._ensure_open()
        if not isinstance(values, Mapping):
            msg = f"metadata must be a mapping: {type(values).__name__}"
            raise TypeError(msg)
        if not values:
            self._doc.remove_metadata()
            return
        checked = {key: _metadata_value(key, value) for key, value in values.items()}
        _write_metadata(self, {key: checked.get(key, "") for key in METADATA_KEYS})

    @property
    def version(self) -> str:
        """Return the PDF version of the file header, such as ``"1.7"``."""
        self._ensure_open()
        return self._doc.version()

    @property
    def encryption_method(self) -> str | None:
        """Return the encryption the document was opened with, such as ``"Standard V5 R6 256-bit AES"``, or ``None``."""
        self._ensure_open()
        return self._doc.encryption_method()

    @property
    def xmp(self) -> str | None:
        """Return the catalog's XMP metadata packet as text, or ``None`` when absent.

        The decoded stream is read as UTF-8 with invalid bytes replaced and
        ends at its first NUL character. Every decoding layer is bounded at
        1 MiB (``LimitError.code == "xmp_metadata_size"``); read a larger
        packet through ``doc.catalog["Metadata"].read(max_size=...)``.

        Assigning a string rewrites an existing ``/Metadata`` stream in place
        or adds one, unfiltered so that tools scanning the file for XMP
        packets find it, up to 1 MiB of UTF-8 (``xmp_metadata_input_size``).
        ``del doc.xmp`` removes the catalog's entry; the stream stays until a
        pruning save. :attr:`metadata` and the XMP packet are independent.
        """
        self._ensure_open()
        data = self._doc.xml_metadata(_DEFAULT_MAX_XMP_METADATA_SIZE)
        if data is None:
            return None
        return data.decode("utf-8", errors="replace").split("\0", 1)[0]

    @xmp.setter
    def xmp(self, packet: str) -> None:
        self._ensure_open()
        if not isinstance(packet, str):
            msg = f"xmp must be a str: {type(packet).__name__}"
            raise TypeError(msg)
        _validate_utf8_text_input(
            packet,
            _DEFAULT_MAX_XMP_METADATA_SIZE,
            limit_code="xmp_metadata_input_size",
            input_label="XMP metadata input",
        )
        self._doc.set_xml_metadata(packet.encode("utf-8"), _DEFAULT_MAX_XMP_METADATA_SIZE)

    @xmp.deleter
    def xmp(self) -> None:
        self._ensure_open()
        self._doc.remove_xml_metadata()

    @property
    def pdfa_claim(self) -> PdfaClaim | None:
        """Return the PDF/A part and conformance that the XMP metadata claims, or ``None``.

        This reads the ``pdfaid:part`` and ``pdfaid:conformance``
        self-declaration and does not validate compliance; use veraPDF or
        another validator for that. The XMP packet is read under the 1 MiB
        bound of :attr:`xmp`.
        """
        self._ensure_open()
        claim = self._doc.pdfa_claim(_DEFAULT_MAX_XMP_METADATA_SIZE)
        return None if claim is None else PdfaClaim(part=int(claim[0]), conformance=claim[1])

    @property
    def page_mode(self) -> PageMode:
        """Return how a viewer opens the document; unset or unknown reads as ``PageMode.USE_NONE``."""
        self._ensure_open()
        value = self.catalog.get("PageMode")
        try:
            return PageMode(str(value)) if isinstance(value, Name) else PageMode.USE_NONE
        except ValueError:
            return PageMode.USE_NONE

    @page_mode.setter
    def page_mode(self, mode: PageMode | str) -> None:
        self._ensure_open()
        self._doc.set_catalog_name("PageMode", _catalog_enum(PageMode, mode, "page_mode"))

    @property
    def page_layout(self) -> PageLayout:
        """Return how a viewer lays pages out; unset or unknown reads as ``PageLayout.SINGLE_PAGE``."""
        self._ensure_open()
        value = self.catalog.get("PageLayout")
        try:
            return PageLayout(str(value)) if isinstance(value, Name) else PageLayout.SINGLE_PAGE
        except ValueError:
            return PageLayout.SINGLE_PAGE

    @page_layout.setter
    def page_layout(self, layout: PageLayout | str) -> None:
        self._ensure_open()
        self._doc.set_catalog_name("PageLayout", _catalog_enum(PageLayout, layout, "page_layout"))

    @property
    def language(self) -> str | None:
        """Return the catalog's ``/Lang`` language tag as stored, or ``None``.

        The complete BCP 47 tag is kept, such as ``"zh-TW"`` or
        ``"sr-Latn-RS"``. Assigning writes a well-formed tag (ISO 32000-2
        §14.9.2.2) of at most 256 bytes, hyphen-separated subtags of one to
        eight ASCII letters or digits, the first of letters only; anything
        else raises :class:`PdfError`. ``del doc.language`` removes it.
        """
        self._ensure_open()
        value = self.catalog.get("Lang")
        return value.text if isinstance(value, String) else None

    @language.setter
    def language(self, tag: str) -> None:
        self._ensure_open()
        if not _is_language_tag(tag):
            msg = f"not a well-formed BCP 47 language tag: {tag!r}"
            raise PdfError(msg)
        self._doc.set_language_tag(tag)

    @language.deleter
    def language(self) -> None:
        self._ensure_open()
        self._doc.set_language_tag(None)

    @property
    def mark_info(self) -> MarkInfo | None:
        """Return the catalog's ``/MarkInfo`` flags, or ``None`` without a ``/MarkInfo`` dictionary.

        Each flag is true only when the dictionary holds ``true``. Assigning
        a :class:`MarkInfo` replaces the dictionary, and ``del
        doc.mark_info`` removes it.
        """
        self._ensure_open()
        mark_info = self.catalog.get("MarkInfo")
        if not isinstance(mark_info, Dictionary):
            return None
        return MarkInfo(
            marked=mark_info.get("Marked") is True,
            user_properties=mark_info.get("UserProperties") is True,
            suspects=mark_info.get("Suspects") is True,
        )

    @mark_info.setter
    def mark_info(self, flags: MarkInfo) -> None:
        self._ensure_open()
        if not isinstance(flags, MarkInfo):
            msg = f"mark_info must be a MarkInfo: {type(flags).__name__}"
            raise TypeError(msg)
        self._doc.set_mark_info(flags.marked, flags.user_properties, flags.suspects)

    @mark_info.deleter
    def mark_info(self) -> None:
        self._ensure_open()
        catalog = self.catalog
        if "MarkInfo" in catalog:
            del catalog["MarkInfo"]

    def markdown(
        self,
        pages: Iterable[int] | None = None,
        *,
        table_strategy: Literal["lines", "text"] | None = "lines",
        max_size: int | None = _DEFAULT_MAX_MARKDOWN_SIZE,
    ) -> str:
        """Convert the document to Markdown for RAG or LLM preprocessing.

        Headings are inferred from font sizes: the size containing the most text
        is body text, and larger sizes map in descending order to
        ``#`` through ``####``. Wrapped CJK lines join without spaces. Leading
        bullets and ``1.``/``1)`` forms normalize to Markdown lists. Scanned PDFs
        work after adding a text layer with :meth:`Page.apply_ocr`.
        Multicolumn text follows deterministic whitespace gutters.
        Conservatively detected vertical CJK columns follow top-to-bottom,
        right-to-left reading order; ruby and mixed-orientation typography are
        not interpreted. Complete bordered grids become Markdown tables by
        default. Pass ``table_strategy="text"`` to extend this with conservative
        borderless detection, or ``None`` to disable table conversion.
        ``pages`` is a sequence of zero-based page numbers emitted in the given
        order; ``None`` means every page. An explicit sequence may contain up to
        the larger of the page count and 4,096 entries; further entries raise
        :class:`LimitError` with code ``page_entry_count``. ``max_size`` caps
        aggregate UTF-8 Markdown output and defaults to 64 MiB; ``None``
        explicitly opts out.
        """
        self._ensure_open()
        if table_strategy not in ("lines", "text", None):
            msg = f"table_strategy must be 'lines', 'text', or None: {table_strategy!r}"
            raise ValueError(msg)
        _validate_optional_positive_int("max_size", max_size)
        count = self._page_count()
        page_numbers: Sequence[int] = (
            range(1, count + 1) if pages is None else self._page_entries(pages, "pages", count)
        )

        # Both passes reuse the validated snapshot instead of re-walking the
        # page tree for every entry, keeping whole-document conversion linear.
        size_counts: Counter[float] = Counter()
        for page_number in page_numbers:
            layout = _text.text_page_dict(*self._page_layout(page_number))
            page = Page(self, page_number - 1)
            tables = [] if table_strategy is None else _markdown_page_tables(page, table_strategy)
            bboxes = [(table.bbox.x0, table.bbox.y0, table.bbox.x1, table.bbox.y1) for table in tables]
            size_counts.update(_markdown.collect_sizes([layout], [bboxes]))
        levels = _markdown.heading_levels(size_counts)

        rendered: list[str] = []
        output_bytes = 0
        for page_number in page_numbers:
            width, height, blocks = self._page_layout(page_number)
            layout = _text.text_page_dict(width, height, blocks)
            page = Page(self, page_number - 1)
            tables = [] if table_strategy is None else _markdown_page_tables(page, table_strategy)
            separator_bytes = 2 if rendered else 0
            page_budget = None if max_size is None else max_size - output_bytes - separator_bytes
            bboxes, markdown_tables = _markdown_table_output(
                layout,
                tables,
                max_size=max_size,
                remaining_size=page_budget,
            )
            words = _text.text_words(blocks) if tables else None
            page_markdown = _bounded_markdown_page_output(
                layout,
                levels,
                markdown_tables,
                words,
                _MarkdownBudget(max_size, page_budget),
            )
            if not page_markdown:
                continue
            if max_size is not None:
                remaining = max_size - output_bytes - separator_bytes
                if len(page_markdown) > remaining:
                    raise _markdown_output_limit_error(max_size)
                page_bytes = _markdown.utf8_size(page_markdown)
                if page_bytes > remaining:
                    raise _markdown_output_limit_error(max_size)
            else:
                page_bytes = 0
            output_bytes += separator_bytes + page_bytes
            rendered.append(page_markdown)
        return "\n\n".join(rendered)

    @property
    def form(self) -> Form:
        """Return the interactive form's terminal fields, a :class:`Form` mapping by full name.

        ``doc.form[name].value = "..."`` fills a field and regenerates its
        appearances, and ``doc.form.font`` chooses the face that fills draw
        with.
        """
        self._ensure_open()
        return Form(self)

    @property
    def page_labels(self) -> list[PageLabelRange]:
        """Return the page-label ranges in page order, or ``[]`` without labels.

        Each :class:`PageLabelRange` labels the pages from its zero-based
        ``start`` up to the next range; ``page.label`` gives one page's
        label. Assigning a list replaces the ranges, the first of which must
        start at page 0, and ``del doc.page_labels`` removes them. Reads
        refuse more than 4,096 ranges or tree nodes, 32 levels, or 1 MiB of
        style and prefix text; writes stop at 4,096 ranges and 1 MiB of text
        (``LimitError.code == "page_label_input_size"``).
        """
        return _read_page_labels(self)

    @page_labels.setter
    def page_labels(self, ranges: Iterable[PageLabelRange]) -> None:
        _write_page_labels(self, ranges)

    @page_labels.deleter
    def page_labels(self) -> None:
        _write_page_labels(self, ())

    @property
    def images(self) -> Images:
        """Return the image XObjects, keyed by object number.

        ``doc.images[xref]`` is an :class:`ImageObject` with the image's size,
        color space, filters, and masks, :meth:`ImageObject.extract` for its
        data, and :meth:`ImageObject.to_pixmap` for its pixels; another object
        number raises :class:`KeyError`. ``page.resources.images`` lists the
        images that one page's resources reach.
        """
        self._ensure_open()
        return Images(self)

    @property
    def attachments(self) -> Attachments:
        """Return the live mapping of embedded files, keyed by name.

        This replaces pymupdf's ``embfile_*`` methods: ``list(doc.attachments)``
        for ``embfile_names``, ``doc.attachments[name].read()`` for
        ``embfile_get``, ``doc.attachments.add(...)`` for ``embfile_add``,
        ``doc.attachments[name].write(data)`` for ``embfile_upd``, and
        ``del doc.attachments[name]`` for ``embfile_del``. The EmbeddedFiles
        name tree is read cycle-aware and rejects more than 4,096 entries or
        nodes, 32 levels, or 1 MiB of encoded/decoded names.
        """
        self._ensure_open()
        return Attachments(self)

    def text(self, pages: Iterable[int] | None = None) -> str:
        """Return the plain text of the given pages in one batched call.

        ``pages`` is a sequence of zero-based page numbers extracted in the
        given order; ``None`` means every page, negative values count from the
        end, and duplicates are allowed. An explicit sequence may contain up to
        the larger of the page count and 4,096 entries; further entries raise
        :class:`LimitError` with code ``page_entry_count``. Pages are processed
        in chunks of 4,096 entries, each sharing one interpreter font cache, so
        a font reused across pages is parsed once per chunk rather than once
        per page; the returned text equals joining :meth:`Page.text` of each
        page in the same order.
        """
        count = self._page_count()
        page_numbers = list(range(1, count + 1)) if pages is None else self._page_entries(pages, "pages", count)
        text = self._doc.extract_text(page_numbers)
        self._emit_warnings()
        return text

    def _page_text(self, pno: int) -> str:
        """Return the text of zero-based page ``pno`` for :meth:`Page.text`."""
        text = self._doc.extract_text([self._lopdf_page_number(pno)])
        self._emit_warnings(stacklevel=4)
        return text

    def _page_layout(self, page_number: int) -> tuple[float, float, list[Any]]:
        """Return the core's layout of a validated one-based page."""
        layout = self._doc.extract_layout(page_number)
        # Attribute warnings to the public caller rather than this helper.
        self._emit_warnings(stacklevel=4)
        return layout

    def _page_raw_layout(self, page_number: int) -> tuple[float, float, list[Any]]:
        """Return the core's layout with glyphs of a validated one-based page."""
        layout = self._doc.extract_raw_layout(page_number)
        self._emit_warnings(stacklevel=4)
        return layout

    @property
    def outline(self) -> Outline:
        """Return the outline (bookmarks) as a live :class:`Outline` of item handles.

        Iterating gives the top-level :class:`OutlineItem` handles, whose
        ``children`` give the rest of the tree; ``walk()`` visits every item
        and ``entries()`` returns them as :class:`OutlineEntry` records.
        Assigning an iterable of :class:`OutlineEntry` records replaces the
        outline, and ``del doc.outline`` removes it. Page numbers are
        zero-based. Reads and writes share the outline limits: 65,536 items
        (``LimitError.code == "toc_entry_count"``), 64 levels, and 16 MiB of
        title and target text (``"toc_text_size"`` to read,
        ``"toc_input_size"`` to write).
        """
        self._ensure_open()
        return Outline(self)

    @outline.setter
    def outline(self, entries: Iterable[OutlineEntry]) -> None:
        _write_outline(self, entries)

    @outline.deleter
    def outline(self) -> None:
        _write_outline(self, ())

    @property
    def fallback_fonts(self) -> FallbackFonts:
        """Return the fonts that render non-embedded CJK fonts, by language.

        Assigning a mapping from ``"ja"``, ``"ko"``, ``"zh-CN"``, or
        ``"zh-TW"`` to :class:`FallbackFont` records replaces every language,
        so ``{}`` turns fallback rendering off; ``del`` returns to the
        installed font extras, which the mapping lists until it is edited.
        """
        self._ensure_open()
        return FallbackFonts(self)

    @fallback_fonts.setter
    def fallback_fonts(self, fonts: Mapping[str, FallbackFont]) -> None:
        _replace_fallback_fonts(self, fonts)

    @fallback_fonts.deleter
    def fallback_fonts(self) -> None:
        _reset_fallback_fonts(self)

    def _ensure_fallback_fonts(self) -> None:
        """Fill the core's fallback slots with the installed extras and the configured fonts."""
        if self._fallback_configured:
            return
        state = self._fallback_state
        # Look the providers up through their module, where tests replace them.
        providers = tuple(
            provider
            for provider in _fonts._bundled_font_providers()
            if provider.language in {"ja", "ko", "zh-CN", "zh-TW"}
            and (state is None or (provider.language in state and state[provider.language] is None))
        )
        if all(isinstance(provider.sans, Path) and isinstance(provider.serif, Path) for provider in providers):
            self._doc.set_fallback_font_locale_files(
                [(provider.language, str(provider.sans), str(provider.serif)) for provider in providers],
                _DEFAULT_MAX_FONT_INPUT_SIZE,
            )
        else:
            self._load_fallback_providers(providers)
        for language, value in (state or {}).items():
            if value is not None:
                _apply_fallback_font(self, language, value)
        self._fallback_configured = True

    def _load_fallback_providers(self, providers: tuple[_fonts._BundledFontProvider, ...]) -> None:
        """Load installed extras whose faces are not all files, checking every size first."""
        for provider in providers:
            for data in (provider.sans, provider.serif):
                if isinstance(data, bytes) and len(data) > _DEFAULT_MAX_FONT_INPUT_SIZE:
                    raise _font_input_limit_error(len(data), _DEFAULT_MAX_FONT_INPUT_SIZE)
        for provider in providers:
            for kind, data in (("sans", provider.sans), ("serif", provider.serif)):
                if isinstance(data, Path):
                    self._doc.set_fallback_font_file(
                        kind,
                        str(data),
                        0,
                        _DEFAULT_MAX_FONT_INPUT_SIZE,
                        provider.language,
                    )
                else:
                    self._doc.set_fallback_font(
                        kind,
                        data,
                        0,
                        _DEFAULT_MAX_FONT_INPUT_SIZE,
                        provider.language,
                    )

    def render(  # noqa: PLR0913 - batch resource controls are keyword-only.
        self,
        pages: Iterable[int] | None = None,
        *,
        dpi: float | None = None,
        matrix: Matrix | Sequence[float] | None = None,
        alpha: bool = False,
        background: tuple[int, int, int] | tuple[int, int, int, int] | None = None,
        annotations: bool = True,
        colorspace: Colorspace | Literal["gray", "rgb"] = Colorspace.RGB,
        format: Literal["png", "jpeg"] = "png",  # noqa: A002 - the public name.
        quality: int = 95,
        workers: int | None = None,
        max_size: int | None = _DEFAULT_MAX_RENDER_BATCH_SIZE,
    ) -> list[bytes]:
        """Render pages to encoded images concurrently while preserving input order.

        ``dpi``, ``matrix``, ``alpha``, ``background``, ``annotations``, and
        ``colorspace`` have the meaning of :meth:`Page.pixmap`, so the default
        is opaque RGB at 72 dpi. Each page is encoded as ``format``, PNG or
        JPEG at ``quality`` (1 through 100); JPEG has no alpha. ``pages``
        contains zero-based page numbers and defaults to every page.
        Duplicates are allowed. ``workers=None`` uses up to four CPUs; explicit
        values must be between 1 and 64. Actual concurrency is also capped to
        roughly 512 MB of estimated live raster and conversion buffers. A
        dedicated bounded worker pool renders one immutable snapshot of the
        document while the GIL is released, replacing each worker's render
        cache after 4,096 pages. An explicit sequence may contain up to the
        larger of the page count and 4,096 entries; further entries raise
        :class:`LimitError` with code ``page_entry_count``. ``max_size`` caps
        their cumulative encoded bytes and defaults to 512 MiB; pass ``None``
        only to accept unbounded output.

        Do not edit or call other methods on this same :class:`Document` from
        another thread during the operation. Such calls are not part of the
        concurrency contract; use this method as the supported same-document
        parallel rendering boundary.
        """
        self._ensure_open()
        scale = _render_scale(matrix, dpi)
        rgba = _render_background(background, alpha=alpha)
        _validate_bool("annotations", annotations)
        gray = _render_gray(colorspace)
        _validate_encoding(format, quality, alpha=alpha)
        if workers is None:
            workers = min(4, os.cpu_count() or 1)
        elif isinstance(workers, bool) or not isinstance(workers, int):
            msg = f"workers must be an integer between 1 and {_MAX_RENDER_WORKERS}"
            raise TypeError(msg)
        if not 1 <= workers <= _MAX_RENDER_WORKERS:
            msg = f"workers must be between 1 and {_MAX_RENDER_WORKERS}"
            raise ValueError(msg)
        _validate_optional_positive_int("max_size", max_size)
        count = self._page_count()
        page_numbers = list(range(1, count + 1)) if pages is None else self._page_entries(pages, "pages", count)
        if not page_numbers:
            return []
        self._ensure_fallback_fonts()
        result = self._doc.render_pages_encoded(
            page_numbers, scale, gray, alpha, rgba, annotations, format, quality, workers, max_size
        )
        self._emit_warnings()
        return result

    def _emit_warnings(self, stacklevel: int = 3) -> None:
        """Emit recoverable interpretation warnings as ``PylopdfWarning``."""
        for message in self._doc.take_warnings():
            _warnings.warn(message, PylopdfWarning, stacklevel=stacklevel)

    def rewrite_images(  # noqa: PLR0913  # pymupdf's keyword arguments.
        self,
        *,
        dpi_threshold: float | None = None,
        dpi_target: float = 0,
        quality: int = 0,
        lossy: bool = True,
        lossless: bool = True,
        bitonal: bool = True,
        color: bool = True,
        gray: bool = True,
        set_to_gray: bool = False,
        options: object = None,
    ) -> ImageCompressionResult:
        """Downsample and JPEG-recompress raster images in place, as pymupdf's ``rewrite_images`` does.

        An image axis whose effective resolution exceeds ``dpi_threshold`` is
        resampled to ``dpi_target``, which must be lower; reused images keep
        the pixels that their largest placement across the document needs.
        ``quality`` (1 through 100) is the JPEG quality of every rewritten
        image; the default 0 asks for no recompression. ``lossy`` and
        ``lossless`` select DCT and Flate sources, ``color`` and ``gray``
        DeviceRGB and DeviceGray ones. Both operations are lossy, and a
        rewritten image is kept only when its JPEG payload is smaller.

        pylopdf re-encodes every image it rewrites as JPEG, so resampling
        requires a ``quality`` above 0; ``set_to_gray`` and MuPDF's
        ``options`` raise :class:`PdfError` rather than being ignored.
        Bitonal images, and every other image outside the conservative set
        below, are counted as ``skipped``. With ``quality=0`` and no
        ``dpi_threshold`` there is nothing to do, and the call returns zero
        counts without interpreting the document.

        The conservative implementation rewrites only indirect, single-filter,
        8-bit DeviceGray/DeviceRGB DCT or Flate XObjects without masks or custom
        decode arrays. DCT decode parameters are excluded; Flate may use no
        predictor or a consistent PNG predictor. Inline images are not
        considered. The operation releases the GIL, is atomic on decoding
        errors, rejects more than 65,536 interpreted indirect image
        placements, 16,384 unique image objects, or 250 million eligible
        source pixels, and skips an individual source above 64 million
        pixels.

        pymupdf returns ``None``; pylopdf returns an
        :class:`ImageCompressionResult`, whose byte totals compare rewritten
        source payloads with their resulting JPEG payloads, not complete PDF
        serialization. Save to a new output and inspect it before replacing
        an original document.
        """
        self._ensure_open()
        if set_to_gray:
            msg = "rewrite_images(set_to_gray=True) is not supported; pylopdf keeps each image's colour space"
            raise PdfError(msg)
        if options is not None:
            msg = "rewrite_images(options=...) is not supported; pass its keyword arguments instead"
            raise PdfError(msg)
        del bitonal  # Bitonal images are never rewritten; they count as skipped.
        if (
            isinstance(quality, bool)
            or not isinstance(quality, int)
            or not 0 <= quality <= _IMAGE_COMPRESSION_MAX_QUALITY
        ):
            msg = "quality must be an integer from 0 through 100"
            raise PdfError(msg)
        threshold = None if not dpi_threshold else _rewrite_dpi("dpi_threshold", dpi_threshold)
        target = 0.0
        if threshold is not None:
            target = _rewrite_dpi("dpi_target", dpi_target)
            if target >= threshold:
                msg = f"dpi_target={dpi_target} must be less than dpi_threshold={dpi_threshold}"
                raise ValueError(msg)
            if quality == 0:
                msg = "resampling requires quality= from 1 through 100; pylopdf re-encodes rewritten images as JPEG"
                raise PdfError(msg)
        if quality == 0:
            counts = (0, 0, 0, 0, 0)
        else:
            counts = self._doc.rewrite_images(threshold, target, quality, lossy, lossless, color, gray)
            self._emit_warnings()
        considered, rewritten, skipped, bytes_before, bytes_after = counts
        return {
            "considered": considered,
            "rewritten": rewritten,
            "skipped": skipped,
            "bytes_before": bytes_before,
            "bytes_after": bytes_after,
            "bytes_saved": bytes_before - bytes_after,
        }

    def save(  # noqa: PLR0913 - one keyword per save option.
        self,
        path: str | os.PathLike[str],
        /,
        *,
        compress: bool = True,
        prune: bool = True,
        dedupe: bool = False,
        renumber: bool = False,
        object_streams: bool = False,
        encryption: Encryption | None = None,
    ) -> None:
        """Write the document to a file.

        ``compress`` applies Flate compression to the streams that have no
        filter, and ``prune`` removes the objects that nothing reaches from
        the trailer; both make the file smaller without changing what a viewer
        shows. ``dedupe`` merges identical objects and streams, and needs
        ``prune`` to remove the duplicates; pages, annotations, form fields,
        structure elements, outline items, and optional-content groups are
        never merged. ``renumber`` numbers the remaining objects
        consecutively. These four options change the document itself, and
        handles to removed or renumbered objects go stale. ``object_streams``
        packs objects into object streams with a cross-reference stream,
        which raises the PDF version to 1.5 when it is lower.

        ``encryption`` takes an :class:`Encryption` and writes AES-256
        encryption while the document in memory stays unencrypted; ``None``
        writes no encryption, even for a document that was encrypted.
        Encryption cannot be combined with ``object_streams``. The file is
        written to a temporary file next to ``path``, which replaces ``path``
        only after the write succeeded.
        """
        self._ensure_open()
        self._check_save_options(
            compress=compress,
            prune=prune,
            dedupe=dedupe,
            renumber=renumber,
            object_streams=object_streams,
            encryption=encryption,
        )
        clean = not self.is_dirty
        try:
            self._apply_save_options(compress=compress, prune=prune, dedupe=dedupe, renumber=renumber)
            writer: Callable[[str], None]
            if encryption is not None:
                writer = functools.partial(
                    self._doc.save_encrypted,
                    user_password=encryption.user_password,
                    owner_password=_owner_password(encryption),
                    permissions=int(encryption.permissions),
                    file_encryption_key=os.urandom(32),
                )
            elif object_streams:
                writer = self._doc.save_with_object_streams
            else:
                writer = self._doc.save
            _atomic_save_file(path, writer)
        finally:
            if clean:
                self._clean_revision = self._doc.edit_revision()

    def to_bytes(  # noqa: PLR0913 - one keyword per save option.
        self,
        *,
        compress: bool = True,
        prune: bool = True,
        dedupe: bool = False,
        renumber: bool = False,
        object_streams: bool = False,
        encryption: Encryption | None = None,
        max_size: int | None = _DEFAULT_MAX_PDF_OUTPUT_SIZE,
    ) -> bytes:
        """Return the document as PDF bytes, with the options of :meth:`save`.

        ``max_size`` bounds the output, 512 MiB by default, and refuses the
        write that would pass it with :class:`LimitError` code
        ``pdf_output_size``; ``None`` leaves it unbounded for trusted input.
        """
        self._ensure_open()
        self._check_save_options(
            compress=compress,
            prune=prune,
            dedupe=dedupe,
            renumber=renumber,
            object_streams=object_streams,
            encryption=encryption,
        )
        _validate_optional_positive_int("max_size", max_size)
        clean = not self.is_dirty
        try:
            self._apply_save_options(compress=compress, prune=prune, dedupe=dedupe, renumber=renumber)
            if encryption is not None:
                return self._doc.save_bytes_encrypted(
                    encryption.user_password,
                    _owner_password(encryption),
                    int(encryption.permissions),
                    os.urandom(32),
                    max_size,
                )
            if object_streams:
                return self._doc.save_bytes_with_object_streams(max_size)
            return self._doc.save_bytes(max_size)
        finally:
            if clean:
                self._clean_revision = self._doc.edit_revision()

    @staticmethod
    def _check_save_options(  # noqa: PLR0913 - one keyword per save option.
        *,
        compress: bool,
        prune: bool,
        dedupe: bool,
        renumber: bool,
        object_streams: bool,
        encryption: Encryption | None,
    ) -> None:
        """Validate the options of :meth:`save` and :meth:`to_bytes` before anything changes."""
        options = {
            "compress": compress,
            "prune": prune,
            "dedupe": dedupe,
            "renumber": renumber,
            "object_streams": object_streams,
        }
        for name, value in options.items():
            if not isinstance(value, bool):
                msg = f"{name} must be True or False: {value!r}"
                raise TypeError(msg)
        if dedupe and not prune:
            msg = "dedupe=True needs prune=True, which removes the merged duplicates"
            raise ValueError(msg)
        if encryption is not None:
            if not isinstance(encryption, Encryption):
                msg = f"encryption must be an Encryption or None: {encryption!r}"
                raise TypeError(msg)
            if object_streams:
                msg = "encryption cannot be combined with object_streams=True"
                raise ValueError(msg)

    def _apply_save_options(self, *, compress: bool, prune: bool, dedupe: bool, renumber: bool) -> None:
        """Apply the save options that change the document before it is written."""
        if prune or renumber:
            self._doc.collect_garbage(dedupe, prune, renumber)
            if renumber:
                # Renumbering moves objects, so object handles go stale.
                self._object_epoch += 1
        if compress:
            self._doc.compress()

    def close(self) -> None:
        """Close the document; subsequent operations raise an error."""
        self._closed = True
        self._doc.release_render_cache()

    def _ensure_not_closed(self) -> None:
        """Reject operations on a closed document."""
        if self._closed:
            msg = "document closed"
            raise DocumentClosedError(msg)

    def _ensure_open(self) -> None:
        """Reject operations on a closed or undecrypted document.

        lopdf makes undecrypted files look like empty zero-page documents, so
        report the encrypted state explicitly.
        """
        self._ensure_not_closed()
        if self._doc.is_encrypted():
            msg = "this PDF is encrypted; open it with the password argument or call unlock()"
            raise EncryptedDocumentError(msg)

    def _page_count(self) -> int:
        """Return the page count of an open, decrypted document."""
        self._ensure_open()
        return self._doc.page_count()

    def _normalize_pno(self, pno: int) -> int:
        """Resolve negative indexing and return a valid zero-based page number."""
        self._ensure_open()
        return _resolve_page_index(pno, self._doc.page_count())

    def _lopdf_page_number(self, pno: int) -> int:
        """Validate a Python page index and convert it to one-based lopdf form."""
        return self._normalize_pno(pno) + 1

    def _page_entries(self, pages: Iterable[int], name: str, count: int) -> list[int]:
        """Validate explicit page entries against one page-count snapshot.

        Return one-based lopdf page numbers. Entries may repeat pages, but
        materialization stops at the first entry beyond the larger of
        ``count`` and 4,096, so repetition never costs more than one complete
        pass over a large document.
        """
        limit = max(count, _MIN_PAGE_ENTRY_LIMIT)
        numbers: list[int] = []
        for pno in pages:
            if len(numbers) >= limit:
                limit_code = "page_entry_count"
                msg = (
                    f"{name} cannot contain more than {limit} entries "
                    f"(the larger of the page count and {_MIN_PAGE_ENTRY_LIMIT})"
                )
                raise LimitError(limit_code, msg)
            numbers.append(_resolve_page_index(pno, count) + 1)
        return numbers

    def __enter__(self) -> Self:
        """Enter a context manager and return this document."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the document when leaving a context manager."""
        self.close()

    def __repr__(self) -> str:
        """Return a representation containing the open or closed state."""
        state = "closed " if self._closed else ""
        return f"<{state}pylopdf.Document>"


def _open_source(source: object) -> tuple[str | None, bytes | None]:
    """Split ``open``'s source into a file path or the bytes of a document."""
    if isinstance(source, bytes):
        return None, source
    if isinstance(source, (bytearray, memoryview)):
        return None, bytes(source)
    if isinstance(source, (str, os.PathLike)):
        path = os.fspath(source)
        if isinstance(path, str):
            return path, None
    if hasattr(source, "read"):
        msg = f"source must be a path or bytes; read the file object first: {type(source).__name__}"
        raise TypeError(msg)
    msg = f"source must be a path (str or os.PathLike[str]) or bytes, bytearray, or memoryview: {type(source).__name__}"
    raise TypeError(msg)


def open(  # noqa: A001 - the package's entry point, pylopdf.open.
    source: str | os.PathLike[str] | bytes | bytearray | memoryview,
    /,
    *,
    password: str | None = None,
    limits: DocumentLimits | None = None,
) -> Document:
    """Open a PDF from a file path or from the bytes of a document.

    A document with an empty user password decrypts on its own; any other
    encrypted document needs ``password``, or :meth:`Document.unlock`
    after opening. Passwords are capped at 127 UTF-8 bytes. ``limits``
    applies structural, decompression, interpretation, and text budgets before
    expensive work; :meth:`DocumentLimits.web` is a conservative starting
    profile for untrusted uploads. Input that is not a readable PDF, including
    empty input, raises :class:`MalformedPdfError`, and a missing file raises
    :class:`FileNotFoundError`.
    """
    path, data = _open_source(source)
    document = Document.__new__(Document)
    document._setup(path, data, password, limits)
    return document


#: The Info dictionary keys that :class:`Probe` reports, by their names in pylopdf.
_PROBE_METADATA_KEYS = (
    ("title", "Title"),
    ("author", "Author"),
    ("subject", "Subject"),
    ("keywords", "Keywords"),
    ("creator", "Creator"),
    ("producer", "Producer"),
    ("created", "CreationDate"),
    ("modified", "ModDate"),
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Probe:
    """What :func:`probe` reads without loading the whole document."""

    #: The number of pages.
    page_count: int
    #: The PDF version of the header, such as ``"1.7"``.
    version: str
    #: Whether the document is encrypted and the password given, if any, did not unlock it.
    needs_password: bool
    #: Whether reading it needed cross-reference recovery.
    is_repaired: bool
    #: The Info dictionary's text entries that are present: ``title``,
    #: ``author``, ``subject``, ``keywords``, ``creator``, ``producer``,
    #: ``created``, and ``modified``, the last two as PDF date strings.
    metadata: Mapping[str, str]


def probe(
    source: str | os.PathLike[str] | bytes | bytearray | memoryview,
    /,
    *,
    password: str | None = None,
    max_file_size: int | None = None,
) -> Probe:
    """Read the page count, version, encryption, and metadata without loading the whole document.

    This suits scanning many files. ``max_file_size`` refuses larger input
    before parsing with :class:`LimitError` code ``file_size``; ``None``, the
    default, leaves it unbounded. The metadata text is capped at 1 MiB, and
    passwords at 127 UTF-8 bytes.
    """
    path, data = _open_source(source)
    _validate_optional_positive_int("max_file_size", max_file_size)
    _validate_password_input(password)
    if data is not None:
        raw, page_count, version, encrypted, recovery = _Document.load_metadata_bytes(data, password, max_file_size)
    else:
        raw, page_count, version, encrypted, recovery = _Document.load_metadata(
            cast("str", path), password, max_file_size
        )
    if recovery is not None:
        _warnings.warn(recovery, PylopdfWarning, stacklevel=2)
    return Probe(
        page_count=page_count,
        version=version,
        needs_password=encrypted,
        is_repaired=recovery is not None,
        metadata=MappingProxyType({name: raw[key] for name, key in _PROBE_METADATA_KEYS if key in raw}),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class Encryption:
    """How :meth:`Document.save` and :meth:`Document.to_bytes` encrypt the file.

    The user password opens the document with the rights in ``permissions``,
    and the owner password opens it with every right. ``owner_password``
    defaults to ``user_password``. An empty user password lets anyone open
    the document while ``permissions`` still restricts it; that restriction
    holds only as long as the owner password is not empty too. Each password
    is capped at 127 UTF-8 bytes. ``method`` is ``"aes-256"``, the AES-256
    encryption of PDF 2.0, the one method that pylopdf writes.
    """

    user_password: str = ""
    owner_password: str | None = None
    permissions: Permissions = Permissions.ALL
    method: Literal["aes-256"] = "aes-256"

    def __post_init__(self) -> None:
        """Validate the passwords, permissions, and method."""
        if not isinstance(self.user_password, str):
            msg = f"user_password must be a str: {self.user_password!r}"
            raise TypeError(msg)
        if self.owner_password is not None and not isinstance(self.owner_password, str):
            msg = f"owner_password must be a str or None: {self.owner_password!r}"
            raise TypeError(msg)
        _validate_password_input(self.user_password, "user password")
        _validate_password_input(self.owner_password, "owner password")
        if not _is_int(self.permissions):
            msg = f"permissions must be Permissions flags: {self.permissions!r}"
            raise TypeError(msg)
        object.__setattr__(self, "permissions", Permissions(int(self.permissions) & Permissions.ALL))
        if self.method != "aes-256":
            msg = f"method must be 'aes-256': {self.method!r}"
            raise ValueError(msg)


# The package re-exports these names; reprs, pickles, and documentation name it.
for _public in (Document, Encryption, Probe, open, probe):
    _public.__module__ = "pylopdf"
