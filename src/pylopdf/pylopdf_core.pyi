# Type stubs for the Rust extension module pylopdf_core.
import os
from typing import TypeAlias

_RawCharTuple: TypeAlias = tuple[str, tuple[float, float, float, float], tuple[float, float]]
_RawSpanTuple: TypeAlias = tuple[
    tuple[float, float, float, float], list[_RawCharTuple], float, tuple[float, float], str, int
]
_RawLineTuple: TypeAlias = tuple[tuple[float, float, float, float], list[_RawSpanTuple], tuple[float, float], int]
_RawBlockTuple: TypeAlias = tuple[tuple[float, float, float, float], list[_RawLineTuple]]

# A low-level object handle's root (None is the trailer) and path steps.
_ObjectRoot: TypeAlias = tuple[int, int] | None
_ObjectStep: TypeAlias = int | bytes

class PdfError(ValueError): ...
class PasswordError(PdfError): ...

class LimitError(PdfError):
    code: str

class OcrError(PdfError): ...
class StaleObjectError(PdfError): ...

def _decode_text_string(data: bytes) -> str: ...
def _encode_text_string(text: str) -> bytes: ...

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

class _Font:
    @staticmethod
    def from_bytes(data: bytes, index: int, max_font_size: int | None = 67108864) -> _Font: ...
    @staticmethod
    def from_file(
        path: str,
        index: int,
        max_font_size: int | None = 67108864,
        package_font: tuple[str, str] | None = None,
    ) -> _Font: ...
    @staticmethod
    def standard(name: str) -> _Font: ...
    @property
    def standard_name(self) -> str | None: ...
    def with_weight(self, weight: int) -> _Font: ...
    @property
    def weight_range(self) -> tuple[float, float, float] | None: ...
    @property
    def name(self) -> str | None: ...
    @property
    def family(self) -> str | None: ...
    @property
    def weight(self) -> int: ...
    @property
    def bold(self) -> bool: ...
    @property
    def italic(self) -> bool: ...
    @property
    def serif(self) -> bool: ...
    @property
    def units_per_em(self) -> int: ...
    @property
    def ascender(self) -> float: ...
    @property
    def descender(self) -> float: ...
    def text_length(self, text: str, fontsize: float, max_text_size: int | None = 1048576) -> float: ...
    def has_glyph(self, codepoint: int) -> int: ...

class _Shape:
    def __init__(self) -> None: ...
    def is_empty(self) -> bool: ...
    def clear(self) -> None: ...
    def add_path(
        self,
        operators: str,
        coordinates: list[float],
        close: bool,
        fill: tuple[list[float], bool, float] | None,
        stroke: tuple[list[float], float, int, int, tuple[list[float], float] | None, float] | None,
    ) -> None: ...
    def insert_text(
        self,
        point: tuple[float, float],
        lines: list[str],
        font: _Font,
        fontsize: float,
        lineheight: float | None,
        color: list[float],
        page_height: float,
        max_text_size: int | None = 1048576,
    ) -> int: ...
    def insert_textbox(
        self,
        rect: tuple[float, float, float, float],
        text: str,
        font: _Font,
        fontsize: float,
        lineheight: float | None,
        align: int,
        color: list[float],
        max_text_size: int | None = 1048576,
    ) -> float: ...

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
    def __buffer__(self, flags: int, /) -> memoryview: ...

