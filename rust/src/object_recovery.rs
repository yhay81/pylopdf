//! Keep lopdf's loaded object graph describing the same document as the
//! original bytes that hayro reads.
//!
//! An unedited document renders and extracts from its original bytes, which
//! hayro resolves through the cross-reference data, while edits and saves
//! serialize lopdf's object graph. The two agree when lopdf loaded exactly the
//! object that each in-use cross-reference entry names. Malformed files break
//! that in three ways:
//!
//! - lopdf stores an uncompressed object under the number in its own header,
//!   even when the entry that locates it names another number. hayro reads
//!   such a number as null until it first reads the mismatched entry, and
//!   then rebuilds its whole table by scanning object headers, so what it
//!   resolves depends on what it read first.
//! - lopdf skips an object it cannot parse, such as a dictionary that spells a
//!   name value without its slash, while hayro reads the bare keyword as null.
//! - lopdf adds object-stream members that no entry lists, including members
//!   that a damaged stream decodes under arbitrary numbers, and raises
//!   `max_id` over them, while hayro reads those numbers as null.
//!
//! Loading re-parses each skipped uncompressed object with a small parser that
//! reads such a bare keyword as null, walks every reference reachable from the
//! trailer, and drops objects that no entry locates and nothing reaches. The
//! original-byte fast path stays only when that walk meets no object that
//! hayro could resolve differently. Any other document renders from its
//! serialized graph, as a document whose cross-reference data lopdf recovered
//! already does.

use std::collections::{HashSet, TryReserveError};
use std::str::FromStr;

use lopdf::xref::{Xref, XrefEntry};
use lopdf::{Dictionary, Document, Object, ObjectId, Stream, StringFormat};
use pyo3::prelude::*;

use crate::document::PdfError;

/// lopdf's own nesting bound, so recovery never builds an object deeper than
/// one that lopdf could have loaded.
const MAX_NESTING_DEPTH: usize = 100;

/// Keywords that end or frame an object, so they never stand for a value.
const STRUCTURAL_KEYWORDS: [&[u8]; 8] = [
    b"R",
    b"obj",
    b"endobj",
    b"stream",
    b"endstream",
    b"xref",
    b"trailer",
    b"startxref",
];

/// Reconcile the loaded graph with its cross-reference data and report
/// whether hayro may read the original bytes instead of the serialized graph.
///
/// A graph that matches the in-use entries exactly returns true at once. Any
/// other graph first receives the skipped uncompressed objects that parse,
/// then loses the objects that no entry locates unless the trailer reaches
/// them. `input` must be the unencrypted bytes lopdf loaded, with
/// cross-reference data that lopdf did not have to recover. Allocation refusal
/// raises `PdfError`, which fails the load.
pub(crate) fn reconcile_loaded_objects(doc: &mut Document, input: &[u8]) -> PyResult<bool> {
    if matches_cross_reference(doc) {
        return Ok(true);
    }
    let recovered = recover_skipped_objects(doc, input).map_err(allocation_error)?;
    let mut recovered_ids = HashSet::new();
    recovered_ids
        .try_reserve(recovered.len())
        .map_err(allocation_error)?;
    for (id, object) in recovered {
        recovered_ids.insert(id);
        doc.objects.insert(id, object);
    }
    let audit = audit_references(doc, &recovered_ids).map_err(allocation_error)?;
    // Objects that no entry locates and nothing reaches are debris, such as
    // members that a damaged object stream decoded under arbitrary numbers.
    let table = &doc.reference_table;
    doc.objects
        .retain(|&id, _| located(table, id) || audit.reachable.contains(&id));
    // lopdf raised `max_id` over every object it loaded, debris included.
    // Lower it again when no reachable reference names a higher number, so new
    // objects cannot alias one and serialization stays proportional to the
    // document rather than to an arbitrary number.
    let highest_object = doc.objects.keys().next_back().map_or(0, |id| id.0);
    let ceiling = doc.reference_table.max_id().max(highest_object);
    if audit.highest_reference <= ceiling {
        doc.max_id = doc.max_id.min(ceiling);
    }
    Ok(!audit.divergent)
}

