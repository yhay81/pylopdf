"""Hebrew generation fonts for pylopdf."""

from __future__ import annotations

from pathlib import Path

__version__ = "0.1.0"
__all__ = ["sans_path", "serif_path"]

_BASE = Path(__file__).parent


def sans_path() -> Path:
    """Return the path to Noto Sans Hebrew."""
    return _BASE / "NotoSansHebrew-Variable.ttf"


def serif_path() -> Path:
    """Return the path to Noto Serif Hebrew."""
    return _BASE / "NotoSerifHebrew-Variable.ttf"
