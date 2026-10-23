"""Tests for locale-specific rendering of non-embedded CJK fonts.

Auto-discovery runs for the installed JP, KR, SC, and TC data packages, as it
does under ``uv sync --all-extras`` and in CI. The shared package-font slots
that rendering and text generation reuse are covered here as well.
"""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import (
    build_nonembedded_cjk_pdf,
    build_nonembedded_locale_cjk_pdf,
    build_raw_pdf,
    draw_text,
    draw_textbox,
    form_entries,
)

import pylopdf
from pylopdf.pylopdf_core import LimitError, PdfError, _Document, _Font

FONTS_DIR = Path(__file__).parents[1] / "fonts" / "pylopdf-fonts-jp" / "src" / "pylopdf_fonts_jp"
SANS = FONTS_DIR / "NotoSansJP-Regular.otf"
SERIF = FONTS_DIR / "NotoSerifJP-Regular.otf"
FONT_ROOT = Path(__file__).parents[1] / "fonts"
LOCALE_SANS = {
    "ja": SANS,
    "ko": FONT_ROOT / "pylopdf-fonts-ko" / "src" / "pylopdf_fonts_ko" / "NotoSansKR-Regular.otf",
    "zh-CN": (FONT_ROOT / "pylopdf-fonts-zh-cn" / "src" / "pylopdf_fonts_zh_cn" / "NotoSansSC-Regular.otf"),
    "zh-TW": (FONT_ROOT / "pylopdf-fonts-zh-tw" / "src" / "pylopdf_fonts_zh_tw" / "NotoSansTC-Regular.otf"),
}


def locale_font_files(directory: Path) -> list[tuple[str, str, str]]:
    """Copy the JP pair and write distinct placeholders for the other locale slots.

    Configuration does not parse font data, and the Adobe-Japan1 fixture only
    ever selects the JP pair, so the placeholders are never interpreted.
    """
    files = [("ja", str(shutil.copy(SANS, directory)), str(shutil.copy(SERIF, directory)))]
    for index, language in enumerate(("ko", "zh-CN", "zh-TW"), start=1):
        sans = directory / f"{language}-sans.otf"
        serif = directory / f"{language}-serif.otf"
        sans.write_bytes(bytes([index]) * (1024 + index))
        serif.write_bytes(bytes([index + 4]) * (2048 + index))
        files.append((language, str(sans), str(serif)))
    return files


def render_with_explicit_serif(font: Path) -> bytes:
    """Render the MS-Mincho fixture with one explicitly configured serif slot."""
    doc = pylopdf.open(build_nonembedded_cjk_pdf())
    doc.fallback_fonts = {"ja": pylopdf.FallbackFont(serif=pylopdf.Font.from_file(font))}
    return doc.pages[0].pixmap(matrix=pylopdf.Matrix(1.0, 1.0), alpha=True).to_bytes()


def package_font_provider(directory: Path) -> SimpleNamespace:
    """Return a JP provider backed by fresh copies, so their first use reads them."""
    return SimpleNamespace(
        language="ja",
        extra="jp",
        sans=Path(shutil.copy(SANS, directory / "sans.otf")),
        serif=Path(shutil.copy(SERIF, directory / "serif.otf")),
    )


def text_field_pdf() -> bytes:
    """Build one text field with two widgets, so one fill generates twice."""
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Annots [6 0 R 7 0 R] >>",
            4: "<< /Fields [5 0 R] /DA (/Helv 0 Tf 0 g) >>",
            5: "<< /FT /Tx /T (name) /V (initial) /Kids [6 0 R 7 0 R] >>",
            6: "<< /Parent 5 0 R /Type /Annot /Subtype /Widget /Rect [20 150 280 170] /P 3 0 R /F 4 >>",
            7: "<< /Parent 5 0 R /Type /Annot /Subtype /Widget /Rect [20 110 280 130] /P 3 0 R /F 4 >>",
        }
    )


def page_fonts(page: pylopdf.Page) -> list[str]:
    """Return the font name of every extracted span in reading order."""
    return [
        span["font"] for block in page.layout().to_dict()["blocks"] for line in block["lines"] for span in line["spans"]
    ]