fn allocation_error(error: TryReserveError) -> PyErr {
    PdfError::new_err(format!(
        "failed to allocate PDF object reconciliation: {error}"
    ))
}

/// Whether the loaded objects are exactly the in-use cross-reference entries.
///
/// Both maps iterate in object-number order, so one merge pass suffices.
/// lopdf records only in-use entries, never free ones, from classic tables
/// and cross-reference streams alike.
fn matches_cross_reference(doc: &Document) -> bool {
    let mut objects = doc.objects.keys();
    for (&number, entry) in &doc.reference_table.entries {
        let generation = match *entry {
            XrefEntry::Normal { generation, .. } => generation,
            XrefEntry::Compressed { .. } => 0,
            XrefEntry::Free | XrefEntry::UnusableFree => continue,
        };
        if objects.next() != Some(&(number, generation)) {
            return false;
        }
    }
    objects.next().is_none()
}

/// Whether an in-use cross-reference entry names this object ID.
fn located(table: &Xref, (number, generation): ObjectId) -> bool {
    match table.entries.get(&number) {
        Some(XrefEntry::Normal {
            generation: entry, ..
        }) => *entry == generation,
        Some(XrefEntry::Compressed { .. }) => generation == 0,
        _ => false,
    }
}

/// What the references reachable from the trailer resolve to.
struct ReferenceAudit {
    /// Loaded objects that the trailer reaches.
    reachable: HashSet<ObjectId>,
    /// Whether hayro could resolve a reachable reference to something else.
    divergent: bool,
    /// Highest object number that a reachable reference names.
    highest_reference: u32,
}

/// Walk every reference reachable from the trailer, never stream content.
///
/// hayro resolves a reference like the graph does only when an entry locates
/// the loaded object and both parsed the same bytes. A reachable reference
/// diverges when it names an object that no entry locates, which hayro reads
/// as null, a recovered object, which hayro parsed on its own, or an in-use
/// entry that lopdf could not load, which hayro parses leniently or answers by
/// rebuilding its whole table.
fn audit_references(
    doc: &Document,
    recovered: &HashSet<ObjectId>,
) -> Result<ReferenceAudit, TryReserveError> {
    let mut audit = ReferenceAudit {
        reachable: HashSet::new(),
        divergent: false,
        highest_reference: 0,
    };
    // Only loaded objects are recorded, so this covers every insertion.
    audit.reachable.try_reserve(doc.objects.len())?;
    let mut pending: Vec<&Object> = Vec::new();
    pending.try_reserve(doc.trailer.len())?;
    pending.extend(doc.trailer.iter().map(|(_, value)| value));
    while let Some(object) = pending.pop() {
        match object {
            Object::Reference(id) => {
                audit.highest_reference = audit.highest_reference.max(id.0);
                match doc.objects.get(id) {
                    Some(target) => {
                        if !located(&doc.reference_table, *id) || recovered.contains(id) {
                            audit.divergent = true;
                        }
                        if audit.reachable.insert(*id) {
                            pending.try_reserve(1)?;
                            pending.push(target);
                        }
                    }
                    None => {
                        if located(&doc.reference_table, *id) {
                            audit.divergent = true;
                        }
                    }
                }
            }
            Object::Array(items) => {
                pending.try_reserve(items.len())?;
                pending.extend(items);
            }
            Object::Dictionary(dict) => {
                pending.try_reserve(dict.len())?;
                pending.extend(dict.iter().map(|(_, value)| value));
            }
            Object::Stream(stream) => {
                pending.try_reserve(stream.dict.len())?;
                pending.extend(stream.dict.iter().map(|(_, value)| value));
            }
            _ => {}
        }
    }
    Ok(audit)
}

