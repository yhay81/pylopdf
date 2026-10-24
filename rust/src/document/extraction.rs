//! Text, table, image, and page content extraction and search, with the text
//! and table page caches.

use super::*;
use crate::extract::{CollectOptions, Unmapped, ViewOptions};

/// Bound interpreted text-page memory on long documents while retaining the
/// common search/extract/annotate working set.
pub(super) const TEXT_PAGE_CACHE_CAPACITY: usize = 8;

/// Keep table interpretations bounded independently from ordinary text pages.
pub(super) const TABLE_PAGE_CACHE_CAPACITY: usize = 8;

/// Bound search input and returned geometry independently from page text.
pub(super) const MAX_SEARCH_INPUT_BYTES: usize = 4_096;

pub(super) const DEFAULT_MAX_SEARCH_HITS: usize = 4_096;

/// Map one text collector refusal to its stable public resource code.
pub(super) fn text_page_limit_err(error: crate::extract::TextPageLimit) -> PyErr {
    match error {
        crate::extract::TextPageLimit::TextSize(limit) => limit_err(
            "text_size",
            format!(
                "page text exceeds the remaining configured Unicode payload budget of {limit} bytes"
            ),
        ),
        crate::extract::TextPageLimit::GlyphCount(limit) => limit_err(
            "text_glyph_count",
            format!(
                "page text exceeds the remaining configured positioned-glyph budget of {limit}"
            ),
        ),
        crate::extract::TextPageLimit::Allocation(message) => PdfError::new_err(message),
    }
}

/// A text page's cache key: the one-based page number and the options that
/// decided which glyphs it collected.
pub(crate) type TextPageKey = (u32, CollectOptions);

/// The text options of the Python views, as one tuple: `(clip, keep clipped
/// glyphs, unmapped glyphs (0 skip, 1 U+FFFD, 2 glyph ID), exact boxes,
/// expand ligatures, normalize whitespace, dehyphenate)`.
#[derive(Default)]
pub(crate) struct TextOptions {
    pub(crate) collect: CollectOptions,
    pub(crate) view: ViewOptions,
}

type TextOptionsTuple = (
    Option<(f64, f64, f64, f64)>,
    bool,
    u8,
    bool,
    bool,
    bool,
    bool,
);

impl<'a, 'py> FromPyObject<'a, 'py> for TextOptions {
    type Error = PyErr;

    fn extract(object: Borrowed<'a, 'py, PyAny>) -> PyResult<Self> {
        let (
            clip,
            keep_clipped,
            unmapped,
            exact_bboxes,
            expand_ligatures,
            normalize_whitespace,
            dehyphenate,
        ): TextOptionsTuple = object.extract()?;
        let unmapped = match unmapped {
            0 => Unmapped::Skip,
            1 => Unmapped::Replacement,
            2 => Unmapped::GlyphId,
            _ => {
                return Err(PyValueError::new_err(format!(
                    "unknown unmapped-glyph option {unmapped}"
                )));
            }
        };
        if let Some((x0, y0, x1, y1)) = clip
            && !(x0.is_finite() && y0.is_finite() && x1.is_finite() && y1.is_finite())
        {
            return Err(PyValueError::new_err("clip must be a finite rectangle"));
        }
        Ok(Self {
            collect: CollectOptions {
                keep_clipped,
                unmapped,
                exact_bboxes,
            },
            view: ViewOptions {
                clip,
                expand_ligatures,
                normalize_whitespace,
                dehyphenate,
            },
        })
    }
}

impl _Document {
    /// The fallback font programs that this document hands hayro for fonts
    /// that are not embedded, which describe no font of the PDF.
    pub(super) fn substitute_programs(&self) -> crate::extract::SubstitutePrograms {
        let fonts = &self.fallback_fonts;
        let programs = [&fonts.ja, &fonts.ko, &fonts.zh_cn, &fonts.zh_tw]
            .into_iter()
            .flat_map(|pair| [&pair.sans, &pair.serif])
            .flatten()
            .map(|(data, _)| data.as_slice());
        crate::extract::SubstitutePrograms::new(programs)
    }

    /// Return the glyph payload still available to one page interpretation.
    pub(super) fn text_budget(&self, page_number: u32) -> PyResult<Option<usize>> {
        let Some(limit) = self.max_text_size else {
            return Ok(None);
        };
        // Another interpretation of an admitted page may grow into what the
        // other pages leave.
        let admitted = self
            .interpreted_text_sizes
            .get(&page_number)
            .copied()
            .unwrap_or(0);
        Ok(Some(
            limit.saturating_sub(self.interpreted_text_total) + admitted,
        ))
    }

