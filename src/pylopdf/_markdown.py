"""``get_text("dict")`` のレイアウトから Markdown を組み立てる（to_markdown の実装）。

初版の方針:

- 本文サイズ = 文字量が最も多いフォントサイズ（0.1 pt 丸め）。
  それより十分大きいサイズを、大きい順に見出しレベル 1..4 へ割り当てる
- 行の連結は「CJK 文字同士なら空白を入れない」（日本語の折り返しを壊さない）
- 行頭の箇条書き記号（・ • ● など）と「1.」「1)」を Markdown のリストへ正規化
- スパンの flags（埋め込みフォントの weight / italic メタデータ由来）から本文の
  太字・斜体を強調マーカーへ変換する（見出し行は # と二重にしないためプレーンのまま）。
  標準 14 フォント（Type1）は hayro がメタデータを公開しないため対象外
- 表・多段組の読み順・縦書きは未対応（ROADMAP の将来テーマ）
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

#: 見出しとみなす最小のサイズ比（本文サイズに対して）
_HEADING_RATIO = 1.15
#: 見出しレベルの最大数
_MAX_HEADING_LEVELS = 4
#: 直後に空白があれば箇条書きとみなす行頭記号（ASCII のダッシュ類を含む）
_SPACED_BULLETS = "・•●○◦▪‣–—-*"
#: 空白なしでも箇条書きとみなす行頭記号（CJK 文書では記号直後に空白が無いことが多い)
_TIGHT_BULLETS = "・•●○◦▪‣"
#: 「1.」「23)」形式の番号付きリスト
_NUMBERED = re.compile(r"^(\d{1,3})[.)][ 　]+")


def _round_size(size: float) -> float:
    return round(size, 1)


def collect_sizes(layouts: list[dict[str, Any]]) -> Counter[float]:
    """ページ dict 群から（丸めサイズ → 文字数）を集計する。"""
    counter: Counter[float] = Counter()
    for layout in layouts:
        for block in layout["blocks"]:
            for line in block["lines"]:
                for span in line["spans"]:
                    counter[_round_size(span["size"])] += len(span["text"])
    return counter


def heading_levels(counter: Counter[float]) -> dict[float, int]:
    """見出しサイズ → レベル（1..4）の対応を決める。本文サイズ以下は含まれない。"""
    if not counter:
        return {}
    body = counter.most_common(1)[0][0]
    bigger = sorted((size for size in counter if size > body * _HEADING_RATIO), reverse=True)
    return {size: min(rank + 1, _MAX_HEADING_LEVELS) for rank, size in enumerate(bigger)}


#: 行連結で空白を入れない文字の Unicode 範囲
#: （CJK 記号・かな / CJK 統合漢字（拡張 A 含む）/ CJK 互換漢字 / 全角形・半角カナ）
_CJK_RANGES = ((0x3000, 0x30FF), (0x3400, 0x9FFF), (0xF900, 0xFAFF), (0xFF00, 0xFFEF))


def _is_cjk(ch: str) -> bool:
    """行連結で空白を入れない文字（CJK・かな・全角形）か。"""
    code = ord(ch)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def _join_lines(lines: list[str]) -> str:
    """段落内の行を連結する（CJK 同士は空白なし、それ以外は空白 1 つ）。"""
    out = lines[0]
    for line in lines[1:]:
        if out and line and _is_cjk(out[-1]) and _is_cjk(line[0]):
            out += line
        else:
            out += " " + line
    return out


#: スパン flags のビット（pymupdf 互換: italic=2, serif=4, monospace=8, bold=16）
_ITALIC = 2
_BOLD = 16


def _line_text(line: dict[str, Any]) -> str:
    return "".join(span["text"] for span in line["spans"]).strip()


def _span_markdown(span: dict[str, Any]) -> str:
    """スパンを太字・斜体マーカー付きの Markdown 片にする（前後の空白は外に出す）。"""
    text: str = span["text"]
    flags = int(span.get("flags", 0))
    bold = bool(flags & _BOLD)
    italic = bool(flags & _ITALIC)
    core = text.strip()
    if not core or not (bold or italic):
        return text
    marker = "***" if bold and italic else ("**" if bold else "*")
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()) :]
    return f"{lead}{marker}{core}{marker}{trail}"


def _line_markdown(line: dict[str, Any]) -> str:
    """行を太字・斜体マーカー付きで組み立てる（段落本文用）。"""
    return "".join(_span_markdown(span) for span in line["spans"]).strip()


def _line_size(line: dict[str, Any]) -> float:
    """行の代表サイズ（文字量が最も多いスパンサイズ）。"""
    sizes: Counter[float] = Counter()
    for span in line["spans"]:
        sizes[_round_size(span["size"])] += len(span["text"])
    return sizes.most_common(1)[0][0] if sizes else 0.0


def _normalize_list_item(text: str) -> str | None:
    """箇条書き行なら Markdown のリスト項目に正規化して返す（違えば None）。"""
    if text[:1] and text[0] in _SPACED_BULLETS and text[1:2] in (" ", "　"):
        return "- " + text[2:].lstrip(" 　")
    if text[:1] and text[0] in _TIGHT_BULLETS:
        return "- " + text[1:].lstrip(" 　")
    matched = _NUMBERED.match(text)
    if matched:
        return f"{matched.group(1)}. " + text[matched.end() :]
    return None


def page_to_markdown(layout: dict[str, Any], levels: dict[float, int]) -> str:
    """1 ページ分の dict レイアウトを Markdown にする。"""
    # (種別, テキスト): 種別は h=見出し / li=リスト項目 / p=段落
    entries: list[tuple[str, str]] = []
    for block in layout["blocks"]:
        paragraph: list[str] = []

        def flush(paragraph: list[str] = paragraph) -> None:
            if paragraph:
                entries.append(("p", _join_lines(paragraph)))
                paragraph.clear()

        for line in block["lines"]:
            text = _line_text(line)
            if not text:
                continue
            level = levels.get(_line_size(line))
            if level is not None:
                # 見出しはプレーンテキスト（# と強調マーカーの二重化を避ける）
                flush()
                entries.append(("h", "#" * level + " " + text))
                continue
            item = _normalize_list_item(text)
            if item is not None:
                flush()
                entries.append(("li", item))
                continue
            paragraph.append(_line_markdown(line))
        flush()

    # 連続するリスト項目は 1 つのリストにまとめる（間に空行を入れない）
    chunks: list[str] = []
    previous = ""
    for kind, text in entries:
        if kind == "li" and previous == "li":
            chunks[-1] += "\n" + text
        else:
            chunks.append(text)
        previous = kind
    return "\n\n".join(chunks)
