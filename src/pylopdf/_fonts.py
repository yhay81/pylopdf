"""Fonts for generated text: Font, installed font extras, and script detection."""

from __future__ import annotations

import functools
import importlib
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast, get_args

from pylopdf._hints import hinted_getattr
from pylopdf._limits import (
    _DEFAULT_MAX_FONT_INPUT_SIZE,
    _DEFAULT_MAX_GENERATED_TEXT_SIZE,
    _UINT32_MAX,
    _validate_optional_positive_int,
    _validate_utf8_text_input,
)
from pylopdf._types import StandardFontName
from pylopdf.pylopdf_core import LimitError, PdfError, _Font

if TYPE_CHECKING:
    from typing import Any

    from pylopdf._types import CjkFontLanguage, FontLanguage


@dataclass(frozen=True)
class _BundledFontProvider:
    """One installed locale-specific font package."""

    language: FontLanguage
    extra: str
    sans: Path | bytes
    serif: Path | bytes


_FONT_PROVIDER_SPECS: tuple[tuple[FontLanguage, str, str], ...] = (
    ("ar", "ar", "pylopdf_fonts_ar"),
    ("he", "he", "pylopdf_fonts_he"),
    ("hi", "hi", "pylopdf_fonts_hi"),
    ("ja", "jp", "pylopdf_fonts_jp"),
    ("ko", "ko", "pylopdf_fonts_ko"),
    ("th", "th", "pylopdf_fonts_th"),
    ("zh-CN", "zh-cn", "pylopdf_fonts_zh_cn"),
    ("zh-TW", "zh-tw", "pylopdf_fonts_zh_tw"),
)


def _import_optional_font_module(module_name: str) -> object | None:
    """Import one known data package without hiding its internal failures."""
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return None
        raise


@functools.cache
def _bundled_font_providers() -> tuple[_BundledFontProvider, ...]:
    """Locate installed locale font packages."""
    providers: list[_BundledFontProvider] = []
    for language, extra, module_name in _FONT_PROVIDER_SPECS:
        module = _import_optional_font_module(module_name)
        if module is None:
            continue
        font_module = cast("Any", module)
        providers.append(_BundledFontProvider(language, extra, font_module.sans_path(), font_module.serif_path()))
    return tuple(providers)


def _font_input_limit_error(size: int, max_font_size: int) -> LimitError:
    """Build the public error for an oversized OpenType font source."""
    return LimitError(
        "font_input_size",
        f"font input is {size} bytes, exceeding the {max_font_size}-byte limit",
    )


def _resolve_font_source(
    fontfile: str | os.PathLike[str] | None,
    fontbuffer: bytes | None,
    max_font_size: int | None,
) -> Path | bytes | None:
    """Resolve optional font input without reading a filesystem path."""
    if fontfile is not None:
        if fontbuffer is not None:
            msg = "fontfile and fontbuffer cannot both be specified"
            raise ValueError(msg)
        return Path(fontfile)
    if fontbuffer is not None:
        if max_font_size is not None and len(fontbuffer) > max_font_size:
            raise _font_input_limit_error(len(fontbuffer), max_font_size)
        data = bytes(fontbuffer)
        if max_font_size is not None and len(data) > max_font_size:
            raise _font_input_limit_error(len(data), max_font_size)
        return data
    return None


#: Script ranges used only to select an installed CJK locale font package.
_KANA_RANGES = ((0x3040, 0x30FF), (0x31F0, 0x31FF), (0xFF65, 0xFF9F))
_HANGUL_RANGES = (
    (0x1100, 0x11FF),
    (0x3130, 0x318F),
    (0xA960, 0xA97F),
    (0xAC00, 0xD7AF),
    (0xD7B0, 0xD7FF),
)
_BOPOMOFO_RANGES = ((0x3100, 0x312F), (0x31A0, 0x31BF))
_ARABIC_RANGES = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))
_HEBREW_RANGES = ((0x0590, 0x05FF), (0xFB1D, 0xFB4F))
_DEVANAGARI_RANGES = ((0x0900, 0x097F), (0xA8E0, 0xA8FF))
_THAI_RANGES = ((0x0E00, 0x0E7F),)
_HAN_RANGES = (
    (0x2E80, 0x2FFF),
    (0x3400, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2FA1F),
)
_FONT_LANGUAGE_ALIASES: dict[str, FontLanguage] = {
    "ar": "ar",
    "he": "he",
    "hi": "hi",
    "ja": "ja",
    "ko": "ko",
    "th": "th",
    "zh-cn": "zh-CN",
    "zh-hans": "zh-CN",
    "zh-tw": "zh-TW",
    "zh-hant": "zh-TW",
}
_SCRIPT_PROVIDER_RANGES: tuple[tuple[FontLanguage, tuple[tuple[int, int], ...]], ...] = (
    ("ar", _ARABIC_RANGES),
    ("he", _HEBREW_RANGES),
    ("hi", _DEVANAGARI_RANGES),
    ("ja", _KANA_RANGES),
    ("ko", _HANGUL_RANGES),
    ("zh-TW", _BOPOMOFO_RANGES),
    ("th", _THAI_RANGES),
)


