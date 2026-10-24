//! Subsetting the embedded fonts of an existing document.
//!
//! A font program is subset only when every glyph that any content can draw
//! with it is known. The walk follows the text state through every content
//! stream that can show text: page content, annotation appearances, Form
//! XObjects with the font and resources they inherit, tiling patterns, Type 3
//! glyph procedures, and soft-mask groups. It records, per program, the
//! glyphs that strings shown in one of its fonts select. A program qualifies
//! when every font that uses it is either a `CIDFontType2` under a `Type0`
//! font with the `Identity-H` or `Identity-V` encoding, whose codes are CIDs
//! that a new `CIDToGIDMap` points at the subset's glyph IDs, or a simple
//! `TrueType` font, whose codes hayro resolves to glyphs through the
//! program's `cmap`: hayro itself maps the codes that content shows, and the
//! subset gets a `cmap` with every original mapping to a kept glyph moved to
//! its new ID. Content and every font dictionary entry but the map stay. Anything that keeps a glyph set from being known (a
//! stream that does not decode or parse, a font in the interactive form's
//! default resources, which a form fill could draw any text with) leaves the
//! programs it could involve as they are.

use super::*;

use std::collections::BTreeSet;
use std::rc::Rc;

use hayro::hayro_interpret::font::{Glyph, GlyphRun};
use hayro::hayro_interpret::{
    BlendMode, ClipPath, Device, DrawMode, DrawProps, Image, ImageDrawProps, SoftMask,
    interpret_page,
};
use hayro::kurbo::BezPath;
use subsetter::GlyphRemapper;

use crate::content::ContentError;
use crate::content::syntax::Instructions;

/// Content streams visited, counting a Form XObject again for every font and
/// resources it inherits, before the operation is refused.
const MAX_STREAM_VISITS: usize = 1 << 16;
/// Operations parsed in all, before the operation is refused.
const MAX_OPERATIONS: usize = 1 << 24;
/// The decoded size of one font program that subsetting reads.
const MAX_PROGRAM_SIZE: usize = 64 * 1024 * 1024;
/// The decoded size of a `CIDToGIDMap`: two bytes for each of 65,536 CIDs.
const MAX_MAP_SIZE: usize = 2 * 65_536;

/// How a `CIDFontType2` maps CIDs to glyph IDs.
#[derive(Clone, PartialEq, Eq, Hash, Debug)]
enum CidMap {
    Identity,
    /// The `/CIDToGIDMap` stream, by object ID.
    Stream(ObjectId),
}

/// A font that subsetting can handle: an Identity-encoded `Type0` font whose
/// descendant is a `CIDFontType2` with an embedded `FontFile2` program.
#[derive(Clone, PartialEq, Eq, Hash, Debug)]
struct Candidate {
    program: ObjectId,
    map: CidMap,
}

/// The font that the text state selects.
#[derive(Clone, PartialEq, Eq, Hash, Debug)]
enum Current {
    None,
    /// A font that subsetting does not change.
    Other,
    Candidate(Candidate),
    /// A simple `TrueType` font object and its `FontFile2` program.
    Simple {
        font: ObjectId,
        program: ObjectId,
    },
}

/// Follow up to 32 references.
fn resolve<'a>(doc: &'a Document, mut value: &'a Object) -> Option<&'a Object> {
    for _ in 0..32 {
        match value {
            Object::Reference(id) => value = doc.objects.get(id)?,
            other => return Some(other),
        }
    }
    None
}

/// The ID of the object that a reference chain ends at.
fn target_id<'a>(doc: &'a Document, mut value: &'a Object) -> Option<ObjectId> {
    let mut last = None;
    for _ in 0..32 {
        match value {
            Object::Reference(id) => {
                last = Some(*id);
                value = doc.objects.get(id)?;
            }
            _ => return last,
        }
    }
    None
}

fn name_of<'a>(doc: &'a Document, dict: &'a Dictionary, key: &[u8]) -> Option<&'a [u8]> {
    resolve(doc, dict.get(key).ok()?)?.as_name().ok()
}

fn dict_of<'a>(doc: &'a Document, dict: &'a Dictionary, key: &[u8]) -> Option<&'a Dictionary> {
    resolve(doc, dict.get(key).ok()?)?.as_dict().ok()
}

/// The `FontFile2` program of a simple `TrueType` font.
fn simple_program(doc: &Document, font: &Dictionary) -> Option<ObjectId> {
    if name_of(doc, font, b"Subtype")? != b"TrueType" {
        return None;
    }
    dict_of(doc, font, b"FontDescriptor")?
        .get(b"FontFile2")
        .ok()?
        .as_reference()
        .ok()
}

