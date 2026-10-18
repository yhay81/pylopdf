//! Bound the depth of the colour space and function graphs that hayro builds.
//!
//! hayro 0.7 builds a colour space and a function by recursion over the
//! objects they name, with no depth bound (hayro 0.8 has none either):
//!
//! * an Indexed or Pattern colour space names a base space, a Separation or
//!   DeviceN space an alternate space and a tint transform, and an ICCBased
//!   space falls back to the `/Alternate` of a profile it cannot parse;
//! * a stitching function (type 3) builds every entry of its `/Functions`;
//! * a Type 4 function parses its `{ }` procedures by recursion.
//!
//! Each object can be an indirect one, so about 20,000 objects that name each
//! other in a chain overflow the stack of the interpreting thread, and a stack
//! overflow kills the process instead of raising. [`GraphGuard`] measures the
//! depth of everything that a root object reaches with an iterative walk and
//! refuses a chain deeper than [`MAX_CHAIN_DEPTH`]; it also hands every Type 4
//! program it meets to [`postscript_scan`](crate::postscript_scan).
//!
//! The walk does not model which entry plays which part. A node is any array,
//! dictionary, or stream that a root names, and its children are every
//! array, dictionary, or stream among the elements of an array, the entries of
//! `/Functions`, and the `/Alternate` of a dictionary or stream. That is a
//! superset of what hayro reads, in whatever order and under whatever names it
//! reads it, and it is exact for the structures that matter: the depth of a
//! chain counts its nodes. Elements that fail to resolve are skipped without
//! ending the array, because hayro's typed reads can step over them.
//!
//! hayro cuts a chain where it comes back to an object it is still building,
//! so a cycle costs one level per object on it. A walk that meets a cycle
//! cannot trust per-object depths, so it keeps nothing and bounds the depth by
//! the number of objects it expanded plus the deepest earlier result it
//! reused. That is an upper bound on every simple path, and real documents
//! have no cycles here.
//!
//! The walk is linear in the objects it reaches: each object is expanded once
//! and its depth is remembered, so shared sub-graphs cost nothing more. An
//! object is identified by its reference, or, for a direct object, by the
//! address of its bytes in the snapshot, which stays valid because decoded
//! object streams live as long as the snapshot.

use std::collections::{HashMap, HashSet, TryReserveError};
use std::hash::Hash;

use hayro::hayro_syntax::object::dict::keys::{ALTERNATE, FUNCTION_TYPE, FUNCTIONS};
use hayro::hayro_syntax::object::{Array, Dict, MaybeRef, Object, ObjectIdentifier};
use hayro::hayro_syntax::reader::{Reader, ReaderContext, ReaderExt};
use hayro::hayro_syntax::xref::XRef;

use crate::postscript_scan;

/// Deepest chain of nested colour spaces and functions that may reach hayro.
///
/// Real documents stay far below it: hayro's builders nested at most three deep
/// while it interpreted 54,088 pages of 5,683 real PDFs, and a node here is at
/// most twice a builder, because an ICCBased space is an array plus its profile
/// stream. The bound equals the 32 indirections that pylopdf follows elsewhere,
/// and hayro's frames for one level measured 0.9 to 1.4 KiB, so the limit costs
/// under 45 KiB of stack.
pub(crate) const MAX_CHAIN_DEPTH: usize = 32;

/// How the walk names an object.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub(crate) enum Identity {
    /// An object that a reference names.
    Indirect(ObjectIdentifier),
    /// A direct object, by the address and length of its bytes.
    Direct(usize, usize),
}

impl Identity {
    /// The words an error message uses for the object.
    pub(crate) fn describe(self) -> String {
        match self {
            Self::Indirect(id) => format!("{} {} R", id.obj_number, id.gen_number),
            Self::Direct(..) => "a direct object".to_owned(),
        }
    }
}

/// Why the colour spaces and functions below a root cannot be handed to hayro.
#[derive(Debug)]
pub(crate) enum Refusal {
    /// The chain that starts at this root is deeper than [`MAX_CHAIN_DEPTH`].
    Chain(Identity),
    /// This Type 4 function nests procedures deeper than
    /// [`postscript_scan::MAX_BRACE_DEPTH`].
    Program(Identity),
    /// The walk could not allocate its state.
    Allocation(TryReserveError),
}

/// The elements of an array, references unresolved.
///
/// `Array::raw_iter` unwraps the read of every element that `Array::skip`
/// accepted, and panics when the two disagree: a bare `+`, or a dictionary
/// followed by `stream`. hayro never iterates an array that way, so a guard that
/// did would turn input hayro renders into a `PanicException`. This iterator
/// reads the elements as `raw_iter` does, steps over one that does not read, and
/// never stops before the end of the array, like hayro's typed reads that pass
/// a reference they cannot resolve. Nested containers carry no reader context:
/// the walk follows every reference through `XRef::get` and reads only direct
/// values.
pub(crate) struct Elements<'a> {
    reader: Reader<'a>,
}

impl<'a> Elements<'a> {
    pub(crate) fn new(array: &Array<'a>) -> Self {
        Self {
            reader: Reader::new(array.data()),
        }
    }
}

