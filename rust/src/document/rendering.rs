//! The hayro snapshot, raster and SVG rendering, and parallel batches.

use super::*;

/// Original unencrypted input retained for the first render or extraction.
pub(super) enum HayroSource {
    Unavailable,
    Bytes(Vec<u8>),
    TooLarge { actual: usize, limit: usize },
}

impl HayroSource {
    pub(super) fn from_owned(data: Vec<u8>, max_size: Option<usize>) -> Self {
        match max_size {
            Some(limit) if data.len() > limit => Self::TooLarge {
                actual: data.len(),
                limit,
            },
            _ => Self::Bytes(data),
        }
    }

    pub(super) fn from_optional_owned(data: Option<Vec<u8>>, max_size: Option<usize>) -> Self {
        data.map_or(Self::Unavailable, |data| Self::from_owned(data, max_size))
    }

    pub(super) fn from_borrowed(data: &[u8], max_size: Option<usize>) -> PyResult<Self> {
        if let Some(limit) = max_size
            && data.len() > limit
        {
            return Ok(Self::TooLarge {
                actual: data.len(),
                limit,
            });
        }
        let mut owned = Vec::new();
        owned.try_reserve_exact(data.len()).map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate rendering and extraction source: {error}"
            ))
        })?;
        owned.extend_from_slice(data);
        Ok(Self::Bytes(owned))
    }

    pub(super) fn take_bytes(&mut self) -> Option<Vec<u8>> {
        match std::mem::replace(self, Self::Unavailable) {
            Self::Bytes(data) => Some(data),
            Self::Unavailable | Self::TooLarge { .. } => None,
        }
    }
}

/// Maximum PNG render pixels, approximately a 256 MB RGBA bitmap.
pub(super) const MAX_RENDER_PIXELS: u64 = 64_000_000;

/// Bound concurrent pixmap plus straight-alpha conversion buffers to ~512 MB.
#[cfg(not(target_os = "emscripten"))]
pub(super) const MAX_PARALLEL_RENDER_BYTES: u64 = 512_000_000;

#[cfg(not(target_os = "emscripten"))]
pub(super) const ESTIMATED_RENDER_BYTES_PER_PIXEL: u64 = 8;

/// Whether hayro resolved exactly the walker's pages, in page order.
pub(super) fn hayro_pages_match(pdf: &Pdf, page_ids: &[ObjectId]) -> bool {
    let pages = pdf.pages();
    pages.len() == page_ids.len()
        && pages
            .iter()
            .zip(page_ids)
            .all(|(page, &(number, generation))| {
                page.raw().obj_id().is_some_and(|id| {
                    i64::from(id.obj_number) == i64::from(number)
                        && i64::from(id.gen_number) == i64::from(generation)
                })
            })
}

/// Convert a hayro Pixmap to straight-alpha RGBA8 bytes in place.
///
/// vello_cpu unpremultiplies its own buffer and reinterprets it as bytes, so
/// this conversion allocates nothing.
pub(super) fn rgba_bytes(pixmap: hayro::vello_cpu::Pixmap) -> Vec<u8> {
    pixmap.take_rgba8(ImageAlphaType::Alpha)
}

/// Horizontal and vertical render scale factors, where 1.0 is 72 dpi.
pub(super) type RenderScale = (f32, f32);

/// Return a rendered raster's samples in `layout`: straight-alpha RGBA, RGB,
/// or their gray counterparts. Without alpha the raster was cleared to an
/// opaque background, so every pixel is opaque and premultiplication is moot;
/// the alpha bytes, and for gray the color components, are dropped in place
/// without a second buffer.
pub(super) fn raster_samples(pixmap: hayro::vello_cpu::Pixmap, layout: Layout) -> Vec<u8> {
    let mut data = rgba_bytes(pixmap);
    if !layout.alpha {
        let pixels = data.len() / 4;
        for index in 0..pixels {
            data.copy_within(index * 4..index * 4 + 3, index * 3);
        }
        data.truncate(pixels * 3);
    }
    if layout.gray {
        crate::pixmap::to_gray_in_place(&mut data, layout.alpha);
    }
    data
}

