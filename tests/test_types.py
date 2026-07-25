"""Public typing-contract tests."""

from __future__ import annotations

from typing import get_type_hints, is_typeddict

from conftest import build_pdf

import pylopdf


def test_public_type_contracts_are_runtime_importable() -> None:
    names = {
        "AnnotationInfo",
        "BlockEntry",
        "DocumentMetadata",
        "DrawingInfo",
        "DrawingItem",
        "FormFieldInfo",
        "FormFieldType",
        "ImageCompressionResult",
        "ImageInfo",
        "LinkInfo",
        "MetadataProbe",
        "MetadataUpdate",
        "OcrRotation",
        "OcrWord",
        "PageLabelInfo",
        "PageLabelSpec",
        "TextBlock",
        "TextLine",
        "TextPage",
        "TextSpan",
        "WordEntry",
    }
    assert names <= set(pylopdf.__all__)
    assert all(hasattr(pylopdf, name) for name in names)


def test_typed_dict_required_and_optional_keys() -> None:
    typed_dicts = (
        pylopdf.AnnotationInfo,
        pylopdf.DocumentMetadata,
        pylopdf.DrawingInfo,
        pylopdf.FormFieldInfo,
        pylopdf.ImageCompressionResult,
        pylopdf.ImageInfo,
        pylopdf.LinkInfo,
        pylopdf.MetadataProbe,
        pylopdf.MetadataUpdate,
        pylopdf.OcrWord,
        pylopdf.PageLabelInfo,
        pylopdf.PageLabelSpec,
        pylopdf.TextBlock,
        pylopdf.TextLine,
        pylopdf.TextPage,
        pylopdf.TextSpan,
    )
    assert all(is_typeddict(contract) for contract in typed_dicts)
    assert pylopdf.LinkInfo.__required_keys__ == frozenset({"kind", "from"})
    assert pylopdf.LinkInfo.__optional_keys__ == frozenset({"uri", "page", "to", "zoom", "nameddest", "file", "name"})
    assert pylopdf.PageLabelSpec.__required_keys__ == frozenset({"startpage"})
    assert pylopdf.PageLabelSpec.__optional_keys__ == frozenset({"style", "prefix", "firstpagenum"})
    assert pylopdf.MetadataUpdate.__required_keys__ == frozenset()
    assert get_type_hints(pylopdf.ImageInfo)["bbox"] is pylopdf.Rect
    assert get_type_hints(pylopdf.OcrWord)["bbox"] is pylopdf.Rect


def test_public_return_annotations_describe_real_values() -> None:
    doc = pylopdf.open(stream=build_pdf(["Typed layout"]))
    layout: pylopdf.TextPage = doc[0].get_text("dict")
    metadata: pylopdf.DocumentMetadata = doc.metadata
    labels: list[pylopdf.PageLabelInfo] = doc.get_page_labels()
    fields: list[pylopdf.FormFieldInfo] = doc.get_form_fields()
    drawings: list[pylopdf.DrawingInfo] = doc[0].get_drawings()
    compression: pylopdf.ImageCompressionResult = doc.compress_images()

    assert layout["blocks"][0]["lines"][0]["spans"][0]["text"] == "Typed layout"
    assert metadata["format"].startswith("PDF ")
    assert labels == []
    assert fields == []
    assert drawings == []
    assert compression["rewritten"] == 0
