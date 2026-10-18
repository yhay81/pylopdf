"""Regression tests over real-world and independent interoperability PDFs.

Run open, metadata, extraction, editing, saving, and rendering over every PDF
in ``tests/assets/real_world``. This catches lopdf and hayro limitations early.
The adjacent README records sources and licenses.
"""

from __future__ import annotations

import random
import re
import time
import warnings
import zlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import build_raw_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"


@dataclass(frozen=True)
class Case:
    """Expected values for one corpus file."""

    name: str
    pages: int
    version: str
    #: Text expected on page 0; None means tracked separately as a known limit.
    snippet: str | None


CASES = [
    Case("f1040.pdf", pages=2, version="PDF 1.7", snippet="U.S. Individual Income Tax Return"),
    Case("pdf20-simple.pdf", pages=1, version="PDF 2.0", snippet="Hello World"),
    Case("usrguide.pdf", pages=27, version="PDF 1.5", snippet="for authors"),
    Case("bill-hr815.pdf", pages=110, version="PDF 1.5", snippet="One Hundred Eighteenth Congress"),
    Case("mhlw-doc.pdf", pages=2, version="PDF 1.7", snippet="裁判例"),
    Case("bunka-kokugo-series-019-p4.pdf", pages=1, version="PDF 1.7", snippet=None),
    Case("patent-us223898.pdf", pages=4, version="PDF 1.3", snippet="Electric-Lamp"),
    Case("wdl6812-manuscript.pdf", pages=2, version="PDF 1.4", snippet=None),
    Case(
        "nics-background-checks-2015-11.pdf",
        pages=1,
        version="PDF 1.3",
        snippet="NICS Firearm Background Checks",
    ),
    Case("senate-expenditures.pdf", pages=1, version="PDF 1.3", snippet="BAIN, J MATTHEW"),
    Case("pdfium-type3.pdf", pages=1, version="PDF 1.7", snippet=None),
    Case("pdfium-smask-blend.pdf", pages=1, version="PDF 1.7", snippet=None),
    Case("pdfium-jpx-lzw.pdf", pages=1, version="PDF 1.7", snippet=None),
    Case(
        "pdfium-links-highlights-annots.pdf",
        pages=1,
        version="PDF 1.7",
        snippet="Link Annotations",
    ),
]

ALL = pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
WITH_TEXT = pytest.mark.parametrize("case", [c for c in CASES if c.snippet is not None], ids=lambda c: c.name)


CASE_BY_NAME = {case.name: case for case in CASES}

OFFSET_WARNING = "recovered a PDF with an incorrect cross-reference offset"
REBUILT_WARNING = "rebuilt a damaged PDF cross-reference table"


def _startxref_value_span(data: bytes) -> tuple[int, int]:
    """Return the byte span of the final startxref value."""
    eof = data.rfind(b"%%EOF")
    startxref = data.rfind(b"startxref", 0, eof)
    assert startxref >= 0
    number_start = startxref + len(b"startxref")
    while data[number_start] in b"\x00\t\n\x0c\r ":
        number_start += 1
    number_end = number_start
    while data[number_end : number_end + 1].isdigit():
        number_end += 1
    assert number_end > number_start
    return number_start, number_end


def _with_startxref(data: bytes, value: int) -> bytes:
    """Replace the final startxref value without changing any xref section."""
    start, end = _startxref_value_span(data)
    return data[:start] + str(value).encode() + data[end:]


def _uses_classic_xref(data: bytes) -> bool:
    """Return whether the final startxref points at a classic xref table."""
    start, end = _startxref_value_span(data)
    return data[data.index(b"%PDF-") + int(data[start:end]) :].startswith(b"xref")


def _truncated(fraction: float) -> Callable[[str, bytes], bytes]:
    def damage(_name: str, data: bytes) -> bytes:
        return data[: int(len(data) * fraction)]

    return damage


def _junk_prefixed(name: str, data: bytes) -> bytes:
    """Prepend 512 seeded bytes that cannot form a PDF header."""
    rng = random.Random(f"junk:{name}")  # noqa: S311 - deterministic fixture damage
    return bytes(rng.randrange(256) for _ in range(512)).replace(b"%", b"#") + data


def _startxref_keyword_corrupted(_name: str, data: bytes) -> bytes:
    position = data.rfind(b"startxref")
    return data[:position] + b"startxrex" + data[position + len(b"startxref") :]


def _final_xref_section_removed(_name: str, data: bytes) -> bytes:
    """Delete the final classic table up to its trailer, or the final xref stream."""
    start, end = _startxref_value_span(data)
    offset = data.index(b"%PDF-") + int(data[start:end])
    if data.startswith(b"xref", offset):
        return data[:offset] + data[data.index(b"trailer", offset) :]
    return data[:offset] + data[data.index(b"endobj", offset) + len(b"endobj") :]


