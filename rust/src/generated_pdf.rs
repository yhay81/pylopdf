//! Load the small PDFs that krilla generates for text runs and form appearances.

use std::collections::BTreeMap;
use std::str::FromStr;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

use lopdf::xref::{Xref, XrefEntry, XrefType};
use lopdf::{Dictionary, Document, Object, ObjectId, Stream, StringFormat};

/// Nesting deeper than pdf-writer writes declines, leaving lopdf's own limit
/// to decide.
const MAX_DEPTH: usize = 32;

static SEQUENTIAL_LOADS: AtomicUsize = AtomicUsize::new(0);
static FALLBACK_LOADS: AtomicUsize = AtomicUsize::new(0);
static MISMATCHED_LOADS: AtomicUsize = AtomicUsize::new(0);
static VERIFY_LOADS: AtomicBool = AtomicBool::new(false);

/// Load a PDF that krilla generated, for import as a Form XObject.
///
/// Native builds enable lopdf's `rayon` feature, so `Document::load_mem`
/// parses objects with `par_iter` on the global pool: a calling thread outside
/// the pool injects the job, wakes a sleeping worker, and blocks until the pool
/// finishes. A generated document holds about a dozen objects, so the hand-off
/// costs more than parsing them. pdf-writer, which krilla writes through, emits
/// one classic cross-reference table and compact direct objects; [`parse`]
/// reads exactly that subset on the calling thread and builds the document
/// lopdf would, allocating the same objects. Other input falls back to
/// `Document::load_mem`, as does every Emscripten load, where lopdf already
/// parses on the calling thread. The Form-import bounds that follow apply to
/// the returned document unchanged.
pub(crate) fn load(data: &[u8]) -> lopdf::Result<Document> {
    if cfg!(target_os = "emscripten") {
        return Document::load_mem(data);
    }
    let Some(document) = parse(data) else {
        FALLBACK_LOADS.fetch_add(1, Ordering::Relaxed);
        return Document::load_mem(data);
    };
    SEQUENTIAL_LOADS.fetch_add(1, Ordering::Relaxed);
    if VERIFY_LOADS.load(Ordering::Relaxed) && !matches_lopdf(&document, data) {
        MISMATCHED_LOADS.fetch_add(1, Ordering::Relaxed);
    }
    Ok(document)
}

/// Return `(sequential, fallback, mismatched)` load counts since the process
/// started; mismatches are counted only while verification is enabled.
pub(crate) fn load_counts() -> (usize, usize, usize) {
    (
        SEQUENTIAL_LOADS.load(Ordering::Relaxed),
        FALLBACK_LOADS.load(Ordering::Relaxed),
        MISMATCHED_LOADS.load(Ordering::Relaxed),
    )
}

/// Compare every later sequential load with lopdf's own parse of the input.
pub(crate) fn set_verification(enabled: bool) {
    VERIFY_LOADS.store(enabled, Ordering::Relaxed);
}

/// Parse `data` sequentially and report whether lopdf reads the same
/// document, or `None` when the subset parser declines the input.
pub(crate) fn compare_with_lopdf(data: &[u8]) -> Option<bool> {
    parse(data).map(|document| matches_lopdf(&document, data))
}

fn matches_lopdf(document: &Document, data: &[u8]) -> bool {
    Document::load_mem(data).is_ok_and(|expected| same_document(document, &expected))
}

