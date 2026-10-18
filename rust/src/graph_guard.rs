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
//! ending the array, because hayro's typed reads can step over them. A
//! `/Functions` array that is an object of its own is a node like any other
//! array, one level more than a direct one, which costs real documents nothing.
//!
//! hayro cuts a chain where it comes back to an object it is still building,
//! so a cycle costs one level per object on it. The walk therefore finds the
//! strongly connected components of the graph (Tarjan's algorithm, run
//! iteratively): a simple path visits each node of a component at most once
//! and cannot return to a component it left, so the depth below any node of a
//! component is at most its size plus the depth of the deepest node that the
//! component names outside it. A node outside every cycle is a component of
//! one and gets its exact depth. Real documents have no cycles here.
//!
//! The walk is linear in the bytes it reaches, however often they are named:
//! each object is expanded once and its depth, or the bound for its component,
//! is remembered, and a reference is classified by its identity before the
//! object is parsed, with an object that cannot have children remembered as a
//! leaf. A document can name one large array, dictionary, or string from
//! thousands of places, and reading it each time would multiply their sizes.
//! An object is identified by its reference, or, for a direct object, by the
//! address of its bytes in the snapshot, which stays valid because decoded
//! object streams live as long as the snapshot.

use std::collections::{HashMap, TryReserveError};
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

/// A node that a root or an earlier node names, not read yet.
///
/// A reference is classified by its identity before anything is parsed: parsing
/// a large array costs as much as walking it, and a document can name one array
/// from thousands of places. Only a reference the walk has never met is read.
enum Child<'a> {
    Reference(ObjectIdentifier),
    Direct(Node<'a>),
}

impl Child<'_> {
    fn identity(&self) -> Identity {
        match self {
            Self::Reference(id) => Identity::Indirect(*id),
            Self::Direct(node) => node.identity,
        }
    }
}

/// The child that an array element names, if it can have children.
fn element(item: MaybeRef<Object<'_>>) -> Option<Child<'_>> {
    match item {
        MaybeRef::Ref(reference) => Some(Child::Reference(ObjectIdentifier::from(reference))),
        MaybeRef::NotRef(object) => node(None, object).map(Child::Direct),
    }
}

/// The child that `dictionary[key]` names, if it can have children.
fn entry<'a>(dictionary: &Dict<'a>, key: &[u8]) -> Option<Child<'a>> {
    match dictionary.get_ref(key) {
        Some(reference) => Some(Child::Reference(ObjectIdentifier::from(reference))),
        None => node(None, dictionary.get::<Object<'a>>(key)?).map(Child::Direct),
    }
}

/// Read the object that a reference names.
///
/// hayro keeps the chain of objects it is reading in a dictionary's reader
/// context and refuses a reference back into it as a cycle, so one dictionary
/// resolves fewer references the longer the path that led to it. The walk
/// remembers an object after the first path, so it reads every reference
/// afresh, from an empty chain: no path may hide what another resolves,
/// including the path hayro takes when it draws. The walk finds cycles itself.
fn resolve<'a>(xref: &'a XRef, id: ObjectIdentifier) -> Option<Node<'a>> {
    node(Some(id), xref.get::<Object<'a>>(id)?)
}

/// A node being measured, with the children still to visit.
struct Frame<'a> {
    identity: Identity,
    /// The `/Alternate` of a dictionary or stream, visited first.
    alternate: Option<Child<'a>>,
    /// The `/Functions` array of a dictionary or stream when it is an object of
    /// its own: a node like any other array.
    functions: Option<Child<'a>>,
    /// The elements of a colour space array, or of a direct `/Functions` array,
    /// not yet visited.
    elements: Option<Elements<'a>>,
    /// How many nodes the walk had reached before this one.
    order: usize,
    /// The smallest `order` among the nodes of an incomplete component that this
    /// node or anything below it names.
    low: usize,
    /// The depth of the deepest complete node outside this node's component
    /// that it names.
    exit: u8,
    /// Where the finished nodes of this node's component start in the walk's list
    /// of incomplete components.
    base: usize,
}

