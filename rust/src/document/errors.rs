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

/// Convert a lenient-load failure, including a refused recovery scan.
pub(super) fn recovery_load_err(
    prefix: Option<&str>,
    error: &recovery::LoadError,
    limit_code: &'static str,
) -> PyErr {
    match error {
        recovery::LoadError::Pdf(error) => load_err(prefix, error, limit_code),
        recovery::LoadError::RecoveryScan { work, budget } => {
            let message = format!(
                "cross-reference recovery could scan about {work} bytes, exceeding the \
                 {budget}-byte linear safety limit for this input"
            );
            limit_err(
                "recovery_scan_size",
                match prefix {
                    Some(prefix) => format!("{prefix}: {message}"),
                    None => message,
                },
            )
        }
        recovery::LoadError::Allocation(error) => {
            let message = format!("failed to allocate cross-reference recovery: {error}");
            PdfError::new_err(match prefix {
                Some(prefix) => format!("{prefix}: {message}"),
                None => message,
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
