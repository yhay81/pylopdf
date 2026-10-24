//! Keep lopdf's loaded object graph describing the same document as the
//! original bytes that hayro reads.
//!
//! An unedited document renders and extracts from its original bytes, which
//! hayro resolves through the cross-reference data, while edits and saves
//! serialize lopdf's object graph. The two agree when lopdf loaded exactly the
//! object that each in-use cross-reference entry names, with the stream data
//! that hayro reads. Malformed files break that in five ways:
//!
//! - lopdf stores an uncompressed object under the number in its own header,
//!   even when the entry that locates it names another number. hayro reads
//!   such a number as null until it first reads the mismatched entry, and
//!   then rebuilds its whole table by scanning object headers, so what it
//!   resolves depends on what it read first.
//! - lopdf skips an object it cannot parse, such as a dictionary that spells a
//!   name value without its slash, while hayro reads the bare keyword as null,
//!   or a stream whose declared bytes `endstream` does not follow and that has
//!   no sole `endstream` before the next entry offset, while hayro reads it up
//!   to its first `endstream`.
//! - lopdf adds object-stream members that no entry lists, including members
//!   that a damaged stream decodes under arbitrary numbers, and raises
//!   `max_id` over them, while hayro reads those numbers as null.
//! - lopdf stores an object-stream member under the number that the stream's
//!   own table gives it, while hayro reads the member that the table lists at
//!   the index the entry names, whatever its number there.
//! - lopdf loads a stream whose `/Length` is missing, is not an integer, or
//!   names an object that holds none without data, and then takes as many
//!   bytes as an integral real declares without looking for `endstream`,
//!   while hayro takes the declared bytes only when `endstream` follows them
//!   and otherwise reads up to the first `endstream`.
//!
//! Loading gives each such stream the data that hayro reads, finds the members
//! that hayro would read from another slot, re-parses each skipped uncompressed
//! object with a small parser that reads such a bare keyword as null and such
//! a stream up to its first `endstream` within the object's bounds, walks
//! every reference reachable from the trailer, and drops objects that no entry
//! locates and nothing reaches. The original-byte fast path stays only when
//! that walk meets no object that hayro could resolve differently. Any other
//! document renders from its serialized graph, as a document whose
//! cross-reference data lopdf recovered already does.

use std::collections::{HashSet, TryReserveError};
use std::io::Read;
use std::ops::Range;
use std::str::FromStr;

use flate2::bufread::ZlibDecoder;
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

/// The magnitude below which lopdf's single-precision real truncates to the
/// integer that hayro's double precision does. Only a decimal just below an
/// integer, which can round up onto it, differs, and moves the end of the
/// data by one trailing white-space byte.
const EXACT_REAL_LIMIT: f32 = 16_777_216.0;

