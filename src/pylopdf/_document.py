"""Documents, opening and metadata probing, and atomic saving."""

from __future__ import annotations

import functools
import math
import os
import secrets
import stat
import warnings as _warnings
from collections import Counter
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast, overload

from pylopdf import _fonts, _markdown
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
from pylopdf._limits import (
    _DEFAULT_MAX_FONT_INPUT_SIZE,
    _DEFAULT_MAX_MARKDOWN_SIZE,
    _DEFAULT_MAX_RENDER_PAGE_SIZE,
    _DEFAULT_MAX_SVG_SIZE,
    _FLOAT32_MAX,
    _UINT32_MAX,
    DocumentLimits,
    _document_limit_args,
    _validate_cumulative_utf8_input,
    _validate_optional_positive_int,
    _validate_utf8_text_input,
)
from pylopdf._page import _PAGE_LABEL_STYLES, Page
from pylopdf._pixmap import _normalize_background
from pylopdf._tables import (
    _bounded_markdown_page_output,
    _markdown_output_limit_error,
    _markdown_page_tables,
    _markdown_table_output,
    _MarkdownBudget,
)
from pylopdf._types import Permissions
from pylopdf.pylopdf_core import LimitError, PdfError, _Document

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence
    from types import TracebackType
    from typing import Self

    from pylopdf._fonts import Font
    from pylopdf._types import (
        BlockEntry,
        CjkFontLanguage,
        DocumentComplexity,
        DocumentMetadata,
        FontLanguage,
        FormFieldInfo,
        FormFieldType,
        ImageCompressionResult,
        MetadataProbe,
        MetadataUpdate,
        PageLabelInfo,
        PageLabelSpec,
        RawTextPage,
        TextPage,
        WordEntry,
    )


_DEFAULT_MAX_EMBEDDED_FILE_SIZE = 64 * 1024 * 1024
_DEFAULT_MAX_XMP_METADATA_SIZE = 1024 * 1024
_DEFAULT_MAX_RENDER_BATCH_SIZE = 512 * 1024 * 1024
_DEFAULT_MAX_PDF_OUTPUT_SIZE = 512 * 1024 * 1024
_MAX_FORM_FIELD_INPUT_SIZE = 1024 * 1024
_MAX_EMBEDDED_FILE_INPUT_TEXT_SIZE = 1024 * 1024
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
_IMAGE_COMPRESSION_MIN_QUALITY = 1
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


def _atomic_save_file(
    filename: str | os.PathLike[str],
    writer: Callable[[str], None],
) -> None:
    """Write a sibling file completely before atomically replacing the target."""
    requested = Path(filename)
    try:
        target = requested.resolve(strict=False) if requested.is_symlink() else requested
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
            with suppress(OSError):
                temporary.unlink()


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
}


