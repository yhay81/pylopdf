"""The interactive form: ``Document.form``, its ``Field`` handles, and their enums."""

from __future__ import annotations

import enum
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pylopdf._fonts import Font, _form_generation_font, _package_face_weight
from pylopdf._limits import _DEFAULT_MAX_FONT_INPUT_SIZE, _validate_utf8_text_input
from pylopdf.pylopdf_core import PdfError, StaleObjectError

if TYPE_CHECKING:
    from pylopdf._document import Document
    from pylopdf._objects import Dictionary

#: Caller field names and values stop here before the core copies them.
_MAX_FORM_FIELD_INPUT_SIZE = 1024 * 1024

#: The core's facts of one field: full name, object number, kind, value, and
#: inherited ``/Ff``.
_FieldTuple = tuple[str, int, str, str | None, int]


class FieldType(enum.StrEnum):
    """The kind of a form field, from its ``/FT`` and the flags that refine it."""

    TEXT = "text"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    PUSHBUTTON = "pushbutton"
    COMBOBOX = "combobox"
    LISTBOX = "listbox"
    SIGNATURE = "signature"
    UNKNOWN = "unknown"
    """A field whose ``/FT`` is missing or not one that ISO 32000-2 defines."""


class FieldFlags(enum.IntFlag):
    """The field flags of ``/Ff`` (ISO 32000-2 Tables 227, 229, 231, and 233).

    Bits 26 and 27 mean different things for buttons and for text and choice
    fields, so ``RADIOS_IN_UNISON`` is the same bit as ``RICH_TEXT``.
    """

    READ_ONLY = 1 << 0
    REQUIRED = 1 << 1
    NO_EXPORT = 1 << 2
    MULTILINE = 1 << 12
    PASSWORD = 1 << 13
    NO_TOGGLE_TO_OFF = 1 << 14
    RADIO = 1 << 15
    PUSHBUTTON = 1 << 16
    COMBO = 1 << 17
    EDIT = 1 << 18
    SORT = 1 << 19
    FILE_SELECT = 1 << 20
    MULTI_SELECT = 1 << 21
    DO_NOT_SPELL_CHECK = 1 << 22
    DO_NOT_SCROLL = 1 << 23
    COMB = 1 << 24
    RICH_TEXT = 1 << 25
    RADIOS_IN_UNISON = 1 << 25
    COMMIT_ON_SEL_CHANGE = 1 << 26


_DEFINED_FIELD_FLAGS = sum(flag.value for flag in FieldFlags)


@dataclass(frozen=True, slots=True, kw_only=True)
class FieldChoice:
    """One option of a choice field's ``/Opt``."""

    value: str
    """The export value, which :attr:`Field.value` holds when the option is selected."""
    label: str
    """The text that a viewer shows, the export value unless the option names another."""


class Field:
    """A handle on one terminal form field, as ``doc.form[name]`` returns it.

    Properties read the document on every access. A handle raises
    :class:`StaleObjectError` once its field is no longer in the form or the
    document's objects were renumbered.
    """

    __slots__ = ("_document", "_epoch", "_xref")

    def __init__(self, document: Document, xref: int) -> None:
        """Create a handle; :class:`Form` does this."""
        self._document = document
        self._epoch = document._object_epoch
        self._xref = xref

    def _facts(self) -> _FieldTuple:
        """Return the core's current facts of this field."""
        document = self._document
        document._ensure_open()
        if self._epoch == document._object_epoch:
            for entry in document._doc.form_fields():
                if entry[1] == self._xref:
                    return entry
            msg = f"object {self._xref} is no longer a field of the form"
        else:
            msg = f"field {self._xref} was invalidated when the document's objects were renumbered or reloaded"
        raise StaleObjectError(msg)

    @property
    def xref(self) -> int:
        """Return the field dictionary's object number."""
        return self._xref

    @property
    def obj(self) -> Dictionary:
        """Return the field dictionary in the low-level object model."""
        self._facts()
        return cast("Dictionary", self._document.objects[self._xref])

    @property
    def name(self) -> str:
        """Return the fully qualified name, the partial names of the field and its ancestors joined by dots."""
        return self._facts()[0]

    @property
    def type(self) -> FieldType:
        """Return the field's kind."""
        return FieldType(self._facts()[2])

    @property
    def flags(self) -> FieldFlags:
        """Return the field flags, inherited from ancestors that set ``/Ff``; undefined bits are dropped."""
        return FieldFlags(self._facts()[4] & _DEFINED_FIELD_FLAGS)

    @property
    def choices(self) -> tuple[FieldChoice, ...]:
        """Return a choice field's options in ``/Opt`` order, or ``()`` without them.

        Reads stop at 4,096 options or 1 MiB of option text.
        """
        self._facts()
        return tuple(
            FieldChoice(value=value, label=label) for value, label in self._document._doc.form_field_choices(self._xref)
        )

    @property
    def value(self) -> str | None:
        """Return or set the field's value.

        A text or choice field holds its text, a multiple selection joined
        by commas; a check box or radio button holds the appearance state of
        the selected widget, such as ``"Yes"``, or ``"Off"``. ``None`` means
        the field has no ``/V``.

        Assigning a string sets text and choice fields, and selects a button
        state by name; ``True`` selects a check box's or radio button's on
        state, and ``False`` selects ``"Off"``. The assignment regenerates the
        widget appearances in the same operation, with the face of
        :attr:`Form.font` when one is set, else Helvetica for WinAnsi text and
        an installed font extra (such as ``pylopdf[jp]``) for other scripts.
        Existing non-empty button appearances are kept; missing ones get
        vector marks. Comb text fields place each grapheme in its cell and
        refuse a longer value. The field's other widgets that lack an
        appearance are completed, and ``NeedAppearances`` is cleared once every
        fillable widget has one. Text without an installed face is stored with
        ``NeedAppearances`` for viewers, but pylopdf cannot render it.

        Names and values stop at 1 MiB (``form_field_input_size``), and text
        layout at 4,096 lines. Pushbuttons, signature fields, and rich text
        cannot be filled; sign with pyHanko. A refusal changes nothing.
        """
        return self._facts()[3]

    @value.setter
    def value(self, value: str | bool) -> None:
        name = self.name
        _fill(self._document, name, value)

    def __eq__(self, other: object) -> bool:
        """Compare the document, the object numbering, and the object number."""
        if not isinstance(other, Field):
            return NotImplemented
        return self._document is other._document and self._epoch == other._epoch and self._xref == other._xref

    def __hash__(self) -> int:
        """Hash the document, the object numbering, and the object number."""
        return hash((id(self._document), self._epoch, self._xref))

    def __repr__(self) -> str:
        """Return ``<Field 'name' 12>``, or ``<Field 12>`` once stale."""
        try:
            name = self.name
        except (PdfError, StaleObjectError):
            return f"<Field {self._xref}>"
        return f"<Field {name!r} {self._xref}>"


