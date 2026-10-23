# Type stubs for the Rust extension module pylopdf_core.
import os
import sys
from typing import Any, Literal, TypeAlias

from typing_extensions import Buffer

from pylopdf._pixmap import Colorspace

if sys.platform == "win32":
    def _copy_windows_dacl(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None: ...

_RawCharTuple: TypeAlias = tuple[str, tuple[float, float, float, float], tuple[float, float]]
_RawSpanTuple: TypeAlias = tuple[
    tuple[float, float, float, float], list[_RawCharTuple], float, tuple[float, float], str, int
]
_RawLineTuple: TypeAlias = tuple[tuple[float, float, float, float], list[_RawSpanTuple], tuple[float, float], int]
_RawBlockTuple: TypeAlias = tuple[tuple[float, float, float, float], list[_RawLineTuple]]

# Page.content(): items as (kind, nested tuple) pairs, then the fonts, clips,
# transparency groups, marked content, and graphics contexts they index.
_Bbox: TypeAlias = tuple[float, float, float, float]
_ContentTuple: TypeAlias = tuple[
    list[tuple[int, Any]],
    list[tuple[str, bool, int | None, bool, bool, bool]],
    list[tuple[bytes, list[float], bool, _Bbox]],
    list[tuple[float, int, tuple[int, bool] | None]],
    list[tuple[str, int | None]],
    list[tuple[list[int], list[int], list[int]]],
]

# A low-level object handle's root (None is the trailer) and path steps.
_ObjectRoot: TypeAlias = tuple[int, int] | None
_PageBox: TypeAlias = tuple[float, float, float, float]
_Target: TypeAlias = tuple[str, int | None, tuple[float, float] | None, float | None, str | None, str | None]
_OutlineColor: TypeAlias = tuple[float, float, float] | None
_AnnotationTexts: TypeAlias = tuple[
    str | None, str | None, str | None, str | None, str | None, str | None, str | None, str | None
]
_Annotation: TypeAlias = tuple[
    int,
    int,
    str | None,
    tuple[float, float, float, float],
    int,
    _AnnotationTexts,
    list[float],
    list[float],
    float | None,
]
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
    def text_width(self, text: str, size: float, max_text_size: int | None = 1048576) -> float: ...
    def text_height(
        self,
        text: str,
        size: float,
        width: float,
        line_height: float | None = None,
        max_text_size: int | None = 1048576,
    ) -> float: ...
    def has_glyph(self, codepoint: int) -> bool: ...

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

_Rgba: TypeAlias = tuple[float, float, float, float]
_CanvasStroke: TypeAlias = tuple[_Rgba, float, int, int, float, list[float], float]

class _Builder:
    def __init__(
        self,
        standards: list[str],
        tagged: bool,
        language: str | None = None,
        title: str | None = None,
        author: str | None = None,
        subject: str | None = None,
        keywords: list[str] = ...,
        created: tuple[int, int, int, int, int, int, int] | None = None,
    ) -> None: ...
    def page_count(self) -> int: ...
    def is_tagged(self) -> bool: ...
    def add_page(self, canvas: _Canvas) -> None: ...
    def finish(self) -> bytes: ...

class _Canvas:
    def __init__(self, width: float, height: float, new_document: bool = False) -> None: ...
    def is_empty(self) -> bool: ...
    def clear(self) -> None: ...
    def add_path(
        self,
        operators: str,
        coordinates: list[float],
        fill: tuple[_Rgba, bool] | None,
        stroke: _CanvasStroke | None,
    ) -> None: ...
    def push_transform(self, matrix: tuple[float, float, float, float, float, float]) -> int: ...
    def push_clip(self, operators: str, coordinates: list[float], even_odd: bool) -> int: ...
    def push_blend(self, mode: int) -> int: ...
    def push_opacity(self, alpha: float) -> int: ...
    def pop(self, depth: int) -> None: ...
    def add_text(
        self,
        at: tuple[float, float],
        lines: list[str],
        font: _Font,
        size: float,
        line_height: float | None,
        fill: _Rgba | None,
        stroke: _CanvasStroke | None,
        max_text_size: int | None = 1048576,
    ) -> None: ...
    def add_textbox(
        self,
        rect: tuple[float, float, float, float],
        text: str,
        font: _Font,
        size: float,
        line_height: float | None,
        align: int,
        fill: _Rgba | None,
        stroke: _CanvasStroke | None,
        max_text_size: int | None = 1048576,
    ) -> float: ...
    def add_image(
        self,
        target: _Document | None,
        data: bytes,
        rect: tuple[float, float, float, float],
        keep_aspect: bool,
        rotation: int,
        max_size: int | None,
        max_pixels: int | None,
    ) -> None: ...
    def add_image_file(
        self,
        target: _Document | None,
        path: str,
        rect: tuple[float, float, float, float],
        keep_aspect: bool,
        rotation: int,
        max_size: int | None,
        max_pixels: int | None,
    ) -> None: ...
    def add_pixmap(
        self,
        target: _Document | None,
        pixmap: Pixmap,
        rect: tuple[float, float, float, float],
        keep_aspect: bool,
        rotation: int,
    ) -> None: ...
    def add_bookmark(self, title: str, level: int, at: tuple[float, float]) -> None: ...
    def push_tag(
        self,
        role: str,
        alt: str | None = None,
        lang: str | None = None,
        title: str | None = None,
        actual_text: str | None = None,
    ) -> int: ...
    def add_page(
        self,
        target: _Document,
        source: _Document,
        page_number: int,
        rect: tuple[float, float, float, float],
        keep_aspect: bool,
    ) -> None: ...

class Pixmap:
    @property
    def width(self) -> int: ...
    @property
    def height(self) -> int: ...
    @property
    def channels(self) -> int: ...
    @property
    def alpha(self) -> bool: ...
    @property
    def colorspace(self) -> Colorspace: ...
    @property
    def stride(self) -> int: ...
    @staticmethod
    def from_array(array: Buffer, /) -> Pixmap: ...
    def to_bytes(
        self,
        format: Literal["png", "jpeg"] = "png",  # noqa: A002 - the public name.
        *,
        quality: int = 95,
        max_size: int | None = 67108864,
    ) -> bytes: ...
    def save(
        self,
        path: str | os.PathLike[str],
        /,
        *,
        format: Literal["png", "jpeg"] | None = None,  # noqa: A002 - the public name.
        quality: int = 95,
    ) -> None: ...
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
    def edit_revision(self) -> int: ...
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
    def set_fallback_face(self, language: str, kind: str, font: _Font | None = None) -> None: ...
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
    def outline_entries(
        self,
    ) -> list[tuple[int, int, int, str, _Target, _OutlineColor, int, int]]: ...
    def outline_children(self, parent: tuple[int, int] | None = None) -> list[tuple[int, int]]: ...
    def outline_item(self, item: tuple[int, int]) -> tuple[int, str, _Target, _OutlineColor, int, int, bool]: ...
    def write_outline(self, entries: list[tuple[int, str, _Target, _OutlineColor, int, bool]]) -> None: ...
    def set_outline_title(self, item: tuple[int, int], title: str) -> None: ...
    def set_outline_style(self, item: tuple[int, int], color: _OutlineColor, flags: int) -> None: ...
    def set_outline_target(self, item: tuple[int, int], target: _Target) -> None: ...
    def set_outline_open(self, item: tuple[int, int], is_open: bool) -> None: ...
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
    def xml_metadata(self, max_size: int | None) -> bytes | None: ...
    def set_xml_metadata(self, data: bytes, max_size: int | None) -> None: ...
    def remove_xml_metadata(self) -> None: ...
    def set_catalog_name(self, key: str, value: str) -> None: ...
    def set_language_tag(self, tag: str | None) -> None: ...
    def set_mark_info(self, marked: bool, user_properties: bool, suspects: bool) -> None: ...
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
    def embfile_info(self, name: str) -> tuple[str, str, int, str, str] | None: ...
    def embfile_upd(self, name: str, data: bytes, max_size: int | None = 67108864) -> None: ...
    def set_metadata(self, key: str, value: str) -> None: ...
    def set_metadata_batch(self, entries: list[tuple[str, str]]) -> None: ...
    def remove_metadata(self) -> None: ...
    def encryption_method(self) -> str | None: ...
    def encryption_permissions(self) -> int | None: ...
    def check_original_password(self, password: str) -> tuple[bool, bool]: ...
    def delete_pages(self, page_numbers: list[int]) -> None: ...
    def get_page_rotation(self, page_number: int) -> int: ...
    def set_page_rotation(self, page_number: int, rotation: int) -> None: ...
    def page_display_size(self, page_number: int) -> tuple[float, float]: ...
    def page_boxes(
        self, page_number: int
    ) -> tuple[_PageBox | None, _PageBox | None, _PageBox | None, _PageBox | None, _PageBox | None]: ...
    def remove_page_box(self, page_number: int, key: str) -> None: ...
    def page_display_matrix(self, page_number: int) -> tuple[float, float, float, float, float, float]: ...
    def set_page_box(self, page_number: int, key: str, x0: float, y0: float, x1: float, y1: float) -> None: ...
    def set_mediabox(self, page_number: int, x0: float, y0: float, x1: float, y1: float) -> None: ...
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
    def page_image_inventory(self, page_number: int) -> list[int]: ...
    def image_xrefs(self) -> list[int]: ...
    def image_facts(self, xref: int) -> tuple[int, int, int, str, str, list[str], int, int, bool] | None: ...
    def extract_image(self, xref: int) -> tuple[str, tuple[int, int], bytes]: ...
    def image_pixmap(self, xref: int, alpha: bool) -> Pixmap: ...
    def page_content(
        self,
        page_number: int,
        kinds: tuple[bool, bool, bool],
        annotations: bool,
        clip: tuple[float, float, float, float] | None,
        max_elements: int | None,
    ) -> _ContentTuple: ...
    def rewrite_images(
        self,
        dpi_threshold: float | None,
        dpi_target: float,
        quality: int,
        lossy: bool,
        lossless: bool,
        color: bool,
        gray: bool,
    ) -> tuple[int, int, int, int, int]: ...
    def render_page_pixmap(
        self,
        page_number: int,
        scale: tuple[float, float],
        gray: bool,
        alpha: bool,
        background: tuple[int, int, int, int] | None,
        clip: tuple[float, float, float, float] | None,
        annotations: bool,
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
    ) -> int: ...
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
    ) -> int: ...
    def insert_pixmap(
        self,
        page_number: int,
        rect: tuple[float, float, float, float],
        pixmap: Pixmap,
        image_rotation: int,
        keep_proportion: bool,
        overlay: bool,
    ) -> int: ...
    def show_pdf_page(
        self,
        page_number: int,
        rect: tuple[float, float, float, float],
        other: _Document,
        src_page_number: int,
        keep_proportion: bool,
        overlay: bool,
    ) -> int: ...
    def show_pdf_page_self(
        self,
        page_number: int,
        rect: tuple[float, float, float, float],
        src_page_number: int,
        keep_proportion: bool,
        overlay: bool,
    ) -> int: ...
    def read_annotations(self, page_number: int) -> list[_Annotation]: ...
    def read_annotation(self, page_number: int, index: int, xref: int) -> _Annotation: ...
    def read_links(self, page_number: int) -> list[tuple[int, int, tuple[float, float, float, float], _Target]]: ...
    def read_link(
        self, page_number: int, index: int, xref: int
    ) -> tuple[int, int, tuple[float, float, float, float], _Target]: ...
    def add_highlight_annotation(
        self,
        page_number: int,
        rects: list[tuple[float, float, float, float]],
        color: tuple[float, float, float],
        opacity: float,
        content: str | None,
    ) -> int: ...
    def add_link(self, page_number: int, rect: tuple[float, float, float, float], target: _Target) -> int: ...
    def set_link(
        self,
        page_number: int,
        index: int,
        xref: int,
        rect: tuple[float, float, float, float] | None = None,
        target: _Target | None = None,
    ) -> int: ...
    def remove_annotations(self, page_number: int, handles: list[tuple[int, int]]) -> None: ...
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
    def commit_canvas(self, page_number: int, canvas: _Canvas, overlay: bool) -> None: ...
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
    def copy_pages(self, page_numbers: list[int], position: int | None) -> None: ...
    def render_page_png(
        self,
        page_number: int,
        scale: tuple[float, float],
        alpha: bool,
        background: tuple[int, int, int, int] | None,
        max_output_size: int | None,
    ) -> bytes: ...
    def render_pages_encoded(
        self,
        page_numbers: list[int],
        scale: tuple[float, float],
        gray: bool,
        alpha: bool,
        background: tuple[int, int, int, int] | None,
        annotations: bool,
        format: str,  # noqa: A002 - the public name.
        quality: int,
        workers: int,
        max_output_size: int | None,
    ) -> list[bytes]: ...
    def render_page_svg(self, page_number: int, annotations: bool, max_output_size: int | None) -> str: ...
    def compress(self) -> None: ...
    def decompress(self) -> None: ...
    def collect_garbage(self, dedupe: bool, prune: bool, renumber: bool) -> None: ...