def render_blank_baseline() -> bytes:
    """Render the blank baseline with fallback and auto-discovery disabled."""
    doc = pylopdf.open(build_nonembedded_cjk_pdf())
    doc.fallback_fonts = {}
    return doc.pages[0].pixmap(matrix=pylopdf.Matrix(1.0, 1.0), alpha=True).to_bytes()


def test_without_fallback_renders_blank() -> None:
    """Render without text when no fallback font is available."""
    png = render_blank_baseline()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_fallback_font_from_path_renders_text() -> None:
    doc = pylopdf.open(build_nonembedded_cjk_pdf())
    doc.fallback_fonts = {"ja": pylopdf.FallbackFont(pylopdf.Font.from_file(SANS))}
    png = doc.pages[0].pixmap(matrix=pylopdf.Matrix(1.0, 1.0), alpha=True).to_bytes()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    # Rendered glyphs add pixels, so the PNG differs from the blank baseline.
    assert png != render_blank_baseline()
    assert len(png) > len(render_blank_baseline())


def test_fallback_font_from_bytes_renders_text() -> None:
    doc = pylopdf.open(build_nonembedded_cjk_pdf())
    doc.fallback_fonts = {"ja": pylopdf.FallbackFont(pylopdf.Font.from_bytes(SANS.read_bytes()))}
    assert doc.pages[0].pixmap(matrix=pylopdf.Matrix(1.0, 1.0), alpha=True).to_bytes() != render_blank_baseline()


def test_serif_slot_used_for_mincho() -> None:
    """Use the serif slot for PDFs whose BaseFont is MS-Mincho."""
    doc = pylopdf.open(build_nonembedded_cjk_pdf())
    doc.fallback_fonts = {"ja": pylopdf.FallbackFont(serif=pylopdf.Font.from_file(SERIF))}
    assert doc.pages[0].pixmap(matrix=pylopdf.Matrix(1.0, 1.0), alpha=True).to_bytes() != render_blank_baseline()


def test_svg_rendering_includes_glyphs() -> None:
    blank_doc = pylopdf.open(build_nonembedded_cjk_pdf())
    blank_doc.fallback_fonts = {}
    blank_svg = blank_doc.pages[0].svg()

    doc = pylopdf.open(build_nonembedded_cjk_pdf())
    doc.fallback_fonts = {"ja": pylopdf.FallbackFont(pylopdf.Font.from_file(SANS))}
    svg = doc.pages[0].svg()
    assert svg.startswith("<svg")
    assert len(svg) > len(blank_svg)


def test_fallback_fonts_validate_before_any_change() -> None:
    doc = pylopdf.open(build_nonembedded_cjk_pdf())
    sans = pylopdf.Font.from_file(SANS)
    doc.fallback_fonts = {"ja": pylopdf.FallbackFont(sans)}
    with pytest.raises(ValueError, match="sans face, a serif face, or both"):
        pylopdf.FallbackFont()
    with pytest.raises(ValueError, match="Standard 14"):
        pylopdf.FallbackFont(pylopdf.Font.standard("Helvetica"))
    with pytest.raises(TypeError, match="Font or None"):
        pylopdf.FallbackFont(SANS)  # ty: ignore[invalid-argument-type]
    with pytest.raises(ValueError, match="'ja', 'ko', 'zh-CN', or 'zh-TW'"):
        doc.fallback_fonts = {"ja": pylopdf.FallbackFont(sans), "th": pylopdf.FallbackFont(sans)}
    with pytest.raises(TypeError, match="FallbackFont record"):
        doc.fallback_fonts["ko"] = sans  # ty: ignore[invalid-assignment]
    assert dict(doc.fallback_fonts) == {"ja": pylopdf.FallbackFont(sans)}


def test_fallback_font_boundary_preserves_previous_font_and_cache() -> None:
    doc = pylopdf.open(build_nonembedded_cjk_pdf())
    doc.fallback_fonts = {"ja": pylopdf.FallbackFont(pylopdf.Font.from_file(SANS))}
    baseline = doc.pages[0].pixmap(matrix=pylopdf.Matrix(1.0, 1.0), alpha=True).to_bytes()

    with pytest.raises(pylopdf.LimitError) as caught:
        doc.fallback_fonts["ja"] = pylopdf.FallbackFont(
            serif=pylopdf.Font.from_file(SERIF, max_size=SERIF.stat().st_size - 1)
        )

    assert caught.value.code == "font_input_size"
    assert doc.pages[0].pixmap(matrix=pylopdf.Matrix(1.0, 1.0), alpha=True).to_bytes() == baseline


