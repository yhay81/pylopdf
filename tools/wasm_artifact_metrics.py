"""Record reproducible size facts for one pylopdf PyEmscripten wheel."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

_WASM_MAGIC = b"\0asm"
_WASM_VERSION_1 = b"\x01\0\0\0"
_VARUINT_CONTINUATION = 0x80
_MAX_U32 = (1 << 32) - 1
_SECTION_NAMES = {
    0: "custom",
    1: "type",
    2: "import",
    3: "function",
    4: "table",
    5: "memory",
    6: "global",
    7: "export",
    8: "start",
    9: "element",
    10: "code",
    11: "data",
    12: "data_count",
    13: "tag",
}


def _read_varuint32(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 35, 7):
        if offset >= len(data):
            msg = "truncated WebAssembly varuint32"
            raise ValueError(msg)
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < _VARUINT_CONTINUATION:
            if value > _MAX_U32:
                msg = "WebAssembly varuint32 exceeds 32 bits"
                raise ValueError(msg)
            return value, offset
    msg = "invalid WebAssembly varuint32"
    raise ValueError(msg)


def wasm_section_metrics(data: bytes) -> dict[str, dict[str, int]]:
    """Return aggregate encoded and payload bytes for each Wasm section."""
    if data[:4] != _WASM_MAGIC or data[4:8] != _WASM_VERSION_1:
        msg = "extension module is not a WebAssembly 1 binary"
        raise ValueError(msg)
    sections: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: {"count": 0, "encoded_bytes": 0, "payload_bytes": 0}
    )
    offset = 8
    while offset < len(data):
        section_start = offset
        section_id = data[offset]
        offset += 1
        payload_size, payload_start = _read_varuint32(data, offset)
        payload_end = payload_start + payload_size
        if payload_end > len(data):
            msg = f"WebAssembly section {section_id} exceeds the input"
            raise ValueError(msg)
        name = _SECTION_NAMES.get(section_id, f"unknown_{section_id}")
        if section_id == 0 and payload_size:
            custom_length, custom_start = _read_varuint32(data, payload_start)
            custom_end = custom_start + custom_length
            if custom_end > payload_end:
                msg = "WebAssembly custom-section name exceeds its payload"
                raise ValueError(msg)
            custom_name = data[custom_start:custom_end].decode("utf-8", errors="replace")
            name = f"custom:{custom_name}"
        entry = sections[name]
        entry["count"] += 1
        entry["encoded_bytes"] += payload_end - section_start
        entry["payload_bytes"] += payload_size
        offset = payload_end
    return dict(sorted(sections.items()))


def _archive_group(name: str, extension_name: str) -> str:
    if name == extension_name:
        return "extension"
    if ".dist-info/licenses/" in name:
        return "licenses"
    if ".dist-info/sboms/" in name:
        return "sbom"
    if ".dist-info/" in name:
        return "metadata"
    if name.endswith((".py", ".pyi", "py.typed")):
        return "python"
    return "other"


def collect_metrics(wheel: Path) -> dict[str, Any]:
    """Inspect one wheel without extracting it."""
    wheel = wheel.resolve()
    wheel_data = wheel.read_bytes()
    with zipfile.ZipFile(wheel) as archive:
        files = [info for info in archive.infolist() if not info.is_dir()]
        extensions = [info for info in files if info.filename.endswith(".so")]
        if len(extensions) != 1:
            msg = f"expected one WebAssembly extension, found {[item.filename for item in extensions]}"
            raise ValueError(msg)
        extension = extensions[0]
        extension_data = archive.read(extension)
        groups: defaultdict[str, dict[str, int]] = defaultdict(
            lambda: {"files": 0, "compressed_bytes": 0, "uncompressed_bytes": 0}
        )
        for info in files:
            group = groups[_archive_group(info.filename, extension.filename)]
            group["files"] += 1
            group["compressed_bytes"] += info.compress_size
            group["uncompressed_bytes"] += info.file_size
    return {
        "schema": 1,
        "wheel": {
            "filename": wheel.name,
            "sha256": hashlib.sha256(wheel_data).hexdigest(),
            "bytes": len(wheel_data),
            "files": len(files),
            "compressed_payload_bytes": sum(info.compress_size for info in files),
            "uncompressed_bytes": sum(info.file_size for info in files),
        },
        "groups": dict(sorted(groups.items())),
        "extension": {
            "path": extension.filename,
            "compressed_bytes": extension.compress_size,
            "uncompressed_bytes": extension.file_size,
            "wasm_header_bytes": 8,
            "sections": wasm_section_metrics(extension_data),
        },
    }


def main() -> None:
    """Parse arguments and emit stable JSON."""
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.wheel.is_file():
        parser.error(f"wheel does not exist: {args.wheel}")
    result = json.dumps(
        collect_metrics(args.wheel),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    sys_output = f"{result}\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(sys_output, encoding="utf-8")
    sys.stdout.write(f"{result}\n")


if __name__ == "__main__":
    main()
