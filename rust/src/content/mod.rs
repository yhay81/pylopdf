//! Bounded interpretation of content streams in the lopdf graph.
//!
//! Redaction must rewrite the operators that produce what a reader sees, so
//! it interprets content tokenized as hayro tokenizes it (`syntax`), keeping
//! every operation's bytes so that an edit rewrites only what it changes. Glyph positions
//! follow hayro-interpret 0.8's text model so that they agree with the
//! glyphs hayro extracts and renders; after an edit, re-interpreting the
//! result with hayro verifies it. Nothing here is public API before the
//! Rewriting milestone; a private `_Document.content_glyphs` hook drives the tests.

mod fonts;
mod interpret;
pub(crate) mod syntax;

pub(crate) use interpret::{
    Container, ContainerInfo, Interpretation, interpret_page, transformed_rect,
};

use hayro::kurbo::Affine;

/// Form XObject nesting below a page, hayro-interpret's
/// `MAX_NESTED_INTERPRETATION_DEPTH`; deeper forms are not drawn.
pub(crate) const MAX_FORM_NESTING: usize = 50;
/// Operations interpreted for one page, counting every repetition of a
/// Form XObject, so that forms drawing forms cannot amplify without bound.
pub(crate) const MAX_CONTENT_OPERATIONS: usize = 1 << 24;
/// Glyph records produced for one page.
pub(crate) const MAX_GLYPHS: usize = 1 << 20;
/// Paths, images, shadings, marked-content sequences, and Form placements
/// recorded for one page, together.
pub(crate) const MAX_CONTENT_ITEMS: usize = 1 << 21;

/// Why interpretation stopped; no partial result accompanies it.
#[derive(Debug)]
pub(crate) enum ContentError {
    /// A bound was reached; `code` is the stable `LimitError` code.
    Limit { code: &'static str, message: String },
    /// The content cannot be parsed or decoded.
    Malformed(String),
    /// An allocation was refused.
    Allocation(String),
}

impl ContentError {
    pub(crate) fn allocation(context: &str, error: &impl std::fmt::Display) -> Self {
        Self::Allocation(format!("failed to allocate {context}: {error}"))
    }
}

/// Push onto a vector whose growth may be refused.
pub(crate) fn try_push<T>(items: &mut Vec<T>, item: T, context: &str) -> Result<(), ContentError> {
    items
        .try_reserve(1)
        .map_err(|error| ContentError::allocation(context, &error))?;
    items.push(item);
    Ok(())
}

/// The transform from default user space to the top-left, rotation-resolved
/// display space of extraction and rendering, as hayro-syntax 0.8's
/// `Page::initial_transform(true)` computes it: the crop box intersected
/// with the media box, an A4 page when either box is missing or the
/// intersection has no area, and box coordinates read in single precision.
pub(crate) fn display_transform(
    media: Option<[f64; 4]>,
    crop: Option<[f64; 4]>,
    rotation: i64,
) -> Affine {
    const POINTS_PER_MM: f64 = 72.0 / 25.4;
    const A4: [f64; 4] = [0.0, 0.0, 210.0 * POINTS_PER_MM, 297.0 * POINTS_PER_MM];
    let normalize = |[x0, y0, x1, y1]: [f64; 4]| {
        let [x0, y0, x1, y1] = [x0, y0, x1, y1].map(|value| f64::from(value as f32));
        [x0.min(x1), y0.min(y1), x1.max(x0), y1.max(y0)]
    };
    let media = media.map_or(A4, normalize);
    let crop = crop.map_or(media, normalize);
    let x0 = crop[0].max(media[0]);
    let y0 = crop[1].max(media[1]);
    let x1 = crop[2].min(media[2]).max(x0);
    let y1 = crop[3].min(media[3]).max(y0);
    let nearly_zero = |value: f64| (value as f32).abs() <= 1.0 / 4096.0;
    let (base_width, base_height) = if nearly_zero(x1 - x0) || nearly_zero(y1 - y0) {
        (f64::from(A4[2] as f32), f64::from(A4[3] as f32))
    } else {
        (
            f64::from((x1 - x0).max(1.0) as f32),
            f64::from((y1 - y0).max(1.0) as f32),
        )
    };
    let (width, height) = if matches!(rotation, 90 | 270) {
        (base_height, base_width)
    } else {
        (base_width, base_height)
    };
    let rotate_cw = Affine::new([0.0, 1.0, -1.0, 0.0, 0.0, 0.0]);
    let rotate_ccw = Affine::new([0.0, -1.0, 1.0, 0.0, 0.0, 0.0]);
    let rotation_transform = match rotation {
        90 => rotate_cw * Affine::translate((0.0, -width)),
        180 => Affine::scale(-1.0) * Affine::translate((-width, -height)),
        270 => Affine::translate((0.0, height)) * rotate_ccw,
        _ => Affine::IDENTITY,
    };
    let inversion = Affine::new([1.0, 0.0, 0.0, -1.0, 0.0, base_height]);
    rotation_transform * inversion * Affine::translate((-x0, -y0))
}