/// Read `data` when it stays inside the pdf-writer subset, or decline.
///
/// This mirrors lopdf 0.45's lenient `Reader::read` for that subset: the
/// header version and binary mark, the `startxref` search from the end, one
/// cross-reference section, the `/Size` correction, and one indirect object
/// per in-use entry. Comments, other cross-reference forms, `/Prev` chains,
/// encryption, object streams, indirect stream lengths, and anything lopdf
/// would repair or skip decline instead.
fn parse(data: &[u8]) -> Option<Document> {
    if !data.starts_with(b"%PDF-") {
        return None;
    }
    let version = header_version(data)?;
    let xref_start = xref_start(data)?;
    if !data.get(xref_start..)?.starts_with(b"xref") {
        return None;
    }
    let (mut xref, trailer) = xref_and_trailer(data, xref_start)?;
    if [b"Prev".as_slice(), b"XRefStm", b"Encrypt"]
        .iter()
        .any(|key| trailer.has(key))
    {
        return None;
    }
    let entry_count = xref.max_id().checked_add(1)?;
    if xref.size != entry_count {
        xref.size = entry_count;
    }

    let mut objects = BTreeMap::new();
    for entry in xref.entries.values() {
        let XrefEntry::Normal { offset, .. } = *entry else {
            return None;
        };
        let (id, object) = indirect_object(data, usize::try_from(offset).ok()?)?;
        if let Object::Stream(stream) = &object
            && stream.dict.has_type(b"ObjStm")
        {
            return None;
        }
        if objects.insert(id, object).is_some() {
            return None;
        }
    }

    let mut document = Document::new();
    document.version = version;
    if let Some(mark) = binary_mark(data) {
        document.binary_mark = mark;
    }
    document.xref_start = xref_start;
    document.max_id = xref.size - 1;
    if let Some((&(highest, _), _)) = objects.last_key_value() {
        document.max_id = document.max_id.max(highest);
    }
    document.trailer = trailer;
    document.reference_table = xref;
    document.objects = objects;
    Some(document)
}

fn line_end(data: &[u8]) -> Option<usize> {
    data.iter().position(|&byte| matches!(byte, b'\r' | b'\n'))
}

/// Read the version digits after `%PDF-`; lopdf requires the line to end.
fn header_version(data: &[u8]) -> Option<String> {
    let rest = &data[5..];
    line_end(rest)?;
    let length = rest
        .iter()
        .take_while(|&&byte| byte.is_ascii_digit() || byte == b'.')
        .count();
    String::from_utf8(rest[..length].to_vec()).ok()
}

/// Read a second-line `%` comment of high bytes, as lopdf does.
fn binary_mark(data: &[u8]) -> Option<Vec<u8>> {
    let line = data.iter().position(|&byte| byte == b'\n')? + 1;
    let rest = data.get(line..)?.strip_prefix(b"%")?;
    let mark = &rest[..line_end(rest)?];
    mark.iter().all(|&byte| byte >= 128).then(|| mark.to_vec())
}

/// Find `startxref` before the last `%%EOF` in the final 512 bytes.
fn xref_start(data: &[u8]) -> Option<usize> {
    let eof = rfind(data, b"%%EOF", data.len() - data.len().min(512))?;
    if eof <= 25 {
        return None;
    }
    let keyword = rfind(&data[..eof], b"startxref", eof - 25)?;
    let mut parser = Parser::at(data, keyword + b"startxref".len());
    parser.skip(b" ");
    parser.eol()?;
    parser.skip_spaces();
    let start = parser.integer()?;
    parser.skip_spaces();
    parser.eol()?;
    if !parser.starts_with(b"%%EOF") {
        return None;
    }
    // lopdf converts with `as` and rejects offsets past the input.
    let start = start as usize;
    (start <= data.len()).then_some(start)
}

fn rfind(data: &[u8], pattern: &[u8], start: usize) -> Option<usize> {
    data.get(start..)?
        .windows(pattern.len())
        .rposition(|window| window == pattern)
        .map(|position| start + position)
}

/// Read one cross-reference section and its trailer at `offset`.
fn xref_and_trailer(data: &[u8], offset: usize) -> Option<(Xref, Dictionary)> {
    let mut parser = Parser::at(data, offset + b"xref".len());
    parser.skip(b" ");
    parser.eol()?;
    let first: usize = parser.unsigned()?;
    parser.expect(b" ")?;
    let _count: u32 = parser.unsigned()?;
    parser.skip(b" ");
    parser.eol()?;

    let mut xref = Xref::new(0, XrefType::CrossReferenceTable);
    let mut index = 0usize;
    while let Some((offset, generation, in_use)) = parser.xref_entry() {
        if in_use {
            let id = u32::try_from(first.checked_add(index)?).ok()?;
            let generation = u16::try_from(generation).ok()?;
            xref.insert(id, XrefEntry::Normal { offset, generation });
        }
        index += 1;
    }
    parser.space()?;
    parser.expect(b"trailer")?;
    parser.space()?;
    if !parser.starts_with(b"<<") {
        return None;
    }
    let trailer = parser.dictionary(MAX_DEPTH)?;
    // lopdf converts with `as`; reject what it would wrap.
    xref.size = u32::try_from(trailer.get(b"Size").ok()?.as_i64().ok()?).ok()?;
    Some((xref, trailer))
}

