"""Coverage-guided fuzzing for pylopdf's public document workflow."""

from __future__ import annotations

import contextlib
import sys
import warnings

import atheris

with atheris.instrument_imports():
    import pylopdf

_MAX_INPUT_BYTES = 1_048_576
_MAX_PAGES = 2
_MAX_DECOMPRESSED_BYTES = 16 * 1024 * 1024


def test_one_input(data: bytes) -> None:
    """Exercise parsing, extraction, rendering, editing, saving, and reopening."""
    if not data or len(data) > _MAX_INPUT_BYTES:
        return

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pylopdf.PylopdfWarning)
        try:
            with pylopdf.open(
                stream=data,
                max_decompressed_size=_MAX_DECOMPRESSED_BYTES,
            ) as doc:
                page_count = min(doc.page_count, _MAX_PAGES)
                for page_number in range(page_count):
                    page = doc[page_number]
                    page.get_text("dict")
                    page.search_for("pdf")
                    page.get_pixmap(dpi=18)

                doc.set_metadata({"producer": "pylopdf fuzz"})
                saved = doc.tobytes(garbage=True, deflate=True, object_streams=True)

            with pylopdf.open(
                stream=saved,
                max_decompressed_size=_MAX_DECOMPRESSED_BYTES,
            ) as reopened:
                if reopened.page_count:
                    reopened[0].get_text()
        except pylopdf.PdfError:
            # Invalid or unsupported PDFs are expected. Crashes, panics, and
            # exceptions outside pylopdf's documented error hierarchy are not.
            return


def main() -> None:
    """Configure Atheris and enter the libFuzzer loop."""
    atheris.Setup(sys.argv, test_one_input)
    atheris.Fuzz()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        main()
