"""Sanitization: removing what a document carries besides its pages.

``Document.sanitize`` removes scripts, actions, embedded files, metadata,
hidden text, comments, and unreachable objects, and returns a
:class:`SanitizationReport`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pylopdf._document import Document


@dataclass(frozen=True, slots=True, kw_only=True)
class SanitizationReport:
    """What :meth:`Document.sanitize` removed.

    ``scripts`` counts JavaScript actions and script containers (the
    document's JavaScript name tree and an XFA form), ``actions`` the other
    actions and automatic triggers, ``embedded_files`` the attached files and
    file attachment annotations, ``metadata`` the metadata entries
    (``/Info``, XMP streams, ``/PieceInfo``, ``/LastModified``),
    ``hidden_glyphs`` the glyphs of invisible text, ``comments`` and ``links``
    the annotations removed, ``fields`` the form fields reset, ``thumbnails``
    the page thumbnails, and ``objects`` the objects that nothing reached any
    more and that went with them.
    """

    scripts: int
    actions: int
    embedded_files: int
    metadata: int
    hidden_glyphs: int
    comments: int
    links: int
    fields: int
    thumbnails: int
    objects: int


def sanitize(  # noqa: PLR0913 - the categories to remove.
    document: Document,
    *,
    scripts: bool,
    actions: bool,
    embedded_files: bool,
    metadata: bool,
    hidden_text: bool,
    comments: bool,
    links: bool,
    form_values: bool,
    thumbnails: bool,
) -> SanitizationReport:
    """Validate the options and sanitize; see :meth:`Document.sanitize`."""
    options = {
        "scripts": scripts,
        "actions": actions,
        "embedded_files": embedded_files,
        "metadata": metadata,
        "hidden_text": hidden_text,
        "comments": comments,
        "links": links,
        "form_values": form_values,
        "thumbnails": thumbnails,
    }
    for name, value in options.items():
        if not isinstance(value, bool):
            msg = f"{name} is a bool: {value!r}"
            raise TypeError(msg)
    document._ensure_open()  # noqa: SLF001 - the document's own state.
    raw = document._doc.sanitize(  # noqa: SLF001
        scripts, actions, embedded_files, metadata, hidden_text, comments, links, thumbnails
    )
    document._emit_warnings()  # noqa: SLF001
    fields = 0
    if form_values:
        for field in document.form.values():
            field.reset()
            fields += 1
    removed_scripts, removed_actions, files, entries, glyphs, removed_comments, removed_links, thumbs, objects = raw
    return SanitizationReport(
        scripts=removed_scripts,
        actions=removed_actions,
        embedded_files=files,
        metadata=entries,
        hidden_glyphs=glyphs,
        comments=removed_comments,
        links=removed_links,
        fields=fields,
        thumbnails=thumbs,
        objects=objects,
    )


SanitizationReport.__module__ = "pylopdf"