/// Read the indirect object at `offset`.
fn indirect_object(data: &[u8], offset: usize) -> Option<(ObjectId, Object)> {
    if offset > data.len() {
        return None;
    }
    let mut parser = Parser::at(data, offset);
    parser.space()?;
    let number: u32 = parser.unsigned()?;
    parser.space()?;
    let generation: u16 = parser.unsigned()?;
    parser.space()?;
    parser.expect(b"obj")?;
    parser.space()?;
    let object = if parser.starts_with(b"<<") {
        let dict = parser.dictionary(MAX_DEPTH)?;
        parser.space()?;
        if parser.starts_with(b"stream") {
            parser.stream(dict)?
        } else {
            Object::Dictionary(dict)
        }
    } else {
        parser.object(MAX_DEPTH)?
    };
    parser.space()?;
    parser
        .starts_with(b"endobj")
        .then_some(((number, generation), object))
}

fn is_whitespace(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\n' | b'\r' | b'\0' | b'\x0C')
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

fn hex_value(byte: u8) -> Option<u8> {
    char::from(byte)
        .to_digit(16)
        .and_then(|value| u8::try_from(value).ok())
}

/// A cursor over lopdf's object grammar that declines whatever lopdf would
/// read differently: comments, tokens not ending at a delimiter or
/// whitespace, and malformed input lopdf would skip or repair.
struct Parser<'a> {
    data: &'a [u8],
    pos: usize,
}

impl<'a> Parser<'a> {
    fn at(data: &'a [u8], pos: usize) -> Self {
        Self { data, pos }
    }

    fn peek(&self) -> Option<u8> {
        self.data.get(self.pos).copied()
    }