def _text_uses_ranges(text: str, ranges: tuple[tuple[int, int], ...]) -> bool:
    """Return whether text contains a scalar in one of the supplied ranges."""
    return any(start <= ord(character) <= end for character in text for start, end in ranges)


def _normalize_font_language(language: str | None) -> FontLanguage | None:
    """Normalize supported BCP 47 font locales."""
    if language is None:
        return None
    if not isinstance(language, str):
        msg = f"font_language must be a string or None: {language!r}"
        raise TypeError(msg)
    normalized = _FONT_LANGUAGE_ALIASES.get(language.replace("_", "-").lower())
    if normalized is None:
        msg = "font_language must be 'ar', 'he', 'hi', 'ja', 'ko', 'th', 'zh-CN', 'zh-TW', or None"
        raise ValueError(msg)
    return normalized


def _normalize_cjk_font_language(language: str) -> CjkFontLanguage:
    """Normalize a locale accepted by the non-embedded CJK fallback API."""
    normalized = _normalize_font_language(language)
    if normalized not in {"ja", "ko", "zh-CN", "zh-TW"}:
        msg = "font_language for fallback must be 'ja', 'ko', 'zh-CN', or 'zh-TW'"
        raise ValueError(msg)
    return normalized


def _provider_for_language(language: FontLanguage) -> _BundledFontProvider | None:
    """Return the installed provider for one normalized locale."""
    return next((provider for provider in _bundled_font_providers() if provider.language == language), None)


def _detected_font_language(text: str) -> FontLanguage | None:
    """Infer an unambiguous script marker, then preserve the JP Han default."""
    markers = [language for language, ranges in _SCRIPT_PROVIDER_RANGES if _text_uses_ranges(text, ranges)]
    if len(markers) > 1:
        msg = "text spans multiple bundled font locales; pass an explicit fontfile or fontbuffer"
        raise ValueError(msg)
    if markers:
        return markers[0]
    if not _text_uses_ranges(text, _HAN_RANGES):
        return None
    installed = {provider.language for provider in _bundled_font_providers()}
    for candidate in ("ja", "zh-CN", "zh-TW", "ko"):
        if candidate in installed:
            return cast("FontLanguage", candidate)
    return None


def _bundled_generation_font(
    text: str,
    font_language: str | None,
) -> tuple[Path | bytes, tuple[FontLanguage, str]] | None:
    """Select the sans face of an installed font extra for a complete generated run.

    Return the font source and its ``(language, kind)`` package face, which
    lets the core share an installed package file across calls.
    """
    language = _normalize_font_language(font_language)
    if language is None:
        language = _detected_font_language(text)
    if language is None:
        return None
    provider = _provider_for_language(language)
    if provider is None:
        extra = next(extra for candidate, extra, _ in _FONT_PROVIDER_SPECS if candidate == language)
        msg = f"font_language={language!r} requires pylopdf[{extra}] or an explicit font"
        raise ValueError(msg)
    return provider.sans, (language, "sans")


def _resolve_generation_font(text: str) -> tuple[str | None, Path | bytes | None, tuple[FontLanguage, str] | None]:
    """Resolve the face that draws ``text`` without a font: an installed extra for its script, else Helvetica.

    Return the Standard 14 font name, the embedded font source, and the
    package face of an automatically selected installed font.
    """
    bundled_font = _bundled_generation_font(text, None)
    if bundled_font is not None:
        source, package_font = bundled_font
        return None, source, package_font
    try:
        text.encode("cp1252")
    except UnicodeEncodeError as exc:
        msg = (
            "text generation can only print WinAnsi (Latin-1-equivalent) characters without an embedded font. "
            "Install a matching optional font extra or pass a Font"
        )
        raise ValueError(msg) from exc
    return "Helvetica", None, None


