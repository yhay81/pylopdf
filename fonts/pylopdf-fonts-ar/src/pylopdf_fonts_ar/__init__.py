"""Arabic generation fonts for pylopdf."""

from __future__ import annotations

from pathlib import Path

__version__ = "0.1.0"
__all__ = ["sans_path", "serif_path"]

_BASE = Path(__file__).parent


def sans_path() -> Path:
    """Return the path to Noto Sans Arabic."""
    return _BASE / "NotoSansArabic-Variable.ttf"


def serif_path() -> Path:
    """Return the path to Noto Naskh Arabic."""
    return _BASE / "NotoNaskhArabic-Variable.ttf"
