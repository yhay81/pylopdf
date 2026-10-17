//! Lenient lopdf loading with visible, bounded cross-reference recovery.
//!
//! lopdf 0.45 recovers damaged cross-reference data while loading leniently:
//! it moves a slightly miswritten `startxref`, `/Prev`, or `/XRefStm` offset to
//! a nearby `xref` keyword, and rebuilds the table by scanning object headers
//! when standard resolution fails. It reports both only as `log` warnings,
//! emitted on the loading thread before object parsing moves to the rayon pool,
//! so one thread-local capture scope attributes them to a single load.

use std::cell::Cell;
use std::sync::Once;

use lopdf::{Document, LoadOptions, PdfMetadata};

/// lopdf's reader module, which resolves and rebuilds cross-reference data.
const LOPDF_READER_TARGET: &str = "lopdf::reader";

/// Bytes inspected per input byte allowed for lopdf's reconstruction scan.
const RECOVERY_SCAN_WORK_FACTOR: u64 = 64;
/// Fixed reconstruction-scan allowance added to the per-byte budget.
const RECOVERY_SCAN_WORK_SLACK: u64 = 64 * 1024 * 1024;

/// Cross-reference recovery performed by lopdf, ordered by severity.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum XrefRecovery {
    /// A cross-reference offset was moved to a nearby `xref` keyword.
    OffsetCorrected,
    /// The table was rebuilt by scanning indirect-object headers.
    Reconstructed,
}

impl XrefRecovery {
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

thread_local! {
    /// `None` outside a capture scope; otherwise the strongest recovery seen.
    static CAPTURE: Cell<Option<Option<XrefRecovery>>> = const { Cell::new(None) };
}

/// Forward lopdf recovery warnings to the active capture scope on this thread.
struct RecoveryLog;

static RECOVERY_LOG: RecoveryLog = RecoveryLog;
static INSTALL_RECOVERY_LOG: Once = Once::new();

impl log::Log for RecoveryLog {
    fn enabled(&self, metadata: &log::Metadata<'_>) -> bool {
        metadata.level() == log::Level::Warn && metadata.target() == LOPDF_READER_TARGET
    }

    fn log(&self, record: &log::Record<'_>) {
        if !self.enabled(record.metadata()) {
            return;
        }
        CAPTURE.with(|capture| {
            let Some(seen) = capture.get() else {
                return;
            };
            let message = record.args().to_string();
            let recovery = if message.contains("by scanning for indirect objects") {
                XrefRecovery::Reconstructed
            } else if message.contains("using nearby offset") {
                XrefRecovery::OffsetCorrected
            } else {
                return;
            };
            capture.set(Some(seen.max(Some(recovery))));
        });
    }

    fn flush(&self) {}
}

/// Restore the enclosing capture state even if lopdf unwinds.
struct CaptureScope(Option<Option<XrefRecovery>>);

impl Drop for CaptureScope {
    fn drop(&mut self) {
        CAPTURE.with(|capture| capture.set(self.0));
    }
}

/// Run one lopdf load and return the strongest cross-reference recovery it logged.
fn capture_recovery<T>(load: impl FnOnce() -> T) -> (T, Option<XrefRecovery>) {
    INSTALL_RECOVERY_LOG.call_once(|| {
        if log::set_logger(&RECOVERY_LOG).is_ok() {
            log::set_max_level(log::LevelFilter::Warn);
        }
    });
    let _scope = CaptureScope(CAPTURE.with(|capture| capture.replace(Some(None))));
    let value = load();
    (value, CAPTURE.with(Cell::get).flatten())
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

/// Load a complete document leniently and report cross-reference recovery.
pub fn load_document(
    data: &[u8],
    options: LoadOptions,
) -> Result<(Document, Option<XrefRecovery>), LoadError> {
    check_recovery_scan_work(data)?;
    let (result, recovery) = capture_recovery(|| Document::load_mem_with_options(data, options));
    let mut document = result.map_err(LoadError::Pdf)?;
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
pub fn load_metadata(
    data: &[u8],
    password: Option<&str>,
) -> Result<(PdfMetadata, Option<XrefRecovery>), LoadError> {
    check_recovery_scan_work(data)?;
    let (result, recovery) = capture_recovery(|| match password {
        Some(password) => Document::load_metadata_mem_with_password(data, password),
        None => Document::load_metadata_mem(data),
    });
    result
        .map(|metadata| (metadata, recovery))
        .map_err(LoadError::Pdf)
}
