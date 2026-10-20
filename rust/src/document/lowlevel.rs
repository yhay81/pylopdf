//! The typed low-level object model: reads and writes of the lopdf graph
//! for `pylopdf.Document.objects`, `trailer`, `catalog`, and `Page.obj`.
//!
//! Python handles address a container by its root, the trailer or one
//! indirect object, and a path of dictionary keys and array indexes through
//! direct containers. Values cross the boundary in a small tagged form that
//! `src/pylopdf/_objects.py` wraps:
//!
//! - `None`, `bool`, `int`, and `float` stand for themselves;
//! - `("n", bytes)` is a name and `("s", bytes, hexadecimal)` a string;
//! - `("a" | "d" | "t", root)` is an array, dictionary, or stream, with
//!   `root` the `(number, generation)` of an indirect one and `None` for a
//!   direct one, which Python addresses by extending its path;
//! - on input, `("a", [values])` and `("d", [(key, value)])` build direct
//!   containers, `("r", number, generation)` references an existing object,
//!   and `("c", root, path)` copies the container a handle addresses.
//!
//! References are resolved on read, as ISO 32000-2 §7.3.10 reads them: a
//! reference to a missing object or to null reads as `None`.

use pyo3::IntoPyObjectExt;
use pyo3::types::{PyBool, PyBytes, PyFloat, PyInt, PyTuple};

use super::*;

pyo3::create_exception!(
    pylopdf,
    StaleObjectError,
    PdfError,
    "Use of a low-level object handle whose object no longer exists or was renumbered."
);

/// References followed before a chain counts as cyclic or over-long, the
/// indirection bound pylopdf applies elsewhere.
const MAX_REFERENCE_CHAIN: usize = 32;

/// Nesting of arrays and dictionaries accepted in one written value.
pub(super) const MAX_VALUE_DEPTH: usize = 32;

/// Decoded stream data returned by default, as for attachments.
pub(super) const DEFAULT_MAX_STREAM_DATA_SIZE: usize = 64 * 1024 * 1024;

/// One step of a handle's path: a dictionary key or an array index.
#[derive(FromPyObject)]
pub(super) enum Step {
    Index(usize),
    Key(Vec<u8>),
}

/// The object a handle's path starts from.
pub(super) type Root = Option<(u32, u16)>;

enum Node<'a> {
    Dictionary(&'a Dictionary),
    Array(&'a Vec<Object>),
}

enum NodeMut<'a> {
    Dictionary(&'a mut Dictionary),
    Array(&'a mut Vec<Object>),
}

fn stale(message: impl Into<String>) -> PyErr {
    StaleObjectError::new_err(message.into())
}

fn describe_root(root: Root) -> String {
    match root {
        None => "the trailer".to_owned(),
        Some((number, generation)) => format!("object {number} {generation} R"),
    }
}

fn describe_path(root: Root, path: &[Step]) -> String {
    let mut text = describe_root(root);
    for step in path {
        match step {
            Step::Index(index) => text.push_str(&format!("[{index}]")),
            Step::Key(key) => {
                text.push('/');
                text.push_str(&String::from_utf8_lossy(key));
            }
        }
    }
    text
}

fn node_of(object: &Object) -> Option<Node<'_>> {
    match object {
        Object::Dictionary(dictionary) => Some(Node::Dictionary(dictionary)),
        Object::Stream(stream) => Some(Node::Dictionary(&stream.dict)),
        Object::Array(array) => Some(Node::Array(array)),
        _ => None,
    }
}

fn node_of_mut(object: &mut Object) -> Option<NodeMut<'_>> {
    match object {
        Object::Dictionary(dictionary) => Some(NodeMut::Dictionary(dictionary)),
        Object::Stream(stream) => Some(NodeMut::Dictionary(&mut stream.dict)),
        Object::Array(array) => Some(NodeMut::Array(array)),
        _ => None,
    }
}

fn root_object(doc: &Document, id: ObjectId) -> PyResult<&Object> {
    doc.objects
        .get(&id)
        .ok_or_else(|| stale(format!("{} no longer exists", describe_root(Some(id)))))
}

/// The object at the end of a direct path, or the root object itself.
fn path_object<'a>(doc: &'a Document, id: ObjectId, path: &[Step]) -> PyResult<&'a Object> {
    let mut object = root_object(doc, id)?;
    for (depth, step) in path.iter().enumerate() {
        let child = node_of(object).and_then(|node| child_of(node, step));
        object = child.ok_or_else(|| {
            stale(format!(
                "{} no longer holds a container",
                describe_path(Some(id), &path[..=depth])
            ))
        })?;
    }
    Ok(object)
}