/// Pixel viewport of a display-coordinate clip on the scaled page raster.
///
/// The clip is intersected with the page and rounded outward to pixel
/// boundaries. Returns the viewport origin and size in pixels.
pub(super) fn clip_viewport(
    page_width: u16,
    page_height: u16,
    scale: RenderScale,
    clip: (f64, f64, f64, f64),
) -> Result<(f64, f64, u16, u16), String> {
    if ![clip.0, clip.1, clip.2, clip.3]
        .into_iter()
        .all(f64::is_finite)
        || clip.0 >= clip.2
        || clip.1 >= clip.3
    {
        return Err("clip must be a finite rectangle with x0 < x1 and y0 < y1".to_owned());
    }
    let (x_scale, y_scale) = (f64::from(scale.0), f64::from(scale.1));
    let (width, height) = (f64::from(page_width), f64::from(page_height));
    let x0 = (clip.0 * x_scale).floor().clamp(0.0, width);
    let y0 = (clip.1 * y_scale).floor().clamp(0.0, height);
    let x1 = (clip.2 * x_scale).ceil().clamp(0.0, width);
    let y1 = (clip.3 * y_scale).ceil().clamp(0.0, height);
    if x0 >= x1 || y0 >= y1 {
        return Err("clip does not intersect the rendered page".to_owned());
    }
    // Both extents are bounded by the u16 page raster, so the casts are exact.
    Ok((x0, y0, (x1 - x0) as u16, (y1 - y0) as u16))
}

/// Validate one page and return its raster pixel count.
pub(super) fn render_pixel_count(
    pdf: &Pdf,
    page_number: u32,
    scale: RenderScale,
) -> Result<u64, String> {
    if !(scale.0.is_finite() && scale.1.is_finite() && scale.0 > 0.0 && scale.1 > 0.0) {
        return Err("scale must be a finite, positive value".to_owned());
    }
    let pages = pdf.pages();
    let page = page_number
        .checked_sub(1)
        .and_then(|index| pages.get(index as usize))
        .ok_or_else(|| format!("page {page_number} does not exist"))?;
    let (page_width, page_height) = page.render_dimensions();
    let pixel_width = (f64::from(page_width) * f64::from(scale.0)).floor();
    let pixel_height = (f64::from(page_height) * f64::from(scale.1)).floor();
    if !pixel_width.is_finite()
        || !pixel_height.is_finite()
        || pixel_width < 1.0
        || pixel_height < 1.0
    {
        return Err("scale is too small, or the PDF page size is invalid".to_owned());
    }
    if pixel_width > f64::from(u16::MAX) || pixel_height > f64::from(u16::MAX) {
        return Err(format!(
            "render size {pixel_width:.0}x{pixel_height:.0} exceeds the 65535-pixel limit per side"
        ));
    }
    let total_pixels = (pixel_width as u64) * (pixel_height as u64);
    if total_pixels > MAX_RENDER_PIXELS {
        return Err(format!(
            "render size {pixel_width:.0}x{pixel_height:.0} ({total_pixels} pixels) exceeds the {MAX_RENDER_PIXELS}-pixel limit"
        ));
    }
    Ok(total_pixels)
}

/// Render one page, or one display-coordinate clip of it, from an immutable
/// hayro snapshot and caller-owned cache.
///
/// A clip renders through an offset viewport, so only its pixels are
/// rasterized and allocated. Full-page render-size limits still apply.
pub(super) fn render_pdf_page<'a>(
    pdf: &'a Pdf,
    cache: &RenderCache<'a>,
    interpreter_settings: &InterpreterSettings,
    page_number: u32,
    scale: RenderScale,
    background: Option<(u8, u8, u8, u8)>,
    clip: Option<(f64, f64, f64, f64)>,
) -> Result<hayro::vello_cpu::Pixmap, String> {
    render_pixel_count(pdf, page_number, scale)?;
    let pages = pdf.pages();
    let page = page_number
        .checked_sub(1)
        .and_then(|index| pages.get(index as usize))
        .ok_or_else(|| format!("page {page_number} does not exist"))?;
    let (page_width, page_height) = page.render_dimensions();
    // Match hayro::render, which truncates the scaled page size.
    let (width, height) = (
        (page_width * scale.0) as u16,
        (page_height * scale.1) as u16,
    );
    let (x0, y0, width, height) = match clip {
        Some(clip) => clip_viewport(width, height, scale, clip)?,
        None => (0.0, 0.0, width, height),
    };
    let transform = Affine::translate((-x0, -y0))
        * Affine::scale_non_uniform(f64::from(scale.0), f64::from(scale.1))
        * page.initial_transform(true).to_kurbo();
    let mut context = RenderContext::new(width, height);
    render_into(
        page,
        cache,
        interpreter_settings,
        &RenderSettings::default(),
        &mut context,
        transform,
    );
    context.flush();
    let bg_color = background.map_or(PixmapSettings::default().bg_color, |(r, g, b, a)| {
        AlphaColor::from_rgba8(r, g, b, a)
    });
    let mut pixmap = hayro::vello_cpu::Pixmap::new(width, height);
    context.render_with(
        &mut pixmap,
        &mut Resources::default(),
        RasterizerSettings {
            target_init: TargetInit::Clear(bg_color),
            ..Default::default()
        },
    );
    Ok(pixmap)
}

