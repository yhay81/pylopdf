//! Preserve a Windows output file's discretionary access control and inheritance.

use std::{
    fs::OpenOptions,
    io,
    os::windows::{fs::OpenOptionsExt, io::AsRawHandle},
    path::Path,
    ptr,
};
use windows_sys::Win32::{
    Foundation::ERROR_INSUFFICIENT_BUFFER,
    Security::{DACL_SECURITY_INFORMATION, GetKernelObjectSecurity, SetKernelObjectSecurity},
    Storage::FileSystem::{READ_CONTROL, WRITE_DAC},
};

pub(crate) fn copy_dacl(source: &Path, destination: &Path) -> io::Result<()> {
    // Rust's file opening already supports extended-length and UNC paths.
    let source = OpenOptions::new().access_mode(READ_CONTROL).open(source)?;
    let destination = OpenOptions::new()
        .access_mode(WRITE_DAC)
        .open(destination)?;
    let mut size = 0_u32;
    // SAFETY: Both handles are owned Files. A null, zero-length buffer requests
    // only the required descriptor size; the output pointer is valid.
    let success = unsafe {
        GetKernelObjectSecurity(
            source.as_raw_handle(),
            DACL_SECURITY_INFORMATION,
            ptr::null_mut(),
            0,
            &mut size,
        )
    };
    if success == 0 {
        let error = io::Error::last_os_error();
        if error.raw_os_error() != Some(ERROR_INSUFFICIENT_BUFFER as i32) {
            return Err(error);
        }
    }
    if size == 0 {
        return Err(io::Error::other("empty file security descriptor"));
    }
    // A self-relative SECURITY_DESCRIPTOR requires DWORD alignment. Grow
    // fallibly and retain the descriptor until the destination update returns.
    let mut descriptor = Vec::<u32>::new();
    let words = (size as usize).div_ceil(size_of::<u32>());
    descriptor
        .try_reserve_exact(words)
        .map_err(io::Error::other)?;
    descriptor.resize(words, 0);
    // SAFETY: The aligned buffer holds at least `size` bytes. On success Windows
    // initializes a complete self-relative descriptor. A concurrent ACL change
    // that needs more space fails the save rather than exposing a partial ACL.
    if unsafe {
        GetKernelObjectSecurity(
            source.as_raw_handle(),
            DACL_SECURITY_INFORMATION,
            descriptor.as_mut_ptr().cast(),
            size,
            &mut size,
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    // Copy the stored descriptor verbatim, including inheritance protection.
    // Get/SetNamedSecurityInfo can reinterpret legacy inheritance control bits
    // or merge parent ACEs, changing the permissions that the caller supplied.
    // SAFETY: The handle has WRITE_DAC and the complete descriptor remains live.
    if unsafe {
        SetKernelObjectSecurity(
            destination.as_raw_handle(),
            DACL_SECURITY_INFORMATION,
            descriptor.as_mut_ptr().cast(),
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

#[pyo3::pyfunction]
pub(crate) fn _copy_windows_dacl(
    py: pyo3::Python<'_>,
    source: std::path::PathBuf,
    destination: std::path::PathBuf,
) -> pyo3::PyResult<()> {
    py.detach(|| copy_dacl(&source, &destination))?;
    Ok(())
}