def _lengths_off_by_37(_name: str, data: bytes) -> bytes:
    """Change the first three direct stream lengths by 37 without moving bytes."""
    damaged = bytearray(data)
    changed = 0
    for match in re.finditer(rb"/Length\s+(\d+)(\s+\d+\s+R)?", data):
        if match.group(2):
            continue
        digits = match.group(1)
        for candidate in (int(digits) + 37, int(digits) - 37):
            if candidate >= 0 and len(str(candidate)) == len(digits):
                damaged[match.start(1) : match.end(1)] = str(candidate).encode()
                changed += 1
                break
        if changed == 3:
            break
    return bytes(damaged)


def _bits_flipped(name: str, data: bytes) -> bytes:
    """Flip 12 seeded bits after the first KiB."""
    rng = random.Random(f"bitflip:{name}")  # noqa: S311 - deterministic fixture damage
    damaged = bytearray(data)
    for _ in range(12):
        damaged[rng.randrange(1024, len(damaged))] ^= 1 << rng.randrange(8)
    return bytes(damaged)


def _endobj_removed(_name: str, data: bytes) -> bytes:
    """Delete the first two endobj keywords, shifting every later offset."""
    for _ in range(2):
        position = data.index(b"endobj")
        data = data[:position] + data[position + len(b"endobj") :]
    return data


DAMAGE: dict[str, Callable[[str, bytes], bytes]] = {
    "truncate-95": _truncated(0.95),
    "truncate-60": _truncated(0.60),
    "junk-prefix-512": _junk_prefixed,
    "startxref-keyword": _startxref_keyword_corrupted,
    "xref-section-removed": _final_xref_section_removed,
    "length-off-37": _lengths_off_by_37,
    "bit-flips-12": _bits_flipped,
    "endobj-removed-2": _endobj_removed,
}

#: Pinned outcome for each damaged variant. ``intact`` opens without
#: cross-reference recovery, ``rebuilt`` reconstructs the table by scanning
#: object headers (``-without-snippet`` when the damage also hit that text),
#: ``offset-broken`` corrects a nearby offset while shifted objects stay
#: unreachable, ``unusable`` opens without a readable page tree, and
#: ``rejected`` raises PdfError. pylopdf 0.14 with lopdf 0.44 rejected every
#: ``rebuilt`` case here. lopdf rebuilds only from a classic trailer, so damaged
#: xref-stream files such as Form 1040 remain rejected.
DAMAGE_OUTCOMES = {
    "pdf20-simple.pdf": {
        "truncate-95": "rejected",
        "truncate-60": "rejected",
        "junk-prefix-512": "intact",
        "startxref-keyword": "rebuilt",
        "xref-section-removed": "rebuilt",
        "length-off-37": "intact",
        "bit-flips-12": "unusable",
        "endobj-removed-2": "offset-broken",
    },
    "nics-background-checks-2015-11.pdf": {
        "truncate-95": "rejected",
        "truncate-60": "rejected",
        "junk-prefix-512": "intact",
        "startxref-keyword": "rebuilt",
        "xref-section-removed": "rebuilt",
        "length-off-37": "intact",
        "bit-flips-12": "rebuilt-without-snippet",
        "endobj-removed-2": "offset-broken",
    },
    "patent-us223898.pdf": {
        "truncate-95": "rebuilt",
        "truncate-60": "rejected",
        "junk-prefix-512": "intact",
        "startxref-keyword": "rebuilt",
        "xref-section-removed": "rebuilt",
        "length-off-37": "intact",
        "bit-flips-12": "intact",
        "endobj-removed-2": "offset-broken",
    },
    "f1040.pdf": {
        "truncate-95": "rejected",
        "truncate-60": "rejected",
        "junk-prefix-512": "intact",
        "startxref-keyword": "rejected",
        "xref-section-removed": "rejected",
        "length-off-37": "rejected",
        "bit-flips-12": "intact",
        "endobj-removed-2": "rejected",
    },
}


def _rgba_pixel(page: pylopdf.Page, x: int, y: int) -> tuple[int, int, int, int]:
    """Return one rendered pixel over a white background."""
    pixmap = page.get_pixmap(background=(255, 255, 255))
    samples = pixmap.samples
    offset = (y * pixmap.width + x) * 4
    return (
        samples[offset],
        samples[offset + 1],
        samples[offset + 2],
        samples[offset + 3],
    )


@ALL
def test_open_from_path_and_stream(case: Case) -> None:
    path = ASSETS / case.name
    assert pylopdf.open(path).page_count == case.pages
    assert pylopdf.open(stream=path.read_bytes()).page_count == case.pages


