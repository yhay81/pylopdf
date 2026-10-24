//! Redaction planning: which recorded items an area removes, and the new
//! bytes of every content stream that loses one.
//!
//! The content engine (`crate::content`) records each glyph, path, image,
//! and shading with the container and operations that painted it. Selection
//! decides from boxes in default user space; editing then rewrites only the
//! operations that change and keeps every other operation's bytes. A
//! container is one placement of a stream, so a Form XObject drawn twice
//! gets one copy for each placement that loses something, and every stream
//! on the path from the page to it is copied too, because each must name its
//! child's copy.

use std::collections::{BTreeMap, HashMap, HashSet};

use hayro::kurbo::{Point, Rect};
use lopdf::content::{Content, Operation};
use lopdf::{Dictionary, Document, Object, ObjectId, StringFormat};

use crate::content::syntax::Instructions;
use crate::content::{Container, Interpretation};
use crate::content::{ContentError, try_push};

/// What happens to an image that an area touches.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum ImageMode {
    Keep,
    Remove,
    Pixels,
}

/// Which paths and shadings an area removes.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) enum GraphicsMode {
    Keep,
    /// Those that lie inside one area.
    Covered,
    /// Those that touch an area.
    Touched,
    /// Those that do not overlap the kept rectangle, for clipping a page.
    Outside(Rect),
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct Options {
    pub(crate) text: bool,
    pub(crate) images: ImageMode,
    pub(crate) graphics: GraphicsMode,
}

/// What an image placement becomes.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum ImageAction {
    Keep,
    Remove,
    Blank,
}

/// The items that the areas remove.
#[derive(Debug, Default)]
pub(crate) struct Selection {
    pub(crate) glyphs: Vec<bool>,
    pub(crate) paths: Vec<bool>,
    pub(crate) shadings: Vec<bool>,
    pub(crate) images: Vec<ImageAction>,
    /// Annotations whose appearances paint something the areas remove.
    pub(crate) annotations: HashSet<ObjectId>,
}

impl Selection {
    pub(crate) fn glyph_count(&self) -> usize {
        self.glyphs.iter().filter(|removed| **removed).count()
    }
}

/// The overlap below which two boxes that share an edge, give or take
/// rounding, do not meet.
const MIN_OVERLAP: f64 = 1e-3;

/// Whether two boxes overlap by more than rounding; a box without width or
/// height meets an area that it crosses inside.
pub(crate) fn overlaps(a: Rect, b: Rect) -> bool {
    let axis = |a0: f64, a1: f64, b0: f64, b1: f64| {
        if a1 - a0 <= 0.0 {
            a0 > b0 && a0 < b1
        } else {
            a1.min(b1) - a0.max(b0) > MIN_OVERLAP
        }
    };
    axis(a.x0, a.x1, b.x0, b.x1) && axis(a.y0, a.y1, b.y0, b.y1)
}

fn inside(inner: Rect, outer: Rect) -> bool {
    inner.x0 >= outer.x0 && inner.x1 <= outer.x1 && inner.y0 >= outer.y0 && inner.y1 <= outer.y1
}

/// Where a glyph may be drawn. For a font that hayro cannot load, other
/// readers may read the codes in another way and space them up to an em
/// apart, so such a glyph may lie anywhere up to one em per byte of its
/// string after the place where hayro puts it.
fn glyph_reach(glyph: &crate::content::interpret::Glyph) -> Rect {
    if !glyph.fallback {
        return glyph.bbox();
    }
    let [x0, y0, x1, y1] = glyph.glyph_box;
    let reach = (glyph.source.offset + glyph.source.len) as f64 * 1000.0;
    crate::content::transformed_rect(Rect::new(x0, y0, x1 + reach, y1), glyph.transform)
}