fn child_of<'a>(node: Node<'a>, step: &Step) -> Option<&'a Object> {
    match (node, step) {
        (Node::Dictionary(dictionary), Step::Key(key)) => dictionary.get(key).ok(),
        (Node::Array(array), Step::Index(index)) => array.get(*index),
        _ => None,
    }
}

fn child_of_mut<'a>(node: NodeMut<'a>, step: &Step) -> Option<&'a mut Object> {
    match (node, step) {
        (NodeMut::Dictionary(dictionary), Step::Key(key)) => dictionary.get_mut(key).ok(),
        (NodeMut::Array(array), Step::Index(index)) => array.get_mut(*index),
        _ => None,
    }
}

/// Address the container that a handle names.
fn container<'a>(doc: &'a Document, root: Root, path: &[Step]) -> PyResult<Node<'a>> {
    let not_container = || {
        stale(format!(
            "{} no longer holds a container",
            describe_path(root, path)
        ))
    };
    match root {
        None => {
            let Some((first, rest)) = path.split_first() else {
                return Ok(Node::Dictionary(&doc.trailer));
            };
            let mut object =
                child_of(Node::Dictionary(&doc.trailer), first).ok_or_else(not_container)?;
            for step in rest {
                object = node_of(object)
                    .and_then(|node| child_of(node, step))
                    .ok_or_else(not_container)?;
            }
            node_of(object).ok_or_else(not_container)
        }
        Some(id) => node_of(path_object(doc, id, path)?).ok_or_else(not_container),
    }
}

fn container_mut<'a>(doc: &'a mut Document, root: Root, path: &[Step]) -> PyResult<NodeMut<'a>> {
    let description = describe_path(root, path);
    let not_container = move || stale(format!("{description} no longer holds a container"));
    let mut object = match root {
        None => {
            let Some((first, rest)) = path.split_first() else {
                return Ok(NodeMut::Dictionary(&mut doc.trailer));
            };
            let mut object = child_of_mut(NodeMut::Dictionary(&mut doc.trailer), first)
                .ok_or_else(not_container.clone())?;
            for step in rest {
                object = node_of_mut(object)
                    .and_then(|node| child_of_mut(node, step))
                    .ok_or_else(not_container.clone())?;
            }
            return node_of_mut(object).ok_or_else(not_container);
        }
        Some(id) => doc
            .objects
            .get_mut(&id)
            .ok_or_else(|| stale(format!("{} no longer exists", describe_root(Some(id)))))?,
    };
    for step in path {
        object = node_of_mut(object)
            .and_then(|node| child_of_mut(node, step))
            .ok_or_else(not_container.clone())?;
    }
    node_of_mut(object).ok_or_else(not_container)
}

/// Resolve a reference chain to the object it ends at, or `None` for a
/// missing object.
fn resolve_reference(doc: &Document, mut id: ObjectId) -> PyResult<Option<(ObjectId, &Object)>> {
    for _ in 0..MAX_REFERENCE_CHAIN {
        match doc.objects.get(&id) {
            None => return Ok(None),
            Some(Object::Reference(next)) => id = *next,
            Some(object) => return Ok(Some((id, object))),
        }
    }
    Err(PdfError::new_err(format!(
        "reference chain from object {} {} R is cyclic or longer than {MAX_REFERENCE_CHAIN} references",
        id.0, id.1
    )))
}

/// Convert a value for Python, resolving a reference first. `indirect`
/// names the object that `object` is, if any.
fn value_to_py(
    py: Python<'_>,
    doc: &Document,
    object: &Object,
    indirect: Option<ObjectId>,
) -> PyResult<Py<PyAny>> {
    match object {
        Object::Null => Ok(py.None()),
        Object::Boolean(value) => value.into_py_any(py),
        Object::Integer(value) => value.into_py_any(py),
        Object::Real(value) => f64::from(*value).into_py_any(py),
        Object::Name(name) => ("n", PyBytes::new(py, name)).into_py_any(py),
        Object::String(text, format) => (
            "s",
            PyBytes::new(py, text),
            matches!(format, StringFormat::Hexadecimal),
        )
            .into_py_any(py),
        Object::Array(_) => ("a", indirect).into_py_any(py),
        Object::Dictionary(_) => ("d", indirect).into_py_any(py),
        Object::Stream(_) => ("t", indirect).into_py_any(py),
        Object::Reference(id) => match resolve_reference(doc, *id)? {
            None | Some((_, Object::Null)) => Ok(py.None()),
            Some((target, resolved)) => value_to_py(py, doc, resolved, Some(target)),
        },
    }
}

