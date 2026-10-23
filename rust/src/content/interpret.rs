//! A bounded text-positioning interpreter over lopdf's strict content parse.
//!
//! The state machine mirrors hayro-interpret 0.8 (`interpret/mod.rs`,
//! `interpret/state.rs`, and `interpret/text.rs`): the text state is part of
//! the graphics state that `q` and `Q` save and restore, a missing font falls
//! back to Helvetica, operators with unusable operands are skipped, a Form
//! XObject runs with its `/Matrix` and with its own `/Resources` or those of
//! the stream that draws it, and nesting stops at hayro's depth of 50. Unlike
//! hayro, the interpreter records every glyph whatever its visibility, its
//! optional-content state, or its font's usability, because redaction must
//! find every byte that can produce one.

use std::collections::HashMap;

use hayro::kurbo::{Affine, Point};
use lopdf::content::Operation;
use lopdf::{Dictionary, Document, Object, ObjectId, Stream};

use super::fonts::{Decoder, FontMetrics, UNITS_PER_EM, dict_of};
use super::syntax::Instructions;
use super::{ContentError, MAX_CONTENT_OPERATIONS, MAX_FORM_NESTING, MAX_GLYPHS, try_push};

/// The stream that holds a glyph's code bytes.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(crate) enum Container {
    /// The page's content streams, parsed as one stream.
    Page,
    /// A Form XObject.
    Form(ObjectId),
}

/// Where a glyph's character code sits in its content stream.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct GlyphSource {
    pub(crate) container: Container,
    /// Index of the operation in the container's parsed operations.
    pub(crate) operation: usize,
    /// Index of the operand that holds the string, the last operand of `Tj`,
    /// `'`, and `"`; for `TJ`, the index of the element in its array.
    pub(crate) operand: usize,
    /// Byte offset and length of the code in the string.
    pub(crate) offset: usize,
    pub(crate) len: usize,
}

/// One glyph that a text-showing operator produces.
#[derive(Clone, Debug)]
pub(crate) struct Glyph {
    pub(crate) source: GlyphSource,
    pub(crate) code: u32,
    /// Maps glyph space, 1000 units per em, to default user space; the glyph
    /// origin is `transform * (0, 0)`.
    pub(crate) transform: Affine,
    /// The glyph's advance in glyph space.
    pub(crate) advance: (f64, f64),
    /// Text rendering mode, 0 to 7.
    pub(crate) render_mode: u8,
    /// Whether a width came from a guess because it lives only in an
    /// embedded font program.
    pub(crate) approximate: bool,
    /// Whether the font was missing or unusable, so hayro substitutes
    /// Helvetica and shows only ASCII bytes.
    pub(crate) fallback: bool,
    /// The annotation whose normal appearance drew the glyph, if any.
    pub(crate) annotation: Option<ObjectId>,
}

impl Glyph {
    /// The glyph origin in default user space.
    pub(crate) fn origin(&self) -> Point {
        self.transform * Point::ORIGIN
    }
}

#[derive(Clone)]
struct TextState {
    char_space: f32,
    word_space: f32,
    /// Percent, as `Tz` sets it.
    horizontal_scaling: f32,
    leading: f32,
    font: Option<FontRef>,
    font_size: f32,
    rise: f32,
    render_mode: u8,
    text_matrix: Affine,
    text_line_matrix: Affine,
}

impl Default for TextState {
    fn default() -> Self {
        Self {
            char_space: 0.0,
            word_space: 0.0,
            horizontal_scaling: 100.0,
            leading: 0.0,
            font: None,
            font_size: 1.0,
            rise: 0.0,
            render_mode: 0,
            text_matrix: Affine::IDENTITY,
            text_line_matrix: Affine::IDENTITY,
        }
    }
}

/// A font in the interpreter's cache, or hayro's Helvetica fallback.
#[derive(Clone, Copy)]
enum FontRef {
    Loaded(usize),
    Fallback,
}

