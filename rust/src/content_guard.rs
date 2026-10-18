//! Check the content streams of a page before hayro interprets it.
//!
//! hayro 0.7 parses arrays and dictionaries in content streams by unbounded
//! recursion, so one stream with a few hundred thousand nested `[` kills the
//! process (LaurenzV/hayro#1347). Nothing can catch that overflow, and lopdf
//! never parses content at load, so `DocumentLimits.max_object_depth` does not
//! cover it. [`ContentGuard`] decodes every stream that hayro may interpret for
//! a page with hayro's own decoder, so the bytes are exactly the ones its
//! parser will see, and refuses the page with `LimitError` code
//! `content_nesting_depth` when [`crate::content_scan::scan`] cannot clear one.
//!
//! hayro 0.7 parses content operators at four places, which this walk mirrors:
//!
//! * the page's `/Contents`, which `Page::page_stream` joins and caches, so the
//!   interpreter later reuses the very decode the scan paid for;
//! * Form XObjects, whether drawn by `Do`, as an annotation or widget
//!   appearance `/AP /N`, or as an ExtGState soft-mask group `/SMask /G`;
//! * tiling pattern streams;
//! * Type 3 glyph procedures in `/CharProcs`, including fonts that only an
//!   ExtGState `/Font` entry selects.
//!
//! Each stream's `/Resources` leads to more of them. hayro resolves a name
//! through the resources of every enclosing stream and the page, so the walk
//! visits all entries of every reachable resource dictionary instead of only
//! the names the content uses. The check is lazy: a page costs one walk, and
//! streams and dictionaries shared with an earlier page are skipped. Only
//! streams are decoded, never the whole document, and a decode that hayro's
//! interpreter would perform anyway is the only extra work.
//!
//! The state belongs to one hayro snapshot. Any edit drops the snapshot and
//! this state with it. An error clears the state too, so nothing recorded
//! before a failed check can stand in for a stream the check did not finish.

use std::collections::{HashSet, TryReserveError};
use std::hash::Hash;

use hayro::hayro_syntax::Pdf;
use hayro::hayro_syntax::object::dict::keys::{
    ANNOTS, AP, CHAR_PROCS, EXT_G_STATE, FONT, FORM, G, N, PATTERN, RESOURCES, SMASK, SUBTYPE,
    XOBJECT,
};
use hayro::hayro_syntax::object::{Array, Dict, Name, Object, ObjectIdentifier, Stream};
use hayro::hayro_syntax::page::{Page, Resources};
use pyo3::prelude::*;

use crate::content_scan::{self, MAX_NESTING_DEPTH, Refusal};
use crate::document::{PdfError, limit_err};

/// The one-based pages an operation is about to interpret.
pub(crate) enum PageSelection<'a> {
    One(u32),
    Many(&'a [u32]),
    All,
}