def _form_generation_font(
    value: str,
    font_language: FontLanguage | None,
) -> tuple[Path | bytes, tuple[FontLanguage, str]] | None:
    """Select an installed font for a form value Helvetica cannot draw.

    An explicit ``font_language`` selects its package even for WinAnsi text.
    """
    if font_language is not None:
        return _bundled_generation_font(value, font_language)
    try:
        value.encode("cp1252")
    except UnicodeEncodeError:
        return _bundled_generation_font(value, None)
    return None


#: The PostScript names that :meth:`Font.standard` takes.
_STANDARD_FONT_NAMES: tuple[str, ...] = get_args(StandardFontName)
#: Family names of the faces that each font extra installs: sans, then serif.
_FONT_EXTRA_FAMILIES: dict[FontLanguage, tuple[str, str]] = {
    "ar": ("Noto Sans Arabic", "Noto Naskh Arabic"),
    "he": ("Noto Sans Hebrew", "Noto Serif Hebrew"),
    "hi": ("Noto Sans Devanagari", "Noto Serif Devanagari"),
    "ja": ("Noto Sans JP", "Noto Serif JP"),
    "ko": ("Noto Sans KR", "Noto Serif KR"),
    "th": ("Noto Sans Thai", "Noto Serif Thai"),
    "zh-CN": ("Noto Sans SC", "Noto Serif SC"),
    "zh-TW": ("Noto Sans TC", "Noto Serif TC"),
}
_REGULAR_FONT_WEIGHT = 400
_BOLD_FONT_WEIGHT = 700
_MAX_FONT_WEIGHT = 1000


def _validate_font_weight(weight: object) -> int | None:
    """Validate an optional OS/2 weight class."""
    if weight is None:
        return None
    if isinstance(weight, bool) or not isinstance(weight, int):
        msg = f"weight must be an OS/2 weight class from 1 through {_MAX_FONT_WEIGHT} or None: {weight!r}"
        raise TypeError(msg)
    if not 1 <= weight <= _MAX_FONT_WEIGHT:
        msg = f"weight must be an OS/2 weight class from 1 through {_MAX_FONT_WEIGHT} or None: {weight!r}"
        raise ValueError(msg)
    return weight


def _validate_font_index(index: object) -> int:
    """Validate the index of a face in a font collection."""
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= _UINT32_MAX:
        msg = f"index must be an integer from 0 through {_UINT32_MAX}: {index!r}"
        raise ValueError(msg)
    return index


def _describe_face(weight: int | None, *, italic: bool) -> str:
    """Describe a requested face for error messages."""
    name = {None: "regular", 400: "regular", _BOLD_FONT_WEIGHT: "bold"}.get(weight, f"weight-{weight}")
    return f"{name} italic" if italic else name


def _font_extra_family(family: str) -> tuple[FontLanguage, bool]:
    """Return the font extra and style that install a family."""
    if not isinstance(family, str):
        msg = f"family must be a string: {family!r}"
        raise TypeError(msg)
    for language, names in _FONT_EXTRA_FAMILIES.items():
        for serif, name in enumerate(names):
            if name.casefold() == family.casefold():
                return language, bool(serif)
    msg = f"no pylopdf font extra installs the family {family!r}; load its font with Font.from_file()"
    raise PdfError(msg)


