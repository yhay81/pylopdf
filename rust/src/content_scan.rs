//! Iterative nesting scan for the content streams that hayro interprets.
//!
//! hayro-syntax 0.7 skips and reads arrays and dictionaries in content streams
//! by recursion with no depth bound (LaurenzV/hayro#1347; hayro 0.8 stops at 64
//! levels). A stream such as `"[" * 300_000 + "(X)" + "]" * 300_000 + " TJ"`
//! overflows the stack of the interpreting thread, and a stack overflow kills
//! the process instead of raising. [`scan`] decides, without recursing itself,
//! whether hayro's object parser can be driven deeper than
//! [`MAX_NESTING_DEPTH`] by the given bytes.
//!
//! The scan runs one small lexer per hypothesis about how hayro tokenizes the
//! stream and refuses the stream when any hypothesis nests too deeply. A lexer
//! models the constructs that decide where a container opens or closes:
//! whitespace, comments, literal and hexadecimal strings, names, `[ ]`, and
//! `<< >>`. Depth counts unclosed containers whatever their kind, because hayro
//! abandons a parse at the first mismatched closer. The lexers never abandon
//! one themselves. Continuing past input that hayro rejects can only add
//! hypotheses.
//!
//! Two constructs hide bytes from tokenization:
//!
//! * Inline images. After `BI`, hayro reads key/value pairs up to an `ID` at a
//!   key position, skips one whitespace byte, and searches for an `EI` followed
//!   by whitespace that ends the data. It decides with a heuristic that parses
//!   the rest of the stream, so the scan starts a fresh lexer behind every
//!   `EI` that hayro does not certainly reject. A candidate is certainly
//!   rejected when a non-ASCII byte precedes the next `EI`; that keeps binary
//!   image data, which may contain any byte sequence, from being tokenized.
//!   The same heuristic searches the bytes behind a candidate for `BI` wherever
//!   it occurs, inside a string or comment as well, and tries to read an inline
//!   image dictionary behind each pair, discarding the result. Once a candidate
//!   exists the scan therefore starts a trial lexer behind every such pair. It
//!   follows the read only while it can go on: at a key position hayro needs a
//!   name or the tag `ID`, so prose and binary bytes end the lexer within a
//!   byte or two.
//!   When no `EI` is accepted, hayro does not stop: it tokenizes from the data
//!   start if the stream holds no `EI`, and otherwise from just behind the last
//!   `EI` it examined, which is the last one or, when a regular byte follows
//!   the one before it, one byte later. Those positions start lexers too.
//!   Before the first image a single lexer suffices, and [`scan_alone`] skips
//!   the bytes that cannot matter. After it the lexers run in lockstep.
//! * Stream objects. A dictionary followed by `stream` makes hayro skip an
//!   opaque body. Content streams never contain one, so the scan refuses the
//!   stream instead of modeling the body.
//!
//! At most [`MAX_LEXERS`] hypotheses stay alive; identical ones merge. Real
//! streams need one or two. A stream that needs more is refused rather than
//! checked partially.

use std::collections::TryReserveError;

/// Deepest array/dictionary nesting that may reach hayro's recursive parser.
///
/// This matches the bound that hayro 0.8 applies and
/// `DocumentLimits.web()` applies to direct objects at load.
pub(crate) const MAX_NESTING_DEPTH: u32 = 64;

/// Hypotheses about hayro's tokenization that may run side by side.
const MAX_LEXERS: usize = 8;

/// Scheduled lexer starts: three behind the last `EI`, plus the starts of
/// images that hayro tokenizes and the `EI` being examined.
const MAX_RESUMES: usize = 8;

/// Why a content stream cannot be handed to hayro.
#[derive(Debug)]
pub(crate) enum Refusal {
    /// Arrays or dictionaries nest deeper than [`MAX_NESTING_DEPTH`].
    Depth,
    /// A dictionary is followed by `stream`, so hayro would skip an opaque body.
    EmbeddedStream,
    /// Inline image boundaries are too ambiguous to check within [`MAX_LEXERS`].
    Ambiguous,
    /// The scan could not allocate its small fixed state.
    Allocation(TryReserveError),
}

/// Character classes of ISO 32000-2 §7.2.3, as hayro's tables define them.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Class {
    Regular,
    Space,
    Slash,
    Percent,
    LeftParen,
    RightParen,
    Less,
    Greater,
    LeftBracket,
    RightBracket,
    Brace,
}