#[derive(Clone)]
struct State {
    ctm: Affine,
    text: TextState,
}

/// Interpret one page and return its glyphs in drawing order.
///
/// `page_content` is the page's decoded content, every content stream
/// joined, and `resources` the page's effective resource dictionary.
/// `decode` decodes Form XObjects and embedded CMaps under the caller's
/// bounds. Refusals return no partial result.
pub(crate) fn interpret_page(
    doc: &Document,
    page: &Dictionary,
    page_content: &[u8],
    resources: Option<&Dictionary>,
    decode: &Decoder<'_>,
) -> Result<Vec<Glyph>, ContentError> {
    let mut interpreter = Interpreter {
        doc,
        decode,
        glyphs: Vec::new(),
        fonts: Vec::new(),
        font_index: HashMap::new(),
        operations: 0,
        annotation: None,
    };
    let content = parse(page_content)?;
    let mut states = vec![State {
        ctm: Affine::IDENTITY,
        text: TextState::default(),
    }];
    interpreter.run(&content, Container::Page, resources, &mut states, 0)?;
    interpreter.draw_annotations(page, resources, &mut states)?;
    Ok(interpreter.glyphs)
}

fn parse(data: &[u8]) -> Result<Vec<Operation>, ContentError> {
    Instructions::parse(data, None)?.to_operations()
}

struct Interpreter<'a, 'd> {
    doc: &'a Document,
    decode: &'d Decoder<'d>,
    glyphs: Vec<Glyph>,
    fonts: Vec<Option<FontMetrics>>,
    /// Loaded fonts by the address of their dictionary, which stays stable
    /// for the borrowed document.
    font_index: HashMap<usize, usize>,
    operations: usize,
    /// The annotation whose appearance is being interpreted.
    annotation: Option<ObjectId>,
}

