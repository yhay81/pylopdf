//! Content-stream syntax that keeps the bytes of every operation.
//!
//! hayro's `UntypedIter` tokenizes the stream, so the operations are exactly
//! the ones hayro reads, inline images and its recoveries from malformed
//! input included. Each operation owns the bytes from the end of the
//! previous one to the end of its operator (to the end of `EI` for an inline
//! image), and the bytes after the last operation stay as the stream's tail,
//! so concatenating every operation's bytes and the tail reproduces the
//! stream. hayro stops at an operation that it cannot read, such as an
//! eleventh operand; the rest of the stream then stays in the tail.

use std::ops::Range;
use std::panic::{AssertUnwindSafe, catch_unwind};

use hayro::hayro_syntax::content::UntypedIter;
use hayro::hayro_syntax::object::{MaybeRef, Object as HObject};
use lopdf::content::Operation;
use lopdf::{Dictionary, Object, Stream, StringFormat};

use super::{ContentError, try_push};

/// One operation and the bytes it occupies.
pub(crate) struct Instruction<'a> {
    pub(crate) operator: &'a [u8],
    /// The bytes of the operation, from the end of the previous one.
    pub(crate) span: Range<usize>,
    operands: Range<usize>,
}

/// The operations of a content stream as hayro reads them.
pub(crate) struct Instructions<'a> {
    pub(crate) data: &'a [u8],
    items: Vec<Instruction<'a>>,
    operands: Vec<HObject<'a>>,
}

impl<'a> Instructions<'a> {
    /// Tokenize `data`, refusing more than `max_operations` operations.
    pub(crate) fn parse(
        data: &'a [u8],
        max_operations: Option<usize>,
    ) -> Result<Self, ContentError> {
        catch_unwind(AssertUnwindSafe(|| Self::parse_inner(data, max_operations))).unwrap_or_else(
            |_| {
                Err(ContentError::Malformed(
                    "the content parser failed on this stream".to_owned(),
                ))
            },
        )
    }

    fn parse_inner(data: &'a [u8], max_operations: Option<usize>) -> Result<Self, ContentError> {
        let mut parsed = Self {
            data,
            items: Vec::new(),
            operands: Vec::new(),
        };
        let base = data.as_ptr() as usize;
        let offset_of = |slice: &[u8]| -> Option<usize> {
            let start = (slice.as_ptr() as usize).checked_sub(base)?;
            (start.checked_add(slice.len())? <= data.len()).then_some(start)
        };
        let mut previous_end = 0;
        let mut iter = UntypedIter::new(data);
        while let Some(instruction) = iter.next() {
            if max_operations.is_some_and(|limit| parsed.items.len() >= limit) {
                return Err(ContentError::Limit {
                    code: "content_operation_count",
                    message: format!(
                        "the content stream holds more than {} operations",
                        max_operations.unwrap_or_default()
                    ),
                });
            }
            // The operator's name borrows the stream unless it was written
            // with a `#` escape, whose decoded copy has no place in it.
            let name: &[u8] = instruction.operator;
            let start = offset_of(name)
                .filter(|&start| &data[start..start + name.len()] == name)
                .ok_or_else(|| {
                    ContentError::Malformed(
                        "a content operator is written with a # escape, which pylopdf cannot locate"
                            .to_owned(),
                    )
                })?;
            let operator = &data[start..start + name.len()];
            let mut end = start + operator.len();
            let first = parsed.operands.len();
            for operand in instruction.operands() {
                if let HObject::Stream(image) = operand {
                    // The inline image's data ends where `EI` begins.
                    let image_data = image.raw_data();
                    let image_start = offset_of(&image_data).ok_or_else(|| {
                        ContentError::Malformed(
                            "an inline image's data is not in the stream".to_owned(),
                        )
                    })?;
                    end = (image_start + image_data.len() + 2).min(data.len());
                }
                try_push(&mut parsed.operands, operand.clone(), "content operands")?;
            }
            if end < previous_end {
                return Err(ContentError::Malformed(
                    "content operations overlap".to_owned(),
                ));
            }
            try_push(
                &mut parsed.items,
                Instruction {
                    operator,
                    span: previous_end..end,
                    operands: first..parsed.operands.len(),
                },
                "content operations",
            )?;
            previous_end = end;
        }
        Ok(parsed)
    }