/// The highest generation stored under an object number.
pub(super) fn object_id(doc: &Document, number: u32) -> Option<ObjectId> {
    doc.objects
        .range((number, 0)..=(number, u16::MAX))
        .next_back()
        .map(|(id, _)| *id)
}

/// Implements [`_Document::object_numbers`].
pub(super) fn object_numbers(this: &_Document) -> Vec<u32> {
    let mut numbers: Vec<u32> = this.doc.objects.keys().map(|(number, _)| *number).collect();
    numbers.dedup();
    numbers
}

/// Implements [`_Document::object_lookup`].
pub(super) fn object_lookup(
    this: &_Document,
    py: Python<'_>,
    number: u32,
) -> PyResult<Option<(u16, Py<PyAny>)>> {
    let Some(id) = object_id(&this.doc, number) else {
        return Ok(None);
    };
    let object = root_object(&this.doc, id)?;
    Ok(Some((id.1, value_to_py(py, &this.doc, object, Some(id))?)))
}

/// Implements [`_Document::object_get`].
pub(super) fn object_get(
    this: &_Document,
    py: Python<'_>,
    root: Root,
    path: Vec<Step>,
    step: Step,
) -> PyResult<Py<PyAny>> {
    let node = container(&this.doc, root, &path)?;
    let child = match (&node, &step) {
        (Node::Dictionary(dictionary), Step::Key(key)) => dictionary.get(key).ok(),
        (Node::Array(array), Step::Index(index)) => array.get(*index),
        (Node::Dictionary(_), Step::Index(_)) => {
            return Err(pyo3::exceptions::PyTypeError::new_err(
                "dictionary keys are strings",
            ));
        }
        (Node::Array(_), Step::Key(_)) => {
            return Err(pyo3::exceptions::PyTypeError::new_err(
                "array indexes are integers",
            ));
        }
    };
    let Some(child) = child else {
        return Err(match step {
            Step::Key(key) => {
                pyo3::exceptions::PyKeyError::new_err(String::from_utf8_lossy(&key).into_owned())
            }
            Step::Index(index) => {
                pyo3::exceptions::PyIndexError::new_err(format!("array index {index} out of range"))
            }
        });
    };
    value_to_py(py, &this.doc, child, None)
}

/// Implements [`_Document::object_keys`].
pub(super) fn object_keys(this: &_Document, root: Root, path: Vec<Step>) -> PyResult<Vec<Vec<u8>>> {
    match container(&this.doc, root, &path)? {
        Node::Dictionary(dictionary) => Ok(dictionary.iter().map(|(key, _)| key.clone()).collect()),
        Node::Array(_) => Err(pyo3::exceptions::PyTypeError::new_err(
            "an array has no keys",
        )),
    }
}

/// Implements [`_Document::object_len`].
pub(super) fn object_len(this: &_Document, root: Root, path: Vec<Step>) -> PyResult<usize> {
    Ok(match container(&this.doc, root, &path)? {
        Node::Dictionary(dictionary) => dictionary.len(),
        Node::Array(array) => array.len(),
    })
}

