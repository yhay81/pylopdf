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
//! streams and dictionaries shared with an earlier page are skipped. A
//! dictionary counts as seen only in the role that was walked, because one
//! object may serve as several resource categories, and a font is identified
//! by its own bytes, because the object number of a direct dictionary names
//! its container. Only streams are decoded, never the whole document, and a
//! decode that hayro's interpreter would perform anyway is the only extra work.
//!
//! The walk resolves every reference through a fresh reader context
//! ([`Fresh`]). hayro keeps the chain of objects being read in a dictionary's
//! context and refuses to follow a reference back into it as a cycle, so one
//! dictionary resolves fewer references the longer the path that led to it. A
//! walk that visits each object once and lets the first path decide would
//! overlook whatever a shorter path resolves, including the path hayro takes
//! when it draws.
//!
//! The same walk reaches the colour spaces, shadings, and functions that hayro
//! builds by recursion over the objects they name, which overflows its stack
//! just as deeply nested content does: a chain of thousands of indirect objects
//! that name each other, or a Type 4 function with hundreds of thousands of
//! nested braces. [`GraphGuard`] measures what each root reaches and refuses the
//! page with `LimitError` code `resource_nesting_depth`. The roots are the
//! `/ColorSpace` and `/Shading` entries of every reachable resource dictionary,
//! shading patterns, the colour space of image XObjects and of soft-mask groups,
//! and the `/TR` and `/TR2` transfer functions of graphics states and soft
//! masks.
//!
//! The walk parses every array, dictionary, and stream it follows a reference
//! to at most once for each purpose ([`Role`]). A document can name one large
//! object from thousands of places, and the walk lists every entry of every
//! resource dictionary whether or not content draws it, so parsing the object
//! for each entry would multiply their sizes: 3,000 entries that name one form
//! of 20,000 keys (0.23 MB) kept the first interpretation busy for several
//! seconds, where hayro, which never touches the unused entries, needs
//! milliseconds. Every reference is therefore recorded by role before the
//! object it names is parsed ([`ContentGuard::first_follow`]), whether or not
//! the object turns out to be readable, and later entries that name it are
//! skipped. A role is what the walk does with the object, never where it was
//! found, so skipping is exact: the first visit did all the work and every
//! later one would repeat it. A stream that was queued as content in one role
//! says nothing about another, for a stream may be a tiling pattern and an
//! image at once.
//!
//! A value that a visited object keeps behind a reference is read once as well.
//! This walk reads only one such value itself, the `/Subtype` of an XObject
//! stream ([`ContentGuard::is_form`]), and remembers by reference whether it is
//! `/Form`: parsing the object behind a reference costs as much as its size,
//! however small the name the walk wants, so 3,000 forms that name one array of
//! 500,000 numbers kept the first interpretation busy for 17 seconds. What
//! hayro reads while it parses and decodes a stream stays outside this rule:
//! `/Length`, `/Filter`, `/DecodeParms`, and the parameters inside them. The
//! walk can only ask hayro to decode the stream, and hayro reads them again for
//! every stream that names them, as it would when drawing it.
//!
//! The state belongs to one hayro snapshot. Any edit drops the snapshot and
//! this state with it. An error clears the state too, so nothing recorded
//! before a failed check can stand in for a stream the check did not finish.

use std::collections::{HashMap, HashSet, TryReserveError};
use std::hash::Hash;

use hayro::hayro_syntax::Pdf;
use hayro::hayro_syntax::object::dict::keys::{
    ANNOTS, AP, CHAR_PROCS, COLORSPACE, CS, EXT_G_STATE, FONT, FORM, FUNCTION, G, GROUP, N,
    PATTERN, RESOURCES, SHADING, SMASK, SUBTYPE, TR, TR2, XOBJECT,
};
use hayro::hayro_syntax::object::{
    Array, Dict, MaybeRef, Name, Object, ObjectIdentifier, Stream, dict_or_stream,
};
use hayro::hayro_syntax::page::{Page, Resources};
use hayro::hayro_syntax::xref::XRef;
use pyo3::prelude::*;

