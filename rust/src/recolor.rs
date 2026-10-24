//! Recoloring: the colour operators of a content stream, and colour values,
//! in another device colour space.
//!
//! Conversions are the naive ones of ISO 32000-2 §10.4: gray to RGB repeats
//! the value, RGB to gray weighs the components as BT.601 does (the weights
//! that gray rendering uses), CMYK to RGB multiplies the complements, and RGB
//! to CMYK takes black from the largest component. Colour spaces that name a
//! device equivalent (ICCBased with one, three, or four components, CalGray,
//! CalRGB) convert like it; Pattern, Indexed, Separation, DeviceN, and Lab
//! spaces are left to the objects that hold them.

use lopdf::content::Operation;
use lopdf::{Dictionary, Document, Object};

use crate::content::ContentError;
use crate::content::syntax::Instructions;
use crate::redact::{self, ContainerEdits, OperationEdit};

/// The colour space that recoloring writes.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Target {
    Gray,
    Rgb,
    Cmyk,
}

impl Target {
    pub(crate) fn components(self) -> usize {
        match self {
            Self::Gray => 1,
            Self::Rgb => 3,
            Self::Cmyk => 4,
        }
    }

    pub(crate) fn device_name(self) -> &'static [u8] {
        match self {
            Self::Gray => b"DeviceGray",
            Self::Rgb => b"DeviceRGB",
            Self::Cmyk => b"DeviceCMYK",
        }
    }

    fn operator(self, stroke: bool) -> &'static str {
        match (self, stroke) {
            (Self::Gray, false) => "g",
            (Self::Gray, true) => "G",
            (Self::Rgb, false) => "rg",
            (Self::Rgb, true) => "RG",
            (Self::Cmyk, false) => "k",
            (Self::Cmyk, true) => "K",
        }
    }

    /// The device space with `components` components, if one has them.
    pub(crate) fn with_components(components: usize) -> Option<Self> {
        match components {
            1 => Some(Self::Gray),
            3 => Some(Self::Rgb),
            4 => Some(Self::Cmyk),
            _ => None,
        }
    }
}

/// Convert one colour from `from` to `to`; values range from 0 to 1.
pub(crate) fn convert(values: &[f32], from: Target, to: Target) -> Vec<f32> {
    let clamp = |value: f32| value.clamp(0.0, 1.0);
    let rgb = match from {
        Target::Gray => {
            let gray = clamp(values.first().copied().unwrap_or(0.0));
            [gray, gray, gray]
        }
        Target::Rgb => {
            let get = |index: usize| clamp(values.get(index).copied().unwrap_or(0.0));
            [get(0), get(1), get(2)]
        }
        Target::Cmyk => {
            let get = |index: usize| clamp(values.get(index).copied().unwrap_or(0.0));
            let black = 1.0 - get(3);
            [
                (1.0 - get(0)) * black,
                (1.0 - get(1)) * black,
                (1.0 - get(2)) * black,
            ]
        }
    };
    if from == to {
        return values.iter().map(|value| clamp(*value)).collect();
    }
    match to {
        Target::Gray => vec![0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]],
        Target::Rgb => rgb.to_vec(),
        Target::Cmyk => {
            if from == Target::Gray {
                return vec![0.0, 0.0, 0.0, 1.0 - rgb[0]];
            }
            let black = 1.0 - rgb[0].max(rgb[1]).max(rgb[2]);
            if black >= 1.0 {
                return vec![0.0, 0.0, 0.0, 1.0];
            }
            let cmy = |value: f32| (1.0 - value - black) / (1.0 - black);
            vec![cmy(rgb[0]), cmy(rgb[1]), cmy(rgb[2]), black]
        }
    }
}

/// Convert 8-bit samples of `from` to `to`.
pub(crate) fn convert_samples(samples: &[u8], from: Target, to: Target) -> Vec<u8> {
    let (n, m) = (from.components(), to.components());
    let mut out = Vec::with_capacity(samples.len() / n * m);
    let mut values = [0.0_f32; 4];
    for pixel in samples.chunks_exact(n) {
        for (slot, sample) in values.iter_mut().zip(pixel) {
            *slot = f32::from(*sample) / 255.0;
        }
        for value in convert(&values[..n], from, to) {
            out.push((value * 255.0).round().clamp(0.0, 255.0) as u8);
        }
    }
    out
}

/// What a colour space name or object stands for when recoloring.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Space {
    Device(Target),
    /// A space that recoloring leaves to the object that holds it.
    Keep,
}

/// The kind of a colour space object.
pub(crate) fn space_of(doc: &Document, value: &Object) -> Space {
    let Some(value) = redact::resolve(doc, value) else {
        return Space::Keep;
    };
    match value {
        Object::Name(name) => match name.as_slice() {
            b"DeviceGray" | b"G" | b"CalGray" => Space::Device(Target::Gray),
            b"DeviceRGB" | b"RGB" | b"CalRGB" => Space::Device(Target::Rgb),
            b"DeviceCMYK" | b"CMYK" => Space::Device(Target::Cmyk),
            _ => Space::Keep,
        },
        Object::Array(items) => match items.first().and_then(|first| first.as_name().ok()) {
            Some(b"CalGray") => Space::Device(Target::Gray),
            Some(b"CalRGB") => Space::Device(Target::Rgb),
            Some(b"ICCBased") => items
                .get(1)
                .and_then(|profile| redact::resolve(doc, profile))
                .and_then(|profile| profile.as_stream().ok())
                .and_then(|profile| profile.dict.get(b"N").ok())
                .and_then(|n| n.as_i64().ok())
                .and_then(|n| usize::try_from(n).ok())
                .and_then(Target::with_components)
                .map_or(Space::Keep, Space::Device),
            _ => Space::Keep,
        },
        _ => Space::Keep,
    }
}