    /// Return the positioned glyph records still available to one page.
    pub(super) fn glyph_budget(&self, page_number: u32) -> PyResult<Option<usize>> {
        let Some(limit) = self.max_text_glyphs else {
            return Ok(None);
        };
        let admitted = self
            .interpreted_glyph_counts
            .get(&page_number)
            .copied()
            .unwrap_or(0);
        Ok(Some(
            limit.saturating_sub(self.interpreted_glyph_total) + admitted,
        ))
    }

    /// Record one page's text resources without charging cache re-interpretation.
    pub(super) fn admit_text_usage(
        &mut self,
        page_number: u32,
        text_size: usize,
        glyph_count: usize,
    ) -> PyResult<()> {
        // A page counts once, at the largest of its interpretations.
        let admitted_text = self
            .interpreted_text_sizes
            .get(&page_number)
            .copied()
            .unwrap_or(0);
        let admitted_glyphs = self
            .interpreted_glyph_counts
            .get(&page_number)
            .copied()
            .unwrap_or(0);
        let text_size = text_size.max(admitted_text);
        let glyph_count = glyph_count.max(admitted_glyphs);
        if (text_size, glyph_count) == (admitted_text, admitted_glyphs)
            && self.interpreted_text_sizes.contains_key(&page_number)
        {
            return Ok(());
        }
        let text_total = (self.interpreted_text_total - admitted_text).checked_add(text_size);
        let glyph_total = (self.interpreted_glyph_total - admitted_glyphs).checked_add(glyph_count);
        if let Some(limit) = self.max_text_size {
            let used = text_total.ok_or_else(|| {
                limit_err(
                    "text_size",
                    "interpreted text size exceeds the platform limit",
                )
            })?;
            if used > limit {
                return Err(limit_err(
                    "text_size",
                    format!(
                        "interpreted text reached {used} bytes, exceeding the configured cumulative limit of {limit}"
                    ),
                ));
            }
        }
        if let Some(limit) = self.max_text_glyphs {
            let used = glyph_total.ok_or_else(|| {
                limit_err(
                    "text_glyph_count",
                    "interpreted text glyph count exceeds the platform limit",
                )
            })?;
            if used > limit {
                return Err(limit_err(
                    "text_glyph_count",
                    format!(
                        "interpreted text reached {used} glyphs, exceeding the configured cumulative limit of {limit}"
                    ),
                ));
            }
        }
        self.interpreted_text_sizes.insert(page_number, text_size);
        self.interpreted_glyph_counts
            .insert(page_number, glyph_count);
        // Overflow is reachable only without a configured limit, where the
        // totals are never compared.
        self.interpreted_text_total = text_total.unwrap_or(usize::MAX);
        self.interpreted_glyph_total = glyph_total.unwrap_or(usize::MAX);
        Ok(())
    }

    /// Refresh a cached text page's LRU position; return false when uncached.
    pub(super) fn touch_text_page(&mut self, key: TextPageKey) -> bool {
        if !self.text_pages.contains_key(&key) {
            return false;
        }
        self.text_page_order.retain(|cached| *cached != key);
        self.text_page_order.push_back(key);
        true
    }