/// Encode a rendered pixmap in `layout` as `encoding` within `max_size`.
pub(super) fn rendered_image(
    pixmap: hayro::vello_cpu::Pixmap,
    layout: Layout,
    encoding: Encoding,
    max_size: Option<usize>,
) -> PyResult<Vec<u8>> {
    let width = u32::from(pixmap.width());
    let height = u32::from(pixmap.height());
    let data = raster_samples(pixmap, layout);
    match crate::pixmap::encode_bounded(width, height, layout, &data, encoding, max_size) {
        Ok(bytes) => Ok(bytes),
        Err(EncodeFailure::OutputLimit) => {
            let limit = max_size.expect("output can exceed only a configured limit");
            Err(limit_err(
                "render_output_size",
                format!("the rendered image exceeds the {limit}-byte encoded-output limit"),
            ))
        }
        Err(EncodeFailure::Allocation(error)) => Err(PdfError::new_err(error)),
        Err(EncodeFailure::Encoding(error)) => Err(PdfError::new_err(format!(
            "failed to encode the rendered image: {error}"
        ))),
    }
}

pub(super) struct BatchOutput<'a> {
    pub(super) bytes: Vec<u8>,
    pub(super) max_size: usize,
    pub(super) output_bytes: &'a AtomicUsize,
    pub(super) exceeded: bool,
    pub(super) committed: bool,
}

impl<'a> BatchOutput<'a> {
    pub(super) fn new(max_size: usize, output_bytes: &'a AtomicUsize) -> Self {
        Self {
            bytes: Vec::new(),
            max_size,
            output_bytes,
            exceeded: false,
            committed: false,
        }
    }

    pub(super) fn finish(mut self) -> Vec<u8> {
        self.committed = true;
        std::mem::take(&mut self.bytes)
    }
}

impl Write for BatchOutput<'_> {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        self.write_all(buffer)?;
        Ok(buffer.len())
    }

    fn write_all(&mut self, buffer: &[u8]) -> io::Result<()> {
        // Keep the shared admission compatible with the pinned Wasm toolchain.
        let mut total = self.output_bytes.load(Ordering::Relaxed);
        loop {
            let Some(new_total) = total
                .checked_add(buffer.len())
                .filter(|&value| value <= self.max_size)
            else {
                self.exceeded = true;
                return Err(io::Error::other("batch output size limit exceeded"));
            };
            match self.output_bytes.compare_exchange_weak(
                total,
                new_total,
                Ordering::Relaxed,
                Ordering::Relaxed,
            ) {
                Ok(_) => break,
                Err(current) => total = current,
            }
        }
        if let Err(error) = self.bytes.try_reserve(buffer.len()) {
            self.output_bytes.fetch_sub(buffer.len(), Ordering::Relaxed);
            return Err(io::Error::other(format!(
                "failed to allocate the rendered image: {error}"
            )));
        }
        self.bytes.extend_from_slice(buffer);
        Ok(())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

impl Drop for BatchOutput<'_> {
    fn drop(&mut self) {
        if !self.committed {
            self.output_bytes
                .fetch_sub(self.bytes.len(), Ordering::Relaxed);
        }
    }
}

/// Encode one batch page while charging each retained chunk atomically.
pub(super) fn rendered_batch_image(
    pixmap: hayro::vello_cpu::Pixmap,
    layout: Layout,
    encoding: Encoding,
    max_size: usize,
    output_bytes: &AtomicUsize,
) -> PyResult<Vec<u8>> {
    let width = u32::from(pixmap.width());
    let height = u32::from(pixmap.height());
    let data = raster_samples(pixmap, layout);
    let mut output = BatchOutput::new(max_size, output_bytes);
    let result = crate::pixmap::write_image(&mut output, width, height, layout, &data, encoding);
    if output.exceeded {
        return Err(limit_err(
            "render_output_size",
            format!("the rendered batch exceeds the {max_size}-byte encoded-output limit"),
        ));
    }
    result.map_err(|error| {
        PdfError::new_err(format!("failed to encode the rendered image: {error}"))
    })?;
    Ok(output.finish())
}

pub(super) struct BatchRenderer<'shared> {
    pub(super) interpreter_settings: &'shared InterpreterSettings,
    pub(super) scale: RenderScale,
    pub(super) layout: Layout,
    pub(super) encoding: Encoding,
    pub(super) background: Option<(u8, u8, u8, u8)>,
    pub(super) max_output_size: Option<usize>,
    pub(super) output_bytes: &'shared AtomicUsize,
}