def test_latin_rendering_unaffected_by_fallback(one_page_pdf: bytes) -> None:
    """Keep Latin rendering unchanged when a fallback font is configured."""
    plain = pylopdf.open(one_page_pdf)
    plain.fallback_fonts = {}
    baseline = plain.pages[0].pixmap(matrix=pylopdf.Matrix(1.0, 1.0), alpha=True).to_bytes()

    with_font = pylopdf.open(one_page_pdf)
    with_font.fallback_fonts = {"ja": pylopdf.FallbackFont(pylopdf.Font.from_file(SANS))}
    assert with_font.pages[0].pixmap(matrix=pylopdf.Matrix(1.0, 1.0), alpha=True).to_bytes() == baseline


def test_auto_discovery_via_extra() -> None:
    """Auto-render Japanese text when pylopdf[jp] is installed."""
    pytest.importorskip("pylopdf_fonts_jp")
    doc = pylopdf.open(build_nonembedded_cjk_pdf())
    png = doc.pages[0].pixmap(matrix=pylopdf.Matrix(1.0, 1.0), alpha=True).to_bytes()
    assert png != render_blank_baseline()


def test_auto_discovery_uses_only_canonical_font_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not probe the removed Japanese compatibility package."""
    requested: list[str] = []
    bundled_font_providers = pylopdf._fonts._bundled_font_providers  # noqa: SLF001
    provider_specs = pylopdf._fonts._FONT_PROVIDER_SPECS  # noqa: SLF001

    def missing_module(module_name: str) -> None:
        requested.append(module_name)

    bundled_font_providers.cache_clear()
    try:
        with monkeypatch.context() as context:
            context.setattr("pylopdf._fonts._import_optional_font_module", missing_module)
            assert bundled_font_providers() == ()
    finally:
        bundled_font_providers.cache_clear()

    assert requested == [module_name for _, _, module_name in provider_specs]


def test_auto_discovered_fonts_render_like_explicit_fonts() -> None:
    """Fresh documents sharing installed locale fonts render identically."""
    pytest.importorskip("pylopdf_fonts_jp")
    expected = render_with_explicit_serif(SERIF)
    pdf = build_nonembedded_cjk_pdf()
    assert [
        pylopdf.open(pdf).pages[0].pixmap(matrix=pylopdf.Matrix(1.0, 1.0), alpha=True).to_bytes() for _ in range(3)
    ] == [expected] * 3


def test_locale_fallback_files_are_read_once_across_documents(one_page_pdf: bytes, tmp_path: Path) -> None:
    """Later documents reuse unchanged files and reread only a changed one."""
    files = locale_font_files(tmp_path)
    first = _Document.load_bytes(one_page_pdf)
    assert first.set_fallback_font_locale_files(files) == 8
    second = _Document.load_bytes(one_page_pdf)
    assert second.set_fallback_font_locale_files(files) == 0

    changed = Path(files[2][2])
    changed.write_bytes(changed.read_bytes() + b"\0")
    third = _Document.load_bytes(one_page_pdf)
    assert third.set_fallback_font_locale_files(files) == 1
    assert _Document.load_bytes(one_page_pdf).set_fallback_font_locale_files(files) == 0
    unit = (1.0, 0.0, 0.0, 1.0)
    assert first.render_page_png(1, unit, True, None, None) == third.render_page_png(1, unit, True, None, None)


def test_cached_locale_fallback_files_are_validated_atomically(tmp_path: Path) -> None:
    """Cached files still fail size and path checks without partial updates."""
    files = locale_font_files(tmp_path)
    pdf = build_nonembedded_cjk_pdf()
    doc = _Document.load_bytes(pdf)
    doc.set_fallback_font_locale_files(files)
    baseline = doc.render_page_png(1, (1.0, 0.0, 0.0, 1.0), True, None, None)
    assert baseline == render_with_explicit_serif(SERIF)

    largest = max(Path(path).stat().st_size for _, sans, serif in files for path in (sans, serif))
    with pytest.raises(LimitError) as caught:
        doc.set_fallback_font_locale_files(files, largest - 1)
    assert caught.value.args[0] == "font_input_size"

    missing = Path(files[3][1])
    data = missing.read_bytes()
    missing.unlink()
    with pytest.raises(FileNotFoundError) as not_found:
        doc.set_fallback_font_locale_files(files)
    assert not_found.value.filename == str(missing)
    assert doc.render_page_png(1, (1.0, 0.0, 0.0, 1.0), True, None, None) == baseline

    # The failed calls left every other cached read in place.
    missing.write_bytes(data + b"\0")
    assert doc.set_fallback_font_locale_files(files) == 1


def test_auto_discovery_rereads_a_replaced_font_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh document sees a package font replaced after an earlier read."""
    serif = tmp_path / "serif.otf"
    shutil.copy(SERIF, serif)
    provider = SimpleNamespace(language="ja", extra="jp", sans=SANS, serif=serif)
    monkeypatch.setattr("pylopdf._fonts._bundled_font_providers", lambda: (provider,))
    pdf = build_nonembedded_cjk_pdf()
    mincho = pylopdf.open(pdf).pages[0].pixmap(matrix=pylopdf.Matrix(1.0, 1.0), alpha=True).to_bytes()
    assert mincho == render_with_explicit_serif(SERIF)

    shutil.copy(SANS, serif)
    gothic = pylopdf.open(pdf).pages[0].pixmap(matrix=pylopdf.Matrix(1.0, 1.0), alpha=True).to_bytes()
    assert gothic == render_with_explicit_serif(SANS)
    assert gothic != mincho