def _font_extra_face(language: FontLanguage, *, serif: bool, weight: int | None, italic: bool) -> _Font:
    """Select the face that an installed font extra provides for one style.

    An extra installs one face per style. A variable face, which the ``ar``,
    ``he``, ``hi``, and ``th`` extras install, provides every weight on its
    weight axis as an instance and is drawn at the regular weight when no
    weight is requested. A static face must match a requested weight, and
    any face must match a requested italic.
    """
    extra = next(extra for candidate, extra, _ in _FONT_PROVIDER_SPECS if candidate == language)
    provider = _provider_for_language(language)
    if provider is None:
        msg = f"language={language!r} requires pylopdf[{extra}]; load another font with Font.from_file()"
        raise PdfError(msg)
    kind = "serif" if serif else "sans"
    source = provider.serif if serif else provider.sans
    if isinstance(source, Path):
        face = _Font.from_file(os.fspath(source), 0, _DEFAULT_MAX_FONT_INPUT_SIZE, (language, kind))
    else:
        face = _Font.from_bytes(source, 0, _DEFAULT_MAX_FONT_INPUT_SIZE)
    weight_range = face.weight_range
    if not italic:
        if weight is None:
            return _regular_instance(face)
        if weight_range is None and weight == face.weight:
            return face
        if weight_range is not None and weight_range[0] <= weight <= weight_range[2]:
            return face.with_weight(weight)
    family = face.family or _FONT_EXTRA_FAMILIES[language][serif]
    described = _describe_face(weight, italic=italic)
    if weight_range is not None and not italic:
        low, _, high = weight_range
        msg = (
            f"pylopdf[{extra}] installs {family!r} with weights {low:g} through {high:g}; "
            f"load its {described} face with Font.from_file()"
        )
    else:
        msg = f"pylopdf[{extra}] installs one {family!r} face; load its {described} face with Font.from_file()"
    raise PdfError(msg)


def _regular_instance(face: _Font) -> _Font:
    """Return a face at the regular weight when it has a weight axis.

    The default instance of a variable face need not be regular: that of
    Noto Sans Hebrew in ``pylopdf[he]`` is Thin.
    """
    return face if face.weight_range is None else face.with_weight(_REGULAR_FONT_WEIGHT)


def _face_at_weight(face: _Font, weight: int) -> _Font:
    """Select ``weight`` on a face loaded from a file or bytes.

    A variable face provides the instance at any weight on its weight axis and
    refuses a weight outside it. Any other face has one weight, its OS/2
    weight class, which must be the one requested: pylopdf never imitates a
    weight that the font does not define.
    """
    if face.weight_range is not None:
        return face.with_weight(weight)
    if face.weight == weight:
        return face
    current = _describe_face(face.weight, italic=False)
    requested = _describe_face(weight, italic=False)
    msg = (
        f"the font has no weight axis, and its only face is {current}, not {requested}; "
        f"load the {requested} face's file instead"
    )
    raise PdfError(msg)


def _package_face_weight(package_font: tuple[FontLanguage, str] | None) -> int | None:
    """Return the weight that drawing selects on an installed package face.

    A face of an installed font extra is drawn at the regular weight, as
    ``Font.installed()`` selects it; any other font input is drawn as given.
    """
    return None if package_font is None else _REGULAR_FONT_WEIGHT


def _measure_size(value: object, name: str) -> float:
    """Return a positive finite measure, such as a font size or a box width."""
    if isinstance(value, (bool, str, bytes)) or not hasattr(value, "__float__"):
        msg = f"{name} must be a positive number: {value!r}"
        raise TypeError(msg)
    number = float(cast("float", value))
    if not (math.isfinite(number) and number > 0):
        msg = f"{name} must be a positive number: {value!r}"
        raise ValueError(msg)
    return number


def _textbox_text(text: object, max_text_size: int | None) -> str:
    """Return text as a textbox lays it out, with every line break a newline and every tab one space.

    The text is first cut one character past ``max_text_size``, since every
    character takes at least one UTF-8 byte; the core counts the bytes exactly.
    """
    if not isinstance(text, str):
        msg = f"text must be a string: {text!r}"
        raise TypeError(msg)
    _validate_optional_positive_int("max_text_size", max_text_size)
    if max_text_size is not None and len(text) > max_text_size:
        text = text[: max_text_size + 1]
    return text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(1)


_CONSTRUCTOR_MESSAGE = (
    "Font has no public constructor: Font.standard(name) takes a Standard 14 PostScript name such as "
    '"Helvetica-Bold", Font.from_file(path) and Font.from_bytes(data) load an OpenType face, and '
    "Font.installed(language=...) selects the face of an installed font extra; "
    "python -m pylopdf.migrate rewrites Font(...) calls"
)


