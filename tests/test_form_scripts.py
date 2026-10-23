"""field.scripts and widget.scripts: JavaScript actions, stored and removed, never run."""

from __future__ import annotations

from typing import Any, cast

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import PushButton, ScriptTrigger, TextField


def _form() -> pylopdf.Document:
    doc = pylopdf.Document()
    page = doc.pages.new((300, 300))
    page.widgets.add(TextField("total", (20, 20, 200, 40)))
    page.widgets.add(PushButton("go", (220, 20, 280, 40), caption="Go"))
    return doc


def test_field_scripts_are_set_listed_and_removed() -> None:
    doc = _form()
    total = doc.form["total"]
    assert dict(total.scripts) == {}

    total.scripts[ScriptTrigger.FORMAT] = 'AFNumber_Format(2, 0, 0, 0, "", true);'
    total.scripts[ScriptTrigger.KEYSTROKE] = 'AFNumber_Keystroke(2, 0, 0, 0, "", true);'
    total.scripts[ScriptTrigger.CALCULATE] = "event.value = 1 + 2;"
    total.scripts[cast("Any", "validate")] = "event.rc = true;"

    assert list(total.scripts) == [
        ScriptTrigger.KEYSTROKE,
        ScriptTrigger.FORMAT,
        ScriptTrigger.VALIDATE,
        ScriptTrigger.CALCULATE,
    ]
    assert total.scripts[ScriptTrigger.CALCULATE] == "event.value = 1 + 2;"
    assert list(doc.catalog["AcroForm"]["CO"]) == [total.obj]
    assert total.obj["AA"]["F"]["S"] == "JavaScript"
    reopened = pylopdf.open(doc.to_bytes()).form["total"]
    assert len(reopened.scripts) == 4

    del total.scripts[ScriptTrigger.CALCULATE]
    assert ScriptTrigger.CALCULATE not in total.scripts
    assert "CO" not in doc.catalog["AcroForm"]
    for trigger in (ScriptTrigger.FORMAT, ScriptTrigger.KEYSTROKE, ScriptTrigger.VALIDATE):
        del total.scripts[trigger]
    assert len(total.scripts) == 0
    assert "AA" not in total.obj
    with pytest.raises(KeyError):
        del total.scripts[ScriptTrigger.FORMAT]


def test_widget_scripts_hold_the_widget_triggers_and_the_action() -> None:
    doc = _form()
    button = doc.pages[0].widgets[1]
    field_widget = doc.pages[0].widgets[0]

    button.scripts[ScriptTrigger.ACTIVATE] = 'app.alert("Submitted");'
    field_widget.scripts[ScriptTrigger.FOCUS] = "console.println('in');"
    doc.form["total"].scripts[ScriptTrigger.FORMAT] = "event.value = event.value;"

    assert dict(button.scripts) == {ScriptTrigger.ACTIVATE: 'app.alert("Submitted");'}
    assert button.obj["A"]["JS"].text == 'app.alert("Submitted");'
    # A field merged with its widget holds both kinds of trigger in one /AA.
    assert dict(field_widget.scripts) == {ScriptTrigger.FOCUS: "console.println('in');"}
    assert list(doc.form["total"].scripts) == [ScriptTrigger.FORMAT]
    with pytest.raises(ValueError, match="not a trigger of this widget"):
        button.scripts[ScriptTrigger.FORMAT] = "x"
    with pytest.raises(ValueError, match="not a trigger of this field"):
        doc.form["total"].scripts[ScriptTrigger.ACTIVATE] = "x"
    with pytest.raises(TypeError, match="a script is a str"):
        button.scripts[ScriptTrigger.UP] = cast("Any", 5)
    with pytest.raises(pylopdf.LimitError) as raised:
        button.scripts[ScriptTrigger.UP] = "x" * (1024 * 1024 + 1)
    assert raised.value.code == "script_size"
    del button.scripts[ScriptTrigger.ACTIVATE]
    assert "A" not in button.obj


def test_scripts_of_other_producers_are_read() -> None:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [4 0 R] >> >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 100] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots [4 0 R] >>",
                4: (
                    "<< /FT /Tx /T (a) /Type /Annot /Subtype /Widget /Rect [10 10 90 30] "
                    "/AA << /F << /S /JavaScript /JS 5 0 R >> /K << /S /URI /URI (http://e.test) >> "
                    "/Bl 6 0 R >> >>"
                ),
                5: "<< /Length 20 >>\nstream\nevent.value = 'x';  \nendstream",
                6: "<< /S /JavaScript /JS (blur\\(\\);) >>",
            }
        )
    )

    field = doc.form["a"]

    assert dict(field.scripts) == {ScriptTrigger.FORMAT: "event.value = 'x';  "}
    assert dict(doc.pages[0].widgets[0].scripts) == {ScriptTrigger.BLUR: "blur();"}
    # Setting one trigger copies an indirect /AA into the field.
    field.scripts[ScriptTrigger.VALIDATE] = "v();"
    assert set(field.scripts) == {ScriptTrigger.FORMAT, ScriptTrigger.VALIDATE}