impl BatchRenderer<'_> {
    /// Render and encode one page through the shared output budget.
    pub(super) fn render<'a>(
        &self,
        pdf: &'a Pdf,
        cache: &RenderCache<'a>,
        page_number: u32,
    ) -> PyResult<Vec<u8>> {
        let pixmap = render_pdf_page(
            pdf,
            cache,
            self.interpreter_settings,
            page_number,
            self.scale,
            self.background,
            None,
        )
        .map_err(PdfError::new_err)?;
        match self.max_output_size {
            Some(limit) => {
                rendered_batch_image(pixmap, self.layout, self.encoding, limit, self.output_bytes)
            }
            None => rendered_image(pixmap, self.layout, self.encoding, None),
        }
    }

    /// Render one ordered page batch with one hayro cache per bounded chunk,
    /// so whole-document batches never retain every resource.
    ///
    /// Each chunk asks the document for its cache: on the creating thread the
    /// first chunk reuses the retained cache, and a chunk beyond the retained
    /// page bound leaves the next chunk a fresh one.
    pub(super) fn render_serial(
        &self,
        pdf: &Arc<Pdf>,
        caches: &crate::render_cache::DocumentRenderCache,
        page_numbers: &[u32],
    ) -> PyResult<Vec<Vec<u8>>> {
        let mut rendered = Vec::new();
        rendered
            .try_reserve_exact(page_numbers.len())
            .map_err(|error| {
                PdfError::new_err(format!("failed to allocate serial render results: {error}"))
            })?;
        for chunk in page_numbers.chunks(PAGE_BATCH_CHUNK) {
            caches.with_cache(pdf, chunk, |pdf, cache| {
                for &page_number in chunk {
                    rendered.push(self.render(pdf, cache, page_number)?);
                }
                Ok::<_, PyErr>(())
            })?;
        }
        Ok(rendered)
    }
}

impl _Document {
    /// Serialize current edit state to bytes for rendering.
    pub(super) fn current_bytes(&mut self) -> PyResult<Vec<u8>> {
        serialize_pdf_with_limit(
            &mut self.doc,
            None,
            self.max_interpretation_size,
            "interpretation_size",
            "serialized rendering and extraction snapshot",
        )
    }

    /// Reject a retained source before the renderer or extractor parses it.
    pub(super) fn validate_interpretation_source(&self, data: &[u8]) -> PyResult<()> {
        validate_interpretation_limit(self.max_interpretation_size)?;
        if let Some(limit) = self.max_interpretation_size
            && data.len() > limit
        {
            return Err(limit_err(
                "interpretation_size",
                format!(
                    "rendering and extraction source is {} bytes, exceeding the configured limit of {limit}",
                    data.len()
                ),
            ));
        }
        Ok(())
    }

