//! Incremental saves: the original bytes followed by one update section.
//!
//! The editable graph stays the only source of truth, so no editing method
//! reports what it changed. An incremental save reads the original bytes
//! again exactly as opening did, compares every object of the graph with
//! that baseline, and appends what differs through lopdf's
//! `IncrementalDocument`. The original bytes stay a prefix of the output,
//! so a signature over them stays intact.

use super::*;

use lopdf::IncrementalDocument;

/// The input of a load that an incremental save can extend.
///
/// Retained only when the bytes describe lopdf's graph exactly: no
/// cross-reference recovery, no encryption, `object_recovery` kept the
/// bytes, and loading moved no object to another number. The buffer is
/// shared with the original-byte rendering source, so an unedited document
/// holds it once.
pub(super) struct IncrementalBase {
    bytes: Arc<Vec<u8>>,
    /// The decoder bound that the load used, so that the baseline reads the
    /// same streams.
    max_decompressed_size: Option<usize>,
}

impl IncrementalBase {
    pub(super) fn new(bytes: Arc<Vec<u8>>, max_decompressed_size: Option<usize>) -> Self {
        Self {
            bytes,
            max_decompressed_size,
        }
    }

    /// Read the original bytes again as opening read them.
    fn baseline(&self) -> PyResult<Document> {
        let options = LoadOptions {
            max_decompressed_size: self.max_decompressed_size,
            ..Default::default()
        };
        let unchanged = || PdfError::new_err("the original bytes no longer load as they did");
        let (mut baseline, recovery) =
            recovery::load_document(&self.bytes, options).map_err(|_| unchanged())?;
        if recovery.is_some()
            || !loading::original_bytes_match(&mut baseline, None, &self.bytes)?
            || normalize_object_numbers(&mut baseline)?
        {
            return Err(unchanged());
        }
        Ok(baseline)
    }
}

/// Trailer entries that describe one cross-reference section rather than
/// the document; the writer sets the ones that the update needs.
const SECTION_KEYS: [&[u8]; 9] = [
    b"Prev",
    b"XRefStm",
    b"Size",
    b"Type",
    b"W",
    b"Index",
    b"Length",
    b"Filter",
    b"DecodeParms",
];

/// Return the trailer without the entries of its cross-reference section.
fn document_trailer(trailer: &Dictionary) -> Dictionary {
    let mut trailer = trailer.clone();
    for key in SECTION_KEYS {
        trailer.remove(key);
    }
    trailer
}

/// Whether two objects hold the same value, ignoring where a stream was read.
fn same_object(current: &Object, baseline: &Object) -> bool {
    match (current, baseline) {
        (Object::Stream(current), Object::Stream(baseline)) => {
            current.dict == baseline.dict && current.content == baseline.content
        }
        _ => current == baseline,
    }
}

/// Add every reference that `object` holds to one of `removed` to `referenced`.
fn collect_removed_references(
    object: &Object,
    removed: &HashSet<ObjectId>,
    referenced: &mut HashSet<ObjectId>,
) -> PyResult<()> {
    let mut stack: Vec<&Object> = Vec::new();
    stack.push(object);
    while let Some(object) = stack.pop() {
        let children: Box<dyn Iterator<Item = &Object>> = match object {
            Object::Reference(id) => {
                if removed.contains(id) {
                    referenced.try_reserve(1).map_err(allocation_err)?;
                    referenced.insert(*id);
                }
                continue;
            }
            Object::Array(items) => Box::new(items.iter()),
            Object::Dictionary(dict) => Box::new(dict.iter().map(|(_, value)| value)),
            Object::Stream(stream) => Box::new(stream.dict.iter().map(|(_, value)| value)),
            _ => continue,
        };
        for child in children {
            stack.try_reserve(1).map_err(allocation_err)?;
            stack.push(child);
        }
    }
    Ok(())
}

fn allocation_err(error: std::collections::TryReserveError) -> PyErr {
    PdfError::new_err(format!(
        "failed to allocate the incremental update: {error}"
    ))
}

/// The objects and trailer that an update must write.
struct Update {
    objects: Vec<(ObjectId, Object)>,
    trailer: Dictionary,
}

impl Update {
    /// Compare `current` with `baseline`; `None` when nothing changed.
    fn plan(
        current: &Document,
        baseline: &Document,
        compress: Option<u32>,
    ) -> PyResult<Option<Self>> {
        let mut objects = Vec::new();
        for (&id, object) in &current.objects {
            if baseline
                .objects
                .get(&id)
                .is_some_and(|before| same_object(object, before))
            {
                continue;
            }
            let mut object = object.clone();
            if let Some(level) = compress
                && let Object::Stream(stream) = &mut object
                && stream.allows_compression
            {
                // As a full save does, keep the data as it is when Flate
                // output would not be smaller.
                saving::flate_compress(stream, level);
            }
            objects.try_reserve(1).map_err(allocation_err)?;
            objects.push((id, object));
        }
        // An object that the graph removed reads as null in memory. The
        // earlier revision still holds it, so a reference that remains must
        // meet an indirect null, which ISO 32000-2 §7.3.10 reads alike.
        let mut removed = HashSet::new();
        for &id in baseline.objects.keys() {
            if !current.objects.contains_key(&id) {
                removed.try_reserve(1).map_err(allocation_err)?;
                removed.insert(id);
            }
        }
        if !removed.is_empty() {
            let mut referenced = HashSet::new();
            let trailer = Object::Dictionary(current.trailer.clone());
            for object in current.objects.values().chain(std::iter::once(&trailer)) {
                collect_removed_references(object, &removed, &mut referenced)?;
            }
            objects
                .try_reserve(referenced.len())
                .map_err(allocation_err)?;
            objects.extend(referenced.into_iter().map(|id| (id, Object::Null)));
        }
        let trailer = document_trailer(&current.trailer);
        if objects.is_empty() && trailer == document_trailer(&baseline.trailer) {
            return Ok(None);
        }
        Ok(Some(Self { objects, trailer }))
    }
}