    /// Interpret one uncached page with the caller's interpreter cache, then
    /// admit and insert it into the bounded text-page cache.
    ///
    /// `pdf` must be the document's detached hayro snapshot: the interpreter
    /// cache borrows it, so the caller temporarily takes `hayro_pdf` and
    /// restores it afterwards. Nothing here may touch `hayro_pdf` or
    /// `hayro_source`.
    pub(super) fn interpret_text_page<'a>(
        &mut self,
        pdf: &'a Pdf,
        cache: &InterpreterCache<'a>,
        key: TextPageKey,
        settings: InterpreterSettings,
    ) -> PyResult<()> {
        debug_assert!(!self.text_pages.contains_key(&key));
        let (page_number, options) = key;
        let text_budget = self.text_budget(page_number)?;
        let glyph_budget = self.glyph_budget(page_number)?;
        let pages = pdf.pages();
        let page = page_number
            .checked_sub(1)
            .and_then(|index| pages.get(index as usize))
            .ok_or_else(|| PdfError::new_err(format!("page {page_number} does not exist")))?;
        let text_page = crate::extract::TextPage::new(
            pdf,
            page,
            cache,
            settings,
            text_budget,
            glyph_budget,
            &self.substitute_programs(),
            options,
        )
        .map_err(text_page_limit_err)?;
        self.admit_text_usage(page_number, text_page.text_size(), text_page.glyph_count())?;
        self.warn_skipped_no_unicode(text_page.skipped_no_unicode());

        if self.text_pages.len() >= TEXT_PAGE_CACHE_CAPACITY
            && let Some(evicted) = self.text_page_order.pop_front()
        {
            self.text_pages.remove(&evicted);
        }
        self.text_pages.insert(key, text_page);
        self.text_page_order.push_back(key);
        Ok(())
    }

    /// Return a cached, owned interpretation of a one-based page.
    pub(super) fn text_page(
        &mut self,
        key: TextPageKey,
        settings: InterpreterSettings,
    ) -> PyResult<&crate::extract::TextPage> {
        let page_number = key.0;
        if !self.touch_text_page(key) {
            self.hayro_view_for(PageSelection::One(page_number))?;
            let pdf = self
                .hayro_pdf
                .take()
                .expect("hayro view was built immediately before");
            let cache = InterpreterCache::new();
            let interpreted = self.interpret_text_page(&pdf, &cache, key, settings);
            drop(cache);
            self.hayro_pdf = Some(pdf);
            interpreted?;
        }
        Ok(self
            .text_pages
            .get(&key)
            .expect("text page was interpreted immediately before"))
    }

    /// Append plain text for one page chunk from the detached hayro view.
    ///
    /// `max_output_size` bounds the complete `out` buffer, so the budget is
    /// shared by every chunk of one call. The same detached-snapshot contract
    /// as `interpret_text_page` applies.
    #[allow(clippy::too_many_arguments)]
    pub(super) fn extract_text_batch<'a>(
        &mut self,
        pdf: &'a Pdf,
        cache: &InterpreterCache<'a>,
        page_numbers: &[u32],
        max_output_size: Option<usize>,
        settings: &InterpreterSettings,
        options: &TextOptions,
        out: &mut String,
    ) -> PyResult<()> {
        for number in page_numbers {
            let remaining = max_output_size.map(|limit| limit.saturating_sub(out.len()));
            let key = (*number, options.collect);
            if !self.touch_text_page(key) {
                self.interpret_text_page(pdf, cache, key, settings.clone())?;
            }
            let page_text = self
                .text_pages
                .get(&key)
                .expect("text page was interpreted immediately before")
                .text(remaining, &options.view)
                .map_err(|error| match error {
                    crate::extract::TextPageLimit::TextSize(_) => {
                        if let Some(limit) = max_output_size {
                            limit_err(
                                "text_size",
                                format!(
                                    "plain text output exceeds the {limit}-byte limit derived from max_text_size"
                                ),
                            )
                        } else {
                            limit_err(
                                "text_size",
                                "plain text output exceeds the platform limit",
                            )
                        }
                    }
                    other => text_page_limit_err(other),
                })?;
            out.try_reserve(page_text.len()).map_err(|error| {
                PdfError::new_err(format!("failed to grow multi-page text output: {error}"))
            })?;
            out.push_str(&page_text);
        }
        Ok(())
    }

    /// Refresh a cached table page's LRU position; return false when uncached.
    pub(super) fn touch_table_page(&mut self, page_number: u32) -> bool {
        if !self.table_pages.contains_key(&page_number) {
            return false;
        }
        self.table_page_order
            .retain(|number| *number != page_number);
        self.table_page_order.push_back(page_number);
        true
    }

    /// Interpret one uncached page's tables with the caller's interpreter
    /// cache, then admit and insert them into the bounded table-page cache.
    ///
    /// The same detached-snapshot contract as `interpret_text_page` applies.
    pub(super) fn interpret_table_page<'a>(
        &mut self,
        pdf: &'a Pdf,
        cache: &InterpreterCache<'a>,
        page_number: u32,
        settings: InterpreterSettings,
    ) -> PyResult<()> {
        debug_assert!(!self.table_pages.contains_key(&page_number));
        let text_budget = self.text_budget(page_number)?;
        let glyph_budget = self.glyph_budget(page_number)?;
        let pages = pdf.pages();
        let page = page_number
            .checked_sub(1)
            .and_then(|index| pages.get(index as usize))
            .ok_or_else(|| PdfError::new_err(format!("page {page_number} does not exist")))?;
        let table_page = crate::extract::TablePage::new(
            pdf,
            page,
            cache,
            settings,
            text_budget,
            glyph_budget,
            &self.substitute_programs(),
        )
        .map_err(text_page_limit_err)?;
        self.admit_text_usage(
            page_number,
            table_page.text_size(),
            table_page.glyph_count(),
        )?;
        self.warn_skipped_no_unicode(table_page.skipped_no_unicode());

        if self.table_pages.len() >= TABLE_PAGE_CACHE_CAPACITY
            && let Some(evicted) = self.table_page_order.pop_front()
        {
            self.table_pages.remove(&evicted);
        }
        self.table_pages.insert(page_number, table_page);
        self.table_page_order.push_back(page_number);
        Ok(())
    }

    /// Return a cached, owned table interpretation of a one-based page.
    pub(super) fn table_page(
        &mut self,
        page_number: u32,
        settings: InterpreterSettings,
    ) -> PyResult<&crate::extract::TablePage> {
        if !self.touch_table_page(page_number) {
            self.hayro_view_for(PageSelection::One(page_number))?;
            let pdf = self
                .hayro_pdf
                .take()
                .expect("hayro view was built immediately before");
            let cache = InterpreterCache::new();
            let interpreted = self.interpret_table_page(&pdf, &cache, page_number, settings);
            drop(cache);
            self.hayro_pdf = Some(pdf);
            interpreted?;
        }
        Ok(self
            .table_pages
            .get(&page_number)
            .expect("table page was interpreted immediately before"))
    }

    /// Surface glyphs skipped for missing Unicode as one deduplicated warning.
    ///
    /// Extraction silently loses such glyphs (for example a Type 3 font
    /// without a ToUnicode CMap, LaurenzV/hayro#1331); keep the message static
    /// so repeated pages cannot grow the pending list.
    pub(super) fn warn_skipped_no_unicode(&self, skipped: usize) {
        if skipped == 0 {
            return;
        }
        let message = "text extraction skipped glyphs with no Unicode mapping \
             (for example a Type 3 font without a ToUnicode CMap)";
        if let Ok(mut pending) = self.pending_warnings.lock()
            && !pending.iter().any(|m| m == message)
        {
            pending.push(message.to_owned());
        }
    }
}

