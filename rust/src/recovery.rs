//! Lenient lopdf loading with visible, bounded cross-reference recovery.
//!
//! lopdf 0.46 recovers damaged cross-reference data while loading leniently:
//! it moves a slightly miswritten `startxref`, `/Prev`, or `/XRefStm` offset
//! to a nearby cross-reference section, and rebuilds the table by scanning
//! object headers when standard resolution fails. It reports which in
//! `Document::xref_recovery` and `PdfMetadata::xref_recovery`
//! (J-F-Liu/lopdf#612). lopdf trusts any table that it resolves, so a load
//! that leaves the page tree unreachable, or that moved an offset onto a
//! section whose later entries miss their objects, is repeated with the
//! rebuild forced.

use lopdf::xref::XrefEntry;
use lopdf::{Document, LoadOptions, Object, PdfMetadata};

/// Cross-reference recovery performed by lopdf.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum XrefRecovery {
    /// A cross-reference offset was moved to a nearby cross-reference section.
    OffsetCorrected,
    /// The table was rebuilt by scanning indirect-object headers.
    Reconstructed,
}

impl XrefRecovery {
    /// Map lopdf's report, reading any future kind as the stronger repair.
    fn from_lopdf(recovery: Option<lopdf::xref::XrefRecovery>) -> Option<Self> {
        recovery.map(|recovery| match recovery {
            lopdf::xref::XrefRecovery::OffsetCorrected => Self::OffsetCorrected,
            _ => Self::Reconstructed,
        })
    }

    /// Return the `PylopdfWarning` text for this recovery.
    pub fn warning(self) -> &'static str {
        match self {
            Self::OffsetCorrected => {
                "recovered a PDF with an incorrect cross-reference offset; saving will rewrite \
                 its cross-reference data"
            }
            Self::Reconstructed => {
                "rebuilt a damaged PDF cross-reference table by scanning object headers; \
                 content may be incomplete, and saving will rewrite its cross-reference data"
            }
        }
    }
}

/// Return a copy of `data` that ends with a `startxref` past the end of the
/// input, or `None` when the copy cannot be allocated.
///
/// lopdf rebuilds the cross-reference table only when standard resolution
/// fails, and it resolves the last `startxref`, so loading the copy rebuilds
/// the table from the same bytes at the same offsets. lopdf finds no trailer
/// or object header in the added bytes, and a `startxref` past the end of the
/// input stops it before it corrects any offset.
fn rebuild_input(data: &[u8]) -> Option<Vec<u8>> {
    // The value exceeds the copy's length, whose tail is shorter than 64 bytes.
    let tail = format!("\nstartxref\n{}\n%%EOF\n", data.len().checked_add(64)?);
    let mut input = Vec::new();
    input.try_reserve_exact(data.len() + tail.len()).ok()?;
    input.extend_from_slice(data);
    input.extend_from_slice(tail.as_bytes());
    Some(input)
}

/// Whether the catalog that the trailer names leads to a root page-tree node,
/// read as the page-tree walker reads them.
fn page_tree_reachable(document: &Document) -> bool {
    document
        .catalog()
        .and_then(|catalog| catalog.get(b"Pages"))
        .and_then(Object::as_reference)
        .and_then(|root| document.get_dictionary(root))
        .is_ok_and(|root| root.get_type().ok() == Some(b"Pages".as_slice()))
}

/// Whether an in-use, uncompressed cross-reference entry names an object
/// that lopdf did not load.
fn misses_entries(document: &Document) -> bool {
    document
        .reference_table
        .entries
        .iter()
        .any(|(&number, entry)| match *entry {
            XrefEntry::Normal { generation, .. } => {
                !document.objects.contains_key(&(number, generation))
            }
            _ => false,
        })
}

/// Load a complete document leniently and report cross-reference recovery.
///
/// lopdf trusts any cross-reference data that it resolves, and it moves a
/// `startxref` that misses by at most 64 bytes onto a nearby table, whose
/// entries can miss their objects in a file that lost bytes. When the
/// objects that lead to the page tree then did not load, the document is
/// unusable, so an unencrypted one is loaded again with the table rebuilt by
/// scanning object headers, as hayro, MuPDF, and pdf.js rebuild it, and the
/// rebuilt document replaces it when its page tree is reachable. lopdf 0.46
/// moves an offset only onto a section of which more than half the entries
/// locate their objects (J-F-Liu/lopdf#611), so a file that gained or lost
/// bytes after its first objects can still load without the later ones; such a
/// load is rebuilt as well, and the rebuilt document replaces it when it also
/// loads more objects.
pub fn load_document(
    data: &[u8],
    options: LoadOptions,
) -> Result<(Document, Option<XrefRecovery>), lopdf::Error> {
    let mut document = Document::load_mem_with_options(data, options.clone())?;
    let mut recovery = XrefRecovery::from_lopdf(document.xref_recovery);
    let reachable = page_tree_reachable(&document);
    let partial = recovery == Some(XrefRecovery::OffsetCorrected) && misses_entries(&document);
    if recovery != Some(XrefRecovery::Reconstructed)
        && !document.is_encrypted()
        && !document.was_encrypted()
        && (!reachable || partial)
        && let Some(input) = rebuild_input(data)
        && let Ok(rebuilt) = Document::load_mem_with_options(&input, options)
        && rebuilt.xref_recovery == Some(lopdf::xref::XrefRecovery::Reconstructed)
        && page_tree_reachable(&rebuilt)
        && (!reachable || rebuilt.objects.len() > document.objects.len())
    {
        document = rebuilt;
        recovery = Some(XrefRecovery::Reconstructed);
    }
    if recovery == Some(XrefRecovery::Reconstructed) {
        // lopdf keeps the newest scanned trailer verbatim, including offsets
        // into the damaged input. The rebuilt table has no on-disk predecessor,
        // and a full save writes trailer keys as-is, so drop both chain links.
        document.trailer.remove(b"Prev");
        document.trailer.remove(b"XRefStm");
    }
    Ok((document, recovery))
}

/// Read metadata without loading every object and report cross-reference recovery.
///
/// lopdf's probe counts no page when it cannot read the catalog or the root
/// page-tree node, so an unencrypted probe that counts none is repeated with
/// the table rebuilt, as [`load_document`] loads again, and the rebuilt probe
/// replaces it when it counts pages.
pub fn load_metadata(
    data: &[u8],
    password: Option<&str>,
) -> Result<(PdfMetadata, Option<XrefRecovery>), lopdf::Error> {
    let probe = |input: &[u8]| match password {
        Some(password) => Document::load_metadata_mem_with_password(input, password),
        None => Document::load_metadata_mem(input),
    };
    let metadata = probe(data)?;
    let recovery = XrefRecovery::from_lopdf(metadata.xref_recovery);
    if recovery != Some(XrefRecovery::Reconstructed)
        && !metadata.encrypted
        && metadata.page_count == 0
        && let Some(input) = rebuild_input(data)
        && let Ok(rebuilt) = probe(&input)
        && rebuilt.xref_recovery == Some(lopdf::xref::XrefRecovery::Reconstructed)
        && rebuilt.page_count > 0
    {
        return Ok((rebuilt, Some(XrefRecovery::Reconstructed)));
    }
    Ok((metadata, recovery))
}