def _fill(document: Document, name: str, value: object) -> None:
    """Set the value of the field named ``name`` and regenerate its appearances."""
    if not isinstance(value, (str, bool)):
        msg = f"a field value is a str or bool: {value!r}"
        raise TypeError(msg)
    _validate_utf8_text_input(
        name,
        _MAX_FORM_FIELD_INPUT_SIZE,
        limit_code="form_field_input_size",
        input_label="form field name",
    )
    if isinstance(value, bool):
        if value:
            on_states = [state for state in document._doc.form_button_states(name) if state != "Off"]
            resolved = on_states[0] if on_states else "Yes"
        else:
            resolved = "Off"
    else:
        _validate_utf8_text_input(
            value,
            _MAX_FORM_FIELD_INPUT_SIZE,
            limit_code="form_field_input_size",
            input_label="form field value",
        )
        resolved = value
    core = document._doc
    font = document._form_font
    if font is not None:
        core.set_form_field_font(name, resolved, font._font)
        return
    selected = None if isinstance(value, bool) else _form_generation_font(resolved)
    if selected is None:
        core.set_form_field(name, resolved, None, 0, _DEFAULT_MAX_FONT_INPUT_SIZE, None)
        return
    data, package_font = selected
    weight = _package_face_weight(package_font)
    if isinstance(data, Path):
        core.set_form_field_file(name, resolved, str(data), 0, _DEFAULT_MAX_FONT_INPUT_SIZE, package_font, weight)
    else:
        core.set_form_field(name, resolved, data, 0, _DEFAULT_MAX_FONT_INPUT_SIZE, weight)


class Form(Mapping[str, Field]):
    """The terminal fields of a document's interactive form, as ``doc.form`` returns them.

    Keys are fully qualified field names in sorted order; when malformed
    files give two fields one name, the mapping holds the first by object
    number. A document without a form is an empty mapping. Reads refuse
    more than 4,096 fields or tree nodes, 8,192 edges, 64 levels, 1 MiB of
    names or values, or 4,096 value items, and return nothing then.
    """

    __slots__ = ("_document",)

    def __init__(self, document: Document) -> None:
        """Wrap a document; use ``Document.form``."""
        self._document = document

    def _xrefs(self) -> dict[str, int]:
        """Return the object number of the first field of every name."""
        self._document._ensure_open()
        fields: dict[str, int] = {}
        for name, xref, *_ in self._document._doc.form_fields():
            fields.setdefault(name, xref)
        return fields

    @property
    def font(self) -> Font | None:
        """Return or set the face that value assignments draw text with, or ``None`` for the default.

        By default Helvetica draws WinAnsi text and an installed font extra
        other scripts. Assign an OpenType :class:`Font`, such as
        ``Font.from_file(path)`` or ``Font.installed(language="zh-CN")`` for
        Han text that should not use Japanese glyphs, to draw every later
        assignment with that face; a variable face draws at the instance it
        was loaded at. The setting belongs to this ``Document`` object and is
        not saved. A Standard 14 font raises :class:`PdfError`, because form
        appearances embed the face they draw.
        """
        return self._document._form_font

    @font.setter
    def font(self, font: Font | None) -> None:
        if font is not None:
            if not isinstance(font, Font):
                msg = f"a form font is a Font or None: {font!r}"
                raise TypeError(msg)
            if font._font.standard_name is not None:
                msg = (
                    f"form appearances draw an OpenType Font, not the Standard 14 font {font.name!r}; "
                    "set None to draw WinAnsi text in Helvetica"
                )
                raise PdfError(msg)
        self._document._ensure_open()
        self._document._form_font = font

    def __getitem__(self, name: str) -> Field:
        """Return the field named ``name``; another name raises :class:`KeyError`."""
        if not isinstance(name, str):
            raise KeyError(name)
        xref = self._xrefs().get(name)
        if xref is None:
            raise KeyError(name)
        return Field(self._document, xref)

    def __iter__(self) -> Iterator[str]:
        """Iterate over the field names in sorted order."""
        return iter(list(self._xrefs()))

    def __len__(self) -> int:
        """Return the number of distinct field names."""
        return len(self._xrefs())

    def __repr__(self) -> str:
        """Return ``Form([...])`` with the field names."""
        return f"Form({list(self._xrefs())!r})"


# The package re-exports these names; reprs, pickles, and documentation name them.
for _public in (Field, FieldChoice, FieldFlags, FieldType, Form):
    _public.__module__ = "pylopdf"
