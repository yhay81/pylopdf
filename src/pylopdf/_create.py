"""New documents that krilla writes as a whole: :func:`create` and :class:`Builder`."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal, TypeAlias, get_args

from pylopdf._canvas import Canvas
from pylopdf._document import Document, _is_language_tag, open  # noqa: A004 - the package's open().
from pylopdf._hints import hinted_getattr
from pylopdf._limits import _FLOAT32_MAX
from pylopdf._pages import _DEFAULT_PAGE_SIZE
from pylopdf.pylopdf_core import _Builder

if TYPE_CHECKING:
    from collections.abc import Iterable

Standard: TypeAlias = Literal[
    "pdf/a-1a",
    "pdf/a-1b",
    "pdf/a-2a",
    "pdf/a-2b",
    "pdf/a-2u",
    "pdf/a-3a",
    "pdf/a-3b",
    "pdf/a-3u",
    "pdf/a-4",
    "pdf/a-4e",
    "pdf/a-4f",
    "pdf/ua-1",
]

_STANDARDS: tuple[str, ...] = get_args(Standard)
#: Standards whose documents must be tagged.
_TAGGED_STANDARDS = frozenset({"pdf/a-1a", "pdf/a-2a", "pdf/a-3a", "pdf/ua-1"})


def _text(value: object, name: str) -> str | None:
    """Return an optional metadata string."""
    if value is not None and not isinstance(value, str):
        msg = f"{name} must be a string or None: {value!r}"
        raise TypeError(msg)
    return value


class Builder:
    """A new document that krilla writes as a whole.

    Get one from :func:`create`. :meth:`page` returns a :class:`Canvas` for
    each new page, which joins the document when its ``with`` block ends;
    :meth:`finish` writes the document and returns it opened, and
    :meth:`to_bytes` returns its bytes exactly as krilla wrote them.
    """

    if not TYPE_CHECKING:
        __getattr__ = hinted_getattr

    def __init__(self, core: _Builder) -> None:
        """Wrap a core builder; :func:`create` is the way to make one."""
        self._core = core

    @property
    def tagged(self) -> bool:
        """Return whether tag scopes build a structure tree."""
        return self._core.is_tagged()

    @property
    def page_count(self) -> int:
        """Return the number of pages added so far."""
        return self._core.page_count()

    def page(self, size: Sequence[float] = _DEFAULT_PAGE_SIZE) -> Canvas:
        """Return a canvas for a new page of ``size`` points, A4 (595 × 842) by default.

        The page joins the document when the canvas's ``with`` block ends
        normally, after the pages whose blocks ended before; after an
        exception it does not. Every font a new document draws is embedded,
        so its text needs an OpenType :class:`Font`: text without ``font``
        uses the face of an installed font extra for its script, and Western
        European text needs ``font`` until krilla draws the Standard 14
        fonts. Placing other pages is not supported yet.
        """
        try:
            width, height = (float(value) for value in size)
        except (TypeError, ValueError) as exc:
            msg = f"size must be (width, height) in points: {size!r}"
            raise ValueError(msg) from exc
        for value in (width, height):
            if not (math.isfinite(value) and 1 <= value <= _FLOAT32_MAX):
                msg = f"size must be two finite numbers of at least 1 point: {size!r}"
                raise ValueError(msg)
        return Canvas._new_page(self, width, height)

    def to_bytes(self) -> bytes:
        """Write the document and return its bytes as krilla wrote them.

        The builder keeps its pages, so more pages can follow and the
        document can be written again.
        """
        return bytes(self._core.finish())

    def finish(self) -> Document:
        """Write the document and return it opened as a :class:`Document`.

        Saving the returned document writes it again with lopdf; use
        :meth:`to_bytes` for the file exactly as krilla validated it.
        """
        return open(self.to_bytes())

    def __repr__(self) -> str:
        """Return the page count."""
        return f"Builder({self.page_count} pages)"


def create(  # noqa: PLR0913 - the document's settings and metadata.
    *,
    standard: Standard | Sequence[Standard] | None = None,
    tagged: bool = False,
    language: str | None = None,
    title: str | None = None,
    author: str | None = None,
    subject: str | None = None,
    keywords: Iterable[str] = (),
    created: dt.datetime | None = None,
) -> Builder:
    """Start a new document that krilla writes as a whole.

    ``standard`` names one standard, or several such as
    ``("pdf/a-2a", "pdf/ua-1")``; writing the document then validates it and
    raises :class:`PdfError` listing every violation that krilla finds,
    instead of writing a non-conforming file. Some requirements are
    semantic, such as meaningful alternate text, and remain the caller's.
    ``tagged=True``, which ``pdf/ua-1`` and the ``a`` levels of PDF/A imply,
    gives the document a structure tree built from :meth:`Canvas.tag`
    scopes. ``language`` is the document's BCP 47 language tag, and the other
    arguments fill its metadata. ``created`` is the creation date, which
    PDF/A and PDF/UA require; it defaults to the current time in UTC, and a
    fixed value makes the output reproducible.
    """
    if standard is None:
        standards: list[str] = []
    elif isinstance(standard, str):
        standards = [str(standard)]
    elif isinstance(standard, Sequence):
        standards = [str(name) for name in standard]
    else:
        msg = f"standard must be a standard's name or a sequence of names: {standard!r}"
        raise TypeError(msg)
    for name in standards:
        if name not in _STANDARDS:
            msg = f"standard must be one of {', '.join(_STANDARDS)}: {name!r}"
            raise ValueError(msg)
    if language is not None and not _is_language_tag(language):
        msg = f"language must be a BCP 47 language tag: {language!r}"
        raise ValueError(msg)
    keyword_list = list(keywords) if not isinstance(keywords, str) else [keywords]
    for keyword in keyword_list:
        _text(keyword, "keywords")
    is_tagged = bool(tagged) or any(name in _TAGGED_STANDARDS for name in standards)
    moment = dt.datetime.now(dt.UTC) if created is None else created
    if not isinstance(moment, dt.datetime):
        msg = f"created must be a datetime.datetime or None: {created!r}"
        raise TypeError(msg)
    offset = moment.utcoffset()
    offset_minutes = 0 if offset is None else int(offset.total_seconds() // 60)
    core = _Builder(
        standards,
        is_tagged,
        language,
        _text(title, "title"),
        _text(author, "author"),
        _text(subject, "subject"),
        keyword_list,
        (
            moment.year,
            moment.month,
            moment.day,
            moment.hour,
            moment.minute,
            moment.second,
            offset_minutes,
        ),
    )
    return Builder(core)


# The package re-exports this class; reprs, pickles, and documentation name it.
Builder.__module__ = "pylopdf"
create.__module__ = "pylopdf"
