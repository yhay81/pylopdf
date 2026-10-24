//! Serialization, encryption on save, compression, and garbage collection.

use super::*;

/// Save options enabling object and xref streams.
///
/// Keep ObjectStreamConfig defaults: 100 objects and compression level 6.
pub(super) fn modern_save_options() -> SaveOptions {
    SaveOptions {
        use_object_streams: true,
        use_xref_streams: true,
        ..Default::default()
    }
}

/// A serialization sink that refuses the write crossing one byte budget.
pub(super) struct BoundedPdfOutput {
    pub(super) bytes: Vec<u8>,
    pub(super) max_size: Option<usize>,
    pub(super) exceeded: bool,
}

impl BoundedPdfOutput {
    pub(super) fn new(max_size: Option<usize>) -> Self {
        Self {
            bytes: Vec::new(),
            max_size,
            exceeded: false,
        }
    }
}

impl Write for BoundedPdfOutput {
    fn write(&mut self, buffer: &[u8]) -> std::io::Result<usize> {
        self.write_all(buffer)?;
        Ok(buffer.len())
    }

    fn write_all(&mut self, buffer: &[u8]) -> std::io::Result<()> {
        let new_len = self
            .bytes
            .len()
            .checked_add(buffer.len())
            .ok_or_else(|| std::io::Error::other("serialized PDF output size overflowed"))?;
        if self.max_size.is_some_and(|limit| new_len > limit) {
            self.exceeded = true;
            return Err(std::io::Error::other(
                "serialized PDF output size limit exceeded",
            ));
        }
        self.bytes.try_reserve(buffer.len()).map_err(|error| {
            std::io::Error::other(format!("failed to allocate serialized PDF output: {error}"))
        })?;
        self.bytes.extend_from_slice(buffer);
        Ok(())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

/// Write one PDF while restoring lopdf's ephemeral xref writer state.
///
/// lopdf materializes neither object streams nor the final xref stream in
/// `Document::objects`, but its writer still advances `max_id` for them and
/// rewrites the trailer dictionary. Leaving those changes behind makes each
/// otherwise identical save choose new object IDs. Save options may
/// intentionally raise the PDF version and change the xref type; those fields
/// are outside this snapshot and retain their existing mutation semantics.
pub(super) fn write_pdf_stably<W: Write>(
    document: &mut Document,
    target: &mut W,
    options: Option<SaveOptions>,
) -> std::io::Result<()> {
    let max_id = document.max_id;
    let trailer = document.trailer.clone();
    let result = match options {
        Some(options) => document.save_with_options(target, options),
        None => document.save_to(target),
    };
    document.max_id = max_id;
    document.trailer = trailer;
    result
}

/// Stream one PDF file through the stable writer-state boundary.
pub(super) fn write_pdf_file_stably(
    document: &mut Document,
    path: &str,
    options: Option<SaveOptions>,
) -> std::io::Result<()> {
    let file = std::fs::File::create(path)?;
    let mut writer = std::io::BufWriter::new(file);
    write_pdf_stably(document, &mut writer, options)?;
    writer
        .into_inner()
        .map(|_| ())
        .map_err(std::io::IntoInnerError::into_error)
}

/// Serialize through a writer that never retains bytes beyond `max_size`.
pub(super) fn serialize_pdf_with_limit(
    document: &mut Document,
    options: Option<SaveOptions>,
    max_size: Option<usize>,
    limit_code: &'static str,
    size_label: &str,
) -> PyResult<Vec<u8>> {
    if max_size == Some(0) {
        return Err(PyValueError::new_err(
            "max_size must be a positive integer or None",
        ));
    }
    let mut output = BoundedPdfOutput::new(max_size);
    let result = write_pdf_stably(document, &mut output, options);
    if output.exceeded {
        let limit = max_size.expect("an output can exceed only a configured limit");
        return Err(limit_err(
            limit_code,
            format!("{size_label} exceeds the configured {limit}-byte limit"),
        ));
    }
    result.map_err(|error| PdfError::new_err(error.to_string()))?;
    Ok(output.bytes)
}

/// Serialize public PDF output through its stable output-size boundary.
pub(super) fn serialize_pdf(
    document: &mut Document,
    options: Option<SaveOptions>,
    max_size: Option<usize>,
) -> PyResult<Vec<u8>> {
    serialize_pdf_with_limit(
        document,
        options,
        max_size,
        "pdf_output_size",
        "serialized PDF output",
    )
}

/// How `save` and `to_bytes` encrypt, as Python's `Encryption` passes it.
pub(super) struct EncryptionRequest<'a> {
    /// `aes-256`, `aes-128`, or `keep`, which keeps the encryption the
    /// document was opened with.
    pub(super) method: &'a str,
    pub(super) user_password: &'a str,
    pub(super) owner_password: &'a str,
    pub(super) permissions: u64,
    /// 32 random bytes from Python's `os.urandom`, the AES-256 file key.
    pub(super) file_encryption_key: &'a [u8],
    /// 16 random bytes from Python's `os.urandom`, which identify a document
    /// that has no `/ID` yet.
    pub(super) document_id: &'a [u8],
}

impl _Document {
    /// Return an encrypted clone as `request` asks; leave self plaintext.
    pub(super) fn encrypted_clone(&self, request: &EncryptionRequest<'_>) -> PyResult<Document> {
        if request.method == "keep" {
            return self.clone_with_original_encryption();
        }
        validate_password_input(Some(request.user_password), "user password")?;
        validate_password_input(Some(request.owner_password), "owner password")?;
        let permissions = Permissions::from_bits_truncate(request.permissions);
        let mut cloned = self.doc.clone();
        // An encrypted file needs an ID (ISO 32000-2 Table 15), and AES-128
        // derives its file key from the first element.
        if !cloned.trailer.has(b"ID") {
            let id = Object::String(request.document_id.to_vec(), StringFormat::Hexadecimal);
            cloned
                .trailer
                .set("ID", Object::Array(vec![id.clone(), id]));
        }
        let version = match request.method {
            "aes-256" => {
                if request.file_encryption_key.len() != 32 {
                    return Err(PdfError::new_err(format!(
                        "file_encryption_key must be 32 bytes ({} bytes given)",
                        request.file_encryption_key.len()
                    )));
                }
                let crypt_filter: Arc<dyn CryptFilter> = Arc::new(Aes256CryptFilter);
                EncryptionVersion::V5 {
                    encrypt_metadata: true,
                    crypt_filters: BTreeMap::from([(b"StdCF".to_vec(), crypt_filter)]),
                    file_encryption_key: request.file_encryption_key,
                    stream_filter: b"StdCF".to_vec(),
                    string_filter: b"StdCF".to_vec(),
                    owner_password: request.owner_password,
                    user_password: request.user_password,
                    permissions,
                }
            }
            "aes-128" => {
                let crypt_filter: Arc<dyn CryptFilter> = Arc::new(Aes128CryptFilter);
                let state = EncryptionState::try_from(EncryptionVersion::V4 {
                    document: &cloned,
                    encrypt_metadata: true,
                    crypt_filters: BTreeMap::from([(b"StdCF".to_vec(), crypt_filter)]),
                    stream_filter: b"StdCF".to_vec(),
                    string_filter: b"StdCF".to_vec(),
                    owner_password: request.owner_password,
                    user_password: request.user_password,
                    permissions,
                })
                .map_err(to_py_err)?;
                cloned.encrypt(&state).map_err(to_py_err)?;
                return Ok(cloned);
            }
            method => {
                return Err(PdfError::new_err(format!(
                    "unsupported encryption method: {method:?}"
                )));
            }
        };
        let state = EncryptionState::try_from(version).map_err(to_py_err)?;
        cloned.encrypt(&state).map_err(to_py_err)?;
        Ok(cloned)
    }

