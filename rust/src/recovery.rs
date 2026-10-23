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

/// Bytes inspected per input byte allowed for lopdf's reconstruction scan.
const RECOVERY_SCAN_WORK_FACTOR: u64 = 64;
/// Fixed reconstruction-scan allowance added to the per-byte budget.
const RECOVERY_SCAN_WORK_SLACK: u64 = 64 * 1024 * 1024;

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

/// Why a lenient load did not produce a document or metadata.
pub enum LoadError {
    Pdf(lopdf::Error),
    /// Reconstruction could scan more than the linear budget allows.
    RecoveryScan {
        work: u64,
        budget: u64,
    },
}

fn find(data: &[u8], needle: &[u8]) -> Option<usize> {
    data.windows(needle.len())
        .position(|window| window == needle)
}

fn rfind(data: &[u8], needle: &[u8]) -> Option<usize> {
    data.windows(needle.len())
        .rposition(|window| window == needle)
}

/// Bound lopdf's object-header scan before any lenient load.
///
/// When standard resolution fails, lopdf skips each `stream` payload by
/// searching forward for `endstream`. Without one, it searches backward for
/// the object's `obj` and `/Length` and then resumes one byte later, so every
/// later unterminated keyword repeats a scan to the end of the input.
/// Terminated payloads are skipped, so their searches cover disjoint ranges.
/// Damaged real files hold at most a truncated final stream after the last
/// `endstream`; hostile input can repeat the keyword until recovery becomes
/// quadratic. Estimate the scan as if recovery were needed, because lopdf
/// decides only after parsing starts, and refuse input beyond a linear budget.
fn check_recovery_scan_work(data: &[u8]) -> Result<(), LoadError> {
    // lopdf scans from its first header and rejects input without one earlier.
    let Some(header) = find(data, b"%PDF-") else {
        return Ok(());
    };
    let buffer = &data[header..];
    let length = buffer.len();
    let budget = (length as u64)
        .saturating_mul(RECOVERY_SCAN_WORK_FACTOR)
        .saturating_add(RECOVERY_SCAN_WORK_SLACK);
    let tail = rfind(buffer, b"endstream").map_or(0, |position| position + b"endstream".len());
    let mut last_obj = rfind(&buffer[..tail], b"obj");
    let mut work = length as u64;
    for position in tail..length {
        if position >= 3 && &buffer[position - 3..position] == b"obj" {
            last_obj = Some(position - 3);
        }
        if buffer[position..].starts_with(b"stream")
            && !buffer[..position].ends_with(b"end")
            && matches!(buffer.get(position + 6), Some(b'\r' | b'\n'))
        {
            let forward = length - position;
            let backward = last_obj.map_or(position, |obj| 2 * (position - obj));
            work = work
                .saturating_add(forward as u64)
                .saturating_add(backward as u64);
            if work > budget {
                return Err(LoadError::RecoveryScan { work, budget });
            }
        }
    }
    Ok(())
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
) -> Result<(Document, Option<XrefRecovery>), LoadError> {
    check_recovery_scan_work(data)?;
    let mut document =
        Document::load_mem_with_options(data, options.clone()).map_err(LoadError::Pdf)?;
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
) -> Result<(PdfMetadata, Option<XrefRecovery>), LoadError> {
    check_recovery_scan_work(data)?;
    let probe = |input: &[u8]| match password {
        Some(password) => Document::load_metadata_mem_with_password(input, password),
        None => Document::load_metadata_mem(input),
    };
    let metadata = probe(data).map_err(LoadError::Pdf)?;
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