/// Decide what the areas remove. `forced` lists glyph origins in default
/// user space that verification found inside an area although their boxes
/// did not reach it.
pub(crate) fn select(
    interpretation: &Interpretation,
    areas: &[Rect],
    options: Options,
    forced: &[Point],
) -> Selection {
    let touches = |rect: Rect| areas.iter().any(|area| overlaps(rect, *area));
    let mut selection = Selection::default();
    let containers = &interpretation.containers;
    let annotation_of = |container: usize| containers[container].annotation;
    selection.glyphs = interpretation
        .glyphs
        .iter()
        .map(|glyph| {
            options.text
                && (touches(glyph_reach(glyph))
                    || forced.iter().any(|point| {
                        let origin = glyph.origin();
                        (origin.x - point.x).abs() <= 0.01 && (origin.y - point.y).abs() <= 0.01
                    }))
        })
        .collect();
    let graphics = |painted: Rect| match options.graphics {
        GraphicsMode::Keep => false,
        GraphicsMode::Covered => areas.iter().any(|area| inside(painted, *area)),
        GraphicsMode::Touched => touches(painted),
        GraphicsMode::Outside(kept) => !overlaps(painted, kept),
    };
    selection.paths = interpretation
        .paths
        .iter()
        .map(|path| graphics(path.painted))
        .collect();
    selection.shadings = interpretation
        .shadings
        .iter()
        .map(|shading| match shading.clip {
            Some(clip) => graphics(clip),
            // Only the page bounds it, so it covers every area.
            None => options.graphics == GraphicsMode::Touched && !areas.is_empty(),
        })
        .collect();
    selection.images = interpretation
        .images
        .iter()
        .map(|image| {
            let bbox = match image.clip {
                Some(clip) => image.bbox().intersect(clip),
                None => image.bbox(),
            };
            if !touches(bbox) {
                return ImageAction::Keep;
            }
            match options.images {
                ImageMode::Keep => ImageAction::Keep,
                ImageMode::Remove => ImageAction::Remove,
                ImageMode::Pixels => ImageAction::Blank,
            }
        })
        .collect();
    let mut hit = |container: usize| {
        if let Some(annotation) = annotation_of(container) {
            selection.annotations.insert(annotation);
        }
    };
    for (glyph, removed) in interpretation.glyphs.iter().zip(&selection.glyphs) {
        if *removed {
            hit(glyph.source.container);
        }
    }
    for (path, removed) in interpretation.paths.iter().zip(&selection.paths) {
        if *removed {
            hit(path.container);
        }
    }
    for (shading, removed) in interpretation.shadings.iter().zip(&selection.shadings) {
        if *removed {
            hit(shading.container);
        }
    }
    for (image, action) in interpretation.images.iter().zip(&selection.images) {
        if *action != ImageAction::Keep {
            hit(image.container);
        }
    }
    selection
}

/// A change to one operation.
#[derive(Clone, Debug)]
pub(crate) enum OperationEdit {
    Remove,
    Replace(Vec<u8>),
}

/// The changes to one container's operations.
#[derive(Debug, Default)]
pub(crate) struct ContainerEdits {
    pub(crate) operations: BTreeMap<usize, OperationEdit>,
}

/// Write `operations` as content-stream bytes with lopdf's writer.
pub(crate) fn encode(operations: Vec<Operation>) -> Result<Vec<u8>, ContentError> {
    Content { operations }
        .encode()
        .map_err(|error| ContentError::Malformed(format!("cannot write content: {error}")))
}

/// Removed glyphs of one text operation: element index, byte offset and
/// length, and the `TJ` adjustment that keeps the pen where the glyph left
/// it.
type Removal = (usize, usize, usize, f32);

