//! Decide whether hayro should read a document's original bytes.
//!
//! hayro-syntax 0.7 and 0.8 parse the member table of an object stream, the
//! `/N` pairs of object number and offset that head its decoded data, from
//! scratch for every lookup of a member and keep nothing. Every lookup costs
//! about 15 ns for each member of the container, so reading most of the
//! members of a container of N costs about N² of those: a page whose 32,768
//! annotations are members of one 65,542-member container took hayro 34 s to
//! interpret, and the content guard's pass over the same array took another
//! 34 s. Merely opening a 723-page book whose objects share one 14,947-member
//! container took hayro 0.68 s, because every page lookup rebuilds the table.
//!
//! Serializing lopdf's graph writes every member as an ordinary object, which
//! hayro finds in constant time, for about 7 ns per input byte. When one
//! container is large enough to dominate every lookup and the tables that a
//! pass over its members would rebuild outweigh the serialization by a wide
//! margin, the serialized graph is the cheaper snapshot for everything that
//! follows, the content guard's walk included.

use lopdf::{Document, Object};

/// Members of one container that make every lookup of a member cost about
/// 60 µs, many times the lookup itself. Ordinary containers hold at most a few
/// hundred, and a hundred containers of 500 members, such as a tagged PDF of
/// fifty thousand objects, cost hayro no measurable time.
const MIN_LARGEST_CONTAINER: u128 = 4_096;

/// Rebuilt table entries that must be expected per input byte. Serializing
/// costs about 7 ns per byte and a rebuilt entry 15 ns, but a pass looks up
/// only some of the members, so the entries must outnumber the bytes by a
/// margin: a large file with a modest container keeps its original bytes.
const WORK_PER_INPUT_BYTE: u128 = 8;

/// The declared member counts of a document's object streams.
#[derive(Default)]
pub(crate) struct MemberTables {
    /// Members of the largest object stream.
    largest: u128,
    /// The squares of all counts, which is the number of table entries that
    /// reading every member of every object stream would parse.
    rebuilt: u128,
}

impl MemberTables {
    /// Read `/N` of every object stream as hayro does; an indirect or
    /// malformed one counts as none. An inflated `/N` can only move the choice
    /// of snapshot, never what hayro reads from it, so the sum saturates.
    pub(crate) fn of(doc: &Document) -> Self {
        doc.objects
            .values()
            .filter_map(|object| {
                let Object::Stream(stream) = object else {
                    return None;
                };
                if !stream.dict.has_type(b"ObjStm") {
                    return None;
                }
                match stream.dict.get(b"N") {
                    Ok(Object::Integer(members)) => u128::try_from(*members).ok(),
                    _ => None,
                }
            })
            .fold(Self::default(), |tables, members| Self {
                largest: tables.largest.max(members),
                rebuilt: tables
                    .rebuilt
                    .saturating_add(members.saturating_mul(members)),
            })
    }

    /// Whether hayro should read the serialized graph instead of `input_len`
    /// original bytes.
    ///
    /// Documents without a container of thousands of members, such as every
    /// file in the bundled corpus, always keep their original bytes.
    pub(crate) fn prefer_serialized_graph(&self, input_len: usize) -> bool {
        self.largest >= MIN_LARGEST_CONTAINER
            && self.rebuilt >= (input_len as u128).saturating_mul(WORK_PER_INPUT_BYTE)
    }
}