/// Implements [`_Document::extract_text`].
pub(super) fn extract_text(
    this: &mut _Document,
    py: Python<'_>,
    page_numbers: Vec<u32>,
    options: TextOptions,
) -> PyResult<String> {
    this.check_page_entries(page_numbers.len(), "extract text from")?;
    // Every collected glyph contributes at least one UTF-8 byte. Plain
    // assembly adds at most one inferred gap or line ending per glyph, so
    // twice the configured payload budget is a complete output bound.
    let max_output_size = this
        .max_text_size
        .map(|limit| {
            limit.checked_mul(2).ok_or_else(|| {
                limit_err(
                    "text_size",
                    "configured text budget exceeds the platform limit",
                )
            })
        })
        .transpose()?;
    // An empty batch must stay a true no-op that never builds the
    // rendering and extraction snapshot or consults its size limit.
    if page_numbers.is_empty() {
        return Ok(String::new());
    }
    let settings = this.interpreter_settings();
    this.contain_renderer_panics(|this| {
        py.detach(|| {
            this.hayro_view_for(PageSelection::Many(&page_numbers))?;
            let pdf = this
                .hayro_pdf
                .take()
                .expect("hayro view was built immediately before");
            // Share one interpreter cache per bounded chunk so fonts reused by
            // several pages parse once per chunk instead of once per page,
            // while whole-document batches never retain every parsed font.
            // The output budget spans the complete call, not one chunk.
            let mut out = String::new();
            let mut extracted = Ok(());
            for chunk in page_numbers.chunks(PAGE_BATCH_CHUNK) {
                let cache = InterpreterCache::new();
                extracted = this.extract_text_batch(
                    &pdf,
                    &cache,
                    chunk,
                    max_output_size,
                    &settings,
                    &options,
                    &mut out,
                );
                drop(cache);
                if extracted.is_err() {
                    break;
                }
            }
            this.hayro_pdf = Some(pdf);
            extracted.map(|()| out)
        })
    })
}

/// Implements [`_Document::extract_layout`].
pub(super) fn extract_layout(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    options: TextOptions,
) -> PyResult<(f64, f64, Vec<crate::extract::BlockTuple>)> {
    let settings = this.interpreter_settings();
    this.contain_renderer_panics(|this| {
        py.detach(|| {
            this.text_page((page_number, options.collect), settings)?
                .layout(&options.view)
                .map_err(text_page_limit_err)
        })
    })
}