/// Rewrite one text-showing operation without the removed glyphs, as a
/// `TJ` whose adjustments keep every later glyph in place: `Tj` becomes
/// `TJ`, `'` becomes `T*` and `TJ`, and `"` sets `Tw` and `Tc` first.
pub(crate) fn rewrite_text(
    operation: &Operation,
    removals: &[Removal],
) -> Result<Vec<u8>, ContentError> {
    let operands = &operation.operands;
    let elements: Vec<(usize, &Object)> = match operation.operator.as_str() {
        "TJ" => match operands.last() {
            Some(Object::Array(items)) => items.iter().enumerate().collect(),
            _ => return Err(ContentError::Malformed("a TJ without an array".to_owned())),
        },
        _ => match operands.last() {
            Some(string @ Object::String(..)) => vec![(operands.len() - 1, string)],
            _ => return Err(ContentError::Malformed("text without a string".to_owned())),
        },
    };
    let mut array: Vec<Object> = Vec::new();
    let mut pending = 0.0_f32;
    let flush_string = |array: &mut Vec<Object>, bytes: &mut Vec<u8>| -> Result<(), ContentError> {
        if !bytes.is_empty() {
            try_push(
                array,
                Object::String(std::mem::take(bytes), StringFormat::Hexadecimal),
                "text array",
            )?;
        }
        Ok(())
    };
    let flush_number = |array: &mut Vec<Object>, pending: &mut f32| -> Result<(), ContentError> {
        if *pending != 0.0 {
            try_push(array, Object::Real(*pending), "text array")?;
            *pending = 0.0;
        }
        Ok(())
    };
    for (element, object) in elements {
        match object {
            Object::String(bytes, _) => {
                let mut removed: Vec<&Removal> = removals
                    .iter()
                    .filter(|removal| removal.0 == element)
                    .collect();
                removed.sort_by_key(|removal| removal.1);
                let mut kept = Vec::new();
                let mut offset = 0;
                for removal in removed {
                    let (_, start, len, compensation) = *removal;
                    if start < offset || start + len > bytes.len() {
                        return Err(ContentError::Malformed(
                            "a removed glyph lies outside its string".to_owned(),
                        ));
                    }
                    if start > offset {
                        flush_number(&mut array, &mut pending)?;
                        kept.extend_from_slice(&bytes[offset..start]);
                    }
                    flush_string(&mut array, &mut kept)?;
                    pending += compensation;
                    offset = start + len;
                }
                if offset < bytes.len() {
                    flush_number(&mut array, &mut pending)?;
                    kept.extend_from_slice(&bytes[offset..]);
                }
                flush_string(&mut array, &mut kept)?;
            }
            other => {
                if let Some(value) = match other {
                    Object::Integer(value) => Some(*value as f32),
                    Object::Real(value) => Some(*value),
                    _ => None,
                } {
                    pending += value;
                }
            }
        }
    }
    flush_number(&mut array, &mut pending)?;
    let mut out = Vec::new();
    match operation.operator.as_str() {
        "'" => out.push(Operation::new("T*", Vec::new())),
        "\"" => {
            let n = operands.len();
            if n >= 3 {
                out.push(Operation::new("Tw", vec![operands[n - 3].clone()]));
                out.push(Operation::new("Tc", vec![operands[n - 2].clone()]));
            }
            out.push(Operation::new("T*", Vec::new()));
        }
        _ => {}
    }
    if !array.is_empty() {
        out.push(Operation::new("TJ", vec![Object::Array(array)]));
    }
    encode(out)
}

/// Write a container's operations with their edits: unchanged operations
/// keep their bytes, and the tail after the last operation hayro reads is
/// dropped when `drop_tail` is set.
pub(crate) fn rewrite_container(
    instructions: &Instructions<'_>,
    edits: &ContainerEdits,
    drop_tail: bool,
) -> Result<Vec<u8>, ContentError> {
    let mut out: Vec<u8> = Vec::new();
    let append = |out: &mut Vec<u8>, chunk: &[u8]| -> Result<(), ContentError> {
        out.try_reserve(chunk.len() + 1)
            .map_err(|error| ContentError::allocation("redacted content", &error))?;
        if let (Some(last), Some(first)) = (out.last(), chunk.first())
            && is_regular(*last)
            && is_regular(*first)
        {
            out.push(b'\n');
        }
        out.extend_from_slice(chunk);
        Ok(())
    };
    for index in 0..instructions.len() {
        match edits.operations.get(&index) {
            Some(OperationEdit::Remove) => {}
            Some(OperationEdit::Replace(bytes)) => {
                append(&mut out, b"\n")?;
                append(&mut out, bytes)?;
            }
            None => append(&mut out, instructions.bytes(index))?,
        }
    }
    if !drop_tail {
        append(&mut out, instructions.tail())?;
    }
    Ok(out)
}

fn is_regular(byte: u8) -> bool {
    !matches!(
        byte,
        b'\0'
            | b'\t'
            | b'\n'
            | b'\x0c'
            | b'\r'
            | b' '
            | b'('
            | b')'
            | b'<'
            | b'>'
            | b'['
            | b']'
            | b'{'
            | b'}'
            | b'/'
            | b'%'
    )
}

/// Whether bytes hold nothing but white space and comments.
pub(crate) fn is_blank(bytes: &[u8]) -> bool {
    let mut comment = false;
    for &byte in bytes {
        match byte {
            b'\r' | b'\n' => comment = false,
            _ if comment => {}
            b'%' => comment = true,
            b'\0' | b'\t' | b'\x0c' | b' ' => {}
            _ => return false,
        }
    }
    true
}

