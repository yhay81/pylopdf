//! Error conversion: lopdf error chains, load errors, and limit errors.

use super::*;

/// Display a lopdf error followed by its wrapped causes.
///
/// lopdf 0.45 keeps causes out of `Display` and exposes them through
/// `Error::source`, so join the chain to retain limit and decoder details.
pub(crate) struct LopdfErrorChain<'a>(pub(crate) &'a lopdf::Error);

impl std::fmt::Display for LopdfErrorChain<'_> {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}", self.0)?;
        let mut source = std::error::Error::source(self.0);
        while let Some(cause) = source {
            write!(formatter, ": {cause}")?;
            source = cause.source();
        }
        Ok(())
    }
}

/// Convert a lopdf error to a Python exception with a context prefix.
///
/// Password/decryption failures become PasswordError; others become PdfError.
pub(super) fn lopdf_err(prefix: Option<&str>, e: &lopdf::Error) -> PyErr {
    let chain = LopdfErrorChain(e);
    let message = match prefix {
        Some(p) => format!("{p}: {chain}"),
        None => chain.to_string(),
    };
    if matches!(
        e,
        lopdf::Error::Decryption(_) | lopdf::Error::InvalidPassword
    ) {
        PasswordError::new_err(message)
    } else if matches!(
        e,
        lopdf::Error::Decompress(DecompressError::MemoryLimitExceeded { .. })
    ) {
        limit_err("decompressed_size", message)
    } else {
        PdfError::new_err(message)
    }
}

/// Convert a lopdf error to a Python exception.
pub(super) fn to_py_err(e: lopdf::Error) -> PyErr {
    lopdf_err(None, &e)
}

/// Convert a load error while retaining which configured bound protected eager
/// object/xref stream decoding.
pub(super) fn load_err(
    prefix: Option<&str>,
    error: &lopdf::Error,
    limit_code: &'static str,
) -> PyErr {
    if matches!(
        error,
        lopdf::Error::Decompress(DecompressError::MemoryLimitExceeded { .. })
    ) {
        let chain = LopdfErrorChain(error);
        let message = match prefix {
            Some(prefix) => format!("{prefix}: {chain}"),
            None => chain.to_string(),
        };
        limit_err(limit_code, message)
    } else {
        lopdf_err(prefix, error)
    }
}

// Input that cannot be read as a PDF, empty input included; defined in Python
// next to the other errors that only Python raises.
pyo3::import_exception!(pylopdf._errors, MalformedPdfError);

/// Reject empty PDF input before parsing it.
pub(super) fn reject_empty_input(data: &[u8], prefix: Option<&str>) -> PyResult<()> {
    if data.is_empty() {
        let message = match prefix {
            Some(prefix) => format!("{prefix}: the input is empty"),
            None => "cannot open an empty stream".to_owned(),
        };
        return Err(MalformedPdfError::new_err(message));
    }
    Ok(())
}

/// Convert an I/O failure on a caller's path, raising Python's built-in
/// `FileNotFoundError` for a missing file and `PdfError` otherwise.
pub(crate) fn path_io_err(prefix: &str, path: &str, error: &std::io::Error) -> PyErr {
    if error.kind() == std::io::ErrorKind::NotFound {
        // ENOENT is 2 on every supported platform, as Python's own open() reports it.
        pyo3::exceptions::PyFileNotFoundError::new_err((
            2,
            "No such file or directory",
            path.to_owned(),
        ))
    } else {
        PdfError::new_err(format!("{prefix} {path}: {error}"))
    }
}

/// Convert a lenient-load failure.
///
/// Input that cannot be parsed raises `MalformedPdfError`; a wrong password and an
/// exceeded decompression budget keep `PasswordError` and `LimitError`.
pub(super) fn recovery_load_err(
    prefix: Option<&str>,
    error: &lopdf::Error,
    limit_code: &'static str,
) -> PyErr {
    match error {
        lopdf::Error::Decryption(_)
        | lopdf::Error::InvalidPassword
        | lopdf::Error::Decompress(DecompressError::MemoryLimitExceeded { .. }) => {
            load_err(prefix, error, limit_code)
        }
        error => {
            let chain = LopdfErrorChain(error);
            MalformedPdfError::new_err(match prefix {
                Some(prefix) => format!("{prefix}: {chain}"),
                None => chain.to_string(),
            })
        }
    }
}

/// Construct a machine-readable resource-limit exception.
///
/// The first exception argument is a stable code; Python supplies a friendly
/// `str(error)` and `error.code` view over the two arguments.
pub(crate) fn limit_err(code: &'static str, message: impl Into<String>) -> PyErr {
    LimitError::new_err((code, message.into()))
}
