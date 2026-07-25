"""Verify a pylopdf wheel built for the Pyodide Emscripten ABI."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from zipfile import ZipFile

_ULEB_CONTINUATION = 0x80
_MAX_ULEB_SHIFT = 64
_IMPORT_SECTION = 2
_FUNCTION_IMPORT = 0
_TABLE_IMPORT = 1
_MEMORY_IMPORT = 2
_GLOBAL_IMPORT = 3
_TAG_IMPORT = 4


def _read_uleb(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if offset >= len(data):
            msg = "truncated WebAssembly unsigned LEB128 value"
            raise RuntimeError(msg)
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < _ULEB_CONTINUATION:
            return value, offset
        shift += 7
        if shift >= _MAX_ULEB_SHIFT:
            msg = "oversized WebAssembly unsigned LEB128 value"
            raise RuntimeError(msg)


def _read_name(data: bytes, offset: int) -> tuple[str, int]:
    length, offset = _read_uleb(data, offset)
    end = offset + length
    if end > len(data):
        msg = "truncated WebAssembly name"
        raise RuntimeError(msg)
    return data[offset:end].decode(), end


def _read_limits(data: bytes, offset: int) -> int:
    flags, offset = _read_uleb(data, offset)
    _, offset = _read_uleb(data, offset)
    if flags & 1:
        _, offset = _read_uleb(data, offset)
    return offset


def _skip_import_type(data: bytes, offset: int, kind: int) -> int:
    if kind == _FUNCTION_IMPORT:
        _, offset = _read_uleb(data, offset)
    elif kind == _TABLE_IMPORT:
        offset = _read_limits(data, offset + 1)
    elif kind == _MEMORY_IMPORT:
        offset = _read_limits(data, offset)
    elif kind == _GLOBAL_IMPORT:
        offset += 2
    elif kind == _TAG_IMPORT:
        offset += 1
        _, offset = _read_uleb(data, offset)
    else:
        msg = f"unsupported WebAssembly import kind: {kind}"
        raise RuntimeError(msg)
    return offset


def _read_imports(extension: bytes) -> list[tuple[str, str, int]]:
    imports: list[tuple[str, str, int]] = []
    offset = 8
    while offset < len(extension):
        section_id = extension[offset]
        section_size, offset = _read_uleb(extension, offset + 1)
        section_end = offset + section_size
        if section_end > len(extension):
            msg = "truncated WebAssembly section"
            raise RuntimeError(msg)
        if section_id != _IMPORT_SECTION:
            offset = section_end
            continue

        count, offset = _read_uleb(extension, offset)
        for _ in range(count):
            module, offset = _read_name(extension, offset)
            name, offset = _read_name(extension, offset)
            if offset >= section_end:
                msg = "truncated WebAssembly import"
                raise RuntimeError(msg)
            kind = extension[offset]
            offset += 1
            imports.append((module, name, kind))
            offset = _skip_import_type(extension, offset, kind)
            if offset > section_end:
                msg = "WebAssembly import exceeds its section"
                raise RuntimeError(msg)
        return imports
    return imports


def verify_wheel(wheel: Path, *, version: str, platform: str) -> None:
    """Verify wheel tags and the Emscripten extension contract."""
    expected_tag = f"cp310-abi3-{platform}"
    expected_suffix = f"-{expected_tag}.whl"
    if not wheel.name.startswith(f"pylopdf-{version}-") or not wheel.name.endswith(expected_suffix):
        msg = f"unexpected wheel filename: {wheel.name}; expected *{expected_suffix}"
        raise RuntimeError(msg)

    with ZipFile(wheel) as archive:
        wheel_metadata_paths = [name for name in archive.namelist() if name.endswith(".dist-info/WHEEL")]
        if len(wheel_metadata_paths) != 1:
            msg = f"expected one WHEEL metadata file, found {wheel_metadata_paths}"
            raise RuntimeError(msg)
        wheel_metadata = archive.read(wheel_metadata_paths[0]).decode()
        if f"Tag: {expected_tag}" not in wheel_metadata:
            msg = f"wheel metadata does not contain Tag: {expected_tag}"
            raise RuntimeError(msg)
        extensions = [name for name in archive.namelist() if name.endswith(".so")]
        if len(extensions) != 1:
            msg = f"expected one extension module, found {extensions}"
            raise RuntimeError(msg)
        extension = archive.read(extensions[0])

    if extension[:8] != b"\0asm\x01\0\0\0":
        msg = "extension module is not a WebAssembly 1 binary"
        raise RuntimeError(msg)

    imports = _read_imports(extension)
    if ("env", "__cpp_exception", 4) not in imports:
        msg = "extension does not import the WebAssembly exception tag"
        raise RuntimeError(msg)
    if any(module == "__wbindgen_placeholder__" for module, _, _ in imports):
        msg = "extension unexpectedly requires a wasm-bindgen JavaScript shim"
        raise RuntimeError(msg)

    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    sys.stdout.write(
        f"wheel={wheel}\n"
        f"tag={expected_tag}\n"
        "exception_tag=env.__cpp_exception\n"
        f"size_bytes={wheel.stat().st_size}\n"
        f"sha256={digest}\n"
    )


def main() -> None:
    """Parse command-line arguments and verify one wheel."""
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--platform", required=True)
    args = parser.parse_args()
    verify_wheel(args.wheel, version=args.version, platform=args.platform)


if __name__ == "__main__":
    main()
