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
_MIB = 1024 * 1024
_FUZZ_LIMITS = pylopdf.DocumentLimits(
    max_file_size=4 * _MIB,
    max_pages=_MAX_PAGES,
    max_objects=20_000,
    max_decompressed_size=16 * _MIB,
    max_page_content_size=4 * _MIB,
    max_total_decompressed_size=32 * _MIB,
    max_object_depth=64,
    max_text_size=_MIB,
)


def test_one_input(data: bytes) -> None:
    """Exercise parsing, extraction, rendering, editing, saving, and reopening."""
    if not data or len(data) > _MAX_INPUT_BYTES:
        return

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pylopdf.PylopdfWarning)
        try:
            with pylopdf.open(data, limits=_FUZZ_LIMITS) as doc:
                for page in doc.pages[:_MAX_PAGES]:
                    page.layout().to_dict()
                    page.layout(chars=True).to_dict()
                    page.search("pdf")
                    page.pixmap(dpi=18, alpha=True)
                    operations = page.operations
                    data = operations.to_bytes()
                    if pylopdf.Operations.parse(data, max_size=None) != operations:
                        msg = "content operations do not round-trip"
                        raise AssertionError(msg)
                    page.operations = operations

                doc.metadata.update({"producer": "pylopdf fuzz"})
                saved = doc.to_bytes(dedupe=True, renumber=True, object_streams=True)

            with pylopdf.open(saved, limits=_FUZZ_LIMITS) as reopened:
                if reopened.pages:
                    reopened.pages[0].text()
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
