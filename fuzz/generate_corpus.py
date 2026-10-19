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


def _shading_with_function(objects: dict[int, bytes | str]) -> bytes:
    """Build a page that draws a shading whose `/Function` is object 6; the caller supplies objects 6 and up."""
    return _build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << /Shading << /Sh1 5 0 R >> >> "
            "/Contents 4 0 R >>",
            4: b"<< /Length 7 >>\nstream\n/Sh1 sh\nendstream",
            5: "<< /ShadingType 2 /ColorSpace /DeviceRGB /Coords [0 0 100 0] /Function 6 0 R >>",
            **objects,
        }
    )


def _function_chain(length: int) -> bytes:
    """Stitching functions that each name the next one, which hayro 0.7 and 0.8 build by recursion."""
    objects: dict[int, bytes | str] = {
        number: f"<< /FunctionType 3 /Domain [0 1] /Functions [{number + 1} 0 R] /Bounds [] /Encode [0 1] >>"
        for number in range(6, 6 + length - 1)
    }
    objects[5 + length] = "<< /FunctionType 2 /Domain [0 1] /C0 [0 0 0] /C1 [1 1 1] /N 1 >>"
    return _shading_with_function(objects)


def _color_space_chain(length: int) -> bytes:
    """Indexed colour spaces that each take the next one as their base."""
    objects: dict[int, bytes | str] = {
        number: f"[/Indexed {number + 1} 0 R 1 <00 FF>]" for number in range(6, 6 + length - 1)
    }
    objects[5 + length] = "[/Indexed /DeviceGray 1 <00 FF>]"
    return _build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << /ColorSpace << /Cs1 6 0 R >> >> "
            "/Contents 4 0 R >>",
            4: b"<< /Length 22 >>\nstream\n/Cs1 cs 0 sc 0 0 9 9 re f\nendstream",
            5: "<< >>",
            **objects,
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
    nested_content = b"BT /F1 12 Tf " + b"[" * 100_000 + b"(X)" + b"]" * 100_000 + b" TJ ET"
    # hayro resumes tokenizing behind an `EI` that a regular byte follows.
    inline_image_resume = b"BI /W 1 /H 1 ID \x00 EITJ " + b"<<" * 100_000
    # hayro reads a dictionary behind every `BI` it meets while it searches for the image end.
    inline_image_search = b"BI /W 1 /H 1 ID \xff EI (BI /A " + b"<<" * 100_000
    # hayro 0.7 and 0.8 parse the braces of a Type 4 function by recursion.
    deep_program = b"{" * 100_000 + b"}" * 100_000
    type4_header = f"<< /FunctionType 4 /Domain [0 1] /Range [0 1 0 1 0 1] /Length {len(deep_program)} >>"
    cases = {
        "bad-xref.pdf": b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\nstartxref\n999999\n%%EOF",
        "deep-content-stream.pdf": _one_page(nested_content),
        "deep-color-space-chain.pdf": _color_space_chain(5_000),
        "deep-direct-object.pdf": _build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>",
                4: deep_array,
            }
        ),
        "deep-function-chain.pdf": _function_chain(5_000),
        "deep-type4-program.pdf": _shading_with_function(
            {6: f"{type4_header}\nstream\n".encode() + deep_program + b"\nendstream"}
        ),
        "flate-bomb.pdf": _one_page(zlib.compress(expanded), filter_name="FlateDecode"),
        "inline-image-resume.pdf": _one_page(inline_image_resume),
        "inline-image-search.pdf": _one_page(inline_image_search),
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
