"""Warnings and exceptions defined in Python, and LimitError's stable code."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from pylopdf.pylopdf_core import LimitError, PdfError

if TYPE_CHECKING:
    from typing import Any


class PylopdfWarning(UserWarning):
    """A recoverable PDF interpretation warning for repair, font, or image issues."""


class DocumentClosedError(PdfError):
    """An operation on a closed :class:`Document`."""


class EncryptedDocumentError(PdfError):
    """An operation on an undecrypted PDF; provide a password or authenticate."""


class StalePageError(PdfError):
    """Use of a stale :class:`Page` after a structural document change.

    Fetch the page again with ``doc[i]``.
    """


_LIMIT_ERROR_STR = LimitError.__str__
_LIMIT_ERROR_ARG_COUNT = 2


def _limit_error_code(error: LimitError) -> str:
    """Return the stable resource identifier from a core limit exception."""
    if len(error.args) >= _LIMIT_ERROR_ARG_COUNT and isinstance(error.args[0], str):
        return error.args[0]
    return "unknown"


def _limit_error_str(error: LimitError) -> str:
    """Render only the human-readable part of a core limit exception."""
    if len(error.args) >= _LIMIT_ERROR_ARG_COUNT and isinstance(error.args[1], str):
        return error.args[1]
    return _LIMIT_ERROR_STR(error)


_limit_error_type = cast("Any", LimitError)
_limit_error_type.code = property(_limit_error_code)
_limit_error_type.__str__ = _limit_error_str


# The package re-exports these names; reprs, pickles, and documentation name it.
for _public in (
    PylopdfWarning,
    DocumentClosedError,
    EncryptedDocumentError,
    StalePageError,
):
    _public.__module__ = "pylopdf"
