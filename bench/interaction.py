"""Check created annotations, links, form fields, and flattening against a pymupdf oracle.

Run ``uv run --group bench python bench/interaction.py``. The optional
benchmark group supplies pymupdf; it is never a runtime dependency of
pylopdf. The report compares how MuPDF and pylopdf render every annotation
type that pylopdf creates, what pymupdf reads of pylopdf's links and fields,
whether pymupdf can fill those fields for pylopdf to read back, and how a
flattened page renders in both.
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pymupdf

import pylopdf
from pylopdf import (
    Caret,
    CheckBox,
    Circle,
    Color,
    ComboBox,
    Destination,
    FileAttachment,
    FreeText,
    Highlight,
    Ink,
    Launch,
    LineAnnotation,
    LineEnding,
    ListBox,
    NamedAction,
    Note,
    Polygon,
    PolyLine,
    PushButton,
    RadioButton,
    Redaction,
    RemoteDestination,
    SignatureField,
    Square,
    Squiggly,
    Stamp,
    StrikeOut,
    TextField,
    Underline,
)

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "bench" / "results" / "interaction-latest.md"
#: A sample differs when a channel differs by more than this; anti-aliasing
#: and text rasterization differ between the renderers by less.
TOLERANCE = 64
DPI = 72
#: Rectangles agree within this many points.
RECT_TOLERANCE = 0.01

ANNOTATIONS: dict[str, Callable[[], Any]] = {
    "Square": lambda: Square((40, 40, 160, 120), interior_color=Color(0, 0, 1)),
    "Square, cloudy": lambda: Square((40, 40, 160, 120), clouds=2),
    "Circle": lambda: Circle((40, 40, 160, 120), interior_color=Color(1, 1, 0)),
    "Line with endings": lambda: LineAnnotation(
        (40, 60), (160, 110), line_endings=(LineEnding.CLOSED_ARROW, LineEnding.CIRCLE)
    ),
    "Polygon": lambda: Polygon([(40, 120), (160, 120), (100, 40)], interior_color=Color(0, 1, 0)),
    "PolyLine": lambda: PolyLine([(40, 120), (100, 40), (160, 120)], line_endings=(LineEnding.NONE, LineEnding.SLASH)),
    "Ink": lambda: Ink([[(40, 120), (80, 40), (120, 120), (160, 40)]]),
    "Highlight": lambda: Highlight((40, 70, 160, 90)),
    "Underline": lambda: Underline((40, 70, 160, 90)),
    "StrikeOut": lambda: StrikeOut((40, 70, 160, 90)),
    "Squiggly": lambda: Squiggly((40, 70, 160, 90)),
    "Note": lambda: Note((90, 70)),
    "FreeText": lambda: FreeText((40, 40, 160, 120), "Free text in a box", border=pylopdf.Border(1)),
    "FreeText with callout": lambda: FreeText(
        (80, 40, 180, 80), "Callout", border=pylopdf.Border(1), callout=[(30, 130), (60, 100), (80, 60)]
    ),
    "Stamp": lambda: Stamp((30, 50, 190, 110)),
    "Caret": lambda: Caret((90, 70)),
    "FileAttachment": lambda: FileAttachment((90, 70), b"data", filename="data.txt"),
    "Redaction": lambda: Redaction((40, 60, 160, 100)),
}


def _differing(pixmap: pylopdf.Pixmap, reference: pymupdf.Pixmap) -> float:
    """Return the share of samples that differ by more than the tolerance."""
    ours = bytes(pixmap)
    theirs = reference.samples
    if len(ours) != len(theirs):
        msg = "the renderers produced pixmaps of different sizes"
        raise ValueError(msg)
    return sum(abs(a - b) > TOLERANCE for a, b in zip(ours, theirs, strict=True)) / len(ours)


def _inked(pixmap: pymupdf.Pixmap) -> float:
    """Return the share of samples that are not white."""
    samples = pixmap.samples
    return sum(value < 255 - TOLERANCE for value in samples) / len(samples)


def _annotation_rows() -> list[str]:
    rows = []
    for rotation in (0, 90):
        for label, spec in ANNOTATIONS.items():
            doc = pylopdf.Document()
            page = doc.pages.new((220, 220))
            page.rotation = rotation
            annotation = page.annotations.add(spec())
            ours = page.pixmap(dpi=DPI)
            reference = pymupdf.open("pdf", doc.to_bytes())
            mupdf_page = reference[0]
            (read,) = list(mupdf_page.annots())
            theirs = mupdf_page.get_pixmap(dpi=DPI)
            # pymupdf reports annotation rectangles unrotated; pylopdf in display space.
            displayed = read.rect * mupdf_page.rotation_matrix
            same_rect = all(abs(a - b) < RECT_TOLERANCE for a, b in zip(annotation.rect, displayed, strict=True))
            rows.append(
                f"| {label} | {rotation} | {read.type[1]} | {'yes' if same_rect else 'no'} | "
                f"{_inked(theirs):.2%} | {_differing(ours, theirs):.2%} |"
            )
    return rows


def _link_rows() -> list[str]:
    doc = pylopdf.Document()
    doc.pages.new((300, 300))
    doc.pages.new((300, 300))
    page = doc.pages[0]
    targets = {
        "URI": "https://example.test/",
        "Destination": Destination(1, pylopdf.Point(10, 20)),
        "RemoteDestination": RemoteDestination("other.pdf", 2),
        "Launch": Launch("notes.txt"),
        "NamedAction": NamedAction("NextPage"),
    }
    for index, target in enumerate(targets.values()):
        page.links.add((10, 10 + 30 * index, 200, 30 + 30 * index), target)
    reference = pymupdf.open("pdf", doc.to_bytes())
    links = reference[0].get_links()
    names = {
        pymupdf.LINK_URI: "LINK_URI",
        pymupdf.LINK_GOTO: "LINK_GOTO",
        pymupdf.LINK_GOTOR: "LINK_GOTOR",
        pymupdf.LINK_LAUNCH: "LINK_LAUNCH",
        pymupdf.LINK_NAMED: "LINK_NAMED",
    }
    rows = []
    for (label, _), link in zip(targets.items(), links, strict=True):
        detail = link.get("uri") or link.get("file") or link.get("name") or link.get("page")
        rows.append(f"| {label} | {names.get(link['kind'], link['kind'])} | `{detail}` |")
    return rows


def _form_rows() -> list[str]:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 300))
    page.widgets.add(TextField("name", (20, 20, 200, 44), value="Ada"))
    page.widgets.add(CheckBox("agree", (20, 60, 36, 76)))
    page.widgets.add(RadioButton("plan", (20, 90, 36, 106), on_state="Basic", selected=True))
    page.widgets.add(RadioButton("plan", (50, 90, 66, 106), on_state="Pro"))
    page.widgets.add(ComboBox("level", (20, 120, 200, 144), choices=["low", "high"], value="low"))
    page.widgets.add(ListBox("color", (20, 160, 200, 220), choices=["red", "green", "blue"], value="red"))
    page.widgets.add(PushButton("go", (220, 20, 280, 44), caption="Go"))
    page.widgets.add(SignatureField("sign", (20, 240, 200, 280)))
    created = doc.to_bytes()

    reference = pymupdf.open("pdf", created)
    mupdf_page = reference[0]
    widgets = {widget.field_name: widget for widget in mupdf_page.widgets()}
    fills = {"name": "Grace", "agree": True, "level": "high", "color": "blue"}
    for name, value in fills.items():
        widget = widgets[name]
        widget.field_value = value if not isinstance(value, bool) else widget.on_state()
        widget.update()
    radio = [widget for widget in mupdf_page.widgets() if widget.field_name == "plan"]
    radio[1].field_value = True
    radio[1].update()
    filled = reference.tobytes()

    reopened = pylopdf.open(filled)
    rows = []
    for name, widget in widgets.items():
        read_back = reopened.form[name].value
        rows.append(f"| `{name}` | {widget.field_type_string} | `{widget.field_value!r}` | `{read_back!r}` |")
    return rows


def _flatten_row() -> str:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 300))
    page.annotations.add(Square((20, 20, 120, 80), interior_color=Color(0, 0, 1)))
    page.annotations.add(FreeText((140, 20, 280, 80), "Flattened", border=pylopdf.Border(1)))
    page.annotations.add(Stamp((20, 100, 280, 160)))
    page.widgets.add(TextField("name", (20, 200, 200, 224), value="Ada Lovelace"))
    page.widgets.add(CheckBox("agree", (220, 200, 236, 216), checked=True))
    created = doc.to_bytes()
    doc.flatten()
    flattened_doc = pymupdf.open("pdf", doc.to_bytes())
    flattened = flattened_doc[0].get_pixmap(dpi=DPI)
    baked_doc = pymupdf.open("pdf", created)
    baked_doc.bake()
    baked = baked_doc[0].get_pixmap(dpi=DPI)
    differing = sum(abs(a - b) > TOLERANCE for a, b in zip(flattened.samples, baked.samples, strict=True)) / len(
        baked.samples
    )
    return (
        f"{differing:.2%} of samples differ between pylopdf's flatten() and pymupdf's bake(), both rendered by MuPDF."
    )


def main() -> None:
    """Write the oracle report."""
    lines = [
        "# Interaction oracle",
        "",
        (
            f"pylopdf {pylopdf.__version__} against pymupdf {pymupdf.VersionBind}, "
            f"Python {platform.python_version()} on {platform.system()} {platform.machine()}."
        ),
        "",
        "## Annotations",
        "",
        (
            f"Each annotation is created by pylopdf on a 220-point page and rendered at {DPI} dpi by pylopdf "
            "(hayro) and by MuPDF from the same saved bytes. A sample differs when a channel differs by more "
            f"than {TOLERANCE}; inked is the share of MuPDF's samples that are not white."
        ),
        "",
        "| Annotation | Page rotation | pymupdf type | Same rect | Inked | Differing |",
        "| --- | ---: | --- | --- | ---: | ---: |",
        *_annotation_rows(),
        "",
        (
            "On the turned page MuPDF draws no note: it honours the NoRotate flag that pylopdf sets on notes "
            "(ISO 32000-2 §12.5.6.4) by turning the icon and its rectangle about its corner, while hayro "
            "ignores the flag (#338). The other differences are anti-aliasing and glyph rasterization "
            "along edges and text."
        ),
        "",
        "## Links",
        "",
        (
            "Each link is created by pylopdf and read by pymupdf's `get_links()`. MuPDF reports a launch "
            "action as `LINK_GOTOR` and resolves the named action `NextPage` to a page."
        ),
        "",
        "| Target | pymupdf kind | pymupdf reads |",
        "| --- | --- | --- |",
        *_link_rows(),
        "",
        "## Form fields",
        "",
        (
            "Fields created by pylopdf, filled by pymupdf, and read back by pylopdf. pymupdf's value of "
            "`plan` is that of the first radio button, which is off once pymupdf selects the second."
        ),
        "",
        "| Field | pymupdf type | pymupdf value | pylopdf reads |",
        "| --- | --- | --- | --- |",
        *_form_rows(),
        "",
        "## Flattening",
        "",
        _flatten_row(),
        "",
    ]
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