/// Reconcile the loaded graph with its cross-reference data and report
/// whether hayro may read the original bytes instead of the serialized graph.
///
/// Streams that lopdf loaded without reading their `/Length` first receive the
/// data that hayro reads. A graph that then matches the in-use entries exactly,
/// with every compressed member where hayro looks for it, returns true at once.
/// Any other graph first receives the skipped uncompressed objects that parse,
/// then loses the objects that no entry locates unless the trailer reaches
/// them. `input` must be the unencrypted bytes lopdf loaded, with
/// cross-reference data that lopdf did not have to recover. Allocation refusal
/// raises `PdfError`, which fails the load.
pub(crate) fn reconcile_loaded_objects(doc: &mut Document, input: &[u8]) -> PyResult<bool> {
    let survey = Survey::of(doc).map_err(allocation_error)?;
    // Objects that hayro could resolve to something other than the graph holds.
    let mut suspects = misplaced_members(doc, survey.members).map_err(allocation_error)?;
    let sections =
        mirror_stream_data(doc, input, survey.streams, &mut suspects).map_err(allocation_error)?;
    if suspects.is_empty() && survey.matches {
        return Ok(true);
    }
    let buffer = lopdf_buffer(input);
    let sections = match sections {
        Some(sections) => sections,
        None => Sections::new(doc, buffer).map_err(allocation_error)?,
    };
    let recovered =
        recover_skipped_objects(doc, buffer, &sections, &mut suspects).map_err(allocation_error)?;
    suspects
        .try_reserve(recovered.len())
        .map_err(allocation_error)?;
    for (id, object) in recovered {
        suspects.insert(id);
        doc.objects.insert(id, object);
    }
    let audit = audit_references(doc, &suspects).map_err(allocation_error)?;
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

/// The input as lopdf reads it: it discards bytes before the header, and every
/// offset it records, including `xref_start`, counts from there.
fn lopdf_buffer(input: &[u8]) -> &[u8] {
    let header = input
        .windows(5)
        .position(|window| window == b"%PDF-")
        .unwrap_or(0);
    &input[header..]
}

fn allocation_error(error: TryReserveError) -> PyErr {
    PdfError::new_err(format!(
        "failed to allocate PDF object reconciliation: {error}"
    ))
}

/// What one pass over the in-use entries and the loaded objects finds.
struct Survey {
    /// Whether the loaded objects are exactly the in-use entries.
    matches: bool,
    /// `(container, index, number)` of every compressed entry whose member
    /// lopdf loaded, in object-number order.
    members: Vec<(u32, u16, u32)>,
    /// Every stream that lopdf loaded without reading its `/Length`, with the
    /// offset of the uncompressed entry that locates it, if any.
    streams: Vec<(Option<usize>, ObjectId)>,
}

impl Survey {
    /// Walk both maps once, in object-number order. lopdf records only in-use
    /// entries, never free ones, from classic tables and cross-reference
    /// streams alike.
    fn of(doc: &Document) -> Result<Self, TryReserveError> {
        Self::skipping(doc, None)
    }

    /// Walk as [`Survey::of`] does, without the in-use entry numbered `skip`,
    /// which names an object that loading removed on purpose.
    fn skipping(doc: &Document, skip: Option<u32>) -> Result<Self, TryReserveError> {
        let mut survey = Self {
            matches: true,
            members: Vec::new(),
            streams: Vec::new(),
        };
        let mut entries = doc
            .reference_table
            .entries
            .iter()
            .filter(|&(&number, _)| Some(number) != skip)
            .filter_map(|(&number, entry)| match *entry {
                XrefEntry::Normal { generation, .. } => Some((number, generation, entry)),
                XrefEntry::Compressed { .. } => Some((number, 0, entry)),
                XrefEntry::Free | XrefEntry::UnusableFree => None,
            })
            .peekable();
        for (&id, object) in &doc.objects {
            // An in-use entry that no loaded object answers.
            while entries.next_if(|&(number, ..)| number < id.0).is_some() {
                survey.matches = false;
            }
            let entry = entries
                .next_if(|&(number, generation, _)| (number, generation) == id)
                .map(|(.., entry)| entry);
            survey.matches &= entry.is_some();
            match (entry, object) {
                (Some(&XrefEntry::Compressed { container, index }), _) => {
                    survey.members.try_reserve(1)?;
                    survey.members.push((container, index, id.0));
                }
                (entry, Object::Stream(stream)) if stream.start_position.is_some() => {
                    let offset = match entry {
                        Some(&XrefEntry::Normal { offset, .. }) => Some(offset as usize),
                        _ => None,
                    };
                    survey.streams.try_reserve(1)?;
                    survey.streams.push((offset, id));
                }
                _ => {}
            }
        }
        survey.matches &= entries.next().is_none();
        Ok(survey)
    }
}

/// Whether lopdf decrypted exactly the objects that the cross-reference
/// entries of an encrypted input locate.
///
/// [`reconcile_loaded_objects`] cannot run on such a graph: the streams it
/// re-reads from the input would be ciphertext. This check changes nothing
/// and answers `false` wherever that function would have to read the input:
/// for an object that no entry locates or an in-use entry without one, for
/// a stream that lopdf loaded without its `/Length`, and for an object
/// stream member that the container's table does not place where the entry
/// says. Decryption removes the encryption dictionary, whose entry is
/// skipped. An incremental save appends to such an input only when this
/// holds.
pub(crate) fn decrypted_graph_matches_entries(doc: &Document) -> PyResult<bool> {
    let skip = doc
        .encryption_state
        .as_ref()
        .and_then(|state| state.encrypt_object_id())
        .map(|(number, _)| number);
    let survey = Survey::skipping(doc, skip).map_err(allocation_error)?;
    if !survey.matches || !survey.streams.is_empty() {
        return Ok(false);
    }
    Ok(misplaced_members(doc, survey.members)
        .map_err(allocation_error)?
        .is_empty())
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
/// as null, a suspect, which hayro parsed on its own or reads from another
/// slot or extent, or an in-use entry that lopdf could not load, which hayro
/// parses leniently or answers by rebuilding its whole table.
fn audit_references(
    doc: &Document,
    suspects: &HashSet<ObjectId>,
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
                        if !located(&doc.reference_table, *id) || suspects.contains(id) {
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

/// The bytes that each uncompressed object may occupy.
///
/// An object is read no further than the next distinct entry offset at which
/// an object header starts, the cross-reference section, or the end of input,
/// as lopdf bounds its own stream-length recovery by every entry offset. An
/// entry offset that starts no header, such as one that points into another
/// object's stream data, marks no object, so it does not cut short the object
/// that holds it. These bounds never overlap, so every pass that examines each
/// distinct offset once stays linear in input size even when many entries
/// share an offset.
struct Sections {
    /// Distinct offsets of in-use uncompressed entries at which a header
    /// starts, ascending.
    offsets: Vec<usize>,
    xref_start: usize,
    len: usize,
}

impl Sections {
    fn new(doc: &Document, buffer: &[u8]) -> Result<Self, TryReserveError> {
        let mut offsets = Vec::new();
        offsets.try_reserve_exact(doc.reference_table.entries.len())?;
        for entry in doc.reference_table.entries.values() {
            if let XrefEntry::Normal { offset, .. } = *entry {
                offsets.push(offset as usize);
            }
        }
        offsets.sort_unstable();
        offsets.dedup();
        let mut sections = Self {
            offsets,
            xref_start: doc.xref_start,
            len: buffer.len(),
        };
        // Read each header within the next distinct offset, so that every
        // offset is examined once over disjoint bytes. The offsets after
        // `index` are still all present while `kept` trails it.
        let mut kept = 0;
        for index in 0..sections.offsets.len() {
            let start = sections.offsets[index];
            let end = sections.bound(start, sections.offsets.get(index + 1).copied());
            let header = start < end
                && Parser {
                    data: &buffer[..end],
                    pos: start,
                }
                .header()
                .is_some();
            if header {
                sections.offsets[kept] = start;
                kept += 1;
            }
        }
        sections.offsets.truncate(kept);
        Ok(sections)
    }

    /// The end of the object that starts at `start`.
    fn end(&self, start: usize) -> usize {
        let next = self.offsets.partition_point(|&offset| offset <= start);
        self.bound(start, self.offsets.get(next).copied())
    }

    /// The end of an object that starts at `start` when the next object
    /// starts at `next`.
    fn bound(&self, start: usize, next: Option<usize>) -> usize {
        let section = (self.xref_start > start).then_some(self.xref_start);
        next.into_iter()
            .chain(section)
            .min()
            .unwrap_or(self.len)
            .min(self.len)
    }
}

/// Return the compressed members that hayro would read from another slot.
///
/// hayro resolves a compressed entry to the member that its container's table
/// lists at the entry's index, whatever number the table gives it there, while
/// lopdf stores every member under the number that the table gives it. Both
/// read the same member only when the table names the entry's number at that
/// index and nowhere else. Members that lopdf did not load are left to the
/// reference walk, and each container is decoded only as far as its table.
fn misplaced_members(
    doc: &Document,
    mut members: Vec<(u32, u16, u32)>,
) -> Result<HashSet<ObjectId>, TryReserveError> {
    let mut misplaced = HashSet::new();
    if members.is_empty() {
        return Ok(misplaced);
    }
    members.sort_unstable();
    let mut reader = TableReader::new();
    for group in members.chunk_by(|first, second| first.0 == second.0) {
        match MemberTable::read(doc, group[0].0, &mut reader)? {
            Some(table) => table.mark_misplaced(group, &mut misplaced)?,
            None => {
                misplaced.try_reserve(group.len())?;
                misplaced.extend(group.iter().map(|&(.., number)| (number, 0)));
            }
        }
    }
    Ok(misplaced)
}

/// The pairs of object number and offset that head an object stream, as
/// lopdf and hayro each read them.
struct MemberTable {
    /// Every pair that lopdf reads, in table order.
    pairs: Vec<(u32, u32)>,
    /// The `/N` pairs that hayro reads, in table order, unless they are the
    /// first `/N` of `pairs`.
    slots: Option<Vec<(u32, u32)>>,
    /// How many pairs hayro reads: the container's `/N`.
    declared: usize,
}

impl MemberTable {
    /// Read the table of object stream `container`, or return `None` when
    /// hayro could read it differently, which marks every member.
    fn read<'a>(
        doc: &'a Document,
        container: u32,
        reader: &mut TableReader<'a>,
    ) -> Result<Option<Self>, TryReserveError> {
        // hayro looks a container up as generation 0, never in an object stream.
        if !matches!(
            doc.reference_table.entries.get(&container),
            Some(XrefEntry::Normal { generation: 0, .. })
        ) {
            return Ok(None);
        }
        let Some(Object::Stream(stream)) = doc.objects.get(&(container, 0)) else {
            return Ok(None);
        };
        // lopdf loads members only when both entries are direct integers.
        let (Ok(&Object::Integer(declared)), Ok(&Object::Integer(first))) =
            (stream.dict.get(b"N"), stream.dict.get(b"First"))
        else {
            return Ok(None);
        };
        let (Ok(declared), Ok(first)) = (usize::try_from(declared), usize::try_from(first)) else {
            return Ok(None);
        };
        let Some(table) = reader.prefix(stream, first)? else {
            return Ok(None);
        };
        if let Some(pairs) = plain_pairs(table, declared)? {
            return Ok(Some(Self {
                pairs,
                slots: None,
                declared,
            }));
        }
        let (Some(slots), Some(pairs)) = (hayro_slots(table, declared)?, lopdf_pairs(table)?)
        else {
            return Ok(None);
        };
        Ok(Some(Self {
            pairs,
            slots: Some(slots),
            declared,
        }))
    }

    /// Add to `misplaced` every member of `group`, sorted by index, unless
    /// hayro's slot at its entry's index names it and every pair that lopdf
    /// reads for its number gives the same offset.
    fn mark_misplaced(
        mut self,
        group: &[(u32, u16, u32)],
        misplaced: &mut HashSet<ObjectId>,
    ) -> Result<(), TryReserveError> {
        let slots = match self.slots.take() {
            Some(slots) => slots,
            None => {
                // Members that fill every slot in order leave lopdf no other
                // pair for any of their numbers, which are distinct.
                let filled = group.len() == self.declared
                    && self.pairs.len() == self.declared
                    && group.iter().zip(&self.pairs).enumerate().all(
                        |(slot, (&(_, index, number), &(listed, _)))| {
                            usize::from(index) == slot && listed == number
                        },
                    );
                if filled {
                    return Ok(());
                }
                let mut slots = Vec::new();
                slots.try_reserve_exact(self.declared)?;
                slots.extend_from_slice(&self.pairs[..self.declared]);
                slots
            }
        };
        self.pairs.sort_unstable();
        for &(_, index, number) in group {
            let start = self.pairs.partition_point(|&(other, _)| other < number);
            let end = self.pairs.partition_point(|&(other, _)| other <= number);
            let listed = slots
                .get(usize::from(index))
                .is_some_and(|&(listed, offset)| {
                    listed == number
                        && start < end
                        && self.pairs[start..end]
                            .iter()
                            .all(|&(_, other)| other == offset)
                });
            if !listed {
                misplaced.try_reserve(1)?;
                misplaced.insert((number, 0));
            }
        }
        Ok(())
    }
}

/// Read a table of runs of digits separated by white space other than NUL,
/// which lopdf and hayro split alike, when every run reads as a `u32` and
/// hayro's `/N` pairs end before `/First`; otherwise return `None`, and the
/// two readers are followed one at a time.
fn plain_pairs(table: &[u8], declared: usize) -> Result<Option<Vec<(u32, u32)>>, TryReserveError> {
    let separator = |byte: u8| matches!(byte, b'\t' | b'\n' | b'\x0c' | b'\r' | b' ');
    let mut tokens = table
        .split(|&byte| separator(byte))
        .filter(|token| !token.is_empty());
    let mut pairs = Vec::new();
    let mut odd = false;
    while let Some(number) = tokens.next() {
        let Some(offset) = tokens.next() else {
            // lopdf drops a last token without a partner.
            odd = true;
            break;
        };
        let (Some(number), Some(offset)) = (digit_run(number), digit_run(offset)) else {
            return Ok(None);
        };
        pairs.try_reserve(1)?;
        pairs.push((number, offset));
    }
    // hayro reads its last number on past `/First` unless white space ends it.
    let cut = declared == pairs.len() && !odd && !table.last().copied().is_some_and(separator);
    if declared > pairs.len() || (declared > 0 && cut) {
        return Ok(None);
    }
    Ok(Some(pairs))
}

/// Read the `declared` pairs that hayro reads from the start of the data, or
/// return `None` when they are not all runs of digits that hayro reads alike
/// before `/First`.
///
/// hayro skips white space and comments between numbers and reads no member at
/// all when one of its pairs fails.
fn hayro_slots(table: &[u8], declared: usize) -> Result<Option<Vec<(u32, u32)>>, TryReserveError> {
    let mut parser = Parser {
        data: table,
        pos: 0,
    };
    let mut slots = Vec::new();
    for _ in 0..declared {
        let (Some(number), Some(offset)) = (parser.digits(), parser.digits()) else {
            return Ok(None);
        };
        slots.try_reserve(1)?;
        slots.push((number, offset));
    }
    Ok(Some(slots))
}

/// Read the pairs that lopdf reads from the bytes before `/First`, in table
/// order.
///
/// lopdf splits them at Unicode white space, pairs the tokens in order, and
/// skips a pair whose tokens do not both read as a `u32`, so this follows the
/// same steps.
fn lopdf_pairs(table: &[u8]) -> Result<Option<Vec<(u32, u32)>>, TryReserveError> {
    // lopdf reads no member from a table that is not UTF-8.
    let Ok(text) = std::str::from_utf8(table) else {
        return Ok(None);
    };
    let mut tokens = text.split_whitespace();
    let mut pairs = Vec::new();
    while let (Some(number), Some(offset)) = (tokens.next(), tokens.next()) {
        if let (Ok(number), Ok(offset)) = (u32::from_str(number), u32::from_str(offset)) {
            pairs.try_reserve(1)?;
            pairs.push((number, offset));
        }
    }
    Ok(Some(pairs))
}

/// The zlib decoder and output buffer that every object stream's table
/// reuses, so that each container costs only its own inflate.
struct TableReader<'a> {
    decoder: ZlibDecoder<&'a [u8]>,
    buffer: Vec<u8>,
}

impl<'a> TableReader<'a> {
    fn new() -> Self {
        Self {
            decoder: ZlibDecoder::new(&[][..]),
            buffer: Vec::new(),
        }
    }

    /// Decode the first `length` bytes of an object stream, or return `None`
    /// when lopdf and hayro could decode them differently.
    ///
    /// Both inflate a zlib stream alike and read an unfiltered one as it is.
    /// hayro reads a stream with `/F` as no stream at all, and other filters or
    /// decode parameters are rare enough in object streams to leave to the
    /// serialized graph. Output stops at `length`, never beyond what lopdf
    /// already decoded.
    fn prefix<'s>(
        &'s mut self,
        stream: &'a Stream,
        length: usize,
    ) -> Result<Option<&'s [u8]>, TryReserveError> {
        if stream.dict.has(b"F") || stream.dict.has(b"DecodeParms") || stream.dict.has(b"DP") {
            return Ok(None);
        }
        let filters = match stream.dict.get(b"Filter") {
            Err(_) => return Ok(stream.content.get(..length)),
            Ok(Object::Array(filters)) => filters.as_slice(),
            Ok(filter) => std::slice::from_ref(filter),
        };
        if !matches!(filters, [Object::Name(name)] if name == b"FlateDecode") {
            return Ok(None);
        }
        self.decoder.reset(stream.content.as_slice());
        self.buffer.clear();
        while self.buffer.len() < length {
            let filled = self.buffer.len();
            let wanted = (length - filled).min(64 * 1024);
            self.buffer.try_reserve(wanted)?;
            self.buffer.resize(filled + wanted, 0);
            match self.decoder.read(&mut self.buffer[filled..]) {
                Ok(0) | Err(_) => return Ok(None),
                Ok(read) => self.buffer.truncate(filled + read),
            }
        }
        Ok(Some(&self.buffer))
    }
}