impl<'a> Interpreter<'a, '_> {
    fn run(
        &mut self,
        content: &[Operation],
        container: Container,
        resources: Option<&'a Dictionary>,
        states: &mut Vec<State>,
        depth: usize,
    ) -> Result<(), ContentError> {
        // hayro's `interpret` saves the state first and restores everything
        // afterwards, so nothing a stream changes leaks out of it.
        let base = states.len();
        let saved = states.last().cloned().ok_or_else(missing_state)?;
        try_push(states, saved, "graphics state stack")?;
        for (index, operation) in content.iter().enumerate() {
            self.operations += 1;
            if self.operations > MAX_CONTENT_OPERATIONS {
                return Err(ContentError::Limit {
                    code: "content_operation_count",
                    message: format!(
                        "content interpretation exceeds {MAX_CONTENT_OPERATIONS} operations"
                    ),
                });
            }
            self.step(operation, index, container, resources, states, depth)?;
        }
        states.truncate(base);
        Ok(())
    }

    fn step(
        &mut self,
        operation: &Operation,
        index: usize,
        container: Container,
        resources: Option<&'a Dictionary>,
        states: &mut Vec<State>,
        depth: usize,
    ) -> Result<(), ContentError> {
        let operands = operation.operands.as_slice();
        match operation.operator.as_str() {
            "q" => {
                let current = states.last().cloned().ok_or_else(missing_state)?;
                try_push(states, current, "graphics state stack")?;
            }
            "Q" => {
                if states.len() > 1 {
                    states.pop();
                }
            }
            "cm" => {
                if let Some(matrix) = tail(operands, 6).and_then(matrix) {
                    let state = current(states)?;
                    state.ctm *= matrix;
                }
            }
            "BT" => {
                let text = &mut current(states)?.text;
                text.text_matrix = Affine::IDENTITY;
                text.text_line_matrix = Affine::IDENTITY;
            }
            "Tm" => {
                if let Some(matrix) = tail(operands, 6).and_then(matrix) {
                    let text = &mut current(states)?.text;
                    text.text_matrix = matrix;
                    text.text_line_matrix = matrix;
                }
            }
            "Tf" => {
                if let Some([Object::Name(name), size]) = tail(operands, 2)
                    && let Some(size) = real(size)
                {
                    let font = self.font(resources, name)?;
                    let text = &mut current(states)?.text;
                    text.font_size = size;
                    text.font = font;
                }
            }
            "Tc" => set(operands, |value| {
                current_text(states, |t| t.char_space = value)
            })?,
            "Tw" => set(operands, |value| {
                current_text(states, |t| t.word_space = value)
            })?,
            "Tz" => set(operands, |value| {
                current_text(states, |t| t.horizontal_scaling = value)
            })?,
            "TL" => set(operands, |value| {
                current_text(states, |t| t.leading = value)
            })?,
            "Ts" => set(operands, |value| current_text(states, |t| t.rise = value))?,
            "Tr" => {
                if let Some([mode]) = tail(operands, 1)
                    && let Some(mode) = real(mode)
                {
                    // hayro reads the mode as an integer and draws any value
                    // outside 0 to 7 as fill.
                    let mode = mode.trunc();
                    let mode = if (0.0..8.0).contains(&mode) {
                        mode as u8
                    } else {
                        0
                    };
                    current(states)?.text.render_mode = mode;
                }
            }
            "Td" => {
                if let Some([x, y]) = tail(operands, 2)
                    && let (Some(x), Some(y)) = (real(x), real(y))
                {
                    next_line(&mut current(states)?.text, f64::from(x), f64::from(y));
                }
            }
            "TD" => {
                if let Some([x, y]) = tail(operands, 2)
                    && let (Some(x), Some(y)) = (real(x), real(y))
                {
                    let text = &mut current(states)?.text;
                    text.leading = -y;
                    next_line(text, f64::from(x), f64::from(y));
                }
            }
            "T*" => {
                let text = &mut current(states)?.text;
                let leading = text.leading;
                next_line(text, 0.0, -f64::from(leading));
            }
            "Tj" => {
                if let Some([Object::String(bytes, _)]) = tail(operands, 1) {
                    let source = source(container, index, operands.len() - 1);
                    self.show(states, bytes, source)?;
                }
            }
            "'" => {
                if let Some([Object::String(bytes, _)]) = tail(operands, 1) {
                    let text = &mut current(states)?.text;
                    let leading = text.leading;
                    next_line(text, 0.0, -f64::from(leading));
                    self.show(states, bytes, source(container, index, operands.len() - 1))?;
                }
            }
            "\"" => {
                if let Some([word, character, Object::String(bytes, _)]) = tail(operands, 3)
                    && let (Some(word), Some(character)) = (real(word), real(character))
                {
                    let text = &mut current(states)?.text;
                    text.word_space = word;
                    text.char_space = character;
                    let leading = text.leading;
                    next_line(text, 0.0, -f64::from(leading));
                    self.show(states, bytes, source(container, index, operands.len() - 1))?;
                }
            }
            "TJ" => {
                if let Some([Object::Array(items)]) = tail(operands, 1) {
                    ensure_font(current(states)?);
                    for (element, item) in items.iter().enumerate() {
                        match item {
                            Object::String(bytes, _) => {
                                self.show(states, bytes, source(container, index, element))?;
                            }
                            other => {
                                if let Some(adjustment) = real(other) {
                                    self.adjust(current(states)?, adjustment);
                                }
                            }
                        }
                    }
                }
            }
            "gs" => {
                if let Some([Object::Name(name)]) = tail(operands, 1) {
                    self.graphics_state_font(resources, name, states)?;
                }
            }
            "Do" => {
                if let Some([Object::Name(name)]) = tail(operands, 1) {
                    self.draw_form(resources, name, states, depth)?;
                }
            }
            _ => {}
        }
        Ok(())
    }

    /// Resolve `Tf`'s font: a missing entry falls back to Helvetica, and an
    /// unusable font leaves no font, which `Tj` then replaces with Helvetica.
    fn font(
        &mut self,
        resources: Option<&'a Dictionary>,
        name: &[u8],
    ) -> Result<Option<FontRef>, ContentError> {
        let fonts = resources
            .and_then(|resources| resources.get(b"Font").ok())
            .and_then(|fonts| dict_of(self.doc, Some(fonts)));
        let Some(font) = fonts
            .and_then(|fonts| fonts.get(name).ok())
            .and_then(|font| dict_of(self.doc, Some(font)))
        else {
            return Ok(Some(FontRef::Fallback));
        };
        self.load_font(font)
    }

    /// Load a font dictionary once, returning `None` for a font hayro
    /// cannot use.
    fn load_font(&mut self, font: &'a Dictionary) -> Result<Option<FontRef>, ContentError> {
        let key = std::ptr::from_ref(font) as usize;
        if let Some(&index) = self.font_index.get(&key) {
            return Ok(self.fonts[index].as_ref().map(|_| FontRef::Loaded(index)));
        }
        let metrics = FontMetrics::read(self.doc, font, self.decode);
        let index = self.fonts.len();
        let loaded = metrics.is_some();
        try_push(&mut self.fonts, metrics, "font cache")?;
        self.font_index
            .try_reserve(1)
            .map_err(|error| ContentError::allocation("font index", &error))?;
        self.font_index.insert(key, index);
        Ok(loaded.then_some(FontRef::Loaded(index)))
    }

    /// Apply an ExtGState's `/Font [font size]`, the only entry of a
    /// graphics state that positions text.
    fn graphics_state_font(
        &mut self,
        resources: Option<&'a Dictionary>,
        name: &[u8],
        states: &mut [State],
    ) -> Result<(), ContentError> {
        let Some(entry) = resources
            .and_then(|resources| resources.get(b"ExtGState").ok())
            .and_then(|states| dict_of(self.doc, Some(states)))
            .and_then(|states| states.get(name).ok())
            .and_then(|state| dict_of(self.doc, Some(state)))
            .and_then(|state| state.get(b"Font").ok())
            .and_then(|value| resolve(self.doc, value))
            .and_then(|value| value.as_array().ok())
        else {
            return Ok(());
        };
        let (Some(font), Some(size)) = (
            entry.first().and_then(|font| dict_of(self.doc, Some(font))),
            entry
                .get(1)
                .and_then(|size| resolve(self.doc, size))
                .and_then(real),
        ) else {
            return Ok(());
        };
        let font = self.load_font(font)?;
        let text = &mut current(states)?.text;
        text.font_size = size;
        text.font = font;
        Ok(())
    }

    fn show(
        &mut self,
        states: &mut [State],
        bytes: &[u8],
        source: GlyphSource,
    ) -> Result<(), ContentError> {
        let state = current(states)?;
        ensure_font(state);
        let fallback_metrics;
        let (metrics, fallback) = match state.text.font {
            Some(FontRef::Loaded(index)) => match self.fonts[index].as_ref() {
                Some(metrics) => (metrics, false),
                None => {
                    fallback_metrics = FontMetrics::fallback();
                    (&fallback_metrics, true)
                }
            },
            Some(FontRef::Fallback) | None => {
                fallback_metrics = FontMetrics::fallback();
                (&fallback_metrics, true)
            }
        };
        let horizontal = metrics.is_horizontal();
        let mut offset = 0;
        while offset < bytes.len() {
            let (code, len) = metrics.read_code(bytes, offset);
            let text = &state.text;
            let displacement = metrics.displacement(code);
            let transform = state.ctm
                * text.text_matrix
                * temp_transform(text)
                * Affine::scale(1.0 / UNITS_PER_EM)
                * Affine::translate(displacement);
            let advance = metrics.advance(code);
            if self.glyphs.len() >= MAX_GLYPHS {
                return Err(ContentError::Limit {
                    code: "content_glyph_count",
                    message: format!("content interpretation exceeds {MAX_GLYPHS} glyphs"),
                });
            }
            try_push(
                &mut self.glyphs,
                Glyph {
                    source: GlyphSource {
                        offset,
                        len,
                        ..source
                    },
                    code,
                    transform,
                    advance,
                    render_mode: text.render_mode,
                    approximate: metrics.is_approximate(),
                    fallback,
                    annotation: self.annotation,
                },
                "glyph list",
            )?;
            apply_code_advance(&mut state.text, advance, code, len, horizontal);
            offset += len;
        }
        Ok(())
    }

    fn adjust(&self, state: &mut State, adjustment: f32) {
        let horizontal = match state.text.font {
            Some(FontRef::Loaded(index)) => self.fonts[index]
                .as_ref()
                .is_none_or(FontMetrics::is_horizontal),
            Some(FontRef::Fallback) | None => true,
        };
        let text = &mut state.text;
        let scaling = if horizontal {
            text.horizontal_scaling / 100.0
        } else {
            1.0
        };
        let scaled = -adjustment / UNITS_PER_EM as f32 * text.font_size * scaling;
        let (tx, ty) = if horizontal {
            (scaled, 0.0)
        } else {
            (0.0, scaled)
        };
        text.text_matrix *= Affine::translate((f64::from(tx), f64::from(ty)));
    }

    fn draw_form(
        &mut self,
        resources: Option<&'a Dictionary>,
        name: &[u8],
        states: &mut Vec<State>,
        depth: usize,
    ) -> Result<(), ContentError> {
        let Some((id, stream)) = resources
            .and_then(|resources| resources.get(b"XObject").ok())
            .and_then(|xobjects| dict_of(self.doc, Some(xobjects)))
            .and_then(|xobjects| xobjects.get(name).ok())
            .and_then(|object| self.stream(object))
        else {
            return Ok(());
        };
        if !matches!(stream.dict.get(b"Subtype"), Ok(Object::Name(subtype)) if subtype == b"Form") {
            return Ok(());
        }
        self.draw_form_stream(id, stream, resources, states, depth)
    }

    /// Draw a Form XObject as hayro's `FormXObject::draw` does: below the
    /// nesting limit, with its `/Matrix`, and with its own resources or those
    /// of the stream that draws it.
    fn draw_form_stream(
        &mut self,
        id: ObjectId,
        stream: &'a Stream,
        resources: Option<&'a Dictionary>,
        states: &mut Vec<State>,
        depth: usize,
    ) -> Result<(), ContentError> {
        if depth >= MAX_FORM_NESTING {
            return Ok(());
        }
        let data = (self.decode)(stream)?;
        let content = parse(&data)?;
        let form_matrix = form_matrix(stream);
        let form_resources = stream
            .dict
            .get(b"Resources")
            .ok()
            .and_then(|value| dict_of(self.doc, Some(value)))
            .or(resources);
        let base = states.len();
        let saved = current(states)?.clone();
        try_push(states, saved, "graphics state stack")?;
        current(states)?.ctm *= form_matrix;
        self.run(
            &content,
            Container::Form(id),
            form_resources,
            states,
            depth + 1,
        )?;
        states.truncate(base);
        Ok(())
    }

    /// Draw the normal appearances of the page's annotations, as hayro's
    /// `interpret_page` does after the page content.
    fn draw_annotations(
        &mut self,
        page: &'a Dictionary,
        resources: Option<&'a Dictionary>,
        states: &mut Vec<State>,
    ) -> Result<(), ContentError> {
        let Some(annotations) = page
            .get(b"Annots")
            .ok()
            .and_then(|value| resolve(self.doc, value))
            .and_then(|value| value.as_array().ok())
        else {
            return Ok(());
        };
        for item in annotations {
            // hayro's typed iteration stops at the first entry that is not a
            // dictionary.
            let annotation_id = item.as_reference().ok();
            let Some(annotation) = resolve(self.doc, item).and_then(|value| value.as_dict().ok())
            else {
                break;
            };
            let flags = annotation
                .get(b"F")
                .ok()
                .and_then(|value| resolve(self.doc, value))
                .and_then(|value| value.as_i64().ok())
                .unwrap_or(0);
            if flags & 2 != 0 {
                continue;
            }
            let Some((id, stream)) = self.appearance(annotation) else {
                continue;
            };
            let Some(bbox) = rect(self.doc, stream.dict.get(b"BBox").ok()) else {
                continue;
            };
            let Some(annotation_rect) = rect(self.doc, annotation.get(b"Rect").ok()) else {
                continue;
            };
            // ISO 32000-2 §12.5.5: map the transformed bounding box onto the
            // annotation rectangle.
            let corners = [
                Point::new(bbox[0], bbox[1]),
                Point::new(bbox[2], bbox[1]),
                Point::new(bbox[2], bbox[3]),
                Point::new(bbox[0], bbox[3]),
            ]
            .map(|corner| form_matrix(stream) * corner);
            let min_x = corners.iter().map(|p| p.x).fold(f64::INFINITY, f64::min);
            let min_y = corners.iter().map(|p| p.y).fold(f64::INFINITY, f64::min);
            let max_x = corners
                .iter()
                .map(|p| p.x)
                .fold(f64::NEG_INFINITY, f64::max);
            let max_y = corners
                .iter()
                .map(|p| p.y)
                .fold(f64::NEG_INFINITY, f64::max);
            let fit = Affine::new([
                (annotation_rect[2] - annotation_rect[0]) / (max_x - min_x),
                0.0,
                0.0,
                (annotation_rect[3] - annotation_rect[1]) / (max_y - min_y),
                annotation_rect[0] - min_x,
                annotation_rect[1] - min_y,
            ]);
            let base = states.len();
            let saved = current(states)?.clone();
            try_push(states, saved, "graphics state stack")?;
            current(states)?.ctm *= fit;
            self.annotation = annotation_id;
            let drawn = self.draw_form_stream(id, stream, resources, states, 0);
            self.annotation = None;
            drawn?;
            states.truncate(base);
        }
        Ok(())
    }

    /// The normal appearance stream that hayro draws: `/AP /N` itself, or
    /// the state that `/AS` names, else `/Off`.
    fn appearance(&self, annotation: &'a Dictionary) -> Option<(ObjectId, &'a Stream)> {
        let appearances = dict_of(self.doc, annotation.get(b"AP").ok())?;
        let normal = appearances.get(b"N").ok()?;
        if let Some(found) = self.stream(normal) {
            return Some(found);
        }
        let states = dict_of(self.doc, Some(normal))?;
        let state = annotation
            .get(b"AS")
            .ok()
            .and_then(|value| resolve(self.doc, value))
            .and_then(|value| value.as_name().ok());
        state
            .and_then(|state| states.get(state).ok())
            .and_then(|value| self.stream(value))
            .or_else(|| states.get(b"Off").ok().and_then(|value| self.stream(value)))
    }

    /// Resolve a reference to a stream, returning its object ID.
    fn stream(&self, object: &'a Object) -> Option<(ObjectId, &'a Stream)> {
        let mut id = object.as_reference().ok()?;
        for _ in 0..32 {
            match self.doc.get_object(id).ok()? {
                Object::Stream(stream) => return Some((id, stream)),
                Object::Reference(next) => id = *next,
                _ => return None,
            }
        }
        None
    }
}