@ALL
def test_metadata_format(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    assert doc.metadata["format"] == case.version


@ALL
def test_batched_get_text_matches_per_page(case: Case) -> None:
    """One shared-interpreter-cache batch must equal fresh per-page extraction."""
    batched = pylopdf.open(ASSETS / case.name).get_text()
    per_page = "".join(pylopdf.open(ASSETS / case.name).get_page_text(pno) for pno in range(case.pages))
    assert batched == per_page


@ALL
def test_peek_metadata_matches_full_load(case: Case) -> None:
    """peek_metadata returns the same page count as a full load."""
    meta = pylopdf.peek_metadata(ASSETS / case.name)
    assert meta["page_count"] == case.pages
    assert meta["encrypted"] is False
    assert meta["repaired"] is False


def _has_ink(page: pylopdf.Page) -> bool:
    """Return whether a low-resolution render of the page is not blank."""
    samples = page.get_pixmap(0.5, background=(255, 255, 255)).samples
    return any(samples[offset : offset + 3] != b"\xff\xff\xff" for offset in range(0, len(samples), 4))


@pytest.mark.parametrize(
    ("name", "damage", "outcome"),
    [
        pytest.param(name, damage, outcome, id=f"{name}-{damage}")
        for name, outcomes in DAMAGE_OUTCOMES.items()
        for damage, outcome in outcomes.items()
    ],
)
def test_damaged_corpus_outcome(name: str, damage: str, outcome: str) -> None:
    """Pin open, extract, render, probe, and save outcomes for damaged inputs."""
    case = CASE_BY_NAME[name]
    damaged = DAMAGE[damage](name, (ASSETS / name).read_bytes())

    if outcome == "rejected":
        with pytest.raises(pylopdf.PdfError):
            pylopdf.open(stream=damaged)
        with pytest.raises(pylopdf.PdfError):
            pylopdf.peek_metadata(stream=damaged)
        return

    expected_warning = {
        "rebuilt": REBUILT_WARNING,
        "rebuilt-without-snippet": REBUILT_WARNING,
        "offset-broken": OFFSET_WARNING,
    }.get(outcome)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        doc = pylopdf.open(stream=damaged)
        probe = pylopdf.peek_metadata(stream=damaged)
    messages = [str(warning.message) for warning in caught if issubclass(warning.category, pylopdf.PylopdfWarning)]
    assert doc.is_repaired is (expected_warning is not None)
    assert probe["repaired"] is doc.is_repaired
    if expected_warning is None:
        assert not [message for message in messages if "cross-reference" in message]
    else:
        assert [message for message in messages if expected_warning in message] == messages
        # One warning comes from opening and one from the metadata probe.
        assert len(messages) == 2

    if outcome in {"unusable", "offset-broken"}:
        with pytest.raises(pylopdf.PdfError):
            _ = doc.page_count
        return

    assert doc.page_count == probe["page_count"] == case.pages
    if case.snippet is not None and outcome != "rebuilt-without-snippet":
        assert case.snippet in doc.get_page_text(0)
    assert _has_ink(doc[0])
    if expected_warning == REBUILT_WARNING:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            reopened = pylopdf.open(stream=doc.tobytes())
        assert not caught
        assert reopened.is_repaired is False
        assert reopened.page_count == case.pages
        assert reopened.get_page_text(0) == doc.get_page_text(0)


@ALL
@pytest.mark.filterwarnings("ignore:text extraction skipped glyphs")
def test_incorrect_startxref_value_recovers_classic_xref_exactly(case: Case) -> None:
    """Recover nearby and distant startxref damage without changing text."""
    original = (ASSETS / case.name).read_bytes()
    start, end = _startxref_value_span(original)
    expected_text = pylopdf.open(stream=original).get_text()

    for value, warning in ((int(original[start:end]) + 12, OFFSET_WARNING), (0, REBUILT_WARNING)):
        damaged = _with_startxref(original, value)
        if not _uses_classic_xref(original):
            with pytest.raises(pylopdf.PdfError):
                pylopdf.open(stream=damaged)
            continue
        with pytest.warns(pylopdf.PylopdfWarning, match=warning):
            doc = pylopdf.open(stream=damaged)
        assert doc.is_repaired is True
        assert doc.get_text() == expected_text
        with pytest.warns(pylopdf.PylopdfWarning, match=warning):
            assert pylopdf.peek_metadata(stream=damaged)["repaired"] is True


def test_incorrect_startxref_path_probe_and_prefixed_stream(tmp_path: Path) -> None:
    """Expose recovery in path probes and keep offsets relative to the header."""
    damaged = _with_startxref((ASSETS / "pdf20-simple.pdf").read_bytes(), 0)
    path = tmp_path / "incorrect-startxref.pdf"
    path.write_bytes(damaged)

    with pytest.warns(pylopdf.PylopdfWarning, match=REBUILT_WARNING):
        metadata = pylopdf.peek_metadata(path)
    assert metadata["page_count"] == 1
    assert metadata["repaired"] is True

    with pytest.warns(pylopdf.PylopdfWarning, match=REBUILT_WARNING):
        from_path = pylopdf.open(path)
    assert from_path.is_repaired is True

    with pytest.warns(pylopdf.PylopdfWarning, match=REBUILT_WARNING):
        prefixed = pylopdf.open(stream=b"transport prefix\n" + damaged)
    assert prefixed.is_repaired is True
    assert "Hello World" in prefixed.get_page_text(0)

    clean = pylopdf.open(stream=b"transport prefix\n" + (ASSETS / "pdf20-simple.pdf").read_bytes())
    assert clean.is_repaired is False


def test_rebuilt_trailer_drops_stale_chain_offsets_before_save() -> None:
    """Never write a damaged input's /Prev offset into a normalized save."""
    original = (ASSETS / "patent-us223898.pdf").read_bytes()
    damaged = _startxref_keyword_corrupted("patent-us223898.pdf", original)
    with pytest.warns(pylopdf.PylopdfWarning, match=REBUILT_WARNING):
        doc = pylopdf.open(stream=damaged)

    saved = doc.tobytes()
    assert b"/Prev" not in saved[saved.rindex(b"trailer") :]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        reopened = pylopdf.open(stream=saved)
    assert not caught
    assert reopened.is_repaired is False
    assert reopened.get_text() == pylopdf.open(stream=original).get_text()


def test_rebuilt_xref_prefers_later_revision_objects() -> None:
    """Keep the newest copy of an object instead of rolling back a revision."""

    def content(text: str) -> str:
        operators = f"BT /F1 12 Tf 10 50 Td ({text}) Tj ET"
        return f"<< /Length {len(operators)} >>\nstream\n{operators}\nendstream"

    base = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] "
                "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
            ),
            4: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            5: content("Earlier revision"),
        }
    )
    update = (
        f"\n5 0 obj\n{content('Later revision')}\nendobj\n"
        "6 0 obj\n<< /Type /XRef /Size 7 /Root 1 0 R /Length 0 >>\nstream\n\nendstream\nendobj\n"
        "startxref\n0\n%%EOF\n"
    ).encode()

    with pytest.warns(pylopdf.PylopdfWarning, match=REBUILT_WARNING):
        doc = pylopdf.open(stream=base + update)
    text = doc.get_page_text(0)
    assert "Later revision" in text
    assert "Earlier revision" not in text