/// Which containers must be written again: those that lose an operation
/// or a tail, and every container above one of them, which must name its
/// child's copy. Annotation appearances are never written; their
/// annotations go instead.
pub(crate) fn containers_to_copy(
    interpretation: &Interpretation,
    edited: &HashSet<usize>,
) -> Result<Vec<bool>, ContentError> {
    let mut needed = Vec::new();
    needed
        .try_reserve_exact(interpretation.containers.len())
        .map_err(|error| ContentError::allocation("redacted containers", &error))?;
    needed.resize(interpretation.containers.len(), false);
    for &container in edited {
        let mut current = Some(container);
        while let Some(index) = current {
            if needed[index] {
                break;
            }
            needed[index] = true;
            current = interpretation.containers[index]
                .parent
                .map(|(parent, _)| parent);
        }
    }
    for (index, info) in interpretation.containers.iter().enumerate() {
        if info.annotation.is_some() {
            needed[index] = false;
        }
    }
    Ok(needed)
}

/// The stream object of a container's Form, if it is one.
pub(crate) fn form_id(interpretation: &Interpretation, container: usize) -> Option<ObjectId> {
    match interpretation.containers[container].stream {
        Container::Page => None,
        Container::Form(id) => Some(id),
    }
}

/// A resource name that `names` does not hold yet.
pub(crate) fn fresh_name(
    names: &Dictionary,
    taken: &mut HashSet<Vec<u8>>,
    prefix: &str,
) -> Vec<u8> {
    for number in 1_usize.. {
        let name = format!("{prefix}{number}").into_bytes();
        if !names.has(&name) && !taken.contains(&name) {
            taken.insert(name.clone());
            return name;
        }
    }
    unreachable!("the name space is unbounded")
}

/// Remove `/ActualText`, `/Alt`, and `/E` from a marked-content property
/// list, which would otherwise still hold the text that the redaction
/// removes. Returns `None` when the list holds none of them.
pub(crate) fn strip_replacement_text(properties: &Dictionary) -> Option<Dictionary> {
    const KEYS: [&[u8]; 3] = [b"ActualText", b"Alt", b"E"];
    if !KEYS.iter().any(|key| properties.has(key)) {
        return None;
    }
    let mut stripped = properties.clone();
    for key in KEYS {
        stripped.remove(key);
    }
    Some(stripped)
}

/// Resolve a value through references.
pub(crate) fn resolve<'a>(doc: &'a Document, value: &'a Object) -> Option<&'a Object> {
    let mut current = value;
    for _ in 0..32 {
        match current {
            Object::Reference(id) => current = doc.get_object(*id).ok()?,
            other => return Some(other),
        }
    }
    None
}

/// Group removed glyphs by container and operation.
pub(crate) fn text_removals(
    interpretation: &Interpretation,
    selection: &Selection,
) -> Result<HashMap<(usize, usize), Vec<Removal>>, ContentError> {
    let mut removals: HashMap<(usize, usize), Vec<Removal>> = HashMap::new();
    for (glyph, removed) in interpretation.glyphs.iter().zip(&selection.glyphs) {
        if !removed {
            continue;
        }
        let compensation = match glyph.compensation {
            Some(value) => value,
            // The pen does not move when the horizontal scaling is zero; a
            // zero font size still moves it by the character and word
            // spacing, which no TJ adjustment can reproduce.
            None if glyph.advance == (0.0, 0.0) => 0.0,
            None => {
                return Err(ContentError::Malformed(
                    "cannot remove a glyph drawn at font size 0 that moves the text position"
                        .to_owned(),
                ));
            }
        };
        let source = glyph.source;
        removals
            .try_reserve(1)
            .map_err(|error| ContentError::allocation("text removals", &error))?;
        let entry = removals
            .entry((source.container, source.operation))
            .or_default();
        try_push(
            entry,
            (source.operand, source.offset, source.len, compensation),
            "text removals",
        )?;
    }
    Ok(removals)
}

/// Whether a container is a Form placement inside an annotation appearance.
pub(crate) fn in_annotation(interpretation: &Interpretation, container: usize) -> bool {
    interpretation.containers[container].annotation.is_some()
}

/// The XObject name that a `Do` operation draws.
pub(crate) fn drawn_name(instructions: &Instructions<'_>, index: usize) -> Option<Vec<u8>> {
    use hayro::hayro_syntax::object::Object as HObject;
    if instructions.instruction(index).operator != b"Do" {
        return None;
    }
    match instructions.operands(index).last() {
        Some(HObject::Name(name)) => Some(name.to_vec()),
        _ => None,
    }
}

/// Every XObject name that a container's `Do` operations draw.
pub(crate) fn drawn_names(instructions: &Instructions<'_>) -> HashSet<Vec<u8>> {
    (0..instructions.len())
        .filter_map(|index| drawn_name(instructions, index))
        .collect()
}
