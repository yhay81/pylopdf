"""Exercise real Windows access controls, sharing modes, and long output paths."""

from __future__ import annotations

import ctypes
import stat
import sys
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import pylopdf
from pylopdf._document import _atomic_save_file

# A module-level skip also tells type checkers that the Win32-only ctypes
# members below are unreachable on other platforms.
if sys.platform != "win32":
    pytest.skip("requires Windows filesystem semantics", allow_module_level=True)

_DACL_INFORMATION = 4
_DACL_PROTECTED = 0x1000


class _WindowsFiles:
    """Use independent Win32 calls to inspect the public save operations."""

    def __init__(self) -> None:
        """Declare pointer widths explicitly, including on 64-bit Python."""
        self.security = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        pointer = ctypes.c_void_p
        dword = ctypes.c_uint32
        self.security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            ctypes.c_wchar_p,
            dword,
            ctypes.POINTER(pointer),
            pointer,
        ]
        self.security.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = ctypes.c_int
        self.security.SetFileSecurityW.argtypes = [ctypes.c_wchar_p, dword, pointer]
        self.security.SetFileSecurityW.restype = ctypes.c_int
        self.security.GetFileSecurityW.argtypes = [
            ctypes.c_wchar_p,
            dword,
            pointer,
            dword,
            ctypes.POINTER(dword),
        ]
        self.security.GetFileSecurityW.restype = ctypes.c_int
        self.security.GetSecurityDescriptorControl.argtypes = [
            pointer,
            ctypes.POINTER(ctypes.c_uint16),
            ctypes.POINTER(dword),
        ]
        self.security.GetSecurityDescriptorControl.restype = ctypes.c_int
        self.security.GetSecurityDescriptorDacl.argtypes = [
            pointer,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(pointer),
            ctypes.POINTER(ctypes.c_int),
        ]
        self.security.GetSecurityDescriptorDacl.restype = ctypes.c_int
        self.kernel.LocalFree.argtypes = [pointer]
        self.kernel.LocalFree.restype = pointer
        self.kernel.CreateFileW.argtypes = [
            ctypes.c_wchar_p,
            dword,
            dword,
            pointer,
            dword,
            dword,
            pointer,
        ]
        self.kernel.CreateFileW.restype = pointer
        self.kernel.CloseHandle.argtypes = [pointer]
        self.kernel.CloseHandle.restype = ctypes.c_int

    def set_dacl(self, path: Path, sddl: str) -> None:
        """Set only a disposable fixture's discretionary security descriptor."""
        descriptor = ctypes.c_void_p()
        if not self.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl,
            1,
            ctypes.byref(descriptor),
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.security.SetFileSecurityW(str(path), _DACL_INFORMATION, descriptor):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.kernel.LocalFree(descriptor)

    def dacl(self, path: Path) -> tuple[bool, bytes]:
        """Return protection plus the complete ACL, including inherited ACEs."""
        size = ctypes.c_uint32()
        self.security.GetFileSecurityW(str(path), _DACL_INFORMATION, None, 0, ctypes.byref(size))
        assert size.value > 0
        descriptor = ctypes.create_string_buffer(size.value)
        if not self.security.GetFileSecurityW(
            str(path),
            _DACL_INFORMATION,
            descriptor,
            size.value,
            ctypes.byref(size),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        control = ctypes.c_uint16()
        revision = ctypes.c_uint32()
        assert self.security.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision))
        present = ctypes.c_int()
        defaulted = ctypes.c_int()
        acl = ctypes.c_void_p()
        assert self.security.GetSecurityDescriptorDacl(
            descriptor,
            ctypes.byref(present),
            ctypes.byref(acl),
            ctypes.byref(defaulted),
        )
        assert present.value
        # ACL.AclSize is the WORD at offset 2 of the eight-byte ACL header.
        header = ctypes.string_at(acl, 8)
        return bool(control.value & _DACL_PROTECTED), ctypes.string_at(acl, int.from_bytes(header[2:4], "little"))

    @contextmanager
    def deny_delete_sharing(self, path: Path) -> Generator[None]:
        """Hold a real reader that permits reads/writes but denies replacement."""
        handle = self.kernel.CreateFileW(str(path), 0x80000000, 3, None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            assert self.kernel.CloseHandle(handle)


@pytest.fixture
def windows_files() -> _WindowsFiles:
    return _WindowsFiles()


@pytest.fixture(params=["pdf", "object-streams", "encrypted", "png"])
def output(request: pytest.FixtureRequest, one_page_pdf: bytes) -> Iterator[tuple[str, Callable[[Path], None]]]:
    """Exercise every public writer that replaces a filesystem path."""
    with pylopdf.open(stream=one_page_pdf) as doc:
        if request.param == "png":
            yield "png", doc[0].get_pixmap().save
        elif request.param == "object-streams":
            yield "pdf", lambda path: doc.save(path, object_streams=True)
        elif request.param == "encrypted":
            yield "pdf", lambda path: doc.save(path, user_pw="test-password")
        else:
            yield "pdf", doc.save


def _assert_output(path: Path, kind: str) -> None:
    if kind == "png":
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    else:
        with pylopdf.open(path) as doc:
            if doc.needs_pass:
                assert doc.authenticate("test-password")
            assert "Hello PDF" in doc.get_text()


@pytest.mark.parametrize("policy", ["protected", "unprotected", "inherited"])
def test_save_preserves_windows_dacl(
    tmp_path: Path,
    output: tuple[str, Callable[[Path], None]],
    windows_files: _WindowsFiles,
    policy: str,
) -> None:
    kind, save = output
    target = tmp_path / f"output.{kind}"
    target.write_bytes(b"original")
    # Owner Rights grants the fixture's owner full access without looking up an
    # account name or depending on the runner's language or administrator status.
    if policy != "inherited":
        windows_files.set_dacl(target, f"D:{'P' if policy == 'protected' else ''}(A;;FA;;;OW)")
    expected = windows_files.dacl(target)
    assert expected[0] is (policy == "protected")

    save(target)

    assert windows_files.dacl(target) == expected
    _assert_output(target, kind)
    assert list(tmp_path.glob(".pylopdf-*.tmp")) == []


def test_save_readonly_target_preserves_original_and_cleans_up(
    tmp_path: Path,
    output: tuple[str, Callable[[Path], None]],
) -> None:
    kind, save = output
    target = tmp_path / f"readonly.{kind}"
    original = b"original"
    target.write_bytes(original)
    target.chmod(stat.S_IREAD)
    try:
        with pytest.raises(pylopdf.PdfError, match="failed to save"):
            save(target)
        assert target.read_bytes() == original
        assert not target.stat().st_mode & stat.S_IWRITE
        assert list(tmp_path.glob(".pylopdf-*.tmp")) == []
    finally:
        target.chmod(stat.S_IWRITE)
        # Permit pytest to remove artifacts if the regression reappears.
        for temporary in tmp_path.glob(".pylopdf-*.tmp"):
            temporary.chmod(stat.S_IWRITE)


def test_save_locked_target_preserves_original_and_dacl(
    tmp_path: Path,
    output: tuple[str, Callable[[Path], None]],
    windows_files: _WindowsFiles,
) -> None:
    kind, save = output
    target = tmp_path / f"locked.{kind}"
    target.write_bytes(b"original")
    windows_files.set_dacl(target, "D:P(A;;FA;;;OW)")
    expected = windows_files.dacl(target)
    with windows_files.deny_delete_sharing(target), pytest.raises(pylopdf.PdfError, match="failed to save"):
        save(target)
    assert target.read_bytes() == b"original"
    assert windows_files.dacl(target) == expected
    assert list(tmp_path.glob(".pylopdf-*.tmp")) == []


def test_save_refuses_an_unreadable_dacl(
    tmp_path: Path,
    output: tuple[str, Callable[[Path], None]],
    windows_files: _WindowsFiles,
) -> None:
    kind, save = output
    target = tmp_path / f"unreadable-acl.{kind}"
    target.write_bytes(b"original")
    # Owner Rights lets the fixture deny READ_CONTROL even to its owner while
    # retaining WRITE_DAC for restoration. No administrator privilege is needed.
    windows_files.set_dacl(target, "D:P(D;;RC;;;OW)(A;;FA;;;OW)")
    try:
        with pytest.raises(pylopdf.PdfError, match="failed to save"):
            save(target)
        assert list(tmp_path.glob(".pylopdf-*.tmp")) == []
    finally:
        windows_files.set_dacl(target, "D:P(A;;FA;;;OW)")
    assert target.read_bytes() == b"original"


def test_pdf_temporary_is_restricted_before_writing_and_removed_on_writer_failure(
    tmp_path: Path,
    windows_files: _WindowsFiles,
) -> None:
    target = tmp_path / "output.pdf"
    target.write_bytes(b"original")
    windows_files.set_dacl(target, "D:P(A;;FA;;;OW)")
    expected = windows_files.dacl(target)

    def fail_after_partial_write(filename: str) -> None:
        temporary = Path(filename)
        assert windows_files.dacl(temporary) == expected
        temporary.write_bytes(b"partial PDF")
        msg = "writer failed"
        raise pylopdf.PdfError(msg)

    with pytest.raises(pylopdf.PdfError, match="writer failed"):
        _atomic_save_file(target, fail_after_partial_write)

    assert target.read_bytes() == b"original"
    assert windows_files.dacl(target) == expected
    assert list(tmp_path.glob(".pylopdf-*.tmp")) == []


@pytest.mark.parametrize("path_form", ["absolute", "relative", "extended"])
def test_save_long_windows_path(
    tmp_path: Path,
    output: tuple[str, Callable[[Path], None]],
    monkeypatch: pytest.MonkeyPatch,
    path_form: str,
) -> None:
    kind, save = output
    directory = tmp_path / ("a" * 100) / ("b" * 100) / ("c" * 50)
    extended = Path("\\\\?\\" + str(directory))
    extended.mkdir(parents=True)
    name = f"日本語 output.{kind}"
    actual = extended / name
    if path_form == "extended":
        target = actual
    elif path_form == "relative":
        monkeypatch.chdir(tmp_path)
        target = Path("a" * 100) / ("b" * 100) / ("c" * 50) / name
    else:
        target = directory / name
    assert len(str(directory / name)) > 260

    save(target)
    _assert_output(actual, kind)
    # Test an existing target as well as first creation.
    save(target)
    _assert_output(actual, kind)
    assert list(extended.glob(".pylopdf-*.tmp")) == []
