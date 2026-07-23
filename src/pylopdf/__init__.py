"""Rust 製の PDF 編集・レンダリングライブラリ。

pymupdf に似た操作感の :class:`Document` を提供する。編集は lopdf、
レンダリングは hayro が担い、どちらも純 Rust・MIT/Apache ライセンス。
"""

from __future__ import annotations

import enum
import functools
import math
import os
import warnings as _warnings
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, overload

from pylopdf import _markdown
from pylopdf.pylopdf_core import PasswordError, PdfError, Pixmap, _Document

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence
    from types import TracebackType
    from typing import Any, Literal, Self

    #: get_text("words") の 1 要素: (x0, y0, x1, y1, 語, ブロック番号, 行番号, 語番号)
    WordEntry = tuple[float, float, float, float, str, int, int, int]
    #: get_text("blocks") の 1 要素: (x0, y0, x1, y1, テキスト, ブロック番号, 種別=0)
    BlockEntry = tuple[float, float, float, float, str, int, int]

__version__ = "0.9.0"
__all__ = [
    "LINK_GOTO",
    "LINK_GOTOR",
    "LINK_LAUNCH",
    "LINK_NAMED",
    "LINK_NONE",
    "LINK_URI",
    "Document",
    "DocumentClosedError",
    "EncryptedDocumentError",
    "Page",
    "PasswordError",
    "PdfError",
    "Permissions",
    "Point",
    "Rect",
    "StalePageError",
    "open",
    "peek_metadata",
]
__all__ += ["Pixmap", "PylopdfWarning"]

# リンク種別（pymupdf 互換の値）
LINK_NONE = 0
LINK_GOTO = 1
LINK_URI = 2
LINK_LAUNCH = 3
LINK_NAMED = 4
LINK_GOTOR = 5


class PylopdfWarning(UserWarning):
    """レンダリング・抽出中に hayro が報告した警告（フォント未解決・画像デコード失敗）。"""


class Permissions(enum.IntFlag):
    """暗号化 PDF の許可フラグ（save の permissions 引数へ ``|`` で組み合わせて渡す）。

    値は PDF 仕様の /P ビット位置に対応する。
    """

    PRINT = 1 << 2
    MODIFY = 1 << 3
    COPY = 1 << 4
    ANNOTATE = 1 << 5
    FILL_FORMS = 1 << 8
    COPY_FOR_ACCESSIBILITY = 1 << 9
    ASSEMBLE = 1 << 10
    PRINT_HIGH_QUALITY = 1 << 11
    ALL = PRINT | MODIFY | COPY | ANNOTATE | FILL_FORMS | COPY_FOR_ACCESSIBILITY | ASSEMBLE | PRINT_HIGH_QUALITY


class DocumentClosedError(PdfError):
    """閉じた Document への操作。"""


class EncryptedDocumentError(PdfError):
    """未復号の暗号化 PDF への操作。password 引数か authenticate() で復号する。"""


class StalePageError(PdfError):
    """文書構造の変更（ページの追加・削除・並べ替え）後に古い Page を使った。

    ``doc[i]`` で取得し直すこと。
    """


class Point(NamedTuple):
    """表示座標の点（x, y）。get_links の宛先 to などで使う。"""

    x: float
    y: float


class Rect(NamedTuple):
    """PDF 座標の矩形（x0, y0, x1, y1）。"""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        """幅（x1 - x0）。"""
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        """高さ（y1 - y0）。"""
        return self.y1 - self.y0


#: A4 縦（PDF 単位）。MediaBox の無い壊れた PDF での既定値（hayro のレンダリングと同じ想定）。
_DEFAULT_MEDIABOX = (0.0, 0.0, 210.0 * 72.0 / 25.4, 297.0 * 72.0 / 25.4)


@functools.cache
def _bundled_cjk_fonts() -> tuple[tuple[str, bytes], ...]:
    """pylopdf-fonts-cjk（``pip install pylopdf[cjk]``）があれば同梱フォントを読み込む。"""
    try:
        import pylopdf_fonts_cjk  # noqa: PLC0415  # optional 依存の遅延 import
    except ImportError:
        return ()
    return (
        ("sans", pylopdf_fonts_cjk.sans_path().read_bytes()),
        ("serif", pylopdf_fonts_cjk.serif_path().read_bytes()),
    )


#: 色成分（R/G/B/A）の最大値。
_COLOR_MAX = 255

#: lopdf の PDF 実数（f32）で有限のまま表現できる絶対値上限。
_FLOAT32_MAX = float.fromhex("0x1.fffffep+127")


def _normalize_background(
    background: tuple[int, int, int] | tuple[int, int, int, int] | None,
) -> tuple[int, int, int, int] | None:
    """render_page の background 引数を検証し、RGBA の 4 タプルへ正規化する。"""
    if background is None:
        return None
    match background:
        case (r, g, b):
            rgba = (r, g, b, _COLOR_MAX)
        case (r, g, b, a):
            rgba = (r, g, b, a)
        case _:
            msg = f"background は (R, G, B) か (R, G, B, A) のタプルで指定してください: {background!r}"
            raise ValueError(msg)
    for value in rgba:
        if not isinstance(value, int) or not 0 <= value <= _COLOR_MAX:
            msg = f"background の各成分は 0〜{_COLOR_MAX} の整数で指定してください: {background!r}"
            raise ValueError(msg)
    return rgba


def _validate_rect(rect: Sequence[float], *, name: str = "rect") -> tuple[float, float, float, float]:
    """(x0, y0, x1, y1) の矩形引数を検証して float タプルにする。"""
    try:
        x0, y0, x1, y1 = (float(v) for v in rect)
    except (TypeError, ValueError) as exc:
        msg = f"{name} は 4 つの数値 (x0, y0, x1, y1) で指定してください: {rect!r}"
        raise ValueError(msg) from exc
    if not all(math.isfinite(v) and abs(v) <= _FLOAT32_MAX for v in (x0, y0, x1, y1)) or x0 >= x1 or y0 >= y1:
        msg = f"{name} は x0 < x1, y0 < y1 で PDF 実数の範囲内にある有限な矩形で指定してください: {rect!r}"
        raise ValueError(msg)
    return x0, y0, x1, y1


def _validate_unit_rgb(color: Sequence[float]) -> tuple[float, float, float]:
    """0〜1 の (r, g, b) を検証して float タプルにする。"""
    try:
        red, green, blue = (float(c) for c in color)
    except (TypeError, ValueError) as exc:
        msg = f"color は 0〜1 の (r, g, b) で指定してください: {color!r}"
        raise ValueError(msg) from exc
    if not all(0.0 <= c <= 1.0 for c in (red, green, blue)):
        msg = f"color は 0〜1 の (r, g, b) で指定してください: {color!r}"
        raise ValueError(msg)
    return red, green, blue


def _read_image_source(filename: str | os.PathLike[str] | None, stream: bytes | None) -> bytes:
    """filename / stream のどちらか一方から画像バイト列を得る。"""
    if filename is not None:
        if stream is not None:
            msg = "filename と stream は同時に指定できません"
            raise ValueError(msg)
        return Path(filename).read_bytes()
    if stream is None:
        msg = "filename か stream のどちらかを指定してください"
        raise ValueError(msg)
    return bytes(stream)