use crate::content_scan::{self, MAX_NESTING_DEPTH, Refusal};
use crate::document::{PdfError, limit_err};
use crate::graph_guard::{self, Elements, GraphGuard, MAX_CHAIN_DEPTH};
use crate::postscript_scan::MAX_BRACE_DEPTH;

/// The one-based pages an operation is about to interpret.
pub(crate) enum PageSelection<'a> {
    One(u32),
    Many(&'a [u32]),
    All,
}

/// Reads values the way a new reader would: a reference is followed from an
/// empty chain of objects being read, never from the one the dictionary it sits
/// in was reached by.
#[derive(Clone, Copy)]
struct Fresh<'a>(&'a XRef);

impl<'a> Fresh<'a> {
    /// The dictionary under `key`; a stream object yields its dictionary.
    fn dictionary(self, from: &Dict<'a>, key: &[u8]) -> Option<Dict<'a>> {
        match from.get_ref(key) {
            Some(reference) => self.0.get::<Dict<'a>>(reference.into()),
            None => from.get::<Dict<'a>>(key),
        }
    }

    fn stream(self, from: &Dict<'a>, key: &[u8]) -> Option<Stream<'a>> {
        match from.get_ref(key) {
            Some(reference) => self.0.get::<Stream<'a>>(reference.into()),
            None => from.get::<Stream<'a>>(key),
        }
    }

    fn object(self, from: &Dict<'a>, key: &[u8]) -> Option<Object<'a>> {
        match from.get_ref(key) {
            Some(reference) => self.0.get::<Object<'a>>(reference.into()),
            None => from.get::<Object<'a>>(key),
        }
    }

    fn array(self, from: &Dict<'a>, key: &[u8]) -> Option<Array<'a>> {
        match from.get_ref(key) {
            Some(reference) => self.0.get::<Array<'a>>(reference.into()),
            None => from.get::<Array<'a>>(key),
        }
    }
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

/// What the walk does with an object. One object may serve several roles at
/// once, for example `/Resources << /XObject 5 0 R /Font 5 0 R >>`, and each
/// role reads it differently, so a visit in one role says nothing about
/// another.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
enum Role {
    /// A `/Resources` dictionary of a stream or a font.
    Resources,
    /// An `/XObject` category dictionary.
    XObjects,
    /// A `/Pattern` category dictionary.
    Patterns,
    /// A `/Font` category dictionary.
    Fonts,
    /// An `/ExtGState` category dictionary.
    GraphicsStates,
    /// A `/ColorSpace` category dictionary.
    ColorSpaces,
    /// A `/Shading` category dictionary.
    Shadings,
    /// One font dictionary.
    Font,
    /// An `/XObject` entry: a form to queue, or an image whose colour space is
    /// measured.
    XObject,
    /// A `/Pattern` entry: a tiling pattern stream to queue, or a shading
    /// pattern whose shading is measured.
    Pattern,
    /// An `/ExtGState` entry.
    GraphicsState,
    /// A shading, whether an entry of `/Shading` or the `/Shading` of a shading
    /// pattern.
    Shading,
    /// The `/SMask` dictionary of a graphics state.
    SoftMask,
    /// The `/G` group of a soft mask: its colour space and its content.
    SoftMaskGroup,
    /// The `/Group` attributes of a soft-mask group.
    GroupAttributes,
    /// The `/Font` array of a graphics state.
    FontSelection,
    /// The font that a `/Font` array names first, which is the only one hayro
    /// reads.
    SelectedFont,
    /// The `/AP` dictionary of an annotation.
    Appearances,
    /// The `/CharProcs` dictionary of a Type 3 font.
    GlyphProcedures,
    /// A stream that hayro interprets as content and that nothing else reads:
    /// the `/N` appearance of an annotation or an entry of `/CharProcs`.
    ContentStream,
}

/// What the check has already cleared for the current hayro snapshot.
#[derive(Default)]
pub(crate) struct ContentGuard {
    /// One bit per zero-based page whose reachable content was cleared.
    pages: Vec<u64>,
    /// Content streams that were decoded and cleared. Several roles can queue
    /// one stream, and the walk decodes it when it takes the first of them from
    /// its queue.
    streams: HashSet<ObjectIdentifier>,
    /// Dictionaries that were visited, by role and by the address of their
    /// bytes in the snapshot. Decoded object streams live as long as the
    /// snapshot, so an address names one dictionary. The object number would
    /// not: every direct dictionary inside an indirect object reports the
    /// number of that object.
    dictionaries: HashSet<(Role, usize, usize)>,
    /// References that were followed, by role, whether or not the object they
    /// name could be read. A reference is recorded before the object is parsed,
    /// so one object that thousands of entries name is parsed once.
    followed: HashSet<(Role, ObjectIdentifier)>,
    /// Whether the value behind each `/Subtype` reference of an XObject reads
    /// as the name `/Form`, so that the value is read once however many
    /// streams name it. The read follows no further reference, so the chain of
    /// objects being read cannot change what the value holds: a stream that
    /// names itself holds no name.
    form_subtypes: HashMap<ObjectIdentifier, bool>,
    /// Colour space and function chains that were measured and cleared.
    graph: GraphGuard,
    /// The one-based page being checked, for error messages.
    page: usize,
}

/// The resource categories of one dictionary.
struct Categories<'a> {
    x_objects: Dict<'a>,
    patterns: Dict<'a>,
    fonts: Dict<'a>,
    ext_g_states: Dict<'a>,
    color_spaces: Dict<'a>,
    shadings: Dict<'a>,
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
        let fresh = Fresh(pdf.xref());
        let result = match selection {
            PageSelection::One(number) => self.check_page(fresh, pages, number),
            PageSelection::Many(numbers) => numbers
                .iter()
                .try_for_each(|&number| self.check_page(fresh, pages, number)),
            PageSelection::All => {
                (0..pages.len()).try_for_each(|index| self.check_indexed_page(fresh, pages, index))
            }
        };
        if result.is_err() {
            self.reset();
        }
        result
    }

    fn check_page<'a>(
        &mut self,
        fresh: Fresh<'a>,
        pages: &[Page<'a>],
        number: u32,
    ) -> PyResult<()> {
        match usize::try_from(number).ok().and_then(|n| n.checked_sub(1)) {
            Some(index) => self.check_indexed_page(fresh, pages, index),
            None => Ok(()),
        }
    }

    fn check_indexed_page<'a>(
        &mut self,
        fresh: Fresh<'a>,
        pages: &[Page<'a>],
        index: usize,
    ) -> PyResult<()> {
        let Some(page) = pages.get(index) else {
            return Ok(());
        };
        if self.page_cleared(index) {
            return Ok(());
        }
        let number = index + 1;
        self.page = number;
        if let Some(content) = page.page_stream() {
            content_scan::scan(content)
                .map_err(|refusal| refusal_error(&Subject::Page(number), refusal))?;
        }
        let mut work = Vec::new();
        // hayro 0.8 resolves a page's names only in its own `/Resources`, or
        // the nearest one it inherits from the page tree, and never falls back
        // to an ancestor's dictionary. A form or Type 3 glyph procedure with
        // its own `/Resources` (or, for a glyph, its font's) uses only that
        // dictionary, and one without uses the resources it is drawn with; a
        // pattern uses only its own. The walk visits every one of these
        // dictionaries below.
        self.visit_resources(fresh, page.resources(), &mut work)?;
        if let Some(annotations) = fresh.array(page.raw(), ANNOTS) {
            // Iterate the way hayro does, which also ends at the first element
            // that is not a dictionary. `Array::raw_iter` unwraps the read of
            // every element that skipping accepted, and `[+ ]` is accepted.
            // hayro resolves every element itself when it draws the page, so
            // the list costs about as much to read here as to interpret. Both
            // pay for members of object streams, whose index hayro-syntax
            // rebuilds on every lookup: a page with thousands of annotations in
            // one object stream is quadratic twice.
            for annotation in annotations.iter::<Dict<'a>>() {
                self.visit_appearance(fresh, &annotation, &mut work)?;
            }
        }
        while let Some(item) = work.pop() {
            match item {
                Work::Content(stream) => self.visit_content(fresh, &stream, number, &mut work)?,
                Work::Resources(dictionary) => {
                    self.visit_resource_dictionary(fresh, &dictionary, &mut work)?
                }
                Work::Font(font) => self.visit_font(fresh, &font, &mut work)?,
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
        fresh: Fresh<'a>,
        resources: &Resources<'a>,
        work: &mut Vec<Work<'a>>,
    ) -> PyResult<()> {
        self.visit_categories(
            fresh,
            &Categories {
                x_objects: resources.x_objects.clone(),
                patterns: resources.patterns.clone(),
                fonts: resources.fonts.clone(),
                ext_g_states: resources.ext_g_states.clone(),
                color_spaces: resources.color_spaces.clone(),
                shadings: resources.shadings.clone(),
            },
            work,
        )
    }

    /// Queue what a stream's or font's `/Resources` dictionary names.
    fn visit_resource_dictionary<'a>(
        &mut self,
        fresh: Fresh<'a>,
        resources: &Dict<'a>,
        work: &mut Vec<Work<'a>>,
    ) -> PyResult<()> {
        if !self.first_visit(Role::Resources, resources)? {
            return Ok(());
        }
        // A category that an earlier dictionary named by reference is empty
        // here, and an empty dictionary is never visited.
        let mut category = |role: Role, key: &[u8]| -> PyResult<Dict<'a>> {
            Ok(self
                .follow_dictionary(fresh, role, resources, key)?
                .unwrap_or_default())
        };
        let categories = Categories {
            x_objects: category(Role::XObjects, XOBJECT)?,
            patterns: category(Role::Patterns, PATTERN)?,
            fonts: category(Role::Fonts, FONT)?,
            ext_g_states: category(Role::GraphicsStates, EXT_G_STATE)?,
            color_spaces: category(Role::ColorSpaces, COLORSPACE)?,
            shadings: category(Role::Shadings, SHADING)?,
        };
        self.visit_categories(fresh, &categories, work)
    }

    fn visit_categories<'a>(
        &mut self,
        fresh: Fresh<'a>,
        categories: &Categories<'a>,
        work: &mut Vec<Work<'a>>,
    ) -> PyResult<()> {
        let Categories {
            x_objects,
            patterns,
            fonts,
            ext_g_states,
            color_spaces,
            shadings,
        } = categories;
        if self.first_visit(Role::XObjects, x_objects)? {
            for key in x_objects.keys() {
                let Some(stream) =
                    self.follow_stream(fresh, Role::XObject, x_objects, key.as_ref())?
                else {
                    continue;
                };
                if self.is_form(&stream)? {
                    push(work, Work::Content(stream))?;
                } else {
                    self.visit_image(fresh, &stream)?;
                }
            }
        }
        if self.first_visit(Role::Patterns, patterns)? {
            for key in patterns.keys() {
                match self.follow_object(fresh, Role::Pattern, patterns, key.as_ref())? {
                    Some(Object::Stream(stream)) => push(work, Work::Content(stream))?,
                    Some(Object::Dict(pattern)) => {
                        self.visit_shading_entry(fresh, &pattern, SHADING)?
                    }
                    _ => {}
                }
            }
        }
        if self.first_visit(Role::Fonts, fonts)? {
            for key in fonts.keys() {
                if let Some(font) =
                    self.follow_dictionary(fresh, Role::Font, fonts, key.as_ref())?
                {
                    push(work, Work::Font(font))?;
                }
            }
        }
        if self.first_visit(Role::GraphicsStates, ext_g_states)? {
            for key in ext_g_states.keys() {
                if let Some(state) =
                    self.follow_dictionary(fresh, Role::GraphicsState, ext_g_states, key.as_ref())?
                {
                    self.visit_graphics_state(fresh, &state, work)?;
                }
            }
        }
        if self.first_visit(Role::ColorSpaces, color_spaces)? {
            for key in color_spaces.keys() {
                self.graph_entry(fresh, color_spaces, key.as_ref())?;
            }
        }
        if self.first_visit(Role::Shadings, shadings)? {
            for key in shadings.keys() {
                self.visit_shading_entry(fresh, shadings, key.as_ref())?;
            }
        }
        Ok(())
    }

    /// Measure the shading that `owner[key]` holds: the entry of a `/Shading`
    /// category dictionary, or the `/Shading` of a shading pattern.
    fn visit_shading_entry<'a>(
        &mut self,
        fresh: Fresh<'a>,
        owner: &Dict<'a>,
        key: &[u8],
    ) -> PyResult<()> {
        let Some(shading) = self.follow_object(fresh, Role::Shading, owner, key)? else {
            return Ok(());
        };
        // A mesh shading's stream data is never read here.
        let Some((dictionary, _)) = dict_or_stream(&shading) else {
            return Ok(());
        };
        self.graph_entry(fresh, dictionary, COLORSPACE)?;
        self.graph_functions(fresh, dictionary, FUNCTION)
    }