class Document:
    """A PDF document.

    Open from a path or byte string, or create an empty document without
    arguments. Documents are context managers and expose :class:`Page` objects
    through ``doc[i]`` and iteration.
    """

    def __init__(
        self,
        filename: str | os.PathLike[str] | None = None,
        stream: bytes | None = None,
        password: str | None = None,
        max_decompressed_size: int | None = None,
        *,
        limits: DocumentLimits | None = None,
    ) -> None:
        """Open from exactly one of a file path and byte stream, or create empty.

        PDFs with an empty user password decrypt automatically. Otherwise pass
        ``password`` or call :meth:`authenticate` after opening.
        Password input is capped at 127 UTF-8 bytes.
        ``limits`` applies structural, decompression, interpretation-snapshot,
        and interpreted-text budgets before expensive work.
        :meth:`DocumentLimits.web` is a conservative starting profile for
        untrusted uploads.
        ``max_decompressed_size`` remains as the compatible single-budget
        shorthand and cannot be combined with ``limits``.
        """
        if filename is not None and stream is not None:
            msg = "filename and stream cannot both be specified"
            raise ValueError(msg)
        _validate_password_input(password)
        if limits is not None and not isinstance(limits, DocumentLimits):
            msg = f"limits must be a DocumentLimits instance or None: {limits!r}"
            raise TypeError(msg)
        if limits is not None and max_decompressed_size is not None:
            msg = "limits and max_decompressed_size cannot both be specified"
            raise ValueError(msg)
        resolved_limits = limits or DocumentLimits(max_decompressed_size=max_decompressed_size)
        limit_args = _document_limit_args(resolved_limits)
        path = None if filename is None else os.fspath(filename)
        self._limits = resolved_limits
        if stream is not None:
            doc = _Document.load_bytes(stream, None, *limit_args)
            needs_pass = doc.is_encrypted()
            if needs_pass and password is not None:
                doc = _Document.load_bytes(stream, password, *limit_args)
        elif path is not None:
            doc = _Document.load(path, None, *limit_args)
            needs_pass = doc.is_encrypted()
            if needs_pass and password is not None:
                doc = _Document.load(path, password, *limit_args)
        else:
            doc = _Document(
                resolved_limits.max_text_size,
                resolved_limits.max_interpretation_size,
                resolved_limits.max_text_glyphs,
            )
            needs_pass = False
        self._doc = doc
        self._closed = False
        self._fallback_configured = False
        # Structural generation; changes invalidate existing Page views.
        self._generation = 0
        # Whether opening initially required a password; remains true after auth.
        self._needs_pass = needs_pass
        # Retain input only while an undecrypted document may need reopening.
        self._source_path = path if self._doc.is_encrypted() else None
        self._source_bytes = stream if self._doc.is_encrypted() else None
        self._emit_warnings()

    @property
    def needs_pass(self) -> bool:
        """Return whether opening required a password; remains true after auth."""
        self._ensure_not_closed()
        return self._needs_pass

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

    def authenticate(self, password: str) -> int:
        """Authenticate and decrypt with a password.

        Return pymupdf-compatible codes: 0 for failure, 1 when authentication is
        unnecessary, 2 for a matching user password, 4 for a matching owner
        password, and 6 when both match. Password input is capped at 127 UTF-8
        bytes before the encryption KDF.
        """
        self._ensure_not_closed()
        if not self._doc.is_encrypted():
            return 1
        _validate_password_input(password)
        code = 0
        if self._doc.authenticate_user_password(password):
            code |= 2
        if self._doc.authenticate_owner_password(password):
            code |= 4
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
        self._emit_warnings()
        return code

    @property
    def page_count(self) -> int:
        """Return the number of pages."""
        self._ensure_open()
        return self._doc.page_count()

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

    def __len__(self) -> int:
        """Return the number of pages."""
        return self.page_count

    def __getitem__(self, pno: int) -> Page:
        """Return a page by zero-based index; negative values count from the end."""
        return Page(self, self._normalize_pno(pno))

    def load_page(self, pno: int) -> Page:
        """Return ``doc[pno]`` through the pymupdf-compatible name."""
        return self[pno]

    def __iter__(self) -> Iterator[Page]:
        """Iterate over every page in order."""
        for pno in range(self.page_count):
            yield self[pno]

    def _bump_generation(self) -> None:
        """Record a structural change and invalidate existing page views."""
        self._generation += 1

    @property
    def metadata(self) -> DocumentMetadata:
        """Return title, author, subject, keywords, dates, producer, and format.

        Only the eight standard Info fields are decoded. Reads reject more than
        1 MiB of aggregate encoded or returned text.
        """
        self._ensure_open()
        raw = self._doc.get_metadata()
        return {
            "title": raw.get("Title", ""),
            "author": raw.get("Author", ""),
            "subject": raw.get("Subject", ""),
            "keywords": raw.get("Keywords", ""),
            "creator": raw.get("Creator", ""),
            "producer": raw.get("Producer", ""),
            "creationDate": raw.get("CreationDate", ""),
            "modDate": raw.get("ModDate", ""),
            "format": f"PDF {self._doc.version()}",
        }

    def set_metadata(self, metadata: MetadataUpdate) -> None:
        """Set metadata, deleting entries whose values are empty strings.

        Keys match :attr:`metadata`, except the read-only ``format`` key.
        The standard fields share 1 MiB input and encoded-text limits. All
        updates are validated before PyO3 copying and applied atomically.
        Caller refusals use ``LimitError.code == "metadata_input_size"``.
        """
        self._ensure_open()
        updates: list[tuple[str, str]] = []
        for key, value in metadata.items():
            pdf_key = _METADATA_KEYS.get(key)
            if pdf_key is None:
                msg = f"unknown metadata key: {key!r} (valid: {sorted(_METADATA_KEYS)})"
                raise ValueError(msg)
            if not isinstance(value, str):
                msg = f"metadata value must be a string: {key!r}={value!r}"
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
        count = self.page_count
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

    def embfile_add(
        self,
        name: str,
        data: bytes,
        *,
        filename: str | None = None,
        desc: str | None = None,
        max_size: int | None = _DEFAULT_MAX_EMBEDDED_FILE_SIZE,
    ) -> None:
        """Add an EmbeddedFiles attachment.

        ``name`` is the unique key used for listing and retrieval. ``filename``
        is the viewer-facing file name and defaults to ``name``; ``desc`` is a
        description. Both support Unicode through UTF-16BE ``UF``/``Desc``.
        ``max_size`` bounds attachment data and defaults to 64 MiB, matching
        :meth:`embfile_get`; ``None`` explicitly accepts unbounded input.
        Their aggregate input text is capped at 1 MiB before data copying;
        refusals use ``embedded_file_input_size``. Existing inline FileSpecs are
        cloned only after bounded shape validation.
        This can build invoice-plus-XML structures such as ZUGFeRD/Factur-X.
        """
        self._ensure_open()
        if not isinstance(name, str):
            msg = f"name must be a string: {type(name).__name__}"
            raise TypeError(msg)
        if not name:
            msg = "name must be at least 1 character"
            raise ValueError(msg)
        if filename is not None and not isinstance(filename, str):
            msg = f"filename must be a string or None: {type(filename).__name__}"
            raise TypeError(msg)
        if desc is not None and not isinstance(desc, str):
            msg = f"desc must be a string or None: {type(desc).__name__}"
            raise TypeError(msg)
        input_text = [name, name if filename is None else filename]
        if desc is not None:
            input_text.append(desc)
        _validate_cumulative_utf8_input(
            input_text,
            _MAX_EMBEDDED_FILE_INPUT_TEXT_SIZE,
            limit_code="embedded_file_input_size",
            input_label="attachment name, filename, and description",
        )
        _validate_optional_positive_int("max_size", max_size)
        if max_size is not None and len(data) > max_size:
            limit_code = "embedded_file_size"
            msg = f"attachment input is {len(data)} bytes, exceeding the {max_size}-byte limit"
            raise LimitError(limit_code, msg)
        self._doc.embfile_add(name, bytes(data), filename, desc, max_size)

    def embfile_names(self) -> list[str]:
        """Return sorted attachment names."""
        self._ensure_open()
        return self._doc.embfile_names()

    def embfile_get(
        self,
        name: str,
        *,
        max_size: int | None = _DEFAULT_MAX_EMBEDDED_FILE_SIZE,
    ) -> bytes:
        """Return attachment contents as bytes.

        ``max_size`` bounds every decoding layer and defaults to 64 MiB. Pass a
        larger positive integer for a known large attachment, or ``None`` only
        when intentionally accepting unbounded materialization. Oversized
        content raises :class:`LimitError` with code ``embedded_file_size``.
        The lookup name stops at 1 MiB of UTF-8 before tree traversal, using
        code ``embedded_file_input_size``.
        """
        self._ensure_open()
        if not isinstance(name, str):
            msg = f"name must be a string: {type(name).__name__}"
            raise TypeError(msg)
        _validate_utf8_text_input(
            name,
            _MAX_EMBEDDED_FILE_INPUT_TEXT_SIZE,
            limit_code="embedded_file_input_size",
            input_label="attachment lookup name",
        )
        _validate_optional_positive_int("max_size", max_size)
        return self._doc.embfile_get(name, max_size)

    def embfile_del(self, name: str) -> None:
        """Delete an attachment, bounding its name before tree traversal."""
        self._ensure_open()
        if not isinstance(name, str):
            msg = f"name must be a string: {type(name).__name__}"
            raise TypeError(msg)
        _validate_utf8_text_input(
            name,
            _MAX_EMBEDDED_FILE_INPUT_TEXT_SIZE,
            limit_code="embedded_file_input_size",
            input_label="attachment lookup name",
        )
        self._doc.embfile_del(name)

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
        :meth:`get_page_text` output in the same order.
        """
        count = self.page_count
        page_numbers = list(range(1, count + 1)) if pages is None else self._page_entries(pages, "pages", count)
        text = self._doc.extract_text(page_numbers)
        self._emit_warnings()
        return text

    @overload
    def get_page_text(self, pno: int, option: Literal["text"] = "text") -> str: ...
    @overload
    def get_page_text(self, pno: int, option: Literal["words"]) -> list[WordEntry]: ...
    @overload
    def get_page_text(self, pno: int, option: Literal["blocks"]) -> list[BlockEntry]: ...
    @overload
    def get_page_text(self, pno: int, option: Literal["dict"]) -> TextPage: ...
    @overload
    def get_page_text(self, pno: int, option: Literal["rawdict"]) -> RawTextPage: ...
    def get_page_text(
        self, pno: int, option: str = "text"
    ) -> str | list[WordEntry] | list[BlockEntry] | TextPage | RawTextPage:
        """Extract text or positioned layout from zero-based page ``pno``.

        ``option`` uses pymupdf-style names without promising full compatibility:

        - ``"text"``: plain text, the default.
        - ``"words"``: ``(x0, y0, x1, y1, word, block, line, word index)``.
        - ``"blocks"``: ``(x0, y0, x1, y1, text, block, 0)``.
        - ``"dict"``: nested width, height, and blocks with lines and spans.
        - ``"rawdict"``: the same text hierarchy with ``chars`` replacing span
          ``text``; each item has ``c``, ``bbox``, and baseline ``origin``.

        Coordinates are display points (1/72 inch at the default PDF UserUnit),
        with a top-left origin,
        downward y, and page rotation/CropBox offsets resolved. Bboxes use
        glyph advances and approximate baseline extents from font-size ratios,
        not exact outlines. Reading order follows the existing geometry and
        script heuristics rather than semantic document order.

        Raw entries preserve one glyph's complete Unicode mapping, including
        ligatures with several characters; no positions are invented by
        dividing span text. Inferred spaces have no raw entry. Unmapped glyphs
        retain the existing skip/warning behavior. Output is text-only and
        shares the existing text/glyph budgets and interpretation cache.
        """
        if option == "text":
            text = self._doc.extract_text([self._lopdf_page_number(pno)])
            self._emit_warnings()
            return text
        if option == "rawdict":
            return self._get_page_rawdict(pno)
        return self._page_layout(self._lopdf_page_number(pno), option)

    @overload
    def _page_layout(self, page_number: int, option: Literal["words"]) -> list[WordEntry]: ...
    @overload
    def _page_layout(self, page_number: int, option: Literal["dict"]) -> TextPage: ...
    @overload
    def _page_layout(self, page_number: int, option: str) -> list[WordEntry] | list[BlockEntry] | TextPage: ...
    def _page_layout(self, page_number: int, option: str) -> list[WordEntry] | list[BlockEntry] | TextPage:
        """Build words, blocks, or dict layout for a validated one-based page."""
        width, height, blocks = self._doc.extract_layout(page_number)
        # Attribute warnings to the public caller rather than this helper.
        self._emit_warnings(stacklevel=4)
        if option == "words":
            words: list[WordEntry] = []
            for bno, (_, lines) in enumerate(blocks):
                for lno, (_, _, line_words, _, _) in enumerate(lines):
                    words.extend(
                        (x0, y0, x1, y1, text, bno, lno, wno) for wno, ((x0, y0, x1, y1), text) in enumerate(line_words)
                    )
            return words
        if option == "blocks":
            return [
                (
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

    def _get_page_rawdict(self, pno: int) -> RawTextPage:
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

    def delete_page(self, pno: int) -> None:
        """Delete zero-based page ``pno``; negative values count from the end."""
        page_number = self._lopdf_page_number(pno)
        self._doc.delete_pages([page_number])
        self._bump_generation()

    def delete_pages(self, page_numbers: Iterable[int]) -> None:
        """Delete zero-based pages; negative values count from the end.

        The iterable may contain up to the larger of the page count and 4,096
        entries; further entries raise :class:`LimitError` with code
        ``page_entry_count`` before the document changes. Links, outline
        destinations, the open action, and named destinations that target
        deleted pages are removed under the same limits as :meth:`get_toc` and
        :meth:`Page.get_links`. The AcroForm loses the widgets of deleted pages
        and the fields whose widgets all lay there, under the
        :meth:`get_form_fields` limits; fields that never had a widget stay.
        A tagged PDF's structure tree loses the marked content and annotation
        references of deleted pages, the elements left without kids, and
        their ``/ParentTree`` and ``/IDTree`` entries; a structure tree beyond
        1,048,576 nodes, 4,194,304 edges, or 256 levels raises
        :class:`LimitError` with code ``structure_tree_count``. A refusal
        leaves the document unchanged.
        """
        numbers = self._page_entries(page_numbers, "page_numbers", self.page_count)
        if not numbers:
            return
        self._doc.delete_pages(numbers)
        self._bump_generation()

    def select(self, page_numbers: Iterable[int]) -> None:
        """Keep only the given zero-based pages in the given order.

        This also reorders pages. Repeating an index duplicates that page; the
        duplicate shares Contents and Resources objects with the original. The
        iterable may contain up to the larger of the page count and 4,096
        entries, so a selection never grows a large document; further entries
        raise :class:`LimitError` with code ``page_entry_count``. Navigation to
        excluded pages, their AcroForm widgets and fields, and their
        structure-tree content are cleaned as in :meth:`delete_pages`.
        """
        numbers = self._page_entries(page_numbers, "page_numbers", self.page_count)
        self._doc.select(numbers)
        self._bump_generation()

    def insert_pdf(
        self,
        other: Document,
        from_page: int = 0,
        to_page: int = -1,
        start_at: int = -1,
    ) -> None:
        """Insert a page range from another document.

        Insert the inclusive, zero-based ``from_page..to_page`` range at
        zero-based ``start_at``; negative source indices count from the end and
        ``start_at=-1`` appends. A descending range imports in reverse order.
        The range is bounded by the source document itself, so a complete
        document of any page count can be inserted. Source-local links,
        including named destinations, become explicit destinations or are
        omitted when their target page is not imported; source name trees share
        the :meth:`Page.get_links` limits.

        AcroForm fields with a widget on an imported page join this document's
        form with their values and appearances, so :meth:`get_form_fields` and
        :meth:`set_form_field` reach them. A top-level field name that this
        document already uses gains the smallest unused ``+N`` suffix, so
        importing one form twice yields ``name`` and ``name+1``; names are
        compared within the 1 MiB name budget of :meth:`get_form_fields`, and
        JavaScript that names a renamed field is not rewritten. Source default
        resources join ``/DR``: an identical resource is shared, and a different
        one under a name already in use gains the smallest unused ``_N`` suffix
        that imported default appearance strings follow. A partial range omits
        widgets of the other pages and the fields left without a widget, and
        raises :class:`PdfError` without changing this document when the source
        field tree exceeds the :meth:`get_form_fields` limits. A complete import
        keeps every field without walking the tree. The source ``/XFA`` and
        ``/SigFlags`` are not copied.
        """
        self._ensure_open()
        if other is self:
            msg = "cannot insert a document into itself"
            raise ValueError(msg)
        other._ensure_open()
        source_count = other.page_count
        if source_count == 0:
            return
        start = _resolve_page_index(from_page, source_count)
        stop = _resolve_page_index(to_page, source_count)
        step = 1 if start <= stop else -1
        position = None if start_at == -1 else self._insert_position(start_at, "start_at")
        self._doc.merge_pages(other._doc, list(range(start + 1, stop + 1 + step, step)), position)
        self._bump_generation()

    def new_page(self, pno: int = -1, width: float = 595.0, height: float = 842.0) -> Page:
        """Insert a blank page and return its :class:`Page`.

        ``pno`` is the zero-based insertion position; -1 appends. ``width`` and
        ``height`` are PDF units and default to 595×842 portrait A4.
        """
        self._ensure_open()
        if (
            not (math.isfinite(width) and math.isfinite(height))
            or not (0 < width <= _FLOAT32_MAX)
            or not (0 < height <= _FLOAT32_MAX)
        ):
            msg = f"width / height must be positive finite values within PDF real-number range: ({width!r}, {height!r})"
            raise ValueError(msg)
        if pno == -1:
            position = None
            index = self.page_count
        else:
            position = self._insert_position(pno, "pno")
            index = position
        self._doc.new_page(position, width, height)
        self._bump_generation()
        return self[index]

    def copy_page(self, pno: int, to: int = -1) -> None:
        """Copy page ``pno`` to insertion position ``to``; -1 appends.

        The copied page shares Contents and Resources objects with the original.
        """
        self._ensure_open()
        page_number = self._lopdf_page_number(pno)
        position = None if to == -1 else self._insert_position(to, "to")
        self._doc.copy_page(page_number, position)
        self._bump_generation()

    def _insert_position(self, value: int, name: str) -> int:
        """Validate an insertion position from 0 through ``page_count``."""
        count = self.page_count
        if not 0 <= value <= count:
            msg = f"{name} {value} is out of range (0..{count} or -1)"
            raise IndexError(msg)
        return value

    def get_toc(self) -> list[list[int | str]]:
        """Return bookmarks as ``[[level, title, page number], ...]``.

        Levels and page numbers are one-based for pymupdf compatibility, unlike
        other page APIs. Return an empty list when no TOC exists. Traversal is
        cycle-aware and rejects more than 65,536 nodes or entries or 131,072
        edges with ``LimitError.code == "toc_entry_count"``, more than 16 MiB of
        source/returned text with ``"toc_text_size"``, and more than 64 levels
        or 32 destination indirections. Named destinations resolve under the
        same name-tree limits as :meth:`Page.get_links`.
        """
        self._ensure_open()
        return [[level, title, page] for level, title, page in self._doc.get_toc()]

    def set_toc(self, toc: Sequence[Sequence[int | str]]) -> None:
        """Replace bookmarks from ``[[level, title, page number], ...]``.

        An empty sequence removes them. At most 65,536 entries, 64 levels, and
        16 MiB each of input and encoded title text are accepted, enough for
        large manuals while matching :meth:`get_toc`. Levels start at 1 and can
        increase by at most one from the previous entry. Page numbers are
        one-based, matching :meth:`get_toc`. Validation is atomic. Refusals occur
        before PyO3 copying with ``LimitError.code == "toc_entry_count"`` for
        entries or ``"toc_input_size"`` for title text.
        """
        self._ensure_open()
        if len(toc) > _MAX_TOC_ENTRIES:
            limit_code = "toc_entry_count"
            msg = f"toc cannot contain more than {_MAX_TOC_ENTRIES} entries"
            raise LimitError(limit_code, msg)
        count = self.page_count
        entries: list[tuple[int, str, int]] = []
        previous_level = 0
        for i, item in enumerate(toc):
            try:
                level_raw, title, page_raw = item
                level = int(level_raw)
                page = int(page_raw)
            except (TypeError, ValueError) as exc:
                msg = f"toc[{i}] must be 3 elements [level, title, page number]: {item!r}"
                raise ValueError(msg) from exc
            if level < 1 or level > previous_level + 1:
                msg = f"toc[{i}] has an invalid level {level} (must be >= 1 and at most the previous level + 1)"
                raise ValueError(msg)
            if not 1 <= page <= count:
                msg = f"toc[{i}] page number {page} is out of range (1..{count})"
                raise ValueError(msg)
            entries.append((level, str(title), page))
            previous_level = level
        titles = [title for _, title, _ in entries]
        _validate_cumulative_utf8_input(
            titles,
            _MAX_TOC_INPUT_TEXT_SIZE,
            limit_code="toc_input_size",
            input_label="TOC title input",
        )
        _validate_cumulative_pdf_text_input(
            titles,
            _MAX_TOC_INPUT_TEXT_SIZE,
            limit_code="toc_input_size",
            input_label="encoded TOC title input",
        )
        self._doc.set_toc(entries)

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

    def render_page(
        self,
        pno: int,
        scale: float = 1.0,
        *,
        dpi: float | None = None,
        background: tuple[int, int, int] | tuple[int, int, int, int] | None = None,
        max_size: int | None = _DEFAULT_MAX_RENDER_PAGE_SIZE,
    ) -> bytes:
        """Render zero-based page ``pno`` to PNG.

        ``scale`` is a positive finite factor where 1.0 equals 72 dpi. ``dpi``
        may be used instead (144 equals scale 2.0) but not together with a
        nondefault scale. ``background`` is an RGB or RGBA tuple with components
        from 0 to 255; the default is transparent. Output is limited to 65,535
        pixels per side and 64,000,000 total pixels. ``max_size`` defaults to
        64 MiB of encoded PNG output; pass ``None`` only for trusted workloads.
        """
        _validate_optional_positive_int("max_size", max_size)
        if dpi is not None:
            if scale != 1.0:
                msg = "scale and dpi cannot both be specified"
                raise ValueError(msg)
            scale = dpi / 72.0
        rgba = _normalize_background(background)
        page_number = self._lopdf_page_number(pno)
        self._ensure_fallback_fonts()
        result = self._doc.render_page_png(page_number, scale, rgba, max_size)
        self._emit_warnings()
        return result

    def render_pages(  # noqa: PLR0913 - batch resource controls are keyword-only.
        self,
        pages: Iterable[int] | None = None,
        scale: float = 1.0,
        *,
        dpi: float | None = None,
        background: tuple[int, int, int] | tuple[int, int, int, int] | None = None,
        workers: int | None = None,
        max_size: int | None = _DEFAULT_MAX_RENDER_BATCH_SIZE,
    ) -> list[bytes]:
        """Render pages to PNG concurrently while preserving input order.

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
        if dpi is not None:
            if scale != 1.0:
                msg = "scale and dpi cannot both be specified"
                raise ValueError(msg)
            scale = dpi / 72.0
        if workers is None:
            workers = min(4, os.cpu_count() or 1)
        elif isinstance(workers, bool) or not isinstance(workers, int):
            msg = f"workers must be an integer between 1 and {_MAX_RENDER_WORKERS}"
            raise TypeError(msg)
        if not 1 <= workers <= _MAX_RENDER_WORKERS:
            msg = f"workers must be between 1 and {_MAX_RENDER_WORKERS}"
            raise ValueError(msg)
        _validate_optional_positive_int("max_size", max_size)
        count = self.page_count
        page_numbers = list(range(1, count + 1)) if pages is None else self._page_entries(pages, "pages", count)
        rgba = _normalize_background(background)
        if not page_numbers:
            return []
        self._ensure_fallback_fonts()
        result = self._doc.render_pages_png(page_numbers, scale, rgba, workers, max_size)
        self._emit_warnings()
        return result

    def render_page_svg(
        self,
        pno: int,
        *,
        max_size: int | None = _DEFAULT_MAX_SVG_SIZE,
    ) -> str:
        """Render zero-based page ``pno`` to a bounded SVG string.

        ``max_size`` caps UTF-8 output and defaults to 64 MiB; ``None``
        explicitly opts out. hayro-svg currently materializes its internal
        Rust string before this boundary is enforced, but over-limit output is
        rejected before conversion to a Python string.
        """
        page_number = self._lopdf_page_number(pno)
        _validate_optional_positive_int("max_size", max_size)
        self._ensure_fallback_fonts()
        result = self._doc.render_page_svg(page_number, max_size)
        self._emit_warnings()
        return result

    def _emit_warnings(self, stacklevel: int = 3) -> None:
        """Emit recoverable interpretation warnings as ``PylopdfWarning``."""
        for message in self._doc.take_warnings():
            _warnings.warn(message, PylopdfWarning, stacklevel=stacklevel)

    def compress_images(
        self,
        *,
        dpi: float | None = 150,
        quality: int = 75,
    ) -> ImageCompressionResult:
        """Downsample and JPEG-recompress safe raster images in place.

        ``dpi`` limits the effective resolution of each source-image axis.
        Reused images retain the pixels required by their largest placement
        across the document. Pass ``None`` to disable downsampling
        while retaining JPEG quality recompression. ``quality`` is 1 through
        100; both operations are lossy.

        The conservative implementation rewrites only indirect, single-filter,
        8-bit DeviceGray/DeviceRGB DCT or Flate XObjects without masks or custom
        decode arrays. DCT decode parameters are excluded; Flate may use no
        predictor or a consistent PNG predictor. Unsupported interpreted
        indirect images and outputs that would not be smaller are counted as
        ``skipped``; inline images are not considered. The operation releases
        the GIL, is atomic on decoding errors, rejects more than 65,536
        interpreted indirect image placements, 16,384 unique image objects, or
        250 million eligible source pixels, and skips an individual source
        above 64 million pixels.

        Byte totals compare rewritten source payloads with their resulting JPEG
        payloads, not complete PDF serialization. Save to a new output and
        inspect it before replacing an original document.
        """
        self._ensure_open()
        if (
            isinstance(quality, bool)
            or not isinstance(quality, int)
            or not _IMAGE_COMPRESSION_MIN_QUALITY <= quality <= _IMAGE_COMPRESSION_MAX_QUALITY
        ):
            msg = "quality must be an integer from 1 through 100"
            raise PdfError(msg)
        if dpi is not None:
            if isinstance(dpi, bool) or not isinstance(dpi, (int, float)):
                msg = "dpi must be a finite number from 36 through 2400, or None"
                raise PdfError(msg)
            dpi = float(dpi)
            if not math.isfinite(dpi) or not _IMAGE_COMPRESSION_MIN_DPI <= dpi <= _IMAGE_COMPRESSION_MAX_DPI:
                msg = "dpi must be a finite number from 36 through 2400, or None"
                raise PdfError(msg)
        considered, rewritten, skipped, bytes_before, bytes_after = self._doc.compress_images(dpi, quality)
        self._emit_warnings()
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
        object_streams: bool = False,
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
        ``object_streams=True`` writes PDF 1.5+ object and xref streams, often
        reducing size and raising the version to 1.5 when necessary.

        Providing ``user_pw`` or ``owner_pw`` writes AES-256 PDF 2.0 encryption
        while the in-memory document stays plaintext. ``owner_pw`` defaults to
        ``user_pw``. An empty user password plus an owner password permits
        unrestricted opening with permission controls. Each password is capped
        at 127 UTF-8 bytes. ``permissions`` combines :class:`Permissions` and
        defaults to all. Encryption cannot be combined with object streams.
        Every mode streams to a same-directory temporary file and replaces
        ``filename`` only after a complete successful write.
        """
        self._ensure_open()
        level = _garbage_level(garbage)
        encryption = self._encryption_args(user_pw, owner_pw, permissions, object_streams=object_streams)
        self._apply_save_options(garbage=level, deflate=deflate)
        writer: Callable[[str], None]
        if encryption is not None:
            user, owner, perms = encryption
            writer = functools.partial(
                self._doc.save_encrypted,
                user_password=user,
                owner_password=owner,
                permissions=perms,
                file_encryption_key=os.urandom(32),
            )
        elif object_streams:
            writer = self._doc.save_with_object_streams
        else:
            writer = self._doc.save
        _atomic_save_file(filename, writer)

    def tobytes(  # noqa: PLR0913  # Save options are keyword-only, like pymupdf.
        self,
        *,
        garbage: int = 0,
        deflate: bool = False,
        object_streams: bool = False,
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
        encryption = self._encryption_args(user_pw, owner_pw, permissions, object_streams=object_streams)
        self._apply_save_options(garbage=level, deflate=deflate)
        if encryption is not None:
            user, owner, perms = encryption
            return self._doc.save_bytes_encrypted(user, owner, perms, os.urandom(32), max_size)
        if object_streams:
            return self._doc.save_bytes_with_object_streams(max_size)
        return self._doc.save_bytes(max_size)

    def _apply_save_options(self, *, garbage: int, deflate: bool) -> None:
        """Apply garbage collection and stream compression before saving."""
        if garbage:
            self._doc.collect_garbage(garbage)
        if deflate:
            self._doc.compress()

    @staticmethod
    def _encryption_args(
        user_pw: str | None,
        owner_pw: str | None,
        permissions: int,
        *,
        object_streams: bool,
    ) -> tuple[str, str, int] | None:
        """Validate encryption arguments, returning ``None`` when disabled."""
        if user_pw is None and owner_pw is None:
            return None
        if object_streams:
            msg = "encryption (user_pw / owner_pw) and object_streams cannot both be specified"
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


def open(  # noqa: A001
    filename: str | os.PathLike[str] | None = None,
    stream: bytes | None = None,
    password: str | None = None,
    max_decompressed_size: int | None = None,
    *,
    limits: DocumentLimits | None = None,
) -> Document:
    """Open a :class:`Document`; equivalent to ``Document(...)``."""
    return Document(
        filename=filename,
        stream=stream,
        password=password,
        max_decompressed_size=max_decompressed_size,
        limits=limits,
    )


def peek_metadata(
    filename: str | os.PathLike[str] | None = None,
    stream: bytes | None = None,
    password: str | None = None,
    *,
    max_file_size: int | None = None,
) -> MetadataProbe:
    """Read metadata and page count without parsing the entire document.

    Return the keys from :attr:`Document.metadata` plus integer ``page_count``
    and boolean ``encrypted`` / ``repaired`` facts. This is suitable for
    scanning many PDFs. ``max_file_size`` rejects oversized path or byte input
    before metadata parsing; ``None`` leaves it unbounded. Returned standard
    Info text is capped at 1 MiB. Password input is capped at 127 UTF-8 bytes.
    """
    if (filename is None) == (stream is None):
        msg = "specify exactly one of filename or stream"
        raise ValueError(msg)
    _validate_optional_positive_int("max_file_size", max_file_size)
    _validate_password_input(password)
    if stream is not None:
        raw, page_count, version, encrypted, recovery = _Document.load_metadata_bytes(
            stream,
            password,
            max_file_size,
        )
    else:
        raw, page_count, version, encrypted, recovery = _Document.load_metadata(
            os.fspath(cast("os.PathLike[str] | str", filename)),
            password,
            max_file_size,
        )
    if recovery is not None:
        _warnings.warn(recovery, PylopdfWarning, stacklevel=2)
    return {
        "title": raw.get("Title", ""),
        "author": raw.get("Author", ""),
        "subject": raw.get("Subject", ""),
        "keywords": raw.get("Keywords", ""),
        "creator": raw.get("Creator", ""),
        "producer": raw.get("Producer", ""),
        "creationDate": raw.get("CreationDate", ""),
        "modDate": raw.get("ModDate", ""),
        "format": f"PDF {version}",
        "page_count": page_count,
        "encrypted": encrypted,
        "repaired": recovery is not None,
    }


# The package re-exports these names; reprs, pickles, and documentation name it.
for _public in (
    Document,
    open,
    peek_metadata,
):
    _public.__module__ = "pylopdf"