class Font:
    """A font face that draws and measures text: a Standard 14 font or an OpenType face.

    Get one from :meth:`standard`, :meth:`from_file`, :meth:`from_bytes`, or
    :meth:`installed`; pylopdf has no default font and keeps no font
    registry, so output depends only on the fonts passed in. A Font reads its
    input once. Pass it as ``font`` to :meth:`Canvas.text` and
    :meth:`Canvas.textbox` on any document, and share it between threads.
    :meth:`text_width` measures one line and :meth:`text_height` wrapped text
    exactly as the canvas draws them.

    An OpenType face is shaped with HarfRust and embedded as a subset by
    krilla. A Standard 14 font is drawn without embedding, so it prints
    WinAnsi (roughly Latin-1) text only, and measures with Adobe's AFM
    widths. pylopdf never imitates a weight or style that a face lacks by
    stroking or overprinting glyphs: load a bold face from its own file, or
    select the bold instance of a variable face with ``weight``.
    """

    __slots__ = ("_font",)

    if not TYPE_CHECKING:
        __getattr__ = hinted_getattr

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        """Refuse direct construction; the class methods create fonts."""
        raise TypeError(_CONSTRUCTOR_MESSAGE)

    @classmethod
    def _wrap(cls, face: _Font) -> Font:
        """Wrap a core face."""
        font = cls.__new__(cls)
        font._font = face
        return font

    @classmethod
    def standard(cls, name: StandardFontName) -> Font:
        """Return a Standard 14 font by its PostScript name, such as ``"Helvetica-Bold"``.

        The 14 names are those of :data:`StandardFontName`. A Standard 14 font
        is drawn without embedding, so it prints WinAnsi (roughly Latin-1)
        text only.
        """
        if not isinstance(name, str):
            msg = f"name must be a string: {name!r}"
            raise TypeError(msg)
        if name not in _STANDARD_FONT_NAMES:
            names = ", ".join(_STANDARD_FONT_NAMES)
            msg = f"name must be the PostScript name of a Standard 14 font ({names}): {name!r}"
            raise ValueError(msg)
        return cls._wrap(_Font.standard(name))

    @classmethod
    def from_file(
        cls,
        path: str | os.PathLike[str],
        /,
        *,
        index: int = 0,
        weight: int | None = None,
        max_size: int | None = _DEFAULT_MAX_FONT_INPUT_SIZE,
    ) -> Font:
        """Load an OpenType face (TrueType or CFF outlines) from a font file.

        ``index`` selects a face in a collection such as a ``.ttc`` file. A
        variable face is used at its default instance, which need not be
        regular (the variable Noto Sans CJK JP defaults to Thin); ``weight``,
        a weight class from 1 through 1000, selects the instance at that
        weight on its weight axis instead and refuses a weight outside the
        axis. A static face must already have the requested weight class. A
        missing file raises :class:`FileNotFoundError`. The file is limited
        to 64 MiB by default (``max_size``, ``None`` for trusted input),
        refused with :class:`LimitError` code ``font_input_size``.
        """
        _validate_optional_positive_int("max_size", max_size)
        resolved_weight = _validate_font_weight(weight)
        face_index = _validate_font_index(index)
        if not isinstance(path, (str, os.PathLike)):
            msg = f"path must be a str or os.PathLike, not {type(path).__name__}"
            raise TypeError(msg)
        face = _Font.from_file(os.fspath(path), face_index, max_size)
        return cls._wrap(face if resolved_weight is None else _face_at_weight(face, resolved_weight))

    @classmethod
    def from_bytes(
        cls,
        data: bytes | bytearray | memoryview,
        /,
        *,
        index: int = 0,
        weight: int | None = None,
        max_size: int | None = _DEFAULT_MAX_FONT_INPUT_SIZE,
    ) -> Font:
        """Load an OpenType face from the bytes of a font file.

        ``index``, ``weight``, and ``max_size`` work as for :meth:`from_file`.
        """
        _validate_optional_positive_int("max_size", max_size)
        resolved_weight = _validate_font_weight(weight)
        face_index = _validate_font_index(index)
        if not isinstance(data, (bytes, bytearray, memoryview)):
            msg = f"data must be bytes, bytearray, or memoryview, not {type(data).__name__}"
            raise TypeError(msg)
        size = memoryview(data).nbytes
        if max_size is not None and size > max_size:
            raise _font_input_limit_error(size, max_size)
        face = _Font.from_bytes(bytes(data), face_index, max_size)
        return cls._wrap(face if resolved_weight is None else _face_at_weight(face, resolved_weight))

    @classmethod
    def installed(
        cls,
        *,
        language: FontLanguage | None = None,
        family: str | None = None,
        serif: bool = False,
        weight: int | None = None,
        italic: bool = False,
    ) -> Font:
        """Select the face of an installed font extra, such as ``pylopdf[jp]``.

        Pass ``language``, which selects the extra's sans face, or its serif
        face with ``serif=True``, or ``family``, a family name that an extra
        installs such as ``"Noto Serif JP"``. The extras install one face per
        style, drawn at the regular weight unless ``weight`` requests
        another. The ``ar``, ``he``, ``hi``, and ``th`` extras install
        variable faces, which provide the weights on their weight axis (100
        through 900, or 400 through 700 for Noto Naskh Arabic); the others
        install regular static faces. A weight or an italic style that the
        extra does not provide raises :class:`PdfError`: load such a face
        with :meth:`from_file`.
        """
        if (language is None) == (family is None):
            msg = "pass language or family"
            raise ValueError(msg)
        resolved_weight = _validate_font_weight(weight)
        if family is not None:
            if serif:
                msg = "serif selects the face of a language; family already names the face"
                raise ValueError(msg)
            extra_language, is_serif = _font_extra_family(family)
        else:
            extra_language = cast("FontLanguage", _normalize_font_language(language))
            is_serif = bool(serif)
        face = _font_extra_face(extra_language, serif=is_serif, weight=resolved_weight, italic=bool(italic))
        return cls._wrap(face)

    @property
    def name(self) -> str | None:
        """The family and style of an OpenType face, or the PostScript name of a Standard 14 font.

        An instance of a variable face takes its style from the instance, as in
        ``Noto Sans Hebrew Bold``; drawn text embeds it under the instance's
        PostScript name, ``NotoSansHebrew-Bold``.
        """
        return self._font.name

    @property
    def family(self) -> str | None:
        """The typographic family name (name ID 16, else 1), or ``None``."""
        return self._font.family

    @property
    def weight(self) -> int:
        """The weight: 400 for regular and 700 for bold faces.

        An instance selected on a variable face's weight axis reports the axis
        value it is drawn at; a face used as given reports its OS/2 weight
        class.
        """
        return self._font.weight

    @property
    def is_bold(self) -> bool:
        """Whether the face is bold by its weight class or style flags."""
        return self._font.bold

    @property
    def is_italic(self) -> bool:
        """Whether the face is marked italic or oblique."""
        return self._font.italic

    @property
    def is_serif(self) -> bool:
        """Whether the face has serifs, by its PANOSE class or family name."""
        return self._font.serif

    @property
    def units_per_em(self) -> int:
        """The design units per em of the face; 1000 for Standard 14 fonts."""
        return self._font.units_per_em

    @property
    def ascender(self) -> float:
        """The ascent above the baseline as a fraction of the font size.

        An OpenType face reports what FreeType reads: the OS/2 typographic
        ascender when the face sets ``USE_TYPO_METRICS``, else the ``hhea``
        ascender, or the typographic or Windows one when both ``hhea`` values
        are zero; a value that is not positive or exceeds 8 becomes 0.8. A
        Standard 14 font reports the bounding box of the URW font that
        renderers commonly substitute for it, such as 1.075 for Helvetica. A
        variable face reports its default instance. The canvas places lines
        of text with :attr:`ascender` and :attr:`descender`.
        """
        return self._font.ascender

    @property
    def descender(self) -> float:
        """The descent below the baseline as a negative fraction of the font size.

        It comes from the same table as :attr:`ascender`; a positive value is
        negated, and one beyond 3 either way becomes -0.2.
        """
        return self._font.descender

    def text_width(
        self,
        text: str,
        size: float,
        *,
        max_text_size: int | None = _DEFAULT_MAX_GENERATED_TEXT_SIZE,
    ) -> float:
        """Return the width of one line of ``text`` at ``size`` points.

        OpenType text is shaped with HarfRust exactly as :meth:`Canvas.text`
        shapes it, so the result is the advance that it draws, including
        kerning and ligatures; Standard 14 text sums the AFM widths of its
        WinAnsi characters. Measure each line separately: a line break
        raises :class:`PdfError`, as does a character the font has no glyph
        for. Text is limited to 1 MiB of UTF-8 by default
        (``max_text_size``, ``None`` for trusted input), refused with
        :class:`LimitError` code ``text_input_size``.
        """
        if not isinstance(text, str):
            msg = f"text must be a string: {text!r}"
            raise TypeError(msg)
        _validate_utf8_text_input(text, max_text_size)
        if "\n" in text or "\r" in text:
            msg = "text_width measures one line; split text at line breaks first"
            raise PdfError(msg)
        return self._font.text_width(text, _measure_size(size, "size"), max_text_size)

    def text_height(
        self,
        text: str,
        size: float,
        *,
        width: float,
        line_height: float | None = None,
        max_text_size: int | None = _DEFAULT_MAX_GENERATED_TEXT_SIZE,
    ) -> float:
        """Return the height that :meth:`Canvas.textbox` needs for ``text`` wrapped ``width`` points wide.

        The text wraps and its lines are placed exactly as the textbox
        places them at ``size`` and ``line_height``: the first baseline one
        :attr:`ascender` below the top, later ones ``line_height`` times
        ``size`` apart (by default the font's line height), and the height
        ends at the :attr:`descender` of the last line. A textbox of this
        height and ``width`` therefore fits the text, so a page can be
        planned before anything is drawn. Empty text needs no height. A word
        that cannot fit ``width`` even one grapheme at a time raises
        :class:`PdfError`. Text is limited to 1 MiB of UTF-8 and 4,096 lines
        by default (``max_text_size``, ``None`` for trusted input), refused
        with :class:`LimitError` code ``text_input_size`` or
        ``text_line_count``.
        """
        normalized = _textbox_text(text, max_text_size)
        font_size = _measure_size(size, "size")
        box_width = _measure_size(width, "width")
        spacing = None if line_height is None else _measure_size(line_height, "line_height")
        if not normalized:
            return 0.0
        return self._font.text_height(normalized, font_size, box_width, spacing, max_text_size)

    def has_glyph(self, char: str) -> bool:
        """Return whether the face has a glyph for the one-character string ``char``.

        A Standard 14 font has one when its encoding, WinAnsi for the text
        fonts, maps the character to a glyph of the font.
        """
        if not isinstance(char, str):
            msg = f"char must be a one-character string: {char!r}"
            raise TypeError(msg)
        if len(char) != 1:
            msg = f"char must be a one-character string: {char!r}"
            raise ValueError(msg)
        return self._font.has_glyph(ord(char))

    def __repr__(self) -> str:
        """Return the font's name."""
        return f"Font({self.name!r})"