/// How hayro reads the data of one stream.
enum Extent {
    /// The data lies in this range of the input.
    Data(Range<usize>),
    /// The stream names an external file, which hayro reads as no stream at
    /// all, from the original bytes and from the serialized graph alike.
    External,
    /// hayro could read the data past the object's bounds, or read no stream.
    Unknown,
}

/// Give each stream whose length lopdf could not read while parsing the data
/// that hayro reads, and add the streams whose data it cannot match to
/// `suspects`.
///
/// lopdf marks such a stream with the position of its data. Each stream is read
/// within the bounds of the entry that locates it, and the header at an offset
/// that several entries share is read once. A stream whose entry locates an
/// object with another number was loaded from bytes that hayro does not read
/// for it. The bounds are computed only when some stream needs them, and
/// returned for the passes that follow.
fn mirror_stream_data(
    doc: &mut Document,
    input: &[u8],
    mut streams: Vec<(Option<usize>, ObjectId)>,
    suspects: &mut HashSet<ObjectId>,
) -> Result<Option<Sections>, TryReserveError> {
    if streams.is_empty() {
        return Ok(None);
    }
    let buffer = lopdf_buffer(input);
    let sections = Sections::new(doc, buffer)?;
    streams.sort_unstable();
    let mut header_at = None;
    for (start, id) in streams {
        let extent = match start.filter(|&start| start < buffer.len()) {
            Some(start) => {
                let end = sections.end(start);
                let header = match header_at {
                    Some((offset, header)) if offset == start => header,
                    _ => {
                        let header = Parser {
                            data: &buffer[..end],
                            pos: start,
                        }
                        .header();
                        header_at = Some((start, header));
                        header
                    }
                };
                if header == Some(id) {
                    hayro_stream_extent(doc, buffer, start, end, suspects)?
                } else {
                    Extent::Unknown
                }
            }
            None => Extent::Unknown,
        };
        match extent {
            Extent::Data(range) => store_stream_data(doc, id, &buffer[range])?,
            Extent::External => {}
            Extent::Unknown => {
                suspects.try_reserve(1)?;
                suspects.insert(id);
            }
        }
    }
    Ok(Some(sections))
}

