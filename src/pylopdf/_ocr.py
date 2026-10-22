"""The pure-Rust OCR engine and its input validation."""

from __future__ import annotations

import functools
import math
import os
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pylopdf._limits import _validate_optional_positive_int, _validate_rect
from pylopdf._types import Rect
from pylopdf.pylopdf_core import OcrError, Pixmap, _OcrEngine

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pylopdf._page import Page
    from pylopdf._types import OcrRotation


_DEFAULT_MAX_OCR_MODEL_SIZE = 64 * 1024 * 1024
_OCR_MAX_THREADS = 16
_OCR_MAX_CONCURRENT = 16
_OCR_MIN_TILE_SIZE = 256
_OCR_MAX_TILE_SIZE = 2048
_OCR_TILE_MULTIPLE = 32


@dataclass(frozen=True, slots=True, kw_only=True)
class OcrWord:
    """One recognized word: its text, its box, and the engine's confidence from 0 to 1.

    :meth:`Page.ocr` and :meth:`Page.apply_ocr` give boxes in the page's
    display space and :meth:`OcrEngine.recognize` in the pixmap's pixels.
    Words that another OCR engine recognized go to :meth:`Page.apply_ocr` as
    ``OcrWord`` records in display space.
    """

    text: str
    bbox: Rect
    confidence: float


def _validate_ocr_dpi(dpi: float) -> float:
    """Resolve one positive finite OCR resolution."""
    if isinstance(dpi, bool):
        msg = f"dpi must be a positive finite number: {dpi!r}"
        raise TypeError(msg)
    try:
        resolved_dpi = float(dpi)
    except (TypeError, ValueError) as exc:
        msg = f"dpi must be a positive finite number: {dpi!r}"
        raise OcrError(msg) from exc
    if not (math.isfinite(resolved_dpi) and resolved_dpi > 0):
        msg = f"dpi must be a positive finite number: {dpi!r}"
        raise OcrError(msg)
    return resolved_dpi


def _validate_ocr_tiles(tile_size: int, overlap: int) -> None:
    """Validate bounded detector tile geometry."""
    if isinstance(tile_size, bool) or not isinstance(tile_size, int):
        msg = (
            f"tile_size must be an integer multiple of {_OCR_TILE_MULTIPLE} from "
            f"{_OCR_MIN_TILE_SIZE} through {_OCR_MAX_TILE_SIZE}: {tile_size!r}"
        )
        raise TypeError(msg)
    if not _OCR_MIN_TILE_SIZE <= tile_size <= _OCR_MAX_TILE_SIZE or tile_size % _OCR_TILE_MULTIPLE:
        msg = (
            f"tile_size must be a multiple of {_OCR_TILE_MULTIPLE} from "
            f"{_OCR_MIN_TILE_SIZE} through {_OCR_MAX_TILE_SIZE}: {tile_size}"
        )
        raise OcrError(msg)
    if isinstance(overlap, bool) or not isinstance(overlap, int):
        msg = f"overlap must be an integer from {_OCR_TILE_MULTIPLE} through half of tile_size: {overlap!r}"
        raise TypeError(msg)
    if not _OCR_TILE_MULTIPLE <= overlap <= tile_size // 2:
        msg = f"overlap must be from {_OCR_TILE_MULTIPLE} through half of tile_size: {overlap}"
        raise OcrError(msg)


def _validate_ocr_confidence(min_confidence: float) -> float:
    """Resolve one finite OCR confidence threshold."""
    if isinstance(min_confidence, bool):
        msg = f"min_confidence must be a finite number from 0 through 1: {min_confidence!r}"
        raise TypeError(msg)
    try:
        resolved_confidence = float(min_confidence)
    except (TypeError, ValueError) as exc:
        msg = f"min_confidence must be a finite number from 0 through 1: {min_confidence!r}"
        raise OcrError(msg) from exc
    if not (math.isfinite(resolved_confidence) and 0 <= resolved_confidence <= 1):
        msg = f"min_confidence must be a finite number from 0 through 1: {min_confidence!r}"
        raise OcrError(msg)
    return resolved_confidence


