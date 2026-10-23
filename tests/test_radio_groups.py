"""doc.form.repair_radio_groups: flat radio buttons gathered under one field."""

from __future__ import annotations

from conftest import build_raw_pdf

import pylopdf
from pylopdf import FieldType

_BUTTON = "/FT /Btn /Ff 49152 /T (plan) /Type /Annot /Subtype /Widget /P 3 0 R"


def _flat() -> pylopdf.Document:
    on = "/AP << /N << /{state} 8 0 R /Off 8 0 R >> >>"
    return pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [4 0 R 5 0 R 6 0 R 7 0 R] >> >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 100] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots [4 0 R 5 0 R 6 0 R 7 0 R] >>",
                4: f"<< {_BUTTON} /Rect [10 10 25 25] /AS /Off {on.format(state='Basic')} >>",
                5: "<< /FT /Tx /T (name) /Type /Annot /Subtype /Widget /Rect [10 40 90 60] /P 3 0 R >>",
                6: f"<< {_BUTTON} /Rect [30 10 45 25] /V /Pro /AS /Pro {on.format(state='Pro')} >>",
                7: f"<< {_BUTTON} /Rect [50 10 65 25] /V /Pro /AS /Off {on.format(state='Team')} >>",
                8: "<< /Length 0 /Subtype /Form /BBox [0 0 15 15] >>\nstream\n\nendstream",
            }
        )
    )


def test_flat_radio_buttons_become_one_field() -> None:
    doc = _flat()
    assert len(doc.form["plan"].widgets) == 1

    assert doc.form.repair_radio_groups() == 1

    plan = doc.form["plan"]
    assert plan.type is FieldType.RADIO
    assert plan.value == "Pro"
    assert [widget.on_state for widget in plan.widgets] == ["Basic", "Pro", "Team"]
    assert list(doc.form) == ["name", "plan"]
    fields = list(doc.catalog["AcroForm"]["Fields"])
    assert fields[0] == plan.obj
    assert len(fields) == 2
    assert all("T" not in widget.obj and widget.obj["Parent"] == plan.obj for widget in plan.widgets)

    plan.value = "Team"
    assert [widget.obj["AS"] for widget in plan.widgets] == ["Off", "Off", "Team"]
    reopened = pylopdf.open(doc.to_bytes())
    assert reopened.form["plan"].value == "Team"
    assert reopened.form.repair_radio_groups() == 0


def test_repair_leaves_other_forms_alone() -> None:
    doc = pylopdf.Document()
    assert doc.form.repair_radio_groups() == 0
    page = doc.pages.new((200, 100))
    page.widgets.add(pylopdf.RadioButton("plan", (10, 10, 25, 25), on_state="A"))
    page.widgets.add(pylopdf.RadioButton("plan", (30, 10, 45, 25), on_state="B"))
    page.widgets.add(pylopdf.CheckBox("agree", (50, 10, 65, 25)))
    before = doc.to_bytes()

    assert doc.form.repair_radio_groups() == 0
    assert doc.to_bytes() == before