/// Parse every skipped uncompressed object whose header matches its entry.
///
/// Each distinct offset is examined once and parsed no further than the next
/// one, the cross-reference section, or the end of input, as lopdf bounds its
/// own stream-length recovery, so the pass stays linear in input size even
/// when many entries share an offset.
fn recover_skipped_objects(
    doc: &Document,
    input: &[u8],
) -> Result<Vec<(ObjectId, Object)>, TryReserveError> {
    // lopdf discards bytes before the header, and every offset it records,
    // including `xref_start`, counts from there.
    let header = input
        .windows(5)
        .position(|window| window == b"%PDF-")
        .unwrap_or(0);
    let buffer = &input[header..];
    let mut offsets = Vec::new();
    offsets.try_reserve_exact(doc.reference_table.entries.len())?;
    for entry in doc.reference_table.entries.values() {
        if let XrefEntry::Normal { offset, .. } = *entry {
            offsets.push(offset as usize);
        }
    }
    offsets.sort_unstable();
    offsets.dedup();

    let mut recovered = Vec::new();
    for (index, &start) in offsets.iter().enumerate() {
        if start >= buffer.len() {
            break;
        }
        let section = (doc.xref_start > start).then_some(doc.xref_start);
        let end = offsets
            .get(index + 1)
            .copied()
            .into_iter()
            .chain(section)
            .min()
            .unwrap_or(buffer.len())
            .min(buffer.len());
        let mut parser = Parser {
            data: &buffer[..end],
            pos: start,
        };
        let Some(id) = parser.header() else {
            continue;
        };
        let skipped = matches!(
            doc.reference_table.entries.get(&id.0),
            Some(XrefEntry::Normal { offset, generation })
                if *offset as usize == start && *generation == id.1
        ) && !doc.objects.contains_key(&id);
        if !skipped {
            continue;
        }
        match parser.object(doc) {
            Ok(object) => {
                recovered.try_reserve(1)?;
                recovered.push((id, object));
            }
            Err(Failure::Syntax) => {}
            Err(Failure::Allocation(error)) => return Err(error),
        }
    }
    Ok(recovered)
}

/// Why an object could not be recovered.
enum Failure {
    /// The bytes are not an object that this parser recovers.
    Syntax,
    /// Holding the object would exceed available memory.
    Allocation(TryReserveError),
}

impl From<TryReserveError> for Failure {
    fn from(error: TryReserveError) -> Self {
        Self::Allocation(error)
    }
}

/// An array or dictionary still waiting for its closing delimiter.
enum Frame {
    Array(Vec<Object>),
    /// A dictionary and the key whose value comes next, if any.
    Dictionary(Dictionary, Option<Vec<u8>>),
}

fn is_whitespace(byte: u8) -> bool {
    matches!(byte, b'\0' | b'\t' | b'\n' | b'\x0c' | b'\r' | b' ')
}

fn is_delimiter(byte: u8) -> bool {
    matches!(
        byte,
        b'(' | b')' | b'<' | b'>' | b'[' | b']' | b'{' | b'}' | b'/' | b'%'
    )
}

fn is_regular(byte: u8) -> bool {
    !is_whitespace(byte) && !is_delimiter(byte)
}

fn hex_digit(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

/// Parse an unsigned decimal token, as object and generation numbers are.
fn unsigned<T: FromStr>(token: &[u8]) -> Option<T> {
    if token.is_empty() || !token.iter().all(u8::is_ascii_digit) {
        return None;
    }
    std::str::from_utf8(token).ok()?.parse().ok()
}

/// Read one regular token that is not a reference as an object.
///
/// A token that is not a number, boolean, null, or structural keyword is a
/// bare keyword, such as a name written without its slash, and reads as null
/// as hayro reads it. A malformed number fails instead of becoming null.
fn scalar(token: &[u8]) -> Result<Object, Failure> {
    match token {
        b"true" => return Ok(Object::Boolean(true)),
        b"false" => return Ok(Object::Boolean(false)),
        b"null" => return Ok(Object::Null),
        _ => {}
    }
    if STRUCTURAL_KEYWORDS.contains(&token) {
        return Err(Failure::Syntax);
    }
    if !matches!(token.first(), Some(b'0'..=b'9' | b'+' | b'-' | b'.')) {
        return Ok(Object::Null);
    }
    let digits = token
        .strip_prefix(b"+")
        .or_else(|| token.strip_prefix(b"-"));
    let digits = digits.unwrap_or(token);
    let points = digits.iter().filter(|&&byte| byte == b'.').count();
    if points > 1
        || !digits.iter().any(u8::is_ascii_digit)
        || !digits
            .iter()
            .all(|&byte| byte == b'.' || byte.is_ascii_digit())
    {
        return Err(Failure::Syntax);
    }
    let text = std::str::from_utf8(token).map_err(|_| Failure::Syntax)?;
    if points == 0 {
        text.parse()
            .map(Object::Integer)
            .map_err(|_| Failure::Syntax)
    } else {
        text.parse::<f32>()
            .ok()
            .filter(|value| value.is_finite())
            .map(Object::Real)
            .ok_or(Failure::Syntax)
    }
}

/// A forward reader over one object's bytes.
struct Parser<'a> {
    /// Input up to the object's boundary.
    data: &'a [u8],
    pos: usize,
}