/// Classify a font dictionary, which object `id` holds when it is indirect.
fn classify(doc: &Document, font: &Dictionary, id: Option<ObjectId>) -> Current {
    if let Some(program) = simple_program(doc, font) {
        // A font held directly in a resource dictionary has no identity to
        // key its codes by; its program stays whole.
        return match id {
            Some(font) => Current::Simple { font, program },
            None => Current::Other,
        };
    }
    if name_of(doc, font, b"Subtype") != Some(b"Type0") {
        return Current::Other;
    }
    if !matches!(
        name_of(doc, font, b"Encoding"),
        Some(b"Identity-H" | b"Identity-V")
    ) {
        return Current::Other;
    }
    let descendant = font
        .get(b"DescendantFonts")
        .ok()
        .and_then(|value| resolve(doc, value))
        .and_then(|value| value.as_array().ok())
        .and_then(|items| items.first())
        .and_then(|value| resolve(doc, value))
        .and_then(|value| value.as_dict().ok());
    match descendant.and_then(|descendant| program_of(doc, descendant)) {
        Some(candidate) => Current::Candidate(candidate),
        None => Current::Other,
    }
}

/// The program and CID map of a `CIDFontType2` with a `FontFile2`.
fn program_of(doc: &Document, cid_font: &Dictionary) -> Option<Candidate> {
    if name_of(doc, cid_font, b"Subtype")? != b"CIDFontType2" {
        return None;
    }
    let program = dict_of(doc, cid_font, b"FontDescriptor")?
        .get(b"FontFile2")
        .ok()?
        .as_reference()
        .ok()?;
    let map = match cid_font.get(b"CIDToGIDMap") {
        Err(_) => CidMap::Identity,
        Ok(Object::Name(name)) if name == b"Identity" => CidMap::Identity,
        Ok(Object::Reference(id)) if matches!(doc.objects.get(id), Some(Object::Stream(_))) => {
            CidMap::Stream(*id)
        }
        Ok(_) => return None,
    };
    Some(Candidate { program, map })
}

/// A dictionary entry of `resources`, by category and name, unresolved.
fn resource<'a>(
    doc: &'a Document,
    resources: Option<&'a Dictionary>,
    category: &[u8],
    name: &[u8],
) -> Option<&'a Object> {
    dict_of(doc, resources?, category)?.get(name).ok()
}

/// Every font that `resources` can select, by `Tf` or by a graphics state.
fn fonts_of(doc: &Document, resources: Option<&Dictionary>) -> Vec<Current> {
    let mut fonts = Vec::new();
    let Some(resources) = resources else {
        return fonts;
    };
    for (_, value) in dict_of(doc, resources, b"Font")
        .into_iter()
        .flat_map(Dictionary::iter)
    {
        if let Some(font) = resolve(doc, value).and_then(|value| value.as_dict().ok()) {
            fonts.push(classify(doc, font, target_id(doc, value)));
        }
    }
    for (_, value) in dict_of(doc, resources, b"ExtGState")
        .into_iter()
        .flat_map(Dictionary::iter)
    {
        if let Some(font) = graphics_state_font(doc, resolve(doc, value)) {
            fonts.push(font);
        }
    }
    fonts
}

/// The font that a graphics state's `/Font` entry selects.
fn graphics_state_font(doc: &Document, state: Option<&Object>) -> Option<Current> {
    let entry = state?.as_dict().ok()?.get(b"Font").ok()?;
    let first = resolve(doc, entry)?.as_array().ok()?.first()?;
    let font = resolve(doc, first)?.as_dict().ok()?;
    Some(classify(doc, font, target_id(doc, first)))
}

/// Read a `CIDToGIDMap` stream as glyph IDs indexed by CID.
fn read_map(doc: &Document, id: ObjectId) -> PyResult<Vec<u16>> {
    let Some(Object::Stream(stream)) = doc.objects.get(&id) else {
        return Ok(Vec::new());
    };
    let data = decoded_stream_content(
        stream,
        Some(MAX_MAP_SIZE),
        "font_program_size",
        "CIDToGIDMap stream",
    )?;
    Ok(data
        .as_chunks::<2>()
        .0
        .iter()
        .map(|pair| u16::from_be_bytes([pair[0], pair[1]]))
        .collect())
}

