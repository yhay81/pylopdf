"""Fonts for generated text: Font, installed font extras, and script detection."""

from __future__ import annotations

import functools
import importlib
import math
import os
import warnings as _warnings
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from pylopdf._errors import PylopdfWarning
from pylopdf._hints import hinted_getattr
from pylopdf._limits import (
    _DEFAULT_MAX_FONT_INPUT_SIZE,
    _DEFAULT_MAX_GENERATED_TEXT_SIZE,
    _UINT32_MAX,
    _validate_optional_positive_int,
    _validate_utf8_text_input,
)
from pylopdf.pylopdf_core import LimitError, PdfError, _Font

if TYPE_CHECKING:
    from collections.abc import Mapping
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


#: Map pymupdf-style insert_text aliases to Standard 14 font names.
_BASE14_FONTS: dict[str, str] = {
    "helv": "Helvetica",
    "hebo": "Helvetica-Bold",
    "heit": "Helvetica-Oblique",
    "hebi": "Helvetica-BoldOblique",
    "cour": "Courier",
    "cobo": "Courier-Bold",
    "coit": "Courier-Oblique",
    "cobi": "Courier-BoldOblique",
    "tiro": "Times-Roman",
    "tibo": "Times-Bold",
    "tiit": "Times-Italic",
    "tibi": "Times-BoldItalic",
    "symb": "Symbol",
    "zadb": "ZapfDingbats",
}