#: insert_text の fontname 略名 → PDF 標準 14 フォントの正式名（pymupdf と同じ略名）
_BASE14_FONTS: dict[str, str] = {
    "helv": "Helvetica",
    "hebo": "Helvetica-Bold",
    "heit": "Helvetica-Oblique",
    "hebi": "Helvetica-BoldOblique",
    "cour": "Courier",
    "cobo": "Courier-Bold",
    "coit": "Courier-Oblique",
    "cobi": "Courier-BoldOblique",
    "tiro": "Times-Roman",
    "tibo": "Times-Bold",
    "tiit": "Times-Italic",
    "tibi": "Times-BoldItalic",
    "symb": "Symbol",
    "zadb": "ZapfDingbats",
}

#: 組み込みエンコーディングを使うべき標準フォント（WinAnsi を指定しない）
_SYMBOLIC_FONTS = frozenset({"symb", "zadb"})


#: ページラベルの番号スタイル（PDF 仕様の /S 値。空文字列は「番号なし = prefix のみ」）
_PAGE_LABEL_STYLES = frozenset({"", "D", "R", "r", "A", "a"})


def _int_to_roman(n: int) -> str:
    """1 以上の整数をローマ数字（大文字）にする。"""
    pairs = (
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    )
    out = []
    for value, symbol in pairs:
        count, n = divmod(n, value)
        out.append(symbol * count)
    return "".join(out)


