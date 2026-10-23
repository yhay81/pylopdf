"""Form widgets: ``Page.widgets`` and its ``Widget`` handles."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING, overload

from pylopdf._annotations import (
    _BORDER_STYLE_NAMES,
    _BORDER_STYLES,
    AnnotationFlags,
    Border,
    _color,
    _opaque_color,
    _text_value,
)
from pylopdf._content import Color
from pylopdf._errors import StalePageError
from pylopdf._fonts import Font
from pylopdf._geometry import Rect
from pylopdf._hints import hinted_getattr
from pylopdf._limits import _validate_rect
from pylopdf.pylopdf_core import StaleObjectError

if TYPE_CHECKING:
    from pylopdf._form import Field, FieldSpecification
    from pylopdf._objects import Dictionary
    from pylopdf._page import Page
    from pylopdf.pylopdf_core import _Widget as _WidgetTuple

#: The Standard 14 font of each ``/DA`` resource name that form widgets use.
_DA_FONTS = {
    "Helv": "Helvetica",
    "HeBo": "Helvetica-Bold",
    "HeOb": "Helvetica-Oblique",
    "HeBO": "Helvetica-BoldOblique",
    "TiRo": "Times-Roman",
    "TiBo": "Times-Bold",
    "TiIt": "Times-Italic",
    "TiBI": "Times-BoldItalic",
    "Cour": "Courier",
    "CoBo": "Courier-Bold",
    "CoOb": "Courier-Oblique",
    "CoBO": "Courier-BoldOblique",
    "Symb": "Symbol",
    "ZaDb": "ZapfDingbats",
}


class Widget:
    """A handle on one form widget of a page, as ``page.widgets`` lists it.

    A widget is where a :class:`Field` appears on a page; its value is its
    field's value. Properties read the document on every access, and
    assigning one changes the widget and draws it again for its field's
    current value. A handle finds its widget again after an edit moved it; it
    raises :class:`StaleObjectError` once the widget left the page and
    :class:`StalePageError` once the page did.
    """

    __slots__ = ("_index", "_page", "_xref")

    if not TYPE_CHECKING:
        __getattr__ = hinted_getattr

    def __init__(self, page: Page, xref: int, index: int) -> None:
        """Create a handle; ``page.widgets`` and :attr:`Field.widgets` do this."""
        self._page = page
        self._xref = xref
        self._index = index

    def _read(self) -> _WidgetTuple:
        page = self._page
        entry = page._document._doc.read_widget(page._page_number(), self._index, self._xref)
        self._index = entry[0]
        return entry

    def _set(self, *, clear: list[str] | None = None, **entries: object) -> None:
        """Change the widget and draw it again with the face that its field's value needs."""
        page = self._page
        self._read()
        edit: dict[str, object] = {"clear": clear or [], **entries}
        field = self.field
        if field is not None:
            from pylopdf._form import _value_face  # noqa: PLC0415 - the form module imports this one.

            face = _value_face(page._document, field.value or "")
            if face is not None:
                edit["font"] = face
        self._xref = page._document._doc.edit_widget(page._page_number(), self._index, self._xref, edit)

    @property
    def page(self) -> Page:
        """Return the page that shows the widget."""
        return self._page

    @property
    def xref(self) -> int:
        """Return the widget's object number, or 0 for a dictionary stored in ``/Annots`` itself."""
        self._read()
        return self._xref

    @property
    def obj(self) -> Dictionary:
        """Return the widget dictionary as a live low-level handle."""
        self._read()
        if self._xref:
            return self._page._document.objects[self._xref]
        return self._page.obj["Annots"][self._index]

    @property
    def field(self) -> Field | None:
        """Return the terminal form field that the widget shows, or ``None`` when no field owns it."""
        from pylopdf._form import Field  # noqa: PLC0415 - the form module imports this one.

        field = self._read()[2]
        return None if field == 0 else Field(self._page._document, field)

    @property
    def rect(self) -> Rect:
        """Return or set the widget rectangle in display coordinates."""
        return Rect(*self._read()[3])

    @rect.setter
    def rect(self, rect: Sequence[float]) -> None:
        self._set(rect=_validate_rect(rect))

    @property
    def flags(self) -> AnnotationFlags:
        """Return or set the widget's ``/F`` flags, such as :attr:`AnnotationFlags.HIDDEN`."""
        return AnnotationFlags(self._read()[4] & 0x3FF)

    @flags.setter
    def flags(self, flags: AnnotationFlags) -> None:
        if isinstance(flags, bool) or not isinstance(flags, int):
            msg = f"flags is an AnnotationFlags value: {flags!r}"
            raise TypeError(msg)
        self._set(flags=int(AnnotationFlags(flags)))

    @property
    def border(self) -> Border | None:
        """Return or set the border, ``/BS`` else ``/Border``; ``del`` removes it."""
        border = self._read()[6]
        if border is None:
            return None
        width, style, dashes = border
        return Border(width, style=_BORDER_STYLES[style], dashes=dashes)

    @border.setter
    def border(self, border: Border) -> None:
        if not isinstance(border, Border):
            msg = f"border must be a Border: {border!r}"
            raise TypeError(msg)
        self._set(border=(border.width, _BORDER_STYLE_NAMES[border.style], list(border.dashes)))

    @border.deleter
    def border(self) -> None:
        self._set(clear=["border"])

    @property
    def border_color(self) -> Color | None:
        """Return or set the border color, ``/MK /BC``; ``del`` removes it."""
        return _color(self._read()[5][0])

    @border_color.setter
    def border_color(self, color: Color) -> None:
        self._set(border_color=_opaque_color(color, "border_color").rgb)

    @border_color.deleter
    def border_color(self) -> None:
        self._set(clear=["border_color"])

    @property
    def background_color(self) -> Color | None:
        """Return or set the background color, ``/MK /BG``; ``del`` removes it."""
        return _color(self._read()[5][1])

    @background_color.setter
    def background_color(self, color: Color) -> None:
        self._set(background_color=_opaque_color(color, "background_color").rgb)

    @background_color.deleter
    def background_color(self) -> None:
        self._set(clear=["background_color"])

    @property
    def caption(self) -> str | None:
        """Return or set the caption of a pushbutton, ``/MK /CA``; ``del`` removes it."""
        return self._read()[5][2]

    @caption.setter
    def caption(self, caption: str) -> None:
        self._set(caption=_text_value(caption, "caption"))

    @caption.deleter
    def caption(self) -> None:
        self._set(clear=["caption"])

    @property
    def text_color(self) -> Color:
        """Return or set the color of the widget's text, from the ``/DA`` it draws with."""
        appearance = self._read()[7]
        return Color(0, 0, 0) if appearance is None else Color(*appearance[2])

    @text_color.setter
    def text_color(self, color: Color) -> None:
        self._set(text_color=_opaque_color(color, "text_color").rgb)

    @property
    def font(self) -> Font | None:
        """Return or set the Standard 14 font that ``/DA`` names, or ``None`` for another font.

        Values that a Standard 14 font cannot encode are drawn with
        :attr:`Form.font` or an installed font extra, as value assignments
        draw them.
        """
        appearance = self._read()[7]
        name = _DA_FONTS.get("Helv" if appearance is None else appearance[0])
        return None if name is None else Font.standard(name)  # ty: ignore[invalid-argument-type]

    @font.setter
    def font(self, font: Font) -> None:
        if not isinstance(font, Font) or font._font.standard_name is None:
            msg = "a widget names a Standard 14 Font, such as Font.standard('Courier')"
            raise TypeError(msg)
        self._set(font_name=font._font.standard_name)

    @property
    def font_size(self) -> float:
        """Return or set the size of the widget's text in points, 0 to fit the text to the widget."""
        appearance = self._read()[7]
        return 0.0 if appearance is None else appearance[1]

    @font_size.setter
    def font_size(self, size: float) -> None:
        if isinstance(size, bool) or not isinstance(size, (int, float)) or not 0 <= size <= 10000:  # noqa: PLR2004
            msg = f"font_size is a number from 0, which fits the text, to 10000: {size!r}"
            raise ValueError(msg)
        self._set(font_size=float(size))

    @property
    def button_states(self) -> tuple[str, ...]:
        """Return the appearance states of a check box or radio button, such as ``("Off", "Yes")``."""
        return tuple(self._read()[8])

    @property
    def on_state(self) -> str | None:
        """Return the state that selects a check box or radio button, or ``None`` without one."""
        return next((state for state in self._read()[8] if state != "Off"), None)

    def __eq__(self, other: object) -> bool:
        """Compare the page and the widget: its object number, or its index for a direct dictionary."""
        if not isinstance(other, Widget):
            return NotImplemented
        return self._page == other._page and self._key() == other._key()

    def __hash__(self) -> int:
        """Hash consistently with equality."""
        return hash((self._page, self._key()))

    def _key(self) -> tuple[int, int]:
        return (self._xref, 0 if self._xref else self._index)

    def __repr__(self) -> str:
        """Return ``Widget(rect=...)``, or a note for a stale handle."""
        try:
            entry = self._read()
        except (StaleObjectError, StalePageError):
            return "<stale Widget>"
        return f"Widget(rect={Rect(*entry[3])!r})"


