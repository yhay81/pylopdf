"""CJK fallback fonts for pylopdf.

This data-only package bundles Noto Sans JP and Noto Serif JP under SIL OFL 1.1
from https://github.com/notofonts/noto-cjk. pylopdf discovers it automatically
to render PDFs that reference Japanese fonts without embedding them.
"""

from __future__ import annotations

from pathlib import Path

__version__ = "0.1.0"
__all__ = ["sans_path", "serif_path"]

_BASE = Path(__file__).parent


def sans_path() -> Path:
    """Return the path to Noto Sans JP."""
    return _BASE / "NotoSansJP-Regular.otf"


def serif_path() -> Path:
    """Return the path to Noto Serif JP."""
    return _BASE / "NotoSerifJP-Regular.otf"