impl _Document {
    /// Whether an incremental save can extend the original bytes.
    pub(super) fn incremental_base(&self) -> PyResult<&IncrementalBase> {
        self.incremental_base.as_ref().ok_or_else(|| {
            PdfError::new_err(
                "this document cannot be saved incrementally: only a document opened from \
                 unencrypted bytes that loaded without repair can be",
            )
        })
    }
}

/// Write the original bytes and, when the graph changed, one update section.
pub(super) fn write_incremental<W: Write>(
    this: &_Document,
    target: &mut W,
    compress: Option<u32>,
    renew_id: bool,
) -> PyResult<()> {
    let base = this.incremental_base()?;
    let mut baseline = base.baseline()?;
    // The header cannot change in an update; the catalog's /Version raises
    // the version instead (ISO 32000-2 §7.7.2).
    let raise_version = this.doc.version.as_str() > baseline.version.as_str();
    let planned = Update::plan(&this.doc, &baseline, compress)?;
    let Some(mut update) = planned.or_else(|| {
        raise_version.then(|| Update {
            objects: Vec::new(),
            trailer: document_trailer(&this.doc.trailer),
        })
    }) else {
        return target
            .write_all(&base.bytes)
            .map_err(|error| PdfError::new_err(error.to_string()));
    };
    if raise_version {
        let root = this
            .doc
            .trailer
            .get(b"Root")
            .and_then(Object::as_reference)
            .map_err(to_py_err)?;
        let mut catalog = this.doc.get_object(root).map_err(to_py_err)?.clone();
        if let Object::Dictionary(dict) = &mut catalog {
            dict.set(
                "Version",
                Object::Name(this.doc.version.clone().into_bytes()),
            );
        }
        update.objects.retain(|(id, _)| *id != root);
        update.objects.push((root, catalog));
    }
    let mut prefix = Vec::new();
    prefix
        .try_reserve_exact(base.bytes.len())
        .map_err(allocation_err)?;
    prefix.extend_from_slice(&base.bytes);
    // `create_from` reads the trailer, cross-reference type, and highest
    // object number of the baseline; the objects themselves are not needed.
    let baseline_max_id = baseline.max_id;
    baseline.objects.clear();
    let mut incremental = IncrementalDocument::create_from(prefix, baseline);
    let prev = incremental.new_document.trailer.get(b"Prev").ok().cloned();
    let mut trailer = update.trailer;
    if let Some(prev) = prev {
        trailer.set("Prev", prev);
    }
    if renew_id && !update.objects.is_empty() {
        // ISO 32000-2 §14.4: an update keeps the first element and renews
        // the second, here a digest of the content as for a full save.
        let fresh = Object::String(
            saving::content_digest(&this.doc).to_vec(),
            StringFormat::Hexadecimal,
        );
        match trailer.get_mut(b"ID") {
            Ok(Object::Array(items))
                if items.len() == 2
                    && items.iter().all(|item| matches!(item, Object::String(..))) =>
            {
                items[1] = fresh;
            }
            _ => trailer.set("ID", Object::Array(vec![fresh.clone(), fresh])),
        }
    }
    incremental.new_document.trailer = trailer;
    // The update's /Size covers the earlier sections too.
    incremental.new_document.max_id = this.doc.max_id.max(baseline_max_id);
    for (id, object) in update.objects {
        incremental.new_document.objects.insert(id, object);
    }
    incremental
        .save_to(target)
        .map_err(|error| PdfError::new_err(error.to_string()))
}

/// Implements [`_Document::save_incremental`].
pub(super) fn save_incremental(
    this: &_Document,
    py: Python<'_>,
    path: &str,
    compress: Option<u32>,
    renew_id: bool,
) -> PyResult<()> {
    py.detach(|| {
        let file = std::fs::File::create(path)
            .map_err(|e| PdfError::new_err(format!("failed to save {path}: {e}")))?;
        let mut writer = std::io::BufWriter::new(file);
        write_incremental(this, &mut writer, compress, renew_id)
            .map_err(|e| PdfError::new_err(format!("failed to save {path}: {e}")))?;
        writer
            .into_inner()
            .map(|_| ())
            .map_err(|e| PdfError::new_err(format!("failed to save {path}: {}", e.error())))
    })
}

/// Implements [`_Document::save_bytes_incremental`].
pub(super) fn save_bytes_incremental(
    this: &_Document,
    py: Python<'_>,
    compress: Option<u32>,
    renew_id: bool,
    max_size: Option<usize>,
) -> PyResult<Vec<u8>> {
    if max_size == Some(0) {
        return Err(PyValueError::new_err(
            "max_size must be a positive integer or None",
        ));
    }
    py.detach(|| {
        let mut output = BoundedPdfOutput::new(max_size);
        let result = write_incremental(this, &mut output, compress, renew_id);
        if output.exceeded {
            let limit = max_size.expect("an output can exceed only a configured limit");
            return Err(limit_err(
                "pdf_output_size",
                format!("serialized PDF output exceeds the configured {limit}-byte limit"),
            ));
        }
        result?;
        Ok(output.bytes)
    })
}