def test_concurrent_first_renders_share_locale_fonts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fresh documents racing to configure unread locale fonts render identically."""
    providers = tuple(
        SimpleNamespace(language=language, extra="", sans=Path(sans), serif=Path(serif))
        for language, sans, serif in locale_font_files(tmp_path)
    )
    monkeypatch.setattr("pylopdf._fonts._bundled_font_providers", lambda: providers)
    pdf = build_nonembedded_cjk_pdf()

    def render(_: int) -> bytes:
        return pylopdf.open(pdf).pages[0].pixmap(matrix=pylopdf.Matrix(1.0, 1.0), alpha=True).to_bytes()

    with ThreadPoolExecutor(max_workers=8) as executor:
        rendered = list(executor.map(render, range(32)))
    assert rendered == [render_with_explicit_serif(SERIF)] * 32


def test_package_generation_fonts_are_read_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generated text, textboxes, and form values share installed package files."""
    provider = package_font_provider(tmp_path)
    monkeypatch.setattr("pylopdf._fonts._bundled_font_providers", lambda: (provider,))

    def generate(sans: pylopdf.Font | None, serif: pylopdf.Font) -> bytes:
        doc = pylopdf.open(text_field_pdf())
        with doc.pages[0].draw() as canvas:
            canvas.text("日本語", (20, 30), font=sans)
            assert canvas.textbox((20, 40, 280, 70), "明朝体の段落", font=serif).fits
        doc.form.font = sans
        doc.form["name"].value = "山田 太郎"
        return doc.to_bytes()

    reads = _Document.package_font_reads()
    automatic = [generate(None, pylopdf.Font.installed(language="ja", serif=True)) for _ in range(3)]
    assert _Document.package_font_reads() - reads == 2
    # Explicit font paths are read on every call without touching the slots.
    explicit = generate(pylopdf.Font.from_file(provider.sans), pylopdf.Font.from_file(provider.serif))
    assert _Document.package_font_reads() - reads == 2
    assert automatic == [explicit] * 3


