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
        "Metadata",
        "FormFieldInfo",
        "FormFieldType",
        "ImageCompressionResult",
        "LimitError",
        "Link",
        "Links",
        "MarkInfo",
        "PageLayout",
        "PageMode",
        "PdfaClaim",
        "OcrRotation",
        "OcrWord",
        "PageLabelRange",
        "PageLabelStyle",
        "Outline",
        "OutlineEntry",
        "OutlineItem",
        "Destination",
        "RemoteDestination",
        "Launch",
        "NamedAction",
        "Target",
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
        pylopdf.FormFieldInfo,
        pylopdf.ImageCompressionResult,
    )
    assert all(is_typeddict(contract) for contract in typed_dicts)
    assert get_type_hints(pylopdf.Image)["bbox"] is pylopdf.Rect
    assert get_type_hints(pylopdf.OcrWord)["bbox"] is pylopdf.Rect
    assert get_type_hints(pylopdf.Table)["diagnostics"] is pylopdf.TableDiagnostics


def test_public_return_annotations_describe_real_values() -> None:
    doc = pylopdf.open(build_pdf(["Typed layout"]))
    layout: dict[str, Any] = doc.pages[0].layout().to_dict()
    metadata: pylopdf.Metadata = doc.metadata
    complexity: pylopdf.DocumentComplexity = doc.complexity
    labels: list[pylopdf.PageLabelRange] = doc.page_labels
    fields: list[pylopdf.FormFieldInfo] = doc.get_form_fields()
    content: pylopdf.Content = doc.pages[0].content()
    compression: pylopdf.ImageCompressionResult = doc.rewrite_images(quality=75)

    assert layout["blocks"][0]["lines"][0]["spans"][0]["text"] == "Typed layout"
    assert isinstance(metadata, pylopdf.Metadata)
    assert complexity["page_count"] == 1
    assert labels == []
    assert fields == []
    assert content.paths == ()
    assert compression["rewritten"] == 0