class _Document:
    def __init__(
        self,
        max_text_size: int | None = None,
        max_interpretation_size: int | None = None,
        max_text_glyphs: int | None = None,
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
        max_text_glyphs: int | None = None,
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
        max_text_glyphs: int | None = None,
    ) -> _Document: ...
    @staticmethod
    def load_metadata(
        path: str,
        password: str | None = None,
        max_file_size: int | None = None,
    ) -> tuple[dict[str, str], int, str, bool, str | None]: ...
    @staticmethod
    def load_metadata_bytes(
        data: bytes,
        password: str | None = None,
        max_file_size: int | None = None,
    ) -> tuple[dict[str, str], int, str, bool, str | None]: ...
    def is_encrypted(self) -> bool: ...
    def is_repaired(self) -> bool: ...
    def was_encrypted(self) -> bool: ...
    def set_fallback_font(
        self,
        kind: str,
        data: bytes,
        index: int,
        max_font_size: int | None = 67108864,
        language: str = "ja",
    ) -> None: ...
    def set_fallback_font_file(
        self,
        kind: str,
        path: str,
        index: int,
        max_font_size: int | None = 67108864,
        language: str = "ja",
    ) -> None: ...
    def set_fallback_font_files(
        self,
        sans_path: str,
        serif_path: str,
        max_font_size: int | None = 67108864,
        language: str = "ja",
    ) -> None: ...
    def set_fallback_font_locale_files(
        self,
        files: list[tuple[str, str, str]],
        max_font_size: int | None = 67108864,
    ) -> int: ...
    @staticmethod
    def package_font_reads() -> int: ...
    @staticmethod
    def generated_pdf_loads() -> tuple[int, int, int]: ...
    @staticmethod
    def verify_generated_pdf_loads(enabled: bool) -> None: ...
    @staticmethod
    def compare_generated_pdf_load(data: bytes) -> bool | None: ...
    def snapshot_source(self) -> str: ...
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
    def object_numbers(self) -> list[int]: ...
    def object_lookup(self, number: int) -> tuple[int, object] | None: ...
    def page_object_id(self, page_number: int) -> tuple[int, int]: ...
    def object_get(self, root: _ObjectRoot, path: list[_ObjectStep], step: _ObjectStep) -> object: ...
    def object_keys(self, root: _ObjectRoot, path: list[_ObjectStep]) -> list[bytes]: ...
    def object_len(self, root: _ObjectRoot, path: list[_ObjectStep]) -> int: ...
    def object_set(self, root: _ObjectRoot, path: list[_ObjectStep], step: _ObjectStep, value: object) -> bool: ...
    def object_delete(self, root: _ObjectRoot, path: list[_ObjectStep], step: _ObjectStep) -> bool: ...
    def object_insert(self, root: _ObjectRoot, path: list[_ObjectStep], index: int, value: object) -> bool: ...
    def object_add(self, value: object) -> tuple[int, int, bool]: ...
    def object_replace(self, number: int, value: object) -> bool: ...
    def object_add_stream(self, dictionary: object, data: bytes, compress: bool) -> tuple[int, int, bool]: ...
    def stream_read(
        self, stream_id: tuple[int, int], path: list[_ObjectStep], decode: bool = True, max_size: int | None = ...
    ) -> bytes: ...
    def stream_write(
        self, stream_id: tuple[int, int], path: list[_ObjectStep], data: bytes, compress: bool
    ) -> bool: ...
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
        weight: int | None = None,
    ) -> None: ...
    def set_form_field_file(
        self,
        name: str,
        value: str,
        path: str,
        font_index: int = 0,
        max_font_size: int | None = 67108864,
        package_font: tuple[str, str] | None = None,
        weight: int | None = None,
    ) -> None: ...
    def set_form_field_font(self, name: str, value: str, font: _Font) -> None: ...
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
    def page_display_size(self, page_number: int) -> tuple[float, float]: ...
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
    def extract_raw_layout(self, page_number: int) -> tuple[float, float, list[_RawBlockTuple]]: ...
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
    def release_render_cache(self) -> None: ...
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
        lineheight: float | None,
        color: tuple[float, float, float],
        overlay: bool,
        max_text_size: int | None = 1048576,
    ) -> int: ...
    def insert_embedded_text(
        self,
        page_number: int,
        point: tuple[float, float],
        lines: list[str],
        font_data: bytes,
        font_index: int,
        fontsize: float,
        lineheight: float | None,
        color: tuple[float, float, float],
        overlay: bool,
        max_font_size: int | None = 67108864,
        max_text_size: int | None = 1048576,
        weight: int | None = None,
    ) -> int: ...
    def insert_embedded_text_file(
        self,
        page_number: int,
        point: tuple[float, float],
        lines: list[str],
        path: str,
        font_index: int,
        fontsize: float,
        lineheight: float | None,
        color: tuple[float, float, float],
        overlay: bool,
        max_font_size: int | None = 67108864,
        max_text_size: int | None = 1048576,
        package_font: tuple[str, str] | None = None,
        weight: int | None = None,
    ) -> int: ...
    def commit_shape(self, page_number: int, shape: _Shape, overlay: bool) -> None: ...
    def insert_font_text(
        self,
        page_number: int,
        point: tuple[float, float],
        lines: list[str],
        font: _Font,
        fontsize: float,
        lineheight: float | None,
        color: tuple[float, float, float],
        overlay: bool,
        max_text_size: int | None = 1048576,
    ) -> int: ...
    def insert_font_textbox(
        self,
        page_number: int,
        rect: tuple[float, float, float, float],
        text: str,
        font: _Font,
        fontsize: float,
        lineheight: float | None,
        align: int,
        color: tuple[float, float, float],
        overlay: bool,
        max_text_size: int | None = 1048576,
    ) -> float: ...
    def insert_page_textbox(
        self,
        page_number: int,
        rect: tuple[float, float, float, float],
        text: str,
        base_font: str,
        winansi: bool,
        fontsize: float,
        lineheight: float | None,
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
        lineheight: float | None,
        align: int,
        color: tuple[float, float, float],
        overlay: bool,
        max_font_size: int | None = 67108864,
        max_text_size: int | None = 1048576,
        weight: int | None = None,
    ) -> float: ...
    def insert_embedded_textbox_file(
        self,
        page_number: int,
        rect: tuple[float, float, float, float],
        text: str,
        path: str,
        font_index: int,
        fontsize: float,
        lineheight: float | None,
        align: int,
        color: tuple[float, float, float],
        overlay: bool,
        max_font_size: int | None = 67108864,
        max_text_size: int | None = 1048576,
        package_font: tuple[str, str] | None = None,
        weight: int | None = None,
    ) -> float: ...
    def content_glyphs(
        self, page_number: int
    ) -> list[
        tuple[
            tuple[float, float],
            int,
            tuple[float, float],
            int,
            tuple[int, int, int, int, int],
            tuple[bool, bool],
            int,
        ]
    ]: ...
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
    def collect_garbage(self, level: int) -> None: ...