/// A Form XObject's `/Matrix`, the identity when absent or malformed.
fn form_matrix(stream: &Stream) -> Affine {
    stream
        .dict
        .get(b"Matrix")
        .ok()
        .and_then(|value| value.as_array().ok())
        .and_then(|values| matrix(values))
        .unwrap_or(Affine::IDENTITY)
}

/// A rectangle as hayro reads one: four numbers in single precision,
/// normalized so that the first corner is the lower left.
fn rect(doc: &Document, value: Option<&Object>) -> Option<[f64; 4]> {
    let values = resolve(doc, value?)?.as_array().ok()?;
    let mut numbers = values
        .iter()
        .map(|value| resolve(doc, value).and_then(real));
    let [x0, y0, x1, y1] = [
        numbers.next()??,
        numbers.next()??,
        numbers.next()??,
        numbers.next()??,
    ]
    .map(f64::from);
    Some([x0.min(x1), y0.min(y1), x1.max(x0), y1.max(y0)])
}

fn resolve<'a>(doc: &'a Document, value: &'a Object) -> Option<&'a Object> {
    let mut current = value;
    for _ in 0..32 {
        match current {
            Object::Reference(id) => current = doc.get_object(*id).ok()?,
            other => return Some(other),
        }
    }
    None
}

/// `[Tfs × Th, 0, 0, Tfs, 0, Trise]`, hayro's `temp_transform`.
fn temp_transform(text: &TextState) -> Affine {
    Affine::new([
        f64::from(text.font_size) * f64::from(text.horizontal_scaling / 100.0),
        0.0,
        0.0,
        f64::from(text.font_size),
        0.0,
        f64::from(text.rise),
    ])
}

