//! CJK fallback fonts for rendering and fonts for generated text.

use super::*;

/// Load generation font input from a path without holding the GIL.
///
/// A face Python selected from an installed font package shares its bytes
/// through `bundled_font_cache`; explicit font paths are read on every call.
/// Both apply the same `font_input_size` boundary before using any bytes.
pub(crate) fn load_generation_font(
    py: Python<'_>,
    path: &str,
    package_font: Option<(String, String)>,
    max_font_size: Option<usize>,
) -> PyResult<FontBytes> {
    validate_font_input(None, max_font_size)?;
    let face = package_font
        .map(|(language, kind)| PackageFace::parse(&language, &kind))
        .transpose()?;
    py.detach(|| match face {
        Some(face) => load_package_font(face, path, max_font_size),
        None => read_font_input(path, max_font_size).map(Arc::new),
    })
}

/// Select the face that a text drawing call embeds.
///
/// A variable face is drawn at `weight` on its weight axis, and Python passes
/// the regular weight for an installed package face: the default instance of
/// Noto Sans Hebrew is Thin. A static face, and any face drawn without a
/// weight, is drawn as given, so explicit `fontfile` and `fontbuffer` input
/// keeps its default instance. Form appearances select it the same way, per
/// widget, through `FormFont`.
pub(super) fn generation_face(
    py: Python<'_>,
    data: FontBytes,
    index: u32,
    weight: Option<u16>,
) -> PyResult<GenerationFace> {
    py.detach(|| GenerationFace::new(data, index, weight))
        .map_err(PdfError::new_err)
}

/// One locale-specific fallback pair for non-embedded CJK fonts.
#[derive(Default, Clone)]
pub(super) struct FallbackFontPair {
    /// Sans/gothic family and the default when style is unknown.
    pub(super) sans: Option<(Arc<Vec<u8>>, u32)>,
    /// Mincho-style serif font.
    pub(super) serif: Option<(Arc<Vec<u8>>, u32)>,
}

impl FallbackFontPair {
    pub(super) fn pick(&self, prefers_serif: bool) -> Option<&(Arc<Vec<u8>>, u32)> {
        if prefers_serif {
            self.serif.as_ref().or(self.sans.as_ref())
        } else {
            self.sans.as_ref().or(self.serif.as_ref())
        }
    }

    pub(super) fn is_configured(&self) -> bool {
        self.sans.is_some() || self.serif.is_some()
    }
}

/// Locale slots aligned with the four predefined Adobe CJK collections.
#[derive(Default, Clone)]
pub(super) struct FallbackFonts {
    pub(super) ja: FallbackFontPair,
    pub(super) ko: FallbackFontPair,
    pub(super) zh_cn: FallbackFontPair,
    pub(super) zh_tw: FallbackFontPair,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum CjkFontLanguage {
    Ja,
    Ko,
    ZhCn,
    ZhTw,
}

impl CjkFontLanguage {
    pub(super) fn parse(language: &str) -> PyResult<Self> {
        match language {
            "ja" => Ok(Self::Ja),
            "ko" => Ok(Self::Ko),
            "zh-CN" => Ok(Self::ZhCn),
            "zh-TW" => Ok(Self::ZhTw),
            _ => Err(PdfError::new_err(format!(
                "font language must be 'ja', 'ko', 'zh-CN', or 'zh-TW': {language:?}"
            ))),
        }
    }

    pub(super) fn index(self) -> usize {
        match self {
            Self::Ja => 0,
            Self::Ko => 1,
            Self::ZhCn => 2,
            Self::ZhTw => 3,
        }
    }
}

impl FallbackFonts {
    pub(super) fn pair(&self, language: CjkFontLanguage) -> &FallbackFontPair {
        match language {
            CjkFontLanguage::Ja => &self.ja,
            CjkFontLanguage::Ko => &self.ko,
            CjkFontLanguage::ZhCn => &self.zh_cn,
            CjkFontLanguage::ZhTw => &self.zh_tw,
        }
    }