fn numbers(operands: &[Object]) -> Option<Vec<f32>> {
    operands
        .iter()
        .map(|value| match value {
            Object::Integer(value) => Some(*value as f32),
            Object::Real(value) => Some(*value),
            _ => None,
        })
        .collect()
}

fn color_operation(target: Target, stroke: bool, values: Vec<f32>) -> Operation {
    Operation::new(
        target.operator(stroke),
        values.into_iter().map(Object::Real).collect(),
    )
}

/// The edits that write the colour operators of one content stream in
/// `target`, and the inline images, by operation, for the caller to
/// convert. `resources` resolves `cs` names.
pub(crate) fn recolor_content(
    doc: &Document,
    instructions: &Instructions<'_>,
    resources: Option<&Dictionary>,
    target: Target,
) -> Result<(ContainerEdits, Vec<usize>), ContentError> {
    let operations = instructions.to_operations()?;
    let spaces = resources
        .and_then(|resources| resources.get(b"ColorSpace").ok())
        .and_then(|value| redact::resolve(doc, value))
        .and_then(|value| value.as_dict().ok());
    let resolve = |name: &[u8]| -> Space {
        match name {
            b"DeviceGray" | b"G" => Space::Device(Target::Gray),
            b"DeviceRGB" | b"RGB" => Space::Device(Target::Rgb),
            b"DeviceCMYK" | b"CMYK" => Space::Device(Target::Cmyk),
            _ => spaces
                .and_then(|spaces| spaces.get(name).ok())
                .map_or(Space::Keep, |space| space_of(doc, space)),
        }
    };
    let mut edits = ContainerEdits::default();
    let mut inline = Vec::new();
    // The fill and stroke spaces, saved by `q` as the graphics state is.
    let initial = (Space::Device(Target::Gray), Space::Device(Target::Gray));
    let mut stack = vec![initial];
    for (index, operation) in operations.iter().enumerate() {
        let operands = operation.operands.as_slice();
        let state = stack.last_mut().expect("the stack keeps its base");
        let replacement = match operation.operator.as_str() {
            "q" => {
                let current = *state;
                stack.push(current);
                None
            }
            "Q" => {
                if stack.len() > 1 {
                    stack.pop();
                }
                None
            }
            operator @ ("g" | "G" | "rg" | "RG" | "k" | "K") => {
                let stroke = operator.chars().all(char::is_uppercase);
                let from = match operator {
                    "g" | "G" => Target::Gray,
                    "rg" | "RG" => Target::Rgb,
                    _ => Target::Cmyk,
                };
                let slot = if stroke { &mut state.1 } else { &mut state.0 };
                *slot = Space::Device(target);
                let count = from.components();
                match operands
                    .get(operands.len().saturating_sub(count)..)
                    .filter(|tail| tail.len() == count)
                    .and_then(numbers)
                {
                    Some(values) if from != target => Some(vec![color_operation(
                        target,
                        stroke,
                        convert(&values, from, target),
                    )]),
                    _ => None,
                }
            }
            operator @ ("cs" | "CS") => {
                let stroke = operator == "CS";
                let space = match operands.last() {
                    Some(Object::Name(name)) => resolve(name),
                    _ => Space::Keep,
                };
                let slot = if stroke { &mut state.1 } else { &mut state.0 };
                *slot = space;
                match space {
                    Space::Device(from) if from != target || !is_device_name(operands.last()) => {
                        Some(vec![Operation::new(
                            operator,
                            vec![Object::Name(target.device_name().to_vec())],
                        )])
                    }
                    _ => None,
                }
            }
            operator @ ("sc" | "scn" | "SC" | "SCN") => {
                let stroke = operator.starts_with('S');
                let space = if stroke { state.1 } else { state.0 };
                match (space, numbers(operands)) {
                    (Space::Device(from), Some(values))
                        if from != target && values.len() == from.components() =>
                    {
                        Some(vec![Operation::new(
                            operator,
                            convert(&values, from, target)
                                .into_iter()
                                .map(Object::Real)
                                .collect(),
                        )])
                    }
                    _ => None,
                }
            }
            "BI" => {
                inline.push(index);
                None
            }
            _ => None,
        };
        if let Some(replacement) = replacement {
            edits
                .operations
                .insert(index, OperationEdit::Replace(redact::encode(replacement)?));
        }
    }
    Ok((edits, inline))
}

fn is_device_name(value: Option<&Object>) -> bool {
    matches!(value, Some(Object::Name(name)) if matches!(name.as_slice(), b"DeviceGray" | b"DeviceRGB" | b"DeviceCMYK"))
}
