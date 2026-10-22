"""Register the AcroForm fields of pages that `insert_pdf` imports."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import build_raw_pdf

import pylopdf

F1040 = Path(__file__).parent / "assets" / "real_world" / "f1040.pdf"
F1040_TEXT = "topmostSubform[0].Page1[0].Address_ReadOrder[0].f1_20[0]"
F1040_CHECKBOX = "topmostSubform[0].Page1[0].Checkbox_ReadOrder[0].c1_8[0]"
EXCLUDED_APPEARANCE = "EXCLUDED-WIDGET-APPEARANCE-4d1e"


def _objects(data: bytes) -> dict[int, bytes]:
    """Map object numbers to their bodies in uncompressed lopdf output."""
    return {int(match[1]): match[2] for match in re.finditer(rb"(\d+) 0 obj\s*(.*?)\s*endobj", data, re.DOTALL)}


def _group(pattern: bytes, data: bytes) -> bytes:
    """Return the first group of the required match of `pattern` in `data`."""
    match = re.search(pattern, data, re.DOTALL)
    assert match is not None, (pattern, data[:200])
    return match[1]


def _reference_count(key: bytes, body: bytes) -> int:
    """Count the references in the direct array under `key`."""
    return len(re.findall(rb"\d+ 0 R", _group(rb"/" + key + rb"\[([^\]]*)\]", body)))


def _field(objects: dict[int, bytes], partial_name: str) -> bytes:
    """Return the one object whose literal `/T` is `partial_name`."""
    needle = rb"/T\(" + re.escape(partial_name.encode()) + rb"\)"
    matches = [body for body in objects.values() if re.search(needle, body)]
    assert len(matches) == 1, (partial_name, matches)
    return matches[0]


def _acroform(objects: dict[int, bytes]) -> bytes:
    """Return the interactive form dictionary that the catalog references."""
    catalog = next(body for body in objects.values() if b"/Type/Catalog" in body)
    reference = re.search(rb"/AcroForm (\d+) 0 R", catalog)
    assert reference is not None, catalog
    return objects[int(reference[1])]


def _fields(doc: pylopdf.Document) -> dict[str, tuple[str, str | None]]:
    return {field["name"]: (field["type"], field["value"]) for field in doc.get_form_fields()}


def _stream(content: str) -> str:
    return f"<< /Type /XObject /Subtype /Form /BBox [0 0 20 20] /Length {len(content)} >>\nstream\n{content}\nendstream"


def _two_page_form(*, receiving_quadding: str = "") -> bytes:
    """Build fields whose widgets lie on one or both of two pages.

    `choice` is a radio group with one widget per page and one `/Opt` export
    value per widget. `person` holds `first` on the first page, `last` on the
    second, and the widgetless `note`. `total` is on the first page, and the
    top-level `hidden` has no widget. The second-page widget's `/P` names the
    first page, and the first-page radio appearance holds a marker.
    """
    marker = f"BT /F1 4 Tf ({EXCLUDED_APPEARANCE}) Tj ET"
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 5 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 200 200] >>",
            3: "<< /Type /Page /Parent 2 0 R /Annots [8 0 R 11 0 R 13 0 R] >>",
            4: "<< /Type /Page /Parent 2 0 R /Annots [9 0 R 12 0 R] >>",
            5: (
                "<< /Fields [6 0 R 10 0 R 13 0 R 15 0 R] /DA (/Helv 0 Tf 0 g) /DR << /Font << /Helv 7 0 R >> >>"
                f" /CO [12 0 R 13 0 R] /NeedAppearances true /XFA [(template) 18 0 R] /SigFlags 3 {receiving_quadding}"
                " >>"
            ),
            6: "<< /FT /Btn /Ff 49152 /T (choice) /V /second /Opt [(first) (second)] /Kids [8 0 R 9 0 R] >>",
            7: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            8: (
                "<< /Type /Annot /Subtype /Widget /Parent 6 0 R /Rect [10 170 30 190] /P 3 0 R /AS /Off"
                " /AP << /N << /first 16 0 R /Off 17 0 R >> >> >>"
            ),
            9: (
                "<< /Type /Annot /Subtype /Widget /Parent 6 0 R /Rect [10 170 30 190] /P 3 0 R /AS /second"
                " /TU (stale page) /AP << /N << /second 17 0 R /Off 17 0 R >> >> >>"
            ),
            10: "<< /FT /Tx /T (person) /Kids [11 0 R 12 0 R 14 0 R] >>",
            11: (
                "<< /T (first) /V (Ada) /Parent 10 0 R /Type /Annot /Subtype /Widget /Rect [40 140 190 160] /P 3 0 R >>"
            ),
            12: (
                "<< /T (last) /V (Lovelace) /Parent 10 0 R /Type /Annot /Subtype /Widget"
                " /Rect [40 140 190 160] /P 4 0 R >>"
            ),
            13: "<< /FT /Tx /T (total) /V (42) /Type /Annot /Subtype /Widget /Rect [40 100 190 120] /P 3 0 R >>",
            14: "<< /T (note) /V (kept) /Parent 10 0 R >>",
            15: "<< /FT /Tx /T (hidden) /V (secret) >>",
            16: _stream(marker),
            17: _stream(""),
            18: "<< /Length 0 >>\nstream\n\nendstream",
        }
    )


TWO_PAGE_FIELDS = {
    "choice": ("radio", "second"),
    "person.first": ("text", "Ada"),
    "person.last": ("text", "Lovelace"),
    "person.note": ("text", "kept"),
    "total": ("text", "42"),
    "hidden": ("text", "secret"),
}


def test_insert_pdf_registers_f1040_fields_with_values() -> None:
    src = pylopdf.open(F1040)
    src.set_form_field(F1040_TEXT, "Jane Doe")
    src.set_form_field(F1040_CHECKBOX, value=True)
    source_bytes = src.to_bytes()

    dst = pylopdf.Document()
    dst.pages.extend(src.pages)

    assert src.to_bytes() == source_bytes
    assert dst.get_form_fields() == src.get_form_fields()
    assert len(dst.get_form_fields()) == 199
    assert _fields(dst)[F1040_TEXT] == ("text", "Jane Doe")
    for page in range(2):
        assert (
            dst.pages[page].pixmap(matrix=pylopdf.Matrix(0.5, 0.5), alpha=True).samples
            == src.pages[page].pixmap(matrix=pylopdf.Matrix(0.5, 0.5), alpha=True).samples
        )

    dst.set_form_field(F1040_TEXT, "John Roe")
    dst.set_form_field(F1040_CHECKBOX, value=False)
    saved = dst.to_bytes()
    assert b"/XFA" not in saved
    assert b"/SigFlags" not in saved
    reopened = pylopdf.open(saved)
    assert len(reopened.get_form_fields()) == 199
    assert _fields(reopened)[F1040_TEXT] == ("text", "John Roe")
    assert _fields(reopened)[F1040_CHECKBOX] == ("checkbox", "Off")
    reopened.set_form_field(F1040_TEXT, "Again")
    assert _fields(reopened)[F1040_TEXT] == ("text", "Again")


def test_insert_pdf_after_existing_pages_registers_f1040_fields(one_page_pdf: bytes) -> None:
    dst = pylopdf.open(one_page_pdf)
    dst.pages.insert(0, pylopdf.open(F1040).pages)
    assert len(dst.pages) == 3
    assert len(dst.get_form_fields()) == 199
    assert len(dst.pages[0].annots(types=[pylopdf.PDF_ANNOT_WIDGET])) == 128
    assert "Hello PDF" in dst.pages[2].text()


def test_inserting_f1040_twice_renames_the_second_form() -> None:
    src = pylopdf.open(F1040)
    src.set_form_field(F1040_TEXT, "Jane Doe")
    dst = pylopdf.Document()
    for _ in range(3):
        dst.pages.extend(src.pages)

    fields = _fields(dst)
    assert len(fields) == 3 * 199
    tail = F1040_TEXT.removeprefix("topmostSubform[0]")
    copies = [F1040_TEXT, f"topmostSubform[0]+1{tail}", f"topmostSubform[0]+2{tail}"]
    assert [fields[name] for name in copies] == [("text", "Jane Doe")] * 3
    assert sum(name.startswith("topmostSubform[0]+1.") for name in fields) == 199

    for index, name in enumerate(copies):
        dst.set_form_field(name, f"copy {index}")
    reopened = pylopdf.open(dst.to_bytes())
    assert [_fields(reopened)[name][1] for name in copies] == ["copy 0", "copy 1", "copy 2"]

    saved = reopened.to_bytes()
    # The partial names are UTF-16BE, and the suffix keeps that encoding.
    assert "topmostSubform[0]+1".encode("utf-16-be").hex().upper().encode() in saved
    # The copies' default resources equal the receiving ones, so they are
    # shared and every default appearance string stays as it was.
    fonts = _group(rb"/Font<<(.*?)>>", _acroform(_objects(saved)))
    assert sorted(re.findall(rb"/([\w-]+) \d+ 0 R", fonts)) == [b"Helv", b"HelveticaLTStd-Bold", b"ZaDb"]
    assert saved.count(b"/HelveticaLTStd-Bold 8.00 Tf") == 3 * 126
    assert saved.count(b"/DA(/Helv 0 Tf 0 g )") == 1


@pytest.mark.parametrize(
    ("page", "prefix", "count"), [(0, "topmostSubform[0].Page1[0].", 128), (1, "topmostSubform[0].Page2[0].", 71)]
)
def test_insert_pdf_range_registers_only_imported_page_fields(page: int, prefix: str, count: int) -> None:
    src = pylopdf.open(F1040)
    expected = [field for field in src.get_form_fields() if field["name"].startswith(prefix)]
    dst = pylopdf.Document()
    dst.pages.extend(src.pages[page])

    assert dst.get_form_fields() == expected
    assert len(expected) == count
    saved = dst.to_bytes()
    assert len(re.findall(rb"/Subtype/Widget", saved)) == count
    reopened = pylopdf.open(saved)
    assert reopened.get_form_fields() == expected
    reopened.set_form_field(expected[0]["name"], "x" if expected[0]["type"] == "text" else "Off")


def test_complete_import_keeps_every_field_and_form_default() -> None:
    src = pylopdf.open(_two_page_form())
    dst = pylopdf.Document()
    dst.pages.extend(src.pages[0:2][::-1])

    assert _fields(dst) == TWO_PAGE_FIELDS
    saved = dst.to_bytes()
    assert EXCLUDED_APPEARANCE.encode() in saved
    acroform = _acroform(_objects(saved))
    assert b"/NeedAppearances true" in acroform
    assert b"/DA(/Helv 0 Tf 0 g)" in acroform
    assert re.search(rb"/DR<</Font<</Helv \d+ 0 R>>>>", acroform)
    assert b"/XFA" not in saved
    assert b"/SigFlags" not in saved
    assert _reference_count(b"CO", acroform) == 2


def test_partial_import_drops_widgets_and_fields_of_other_pages() -> None:
    src = pylopdf.open(_two_page_form())
    dst = pylopdf.Document()
    dst.pages.extend(src.pages[1:2])

    expected = {name: TWO_PAGE_FIELDS[name] for name in ("choice", "person.last", "person.note")}
    assert _fields(dst) == expected
    saved = dst.to_bytes()
    assert EXCLUDED_APPEARANCE.encode() not in saved
    objects = _objects(saved)
    choice = _field(objects, "choice")
    assert b"/Opt[(second)]" in choice
    assert _reference_count(b"Kids", choice) == 1
    person = _field(objects, "person")
    assert _reference_count(b"Kids", person) == 2
    acroform = _acroform(objects)
    order = re.findall(rb"(\d+) 0 R", _group(rb"/CO\[([^\]]*)\]", acroform))
    assert [objects[int(number)] for number in order] == [_field(objects, "last")]

    (page_number,) = [number for number, body in objects.items() if re.search(rb"/Type/Page(?!s)", body)]
    stale = next(body for body in objects.values() if b"/TU(stale page)" in body)
    assert f"/P {page_number} 0 R".encode() in stale

    reopened = pylopdf.open(saved)
    assert _fields(reopened) == expected
    reopened.set_form_field("person.last", "Byron")
    reopened.set_form_field("choice", "Off")
    assert _fields(reopened)["person.last"] == ("text", "Byron")


def test_partial_import_of_first_page_keeps_its_radio_widget() -> None:
    dst = pylopdf.Document()
    dst.pages.extend(pylopdf.open(_two_page_form()).pages[0:1])

    expected = {name: TWO_PAGE_FIELDS[name] for name in ("choice", "person.first", "person.note", "total")}
    assert _fields(dst) == expected
    objects = _objects(dst.to_bytes())
    assert b"/Opt[(first)]" in _field(objects, "choice")
    assert EXCLUDED_APPEARANCE.encode() in b"".join(objects.values())


@pytest.mark.parametrize(("page", "names"), [(0, ["text"]), (1, ["signature"])])
def test_a_widget_that_no_page_lists_follows_its_page_entry(page: int, names: list[str]) -> None:
    """An invisible signature widget is often missing from `/Annots`."""
    src = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [5 0 R 6 0 R] >> >>",
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 100 100] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots [5 0 R] >>",
                4: "<< /Type /Page /Parent 2 0 R >>",
                5: "<< /FT /Tx /T (text) /Type /Annot /Subtype /Widget /Rect [0 0 10 10] /P 4 0 R >>",
                6: "<< /FT /Sig /T (signature) /Kids [7 0 R] >>",
                7: "<< /Parent 6 0 R /Type /Annot /Subtype /Widget /Rect [0 0 0 0] /F 132 /P 4 0 R >>",
            }
        )
    )
    dst = pylopdf.Document()
    dst.pages.extend(src.pages[page])
    assert [field["name"] for field in dst.get_form_fields()] == names


def test_partial_import_without_imported_widgets_adds_no_form(one_page_pdf: bytes) -> None:
    src = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [5 0 R] >> >>",
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 100 100] >>",
                3: "<< /Type /Page /Parent 2 0 R >>",
                4: "<< /Type /Page /Parent 2 0 R /Annots [5 0 R] >>",
                5: "<< /FT /Tx /T (only) /Type /Annot /Subtype /Widget /Rect [0 0 10 10] /P 4 0 R >>",
            }
        )
    )
    dst = pylopdf.open(one_page_pdf)
    dst.pages.extend(src.pages[0:1])
    assert dst.get_form_fields() == []
    assert b"/AcroForm" not in dst.to_bytes()


def _receiving_form(fields: str, extra: str = "", objects: dict[int, str] | None = None) -> bytes:
    """Build a one-page form whose catalog references the dictionary in object 4."""
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 250 100] >>",
            3: "<< /Type /Page /Parent 2 0 R >>",
            4: f"<< /Fields {fields} {extra} >>",
            **(objects or {}),
        }
    )


def _named_fields(names: list[str], acroform: str = "") -> bytes:
    """Build one page whose widget-fields carry the given top-level names."""
    widgets = {
        5 + index: f"<< /FT /Tx /T ({name}) /Type /Annot /Subtype /Widget /Rect [0 0 10 10] /P 3 0 R >>"
        for index, name in enumerate(names)
    }
    references = " ".join(f"{number} 0 R" for number in widgets)
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 100 100] >>",
            3: f"<< /Type /Page /Parent 2 0 R /Annots [{references}] >>",
            4: f"<< /Fields [{references}] {acroform} >>",
            **widgets,
        }
    )


def test_colliding_top_level_names_take_the_smallest_unused_suffix() -> None:
    dst = pylopdf.open(_named_fields(["a", "a+1"]))
    src = pylopdf.open(_named_fields(["a", "a+2", "b"]))
    dst.pages.extend(src.pages)
    assert [field["name"] for field in dst.get_form_fields()] == ["a", "a+1", "a+2", "a+3", "b"]
    dst.pages.extend(src.pages)
    assert [field["name"] for field in dst.get_form_fields()] == [
        "a",
        "a+1",
        "a+2",
        "a+2+1",
        "a+3",
        "a+4",
        "b",
        "b+1",
    ]
    assert len(pylopdf.open(dst.to_bytes()).get_form_fields()) == 8


def _default_appearance_form(fonts: tuple[str, str], field: str, extra: dict[int, bytes | str] | None = None) -> bytes:
    """Build one text field whose `/DR` fonts `/Helv` and `/F#20a` use `fonts`."""
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 6 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 100 100] >>",
            3: "<< /Type /Page /Parent 2 0 R /Annots [7 0 R] >>",
            4: f"<< /Type /Font /Subtype /Type1 /BaseFont /{fonts[0]} >>",
            5: f"<< /Type /Font /Subtype /Type1 /BaseFont /{fonts[1]} >>",
            6: "<< /Fields [7 0 R] /DR << /Font << /Helv 4 0 R /F#20a 5 0 R >> >> >>",
            7: f"<< /FT /Tx {field} /Type /Annot /Subtype /Widget /Rect [0 0 10 10] /P 3 0 R >>",
            **(extra or {}),
        }
    )


@pytest.mark.parametrize(
    ("appearance", "expected"),
    [
        ("(/Helv 9 Tf 0 g)", b"(/Helv_1 9 Tf 0 g)"),
        ("(0 g /Helv 9 Tf)", b"(0 g /Helv_1 9 Tf)"),
        ("(/He#6Cv 9 Tf)", b"(/Helv_1 9 Tf)"),
        ("(/F#20a 9 Tf)", b"(/F#20a_1 9 Tf)"),
        ("(/Helv 9 Tf /Helv gs 1 0 0 rg)", b"(/Helv_1 9 Tf /Helv gs 1 0 0 rg)"),
        ("<FEFF002F00480065006C0076002000390020005400660020003000200067>", b"(/Helv_1 9 Tf 0 g)"),
        ("8 0 R", b"(/Helv_1 9 Tf 0 g)"),
    ],
    ids=["leading", "trailing", "escaped", "escaped-rename", "graphics-state", "utf-16", "indirect"],
)
def test_imported_default_appearances_follow_renamed_fonts(appearance: str, expected: bytes) -> None:
    dst = pylopdf.open(_default_appearance_form(("Helvetica", "Courier"), "/T (own) /DA (/Helv 9 Tf 0 g)"))
    src = _default_appearance_form(
        ("Times-Roman", "Symbol"), f"/T (imported) /DA {appearance}", {8: "(/Helv 9 Tf 0 g)"}
    )
    dst.pages.extend(pylopdf.open(src).pages)

    objects = _objects(dst.to_bytes())
    fonts = _group(rb"/Font<<(.*?)>>", _acroform(objects))
    assert sorted(re.findall(rb"/([\w#]+) \d+ 0 R", fonts)) == [b"F#20a", b"F#20a_1", b"Helv", b"Helv_1"]
    assert b"/DA(/Helv 9 Tf 0 g)" in _field(objects, "own")
    imported = _field(objects, "imported")
    if appearance == "8 0 R":
        number = int(_group(rb"/DA (\d+) 0 R", imported))
        assert objects[number] == expected
    else:
        assert b"/DA" + expected in imported


def test_imported_fields_follow_renamed_default_resources_and_form_defaults() -> None:
    dst = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 5 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 250 100] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots [6 0 R] >>",
                4: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
                5: "<< /Fields [6 0 R] /DA (/Helv 0 Tf 0 g) /DR << /Font << /Helv 4 0 R >> >> /Q 2 >>",
                6: (
                    "<< /FT /Tx /T (name) /Type /Annot /Subtype /Widget /Rect [5 60 245 90] /P 3 0 R /BS << /W 0 >> >>"
                ),
            }
        )
    )
    src = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 5 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 250 100] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots [6 0 R 7 0 R] >>",
                4: "<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman >>",
                5: "<< /Fields [6 0 R 7 0 R] /DA (/Helv 0 Tf 0 g) /DR << /Font << /Helv 4 0 R /ZaDb 8 0 R >> >> >>",
                6: (
                    "<< /FT /Tx /T (name) /V (imported) /Type /Annot /Subtype /Widget /Rect [5 60 245 90]"
                    " /P 3 0 R /BS << /W 0 >> >>"
                ),
                7: (
                    "<< /FT /Tx /T (email) /DA (/Helv 9 Tf 0 g) /Type /Annot /Subtype /Widget"
                    " /Rect [5 10 245 40] /P 3 0 R /BS << /W 0 >> >>"
                ),
                8: "<< /Type /Font /Subtype /Type1 /BaseFont /ZapfDingbats >>",
            }
        )
    )
    dst.pages.extend(src.pages)

    assert _fields(dst) == {"name": ("text", None), "name+1": ("text", "imported"), "email": ("text", None)}
    objects = _objects(dst.to_bytes())
    acroform = _acroform(objects)
    resources = {
        name: objects[int(number)]
        for name, number in re.findall(rb"/(\w+) (\d+) 0 R", _group(rb"/Font<<(.*?)>>", acroform))
    }
    assert sorted(resources) == [b"Helv", b"Helv_1", b"ZaDb"]
    assert b"/BaseFont/Helvetica" in resources[b"Helv"]
    assert b"/BaseFont/Times-Roman" in resources[b"Helv_1"]
    assert b"/BaseFont/ZapfDingbats" in resources[b"ZaDb"]
    assert b"/DA(/Helv 0 Tf 0 g)" in acroform
    assert b"/DA" not in _field(objects, "name")
    assert b"/DA(/Helv_1 0 Tf 0 g)" in _field(objects, "name+1")
    assert b"/Q 0" in _field(objects, "name+1")
    assert b"/DA(/Helv_1 9 Tf 0 g)" in _field(objects, "email")
    assert b"/Q 0" in _field(objects, "email")

    # The receiving field keeps the receiving document's right alignment,
    # and the imported field keeps the source's default left alignment.
    for name in ("name", "email"):
        dst.set_form_field(name, "xyz")

    def ink(page: int, clip: tuple[float, float, float, float]) -> bool:
        return any(dst.pages[page].pixmap(clip=clip, alpha=True).samples[3::4])

    # In display coordinates the receiving `name` on the first page spans
    # y 10..40, and the imported `email` on the second page spans y 60..90.
    assert ink(0, (125, 10, 245, 40))
    assert not ink(0, (5, 10, 125, 40))
    assert ink(1, (5, 60, 125, 90))
    assert not ink(1, (125, 60, 245, 90))


def _font_file_form(size: int, name: str) -> bytes:
    """Build one field whose `/DR` font `/F1` embeds `size` bytes of font data."""
    data = "0" * size
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 100 100] >>",
            3: "<< /Type /Page /Parent 2 0 R /Annots [5 0 R] >>",
            4: "<< /Fields [5 0 R] /DA (/F1 0 Tf 0 g) /DR << /Font << /F1 6 0 R >> >> >>",
            5: f"<< /FT /Tx /T ({name}) /Type /Annot /Subtype /Widget /Rect [0 0 10 10] /P 3 0 R >>",
            6: "<< /Type /Font /Subtype /TrueType /BaseFont /Embedded /FontDescriptor 7 0 R >>",
            7: "<< /Type /FontDescriptor /FontName /Embedded /FontFile2 8 0 R >>",
            8: f"<< /Length {size} >>\nstream\n{data}\nendstream",
        }
    )


@pytest.mark.parametrize(("size", "fonts"), [(1024, [b"F1"]), (1024 * 1024 + 1, [b"F1", b"F1_1"])])
def test_identical_default_resources_are_shared_within_the_comparison_budget(size: int, fonts: list[bytes]) -> None:
    dst = pylopdf.open(_font_file_form(size, "own"))
    dst.pages.extend(pylopdf.open(_font_file_form(size, "imported")).pages)
    objects = _objects(dst.to_bytes())
    assert re.findall(rb"/(\w+) \d+ 0 R", _group(rb"/Font<<(.*?)>>", _acroform(objects))) == fonts
    # A renamed font moves the source's default appearance onto the field.
    imported = _field(objects, "imported")
    if len(fonts) == 2:
        assert b"/DA(/F1_1 0 Tf 0 g)" in imported
    else:
        assert b"/DA" not in imported


def test_import_adds_missing_receiving_defaults_and_need_appearances() -> None:
    src = build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R /AcroForm 4 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 100 100] >>",
            3: "<< /Type /Page /Parent 2 0 R /Annots [5 0 R] >>",
            4: "<< /Fields [5 0 R] /DA (/Helv 0 Tf 0 g) /DR << /Font << /Helv 6 0 R >> >> /NeedAppearances true >>",
            5: "<< /FT /Tx /T (a) /Type /Annot /Subtype /Widget /Rect [0 0 10 10] /P 3 0 R >>",
            6: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        }
    )
    dst = pylopdf.open(_receiving_form("[]", "/NeedAppearances false"))
    dst.pages.extend(pylopdf.open(src).pages)
    objects = _objects(dst.to_bytes())
    acroform = _acroform(objects)
    assert b"/NeedAppearances true" in acroform
    assert re.search(rb"/DR<</Font<</Helv \d+ 0 R>>>>", acroform)
    assert b"/DA" not in acroform
    # The receiving form names no default appearance, so the imported field
    # carries the source's.
    assert b"/DA(/Helv 0 Tf 0 g)" in _field(objects, "a")
    assert [field["name"] for field in dst.get_form_fields()] == ["a"]


@pytest.mark.parametrize("value", ["7", "null", "8 0 R"])
def test_unusable_receiving_acroform_is_replaced(value: str) -> None:
    dst = pylopdf.open(
        build_raw_pdf(
            {
                1: f"<< /Type /Catalog /Pages 2 0 R /AcroForm {value} >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 100 100] >>",
                3: "<< /Type /Page /Parent 2 0 R >>",
                4: "null",
                5: "null",
                6: "null",
                7: "null",
                8: "42",
            }
        )
    )
    dst.pages.extend(pylopdf.open(_named_fields(["a", "b"])).pages)
    assert [field["name"] for field in dst.get_form_fields()] == ["a", "b"]
    assert [field["name"] for field in pylopdf.open(dst.to_bytes()).get_form_fields()] == ["a", "b"]


def test_import_appends_to_an_indirect_fields_array() -> None:
    dst = pylopdf.open(
        _receiving_form(
            "5 0 R",
            objects={5: "[6 0 R]", 6: "<< /FT /Tx /T (own) >>"},
        )
    )
    dst.pages.extend(pylopdf.open(_named_fields(["a"])).pages)
    assert [field["name"] for field in dst.get_form_fields()] == ["a", "own"]
    objects = _objects(dst.to_bytes())
    assert b"/Fields 5 0 R" in _acroform(objects)
    assert len(re.findall(rb"\d+ 0 R", objects[5])) == 2


def test_partial_import_walks_a_cyclic_field_tree() -> None:
    """A nameless intermediate field that lists itself is folded once."""
    src = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [5 0 R] >> >>",
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 100 100] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots [7 0 R] >>",
                4: "<< /Type /Page /Parent 2 0 R /Annots [8 0 R] >>",
                5: "<< /FT /Tx /T (outer) /Kids [6 0 R] >>",
                6: "<< /Parent 5 0 R /Kids [7 0 R 6 0 R 8 0 R] >>",
                7: "<< /Parent 6 0 R /Type /Annot /Subtype /Widget /Rect [0 0 10 10] /P 3 0 R >>",
                8: "<< /Parent 6 0 R /Type /Annot /Subtype /Widget /Rect [0 0 10 10] /P 4 0 R >>",
            }
        )
    )
    assert [field["name"] for field in src.get_form_fields()] == ["outer"]
    dst = pylopdf.Document()
    dst.pages.extend(src.pages[0:1])
    assert [field["name"] for field in dst.get_form_fields()] == ["outer"]
    objects = _objects(dst.to_bytes())
    assert len(re.findall(rb"/Subtype/Widget", b"".join(objects.values()))) == 1


def _wide_form(kids: int, *, widgets: bool = False) -> bytes:
    """Build two pages and one field with `kids` child fields or widgets."""
    objects: dict[int, bytes | str] = {
        1: "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [5 0 R] >> >>",
        2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 100 100] >>",
        3: "<< /Type /Page /Parent 2 0 R /Annots [6 0 R] >>",
        4: "<< /Type /Page /Parent 2 0 R >>",
        5: "<< /FT /Tx /T (root) /Kids [" + " ".join(f"{6 + index} 0 R" for index in range(kids)) + "] >>",
    }
    for index in range(kids):
        objects[6 + index] = (
            "<< /Parent 5 0 R /Type /Annot /Subtype /Widget /Rect [0 0 10 10] >>"
            if widgets
            else f"<< /T (f{index}) /Parent 5 0 R /Type /Annot /Subtype /Widget /Rect [0 0 10 10] >>"
        )
    return build_raw_pdf(objects)


def _deep_form(levels: int) -> bytes:
    """Build two pages and a chain of `levels` nested fields."""
    objects: dict[int, bytes | str] = {
        1: "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [5 0 R] >> >>",
        2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 100 100] >>",
        3: f"<< /Type /Page /Parent 2 0 R /Annots [{4 + levels} 0 R] >>",
        4: "<< /Type /Page /Parent 2 0 R >>",
    }
    for level in range(levels):
        kids = f"/Kids [{6 + level} 0 R]" if level + 1 < levels else ""
        widget = "/Type /Annot /Subtype /Widget /Rect [0 0 10 10] /P 3 0 R" if level + 1 == levels else ""
        objects[5 + level] = f"<< /FT /Tx /T (l{level}) {kids} {widget} >>"
    return build_raw_pdf(objects)


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (_wide_form(4096), "4096-node"),
        (_wide_form(4097, widgets=True), "4096-widget"),
        (_deep_form(65), "64-level"),
    ],
    ids=["nodes", "widgets", "levels"],
)
def test_partial_import_refuses_field_trees_beyond_the_read_limits(
    source: bytes, message: str, one_page_pdf: bytes
) -> None:
    src = pylopdf.open(source)
    dst = pylopdf.open(one_page_pdf)
    page = dst.pages[0]
    before = dst.to_bytes()
    with pytest.raises(pylopdf.PdfError, match=message):
        dst.pages.extend(src.pages[0:1])
    assert dst.to_bytes() == before
    assert "Hello PDF" in page.text()


@pytest.mark.parametrize(
    ("source", "name"), [(_wide_form(4095), "root.f0"), (_deep_form(64), None)], ids=["nodes", "levels"]
)
def test_field_trees_within_the_read_limits_import_partially(source: bytes, name: str | None) -> None:
    dst = pylopdf.Document()
    dst.pages.extend(pylopdf.open(source).pages[0:1])
    names = [field["name"] for field in dst.get_form_fields()]
    assert names == [name or ".".join(f"l{level}" for level in range(64))]


def test_complete_import_keeps_a_field_tree_beyond_the_read_limits() -> None:
    src = pylopdf.open(_wide_form(4096))
    dst = pylopdf.Document()
    dst.pages.extend(src.pages)
    for doc in (src, dst):
        with pytest.raises(pylopdf.PdfError, match="4096-node"):
            doc.get_form_fields()


def _flat_form(count: int) -> bytes:
    """Build two pages and `count` widgetless top-level fields."""
    objects: dict[int, bytes | str] = {
        1: "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields ["
        + " ".join(f"{5 + index} 0 R" for index in range(count))
        + "] >> >>",
        2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 100 100] >>",
        3: "<< /Type /Page /Parent 2 0 R >>",
        4: "<< /Type /Page /Parent 2 0 R >>",
    }
    for index in range(count):
        objects[5 + index] = f"<< /FT /Tx /T (f{index}) >>"
    return build_raw_pdf(objects)


def test_partial_import_refuses_more_than_8192_top_level_fields(one_page_pdf: bytes) -> None:
    dst = pylopdf.open(one_page_pdf)
    before = dst.to_bytes()
    with pytest.raises(pylopdf.PdfError, match="8192-edge"):
        dst.pages.extend(pylopdf.open(_flat_form(8193)).pages[0:1])
    assert dst.to_bytes() == before


def test_complete_import_never_refuses_a_large_form() -> None:
    """Copy `/Fields` without walking it, so its size only limits later reads."""
    dst = pylopdf.open(_receiving_form("[" + "5 0 R " * 8192 + "]", objects={5: "<< /FT /Tx /T (own) >>"}))
    dst.pages.extend(pylopdf.open(_named_fields(["a"])).pages)
    dst.pages.extend(pylopdf.open(_flat_form(8193)).pages)
    assert len(dst.pages) == 4
    assert _reference_count(b"Fields", _acroform(_objects(dst.to_bytes()))) == 8192 + 1 + 8193
    with pytest.raises(pylopdf.PdfError, match="8192-edge"):
        dst.get_form_fields()


def test_annotations_listed_as_fields_are_not_imported() -> None:
    """pdf.js `issue19281.pdf` lists 35,200 link annotations in `/Fields`."""
    links = range(6, 6 + 9000)
    src = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields ["
                + " ".join(f"{number} 0 R" for number in (5, *links))
                + "] >> >>",
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 100 100] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots [5 0 R 6 0 R] >>",
                4: "<< /Type /Page /Parent 2 0 R >>",
                5: "<< /FT /Tx /T (only) /Type /Annot /Subtype /Widget /Rect [0 0 10 10] /P 3 0 R >>",
                **dict.fromkeys(links, "<< /Type /Annot /Subtype /Link /Rect [0 0 10 10] /F 4 >>"),
            }
        )
    )
    for pages in ((0, 1), (0, 0)):
        dst = pylopdf.Document()
        dst.pages.extend(src.pages[pages[0] : pages[1] + 1])
        assert [field["name"] for field in dst.get_form_fields()] == ["only"]
        assert _reference_count(b"Fields", _acroform(_objects(dst.to_bytes()))) == 1


def test_import_refuses_top_level_names_beyond_the_name_limit() -> None:
    dst = pylopdf.open(_named_fields(["a"]))
    before = dst.to_bytes()
    with pytest.raises(pylopdf.PdfError, match="field-name text exceeds"):
        dst.pages.extend(pylopdf.open(_named_fields(["x" * (1024 * 1024 + 1)])).pages)
    assert dst.to_bytes() == before