/// A stream or dictionary still to be visited for one page.
enum Work<'a> {
    /// A stream whose content hayro interprets.
    Content(Stream<'a>),
    /// A `/Resources` dictionary reached through a stream or font.
    Resources(Dict<'a>),
    /// A font dictionary that may carry Type 3 glyph procedures.
    Font(Dict<'a>),
}

/// What the check has already cleared for the current hayro snapshot.
#[derive(Default)]
pub(crate) struct ContentGuard {
    /// One bit per zero-based page whose reachable content was cleared.
    pages: Vec<u64>,
    /// Content streams that were decoded and cleared.
    streams: HashSet<ObjectIdentifier>,
    /// Fonts that were examined.
    fonts: HashSet<ObjectIdentifier>,
    /// Graphics state dictionaries that were examined.
    graphics_states: HashSet<ObjectIdentifier>,
    /// `/XObject` entries that are not forms, mostly images, which hayro never
    /// reads as content and which are costly to decode.
    inert: HashSet<ObjectIdentifier>,
    /// Resource category and resource dictionaries that were visited, by the
    /// address of their bytes in the snapshot. Decoded object streams live as
    /// long as the snapshot, so an address names one dictionary.
    dictionaries: HashSet<(usize, usize)>,
}

impl ContentGuard {
    /// Forget everything cleared so far.
    pub(crate) fn reset(&mut self) {
        *self = Self::default();
    }

    /// Clear the content that interpreting `selection` can reach.
    ///
    /// Pages beyond the snapshot's page count are left to the caller, which
    /// reports them as missing.
    pub(crate) fn check(&mut self, pdf: &Pdf, selection: PageSelection<'_>) -> PyResult<()> {
        let pages = pdf.pages();
        let result = match selection {
            PageSelection::One(number) => self.check_page(pages, number),
            PageSelection::Many(numbers) => numbers
                .iter()
                .try_for_each(|&number| self.check_page(pages, number)),
            PageSelection::All => {
                (0..pages.len()).try_for_each(|index| self.check_indexed_page(pages, index))
            }
        };
        if result.is_err() {
            self.reset();
        }
        result
    }

    fn check_page(&mut self, pages: &[Page<'_>], number: u32) -> PyResult<()> {
        match usize::try_from(number).ok().and_then(|n| n.checked_sub(1)) {
            Some(index) => self.check_indexed_page(pages, index),
            None => Ok(()),
        }
    }

    fn check_indexed_page(&mut self, pages: &[Page<'_>], index: usize) -> PyResult<()> {
        let Some(page) = pages.get(index) else {
            return Ok(());
        };
        if self.page_cleared(index) {
            return Ok(());
        }
        let number = index + 1;
        if let Some(content) = page.page_stream() {
            content_scan::scan(content)
                .map_err(|refusal| refusal_error(&Subject::Page(number), refusal))?;
        }
        let mut work = Vec::new();
        let mut level = Some(page.resources());
        while let Some(resources) = level {
            self.visit_resources(resources, &mut work)?;
            level = resources.parent();
        }
        if let Some(annotations) = page.raw().get::<Array<'_>>(ANNOTS) {
            for annotation in annotations.iter::<Dict<'_>>() {
                self.visit_appearance(&annotation, &mut work)?;
            }
        }
        while let Some(item) = work.pop() {
            match item {
                Work::Content(stream) => self.visit_content(&stream, number, &mut work)?,
                Work::Resources(dictionary) => {
                    self.visit_resource_dictionary(&dictionary, &mut work)?
                }
                Work::Font(font) => self.visit_font(&font, &mut work)?,
            }
        }
        self.clear_page(index)
    }

    fn page_cleared(&self, index: usize) -> bool {
        self.pages
            .get(index / 64)
            .is_some_and(|word| word >> (index % 64) & 1 == 1)
    }

    fn clear_page(&mut self, index: usize) -> PyResult<()> {
        let word = index / 64;
        if self.pages.len() <= word {
            self.pages
                .try_reserve(word + 1 - self.pages.len())
                .map_err(allocation_error)?;
            self.pages.resize(word + 1, 0);
        }
        self.pages[word] |= 1 << (index % 64);
        Ok(())
    }

    /// Queue the streams and fonts that one level of a resource chain names.
    fn visit_resources<'a>(
        &mut self,
        resources: &Resources<'a>,
        work: &mut Vec<Work<'a>>,
    ) -> PyResult<()> {
        self.visit_categories(
            &resources.x_objects,
            &resources.patterns,
            &resources.fonts,
            &resources.ext_g_states,
            work,
        )
    }

    /// Queue what a stream's or font's `/Resources` dictionary names.
    fn visit_resource_dictionary<'a>(
        &mut self,
        resources: &Dict<'a>,
        work: &mut Vec<Work<'a>>,
    ) -> PyResult<()> {
        if !self.first_visit(resources)? {
            return Ok(());
        }
        let category = |key: &[u8]| resources.get::<Dict<'a>>(key).unwrap_or_default();
        self.visit_categories(
            &category(XOBJECT),
            &category(PATTERN),
            &category(FONT),
            &category(EXT_G_STATE),
            work,
        )
    }

    fn visit_categories<'a>(
        &mut self,
        x_objects: &Dict<'a>,
        patterns: &Dict<'a>,
        fonts: &Dict<'a>,
        ext_g_states: &Dict<'a>,
        work: &mut Vec<Work<'a>>,
    ) -> PyResult<()> {
        if self.first_visit(x_objects)? {
            for key in x_objects.keys() {
                let reference = x_objects.get_ref(key.as_ref()).map(ObjectIdentifier::from);
                if reference
                    .is_some_and(|id| self.streams.contains(&id) || self.inert.contains(&id))
                {
                    continue;
                }
                let Some(stream) = x_objects.get::<Stream<'a>>(key.as_ref()) else {
                    continue;
                };
                if is_form(&stream) {
                    push(work, Work::Content(stream))?;
                } else if let Some(id) = reference {
                    insert(&mut self.inert, id)?;
                }
            }
        }
        if self.first_visit(patterns)? {
            for key in patterns.keys() {
                if self.is_cleared_stream(patterns, key.as_ref()) {
                    continue;
                }
                if let Some(Object::Stream(stream)) = patterns.get::<Object<'a>>(key.as_ref()) {
                    push(work, Work::Content(stream))?;
                }
            }
        }
        if self.first_visit(fonts)? {
            for key in fonts.keys() {
                if self.is_examined_font(fonts, key.as_ref()) {
                    continue;
                }
                if let Some(font) = fonts.get::<Dict<'a>>(key.as_ref()) {
                    push(work, Work::Font(font))?;
                }
            }
        }
        if self.first_visit(ext_g_states)? {
            for key in ext_g_states.keys() {
                if let Some(id) = ext_g_states
                    .get_ref(key.as_ref())
                    .map(ObjectIdentifier::from)
                    && !insert(&mut self.graphics_states, id)?
                {
                    continue;
                }
                if let Some(state) = ext_g_states.get::<Dict<'a>>(key.as_ref()) {
                    visit_graphics_state(&state, work)?;
                }
            }
        }
        Ok(())
    }

    /// Queue an annotation's or widget's normal appearance stream.
    fn visit_appearance<'a>(
        &mut self,
        annotation: &Dict<'a>,
        work: &mut Vec<Work<'a>>,
    ) -> PyResult<()> {
        let Some(appearances) = annotation.get::<Dict<'a>>(AP) else {
            return Ok(());
        };
        if self.is_cleared_stream(&appearances, N) {
            return Ok(());
        }
        if let Some(stream) = appearances.get::<Stream<'a>>(N) {
            push(work, Work::Content(stream))?;
        }
        Ok(())
    }

    /// Scan one stream's decoded content and queue its resources.
    fn visit_content<'a>(
        &mut self,
        stream: &Stream<'a>,
        page_number: usize,
        work: &mut Vec<Work<'a>>,
    ) -> PyResult<()> {
        let id = stream.obj_id();
        let tracked = id != ObjectIdentifier::new(0, 0);
        if tracked && self.streams.contains(&id) {
            return Ok(());
        }
        // A stream that fails to decode is skipped by hayro as well.
        if let Ok(decoded) = stream.decoded() {
            content_scan::scan(&decoded)
                .map_err(|refusal| refusal_error(&Subject::Stream(id, page_number), refusal))?;
        }
        if tracked {
            insert(&mut self.streams, id)?;
        }
        if let Some(resources) = stream.dict().get::<Dict<'a>>(RESOURCES) {
            push(work, Work::Resources(resources))?;
        }
        Ok(())
    }

    /// Queue a Type 3 font's glyph procedures and its resources.
    fn visit_font<'a>(&mut self, font: &Dict<'a>, work: &mut Vec<Work<'a>>) -> PyResult<()> {
        if let Some(id) = font.obj_id()
            && !insert(&mut self.fonts, id)?
        {
            return Ok(());
        }
        if let Some(procedures) = font.get::<Dict<'a>>(CHAR_PROCS) {
            for key in procedures.keys() {
                if self.is_cleared_stream(&procedures, key.as_ref()) {
                    continue;
                }
                if let Some(stream) = procedures.get::<Stream<'a>>(key.as_ref()) {
                    push(work, Work::Content(stream))?;
                }
            }
        }
        if let Some(resources) = font.get::<Dict<'a>>(RESOURCES) {
            push(work, Work::Resources(resources))?;
        }
        Ok(())
    }

    /// Whether `key` references a stream that was already cleared.
    fn is_cleared_stream(&self, dictionary: &Dict<'_>, key: &[u8]) -> bool {
        dictionary
            .get_ref(key)
            .is_some_and(|reference| self.streams.contains(&reference.into()))
    }

    /// Whether `key` references a font that was already examined.
    fn is_examined_font(&self, dictionary: &Dict<'_>, key: &[u8]) -> bool {
        dictionary
            .get_ref(key)
            .is_some_and(|reference| self.fonts.contains(&reference.into()))
    }

    /// Record a dictionary; false when it was visited or has no entries.
    fn first_visit(&mut self, dictionary: &Dict<'_>) -> PyResult<bool> {
        if dictionary.is_empty() {
            return Ok(false);
        }
        let bytes = dictionary.data();
        insert(
            &mut self.dictionaries,
            (bytes.as_ptr() as usize, bytes.len()),
        )
    }
}

