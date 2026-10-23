"""Page imports carry the optional-content groups that the pages use."""

from __future__ import annotations

from conftest import build_raw_pdf

import pylopdf


def _source(*, properties: str = "<< /OCGs [7 0 R 8 0 R 9 0 R] /D << /OFF [8 0 R] >> >>") -> bytes:
    first = "/OC /L1 BDC BT /F1 12 Tf 20 150 Td (Shown) Tj ET EMC"
    second = "/OC /L2 BDC BT /F1 12 Tf 20 150 Td (Hidden) Tj ET EMC"
    return build_raw_pdf(
        {
            1: f"<< /Type /Catalog /Pages 2 0 R /OCProperties {properties} >>",
            2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
            3: (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 5 0 R "
                "/Resources << /Font << /F1 10 0 R >> /Properties << /L1 7 0 R >> >> >>"
            ),
            4: (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 6 0 R "
                "/Resources << /Font << /F1 10 0 R >> /Properties << /L2 8 0 R >> >> >>"
            ),
            5: f"<< /Length {len(first)} >>\nstream\n{first}\nendstream",
            6: f"<< /Length {len(second)} >>\nstream\n{second}\nendstream",
            7: "<< /Type /OCG /Name (Shown) >>",
            8: "<< /Type /OCG /Name (Hidden) >>",
            9: "<< /Type /OCG /Name (Unused) >>",
            10: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        }
    )


def _states(doc: pylopdf.Document) -> list[tuple[str, bool]]:
    return [(layer.name, layer.is_visible) for layer in doc.layers]


def test_imported_pages_bring_the_groups_they_use_with_their_state() -> None:
    source = pylopdf.open(_source())
    target = pylopdf.Document()
    target.pages.new((200, 200))

    target.pages.extend(source.pages[1])

    assert _states(target) == [("Hidden", False)]
    assert target.pages[1].text() == ""
    order = target.layers.configurations[0].order
    assert order == (target.layers[0],)
    target.layers[0].is_visible = True
    assert target.pages[1].text().split() == ["Hidden"]

    reopened = pylopdf.open(target.to_bytes())
    assert _states(reopened) == [("Hidden", True)]


def test_importing_every_page_brings_only_used_groups_in_ocgs_order() -> None:
    source = pylopdf.open(_source())
    target = pylopdf.Document()

    target.pages.extend(source.pages)

    assert _states(target) == [("Shown", True), ("Hidden", False)]
    assert [page.text().split() for page in target.pages] == [["Shown"], []]


def test_imports_join_existing_optional_content() -> None:
    source = pylopdf.open(_source())
    target = pylopdf.open(_source(properties="<< /OCGs [7 0 R] /D << /BaseState /OFF /ON [7 0 R] /Order [7 0 R] >> >>"))

    target.pages.insert(0, source.pages[0:2])

    names = [layer.name for layer in target.layers]
    assert names == ["Shown", "Shown", "Hidden"]
    default = target.layers.configurations[0]
    assert len(default.order) == 3
    assert [layer.is_visible for layer in target.layers] == [True, True, False]
    assert [item.xref for item in default.on] == [target.layers[0].xref, target.layers[1].xref]
    assert [page.text().split() for page in target.pages][:2] == [["Shown"], []]


def test_importing_twice_makes_independent_groups() -> None:
    source = pylopdf.open(_source())
    target = pylopdf.Document()

    target.pages.extend(source.pages[0])
    target.pages.extend(source.pages[0])
    target.layers[0].is_visible = False

    assert [layer.name for layer in target.layers] == ["Shown", "Shown"]
    assert [page.text().split() for page in target.pages] == [[], ["Shown"]]


def test_sources_without_optional_content_leave_the_target_alone() -> None:
    target = pylopdf.Document()
    target.pages.extend(pylopdf.open(build_raw_pdf_without_layers()).pages)

    assert "OCProperties" not in target.catalog
    assert list(target.layers) == []


def test_copies_within_a_document_share_their_groups() -> None:
    doc = pylopdf.open(_source())

    doc.pages.insert(0, doc.pages[1])

    assert _states(doc) == [("Shown", True), ("Hidden", False), ("Unused", True)]


def build_raw_pdf_without_layers() -> bytes:
    return build_raw_pdf(
        {
            1: "<< /Type /Catalog /Pages 2 0 R >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
        }
    )