/// Implements [`_Document::extract_raw_layout`].
pub(super) fn extract_raw_layout(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    options: TextOptions,
) -> PyResult<(f64, f64, Vec<crate::extract::RawBlockTuple>)> {
    let settings = this.interpreter_settings();
    this.contain_renderer_panics(|this| {
        py.detach(|| {
            this.text_page((page_number, options.collect), settings)?
                .raw_layout(&options.view)
                .map_err(text_page_limit_err)
        })
    })
}

/// Implements [`_Document::find_tables`].
pub(super) fn find_tables(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    strategy: &str,
    clip: Option<(f64, f64, f64, f64)>,
) -> PyResult<Vec<crate::extract::TableTuple>> {
    let text_strategy = match strategy {
        "lines" => false,
        "text" => true,
        _ => {
            return Err(PyValueError::new_err("strategy must be 'lines' or 'text'"));
        }
    };
    let settings = this.interpreter_settings();
    this.contain_renderer_panics(|this| {
        py.detach(|| {
            this.table_page(page_number, settings)?
                .tables(text_strategy, clip)
                .map_err(text_page_limit_err)
        })
    })
}

/// Implements [`_Document::extract_images`].
pub(super) fn extract_images(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
) -> PyResult<Vec<crate::extract::ImageTuple>> {
    let settings = this.interpreter_settings();
    this.contain_renderer_panics(|this| {
        let pdf = this.hayro_view_for(PageSelection::One(page_number))?;
        py.detach(|| {
            let pages = pdf.pages();
            let page = page_number
                .checked_sub(1)
                .and_then(|index| pages.get(index as usize))
                .ok_or_else(|| PdfError::new_err(format!("page {page_number} does not exist")))?;
            crate::extract::extract_page_images(pdf, page, settings).map_err(PdfError::new_err)
        })
    })
}

/// Implements [`_Document::page_content`].
pub(super) fn page_content(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    (text_runs, paths, images): (bool, bool, bool),
    annotations: bool,
    clip: Option<(f64, f64, f64, f64)>,
    max_elements: Option<usize>,
) -> PyResult<Py<PyAny>> {
    use crate::content_model::{ContentError, ContentItem, ItemKinds};
    use pyo3::IntoPyObjectExt;

    let mut settings = this.interpreter_settings();
    settings.render_annotations = annotations;
    let content = this.contain_renderer_panics(|this| {
        let pdf = this.hayro_view_for(PageSelection::One(page_number))?;
        py.detach(|| {
            let pages = pdf.pages();
            let page = page_number
                .checked_sub(1)
                .and_then(|index| pages.get(index as usize))
                .ok_or_else(|| PdfError::new_err(format!("page {page_number} does not exist")))?;
            let kinds = ItemKinds {
                text_runs,
                paths,
                images,
            };
            crate::content_model::collect_page_content(
                pdf,
                page,
                settings,
                kinds,
                clip,
                max_elements,
            )
            .map_err(|error| match error {
                ContentError::ElementCount(limit) => limit_err(
                    "content_element_count",
                    format!(
                        "page content exceeds the {limit}-element limit on glyphs, \
                             segments, dash lengths, and items"
                    ),
                ),
                ContentError::Allocation(message) => PdfError::new_err(message),
            })
        })
    })?;
    let mut items = Vec::new();
    items
        .try_reserve_exact(content.items.len())
        .map_err(|error| PdfError::new_err(format!("failed to allocate content items: {error}")))?;
    for item in content.items {
        items.push(match item {
            ContentItem::Text(run) => (0u8, run).into_py_any(py)?,
            ContentItem::Path(path) => (1u8, path).into_py_any(py)?,
            ContentItem::Image(image) => (2u8, image).into_py_any(py)?,
        });
    }
    (
        items,
        content.fonts,
        content.clips,
        content.groups,
        content.marks,
        content.states,
    )
        .into_py_any(py)
}

/// Implements [`_Document::search_page`].
pub(super) fn search_page<T>(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    needle: &str,
    max_hits: Option<usize>,
    options: TextOptions,
    search: impl FnOnce(
        &crate::extract::TextPage,
        &str,
        Option<usize>,
        &ViewOptions,
    ) -> Result<Vec<T>, crate::extract::SearchError>
    + Send,
) -> PyResult<Vec<T>>
where
    T: Send,
{
    if needle.is_empty() {
        return Err(PyValueError::new_err("needle must be at least 1 character"));
    }
    if needle.len() > MAX_SEARCH_INPUT_BYTES {
        return Err(limit_err(
            "search_input_size",
            format!("search needle exceeds the {MAX_SEARCH_INPUT_BYTES}-byte UTF-8 safety limit"),
        ));
    }
    if max_hits == Some(0) {
        return Err(PyValueError::new_err(
            "max_hits must be a positive integer or None",
        ));
    }
    let settings = this.interpreter_settings();
    this.contain_renderer_panics(|this| {
        py.detach(|| {
            let page = this.text_page((page_number, options.collect), settings)?;
            search(page, needle, max_hits, &options.view).map_err(|error| match error {
                crate::extract::SearchError::TooManyHits => {
                    let limit = max_hits.expect("bounded search hit count");
                    limit_err(
                        "search_hit_count",
                        format!("search results exceed the {limit}-hit safety limit"),
                    )
                }
                crate::extract::SearchError::Allocation(message) => PdfError::new_err(message),
            })
        })
    })
}

