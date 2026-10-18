//! Share installed locale and script font files across documents and calls.
//!
//! The optional `pylopdf-fonts-*` packages supply the CJK fallback fonts that
//! every new `Document` configures before its first render, and the faces
//! that text generation and form filling select when no font is passed.
//! Reading a package file again for every document took about 6 ms and 57 MB
//! of fresh allocations with all four CJK packages, and rereading it for every
//! generated run cost more than generating a short line. This process-wide
//! cache keeps the bytes most recently read for each package/style slot and
//! reuses them while the path names an unchanged file.

use std::fs::Metadata;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::SystemTime;

use pyo3::prelude::*;

use crate::document::{CjkFontLanguage, PdfError, font_file_metadata, read_font_file};

/// Shared bytes of one font file.
pub(crate) type FontBytes = Arc<Vec<u8>>;

/// One optional font package, named by Python's normalized `FontLanguage`.
#[derive(Clone, Copy)]
enum FontPackage {
    Ar,
    He,
    Hi,
    Ja,
    Ko,
    Th,
    ZhCn,
    ZhTw,
}

impl From<CjkFontLanguage> for FontPackage {
    fn from(language: CjkFontLanguage) -> Self {
        match language {
            CjkFontLanguage::Ja => Self::Ja,
            CjkFontLanguage::Ko => Self::Ko,
            CjkFontLanguage::ZhCn => Self::ZhCn,
            CjkFontLanguage::ZhTw => Self::ZhTw,
        }
    }
}

/// The sans or serif file of one installed font package.
#[derive(Clone, Copy)]
pub(crate) struct PackageFace {
    package: FontPackage,
    serif: bool,
}

impl PackageFace {
    /// Parse the `(language, kind)` pair Python passes for a package face.
    pub(crate) fn parse(language: &str, kind: &str) -> PyResult<Self> {
        let package = match language {
            "ar" => FontPackage::Ar,
            "he" => FontPackage::He,
            "hi" => FontPackage::Hi,
            "ja" => FontPackage::Ja,
            "ko" => FontPackage::Ko,
            "th" => FontPackage::Th,
            "zh-CN" => FontPackage::ZhCn,
            "zh-TW" => FontPackage::ZhTw,
            _ => {
                return Err(PdfError::new_err(format!(
                    "unknown font package language: {language:?}"
                )));
            }
        };
        let serif = match kind {
            "sans" => false,
            "serif" => true,
            _ => {
                return Err(PdfError::new_err(format!(
                    "kind must be 'sans' or 'serif': {kind:?}"
                )));
            }
        };
        Ok(Self { package, serif })
    }

    fn slot(self) -> usize {
        self.package as usize * 2 + usize::from(self.serif)
    }
}

/// Eight packages, each with a sans and a serif slot.
const SLOT_COUNT: usize = 16;

/// File metadata identifying the version whose bytes were read.
#[derive(PartialEq, Eq)]
struct FileVersion {
    len: u64,
    modified: SystemTime,
    #[cfg(unix)]
    device: u64,
    #[cfg(unix)]
    inode: u64,
    #[cfg(unix)]
    changed: (i64, i64),
}

impl FileVersion {
    /// Return `None` when the platform reports no modification time, so the
    /// file is read on every call rather than cached.
    fn of(metadata: &Metadata) -> Option<Self> {
        #[cfg(unix)]
        use std::os::unix::fs::MetadataExt;

        Some(Self {
            len: metadata.len(),
            modified: metadata.modified().ok()?,
            #[cfg(unix)]
            device: metadata.dev(),
            #[cfg(unix)]
            inode: metadata.ino(),
            #[cfg(unix)]
            changed: (metadata.ctime(), metadata.ctime_nsec()),
        })
    }
}

struct CachedFontFile {
    path: String,
    version: FileVersion,
    data: FontBytes,
}

type Slots = [Option<CachedFontFile>; SLOT_COUNT];

/// The most recent successful read for each package/style slot.
static PACKAGE_FONT_FILES: Mutex<Slots> = Mutex::new([const { None }; SLOT_COUNT]);

/// Files read into the slots since the process started, so tests can pin reuse.
static FILES_READ: AtomicUsize = AtomicUsize::new(0);

/// Lock the slots only to look up or swap entries, never across file I/O, so
/// a hung filesystem cannot stall other threads and a fork cannot inherit
/// the lock held.
fn locked_slots() -> MutexGuard<'static, Slots> {
    PACKAGE_FONT_FILES
        .lock()
        .unwrap_or_else(PoisonError::into_inner)
}

/// Return how many files the slots have read since the process started.
pub(crate) fn files_read() -> usize {
    FILES_READ.load(Ordering::Relaxed)
}

/// One requested file: its slot, path, inspected version, and bytes once known.
struct RequestedFile<'a> {
    slot: usize,
    path: &'a str,
    version: Option<FileVersion>,
    data: Option<FontBytes>,
}