def test_recovered_documents_keep_document_limits() -> None:
    """Rebuilt cross-reference data still passes every configured load budget."""
    damaged = _startxref_keyword_corrupted("patent-us223898.pdf", (ASSETS / "patent-us223898.pdf").read_bytes())
    for limits, code in (
        (pylopdf.DocumentLimits(max_pages=1), "page_count"),
        (pylopdf.DocumentLimits(max_objects=10), "object_count"),
        (pylopdf.DocumentLimits(max_decompressed_size=1024), "decompressed_size"),
        (pylopdf.DocumentLimits(max_page_content_size=64), "page_content_size"),
        (pylopdf.DocumentLimits(max_total_decompressed_size=4096), "total_decompressed_size"),
    ):
        with pytest.raises(pylopdf.LimitError) as caught:
            pylopdf.open(stream=damaged, limits=limits)
        assert caught.value.code == code

    bomb = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R >>",
            4: (
                f"<< /Length {len(zlib.compress(b' ' * 200_000))} /Filter /FlateDecode >>\nstream\n".encode()
                + zlib.compress(b" " * 200_000)
                + b"\nendstream"
            ),
        }
    )
    with pytest.raises(pylopdf.LimitError) as caught:
        pylopdf.open(stream=_with_startxref(bomb, 0), max_decompressed_size=100)
    assert caught.value.code == "decompressed_size"


def test_unterminated_stream_keywords_cannot_make_recovery_quadratic() -> None:
    """Refuse input whose lopdf reconstruction scan would exceed a linear budget.

    lopdf rescans the rest of the input after every ``stream`` keyword that has
    no later ``endstream``; this 1 MiB input used to take over a minute.
    """
    hostile = b"%PDF-1.4\n" + b"stream\n" * 150_000
    start = time.perf_counter()
    for load in (lambda: pylopdf.open(stream=hostile), lambda: pylopdf.peek_metadata(stream=hostile)):
        with pytest.raises(pylopdf.LimitError) as caught:
            load()
        assert caught.value.code == "recovery_scan_size"
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"recovery-scan refusal took {elapsed:.2f}s"


def test_repeated_xref_keywords_fail_without_quadratic_scanning() -> None:
    """Fail fast where the removed classic-startxref repair rescanned per keyword."""
    hostile = b"%PDF-1.4\n" + b"xref" * 262_144 + b"\ntrailer\n<< >>\nstartxref\n0\n%%EOF\n"
    start = time.perf_counter()
    with pytest.raises(pylopdf.PdfError):
        pylopdf.open(stream=hostile)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"malformed xref refusal took {elapsed:.2f}s"


