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
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, cast, overload

from pylopdf import _fonts, _markdown
from pylopdf._attachments import Attachments
from pylopdf._errors import DocumentClosedError, EncryptedDocumentError, PylopdfWarning
from pylopdf._fonts import (
    _font_input_limit_error,
    _form_generation_font,
    _normalize_cjk_font_language,
    _normalize_font_language,
    _package_face_weight,
    _resolve_font_source,
    _validate_form_font,
)
from pylopdf._hints import hinted_getattr, hinted_protocol
from pylopdf._limits import (
    _DEFAULT_MAX_FONT_INPUT_SIZE,
    _DEFAULT_MAX_MARKDOWN_SIZE,
    _UINT32_MAX,
    DocumentLimits,
    _document_limit_args,
    _validate_cumulative_utf8_input,
    _validate_optional_positive_int,
    _validate_utf8_text_input,
)
from pylopdf._objects import Dictionary, Name, Objects, String
from pylopdf._page import _PAGE_LABEL_STYLES, Page
from pylopdf._pages import Pages
from pylopdf._pixmap import _render_background, _render_scale
from pylopdf._tables import (
    _bounded_markdown_page_output,
    _markdown_output_limit_error,
    _markdown_page_tables,
    _markdown_table_output,
    _MarkdownBudget,
)
from pylopdf._types import (
    PDF_ENCRYPT_AES_256,
    PDF_ENCRYPT_NONE,
    Block,
    EncryptionMethod,
    OutlineEntry,
    Permissions,
    Word,
)
from pylopdf.pylopdf_core import LimitError, PdfError, _Document

if sys.platform == "win32":
    from pylopdf.pylopdf_core import _copy_windows_dacl

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from types import TracebackType
    from typing import Self

    from pylopdf._fonts import Font
    from pylopdf._geometry import Matrix
    from pylopdf._types import (
        CjkFontLanguage,
        DocumentComplexity,
        DocumentMetadata,
        ExtractedImage,
        FontLanguage,
        FormFieldInfo,
        FormFieldType,
        ImageCompressionResult,
        MarkInfo,
        MetadataUpdate,
        PageLabelInfo,
        PageLabelSpec,
        RawTextPageDict,
        TextPageDict,
    )


_DEFAULT_MAX_XMP_METADATA_SIZE = 1024 * 1024
_DEFAULT_MAX_RENDER_BATCH_SIZE = 512 * 1024 * 1024
_DEFAULT_MAX_PDF_OUTPUT_SIZE = 512 * 1024 * 1024
#: The first garbage level that renumbers objects.
_GARBAGE_RENUMBERS = 2
_MAX_FORM_FIELD_INPUT_SIZE = 1024 * 1024
_MAX_METADATA_INPUT_TEXT_SIZE = 1024 * 1024
_MAX_TOC_INPUT_TEXT_SIZE = 16 * 1024 * 1024
_MAX_PAGE_LABEL_INPUT_TEXT_SIZE = 1024 * 1024
_UTF16_BMP_MAX = (1 << 16) - 1
_MAX_PAGE_LABEL_RANGES = 4096
_MAX_PASSWORD_INPUT_BYTES = 127
_MAX_TOC_ENTRIES = 65_536
# Explicit page iterables may repeat pages up to the larger of the page count
# and this floor, so repetition never costs more than one full document pass.
_MIN_PAGE_ENTRY_LIMIT = 4096
_TEMPORARY_FILE_ATTEMPTS = 100

_IMAGE_COMPRESSION_MIN_DPI = 36.0
_IMAGE_COMPRESSION_MAX_DPI = 2400.0
_IMAGE_COMPRESSION_MAX_QUALITY = 100
_MAX_RENDER_WORKERS = 64


_MAX_GARBAGE_LEVEL = 4


def _garbage_level(garbage: int) -> int:
    """Validate a ``garbage`` save option; ``True`` and ``False`` mean 1 and 0."""
    if not isinstance(garbage, int):
        msg = f"garbage must be an integer from 0 to 4: {garbage!r}"
        raise TypeError(msg)
    if not 0 <= garbage <= _MAX_GARBAGE_LEVEL:
        msg = f"garbage must be an integer from 0 to 4: {garbage!r}"
        raise PdfError(msg)
    return int(garbage)


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


def _validate_cumulative_pdf_text_input(
    values: Iterable[str],
    limit: int,
    *,
    limit_code: str,
    input_label: str,
) -> None:
    """Bound aggregate ASCII or UTF-16BE PDF text strings without encoding."""
    remaining = limit
    for value in values:
        size: int | None
        if value.isascii():
            size = len(value)
            if size > remaining:
                size = None
        else:
            size = 2  # UTF-16BE byte-order mark.
            for character in value:
                size += 2 if ord(character) <= _UTF16_BMP_MAX else 4
                if size > remaining:
                    size = None
                    break
        if size is None:
            raise LimitError(
                limit_code,
                f"{input_label} exceeds the {limit}-byte encoded-text safety limit",
            )
        remaining -= size


def _validate_password_input(password: str | None, label: str = "password") -> None:
    """Bound password KDF input before crossing the Python/Rust boundary."""
    if password is not None:
        _validate_utf8_text_input(
            password,
            _MAX_PASSWORD_INPUT_BYTES,
            limit_code="password_input_size",
            input_label=label,
        )


#: Map pymupdf-style Python metadata keys to PDF Info keys.
_METADATA_KEYS: dict[str, str] = {
    "title": "Title",
    "author": "Author",
    "subject": "Subject",
    "keywords": "Keywords",
    "creator": "Creator",
    "producer": "Producer",
    "creationDate": "CreationDate",
    "modDate": "ModDate",
    "trapped": "Trapped",
}
# Metadata keys that set_metadata ignores, so that a metadata round trip works.
_READ_ONLY_METADATA_KEYS = frozenset({"format", "encryption"})
# The catalog names that set_pagemode and set_pagelayout accept (ISO 32000-2 Table 29).
_PAGE_MODES = ("UseNone", "UseOutlines", "UseThumbs", "FullScreen", "UseOC", "UseAttachments")
_PAGE_LAYOUTS = ("SinglePage", "OneColumn", "TwoColumnLeft", "TwoColumnRight", "TwoPageLeft", "TwoPageRight")
_MARK_INFO_KEYS = ("Marked", "UserProperties", "Suspects")
_MAX_LANGUAGE_TAG_BYTES = 256
_LANGUAGE_TAG = re.compile(r"[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*", re.ASCII)
#: pymupdf's authentication-code bit for a matching owner password.
_OWNER_PASSWORD_CODE = 4
#: MuPDF's permissions with every flag granted: 0xFFFFFFFC as a signed 32-bit integer.
_ALL_PERMISSIONS = -4