/// What a walk knows about a node it has reached.
#[derive(Clone, Copy)]
enum Reached {
    /// The node's component is incomplete; it was the `order`-th node reached.
    Open(usize),
    /// The node's component is complete; nothing below it is deeper than this.
    Done(u8),
}

/// A finished node whose component is incomplete.
struct Member {
    identity: Identity,
    /// The depth of the deepest complete node outside the component that it names.
    exit: u8,
}

impl<'a> Frame<'a> {
    /// Open a node, scanning the program of a Type 4 function on the way.
    fn open(node: &Node<'a>, order: usize, base: usize) -> Result<Self, Refusal> {
        let mut frame = Self {
            identity: node.identity,
            alternate: None,
            functions: None,
            elements: None,
            order,
            low: order,
            exit: 0,
            base,
        };
        match &node.object {
            Object::Array(array) => frame.elements = Some(Elements::new(array)),
            Object::Dict(dictionary) => frame.read(dictionary),
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
                frame.read(dictionary);
            }
            _ => {}
        }
        Ok(frame)
    }

    fn read(&mut self, dictionary: &Dict<'a>) {
        match dictionary.get_ref(FUNCTIONS) {
            Some(reference) => {
                self.functions = Some(Child::Reference(ObjectIdentifier::from(reference)));
            }
            None => {
                if let Some(Object::Array(functions)) = dictionary.get::<Object<'a>>(FUNCTIONS) {
                    self.elements = Some(Elements::new(&functions));
                }
            }
        }
        self.alternate = entry(dictionary, ALTERNATE);
    }

    fn next_child(&mut self) -> Option<Child<'a>> {
        if let Some(child) = self.alternate.take().or_else(|| self.functions.take()) {
            return Some(child);
        }
        self.elements.as_mut()?.find_map(element)
    }
}

/// What the walk has already measured for the current hayro snapshot.
#[derive(Default)]
pub(crate) struct GraphGuard {
    /// The depth of every object whose complete sub-graph was measured, counting
    /// the object itself: exact outside a cycle, and for the nodes of a cycle the
    /// size of its component plus the deepest node it names outside. An object
    /// that cannot have children has depth zero. Everything below a remembered
    /// object is remembered too.
    depths: HashMap<Identity, u8>,
}