/// Convert a tagged Python value into a lopdf object, checking that every
/// reference names an existing object.
fn value_from_py(doc: &Document, value: &Bound<'_, PyAny>, depth: usize) -> PyResult<Object> {
    if value.is_none() {
        return Ok(Object::Null);
    }
    if value.is_instance_of::<PyBool>() {
        return Ok(Object::Boolean(value.extract()?));
    }
    if value.is_instance_of::<PyInt>() {
        return Ok(Object::Integer(value.extract()?));
    }
    if value.is_instance_of::<PyFloat>() {
        let number: f64 = value.extract()?;
        // lopdf stores reals as f32, as it reads them.
        #[allow(clippy::cast_possible_truncation)]
        let real = number as f32;
        if !real.is_finite() {
            return Err(PyValueError::new_err(format!(
                "PDF numbers must be finite single-precision values: {number}"
            )));
        }
        return Ok(Object::Real(real));
    }
    let tuple = value.extract::<Bound<'_, PyTuple>>()?;
    let tag: String = tuple.get_item(0)?.extract()?;
    match tag.as_str() {
        "n" => Ok(Object::Name(tuple.get_item(1)?.extract()?)),
        "s" => {
            let hexadecimal: bool = tuple.get_item(2)?.extract()?;
            let format = if hexadecimal {
                StringFormat::Hexadecimal
            } else {
                StringFormat::Literal
            };
            Ok(Object::String(tuple.get_item(1)?.extract()?, format))
        }
        "r" => {
            let id: ObjectId = (tuple.get_item(1)?.extract()?, tuple.get_item(2)?.extract()?);
            if !doc.objects.contains_key(&id) {
                return Err(stale(format!(
                    "{} no longer exists",
                    describe_root(Some(id))
                )));
            }
            Ok(Object::Reference(id))
        }
        "a" | "d" => {
            if depth >= MAX_VALUE_DEPTH {
                return Err(limit_err(
                    "object_value_depth",
                    format!(
                        "a written value nests arrays and dictionaries deeper than {MAX_VALUE_DEPTH} levels"
                    ),
                ));
            }
            let items: Vec<Bound<'_, PyAny>> = tuple.get_item(1)?.extract()?;
            if tag == "a" {
                let mut array = Vec::new();
                array.try_reserve_exact(items.len()).map_err(|error| {
                    PdfError::new_err(format!("failed to allocate a written array: {error}"))
                })?;
                for item in &items {
                    array.push(direct_value(doc, item, depth + 1)?);
                }
                Ok(Object::Array(array))
            } else {
                let mut dictionary = Dictionary::new();
                for item in &items {
                    let (key, entry): (Vec<u8>, Bound<'_, PyAny>) = item.extract()?;
                    dictionary.set(key, direct_value(doc, &entry, depth + 1)?);
                }
                Ok(Object::Dictionary(dictionary))
            }
        }
        "c" => {
            let root: Root = tuple.get_item(1)?.extract()?;
            let path: Vec<Step> = tuple.get_item(2)?.extract()?;
            let copied = match root {
                None if path.is_empty() => Object::Dictionary(doc.trailer.clone()),
                None => match container(doc, root, &path)? {
                    Node::Dictionary(dictionary) => Object::Dictionary(dictionary.clone()),
                    Node::Array(array) => Object::Array(array.clone()),
                },
                Some(id) => path_object(doc, id, &path)?.clone(),
            };
            Ok(copied)
        }
        other => Err(PyValueError::new_err(format!(
            "unknown object value tag {other:?}"
        ))),
    }
}

/// Convert a value stored inside a container, where a stream cannot be.
fn direct_value(doc: &Document, value: &Bound<'_, PyAny>, depth: usize) -> PyResult<Object> {
    match value_from_py(doc, value, depth)? {
        Object::Stream(_) => Err(PyValueError::new_err(
            "a stream must be an indirect object; store a reference to it instead",
        )),
        other => Ok(other),
    }
}

impl _Document {
    /// Commit a low-level write and report whether it changed the page order.
    ///
    /// Any write can retype an object that the page tree reaches, so the
    /// order is compared with a fresh walk. The walk caches the new page
    /// index, which the next page access would build anyway. Every derived
    /// cache and every remembered placement is dropped, and so are the
    /// pages whose contents drawing had isolated, because a write can
    /// replace their `/Contents`.
    fn commit_object_write(
        &mut self,
        write: impl FnOnce(&mut Document) -> PyResult<()>,
    ) -> PyResult<bool> {
        let before = self.doc.owned_page_ids().ok();
        write(&mut self.doc)?;
        self.invalidate_hayro_pdf();
        self.isolated_content_pages.clear();
        let after = self.doc.page_ids().ok();
        Ok(match (before.as_deref(), after) {
            (Some(before), Some(after)) => before != after,
            _ => true,
        })
    }
}

/// Implements [`_Document::object_set`].
pub(super) fn object_set(
    this: &mut _Document,
    root: Root,
    path: Vec<Step>,
    step: Step,
    value: &Bound<'_, PyAny>,
) -> PyResult<bool> {
    let object = direct_value(&this.doc, value, 1)?;
    // Check the target before the write so a refusal changes nothing.
    container(&this.doc, root, &path)?;
    this.commit_object_write(|doc| match (container_mut(doc, root, &path)?, step) {
        (NodeMut::Dictionary(dictionary), Step::Key(key)) => {
            dictionary.set(key, object);
            Ok(())
        }
        (NodeMut::Array(array), Step::Index(index)) => {
            let slot = array.get_mut(index).ok_or_else(|| {
                pyo3::exceptions::PyIndexError::new_err(format!("array index {index} out of range"))
            })?;
            *slot = object;
            Ok(())
        }
        (NodeMut::Dictionary(_), Step::Index(_)) => Err(pyo3::exceptions::PyTypeError::new_err(
            "dictionary keys are strings",
        )),
        (NodeMut::Array(_), Step::Key(_)) => Err(pyo3::exceptions::PyTypeError::new_err(
            "array indexes are integers",
        )),
    })
}

/// Implements [`_Document::object_delete`].
pub(super) fn object_delete(
    this: &mut _Document,
    root: Root,
    path: Vec<Step>,
    step: Step,
) -> PyResult<bool> {
    match (container(&this.doc, root, &path)?, &step) {
        (Node::Dictionary(dictionary), Step::Key(key)) if !dictionary.has(key) => {
            return Err(pyo3::exceptions::PyKeyError::new_err(
                String::from_utf8_lossy(key).into_owned(),
            ));
        }
        (Node::Array(array), Step::Index(index)) if *index >= array.len() => {
            return Err(pyo3::exceptions::PyIndexError::new_err(format!(
                "array index {index} out of range"
            )));
        }
        (Node::Dictionary(_), Step::Key(_)) | (Node::Array(_), Step::Index(_)) => {}
        _ => {
            return Err(pyo3::exceptions::PyTypeError::new_err(
                "mismatched key type",
            ));
        }
    }
    this.commit_object_write(|doc| {
        match (container_mut(doc, root, &path)?, step) {
            (NodeMut::Dictionary(dictionary), Step::Key(key)) => {
                dictionary.remove(&key);
            }
            (NodeMut::Array(array), Step::Index(index)) => {
                array.remove(index);
            }
            _ => {}
        }
        Ok(())
    })
}

/// Implements [`_Document::object_insert`].
pub(super) fn object_insert(
    this: &mut _Document,
    root: Root,
    path: Vec<Step>,
    index: usize,
    value: &Bound<'_, PyAny>,
) -> PyResult<bool> {
    let object = direct_value(&this.doc, value, 1)?;
    match container(&this.doc, root, &path)? {
        Node::Array(array) if index <= array.len() => {}
        Node::Array(_) => {
            return Err(pyo3::exceptions::PyIndexError::new_err(format!(
                "array index {index} out of range"
            )));
        }
        Node::Dictionary(_) => {
            return Err(pyo3::exceptions::PyTypeError::new_err(
                "only arrays take insertions",
            ));
        }
    }
    this.commit_object_write(|doc| {
        if let NodeMut::Array(array) = container_mut(doc, root, &path)? {
            array
                .try_reserve(1)
                .map_err(|error| PdfError::new_err(format!("failed to grow an array: {error}")))?;
            array.insert(index, object);
        }
        Ok(())
    })
}

/// Implements [`_Document::object_add`].
pub(super) fn object_add(
    this: &mut _Document,
    value: &Bound<'_, PyAny>,
) -> PyResult<(u32, u16, bool)> {
    let object = value_from_py(&this.doc, value, 1)?;
    let mut id = (0, 0);
    let changed = this.commit_object_write(|doc| {
        id = doc.add_object(object);
        Ok(())
    })?;
    Ok((id.0, id.1, changed))
}

/// Implements [`_Document::object_replace`].
pub(super) fn object_replace(
    this: &mut _Document,
    number: u32,
    value: &Bound<'_, PyAny>,
) -> PyResult<bool> {
    let id = object_id(&this.doc, number)
        .ok_or_else(|| pyo3::exceptions::PyKeyError::new_err(number))?;
    let object = value_from_py(&this.doc, value, 1)?;
    this.commit_object_write(|doc| {
        doc.objects.insert(id, object);
        Ok(())
    })
}

fn flate_encode(data: &[u8]) -> PyResult<Vec<u8>> {
    use flate2::Compression;
    use flate2::write::ZlibEncoder;
    let mut encoder = ZlibEncoder::new(Vec::new(), Compression::default());
    encoder
        .write_all(data)
        .and_then(|()| encoder.finish())
        .map_err(|error| PdfError::new_err(format!("failed to compress stream data: {error}")))
}

fn stream_content(data: Vec<u8>, compress: bool) -> PyResult<(Vec<u8>, bool)> {
    if compress {
        Ok((flate_encode(&data)?, true))
    } else {
        Ok((data, false))
    }
}

/// Implements [`_Document::object_add_stream`].
pub(super) fn object_add_stream(
    this: &mut _Document,
    py: Python<'_>,
    dictionary: &Bound<'_, PyAny>,
    data: Vec<u8>,
    compress: bool,
) -> PyResult<(u32, u16, bool)> {
    let Object::Dictionary(mut dict) = value_from_py(&this.doc, dictionary, 1)? else {
        return Err(PyValueError::new_err(
            "a stream dictionary must be a dictionary",
        ));
    };
    let (content, compressed) = py.detach(|| stream_content(data, compress))?;
    dict.remove(b"DecodeParms");
    dict.remove(b"Filter");
    if compressed {
        dict.set("Filter", Object::Name(b"FlateDecode".to_vec()));
    }
    let stream = Stream::new(dict, content);
    let mut id = (0, 0);
    let changed = this.commit_object_write(|doc| {
        id = doc.add_object(stream);
        Ok(())
    })?;
    Ok((id.0, id.1, changed))
}

fn stream_at<'a>(doc: &'a Document, id: ObjectId, path: &[Step]) -> PyResult<&'a Stream> {
    match path_object(doc, id, path)? {
        Object::Stream(stream) => Ok(stream),
        _ => Err(stale(format!(
            "{} no longer holds a stream",
            describe_path(Some(id), path)
        ))),
    }
}