const CLASSES: [Class; 256] = {
    let mut table = [Class::Regular; 256];
    table[0x00] = Class::Space;
    table[0x09] = Class::Space;
    table[0x0a] = Class::Space;
    table[0x0c] = Class::Space;
    table[0x0d] = Class::Space;
    table[0x20] = Class::Space;
    table[b'/' as usize] = Class::Slash;
    table[b'%' as usize] = Class::Percent;
    table[b'(' as usize] = Class::LeftParen;
    table[b')' as usize] = Class::RightParen;
    table[b'<' as usize] = Class::Less;
    table[b'>' as usize] = Class::Greater;
    table[b'[' as usize] = Class::LeftBracket;
    table[b']' as usize] = Class::RightBracket;
    table[b'{' as usize] = Class::Brace;
    table[b'}' as usize] = Class::Brace;
    table
};

fn is_space(byte: u8) -> bool {
    CLASSES[usize::from(byte)] == Class::Space
}

/// Where a lexer is inside the byte stream.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Zone {
    /// Between tokens or inside a regular-character token.
    Plain,
    /// Inside a comment, up to the next CR or LF.
    Comment,
    /// Inside a literal string with this many unmatched `(`.
    Literal(u32),
    /// Behind a backslash inside a literal string.
    Escape(u32),
    /// Inside a hexadecimal string.
    Hex,
    /// Behind a `<` that starts either `<<` or a hexadecimal string.
    Less,
    /// Behind a `>` that may complete `>>`.
    Greater,
}

/// Where a lexer is inside an inline image's dictionary.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Phase {
    /// Ordinary content.
    Content,
    /// Behind `BI`, expecting a key or `ID`.
    Key,
    /// Behind a key, expecting one value object.
    Value,
}

/// The regular-character token in progress.
///
/// Only the properties the scan needs are kept: whether it is a name and, for
/// a two-byte keyword, its bytes.
#[derive(Clone, Copy, Default, PartialEq, Eq)]
struct Word {
    /// Whether any byte was seen, even if only a sign or dot that is a number.
    any: bool,
    len: u8,
    first: u8,
    second: u8,
    name: bool,
    /// A leading `+` that a `.` may still turn into the number `+.`.
    plus: bool,
}

impl Word {
    fn name() -> Self {
        Self {
            any: true,
            len: 1,
            first: b'/',
            second: 0,
            name: true,
            plus: false,
        }
    }

    fn push(&mut self, byte: u8) {
        self.any = true;
        // hayro reads a sign or dot that no digit follows as the number 0 and
        // starts the next token right behind it, so `-.BI` is a number and the
        // keyword `BI`. `+` alone is no such number, only `+.` is. These
        // leading bytes are not part of the keyword.
        if self.len == 0 {
            if self.plus {
                self.plus = false;
                if byte == b'.' {
                    return;
                }
                self.first = b'+';
                self.len = 1;
            } else if byte == b'+' {
                self.plus = true;
                return;
            } else if matches!(byte, b'-' | b'.') {
                return;
            }
        }
        match self.len {
            0 => self.first = byte,
            1 => self.second = byte,
            _ => {}
        }
        self.len = self.len.saturating_add(1);
    }

    /// The token as a lone `+` still counts as one byte.
    fn finish(mut self) -> Self {
        if self.plus {
            self.plus = false;
            self.first = b'+';
            self.len = 1;
        }
        self
    }

    fn is(&self, keyword: &[u8; 2]) -> bool {
        !self.name && self.len == 2 && [self.first, self.second] == *keyword
    }
}

/// What one byte did to a lexer, beyond moving it.
enum Event {
    None,
    /// `ID` at a key position, followed by whitespace: inline image data starts.
    ImageData,
    /// A `>>` closed a container.
    DictionaryClosed,
    /// A trial lexer's dictionary read ended, by finishing or by failing.
    Over,
}

/// One hypothesis about how hayro tokenizes the stream.
#[derive(Clone, Copy, PartialEq, Eq)]
struct Lexer {
    zone: Zone,
    phase: Phase,
    /// Unclosed `[` and `<<` containers.
    depth: u32,
    word: Word,
    /// Whether this only models the dictionary read that hayro's `EI` search
    /// tries behind a stray `BI`, which stops at the first key it cannot read.
    trial: bool,
}

impl Lexer {
    fn new() -> Self {
        Self {
            zone: Zone::Plain,
            phase: Phase::Content,
            depth: 0,
            word: Word::default(),
            trial: false,
        }
    }