/// A content stream to walk, with the state it starts from.
struct Visit {
    data: Vec<u8>,
    resources: Option<Rc<Dictionary>>,
    font: Current,
}

/// What the walk learned about every program.
#[derive(Default)]
struct Walk {
    /// Glyph IDs that some content can draw, per program.
    used: HashMap<ObjectId, BTreeSet<u16>>,
    /// Programs whose glyph set the walk could not know.
    unknown: HashSet<ObjectId>,
    /// Codes that content shows in each simple font, with its program.
    simple: HashMap<ObjectId, (ObjectId, BTreeSet<u8>)>,
    /// CIDToGIDMap streams already read.
    maps: HashMap<ObjectId, Vec<u16>>,
    queue: Vec<Visit>,
    /// Streams queued already, with the font and the inherited resources.
    seen: HashSet<(ObjectId, Current, usize)>,
    visits: usize,
    operations: usize,
}

impl Walk {
    fn spoil(&mut self, fonts: impl IntoIterator<Item = Current>) {
        for font in fonts {
            match font {
                Current::Candidate(candidate) => {
                    self.unknown.insert(candidate.program);
                }
                Current::Simple { program, .. } => {
                    self.unknown.insert(program);
                }
                Current::None | Current::Other => {}
            }
        }
    }

    /// Record the codes that a string shows in the current font.
    fn show_in(&mut self, doc: &Document, font: &Current, string: &[u8]) -> PyResult<()> {
        match font {
            Current::Candidate(candidate) => self.show(doc, candidate, string),
            Current::Simple { font, program } => {
                let (_, codes) = self
                    .simple
                    .entry(*font)
                    .or_insert_with(|| (*program, BTreeSet::new()));
                codes.extend(string.iter().copied());
                Ok(())
            }
            Current::None | Current::Other => Ok(()),
        }
    }

    /// Record the glyphs that a string's codes select through a candidate.
    fn show(&mut self, doc: &Document, candidate: &Candidate, string: &[u8]) -> PyResult<()> {
        if let CidMap::Stream(id) = candidate.map
            && !self.maps.contains_key(&id)
        {
            let map = read_map(doc, id)?;
            self.maps.insert(id, map);
        }
        let glyphs = self.used.entry(candidate.program).or_default();
        for code in string.as_chunks::<2>().0 {
            let cid = u16::from_be_bytes([code[0], code[1]]);
            let gid = match candidate.map {
                CidMap::Identity => Some(cid),
                CidMap::Stream(id) => self.maps[&id].get(usize::from(cid)).copied(),
            };
            glyphs.extend(gid);
        }
        Ok(())
    }

    /// Queue the stream that `value` names, drawn with its own resources or,
    /// without them, with `inherited`.
    fn enqueue(
        &mut self,
        doc: &Document,
        value: &Object,
        inherited: Option<&Rc<Dictionary>>,
        font: Current,
    ) -> PyResult<()> {
        let (Some(id), Some(Object::Stream(stream))) = (target_id(doc, value), resolve(doc, value))
        else {
            return Ok(());
        };
        let own = dict_of(doc, &stream.dict, b"Resources");
        let inherited_key = match (own, inherited) {
            (None, Some(dict)) => Rc::as_ptr(dict) as usize,
            _ => 0,
        };
        if !self.seen.insert((id, font.clone(), inherited_key)) {
            return Ok(());
        }
        self.visits += 1;
        if self.visits > MAX_STREAM_VISITS {
            return Err(limit_err(
                "content_operation_count",
                format!("font subsetting visits more than {MAX_STREAM_VISITS} content streams"),
            ));
        }
        let resources = match own {
            Some(dict) => Some(Rc::new(dict.clone())),
            None => inherited.cloned(),
        };
        match decoded_stream_content(
            stream,
            Some(replacement::MAX_PAGE_CONTENT_BYTES),
            "content_stream_size",
            "content stream",
        ) {
            Ok(data) => self.queue.push(Visit {
                data,
                resources,
                font,
            }),
            Err(error) if Python::attach(|py| error.is_instance_of::<LimitError>(py)) => {
                return Err(error);
            }
            Err(_) => {
                let mut fonts = fonts_of(doc, resources.as_deref());
                fonts.push(font);
                self.spoil(fonts);
            }
        }
        Ok(())
    }

