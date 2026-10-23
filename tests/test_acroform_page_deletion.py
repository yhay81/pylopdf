"""Remove the AcroForm widgets and fields of pages that `delete_pages` and `select` drop."""

from __future__ import annotations

import re

import pytest
from conftest import build_raw_pdf, form_entries
from test_acroform_import import (
    EXCLUDED_APPEARANCE,
    F1040,
    TWO_PAGE_FIELDS,
    _acroform,
    _deep_form,
    _field,
    _fields,
    _group,
    _objects,
    _reference_count,
    _two_page_form,
    _wide_form,
)

import pylopdf


def _page_numbers(objects: dict[int, bytes]) -> list[int]:
    return [number for number, body in objects.items() if re.search(rb"/Type/Page(?!s)", body)]


def _calculation_order(objects: dict[int, bytes]) -> list[bytes]:
    order = re.findall(rb"(\d+) 0 R", _group(rb"/CO\[([^\]]*)\]", _acroform(objects)))
    return [objects[int(number)] for number in order]


@pytest.mark.parametrize("edit", ["delete", "select"])
def test_deleting_the_second_page_removes_its_widgets_and_fields(edit: str) -> None:
    doc = pylopdf.open(_two_page_form())
    if edit == "delete":
        del doc.pages[1:2]
    else:
        doc.pages.select([0])

    expected = {name: TWO_PAGE_FIELDS[name] for name in ("choice", "person.first", "person.note", "total", "hidden")}
    assert _fields(doc) == expected
    saved = doc.to_bytes()
    objects = _objects(saved)
    assert len(_page_numbers(objects)) == 1
    assert len(re.findall(rb"/Subtype/Widget", saved)) == 3
    assert b"/TU(stale page)" not in saved
    assert EXCLUDED_APPEARANCE.encode() in saved
    choice = _field(objects, "choice")
    assert b"/Opt[(first)]" in choice
    assert _reference_count(b"Kids", choice) == 1
    assert _reference_count(b"Kids", _field(objects, "person")) == 2
    assert _calculation_order(objects) == [_field(objects, "total")]

    reopened = pylopdf.open(saved)
    assert _fields(reopened) == expected
    reopened.form["person.first"].value = "Grace"
    assert _fields(reopened)["person.first"] == ("text", "Grace")


@pytest.mark.parametrize("edit", ["delete", "select"])
def test_deleting_the_first_page_points_remaining_widgets_at_their_page(edit: str) -> None:
    doc = pylopdf.open(_two_page_form())
    if edit == "delete":
        del doc.pages[0:1]
    else:
        doc.pages.select([1])

    expected = {name: TWO_PAGE_FIELDS[name] for name in ("choice", "person.last", "person.note", "hidden")}
    assert _fields(doc) == expected
    saved = doc.to_bytes()
    objects = _objects(saved)
    (page_number,) = _page_numbers(objects)
    assert EXCLUDED_APPEARANCE.encode() not in saved
    assert len(re.findall(rb"/Subtype/Widget", saved)) == 2
    assert b"/Opt[(second)]" in _field(objects, "choice")
    stale = next(body for body in objects.values() if b"/TU(stale page)" in body)
    assert f"/P {page_number} 0 R".encode() in stale
    assert _calculation_order(objects) == [_field(objects, "last")]
    assert b"(total)" not in saved

    reopened = pylopdf.open(saved)
    assert _fields(reopened) == expected
    reopened.form["choice"].value = "Off"
    assert _fields(reopened)["choice"] == ("radio", "Off")


@pytest.mark.parametrize("order", [[1, 0], [0, 1], [0, 0, 1], [1, 1, 0]])
def test_selecting_every_page_keeps_the_form(order: list[int]) -> None:
    doc = pylopdf.open(_two_page_form())
    doc.pages.select(order)
    assert _fields(doc) == TWO_PAGE_FIELDS
    assert _fields(pylopdf.open(doc.to_bytes())) == TWO_PAGE_FIELDS