def test_the_core_refuses_unknown_triggers() -> None:
    doc = _form()
    core = doc._doc  # noqa: SLF001
    with pytest.raises(pylopdf.PdfError, match="unknown script trigger"):
        core.set_field_script(doc.form["total"].xref, "Fo", "x")
    with pytest.raises(pylopdf.LimitError):
        core.set_field_script(doc.form["total"].xref, "F", "x" * (1024 * 1024 + 1))


def test_an_indirect_calculation_order_keeps_its_fields() -> None:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [4 0 R 5 0 R] /CO 6 0 R >> >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 100] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots [4 0 R 5 0 R] >>",
                4: (
                    "<< /FT /Tx /T (a) /Type /Annot /Subtype /Widget /Rect [10 10 90 30] "
                    "/AA << /C << /S /JavaScript /JS (event.value = 1;) >> >> >>"
                ),
                5: "<< /FT /Tx /T (b) /Type /Annot /Subtype /Widget /Rect [10 40 90 60] >>",
                6: "[4 0 R]",
            }
        )
    )

    doc.form["b"].scripts[ScriptTrigger.CALCULATE] = "event.value = 2;"

    assert list(doc.catalog["AcroForm"]["CO"]) == [doc.form["a"].obj, doc.form["b"].obj]


def test_replacing_a_calculation_preserves_its_position() -> None:
    doc = _form()
    doc.pages[0].widgets.add(TextField("other", (20, 60, 200, 80)))
    total = doc.form["total"]
    other = doc.form["other"]
    total.scripts[ScriptTrigger.CALCULATE] = "event.value = 1;"
    other.scripts[ScriptTrigger.CALCULATE] = "event.value = 2;"

    total.scripts[ScriptTrigger.CALCULATE] = "event.value = 3;"

    assert list(doc.catalog["AcroForm"]["CO"]) == [total.obj, other.obj]


def test_splitting_a_merged_field_keeps_widget_actions_on_the_widget() -> None:
    doc = _form()
    first = doc.pages[0].widgets[0]
    first.scripts[ScriptTrigger.FOCUS] = "focus();"
    doc.form["total"].scripts[ScriptTrigger.FORMAT] = "format();"
    doc.form["total"].scripts[ScriptTrigger.CALCULATE] = "calculate();"

    second = doc.pages[0].widgets.add(TextField("total", (20, 60, 200, 80)))

    assert dict(first.scripts) == {ScriptTrigger.FOCUS: "focus();"}
    assert dict(second.scripts) == {}
    assert dict(doc.form["total"].scripts) == {
        ScriptTrigger.FORMAT: "format();",
        ScriptTrigger.CALCULATE: "calculate();",
    }
    assert list(doc.catalog["AcroForm"]["CO"]) == [doc.form["total"].obj]


def test_utf16_script_streams_are_decoded_as_text() -> None:
    doc = _form()
    source = "event.value = '\u65e5\u672c\u8a9e';"
    stream = doc.objects.add_stream(b"\xfe\xff" + source.encode("utf-16-be"))
    doc.form["total"].obj["AA"] = {"F": {"S": pylopdf.Name("JavaScript"), "JS": doc.objects[stream]}}

    assert doc.form["total"].scripts[ScriptTrigger.FORMAT] == source


@pytest.mark.parametrize("invalidate", ["close", "renumber"])
def test_saved_script_mappings_validate_their_field_handle(invalidate: str) -> None:
    doc = _form()
    scripts = doc.form["total"].scripts
    scripts[ScriptTrigger.FORMAT] = "format();"
    if invalidate == "close":
        doc.close()
        error = pylopdf.DocumentClosedError
    else:
        doc.to_bytes(renumber=True)
        error = pylopdf.StaleObjectError

    with pytest.raises(error):
        list(scripts)


@pytest.mark.parametrize("widget", [False, True])
def test_script_writes_cannot_make_the_mapping_exceed_its_read_limit(*, widget: bool) -> None:
    doc = _form()
    scripts = doc.pages[0].widgets[0].scripts if widget else doc.form["total"].scripts
    first, second = (
        (ScriptTrigger.FOCUS, ScriptTrigger.BLUR) if widget else (ScriptTrigger.FORMAT, ScriptTrigger.KEYSTROKE)
    )
    scripts[first] = "x" * 600_000

    with pytest.raises(pylopdf.LimitError) as caught:
        scripts[second] = "y" * 600_000

    assert caught.value.code == "script_size"
    assert dict(scripts) == {first: "x" * 600_000}


def test_a_unicode_script_at_the_utf8_limit_round_trips() -> None:
    doc = _form()
    source = "x" * (1024 * 1024 - 2) + "\u00e9"

    doc.form["total"].scripts[ScriptTrigger.FORMAT] = source

    assert doc.form["total"].scripts[ScriptTrigger.FORMAT] == source
    assert pylopdf.open(doc.to_bytes()).form["total"].scripts[ScriptTrigger.FORMAT] == source