impl<'a> Parser<'a> {
    /// Skip white space and comments.
    fn skip_whitespace(&mut self) {
        while let Some(&byte) = self.data.get(self.pos) {
            if is_whitespace(byte) {
                self.pos += 1;
            } else if byte == b'%' {
                while self
                    .data
                    .get(self.pos)
                    .is_some_and(|&byte| byte != b'\r' && byte != b'\n')
                {
                    self.pos += 1;
                }
            } else {
                break;
            }
        }
    }

    /// Read a maximal run of regular characters, which may be empty.
    fn regular_token(&mut self) -> &'a [u8] {
        let data = self.data;
        let start = self.pos;
        while data.get(self.pos).copied().is_some_and(is_regular) {
            self.pos += 1;
        }
        &data[start..self.pos]
    }

    /// Read an `N G obj` header.
    fn header(&mut self) -> Option<ObjectId> {
        self.skip_whitespace();
        let number = unsigned(self.regular_token())?;
        self.skip_whitespace();
        let generation = unsigned(self.regular_token())?;
        self.skip_whitespace();
        (self.regular_token() == b"obj").then_some((number, generation))
    }

    /// Finish `number G R` when the following tokens complete a reference;
    /// otherwise leave the position unchanged.
    fn reference_tail(&mut self, number: u32) -> Option<ObjectId> {
        let start = self.pos;
        self.skip_whitespace();
        let generation = unsigned(self.regular_token());
        self.skip_whitespace();
        if let Some(generation) = generation
            && self.regular_token() == b"R"
        {
            return Some((number, generation));
        }
        self.pos = start;
        None
    }

    /// Read the body after a header, including stream data.
    fn object(&mut self, doc: &Document) -> Result<Object, Failure> {
        let value = self.value()?;
        self.skip_whitespace();
        match self.regular_token() {
            b"stream" => {
                let Object::Dictionary(dict) = value else {
                    return Err(Failure::Syntax);
                };
                let content = self.stream_content(&dict, doc)?;
                Ok(Object::Stream(Stream::new(dict, content)))
            }
            // Like lopdf, accept an object whose `endobj` is missing when the
            // next object or the cross-reference section follows.
            b"endobj" => Ok(value),
            b"" if self.pos == self.data.len() => Ok(value),
            _ => Err(Failure::Syntax),
        }
    }

    /// Read one direct object without recursion.
    fn value(&mut self) -> Result<Object, Failure> {
        let mut stack: Vec<Frame> = Vec::new();
        loop {
            self.skip_whitespace();
            let Some(&byte) = self.data.get(self.pos) else {
                return Err(Failure::Syntax);
            };
            let next = self.data.get(self.pos + 1).copied();
            let value = match byte {
                b'[' => {
                    self.pos += 1;
                    open(&mut stack, Frame::Array(Vec::new()))?;
                    continue;
                }
                b'<' if next == Some(b'<') => {
                    self.pos += 2;
                    open(&mut stack, Frame::Dictionary(Dictionary::new(), None))?;
                    continue;
                }
                b']' => {
                    self.pos += 1;
                    match stack.pop() {
                        Some(Frame::Array(items)) => Object::Array(items),
                        _ => return Err(Failure::Syntax),
                    }
                }
                b'>' if next == Some(b'>') => {
                    self.pos += 2;
                    match stack.pop() {
                        Some(Frame::Dictionary(dict, None)) => Object::Dictionary(dict),
                        _ => return Err(Failure::Syntax),
                    }
                }
                b'/' => {
                    self.pos += 1;
                    let name = self.name()?;
                    if let Some(Frame::Dictionary(_, key @ None)) = stack.last_mut() {
                        *key = Some(name);
                        continue;
                    }
                    Object::Name(name)
                }
                b'(' => Object::String(self.literal_string()?, StringFormat::Literal),
                b'<' => Object::String(self.hexadecimal_string()?, StringFormat::Hexadecimal),
                _ if is_regular(byte) => {
                    let token = self.regular_token();
                    match unsigned(token).and_then(|number| self.reference_tail(number)) {
                        Some(id) => Object::Reference(id),
                        None => scalar(token)?,
                    }
                }
                _ => return Err(Failure::Syntax),
            };
            match stack.last_mut() {
                None => return Ok(value),
                Some(Frame::Array(items)) => {
                    items.try_reserve(1)?;
                    items.push(value);
                }
                Some(Frame::Dictionary(dict, key)) => match key.take() {
                    Some(key) => dict.set(key, value),
                    None => return Err(Failure::Syntax),
                },
            }
        }
    }

    /// Read a name after its slash, decoding `#xx` escapes.
    fn name(&mut self) -> Result<Vec<u8>, Failure> {
        let token = self.regular_token();
        let mut name = Vec::new();
        name.try_reserve_exact(token.len())?;
        let mut index = 0;
        while let Some(&byte) = token.get(index) {
            let escaped = (byte == b'#')
                .then(|| {
                    let high = hex_digit(*token.get(index + 1)?)?;
                    let low = hex_digit(*token.get(index + 2)?)?;
                    Some((high << 4) | low)
                })
                .flatten();
            match escaped {
                Some(decoded) => {
                    name.push(decoded);
                    index += 3;
                }
                None => {
                    name.push(byte);
                    index += 1;
                }
            }
        }
        Ok(name)
    }

    /// Read a literal string after its opening parenthesis.
    fn literal_string(&mut self) -> Result<Vec<u8>, Failure> {
        self.pos += 1;
        let mut text = Vec::new();
        let mut depth = 1usize;
        loop {
            let Some(&byte) = self.data.get(self.pos) else {
                return Err(Failure::Syntax);
            };
            self.pos += 1;
            let decoded = match byte {
                b'\\' => {
                    let Some(&escaped) = self.data.get(self.pos) else {
                        return Err(Failure::Syntax);
                    };
                    self.pos += 1;
                    match escaped {
                        b'n' => b'\n',
                        b'r' => b'\r',
                        b't' => b'\t',
                        b'b' => b'\x08',
                        b'f' => b'\x0c',
                        b'0'..=b'7' => {
                            let mut code = u32::from(escaped - b'0');
                            for _ in 0..2 {
                                match self.data.get(self.pos) {
                                    Some(&digit @ b'0'..=b'7') => {
                                        code = code * 8 + u32::from(digit - b'0');
                                        self.pos += 1;
                                    }
                                    _ => break,
                                }
                            }
                            // ISO 32000-2 §7.3.4.2 ignores high-order overflow.
                            (code & 0xff) as u8
                        }
                        // A backslash before an end-of-line continues the line.
                        b'\r' => {
                            if self.data.get(self.pos) == Some(&b'\n') {
                                self.pos += 1;
                            }
                            continue;
                        }
                        b'\n' => continue,
                        // Includes `\(`, `\)`, and `\\`; other escapes drop
                        // the backslash.
                        _ => escaped,
                    }
                }
                b'(' => {
                    depth += 1;
                    byte
                }
                b')' => {
                    depth -= 1;
                    if depth == 0 {
                        return Ok(text);
                    }
                    byte
                }
                // An unescaped end-of-line reads as a single line feed.
                b'\r' => {
                    if self.data.get(self.pos) == Some(&b'\n') {
                        self.pos += 1;
                    }
                    b'\n'
                }
                _ => byte,
            };
            text.try_reserve(1)?;
            text.push(decoded);
        }
    }

    /// Read a hexadecimal string after its opening angle bracket.
    fn hexadecimal_string(&mut self) -> Result<Vec<u8>, Failure> {
        self.pos += 1;
        let mut bytes = Vec::new();
        let mut high = None;
        loop {
            let Some(&byte) = self.data.get(self.pos) else {
                return Err(Failure::Syntax);
            };
            self.pos += 1;
            if byte == b'>' {
                break;
            }
            if is_whitespace(byte) {
                continue;
            }
            let digit = hex_digit(byte).ok_or(Failure::Syntax)?;
            match high.take() {
                Some(high) => {
                    bytes.try_reserve(1)?;
                    bytes.push((high << 4) | digit);
                }
                None => high = Some(digit),
            }
        }
        // An odd final digit is followed by an implied zero.
        if let Some(high) = high {
            bytes.try_reserve(1)?;
            bytes.push(high << 4);
        }
        Ok(bytes)
    }

    /// Read stream data after the `stream` keyword.
    ///
    /// Use `/Length` when `endstream` follows that many bytes; otherwise,
    /// like lopdf, accept only a sole end-of-line-framed `endstream` that
    /// `endobj` follows before the object's boundary.
    fn stream_content(&mut self, dict: &Dictionary, doc: &Document) -> Result<Vec<u8>, Failure> {
        while matches!(self.data.get(self.pos), Some(b' ' | b'\t')) {
            self.pos += 1;
        }
        match self.data.get(self.pos) {
            Some(b'\r') if self.data.get(self.pos + 1) == Some(&b'\n') => self.pos += 2,
            Some(b'\r' | b'\n') => self.pos += 1,
            _ => return Err(Failure::Syntax),
        }
        let start = self.pos;
        let end = declared_length(dict, doc)
            .and_then(|length| start.checked_add(length))
            .filter(|&end| end <= self.data.len() && self.endstream_follows(end))
            .or_else(|| sole_endstream(&self.data[start..]).map(|length| start + length))
            .ok_or(Failure::Syntax)?;
        let mut content = Vec::new();
        content.try_reserve_exact(end - start)?;
        content.extend_from_slice(&self.data[start..end]);
        Ok(content)
    }

    /// Whether `endstream` follows `position` after optional white space.
    fn endstream_follows(&self, position: usize) -> bool {
        let mut parser = Parser {
            data: self.data,
            pos: position,
        };
        while parser
            .data
            .get(parser.pos)
            .copied()
            .is_some_and(is_whitespace)
        {
            parser.pos += 1;
        }
        parser.regular_token() == b"endstream"
    }
}

