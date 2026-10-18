"""Time bundled-font text generation for one installed build.

Usage: python gen_ab.py [REPEATS]

Prints one JSON line with the median milliseconds of each workload after one
warmup, plus digests of every generated document so builds can be compared.
Every locale font extra must be installed.
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pylopdf

REUSED_CALLS = 20

JA = "日本語テキストの挿入"
ZH = "简体中文的文本"
AR = "مرحبا بالعالم"
JA_BOX = "日本語の段落を折り返して配置します。" * 4
ZH_BOX = "简体中文的段落会自动换行。" * 4
AR_BOX = "هذه فقرة عربية قصيرة للتجربة " * 4


def blank() -> pylopdf.Document:
    doc = pylopdf.Document()
    doc.new_page(width=595, height=842)
    return doc


def form_pdf(widgets: int) -> bytes:
    """One text field with ``widgets`` widget annotations on one page."""
    kids = " ".join(f"{5 + index} 0 R" for index in range(widgets))
    objects: dict[int, str] = {
        1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Annots [{kids}] >>",
        4: f"<< /Fields [{5 + widgets} 0 R] /DA (/Helv 0 Tf 0 g) >>",
        5 + widgets: f"<< /FT /Tx /T (customer) /V (initial) /Kids [{kids}] >>",
    }
    for index in range(widgets):
        top = 740 - 40 * index
        objects[5 + index] = (
            f"<< /Parent {5 + widgets} 0 R /Type /Annot /Subtype /Widget"
            f" /Rect [50 {top - 20} 300 {top}] /P 3 0 R /F 4 >>"
        )
    out = bytearray(b"%PDF-1.7\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n{objects[number]}\nendobj\n".encode("latin-1")
    xref = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n0000000000 65535 f \n".encode("ascii")
    for number in range(1, size):
        out += f"{offsets[number]:010d} 00000 n \n".encode("ascii")
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode("ascii")
    return bytes(out)


FORM_1 = form_pdf(1)
FORM_4 = form_pdf(4)


def text_call(text: str, **options: object) -> Callable[[pylopdf.Document], None]:
    def run(doc: pylopdf.Document) -> None:
        doc[0].insert_text((50, 100), text, fontsize=14, **options)  # type: ignore[arg-type]

    return run


def textbox_call(text: str, **options: object) -> Callable[[pylopdf.Document], None]:
    def run(doc: pylopdf.Document) -> None:
        spare = doc[0].insert_textbox((50, 120, 545, 400), text, fontsize=12, **options)  # type: ignore[arg-type]
        assert spare >= 0

    return run


def reused_text_call(text: str, **options: object) -> Callable[[pylopdf.Document], None]:
    def run(doc: pylopdf.Document) -> None:
        page = doc[0]
        for index in range(REUSED_CALLS):
            page.insert_text((50, 60 + 36 * index), text, fontsize=14, **options)  # type: ignore[arg-type]

    return run


def reused_textbox_call(text: str, **options: object) -> Callable[[pylopdf.Document], None]:
    def run(doc: pylopdf.Document) -> None:
        page = doc[0]
        for index in range(REUSED_CALLS):
            top = 20 + 40 * index
            page.insert_textbox((50, top, 545, top + 40), text[:24], fontsize=12, **options)  # type: ignore[arg-type]

    return run


def form_call(value: str, **options: object) -> Callable[[pylopdf.Document], None]:
    def run(doc: pylopdf.Document) -> None:
        doc.set_form_field("customer", value, **options)  # type: ignore[arg-type]

    return run


def reused_form_call(values: tuple[str, ...]) -> Callable[[pylopdf.Document], None]:
    def run(doc: pylopdf.Document) -> None:
        for index in range(REUSED_CALLS):
            doc.set_form_field("customer", values[index % len(values)])

    return run


def providers() -> dict[str, object]:
    return {provider.language: provider for provider in pylopdf._bundled_font_providers()}  # noqa: SLF001


def workloads() -> list[tuple[str, Callable[[], pylopdf.Document], Callable[[pylopdf.Document], None]]]:
    """Return (name, document factory, timed call) for every workload."""
    ja_bytes = Path(providers()["ja"].sans).read_bytes()  # type: ignore[attr-defined]
    return [
        ("ja_text_fresh", blank, text_call(JA)),
        ("ja_text_reused_x20", blank, reused_text_call(JA)),
        ("ja_textbox_fresh", blank, textbox_call(JA_BOX)),
        ("ja_textbox_reused_x20", blank, reused_textbox_call(JA_BOX)),
        ("ja_form_fresh", lambda: pylopdf.open(stream=FORM_1), form_call("山田 太郎")),
        ("ja_form_4_widgets_fresh", lambda: pylopdf.open(stream=FORM_4), form_call("山田 太郎")),
        ("ja_form_reused_x20", lambda: pylopdf.open(stream=FORM_1), reused_form_call(("山田 太郎", "佐藤 花子"))),
        ("zh_serif_text_fresh", blank, text_call(ZH, fontname="tiro", font_language="zh-CN")),
        ("zh_serif_text_reused_x20", blank, reused_text_call(ZH, fontname="tiro", font_language="zh-CN")),
        ("zh_serif_textbox_fresh", blank, textbox_call(ZH_BOX, fontname="tiro", font_language="zh-CN")),
        ("zh_form_fresh", lambda: pylopdf.open(stream=FORM_1), form_call("简体中文", font_language="zh-CN")),
        ("ar_text_fresh", blank, text_call(AR)),
        ("ar_text_reused_x20", blank, reused_text_call(AR)),
        ("ar_textbox_fresh", blank, textbox_call(AR_BOX)),
        ("ar_form_fresh", lambda: pylopdf.open(stream=FORM_1), form_call("مرحبا")),
        # Controls: explicit font buffer (copied per call) and Standard 14 text.
        ("ja_fontbuffer_text_fresh", blank, text_call(JA, fontbuffer=ja_bytes)),
        ("helv_text_fresh", blank, text_call("Latin text")),
    ]


def digest(doc: pylopdf.Document) -> str:
    return hashlib.sha256(doc.tobytes()).hexdigest()[:16]


def measure(
    factory: Callable[[], pylopdf.Document], call: Callable[[pylopdf.Document], None], repeats: int
) -> tuple[float, str]:
    doc = factory()
    call(doc)  # warmup
    result = digest(doc)
    doc.close()
    samples = []
    for _ in range(repeats):
        doc = factory()
        gc.collect()
        start = time.perf_counter()
        call(doc)
        samples.append((time.perf_counter() - start) * 1000)
        doc.close()
        del doc
    return statistics.median(samples), result


def main() -> None:
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    gc.disable()
    results: dict[str, float] = {}
    digests: dict[str, str] = {}
    for name, factory, call in workloads():
        results[name], digests[name] = measure(factory, call, repeats)
    print(
        json.dumps(
            {
                "pid": os.getpid(),
                "version": pylopdf.__version__,
                "font_providers": sorted(providers()),
                "ms": results,
                "digests": digests,
            }
        )
    )


if __name__ == "__main__":
    main()