/// Implements [`_Document::stream_read`].
pub(super) fn stream_read(
    this: &_Document,
    py: Python<'_>,
    id: (u32, u16),
    path: Vec<Step>,
    decode: bool,
    max_size: Option<usize>,
) -> PyResult<Vec<u8>> {
    let stream = stream_at(&this.doc, id, &path)?;
    let context = format!("stream {}", describe_path(Some(id), &path));
    py.detach(|| {
        if decode {
            return decoded_stream_content(stream, max_size, "stream_data_size", &context);
        }
        if let Some(limit) = max_size
            && stream.content.len() > limit
        {
            return Err(limit_err(
                "stream_data_size",
                format!(
                    "{context} is {} bytes, exceeding the {limit}-byte size limit",
                    stream.content.len()
                ),
            ));
        }
        let mut data = Vec::new();
        data.try_reserve_exact(stream.content.len())
            .map_err(|error| PdfError::new_err(format!("failed to allocate {context}: {error}")))?;
        data.extend_from_slice(&stream.content);
        Ok(data)
    })
}

/// Implements [`_Document::stream_write`].
pub(super) fn stream_write(
    this: &mut _Document,
    py: Python<'_>,
    id: (u32, u16),
    path: Vec<Step>,
    data: Vec<u8>,
    compress: bool,
) -> PyResult<bool> {
    stream_at(&this.doc, id, &path)?;
    let (content, compressed) = py.detach(|| stream_content(data, compress))?;
    this.commit_object_write(|doc| {
        let mut object = doc
            .objects
            .get_mut(&id)
            .ok_or_else(|| stale("stream vanished"))?;
        for step in &path {
            object = node_of_mut(object)
                .and_then(|node| child_of_mut(node, step))
                .ok_or_else(|| stale("stream vanished"))?;
        }
        let Object::Stream(stream) = object else {
            return Err(stale("stream vanished"));
        };
        stream.set_plain_content(content);
        if compressed {
            stream
                .dict
                .set("Filter", Object::Name(b"FlateDecode".to_vec()));
        }
        Ok(())
    })
}

/// Decode a PDF text string: UTF-16BE or UTF-8 after a byte-order mark,
/// PDFDocEncoding otherwise (ISO 32000-2 §7.9.2.2).
#[pyfunction]
pub(crate) fn _decode_text_string(data: Vec<u8>) -> PyResult<String> {
    let object = Object::String(data, StringFormat::Literal);
    let text = decode_text_string(&object)
        .map_err(|error| PdfError::new_err(format!("not a PDF text string: {error}")))?;
    // lopdf keeps the UTF-8 byte-order mark in the decoded text.
    Ok(match text.strip_prefix('\u{feff}') {
        Some(rest) => rest.to_owned(),
        None => text,
    })
}

/// Encode a PDF text string: ASCII as is, anything else as UTF-16BE with a
/// byte-order mark, as Info metadata is written.
#[pyfunction]
pub(crate) fn _encode_text_string(text: &str) -> Vec<u8> {
    match text_string(text) {
        Object::String(bytes, _) => bytes,
        _ => unreachable!("text_string always returns a string"),
    }
}