    /// Return the hayro view, preferring original bytes before normalization.
    ///
    /// Editing methods invalidate the cache, preserving the invariant that
    /// rendered state always reflects edits. Consecutive renders rebuild once.
    /// Extraction and rendering index hayro's pages by page number, so every
    /// view must resolve exactly the page-tree walker's pages, in order.
    pub(super) fn hayro_view(&mut self) -> PyResult<&Arc<Pdf>> {
        if self.hayro_pdf.is_none() {
            let page_ids = self.doc.owned_page_ids()?;
            let text_markup_appearances = missing_text_markup_appearance_plans(&self.doc)?;
            let prepare_appearances = text_markup_appearances
                .as_ref()
                .is_some_and(|plans| !plans.is_empty());
            if !prepare_appearances {
                match &self.hayro_source {
                    HayroSource::TooLarge { actual, limit } => {
                        return Err(limit_err(
                            "interpretation_size",
                            format!(
                                "rendering and extraction source is {actual} bytes, exceeding the configured limit of {limit}"
                            ),
                        ));
                    }
                    HayroSource::Bytes(data) => {
                        self.validate_interpretation_source(data)?;
                    }
                    HayroSource::Unavailable => {}
                }
            }
            // hayro cannot open a document whose trailer or catalog nests too
            // deeply, and lists pages without their inherited attributes when
            // the root page-tree node does, so such a document reads from the
            // rendering copy that cuts them.
            let readable_roots =
                !read_depth::roots_too_deep(&self.doc, page_tree_root(&self.doc).ok())?;
            let copy_needed = prepare_appearances || !readable_roots;
            // hayro rebuilds an object stream's member table for every member
            // it looks up, so a document with a very large container reads
            // faster from its serialized graph, which has none. The original
            // bytes stay the fallback when the serialization is refused or
            // lists other pages.
            let serialized_pdf = (!copy_needed && self.prefers_serialized_graph())
                .then(|| self.render_snapshot(None, None).ok())
                .flatten()
                .and_then(|(data, _)| Pdf::new(data).ok())
                .filter(|pdf| hayro_pages_match(pdf, &page_ids));
            if serialized_pdf.is_some() {
                self.hayro_source = HayroSource::Unavailable;
            }
            let source_pdf = (!copy_needed && serialized_pdf.is_none())
                .then(|| self.hayro_source.take_bytes())
                .flatten()
                .and_then(|data| Pdf::new(data).ok())
                .filter(|pdf| hayro_pages_match(pdf, &page_ids));
            self.hayro_pdf_from_original = source_pdf.is_some();
            let (pdf, pages_too_deep) = match serialized_pdf.or(source_pdf) {
                Some(pdf) => (pdf, Vec::new()),
                None => {
                    let plain = if readable_roots {
                        let (data, _) = self.render_snapshot(text_markup_appearances, None)?;
                        Pdf::new(data).ok()
                    } else {
                        None
                    };
                    match plain {
                        Some(pdf) if hayro_pages_match(&pdf, &page_ids) => (pdf, Vec::new()),
                        _ => {
                            // hayro 0.8 reads untyped page-tree kids as pages,
                            // which the walker skips, and leaves out a kid it
                            // cannot read, such as one nested more than 64
                            // levels deep. It cannot open a document whose
                            // trailer or catalog is nested that deeply. List
                            // only the walker's pages in a rendering copy and
                            // cut what hayro could not read.
                            let text_markup_appearances =
                                missing_text_markup_appearance_plans(&self.doc)?;
                            let (data, cut) =
                                self.render_snapshot(text_markup_appearances, Some(&page_ids))?;
                            let pdf = Pdf::new(data).map_err(|e| {
                                PdfError::new_err(format!(
                                    "failed to parse PDF for rendering: {e:?}"
                                ))
                            })?;
                            if !hayro_pages_match(&pdf, &page_ids) {
                                return Err(PdfError::new_err(
                                    "the renderer could not resolve the document page tree",
                                ));
                            }
                            (pdf, cut)
                        }
                    }
                }
            };
            self.content_guard.reset();
            self.pages_too_deep.clear();
            self.pages_too_deep
                .try_reserve_exact(pages_too_deep.len())
                .map_err(|error| {
                    PdfError::new_err(format!("failed to record pages nested too deeply: {error}"))
                })?;
            for index in pages_too_deep {
                let number = u32::try_from(index + 1)
                    .map_err(|_| PdfError::new_err("page number exceeds the platform limit"))?;
                self.pages_too_deep.push(number);
            }
            self.hayro_pdf = Some(Arc::new(pdf));
        }
        Ok(self
            .hayro_pdf
            .as_ref()
            .expect("constructed immediately before"))
    }

    /// Whether the retained original input should give way to a serialization
    /// of the graph, because rebuilding hayro's member tables would cost more.
    pub(super) fn prefers_serialized_graph(&self) -> bool {
        matches!(
            &self.hayro_source,
            HayroSource::Bytes(data)
                if object_stream_work::MemberTables::of(&self.doc).prefer_serialized_graph(data.len())
        )
    }

    /// Return the hayro view after clearing `pages` for nesting.
    ///
    /// hayro 0.7 and 0.8 overflow their stack, and kill the process, on Type 4
    /// function braces nested hundreds of thousands deep and on chains of
    /// thousands of colour spaces or functions that name each other. hayro 0.7
    /// did on arrays or dictionaries nested as deeply in a content stream,
    /// which hayro 0.8 truncates at 64 levels, and hayro 0.8 cannot read a
    /// page dictionary nested more than 64 levels deep at all. Every path that
    /// lets hayro interpret a page therefore starts here; a refusal raises
    /// `LimitError` before any interpretation or cache insertion.
    pub(super) fn hayro_view_for(&mut self, pages: PageSelection<'_>) -> PyResult<&Arc<Pdf>> {
        self.hayro_view()?;
        let too_deep = |number: &u32| self.pages_too_deep.binary_search(number).is_ok();
        let refused = match pages {
            PageSelection::One(number) => too_deep(&number).then_some(number),
            PageSelection::Many(numbers) => numbers.iter().copied().find(too_deep),
            PageSelection::All => self.pages_too_deep.first().copied(),
        };
        if let Some(number) = refused {
            return Err(limit_err(
                "object_depth",
                format!(
                    "page {number} nests arrays or dictionaries deeper than the {MAX_READ_DEPTH} levels that rendering and extraction read"
                ),
            ));
        }
        let pdf = self
            .hayro_pdf
            .as_ref()
            .expect("hayro view was built immediately before");
        self.content_guard.check(pdf, pages)?;
        Ok(pdf)
    }

