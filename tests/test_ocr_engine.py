"""Tests for the optional native PP-OCR engine."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import pylopdf

MODEL_ROOT = Path(__file__).parents[1] / "models" / "pylopdf-ocr-models" / "src" / "pylopdf_ocr_models"
FONT_ROOT = Path(__file__).parents[1] / "fonts" / "pylopdf-fonts-cjk" / "src" / "pylopdf_fonts_cjk"


@pytest.fixture(scope="module")
def ocr_engine() -> pylopdf.OcrEngine:
    """Load the bundled model set once for OCR integration tests."""
    return pylopdf.OcrEngine(
        MODEL_ROOT / "PP-OCRv6_det_small.rten",
        MODEL_ROOT / "PP-OCRv6_rec_small.rten",
        MODEL_ROOT / "ppocrv6_dict.txt",
    )


def test_ocr_model_artifact_hashes() -> None:
    checksums = (MODEL_ROOT / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    for entry in checksums:
        expected, filename = entry.split("  ", maxsplit=1)
        assert hashlib.sha256((MODEL_ROOT / filename).read_bytes()).hexdigest() == expected


def test_ocr_model_package_exposes_complete_paths() -> None:
    models = pytest.importorskip("pylopdf_ocr_models")

    paths = models.model_paths()

    assert paths.detector.name == "PP-OCRv6_det_small.rten"
    assert paths.recognizer.name == "PP-OCRv6_rec_small.rten"
    assert paths.dictionary.name == "ppocrv6_dict.txt"
    assert all(path.is_file() for path in paths)
    assert (paths.detector.parent / "SHA256SUMS").is_file()


def test_ocr_engine_recognizes_rendered_text(ocr_engine: pylopdf.OcrEngine) -> None:
    doc = pylopdf.Document()
    doc.new_page(width=360, height=120)
    doc[0].insert_text((20, 75), "Invoice 123", fontsize=36)

    words = ocr_engine.recognize(doc[0], dpi=150, tile_size=512, overlap=64)

    assert [word["text"] for word in words] == ["Invoice 123"]
    assert words[0]["confidence"] > 0.99
    assert words[0]["bbox"].x0 < 20 < words[0]["bbox"].x1
    assert words[0]["bbox"].y0 < 60 < words[0]["bbox"].y1


def test_ocr_engine_recognizes_japanese(ocr_engine: pylopdf.OcrEngine) -> None:
    doc = pylopdf.Document()
    doc.new_page(width=400, height=120)
    doc[0].insert_text(
        (20, 75),
        "請求書 123",
        fontsize=36,
        fontfile=FONT_ROOT / "NotoSansJP-Regular.otf",
    )

    words = ocr_engine.recognize(doc[0], dpi=150, tile_size=512, overlap=64)

    assert [word["text"] for word in words] == ["請求書 123"]
    assert words[0]["confidence"] > 0.9


def test_ocr_engine_orders_sustained_columns(ocr_engine: pylopdf.OcrEngine) -> None:
    doc = pylopdf.Document()
    doc.new_page(width=500, height=180)
    page = doc[0]
    page.insert_text((20, 60), "Left one", fontsize=24)
    page.insert_text((20, 120), "Left two", fontsize=24)
    page.insert_text((290, 60), "Right one", fontsize=24)
    page.insert_text((290, 120), "Right two", fontsize=24)

    words = ocr_engine.recognize(page, dpi=150, tile_size=512, overlap=64)

    assert [word["text"] for word in words] == ["Left one", "Left two", "Right one", "Right two"]


def test_ocr_engine_is_reusable_across_concurrent_documents(ocr_engine: pylopdf.OcrEngine) -> None:
    documents = []
    for text in ("Alpha 123", "Beta 456"):
        document = pylopdf.Document()
        document.new_page(width=280, height=100)
        document[0].insert_text((20, 65), text, fontsize=30)
        documents.append(document)

    def recognize(document: pylopdf.Document) -> list[str]:
        words = document[0].get_text_ocr(
            engine=ocr_engine,
            dpi=150,
            tile_size=512,
            overlap=64,
        )
        return [word["text"] for word in words]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(recognize, documents))

    assert results == [["Alpha 123"], ["Beta 456"]]


def test_apply_ocr_adds_searchable_invisible_text(ocr_engine: pylopdf.OcrEngine) -> None:
    source = pylopdf.Document()
    source.new_page(width=360, height=120)
    source[0].insert_text((20, 75), "Search 456", fontsize=36)
    raster = source[0].get_pixmap(dpi=150, background=(255, 255, 255))

    target = pylopdf.Document()
    target.new_page(width=360, height=120)
    target[0].insert_image(target[0].rect, stream=raster.tobytes())
    before = target[0].get_pixmap(background=(255, 255, 255)).samples

    words = target[0].apply_ocr(dpi=150, engine=ocr_engine, tile_size=512, overlap=64)

    assert [word["text"] for word in words] == ["Search 456"]
    assert target[0].search_for("Search 456")
    assert target[0].get_pixmap(background=(255, 255, 255)).samples == before
    reopened = pylopdf.open(stream=target.tobytes())
    assert reopened[0].search_for("Search 456")
    assert reopened[0].get_pixmap(background=(255, 255, 255)).samples == before


def test_apply_ocr_clip_handles_mixed_content(ocr_engine: pylopdf.OcrEngine) -> None:
    source = pylopdf.Document()
    source.new_page(width=360, height=120)
    source[0].insert_text((20, 75), "Region 789", fontsize=36)
    raster = source[0].get_pixmap(dpi=150, background=(255, 255, 255))

    target = pylopdf.Document()
    target.new_page(width=360, height=220)
    target[0].insert_text((20, 30), "Digital header", fontsize=14)
    target[0].insert_image((0, 80, 360, 200), stream=raster.tobytes())
    before = target[0].get_pixmap(background=(255, 255, 255)).samples

    words = target[0].apply_ocr(
        dpi=150,
        engine=ocr_engine,
        tile_size=512,
        overlap=64,
        clip=(0, 70, 360, 210),
    )

    assert [word["text"] for word in words] == ["Region 789"]
    assert words[0]["bbox"].y0 > 70
    assert target[0].search_for("Region 789")
    assert target[0].get_pixmap(background=(255, 255, 255)).samples == before


def test_apply_ocr_skips_existing_text_by_default() -> None:
    doc = pylopdf.Document()
    doc.new_page(width=200, height=100)
    doc[0].insert_text((20, 50), "Already searchable")

    assert doc[0].apply_ocr() == []
    with pytest.raises(TypeError, match="skip_existing"):
        doc[0].apply_ocr(skip_existing=1)  # type: ignore[arg-type]


@pytest.mark.parametrize("dpi", [0, -1, float("inf"), float("nan"), "bad"])
def test_ocr_engine_rejects_invalid_dpi(ocr_engine: pylopdf.OcrEngine, dpi: object) -> None:
    doc = pylopdf.Document()
    doc.new_page(width=100, height=100)
    with pytest.raises(pylopdf.OcrError, match="dpi"):
        ocr_engine.recognize(doc[0], dpi=dpi)  # type: ignore[arg-type]


def test_ocr_engine_rejects_incomplete_or_missing_model_paths(tmp_path: Path) -> None:
    with pytest.raises(pylopdf.OcrError, match="provided together"):
        pylopdf.OcrEngine(tmp_path / "det.onnx", None, tmp_path / "dict.txt")
    with pytest.raises(pylopdf.OcrError, match="failed to load OCR detector"):
        pylopdf.OcrEngine(tmp_path / "det.onnx", tmp_path / "rec.onnx", tmp_path / "dict.txt")


def test_ocr_engine_rejects_mismatched_dictionary(tmp_path: Path) -> None:
    dictionary = tmp_path / "mismatched.txt"
    dictionary.write_text("x\n", encoding="utf-8")
    engine = pylopdf.OcrEngine(
        MODEL_ROOT / "PP-OCRv6_det_small.rten",
        MODEL_ROOT / "PP-OCRv6_rec_small.rten",
        dictionary,
    )
    doc = pylopdf.Document()
    doc.new_page(width=200, height=100)
    doc[0].insert_text((20, 60), "Text", fontsize=30)

    with pytest.raises(pylopdf.OcrError, match="dictionary requires"):
        engine.recognize(doc[0], dpi=150, tile_size=512, overlap=64)


def test_ocr_engine_discovers_installed_model_extra() -> None:
    pytest.importorskip("pylopdf_ocr_models")
    engine = pylopdf.OcrEngine()
    assert 1 <= engine.threads <= 4


@pytest.mark.parametrize(
    ("threads", "error"),
    [(0, pylopdf.OcrError), (17, pylopdf.OcrError), (True, TypeError), (1.5, TypeError)],
)
def test_ocr_engine_validates_threads(threads: object, error: type[Exception]) -> None:
    with pytest.raises(error, match="threads"):
        pylopdf.OcrEngine(threads=threads)  # type: ignore[arg-type]


def test_ocr_engine_validates_runtime_options(ocr_engine: pylopdf.OcrEngine) -> None:
    doc = pylopdf.Document()
    doc.new_page(width=100, height=100)
    with pytest.raises(pylopdf.OcrError, match="tile_size"):
        ocr_engine.recognize(doc[0], dpi=72, tile_size=500)
    with pytest.raises(pylopdf.OcrError, match="overlap"):
        ocr_engine.recognize(doc[0], dpi=72, tile_size=512, overlap=16)
    with pytest.raises(pylopdf.OcrError, match="min_confidence"):
        ocr_engine.recognize(doc[0], dpi=72, tile_size=512, overlap=64, min_confidence=2)
    with pytest.raises(TypeError, match="tile_size"):
        ocr_engine.recognize(doc[0], dpi=72, tile_size=512.0)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="overlap"):
        ocr_engine.recognize(doc[0], dpi=72, tile_size=512, overlap=True)
    with pytest.raises(TypeError, match="dpi"):
        ocr_engine.recognize(doc[0], dpi=True)
    with pytest.raises(TypeError, match="min_confidence"):
        ocr_engine.recognize(doc[0], dpi=72, min_confidence=True)
    with pytest.raises(pylopdf.PdfError, match="clip does not intersect"):
        ocr_engine.recognize(doc[0], dpi=72, clip=(200, 200, 300, 300))


def test_ocr_error_hierarchy() -> None:
    assert issubclass(pylopdf.OcrError, pylopdf.PdfError)
    assert issubclass(pylopdf.OcrError, ValueError)