def _validate_ocr_concurrency(max_concurrent: int) -> int:
    """Validate the number of complete OCR calls admitted per engine."""
    if isinstance(max_concurrent, bool) or not isinstance(max_concurrent, int):
        msg = f"max_concurrent must be an integer from 1 through {_OCR_MAX_CONCURRENT}: {max_concurrent!r}"
        raise TypeError(msg)
    if not 1 <= max_concurrent <= _OCR_MAX_CONCURRENT:
        msg = f"max_concurrent must be from 1 through {_OCR_MAX_CONCURRENT}: {max_concurrent}"
        raise OcrError(msg)
    return max_concurrent


def _validate_ocr_rotation(rotation: int) -> OcrRotation:
    """Validate one clockwise OCR input correction."""
    if isinstance(rotation, bool) or not isinstance(rotation, int):
        msg = f"rotation must be 0, 90, 180, or 270: {rotation!r}"
        raise TypeError(msg)
    if rotation not in (0, 90, 180, 270):
        msg = f"rotation must be 0, 90, 180, or 270: {rotation}"
        raise OcrError(msg)
    return rotation


class OcrEngine:
    """Reusable, pure-Rust PP-OCR engine.

    With no paths, the engine discovers models from the optional
    ``pylopdf[ocr]`` installation. Explicit paths are useful for evaluating a
    compatible RTen-format PP-OCR detector, recognizer, and character
    dictionary.
    """

    def __init__(  # noqa: PLR0913 - model resource controls are keyword-only.
        self,
        detector_path: str | os.PathLike[str] | None = None,
        recognizer_path: str | os.PathLike[str] | None = None,
        dictionary_path: str | os.PathLike[str] | None = None,
        *,
        threads: int | None = None,
        max_concurrent: int = 1,
        max_model_size: int | None = _DEFAULT_MAX_OCR_MODEL_SIZE,
    ) -> None:
        """Load an OCR model set once for reuse across pages and documents.

        ``threads=None`` uses up to four logical CPUs. Lower it to reduce peak
        inference memory or raise it to at most 16 after measuring the target
        workload. ``max_concurrent`` bounds complete render-and-recognize calls
        sharing this engine; its memory-safe default is one. ``max_model_size``
        bounds the cumulative detector, recognizer, and dictionary input.
        Pass ``None`` only for trusted custom model sets.
        """
        _validate_optional_positive_int("max_model_size", max_model_size)
        if threads is None:
            resolved_threads = min(4, os.cpu_count() or 1)
        elif isinstance(threads, bool) or not isinstance(threads, int):
            msg = f"threads must be an integer from 1 through {_OCR_MAX_THREADS} or None: {threads!r}"
            raise TypeError(msg)
        elif not 1 <= threads <= _OCR_MAX_THREADS:
            msg = f"threads must be from 1 through {_OCR_MAX_THREADS}: {threads}"
            raise OcrError(msg)
        else:
            resolved_threads = threads
        resolved_max_concurrent = _validate_ocr_concurrency(max_concurrent)
        paths = (detector_path, recognizer_path, dictionary_path)
        if all(path is None for path in paths):
            try:
                import pylopdf_ocr_models  # noqa: PLC0415 - lazy optional dependency.
            except ImportError as exc:
                msg = "OCR models are not installed; install pylopdf[ocr] or pass explicit model paths"
                raise OcrError(msg) from exc
            try:
                discovered = pylopdf_ocr_models.model_paths()
                detector_path = discovered.detector
                recognizer_path = discovered.recognizer
                dictionary_path = discovered.dictionary
            except (AttributeError, TypeError) as exc:
                msg = "pylopdf-ocr-models does not expose a compatible model_paths() result"
                raise OcrError(msg) from exc
        elif any(path is None for path in paths):
            msg = "detector_path, recognizer_path, and dictionary_path must be provided together"
            raise OcrError(msg)
        if detector_path is None or recognizer_path is None or dictionary_path is None:
            msg = "OCR model discovery returned incomplete paths"
            raise OcrError(msg)
        self._engine = _OcrEngine(
            os.fspath(detector_path),
            os.fspath(recognizer_path),
            os.fspath(dictionary_path),
            resolved_threads,
            max_model_size,
        )
        self.threads = resolved_threads
        self.max_concurrent = resolved_max_concurrent
        self.max_model_size = max_model_size
        self._recognition_slots = threading.BoundedSemaphore(resolved_max_concurrent)

    def recognize(
        self,
        pixmap: Pixmap,
        *,
        tile_size: int = 1408,
        overlap: int = 192,
        min_confidence: float = 0.5,
        rotation: OcrRotation = 0,
    ) -> list[OcrWord]:
        """Recognize text in any :class:`Pixmap`.

        Boxes are in the pixmap's pixel coordinates, with the origin at its
        top-left corner. Transparent pixels are read over white. Detection
        uses bounded, overlapping tiles so A4 scans do not require a
        gigabyte-scale detector tensor. ``rotation`` turns the raster
        clockwise for recognition, then maps boxes back. To recognize a page
        in page coordinates, use :meth:`Page.ocr`.
        """
        if not isinstance(pixmap, Pixmap):
            msg = f"pixmap must be a pylopdf.Pixmap: {pixmap!r}"
            raise TypeError(msg)
        _validate_ocr_tiles(tile_size, overlap)
        resolved_confidence = _validate_ocr_confidence(min_confidence)
        resolved_rotation = _validate_ocr_rotation(rotation)
        with self._recognition_slots:
            raster_results = self._engine.recognize_pixmap(
                pixmap,
                tile_size=tile_size,
                overlap=overlap,
                min_confidence=resolved_confidence,
                rotation=resolved_rotation,
            )
        return [
            OcrWord(text=text, bbox=Rect(x0, y0, x1, y1), confidence=confidence)
            for x0, y0, x1, y1, text, confidence in raster_results
        ]

    def _recognize_page(  # noqa: PLR0913 - OCR resource controls are keyword-only.
        self,
        page: Page,
        *,
        dpi: float,
        tile_size: int,
        overlap: int,
        min_confidence: float,
        rotation: OcrRotation,
        clip: Sequence[float] | None,
    ) -> list[OcrWord]:
        """Render a page on white at ``dpi`` and recognize it in page coordinates.

        One admission slot covers rendering and recognition, so the configured
        concurrency bounds both rasters. ``clip`` limits recognition to one
        display-coordinate region while keeping page coordinates in the boxes.
        """
        resolved_dpi = _validate_ocr_dpi(dpi)
        _validate_ocr_tiles(tile_size, overlap)
        resolved_confidence = _validate_ocr_confidence(min_confidence)
        resolved_rotation = _validate_ocr_rotation(rotation)
        clip_rect = None if clip is None else _validate_rect(clip, name="clip")
        with self._recognition_slots:
            # Opaque RGBA on white, which recognition reads without a copy.
            pixmap = page.pixmap(dpi=resolved_dpi, clip=clip_rect, alpha=True, background=(255, 255, 255))
            raster_results = self._engine.recognize_pixmap(
                pixmap,
                tile_size=tile_size,
                overlap=overlap,
                min_confidence=resolved_confidence,
                rotation=resolved_rotation,
            )
        pixel_to_page = 72.0 / resolved_dpi
        if clip_rect is None:
            offset_x = offset_y = 0.0
        else:
            page_rect = page.rect
            offset_x = math.floor(max(clip_rect[0], page_rect.x0) / pixel_to_page) * pixel_to_page
            offset_y = math.floor(max(clip_rect[1], page_rect.y0) / pixel_to_page) * pixel_to_page
        return [
            OcrWord(
                text=text,
                bbox=Rect(
                    offset_x + x0 * pixel_to_page,
                    offset_y + y0 * pixel_to_page,
                    offset_x + x1 * pixel_to_page,
                    offset_y + y1 * pixel_to_page,
                ),
                confidence=confidence,
            )
            for x0, y0, x1, y1, text, confidence in raster_results
        ]


@functools.cache
def _default_ocr_engine() -> OcrEngine:
    """Load and cache the optional default OCR model set."""
    return OcrEngine()


# The package re-exports these classes; reprs, pickles, and documentation name them.
OcrEngine.__module__ = "pylopdf"
OcrWord.__module__ = "pylopdf"