    pub(super) fn pair_mut(&mut self, language: CjkFontLanguage) -> &mut FallbackFontPair {
        match language {
            CjkFontLanguage::Ja => &mut self.ja,
            CjkFontLanguage::Ko => &mut self.ko,
            CjkFontLanguage::ZhCn => &mut self.zh_cn,
            CjkFontLanguage::ZhTw => &mut self.zh_tw,
        }
    }

    pub(super) fn is_configured(&self) -> bool {
        self.ja.is_configured()
            || self.ko.is_configured()
            || self.zh_cn.is_configured()
            || self.zh_tw.is_configured()
    }
}

/// Lowercase BaseFont-name patterns indicating Japanese fonts.
pub(super) const JP_NAME_HINTS: [&str; 12] = [
    "mincho", "gothic", "ryumin", "kozmin", "kozgo", "kozuka", "meiryo", "yugoth", "yumin",
    "hiragino", "ipaex", "ipam",
];

/// Lowercase BaseFont-name patterns indicating Korean fonts.
pub(super) const KO_NAME_HINTS: [&str; 7] = [
    "batang",
    "gulim",
    "dotum",
    "malgun",
    "nanum",
    "hygothic",
    "hysmyeongjo",
];

/// Lowercase BaseFont-name patterns indicating Simplified Chinese fonts.
pub(super) const ZH_CN_NAME_HINTS: [&str; 8] = [
    "simsun", "simhei", "fangsong", "kaiti", "yahei", "songti", "heiti", "cjk-sc",
];

/// Lowercase BaseFont-name patterns indicating Traditional Chinese fonts.
pub(super) const ZH_TW_NAME_HINTS: [&str; 6] = [
    "mingliu", "pmingliu", "jhenghei", "dfkai", "cjk-tc", "cjk-hk",
];

/// Lowercase BaseFont-name patterns indicating a serif/mincho family.
pub(super) const SERIF_NAME_HINTS: [&str; 12] = [
    "mincho", "ryumin", "kozmin", "yumin", "serif", "batang", "myeongjo", "simsun", "song", "ming",
    "kaiti", "kai",
];

pub(super) fn hinted_cjk_language(name: &str) -> Option<CjkFontLanguage> {
    if JP_NAME_HINTS.iter().any(|hint| name.contains(hint)) {
        Some(CjkFontLanguage::Ja)
    } else if KO_NAME_HINTS.iter().any(|hint| name.contains(hint)) {
        Some(CjkFontLanguage::Ko)
    } else if ZH_CN_NAME_HINTS.iter().any(|hint| name.contains(hint)) {
        Some(CjkFontLanguage::ZhCn)
    } else if ZH_TW_NAME_HINTS.iter().any(|hint| name.contains(hint)) {
        Some(CjkFontLanguage::ZhTw)
    } else {
        None
    }
}

/// Return a configured fallback when a non-embedded font request is CJK.
///
/// Detect CJK through CIDSystemInfo (Adobe-Japan1/GB1/CNS1/Korea1) or BaseFont.
/// Adobe-Identity lacks CID-to-Unicode clues in its CMap, so use the name;
/// hayro resolves an embedded ToUnicode map when present.
pub(super) fn pick_cjk_fallback(
    fonts: &FallbackFonts,
    query: &FallbackFontQuery,
) -> Option<(FontData, u32)> {
    let name = query
        .post_script_name
        .as_deref()
        .unwrap_or_default()
        .to_ascii_lowercase();
    let language = match query.character_collection.as_ref().map(|cc| &cc.family) {
        Some(CidFamily::AdobeJapan1) => Some(CjkFontLanguage::Ja),
        Some(CidFamily::AdobeGB1) => Some(CjkFontLanguage::ZhCn),
        Some(CidFamily::AdobeCNS1) => Some(CjkFontLanguage::ZhTw),
        Some(CidFamily::AdobeKorea1) => Some(CjkFontLanguage::Ko),
        _ => hinted_cjk_language(&name),
    }?;
    let prefers_serif = SERIF_NAME_HINTS.iter().any(|hint| name.contains(hint));
    let slot = fonts.pair(language).pick(prefers_serif);
    slot.map(|(data, index)| (Arc::clone(data) as FontData, *index))
}

/// Implements [`_Document::set_fallback_font`].
pub(super) fn set_fallback_font(
    this: &mut _Document,
    kind: &str,
    data: Vec<u8>,
    index: u32,
    max_font_size: Option<usize>,
    language: &str,
) -> PyResult<()> {
    validate_font_input(Some(&data), max_font_size)?;
    let pair = this
        .fallback_fonts
        .pair_mut(CjkFontLanguage::parse(language)?);
    let slot = match kind {
        "sans" => &mut pair.sans,
        "serif" => &mut pair.serif,
        _ => {
            return Err(PdfError::new_err(format!(
                "kind must be 'sans' or 'serif': {kind:?}"
            )));
        }
    };
    *slot = Some((Arc::new(data), index));
    this.invalidate_interpreted_pages();
    Ok(())
}

/// Implements [`_Document::set_fallback_face`]: put the program of a loaded
/// OpenType face, shared rather than copied, in one slot, or empty the slot.
pub(super) fn set_fallback_face(
    this: &mut _Document,
    language: &str,
    kind: &str,
    font: Option<&_Font>,
) -> PyResult<()> {
    let program = match font {
        Some(font) => Some(
            font.program()
                .ok_or_else(|| PdfError::new_err("a Standard 14 font cannot render CJK text"))?,
        ),
        None => None,
    };
    let pair = this
        .fallback_fonts
        .pair_mut(CjkFontLanguage::parse(language)?);
    let slot = match kind {
        "sans" => &mut pair.sans,
        "serif" => &mut pair.serif,
        _ => {
            return Err(PdfError::new_err(format!(
                "kind must be 'sans' or 'serif': {kind:?}"
            )));
        }
    };
    *slot = program;
    this.invalidate_interpreted_pages();
    Ok(())
}

/// Implements [`_Document::set_fallback_font_files`].
pub(super) fn set_fallback_font_files(
    this: &mut _Document,
    py: Python<'_>,
    sans_path: &str,
    serif_path: &str,
    max_font_size: Option<usize>,
    language: &str,
) -> PyResult<()> {
    validate_font_input(None, max_font_size)?;
    let language = CjkFontLanguage::parse(language)?;
    let (sans, serif) = py.detach(|| {
        let sans = read_font_input(sans_path, max_font_size)?;
        let serif = read_font_input(serif_path, max_font_size)?;
        Ok::<_, PyErr>((sans, serif))
    })?;
    let pair = this.fallback_fonts.pair_mut(language);
    pair.sans = Some((Arc::new(sans), 0));
    pair.serif = Some((Arc::new(serif), 0));
    this.invalidate_interpreted_pages();
    Ok(())
}

/// Implements [`_Document::set_fallback_font_locale_files`].
pub(super) fn set_fallback_font_locale_files(
    this: &mut _Document,
    py: Python<'_>,
    files: Vec<(String, String, String)>,
    max_font_size: Option<usize>,
) -> PyResult<usize> {
    validate_font_input(None, max_font_size)?;
    if files.len() > 4 {
        return Err(PdfError::new_err(
            "cannot configure more than four CJK locale font pairs",
        ));
    }
    let mut seen = [false; 4];
    let mut requests = Vec::new();
    requests.try_reserve_exact(files.len()).map_err(|error| {
        PdfError::new_err(format!(
            "failed to allocate CJK locale font requests: {error}"
        ))
    })?;
    for (language, sans_path, serif_path) in files {
        let language = CjkFontLanguage::parse(&language)?;
        if seen[language.index()] {
            return Err(PdfError::new_err(
                "each CJK font language can be configured only once",
            ));
        }
        seen[language.index()] = true;
        requests.push((language, sans_path, serif_path));
    }
    if requests.is_empty() {
        return Ok(0);
    }
    let loaded =
        py.detach(|| crate::bundled_font_cache::load_locale_font_pairs(&requests, max_font_size))?;
    let mut fallback_fonts = this.fallback_fonts.clone();
    for ((language, _, _), (sans, serif)) in requests.iter().zip(loaded.pairs) {
        let pair = fallback_fonts.pair_mut(*language);
        pair.sans = Some((sans, 0));
        pair.serif = Some((serif, 0));
    }
    this.fallback_fonts = fallback_fonts;
    this.invalidate_interpreted_pages();
    Ok(loaded.files_read)
}