    fn rest(&self) -> &'a [u8] {
        self.data.get(self.pos..).unwrap_or_default()
    }

    fn starts_with(&self, token: &[u8]) -> bool {
        self.rest().starts_with(token)
    }

    fn expect(&mut self, token: &[u8]) -> Option<()> {
        self.starts_with(token).then(|| self.pos += token.len())
    }

    fn skip(&mut self, token: &[u8]) {
        let _ = self.expect(token);
    }

    fn skip_spaces(&mut self) {
        while self.peek() == Some(b' ') {
            self.pos += 1;
        }
    }

    fn eol(&mut self) -> Option<()> {
        self.expect(b"\r\n")
            .or_else(|| self.expect(b"\n"))
            .or_else(|| self.expect(b"\r"))
    }

    /// Skip whitespace; lopdf would also skip a comment, so one declines.
    fn space(&mut self) -> Option<()> {
        while let Some(byte) = self.peek() {
            if byte == b'%' {
                return None;
            }
            if !is_whitespace(byte) {
                break;
            }
            self.pos += 1;
        }
        Some(())
    }

    fn at_token_end(&self) -> bool {
        self.peek().is_none_or(|byte| !is_regular(byte))
    }

    fn digits(&self, start: usize) -> usize {
        self.data
            .get(start..)
            .unwrap_or_default()
            .iter()
            .take_while(|byte| byte.is_ascii_digit())
            .count()
    }

    fn unsigned<T: FromStr>(&mut self) -> Option<T> {
        let length = self.digits(self.pos);
        if length == 0 {
            return None;
        }
        let text = std::str::from_utf8(&self.data[self.pos..self.pos + length]).ok()?;
        let value = text.parse().ok()?;
        self.pos += length;
        Some(value)
    }

    fn integer(&mut self) -> Option<i64> {
        let sign = usize::from(matches!(self.peek(), Some(b'+' | b'-')));
        let length = self.digits(self.pos + sign);
        if length == 0 {
            return None;
        }
        let end = self.pos + sign + length;
        let value = std::str::from_utf8(&self.data[self.pos..end])
            .ok()?
            .parse()
            .ok()?;
        self.pos = end;
        Some(value)
    }

    /// Read `offset generation n|f` and lopdf's lenient entry terminators.
    fn xref_entry(&mut self) -> Option<(u32, u32, bool)> {
        let start = self.pos;
        let entry = (|| {
            let offset = self.unsigned()?;
            self.expect(b" ")?;
            let generation = self.unsigned()?;
            self.expect(b" ")?;
            let in_use = match self.peek()? {
                b'n' => true,
                b'f' => false,
                _ => return None,
            };
            self.pos += 1;
            [b" \r".as_slice(), b" \n", b"\r\n", b"\n", b"\r"]
                .iter()
                .find_map(|terminator| self.expect(terminator))?;
            Some((offset, generation, in_use))
        })();
        if entry.is_none() {
            self.pos = start;
        }
        entry
    }

    /// Read one direct object and the whitespace after it.
    fn object(&mut self, depth: usize) -> Option<Object> {
        let object = match self.peek()? {
            b'/' => Object::Name(self.name()?),
            b'(' => Object::String(self.literal_string()?, StringFormat::Literal),
            b'<' if self.starts_with(b"<<") => Object::Dictionary(self.dictionary(depth)?),
            b'<' => Object::String(self.hex_string()?, StringFormat::Hexadecimal),
            b'[' => Object::Array(self.array(depth)?),
            b'n' => {
                self.keyword(b"null")?;
                Object::Null
            }
            b't' => {
                self.keyword(b"true")?;
                Object::Boolean(true)
            }
            b'f' => {
                self.keyword(b"false")?;
                Object::Boolean(false)
            }
            b'+' | b'-' | b'.' | b'0'..=b'9' => self.number()?,
            _ => return None,
        };
        self.space()?;
        Some(object)
    }

    fn keyword(&mut self, token: &[u8]) -> Option<()> {
        self.expect(token)?;
        self.at_token_end().then_some(())
    }

    /// Read a reference, real, or integer, tried in lopdf's order.
    fn number(&mut self) -> Option<Object> {
        let start = self.pos;
        let sign = usize::from(matches!(self.peek(), Some(b'+' | b'-')));
        let whole = self.digits(start + sign);
        let mut end = start + sign + whole;
        if self.data.get(end) == Some(&b'.') {
            let fraction = self.digits(end + 1);
            if whole == 0 && fraction == 0 {
                return None;
            }
            end += 1 + fraction;
            self.pos = end;
            if !self.at_token_end() {
                return None;
            }
            let text = std::str::from_utf8(&self.data[start..end]).ok()?;
            return f32::from_str(text).ok().map(Object::Real);
        }
        if whole == 0 {
            return None;
        }
        if sign == 0
            && let Some((id, after)) = self.reference(start, end)?
        {
            self.pos = after;
            return Some(Object::Reference(id));
        }
        self.pos = end;
        if !self.at_token_end() {
            return None;
        }
        let text = std::str::from_utf8(&self.data[start..end]).ok()?;
        i64::from_str(text).ok().map(Object::Integer)
    }

    /// Look past the unsigned digits `start..end` for `generation R`.
    ///
    /// `Some(None)` means no reference follows; `None` declines a comment or
    /// an `R` that a regular character continues.
    fn reference(&self, start: usize, end: usize) -> Option<Option<(ObjectId, usize)>> {
        let mut lookahead = Parser::at(self.data, end);
        lookahead.space()?;
        let generation_start = lookahead.pos;
        let generation_length = lookahead.digits(generation_start);
        if generation_length == 0 {
            return Some(None);
        }
        lookahead.pos += generation_length;
        lookahead.space()?;
        if lookahead.peek() != Some(b'R') {
            return Some(None);
        }
        lookahead.pos += 1;
        if !lookahead.at_token_end() {
            return None;
        }
        let number = std::str::from_utf8(&self.data[start..end]).ok()?.parse();
        let generation =
            std::str::from_utf8(&self.data[generation_start..generation_start + generation_length])
                .ok()?
                .parse();
        // lopdf reads numbers out of range as integers and then fails on `R`.
        let (Ok(number), Ok(generation)) = (number, generation) else {
            return None;
        };
        Some(Some(((number, generation), lookahead.pos)))
    }

    fn name(&mut self) -> Option<Vec<u8>> {
        self.pos += 1;
        let mut name = Vec::new();
        while let Some(byte) = self.peek() {
            if byte == b'#' {
                let high = hex_value(*self.data.get(self.pos + 1)?)?;
                let low = hex_value(*self.data.get(self.pos + 2)?)?;
                name.push((high << 4) | low);
                self.pos += 3;
            } else if is_regular(byte) {
                name.push(byte);
                self.pos += 1;
            } else {
                break;
            }
        }
        Some(name)
    }

    /// Read a literal string, keeping nested parentheses and line endings.
    fn literal_string(&mut self) -> Option<Vec<u8>> {
        self.pos += 1;
        let mut text = Vec::new();
        let mut depth = 0usize;
        loop {
            let byte = self.peek()?;
            self.pos += 1;
            match byte {
                b'(' => {
                    depth += 1;
                    if depth > MAX_DEPTH {
                        return None;
                    }
                    text.push(byte);
                }
                b')' if depth == 0 => return Some(text),
                b')' => {
                    depth -= 1;
                    text.push(byte);
                }
                b'\\' => self.escape(&mut text)?,
                _ => text.push(byte),
            }
        }
    }

    fn escape(&mut self, text: &mut Vec<u8>) -> Option<()> {
        let byte = self.peek()?;
        let octal = self
            .rest()
            .iter()
            .take(3)
            .take_while(|byte| (b'0'..=b'7').contains(byte))
            .count();
        if octal > 0 {
            let value = self.rest()[..octal]
                .iter()
                .fold(0u16, |value, digit| value * 8 + u16::from(digit - b'0'));
            // lopdf ignores octal overflow, as the specification requires.
            text.push(value as u8);
            self.pos += octal;
            return Some(());
        }
        if self.eol().is_some() {
            return Some(());
        }
        self.pos += 1;
        text.push(match byte {
            b'n' => b'\n',
            b'r' => b'\r',
            b't' => b'\t',
            b'b' => b'\x08',
            b'f' => b'\x0C',
            other => other,
        });
        Some(())
    }

    fn hex_string(&mut self) -> Option<Vec<u8>> {
        self.pos += 1;
        let mut bytes = Vec::new();
        let mut high = None;
        loop {
            while self.peek().is_some_and(is_whitespace) {
                self.pos += 1;
            }
            let byte = self.peek()?;
            self.pos += 1;
            if byte == b'>' {
                break;
            }
            let value = hex_value(byte)?;
            match high.take() {
                Some(high) => bytes.push((high << 4) | value),
                None => high = Some(value),
            }
        }
        if let Some(high) = high {
            bytes.push(high << 4);
        }
        Some(bytes)
    }

    fn array(&mut self, depth: usize) -> Option<Vec<Object>> {
        let depth = depth.checked_sub(1)?;
        self.pos += 1;
        self.space()?;
        let mut items = Vec::new();
        while self.peek()? != b']' {
            items.push(self.object(depth)?);
        }
        self.pos += 1;
        Some(items)
    }

    fn dictionary(&mut self, depth: usize) -> Option<Dictionary> {
        let depth = depth.checked_sub(1)?;
        self.pos += 2;
        self.space()?;
        let mut dict = Dictionary::new();
        while !self.starts_with(b">>") {
            if self.peek()? != b'/' {
                return None;
            }
            let key = self.name()?;
            self.space()?;
            let value = self.object(depth)?;
            dict.set(key, value);
        }
        self.pos += 2;
        Some(dict)
    }

    /// Read stream data after `dict` and the `stream` keyword.
    fn stream(&mut self, dict: Dictionary) -> Option<Object> {
        self.pos += b"stream".len();
        while matches!(self.peek(), Some(b' ' | b'\t')) {
            self.pos += 1;
        }
        self.eol()?;
        // lopdf resolves an indirect length through its reader; decline it.
        let length = usize::try_from(dict.get(b"Length").ok()?.as_i64().ok()?).ok()?;
        let end = self.pos.checked_add(length)?;
        let content = self.data.get(self.pos..end)?;
        self.pos = end;
        let _ = self.eol();
        self.expect(b"endstream")?;
        Some(Object::Stream(Stream::new(dict, content.to_vec())))
    }
}