    /// Walk one stream with the text state it starts from.
    fn walk(&mut self, doc: &Document, visit: Visit) -> PyResult<()> {
        let resources = visit.resources.as_deref();
        let operations = Instructions::parse(&visit.data, Some(MAX_OPERATIONS - self.operations))
            .and_then(|parsed| {
                self.operations += parsed.len();
                parsed.to_operations()
            });
        let operations = match operations {
            Ok(operations) => operations,
            Err(ContentError::Limit { code, message }) => return Err(limit_err(code, message)),
            Err(ContentError::Allocation(message)) => return Err(PdfError::new_err(message)),
            Err(ContentError::Malformed(_)) => {
                let mut fonts = fonts_of(doc, resources);
                fonts.push(visit.font);
                self.spoil(fonts);
                return Ok(());
            }
        };
        let mut font = visit.font.clone();
        let mut saved: Vec<Current> = Vec::new();
        for operation in &operations {
            let operands = &operation.operands;
            let named = |category: &[u8]| {
                operands
                    .last()
                    .and_then(|value| value.as_name().ok())
                    .and_then(|name| resource(doc, resources, category, name))
            };
            match operation.operator.as_str() {
                "q" => saved.push(font.clone()),
                "Q" => font = saved.pop().unwrap_or(font),
                "Tf" => {
                    let entry = operands
                        .first()
                        .and_then(|value| value.as_name().ok())
                        .and_then(|name| resource(doc, resources, b"Font", name));
                    let selected = entry
                        .and_then(|value| resolve(doc, value))
                        .and_then(|value| value.as_dict().ok());
                    font = selected.map_or(Current::None, |dict| {
                        classify(doc, dict, entry.and_then(|value| target_id(doc, value)))
                    });
                    if let Some(dict) = selected
                        && name_of(doc, dict, b"Subtype") == Some(b"Type3")
                    {
                        self.type3_procedures(doc, dict, visit.resources.as_ref())?;
                    }
                }
                "gs" => {
                    let state = named(b"ExtGState").and_then(|value| resolve(doc, value));
                    if let Some(selected) = graphics_state_font(doc, state) {
                        font = selected;
                    }
                    // A soft mask's group draws with the state in effect.
                    let group = state
                        .and_then(|value| value.as_dict().ok())
                        .and_then(|dict| dict_of(doc, dict, b"SMask"))
                        .and_then(|mask| mask.get(b"G").ok());
                    if let Some(group) = group {
                        self.enqueue(doc, group, visit.resources.as_ref(), font.clone())?;
                    }
                }
                "Tj" | "'" | "\"" => {
                    if let Some(Object::String(bytes, _)) = operands.last() {
                        self.show_in(doc, &font, bytes)?;
                    }
                }
                "TJ" => {
                    if let Some(Object::Array(items)) = operands.last() {
                        for item in items {
                            if let Object::String(bytes, _) = item {
                                self.show_in(doc, &font, bytes)?;
                            }
                        }
                    }
                }
                "Do" => {
                    if let Some(form) = named(b"XObject")
                        && let Some(Object::Stream(stream)) = resolve(doc, form)
                        && name_of(doc, &stream.dict, b"Subtype") == Some(b"Form")
                    {
                        self.enqueue(doc, form, visit.resources.as_ref(), font.clone())?;
                    }
                }
                "scn" | "SCN" => {
                    // A tiling pattern's cell starts from the initial state.
                    if let Some(pattern) = named(b"Pattern") {
                        self.enqueue(doc, pattern, None, Current::None)?;
                    }
                }
                _ => {}
            }
        }
        Ok(())
    }

    /// Queue the glyph procedures of a Type 3 font.
    fn type3_procedures(
        &mut self,
        doc: &Document,
        font: &Dictionary,
        resources: Option<&Rc<Dictionary>>,
    ) -> PyResult<()> {
        let own = dict_of(doc, font, b"Resources").map(|dict| Rc::new(dict.clone()));
        let inherited = own.as_ref().or(resources);
        let procedures: Vec<Object> = dict_of(doc, font, b"CharProcs")
            .into_iter()
            .flat_map(Dictionary::iter)
            .map(|(_, value)| value.clone())
            .collect();
        for procedure in &procedures {
            self.enqueue(doc, procedure, inherited, Current::None)?;
        }
        Ok(())
    }
}