    /// Run an operation that lets hayro read or interpret pages, and turn a
    /// panic in it into `PdfError`.
    ///
    /// hayro 0.8 panics on some malformed colour data, such as a tint
    /// transform that returns the wrong number of components
    /// (LaurenzV/hayro#1546), and a panic that reached PyO3 would raise
    /// `PanicException`, which derives from `BaseException`. hayro may leave
    /// its snapshot and caches half-built when it panics, so everything an
    /// edit drops is dropped here too, and the next operation builds a new
    /// snapshot from the graph.
    pub(super) fn contain_renderer_panics<R>(
        &mut self,
        operation: impl FnOnce(&mut Self) -> PyResult<R>,
    ) -> PyResult<R> {
        match catch_unwind(AssertUnwindSafe(|| operation(&mut *self))) {
            Ok(result) => result,
            Err(payload) => {
                self.invalidate_hayro_pdf();
                let message = payload
                    .downcast_ref::<&str>()
                    .copied()
                    .or_else(|| payload.downcast_ref::<String>().map(String::as_str))
                    .unwrap_or("no message");
                Err(PdfError::new_err(format!(
                    "the renderer failed on malformed input: {message}"
                )))
            }
        }
    }

    /// Serialize a snapshot, normalizing a rendering copy when appearances or
    /// the page tree need it.
    ///
    /// `flattened_pages` lists the walker's pages directly under the root
    /// page-tree node and fits that copy to the nesting hayro reads, which
    /// returns the zero-based indexes of the pages it cut. None of this
    /// changes the editable or saved document.
    pub(super) fn render_snapshot(
        &mut self,
        text_markup_appearances: Option<Vec<TextMarkupAppearancePlan>>,
        flattened_pages: Option<&[ObjectId]>,
    ) -> PyResult<(Vec<u8>, Vec<usize>)> {
        let mut cut = Vec::new();
        let normalize = text_markup_appearances
            .as_ref()
            .is_some_and(|plans| !plans.is_empty())
            || flattened_pages.is_some();
        let data = if normalize {
            let mut doc = if self.max_interpretation_size.is_some() {
                let data = self.current_bytes()?;
                Document::load_mem(&data).map_err(|error| {
                    PdfError::new_err(format!(
                        "failed to prepare bounded PDF for rendering: {}",
                        LopdfErrorChain(&error)
                    ))
                })?
            } else {
                self.doc.clone()
            };
            if let Some(plans) = text_markup_appearances {
                synthesize_missing_text_markup_appearances_for_render(&mut doc, plans)?;
            }
            if let Some(pages) = flattened_pages {
                let pages_id = page_tree_root(&doc)?;
                rebuild_flat_page_tree(&mut doc, pages_id, pages, None)?;
                cut = read_depth::fit_rendering_copy(&mut doc, pages_id, pages)?;
            }
            serialize_pdf_with_limit(
                &mut doc,
                None,
                self.max_interpretation_size,
                "interpretation_size",
                "serialized rendering and extraction snapshot",
            )?
        } else {
            self.current_bytes()?
        };
        Ok((data, cut))
    }

    /// Build InterpreterSettings with fallbacks and the warning sink.
    pub(super) fn interpreter_settings(&self) -> InterpreterSettings {
        let mut settings = InterpreterSettings::default();
        if self.fallback_fonts.is_configured() {
            let fonts = self.fallback_fonts.clone();
            let default_resolver = settings.font_resolver.clone();
            settings.font_resolver = Arc::new(move |query| {
                if let FontQuery::Fallback(fallback) = query
                    && let Some(picked) = pick_cjk_fallback(&fonts, fallback)
                {
                    return Some(picked);
                }
                default_resolver(query)
            });
        }
        // Collect hayro warnings in pending_warnings, deduplicating messages.
        let sink = Arc::clone(&self.pending_warnings);
        settings.warning_sink = Arc::new(move |warning| {
            let message = match warning {
                InterpreterWarning::UnsupportedFont => {
                    "encountered an unsupported font format; some glyphs could not be processed"
                }
                InterpreterWarning::ImageDecodeFailure => "failed to decode an image",
                // hayro also reports a check box whose `/AS /Off` has no Off
                // stream. ISO 32000 makes that appearance optional, and forms
                // such as f1040 omit it for every unchecked box, so this
                // warning would fire on valid input during every operation.
                InterpreterWarning::UnresolvedAnnotationAppearance => return,
                _ => "encountered an unsupported PDF construct during interpretation",
            };
            if let Ok(mut pending) = sink.lock()
                && !pending.iter().any(|m| m == message)
            {
                pending.push(message.to_owned());
            }
        });
        settings
    }

