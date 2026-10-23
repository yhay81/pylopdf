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
from conftest import annotation_subtypes, build_pdf, build_raw_pdf, drawn_images, form_entries, with_xref_entry_offset

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


def _bytes_inserted_late(_name: str, data: bytes) -> bytes:
    """Insert an 18-byte comment line before the last quarter of the object headers."""
    start, _ = _startxref_value_span(data)
    headers = [match.start() for match in re.finditer(rb"(?m)^\d+ \d+ obj", data[:start])]
    position = headers[len(headers) * 3 // 4]
    return data[:position] + b"%" + b"x" * 16 + b"\n" + data[position:]


DAMAGE: dict[str, Callable[[str, bytes], bytes]] = {
    "truncate-95": _truncated(0.95),
    "truncate-60": _truncated(0.60),
    "junk-prefix-512": _junk_prefixed,
    "startxref-keyword": _startxref_keyword_corrupted,
    "xref-section-removed": _final_xref_section_removed,
    "length-off-37": _lengths_off_by_37,
    "bit-flips-12": _bits_flipped,
    "endobj-removed-2": _endobj_removed,
    "bytes-inserted-late": _bytes_inserted_late,
}

#: Pinned outcome for each damaged variant. ``intact`` opens without
#: cross-reference recovery, ``rebuilt`` reconstructs the table by scanning
#: object headers (``-without-snippet`` when the damage also hit that text),
#: ``unusable`` opens without a readable page tree (``rebuilt-unusable`` after
#: a reconstruction), and ``rejected`` raises PdfError. pylopdf 0.14 with lopdf
#: 0.44 rejected every ``rebuilt`` case here. Deleting two ``endobj`` keywords
#: or inserting bytes before the last objects shifts every later object, and
#: lopdf corrects the nearby ``startxref`` onto a table whose entries miss
#: those objects; loading rebuilds such a table, while the metadata probe keeps
#: the correction when the page tree loads (``rebuilt-after-offset``). lopdf
#: 0.46 also rebuilds damaged xref-stream files such as Form 1040, which lopdf
#: 0.45 rejected because it rebuilt only from a classic trailer.
DAMAGE_OUTCOMES = {
    "pdf20-simple.pdf": {
        "truncate-95": "rejected",
        "truncate-60": "rejected",
        "junk-prefix-512": "intact",
        "startxref-keyword": "rebuilt",
        "xref-section-removed": "rebuilt",
        "length-off-37": "intact",
        "bit-flips-12": "unusable",
        "endobj-removed-2": "rebuilt",
        "bytes-inserted-late": "rebuilt-after-offset",
    },
    "nics-background-checks-2015-11.pdf": {
        "truncate-95": "rejected",
        "truncate-60": "rejected",
        "junk-prefix-512": "intact",
        "startxref-keyword": "rebuilt",
        "xref-section-removed": "rebuilt",
        "length-off-37": "intact",
        "bit-flips-12": "rebuilt-without-snippet",
        "endobj-removed-2": "rebuilt",
        "bytes-inserted-late": "rebuilt-after-offset",
    },
    "patent-us223898.pdf": {
        "truncate-95": "rebuilt",
        "truncate-60": "rejected",
        "junk-prefix-512": "intact",
        "startxref-keyword": "rebuilt",
        "xref-section-removed": "rebuilt",
        "length-off-37": "intact",
        "bit-flips-12": "intact",
        "endobj-removed-2": "rebuilt",
        "bytes-inserted-late": "rebuilt",
    },
    "f1040.pdf": {
        "truncate-95": "rebuilt",
        "truncate-60": "rebuilt-unusable",
        "junk-prefix-512": "intact",
        "startxref-keyword": "rebuilt",
        "xref-section-removed": "rebuilt",
        "length-off-37": "rebuilt",
        "bit-flips-12": "intact",
        "endobj-removed-2": "rebuilt",
        "bytes-inserted-late": "rebuilt-after-offset",
    },
}


def _rgba_pixel(page: pylopdf.Page, x: int, y: int) -> tuple[int, int, int, int]:
    """Return one rendered pixel over a white background."""
    pixmap = page.pixmap(background=(255, 255, 255), alpha=True)
    samples = bytes(pixmap)
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
    assert len(pylopdf.open(path).pages) == case.pages
    assert len(pylopdf.open(path.read_bytes()).pages) == case.pages


@ALL
def test_metadata_format(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    assert f"PDF {doc.version}" == case.version


@ALL
def test_batched_get_text_matches_per_page(case: Case) -> None:
    """One shared-interpreter-cache batch must equal fresh per-page extraction."""
    batched = pylopdf.open(ASSETS / case.name).text()
    per_page = "".join(pylopdf.open(ASSETS / case.name).pages[pno].text() for pno in range(case.pages))
    assert batched == per_page


@ALL
def test_probe_matches_full_load(case: Case) -> None:
    """Probe returns the same page count as a full load."""
    meta = pylopdf.probe(ASSETS / case.name)
    assert meta.page_count == case.pages
    assert meta.needs_password is False
    assert meta.is_repaired is False


def _has_ink(page: pylopdf.Page) -> bool:
    """Return whether a low-resolution render of the page is not blank."""
    samples = bytes(page.pixmap(matrix=pylopdf.Matrix(0.5, 0.5), background=(255, 255, 255), alpha=True))
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
            pylopdf.open(damaged)
        with pytest.raises(pylopdf.PdfError):
            pylopdf.probe(damaged)
        return

    expected_warning = {
        "rebuilt": REBUILT_WARNING,
        "rebuilt-without-snippet": REBUILT_WARNING,
        "rebuilt-after-offset": REBUILT_WARNING,
        "rebuilt-unusable": REBUILT_WARNING,
    }.get(outcome)
    # The probe reads too few objects to see that a corrected offset found a
    # section whose later entries miss their objects, so it keeps the correction.
    probe_warning = OFFSET_WARNING if outcome == "rebuilt-after-offset" else expected_warning
    for load, expected in ((pylopdf.open, expected_warning), (pylopdf.probe, probe_warning)):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            loaded = load(damaged)
        messages = [str(warning.message) for warning in caught if issubclass(warning.category, pylopdf.PylopdfWarning)]
        assert loaded.is_repaired is (expected is not None)
        if expected is None:
            assert not [message for message in messages if "cross-reference" in message]
        else:
            assert [message for message in messages if expected in message] == messages
            assert len(messages) == 1
    doc = pylopdf.open(damaged)
    probe = pylopdf.probe(damaged)

    if outcome in {"unusable", "rebuilt-unusable"}:
        with pytest.raises(pylopdf.PdfError):
            _ = len(doc.pages)
        return

    assert len(doc.pages) == probe.page_count == case.pages
    if case.snippet is not None and outcome != "rebuilt-without-snippet":
        assert case.snippet in doc.pages[0].text()
    assert _has_ink(doc.pages[0])
    if expected_warning == REBUILT_WARNING:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            reopened = pylopdf.open(doc.to_bytes())
        assert not caught
        assert reopened.is_repaired is False
        assert len(reopened.pages) == case.pages
        assert reopened.pages[0].text() == doc.pages[0].text()


@ALL
@pytest.mark.filterwarnings("ignore:text extraction skipped glyphs")
def test_incorrect_startxref_value_recovers_exactly(case: Case) -> None:
    """Recover nearby and distant startxref damage without changing text.

    lopdf 0.46 moves a nearby offset onto a cross-reference stream as it does
    onto a classic table, and rebuilds files that only have cross-reference
    streams (J-F-Liu/lopdf#611, J-F-Liu/lopdf#613), which 0.45 rejected.
    """
    original = (ASSETS / case.name).read_bytes()
    start, end = _startxref_value_span(original)
    expected_text = pylopdf.open(original).text()

    for value, warning in ((int(original[start:end]) + 12, OFFSET_WARNING), (0, REBUILT_WARNING)):
        damaged = _with_startxref(original, value)
        with pytest.warns(pylopdf.PylopdfWarning, match=warning):
            doc = pylopdf.open(damaged)
        assert doc.is_repaired is True
        assert doc.text() == expected_text
        with pytest.warns(pylopdf.PylopdfWarning, match=warning):
            assert pylopdf.probe(damaged).is_repaired is True


def test_incorrect_startxref_path_probe_and_prefixed_stream(tmp_path: Path) -> None:
    """Expose recovery in path probes and keep offsets relative to the header."""
    damaged = _with_startxref((ASSETS / "pdf20-simple.pdf").read_bytes(), 0)
    path = tmp_path / "incorrect-startxref.pdf"
    path.write_bytes(damaged)

    with pytest.warns(pylopdf.PylopdfWarning, match=REBUILT_WARNING):
        metadata = pylopdf.probe(path)
    assert metadata.page_count == 1
    assert metadata.is_repaired is True

    with pytest.warns(pylopdf.PylopdfWarning, match=REBUILT_WARNING):
        from_path = pylopdf.open(path)
    assert from_path.is_repaired is True

    with pytest.warns(pylopdf.PylopdfWarning, match=REBUILT_WARNING):
        prefixed = pylopdf.open(b"transport prefix\n" + damaged)
    assert prefixed.is_repaired is True
    assert "Hello World" in prefixed.pages[0].text()

    clean = pylopdf.open(b"transport prefix\n" + (ASSETS / "pdf20-simple.pdf").read_bytes())
    assert clean.is_repaired is False


def test_rebuilt_trailer_drops_stale_chain_offsets_before_save() -> None:
    """Never write a damaged input's /Prev offset into a normalized save."""
    original = (ASSETS / "patent-us223898.pdf").read_bytes()
    damaged = _startxref_keyword_corrupted("patent-us223898.pdf", original)
    with pytest.warns(pylopdf.PylopdfWarning, match=REBUILT_WARNING):
        doc = pylopdf.open(damaged)

    saved = doc.to_bytes()
    assert b"/Prev" not in saved[saved.rindex(b"trailer") :]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        reopened = pylopdf.open(saved)
    assert not caught
    assert reopened.is_repaired is False
    assert reopened.text() == pylopdf.open(original).text()


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
        doc = pylopdf.open(base + update)
    text = doc.pages[0].text()
    assert "Later revision" in text
    assert "Earlier revision" not in text


@pytest.mark.parametrize(
    ("number", "shift"),
    [
        pytest.param(1, None, id="catalog-past-the-end"),
        pytest.param(1, 2, id="catalog-inside-its-header"),
        pytest.param(2, None, id="root-node-past-the-end"),
        pytest.param(2, 3, id="root-node-inside-its-header"),
    ],
)
def test_unreachable_page_tree_rebuilds_the_xref_table(number: int, shift: int | None) -> None:
    """Rebuild the table when its entries miss the catalog or the root page-tree node.

    lopdf trusts any cross-reference data that it resolves, so the document opened with every page
    operation raising, while hayro, MuPDF, and pdf.js rebuild the table by scanning object headers
    (pdf.js `operator_list_cycle.pdf` locates its root node past the end of the file, and
    `bug1130815.pdf` lost bytes before a table that lopdf reached by correcting `startxref`).
    """
    original = build_pdf(["Rebuilt page"])
    header = original.index(f"\n{number} 0 obj".encode()) + 1
    damaged = with_xref_entry_offset(original, number, 4_294_967_295 if shift is None else header + shift)

    with pytest.warns(pylopdf.PylopdfWarning, match=REBUILT_WARNING):
        doc = pylopdf.open(damaged)
    with pytest.warns(pylopdf.PylopdfWarning, match=REBUILT_WARNING):
        probe = pylopdf.probe(damaged)
    assert doc.is_repaired is True
    assert len(doc.pages) == probe.page_count == 1
    assert probe.is_repaired is True
    assert doc.pages[0].text().split() == ["Rebuilt", "page"]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        reopened = pylopdf.open(doc.to_bytes())
    assert reopened.is_repaired is False
    assert reopened.pages[0].text() == doc.pages[0].text()


@pytest.mark.parametrize("hidden", [1, 2], ids=["catalog", "root-node"])
def test_page_tree_that_no_rebuild_reaches_keeps_the_resolved_table(hidden: int) -> None:
    """Keep the resolved table, without a rebuilt warning, when a rebuilt one reaches no page tree.

    The entries miss the catalog and the root node. No header names the catalog, so no table can be
    rebuilt, or none names the root node, which the rebuilt table then lacks.
    """
    damaged = build_pdf(["Lost page"]).replace(f"\n{hidden} 0 obj".encode(), f"\n{hidden} 0 xbj".encode())
    for number in (1, 2):
        damaged = with_xref_entry_offset(damaged, number, 4_294_967_295)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        doc = pylopdf.open(damaged)
        probe = pylopdf.probe(damaged)
    assert doc.is_repaired is False
    assert probe.is_repaired is False
    assert probe.page_count == 0
    with pytest.raises(pylopdf.PdfError, match="object ID 1 0 not found"):
        _ = len(doc.pages)


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
            pylopdf.open(damaged, limits=limits)
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
        pylopdf.open(_with_startxref(bomb, 0), limits=pylopdf.DocumentLimits(max_decompressed_size=100))
    assert caught.value.code == "decompressed_size"


def test_unterminated_stream_keywords_keep_recovery_linear() -> None:
    """Rebuild input full of unterminated ``stream`` keywords in linear time.

    lopdf 0.45 rescanned the rest of the input after every ``stream`` keyword
    that had no later ``endstream``, so a 1 MiB input took over a minute and
    pylopdf refused such input before loading. lopdf 0.46 skips them in one
    pass (J-F-Liu/lopdf#610): these 8 MiB inputs take about 0.1 s.
    """
    trailer = b"trailer\n<</Root 1 0 R>>\nstartxref\n0\n%%EOF\n"
    rebuilt = b"%PDF-1.4\n" + b"1 0 obj\n<</Length 5>>stream\n" * 300_000 + trailer
    rejected = b"%PDF-1.4\n" + b"stream\n" * 1_200_000 + trailer
    start = time.perf_counter()
    for load in (pylopdf.open, pylopdf.probe):
        with pytest.warns(pylopdf.PylopdfWarning, match=REBUILT_WARNING):
            assert load(rebuilt).is_repaired is True
        with pytest.raises(pylopdf.MalformedPdfError):
            load(rejected)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"recovery of unterminated streams took {elapsed:.2f}s"


def test_repeated_xref_keywords_fail_without_quadratic_scanning() -> None:
    """Fail fast where the removed classic-startxref repair rescanned per keyword."""
    hostile = b"%PDF-1.4\n" + b"xref" * 262_144 + b"\ntrailer\n<< >>\nstartxref\n0\n%%EOF\n"
    start = time.perf_counter()
    with pytest.raises(pylopdf.PdfError):
        pylopdf.open(hostile)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"malformed xref refusal took {elapsed:.2f}s"


def test_recovery_reports_stay_with_their_own_load() -> None:
    """Keep each concurrent load's recovery report with its own document."""
    clean = (ASSETS / "pdf20-simple.pdf").read_bytes()
    damaged = _startxref_keyword_corrupted("pdf20-simple.pdf", clean)
    inputs = [damaged if index % 2 else clean for index in range(64)]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pylopdf.PylopdfWarning)
        with ThreadPoolExecutor(max_workers=8) as executor:
            repaired = list(executor.map(lambda data: pylopdf.open(data).is_repaired, inputs))
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
        pylopdf.open(raw[: entry + 8])


def test_max_decompressed_size_guards_against_bombs() -> None:
    """A tiny decompression limit rejects PDFs containing object streams."""
    path = ASSETS / "f1040.pdf"
    with pytest.raises(pylopdf.PdfError, match="limit"):
        pylopdf.open(path, limits=pylopdf.DocumentLimits(max_decompressed_size=100))
    assert len(pylopdf.open(path, limits=pylopdf.DocumentLimits(max_decompressed_size=50_000_000)).pages) == 2


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
    document = pylopdf.open(bytes(data), limits=pylopdf.DocumentLimits(max_decompressed_size=16 * 1024 * 1024))
    text = document.pages[0].text()
    elapsed = time.perf_counter() - start

    assert "U.S. Individual Income Tax Return" in text
    assert elapsed < 5.0, f"initial extraction took {elapsed:.2f}s"

    start = time.perf_counter()
    saved = document.to_bytes()
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
        pylopdf.open(pdf, limits=pylopdf.DocumentLimits(max_decompressed_size=100))
    doc = pylopdf.open(pdf, limits=pylopdf.DocumentLimits(max_decompressed_size=len(expanded)))
    assert doc.pages[0].text() == ""


@WITH_TEXT
def test_extract_text_page0(case: Case) -> None:
    assert case.snippet is not None
    doc = pylopdf.open(ASSETS / case.name)
    assert case.snippet in doc.pages[0].text()


def test_narrow_patent_columns_keep_column_major_reading_order() -> None:
    """Keep the two body columns separate despite their sub-em gutter."""
    doc = pylopdf.open(ASSETS / "patent-us223898.pdf")
    text = doc.pages[1].text()

    assert text.index("Beit known") < text.index("sistance shall")
    assert text.index("sistance shall") < text.index("dimensions and good conductors")
    assert text.index("dimensions and good conductors") < text.index("globe cannot")

    drifting = doc.pages[2].text()
    assert drifting.index("material be molded") < drifting.index("whole, with a leading-tube")
    assert drifting.index("whole, with a leading-tube") < drifting.index("vacuum has been reached")
    assert drifting.index("vacuum has been reached") < drifting.index("I clain as my invention")


def test_nics_table_rows_keep_row_major_reading_order() -> None:
    """Keep the statistics table's state labels beside their own numbers."""
    doc = pylopdf.open(ASSETS / "nics-background-checks-2015-11.pdf")
    text = doc.pages[0].text()

    assert text.index("Alabama") < text.index("18,870") < text.index("Alaska")
    assert text.index("Alaska") < text.index("Arizona") < text.index("Arkansas")


def test_markdown_keeps_word_gaps_between_font_spans() -> None:
    """Keep the extractor's inferred spaces where a font change ends a span."""
    doc = pylopdf.open(ASSETS / "usrguide.pdf")
    markdown = doc.markdown(pages=[1])

    assert "6 Expandable \\input equivalent" in markdown
    assert [word for word in markdown.split() if word.strip("#")] == doc.pages[1].text().split()


@ALL
def test_content_paths_page0(case: Case) -> None:
    """Interpret real vector paths into painted segments."""
    paths = pylopdf.open(ASSETS / case.name).pages[0].content().paths
    segments = (pylopdf.MoveTo, pylopdf.LineTo, pylopdf.CurveTo, pylopdf.ClosePath)
    assert all(path.fill is not None or path.stroke is not None for path in paths)
    assert all(isinstance(segment, segments) for path in paths for segment in path.segments)


def test_pdf20_comment_streams_extract() -> None:
    """Protect extraction from comment-plus-indentation regression lopdf#535.

    v0.7 fixed pylopdf by moving extraction to hayro. lopdf ``extract_text``
    remains affected, but pylopdf no longer uses it.
    """
    doc = pylopdf.open(ASSETS / "pdf20-simple.pdf")
    assert "Hello World" in doc.pages[0].text()


def test_pdfium_type3_svg_retains_stencil_glyphs() -> None:
    """Exercise the independent Type 3 glyph program through the SVG device."""
    svg = pylopdf.open(ASSETS / "pdfium-type3.pdf").pages[0].svg()

    assert '<defs id="type3-glyph">' in svg
    assert svg.count("<image ") == 3


def test_pdfium_type3_stencil_glyphs_render_to_raster() -> None:
    """Expose the current platform-dependent Type 3 raster behavior."""
    page = pylopdf.open(ASSETS / "pdfium-type3.pdf").pages[0]
    pixmap = page.pixmap(background=(255, 255, 255), alpha=True)
    samples = bytes(pixmap)
    has_ink = any(samples[offset : offset + 3] != b"\xff\xff\xff" for offset in range(0, len(samples), 4))

    if not has_ink:
        pytest.xfail("hayro omits these Type 3 stencil glyphs on this renderer target, as hayro 0.7.1 did on some")


def test_pdfium_type3_text_extraction_recovers_stencil_text() -> None:
    """Pin the Type 3 no-ToUnicode extraction gap (LaurenzV/hayro#1331).

    The fixture draws ``(A)Tj`` three times from a Type 3 font whose
    ``/Differences [65 /C1]`` name is not AGL-mappable and that has no
    ToUnicode CMap, so hayro cannot determine any Unicode value. MuPDF
    recovers "A A A" through a raw character-code fallback.
    """
    page = pylopdf.open(ASSETS / "pdfium-type3.pdf").pages[0]
    words = [word.text for word in page.words()]

    if not words:
        pytest.xfail("hayro 0.8.0 maps Type 3 glyphs to Unicode only via a ToUnicode CMap (LaurenzV/hayro#1331)")
    assert words == ["A", "A", "A"]


def test_pdfium_type3_skipped_glyphs_emit_one_warning() -> None:
    """Losing no-Unicode glyphs must be visible as one PylopdfWarning."""
    page = pylopdf.open(ASSETS / "pdfium-type3.pdf").pages[0]

    with pytest.warns(pylopdf.PylopdfWarning, match="no Unicode mapping") as caught:
        page.text()

    messages = [str(warning.message) for warning in caught if "no Unicode mapping" in str(warning.message)]
    assert len(messages) == 1


def test_pdfium_jpx_inside_lzw_decodes_as_red_image() -> None:
    """Decode an ASCIIHex/LZW/JPX image chain through both image APIs."""
    page = pylopdf.open(ASSETS / "pdfium-jpx-lzw.pdf").pages[0]
    images = drawn_images(page)

    assert [(image["width"], image["height"], image["ext"]) for image in images] == [(612, 792, "png")]
    assert images[0]["image"].startswith(b"\x89PNG\r\n\x1a\n")
    assert _rgba_pixel(page, 300, 300) == (255, 0, 0, 255)


def test_pdfium_soft_mask_and_blend_render_expected_region() -> None:
    """Apply a form soft mask and Screen blend without losing its opacity."""
    page = pylopdf.open(ASSETS / "pdfium-smask-blend.pdf").pages[0]

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
    document.metadata.update({"title": "Edited"})
    saved = document.to_bytes()

    assert len(re.findall(rb"/Type null\s*/Subtype\s*/Form", saved)) == 3
    for page in (document.pages[0], pylopdf.open(saved).pages[0]):
        assert _rgba_pixel(page, 75, 150) == (102, 102, 102, 255)
        assert _rgba_pixel(page, 25, 150) == (255, 255, 255, 255)
        assert _rgba_pixel(page, 75, 50) == (255, 255, 255, 255)


def test_pdfium_existing_annotations_and_external_link_are_read() -> None:
    """Read and render independent annotations without editing their source."""
    document = pylopdf.open(ASSETS / "pdfium-links-highlights-annots.pdf")
    page = document.pages[0]
    assert annotation_subtypes(page) == ["Widget", "Widget", "Widget", "Widget", "Link", "Highlight"]
    (highlight,) = page.annotations
    assert highlight.type is pylopdf.AnnotationType.HIGHLIGHT
    (link,) = page.links
    assert link.xref == 12
    assert (link.rect, link.target) == (pylopdf.Rect(69.0, 139.0, 542.0, 159.0), "https://www.google.com/")
    pixmap = page.pixmap(background=(255, 255, 255), alpha=True)
    samples = bytes(pixmap)
    yellow_offset = (252 * pixmap.width + 320) * 4
    red, green, blue = samples[yellow_offset : yellow_offset + 3]
    assert red > 245
    assert 215 < green < 240
    assert blue < 16
    assert any(samples[(y * pixmap.width + x) * 4] < 128 for y in range(250, 263) for x in range(293, 350))
    assert b"/AP" not in document.to_bytes()


def test_f1040_metadata_title() -> None:
    doc = pylopdf.open(ASSETS / "f1040.pdf")
    assert doc.metadata["title"] == "2025 Form 1040"


@pytest.mark.parametrize("edit", ["delete", "select"])
def test_f1040_page_deletion_drops_the_page_from_the_structure_tree(edit: str) -> None:
    """The structure tree of the tagged form names page 2 and its 71 widgets.

    `/Pg`, OBJR `/Obj`, `/ParentTree`, and `/IDTree` entries kept the deleted
    page and its widgets reachable through every garbage-collecting save.
    """
    doc = pylopdf.open(ASSETS / "f1040.pdf")
    first_page = doc.pages[0].text()
    if edit == "delete":
        del doc.pages[1:2]
    else:
        doc.pages.select([0])
    saved = doc.to_bytes()

    assert len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", saved)) == 1
    assert len(re.findall(rb"/Subtype\s*/Widget", saved)) == 128
    assert b"topmostSubform[0].Page2[0]" not in saved
    assert b"/StructTreeRoot" in saved
    reopened = pylopdf.open(saved)
    assert len(reopened.pages) == 1
    assert reopened.pages[0].text() == first_page
    assert len(form_entries(reopened)) == 128


def test_f1040_bordered_table() -> None:
    """Extract a real stroked dependency grid without rasterization."""
    tables = pylopdf.open(ASSETS / "f1040.pdf").pages[0].tables()
    assert len(tables) >= 1
    table = tables[0]
    assert (len(table.rows), len(table.rows[0])) == (2, 7)
    text = "\n".join(cell for row in table.rows for cell in row if cell is not None)
    assert "Full-time\nstudent" in text
    assert "Child tax\ncredit" in text


def test_f1040_content_retains_form_paths() -> None:
    """Expose the dense vector structure underlying a real tax form."""
    paths = pylopdf.open(ASSETS / "f1040.pdf").pages[0].content().paths
    assert len(paths) >= 400
    assert any(path.fill is not None for path in paths)
    assert any(path.stroke is not None for path in paths)


def test_f1040_markdown_integrates_bordered_table_once() -> None:
    """Place a real stroked dependency grid into page Markdown."""
    page = pylopdf.open(ASSETS / "f1040.pdf").pages[0]
    markdown = page.markdown()

    assert "| --- | --- | --- | --- | --- | --- | --- |" in markdown
    assert "Full-time<br>student" in markdown
    assert "Child tax<br>credit" in markdown
    assert "(6) Check if" in markdown
    assert "(7) Credits" in markdown
    assert markdown.count("Full-time") == page.text().count("Full-time")
    assert markdown.count("Child tax") == page.text().count("Child tax")


def test_f1040_borderless_text_table() -> None:
    """Extract aligned form rows that have no complete surrounding grid."""
    tables = pylopdf.open(ASSETS / "f1040.pdf").pages[0].tables(strategy="text")
    extracted = [table.rows for table in tables]

    assert any(any(cell == "Filing Status" for row in table for cell in row) for table in extracted)


def test_nics_sparse_grid_infers_individual_rows() -> None:
    """Split dense records inside coarse vector row spans."""
    table = pylopdf.open(ASSETS / "nics-background-checks-2015-11.pdf").pages[0].tables()[0]
    rows = table.rows

    assert (len(table.rows), len(table.rows[0])) == (58, 25)
    assert table.diagnostics.confidence == 0.95
    assert list(rows[2]) == [
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
    document.pages[0].rotation = rotation
    table = document.pages[0].tables()[0]

    expected_shape = (58, 25) if rotation in (0, 180) else (25, 58)
    assert (len(table.rows), len(table.rows[0])) == expected_shape

    markdown = document.markdown()
    assert "| Alabama | 18,870 | 23,022 |" in markdown
    assert "| Wyoming | 383 | 1,745 |" in markdown
    assert markdown.count("Alabama") == 1
    assert markdown.count("Wyoming") == 1


def test_rotated_senate_header_and_borderless_body() -> None:
    """Keep a merged vector header separate from aligned expenditure rows."""
    page = pylopdf.open(ASSETS / "senate-expenditures.pdf").pages[0]

    header = page.tables()[0]
    assert (len(header.rows), len(header.rows[0])) == (2, 7)
    assert header.diagnostics.confidence == 1.0

    body = page.tables(strategy="text")[0]
    assert (len(body.rows), len(body.rows[0])) == (10, 3)
    assert body.rows[0] == ("BAIN, J MATTHEW", "DISTRICT DIRECTOR", "37,499.96")


def test_manuscript_scan_has_no_text_layer() -> None:
    """A pure scan without a text layer correctly extracts as empty."""
    doc = pylopdf.open(ASSETS / "wdl6812-manuscript.pdf")
    assert doc.pages[0].text().strip() == ""


def test_manuscript_compression_preserves_mismatched_masks() -> None:
    """Skip real JPEG/mask pairs rather than changing their compositing."""
    doc = pylopdf.open(ASSETS / "wdl6812-manuscript.pdf")
    before = bytes(doc.pages[0].pixmap(alpha=True))

    result = doc.rewrite_images(dpi_threshold=150, dpi_target=100, quality=50)

    assert result.considered >= 2
    assert result.rewritten == 0
    assert bytes(doc.pages[0].pixmap(alpha=True)) == before


@ALL
def test_select_first_page_and_roundtrip(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    doc.pages.select([0])
    assert len(doc.pages) == 1
    reopened = pylopdf.open(doc.to_bytes())
    assert len(reopened.pages) == 1


@ALL
def test_insert_subset_with_position_roundtrip(case: Case) -> None:
    """Importing page 0 at the front survives pruning without content loss."""
    doc = pylopdf.open(ASSETS / case.name)
    src = pylopdf.open(ASSETS / case.name)
    doc.pages.insert(0, src.pages[0:1])
    assert len(doc.pages) == case.pages + 1
    reopened = pylopdf.open(doc.to_bytes())
    assert len(reopened.pages) == case.pages + 1
    # Pruning preserves fonts/images referenced by the inserted page.
    assert reopened.pages[0].pixmap(alpha=True).to_bytes().startswith(b"\x89PNG")


@ALL
def test_merge_self_and_roundtrip(case: Case) -> None:
    raw = (ASSETS / case.name).read_bytes()
    doc = pylopdf.open(raw)
    doc.pages.extend(pylopdf.open(raw).pages)
    assert len(doc.pages) == case.pages * 2
    reopened = pylopdf.open(doc.to_bytes())
    assert len(reopened.pages) == case.pages * 2


@ALL
def test_merge_into_empty_and_roundtrip(case: Case) -> None:
    """Importing a real PDF into an empty document avoids Catalog/Pages ID collisions."""
    source = pylopdf.open(ASSETS / case.name)
    doc = pylopdf.Document()
    doc.pages.extend(source.pages)
    assert len(doc.pages) == case.pages
    reopened = pylopdf.open(doc.to_bytes())
    assert len(reopened.pages) == case.pages


@ALL
def test_delete_page_and_roundtrip(case: Case) -> None:
    if case.pages < 2:
        pytest.skip("deleting every page from a one-page document is out of scope")
    doc = pylopdf.open(ASSETS / case.name)
    del doc.pages[0]
    assert len(doc.pages) == case.pages - 1
    reopened = pylopdf.open(doc.to_bytes())
    assert len(reopened.pages) == case.pages - 1


@ALL
def test_save_optimized_roundtrip(case: Case) -> None:
    """Garbage, deflate, and object-stream saves preserve readable content."""
    doc = pylopdf.open(ASSETS / case.name)
    data = doc.to_bytes(object_streams=True)
    reopened = pylopdf.open(data)
    assert len(reopened.pages) == case.pages


def test_object_streams_reduce_size() -> None:
    """Object-stream saving reduces a medium-size document."""
    doc = pylopdf.open(ASSETS / "bill-hr815.pdf")
    plain = doc.to_bytes()
    optimized = doc.to_bytes(object_streams=True)
    assert len(optimized) < len(plain)


@ALL
def test_metadata_roundtrip(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    doc.metadata.update({"title": "回帰テスト", "author": "pylopdf"})
    reopened = pylopdf.open(doc.to_bytes())
    assert reopened.metadata["title"] == "回帰テスト"
    assert reopened.metadata["author"] == "pylopdf"


@ALL
def test_render_page_png(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    png = doc.pages[0].pixmap(matrix=pylopdf.Matrix(1.0, 1.0), alpha=True).to_bytes()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 1000


@ALL
def test_render_page_svg(case: Case) -> None:
    doc = pylopdf.open(ASSETS / case.name)
    svg = doc.pages[0].svg()
    assert svg.startswith("<svg")


@WITH_TEXT
def test_extract_text_survives_edit(case: Case) -> None:
    """Text extraction survives select with inherited attributes materialized."""
    assert case.snippet is not None
    doc = pylopdf.open(ASSETS / case.name)
    doc.pages.select([0])
    reopened = pylopdf.open(doc.to_bytes())
    assert case.snippet in reopened.pages[0].text()