def test_package_generation_fonts_are_checked_on_every_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cached package fonts still fail size and path checks without editing."""
    provider = package_font_provider(tmp_path)
    monkeypatch.setattr("pylopdf._fonts._bundled_font_providers", lambda: (provider,))
    doc = pylopdf.open(text_field_pdf())
    page = doc.pages[0]
    with page.draw() as canvas:
        canvas.text("日本語", (20, 30))
    doc.form["name"].value = "山田 太郎"
    baseline = doc.to_bytes()

    size = provider.sans.stat().st_size
    with pytest.raises(LimitError) as caught:
        doc._doc.set_form_field_file("name", "佐藤 花子", str(provider.sans), 0, size - 1, ("ja", "sans"), 400)  # noqa: SLF001
    assert caught.value.args[0] == "font_input_size"

    data = provider.sans.read_bytes()
    provider.sans.unlink()
    for call in (
        lambda: draw_text(page, (20, 60), "日本語"),
        lambda: draw_textbox(page, (20, 60, 280, 90), "日本語"),
        lambda: setattr(doc.form["name"], "value", "佐藤 花子"),
    ):
        with pytest.raises(FileNotFoundError) as not_found:
            call()
        assert Path(not_found.value.filename) == provider.sans
    assert doc.to_bytes() == baseline
    assert {field["name"]: field["value"] for field in form_entries(doc)} == {"name": "山田 太郎"}

    # A file restored or replaced at the same path is read again.
    provider.sans.write_bytes(SERIF.read_bytes())
    reads = _Document.package_font_reads()
    replaced = pylopdf.Document()
    replaced.pages.new((200, 100))
    with replaced.pages[0].draw() as canvas:
        canvas.text("日本語", (20, 40))
    assert _Document.package_font_reads() - reads == 1
    assert [font.split("-")[0] for font in page_fonts(replaced.pages[0])] == ["NotoSerifJP"]
    provider.sans.write_bytes(data)
    with page.draw() as canvas:
        canvas.text("日本語", (20, 60))
    assert _Document.package_font_reads() - reads == 2


def test_rendering_and_generation_share_package_font_slots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback configuration and generation reuse one read of each JP face."""
    provider = package_font_provider(tmp_path)
    monkeypatch.setattr("pylopdf._fonts._bundled_font_providers", lambda: (provider,))
    reads = _Document.package_font_reads()
    doc = pylopdf.open(build_nonembedded_cjk_pdf())
    doc.pages[0].pixmap(matrix=pylopdf.Matrix(1.0, 1.0), alpha=True).to_bytes()
    with doc.pages[0].draw() as canvas:
        canvas.text("日本語", (20, 120))
    with doc.pages[0].draw() as canvas:
        canvas.text("明朝体", (20, 160), font=pylopdf.Font.installed(language="ja", serif=True))
    assert _Document.package_font_reads() - reads == 2