/// Read the stream object at `start` as hayro reads it, no further than `end`.
///
/// hayro takes `/Length` bytes when `stream`, one end-of-line marker, that
/// many bytes, white space, and `endstream` follow the dictionary. Otherwise
/// it takes everything from the end-of-line marker after the first `stream` up
/// to the first `endstream`, less trailing white space.
fn hayro_stream_extent(
    doc: &Document,
    buffer: &[u8],
    start: usize,
    end: usize,
    suspects: &HashSet<ObjectId>,
) -> Result<Extent, TryReserveError> {
    let mut parser = Parser {
        data: &buffer[..end],
        pos: start,
    };
    if parser.header().is_none() {
        return Ok(Extent::Unknown);
    }
    let dict = match parser.value() {
        Ok(Object::Dictionary(dict)) => dict,
        Ok(_) | Err(Failure::Syntax) => return Ok(Extent::Unknown),
        Err(Failure::Allocation(error)) => return Err(error),
    };
    if dict.has(b"F") {
        return Ok(Extent::External);
    }
    // Reading past `end` is certain to fail only at the end of the input.
    let window = Window {
        data: &buffer[..end],
        final_end: end == buffer.len(),
    };
    let declared = match hayro_length(&dict, doc, suspects) {
        Length::Absent => Proper::Failed,
        Length::Value(length) => window.declared_extent(parser.pos, length),
        Length::Unknown => Proper::Unknown,
    };
    Ok(match declared {
        Proper::Data(range) => Extent::Data(range),
        Proper::Failed => window
            .searched_extent(parser.pos)
            .map_or(Extent::Unknown, Extent::Data),
        Proper::Unknown => Extent::Unknown,
    })
}