/// Compare documents field by field, keeping dictionary order and real bits.
fn same_document(left: &Document, right: &Document) -> bool {
    left.version == right.version
        && left.binary_mark == right.binary_mark
        && left.xref_start == right.xref_start
        && left.max_id == right.max_id
        && left.max_bookmark_id == right.max_bookmark_id
        && left.bookmarks == right.bookmarks
        && left.bookmark_table.is_empty()
        && right.bookmark_table.is_empty()
        && left.encryption_state.is_none()
        && right.encryption_state.is_none()
        && same_dictionary(&left.trailer, &right.trailer)
        && same_xref(&left.reference_table, &right.reference_table)
        && left.objects.len() == right.objects.len()
        && left
            .objects
            .iter()
            .zip(&right.objects)
            .all(|((left_id, left), (right_id, right))| {
                left_id == right_id && same_object(left, right)
            })
}

fn same_xref(left: &Xref, right: &Xref) -> bool {
    matches!(
        (left.cross_reference_type, right.cross_reference_type),
        (XrefType::CrossReferenceTable, XrefType::CrossReferenceTable)
            | (
                XrefType::CrossReferenceStream,
                XrefType::CrossReferenceStream
            )
    ) && left.size == right.size
        && left.entries.len() == right.entries.len()
        && left
            .entries
            .iter()
            .zip(&right.entries)
            .all(|((left_id, left), (right_id, right))| {
                left_id == right_id
                    && match (left, right) {
                        (
                            XrefEntry::Normal {
                                offset: left_offset,
                                generation: left_generation,
                            },
                            XrefEntry::Normal {
                                offset: right_offset,
                                generation: right_generation,
                            },
                        ) => left_offset == right_offset && left_generation == right_generation,
                        (
                            XrefEntry::Compressed {
                                container: left_container,
                                index: left_index,
                            },
                            XrefEntry::Compressed {
                                container: right_container,
                                index: right_index,
                            },
                        ) => left_container == right_container && left_index == right_index,
                        (XrefEntry::Free, XrefEntry::Free)
                        | (XrefEntry::UnusableFree, XrefEntry::UnusableFree) => true,
                        _ => false,
                    }
            })
}

