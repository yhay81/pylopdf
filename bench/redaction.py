"""Check pylopdf's redaction of the adversarial corpus with pymupdf as the oracle.

Run ``uv run --no-sync --with pymupdf python bench/redaction.py``. pymupdf is
a development-only oracle, never a dependency of pylopdf. For each case of
``tests/redaction_corpus.py``, pylopdf redacts page 0 and saves the file in
full; pymupdf then opens the result and reports:

- whether its text extraction of the whole page, or of any area, still holds
  a secret, and whether a character of a secret lies inside an area;
- whether any of its drawings or image placements lies inside an area,
  except the fills of applied redaction annotations;
- whether MuPDF's rendering of an area, inset by 1.5 points, holds any pixel
  other than white or the black of blanked or filled areas;
- whether the kept strings survived.

Results are written to ``bench/results/redaction-latest.md``; a leak in any
column fails the run.
"""

from __future__ import annotations

import platform
import sys
import warnings
from pathlib import Path

import pymupdf

import pylopdf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from redaction_corpus import Case, cases, redact  # noqa: E402 - the corpus lives with the tests.

OUTPUT = ROOT / "bench" / "results" / "redaction-latest.md"
#: A channel this far from white or black counts as ink.
TOLERANCE = 24
INSET = 1.5


def _meets(a: pymupdf.Rect, b: pymupdf.Rect) -> bool:
    return a.x0 < b.x1 and a.x1 > b.x0 and a.y0 < b.y1 and a.y1 > b.y0


def _inset(area: pylopdf.Rect, by: float) -> pymupdf.Rect:
    return pymupdf.Rect(area.x0 + by, area.y0 + by, area.x1 - by, area.y1 - by)


def _display(page: pymupdf.Page, rect: pymupdf.Rect) -> pymupdf.Rect:
    """Map an unrotated pymupdf rectangle to display space, as pylopdf reports areas."""
    return rect * page.rotation_matrix


def _check(case: Case) -> dict[str, object]:  # noqa: C901, PLR0912 - one check after another.
    doc, areas, report = redact(case)
    saved = doc.to_bytes()
    reference = pymupdf.open("pdf", saved)
    page = reference[0]
    inner = [_inset(area, INSET) for area in areas]
    text = page.get_text("text")
    flat = text.replace("\n", "").replace(" ", "")
    leaked_text = [secret for secret in case.secrets if secret in text or secret in flat]
    for area in inner:
        clipped = page.get_text("text", clip=area * ~page.rotation_matrix).strip()
        if clipped:
            leaked_text.append(f"clip: {clipped!r}")
    chars_inside = 0
    secret_chars = set("".join(case.secrets))
    raw = page.get_text("rawdict")
    for block in raw["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for char in span["chars"]:
                    box = _display(page, pymupdf.Rect(char["bbox"]))
                    center = pymupdf.Rect(
                        box.x0 + box.width / 2 - 0.01,
                        box.y0 + box.height / 2 - 0.01,
                        box.x0 + box.width / 2 + 0.01,
                        box.y0 + box.height / 2 + 0.01,
                    )
                    # MuPDF and hayro can place the glyphs of a font that is
                    # not embedded differently, so a kept neighbour's box may
                    # reach in; only a secret's characters count.
                    if char["c"] in secret_chars and any(_meets(center, area) for area in inner):
                        chars_inside += 1
    graphics_inside = 0
    if case.options.get("graphics", "touched") == "touched" and not case.annotate:
        for drawing in page.get_drawings():
            if any(_meets(_display(page, drawing["rect"]), area) for area in inner):
                graphics_inside += 1
    images_inside = 0
    for info in page.get_image_info(xrefs=True):
        box = _display(page, pymupdf.Rect(info["bbox"]))
        blanked = report.blanked_images > 0
        if any(_meets(box, area) for area in inner) and not blanked:
            images_inside += 1
    inked = 0
    pixmap = page.get_pixmap(dpi=72)
    for area in areas:
        box = _inset(area, INSET)
        for y in range(max(0, int(box.y0)), min(pixmap.height, int(box.y1) + 1)):
            for x in range(max(0, int(box.x0)), min(pixmap.width, int(box.x1) + 1)):
                pixel = pixmap.pixel(x, y)[:3]
                white = all(value >= 255 - TOLERANCE for value in pixel)
                black = all(value <= TOLERANCE for value in pixel)
                if not (white or black):
                    inked += 1
    missing = [kept for kept in case.kept if kept not in flat]
    return {
        "name": case.name,
        "areas": len(areas),
        "glyphs": report.glyphs,
        "images": report.images + report.blanked_images,
        "paths": report.paths,
        "leaked_text": leaked_text,
        "chars_inside": chars_inside,
        "graphics_inside": graphics_inside,
        "images_inside": images_inside,
        "inked": inked,
        "missing": missing,
    }


def main() -> None:
    """Redact every case, check it with pymupdf, and write the report."""
    warnings.simplefilter("ignore", pylopdf.PylopdfWarning)
    rows = [_check(case) for case in cases()]
    leaks = [
        row
        for row in rows
        if row["leaked_text"] or row["chars_inside"] or row["graphics_inside"] or row["images_inside"] or row["inked"]
    ]
    lines = [
        "# Redaction against a pymupdf oracle",
        "",
        (
            f"pylopdf {pylopdf.__version__}, pymupdf {pymupdf.VersionBind}, Python {platform.python_version()}, "
            f"{platform.system()} {platform.machine()}."
        ),
        "",
        "pylopdf redacts page 0 of every case of `tests/redaction_corpus.py` and saves it in full;",
        "pymupdf reads the result. A leak is a secret in pymupdf's text of the page or of an area, a",
        "character of a secret whose center lies in an area, a drawing or unblanked image placement in an",
        "area, or a",
        f"pixel of MuPDF's rendering of an area (inset by {INSET} points) that is neither white nor black.",
        "",
        (
            "| Case | Areas | Glyphs | Images | Paths | Text leaks | Chars in areas | Drawings in areas "
            "| Images in areas | Inked pixels | Kept missing |"
        ),
        "|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|",
    ]
    lines.extend(
        f"| {row['name']} | {row['areas']} | {row['glyphs']} | {row['images']} | {row['paths']} | "
        f"{', '.join(row['leaked_text']) or 'none'} | {row['chars_inside']} | {row['graphics_inside']} | "
        f"{row['images_inside']} | {row['inked']} | {', '.join(row['missing']) or 'none'} |"
        for row in rows
    )
    lines += ["", f"{len(rows)} cases, {len(leaks)} with a leak.", ""]
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    sys.stdout.write("\n".join(lines))
    if leaks:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