def test_concurrent_generation_shares_package_fonts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fresh documents racing to generate with unread package fonts match explicit fonts."""
    provider = package_font_provider(tmp_path)
    monkeypatch.setattr("pylopdf._fonts._bundled_font_providers", lambda: (provider,))

    def generate(font: pylopdf.Font | None = None) -> bytes:
        doc = pylopdf.Document()
        doc.pages.new((200, 100))
        draw_text(doc.pages[0], (20, 40), "日本語", font=font)
        return doc.to_bytes()

    reads = _Document.package_font_reads()
    with ThreadPoolExecutor(max_workers=8) as executor:
        generated = list(executor.map(lambda _: generate(), range(32)))
    assert 1 <= _Document.package_font_reads() - reads <= 8
    assert generated == [generate(pylopdf.Font.from_file(provider.sans))] * 32


@pytest.mark.parametrize("package_font", [("fr", "sans"), ("ja", "bold")])
def test_package_font_face_must_name_a_known_package_style(package_font: tuple[str, str]) -> None:
    with pytest.raises(PdfError, match=r"font package language|kind must be"):
        _Font.from_file(str(SANS), 0, None, package_font)


@pytest.mark.parametrize(
    ("font_language", "text"),
    [("ko", "한국어"), ("zh-CN", "简体中文"), ("zh-TW", "繁體中文")],
)
def test_locale_fallback_matches_adobe_character_collection(
    font_language: pylopdf.CjkFontLanguage,
    text: str,
) -> None:
    """Render each predefined Adobe collection through its matching locale slot."""
    pdf = build_nonembedded_locale_cjk_pdf(text, font_language)
    blank = pylopdf.open(pdf)
    blank.fallback_fonts = {}
    blank_png = blank.pages[0].pixmap(alpha=True).to_bytes()

    doc = pylopdf.open(pdf)
    doc.fallback_fonts = {font_language: pylopdf.FallbackFont(pylopdf.Font.from_file(LOCALE_SANS[font_language]))}

    assert doc.pages[0].pixmap(alpha=True).to_bytes() != blank_png
    assert doc.pages[0].text().strip() == text


@pytest.mark.parametrize("wrong_language", ["ja", "ko", "zh-TW"])
def test_wrong_locale_does_not_override_matching_collection(
    wrong_language: pylopdf.CjkFontLanguage,
) -> None:
    """Do not send any configured non-SC font into an Adobe-GB1 request."""
    pdf = build_nonembedded_locale_cjk_pdf("简体中文", "zh-CN")
    blank = pylopdf.open(pdf)
    blank.fallback_fonts = {}
    blank_png = blank.pages[0].pixmap(alpha=True).to_bytes()

    doc = pylopdf.open(pdf)
    doc.fallback_fonts = {wrong_language: pylopdf.FallbackFont(pylopdf.Font.from_file(LOCALE_SANS[wrong_language]))}

    assert doc.pages[0].pixmap(alpha=True).to_bytes() == blank_png


def test_nonembedded_cjk_extract_text() -> None:
    """Extract text from non-embedded CJK using 90ms-RKSJ-H.

    Hayro resolves the predefined CMap, and Unicode comes from that map, so
    extraction does not require a fallback font.
    """
    doc = pylopdf.open(build_nonembedded_cjk_pdf())
    doc.fallback_fonts = {}
    assert "こんにちは日本語" in doc.pages[0].text()


def _render(doc: pylopdf.Document) -> bytes:
    return doc.pages[0].pixmap(alpha=True).to_bytes()


def test_fallback_fonts_list_the_installed_extras_until_edited() -> None:
    pytest.importorskip("pylopdf_fonts_jp")
    doc = pylopdf.open(build_nonembedded_cjk_pdf())
    installed = list(doc.fallback_fonts)
    assert "ja" in installed
    assert repr(doc.fallback_fonts) == f"FallbackFonts({installed!r})"
    japanese = doc.fallback_fonts["ja"]
    assert japanese.sans is not None
    assert japanese.serif is not None
    assert japanese.serif.is_serif
    assert not japanese.sans.is_serif
    auto = _render(doc)

    # Editing one language keeps the installed extras of the others.
    doc.fallback_fonts["ja"] = pylopdf.FallbackFont(serif=pylopdf.Font.from_file(SERIF))
    assert list(doc.fallback_fonts) == installed
    assert doc.fallback_fonts["ja"] == pylopdf.FallbackFont(serif=doc.fallback_fonts["ja"].serif)
    assert _render(doc) == render_with_explicit_serif(SERIF)

    del doc.fallback_fonts["ja"]
    assert "ja" not in doc.fallback_fonts
    assert _render(doc) == render_blank_baseline()
    with pytest.raises(KeyError):
        del doc.fallback_fonts["ja"]

    del doc.fallback_fonts  # Back to the installed extras.
    assert list(doc.fallback_fonts) == installed
    assert _render(doc) == auto

    doc.fallback_fonts = {}
    assert len(doc.fallback_fonts) == 0
    assert _render(doc) == render_blank_baseline()


def test_fallback_fonts_survive_unlock() -> None:
    """unlock() reads the document again; the configured fonts still render it."""
    data = pylopdf.open(build_nonembedded_cjk_pdf()).to_bytes(
        encryption=pylopdf.Encryption(user_password="user", owner_password="owner")
    )
    doc = pylopdf.open(data, password="user")
    doc.fallback_fonts = {"ja": pylopdf.FallbackFont(serif=pylopdf.Font.from_file(SERIF))}
    before = _render(doc)
    assert before == render_with_explicit_serif(SERIF)

    assert doc.unlock("owner") == pylopdf.Access.OWNER

    assert _render(doc) == before
    assert list(doc.fallback_fonts) == ["ja"]


def test_a_fallback_face_is_shared_not_copied() -> None:
    font = pylopdf.Font.from_file(SANS)
    documents = [pylopdf.open(build_nonembedded_cjk_pdf()) for _ in range(3)]
    for doc in documents:
        doc.fallback_fonts = {"ja": pylopdf.FallbackFont(font)}

    renders = {_render(doc) for doc in documents}
    assert len(renders) == 1
    assert renders != {render_blank_baseline()}
