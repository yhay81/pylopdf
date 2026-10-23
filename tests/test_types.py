"""Public typing-contract tests."""

from __future__ import annotations

import dataclasses
from typing import Any, get_type_hints, is_typeddict

import pytest
from conftest import build_pdf

import pylopdf


def test_public_names_report_the_package_rather_than_a_private_module() -> None:
    """Reprs, pickles, and documentation name ``pylopdf.X``, not the module that defines it."""
    private = {
        name: module
        for name in pylopdf.__all__
        if (module := getattr(getattr(pylopdf, name), "__module__", None)) and module.startswith("pylopdf._")
    }
    assert private == {}


def test_records_are_frozen_dataclasses() -> None:
    for record in (pylopdf.DocumentComplexity, pylopdf.ImageCompressionResult):
        assert dataclasses.is_dataclass(record)
        assert not is_typeddict(record)
        assert record.__dataclass_params__.frozen
    doc = pylopdf.open(build_pdf(["Typed layout"]))
    with pytest.raises(TypeError, match=r"read doc\.complexity\.page_count"):
        _ = doc.complexity["page_count"]  # ty: ignore[not-subscriptable]
    with pytest.raises(TypeError, match=r"read result\.rewritten"):
        _ = doc.rewrite_images(quality=75)["rewritten"]  # ty: ignore[not-subscriptable]
    assert get_type_hints(pylopdf.Image)["bbox"] is pylopdf.Rect
    assert get_type_hints(pylopdf.OcrWord)["bbox"] is pylopdf.Rect
    assert get_type_hints(pylopdf.Table)["diagnostics"] is pylopdf.TableDiagnostics


def test_public_return_annotations_describe_real_values() -> None:
    doc = pylopdf.open(build_pdf(["Typed layout"]))
    layout: dict[str, Any] = doc.pages[0].layout().to_dict()
    metadata: pylopdf.Metadata = doc.metadata
    complexity: pylopdf.DocumentComplexity = doc.complexity
    labels: list[pylopdf.PageLabelRange] = doc.page_labels
    fields: pylopdf.Form = doc.form
    content: pylopdf.Content = doc.pages[0].content()
    compression: pylopdf.ImageCompressionResult = doc.rewrite_images(quality=75)

    assert layout["blocks"][0]["lines"][0]["spans"][0]["text"] == "Typed layout"
    assert isinstance(metadata, pylopdf.Metadata)
    assert complexity.page_count == 1
    assert labels == []
    assert dict(fields) == {}
    assert content.paths == ()
    assert compression.rewritten == 0