    /// A lexer that starts at the first key of an inline image dictionary.
    fn trial() -> Self {
        Self {
            phase: Phase::Key,
            trial: true,
            ..Self::new()
        }
    }

    /// Whether hayro's dictionary read has finished or failed before `byte`.
    ///
    /// At a key position hayro reads a name or the tag `ID`. The tag ends the
    /// read whatever follows it, and any other byte makes the read fail.
    fn trial_over(&self, byte: u8, class: Class) -> bool {
        if !self.trial || self.phase != Phase::Key || self.depth != 0 {
            return false;
        }
        if self.word.any {
            // A name goes on. Anything else started with `I`, and the next byte
            // either completes the tag or is not a name.
            return !self.word.name;
        }
        !matches!(class, Class::Space | Class::Percent | Class::Slash) && byte != b'I'
    }

    fn step(&mut self, byte: u8) -> Result<Event, Refusal> {
        loop {
            match self.zone {
                Zone::Plain => return self.step_plain(byte),
                Zone::Comment => {
                    if matches!(byte, b'\n' | b'\r') {
                        self.zone = Zone::Plain;
                    }
                    return Ok(Event::None);
                }
                Zone::Literal(open) => {
                    match byte {
                        b'\\' => self.zone = Zone::Escape(open),
                        b'(' => self.zone = Zone::Literal(open.saturating_add(1)),
                        b')' if open > 1 => self.zone = Zone::Literal(open - 1),
                        b')' => {
                            self.zone = Zone::Plain;
                            self.value_done();
                        }
                        _ => {}
                    }
                    return Ok(Event::None);
                }
                Zone::Escape(open) => {
                    self.zone = Zone::Literal(open);
                    return Ok(Event::None);
                }
                Zone::Hex => {
                    if byte == b'>' {
                        self.zone = Zone::Plain;
                        self.value_done();
                        return Ok(Event::None);
                    }
                    if byte.is_ascii_hexdigit() || is_space(byte) {
                        return Ok(Event::None);
                    }
                    // hayro rejects the string here; read the byte as content.
                    self.zone = Zone::Plain;
                }
                Zone::Less => {
                    if byte == b'<' {
                        self.zone = Zone::Plain;
                        self.open()?;
                        return Ok(Event::None);
                    }
                    self.zone = Zone::Hex;
                }
                Zone::Greater => {
                    self.zone = Zone::Plain;
                    if byte == b'>' {
                        return Ok(self.close(true));
                    }
                }
            }
        }
    }

    fn step_plain(&mut self, byte: u8) -> Result<Event, Refusal> {
        let class = CLASSES[usize::from(byte)];
        if self.trial_over(byte, class) {
            return Ok(Event::Over);
        }
        if class == Class::Regular {
            self.word.push(byte);
            return Ok(Event::None);
        }
        let event = self.end_word(byte);
        // The byte may start the next key even though it ended a value.
        if self.trial_over(byte, class) {
            return Ok(Event::Over);
        }
        match class {
            Class::Regular | Class::Space | Class::RightParen | Class::Brace => {}
            Class::Slash => self.word = Word::name(),
            Class::Percent => self.zone = Zone::Comment,
            Class::LeftParen => self.zone = Zone::Literal(1),
            Class::Less => self.zone = Zone::Less,
            Class::Greater => self.zone = Zone::Greater,
            Class::LeftBracket => self.open()?,
            Class::RightBracket => {
                self.close(false);
            }
        }
        Ok(event)
    }

    /// Finish the token in progress; `terminator` is the byte that ended it.
    fn end_word(&mut self, terminator: u8) -> Event {
        let word = std::mem::take(&mut self.word).finish();
        if !word.any || self.depth != 0 {
            return Event::None;
        }
        match self.phase {
            Phase::Content => {
                if word.is(b"BI") {
                    self.phase = Phase::Key;
                }
            }
            Phase::Key => {
                if word.name {
                    self.phase = Phase::Value;
                } else if word.is(b"ID") && is_space(terminator) {
                    return Event::ImageData;
                }
            }
            Phase::Value => self.phase = Phase::Key,
        }
        Event::None
    }

    /// Finish a string or container that was the value in an inline dictionary.
    fn value_done(&mut self) {
        if self.depth == 0 && self.phase == Phase::Value {
            self.phase = Phase::Key;
        }
    }