def _require_winansi(text: str) -> None:
    """Refuse text that a Standard 14 font cannot print."""
    try:
        text.encode("cp1252")
    except UnicodeEncodeError as exc:
        msg = (
            "Standard 14 fonts can only print WinAnsi (Latin-1-equivalent) characters; "
            "pass an OpenType Font for other text"
        )
        raise ValueError(msg) from exc


def _validate_font_choice(
    font: Font,
    fontfile: str | os.PathLike[str] | None,
    fontbuffer: bytes | None,
    fontindex: int,
    font_language: str | None,
) -> _Font:
    """Return the core face of a ``font`` argument that no other source contradicts."""
    if not isinstance(font, Font):
        msg = f"font must be a pylopdf.Font, not {type(font).__name__}"
        raise TypeError(msg)
    if fontfile is not None or fontbuffer is not None or fontindex != 0 or font_language is not None:
        msg = "font cannot be combined with fontfile, fontbuffer, fontindex, or font_language"
        raise ValueError(msg)
    return font._font


def _validate_form_font(
    font: Font | None,
    fontfile: str | os.PathLike[str] | None,
    fontbuffer: bytes | None,
    fontindex: int,
    font_language: str | None,
) -> _Font | None:
    """Return the OpenType face of a form ``font`` argument, or ``None`` without one.

    Form appearances embed the face they draw; the Standard 14 path of form
    filling draws Helvetica only.
    """
    if font is None:
        return None
    core_font = _validate_font_choice(font, fontfile, fontbuffer, fontindex, font_language)
    if core_font.standard_name is not None:
        msg = (
            f"set_form_field draws with an OpenType Font, not the Standard 14 font {core_font.standard_name!r}; "
            "omit font to draw WinAnsi text in Helvetica"
        )
        raise PdfError(msg)
    return core_font


# The package re-exports this class; reprs, pickles, and documentation name it.
Font.__module__ = "pylopdf"