    /// Validate and render a page to hayro Pixmap; shared by PNG and Pixmap APIs.
    ///
    /// `annotations` false leaves every annotation's appearance out. The
    /// retained render cache holds fonts and images, which do not depend on
    /// that choice.
    pub(super) fn render_pixmap_impl(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        scale: RenderScale,
        background: Option<(u8, u8, u8, u8)>,
        clip: Option<(f64, f64, f64, f64)>,
        annotations: bool,
    ) -> PyResult<hayro::vello_cpu::Pixmap> {
        let mut interpreter_settings = self.interpreter_settings();
        interpreter_settings.render_annotations = annotations;
        self.contain_renderer_panics(|this| {
            py.detach(|| {
                this.hayro_view_for(PageSelection::One(page_number))?;
                let pdf = this
                    .hayro_pdf
                    .as_ref()
                    .expect("hayro view was built immediately before");
                this.render_cache
                    .with_cache(pdf, &[page_number], |pdf, cache| {
                        render_pdf_page(
                            pdf,
                            cache,
                            &interpreter_settings,
                            page_number,
                            scale,
                            background,
                            clip,
                        )
                    })
                    .map_err(PdfError::new_err)
            })
        })
    }
}

/// Implements [`_Document::snapshot_source`].
pub(super) fn snapshot_source(this: &_Document) -> &'static str {
    match (&this.hayro_pdf, this.hayro_pdf_from_original) {
        (None, _) => "none",
        (Some(_), true) => "original",
        (Some(_), false) => "serialized",
    }
}

/// Implements [`_Document::render_page_png`].
pub(super) fn render_page_png(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    scale: RenderScale,
    alpha: bool,
    background: Option<(u8, u8, u8, u8)>,
    max_output_size: Option<usize>,
) -> PyResult<Vec<u8>> {
    if max_output_size == Some(0) {
        return Err(PyValueError::new_err(
            "max_output_size must be a positive integer or None",
        ));
    }
    let pixmap = this.render_pixmap_impl(py, page_number, scale, background, None, true)?;
    // PNG encoding can cost more than rasterization, so release the GIL and
    // use Fast/fdeflate. Balanced is tens of times slower for about 10%
    // smaller output and made PNG the dominant render cost in benchmarks.
    let layout = Layout { gray: false, alpha };
    py.detach(|| rendered_image(pixmap, layout, Encoding::Png, max_output_size))
}