def test_recovery_reports_stay_with_their_own_load() -> None:
    """Attribute lopdf's per-thread recovery records to the matching document."""
    clean = (ASSETS / "pdf20-simple.pdf").read_bytes()
    damaged = _startxref_keyword_corrupted("pdf20-simple.pdf", clean)
    inputs = [damaged if index % 2 else clean for index in range(64)]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pylopdf.PylopdfWarning)
        with ThreadPoolExecutor(max_workers=8) as executor:
            repaired = list(executor.map(lambda data: pylopdf.open(stream=data).is_repaired, inputs))
    assert repaired == [index % 2 == 1 for index in range(64)]


def test_truncated_classic_xref_is_rejected() -> None:
    """Do not treat a stream cut inside the final xref table as a document."""
    raw = (ASSETS / "pdf20-simple.pdf").read_bytes()
    startxref = raw.rfind(b"startxref")
    xref = raw.rfind(b"xref\r\n", 0, startxref)
    entry = raw.find(b"0000000096 00000 n", xref, startxref)
    assert xref >= 0
    assert entry >= 0

    with pytest.raises(pylopdf.PdfError):
        pylopdf.open(stream=raw[: entry + 8])


def test_max_decompressed_size_guards_against_bombs() -> None:
    """A tiny decompression limit rejects PDFs containing object streams."""
    path = ASSETS / "f1040.pdf"
    with pytest.raises(pylopdf.PdfError, match="limit"):
        pylopdf.open(path, max_decompressed_size=100)
    assert pylopdf.open(path, max_decompressed_size=50_000_000).page_count == 2


def test_recovered_pdf_avoids_slow_initial_reserialization() -> None:
    """Keep a damaged but readable PDF fast to interpret and to save.

    This five-byte f1040 mutation is a minimized Atheris slow unit. It damages
    an object stream: lopdf loses 69 listed structure elements and decodes
    other members under unlisted numbers up to 521,291,308, then raises the
    next object number over them. Its cross-reference writer runs in time
    linear in that number, so serializing the graph took about 9 seconds.
    References to the lost elements now make hayro read the serialized graph,
    and loading first drops the unreachable members and their numbers.
    """
    data = bytearray((ASSETS / "f1040.pdf").read_bytes())
    data[29_909] = 244
    data[186_564:186_568] = bytes(4)

    start = time.perf_counter()
    document = pylopdf.open(
        stream=bytes(data),
        max_decompressed_size=16 * 1024 * 1024,
    )
    text = document.get_page_text(0)
    elapsed = time.perf_counter() - start

    assert "U.S. Individual Income Tax Return" in text
    assert elapsed < 5.0, f"initial extraction took {elapsed:.2f}s"

    start = time.perf_counter()
    saved = document.tobytes()
    elapsed = time.perf_counter() - start

    assert elapsed < 5.0, f"saving took {elapsed:.2f}s"
    assert b"/Size 2455" in saved


@pytest.mark.parametrize("filter_name", ["FlateDecode", "Fl"])
def test_max_decompressed_size_guards_page_content_streams(filter_name: str) -> None:
    """Load-time limits cover page Contents that hayro decodes lazily."""
    expanded = b" " * 200_000
    compressed = zlib.compress(expanded)
    pdf = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R >>",
            4: (
                f"<< /Length {len(compressed)} /Filter /{filter_name} >>\nstream\n".encode()
                + compressed
                + b"\nendstream"
            ),
        }
    )
    with pytest.raises(pylopdf.PdfError, match="100-byte limit"):
        pylopdf.open(stream=pdf, max_decompressed_size=100)
    doc = pylopdf.open(stream=pdf, max_decompressed_size=len(expanded))
    assert doc.get_page_text(0) == ""


@WITH_TEXT
def test_extract_text_page0(case: Case) -> None:
    assert case.snippet is not None
    doc = pylopdf.open(ASSETS / case.name)
    assert case.snippet in doc.get_page_text(0)


def test_narrow_patent_columns_keep_column_major_reading_order() -> None:
    """Keep the two body columns separate despite their sub-em gutter."""
    doc = pylopdf.open(ASSETS / "patent-us223898.pdf")
    text = doc.get_page_text(1)

    assert text.index("Beit known") < text.index("sistance shall")
    assert text.index("sistance shall") < text.index("dimensions and good conductors")
    assert text.index("dimensions and good conductors") < text.index("globe cannot")

    drifting = doc.get_page_text(2)
    assert drifting.index("material be molded") < drifting.index("whole, with a leading-tube")
    assert drifting.index("whole, with a leading-tube") < drifting.index("vacuum has been reached")
    assert drifting.index("vacuum has been reached") < drifting.index("I clain as my invention")


def test_nics_table_rows_keep_row_major_reading_order() -> None:
    """Keep the statistics table's state labels beside their own numbers."""
    doc = pylopdf.open(ASSETS / "nics-background-checks-2015-11.pdf")
    text = doc.get_page_text(0)

    assert text.index("Alabama") < text.index("18,870") < text.index("Alaska")
    assert text.index("Alaska") < text.index("Arizona") < text.index("Arkansas")