    /// Measure the colour space of an image XObject without decoding its data.
    fn visit_image<'a>(&mut self, fresh: Fresh<'a>, image: &Stream<'a>) -> PyResult<()> {
        let dictionary = image.dict();
        self.graph_entry(fresh, dictionary, CS)?;
        self.graph_entry(fresh, dictionary, COLORSPACE)
    }

    /// Measure the transfer functions of a graphics state and the colour space
    /// of its soft-mask group, and queue the group and the font it selects.
    fn visit_graphics_state<'a>(
        &mut self,
        fresh: Fresh<'a>,
        state: &Dict<'a>,
        work: &mut Vec<Work<'a>>,
    ) -> PyResult<()> {
        self.graph_functions(fresh, state, TR)?;
        self.graph_functions(fresh, state, TR2)?;
        // Graphics states that name one soft mask, or masks that name one group
        // or one set of group attributes, share what it names.
        if let Some(mask) = self.follow_dictionary(fresh, Role::SoftMask, state, SMASK)? {
            self.graph_functions(fresh, &mask, TR)?;
            if let Some(group) = self.follow_stream(fresh, Role::SoftMaskGroup, &mask, G)? {
                if let Some(attributes) =
                    self.follow_dictionary(fresh, Role::GroupAttributes, group.dict(), GROUP)?
                {
                    self.graph_entry(fresh, &attributes, CS)?;
                }
                push(work, Work::Content(group))?;
            }
        }
        if let Some(selection) = self.follow_array(fresh, Role::FontSelection, state, FONT)? {
            self.visit_font_selection(fresh, &selection, work)?;
        }
        Ok(())
    }

    /// Queue the font that a graphics state's `/Font` array selects.
    ///
    /// hayro reads the first element as an object and keeps it only if it is a
    /// dictionary; a stream is no font to it. The element is classified by its
    /// reference before it is read, because graphics states that select one
    /// font share the object it names.
    fn visit_font_selection<'a>(
        &mut self,
        fresh: Fresh<'a>,
        selection: &Array<'a>,
        work: &mut Vec<Work<'a>>,
    ) -> PyResult<()> {
        let font = match Elements::new(selection).next() {
            Some(MaybeRef::NotRef(Object::Dict(font))) => Some(font),
            Some(MaybeRef::Ref(reference)) => {
                let id = ObjectIdentifier::from(reference);
                if self.first_follow_id(Role::SelectedFont, id)? {
                    match fresh.0.get::<Object<'a>>(id) {
                        Some(Object::Dict(font)) => Some(font),
                        _ => None,
                    }
                } else {
                    None
                }
            }
            _ => None,
        };
        if let Some(font) = font {
            push(work, Work::Font(font))?;
        }
        Ok(())
    }

    /// Measure the colour space or function that `dictionary[key]` holds.
    fn graph_entry<'a>(
        &mut self,
        fresh: Fresh<'a>,
        dictionary: &Dict<'a>,
        key: &[u8],
    ) -> PyResult<()> {
        let page = self.page;
        self.graph
            .check_entry(fresh.0, dictionary, key)
            .map_err(|refusal| graph_error(page, refusal))
    }

    /// Measure the function, or each function of an array, that
    /// `dictionary[key]` holds.
    fn graph_functions<'a>(
        &mut self,
        fresh: Fresh<'a>,
        dictionary: &Dict<'a>,
        key: &[u8],
    ) -> PyResult<()> {
        let page = self.page;
        self.graph
            .check_functions(fresh.0, dictionary, key)
            .map_err(|refusal| graph_error(page, refusal))
    }

    /// Queue an annotation's or widget's normal appearance stream.
    ///
    /// hayro reads the appearance of every annotation it draws, so this costs
    /// about as much as interpreting the page does, plus the annotations it
    /// skips as hidden, which this walk reads as well.
    fn visit_appearance<'a>(
        &mut self,
        fresh: Fresh<'a>,
        annotation: &Dict<'a>,
        work: &mut Vec<Work<'a>>,
    ) -> PyResult<()> {
        let Some(appearances) = self.follow_dictionary(fresh, Role::Appearances, annotation, AP)?
        else {
            return Ok(());
        };
        if let Some(stream) = self.follow_stream(fresh, Role::ContentStream, &appearances, N)? {
            push(work, Work::Content(stream))?;
        }
        Ok(())
    }

    /// Scan one stream's decoded content and queue its resources.
    fn visit_content<'a>(
        &mut self,
        fresh: Fresh<'a>,
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
        if let Some(resources) =
            self.follow_dictionary(fresh, Role::Resources, stream.dict(), RESOURCES)?
        {
            push(work, Work::Resources(resources))?;
        }
        Ok(())
    }

    /// Queue a Type 3 font's glyph procedures and its resources.
    ///
    /// A font is the same font when its dictionary bytes are. `Dict::obj_id`
    /// would name the object that contains a direct font, which several fonts
    /// in one `/Font` object share.
    fn visit_font<'a>(
        &mut self,
        fresh: Fresh<'a>,
        font: &Dict<'a>,
        work: &mut Vec<Work<'a>>,
    ) -> PyResult<()> {
        if !self.first_visit(Role::Font, font)? {
            return Ok(());
        }
        if let Some(procedures) =
            self.follow_dictionary(fresh, Role::GlyphProcedures, font, CHAR_PROCS)?
        {
            for key in procedures.keys() {
                if let Some(stream) =
                    self.follow_stream(fresh, Role::ContentStream, &procedures, key.as_ref())?
                {
                    push(work, Work::Content(stream))?;
                }
            }
        }
        if let Some(resources) = self.follow_dictionary(fresh, Role::Resources, font, RESOURCES)? {
            push(work, Work::Resources(resources))?;
        }
        Ok(())
    }

    /// Record a dictionary in one role; false when it was visited in that role
    /// or has no entries.
    fn first_visit(&mut self, role: Role, dictionary: &Dict<'_>) -> PyResult<bool> {
        if dictionary.is_empty() {
            return Ok(false);
        }
        let bytes = dictionary.data();
        insert(
            &mut self.dictionaries,
            (role, bytes.as_ptr() as usize, bytes.len()),
        )
    }

    /// Record the reference that `owner[key]` holds as followed for one role;
    /// false when it was followed for that role before. A direct value has no
    /// reference to record: its bytes belong to `owner`, which the walk reads
    /// once, so it is always read.
    fn first_follow(&mut self, role: Role, owner: &Dict<'_>, key: &[u8]) -> PyResult<bool> {
        match owner.get_ref(key) {
            Some(reference) => self.first_follow_id(role, reference.into()),
            None => Ok(true),
        }
    }

    /// Record a reference as followed for one role; false when it was before.
    fn first_follow_id(&mut self, role: Role, id: ObjectIdentifier) -> PyResult<bool> {
        insert(&mut self.followed, (role, id))
    }

    /// The dictionary under `owner[key]`, unless a reference that names it was
    /// followed for `role` before. A stream object yields its dictionary.
    fn follow_dictionary<'a>(
        &mut self,
        fresh: Fresh<'a>,
        role: Role,
        owner: &Dict<'a>,
        key: &[u8],
    ) -> PyResult<Option<Dict<'a>>> {
        Ok(self
            .first_follow(role, owner, key)?
            .then(|| fresh.dictionary(owner, key))
            .flatten())
    }

    /// The stream under `owner[key]`, unless a reference that names it was
    /// followed for `role` before.
    fn follow_stream<'a>(
        &mut self,
        fresh: Fresh<'a>,
        role: Role,
        owner: &Dict<'a>,
        key: &[u8],
    ) -> PyResult<Option<Stream<'a>>> {
        Ok(self
            .first_follow(role, owner, key)?
            .then(|| fresh.stream(owner, key))
            .flatten())
    }

    /// The object under `owner[key]`, unless a reference that names it was
    /// followed for `role` before.
    fn follow_object<'a>(
        &mut self,
        fresh: Fresh<'a>,
        role: Role,
        owner: &Dict<'a>,
        key: &[u8],
    ) -> PyResult<Option<Object<'a>>> {
        Ok(self
            .first_follow(role, owner, key)?
            .then(|| fresh.object(owner, key))
            .flatten())
    }

    /// The array under `owner[key]`, unless a reference that names it was
    /// followed for `role` before.
    fn follow_array<'a>(
        &mut self,
        fresh: Fresh<'a>,
        role: Role,
        owner: &Dict<'a>,
        key: &[u8],
    ) -> PyResult<Option<Array<'a>>> {
        Ok(self
            .first_follow(role, owner, key)?
            .then(|| fresh.array(owner, key))
            .flatten())
    }

    /// Whether hayro draws the stream as a Form XObject.
    ///
    /// A `/Subtype` behind a reference is read for the first stream that names
    /// it only. Parsing the object costs as much as its size, and the walk
    /// lists every `/XObject` entry whether or not content draws it, so
    /// thousands of streams that name one large object would read it once
    /// each. A direct value belongs to the stream's own dictionary, which was
    /// parsed already.
    fn is_form(&mut self, stream: &Stream<'_>) -> PyResult<bool> {
        let Some(reference) = stream.dict().get_ref(SUBTYPE) else {
            return Ok(has_form_subtype(stream));
        };
        let id = ObjectIdentifier::from(reference);
        if let Some(&form) = self.form_subtypes.get(&id) {
            return Ok(form);
        }
        let form = has_form_subtype(stream);
        insert_value(&mut self.form_subtypes, id, form)?;
        Ok(form)
    }
}

