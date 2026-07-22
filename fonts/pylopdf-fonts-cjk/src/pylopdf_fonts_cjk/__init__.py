"""pylopdf 用の CJK（日本語）fallback フォント。

Noto Sans JP / Noto Serif JP（SIL OFL 1.1、https://github.com/notofonts/noto-cjk）を
同梱するデータ専用パッケージ。pylopdf がインストール時に自動検出して、
フォント非埋め込みの日本語 PDF のレンダリングに使う。
"""

from __future__ import annotations

from pathlib import Path

__version__ = "0.1.0"
__all__ = ["sans_path", "serif_path"]

_BASE = Path(__file__).parent


def sans_path() -> Path:
    """Noto Sans JP（ゴシック体）のフォントファイルパスを返す。"""
    return _BASE / "NotoSansJP-Regular.otf"


def serif_path() -> Path:
    """Noto Serif JP（明朝体）のフォントファイルパスを返す。"""
    return _BASE / "NotoSerifJP-Regular.otf"