@ALL
def test_extract_drawings_page0(case: Case) -> None:
    """Interpret real vector paths without returning malformed commands."""
    drawings = pylopdf.open(ASSETS / case.name)[0].get_drawings()
    assert all(drawing["type"] in {"f", "s", "fs"} for drawing in drawings)
    assert all(item[0] in {"l", "c"} for drawing in drawings for item in drawing["items"])


def test_pdf20_comment_streams_extract() -> None:
    """Protect extraction from comment-plus-indentation regression lopdf#535.

    v0.7 fixed pylopdf by moving extraction to hayro. lopdf ``extract_text``
    remains affected, but pylopdf no longer uses it.
    """
    doc = pylopdf.open(ASSETS / "pdf20-simple.pdf")
    assert "Hello World" in doc.get_page_text(0)


def test_pdfium_type3_svg_retains_stencil_glyphs() -> None:
    """Exercise the independent Type 3 glyph program through the SVG device."""
    svg = pylopdf.open(ASSETS / "pdfium-type3.pdf").render_page_svg(0)

    assert '<defs id="type3-glyph">' in svg
    assert svg.count("<image ") == 3


def test_pdfium_type3_stencil_glyphs_render_to_raster() -> None:
    """Expose the current platform-dependent Type 3 raster behavior."""
    page = pylopdf.open(ASSETS / "pdfium-type3.pdf")[0]
    pixmap = page.get_pixmap(background=(255, 255, 255))
    samples = pixmap.samples
    has_ink = any(samples[offset : offset + 3] != b"\xff\xff\xff" for offset in range(0, len(samples), 4))

    if not has_ink:
        pytest.xfail("hayro 0.7.1 omits these Type 3 stencil glyphs on this renderer target")


def test_pdfium_type3_text_extraction_recovers_stencil_text() -> None:
    """Pin the Type 3 no-ToUnicode extraction gap (LaurenzV/hayro#1331).

    The fixture draws ``(A)Tj`` three times from a Type 3 font whose
    ``/Differences [65 /C1]`` name is not AGL-mappable and that has no
    ToUnicode CMap, so hayro cannot determine any Unicode value. MuPDF
    recovers "A A A" through a raw character-code fallback.
    """
    page = pylopdf.open(ASSETS / "pdfium-type3.pdf")[0]
    words = [word[4] for word in page.get_text("words")]

    if not words:
        pytest.xfail("hayro 0.7 maps Type 3 glyphs to Unicode only via a ToUnicode CMap (LaurenzV/hayro#1331)")
    assert words == ["A", "A", "A"]


def test_pdfium_type3_skipped_glyphs_emit_one_warning() -> None:
    """Losing no-Unicode glyphs must be visible as one PylopdfWarning."""
    page = pylopdf.open(ASSETS / "pdfium-type3.pdf")[0]

    with pytest.warns(pylopdf.PylopdfWarning, match="no Unicode mapping") as caught:
        page.get_text("text")

    messages = [str(warning.message) for warning in caught if "no Unicode mapping" in str(warning.message)]
    assert len(messages) == 1


def test_pdfium_jpx_inside_lzw_decodes_as_red_image() -> None:
    """Decode an ASCIIHex/LZW/JPX image chain through both image APIs."""
    page = pylopdf.open(ASSETS / "pdfium-jpx-lzw.pdf")[0]
    images = page.get_images()

    assert [(image["width"], image["height"], image["ext"]) for image in images] == [(612, 792, "png")]
    assert images[0]["image"].startswith(b"\x89PNG\r\n\x1a\n")
    assert _rgba_pixel(page, 300, 300) == (255, 0, 0, 255)


def test_pdfium_soft_mask_and_blend_render_expected_region() -> None:
    """Apply a form soft mask and Screen blend without losing its opacity."""
    page = pylopdf.open(ASSETS / "pdfium-smask-blend.pdf")[0]

    assert _rgba_pixel(page, 75, 150) == (102, 102, 102, 255)
    assert _rgba_pixel(page, 25, 150) == (255, 255, 255, 255)
    assert _rgba_pixel(page, 75, 50) == (255, 255, 255, 255)


def test_pdfium_soft_mask_forms_survive_edits_and_saves() -> None:
    """Keep the three forms that spell `/Type XObject` without its slash.

    lopdf skipped them, so the unedited page showed the soft mask while any
    edit or save dropped it. Loading now recovers them, reading the bare
    keyword as null as hayro does.
    """
    document = pylopdf.open(ASSETS / "pdfium-smask-blend.pdf")
    document.set_metadata({"title": "Edited"})
    saved = document.tobytes()

    assert len(re.findall(rb"/Type null\s*/Subtype\s*/Form", saved)) == 3
    for page in (document[0], pylopdf.open(stream=saved)[0]):
        assert _rgba_pixel(page, 75, 150) == (102, 102, 102, 255)
        assert _rgba_pixel(page, 25, 150) == (255, 255, 255, 255)
        assert _rgba_pixel(page, 75, 50) == (255, 255, 255, 255)


