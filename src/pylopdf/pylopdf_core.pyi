# Type stubs for the Rust extension module pylopdf_core.
import os

class PdfError(ValueError): ...
class PasswordError(PdfError): ...

class LimitError(PdfError):
    code: str

class OcrError(PdfError): ...

class _OcrEngine:
    def __init__(
        self,
        detector_path: str,
        recognizer_path: str,
        dictionary_path: str,
        threads: int,
        max_model_size: int | None,
    ) -> None: ...
    def recognize_pixmap(
        self,
        pixmap: Pixmap,
        *,
        tile_size: int = 1408,
        overlap: int = 192,
        min_confidence: float = 0.5,
        rotation: int = 0,
    ) -> list[tuple[float, float, float, float, str, float]]: ...

class Pixmap:
    @property
    def width(self) -> int: ...
    @property
    def height(self) -> int: ...
    @property
    def n(self) -> int: ...
    @property
    def stride(self) -> int: ...
    @property
    def samples(self) -> bytes: ...
    def tobytes(self, *, max_size: int | None = 67108864) -> bytes: ...
    def save(self, path: str | os.PathLike[str]) -> None: ...

class _Document:
    def __init__(
        self,
        max_text_size: int | None = None,
        max_interpretation_size: int | None = None,
    ) -> None: ...
    @staticmethod
    def load(
        path: str,
        password: str | None = None,
        max_decompressed_size: int | None = None,
        max_page_content_size: int | None = None,
        max_file_size: int | None = None,
        max_pages: int | None = None,
        max_objects: int | None = None,
        max_total_decompressed_size: int | None = None,
        max_object_depth: int | None = None,
        max_text_size: int | None = None,
        max_interpretation_size: int | None = None,
    ) -> _Document: ...
    @staticmethod
    def load_bytes(
        data: bytes,
        password: str | None = None,
        max_decompressed_size: int | None = None,
        max_page_content_size: int | None = None,
        max_file_size: int | None = None,
        max_pages: int | None = None,
        max_objects: int | None = None,
        max_total_decompressed_size: int | None = None,
        max_object_depth: int | None = None,
        max_text_size: int | None = None,
        max_interpretation_size: int | None = None,
    ) -> _Document: ...
    @staticmethod
    def load_metadata(
        path: str,
        password: str | None = None,
        max_file_size: int | None = None,
    ) -> tuple[dict[str, str], int, str, bool, bool]: ...
    @staticmethod
    def load_metadata_bytes(
        data: bytes,
        password: str | None = None,
        max_file_size: int | None = None,
    ) -> tuple[dict[str, str], int, str, bool, bool]: ...
    def is_encrypted(self) -> bool: ...
    def is_repaired(self) -> bool: ...
    def was_encrypted(self) -> bool: ...
    def set_fallback_font(self, kind: str, data: bytes, index: int, max_font_size: int | None = 67108864) -> None: ...
    def set_fallback_font_file(
        self, kind: str, path: str, index: int, max_font_size: int | None = 67108864
    ) -> None: ...
    def set_fallback_font_files(
        self, sans_path: str, serif_path: str, max_font_size: int | None = 67108864
    ) -> None: ...
    def clear_fallback_fonts(self) -> None: ...
    def authenticate_user_password(self, password: str) -> bool: ...
    def authenticate_owner_password(self, password: str) -> bool: ...
    def save(self, path: str) -> None: ...
    def save_bytes(self, max_size: int | None = None) -> bytes: ...
    def save_with_object_streams(self, path: str) -> None: ...
    def save_bytes_with_object_streams(self, max_size: int | None = None) -> bytes: ...
    def save_encrypted(
        self, path: str, user_password: str, owner_password: str, permissions: int, file_encryption_key: bytes
    ) -> None: ...
    def save_bytes_encrypted(
        self,
        user_password: str,
        owner_password: str,
        permissions: int,
        file_encryption_key: bytes,
        max_size: int | None = None,
    ) -> bytes: ...
    def get_toc(self) -> list[tuple[int, str, int]]: ...
    def set_toc(self, entries: list[tuple[int, str, int]]) -> None: ...
    def page_count(self) -> int: ...
    def complexity(self) -> tuple[int, int, int, int, int]: ...
    def version(self) -> str: ...
    def get_metadata(self) -> dict[str, str]: ...
    def pdfa_claim(self, max_size: int | None) -> tuple[int, str] | None: ...
    def get_form_fields(self) -> list[tuple[str, str, str | None]]: ...
    def form_button_states(self, name: str) -> list[str]: ...
    def set_form_field(
        self,
        name: str,
        value: str,
        font_data: bytes | None = None,
        font_index: int = 0,
        max_font_size: int | None = 67108864,
    ) -> None: ...
    def set_form_field_file(
        self,
        name: str,
        value: str,
        path: str,
        font_index: int = 0,
        max_font_size: int | None = 67108864,
    ) -> None: ...
    def get_page_labels(self) -> list[tuple[int, str | None, str | None, int]]: ...
    def set_page_labels(self, labels: list[tuple[int, str | None, str | None, int]]) -> None: ...
    def embfile_add(
        self,
        name: str,
        data: bytes,
        filename: str | None,
        desc: str | None,
        max_size: int | None = 67108864,
    ) -> None: ...
    def embfile_names(self) -> list[str]: ...
    def embfile_get(self, name: str, max_size: int | None) -> bytes: ...
    def embfile_del(self, name: str) -> None: ...
    def set_metadata(self, key: str, value: str) -> None: ...
    def set_metadata_batch(self, entries: list[tuple[str, str]]) -> None: ...
    def delete_pages(self, page_numbers: list[int]) -> None: ...
    def get_page_rotation(self, page_number: int) -> int: ...
    def set_page_rotation(self, page_number: int, rotation: int) -> None: ...
    def get_page_box(self, page_number: int, key: str) -> tuple[float, float, float, float] | None: ...
    def set_page_box(self, page_number: int, key: str, x0: float, y0: float, x1: float, y1: float) -> None: ...
    def select(self, page_numbers: list[int]) -> None: ...
    def extract_text(self, page_numbers: list[int]) -> str: ...
    def extract_layout(
        self, page_number: int
    ) -> tuple[
        float,
        float,
        list[
            tuple[
                tuple[float, float, float, float],
                list[
                    tuple[
                        tuple[float, float, float, float],
                        list[tuple[tuple[float, float, float, float], str, float, tuple[float, float], str, int]],
                        list[tuple[tuple[float, float, float, float], str]],
                        tuple[float, float],
                        int,
                    ]
                ],
            ]
        ],
    ]: ...
    def find_tables(
        self,
        page_number: int,
        strategy: str,
        clip: tuple[float, float, float, float] | None = None,
    ) -> list[
        tuple[
            tuple[float, float, float, float],
            int,
            int,
            list[tuple[tuple[float, float, float, float], str] | None],
            list[int],
            tuple[float, float | None, float | None, float | None],
        ]
    ]: ...
    def search_page(
        self,
        page_number: int,
        needle: str,
        max_hits: int | None = 4096,
    ) -> list[tuple[float, float, float, float]]: ...
    def extract_images(
        self, page_number: int
    ) -> list[tuple[int, int, tuple[float, float, float, float], str, bytes]]: ...
    def extract_drawings(
        self, page_number: int
    ) -> list[
        tuple[
            tuple[float, float, float, float],
            str,
            list[tuple[str, list[tuple[float, float]]]],
            bool,
            tuple[
                tuple[float, float, float] | None,
                float | None,
                float | None,
                tuple[int, int, int] | None,
                int | None,
                str | None,
            ],
            tuple[tuple[float, float, float] | None, float | None, bool | None],
        ]
    ]: ...
    def compress_images(self, target_dpi: float | None, quality: int) -> tuple[int, int, int, int, int]: ...
    def render_page_pixmap(
        self,
        page_number: int,
        scale: float,
        background: tuple[int, int, int, int] | None,
        clip: tuple[float, float, float, float] | None,
    ) -> Pixmap: ...
    def take_warnings(self) -> list[str]: ...
    def insert_image(
        self,
        page_number: int,
        rect: tuple[float, float, float, float],
        data: bytes,
        image_rotation: int,
        keep_proportion: bool,
        overlay: bool,
        max_size: int | None = 67108864,
        max_pixels: int | None = 64000000,
    ) -> None: ...
    def insert_image_file(
        self,
        page_number: int,
        rect: tuple[float, float, float, float],
        path: str,
        image_rotation: int,
        keep_proportion: bool,
        overlay: bool,
        max_size: int | None = 67108864,
        max_pixels: int | None = 64000000,
    ) -> None: ...
    def insert_pixmap(
        self,
        page_number: int,
        rect: tuple[float, float, float, float],
        pixmap: Pixmap,
        image_rotation: int,
        keep_proportion: bool,
        overlay: bool,
    ) -> None: ...
    def show_pdf_page(
        self,
        page_number: int,
        rect: tuple[float, float, float, float],
        other: _Document,
        src_page_number: int,
        keep_proportion: bool,
        overlay: bool,
    ) -> None: ...
    def show_pdf_page_self(
        self,
        page_number: int,
        rect: tuple[float, float, float, float],
        src_page_number: int,
        keep_proportion: bool,
        overlay: bool,
    ) -> None: ...
    def read_annotations(
        self, page_number: int
    ) -> list[tuple[str, tuple[float, float, float, float], str | None, str | None]]: ...
    def read_links(
        self, page_number: int
    ) -> list[
        tuple[
            str,
            tuple[float, float, float, float],
            str | None,
            int | None,
            tuple[float, float] | None,
            float | None,
            str | None,
            str | None,
        ]
    ]: ...
    def add_highlight_annotation(
        self,
        page_number: int,
        rects: list[tuple[float, float, float, float]],
        color: tuple[float, float, float],
        opacity: float,
        content: str | None,
    ) -> None: ...
    def add_link_annotation(self, page_number: int, rect: tuple[float, float, float, float], uri: str) -> None: ...
    def insert_ocr_layer(
        self,
        page_number: int,
        words: list[tuple[float, float, float, float, str]],
        text_rotation: int = 0,
    ) -> None: ...
    def insert_page_text(
        self,
        page_number: int,
        point: tuple[float, float],
        lines: list[bytes],
        base_font: str,
        winansi: bool,
        fontsize: float,
        color: tuple[float, float, float],
        overlay: bool,
        max_text_size: int | None = 1048576,
    ) -> None: ...
    def insert_embedded_text(
        self,
        page_number: int,
        point: tuple[float, float],
        lines: list[str],
        font_data: bytes,
        font_index: int,
        fontsize: float,
        color: tuple[float, float, float],
        overlay: bool,
        max_font_size: int | None = 67108864,
        max_text_size: int | None = 1048576,
    ) -> None: ...
    def insert_embedded_text_file(
        self,
        page_number: int,
        point: tuple[float, float],
        lines: list[str],
        path: str,
        font_index: int,
        fontsize: float,
        color: tuple[float, float, float],
        overlay: bool,
        max_font_size: int | None = 67108864,
        max_text_size: int | None = 1048576,
    ) -> None: ...
    def insert_page_textbox(
        self,
        page_number: int,
        rect: tuple[float, float, float, float],
        text: str,
        base_font: str,
        winansi: bool,
        fontsize: float,
        line_height: float,
        align: int,
        color: tuple[float, float, float],
        overlay: bool,
        max_text_size: int | None = 1048576,
    ) -> float: ...
    def insert_embedded_textbox(
        self,
        page_number: int,
        rect: tuple[float, float, float, float],
        text: str,
        font_data: bytes,
        font_index: int,
        fontsize: float,
        line_height: float,
        align: int,
        color: tuple[float, float, float],
        overlay: bool,
        max_font_size: int | None = 67108864,
        max_text_size: int | None = 1048576,
    ) -> float: ...
    def insert_embedded_textbox_file(
        self,
        page_number: int,
        rect: tuple[float, float, float, float],
        text: str,
        path: str,
        font_index: int,
        fontsize: float,
        line_height: float,
        align: int,
        color: tuple[float, float, float],
        overlay: bool,
        max_font_size: int | None = 67108864,
        max_text_size: int | None = 1048576,
    ) -> float: ...
    def replace_text_on_page(
        self,
        page_number: int,
        search: str,
        replacement: str,
        default_char: str | None,
        max_size: int | None,
    ) -> int: ...
    def merge(self, other: _Document) -> None: ...
    def merge_pages(self, other: _Document, page_numbers: list[int], position: int | None) -> None: ...
    def new_page(self, position: int | None, width: float, height: float) -> None: ...
    def copy_page(self, page_number: int, position: int | None) -> None: ...
    def render_page_png(
        self,
        page_number: int,
        scale: float,
        background: tuple[int, int, int, int] | None,
        max_output_size: int | None,
    ) -> bytes: ...
    def render_pages_png(
        self,
        page_numbers: list[int],
        scale: float,
        background: tuple[int, int, int, int] | None,
        workers: int,
        max_output_size: int | None,
    ) -> list[bytes]: ...
    def render_page_svg(self, page_number: int, max_output_size: int | None) -> str: ...
    def compress(self) -> None: ...
    def decompress(self) -> None: ...
    def prune_objects(self) -> None: ...