/// How hayro reads a stream's `/Length`.
enum Length {
    /// No usable length, so hayro searches for `endstream` at once.
    Absent,
    Value(u32),
    /// The length names an object that hayro could read differently.
    Unknown,
}

/// Resolve `/Length` as hayro does: an integer, a real truncated toward zero,
/// or an indirect object holding one, that fits a `u32`.
fn hayro_length(dict: &Dictionary, doc: &Document, suspects: &HashSet<ObjectId>) -> Length {
    let value = match dict.get(b"Length") {
        Err(_) => return Length::Absent,
        Ok(Object::Reference(id)) => {
            if suspects.contains(id) {
                return Length::Unknown;
            }
            match doc.objects.get(id) {
                // hayro reads the object number of a reference as the length.
                Some(Object::Reference(_)) => return Length::Unknown,
                Some(value) => value,
                // hayro parses an entry that lopdf could not load on its own,
                // and reads a number that no entry lists as null.
                None if located(&doc.reference_table, *id) => return Length::Unknown,
                None => return Length::Absent,
            }
        }
        Ok(value) => value,
    };
    let length = match *value {
        Object::Integer(length) => length,
        Object::Real(length) if length.abs() < EXACT_REAL_LIMIT => length as i64,
        Object::Real(_) => return Length::Unknown,
        _ => return Length::Absent,
    };
    u32::try_from(length).map_or(Length::Absent, Length::Value)
}