def test_pdfium_existing_annotations_and_external_link_are_read() -> None:
    """Read and render independent annotations without editing their source."""
    document = pylopdf.open(ASSETS / "pdfium-links-highlights-annots.pdf")
    page = document[0]
    annotations = page.annots()

    assert [annotation["type"] for annotation in annotations] == [
        "Widget",
        "Widget",
        "Widget",
        "Widget",
        "Link",
        "Highlight",
    ]
    assert annotations[4]["uri"] == "https://www.google.com/"
    assert page.get_links() == [
        {
            "kind": 2,
            "from": pylopdf.Rect(69.0, 139.0, 542.0, 159.0),
            "uri": "https://www.google.com/",
        }
    ]
    pixmap = page.get_pixmap(background=(255, 255, 255))
    samples = pixmap.samples
    yellow_offset = (252 * pixmap.width + 320) * 4
    red, green, blue = samples[yellow_offset : yellow_offset + 3]
    assert red > 245
    assert 215 < green < 240
    assert blue < 16
    assert any(samples[(y * pixmap.width + x) * 4] < 128 for y in range(250, 263) for x in range(293, 350))
    assert b"/AP" not in document.tobytes()


def test_f1040_metadata_title() -> None:
    doc = pylopdf.open(ASSETS / "f1040.pdf")
    assert doc.metadata["title"] == "2025 Form 1040"


def test_f1040_bordered_table() -> None:
    """Extract a real stroked dependency grid without rasterization."""
    tables = pylopdf.open(ASSETS / "f1040.pdf")[0].find_tables()
    assert len(tables) >= 1
    table = tables[0]
    assert (table.row_count, table.col_count) == (2, 7)
    text = "\n".join(cell for row in table.extract() for cell in row if cell is not None)
    assert "Full-time\nstudent" in text
    assert "Child tax\ncredit" in text


def test_f1040_drawing_extraction_retains_form_paths() -> None:
    """Expose the dense vector structure underlying a real tax form."""
    drawings = pylopdf.open(ASSETS / "f1040.pdf")[0].get_drawings()
    assert len(drawings) >= 400
    assert any(drawing["type"] == "fs" for drawing in drawings)


def test_f1040_markdown_integrates_bordered_table_once() -> None:
    """Place a real stroked dependency grid into page Markdown."""
    page = pylopdf.open(ASSETS / "f1040.pdf")[0]
    markdown = page.to_markdown()

    assert "| --- | --- | --- | --- | --- | --- | --- |" in markdown
    assert "Full-time<br>student" in markdown
    assert "Child tax<br>credit" in markdown
    assert "(6) Check if" in markdown
    assert "(7) Credits" in markdown
    assert markdown.count("Full-time") == page.get_text().count("Full-time")
    assert markdown.count("Child tax") == page.get_text().count("Child tax")


def test_f1040_borderless_text_table() -> None:
    """Extract aligned form rows that have no complete surrounding grid."""
    tables = pylopdf.open(ASSETS / "f1040.pdf")[0].find_tables(strategy="text")
    extracted = [table.extract() for table in tables]

    assert any(any(cell == "Filing Status" for row in table for cell in row) for table in extracted)


def test_nics_sparse_grid_infers_individual_rows() -> None:
    """Split dense records inside coarse vector row spans."""
    table = pylopdf.open(ASSETS / "nics-background-checks-2015-11.pdf")[0].find_tables()[0]
    rows = table.extract()

    assert (table.row_count, table.col_count) == (58, 25)
    assert table.confidence == 0.95
    assert rows[2] == [
        "Alabama",
        "18,870",
        "23,022",
        "22,650",
        "859",
        "1,178",
        "0",
        "14",
        "15",
        "0",
        "2,179",
        "2,307",
        "11",
        "0",
        "0",
        "0",
        "",
        "",
        "13",
        "14",
        "0",
        "3",
        "2",
        "0",
        "71,137",
    ]
    assert rows[56][0] == "Wyoming"
    assert rows[56][-1] == "5,017"
    assert rows[57][0] == "Totals"
    assert rows[57][-1] == "2,236,457"


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_nics_markdown_preserves_sparse_grid_reading_order(rotation: int) -> None:
    """Expand merged headers without duplicating adjacent data after rotation."""
    document = pylopdf.open(ASSETS / "nics-background-checks-2015-11.pdf")
    document[0].set_rotation(rotation)
    table = document[0].find_tables()[0]

    expected_shape = (58, 25) if rotation in (0, 180) else (25, 58)
    assert (table.row_count, table.col_count) == expected_shape

    markdown = document.to_markdown()
    assert "| Alabama | 18,870 | 23,022 |" in markdown
    assert "| Wyoming | 383 | 1,745 |" in markdown
    assert markdown.count("Alabama") == 1
    assert markdown.count("Wyoming") == 1


