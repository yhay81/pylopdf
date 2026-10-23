"""Field labels, maximum lengths, default values, flags, choices, reset, and NeedAppearances."""

from __future__ import annotations

from typing import Any, cast

import pytest
from test_acroform import _build_form_pdf

import pylopdf
from pylopdf import FieldChoice, FieldFlags


def _form() -> pylopdf.Document:
    return pylopdf.open(_build_form_pdf())


def test_text_properties_are_set_read_and_removed() -> None:
    doc = _form()
    customer = doc.form["customer"]
    assert (customer.label, customer.max_length, customer.default_value) == (None, None, None)

    customer.label = "Customer name"
    customer.max_length = 20
    customer.default_value = "nobody"

    assert (customer.label, customer.max_length, customer.default_value) == ("Customer name", 20, "nobody")
    reopened = pylopdf.open(doc.to_bytes()).form["customer"]
    assert (reopened.label, reopened.max_length, reopened.default_value) == ("Customer name", 20, "nobody")
    del customer.label, customer.max_length, customer.default_value
    assert (customer.label, customer.max_length, customer.default_value) == (None, None, None)
    # An inherited /MaxLen reads through the field's parents.
    assert doc.form["code"].max_length == 6


def test_reset_restores_the_default_or_clears_the_value() -> None:
    doc = _form()
    customer = doc.form["customer"]
    customer.default_value = "nobody"

    customer.reset()

    assert customer.value == "nobody"
    assert "nobody" in doc.pages[0].text()
    del customer.default_value
    customer.reset()
    assert customer.value is None
    assert "nobody" not in doc.pages[0].text()
    agree = doc.form["agree"]
    agree.value = True
    agree.reset()
    assert agree.value == "Off"
    plan = doc.form["plan"]
    plan.value = "Pro"
    plan.default_value = "Basic"
    plan.reset()
    assert plan.value == "Basic"
    assert [widget.obj["AS"] for widget in plan.widgets] == ["Off", "Basic"]


def test_flags_and_choices_are_assignable() -> None:
    doc = _form()
    customer = doc.form["customer"]
    level = doc.form["level"]

    customer.flags = customer.flags | FieldFlags.REQUIRED | FieldFlags.MULTILINE
    level.choices = [FieldChoice(value="a", label="Alpha"), "b"]

    assert FieldFlags.REQUIRED in customer.flags
    assert FieldFlags.MULTILINE in customer.flags
    assert level.choices == (FieldChoice(value="a", label="Alpha"), FieldChoice(value="b", label="b"))
    assert level.obj["Opt"][1] == b"b"
    with pytest.raises(pylopdf.PdfError, match="decide a field's kind"):
        level.flags = FieldFlags(0)
    with pytest.raises(pylopdf.PdfError, match="only a choice field"):
        customer.choices = ["x"]
    with pytest.raises(pylopdf.PdfError, match="only a text field"):
        level.max_length = 3
    with pytest.raises(TypeError, match="FieldChoice or a str"):
        level.choices = cast("Any", [1])
    with pytest.raises(ValueError, match="at most 4096"):
        level.choices = ["x"] * 4097
    with pytest.raises(ValueError, match="max_length"):
        customer.max_length = -1
    with pytest.raises(pylopdf.LimitError):
        customer.label = "x" * (1024 * 1024 + 1)


def test_need_appearances_is_read_and_set() -> None:
    doc = _form()

    assert doc.form.need_appearances is False
    doc.form.need_appearances = True
    assert doc.form.need_appearances is True
    assert doc.catalog["AcroForm"]["NeedAppearances"] is True
    # Filling every field draws them, which clears the flag.
    doc.form["customer"].value = "x"
    assert doc.form.need_appearances is False
    with pytest.raises(TypeError, match="bool"):
        doc.form.need_appearances = cast("Any", 1)
    empty = pylopdf.Document()
    empty.pages.new()
    assert empty.form.need_appearances is False
    with pytest.raises(pylopdf.PdfError, match="no interactive form"):
        empty.form.need_appearances = True


def test_the_core_refuses_bad_field_edits() -> None:
    doc = _form()
    core = doc._doc  # noqa: SLF001
    with pytest.raises(pylopdf.PdfError, match="cannot be removed"):
        core.edit_field(doc.form["customer"].xref, {"clear": ["value"]})
    with pytest.raises(pylopdf.PdfError, match="32-bit"):
        core.edit_field(doc.form["customer"].xref, {"clear": [], "flags": -1})
    with pytest.raises(pylopdf.StaleObjectError):
        core.edit_field(1, {"clear": []})
    assert doc.form["customer"].value == "initial"
