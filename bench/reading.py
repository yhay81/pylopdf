"""Check extracted text against a pymupdf oracle on the bundled corpus.

Run ``uv run --group bench python bench/reading.py``. The optional benchmark
group supplies pymupdf; it is never a runtime dependency of pylopdf. The
report pairs every character of ``page.layout(chars=True)`` on the first six
pages of each corpus document with the character of pymupdf's ``rawdict`` that
has the same text and the same origin to a tenth of a point, then compares
their span colors and the intersection over union of their boxes.
"""

from __future__ import annotations

import platform
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

import pylopdf

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "tests" / "assets" / "real_world"
OUTPUT = ROOT / "bench" / "results" / "reading-latest.md"
PAGES = 6
#: The published thresholds that the boxes of characters with font metrics
#: meet: their mean IoU, and their share at or above ``IOU_FLOOR``.
MEAN_IOU = 0.95
IOU_FLOOR = 0.8
FLOOR_SHARE = 0.99


@dataclass
class Tally:
    """The paired characters of one document."""

    paired: int = 0
    unpaired: int = 0
    same_color: int = 0
    ious: list[float] = field(default_factory=list)
    with_metrics: list[float] = field(default_factory=list)


def _iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    inter = max(0.0, width) * max(0.0, height)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _srgb(color: pylopdf.Color | None) -> int:
    if color is None:
        return 0
    return round(color.red * 255) << 16 | round(color.green * 255) << 8 | round(color.blue * 255)


def _key(text: str, x: float, y: float) -> tuple[str, float, float]:
    return (text, round(x, 1), round(y, 1))


def _oracle_chars(page: pymupdf.Page) -> dict[tuple[str, float, float], tuple[int, tuple[float, ...]]]:
    """Return pymupdf's characters by text and origin, with their span color and box.

    pymupdf reports positions on the unrotated page and pylopdf on the
    displayed one, so the positions are turned with the page.
    """
    rotation = page.rotation_matrix
    chars = {}
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for char in span["chars"]:
                    if char["c"].strip():
                        key = _key(char["c"], *(pymupdf.Point(char["origin"]) * rotation))
                        chars.setdefault(key, (span["color"], tuple(pymupdf.Rect(char["bbox"]) * rotation)))
    return chars


def _tally(path: Path) -> Tally:
    tally = Tally()
    oracle = pymupdf.open(path)
    document = pylopdf.open(path)
    for index, page in enumerate(document.pages[:PAGES]):
        expected = _oracle_chars(oracle[index])
        spans = [span for block in page.layout(chars=True).blocks for line in block.lines for span in line.spans]
        for span in spans:
            for char in span.chars:
                if not char.text.strip():
                    continue
                match = expected.get(_key(char.text, char.origin.x, char.origin.y))
                if match is None:
                    tally.unpaired += 1
                    continue
                tally.paired += 1
                tally.same_color += match[0] == _srgb(span.color)
                iou = _iou(match[1], tuple(char.bbox))
                tally.ious.append(iou)
                if (span.ascender, span.descender) != (0.8, -0.2):
                    tally.with_metrics.append(iou)
    return tally


def _summary(ious: list[float]) -> str:
    if not ious:
        return "n/a | n/a | n/a"
    ordered = sorted(ious)
    mean = sum(ordered) / len(ordered)
    share = sum(value >= IOU_FLOOR for value in ordered) / len(ordered)
    return f"{mean:.3f} | {ordered[len(ordered) // 20]:.3f} | {share:.2%}"


def main() -> None:
    """Write the oracle report."""
    warnings.simplefilter("ignore", pylopdf.PylopdfWarning)
    rows = []
    total = Tally()
    for path in sorted(CORPUS.glob("*.pdf")):
        try:
            tally = _tally(path)
        except (pylopdf.PdfError, RuntimeError) as error:
            rows.append(f"| {path.name} | skipped: {error} | | | | | |")
            continue
        total.paired += tally.paired
        total.unpaired += tally.unpaired
        total.same_color += tally.same_color
        total.ious += tally.ious
        total.with_metrics += tally.with_metrics
        colors = f"{tally.same_color / tally.paired:.2%}" if tally.paired else "n/a"
        rows.append(f"| {path.name} | {tally.paired} | {tally.unpaired} | {colors} | {_summary(tally.ious)} |")
    measured = total.with_metrics
    mean = sum(measured) / len(measured)
    share = sum(value >= IOU_FLOOR for value in measured) / len(measured)
    verdict = "meet" if mean >= MEAN_IOU and share >= FLOOR_SHARE else "miss"
    lines = [
        "# Reading oracle",
        "",
        (
            f"pylopdf {pylopdf.__version__} against pymupdf {pymupdf.VersionBind}, "
            f"Python {platform.python_version()} on {platform.system()} {platform.machine()}."
        ),
        "",
        (
            f"Characters of `page.layout(chars=True)` on the first {PAGES} pages of each corpus document, paired "
            "with the character of pymupdf's `rawdict` that has the same text and origin to a tenth of a point, "
            "both in display space. "
            "Unpaired characters are those either reader lacks or places elsewhere."
        ),
        "",
        "| Document | Paired | Unpaired | Same span color | Mean IoU | 5th percentile | IoU ≥ 0.8 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        *rows,
        (
            f"| **all** | {total.paired} | {total.unpaired} | {total.same_color / total.paired:.2%} | "
            f"{_summary(total.ious)} |"
        ),
        "",
        "## Boxes",
        "",
        (
            "Characters whose font program hayro hands out take the program's ascender and descender, as "
            "MuPDF falls back to without a font descriptor. Over those "
            f"{len(measured)} characters the mean IoU, the 5th percentile, and the share at or above "
            f"{IOU_FLOOR} are {_summary(measured)}. The published threshold is a mean IoU of at least "
            f"{MEAN_IOU} with at least {FLOOR_SHARE:.0%} of them at {IOU_FLOOR} or more; the boxes "
            f"{verdict} "
            "it. The other characters, in Type 1 fonts and fonts that are not embedded, take MuPDF's defaults "
            "of 0.8 and -0.2, where MuPDF reads the font descriptor's `/Ascent` and `/Descent`; reading them "
            "waits for hayro to identify the font dictionary of a glyph (#338)."
        ),
        "",
    ]
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