/// Whether the stream's `/Subtype` reads as the name `/Form`.
fn has_form_subtype(stream: &Stream<'_>) -> bool {
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

fn graph_error(page: usize, refusal: graph_guard::Refusal) -> PyErr {
    match refusal {
        graph_guard::Refusal::Chain(root) => limit_err(
            "resource_nesting_depth",
            format!(
                "the chain of colour spaces and functions that starts at {} reachable from page {page} is deeper than the {MAX_CHAIN_DEPTH}-level limit",
                root.describe()
            ),
        ),
        graph_guard::Refusal::Program(function) => limit_err(
            "resource_nesting_depth",
            format!(
                "Type 4 function {} reachable from page {page} nests procedures deeper than the {MAX_BRACE_DEPTH}-level limit",
                function.describe()
            ),
        ),
        graph_guard::Refusal::Allocation(error) => allocation_error(error),
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

/// Insert after reserving fallibly.
fn insert_value<K: Eq + Hash, V>(map: &mut HashMap<K, V>, key: K, value: V) -> PyResult<()> {
    map.try_reserve(1).map_err(allocation_error)?;
    map.insert(key, value);
    Ok(())
}

fn push<'a>(work: &mut Vec<Work<'a>>, item: Work<'a>) -> PyResult<()> {
    work.try_reserve(1).map_err(allocation_error)?;
    work.push(item);
    Ok(())
}