    fn open(&mut self) -> Result<(), Refusal> {
        self.depth += 1;
        if self.depth > MAX_NESTING_DEPTH {
            return Err(Refusal::Depth);
        }
        Ok(())
    }

    fn close(&mut self, dictionary: bool) -> Event {
        if self.depth == 0 {
            return Event::None;
        }
        self.depth -= 1;
        if self.depth == 0 {
            self.value_done();
        }
        if dictionary {
            Event::DictionaryClosed
        } else {
            Event::None
        }
    }
}

/// Check one decoded content stream, exactly as hayro will parse it.
///
/// For a page, `data` is the concatenation hayro builds from `/Contents`.
pub(crate) fn scan(data: &[u8]) -> Result<(), Refusal> {
    // One hypothesis suffices until an inline image makes hayro's next step
    // depend on a search that this scan cannot replay exactly.
    let Some(index) = scan_alone(data)? else {
        return Ok(());
    };
    let mut images = Images::default();
    images.start(data, index)?;
    scan_images(data, images, index + 1)
}

/// Bytes at which a lone lexer in ordinary content has anything to do.
///
/// Every other byte only extends or ends a token, which matters for the `BI`
/// keyword and nothing else outside an inline image dictionary.
const WATCHED_IN_CONTENT: [bool; 256] = {
    let mut table = [false; 256];
    table[b'%' as usize] = true;
    table[b'(' as usize] = true;
    table[b'<' as usize] = true;
    table[b'>' as usize] = true;
    table[b'[' as usize] = true;
    table[b']' as usize] = true;
    table[b'I' as usize] = true;
    table
};

/// Bytes that change a literal string's state.
const WATCHED_IN_LITERAL: [bool; 256] = {
    let mut table = [false; 256];
    table[b'\\' as usize] = true;
    table[b'(' as usize] = true;
    table[b')' as usize] = true;
    table
};

/// Index of the first byte at or after `index` that `watched` marks.
fn next_watched(data: &[u8], index: usize, watched: &[bool; 256]) -> usize {
    data[index..]
        .iter()
        .position(|&byte| watched[usize::from(byte)])
        .map_or(data.len(), |offset| index + offset)
}

/// Whether the `I` at `index` ends a `BI` keyword that hayro reads as an operator.
///
/// No token starts before `floor`: the lexer read the bytes up to it as a
/// hexadecimal string that the next byte then broke off.
fn ends_image_keyword(data: &[u8], index: usize, floor: usize) -> bool {
    let is_regular = |byte: u8| CLASSES[usize::from(byte)] == Class::Regular;
    if index == 0 || data[index - 1] != b'B' || data.get(index + 1).is_some_and(|&b| is_regular(b))
    {
        return false;
    }
    // Signs and dots right before the keyword are numbers of their own: `-`,
    // `.`, and `+.`, but not a `+` alone.
    let mut start = index - 1;
    while start > floor {
        match data[start - 1] {
            b'.' if start - 1 > floor && data[start - 2] == b'+' => start -= 2,
            b'-' | b'.' => start -= 1,
            _ => break,
        }
    }
    // A name such as `/BI` is not the operator.
    start <= floor || (!is_regular(data[start - 1]) && data[start - 1] != b'/')
}

/// Run the only lexer through the stream until it reaches inline image data.
///
/// Return the index of the whitespace byte after `ID`, or `None` at the end.
/// Outside an inline image dictionary, skipping the bytes in
/// [`WATCHED_IN_CONTENT`]'s complement is exactly what the byte-wise lexer does.
fn scan_alone(data: &[u8]) -> Result<Option<usize>, Refusal> {
    let mut lexer = Lexer::new();
    let mut index = 0;
    // The first byte that can belong to the token in progress.
    let mut floor = 0;
    while index < data.len() {
        match (lexer.zone, lexer.phase) {
            (Zone::Plain, Phase::Content) => {
                index = next_watched(data, index, &WATCHED_IN_CONTENT);
                let Some(&byte) = data.get(index) else {
                    break;
                };
                if byte == b'I' {
                    if lexer.depth == 0 && ends_image_keyword(data, index, floor) {
                        // The byte-wise lexer has consumed the keyword by the
                        // time it reads the byte behind it, and the bytes this
                        // loop skipped left a stale token behind.
                        lexer.phase = Phase::Key;
                        lexer.word = Word::default();
                    }
                } else {
                    lexer.step(byte)?;
                }
            }
            (Zone::Literal(_), _) => {
                index = next_watched(data, index, &WATCHED_IN_LITERAL);
                if let Some(&byte) = data.get(index) {
                    lexer.step(byte)?;
                }
            }
            (Zone::Comment, _) => {
                index = data[index..]
                    .iter()
                    .position(|&byte| matches!(byte, b'\n' | b'\r'))
                    .map_or(data.len(), |offset| index + offset);
                if let Some(&byte) = data.get(index) {
                    lexer.step(byte)?;
                }
            }
            _ => {
                let byte = data[index];
                let breaks_hex = lexer.zone == Zone::Hex
                    && byte != b'>'
                    && !byte.is_ascii_hexdigit()
                    && !is_space(byte);
                match lexer.step(byte)? {
                    Event::ImageData => return Ok(Some(index)),
                    Event::DictionaryClosed if stream_keyword_follows(data, index + 1) => {
                        return Err(Refusal::EmbeddedStream);
                    }
                    // The only lexer here is never a trial lexer.
                    Event::DictionaryClosed | Event::None | Event::Over => {}
                }
                if breaks_hex {
                    floor = index;
                }
            }
        }
        index += 1;
    }
    Ok(None)
}

