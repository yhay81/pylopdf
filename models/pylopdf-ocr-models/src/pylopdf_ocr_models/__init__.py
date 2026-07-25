"""PP-OCRv6 small model paths for pylopdf's optional OCR engine."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

__version__ = "0.1.0"
__all__ = ["ModelPaths", "model_paths"]

_BASE = Path(__file__).parent


class ModelPaths(NamedTuple):
    """Paths to one compatible detector, recognizer, and dictionary set."""

    detector: Path
    recognizer: Path
    dictionary: Path


def model_paths() -> ModelPaths:
    """Return paths to the bundled PP-OCRv6 small model set."""
    return ModelPaths(
        detector=_BASE / "PP-OCRv6_det_small.rten",
        recognizer=_BASE / "PP-OCRv6_rec_small.rten",
        dictionary=_BASE / "ppocrv6_dict.txt",
    )