impl<'a> RequestedFile<'a> {
    /// Inspect a path and apply `max_font_size`, failing exactly as an
    /// uncached bounded read would for a missing or over-limit file.
    fn inspect(face: PackageFace, path: &'a str, max_font_size: Option<usize>) -> PyResult<Self> {
        let metadata = font_file_metadata(path, max_font_size)?;
        Ok(Self {
            slot: face.slot(),
            path,
            version: FileVersion::of(&metadata),
            data: None,
        })
    }
}

/// Fill every inspected file from its slot or the bounded font reader.
///
/// A file whose path, size, and modification time (plus device, inode, and
/// change time on Unix) match its slot's cached read is not read again; any
/// other file goes through the bounded reader. New reads replace their slots
/// only after every file succeeds. Concurrent first calls may each read a
/// file before either publishes it; later calls share one copy. Return how
/// many files were read.
fn load_files(files: &mut [RequestedFile<'_>], max_font_size: Option<usize>) -> PyResult<usize> {
    {
        let slots = locked_slots();
        for file in files.iter_mut() {
            if let (Some(version), Some(cached)) = (&file.version, &slots[file.slot])
                && cached.path == file.path
                && cached.version == *version
            {
                file.data = Some(Arc::clone(&cached.data));
            }
        }
    }

    let unread = files.iter().filter(|file| file.data.is_none()).count();
    if unread == 0 {
        return Ok(0);
    }
    let mut updates = Vec::new();
    updates.try_reserve_exact(unread).map_err(|error| {
        PdfError::new_err(format!("failed to allocate font file updates: {error}"))
    })?;
    for file in files.iter_mut().filter(|file| file.data.is_none()) {
        let (data, metadata) = read_font_file(file.path, max_font_size)?;
        FILES_READ.fetch_add(1, Ordering::Relaxed);
        let data = Arc::new(data);
        // A file that changed size while being read is used once but not cached.
        if let Some(version) = FileVersion::of(&metadata)
            && u64::try_from(data.len()).is_ok_and(|len| len == version.len)
        {
            updates.push((
                file.slot,
                Some(CachedFontFile {
                    path: file.path.to_owned(),
                    version,
                    data: Arc::clone(&data),
                }),
            ));
        }
        file.data = Some(data);
    }

    if !updates.is_empty() {
        let mut slots = locked_slots();
        for (slot, entry) in &mut updates {
            std::mem::swap(&mut slots[*slot], entry);
        }
    }
    // `updates` now holds the replaced reads, released after unlocking.
    drop(updates);
    Ok(unread)
}

/// Return shared bytes for the selected face of one installed font package.
///
/// Every call inspects the path and applies `max_font_size` before reusing
/// the slot's bytes, so a missing, over-limit, or replaced file fails or is
/// reread exactly as an uncached read would.
pub(crate) fn load_package_font(
    face: PackageFace,
    path: &str,
    max_font_size: Option<usize>,
) -> PyResult<FontBytes> {
    let mut files = [RequestedFile::inspect(face, path, max_font_size)?];
    load_files(&mut files, max_font_size)?;
    let [file] = files;
    Ok(file.data.expect("the requested file was reused or read"))
}

/// Sans and serif bytes for each requested locale, plus how many files were read.
pub(crate) struct LoadedLocaleFonts {
    pub(crate) pairs: Vec<(FontBytes, FontBytes)>,
    pub(crate) files_read: usize,
}

/// Return shared sans and serif bytes for each `(language, sans, serif)` path pair.
///
/// Every path is inspected, and `max_font_size` applied, before any slot is
/// reused or replaced; see `load_files`.
pub(crate) fn load_locale_font_pairs(
    pairs: &[(CjkFontLanguage, String, String)],
    max_font_size: Option<usize>,
) -> PyResult<LoadedLocaleFonts> {
    let mut files = Vec::new();
    let mut loaded = Vec::new();
    files
        .try_reserve_exact(pairs.len().saturating_mul(2))
        .and_then(|()| loaded.try_reserve_exact(pairs.len()))
        .map_err(|error| {
            PdfError::new_err(format!(
                "failed to allocate CJK locale font requests: {error}"
            ))
        })?;
    for (language, sans, serif) in pairs {
        let package = FontPackage::from(*language);
        for (serif_face, path) in [(false, sans), (true, serif)] {
            let face = PackageFace {
                package,
                serif: serif_face,
            };
            files.push(RequestedFile::inspect(face, path, max_font_size)?);
        }
    }
    let files_read = load_files(&mut files, max_font_size)?;

    let mut data = files
        .into_iter()
        .map(|file| file.data.expect("every requested file was reused or read"));
    while let (Some(sans), Some(serif)) = (data.next(), data.next()) {
        loaded.push((sans, serif));
    }
    Ok(LoadedLocaleFonts {
        pairs: loaded,
        files_read,
    })
}