def test_selecting_no_page_keeps_only_widgetless_fields() -> None:
    doc = pylopdf.open(_two_page_form())
    doc.pages.select([])
    assert _fields(doc) == {"hidden": TWO_PAGE_FIELDS["hidden"]}
    saved = doc.to_bytes()
    assert b"/Subtype/Widget" not in saved
    assert re.search(rb"/CO\[\s*\]", _acroform(_objects(saved)))


@pytest.mark.parametrize(("deleted", "names"), [(0, ["signature", "unplaced"]), (1, ["text", "unplaced"])])
def test_a_widget_that_no_page_lists_goes_only_with_its_page_entry(deleted: int, names: list[str]) -> None:
    """An invisible signature widget is often missing from `/Annots`; one without `/P` stays."""
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [5 0 R 6 0 R 8 0 R] >> >>",
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 100 100] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots [5 0 R] >>",
                4: "<< /Type /Page /Parent 2 0 R >>",
                5: "<< /FT /Tx /T (text) /Type /Annot /Subtype /Widget /Rect [0 0 10 10] /P 3 0 R >>",
                6: "<< /FT /Sig /T (signature) /Kids [7 0 R] >>",
                7: "<< /Parent 6 0 R /Type /Annot /Subtype /Widget /Rect [0 0 0 0] /F 132 /P 4 0 R >>",
                8: "<< /FT /Tx /T (unplaced) /Type /Annot /Subtype /Widget /Rect [0 0 0 0] >>",
            }
        )
    )
    del doc.pages[deleted]
    assert [field["name"] for field in form_entries(doc)] == names


def test_f1040_keeps_the_fields_of_its_remaining_page() -> None:
    doc = pylopdf.open(F1040)
    fields = form_entries(doc)
    first_page = [field for field in fields if field["name"].startswith("topmostSubform[0].Page1[0].")]
    assert len(fields) == 199
    assert len(first_page) == 128

    del doc.pages[1:2]
    assert form_entries(doc) == first_page
    reopened = pylopdf.open(doc.to_bytes())
    assert form_entries(reopened) == first_page
    reopened.form[first_page[0]["name"]].value = "x" if first_page[0]["type"] == "text" else "Off"


def test_documents_without_a_form_delete_pages_unchanged(one_page_pdf: bytes) -> None:
    doc = pylopdf.open(one_page_pdf)
    doc.pages.new()
    del doc.pages[1:2]
    assert form_entries(doc) == []
    assert b"/AcroForm" not in doc.to_bytes()


@pytest.mark.parametrize(
    ("source", "message"),
    [(_wide_form(4096), "4096-node"), (_wide_form(4097, widgets=True), "4096-widget"), (_deep_form(65), "64-level")],
    ids=["nodes", "widgets", "levels"],
)
@pytest.mark.parametrize("edit", ["delete", "select"])
def test_deletion_refuses_field_trees_beyond_the_read_limits(source: bytes, message: str, edit: str) -> None:
    doc = pylopdf.open(source)
    page = doc.pages[0]
    before = doc.to_bytes()

    def delete() -> None:
        del doc.pages[1]

    action = delete if edit == "delete" else (lambda: doc.pages.select([0]))
    with pytest.raises(pylopdf.PdfError, match=message):
        action()
    assert doc.to_bytes() == before
    assert len(doc.pages) == 2
    assert page.rect.width == 100


def test_indirect_fields_and_kids_arrays_are_filtered_in_place() -> None:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields 5 0 R >> >>",
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 100 100] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots [7 0 R] >>",
                4: "<< /Type /Page /Parent 2 0 R /Annots [8 0 R] >>",
                5: "[6 0 R]",
                6: "<< /FT /Tx /T (group) /Kids 9 0 R >>",
                7: "<< /T (a) /Parent 6 0 R /Type /Annot /Subtype /Widget /Rect [0 0 10 10] /P 3 0 R >>",
                8: "<< /T (b) /Parent 6 0 R /Type /Annot /Subtype /Widget /Rect [0 0 10 10] /P 4 0 R >>",
                9: "[7 0 R 8 0 R]",
            }
        )
    )
    del doc.pages[1:2]
    assert [field["name"] for field in form_entries(doc)] == ["group.a"]
