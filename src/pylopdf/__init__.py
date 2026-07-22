"""Rust 製の PDF 編集・レンダリングライブラリ。

pymupdf に似た操作感の :class:`Document` を提供する。編集は lopdf、
レンダリングは hayro が担い、どちらも純 Rust・MIT/Apache ライセンス。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pylopdf.pylopdf_core import _Document

if TYPE_CHECKING:
    import os
    from collections.abc import Iterable
    from types import TracebackType
    from typing import Self

__version__ = "0.3.0"
__all__ = ["Document", "open"]

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


class Document:
    """PDF ドキュメント。

    ファイルパスかバイト列から開くか、引数なしで空ドキュメントを作る。
    コンテキストマネージャとしても使える。
    """

    def __init__(
        self,
        filename: str | os.PathLike[str] | None = None,
        stream: bytes | None = None,
    ) -> None:
        """filename（パス）か stream（バイト列)のどちらか一方から開く。両方 None なら空ドキュメント。"""
        if filename is not None and stream is not None:
            msg = "filename と stream は同時に指定できません"
            raise ValueError(msg)
        if stream is not None:
            self._doc = _Document.load_bytes(stream)
        elif filename is not None:
            self._doc = _Document.load(str(filename))
        else:
            self._doc = _Document()
        self._closed = False

    @property
    def page_count(self) -> int:
        """ページ数。"""
        self._ensure_open()
        return self._doc.page_count()

    def __len__(self) -> int:
        """ページ数を返す。"""
        return self.page_count

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
        for key, value in metadata.items():
            pdf_key = _METADATA_KEYS.get(key)
            if pdf_key is None:
                msg = f"不明なメタデータキー: {key!r}（有効: {sorted(_METADATA_KEYS)}）"
                raise ValueError(msg)
            self._doc.set_metadata(pdf_key, value)

    def get_page_text(self, pno: int) -> str:
        """ページ pno（0 始まり）のテキストを抽出する。"""
        return self._doc.extract_text([self._lopdf_page_number(pno)])

    def delete_page(self, pno: int) -> None:
        """ページ pno（0 始まり）を削除する。"""
        self._doc.delete_pages([self._lopdf_page_number(pno)])

    def delete_pages(self, page_numbers: Iterable[int]) -> None:
        """複数ページ（0 始まり）をまとめて削除する。"""
        self._doc.delete_pages([self._lopdf_page_number(pno) for pno in page_numbers])

    def insert_pdf(self, other: Document) -> None:
        """別ドキュメントの全ページを末尾に取り込む。"""
        self._ensure_open()
        if other is self:
            msg = "自分自身は挿入できません"
            raise ValueError(msg)
        other._ensure_open()
        self._doc.merge(other._doc)

    def render_page(self, pno: int, scale: float = 1.0) -> bytes:
        """ページ pno（0 始まり）を PNG 画像にレンダリングする。

        scale は拡大率（1.0 = 72dpi 相当）。
        """
        return self._doc.render_page_png(self._lopdf_page_number(pno), scale)

    def render_page_svg(self, pno: int) -> str:
        """ページ pno（0 始まり）を SVG 文字列にレンダリングする。"""
        return self._doc.render_page_svg(self._lopdf_page_number(pno))

    def save(self, filename: str | os.PathLike[str]) -> None:
        """ファイルへ保存する。"""
        self._ensure_open()
        self._doc.save(str(filename))

    def tobytes(self) -> bytes:
        """PDF をバイト列で返す。"""
        self._ensure_open()
        return self._doc.save_bytes()

    def close(self) -> None:
        """ドキュメントを閉じる。以後の操作は ValueError になる。"""
        self._closed = True

    def _ensure_open(self) -> None:
        """閉じたドキュメントへの操作を防ぐ。"""
        if self._closed:
            msg = "document closed"
            raise ValueError(msg)

    def _lopdf_page_number(self, pno: int) -> int:
        """0 始まりのページ番号を検証し、lopdf の 1 始まりへ変換する。"""
        self._ensure_open()
        count = self._doc.page_count()
        if not 0 <= pno < count:
            msg = f"ページ番号 {pno} は範囲外です（0..{count - 1}）"
            raise IndexError(msg)
        return pno + 1

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
) -> Document:
    """:class:`Document` を開く。``pylopdf.open(...)`` は ``Document(...)`` と同じ。"""
    return Document(filename=filename, stream=stream)
