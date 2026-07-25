"""Generate deterministic adversarial seeds for native and Wasm boundaries."""

from __future__ import annotations

import argparse
import zlib
from pathlib import Path


def _build_raw_pdf(objects: dict[int, bytes | str]) -> bytes:
    """Build one minimal xref-table PDF from consecutive indirect objects."""
    numbers = list(range(1, len(objects) + 1))
    if sorted(objects) != numbers:
        msg = "object numbers must be consecutive"
        raise ValueError(msg)
    output = bytearray(b"%PDF-1.7\n")
    offsets: dict[int, int] = {}
    for number in numbers:
        value = objects[number]
        body = value.encode("latin-1") if isinstance(value, str) else value
        offsets[number] = len(output)
        output.extend(f"{number} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref_position = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for number in numbers:
        output.extend(f"{offsets[number]:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_position}\n%%EOF".encode())
    return bytes(output)


def _one_page(content: bytes, *, filter_name: str | None = None) -> bytes:
    filter_entry = "" if filter_name is None else f" /Filter /{filter_name}"
    return _build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R >>",
            4: (f"<< /Length {len(content)}{filter_entry} >>\nstream\n".encode() + content + b"\nendstream"),
        }
    )


def _reference_cycle() -> bytes:
    return _build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /Cycle 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
            4: "<< /Next 5 0 R >>",
            5: "<< /Next 4 0 R >>",
        }
    )


def _many_pages(count: int) -> bytes:
    kids = " ".join(f"{number} 0 R" for number in range(3, count + 3))
    objects: dict[int, str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {count} >>",
    }
    objects.update(
        dict.fromkeys(
            range(3, count + 3),
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
        )
    )
    return _build_raw_pdf(objects)


def generate(output: Path) -> None:
    """Write bounded seeds that exercise each hostile-input class."""
    output.mkdir(parents=True, exist_ok=True)
    expanded = b" " * (8 * 1024 * 1024)
    run_length = bytes((129, 0)) * 65_536 + bytes((128,))
    deep_array = "[" * 96 + "0" + "]" * 96
    cases = {
        "bad-xref.pdf": b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\nstartxref\n999999\n%%EOF",
        "deep-direct-object.pdf": _build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
                4: deep_array,
            }
        ),
        "flate-bomb.pdf": _one_page(zlib.compress(expanded), filter_name="FlateDecode"),
        "many-pages.pdf": _many_pages(250),
        "reference-cycle.pdf": _reference_cycle(),
        "run-length-bomb.pdf": _one_page(run_length, filter_name="RunLengthDecode"),
        "truncated-stream.pdf": b"%PDF-1.7\n1 0 obj\n<< /Length 100 >>\nstream\ntruncated",
        "unverifiable-filter.pdf": _one_page(b"opaque", filter_name="Crypt"),
    }
    for name, data in cases.items():
        (output / name).write_bytes(data)


def main() -> None:
    """Parse the output directory and generate the seed corpus."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    generate(args.output)


if __name__ == "__main__":
    main()