/// The outcome of hayro's attempt to read `/Length` bytes.
enum Proper {
    Data(Range<usize>),
    /// hayro searches for `endstream` instead.
    Failed,
    /// hayro could succeed past the object's bounds.
    Unknown,
}

/// The bytes of one object, through which hayro's stream reads are followed.
struct Window<'a> {
    /// The input up to the object's bound.
    data: &'a [u8],
    /// Whether the bound is the end of the input, past which hayro reads nothing.
    final_end: bool,
}

impl Window<'_> {
    /// Whether `tag` starts at `position`: `None` when the bound cuts it off.
    fn tag_at(&self, position: usize, tag: &[u8]) -> Option<bool> {
        let available = &self.data[position.min(self.data.len())..];
        if available.len() >= tag.len() {
            Some(available.starts_with(tag))
        } else if !tag.starts_with(available) || self.final_end {
            Some(false)
        } else {
            None
        }
    }

    /// The position after the end-of-line marker that ends `stream` at
    /// `position`, `Some(None)` when there is none, or `None` when the bound
    /// cuts it off.
    fn data_start(&self, position: usize) -> Option<Option<usize>> {
        match self.data.get(position) {
            Some(b'\n') => Some(Some(position + 1)),
            Some(b'\r') => match self.data.get(position + 1) {
                Some(b'\n') => Some(Some(position + 2)),
                Some(_) => Some(Some(position + 1)),
                None if self.final_end => Some(Some(position + 1)),
                None => None,
            },
            Some(_) => Some(None),
            None if self.final_end => Some(None),
            None => None,
        }
    }

    /// Mirror hayro's `parse_proper`.
    fn declared_extent(&self, dict_end: usize, length: u32) -> Proper {
        let mut parser = Parser {
            data: self.data,
            pos: dict_end,
        };
        parser.skip_whitespace();
        let keyword = parser.pos;
        match self.tag_at(keyword, b"stream") {
            Some(true) => {}
            Some(false) => return Proper::Failed,
            None => return Proper::Unknown,
        }
        let start = match self.data_start(keyword + b"stream".len()) {
            Some(Some(start)) => start,
            Some(None) => return Proper::Failed,
            None => return Proper::Unknown,
        };
        let Some(end) = start.checked_add(length as usize) else {
            return Proper::Failed;
        };
        if end > self.data.len() {
            return if self.final_end {
                Proper::Failed
            } else {
                Proper::Unknown
            };
        }
        let mut after = end;
        while self.data.get(after).copied().is_some_and(is_whitespace) {
            after += 1;
        }
        match self.tag_at(after, b"endstream") {
            Some(true) => Proper::Data(start..end),
            Some(false) => Proper::Failed,
            None => Proper::Unknown,
        }
    }

    /// Mirror hayro's `parse_fallback`, or return `None` when hayro could read
    /// past the bound or read no stream.
    fn searched_extent(&self, dict_end: usize) -> Option<Range<usize>> {
        let keyword = dict_end + find(&self.data[dict_end..], b"stream")?;
        let start = self.data_start(keyword + b"stream".len())??;
        let length = first_endstream(&self.data[start..])?;
        Some(start..start + length)
    }
}