/// Implements [`_Document::select_text`].
pub(super) fn select_text(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    start: (f64, f64),
    end: (f64, f64),
    options: TextOptions,
) -> PyResult<String> {
    if ![start.0, start.1, end.0, end.1]
        .iter()
        .all(|value| value.is_finite())
    {
        return Err(PyValueError::new_err("selection points must be finite"));
    }
    let settings = this.interpreter_settings();
    this.contain_renderer_panics(|this| {
        py.detach(|| {
            this.text_page((page_number, options.collect), settings)?
                .selection(start, end, &options.view)
                .map_err(text_page_limit_err)
        })
    })
}

/// The dictionary facts of one image XObject: width, height, bits per
/// component (0 when absent), color space and alternate or base color space
/// names (empty when absent), the names of its filters in decoding order, the
/// xrefs of its soft mask and of its stencil mask stream (0 for none), and
/// whether it is itself a stencil mask.
pub(super) type ImageFactsTuple = (i64, i64, i64, String, String, Vec<String>, u32, u32, bool);

/// Read a name, or the name an indirect reference resolves to, as text.
fn object_name_text(doc: &Document, object: &Object) -> Option<String> {
    deref_object(doc, object)
        .as_name()
        .ok()
        .map(|name| String::from_utf8_lossy(name).into_owned())
}

/// Return an image dictionary's color space and alternate color space names.
fn image_color_space_names(doc: &Document, dict: &Dictionary) -> (String, String) {
    let Ok(space) = dict.get(b"ColorSpace") else {
        return (String::new(), String::new());
    };
    let space = deref_object(doc, space);
    if let Some(name) = object_name_text(doc, space) {
        return (name, String::new());
    }
    let Ok(items) = space.as_array() else {
        return (String::new(), String::new());
    };
    let family = items
        .first()
        .and_then(|first| object_name_text(doc, first))
        .unwrap_or_default();
    let alternate = match family.as_str() {
        "ICCBased" => items
            .get(1)
            .map(|profile| deref_object(doc, profile))
            .and_then(|profile| profile.as_stream().ok())
            .map(|profile| {
                profile
                    .dict
                    .get(b"Alternate")
                    .ok()
                    .and_then(|alternate| object_name_text(doc, alternate))
                    .unwrap_or_else(|| {
                        match profile.dict.get(b"N").and_then(Object::as_i64) {
                            Ok(1) => "DeviceGray",
                            Ok(4) => "DeviceCMYK",
                            _ => "DeviceRGB",
                        }
                        .to_owned()
                    })
            })
            .unwrap_or_default(),
        "Indexed" => items
            .get(1)
            .and_then(|base| {
                object_name_text(doc, base).or_else(|| {
                    deref_object(doc, base)
                        .as_array()
                        .ok()
                        .and_then(|base| base.first())
                        .and_then(|first| object_name_text(doc, first))
                })
            })
            .unwrap_or_default(),
        "Separation" | "DeviceN" => items
            .get(2)
            .and_then(|alternate| {
                object_name_text(doc, alternate).or_else(|| {
                    deref_object(doc, alternate)
                        .as_array()
                        .ok()
                        .and_then(|alternate| alternate.first())
                        .and_then(|first| object_name_text(doc, first))
                })
            })
            .unwrap_or_default(),
        _ => String::new(),
    };
    (family, alternate)
}

/// Return the image XObject that an object number names, at its newest
/// generation, or `None` when the object is no image stream.
fn image_stream(doc: &Document, xref: u32) -> Option<(ObjectId, &Stream)> {
    let id = doc
        .objects
        .range((xref, 0)..=(xref, u16::MAX))
        .next_back()
        .map(|(id, _)| *id)?;
    let stream = doc.get_object(id).and_then(Object::as_stream).ok()?;
    matches!(
        stream.dict.get(b"Subtype").and_then(Object::as_name),
        Ok(b"Image")
    )
    .then_some((id, stream))
}

