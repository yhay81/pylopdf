"""Public typing-contract tests."""

from __future__ import annotations

from typing import Any, get_type_hints, is_typeddict

from conftest import build_pdf

import pylopdf


def test_public_type_contracts_are_runtime_importable() -> None:
    names = {
        "Block",
        "DocumentComplexity",
        "DocumentLimits",
        "DocumentMetadata",
        "DrawingInfo",
        "DrawingItem",
        "FormFieldInfo",
        "FormFieldType",
        "ImageCompressionResult",
        "ImageInfo",
        "LinkInfo",
        "LimitError",
        "MetadataUpdate",
        "OcrRotation",
        "OcrWord",
        "PageLabelInfo",
        "PageLabelSpec",
        "Char",
        "Layout",
        "Line",
        "Span",
        "Word",
    }
    assert names <= set(pylopdf.__all__)
    assert all(hasattr(pylopdf, name) for name in names)


def test_public_names_report_the_package_rather_than_a_private_module() -> None:
    """Reprs, pickles, and documentation name ``pylopdf.X``, not the module that defines it."""
    private = {
        name: module
        for name in pylopdf.__all__
        if (module := getattr(getattr(pylopdf, name), "__module__", None)) and module.startswith("pylopdf._")
    }
    assert private == {}


def test_typed_dict_required_and_optional_keys() -> None:
    typed_dicts = (
        pylopdf.DocumentComplexity,
        pylopdf.DocumentMetadata,
        pylopdf.DrawingInfo,
        pylopdf.FormFieldInfo,
        pylopdf.ImageCompressionResult,
        pylopdf.ImageInfo,
        pylopdf.LinkInfo,
        pylopdf.MetadataUpdate,
        pylopdf.PageLabelInfo,
        pylopdf.PageLabelSpec,
    )
    assert all(is_typeddict(contract) for contract in typed_dicts)
    assert pylopdf.LinkInfo.__required_keys__ == frozenset({"kind", "from", "xref", "id"})
    assert pylopdf.LinkInfo.__optional_keys__ == frozenset({"uri", "page", "to", "zoom", "nameddest", "file", "name"})
    assert pylopdf.PageLabelSpec.__required_keys__ == frozenset({"startpage"})
    assert pylopdf.PageLabelSpec.__optional_keys__ == frozenset({"style", "prefix", "firstpagenum"})
    assert pylopdf.MetadataUpdate.__required_keys__ == frozenset()
    assert get_type_hints(pylopdf.ImageInfo)["bbox"] is pylopdf.Rect
    assert get_type_hints(pylopdf.OcrWord)["bbox"] is pylopdf.Rect
    assert get_type_hints(pylopdf.Table)["diagnostics"] is pylopdf.TableDiagnostics


def test_public_return_annotations_describe_real_values() -> None:
    doc = pylopdf.open(build_pdf(["Typed layout"]))
    layout: dict[str, Any] = doc.pages[0].layout().to_dict()
    metadata: pylopdf.DocumentMetadata = doc.metadata
    complexity: pylopdf.DocumentComplexity = doc.complexity
    labels: list[pylopdf.PageLabelInfo] = doc.get_page_labels()
    fields: list[pylopdf.FormFieldInfo] = doc.get_form_fields()
    drawings: list[pylopdf.DrawingInfo] = doc.pages[0].get_drawings()
    compression: pylopdf.ImageCompressionResult = doc.rewrite_images(quality=75)

    assert layout["blocks"][0]["lines"][0]["spans"][0]["text"] == "Typed layout"
    assert metadata["format"].startswith("PDF ")
    assert complexity["page_count"] == 1
    assert labels == []
    assert fields == []
    assert drawings == []
    assert compression["rewritten"] == 0