/// Walk every content stream that can show text.
fn collect_usage(this: &_Document) -> PyResult<Walk> {
    let doc: &Document = &this.doc;
    let mut walk = Walk::default();
    for page_id in this.doc.owned_page_ids()? {
        let resources = match this.resolve_page_attr(page_id, b"Resources")? {
            Some(Object::Dictionary(dict)) => Some(Rc::new(dict)),
            _ => None,
        };
        match replacement::page_content(doc, page_id) {
            Ok(data) => walk.queue.push(Visit {
                data,
                resources: resources.clone(),
                font: Current::None,
            }),
            Err(error) if Python::attach(|py| error.is_instance_of::<LimitError>(py)) => {
                return Err(error);
            }
            Err(_) => walk.spoil(fonts_of(doc, resources.as_deref())),
        }
        let page = doc.get_dictionary(page_id).map_err(to_py_err)?;
        let annotations = page
            .get(b"Annots")
            .ok()
            .and_then(|value| resolve(doc, value))
            .and_then(|value| value.as_array().ok());
        for annotation in annotations.into_iter().flatten() {
            let Some(appearances) = resolve(doc, annotation)
                .and_then(|value| value.as_dict().ok())
                .and_then(|dict| dict_of(doc, dict, b"AP"))
            else {
                continue;
            };
            for (_, appearance) in appearances.iter() {
                match resolve(doc, appearance) {
                    Some(Object::Dictionary(states)) => {
                        for (_, state) in states.iter() {
                            walk.enqueue(doc, state, None, Current::None)?;
                        }
                    }
                    Some(Object::Stream(_)) => {
                        walk.enqueue(doc, appearance, None, Current::None)?;
                    }
                    _ => {}
                }
            }
        }
    }
    while let Some(visit) = walk.queue.pop() {
        walk.walk(doc, visit)?;
    }
    Ok(walk)
}

/// Programs that subsetting must leave as they are: those that a font other
/// than an Identity `Type0` over a `CIDFontType2` could use, those that a
/// descriptor or font held directly inside another object uses, and those
/// of the fonts that the interactive form's default resources name.
fn excluded_programs(doc: &Document) -> HashSet<ObjectId> {
    let mut excluded = HashSet::new();
    // The CIDFontType2 objects that some Identity Type0 object names.
    let mut descendants = HashSet::new();
    for object in doc.objects.values() {
        if let Object::Dictionary(dict) = object
            && let Current::Candidate(_) = classify(doc, dict, None)
            && let Some(Object::Array(items)) = dict
                .get(b"DescendantFonts")
                .ok()
                .and_then(|value| resolve(doc, value))
            && let Some(Object::Reference(id)) = items.first()
        {
            descendants.insert(*id);
        }
    }
    for (&id, object) in &doc.objects {
        let mut stack: Vec<(&Object, bool)> = vec![(object, true)];
        while let Some((value, top)) = stack.pop() {
            let dict = match value {
                Object::Dictionary(dict) => dict,
                Object::Stream(stream) => &stream.dict,
                Object::Array(items) => {
                    stack.extend(items.iter().map(|item| (item, false)));
                    continue;
                }
                _ => continue,
            };
            if let Ok(descriptor) = dict.get(b"FontDescriptor")
                && let Some(program) = resolve(doc, descriptor)
                    .and_then(|value| value.as_dict().ok())
                    .and_then(|descriptor| descriptor.get(b"FontFile2").ok())
                    .and_then(|value| value.as_reference().ok())
                && !(top
                    && (descendants.contains(&id) && program_of(doc, dict).is_some()
                        || simple_program(doc, dict).is_some()))
            {
                excluded.insert(program);
            }
            if !top && let Ok(Object::Reference(program)) = dict.get(b"FontFile2") {
                excluded.insert(*program);
            }
            stack.extend(dict.iter().map(|(_, item)| (item, false)));
        }
    }
    // Fonts that a form fill may draw any text with.
    let default_resources = doc
        .catalog()
        .ok()
        .and_then(|catalog| dict_of(doc, catalog, b"AcroForm"))
        .and_then(|form| dict_of(doc, form, b"DR"));
    for font in fonts_of(doc, default_resources) {
        match font {
            Current::Candidate(candidate) => {
                excluded.insert(candidate.program);
            }
            Current::Simple { program, .. } => {
                excluded.insert(program);
            }
            Current::None | Current::Other => {}
        }
    }
    excluded
}

/// Where a new `CIDToGIDMap` goes.
enum MapTarget {
    /// A new stream for a font whose map was `Identity`.
    NewFor(ObjectId),
    /// The existing map stream.
    Replace(ObjectId),
}

/// A planned replacement of one program and the maps that point into it.
struct Plan {
    program: ObjectId,
    data: Vec<u8>,
    before: usize,
    maps: Vec<(MapTarget, Vec<u8>)>,
}