/// What the scan knows about inline image data, shared by all lexers.
#[derive(Default)]
struct Images {
    /// The earliest byte that can belong to inline image data.
    from: Option<usize>,
    /// Where a fresh lexer starts.
    resumes: Positions,
    /// The last `EI` in the stream, found once the first image starts.
    last_end: Option<Option<usize>>,
    /// Where hayro's `EI` search first looks at the bytes behind a candidate.
    /// From here on it reads a dictionary behind every `BI` byte pair it meets.
    search_from: Option<usize>,
}

impl Images {
    /// Record that image data starts after the whitespace byte at `index`.
    fn start(&mut self, data: &[u8], index: usize) -> Result<(), Refusal> {
        let first = self.from.is_none();
        let start = index + 1;
        self.from.get_or_insert(start);
        let last = *self
            .last_end
            .get_or_insert_with(|| data.windows(2).rposition(|pair| pair == b"EI"));
        match last {
            // hayro skips an `EI` followed by a regular byte and ends its search
            // after the last `EI` it examined, or after the one before it when
            // that byte began the last one. The tokens it reads next follow there.
            Some(position) if position >= start => {
                if first {
                    for ahead in 1..=3 {
                        self.resumes.push(position + ahead, data.len())?;
                    }
                }
                Ok(())
            }
            // Without any `EI` hayro tokenizes the "image data".
            _ => self.resumes.push(start, data.len()),
        }
    }
}

/// Run every hypothesis in lockstep once inline image data has started.
fn scan_images(data: &[u8], mut images: Images, mut index: usize) -> Result<(), Refusal> {
    let mut lexers: Vec<Lexer> = Vec::new();
    lexers
        .try_reserve_exact(MAX_LEXERS)
        .map_err(Refusal::Allocation)?;
    while index < data.len() {
        if lexers.is_empty() {
            // Nothing tokenizes image data; jump to the next place a lexer can
            // start: a scheduled position, a possible `EI`, or once the search
            // has begun the end of a `BI` pair, whichever comes first. Looking
            // no further than the scheduled position keeps repeated jumps linear.
            let start = index.max(images.from.unwrap_or(0));
            let scheduled = images.resumes.earliest();
            let candidate = data
                .get(start..scheduled.unwrap_or(data.len()).max(start))
                .and_then(|window| match images.search_from {
                    None => window.iter().position(|&byte| byte == b'E'),
                    Some(from) => window.iter().enumerate().position(|(offset, &byte)| {
                        byte == b'E' || ends_stray_keyword(data, start + offset, from)
                    }),
                })
                .map(|offset| start + offset);
            match (candidate, scheduled) {
                (None, None) => return Ok(()),
                (Some(position), _) | (None, Some(position)) => index = position,
            }
        }
        let byte = data[index];
        if images.resumes.take(index) {
            let fresh = Lexer::new();
            if !lexers.contains(&fresh) {
                if lexers.len() == MAX_LEXERS {
                    return Err(Refusal::Ambiguous);
                }
                lexers.push(fresh);
            }
        }
        let mut dictionary_closed = false;
        let mut slot = 0;
        while slot < lexers.len() {
            match lexers[slot].step(byte)? {
                Event::None => slot += 1,
                Event::DictionaryClosed => {
                    dictionary_closed = true;
                    slot += 1;
                }
                Event::ImageData => {
                    lexers.swap_remove(slot);
                    images.start(data, index)?;
                }
                Event::Over => {
                    lexers.swap_remove(slot);
                }
            }
        }
        if dictionary_closed && stream_keyword_follows(data, index + 1) {
            return Err(Refusal::EmbeddedStream);
        }
        if lexers.len() > 1 {
            merge_equal(&mut lexers);
        }
        if images.from.is_some_and(|from| index >= from)
            && ends_image(data, index)
            && !certainly_rejected(&data[index + 2..])
        {
            images.resumes.push(index + 2, data.len())?;
            images.search_from.get_or_insert(index + 2);
        }
        if images
            .search_from
            .is_some_and(|from| ends_stray_keyword(data, index, from))
        {
            // hayro searches for the bytes `BI` wherever they are, in a string
            // or comment as well, and tries to read a dictionary behind them.
            let trial = Lexer::trial();
            if !lexers.contains(&trial) {
                if lexers.len() == MAX_LEXERS {
                    return Err(Refusal::Ambiguous);
                }
                lexers.push(trial);
            }
        }
        index += 1;
    }
    Ok(())
}