def _int_to_letters(n: int) -> str:
    """1 以上の整数を A..Z, AA..ZZ, AAA... 形式（PDF 仕様の反復式）にする。"""
    letter = chr(ord("A") + (n - 1) % 26)
    return letter * ((n - 1) // 26 + 1)


def _format_page_label(style: str, prefix: str, number: int) -> str:
    """ページラベル 1 件分の表示文字列を組み立てる。"""
    match style:
        case "D":
            digits = str(number)
        case "R":
            digits = _int_to_roman(number)
        case "r":
            digits = _int_to_roman(number).lower()
        case "A":
            digits = _int_to_letters(number)
        case "a":
            digits = _int_to_letters(number).lower()
        case _:
            digits = ""
    return prefix + digits


#: Python 側メタデータキー → PDF Info 辞書キーの対応（pymupdf と同じキー名）
_METADATA_KEYS: dict[str, str] = {
    "title": "Title",
    "author": "Author",
    "subject": "Subject",
    "keywords": "Keywords",
    "creator": "Creator",
    "producer": "Producer",
    "creationDate": "CreationDate",
    "modDate": "ModDate",
}


class Page:
    """ドキュメント内の 1 ページへのビュー。``doc[i]`` で取得する。

    ページの追加・削除・並べ替えを行うと、それ以前に取得した Page は無効になり、
    使うと :class:`StalePageError` になる。``doc[i]`` で取得し直すこと。
    """

    def __init__(self, document: Document, pno: int) -> None:
        """``Document.__getitem__`` から呼ばれる。直接構築しない。"""
        self._document = document
        self._pno = pno
        self._generation = document._generation

    @property
    def number(self) -> int:
        """0 始まりのページ番号。"""
        return self._pno

    @property
    def parent(self) -> Document:
        """このページが属する Document。"""
        return self._document

    def _page_number(self) -> int:
        """有効性を検証し、lopdf の 1 始まりページ番号を返す。"""
        doc = self._document
        doc._ensure_open()
        if self._generation != doc._generation:
            msg = f"ページ {self._pno} は文書構造の変更で無効になりました。doc[{self._pno}] で取得し直してください"
            raise StalePageError(msg)
        return self._pno + 1

    @property
    def rotation(self) -> int:
        """ページの表示回転角（0 / 90 / 180 / 270。継承解決済み）。"""
        return self._document._doc.get_page_rotation(self._page_number())

    def set_rotation(self, rotation: int) -> None:
        """表示回転角を設定する（90 の倍数。負値・360 以上は 0..360 に正規化）。"""
        if rotation % 90 != 0:
            msg = f"rotation は 90 の倍数で指定してください: {rotation!r}"
            raise ValueError(msg)
        self._document._doc.set_page_rotation(self._page_number(), rotation % 360)

    @property
    def mediabox(self) -> Rect:
        """MediaBox（継承解決済み。無い場合は A4 相当）。"""
        box = self._document._doc.get_page_box(self._page_number(), "MediaBox")
        return Rect(*(box if box is not None else _DEFAULT_MEDIABOX))

    @property
    def cropbox(self) -> Rect:
        """CropBox（無い場合は MediaBox と同じ値）。"""
        box = self._document._doc.get_page_box(self._page_number(), "CropBox")
        return Rect(*box) if box is not None else self.mediabox

    @property
    def rect(self) -> Rect:
        """表示上のページ矩形（原点 0,0。回転 90/270 で幅と高さが入れ替わる）。"""
        box = self.cropbox
        if self.rotation in (90, 270):
            return Rect(0.0, 0.0, box.height, box.width)
        return Rect(0.0, 0.0, box.width, box.height)

    def set_mediabox(self, rect: Sequence[float]) -> None:
        """MediaBox を (x0, y0, x1, y1) で設定する。"""
        self._set_box("MediaBox", rect)

    def set_cropbox(self, rect: Sequence[float]) -> None:
        """CropBox を (x0, y0, x1, y1) で設定する。"""
        self._set_box("CropBox", rect)

    def _set_box(self, key: str, rect: Sequence[float]) -> None:
        """ボックス引数を検証して設定する。"""
        x0, y0, x1, y1 = _validate_rect(rect, name=key)
        self._document._doc.set_page_box(self._page_number(), key, x0, y0, x1, y1)

    @overload
    def get_text(self, option: Literal["text"] = "text") -> str: ...
    @overload
    def get_text(self, option: Literal["words"]) -> list[WordEntry]: ...
    @overload
    def get_text(self, option: Literal["blocks"]) -> list[BlockEntry]: ...
    @overload
    def get_text(self, option: Literal["dict"]) -> dict[str, Any]: ...
    def get_text(self, option: str = "text") -> str | list[WordEntry] | list[BlockEntry] | dict[str, Any]:
        """ページのテキスト（または位置付きレイアウト）を抽出する。

        option は :meth:`Document.get_page_text` と同じ（"text" / "words" / "blocks" / "dict"）。
        """
        self._page_number()
        return self._document.get_page_text(self._pno, option)  # type: ignore[call-overload]

    def to_markdown(self) -> str:
        """このページを Markdown へ変換する（:meth:`Document.to_markdown` の 1 ページ版）。

        見出しサイズの推定もこのページ内だけで行う。
        """
        self._page_number()
        return self._document.to_markdown(pages=[self._pno])

    def search_for(self, needle: str) -> list[Rect]:
        """ページ内のテキスト検索（大文字小文字を区別しない）。

        ヒットごとの :class:`Rect` を返す。行単位で検索するため、
        行をまたぐ一致は検出しない。
        """
        if not needle:
            msg = "needle は 1 文字以上で指定してください"
            raise ValueError(msg)
        hits = self._document._doc.search_page(self._page_number(), needle)
        self._document._emit_warnings()
        return [Rect(*hit) for hit in hits]

    def get_images(self) -> list[dict[str, Any]]:
        """ページ上に描画される画像を抽出する。

        各要素は ``{"width", "height", "bbox", "ext", "image"}`` の辞書。
        DCTDecode 単独の画像は元の JPEG バイト列をそのまま返し（``ext="jpeg"``）、
        それ以外（CCITT / JBIG2 / Flate 等）はデコードして PNG 化する（``ext="png"``）。
        bbox はページ上の描画位置（左上原点の :class:`Rect`）。
        """
        raw = self._document._doc.extract_images(self._page_number())
        self._document._emit_warnings()
        return [
            {"width": width, "height": height, "bbox": Rect(*bbox), "ext": ext, "image": data}
            for width, height, bbox, ext, data in raw
        ]

    def insert_image(
        self,
        rect: Sequence[float],
        *,
        filename: str | os.PathLike[str] | None = None,
        stream: bytes | None = None,
        keep_proportion: bool = True,
        overlay: bool = True,
    ) -> None:
        """rect（表示座標、左上原点）へ画像を描き込む。

        JPEG は再圧縮せずそのまま埋め込み（DCTDecode パススルー）、PNG は展開して
        埋め込む（透過はソフトマスクとして保持）。他形式は Pillow 等で JPEG / PNG に
        変換してから渡すこと。rect は :meth:`search_for` / :meth:`get_text` と同じ
        表示座標系なので、検索結果の位置へそのまま描ける。keep_proportion なら
        縦横比を保って rect 内へ中央合わせで収める。overlay=False で既存コンテンツの
        下に描く。既存のページコンテンツには一切手を入れない（追記のみ）。
        """
        data = _read_image_source(filename, stream)
        x0, y0, x1, y1 = _validate_rect(rect)
        self._document._doc.insert_image(self._page_number(), (x0, y0, x1, y1), data, keep_proportion, overlay)

    def show_pdf_page(
        self,
        rect: Sequence[float],
        src: Document,
        pno: int = 0,
        *,
        keep_proportion: bool = True,
        overlay: bool = True,
    ) -> None:
        """別ドキュメント src のページ pno（0 始まり、負数可）をベクタのまま rect へ重ねる。

        透かし・スタンプ・レターヘッドの焼き込みに使う（pymupdf の show_pdf_page 相当）。
        取り込み元ページは Form XObject として埋め込まれるため、テキストやベクタは
        劣化せず、フォント埋め込みも保たれる。typst 等で組んだ 1 ページ PDF を
        全ページへ重ねれば、日本語の透かしやヘッダ / フッタも描ける
        （README のエコシステム連携を参照）。src の回転・CropBox は表示上の見た目で
        rect へ収まるよう解決される。src が self と同じドキュメントの場合は未対応
        （``pylopdf.open(stream=doc.tobytes())`` で複製してから渡すこと）。
        """
        if src is self._document:
            msg = "同一ドキュメントのページは重ねられません（open(stream=doc.tobytes()) で複製してから渡してください）"
            raise ValueError(msg)
        x0, y0, x1, y1 = _validate_rect(rect)
        src_number = src[pno]._page_number()
        self._document._doc.show_pdf_page(
            self._page_number(), (x0, y0, x1, y1), src._doc, src_number, keep_proportion, overlay
        )

    def insert_text(
        self,
        point: Sequence[float],
        text: str,
        *,
        fontsize: float = 11.0,
        fontname: str = "helv",
        color: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> None:
        r"""point（表示座標。1 行目のベースライン左端）へテキストを印字する。

        fontname は PDF 標準 14 フォントの略名（pymupdf と同じ: "helv" / "tiro" /
        "cour" 系 + 太字 bo / 斜体 it、"symb"、"zadb"）。フォントは埋め込まず、
        ビューア側の標準書体で表示される。文字は WinAnsi（Latin-1 相当）の範囲のみで、
        日本語などの CJK は印字できない — typst で組んで :meth:`show_pdf_page` で
        焼くレシピを使うこと（README のエコシステム連携）。``\n`` で複数行になり、
        行送りは fontsize の 1.2 倍。回転ページでも表示上で正立する。
        ヘッダ / フッタ / ページ番号 / Bates 番号はこれをループで印字する。
        """
        try:
            x, y = (float(v) for v in point)
        except (TypeError, ValueError) as exc:
            msg = f"point は (x, y) の 2 つの数値で指定してください: {point!r}"
            raise ValueError(msg) from exc
        if not (math.isfinite(x) and math.isfinite(y)):
            msg = f"point は有限の座標で指定してください: {point!r}"
            raise ValueError(msg)
        if not (math.isfinite(fontsize) and fontsize > 0):
            msg = f"fontsize は正の数値で指定してください: {fontsize!r}"
            raise ValueError(msg)
        base_font = _BASE14_FONTS.get(fontname)
        if base_font is None:
            msg = f"fontname は標準 14 フォントの略名で指定してください（{sorted(_BASE14_FONTS)}）: {fontname!r}"
            raise ValueError(msg)
        red, green, blue = _validate_unit_rgb(color)
        if not text:
            msg = "text は 1 文字以上で指定してください"
            raise ValueError(msg)
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        try:
            lines = [line.encode("cp1252") for line in normalized.split("\n")]
        except UnicodeEncodeError as exc:
            msg = (
                "insert_text は WinAnsi（Latin-1 相当）の文字だけ印字できます。"
                "日本語などの CJK は typst + show_pdf_page のレシピを使ってください（README のエコシステム連携）"
            )
            raise ValueError(msg) from exc
        self._document._doc.insert_page_text(
            self._page_number(),
            (x, y),
            lines,
            base_font,
            fontname not in _SYMBOLIC_FONTS,
            float(fontsize),
            (red, green, blue),
        )

    def insert_ocr_text_layer(self, words: Iterable[Sequence[Any]]) -> None:
        """OCR 結果を不可視テキスト層として書き込む（searchable PDF 化）。

        words の各要素は ``(x0, y0, x1, y1, テキスト, ...)`` — 先頭 5 要素だけを
        使うので、:meth:`get_text` の "words" 形式や一般的な OCR API の出力を
        そのまま渡せる。座標は表示空間（左上原点）。テキストは描画されず、
        抽出・検索にだけ現れる。フォント実体は埋め込まない（Identity-H +
        ToUnicode の参照フォント）ため、日本語を含むどの言語でもファイル
        サイズをほぼ増やさない。どの OCR エンジン（クラウド API / Tesseract 等）の
        結果とも組める中立プリミティブ。
        """
        payload: list[tuple[float, float, float, float, str]] = []
        for entry in words:
            x0, y0, x1, y1 = _validate_rect(entry[:4])
            text = str(entry[4])
            if text:
                payload.append((x0, y0, x1, y1, text))
        if not payload:
            msg = "words には少なくとも 1 つのテキスト付きの語が必要です"
            raise ValueError(msg)
        self._document._doc.insert_ocr_layer(self._page_number(), payload)

    def replace_text(self, search: str, replacement: str, *, default_char: str | None = None) -> int:
        """ページ内のテキストを置換し、置換した箇所数を返す。

        lopdf の部分置換（replace_partial_text）の薄い公開で、制約が多い:
        単純エンコーディング（WinAnsi 等）のフォントだけが対象で、CID フォント
        （日本語等の CJK）には効かない。置換後の文字がフォントに無い場合は
        default_char（既定 "?"）へ落ちる。文字幅は再計算しないため、長さの違う
        置換ではレイアウトがずれることがある。
        """
        if not search:
            msg = "search は 1 文字以上で指定してください"
            raise ValueError(msg)
        return self._document._doc.replace_text_on_page(self._page_number(), search, replacement, default_char)

    def get_label(self) -> str:
        """このページの表示ラベル（"iv" や "A-2" など）を返す。定義が無ければ空文字列。"""
        pno = self._page_number() - 1
        applicable: dict[str, Any] | None = None
        for label in self._document.get_page_labels():
            if label["startpage"] <= pno:
                applicable = label
            else:
                break
        if applicable is None:
            return ""
        number = pno - applicable["startpage"] + applicable["firstpagenum"]
        return _format_page_label(applicable["style"], applicable["prefix"], number)

    def annots(self) -> list[dict[str, Any]]:
        """ページの注釈を読み取る。

        各要素は ``{"type", "rect", "contents", "uri"}`` の辞書。type は PDF の
        Subtype 名（"Highlight" / "Link" など）、rect は表示座標の :class:`Rect`、
        contents は注釈本文、uri は URI アクションのリンク先（無ければ None）。
        """
        raw = self._document._doc.read_annotations(self._page_number())
        return [
            {"type": subtype, "rect": Rect(*rect), "contents": contents, "uri": uri}
            for subtype, rect, contents, uri in raw
        ]

    def get_links(self) -> list[dict[str, Any]]:
        """ページのリンク注釈を宛先解決付きで読み取る。

        各要素は pymupdf 風の辞書で、共通キーは ``kind``（:data:`LINK_GOTO` などの
        定数）と ``from``（表示座標の :class:`Rect`）。kind に応じて追加キーを持つ:

        - ``LINK_URI``: ``uri``
        - ``LINK_GOTO``: ``page``（0 始まり。解決できなければ -1）、あれば
          ``to``（宛先ページ表示座標の :class:`Point`）/ ``zoom`` / ``nameddest``
        - ``LINK_GOTOR`` / ``LINK_LAUNCH``: ``file``（あれば ``nameddest`` も）
        - ``LINK_NAMED``: ``name``（NextPage などのアクション名）

        GoTo の named destination は /Names の名前ツリーと旧式の /Dests 辞書の
        両方から解決する。
        """
        raw = self._document._doc.read_links(self._page_number())
        kind_map = {
            "uri": LINK_URI,
            "goto": LINK_GOTO,
            "gotor": LINK_GOTOR,
            "launch": LINK_LAUNCH,
            "named": LINK_NAMED,
        }
        links: list[dict[str, Any]] = []
        for kind, rect, uri, page, to, zoom, file, name in raw:
            link: dict[str, Any] = {
                "kind": kind_map.get(kind, LINK_NONE),
                "from": Rect(*rect),
            }
            if kind == "uri":
                link["uri"] = uri
            elif kind == "goto":
                link["page"] = page - 1 if page is not None else -1
                if to is not None:
                    link["to"] = Point(*to)
                if zoom is not None:
                    link["zoom"] = zoom
                if name is not None:
                    link["nameddest"] = name
            elif kind in ("gotor", "launch"):
                link["file"] = file
                if name is not None:
                    link["nameddest"] = name
            elif kind == "named":
                link["name"] = name
            links.append(link)
        return links

    def add_highlight_annot(
        self,
        rects: Sequence[float] | Sequence[Sequence[float]],
        *,
        color: tuple[float, float, float] = (1.0, 1.0, 0.0),
        opacity: float = 0.4,
        content: str | None = None,
    ) -> None:
        """rects（表示座標。単一の矩形か矩形のリスト）へハイライト注釈を付ける。

        :meth:`search_for` の結果をそのまま渡せば「検索してマーカー」になる。
        QuadPoints に加えて外観ストリーム（AP、Multiply ブレンド）も生成するため、
        pylopdf 自身のレンダリングを含むどのビューアでも同じ見た目で表示される。
        複数の矩形は 1 つの注釈にまとまる。content は注釈本文（ポップアップ）。
        """
        seq = list(rects)
        if not seq:
            msg = "rects は 1 つ以上の矩形で指定してください"
            raise ValueError(msg)
        rect_list = [seq] if isinstance(seq[0], (int, float)) else seq
        validated = [_validate_rect(r) for r in rect_list]  # type: ignore[arg-type]
        rgb = _validate_unit_rgb(color)
        if not (math.isfinite(opacity) and 0.0 < opacity <= 1.0):
            msg = f"opacity は 0 より大きく 1 以下で指定してください: {opacity!r}"
            raise ValueError(msg)
        self._document._doc.add_highlight_annotation(self._page_number(), validated, rgb, float(opacity), content)

    def add_link_annot(self, rect: Sequence[float], uri: str) -> None:
        """rect（表示座標）へ URI リンク注釈を付ける（枠線なし）。

        :meth:`search_for` の結果へ「検索してリンク」する用途を想定。
        新規文書のリンクは typst 側で組む方が自然（README のエコシステム連携）。
        """
        if not uri:
            msg = "uri は 1 文字以上で指定してください"
            raise ValueError(msg)
        x0, y0, x1, y1 = _validate_rect(rect)
        self._document._doc.add_link_annotation(self._page_number(), (x0, y0, x1, y1), uri)

    def get_pixmap(
        self,
        scale: float = 1.0,
        *,
        dpi: float | None = None,
        background: tuple[int, int, int] | tuple[int, int, int, int] | None = None,
    ) -> Pixmap:
        """ページを :class:`Pixmap`（ストレートアルファ RGBA8）にレンダリングする。

        引数は :meth:`Document.render_page` と同じ。得られる Pixmap は
        ``width`` / ``height`` / ``stride`` / ``n`` / ``samples``（bytes）と
        ``tobytes()``（PNG）を持ち、``np.frombuffer(pix.samples, np.uint8)``
        ``.reshape(pix.height, pix.width, 4)`` で NumPy 配列にできる。
        """
        if dpi is not None:
            if scale != 1.0:
                msg = "scale と dpi は同時に指定できません"
                raise ValueError(msg)
            scale = dpi / 72.0
        rgba = _normalize_background(background)
        page_number = self._page_number()
        document = self._document
        document._ensure_fallback_fonts()
        result = document._doc.render_page_pixmap(page_number, scale, rgba)
        document._emit_warnings()
        return result

    def render(
        self,
        scale: float = 1.0,
        *,
        dpi: float | None = None,
        background: tuple[int, int, int] | tuple[int, int, int, int] | None = None,
    ) -> bytes:
        """ページを PNG にレンダリングする。引数は :meth:`Document.render_page` と同じ。"""
        self._page_number()
        return self._document.render_page(self._pno, scale, dpi=dpi, background=background)

    def render_svg(self) -> str:
        """ページを SVG 文字列にレンダリングする。"""
        self._page_number()
        return self._document.render_page_svg(self._pno)

    def __repr__(self) -> str:
        """ページ番号と所属ドキュメントを含む表現を返す。"""
        return f"<Page {self._pno} of {self._document!r}>"


class Document:
    """PDF ドキュメント。

    ファイルパスかバイト列から開くか、引数なしで空ドキュメントを作る。
    コンテキストマネージャとしても使え、``doc[i]`` / イテレーションで
    :class:`Page` を取得できる。
    """

    def __init__(
        self,
        filename: str | os.PathLike[str] | None = None,
        stream: bytes | None = None,
        password: str | None = None,
        max_decompressed_size: int | None = None,
    ) -> None:
        """filename（パス）か stream（バイト列)のどちらか一方から開く。両方 None なら空ドキュメント。

        暗号化 PDF は user password 空なら自動復号される。それ以外は password を
        指定するか、開いた後に :meth:`authenticate` を呼ぶ。
        max_decompressed_size は 1 ストリームあたりの展開上限バイト数
        （信頼できない PDF の解凍爆弾対策。None で無制限）。ページ内容など
        遅延展開されるストリームもロード時に検証し、安全に上限を判定できない
        フィルタ構成は拒否する。
        """
        if filename is not None and stream is not None:
            msg = "filename と stream は同時に指定できません"
            raise ValueError(msg)
        if max_decompressed_size is not None and max_decompressed_size <= 0:
            msg = f"max_decompressed_size は正の整数で指定してください: {max_decompressed_size!r}"
            raise ValueError(msg)
        path = None if filename is None else str(filename)
        self._max_decompressed_size = max_decompressed_size
        if stream is not None:
            doc = _Document.load_bytes(stream, None, max_decompressed_size)
            needs_pass = doc.is_encrypted()
            if needs_pass and password is not None:
                doc = _Document.load_bytes(stream, password, max_decompressed_size)
        elif path is not None:
            doc = _Document.load(path, None, max_decompressed_size)
            needs_pass = doc.is_encrypted()
            if needs_pass and password is not None:
                doc = _Document.load(path, password, max_decompressed_size)
        else:
            doc = _Document()
            needs_pass = False
        self._doc = doc
        self._closed = False
        self._fallback_configured = False
        # ページ構造の世代番号。構造変更で増え、古い Page ビューを無効化する
        self._generation = 0
        # password なしでは復号できなかったかを保持する（認証後も True のまま）
        self._needs_pass = needs_pass
        # authenticate() で開き直す必要がある未復号ドキュメントだけ、開いた元を保持する
        self._source_path = path if self._doc.is_encrypted() else None
        self._source_bytes = stream if self._doc.is_encrypted() else None

    @property
    def needs_pass(self) -> bool:
        """この PDF を開くのにパスワードが必要か（認証後も True のまま）。"""
        self._ensure_not_closed()
        return self._needs_pass

    @property
    def is_encrypted(self) -> bool:
        """まだ復号されていない（認証が必要な）状態か。認証に成功すると False になる。"""
        self._ensure_not_closed()
        return self._doc.is_encrypted()

    def authenticate(self, password: str) -> int:
        """パスワードで認証して復号する。

        戻り値は pymupdf 互換: 0 = 失敗、1 = 認証不要、2 = user password が一致、
        4 = owner password が一致、6 = 両方に一致。
        """
        self._ensure_not_closed()
        if not self._doc.is_encrypted():
            return 1
        code = 0
        if self._doc.authenticate_user_password(password):
            code |= 2
        if self._doc.authenticate_owner_password(password):
            code |= 4
        if code == 0:
            return 0
        # オブジェクトストリーム内のオブジェクトも読めるよう、パスワード付きで開き直す
        if self._source_path is not None:
            self._doc = _Document.load(self._source_path, password, self._max_decompressed_size)
        elif self._source_bytes is not None:
            self._doc = _Document.load_bytes(self._source_bytes, password, self._max_decompressed_size)
        self._source_path = None
        self._source_bytes = None
        return code

    @property
    def page_count(self) -> int:
        """ページ数。"""
        self._ensure_open()
        return self._doc.page_count()

    def __len__(self) -> int:
        """ページ数を返す。"""
        return self.page_count

    def __getitem__(self, pno: int) -> Page:
        """0 始まり（負数は末尾から）のページ番号で :class:`Page` を取得する。"""
        return Page(self, self._normalize_pno(pno))

    def load_page(self, pno: int) -> Page:
        """``doc[pno]`` と同じ（pymupdf 互換名）。"""
        return self[pno]

    def __iter__(self) -> Iterator[Page]:
        """全ページを先頭から順に返す。"""
        for pno in range(self.page_count):
            yield self[pno]

    def _bump_generation(self) -> None:
        """ページ構造の変更を記録し、取得済みの Page ビューを無効化する。"""
        self._generation += 1

    @property
    def metadata(self) -> dict[str, str]:
        """メタデータ辞書（title, author, subject, keywords, creator, producer, creationDate, modDate, format）。"""
        self._ensure_open()
        raw = self._doc.get_metadata()
        result = {key: raw.get(pdf_key, "") for key, pdf_key in _METADATA_KEYS.items()}
        result["format"] = f"PDF {self._doc.version()}"
        return result

    def set_metadata(self, metadata: dict[str, str]) -> None:
        """メタデータを設定する。値が空文字列の項目は削除する。

        キーは :attr:`metadata` と同じ（format は読み取り専用のため不可）。
        """
        self._ensure_open()
        updates: list[tuple[str, str]] = []
        for key, value in metadata.items():
            pdf_key = _METADATA_KEYS.get(key)
            if pdf_key is None:
                msg = f"不明なメタデータキー: {key!r}（有効: {sorted(_METADATA_KEYS)}）"
                raise ValueError(msg)
            if not isinstance(value, str):
                msg = f"メタデータ値は文字列で指定してください: {key!r}={value!r}"
                raise TypeError(msg)
            updates.append((pdf_key, value))
        for pdf_key, value in updates:
            self._doc.set_metadata(pdf_key, value)

    def to_markdown(self, pages: Iterable[int] | None = None) -> str:
        """文書を Markdown へ変換する（RAG / LLM 前処理向けの初版）。

        見出しはフォントサイズから推定する（文字量最頻のサイズ = 本文、
        それより大きいサイズを大きい順に ``#``..``####`` へ）。日本語の
        行折り返しは空白を入れずに連結し、行頭の箇条書き記号（・• など）と
        「1.」「1)」はリストへ正規化する。スキャン PDF も
        :meth:`Page.insert_ocr_text_layer` で層を書いてあれば変換できる。
        未対応: 太字・斜体（フォント名情報なし）、表、多段組の読み順、縦書き。
        pages は 0 始まりのページ番号列（None で全ページ。指定順に出力）。
        """
        self._ensure_open()
        page_numbers = range(self.page_count) if pages is None else pages
        layouts = [self.get_page_text(pno, "dict") for pno in page_numbers]
        levels = _markdown.heading_levels(_markdown.collect_sizes(layouts))
        rendered = (_markdown.page_to_markdown(layout, levels) for layout in layouts)
        return "\n\n".join(md for md in rendered if md)

    def get_form_fields(self) -> list[dict[str, Any]]:
        """AcroForm フィールドの一覧を返す。

        各要素は ``{"name", "type", "value"}``。name はネストを ``.`` で
        連結した完全名、type は text / checkbox / radio / button / combobox /
        listbox / signature、value は現在値（チェックボックスは "Yes"/"Off" 等の
        状態名。未設定なら None）。
        """
        self._ensure_open()
        return [{"name": name, "type": kind, "value": value} for name, kind, value in self._doc.get_form_fields()]

    def set_form_field(self, name: str, value: str | bool) -> None:  # noqa: FBT001  # pymupdf 同様に bool 値を受ける
        """AcroForm フィールドに値を設定する（記入）。

        テキスト / 選択フィールドは文字列を、チェックボックス / ラジオは状態名
        （多くは "Yes" / "Off"）か bool を渡す（True はウィジェットの外観から
        on 状態名を自動解決、False は "Off"）。外観ストリームは再生成せず
        NeedAppearances を立てるので、値の見た目はビューア側が描画する
        （pylopdf 自身のレンダリングには現れない点に注意）。
        署名フィールドへの記入は未対応（電子署名は pyHanko 連携を参照）。
        """
        self._ensure_open()
        if not name:
            msg = "name は 1 文字以上で指定してください"
            raise ValueError(msg)
        if isinstance(value, bool):
            if value:
                states = self._doc.form_button_states(name)
                on_states = [s for s in states if s != "Off"]
                resolved = on_states[0] if on_states else "Yes"
            else:
                resolved = "Off"
            self._doc.set_form_field(name, resolved)
            return
        self._doc.set_form_field(name, value)

    def get_page_labels(self) -> list[dict[str, Any]]:
        """ページラベル定義を読む。

        各要素は ``{"startpage", "style", "prefix", "firstpagenum"}``（startpage は
        0 始まり、style は "D"/"R"/"r"/"A"/"a" か空文字列 = prefix のみ）。
        個々のページの表示ラベルは :meth:`Page.get_label` で得る。
        """
        self._ensure_open()
        return [
            {
                "startpage": int(start),
                "style": style or "",
                "prefix": prefix or "",
                "firstpagenum": int(first),
            }
            for start, style, prefix, first in self._doc.get_page_labels()
        ]

    def set_page_labels(self, labels: Sequence[dict[str, Any]]) -> None:
        """ページラベルを設定する（:meth:`get_page_labels` と同じ形式。空リストで削除）。

        最初の範囲は startpage 0 から始まる必要がある（PDF 仕様）。
        firstpagenum は各範囲の開始番号（既定 1）。
        """
        self._ensure_open()
        payload: list[tuple[int, str | None, str | None, int]] = []
        seen: set[int] = set()
        for label in labels:
            start = int(label.get("startpage", -1))
            style = str(label.get("style", ""))
            prefix = str(label.get("prefix", ""))
            first = int(label.get("firstpagenum", 1))
            if start < 0 or start in seen:
                msg = f"startpage は 0 以上で重複なく指定してください: {label!r}"
                raise ValueError(msg)
            if style not in _PAGE_LABEL_STYLES:
                msg = f"style は {sorted(_PAGE_LABEL_STYLES)} のいずれかで指定してください: {style!r}"
                raise ValueError(msg)
            if first < 1:
                msg = f"firstpagenum は 1 以上で指定してください: {label!r}"
                raise ValueError(msg)
            seen.add(start)
            payload.append((start, style or None, prefix or None, first))
        if payload and min(seen) != 0:
            msg = "最初のページラベル範囲は startpage 0 から始まる必要があります（PDF 仕様）"
            raise ValueError(msg)
        payload.sort(key=lambda item: item[0])
        self._doc.set_page_labels(payload)

    def embfile_add(
        self,
        name: str,
        data: bytes,
        *,
        filename: str | None = None,
        desc: str | None = None,
    ) -> None:
        """添付ファイル（EmbeddedFiles）を追加する。

        name は一覧・取得に使うキー（同名が既にあればエラー）。filename は
        ビューアに表示されるファイル名（省略時は name）、desc は説明文。
        どちらも日本語可（UF / Desc へ UTF-16BE で入る)。請求書 PDF への
        XML 添付（ZUGFeRD / Factur-X 風の構成）などに使える。
        """
        self._ensure_open()
        if not name:
            msg = "name は 1 文字以上で指定してください"
            raise ValueError(msg)
        self._doc.embfile_add(name, bytes(data), filename, desc)

    def embfile_names(self) -> list[str]:
        """添付ファイル名の一覧を返す（ソート済み）。"""
        self._ensure_open()
        return self._doc.embfile_names()

    def embfile_get(self, name: str) -> bytes:
        """添付ファイルの中身（bytes）を取り出す。"""
        self._ensure_open()
        return self._doc.embfile_get(name)

    def embfile_del(self, name: str) -> None:
        """添付ファイルを削除する（無ければエラー）。"""
        self._ensure_open()
        self._doc.embfile_del(name)

    def get_pdfa_claim(self) -> tuple[int, str] | None:
        """XMP メタデータの PDF/A 宣言（pdfaid:part / conformance）を読み取る。

        戻り値は ``(part, conformance)``（例: ``(2, "B")`` = PDF/A-2b の宣言。
        conformance を持たない PDF/A-4 は空文字列）。宣言が無ければ None。
        これは自己申告の**読み取り**であって準拠の検証ではない — 本検証は
        veraPDF などの外部ツールへ（README のエコシステム連携を参照）。
        """
        self._ensure_open()
        claim = self._doc.pdfa_claim()
        return None if claim is None else (int(claim[0]), claim[1])

    @overload
    def get_page_text(self, pno: int, option: Literal["text"] = "text") -> str: ...
    @overload
    def get_page_text(self, pno: int, option: Literal["words"]) -> list[WordEntry]: ...
    @overload
    def get_page_text(self, pno: int, option: Literal["blocks"]) -> list[BlockEntry]: ...
    @overload
    def get_page_text(self, pno: int, option: Literal["dict"]) -> dict[str, Any]: ...
    def get_page_text(
        self, pno: int, option: str = "text"
    ) -> str | list[WordEntry] | list[BlockEntry] | dict[str, Any]:
        """ページ pno（0 始まり）のテキスト（または位置付きレイアウト）を抽出する。

        option は pymupdf 互換:

        - "text": プレーンテキスト（既定）
        - "words": (x0, y0, x1, y1, 語, ブロック番号, 行番号, 語番号) のリスト
        - "blocks": (x0, y0, x1, y1, テキスト, ブロック番号, 0) のリスト
        - "dict": width / height / blocks（lines → spans）の入れ子辞書

        座標は左上原点・下向き y。bbox の縦方向はベースライン ± フォントサイズ比の
        近似（実フォントメトリクスではない）。
        """
        if option == "text":
            text = self._doc.extract_text([self._lopdf_page_number(pno)])
            self._emit_warnings()
            return text
        width, height, blocks = self._doc.extract_layout(self._lopdf_page_number(pno))
        self._emit_warnings()
        if option == "words":
            words: list[WordEntry] = []
            for bno, (_, lines) in enumerate(blocks):
                for lno, (_, _, line_words) in enumerate(lines):
                    words.extend(
                        (x0, y0, x1, y1, text, bno, lno, wno) for wno, ((x0, y0, x1, y1), text) in enumerate(line_words)
                    )
            return words
        if option == "blocks":
            return [
                (
                    x0,
                    y0,
                    x1,
                    y1,
                    "\n".join(" ".join(text for _, text in line_words) for _, _, line_words in lines),
                    bno,
                    0,
                )
                for bno, ((x0, y0, x1, y1), lines) in enumerate(blocks)
            ]
        if option == "dict":
            return {
                "width": width,
                "height": height,
                "blocks": [
                    {
                        "number": bno,
                        "type": 0,
                        "bbox": bbox,
                        "lines": [
                            {
                                "bbox": line_bbox,
                                "wmode": 0,
                                "dir": (1.0, 0.0),
                                "spans": [
                                    {
                                        "bbox": span_bbox,
                                        "origin": origin,
                                        "size": size,
                                        "font": font,
                                        "flags": flags,
                                        "text": text,
                                    }
                                    for span_bbox, text, size, origin, font, flags in spans
                                ],
                            }
                            for line_bbox, spans, _ in lines
                        ],
                    }
                    for bno, (bbox, lines) in enumerate(blocks)
                ],
            }
        msg = f"option は 'text' / 'words' / 'blocks' / 'dict' のいずれかで指定してください: {option!r}"
        raise ValueError(msg)

    def delete_page(self, pno: int) -> None:
        """ページ pno（0 始まり、負数可）を削除する。"""
        page_number = self._lopdf_page_number(pno)
        self._bump_generation()
        self._doc.delete_pages([page_number])

    def delete_pages(self, page_numbers: Iterable[int]) -> None:
        """複数ページ（0 始まり、負数可）をまとめて削除する。"""
        self._ensure_open()
        numbers = [self._lopdf_page_number(pno) for pno in page_numbers]
        self._bump_generation()
        self._doc.delete_pages(numbers)

    def select(self, page_numbers: Iterable[int]) -> None:
        """指定した 0 始まりのページ番号だけを、指定順で残す。

        並べ替えにも使える。同一ページを複数回指定するとそのページを複製する
        （複製ページの Contents / Resources は元とオブジェクトを共有する）。
        """
        self._ensure_open()
        numbers = [self._lopdf_page_number(pno) for pno in page_numbers]
        self._bump_generation()
        self._doc.select(numbers)

    def insert_pdf(
        self,
        other: Document,
        from_page: int = 0,
        to_page: int = -1,
        start_at: int = -1,
    ) -> None:
        """別ドキュメントのページ範囲を取り込む。

        from_page..to_page（0 始まり・両端含む・負数は末尾から）を、
        start_at（0 始まりの挿入位置。-1 で末尾に追加）へ挿入する。
        from_page > to_page なら逆順で取り込む。
        """
        self._ensure_open()
        if other is self:
            msg = "自分自身は挿入できません"
            raise ValueError(msg)
        other._ensure_open()
        if other.page_count == 0:
            return
        start = other._normalize_pno(from_page)
        stop = other._normalize_pno(to_page)
        step = 1 if start <= stop else -1
        numbers = list(range(start, stop + step, step))
        position = None if start_at == -1 else self._insert_position(start_at, "start_at")
        self._bump_generation()
        self._doc.merge_pages(other._doc, [n + 1 for n in numbers], position)

    def new_page(self, pno: int = -1, width: float = 595.0, height: float = 842.0) -> Page:
        """空ページを挿入して、その :class:`Page` を返す。

        pno は挿入位置（0 始まり。-1 で末尾に追加）。width / height は
        ページサイズ（PDF 単位。既定は A4 縦 595x842）。
        """
        self._ensure_open()
        if (
            not (math.isfinite(width) and math.isfinite(height))
            or not (0 < width <= _FLOAT32_MAX)
            or not (0 < height <= _FLOAT32_MAX)
        ):
            msg = f"width / height は PDF 実数の範囲内にある正の有限値で指定してください: ({width!r}, {height!r})"
            raise ValueError(msg)
        if pno == -1:
            position = None
            index = self.page_count
        else:
            position = self._insert_position(pno, "pno")
            index = position
        self._bump_generation()
        self._doc.new_page(position, width, height)
        return self[index]

    def copy_page(self, pno: int, to: int = -1) -> None:
        """ページ pno（0 始まり、負数可）の複製を挿入位置 to（-1 で末尾）に追加する。

        複製ページの Contents / Resources は元ページとオブジェクトを共有する。
        """
        self._ensure_open()
        page_number = self._lopdf_page_number(pno)
        position = None if to == -1 else self._insert_position(to, "to")
        self._bump_generation()
        self._doc.copy_page(page_number, position)

    def _insert_position(self, value: int, name: str) -> int:
        """挿入位置（0..page_count。page_count は末尾追加と同義）を検証して返す。"""
        count = self.page_count
        if not 0 <= value <= count:
            msg = f"{name} {value} は範囲外です（0..{count} か -1）"
            raise IndexError(msg)
        return value

    def get_toc(self) -> list[list[int | str]]:
        """目次（しおり／アウトライン）を ``[[レベル, タイトル, ページ番号], ...]`` で返す。

        レベルは 1 始まりの階層。ページ番号は **1 始まり**（pymupdf 互換。
        0 始まりの他 API と異なるので注意）。目次が無ければ空リスト。
        """
        self._ensure_open()
        return [[level, title, page] for level, title, page in self._doc.get_toc()]

    def set_toc(self, toc: Sequence[Sequence[int | str]]) -> None:
        """目次を ``[[レベル, タイトル, ページ番号], ...]`` で置き換える。空で削除。

        レベルは 1 から始まり、直前の項目のレベル +1 までしか深くできない。
        ページ番号は 1 始まり（:meth:`get_toc` と対称）。
        """
        self._ensure_open()
        count = self.page_count
        entries: list[tuple[int, str, int]] = []
        previous_level = 0
        for i, item in enumerate(toc):
            try:
                level_raw, title, page_raw = item
                level = int(level_raw)
                page = int(page_raw)
            except (TypeError, ValueError) as exc:
                msg = f"toc[{i}] は [レベル, タイトル, ページ番号] の 3 要素で指定してください: {item!r}"
                raise ValueError(msg) from exc
            if level < 1 or level > previous_level + 1:
                msg = f"toc[{i}] のレベル {level} が不正です（1 以上かつ直前のレベル +1 まで）"
                raise ValueError(msg)
            if not 1 <= page <= count:
                msg = f"toc[{i}] のページ番号 {page} は範囲外です（1..{count}）"
                raise ValueError(msg)
            entries.append((level, str(title), page))
            previous_level = level
        self._doc.set_toc(entries)

    def set_fallback_font(
        self,
        font: bytes | str | os.PathLike[str] | None,
        kind: str = "sans",
        index: int = 0,
    ) -> None:
        """非埋め込み CJK フォントのレンダリングに使う代替フォントを設定する。

        font はフォントファイル（TTF/OTF/TTC）のパスかバイト列。None を渡すと
        設定を解除し、pylopdf[cjk] 同梱フォントの自動検出も無効化する。
        kind は "sans"（ゴシック系・既定）か "serif"（明朝系）、
        index は TTC 内の face 番号。
        """
        self._ensure_open()
        self._fallback_configured = True
        if font is None:
            self._doc.clear_fallback_fonts()
            return
        data = font if isinstance(font, bytes) else Path(font).read_bytes()
        self._doc.set_fallback_font(kind, data, index)

    def _ensure_fallback_fonts(self) -> None:
        """set_fallback_font 未使用なら、pylopdf[cjk] の同梱フォントを自動設定する。"""
        if self._fallback_configured:
            return
        self._fallback_configured = True
        for kind, data in _bundled_cjk_fonts():
            self._doc.set_fallback_font(kind, data, 0)

    def render_page(
        self,
        pno: int,
        scale: float = 1.0,
        *,
        dpi: float | None = None,
        background: tuple[int, int, int] | tuple[int, int, int, int] | None = None,
    ) -> bytes:
        """ページ pno（0 始まり）を PNG 画像にレンダリングする。

        scale は有限の正の拡大率（1.0 = 72dpi 相当）。dpi を指定すると解像度で
        指定できる（dpi=144 は scale=2.0 と同じ。scale との併用は不可）。
        background は背景色の (R, G, B) か (R, G, B, A)（各 0〜255）。
        省略時は透明背景。出力は1辺65,535ピクセル、総64,000,000画素まで。
        """
        if dpi is not None:
            if scale != 1.0:
                msg = "scale と dpi は同時に指定できません"
                raise ValueError(msg)
            scale = dpi / 72.0
        rgba = _normalize_background(background)
        page_number = self._lopdf_page_number(pno)
        self._ensure_fallback_fonts()
        result = self._doc.render_page_png(page_number, scale, rgba)
        self._emit_warnings()
        return result

    def render_page_svg(self, pno: int) -> str:
        """ページ pno（0 始まり）を SVG 文字列にレンダリングする。"""
        page_number = self._lopdf_page_number(pno)
        self._ensure_fallback_fonts()
        result = self._doc.render_page_svg(page_number)
        self._emit_warnings()
        return result

    def _emit_warnings(self) -> None:
        """直近の操作で hayro が出した警告を :class:`PylopdfWarning` として発行する。"""
        for message in self._doc.take_warnings():
            _warnings.warn(message, PylopdfWarning, stacklevel=3)

    def save(  # noqa: PLR0913  # 保存オプションはすべてキーワード専用（pymupdf 互換の形）
        self,
        filename: str | os.PathLike[str],
        *,
        garbage: bool = False,
        deflate: bool = False,
        object_streams: bool = False,
        user_pw: str | None = None,
        owner_pw: str | None = None,
        permissions: int = Permissions.ALL,
    ) -> None:
        """ファイルへ保存する。

        garbage=True は未参照オブジェクトの削除、deflate=True はストリームの
        Flate 圧縮を保存前に適用する（どちらもドキュメント自体に作用する）。
        object_streams=True は object stream + xref stream（PDF 1.5+ 形式）で
        書き出し、多くの PDF でファイルサイズを削減する（バージョンは必要に
        応じて 1.5 へ引き上げられる）。

        user_pw / owner_pw のどちらかを与えると AES-256（PDF 2.0）で暗号化して
        書き出す（このドキュメント自体は平文のまま）。owner_pw 省略時は user_pw と
        同じ。user_pw を空にして owner_pw だけ与えると「閲覧自由・権限制限のみ」の
        PDF になる。permissions は :class:`Permissions` の組み合わせ（既定は全許可）。
        暗号化と object_streams の併用は未対応。
        """
        self._ensure_open()
        encryption = self._encryption_args(user_pw, owner_pw, permissions, object_streams=object_streams)
        self._apply_save_options(garbage=garbage, deflate=deflate)
        if encryption is not None:
            user, owner, perms = encryption
            self._doc.save_encrypted(str(filename), user, owner, perms, os.urandom(32))
        elif object_streams:
            self._doc.save_with_object_streams(str(filename))
        else:
            self._doc.save(str(filename))

    def tobytes(  # noqa: PLR0913  # 保存オプションはすべてキーワード専用（pymupdf 互換の形）
        self,
        *,
        garbage: bool = False,
        deflate: bool = False,
        object_streams: bool = False,
        user_pw: str | None = None,
        owner_pw: str | None = None,
        permissions: int = Permissions.ALL,
    ) -> bytes:
        """PDF をバイト列で返す。オプションの意味は :meth:`save` と同じ。"""
        self._ensure_open()
        encryption = self._encryption_args(user_pw, owner_pw, permissions, object_streams=object_streams)
        self._apply_save_options(garbage=garbage, deflate=deflate)
        if encryption is not None:
            user, owner, perms = encryption
            return self._doc.save_bytes_encrypted(user, owner, perms, os.urandom(32))
        if object_streams:
            return self._doc.save_bytes_with_object_streams()
        return self._doc.save_bytes()

    def _apply_save_options(self, *, garbage: bool, deflate: bool) -> None:
        """保存前の最適化（未参照オブジェクト削除・ストリーム圧縮）を適用する。"""
        if garbage:
            self._doc.prune_objects()
        if deflate:
            self._doc.compress()

    @staticmethod
    def _encryption_args(
        user_pw: str | None,
        owner_pw: str | None,
        permissions: int,
        *,
        object_streams: bool,
    ) -> tuple[str, str, int] | None:
        """暗号化保存の引数を検証・正規化する。暗号化しないなら None。"""
        if user_pw is None and owner_pw is None:
            return None
        if object_streams:
            msg = "暗号化（user_pw / owner_pw）と object_streams は同時に指定できません"
            raise ValueError(msg)
        user = user_pw if user_pw is not None else ""
        owner = owner_pw if owner_pw is not None else user
        return (user, owner, int(permissions))

    def close(self) -> None:
        """ドキュメントを閉じる。以後の操作は ValueError になる。"""
        self._closed = True

    def _ensure_not_closed(self) -> None:
        """閉じたドキュメントへの操作を防ぐ。"""
        if self._closed:
            msg = "document closed"
            raise DocumentClosedError(msg)

    def _ensure_open(self) -> None:
        """閉じた・未復号のドキュメントへの操作を防ぐ。

        未復号のまま操作すると「0 ページの空文書」に見えてしまうため、
        暗号化が解けていない場合は明確なエラーにする。
        """
        self._ensure_not_closed()
        if self._doc.is_encrypted():
            msg = "暗号化された PDF です。password 引数を付けて開くか authenticate() を呼んでください"
            raise EncryptedDocumentError(msg)

    def _normalize_pno(self, pno: int) -> int:
        """負数（末尾から数える）を解決した 0 始まりのページ番号を返す。範囲外は IndexError。"""
        self._ensure_open()
        count = self._doc.page_count()
        normalized = pno + count if pno < 0 else pno
        if not 0 <= normalized < count:
            msg = f"ページ番号 {pno} は範囲外です（0..{count - 1}）"
            raise IndexError(msg)
        return normalized

    def _lopdf_page_number(self, pno: int) -> int:
        """0 始まり（負数可）のページ番号を検証し、lopdf の 1 始まりへ変換する。"""
        return self._normalize_pno(pno) + 1

    def __enter__(self) -> Self:
        """コンテキストマネージャの開始。自身を返す。"""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """コンテキストマネージャの終了時にドキュメントを閉じる。"""
        self.close()

    def __repr__(self) -> str:
        """開閉状態を含む表現を返す。"""
        state = "closed " if self._closed else ""
        return f"<{state}pylopdf.Document>"


def open(  # noqa: A001
    filename: str | os.PathLike[str] | None = None,
    stream: bytes | None = None,
    password: str | None = None,
    max_decompressed_size: int | None = None,
) -> Document:
    """:class:`Document` を開く。``pylopdf.open(...)`` は ``Document(...)`` と同じ。"""
    return Document(
        filename=filename,
        stream=stream,
        password=password,
        max_decompressed_size=max_decompressed_size,
    )


def peek_metadata(
    filename: str | os.PathLike[str] | None = None,
    stream: bytes | None = None,
    password: str | None = None,
) -> dict[str, str | int | bool]:
    """文書全体をパースせずに、メタデータとページ数だけを高速に読み取る。

    戻り値は :attr:`Document.metadata` と同じキー（title, author, subject,
    keywords, creator, producer, creationDate, modDate, format）に、
    page_count（int）と encrypted（bool）を加えた辞書。大量の PDF の走査に向く。
    """
    if (filename is None) == (stream is None):
        msg = "filename と stream はどちらか一方だけを指定してください"
        raise ValueError(msg)
    if stream is not None:
        raw, page_count, version, encrypted = _Document.load_metadata_bytes(stream, password)
    else:
        raw, page_count, version, encrypted = _Document.load_metadata(str(filename), password)
    result: dict[str, str | int | bool] = {key: raw.get(pdf_key, "") for key, pdf_key in _METADATA_KEYS.items()}
    result["format"] = f"PDF {version}"
    result["page_count"] = page_count
    result["encrypted"] = encrypted
    return result
