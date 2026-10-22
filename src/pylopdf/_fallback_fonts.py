"""CJK fallback fonts: ``Document.fallback_fonts`` and its ``FallbackFont`` records."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, MutableMapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from pylopdf import _fonts
from pylopdf._fonts import Font, _normalize_cjk_font_language

if TYPE_CHECKING:
    from pylopdf._document import Document
    from pylopdf._types import CjkFontLanguage

#: The languages in the order of their Adobe character collections' slots.
_LANGUAGES: tuple[CjkFontLanguage, ...] = ("ja", "ko", "zh-CN", "zh-TW")


def _face(value: object, name: str) -> Font | None:
    if value is None:
        return None
    if not isinstance(value, Font):
        msg = f"{name} must be a Font or None: {value!r}"
        raise TypeError(msg)
    if value._font.standard_name is not None:
        msg = f"{name} must be an OpenType face; a Standard 14 font cannot render CJK text: {value.name!r}"
        raise ValueError(msg)
    return value


@dataclass(frozen=True, slots=True)
class FallbackFont:
    """The faces that render one CJK language's fonts that a document does not embed.

    Rendering and extraction pick ``serif`` for a font whose name marks it as
    serif, such as a Mincho, Song, Ming, or Batang face, and ``sans`` for
    every other one; when one of them is ``None``, the other serves both. A
    variable face renders at its default instance.
    """

    sans: Font | None = None
    serif: Font | None = None

    def __post_init__(self) -> None:
        """Validate the faces: OpenType fonts, at least one of them."""
        _face(self.sans, "sans")
        _face(self.serif, "serif")
        if self.sans is None and self.serif is None:
            msg = "a FallbackFont needs a sans face, a serif face, or both"
            raise ValueError(msg)


def _language(language: object) -> CjkFontLanguage:
    if not isinstance(language, str):
        msg = f"a fallback font language is a str: {language!r}"
        raise TypeError(msg)
    return _normalize_cjk_font_language(language)


def _installed(language: CjkFontLanguage) -> FallbackFont:
    """Return the faces of the installed font extra for ``language``."""
    return FallbackFont(Font(language=language), Font(language=language, is_serif=True))


def _installed_languages() -> list[CjkFontLanguage]:
    # Look the providers up through their module, where tests replace them.
    found = {provider.language for provider in _fonts._bundled_font_providers()}
    return [language for language in _LANGUAGES if language in found]


class FallbackFonts(MutableMapping[str, FallbackFont]):
    """The fonts that render non-embedded CJK fonts, as ``doc.fallback_fonts`` returns them.

    The keys are ``"ja"``, ``"ko"``, ``"zh-CN"``, and ``"zh-TW"``, the
    languages of the Adobe-Japan1, Korea1, GB1, and CNS1 character
    collections, and each value is a :class:`FallbackFont`. Until it is
    edited, the mapping lists the installed font extras (``pylopdf[jp]``,
    ``[ko]``, ``[zh-cn]``, and ``[zh-tw]``). Assigning or deleting a language
    edits only that language; assigning a mapping to ``doc.fallback_fonts``
    replaces every language, so ``{}`` turns fallback rendering off, and
    ``del doc.fallback_fonts`` returns to the installed extras.
    """

    __slots__ = ("_document",)

    def __init__(self, document: Document) -> None:
        """Wrap a document; use ``Document.fallback_fonts``."""
        self._document = document

    def _state(self) -> dict[CjkFontLanguage, FallbackFont | None]:
        """Return the configured languages, ``None`` for an installed extra not yet loaded as a Font."""
        document = self._document
        document._ensure_open()
        if document._fallback_state is None:
            return dict.fromkeys(_installed_languages())
        return document._fallback_state

    def __getitem__(self, language: str) -> FallbackFont:
        """Return the faces that render ``language``."""
        key = _language(language)
        state = self._state()
        if key not in state:
            raise KeyError(language)
        value = state[key]
        if value is None:
            value = _installed(key)
            if self._document._fallback_state is not None:
                self._document._fallback_state[key] = value
        return value

    def __setitem__(self, language: str, value: FallbackFont) -> None:
        """Render ``language`` with ``value``'s faces."""
        key = _language(language)
        if not isinstance(value, FallbackFont):
            msg = f"a fallback font is a FallbackFont record: {value!r}"
            raise TypeError(msg)
        state = self._edit()
        _apply_fallback_font(self._document, key, value)
        state[key] = value

    def __delitem__(self, language: str) -> None:
        """Stop rendering ``language`` with a fallback font."""
        key = _language(language)
        if key not in self._state():
            raise KeyError(language)
        state = self._edit()
        _apply_fallback_font(self._document, key, None)
        del state[key]

    def __iter__(self) -> Iterator[str]:
        """Iterate over the configured languages."""
        return iter(list(self._state()))

    def __len__(self) -> int:
        """Return the number of configured languages."""
        return len(self._state())

    def __repr__(self) -> str:
        """Return ``FallbackFonts([...])`` with the configured languages."""
        return f"FallbackFonts({list(self._state())!r})"

    def _edit(self) -> dict[CjkFontLanguage, FallbackFont | None]:
        """Leave the installed extras' default, keeping them for the other languages."""
        document = self._document
        document._ensure_open()
        # Fill the slots first, so that the edit lands on what rendering uses.
        document._ensure_fallback_fonts()
        if document._fallback_state is None:
            document._fallback_state = dict.fromkeys(_installed_languages())
        return document._fallback_state


def _apply_fallback_font(document: Document, language: CjkFontLanguage, value: FallbackFont | None) -> None:
    """Put ``value``'s faces in the core's slots for ``language``, or empty them."""
    core = document._doc
    for kind in ("sans", "serif"):
        face = None if value is None else cast("Font | None", getattr(value, kind))
        core.set_fallback_face(language, kind, None if face is None else face._font)


def _replace_fallback_fonts(document: Document, fonts: Mapping[str, FallbackFont]) -> None:
    """Replace every language's fallback faces, validated before any change."""
    document._ensure_open()
    if not isinstance(fonts, Mapping):
        msg = f"fallback fonts are a mapping from language to FallbackFont: {type(fonts).__name__}"
        raise TypeError(msg)
    prepared: dict[CjkFontLanguage, FallbackFont | None] = {}
    for language, value in fonts.items():
        key = _language(language)
        if not isinstance(value, FallbackFont):
            msg = f"a fallback font is a FallbackFont record: {value!r}"
            raise TypeError(msg)
        if key in prepared:
            msg = f"the fallback fonts name {key!r} twice"
            raise ValueError(msg)
        prepared[key] = value
    document._doc.clear_fallback_fonts()
    document._fallback_configured = True
    for key, value in prepared.items():
        _apply_fallback_font(document, key, value)
    document._fallback_state = prepared


def _reset_fallback_fonts(document: Document) -> None:
    """Return to the installed font extras."""
    document._ensure_open()
    document._doc.clear_fallback_fonts()
    document._fallback_configured = False
    document._fallback_state = None


# The package re-exports these names; reprs, pickles, and documentation name them.
for _public in (FallbackFont, FallbackFonts):
    _public.__module__ = "pylopdf"