/// Read the facts of an image XObject's dictionary.
fn image_facts_of(doc: &Document, dict: &Dictionary) -> ImageFactsTuple {
    let integer = |key: &[u8]| {
        dict.get(key)
            .map(|value| deref_object(doc, value))
            .and_then(Object::as_i64)
            .unwrap_or(0)
    };
    let stream_reference = |key: &[u8]| {
        dict.get(key)
            .and_then(Object::as_reference)
            .ok()
            .filter(|id| image_stream(doc, id.0).is_some())
            .map_or(0, |id| id.0)
    };
    let (space, alternate) = image_color_space_names(doc, dict);
    let filters = dict
        .get(b"Filter")
        .ok()
        .map(|filter| deref_object(doc, filter))
        .map(|filter| match object_name_text(doc, filter) {
            Some(name) => vec![name],
            None => filter
                .as_array()
                .map(|filters| {
                    filters
                        .iter()
                        .filter_map(|filter| object_name_text(doc, filter))
                        .collect()
                })
                .unwrap_or_default(),
        })
        .unwrap_or_default();
    let is_stencil = matches!(
        dict.get(b"ImageMask")
            .map(|value| deref_object(doc, value))
            .and_then(Object::as_bool),
        Ok(true)
    );
    (
        integer(b"Width"),
        integer(b"Height"),
        integer(b"BitsPerComponent"),
        space,
        alternate,
        filters,
        stream_reference(b"SMask"),
        stream_reference(b"Mask"),
        is_stencil,
    )
}

/// Implements [`_Document::image_facts`].
pub(super) fn image_facts(this: &_Document, xref: u32) -> Option<ImageFactsTuple> {
    let doc = &this.doc;
    image_stream(doc, xref).map(|(_, stream)| image_facts_of(doc, &stream.dict))
}

/// Implements [`_Document::image_xrefs`].
pub(super) fn image_xrefs(this: &_Document) -> PyResult<Vec<u32>> {
    let doc = &this.doc;
    let mut xrefs: Vec<u32> = Vec::new();
    for (id, object) in &doc.objects {
        let Ok(stream) = object.as_stream() else {
            continue;
        };
        if !matches!(
            stream.dict.get(b"Subtype").and_then(Object::as_name),
            Ok(b"Image")
        ) || xrefs.last() == Some(&id.0)
        {
            continue;
        }
        xrefs.try_reserve(1).map_err(|error| {
            PdfError::new_err(format!("failed to grow the image list: {error}"))
        })?;
        xrefs.push(id.0);
    }
    Ok(xrefs)
}

/// Implements [`_Document::page_image_inventory`].
///
/// Walk the page's inherited resources and, depth first, the resources of
/// the Form XObjects they list, recording the xref of each indirect image
/// XObject once in resource order. Images that only inline content or
/// annotation appearances hold are not listed, as in pymupdf.
pub(super) fn page_image_inventory(this: &_Document, page_number: u32) -> PyResult<Vec<u32>> {
    page_resource_inventory(this, page_number, InventoryKind::Images)
        .map(|inventory| inventory.images)
}