/// hayro's `apply_code_advance`, in the same single precision.
fn apply_code_advance(
    text: &mut TextState,
    advance: (f64, f64),
    code: u32,
    len: usize,
    horizontal: bool,
) {
    let word_space = if code == 32 && len == 1 {
        text.word_space
    } else {
        0.0
    };
    let base = |advance: f32| {
        advance / UNITS_PER_EM as f32 * text.font_size + text.char_space + word_space
    };
    let tx = if horizontal {
        base(advance.0 as f32) * (text.horizontal_scaling / 100.0)
    } else {
        0.0
    };
    let ty = if horizontal {
        0.0
    } else {
        base(advance.1 as f32)
    };
    text.text_matrix *= Affine::translate((f64::from(tx), f64::from(ty)));
}

fn next_line(text: &mut TextState, tx: f64, ty: f64) {
    let matrix = text.text_line_matrix * Affine::translate((tx, ty));
    text.text_line_matrix = matrix;
    text.text_matrix = matrix;
}

/// Before showing text without a font, hayro assumes Helvetica.
fn ensure_font(state: &mut State) {
    if state.text.font.is_none() {
        state.text.font = Some(FontRef::Fallback);
    }
}

fn source(container: Container, operation: usize, operand: usize) -> GlyphSource {
    GlyphSource {
        container,
        operation,
        operand,
        offset: 0,
        len: 0,
    }
}