/// The fonts over one program.
#[derive(Default)]
struct Users {
    cid_fonts: Vec<(ObjectId, CidMap)>,
    simple_fonts: Vec<ObjectId>,
}

/// Records the glyph of every outline glyph that hayro draws, in order.
#[derive(Default)]
struct GlyphRecorder {
    glyphs: Vec<u16>,
}

impl<'a> Device<'a> for GlyphRecorder {
    fn draw_path(&mut self, _: &BezPath, _: DrawProps<'a>, _: &DrawMode) {}
    fn push_clip_path(&mut self, _: &ClipPath) {}
    fn push_transparency_group(&mut self, _: f32, _: Option<SoftMask<'a>>, _: BlendMode) {}
    fn draw_glyph_run(&mut self, run: &GlyphRun<'_, 'a>, _: DrawProps<'a>, _: &DrawMode) {
        for glyph in run.glyphs() {
            if let Glyph::Outline(outline) = &**glyph {
                self.glyphs
                    .push(u16::try_from(outline.glyph_id().to_u32()).unwrap_or(0));
            }
        }
    }
    fn draw_image(&mut self, _: Image<'a, '_>, _: ImageDrawProps<'a>) {}
    fn pop_clip(&mut self) {}
    fn pop_transparency_group(&mut self) {}
}

/// The glyph that hayro draws for each of `codes` in the simple font
/// `font`, or `None` when it cannot tell.
///
/// The font, with the objects it reaches, goes into a one-page document
/// that shows each code with its own `Tj`, so hayro's own encoding and
/// `cmap` rules decide; hayro's interpreter reports glyph IDs but not the
/// fonts' code mapping.
fn hayro_glyphs(doc: &Document, font: ObjectId, codes: &BTreeSet<u8>) -> Option<Vec<u16>> {
    let reachable =
        crate::prune::reachable_object_ids(doc, [&Object::Reference(font)], |_| true).ok()?;
    let mut copy = Document::with_version("1.7");
    for id in &reachable {
        copy.objects.insert(*id, doc.objects.get(id)?.clone());
    }
    copy.max_id = doc.max_id;
    let mut content = b"BT /F 10 Tf ".to_vec();
    for code in codes {
        content.extend_from_slice(format!("<{code:02X}> Tj ").as_bytes());
    }
    content.extend_from_slice(b"ET");
    let content = copy.add_object(Stream::new(Dictionary::new(), content));
    let pages = copy.new_object_id();
    let page = copy.add_object(dictionary! {
        "Type" => "Page",
        "Parent" => pages,
        "MediaBox" => vec![0.into(), 0.into(), 100.into(), 100.into()],
        "Contents" => content,
        "Resources" => dictionary! { "Font" => dictionary! { "F" => font } },
    });
    copy.objects.insert(
        pages,
        Object::Dictionary(dictionary! {
            "Type" => "Pages",
            "Kids" => vec![page.into()],
            "Count" => 1,
        }),
    );
    let catalog = copy.add_object(dictionary! { "Type" => "Catalog", "Pages" => pages });
    copy.trailer.set("Root", catalog);
    let mut bytes = Vec::new();
    copy.save_to(&mut bytes).ok()?;
    let pdf = Pdf::new(bytes).ok()?;
    let page = pdf.pages().first()?;
    let cache = InterpreterCache::new();
    let mut context =
        crate::extract::extraction_context(&pdf, page, &cache, InterpreterSettings::default());
    let mut recorder = GlyphRecorder::default();
    catch_unwind(AssertUnwindSafe(|| {
        interpret_page(page, &mut context, &mut recorder);
    }))
    .ok()?;
    (recorder.glyphs.len() == codes.len()).then_some(recorder.glyphs)
}

/// Glyphs that the program's own `cmap` gives `codes` directly, as readers
/// of symbolic fonts look them up (the code, or the code at `0xF000`,
/// `0xF100`, or `0xF200`), so that other readers' choices survive too.
fn cmap_glyphs(data: &[u8], codes: &BTreeSet<u8>) -> Vec<u16> {
    use write_fonts::read::{FontRef, TableProvider};

    let mut glyphs = Vec::new();
    let Ok(cmap) = FontRef::new(data).and_then(|font| font.cmap()) else {
        return glyphs;
    };
    for record in cmap.encoding_records() {
        let Ok(subtable) = record.subtable(cmap.offset_data()) else {
            continue;
        };
        for &code in codes {
            for offset in [0u32, 0xF000, 0xF100, 0xF200] {
                if let Some(glyph) = subtable.map_codepoint(u32::from(code) + offset) {
                    glyphs.extend(u16::try_from(glyph.to_u32()));
                }
            }
        }
    }
    glyphs
}

/// The subset with a `cmap` that keeps every mapping of the original `cmap`
/// to a kept glyph, at its new glyph ID.
fn with_remapped_cmap(original: &[u8], subset: &[u8], remapper: &GlyphRemapper) -> Option<Vec<u8>> {
    use write_fonts::read::{FontRef, TableProvider};
    use write_fonts::tables::cmap::{Cmap, Cmap4, CmapSubtable, EncodingRecord};
    use write_fonts::types::Tag;

    let cmap = FontRef::new(original).ok()?.cmap().ok()?;
    let mut records = Vec::new();
    for record in cmap.encoding_records() {
        let Ok(subtable) = record.subtable(cmap.offset_data()) else {
            continue;
        };
        let mut pairs: Vec<(u16, u16)> = subtable
            .iter()
            .filter_map(|(code, glyph)| {
                let code = u16::try_from(code).ok().filter(|&code| code != 0xFFFF)?;
                let glyph = u16::try_from(glyph.to_u32())
                    .ok()
                    .filter(|&glyph| glyph != 0)?;
                Some((code, remapper.get(glyph)?))
            })
            .collect();
        pairs.sort_unstable();
        pairs.dedup_by_key(|(code, _)| *code);
        if pairs.is_empty() {
            continue;
        }
        // One segment for each code, then the closing 0xFFFF segment.
        let mut end_code: Vec<u16> = pairs.iter().map(|&(code, _)| code).collect();
        let mut start_code = end_code.clone();
        let mut id_delta: Vec<i16> = pairs
            .iter()
            .map(|&(code, glyph)| glyph.wrapping_sub(code).cast_signed())
            .collect();
        end_code.push(0xFFFF);
        start_code.push(0xFFFF);
        id_delta.push(1);
        let segments = end_code.len();
        let format4 = Cmap4::new(
            0,
            end_code,
            start_code,
            id_delta,
            vec![0; segments],
            Vec::new(),
        );
        records.push(EncodingRecord::new(
            record.platform_id(),
            record.encoding_id(),
            CmapSubtable::Format4(format4),
        ));
    }
    let table = write_fonts::dump_table(&Cmap::new(records)).ok()?;
    let subset_font = FontRef::new(subset).ok()?;
    Some(
        write_fonts::FontBuilder::new()
            .add_raw(Tag::new(b"cmap"), table)
            .copy_missing_tables(subset_font)
            .build(),
    )
}

/// What the walk found that planning needs.
struct Usage {
    used: HashMap<ObjectId, BTreeSet<u16>>,
    simple: HashMap<ObjectId, (ObjectId, BTreeSet<u8>)>,
}

/// Plan the subset of one program, or `None` when it cannot shrink.
fn plan(doc: &Document, program: ObjectId, users: &Users, walk: &Usage) -> PyResult<Option<Plan>> {
    let Some(Object::Stream(stream)) = doc.objects.get(&program) else {
        return Ok(None);
    };
    let Ok(data) = decoded_stream_content(
        stream,
        Some(MAX_PROGRAM_SIZE),
        "font_program_size",
        "font program",
    ) else {
        return Ok(None);
    };
    let mut glyphs: BTreeSet<u16> = walk.used.get(&program).cloned().unwrap_or_default();
    for font in &users.simple_fonts {
        let Some((_, codes)) = walk.simple.get(font) else {
            continue;
        };
        let Some(drawn) = hayro_glyphs(doc, *font, codes) else {
            return Ok(None);
        };
        glyphs.extend(drawn);
        glyphs.extend(cmap_glyphs(&data, codes));
    }
    if glyphs.is_empty() {
        return Ok(None);
    }
    let mut remapper = GlyphRemapper::new();
    for &glyph in &glyphs {
        remapper.remap(glyph);
    }
    let Ok(mut subset) = subsetter::subset(&data, 0, &remapper) else {
        return Ok(None);
    };
    if !users.simple_fonts.is_empty() {
        let Some(with_cmap) = with_remapped_cmap(&data, &subset, &remapper) else {
            return Ok(None);
        };
        subset = with_cmap;
    }
    if subset.len() >= data.len() {
        return Ok(None);
    }
    let mut maps = Vec::new();
    for (font, map) in &users.cid_fonts {
        let (target, old) = match map {
            CidMap::Identity => {
                let highest = walk
                    .used
                    .get(&program)
                    .and_then(|glyphs| glyphs.last().copied())
                    .unwrap_or(0);
                (
                    MapTarget::NewFor(*font),
                    (0..=highest).collect::<Vec<u16>>(),
                )
            }
            CidMap::Stream(id) => (MapTarget::Replace(*id), read_map(doc, *id)?),
        };
        let mut table: Vec<u8> = old
            .iter()
            .flat_map(|&gid| remapper.get(gid).unwrap_or(0).to_be_bytes())
            .collect();
        while table.ends_with(&[0, 0]) {
            table.truncate(table.len() - 2);
        }
        maps.push((target, table));
    }
    Ok(Some(Plan {
        program,
        before: data.len(),
        data: subset,
        maps,
    }))
}

/// Implements [`_Document::subset_fonts`].
pub(super) fn subset_fonts(
    this: &mut _Document,
    py: Python<'_>,
) -> PyResult<(usize, usize, usize, usize, usize)> {
    let Walk {
        used,
        unknown,
        simple,
        ..
    } = collect_usage(this)?;
    let walk = Usage { used, simple };
    let doc: &Document = &this.doc;
    let excluded = excluded_programs(doc);
    // Every CIDFontType2 and simple TrueType object over each program.
    let mut programs: BTreeMap<ObjectId, Users> = BTreeMap::new();
    for (&id, object) in &doc.objects {
        let Object::Dictionary(dict) = object else {
            continue;
        };
        if let Some(candidate) = program_of(doc, dict) {
            programs
                .entry(candidate.program)
                .or_default()
                .cid_fonts
                .push((id, candidate.map));
        } else if let Some(program) = simple_program(doc, dict) {
            programs.entry(program).or_default().simple_fonts.push(id);
        }
    }
    // A map stream that fonts over different programs share cannot take
    // one program's new glyph IDs.
    let mut map_programs: HashMap<ObjectId, ObjectId> = HashMap::new();
    let mut shared_maps = HashSet::new();
    for (program, users) in &programs {
        for (_, map) in &users.cid_fonts {
            if let CidMap::Stream(id) = map
                && *map_programs.entry(*id).or_insert(*program) != *program
            {
                shared_maps.insert(*id);
            }
        }
    }
    let shares_a_map = |users: &Users| {
        users
            .cid_fonts
            .iter()
            .any(|(_, map)| matches!(map, CidMap::Stream(id) if shared_maps.contains(id)))
    };
    let considered = programs.len();
    let plans = py.detach(|| -> PyResult<Vec<Plan>> {
        let mut plans = Vec::new();
        for (program, users) in &programs {
            if excluded.contains(program) || unknown.contains(program) || shares_a_map(users) {
                continue;
            }
            if let Some(plan) = plan(doc, *program, users, &walk)? {
                plans.push(plan);
            }
        }
        Ok(plans)
    })?;
    let subset = plans.len();
    let skipped = considered - subset;
    if plans.is_empty() {
        return Ok((considered, 0, skipped, 0, 0));
    }
    let before: usize = plans.iter().map(|plan| plan.before).sum();
    let after: usize = plans.iter().map(|plan| plan.data.len()).sum();
    this.invalidate_hayro_pdf();
    let doc: &mut Document = &mut this.doc;
    for plan in plans {
        let length = i64::try_from(plan.data.len()).unwrap_or(i64::MAX);
        let mut program = Stream::new(dictionary! { "Length1" => length }, plan.data);
        let _ = program.compress();
        if let Some(Object::Stream(stream)) = doc.objects.get_mut(&plan.program) {
            stream.dict.remove(b"DecodeParms");
            stream.dict.remove(b"Filter");
            for (key, value) in program.dict.iter() {
                stream.dict.set(key.clone(), value.clone());
            }
            stream.set_content(program.content);
        }
        for (target, table) in plan.maps {
            let mut map = Stream::new(Dictionary::new(), table);
            let _ = map.compress();
            match target {
                MapTarget::Replace(id) => {
                    doc.objects.insert(id, Object::Stream(map));
                }
                MapTarget::NewFor(font) => {
                    let map_id = doc.add_object(map);
                    if let Some(Object::Dictionary(font)) = doc.objects.get_mut(&font) {
                        font.set("CIDToGIDMap", Object::Reference(map_id));
                    }
                }
            }
        }
    }
    Ok((considered, subset, skipped, before, after))
}
