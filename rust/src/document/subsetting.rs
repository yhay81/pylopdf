//! Subsetting the embedded fonts of an existing document.
//!
//! A font program is subset only when every glyph that any content can draw
//! with it is known. The walk follows the text state through every content
//! stream that can show text: page content, annotation appearances, Form
//! XObjects with the font and resources they inherit, tiling patterns, Type 3
//! glyph procedures, and soft-mask groups. It records, per program, the
//! glyphs that strings shown in one of its fonts select. A program qualifies
//! when every font that uses it is a `CIDFontType2` under a `Type0` font with
//! the `Identity-H` or `Identity-V` encoding, so that codes are CIDs and a
//! new `CIDToGIDMap` can point them at the subset's glyph IDs without any
//! change to content. Anything that keeps a glyph set from being known (a
//! stream that does not decode or parse, a font in the interactive form's
//! default resources, which a form fill could draw any text with) leaves the
//! programs it could involve as they are.

use super::*;

use std::collections::BTreeSet;
use std::rc::Rc;

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

/// Classify a font dictionary.
fn classify(doc: &Document, font: &Dictionary) -> Current {
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
            fonts.push(classify(doc, font));
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
    let font = resolve(doc, resolve(doc, entry)?.as_array().ok()?.first()?)?
        .as_dict()
        .ok()?;
    Some(classify(doc, font))
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
            if let Current::Candidate(candidate) = font {
                self.unknown.insert(candidate.program);
            }
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
                    let selected = operands
                        .first()
                        .and_then(|value| value.as_name().ok())
                        .and_then(|name| resource(doc, resources, b"Font", name))
                        .and_then(|value| resolve(doc, value))
                        .and_then(|value| value.as_dict().ok());
                    font = selected.map_or(Current::None, |dict| classify(doc, dict));
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
                    if let (Current::Candidate(candidate), Some(Object::String(bytes, _))) =
                        (&font, operands.last())
                    {
                        self.show(doc, candidate, bytes)?;
                    }
                }
                "TJ" => {
                    if let (Current::Candidate(candidate), Some(Object::Array(items))) =
                        (&font, operands.last())
                    {
                        for item in items {
                            if let Object::String(bytes, _) = item {
                                self.show(doc, candidate, bytes)?;
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
            && let Current::Candidate(_) = classify(doc, dict)
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
                && !(top && descendants.contains(&id) && program_of(doc, dict).is_some())
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
        if let Current::Candidate(candidate) = font {
            excluded.insert(candidate.program);
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

/// Plan the subset of one program, or `None` when it cannot shrink.
fn plan(
    doc: &Document,
    program: ObjectId,
    fonts: &[(ObjectId, CidMap)],
    glyphs: &BTreeSet<u16>,
) -> PyResult<Option<Plan>> {
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
    let mut remapper = GlyphRemapper::new();
    for &glyph in glyphs {
        remapper.remap(glyph);
    }
    let Ok(subset) = subsetter::subset(&data, 0, &remapper) else {
        return Ok(None);
    };
    if subset.len() >= data.len() {
        return Ok(None);
    }
    let mut maps = Vec::new();
    for (font, map) in fonts {
        let (target, old) = match map {
            CidMap::Identity => {
                let highest = glyphs.last().copied().unwrap_or(0);
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
    let walk = collect_usage(this)?;
    let doc: &Document = &this.doc;
    let excluded = excluded_programs(doc);
    // Every CIDFontType2 object over each program, with its map.
    let mut cid_fonts: BTreeMap<ObjectId, Vec<(ObjectId, CidMap)>> = BTreeMap::new();
    for (&id, object) in &doc.objects {
        if let Object::Dictionary(dict) = object
            && let Some(candidate) = program_of(doc, dict)
        {
            cid_fonts
                .entry(candidate.program)
                .or_default()
                .push((id, candidate.map));
        }
    }
    let considered = cid_fonts.len();
    // A map stream that fonts over different programs share cannot take
    // one program's new glyph IDs.
    let mut map_programs: HashMap<ObjectId, ObjectId> = HashMap::new();
    let mut shared_maps = HashSet::new();
    for (program, fonts) in &cid_fonts {
        for (_, map) in fonts {
            if let CidMap::Stream(id) = map
                && *map_programs.entry(*id).or_insert(*program) != *program
            {
                shared_maps.insert(*id);
            }
        }
    }
    let shares_a_map = |fonts: &[(ObjectId, CidMap)]| {
        fonts
            .iter()
            .any(|(_, map)| matches!(map, CidMap::Stream(id) if shared_maps.contains(id)))
    };
    let plans = py.detach(|| -> PyResult<Vec<Plan>> {
        let mut plans = Vec::new();
        for (program, fonts) in &cid_fonts {
            if excluded.contains(program) || walk.unknown.contains(program) || shares_a_map(fonts) {
                continue;
            }
            let Some(glyphs) = walk.used.get(program) else {
                continue;
            };
            if let Some(plan) = plan(doc, *program, fonts, glyphs)? {
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