    pub(crate) fn len(&self) -> usize {
        self.items.len()
    }

    pub(crate) fn instruction(&self, index: usize) -> &Instruction<'a> {
        &self.items[index]
    }

    pub(crate) fn operands(&self, index: usize) -> &[HObject<'a>] {
        &self.operands[self.items[index].operands.clone()]
    }

    /// The bytes of one operation.
    pub(crate) fn bytes(&self, index: usize) -> &'a [u8] {
        &self.data[self.items[index].span.clone()]
    }

    /// The bytes after the last operation.
    pub(crate) fn tail(&self) -> &'a [u8] {
        let start = self.items.last().map_or(0, |item| item.span.end);
        &self.data[start..]
    }

    /// The operations as lopdf values, for the interpreter.
    pub(crate) fn to_operations(&self) -> Result<Vec<Operation>, ContentError> {
        let mut operations = Vec::new();
        operations
            .try_reserve_exact(self.items.len())
            .map_err(|error| ContentError::allocation("content operations", &error))?;
        for (index, item) in self.items.iter().enumerate() {
            let source = self.operands(index);
            let mut operands = Vec::new();
            operands
                .try_reserve_exact(source.len())
                .map_err(|error| ContentError::allocation("content operands", &error))?;
            for operand in source {
                operands.push(to_lopdf(operand)?);
            }
            operations.push(Operation {
                operator: String::from_utf8_lossy(item.operator).into_owned(),
                operands,
            });
        }
        Ok(operations)
    }
}

/// Whether a number was written as an integer.
pub(crate) fn is_integer(number: &hayro::hayro_syntax::object::Number) -> bool {
    let value = number.as_f64();
    if value.fract() != 0.0 || !value.is_finite() {
        return false;
    }
    // Display writes a real with its decimal point and an integer without.
    let text = number.to_string();
    !text.contains(['.', 'e', 'E', 'i', 'N'])
}

/// Convert a content operand to a lopdf value.
pub(crate) fn to_lopdf(object: &HObject<'_>) -> Result<Object, ContentError> {
    Ok(match object {
        HObject::Null(_) => Object::Null,
        HObject::Boolean(value) => Object::Boolean(*value),
        HObject::Number(number) => {
            if is_integer(number) {
                Object::Integer(number.as_i64())
            } else {
                Object::Real(number.as_f32())
            }
        }
        HObject::String(string) => Object::String(copy(string.as_bytes())?, StringFormat::Literal),
        HObject::Name(name) => Object::Name(copy(name)?),
        HObject::Array(array) => {
            let mut items = Vec::new();
            // hayro's typed iteration stops at an element that does not read.
            for item in array.iter::<HObject<'_>>() {
                try_push(&mut items, to_lopdf(&item)?, "content array")?;
            }
            Object::Array(items)
        }
        HObject::Dict(dict) => Object::Dictionary(dictionary(dict)?),
        HObject::Stream(stream) => {
            let dict = dictionary(stream.dict())?;
            Object::Stream(Stream::new(dict, copy(&stream.raw_data())?))
        }
    })
}

fn dictionary(dict: &hayro::hayro_syntax::object::Dict<'_>) -> Result<Dictionary, ContentError> {
    let mut out = Dictionary::new();
    for (key, value) in dict.entries() {
        let value = match value {
            MaybeRef::NotRef(value) => to_lopdf(&value)?,
            MaybeRef::Ref(reference) => {
                let (number, generation) = (reference.obj_number, reference.gen_number);
                match (u32::try_from(number), u16::try_from(generation)) {
                    (Ok(number), Ok(generation)) => Object::Reference((number, generation)),
                    _ => Object::Null,
                }
            }
        };
        out.set(copy(&key)?, value);
    }
    Ok(out)
}

fn copy(bytes: &[u8]) -> Result<Vec<u8>, ContentError> {
    let mut out = Vec::new();
    out.try_reserve_exact(bytes.len())
        .map_err(|error| ContentError::allocation("content operand", &error))?;
    out.extend_from_slice(bytes);
    Ok(out)
}