def test_rotated_senate_header_and_borderless_body() -> None:
    """Keep a merged vector header separate from aligned expenditure rows."""
    page = pylopdf.open(ASSETS / "senate-expenditures.pdf")[0]

    header = page.find_tables()[0]
    assert (header.row_count, header.col_count) == (2, 7)
    assert header.confidence == 1.0

    body = page.find_tables(strategy="text")[0]
    assert (body.row_count, body.col_count) == (10, 3)
    assert body.extract()[0] == ["BAIN, J MATTHEW", "DISTRICT DIRECTOR", "37,499.96"]


def test_manuscript_scan_has_no_text_layer() -> None:
    """A pure scan without a text layer correctly extracts as empty."""
    doc = pylopdf.open(ASSETS / "wdl6812-manuscript.pdf")
    assert doc.get_page_text(0).strip() == ""


def test_manuscript_compression_preserves_mismatched_masks() -> None:
    """Skip real JPEG/mask pairs rather than changing their compositing."""
    doc = pylopdf.open(ASSETS / "wdl6812-manuscript.pdf")
    before = doc[0].get_pixmap().samples

    result = doc.compress_images(dpi=100, quality=50)

    assert result["considered"] >= 2
    assert result["rewritten"] == 0
    assert doc[0].get_pixmap().samples == before


@ALL
def test_select_first_page_and_roundtrip(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    doc.select([0])
    assert doc.page_count == 1
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.page_count == 1


@ALL
def test_insert_subset_with_position_roundtrip(case: Case) -> None:
    """Importing page 0 at the front survives pruning without content loss."""
    doc = pylopdf.open(ASSETS / case.name)
    src = pylopdf.open(ASSETS / case.name)
    doc.insert_pdf(src, from_page=0, to_page=0, start_at=0)
    assert doc.page_count == case.pages + 1
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.page_count == case.pages + 1
    # Pruning preserves fonts/images referenced by the inserted page.
    assert reopened.render_page(0).startswith(b"\x89PNG")


@ALL
def test_merge_self_and_roundtrip(case: Case) -> None:
    raw = (ASSETS / case.name).read_bytes()
    doc = pylopdf.open(stream=raw)
    doc.insert_pdf(pylopdf.open(stream=raw))
    assert doc.page_count == case.pages * 2
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.page_count == case.pages * 2


@ALL
def test_merge_into_empty_and_roundtrip(case: Case) -> None:
    """Importing a real PDF into an empty document avoids Catalog/Pages ID collisions."""
    source = pylopdf.open(ASSETS / case.name)
    doc = pylopdf.Document()
    doc.insert_pdf(source)
    assert doc.page_count == case.pages
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.page_count == case.pages


@ALL
def test_delete_page_and_roundtrip(case: Case) -> None:
    if case.pages < 2:
        pytest.skip("deleting every page from a one-page document is out of scope")
    doc = pylopdf.open(ASSETS / case.name)
    doc.delete_page(0)
    assert doc.page_count == case.pages - 1
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.page_count == case.pages - 1


@ALL
def test_save_optimized_roundtrip(case: Case) -> None:
    """Garbage, deflate, and object-stream saves preserve readable content."""
    doc = pylopdf.open(ASSETS / case.name)
    data = doc.tobytes(garbage=True, deflate=True, object_streams=True)
    reopened = pylopdf.open(stream=data)
    assert reopened.page_count == case.pages


def test_object_streams_reduce_size() -> None:
    """Object-stream saving reduces a medium-size document."""
    doc = pylopdf.open(ASSETS / "bill-hr815.pdf")
    plain = doc.tobytes()
    optimized = doc.tobytes(garbage=True, deflate=True, object_streams=True)
    assert len(optimized) < len(plain)


@ALL
def test_set_metadata_roundtrip(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    doc.set_metadata({"title": "回帰テスト", "author": "pylopdf"})
    reopened = pylopdf.open(stream=doc.tobytes())
    assert reopened.metadata["title"] == "回帰テスト"
    assert reopened.metadata["author"] == "pylopdf"


@ALL
def test_render_page_png(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    png = doc.render_page(0, scale=1.0)
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 1000


@ALL
def test_render_page_svg(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    svg = doc.render_page_svg(0)
    assert svg.startswith("<svg")


@WITH_TEXT
def test_extract_text_survives_edit(case: Case) -> None:
    """Text extraction survives select with inherited attributes materialized."""
    assert case.snippet is not None
    doc = pylopdf.open(ASSETS / case.name)
    doc.select([0])
    reopened = pylopdf.open(stream=doc.tobytes())
    assert case.snippet in reopened.get_page_text(0)
