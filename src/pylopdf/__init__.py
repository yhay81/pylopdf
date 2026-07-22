"""Rust 製の PDF 編集・レンダリングライブラリ。

pymupdf に似た操作感の :class:`Document` を提供する。編集は lopdf、
レンダリングは hayro が担い、どちらも純 Rust・MIT/Apache ライセンス。
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import TYPE_CHECKING

from pylopdf.pylopdf_core import _Document

if TYPE_CHECKING:
    import os
    from collections.abc import Iterable
    from types import TracebackType
    from typing import Self

__version__ = "0.4.1"
__all__ = ["Document", "open"]


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
        password: str | None = None,
    ) -> None:
        """filename（パス）か stream（バイト列)のどちらか一方から開く。両方 None なら空ドキュメント。

        暗号化 PDF は user password 空なら自動復号される。それ以外は password を
        指定するか、開いた後に :meth:`authenticate` を呼ぶ。
        """
        if filename is not None and stream is not None:
            msg = "filename と stream は同時に指定できません"
            raise ValueError(msg)
        # authenticate() での開き直しに使うため、開いた元を保持する
        path = None if filename is None else str(filename)
        self._source_path = path
        self._source_bytes = stream
        if stream is not None:
            self._doc = (
                _Document.load_bytes(stream)
                if password is None
                else _Document.load_bytes_with_password(stream, password)
            )
        elif path is not None:
            self._doc = _Document.load(path) if password is None else _Document.load_with_password(path, password)
        else:
            self._doc = _Document()
        self._closed = False
        self._fallback_configured = False
        # 「この PDF はパスワードを要するか」。password 指定で開けた場合も、
        # 元が暗号化されていたなら True のままにする（pymupdf の needs_pass と同じ意味論）
        self._needs_pass = self._doc.is_encrypted() or (password is not None and self._doc.was_encrypted())

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
            self._doc = _Document.load_with_password(self._source_path, password)
        elif self._source_bytes is not None:
            self._doc = _Document.load_bytes_with_password(self._source_bytes, password)
        return code

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

    def select(self, page_numbers: Iterable[int]) -> None:
        """指定した 0 始まりのページ番号だけを、指定順で残す。

        並べ替えにも使える。同一ページの重複指定（複製）は未対応。
        """
        self._doc.select([self._lopdf_page_number(pno) for pno in page_numbers])

    def insert_pdf(self, other: Document) -> None:
        """別ドキュメントの全ページを末尾に取り込む。"""
        self._ensure_open()
        if other is self:
            msg = "自分自身は挿入できません"
            raise ValueError(msg)
        other._ensure_open()
        self._doc.merge(other._doc)

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

    def render_page(self, pno: int, scale: float = 1.0) -> bytes:
        """ページ pno（0 始まり）を PNG 画像にレンダリングする。

        scale は拡大率（1.0 = 72dpi 相当）。
        """
        self._ensure_fallback_fonts()
        return self._doc.render_page_png(self._lopdf_page_number(pno), scale)

    def render_page_svg(self, pno: int) -> str:
        """ページ pno（0 始まり）を SVG 文字列にレンダリングする。"""
        self._ensure_fallback_fonts()
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

    def _ensure_not_closed(self) -> None:
        """閉じたドキュメントへの操作を防ぐ。"""
        if self._closed:
            msg = "document closed"
            raise ValueError(msg)

    def _ensure_open(self) -> None:
        """閉じた・未復号のドキュメントへの操作を防ぐ。

        未復号のまま操作すると「0 ページの空文書」に見えてしまうため、
        暗号化が解けていない場合は明確なエラーにする。
        """
        self._ensure_not_closed()
        if self._doc.is_encrypted():
            msg = "暗号化された PDF です。password 引数を付けて開くか authenticate() を呼んでください"
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
    password: str | None = None,
) -> Document:
    """:class:`Document` を開く。``pylopdf.open(...)`` は ``Document(...)`` と同じ。"""
    return Document(filename=filename, stream=stream, password=password)
