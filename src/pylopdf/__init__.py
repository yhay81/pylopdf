"""Rust 製の PDF 編集・レンダリングライブラリ。

pymupdf に似た操作感の :class:`Document` を提供する。編集は lopdf、
レンダリングは hayro が担い、どちらも純 Rust・MIT/Apache ライセンス。
"""

from __future__ import annotations

import enum
import functools
import math
import os
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, overload

from pylopdf.pylopdf_core import PasswordError, PdfError, _Document

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence
    from types import TracebackType
    from typing import Any, Literal, Self

    #: get_text("words") の 1 要素: (x0, y0, x1, y1, 語, ブロック番号, 行番号, 語番号)
    WordEntry = tuple[float, float, float, float, str, int, int, int]
    #: get_text("blocks") の 1 要素: (x0, y0, x1, y1, テキスト, ブロック番号, 種別=0)
    BlockEntry = tuple[float, float, float, float, str, int, int]

__version__ = "0.6.0"
__all__ = [
    "Document",
    "DocumentClosedError",
    "EncryptedDocumentError",
    "Page",
    "PasswordError",
    "PdfError",
    "Permissions",
    "Rect",
    "StalePageError",
    "open",
    "peek_metadata",
]


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
        try:
            x0, y0, x1, y1 = (float(v) for v in rect)
        except (TypeError, ValueError) as exc:
            msg = f"{key} は 4 つの数値 (x0, y0, x1, y1) で指定してください: {rect!r}"
            raise ValueError(msg) from exc
        if not all(map(math.isfinite, (x0, y0, x1, y1))) or x0 >= x1 or y0 >= y1:
            msg = f"{key} は x0 < x1, y0 < y1 の有限な矩形で指定してください: {rect!r}"
            raise ValueError(msg)
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

    def search_for(self, needle: str) -> list[Rect]:
        """ページ内のテキスト検索（大文字小文字を区別しない）。

        ヒットごとの :class:`Rect` を返す。行単位で検索するため、
        行をまたぐ一致は検出しない。
        """
        if not needle:
            msg = "needle は 1 文字以上で指定してください"
            raise ValueError(msg)
        return [Rect(*hit) for hit in self._document._doc.search_page(self._page_number(), needle)]

    def get_images(self) -> list[dict[str, Any]]:
        """ページ上に描画される画像を抽出する。

        各要素は ``{"width", "height", "bbox", "ext", "image"}`` の辞書。
        DCTDecode 単独の画像は元の JPEG バイト列をそのまま返し（``ext="jpeg"``）、
        それ以外（CCITT / JBIG2 / Flate 等）はデコードして PNG 化する（``ext="png"``）。
        bbox はページ上の描画位置（左上原点の :class:`Rect`）。
        """
        return [
            {"width": width, "height": height, "bbox": Rect(*bbox), "ext": ext, "image": data}
            for width, height, bbox, ext, data in self._document._doc.extract_images(self._page_number())
        ]

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
        （信頼できない PDF の解凍爆弾対策。None で無制限）。
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
            return self._doc.extract_text([self._lopdf_page_number(pno)])
        width, height, blocks = self._doc.extract_layout(self._lopdf_page_number(pno))
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
                                        "font": "",
                                        "text": text,
                                    }
                                    for span_bbox, text, size, origin in spans
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
        if not (math.isfinite(width) and math.isfinite(height)) or width <= 0 or height <= 0:
            msg = f"width / height は正の有限値で指定してください: ({width!r}, {height!r})"
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
        return self._doc.render_page_png(page_number, scale, rgba)

    def render_page_svg(self, pno: int) -> str:
        """ページ pno（0 始まり）を SVG 文字列にレンダリングする。"""
        page_number = self._lopdf_page_number(pno)
        self._ensure_fallback_fonts()
        return self._doc.render_page_svg(page_number)

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
