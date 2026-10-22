"""Error categories for missing, empty, and unreadable input."""

from __future__ import annotations

import errno
import pickle
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import build_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets"


def test_malformed_pdf_error_is_a_pdf_error() -> None:
    assert issubclass(pylopdf.MalformedPdfError, pylopdf.PdfError)
    assert issubclass(pylopdf.MalformedPdfError, ValueError)
    assert not issubclass(pylopdf.MalformedPdfError, RuntimeError)
    assert pylopdf.MalformedPdfError.__module__ == "pylopdf"
    restored = pickle.loads(pickle.dumps(pylopdf.MalformedPdfError("empty")))  # noqa: S301 - own value
    assert type(restored) is pylopdf.MalformedPdfError
    assert restored.args == ("empty",)


@pytest.mark.parametrize(
    "opener",
    [
        pytest.param(pylopdf.open, id="open"),
        pytest.param(pylopdf.probe, id="probe"),
    ],
)
def test_missing_file_raises_builtin_file_not_found(tmp_path: Path, opener: Callable[[Path], object]) -> None:
    missing = tmp_path / "missing.pdf"

    with pytest.raises(FileNotFoundError) as caught:
        opener(missing)

    assert caught.value.errno == errno.ENOENT
    assert caught.value.filename == str(missing)
    assert not isinstance(caught.value, pylopdf.PdfError)


def test_missing_font_and_image_files_raise_file_not_found(tmp_path: Path) -> None:
    page = pylopdf.open(build_pdf([""])).pages[0]

    with pytest.raises(FileNotFoundError):
        page.insert_text((20, 40), "x", fontfile=tmp_path / "missing.otf")
    with pytest.raises(FileNotFoundError):
        page.insert_image((0, 0, 50, 50), filename=tmp_path / "missing.png")
    with pytest.raises(FileNotFoundError):
        pylopdf.Font(fontfile=tmp_path / "missing.otf")


def test_empty_input_raises_malformed_pdf_error(tmp_path: Path) -> None:
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")

    with pytest.raises(pylopdf.MalformedPdfError):
        pylopdf.open(b"")
    with pytest.raises(pylopdf.MalformedPdfError):
        pylopdf.open(empty)
    with pytest.raises(pylopdf.MalformedPdfError):
        pylopdf.probe(b"")
    with pytest.raises(pylopdf.MalformedPdfError):
        pylopdf.probe(empty)


@pytest.mark.parametrize("data", [b"not a pdf at all", b"%PDF-1.7\n%%EOF\n", b"\x00" * 64])
def test_unreadable_input_raises_malformed_pdf_error(tmp_path: Path, data: bytes) -> None:
    target = tmp_path / "bad.pdf"
    target.write_bytes(data)

    for source in (data, target):
        with pytest.raises(pylopdf.MalformedPdfError):
            pylopdf.open(source)
    with pytest.raises(pylopdf.MalformedPdfError):
        pylopdf.probe(data)


def test_password_and_limit_failures_keep_their_types() -> None:
    encrypted = (ASSETS / "encrypted" / "user-aes-256.pdf").read_bytes()
    with pytest.raises(pylopdf.PasswordError) as wrong_password:
        pylopdf.open(encrypted, password="wrong")
    assert not isinstance(wrong_password.value, pylopdf.MalformedPdfError)

    with pytest.raises(pylopdf.LimitError) as too_large:
        pylopdf.open(build_pdf(["x"]), limits=pylopdf.DocumentLimits(max_file_size=16))
    assert too_large.value.code == "file_size"
    assert not isinstance(too_large.value, pylopdf.MalformedPdfError)