impl<'a> Iterator for Elements<'a> {
    type Item = MaybeRef<Object<'a>>;

    fn next(&mut self) -> Option<Self::Item> {
        loop {
            self.reader.skip_white_spaces_and_comments();
            if self.reader.at_end() {
                return None;
            }
            let context = ReaderContext::dummy();
            if let Some(element) = self
                .reader
                .read_with_context::<MaybeRef<Object<'a>>>(&context)
            {
                return Some(element);
            }
            self.reader.skip::<MaybeRef<Object<'a>>>(false)?;
        }
    }
}

/// An array, dictionary, or stream that a root or an earlier node names.
struct Node<'a> {
    identity: Identity,
    object: Object<'a>,
}

/// The node an object makes, or none for the objects that cannot have children.
fn node<'a>(reference: Option<ObjectIdentifier>, object: Object<'a>) -> Option<Node<'a>> {
    let bytes = match &object {
        Object::Array(array) => array.data(),
        Object::Dict(dictionary) => dictionary.data(),
        Object::Stream(stream) => stream.dict().data(),
        _ => return None,
    };
    let identity = match reference {
        Some(id) => Identity::Indirect(id),
        None => Identity::Direct(bytes.as_ptr() as usize, bytes.len()),
    };
    Some(Node { identity, object })
}