impl GraphGuard {
    /// Measure the colour space or function that `dictionary[key]` holds.
    pub(crate) fn check_entry<'a>(
        &mut self,
        xref: &'a XRef,
        dictionary: &Dict<'a>,
        key: &[u8],
    ) -> Result<(), Refusal> {
        match entry(dictionary, key) {
            Some(root) => self.check_child(xref, root),
            None => Ok(()),
        }
    }

    /// Measure the function, or each function of the direct array, that
    /// `dictionary[key]` holds. A referenced array is a node of its own.
    pub(crate) fn check_functions<'a>(
        &mut self,
        xref: &'a XRef,
        dictionary: &Dict<'a>,
        key: &[u8],
    ) -> Result<(), Refusal> {
        if dictionary.get_ref(key).is_none()
            && let Some(Object::Array(functions)) = dictionary.get::<Object<'a>>(key)
        {
            for root in Elements::new(&functions).filter_map(element) {
                self.check_child(xref, root)?;
            }
            return Ok(());
        }
        self.check_entry(xref, dictionary, key)
    }

    /// Measure a root, reading it only if the walk has not met it.
    fn check_child<'a>(&mut self, xref: &'a XRef, root: Child<'a>) -> Result<(), Refusal> {
        match root {
            Child::Direct(root) => self.check(xref, root),
            Child::Reference(id) => {
                let identity = Identity::Indirect(id);
                if self.depths.contains_key(&identity) {
                    return Ok(());
                }
                match resolve(xref, id) {
                    Some(root) => self.check(xref, root),
                    None => insert_map(&mut self.depths, identity, 0),
                }
            }
        }
    }

    /// Measure everything below `root`, remember it, and refuse a chain that
    /// is too deep.
    ///
    /// A component is a set of nodes that all reach one another, so a cycle is
    /// one. It closes at the first of its nodes that the walk reached, once
    /// everything below that node has finished.
    fn check<'a>(&mut self, xref: &'a XRef, root: Node<'a>) -> Result<(), Refusal> {
        if self.depths.contains_key(&root.identity) {
            return Ok(());
        }
        let start = root.identity;
        let mut stack: Vec<Frame<'a>> = Vec::new();
        stack
            .try_reserve_exact(MAX_CHAIN_DEPTH)
            .map_err(Refusal::Allocation)?;
        let mut reached: HashMap<Identity, Reached> = HashMap::new();
        let mut members: Vec<Member> = Vec::new();
        let mut entering = Some(root);
        loop {
            if let Some(node) = entering.take() {
                if stack.len() == MAX_CHAIN_DEPTH {
                    return Err(Refusal::Chain(start));
                }
                let order = reached.len();
                insert_map(&mut reached, node.identity, Reached::Open(order))?;
                stack.push(Frame::open(&node, order, members.len())?);
                continue;
            }
            let Some(frame) = stack.last_mut() else {
                break;
            };
            if let Some(child) = frame.next_child() {
                let identity = child.identity();
                let known = reached
                    .get(&identity)
                    .copied()
                    .or_else(|| self.depths.get(&identity).copied().map(Reached::Done));
                match known {
                    // The node itself or a node that reaches it: one component.
                    Some(Reached::Open(order)) => frame.low = frame.low.min(order),
                    Some(Reached::Done(depth)) => frame.exit = frame.exit.max(depth),
                    None => match child {
                        Child::Direct(node) => entering = Some(node),
                        Child::Reference(id) => match resolve(xref, id) {
                            Some(node) => entering = Some(node),
                            // Nothing below it, and not worth reading again.
                            None => insert_map(&mut reached, identity, Reached::Done(0))?,
                        },
                    },
                }
                continue;
            }
            let finished = stack.pop().expect("a frame was just borrowed");
            reserve_one(&mut members)?;
            members.push(Member {
                identity: finished.identity,
                exit: finished.exit,
            });
            if finished.low < finished.order {
                // A node reached earlier on the path reaches this one and is
                // reached from it, so the component is not closed yet.
                if let Some(parent) = stack.last_mut() {
                    parent.low = parent.low.min(finished.low);
                }
                continue;
            }
            // Nothing earlier is reached from here, so this node closes its
            // component: every node that finished since this one was reached.
            let component = &members[finished.base..];
            let exit = component
                .iter()
                .map(|member| member.exit)
                .max()
                .unwrap_or(0);
            let bound = match u8::try_from(component.len() + usize::from(exit)) {
                Ok(bound) if usize::from(bound) <= MAX_CHAIN_DEPTH => bound,
                _ => return Err(Refusal::Chain(start)),
            };
            for member in component {
                if let Some(state) = reached.get_mut(&member.identity) {
                    *state = Reached::Done(bound);
                }
            }
            members.truncate(finished.base);
            if let Some(parent) = stack.last_mut() {
                parent.exit = parent.exit.max(bound);
            }
        }
        self.depths
            .try_reserve(reached.len())
            .map_err(Refusal::Allocation)?;
        self.depths.extend(
            reached
                .into_iter()
                .filter_map(|(identity, state)| match state {
                    Reached::Done(depth) => Some((identity, depth)),
                    Reached::Open(_) => None,
                }),
        );
        Ok(())
    }
}

/// Insert after reserving fallibly.
fn insert_map<K: Eq + Hash, V>(map: &mut HashMap<K, V>, key: K, value: V) -> Result<(), Refusal> {
    map.try_reserve(1).map_err(Refusal::Allocation)?;
    map.insert(key, value);
    Ok(())
}

/// Make room for one more element, so that the push after it cannot abort.
fn reserve_one<T>(list: &mut Vec<T>) -> Result<(), Refusal> {
    list.try_reserve(1).map_err(Refusal::Allocation)
}