#: Times aliases select an optional locale-specific serif generation font.
_SERIF_BASE14_FONTS = frozenset({"tiro", "tibo", "tiit", "tibi"})

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
    fontname: str,
    font_language: str | None,
) -> tuple[Path | bytes, tuple[FontLanguage, str]] | None:
    """Select one optional locale font for a complete generated run.

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
        msg = f"font_language={language!r} requires pylopdf[{extra}] or an explicit fontfile/fontbuffer"
        raise ValueError(msg)
    kind = "serif" if fontname in _SERIF_BASE14_FONTS else "sans"
    return (provider.serif if kind == "serif" else provider.sans), (language, kind)


def _resolve_generation_font(
    text: str,
    fontname: str,
    font_data: Path | bytes | None,
    fontindex: int,
    font_language: str | None,
) -> tuple[str | None, Path | bytes | None, tuple[FontLanguage, str] | None]:
    """Resolve explicit, optional locale, or Standard 14 generation input.

    Return the Standard 14 font name, the embedded font source, and the
    package face of an automatically selected installed font.
    """
    normalized_language = _normalize_font_language(font_language)
    if font_data is not None:
        if normalized_language is not None:
            msg = "font_language cannot be combined with fontfile or fontbuffer"
            raise ValueError(msg)
        if isinstance(fontindex, bool) or not isinstance(fontindex, int) or not 0 <= fontindex <= _UINT32_MAX:
            msg = f"fontindex must be an integer from 0 through 4294967295: {fontindex!r}"
            raise ValueError(msg)
        return None, font_data, None
    if fontindex != 0:
        msg = "fontindex requires fontfile or fontbuffer"
        raise ValueError(msg)

    base_font = _standard_font_name(fontname)
    bundled_font = _bundled_generation_font(text, _BASE14_ALIASES[base_font], normalized_language)
    if bundled_font is not None:
        source, package_font = bundled_font
        return None, source, package_font
    try:
        text.encode("cp1252")
    except UnicodeEncodeError as exc:
        msg = (
            "text generation can only print WinAnsi (Latin-1-equivalent) characters without an embedded font. "
            "Install a matching optional font extra or pass fontfile or fontbuffer"
        )
        raise ValueError(msg) from exc
    return base_font, None, None


def _form_generation_font(
    value: str,
    font_language: FontLanguage | None,
) -> tuple[Path | bytes, tuple[FontLanguage, str]] | None:
    """Select an installed font for a form value Helvetica cannot draw.

    An explicit ``font_language`` selects its package even for WinAnsi text.
    """
    if font_language is not None:
        return _bundled_generation_font(value, "helv", font_language)
    try:
        value.encode("cp1252")
    except UnicodeEncodeError:
        return _bundled_generation_font(value, "helv", None)
    return None


#: pymupdf's Standard 14 PostScript names, in pymupdf's order.
Base14_fontnames: tuple[str, ...] = (
    "Courier",
    "Courier-Oblique",
    "Courier-Bold",
    "Courier-BoldOblique",
    "Helvetica",
    "Helvetica-Oblique",
    "Helvetica-Bold",
    "Helvetica-BoldOblique",
    "Times-Roman",
    "Times-Italic",
    "Times-Bold",
    "Times-BoldItalic",
    "Symbol",
    "ZapfDingbats",
)
#: pymupdf's lookup from the lower-case PostScript names and the short aliases
#: to the PostScript names. Read-only, because pylopdf keeps no process-global
#: mutable state.
Base14_fontdict: Mapping[str, str] = MappingProxyType(
    {**{name.lower(): name for name in Base14_fontnames}, **_BASE14_FONTS}
)
#: Standard 14 names that ``fontname=`` and :class:`Font` accept, matched
#: without regard to case.
_BASE14_FONT_NAMES = Base14_fontdict
#: The short alias of each PostScript name, which selects serif or sans faces.
_BASE14_ALIASES: dict[str, str] = {name: alias for alias, name in _BASE14_FONTS.items()}
_SYMBOLIC_BASE14_NAMES = frozenset({"Symbol", "ZapfDingbats"})

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
_MIN_BOLD_FONT_WEIGHT = 600
_BOLD_FONT_WEIGHT = 700
_MAX_FONT_WEIGHT = 1000
_MAX_UNICODE_CODEPOINT = 0x10FFFF


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


def _describe_face(weight: int | None, *, italic: bool) -> str:
    """Describe a requested face for error and warning messages."""
    name = {None: "regular", 400: "regular", _BOLD_FONT_WEIGHT: "bold"}.get(weight, f"weight-{weight}")
    return f"{name} italic" if italic else name


def _standard_font_name(fontname: str) -> str:
    """Resolve a Standard 14 alias or name to its PostScript name."""
    if not isinstance(fontname, str):
        msg = f"fontname must be a string: {fontname!r}"
        raise TypeError(msg)
    name = _BASE14_FONT_NAMES.get(fontname) or _BASE14_FONT_NAMES.get(fontname.lower())
    if name is None:
        msg = (
            f"fontname must be a Standard 14 name or alias such as 'helv' or 'Times-Roman': {fontname!r}; "
            "pymupdf's other built-in fonts are not available, so pass fontfile, fontbuffer, or language"
        )
        raise PdfError(msg)
    return name


def _load_font(
    fontfile: str | os.PathLike[str] | None,
    fontbuffer: bytes | None,
    fontindex: int,
    max_font_size: int | None,
) -> _Font:
    """Load one face from a bounded file or buffer."""
    if isinstance(fontindex, bool) or not isinstance(fontindex, int) or not 0 <= fontindex <= _UINT32_MAX:
        msg = f"fontindex must be an integer from 0 through {_UINT32_MAX}: {fontindex!r}"
        raise ValueError(msg)
    source = _resolve_font_source(fontfile, fontbuffer, max_font_size)
    if isinstance(source, Path):
        return _Font.from_file(os.fspath(source), fontindex, max_font_size)
    return _Font.from_bytes(cast("bytes", source), fontindex, max_font_size)


def _named_or_loaded_font(
    fontname: str | None,
    fontfile: str | os.PathLike[str] | None,
    fontbuffer: bytes | None,
    fontindex: int,
    max_font_size: int | None,
) -> _Font:
    """Return a Standard 14 font by name, or load a face from a file or buffer."""
    if fontname is None:
        return _load_font(fontfile, fontbuffer, fontindex, max_font_size)
    if fontindex != 0:
        msg = "fontindex requires fontfile or fontbuffer"
        raise PdfError(msg)
    return _Font.standard(_standard_font_name(fontname))


def _requested_font_weight(weight: int | None, *, is_bold: bool) -> int | None:
    """Combine ``weight`` and ``is_bold`` into the weight class a lookup must match."""
    if not is_bold:
        return weight
    if weight is None:
        return _BOLD_FONT_WEIGHT
    if weight < _MIN_BOLD_FONT_WEIGHT:
        msg = f"weight={weight} contradicts is_bold=True"
        raise PdfError(msg)
    return weight


def _font_extra_family(family: str) -> tuple[FontLanguage, bool]:
    """Return the font extra and style that install a family."""
    if not isinstance(family, str):
        msg = f"family must be a string: {family!r}"
        raise TypeError(msg)
    for language, names in _FONT_EXTRA_FAMILIES.items():
        for serif, name in enumerate(names):
            if name.casefold() == family.casefold():
                return language, bool(serif)
    msg = f"no pylopdf font extra installs the family {family!r}; pass its font as fontfile or fontbuffer"
    raise PdfError(msg)


def _font_extra_face(  # noqa: PLR0913 - the lookup keeps every Font argument explicit.
    language: FontLanguage,
    *,
    serif: bool,
    weight: int | None,
    italic: bool,
    allow_regular_fallback: bool,
    max_font_size: int | None,
) -> _Font:
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
        msg = f"language={language!r} requires pylopdf[{extra}]; pass fontfile or fontbuffer instead"
        raise PdfError(msg)
    kind = "serif" if serif else "sans"
    source = provider.serif if serif else provider.sans
    if isinstance(source, Path):
        face = _Font.from_file(os.fspath(source), 0, max_font_size, (language, kind))
    else:
        face = _Font.from_bytes(source, 0, max_font_size)
    weight_range = face.weight_range
    if not italic:
        if weight is None:
            return _regular_instance(face)
        if weight_range is None and weight == face.weight:
            return face
        if weight_range is not None and weight_range[0] <= weight <= weight_range[2]:
            return face.with_weight(weight)
    family = face.family or _FONT_EXTRA_FAMILIES[language][serif]
    if allow_regular_fallback:
        msg = f"pylopdf[{extra}] has no {_describe_face(weight, italic=italic)} {family!r} face; using its own face"
        # Point at the caller of Font(): this helper and __init__ sit between.
        _warnings.warn(msg, PylopdfWarning, stacklevel=3)
        return _regular_instance(face)
    described = _describe_face(weight, italic=italic)
    if weight_range is not None and not italic:
        low, _, high = weight_range
        msg = (
            f"pylopdf[{extra}] installs {family!r} with weights {low:g} through {high:g}; "
            f"pass its {described} face as fontfile or fontbuffer, or set allow_regular_fallback=True"
        )
    else:
        msg = (
            f"pylopdf[{extra}] installs one {family!r} face; pass its {described} "
            "face as fontfile or fontbuffer, or set allow_regular_fallback=True"
        )
    raise PdfError(msg)


def _regular_instance(face: _Font) -> _Font:
    """Return a face at the regular weight when it has a weight axis.

    The default instance of a variable face need not be regular: that of
    Noto Sans Hebrew in ``pylopdf[he]`` is Thin.
    """
    return face if face.weight_range is None else face.with_weight(_REGULAR_FONT_WEIGHT)


def _face_at_weight(face: _Font, weight: int) -> _Font:
    """Select ``weight`` on a face loaded from ``fontfile`` or ``fontbuffer``.

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
    ``Font(language=...)`` selects it; any other font input is drawn as given.
    """
    return None if package_font is None else _REGULAR_FONT_WEIGHT


class Font:
    """A font for generated text and its measurement, as pymupdf's ``Font``.

    Pass exactly one source. ``fontname`` names a Standard 14 font by its
    PostScript name or pymupdf alias, such as ``"Times-Bold"`` or ``"helv"``.
    ``fontfile`` or ``fontbuffer`` loads an OpenType face, with ``fontindex``
    selecting a face in a collection. ``language`` selects the face of an
    installed font extra such as ``pylopdf[jp]`` (``is_serif=True`` for its
    serif face), and ``family`` selects an installed extra's face by family
    name, such as ``"Noto Sans JP"``. pylopdf has no default font and keeps no
    font registry; load any other face, such as a bold weight, from its file.

    A face from ``fontfile`` or ``fontbuffer`` is used as given, a variable
    font at its default instance, which need not be regular: the variable
    Noto Sans CJK JP defaults to Thin. ``weight``, a weight class from 1
    through 1000, selects the instance at that weight on a variable font's
    weight axis instead and refuses a weight outside the axis, so
    ``weight=400`` differs from an omitted weight for such a font. A static
    face must already have the requested weight class.

    For ``language`` and ``family``, ``is_bold`` and ``is_italic`` request a
    style and ``weight`` a weight class. The extras install one face per
    style. The ``ar``, ``he``, ``hi``, and ``th`` extras
    install variable faces, which provide the weights on their weight axis as
    instances (100 through 900, or 400 through 700 for the Noto Naskh Arabic
    serif face) and are drawn at the regular weight unless another is
    requested; ``is_bold=True`` selects the bold instance. A request that the
    installed face does not provide, including a weight outside its axis,
    raises :class:`PdfError` instead of imitating a weight by stroking or
    overprinting glyphs; ``allow_regular_fallback=True`` explicitly uses the
    extra's regular face and emits a :class:`PylopdfWarning`. Apart from
    ``weight`` with ``fontfile`` and ``fontbuffer``, these options do not
    apply to the other sources and raise there, where pymupdf would ignore
    them.

    A Font reads its input once. Pass it as ``font`` to
    :meth:`Page.insert_text` and :meth:`Page.insert_textbox`, and an OpenType
    face also to :meth:`Document.set_form_field`, on any document, and share
    it between threads. :meth:`text_length` measures with the shaping and
    advances that drawing uses, so a measured line is as wide as the drawn
    line. Standard 14 fonts are drawn without embedding and measure with
    Adobe's AFM widths. Font input defaults to a 64 MiB boundary;
    ``max_font_size=None`` opts trusted input out, and refusal raises
    :class:`LimitError` with code ``font_input_size``.
    """

    if not TYPE_CHECKING:
        __getattr__ = hinted_getattr

    def __init__(  # noqa: PLR0913 - pymupdf's sources plus keyword-only lookup options.
        self,
        fontname: str | None = None,
        fontfile: str | os.PathLike[str] | None = None,
        fontbuffer: bytes | None = None,
        *,
        language: FontLanguage | None = None,
        is_bold: bool = False,
        is_italic: bool = False,
        is_serif: bool = False,
        weight: int | None = None,
        fontindex: int = 0,
        family: str | None = None,
        allow_regular_fallback: bool = False,
        max_font_size: int | None = _DEFAULT_MAX_FONT_INPUT_SIZE,
    ) -> None:
        """Select a Standard 14 font, load a face, or select an installed extra's face."""
        _validate_optional_positive_int("max_font_size", max_font_size)
        resolved_weight = _validate_font_weight(weight)
        sources = [
            name
            for name, value in (
                ("fontname", fontname),
                ("fontfile", fontfile),
                ("fontbuffer", fontbuffer),
                ("family", family),
                ("language", language),
            )
            if value is not None
        ]
        if not sources:
            msg = "pylopdf has no default font; pass fontname, fontfile, fontbuffer, family, or language"
            raise PdfError(msg)
        if len(sources) > 1:
            msg = f"pass one font source, not {' and '.join(sources)}"
            raise PdfError(msg)
        if fontname is not None or fontfile is not None or fontbuffer is not None:
            if is_bold or is_italic or is_serif or allow_regular_fallback:
                msg = (
                    "is_bold, is_italic, is_serif, and allow_regular_fallback select the face of an installed "
                    "font extra; fontname names one Standard 14 variant and a font file is used as given, "
                    "or at the instance that weight selects on a variable font"
                )
                raise PdfError(msg)
            if fontname is not None and resolved_weight is not None:
                msg = "weight selects an instance of a variable font file; fontname names one Standard 14 variant"
                raise PdfError(msg)
            face = _named_or_loaded_font(fontname, fontfile, fontbuffer, fontindex, max_font_size)
            self._font = face if resolved_weight is None else _face_at_weight(face, resolved_weight)
            return
        if fontindex != 0:
            msg = "fontindex requires fontfile or fontbuffer"
            raise PdfError(msg)
        requested = _requested_font_weight(resolved_weight, is_bold=bool(is_bold))
        if family is not None:
            if is_serif:
                msg = "is_serif cannot be combined with family, which already names the face"
                raise PdfError(msg)
            extra_language, serif = _font_extra_family(family)
        else:
            extra_language = cast("FontLanguage", _normalize_font_language(language))
            serif = bool(is_serif)
        self._font = _font_extra_face(
            extra_language,
            serif=serif,
            weight=requested,
            italic=bool(is_italic),
            allow_regular_fallback=allow_regular_fallback,
            max_font_size=max_font_size,
        )

    @property
    def name(self) -> str | None:
        """The full name of an OpenType face or the PostScript name of a Standard 14 font.

        An instance of a variable face takes its style from the instance, as in
        ``Noto Sans Hebrew Bold``; generated text embeds it under the
        instance's PostScript name, ``NotoSansHebrew-Bold``.
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
        """Ascent above the baseline as a fraction of the font size, as pymupdf reports it.

        An OpenType face reports what FreeType reads for MuPDF: the OS/2
        typographic ascender when the face sets ``USE_TYPO_METRICS``, else
        the ``hhea`` ascender, or the typographic or Windows one when both
        ``hhea`` values are zero; MuPDF replaces a value that is not positive
        or exceeds 8 with 0.8. A Standard 14 font reports the bounding box of
        the URW font that MuPDF draws it with, such as 1.075 for Helvetica. A
        variable face reports its default instance. :meth:`Page.insert_text`
        and :meth:`Page.insert_textbox` place lines with :attr:`ascender` and
        :attr:`descender`.
        """
        return self._font.ascender

    @property
    def descender(self) -> float:
        """Descent below the baseline as a negative fraction of the font size, as pymupdf reports it.

        It comes from the same table as :attr:`ascender`. MuPDF negates a
        positive value and replaces one beyond 3 either way with -0.2.
        """
        return self._font.descender

    def text_length(
        self,
        text: str,
        fontsize: float = 11,
        *,
        max_text_size: int | None = _DEFAULT_MAX_GENERATED_TEXT_SIZE,
    ) -> float:
        """Return the width of one line of ``text`` at ``fontsize`` in PDF units.

        OpenType text is shaped with HarfRust exactly as drawing shapes it, so
        the result is the advance that :meth:`Page.insert_text` draws,
        including kerning and ligatures; Standard 14 text sums the AFM widths
        of its WinAnsi characters. Measure each line separately: line breaks
        raise :class:`PdfError`. A character the font has no glyph for raises
        :class:`PdfError`, as drawing does. Text input defaults to a 1 MiB
        UTF-8 boundary; ``max_text_size=None`` opts trusted input out, and
        refusal raises :class:`LimitError` with code ``text_input_size``.
        """
        if not isinstance(text, str):
            msg = f"text must be a string: {text!r}"
            raise TypeError(msg)
        _validate_utf8_text_input(text, max_text_size)
        if "\n" in text or "\r" in text:
            msg = "text_length measures one line; split text at line breaks first"
            raise PdfError(msg)
        try:
            size = float(fontsize)
        except (TypeError, ValueError) as exc:
            msg = f"fontsize must be a positive number: {fontsize!r}"
            raise ValueError(msg) from exc
        if not (math.isfinite(size) and size > 0):
            msg = f"fontsize must be a positive number: {fontsize!r}"
            raise ValueError(msg)
        return self._font.text_length(text, size, max_text_size)

    def has_glyph(self, chr: int | str) -> int:  # noqa: A002 - pymupdf's parameter name.
        """Return the glyph number of the code point ``chr``, or 0 when the font has none.

        ``chr`` is an integer code point, as in pymupdf, or a one-character
        string. A Standard 14 font has no glyph numbers because it is not
        embedded; for it, the number is the character's code in the font's
        encoding.
        """
        if isinstance(chr, str):
            if len(chr) != 1:
                msg = f"chr must be a code point or a one-character string: {chr!r}"
                raise ValueError(msg)
            codepoint = ord(chr)
        elif isinstance(chr, int) and not isinstance(chr, bool):
            if not 0 <= chr <= _MAX_UNICODE_CODEPOINT:
                msg = f"chr must be a Unicode code point: {chr!r}"
                raise ValueError(msg)
            codepoint = chr
        else:
            msg = f"chr must be a code point or a one-character string: {chr!r}"
            raise TypeError(msg)
        return self._font.has_glyph(codepoint)

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