/// Implements [`_Document::render_pages_encoded`].
#[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
pub(super) fn render_pages_encoded(
    this: &mut _Document,
    py: Python<'_>,
    page_numbers: Vec<u32>,
    scale: RenderScale,
    layout: Layout,
    background: Option<(u8, u8, u8, u8)>,
    annotations: bool,
    encoding: Encoding,
    workers: usize,
    max_output_size: Option<usize>,
) -> PyResult<Vec<Vec<u8>>> {
    if workers == 0 || workers > 64 {
        return Err(PyValueError::new_err("workers must be between 1 and 64"));
    }
    if page_numbers.is_empty() {
        return Ok(Vec::new());
    }
    this.check_page_entries(page_numbers.len(), "render")?;
    let mut interpreter_settings = this.interpreter_settings();
    interpreter_settings.render_annotations = annotations;
    this.contain_renderer_panics(|this| {
        py.detach(|| {
            this.hayro_view_for(PageSelection::Many(&page_numbers))?;
            let pdf = this
                .hayro_pdf
                .as_ref()
                .expect("hayro view was built immediately before");
            let max_pixels = page_numbers
                .iter()
                .try_fold(0u64, |maximum, &page_number| {
                    render_pixel_count(pdf, page_number, scale).map(|pixels| maximum.max(pixels))
                })
                .map_err(PdfError::new_err)?;
            let output_bytes = AtomicUsize::new(0);
            let renderer = BatchRenderer {
                interpreter_settings: &interpreter_settings,
                scale,
                layout,
                encoding,
                background,
                max_output_size,
                output_bytes: &output_bytes,
            };
            // A serial batch starts from the document's retained cache, so
            // fonts parsed by earlier single-page or batch calls are reused.
            let render_serial = || renderer.render_serial(pdf, &this.render_cache, &page_numbers);
            #[cfg(target_os = "emscripten")]
            let rendered = {
                let _ = max_pixels;
                render_serial()
            };
            #[cfg(not(target_os = "emscripten"))]
            let rendered: PyResult<Vec<Vec<u8>>> = {
                let estimated_page_bytes =
                    max_pixels.saturating_mul(ESTIMATED_RENDER_BYTES_PER_PIXEL);
                let memory_limited_workers =
                    usize::try_from((MAX_PARALLEL_RENDER_BYTES / estimated_page_bytes).max(1))
                        .unwrap_or(usize::MAX);
                let worker_count = workers.min(page_numbers.len()).min(memory_limited_workers);
                if worker_count == 1 {
                    render_serial()
                } else {
                    let pool = rayon::ThreadPoolBuilder::new()
                        .num_threads(worker_count)
                        .thread_name(|index| format!("pylopdf-render-{index}"))
                        .build()
                        .map_err(|error| {
                            PdfError::new_err(format!(
                                "failed to create render worker pool: {error}"
                            ))
                        })?;
                    let next_page = AtomicUsize::new(0);
                    let indexed = pool.install(|| {
                        (0..worker_count)
                            .into_par_iter()
                            .map(|_| {
                                let mut cache = RenderCache::new();
                                let mut cached_pages = 0usize;
                                let mut rendered = Vec::new();
                                loop {
                                    let index = next_page.fetch_add(1, Ordering::Relaxed);
                                    let Some(&page_number) = page_numbers.get(index) else {
                                        break;
                                    };
                                    // Bound each worker cache like a serial chunk.
                                    if cached_pages == PAGE_BATCH_CHUNK {
                                        cache = RenderCache::new();
                                        cached_pages = 0;
                                    }
                                    cached_pages += 1;
                                    rendered.try_reserve(1).map_err(|error| {
                                        PdfError::new_err(format!(
                                            "failed to grow worker render results: {error}"
                                        ))
                                    })?;
                                    rendered
                                        .push((index, renderer.render(pdf, &cache, page_number)?));
                                }
                                Ok::<_, PyErr>(rendered)
                            })
                            .try_reduce(Vec::new, |mut left, mut right| {
                                left.try_reserve(right.len()).map_err(|error| {
                                    PdfError::new_err(format!(
                                        "failed to merge worker render results: {error}"
                                    ))
                                })?;
                                left.append(&mut right);
                                Ok(left)
                            })
                    });
                    let mut ordered = Vec::new();
                    ordered
                        .try_reserve_exact(page_numbers.len())
                        .map_err(|error| {
                            PdfError::new_err(format!(
                                "failed to allocate ordered render results: {error}"
                            ))
                        })?;
                    ordered.resize_with(page_numbers.len(), || None);
                    for (index, png) in indexed? {
                        ordered[index] = Some(png);
                    }
                    let mut rendered = Vec::new();
                    rendered
                        .try_reserve_exact(page_numbers.len())
                        .map_err(|error| {
                            PdfError::new_err(format!(
                                "failed to allocate final render results: {error}"
                            ))
                        })?;
                    for png in ordered {
                        rendered.push(png.expect("every claimed page is rendered"));
                    }
                    Ok(rendered)
                }
            };
            rendered
        })
    })
}

/// Implements [`_Document::render_page_pixmap`].
#[allow(clippy::too_many_arguments)] // Mirrors the Python-facing method.
pub(super) fn render_page_pixmap(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    scale: RenderScale,
    layout: Layout,
    background: Option<(u8, u8, u8, u8)>,
    clip: Option<(f64, f64, f64, f64)>,
    annotations: bool,
) -> PyResult<crate::pixmap::Pixmap> {
    let pixmap = this.render_pixmap_impl(py, page_number, scale, background, clip, annotations)?;
    let width = u32::from(pixmap.width());
    let height = u32::from(pixmap.height());
    // Release the GIL: unpremultiplication touches every rendered pixel.
    let data = py.detach(|| raster_samples(pixmap, layout));
    Ok(crate::pixmap::Pixmap {
        width,
        height,
        layout,
        data: Arc::new(data),
    })
}

/// Implements [`_Document::render_page_svg`].
pub(super) fn render_page_svg(
    this: &mut _Document,
    py: Python<'_>,
    page_number: u32,
    annotations: bool,
    max_output_size: Option<usize>,
) -> PyResult<String> {
    let mut interpreter_settings = this.interpreter_settings();
    interpreter_settings.render_annotations = annotations;
    this.contain_renderer_panics(|this| {
        py.detach(|| {
            let pdf = this.hayro_view_for(PageSelection::One(page_number))?;
            let pages = pdf.pages();
            let page = page_number
                .checked_sub(1)
                .and_then(|index| pages.get(index as usize))
                .ok_or_else(|| PdfError::new_err(format!("page {page_number} does not exist")))?;
            let cache = hayro_svg::RenderCache::new();
            let settings = hayro_svg::SvgRenderSettings::default();
            let svg = hayro_svg::convert(page, &cache, &interpreter_settings, &settings);
            if let Some(limit) = max_output_size
                && svg.len() > limit
            {
                return Err(limit_err(
                    "svg_output_size",
                    format!("rendered SVG exceeds the {limit}-byte UTF-8 output limit"),
                ));
            }
            Ok(svg)
        })
    })
}