/// The node that an array element or an entry of `/Functions` names.
fn resolve<'a>(xref: &'a XRef, item: MaybeRef<Object<'a>>) -> Option<Node<'a>> {
    match item {
        MaybeRef::Ref(reference) => {
            let id = ObjectIdentifier::from(reference);
            node(Some(id), xref.get::<Object<'a>>(id)?)
        }
        MaybeRef::NotRef(object) => node(None, object),
    }
}

/// The object under `key`, with the reference it was reached through, followed
/// from an empty chain of objects being read.
///
/// hayro keeps the chain of objects it is reading in a dictionary's reader
/// context and refuses a reference back into it as a cycle, so one dictionary
/// resolves fewer references the longer the path that led to it. The walk
/// remembers an object after the first path, so it reads every reference
/// afresh: no path may hide what another resolves, including the path hayro
/// takes when it draws. The walk finds cycles itself.
fn lookup<'a>(
    xref: &'a XRef,
    from: &Dict<'a>,
    key: &[u8],
) -> Option<(Option<ObjectIdentifier>, Object<'a>)> {
    match from.get_ref(key) {
        Some(reference) => {
            let id = ObjectIdentifier::from(reference);
            Some((Some(id), xref.get::<Object<'a>>(id)?))
        }
        None => Some((None, from.get::<Object<'a>>(key)?)),
    }
}

/// The node that `dictionary[key]` holds.
fn entry<'a>(xref: &'a XRef, dictionary: &Dict<'a>, key: &[u8]) -> Option<Node<'a>> {
    let (reference, object) = lookup(xref, dictionary, key)?;
    node(reference, object)
}

/// A node being measured, with the children still to visit.
struct Frame<'a> {
    identity: Identity,
    /// The elements of a colour space array or of a stitching function's
    /// `/Functions`, not yet visited.
    elements: Option<Elements<'a>>,
    /// The `/Alternate` of a dictionary or stream, visited before the elements.
    alternate: Option<Node<'a>>,
    /// The depth of the deepest finished child.
    deepest: u8,
}

impl<'a> Frame<'a> {
    /// Open a node, scanning the program of a Type 4 function on the way.
    fn open(xref: &'a XRef, node: &Node<'a>) -> Result<Self, Refusal> {
        let mut frame = Self {
            identity: node.identity,
            elements: None,
            alternate: None,
            deepest: 0,
        };
        match &node.object {
            Object::Array(array) => frame.elements = Some(Elements::new(array)),
            Object::Dict(dictionary) => frame.read(xref, dictionary),
            Object::Stream(stream) => {
                let dictionary = stream.dict();
                if dictionary.get::<u8>(FUNCTION_TYPE) == Some(4)
                    // A stream that fails to decode is no function for hayro either.
                    && stream
                        .decoded()
                        .is_ok_and(|program| postscript_scan::exceeds_depth(&program))
                {
                    return Err(Refusal::Program(node.identity));
                }
                frame.read(xref, dictionary);
            }
            _ => {}
        }
        Ok(frame)
    }

    fn read(&mut self, xref: &'a XRef, dictionary: &Dict<'a>) {
        self.elements = match lookup(xref, dictionary, FUNCTIONS) {
            Some((_, Object::Array(functions))) => Some(Elements::new(&functions)),
            _ => None,
        };
        self.alternate = entry(xref, dictionary, ALTERNATE);
    }

    fn next_child(&mut self, xref: &'a XRef) -> Option<Node<'a>> {
        if let Some(alternate) = self.alternate.take() {
            return Some(alternate);
        }
        self.elements
            .as_mut()?
            .find_map(|element| resolve(xref, element))
    }
}

/// What the walk has already measured for the current hayro snapshot.
#[derive(Default)]
pub(crate) struct GraphGuard {
    /// The depth of every object whose complete sub-graph was measured and found
    /// free of cycles, counting the object itself.
    depths: HashMap<Identity, u8>,
}

impl GraphGuard {
    /// Whether the object that `id` names was measured and cleared.
    pub(crate) fn is_cleared(&self, id: ObjectIdentifier) -> bool {
        self.depths.contains_key(&Identity::Indirect(id))
    }

    /// Measure the colour space or function that `dictionary[key]` holds.
    pub(crate) fn check_entry<'a>(
        &mut self,
        xref: &'a XRef,
        dictionary: &Dict<'a>,
        key: &[u8],
    ) -> Result<(), Refusal> {
        let reference = dictionary.get_ref(key).map(ObjectIdentifier::from);
        if reference.is_some_and(|id| self.is_cleared(id)) {
            return Ok(());
        }
        match entry(xref, dictionary, key) {
            Some(root) => self.check(xref, root),
            None => Ok(()),
        }
    }

    /// Measure the function, or each function of the array, that
    /// `dictionary[key]` holds.
    pub(crate) fn check_functions<'a>(
        &mut self,
        xref: &'a XRef,
        dictionary: &Dict<'a>,
        key: &[u8],
    ) -> Result<(), Refusal> {
        let reference = dictionary.get_ref(key).map(ObjectIdentifier::from);
        if reference.is_some_and(|id| self.is_cleared(id)) {
            return Ok(());
        }
        match lookup(xref, dictionary, key) {
            Some((_, Object::Array(functions))) => {
                for element in Elements::new(&functions) {
                    if let Some(root) = resolve(xref, element) {
                        self.check(xref, root)?;
                    }
                }
                Ok(())
            }
            Some((reference, object)) => match node(reference, object) {
                Some(root) => self.check(xref, root),
                None => Ok(()),
            },
            None => Ok(()),
        }
    }

    /// Measure everything below `root`, remember it, and refuse a chain that
    /// is too deep.
    fn check<'a>(&mut self, xref: &'a XRef, root: Node<'a>) -> Result<(), Refusal> {
        if self.depths.contains_key(&root.identity) {
            return Ok(());
        }
        let start = root.identity;
        let mut stack: Vec<Frame<'a>> = Vec::new();
        stack
            .try_reserve_exact(MAX_CHAIN_DEPTH)
            .map_err(Refusal::Allocation)?;
        // The nodes on the current path, and the depth of those finished so far.
        let mut path = HashSet::new();
        let mut measured = HashMap::new();
        // The deepest earlier result that this walk reused.
        let mut inherited = 0u8;
        let mut cyclic = false;
        let mut entering = Some(root);
        loop {
            if let Some(node) = entering.take() {
                if stack.len() == MAX_CHAIN_DEPTH {
                    return Err(Refusal::Chain(start));
                }
                insert_set(&mut path, node.identity)?;
                stack.push(Frame::open(xref, &node)?);
                continue;
            }
            let Some(frame) = stack.last_mut() else {
                break;
            };
            if let Some(child) = frame.next_child(xref) {
                if path.contains(&child.identity) {
                    // hayro cuts the chain where it comes back to itself.
                    cyclic = true;
                } else if let Some(&depth) = measured.get(&child.identity) {
                    frame.deepest = frame.deepest.max(depth);
                } else if let Some(&depth) = self.depths.get(&child.identity) {
                    inherited = inherited.max(depth);
                    frame.deepest = frame.deepest.max(depth);
                } else {
                    entering = Some(child);
                }
                continue;
            }
            let finished = stack.pop().expect("a frame was just borrowed");
            let depth = finished.deepest + 1;
            if usize::from(depth) > MAX_CHAIN_DEPTH {
                return Err(Refusal::Chain(start));
            }
            path.remove(&finished.identity);
            insert_map(&mut measured, finished.identity, depth)?;
            if let Some(parent) = stack.last_mut() {
                parent.deepest = parent.deepest.max(depth);
            }
        }
        if cyclic {
            // Per-object depths may be too small inside a cycle, so keep none.
            // A simple path visits each object this walk expanded at most once
            // and cannot return from an earlier result to one of them.
            let bound = measured.len() + usize::from(inherited);
            return if bound > MAX_CHAIN_DEPTH {
                Err(Refusal::Chain(start))
            } else {
                Ok(())
            };
        }
        self.depths
            .try_reserve(measured.len())
            .map_err(Refusal::Allocation)?;
        self.depths.extend(measured);
        Ok(())
    }
}

/// Insert after reserving fallibly.
fn insert_set<T: Eq + Hash>(set: &mut HashSet<T>, value: T) -> Result<(), Refusal> {
    set.try_reserve(1).map_err(Refusal::Allocation)?;
    set.insert(value);
    Ok(())
}

fn insert_map<K: Eq + Hash, V>(map: &mut HashMap<K, V>, key: K, value: V) -> Result<(), Refusal> {
    map.try_reserve(1).map_err(Refusal::Allocation)?;
    map.insert(key, value);
    Ok(())
}