/// Push a container, refusing nesting deeper than lopdf accepts.
fn open(stack: &mut Vec<Frame>, frame: Frame) -> Result<(), Failure> {
    if stack.len() >= MAX_NESTING_DEPTH {
        return Err(Failure::Syntax);
    }
    stack.try_reserve(1)?;
    stack.push(frame);
    Ok(())
}

/// Resolve a direct or indirect, non-negative integral `/Length`.
fn declared_length(dict: &Dictionary, doc: &Document) -> Option<usize> {
    let value = match dict.get(b"Length").ok()? {
        Object::Reference(id) => doc.objects.get(id)?,
        value => value,
    };
    match *value {
        Object::Integer(length) => usize::try_from(length).ok(),
        // lopdf also accepts an integral real, which some producers write.
        Object::Real(length) if length >= 0.0 && length.fract() == 0.0 => {
            usize::try_from(length as u64).ok()
        }
        _ => None,
    }
}

/// Return the data length before the sole end-of-line-framed `endstream`
/// that `endobj` follows, as lopdf recovers a wrong `/Length`.
fn sole_endstream(data: &[u8]) -> Option<usize> {
    const ENDSTREAM: &[u8] = b"endstream";
    let mut found = None;
    for (position, window) in data.windows(ENDSTREAM.len()).enumerate() {
        if window != ENDSTREAM {
            continue;
        }
        let before = &data[..position];
        let length = if before.ends_with(b"\r\n") {
            position - 2
        } else if before.ends_with(b"\n") || before.ends_with(b"\r") {
            position - 1
        } else {
            continue;
        };
        let mut parser = Parser {
            data,
            pos: position + ENDSTREAM.len(),
        };
        parser.skip_whitespace();
        if parser.regular_token() != b"endobj" {
            continue;
        }
        if found.is_some() {
            return None;
        }
        found = Some(length);
    }
    found
}