    /// Return a clone encrypted as the document was when it was opened, or
    /// plaintext when it was not encrypted.
    ///
    /// The clone keeps the file key, the passwords, and the permissions, so
    /// the passwords that opened the input open the output. Only AES-128
    /// (revision 4 with AESV2 crypt filters) and AES-256 (revision 6) are
    /// kept: pylopdf writes no RC4 and not the deprecated revision 5.
    fn clone_with_original_encryption(&self) -> PyResult<Document> {
        let mut cloned = self.doc.clone();
        let Some(state) = self.doc.encryption_state.as_ref() else {
            return Ok(cloned);
        };
        let description = self.encryption_description().unwrap_or_default();
        let kept = matches!(
            description.as_str(),
            "Standard V4 R4 128-bit AES" | "Standard V5 R6 256-bit AES"
        );
        if !kept {
            return Err(PdfError::new_err(format!(
                "pylopdf cannot keep this document's encryption ({description}): it writes \
                 AES-128 with revision 4 and AES-256 with revision 6 only, not RC4 or the \
                 deprecated revision 5; pass Encryption(...) to encrypt with AES-256"
            )));
        }
        cloned.encrypt(state).map_err(to_py_err)?;
        Ok(cloned)
    }
}

/// Implements [`_Document::save_with_object_streams`].
pub(super) fn save_with_object_streams(
    this: &mut _Document,
    py: Python<'_>,
    path: &str,
) -> PyResult<()> {
    this.invalidate_hayro_pdf_keeping_placements();
    py.detach(|| {
        write_pdf_file_stably(&mut this.doc, path, Some(modern_save_options()))
            .map_err(|e| PdfError::new_err(format!("failed to save {path}: {e}")))
    })
}

/// Implements [`_Document::save_encrypted`].
pub(super) fn save_encrypted(
    this: &_Document,
    py: Python<'_>,
    path: &str,
    request: &EncryptionRequest<'_>,
) -> PyResult<()> {
    py.detach(|| {
        let mut cloned = this.encrypted_clone(request)?;
        cloned
            .save(path)
            .map(|_| ())
            .map_err(|e| PdfError::new_err(format!("failed to save {path}: {e}")))
    })
}

/// Implements [`_Document::save_bytes_encrypted`].
pub(super) fn save_bytes_encrypted(
    this: &_Document,
    py: Python<'_>,
    request: &EncryptionRequest<'_>,
    max_size: Option<usize>,
) -> PyResult<Vec<u8>> {
    py.detach(|| {
        let mut cloned = this.encrypted_clone(request)?;
        serialize_pdf(&mut cloned, None, max_size)
    })
}

/// Return whether `object` is a stream that compression may change.
fn compressible(object: &Object) -> bool {
    matches!(object, Object::Stream(stream) if stream.allows_compression && stream.dict.get(b"Filter").is_err())
}

/// Implements [`_Document::compress`].
pub(super) fn compress(this: &mut _Document, py: Python<'_>) {
    // Reading first keeps the page index when there is nothing to compress.
    if !this.doc.objects.values().any(compressible) {
        return;
    }
    let doc: &mut Document = &mut this.doc;
    let changed = py.detach(|| {
        let mut changed = false;
        for object in doc.objects.values_mut() {
            if compressible(object)
                && let Object::Stream(stream) = object
            {
                // As in lopdf's Document::compress, a failure leaves the
                // stream as it was, and so does Flate output that is not
                // smaller.
                let _ = stream.compress();
                changed |= stream.dict.has(b"Filter");
            }
        }
        changed
    });
    if changed {
        this.invalidate_hayro_pdf();
    }
}

/// Implements [`_Document::collect_garbage`].
pub(super) fn collect_garbage(
    this: &mut _Document,
    py: Python<'_>,
    dedupe: bool,
    prune: bool,
    renumber: bool,
) -> PyResult<()> {
    if dedupe && !prune {
        return Err(PdfError::new_err(
            "dedupe leaves the merged duplicates unreachable, so it needs prune",
        ));
    }
    if !(prune || renumber) {
        return Ok(());
    }
    if !(dedupe || renumber) {
        // Pruning alone reads the graph first, so that saving a document
        // without unreachable objects keeps every cache, including the
        // original-byte rendering path.
        let doc: &Document = &this.doc;
        let reachable = py.detach(|| crate::prune::trailer_reachable_object_ids(doc))?;
        if reachable.len() < this.doc.objects.len() {
            this.invalidate_hayro_pdf_keeping_placements();
            this.doc.objects.retain(|id, _| reachable.contains(id));
        }
        return Ok(());
    }
    this.invalidate_hayro_pdf();
    let doc: &mut Document = &mut this.doc;
    let renumbered = py.detach(|| -> PyResult<_> {
        if dedupe {
            crate::garbage::merge_identical_objects(doc, true)?;
        }
        if prune {
            prune_unreachable_objects(doc)?;
        }
        if renumber {
            return crate::garbage::compact_object_numbers(doc).map(Some);
        }
        Ok(None)
    })?;
    if let Some(renumbered) = renumbered {
        let isolated = std::mem::take(&mut this.isolated_content_pages);
        this.isolated_content_pages = isolated
            .into_iter()
            .filter_map(|id| renumbered.get(&id).copied())
            .collect();
    }
    Ok(())
}
