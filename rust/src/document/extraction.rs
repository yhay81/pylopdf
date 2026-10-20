//! Text, table, image, and drawing extraction and search, with the text
//! and table page caches.

use super::*;

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

impl _Document {
    /// Return the glyph payload still available to one page interpretation.
    pub(super) fn text_budget(&self, page_number: u32) -> PyResult<Option<usize>> {
        let Some(limit) = self.max_text_size else {
            return Ok(None);
        };
        if let Some(admitted) = self.interpreted_text_sizes.get(&page_number) {
            return Ok(Some(*admitted));
        }
        Ok(Some(limit.saturating_sub(self.interpreted_text_total)))
    }

    /// Return the positioned glyph records still available to one page.
    pub(super) fn glyph_budget(&self, page_number: u32) -> PyResult<Option<usize>> {
        let Some(limit) = self.max_text_glyphs else {
            return Ok(None);
        };
        if let Some(admitted) = self.interpreted_glyph_counts.get(&page_number) {
            return Ok(Some(*admitted));
        }
        Ok(Some(limit.saturating_sub(self.interpreted_glyph_total)))
    }

    /// Record one page's text resources without charging cache re-interpretation.
    pub(super) fn admit_text_usage(
        &mut self,
        page_number: u32,
        text_size: usize,
        glyph_count: usize,
    ) -> PyResult<()> {
        if self.interpreted_text_sizes.contains_key(&page_number) {
            debug_assert!(self.interpreted_glyph_counts.contains_key(&page_number));
            return Ok(());
        }
        debug_assert!(!self.interpreted_glyph_counts.contains_key(&page_number));
        let text_total = self.interpreted_text_total.checked_add(text_size);
        let glyph_total = self.interpreted_glyph_total.checked_add(glyph_count);
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
    pub(super) fn touch_text_page(&mut self, page_number: u32) -> bool {
        if !self.text_pages.contains_key(&page_number) {
            return false;
        }
        self.text_page_order.retain(|number| *number != page_number);
        self.text_page_order.push_back(page_number);
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
        page_number: u32,
        settings: InterpreterSettings,
    ) -> PyResult<()> {
        debug_assert!(!self.text_pages.contains_key(&page_number));
        let text_budget = self.text_budget(page_number)?;
        let glyph_budget = self.glyph_budget(page_number)?;
        let pages = pdf.pages();
        let page = page_number
            .checked_sub(1)
            .and_then(|index| pages.get(index as usize))
            .ok_or_else(|| PdfError::new_err(format!("page {page_number} does not exist")))?;
        let text_page =
            crate::extract::TextPage::new(pdf, page, cache, settings, text_budget, glyph_budget)
                .map_err(text_page_limit_err)?;
        self.admit_text_usage(page_number, text_page.text_size(), text_page.glyph_count())?;
        self.warn_skipped_no_unicode(text_page.skipped_no_unicode());

        if self.text_pages.len() >= TEXT_PAGE_CACHE_CAPACITY
            && let Some(evicted) = self.text_page_order.pop_front()
        {
            self.text_pages.remove(&evicted);
        }
        self.text_pages.insert(page_number, text_page);
        self.text_page_order.push_back(page_number);
        Ok(())
    }

    /// Return a cached, owned interpretation of a one-based page.
    pub(super) fn text_page(
        &mut self,
        page_number: u32,
        settings: InterpreterSettings,
    ) -> PyResult<&crate::extract::TextPage> {
        if !self.touch_text_page(page_number) {
            self.hayro_view_for(PageSelection::One(page_number))?;
            let pdf = self
                .hayro_pdf
                .take()
                .expect("hayro view was built immediately before");
            let cache = InterpreterCache::new();
            let interpreted = self.interpret_text_page(&pdf, &cache, page_number, settings);
            drop(cache);
            self.hayro_pdf = Some(pdf);
            interpreted?;
        }
        Ok(self
            .text_pages
            .get(&page_number)
            .expect("text page was interpreted immediately before"))
    }

    /// Append plain text for one page chunk from the detached hayro view.
    ///
    /// `max_output_size` bounds the complete `out` buffer, so the budget is
    /// shared by every chunk of one call. The same detached-snapshot contract
    /// as `interpret_text_page` applies.
    pub(super) fn extract_text_batch<'a>(
        &mut self,
        pdf: &'a Pdf,
        cache: &InterpreterCache<'a>,
        page_numbers: &[u32],
        max_output_size: Option<usize>,
        settings: &InterpreterSettings,
        out: &mut String,
    ) -> PyResult<()> {
        for number in page_numbers {
            let remaining = max_output_size.map(|limit| limit.saturating_sub(out.len()));
            if !self.touch_text_page(*number) {
                self.interpret_text_page(pdf, cache, *number, settings.clone())?;
            }
            let page_text = self
                .text_pages
                .get(number)
                .expect("text page was interpreted immediately before")
                .text(remaining)
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
        let table_page =
            crate::extract::TablePage::new(pdf, page, cache, settings, text_budget, glyph_budget)
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
) -> PyResult<(f64, f64, Vec<crate::extract::BlockTuple>)> {
    let settings = this.interpreter_settings();
    this.contain_renderer_panics(|this| {
        py.detach(|| {
            this.text_page(page_number, settings)?
                .layout()
                .map_err(text_page_limit_err)
        })
    })
}

/// Implements [`_Document::extract_raw_layout`].
pub(super) fn extract_raw_layout(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
) -> PyResult<(f64, f64, Vec<crate::extract::RawBlockTuple>)> {
    let settings = this.interpreter_settings();
    this.contain_renderer_panics(|this| {
        py.detach(|| {
            this.text_page(page_number, settings)?
                .raw_layout()
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

/// Implements [`_Document::extract_drawings`].
pub(super) fn extract_drawings(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
) -> PyResult<Vec<crate::extract::DrawingTuple>> {
    let settings = this.interpreter_settings();
    this.contain_renderer_panics(|this| {
        let pdf = this.hayro_view_for(PageSelection::One(page_number))?;
        py.detach(|| {
            let pages = pdf.pages();
            let page = page_number
                .checked_sub(1)
                .and_then(|index| pages.get(index as usize))
                .ok_or_else(|| PdfError::new_err(format!("page {page_number} does not exist")))?;
            crate::extract::extract_page_drawings(pdf, page, settings).map_err(PdfError::new_err)
        })
    })
}

/// Implements [`_Document::search_page`].
pub(super) fn search_page(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    needle: &str,
    max_hits: Option<usize>,
) -> PyResult<Vec<(f64, f64, f64, f64)>> {
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
            this.text_page(page_number, settings)?
                .search(needle, max_hits)
                .map_err(|error| match error {
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