/// Whether the `I` at `index` completes the bytes `BI` at or after `from`.
fn ends_stray_keyword(data: &[u8], index: usize, from: usize) -> bool {
    index > from && data[index] == b'I' && data[index - 1] == b'B'
}

/// Places where a fresh lexer starts, in no particular order.
#[derive(Default)]
struct Positions {
    positions: [usize; MAX_RESUMES],
    len: usize,
}

impl Positions {
    /// Schedule a lexer at `position`, unless it is scheduled or past the end.
    fn push(&mut self, position: usize, end: usize) -> Result<(), Refusal> {
        if position >= end || self.positions[..self.len].contains(&position) {
            return Ok(());
        }
        if self.len == MAX_RESUMES {
            return Err(Refusal::Ambiguous);
        }
        self.positions[self.len] = position;
        self.len += 1;
        Ok(())
    }

    fn earliest(&self) -> Option<usize> {
        self.positions[..self.len].iter().copied().min()
    }

    /// Remove `position` and report whether it was scheduled.
    fn take(&mut self, position: usize) -> bool {
        match self.positions[..self.len]
            .iter()
            .position(|&scheduled| scheduled == position)
        {
            Some(slot) => {
                self.len -= 1;
                self.positions[slot] = self.positions[self.len];
                true
            }
            None => false,
        }
    }
}

/// Drop lexers whose state equals an earlier lexer's: they behave identically.
fn merge_equal(lexers: &mut Vec<Lexer>) {
    let mut slot = 1;
    while slot < lexers.len() {
        if lexers[..slot].contains(&lexers[slot]) {
            lexers.swap_remove(slot);
        } else {
            slot += 1;
        }
    }
}

/// Whether `EI` starts at `index` and is followed by whitespace or the end.
///
/// Only such an `EI` can end inline image data in hayro.
fn ends_image(data: &[u8], index: usize) -> bool {
    data[index] == b'E'
        && data.get(index + 1) == Some(&b'I')
        && data.get(index + 2).is_none_or(|&byte| is_space(byte))
}

/// Whether hayro certainly refuses the `EI` before `tail` as the image end.
///
/// hayro looks for the next `EI` or `BI` in `tail`. If an `EI` comes first and
/// any byte before it is non-ASCII, the earlier `EI` was inside binary data.
/// Every other case may be accepted by the heuristic that parses `tail`.
fn certainly_rejected(tail: &[u8]) -> bool {
    let mut non_ascii = false;
    for pair in tail.windows(2) {
        match pair {
            b"EI" => return non_ascii,
            b"BI" => return false,
            _ => non_ascii |= !pair[0].is_ascii(),
        }
    }
    false
}

/// Whether the bytes from `from` on, past whitespace and comments, read `stream`.
///
/// hayro reads a dictionary operand as a stream object when this holds.
fn stream_keyword_follows(data: &[u8], from: usize) -> bool {
    let mut position = from;
    while let Some(&byte) = data.get(position) {
        if is_space(byte) {
            position += 1;
        } else if byte == b'%' {
            while data
                .get(position)
                .is_some_and(|&byte| !matches!(byte, b'\n' | b'\r'))
            {
                position += 1;
            }
        } else {
            break;
        }
    }
    data[position.min(data.len())..].starts_with(b"stream")
}