fn same_dictionary(left: &Dictionary, right: &Dictionary) -> bool {
    left.len() == right.len()
        && left
            .iter()
            .zip(right.iter())
            .all(|((left_key, left), (right_key, right))| {
                left_key == right_key && same_object(left, right)
            })
}

fn same_object(left: &Object, right: &Object) -> bool {
    match (left, right) {
        (Object::Null, Object::Null) => true,
        (Object::Boolean(left), Object::Boolean(right)) => left == right,
        (Object::Integer(left), Object::Integer(right)) => left == right,
        (Object::Real(left), Object::Real(right)) => left.to_bits() == right.to_bits(),
        (Object::Name(left), Object::Name(right)) => left == right,
        (Object::String(left, left_format), Object::String(right, right_format)) => {
            left == right && left_format == right_format
        }
        (Object::Array(left), Object::Array(right)) => {
            left.len() == right.len()
                && left
                    .iter()
                    .zip(right)
                    .all(|(left, right)| same_object(left, right))
        }
        (Object::Dictionary(left), Object::Dictionary(right)) => same_dictionary(left, right),
        (Object::Stream(left), Object::Stream(right)) => {
            same_dictionary(&left.dict, &right.dict)
                && left.content == right.content
                && left.allows_compression == right.allows_compression
                && left.start_position == right.start_position
        }
        (Object::Reference(left), Object::Reference(right)) => left == right,
        _ => false,
    }
}