/// The position of the first `needle` in `haystack`.
fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

/// Store `data` as a stream's content, with the direct `/Length` that lopdf
/// gives every stream it reads, so that saving writes the data hayro reads.
fn store_stream_data(doc: &mut Document, id: ObjectId, data: &[u8]) -> Result<(), TryReserveError> {
    let Some(Object::Stream(stream)) = doc.objects.get_mut(&id) else {
        return Ok(());
    };
    if stream.content != data {
        let mut content = Vec::new();
        content.try_reserve_exact(data.len())?;
        content.extend_from_slice(data);
        stream.content = content;
    }
    stream
        .dict
        .set("Length", Object::Integer(data.len() as i64));
    Ok(())
}

/// Parse every skipped uncompressed object whose header matches its entry.
///
/// Each distinct offset is examined once and parsed no further than its
/// section, so the pass stays linear in input size. A loaded object whose
/// header disagrees with the entry that locates it joins `suspects` under the
/// number in its header: lopdf stores it there, possibly in place of the
/// object that hayro reads for that number from its own entry or container.
fn recover_skipped_objects(
    doc: &Document,
    buffer: &[u8],
    sections: &Sections,
    suspects: &mut HashSet<ObjectId>,
) -> Result<Vec<(ObjectId, Object)>, TryReserveError> {
    let mut recovered = Vec::new();
    for &start in &sections.offsets {
        if start >= buffer.len() {
            break;
        }
        let mut parser = Parser {
            data: &buffer[..sections.end(start)],
            pos: start,
        };
        let Some(id) = parser.header() else {
            continue;
        };
        let located_here = matches!(
            doc.reference_table.entries.get(&id.0),
            Some(XrefEntry::Normal { offset, generation })
                if *offset as usize == start && *generation == id.1
        );
        if doc.objects.contains_key(&id) {
            if !located_here {
                suspects.try_reserve(1)?;
                suspects.insert(id);
            }
            continue;
        }
        if !located_here {
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

/// Read a non-empty run of ASCII digits as a `u32`, as `unsigned` does, without
/// going through text, which a table of a hundred thousand pairs would notice.
fn digit_run(token: &[u8]) -> Option<u32> {
    if token.is_empty() {
        return None;
    }
    token.iter().try_fold(0u32, |value, &byte| {
        let digit = byte.wrapping_sub(b'0');
        if digit > 9 {
            return None;
        }
        value.checked_mul(10)?.checked_add(u32::from(digit))
    })
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

    /// Read a run of digits after white space and comments as hayro reads a
    /// `u32`, or return `None` for anything else, including a run that the
    /// data cuts off or that a regular character follows.
    fn digits(&mut self) -> Option<u32> {
        self.skip_whitespace();
        let start = self.pos;
        while self.data.get(self.pos).is_some_and(u8::is_ascii_digit) {
            self.pos += 1;
        }
        let ended = self
            .data
            .get(self.pos)
            .is_some_and(|&byte| !is_regular(byte));
        if !ended {
            return None;
        }
        unsigned(&self.data[start..self.pos])
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
    /// like lopdf, the sole end-of-line-framed `endstream` that `endobj`
    /// follows before the object's boundary, or else, as hayro reads a stream
    /// whose declared bytes `endstream` does not follow, the first `endstream`
    /// less trailing white space.
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
            .or_else(|| first_endstream(&self.data[start..]).map(|length| start + length))
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

/// Return the data length before the first `endstream`, less trailing white
/// space, as hayro's fallback reads a stream.
fn first_endstream(data: &[u8]) -> Option<usize> {
    let end = find(data, b"endstream")?;
    Some(
        data[..end]
            .iter()
            .rposition(|&byte| !is_whitespace(byte))
            .map_or(0, |last| last + 1),
    )
}