/// Queue an ExtGState's soft-mask group and the font it selects.
fn visit_graphics_state<'a>(state: &Dict<'a>, work: &mut Vec<Work<'a>>) -> PyResult<()> {
    if let Some(group) = state
        .get::<Dict<'a>>(SMASK)
        .and_then(|mask| mask.get::<Stream<'a>>(G))
    {
        push(work, Work::Content(group))?;
    }
    if let Some(Object::Dict(font)) = state
        .get::<Array<'a>>(FONT)
        .and_then(|selection| selection.iter::<Object<'a>>().next())
    {
        push(work, Work::Font(font))?;
    }
    Ok(())
}

/// Whether hayro draws the stream as a Form XObject.
fn is_form(stream: &Stream<'_>) -> bool {
    stream
        .dict()
        .get::<Name<'_>>(SUBTYPE)
        .is_some_and(|name| name.as_ref() == FORM)
}

/// What a refused stream is, for the error message.
enum Subject {
    /// The joined `/Contents` of a one-based page.
    Page(usize),
    /// A stream reachable from a one-based page.
    Stream(ObjectIdentifier, usize),
}

fn refusal_error(subject: &Subject, refusal: Refusal) -> PyErr {
    let whose = match subject {
        Subject::Page(number) => format!("content of page {number}"),
        Subject::Stream(id, number) => format!(
            "content stream {} {} R reachable from page {number}",
            id.obj_number, id.gen_number
        ),
    };
    match refusal {
        Refusal::Depth => limit_err(
            "content_nesting_depth",
            format!(
                "{whose} nests arrays or dictionaries deeper than the {MAX_NESTING_DEPTH}-level limit"
            ),
        ),
        Refusal::EmbeddedStream => limit_err(
            "content_nesting_depth",
            format!("{whose} embeds a stream object, whose body cannot be checked for nesting"),
        ),
        Refusal::Ambiguous => limit_err(
            "content_nesting_depth",
            format!("{whose} has inline image boundaries too ambiguous to check for nesting"),
        ),
        Refusal::Allocation(error) => allocation_error(error),
    }
}

fn allocation_error(error: TryReserveError) -> PyErr {
    PdfError::new_err(format!(
        "failed to allocate content nesting check state: {error}"
    ))
}

/// Insert after reserving fallibly; false when the value was already present.
fn insert<T: Eq + Hash>(set: &mut HashSet<T>, value: T) -> PyResult<bool> {
    set.try_reserve(1).map_err(allocation_error)?;
    Ok(set.insert(value))
}

fn push<'a>(work: &mut Vec<Work<'a>>, item: Work<'a>) -> PyResult<()> {
    work.try_reserve(1).map_err(allocation_error)?;
    work.push(item);
    Ok(())
}