class Widgets(Sequence[Widget]):
    """The form widgets of a page, as ``page.widgets`` returns them, in ``/Annots`` order.

    Reads reject more than 4,096 annotations or 1 MiB of captions and state
    names per page.
    """

    __slots__ = ("_page",)

    def __init__(self, page: Page) -> None:
        """Wrap a page; use ``Page.widgets``."""
        self._page = page

    def _handles(self) -> list[Widget]:
        page = self._page
        return [Widget(page, entry[1], entry[0]) for entry in page._document._doc.read_widgets(page._page_number())]

    @overload
    def __getitem__(self, index: int) -> Widget: ...
    @overload
    def __getitem__(self, index: slice) -> list[Widget]: ...
    def __getitem__(self, index: int | slice) -> Widget | list[Widget]:
        """Return one widget, or a list of them for a slice."""
        return self._handles()[index]

    def __len__(self) -> int:
        """Return the number of widgets on the page."""
        return len(self._handles())

    def __iter__(self) -> Iterator[Widget]:
        """Iterate over the widgets that the page lists now."""
        return iter(self._handles())

    def __delitem__(self, index: int | slice) -> None:
        """Remove widgets from the page and their fields; a field goes with its last widget."""
        selected = self._handles()[index]
        widgets = selected if isinstance(selected, list) else [selected]
        page = self._page
        for widget in widgets:
            page._document._doc.remove_widget(page._page_number(), widget._index, widget._xref)

    def add(self, specification: FieldSpecification) -> Widget:
        """Create the field that ``specification`` describes with its widget here, and return the widget.

        A specification whose name is a field of the form adds a widget to
        that field instead, which must be of the same kind, such as another
        button of a radio group; a new field's name has no dots.
        """
        from pylopdf._form import _FIELD_SPECIFICATIONS  # noqa: PLC0415 - the form module imports this one.

        if not isinstance(specification, _FIELD_SPECIFICATIONS):
            msg = f"page.widgets.add takes a field specification: {type(specification).__name__}"
            raise TypeError(msg)
        page = self._page
        xref = page._document._doc.add_widget(page._page_number(), specification._payload(page._document))
        return Widget(page, xref, 0)

    def __repr__(self) -> str:
        """Return ``Widgets([...])`` with the page's widgets."""
        return f"Widgets({list(self)!r})"


# The package re-exports these names; reprs, pickles, and documentation name them.
for _public in (Widget, Widgets):
    _public.__module__ = "pylopdf"