fn current(states: &mut [State]) -> Result<&mut State, ContentError> {
    states.last_mut().ok_or_else(missing_state)
}

fn current_text(
    states: &mut [State],
    apply: impl FnOnce(&mut TextState),
) -> Result<(), ContentError> {
    apply(&mut current(states)?.text);
    Ok(())
}

fn set(
    operands: &[Object],
    apply: impl FnOnce(f32) -> Result<(), ContentError>,
) -> Result<(), ContentError> {
    match tail(operands, 1) {
        Some([value]) => real(value).map_or(Ok(()), apply),
        _ => Ok(()),
    }
}

/// The last `count` operands, as hayro reads a fixed-arity operator: garbage
/// operands in front of them are ignored.
fn tail(operands: &[Object], count: usize) -> Option<&[Object]> {
    operands.get(operands.len().checked_sub(count)?..)
}

fn missing_state() -> ContentError {
    ContentError::Malformed("the graphics state stack is empty".to_owned())
}

fn real(value: &Object) -> Option<f32> {
    match value {
        Object::Integer(value) => Some(*value as f32),
        Object::Real(value) => Some(*value),
        _ => None,
    }
}

fn matrix(values: &[Object]) -> Option<Affine> {
    let [a, b, c, d, e, f] = values else {
        return None;
    };
    let mut coefficients = [0.0; 6];
    for (slot, value) in coefficients.iter_mut().zip([a, b, c, d, e, f]) {
        *slot = f64::from(real(value)?);
    }
    Some(Affine::new(coefficients))
}