/// What `ImageObject.extract` returns: the encoded image's format, its
/// resolution, and its bytes.
pub(super) type ExtractedImageTuple = (&'static str, (u32, u32), Vec<u8>);

/// Serialize a one-page document that draws image `xref` at its pixel size,
/// with the objects the image reaches; without `keep_masks` the image's soft
/// mask and mask are left out, as pymupdf extracts an image.
fn image_snapshot(this: &_Document, xref: u32, keep_masks: bool) -> PyResult<Vec<u8>> {
    let doc = &this.doc;
    let (id, stream) = image_stream(doc, xref)
        .ok_or_else(|| PdfError::new_err(format!("xref {xref} is not an image")))?;
    let (width, height, ..) = image_facts_of(doc, &stream.dict);
    if width <= 0 || height <= 0 || width > i64::from(u16::MAX) || height > i64::from(u16::MAX) {
        return Err(PdfError::new_err(format!(
            "xref {xref} is an image of unsupported size {width}x{height}"
        )));
    }
    let mut image = stream.clone();
    if !keep_masks {
        image.dict.remove(b"SMask");
        image.dict.remove(b"Mask");
    }
    let reachable =
        crate::prune::reachable_object_ids(doc, image.dict.iter().map(|(_, value)| value), |_| {
            true
        })?;
    let mut copy = Document::with_version("1.7");
    for object_id in &reachable {
        if *object_id == id {
            continue;
        }
        if let Ok(object) = doc.get_object(*object_id) {
            copy.objects.insert(*object_id, object.clone());
        }
    }
    copy.objects.insert(id, Object::Stream(image));
    copy.max_id = copy
        .objects
        .keys()
        .map(|object| object.0)
        .max()
        .unwrap_or(0)
        .max(id.0);
    let content = format!("q {width} 0 0 {height} 0 0 cm /Im Do Q");
    let content_id = copy.add_object(Stream::new(Dictionary::new(), content.into_bytes()));
    let pages_id = copy.new_object_id();
    let page_id = copy.add_object(lopdf::dictionary! {
        "Type" => "Page",
        "Parent" => pages_id,
        "MediaBox" => vec![0.into(), 0.into(), width.into(), height.into()],
        "Contents" => content_id,
        "Resources" => lopdf::dictionary! { "XObject" => lopdf::dictionary! { "Im" => id } },
    });
    copy.objects.insert(
        pages_id,
        Object::Dictionary(lopdf::dictionary! {
            "Type" => "Pages",
            "Kids" => vec![page_id.into()],
            "Count" => 1,
        }),
    );
    let catalog_id =
        copy.add_object(lopdf::dictionary! { "Type" => "Catalog", "Pages" => pages_id });
    copy.trailer.set("Root", catalog_id);
    serialize_pdf_with_limit(
        &mut copy,
        None,
        this.max_interpretation_size,
        "interpretation_size",
        "image snapshot",
    )
}

/// Parse an image snapshot and clear its page with a fresh content guard.
fn guarded_image_snapshot(xref: u32, data: Vec<u8>) -> PyResult<Pdf> {
    let pdf = Pdf::new(data)
        .map_err(|error| PdfError::new_err(format!("failed to read xref {xref}: {error:?}")))?;
    let mut guard = ContentGuard::default();
    guard.check(&pdf, PageSelection::One(1))?;
    Ok(pdf)
}

/// Implements [`_Document::extract_image`].
///
/// Draw the image without its masks on a page of its own and let hayro decode
/// it as the placement extractor does: DCT data passes through as JPEG and
/// other data becomes PNG. The content and colour-space guards run on that
/// page.
pub(super) fn extract_image(
    this: &mut _Document,
    py: Python<'_>,
    xref: u32,
) -> PyResult<ExtractedImageTuple> {
    let data = image_snapshot(this, xref, false)?;
    let settings = this.interpreter_settings();
    this.contain_renderer_panics(|_| {
        py.detach(|| {
            let pdf = guarded_image_snapshot(xref, data)?;
            let pages = pdf.pages();
            let page = pages
                .first()
                .ok_or_else(|| PdfError::new_err(format!("failed to read xref {xref}")))?;
            let mut images = crate::extract::extract_page_images(&pdf, page, settings)
                .map_err(PdfError::new_err)?;
            let (_, _, _, format, bytes) = images
                .pop()
                .ok_or_else(|| PdfError::new_err(format!("xref {xref} could not be decoded")))?;
            let resolution = if format == "jpeg" {
                crate::extract::jpeg_resolution(&bytes)
            } else {
                (96, 96)
            };
            Ok((format, resolution, bytes))
        })
    })
}

/// Implements [`_Document::image_pixmap`].
///
/// Render the image at its pixel size on a page of its own: opaque RGB over
/// white without its masks, or straight-alpha RGBA with them.
pub(super) fn image_pixmap(
    this: &mut _Document,
    py: Python<'_>,
    xref: u32,
    alpha: bool,
) -> PyResult<crate::pixmap::Pixmap> {
    let data = image_snapshot(this, xref, alpha)?;
    let settings = this.interpreter_settings();
    let background = if alpha {
        Some((0, 0, 0, 0))
    } else {
        Some((255, 255, 255, 255))
    };
    let pixmap = this.contain_renderer_panics(|_| {
        py.detach(|| {
            let pdf = guarded_image_snapshot(xref, data)?;
            let cache = RenderCache::new();
            render_pdf_page(
                &pdf,
                &cache,
                &settings,
                1,
                (1.0, 0.0, 0.0, 1.0),
                background,
                None,
            )
            .map_err(PdfError::new_err)
        })
    })?;
    let width = u32::from(pixmap.width());
    let height = u32::from(pixmap.height());
    let layout = Layout { gray: false, alpha };
    let data = py.detach(|| raster_samples(pixmap, layout));
    Ok(crate::pixmap::Pixmap {
        width,
        height,
        layout,
        data: Arc::new(data),
        resolution: crate::pixmap::DEFAULT_RESOLUTION,
    })
}