def _authentication_code(password: str, *, user: bool, owner: bool) -> int:
    """Combine password matches into pymupdf's code, as MuPDF does.

    An empty owner password counts only when the user password is empty too.
    """
    code = (2 if user else 0) | (_OWNER_PASSWORD_CODE if owner else 0)
    if not password and code == _OWNER_PASSWORD_CODE:
        return 0
    return code


def _catalog_name(value: str, valid: tuple[str, ...], key: str) -> str:
    """Match a catalog name as pymupdf does: case-insensitively, slash optional."""
    if isinstance(value, str):
        text = value.removeprefix("/").lower()
        for name in valid:
            if name.lower() == text:
                return name
    msg = f"bad {key} value: {value!r} (valid: {list(valid)})"
    raise PdfError(msg)


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
            needs_password = doc.is_encrypted()
            if needs_password and password is not None:
                doc = _Document.load_bytes(data, password, *limit_args)
        elif path is not None:
            doc = _Document.load(path, None, *limit_args)
            needs_password = doc.is_encrypted()
            if needs_password and password is not None:
                doc = _Document.load(path, password, *limit_args)
        else:
            doc = _Document(
                resolved_limits.max_text_size,
                resolved_limits.max_interpretation_size,
                resolved_limits.max_text_glyphs,
            )
            needs_password = False
        self._doc = doc
        self._closed = False
        self._path = None if path is None else Path(path)
        self._fallback_configured = False
        # Structural generation; changes invalidate existing Page views.
        self._generation = 0
        # Object-numbering epoch; renumbering or reloading the objects
        # invalidates existing low-level object handles.
        self._object_epoch = 0
        # Whether opening initially required a password; remains true after auth.
        self._needs_password = needs_password
        # The edit revision without unsaved changes; a new document starts
        # with changes, as in pymupdf.
        self._clean_revision = None if data is None and path is None else doc.edit_revision()
        # The authentication code of the password that opened the document,
        # which decides its permissions.
        self._access_code = self._original_password_code(password if needs_password and password is not None else "")
        # Retain input only while an undecrypted document may need reopening.
        self._source_path = path if self._doc.is_encrypted() else None
        self._source_bytes = data if self._doc.is_encrypted() else None
        self._emit_warnings()

    @property
    def needs_password(self) -> bool:
        """Return whether opening required a password; it stays true after authentication."""
        self._ensure_not_closed()
        return self._needs_password

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
    def is_encrypted(self) -> bool:
        """Return whether the document still requires authentication."""
        self._ensure_not_closed()
        return self._doc.is_encrypted()

    @property
    def is_repaired(self) -> bool:
        """Return whether opening recovered damaged cross-reference data."""
        self._ensure_not_closed()
        return self._doc.is_repaired()

    @property
    def is_closed(self) -> bool:
        """Return whether :meth:`close` was called."""
        return self._closed

    def _original_password_code(self, password: str) -> int:
        """Return pymupdf's code for a password of a decrypted document, else 0."""
        if self._doc.is_encrypted() or not self._doc.was_encrypted():
            return 0
        user, owner = self._doc.check_original_password(password)
        return _authentication_code(password, user=user, owner=owner)

    @property
    def is_dirty(self) -> bool:
        """Return whether the document was edited since it was opened.

        As in pymupdf, every edit counts, even one that leaves the content as
        it was; a document created empty starts edited; and saving never
        resets it. Save options that change the in-memory document, such as
        ``garbage`` and ``deflate``, do not count as edits.
        """
        self._ensure_not_closed()
        return self._clean_revision != self._doc.edit_revision()

    @property
    def permissions(self) -> int:
        """Return the document's permission flags as pymupdf does.

        A document that still needs a password has none, ``0``. An
        unencrypted document, or one opened or authenticated with its owner
        password, has all, ``-4`` (0xFFFFFFFC as a signed 32-bit integer).
        Otherwise the value is the encryption's ``/P`` entry, likewise a
        signed 32-bit integer. Test a flag with :class:`Permissions`, as in
        ``doc.permissions & Permissions.PRINT``.
        """
        self._ensure_not_closed()
        if self._doc.is_encrypted():
            return 0
        if self._access_code & _OWNER_PASSWORD_CODE:
            return _ALL_PERMISSIONS
        value = self._doc.encryption_permissions()
        return _ALL_PERMISSIONS if value is None else value

    def authenticate(self, password: str) -> int:
        """Authenticate and decrypt with a password.

        Return pymupdf's codes: 0 for failure, 1 for an unencrypted document,
        2 for a matching user password, 4 for a matching owner password, and 6
        when both match. A document that opened with an empty user password
        is still checked against its encryption, as in pymupdf, and an empty
        owner password counts only when the user password is empty too.
        Password input is capped at 127 UTF-8 bytes before the encryption KDF.
        """
        self._ensure_not_closed()
        if not self._doc.is_encrypted():
            if not self._doc.was_encrypted():
                return 1
            _validate_password_input(password)
            code = self._original_password_code(password)
            if code:
                self._access_code = code
            return code
        _validate_password_input(password)
        code = _authentication_code(
            password,
            user=self._doc.authenticate_user_password(password),
            owner=self._doc.authenticate_owner_password(password),
        )
        if code == 0:
            return 0
        # Reopen with the password so objects inside object streams are readable.
        limit_args = _document_limit_args(self._limits)
        if self._source_path is not None:
            self._doc = _Document.load(self._source_path, password, *limit_args)
        elif self._source_bytes is not None:
            self._doc = _Document.load_bytes(self._source_bytes, password, *limit_args)
        self._source_path = None
        self._source_bytes = None
        self._clean_revision = self._doc.edit_revision()
        self._access_code = code
        self._object_epoch += 1
        self._emit_warnings()
        return code

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
    def metadata(self) -> DocumentMetadata:
        """Return pymupdf's metadata dictionary.

        ``format`` is the PDF version, such as ``"PDF 1.7"``; the nine
        standard Info fields read as ``""`` when absent; and ``encryption``
        describes the encryption the document was opened with, as in
        ``"Standard V5 R6 256-bit AES"``, or is ``None``. Reads reject more
        than 1 MiB of aggregate encoded or returned text.
        """
        self._ensure_open()
        raw = self._doc.get_metadata()
        return {
            "format": f"PDF {self._doc.version()}",
            "title": raw.get("Title", ""),
            "author": raw.get("Author", ""),
            "subject": raw.get("Subject", ""),
            "keywords": raw.get("Keywords", ""),
            "creator": raw.get("Creator", ""),
            "producer": raw.get("Producer", ""),
            "creationDate": raw.get("CreationDate", ""),
            "modDate": raw.get("ModDate", ""),
            "trapped": raw.get("Trapped", ""),
            "encryption": self._doc.encryption_method(),
        }

    def set_metadata(self, m: MetadataUpdate | DocumentMetadata) -> None:
        """Set metadata with pymupdf's meaning.

        Keys match :attr:`metadata`; an unknown key raises ``ValueError``, and
        the read-only ``format`` and ``encryption`` keys are ignored, so
        ``doc.set_metadata(doc.metadata)`` works. A false value (``None`` or
        ``""``) or the text ``"none"`` or ``"null"`` removes its entry, and an
        empty dictionary removes the Info dictionary. ``trapped`` writes the
        name ``/True``, ``/False``, or ``/Unknown`` for those values. The
        fields share 1 MiB input and encoded-text limits. All updates are
        validated before PyO3 copying and applied atomically. Caller refusals
        use ``LimitError.code == "metadata_input_size"``.
        """
        self._ensure_open()
        if not isinstance(m, dict):
            msg = f"metadata must be a dict: {type(m).__name__}"
            raise TypeError(msg)
        unknown = sorted(key for key in m if key not in _METADATA_KEYS and key not in _READ_ONLY_METADATA_KEYS)
        if unknown:
            msg = f"unknown metadata key(s): {unknown} (valid: {sorted(_METADATA_KEYS)})"
            raise ValueError(msg)
        if not m:
            self._doc.remove_metadata()
            return
        updates: list[tuple[str, str]] = []
        for key, value in m.items():
            pdf_key = _METADATA_KEYS.get(key)
            if pdf_key is None:
                continue
            if value is None or value in {"none", "null"}:
                value = ""  # noqa: PLW2901 - an empty value removes the entry.
            if not isinstance(value, str):
                msg = f"metadata value must be a string or None: {key!r}={value!r}"
                raise TypeError(msg)
            updates.append((pdf_key, value))
        values = [value for _, value in updates]
        _validate_cumulative_utf8_input(
            values,
            _MAX_METADATA_INPUT_TEXT_SIZE,
            limit_code="metadata_input_size",
            input_label="Info metadata input",
        )
        _validate_cumulative_pdf_text_input(
            (value for value in values if value),
            _MAX_METADATA_INPUT_TEXT_SIZE,
            limit_code="metadata_input_size",
            input_label="encoded Info metadata input",
        )
        self._doc.set_metadata_batch(updates)

    def get_xml_metadata(self, *, max_size: int | None = _DEFAULT_MAX_XMP_METADATA_SIZE) -> str:
        """Return the catalog's XMP metadata as text, or ``""`` when absent.

        As in pymupdf, the decoded stream is read as UTF-8 with invalid bytes
        replaced and ends at its first NUL character. ``max_size`` bounds
        every decoding layer and defaults to 1 MiB; pass ``None`` only to
        accept unbounded materialization. Refusals use
        ``LimitError.code == "xmp_metadata_size"``.
        """
        self._ensure_open()
        _validate_optional_positive_int("max_size", max_size)
        data = self._doc.xml_metadata(max_size)
        if data is None:
            return ""
        return data.decode("utf-8", errors="replace").split("\0", 1)[0]

    def set_xml_metadata(self, metadata: str, *, max_size: int | None = _DEFAULT_MAX_XMP_METADATA_SIZE) -> None:
        """Store ``metadata`` as the catalog's XMP metadata, encoded as UTF-8.

        As in pymupdf, an existing ``/Metadata`` stream is rewritten in place
        and otherwise a new one is added. The stream stays unfiltered, also
        when saving with ``deflate=True``, so that tools scanning the file for
        XMP packets find it. :meth:`set_metadata` does not change XMP.
        ``max_size`` bounds the encoded input and defaults to 1 MiB, the
        default that :meth:`get_xml_metadata` reads back. Refusals use
        ``LimitError.code == "xmp_metadata_input_size"``.
        """
        self._ensure_open()
        if not isinstance(metadata, str):
            msg = f"metadata must be a str: {type(metadata).__name__}"
            raise TypeError(msg)
        _validate_utf8_text_input(
            metadata,
            max_size,
            limit_code="xmp_metadata_input_size",
            input_label="XMP metadata input",
        )
        self._doc.set_xml_metadata(metadata.encode("utf-8"), max_size)

    def del_xml_metadata(self) -> None:
        """Remove the catalog's ``/Metadata`` entry, as pymupdf does.

        The stream itself stays until a ``garbage`` save removes it.
        """
        self._ensure_open()
        self._doc.remove_xml_metadata()

    @property
    def pagemode(self) -> str:
        """Return the catalog's ``/PageMode`` name, ``"UseNone"`` when unset."""
        self._ensure_open()
        value = self.catalog.get("PageMode")
        return str(value) if isinstance(value, Name) else "UseNone"

    def set_pagemode(self, pagemode: str) -> bool:
        """Set ``/PageMode`` and return ``True``.

        As in pymupdf, the name matches case-insensitively with or without a
        leading slash: ``UseNone``, ``UseOutlines``, ``UseThumbs``,
        ``FullScreen``, ``UseOC``, or ``UseAttachments``. Another value raises
        :class:`PdfError`, a ``ValueError``.
        """
        self._ensure_open()
        self._doc.set_catalog_name("PageMode", _catalog_name(pagemode, _PAGE_MODES, "PageMode"))
        return True

    @property
    def pagelayout(self) -> str:
        """Return the catalog's ``/PageLayout`` name, ``"SinglePage"`` when unset."""
        self._ensure_open()
        value = self.catalog.get("PageLayout")
        return str(value) if isinstance(value, Name) else "SinglePage"

    def set_pagelayout(self, pagelayout: str) -> bool:
        """Set ``/PageLayout`` and return ``True``.

        As in pymupdf, the name matches case-insensitively with or without a
        leading slash: ``SinglePage``, ``OneColumn``, ``TwoColumnLeft``,
        ``TwoColumnRight``, ``TwoPageLeft``, or ``TwoPageRight``. Another
        value raises :class:`PdfError`, a ``ValueError``.
        """
        self._ensure_open()
        self._doc.set_catalog_name("PageLayout", _catalog_name(pagelayout, _PAGE_LAYOUTS, "PageLayout"))
        return True

    @property
    def language_tag(self) -> str | None:
        """Return the catalog's ``/Lang`` language tag as stored, or ``None``.

        The complete BCP 47 tag is returned, such as ``"zh-TW"`` or
        ``"sr-Latn-RS"``. pymupdf's ``language`` keeps at most three letters
        and maps Chinese tags to ``zh-Hans`` or ``zh-Hant``, so pylopdf
        replaces it under this name.
        """
        self._ensure_open()
        value = self.catalog.get("Lang")
        return value.text if isinstance(value, String) else None

    def set_language_tag(self, tag: str | None) -> None:
        """Store ``tag`` as the catalog's ``/Lang``, or remove it for ``None``.

        The tag is written unchanged and must be a well-formed BCP 47 tag
        (ISO 32000-2 §14.9.2.2) of at most 256 bytes: hyphen-separated
        subtags of one to eight ASCII letters or digits, the first of letters
        only. Anything else raises :class:`PdfError`, a ``ValueError``.
        """
        self._ensure_open()
        if tag is not None and not _is_language_tag(tag):
            msg = f"not a well-formed BCP 47 language tag: {tag!r}"
            raise PdfError(msg)
        self._doc.set_language_tag(tag)

    @property
    def markinfo(self) -> MarkInfo:
        """Return the catalog's ``/MarkInfo`` flags.

        A document without a ``/MarkInfo`` dictionary returns ``{}``.
        Otherwise ``Marked``, ``UserProperties``, and ``Suspects`` are all
        present, each ``True`` only when the dictionary holds ``true``.
        """
        self._ensure_open()
        mark_info = self.catalog.get("MarkInfo")
        if not isinstance(mark_info, Dictionary):
            return {}
        return {
            "Marked": mark_info.get("Marked") is True,
            "UserProperties": mark_info.get("UserProperties") is True,
            "Suspects": mark_info.get("Suspects") is True,
        }

    def set_markinfo(self, markinfo: MarkInfo) -> bool:
        """Replace ``/MarkInfo`` with pymupdf's meaning and return ``True``.

        Keys missing from ``markinfo`` are written as ``false``, whatever the
        document held before. Values are booleans or the strings ``"true"``
        and ``"false"`` in any case. An empty or non-dictionary argument
        changes nothing and returns ``False``; an unknown key or another
        value raises ``ValueError``.
        """
        self._ensure_open()
        if not markinfo or not isinstance(markinfo, dict):
            return False
        unknown = sorted(str(key) for key in markinfo if key not in _MARK_INFO_KEYS)
        if unknown:
            msg = f"bad MarkInfo key(s): {unknown} (valid: {list(_MARK_INFO_KEYS)})"
            raise ValueError(msg)
        flags = dict.fromkeys(_MARK_INFO_KEYS, False)
        for key, value in markinfo.items():
            text = str(value).lower()
            if text not in {"true", "false"}:
                msg = f"bad MarkInfo value for {key!r}: {value!r}"
                raise ValueError(msg)
            flags[key] = text == "true"
        self._doc.set_mark_info(flags["Marked"], flags["UserProperties"], flags["Suspects"])
        return True

    def has_links(self) -> bool:
        """Return whether any page lists a link annotation."""
        self._ensure_open()
        return self._doc.has_annotation_kind(links=True)

    def has_annots(self) -> bool:
        """Return whether any page lists an annotation other than a link or widget."""
        self._ensure_open()
        return self._doc.has_annotation_kind(links=False)

    def to_markdown(
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
        work after adding a layer with :meth:`Page.insert_ocr_text_layer`.
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
            layout = self._page_layout(page_number, "dict")
            page = Page(self, page_number - 1)
            tables = [] if table_strategy is None else _markdown_page_tables(page, table_strategy)
            bboxes = [(table.bbox.x0, table.bbox.y0, table.bbox.x1, table.bbox.y1) for table in tables]
            size_counts.update(_markdown.collect_sizes([layout], [bboxes]))
        levels = _markdown.heading_levels(size_counts)

        rendered: list[str] = []
        output_bytes = 0
        for page_number in page_numbers:
            layout = self._page_layout(page_number, "dict")
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
            words = self._page_layout(page_number, "words") if tables else None
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

    def get_form_fields(self) -> list[FormFieldInfo]:
        """Return AcroForm fields.

        Each item is ``{"name", "type", "value"}``. ``name`` is the fully
        qualified dotted name; ``type`` is text, checkbox, radio, button,
        combobox, listbox, or signature; and ``value`` is the current value.
        Button values are appearance state names such as ``"Yes"`` or ``"Off"``.
        Reads reject partial output above 4,096 entries/nodes, 8,192 edges,
        64 levels, 1 MiB of field names or values, or 4,096 choice-value items.
        """
        self._ensure_open()
        return [
            {"name": name, "type": cast("FormFieldType", kind), "value": value}
            for name, kind, value in self._doc.get_form_fields()
        ]

    def set_form_field(  # noqa: C901, PLR0912, PLR0913 - Keep validation and font bounds adjacent.
        self,
        name: str,
        value: str | bool,  # noqa: FBT001 - Match pymupdf's bool API.
        *,
        font: Font | None = None,
        fontfile: str | os.PathLike[str] | None = None,
        fontbuffer: bytes | None = None,
        fontindex: int = 0,
        font_language: FontLanguage | None = None,
        max_font_size: int | None = _DEFAULT_MAX_FONT_INPUT_SIZE,
    ) -> None:
        """Set an AcroForm field value and regenerate its widget appearance.

        Pass strings for text/choice fields. For checkboxes and radio buttons,
        pass an appearance state such as ``"Yes"``/``"Off"`` or a bool. ``True``
        resolves the on state from widget appearances; ``False`` becomes
        ``"Off"``. Text, choice, checkbox, and radio appearances render in
        pylopdf as well as external viewers. Existing non-empty button
        appearances are preserved; missing ones use native vector marks.
        Missing appearances on other WinAnsi fields are completed in the same
        operation, and ``NeedAppearances`` is cleared once every fillable widget
        has a usable normal appearance. Comb text fields honor inherited
        ``MaxLen`` and alignment, center Unicode graphemes in their positions,
        and reject overlength values without changing the document. Field-tree
        and 1 MiB name/value input limits are checked without leaving a partial
        update; caller input refusals use ``form_field_input_size``. Text and
        choice appearances stop at 4,096 layout lines. Button handling
        additionally caps widgets, appearance states, and state names before
        resolving booleans or generating missing appearances.

        WinAnsi text uses Helvetica. Pass exactly one of ``fontfile`` or
        ``fontbuffer`` to subset-embed an arbitrary OpenType font, a variable
        font at its default instance. Pass a :class:`Font` with an OpenType
        face as ``font`` to draw with a face loaded once, such as the bold
        instance that ``Font(fontfile=..., weight=700)`` selects on a variable
        font's weight axis; text is fitted, aligned, and placed in comb
        positions with the advances that :meth:`Font.text_length` returns.
        ``font`` cannot be combined with ``fontfile``, ``fontbuffer``,
        ``fontindex``, or ``font_language``, and a Standard 14 :class:`Font`
        raises :class:`PdfError`. Locale font extras can supply Unicode
        appearances automatically; use ``font_language`` for ambiguous Han
        text. Otherwise the value is still
        stored with ``NeedAppearances`` for viewer compatibility, but its
        appearance cannot be rendered by pylopdf.
        Explicit and automatically selected font input defaults to a 64 MiB
        boundary; ``max_font_size=None`` opts trusted workloads out. Refusal
        raises :class:`LimitError` with code ``font_input_size``.
        Rich text, pushbuttons, and signature fields are unsupported; use the
        pyHanko integration for signatures.
        """
        self._ensure_open()
        _validate_optional_positive_int("max_font_size", max_font_size)
        if not isinstance(name, str):
            msg = f"name must be a string: {type(name).__name__}"
            raise TypeError(msg)
        if not name:
            msg = "name must be at least 1 character"
            raise ValueError(msg)
        _validate_utf8_text_input(
            name,
            _MAX_FORM_FIELD_INPUT_SIZE,
            limit_code="form_field_input_size",
            input_label="form field name",
        )
        if not isinstance(value, (str, bool)):
            msg = f"value must be a string or bool: {value!r}"
            raise TypeError(msg)
        if isinstance(value, str):
            _validate_utf8_text_input(
                value,
                _MAX_FORM_FIELD_INPUT_SIZE,
                limit_code="form_field_input_size",
                input_label="form field value",
            )
        core_font = _validate_form_font(font, fontfile, fontbuffer, fontindex, font_language)
        normalized_font_language = _normalize_font_language(font_language)
        font_data = _resolve_font_source(fontfile, fontbuffer, max_font_size)
        package_font: tuple[FontLanguage, str] | None = None
        if font_data is not None:
            if normalized_font_language is not None:
                msg = "font_language cannot be combined with fontfile or fontbuffer"
                raise ValueError(msg)
            if isinstance(fontindex, bool) or not isinstance(fontindex, int) or not 0 <= fontindex <= _UINT32_MAX:
                msg = f"fontindex must be an integer from 0 through 4294967295: {fontindex!r}"
                raise ValueError(msg)
        elif fontindex != 0:
            msg = "fontindex requires fontfile or fontbuffer"
            raise ValueError(msg)
        if isinstance(value, bool):
            if value:
                states = self._doc.form_button_states(name)
                on_states = [s for s in states if s != "Off"]
                resolved = on_states[0] if on_states else "Yes"
            else:
                resolved = "Off"
        else:
            resolved = value
            if font_data is None and core_font is None:
                bundled_font = _form_generation_font(resolved, normalized_font_language)
                if bundled_font is not None:
                    font_data, package_font = bundled_font
        if core_font is not None:
            self._doc.set_form_field_font(name, resolved, core_font)
            return
        weight = _package_face_weight(package_font)
        if isinstance(font_data, Path):
            self._doc.set_form_field_file(
                name, resolved, str(font_data), fontindex, max_font_size, package_font, weight
            )
        else:
            self._doc.set_form_field(name, resolved, font_data, fontindex, max_font_size, weight)

    def get_page_labels(self) -> list[PageLabelInfo]:
        """Read page-label definitions.

        Each item has ``startpage``, ``style``, ``prefix``, and
        ``firstpagenum``. ``startpage`` is zero-based; ``style`` is
        ``"D"``, ``"R"``, ``"r"``, ``"A"``, ``"a"``, or empty for prefix only.
        Use :meth:`Page.get_label` for a page's rendered label. Number-tree reads
        reject partial output above 4,096 entries/nodes, 32 levels, or 1 MiB of
        encoded or decoded style/prefix text.
        """
        self._ensure_open()
        return [
            {
                "startpage": int(start),
                "style": style or "",
                "prefix": prefix or "",
                "firstpagenum": int(first),
            }
            for start, style, prefix, first in self._doc.get_page_labels()
        ]

    def set_page_labels(self, labels: Sequence[PageLabelSpec]) -> None:
        """Set page labels in :meth:`get_page_labels` format; empty removes all.

        The PDF specification requires the first range to start at page 0.
        ``firstpagenum`` defaults to 1 for each range.
        Aggregate style and prefix input plus encoded PDF text stop at 1 MiB
        before PyO3 copying with ``page_label_input_size``.
        """
        self._ensure_open()
        if len(labels) > _MAX_PAGE_LABEL_RANGES:
            msg = f"labels cannot contain more than {_MAX_PAGE_LABEL_RANGES} ranges"
            raise ValueError(msg)
        payload: list[tuple[int, str | None, str | None, int]] = []
        seen: set[int] = set()
        for label in labels:
            start = int(label.get("startpage", -1))
            style = str(label.get("style", ""))
            prefix = str(label.get("prefix", ""))
            first = int(label.get("firstpagenum", 1))
            if start < 0 or start in seen:
                msg = f"startpage must be >= 0 and unique: {label!r}"
                raise ValueError(msg)
            if style not in _PAGE_LABEL_STYLES:
                msg = f"style must be one of {sorted(_PAGE_LABEL_STYLES)}: {style!r}"
                raise ValueError(msg)
            if first < 1:
                msg = f"firstpagenum must be >= 1: {label!r}"
                raise ValueError(msg)
            seen.add(start)
            payload.append((start, style or None, prefix or None, first))
        if payload and min(seen) != 0:
            msg = "the first page label range must start at startpage 0 (PDF spec requirement)"
            raise ValueError(msg)
        payload.sort(key=lambda item: item[0])
        _validate_cumulative_utf8_input(
            (
                text
                for _, style, prefix, _ in payload
                for text in ("" if style is None else style, "" if prefix is None else prefix)
            ),
            _MAX_PAGE_LABEL_INPUT_TEXT_SIZE,
            limit_code="page_label_input_size",
            input_label="page-label text input",
        )
        _validate_cumulative_pdf_text_input(
            (
                text
                for _, style, prefix, _ in payload
                for text in ("" if style is None else style, "" if prefix is None else prefix)
            ),
            _MAX_PAGE_LABEL_INPUT_TEXT_SIZE,
            limit_code="page_label_input_size",
            input_label="encoded page-label text input",
        )
        self._doc.set_page_labels(payload)

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

    def get_pdfa_claim(
        self,
        *,
        max_size: int | None = _DEFAULT_MAX_XMP_METADATA_SIZE,
    ) -> tuple[int, str] | None:
        """Read the XMP PDF/A claim from ``pdfaid:part`` and conformance.

        Return ``(part, conformance)``, for example ``(2, "B")`` for a
        PDF/A-2b claim. PDF/A-4 without conformance uses an empty string. Return
        ``None`` when absent. This reads a self-declaration; it does not validate
        compliance. Use veraPDF or another external validator. ``max_size``
        bounds every XMP decoding layer and defaults to 1 MiB. Pass a larger
        positive integer for known large metadata or ``None`` only when
        intentionally accepting unbounded materialization.
        """
        self._ensure_open()
        _validate_optional_positive_int("max_size", max_size)
        claim = self._doc.pdfa_claim(max_size)
        return None if claim is None else (int(claim[0]), claim[1])

    def get_text(self, pages: Iterable[int] | None = None) -> str:
        """Extract plain text from the given pages in one batched call.

        ``pages`` is a sequence of zero-based page numbers extracted in the
        given order; ``None`` means every page, negative values count from the
        end, and duplicates are allowed. An explicit sequence may contain up to
        the larger of the page count and 4,096 entries; further entries raise
        :class:`LimitError` with code ``page_entry_count``. Pages are processed
        in chunks of 4,096 entries, each sharing one interpreter font cache, so
        a font reused across pages is parsed once per chunk rather than once
        per page; the returned text equals joining single-page
        :meth:`Page.get_text` output in the same order.
        """
        count = self._page_count()
        page_numbers = list(range(1, count + 1)) if pages is None else self._page_entries(pages, "pages", count)
        text = self._doc.extract_text(page_numbers)
        self._emit_warnings()
        return text

    @overload
    def _page_text(self, pno: int, option: Literal["text"] = "text") -> str: ...
    @overload
    def _page_text(self, pno: int, option: Literal["words"]) -> list[Word]: ...
    @overload
    def _page_text(self, pno: int, option: Literal["blocks"]) -> list[Block]: ...
    @overload
    def _page_text(self, pno: int, option: Literal["dict"]) -> TextPageDict: ...
    @overload
    def _page_text(self, pno: int, option: Literal["rawdict"]) -> RawTextPageDict: ...
    def _page_text(
        self, pno: int, option: str = "text"
    ) -> str | list[Word] | list[Block] | TextPageDict | RawTextPageDict:
        """Extract text from zero-based page ``pno`` for :meth:`Page.get_text`."""
        if option == "text":
            text = self._doc.extract_text([self._lopdf_page_number(pno)])
            self._emit_warnings()
            return text
        if option == "rawdict":
            return self._get_page_rawdict(pno)
        return self._page_layout(self._lopdf_page_number(pno), option)

    @overload
    def _page_layout(self, page_number: int, option: Literal["words"]) -> list[Word]: ...
    @overload
    def _page_layout(self, page_number: int, option: Literal["dict"]) -> TextPageDict: ...
    @overload
    def _page_layout(self, page_number: int, option: str) -> list[Word] | list[Block] | TextPageDict: ...
    def _page_layout(self, page_number: int, option: str) -> list[Word] | list[Block] | TextPageDict:
        """Build words, blocks, or dict layout for a validated one-based page."""
        width, height, blocks = self._doc.extract_layout(page_number)
        # Attribute warnings to the public caller rather than this helper.
        self._emit_warnings(stacklevel=4)
        if option == "words":
            words: list[Word] = []
            for bno, (_, lines) in enumerate(blocks):
                for lno, (_, _, line_words, _, _) in enumerate(lines):
                    words.extend(
                        Word(x0, y0, x1, y1, text, bno, lno, wno)
                        for wno, ((x0, y0, x1, y1), text) in enumerate(line_words)
                    )
            return words
        if option == "blocks":
            return [
                Block(
                    x0,
                    y0,
                    x1,
                    y1,
                    "\n".join(" ".join(text for _, text in line_words) for _, _, line_words, _, _ in lines),
                    bno,
                    0,
                )
                for bno, ((x0, y0, x1, y1), lines) in enumerate(blocks)
            ]
        if option == "dict":
            return {
                "width": width,
                "height": height,
                "blocks": [
                    {
                        "number": bno,
                        "type": 0,
                        "bbox": bbox,
                        "lines": [
                            {
                                "bbox": line_bbox,
                                "wmode": writing_mode,
                                "dir": direction,
                                "spans": [
                                    {
                                        "bbox": span_bbox,
                                        "origin": origin,
                                        "size": size,
                                        "font": font,
                                        "flags": flags,
                                        "text": text,
                                    }
                                    for span_bbox, text, size, origin, font, flags in spans
                                ],
                            }
                            for line_bbox, spans, _, direction, writing_mode in lines
                        ],
                    }
                    for bno, (bbox, lines) in enumerate(blocks)
                ],
            }
        msg = f"option must be one of 'text' / 'words' / 'blocks' / 'dict' / 'rawdict': {option!r}"
        raise ValueError(msg)

    def _get_page_rawdict(self, pno: int) -> RawTextPageDict:
        """Materialize glyph mappings only for an explicitly requested raw layout."""
        width, height, blocks = self._doc.extract_raw_layout(self._lopdf_page_number(pno))
        self._emit_warnings()
        return {
            "width": width,
            "height": height,
            "blocks": [
                {
                    "number": bno,
                    "type": 0,
                    "bbox": bbox,
                    "lines": [
                        {
                            "bbox": line_bbox,
                            "wmode": writing_mode,
                            "dir": direction,
                            "spans": [
                                {
                                    "bbox": span_bbox,
                                    "origin": origin,
                                    "size": size,
                                    "font": font,
                                    "flags": flags,
                                    "chars": [
                                        {"c": text, "bbox": char_bbox, "origin": char_origin}
                                        for text, char_bbox, char_origin in chars
                                    ],
                                }
                                for span_bbox, chars, size, origin, font, flags in spans
                            ],
                        }
                        for line_bbox, spans, direction, writing_mode in lines
                    ],
                }
                for bno, (bbox, lines) in enumerate(blocks)
            ],
        }

    def get_outline(self) -> list[OutlineEntry]:
        """Return the outline (bookmarks) as :class:`OutlineEntry` items.

        Each item is ``(level, title, page)``: levels start at 1, and ``page``
        is zero-based like every other pylopdf page number. This replaces
        pymupdf's ``get_toc()``, whose page numbers are one-based. Items whose
        destination does not resolve to a page are left out. Return an empty
        list when no outline exists. Traversal is cycle-aware and rejects more
        than 65,536 nodes or entries or 131,072 edges with
        ``LimitError.code == "toc_entry_count"``, more than 16 MiB of
        source/returned text with ``"toc_text_size"``, and more than 64 levels
        or 32 destination indirections. Named destinations resolve under the
        same name-tree limits as :meth:`Page.get_links`.
        """
        self._ensure_open()
        return [OutlineEntry(level, title, page - 1) for level, title, page in self._doc.get_toc()]

    def set_outline(self, entries: Sequence[OutlineEntry | Sequence[int | str]], /) -> None:
        """Replace the outline from ``(level, title, page)`` items.

        ``page`` is zero-based, as :meth:`get_outline` returns it; this
        replaces pymupdf's ``set_toc()``, whose page numbers are one-based,
        and editing the list replaces ``set_toc_item()`` and
        ``del_toc_item()``. An empty sequence removes the outline. Levels start
        at 1 and can increase by at most one from the previous item. At most
        65,536 items, 64 levels, and 16 MiB each of input and encoded title
        text are accepted, enough for large manuals. Validation is atomic.
        Refusals occur before PyO3 copying with ``LimitError.code ==
        "toc_entry_count"`` for items or ``"toc_input_size"`` for title text.
        """
        self._ensure_open()
        if len(entries) > _MAX_TOC_ENTRIES:
            limit_code = "toc_entry_count"
            msg = f"the outline cannot contain more than {_MAX_TOC_ENTRIES} entries"
            raise LimitError(limit_code, msg)
        count = self._page_count()
        prepared: list[tuple[int, str, int]] = []
        previous_level = 0
        for i, item in enumerate(entries):
            try:
                level_raw, title, page_raw = item
                level = int(level_raw)
                page = int(page_raw)
            except (TypeError, ValueError) as exc:
                msg = f"entries[{i}] must be (level, title, page): {item!r}"
                raise ValueError(msg) from exc
            if level < 1 or level > previous_level + 1:
                msg = f"entries[{i}] has an invalid level {level} (must be >= 1 and at most the previous level + 1)"
                raise ValueError(msg)
            if not 0 <= page < count:
                msg = f"entries[{i}] page {page} is out of range (0..{count - 1})"
                raise ValueError(msg)
            prepared.append((level, str(title), page + 1))
            previous_level = level
        titles = [title for _, title, _ in prepared]
        _validate_cumulative_utf8_input(
            titles,
            _MAX_TOC_INPUT_TEXT_SIZE,
            limit_code="toc_input_size",
            input_label="outline title input",
        )
        _validate_cumulative_pdf_text_input(
            titles,
            _MAX_TOC_INPUT_TEXT_SIZE,
            limit_code="toc_input_size",
            input_label="encoded outline title input",
        )
        self._doc.set_toc(prepared)

    def set_fallback_font(
        self,
        font: bytes | str | os.PathLike[str] | None,
        kind: str = "sans",
        index: int = 0,
        *,
        font_language: CjkFontLanguage = "ja",
        max_font_size: int | None = _DEFAULT_MAX_FONT_INPUT_SIZE,
    ) -> None:
        """Set a fallback font for rendering non-embedded CJK fonts.

        ``font`` is a TTF/OTF/TTC path or bytes. ``None`` clears the setting and
        disables automatic locale-font discovery. ``kind`` is ``"sans"``
        (default) or ``"serif"``; ``index`` selects a TTC face.
        ``font_language`` selects the Adobe-Japan1, Korea1, GB1, or CNS1 slot.
        Font input defaults to a 64 MiB boundary; ``max_font_size=None`` opts
        trusted workloads out. Refusal raises :class:`LimitError` with code
        ``font_input_size``.
        """
        self._ensure_open()
        _validate_optional_positive_int("max_font_size", max_font_size)
        normalized_language = _normalize_cjk_font_language(font_language)
        if font is None:
            self._doc.clear_fallback_fonts()
            self._fallback_configured = True
            return
        if isinstance(font, bytes):
            if max_font_size is not None and len(font) > max_font_size:
                raise _font_input_limit_error(len(font), max_font_size)
            self._doc.set_fallback_font(kind, font, index, max_font_size, normalized_language)
        else:
            self._doc.set_fallback_font_file(kind, str(Path(font)), index, max_font_size, normalized_language)
        self._fallback_configured = True

    def _ensure_fallback_fonts(self) -> None:
        """Auto-configure installed locale fonts unless explicitly configured."""
        if self._fallback_configured:
            return
        # Look the providers up through their module, where tests replace them.
        providers = tuple(
            provider
            for provider in _fonts._bundled_font_providers()
            if provider.language in {"ja", "ko", "zh-CN", "zh-TW"}
        )
        if all(isinstance(provider.sans, Path) and isinstance(provider.serif, Path) for provider in providers):
            self._doc.set_fallback_font_locale_files(
                [(provider.language, str(provider.sans), str(provider.serif)) for provider in providers],
                _DEFAULT_MAX_FONT_INPUT_SIZE,
            )
            self._fallback_configured = True
            return
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
        self._fallback_configured = True

    def extract_image(self, xref: int) -> ExtractedImage:
        """Return the data of image XObject ``xref``, as pymupdf does.

        The dictionary has pymupdf's keys. DCT data passes through unchanged
        with ``ext == "jpeg"``; other image data, including JPX, JBIG2, CCITT,
        and Flate, is decoded to PNG with ``ext == "png"``. The image is
        returned without its soft mask, whose xref ``smask`` names (0 when
        absent), and ``colorspace`` counts the returned image's components.
        Decoding runs under the same content and colour-space guards and
        resource limits as rendering. An xref that is not an image raises
        :class:`PdfError`.
        """
        self._ensure_open()
        if isinstance(xref, bool) or not isinstance(xref, int) or xref <= 0:
            msg = f"xref must be a positive integer: {xref!r}"
            raise ValueError(msg)
        ext, width, height, components, cs_name, bpc, smask, (xres, yres), data = self._doc.extract_image(xref)
        self._emit_warnings()
        return {
            "ext": cast("Literal['jpeg', 'png']", ext),
            "smask": smask,
            "width": width,
            "height": height,
            "colorspace": components,
            "cs-name": cs_name,
            "bpc": bpc,
            "xres": xres,
            "yres": yres,
            "image": data,
        }

    def render_pages(  # noqa: PLR0913 - batch resource controls are keyword-only.
        self,
        pages: Iterable[int] | None = None,
        *,
        matrix: Matrix | Sequence[float] | None = None,
        dpi: float | None = None,
        alpha: bool = False,
        background: tuple[int, int, int] | tuple[int, int, int, int] | None = None,
        workers: int | None = None,
        max_size: int | None = _DEFAULT_MAX_RENDER_BATCH_SIZE,
    ) -> list[bytes]:
        """Render pages to PNG concurrently while preserving input order.

        ``matrix``, ``dpi``, ``alpha``, and ``background`` have the meaning of
        :meth:`Page.get_pixmap`, so the default is opaque RGB at 72 dpi.
        ``pages`` contains zero-based page numbers and defaults to every page.
        Duplicates are allowed. ``workers=None`` uses up to four CPUs; explicit
        values must be between 1 and 64. Actual concurrency is also capped to
        roughly 512 MB of estimated live raster and conversion buffers. A
        dedicated bounded worker pool renders one immutable snapshot of the
        document while the GIL is released, replacing each worker's render
        cache after 4,096 pages. An explicit sequence may contain up to the
        larger of the page count and 4,096 entries; further entries raise
        :class:`LimitError` with code ``page_entry_count``. ``max_size`` caps
        their cumulative encoded PNG bytes and defaults to 512 MiB; pass
        ``None`` only to accept unbounded output.

        Do not edit or call other methods on this same :class:`Document` from
        another thread during the operation. Such calls are not part of the
        concurrency contract; use this method as the supported same-document
        parallel rendering boundary.
        """
        self._ensure_open()
        scale = _render_scale(matrix, dpi)
        rgba = _render_background(background, alpha=alpha)
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
        result = self._doc.render_pages_png(page_numbers, scale, alpha, rgba, workers, max_size)
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

    def save(  # noqa: PLR0913  # Save options are keyword-only, like pymupdf.
        self,
        filename: str | os.PathLike[str],
        *,
        garbage: int = 0,
        deflate: bool = False,
        use_objstms: bool = False,
        encryption: int = PDF_ENCRYPT_NONE,
        user_pw: str | None = None,
        owner_pw: str | None = None,
        permissions: int = Permissions.ALL,
    ) -> None:
        """Save to a file.

        ``garbage`` takes pymupdf's cumulative levels: 1 removes objects that
        nothing references, 2 also numbers the remaining objects
        consecutively, 3 also merges identical objects other than streams, and
        4 also merges identical streams. ``True`` and ``False`` mean 1 and 0.
        Pages, annotations, form fields, structure elements, outline items,
        and optional-content groups are never merged. ``deflate=True`` applies
        Flate compression before saving. Both mutate the document.
        ``use_objstms=True`` writes PDF 1.5+ object and xref streams, often
        reducing size and raising the version to 1.5 when necessary.

        ``encryption`` takes pymupdf's :class:`EncryptionMethod` values and
        defaults to :data:`PDF_ENCRYPT_NONE`, which writes plaintext;
        :data:`PDF_ENCRYPT_AES_256` writes AES-256 PDF 2.0 encryption while the
        in-memory document stays plaintext. The other methods raise
        :class:`PdfError`, and so does ``user_pw`` or ``owner_pw`` without
        :data:`PDF_ENCRYPT_AES_256`, instead of being ignored. ``user_pw``
        defaults to empty and ``owner_pw`` to ``user_pw``. An empty user
        password plus an owner password permits unrestricted opening with
        permission controls. Each password is capped at 127 UTF-8 bytes.
        ``permissions`` combines :class:`Permissions` and defaults to all.
        Encryption cannot be combined with object streams.
        Every mode streams to a same-directory temporary file and replaces
        ``filename`` only after a complete successful write.
        """
        self._ensure_open()
        level = _garbage_level(garbage)
        encrypt_args = self._encryption_args(encryption, user_pw, owner_pw, permissions, use_objstms=use_objstms)
        clean = not self.is_dirty
        try:
            self._save_file(filename, encrypt_args, level=level, deflate=deflate, use_objstms=use_objstms)
        finally:
            if clean:
                self._clean_revision = self._doc.edit_revision()

    def _save_file(
        self,
        filename: str | os.PathLike[str],
        encrypt_args: tuple[str, str, int] | None,
        *,
        level: int,
        deflate: bool,
        use_objstms: bool,
    ) -> None:
        """Apply the save options and write the file for :meth:`save`."""
        self._apply_save_options(garbage=level, deflate=deflate)
        writer: Callable[[str], None]
        if encrypt_args is not None:
            user, owner, perms = encrypt_args
            writer = functools.partial(
                self._doc.save_encrypted,
                user_password=user,
                owner_password=owner,
                permissions=perms,
                file_encryption_key=os.urandom(32),
            )
        elif use_objstms:
            writer = self._doc.save_with_object_streams
        else:
            writer = self._doc.save
        _atomic_save_file(filename, writer)

    def tobytes(  # noqa: PLR0913  # Save options are keyword-only, like pymupdf.
        self,
        *,
        garbage: int = 0,
        deflate: bool = False,
        use_objstms: bool = False,
        encryption: int = PDF_ENCRYPT_NONE,
        user_pw: str | None = None,
        owner_pw: str | None = None,
        permissions: int = Permissions.ALL,
        max_size: int | None = _DEFAULT_MAX_PDF_OUTPUT_SIZE,
    ) -> bytes:
        """Return bounded PDF bytes; save options have the same meaning as :meth:`save`.

        ``max_size`` limits serialization before converting the Rust buffer to
        Python ``bytes``. ``None`` explicitly opts out for trusted workloads.
        Refusal raises :class:`LimitError` with code ``pdf_output_size``.
        Encryption passwords are capped at 127 UTF-8 bytes.
        """
        self._ensure_open()
        _validate_optional_positive_int("max_size", max_size)
        level = _garbage_level(garbage)
        encrypt_args = self._encryption_args(encryption, user_pw, owner_pw, permissions, use_objstms=use_objstms)
        clean = not self.is_dirty
        try:
            return self._serialize(
                encrypt_args, level=level, deflate=deflate, use_objstms=use_objstms, max_size=max_size
            )
        finally:
            if clean:
                self._clean_revision = self._doc.edit_revision()

    def _serialize(
        self,
        encrypt_args: tuple[str, str, int] | None,
        *,
        level: int,
        deflate: bool,
        use_objstms: bool,
        max_size: int | None,
    ) -> bytes:
        """Apply the save options and serialize for :meth:`tobytes`."""
        self._apply_save_options(garbage=level, deflate=deflate)
        if encrypt_args is not None:
            user, owner, perms = encrypt_args
            return self._doc.save_bytes_encrypted(user, owner, perms, os.urandom(32), max_size)
        if use_objstms:
            return self._doc.save_bytes_with_object_streams(max_size)
        return self._doc.save_bytes(max_size)

    def _apply_save_options(self, *, garbage: int, deflate: bool) -> None:
        """Apply garbage collection and stream compression before saving."""
        if garbage:
            self._doc.collect_garbage(garbage)
            if garbage >= _GARBAGE_RENUMBERS:
                # Levels 2-4 renumber objects, so object handles go stale.
                self._object_epoch += 1
        if deflate:
            self._doc.compress()

    @staticmethod
    def _encryption_args(
        encryption: int,
        user_pw: str | None,
        owner_pw: str | None,
        permissions: int,
        *,
        use_objstms: bool,
    ) -> tuple[str, str, int] | None:
        """Validate encryption arguments, returning ``None`` when disabled."""
        if isinstance(encryption, bool) or not isinstance(encryption, int):
            msg = f"encryption must be a PDF_ENCRYPT_* value: {encryption!r}"
            raise TypeError(msg)
        if encryption == PDF_ENCRYPT_NONE:
            if user_pw is not None or owner_pw is not None:
                msg = "user_pw and owner_pw need encryption=PDF_ENCRYPT_AES_256"
                raise PdfError(msg)
            return None
        if encryption != PDF_ENCRYPT_AES_256:
            try:
                name = f"PDF_ENCRYPT_{EncryptionMethod(encryption).name}"
            except ValueError:
                name = repr(encryption)
            msg = f"encryption={name} is not supported; use PDF_ENCRYPT_NONE or PDF_ENCRYPT_AES_256"
            raise PdfError(msg)
        if use_objstms:
            msg = "encryption (user_pw / owner_pw) and use_objstms cannot both be specified"
            raise ValueError(msg)
        _validate_password_input(user_pw, "user password")
        _validate_password_input(owner_pw, "owner password")
        user = user_pw if user_pw is not None else ""
        owner = owner_pw if owner_pw is not None else user
        return (user, owner, int(permissions))

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
            msg = "this PDF is encrypted; open it with the password argument or call authenticate()"
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
    encrypted document needs ``password``, or :meth:`Document.authenticate`
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
    #: Whether the document is encrypted and the password given, if any, did not open it.
    is_encrypted: bool
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
        is_encrypted=encrypted,
        is_repaired=recovery is not None,
        metadata=MappingProxyType({name: raw[key] for name, key in _PROBE_METADATA_KEYS if key in raw}),
    )


# The package re-exports these names; reprs, pickles, and documentation name it.
for _public in (Document, Probe, open, probe):
    _public.__module__ = "pylopdf"
