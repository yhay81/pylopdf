"""Tests for Page.get_links annotation reads and destination resolution.

Resolve named destinations through a /Names tree in the real-world
pdfTeX/hyperref usrguide.pdf, and direct /Dest arrays in a minimal fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf

ASSETS = Path(__file__).parent / "assets" / "real_world"
MAX_NAMED_DEST_ENTRIES = 65_536
MAX_NAMED_DEST_NODES = 65_536
MAX_NAMED_DEST_EDGES = 131_072
MAX_NAMED_DEST_TEXT_BYTES = 16 * 1024 * 1024


def _build_direct_dest_fixture() -> bytes:
    """Build a minimal two-page PDF with one direct /Dest array link."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Annots [5 0 R] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
        b"<< /Type /Annot /Subtype /Link /Rect [10 10 100 30] /Dest [4 0 R /XYZ 5 195 null] >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n").encode()
    return bytes(out)


def _build_named_dest_tree_fixture(tree_objects: dict[int, str | bytes]) -> bytes:
    """Build one named link and a caller-provided `/Names/Dests` tree."""
    objects: dict[int, str | bytes] = {
        1: "<< /Type /Catalog /Pages 2 0 R /Names << /Dests 6 0 R >> >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Annots [4 0 R] >>",
        4: "<< /Type /Annot /Subtype /Link /Rect [1 1 10 10] /Dest (missing) >>",
        5: "<< >>",
    }
    objects.update(tree_objects)
    return build_raw_pdf(objects)


def test_direct_dest_array() -> None:
    """Resolve a direct /Dest array to a page number and target point."""
    doc = pylopdf.open(_build_direct_dest_fixture())
    links = doc.pages[0].get_links()
    assert len(links) == 1
    link = links[0]
    assert link["kind"] == pylopdf.LINK_GOTO
    assert link["page"] == 1
    # Display coordinates for crop [0,0,200,200] without rotation: (x, 200-y).
    assert link["from"] == pylopdf.Rect(10.0, 170.0, 100.0, 190.0)
    assert link["to"] == pylopdf.Point(5.0, 5.0)
    assert "zoom" not in link  # A null value means no zoom.
    assert "nameddest" not in link
    assert doc.pages[1].get_links() == []
    doc.close()


def _build_integer_dest_fixture(link: str, catalog_extra: str = "") -> bytes:
    """Build two pages whose first page holds one link using ``link`` entries."""
    return build_raw_pdf(
        {
            1: f"<< /Type /Catalog /Pages 2 0 R{catalog_extra} >>",
            2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Annots [5 0 R] >>",
            4: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
            5: f"<< /Type /Annot /Subtype /Link /Rect [10 10 100 30] {link} >>",
        }
    )


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        (0, 0),
        (1, 1),
        (2, -1),
        (7, -1),
        (-1, -1),
        # Former truncating casts wrapped these to page -1, 0, and 1.
        (2**32 - 1, -1),
        (2**32, -1),
        (2**32 + 1, -1),
        (2**63 - 1, -1),
    ],
)
@pytest.mark.parametrize("entry", ["/Dest [{index} /XYZ 5 195 null]", "/A << /S /GoTo /D [{index} /XYZ 5 195 null] >>"])
def test_integer_destination_page_is_checked_against_the_page_count(index: int, expected: int, entry: str) -> None:
    doc = pylopdf.open(_build_integer_dest_fixture(entry.format(index=index)))
    (link,) = doc.pages[0].get_links()

    assert link["kind"] == pylopdf.LINK_GOTO
    assert link["page"] == expected
    if expected >= 0:
        assert link["to"] == pylopdf.Point(5.0, 5.0)
    else:
        assert "to" not in link
    assert "nameddest" not in link


def test_named_destination_with_out_of_range_integer_page_is_unresolved() -> None:
    doc = pylopdf.open(
        _build_integer_dest_fixture(
            "/Dest (far)",
            " /Names << /Dests << /Names [(far) [4294967297 /Fit] (near) [1 /Fit]] >> >>",
        )
    )
    (link,) = doc.pages[0].get_links()

    assert link["page"] == -1
    assert link["nameddest"] == "far"
    assert "to" not in link


def test_uri_link_roundtrip() -> None:
    """Read back a URI link created with insert_link."""
    doc = pylopdf.open(build_pdf(["Hello link"]))
    page = doc.pages[0]
    rect = (10.0, 20.0, 110.0, 40.0)
    page.insert_link({"kind": pylopdf.LINK_URI, "from": rect, "uri": "https://example.com/"})
    links = page.get_links()
    assert len(links) == 1
    link = links[0]
    assert link["kind"] == pylopdf.LINK_URI
    assert link["uri"] == "https://example.com/"
    got = link["from"]
    assert (got.x0, got.y0, got.x1, got.y1) == rect
    doc.close()


def test_usrguide_named_destinations() -> None:
    """Resolve every named destination in a pdfTeX/hyperref PDF."""
    doc = pylopdf.open(ASSETS / "usrguide.pdf")
    goto = []
    uri = []
    for page_number in range(len(doc.pages)):
        for link in doc.pages[page_number].get_links():
            if link["kind"] == pylopdf.LINK_GOTO:
                goto.append(link)
            elif link["kind"] == pylopdf.LINK_URI:
                uri.append(link)
    assert len(goto) == 40
    assert len(uri) == 2
    # Resolve all entries through the two-level /Names tree.
    assert all(link["page"] >= 0 for link in goto)
    assert all(link["nameddest"] for link in goto)
    assert all(isinstance(link.get("to"), pylopdf.Point) for link in goto)
    first = goto[0]
    assert first["nameddest"] == "section.1"
    assert first["page"] == 1
    assert all(link["uri"] is not None and link["uri"].startswith(("http://", "https://")) for link in uri)
    doc.close()


def test_named_destination_reference_cycle_is_visited_once() -> None:
    doc = pylopdf.open(_build_named_dest_tree_fixture({6: "<< /Kids [6 0 R] >>"}))
    links = doc.pages[0].get_links()
    assert len(links) == 1
    assert links[0]["page"] == -1
    assert links[0]["nameddest"] == "missing"


def test_multiple_named_links_share_one_destination_tree() -> None:
    doc = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /Names << /Dests 7 0 R >> >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Annots [4 0 R 5 0 R] >>",
                4: "<< /Type /Annot /Subtype /Link /Rect [1 1 10 10] /Dest (alpha) >>",
                5: "<< /Type /Annot /Subtype /Link /Rect [20 1 30 10] /Dest (beta) >>",
                6: "<< >>",
                7: "<< /Names [(alpha) [3 0 R /Fit] (beta) [3 0 R /Fit]] >>",
            }
        )
    )
    links = doc.pages[0].get_links()
    assert [link["nameddest"] for link in links] == ["alpha", "beta"]
    assert [link["page"] for link in links] == [0, 0]


def test_named_destination_tree_rejects_excessive_depth() -> None:
    objects: dict[int, str | bytes] = {}
    for object_id in range(6, 38):
        objects[object_id] = f"<< /Kids [{object_id + 1} 0 R] >>"
    objects[38] = "<< /Names [(target) [3 0 R /Fit]] >>"

    doc = pylopdf.open(_build_named_dest_tree_fixture(objects))
    with pytest.raises(pylopdf.PdfError, match="32-level safety limit"):
        doc.pages[0].get_links()


def test_named_destination_tree_rejects_excessive_entries() -> None:
    pairs = " ".join(f"(key{index}) [3 0 R /Fit]" for index in range(MAX_NAMED_DEST_ENTRIES + 1))
    doc = pylopdf.open(_build_named_dest_tree_fixture({6: f"<< /Names [{pairs}] >>"}))
    with pytest.raises(pylopdf.LimitError, match="65536-entry safety limit") as caught:
        doc.pages[0].get_links()
    assert caught.value.code == "named_destination_count"


def test_named_destination_tree_rejects_excessive_nodes() -> None:
    kids = "<< >> " * MAX_NAMED_DEST_NODES
    doc = pylopdf.open(_build_named_dest_tree_fixture({6: f"<< /Kids [{kids}] >>"}))
    with pytest.raises(pylopdf.LimitError, match="65536-node safety limit") as caught:
        doc.pages[0].get_links()
    assert caught.value.code == "named_destination_count"


def test_named_destination_tree_rejects_excessive_edges() -> None:
    kids = "7 0 R " * (MAX_NAMED_DEST_EDGES + 1)
    doc = pylopdf.open(
        _build_named_dest_tree_fixture(
            {
                6: f"<< /Kids [{kids}] >>",
                7: "<< >>",
            }
        )
    )
    with pytest.raises(pylopdf.LimitError, match="131072-edge safety limit") as caught:
        doc.pages[0].get_links()
    assert caught.value.code == "named_destination_count"


def test_named_destination_tree_rejects_excessive_key_bytes() -> None:
    key = "x" * 1024
    pairs = f"({key}) [3 0 R /Fit] " * (MAX_NAMED_DEST_TEXT_BYTES // len(key) + 1)
    doc = pylopdf.open(_build_named_dest_tree_fixture({6: f"<< /Names [{pairs}] >>"}))
    with pytest.raises(pylopdf.LimitError, match="16777216-byte safety limit") as caught:
        doc.pages[0].get_links()
    assert caught.value.code == "named_destination_text_size"


def _build_import_link_fixture(*, legacy: bool = False, action: bool = True) -> bytes:
    """Mix source-local, URI, and remote links in an indirect annotation array."""
    catalog_destinations = "/Dests << /section 7 0 R >>" if legacy else "/Names << /Dests 7 0 R >>"
    local_destination = "/A 6 0 R" if action else "/Dest /section"
    destination = "<< /D [4 0 R /XYZ 5 195 1.25] >>"
    return build_raw_pdf(
        {
            1: f"<< /Type /Catalog /Pages 2 0 R {catalog_destinations} >>",
            2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Annots 8 0 R >>",
            4: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
            5: f"<< /Type /Annot /Subtype /Link /Rect [10 10 100 30] {local_destination} >>",
            6: "<< /S /GoTo /D (section) >>",
            7: destination if legacy else f"<< /Names [(section) {destination}] >>",
            8: (
                "[5 0 R "
                "<< /Type /Annot /Subtype /Link /Rect [10 40 100 60] "
                "/A << /S /URI /URI (https://example.com/) >> >> "
                "<< /Type /Annot /Subtype /Link /Rect [10 70 100 90] "
                "/A << /S /GoToR /F (remote.pdf) /D (section) >> >>]"
            ),
        }
    )


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("action", [False, True])
def test_insert_pdf_resolves_source_named_destinations(*, legacy: bool, action: bool) -> None:
    source = pylopdf.open(_build_import_link_fixture(legacy=legacy, action=action))
    source_bytes = source.to_bytes()
    target = pylopdf.Document()
    target.pages.new((200, 200))

    target.pages.extend(source.pages)

    for merged in [target, pylopdf.open(target.to_bytes())]:
        links = merged.pages[1].get_links()
        assert [link["kind"] for link in links] == [pylopdf.LINK_GOTO, pylopdf.LINK_URI, pylopdf.LINK_GOTOR]
        assert links[0]["page"] == 2
        assert links[0]["to"] == pylopdf.Point(5, 5)
        assert links[0]["zoom"] == pytest.approx(1.25)
        assert "nameddest" not in links[0]
        assert links[1]["uri"] == "https://example.com/"
        assert links[2]["file"] == "remote.pdf"
        assert links[2]["nameddest"] == "section"
    assert source.to_bytes() == source_bytes
    assert source.pages[0].get_links()[0]["nameddest"] == "section"


def test_insert_pdf_named_destination_collision_stays_source_local() -> None:
    source = pylopdf.open(_build_import_link_fixture())
    target = pylopdf.open(_build_import_link_fixture())

    target.pages.extend(source.pages)

    assert target.pages[0].get_links()[0]["page"] == 1
    assert target.pages[2].get_links()[0]["page"] == 3
    assert pylopdf.open(target.to_bytes()).pages[2].get_links()[0]["page"] == 3


def test_insert_pdf_reversed_range_remaps_internal_link() -> None:
    source = pylopdf.open(_build_import_link_fixture())
    target = pylopdf.open(build_pdf(["Before", "After"]))

    target.pages.insert(1, source.pages[0:2][::-1])

    assert target.pages[2].get_links()[0]["page"] == 1
    assert target.pages[2].get_links()[0]["to"] == pylopdf.Point(5, 5)
    assert "Before" in target.pages[0].get_text()
    assert "After" in target.pages[3].get_text()


def test_insert_pdf_reversed_range_remaps_integer_destination() -> None:
    source = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 200 200] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots [5 0 R] >>",
                4: "<< /Type /Page /Parent 2 0 R >>",
                5: "<< /Type /Annot /Subtype /Link /Rect [10 10 100 30] /Dest [1 /XYZ 5 195 null] >>",
            }
        )
    )
    target = pylopdf.Document()

    target.pages.extend(source.pages[0:2][::-1])

    assert target.pages[1].get_links()[0]["page"] == 0
    assert target.pages[1].get_links()[0]["to"] == pylopdf.Point(5, 5)


@pytest.mark.parametrize("named", [False, True])
def test_insert_pdf_omits_links_outside_imported_range(*, named: bool) -> None:
    source = pylopdf.open(_build_import_link_fixture() if named else _build_direct_dest_fixture())
    source_bytes = source.to_bytes()
    target = pylopdf.Document()

    target.pages.extend(source.pages[0:1])

    expected = [pylopdf.LINK_URI, pylopdf.LINK_GOTOR] if named else []
    assert [link["kind"] for link in target.pages[0].get_links()] == expected
    assert [link["kind"] for link in pylopdf.open(target.to_bytes()).pages[0].get_links()] == expected
    assert source.to_bytes() == source_bytes


def test_insert_pdf_unresolved_name_does_not_bind_to_destination_catalog() -> None:
    source = pylopdf.open(_build_named_dest_tree_fixture({6: "<< /Names [(other) [3 0 R /Fit]] >>"}))
    target = pylopdf.open(_build_named_dest_tree_fixture({6: "<< /Names [(missing) [3 0 R /Fit]] >>"}))

    target.pages.extend(source.pages)

    assert target.pages[0].get_links()[0]["page"] == 0
    assert target.pages[1].get_links() == []


def _build_import_target(shape: str, populated_pdf: bytes) -> pylopdf.Document:
    if shape == "populated":
        return pylopdf.open(populated_pdf)
    if shape == "empty":
        return pylopdf.Document()
    return pylopdf.open(build_raw_pdf({1: "<< /Type /Catalog >>"}))


@pytest.mark.parametrize("target_shape", ["populated", "empty", "missing_pages"])
def test_insert_pdf_named_destination_limit_is_atomic(target_shape: str) -> None:
    objects: dict[int, str | bytes] = {}
    for object_id in range(6, 38):
        objects[object_id] = f"<< /Kids [{object_id + 1} 0 R] >>"
    objects[38] = "<< /Names [(missing) [3 0 R /Fit]] >>"
    source = pylopdf.open(_build_named_dest_tree_fixture(objects))
    target = _build_import_target(target_shape, build_pdf(["Unchanged"]))
    page = target.pages[0] if target_shape == "populated" else None
    before = target.to_bytes()
    source_before = source.to_bytes()
    expected_error = "Catalog Pages must be" if target_shape == "missing_pages" else "32-level safety limit"

    with pytest.raises(pylopdf.PdfError, match=expected_error):
        target.pages.extend(source.pages)

    assert target.to_bytes() == before
    assert source.to_bytes() == source_before
    if page is not None:
        assert "Unchanged" in page.get_text()


@pytest.mark.parametrize("target_shape", ["populated", "empty", "missing_pages"])
def test_insert_pdf_refuses_annotation_aliasing_a_page_before_mutation(target_shape: str) -> None:
    source = pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R /Names << /Dests 5 0 R >> >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                3: (
                    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Annots [3 0 R] "
                    "/Subtype /Link /Rect [1 1 10 10] /Dest (section) >>"
                ),
                4: "<< >>",
                5: "<< /Names [(section) [3 0 R /Fit]] >>",
            }
        )
    )
    target = _build_import_target(target_shape, _build_import_link_fixture())
    existing = target.pages[0] if target_shape == "populated" else None
    before = target.to_bytes()
    source_before = source.to_bytes()
    expected_error = "Catalog Pages must be" if target_shape == "missing_pages" else "annotation cannot alias a page"

    with pytest.raises(pylopdf.PdfError, match=expected_error):
        target.pages.extend(source.pages)

    assert target.to_bytes() == before
    if existing is not None:
        assert existing.get_links()[0]["page"] == 1
    assert source.to_bytes() == source_before


def test_usrguide_named_destinations_survive_merge() -> None:
    source = pylopdf.open(ASSETS / "usrguide.pdf")
    original_links = [page.get_links() for page in source.pages]
    target = pylopdf.Document()
    target.pages.new()

    target.pages.extend(source.pages)

    merged = pylopdf.open(target.to_bytes())
    for page_number, expected in enumerate(original_links, start=1):
        actual = merged.pages[page_number].get_links()
        assert len(actual) == len(expected)
        for before, after in zip(expected, actual, strict=True):
            assert after["kind"] == before["kind"]
            if before["kind"] == pylopdf.LINK_GOTO:
                assert after["page"] == before["page"] + 1
                assert after.get("to") == before.get("to")
                assert after.get("zoom") == before.get("zoom")
            else:
                assert after["uri"] == before["uri"]


def test_insert_pdf_refusal_preserves_original_interpretation_fast_path() -> None:
    """Keep a bounded original snapshot usable after source-navigation refusal."""
    compressed = pylopdf.open(build_pdf(["Unchanged"] * 8)).to_bytes(object_streams=True)
    limit = len(compressed) + 10
    assert len(pylopdf.open(compressed).to_bytes()) > limit
    target = pylopdf.open(compressed, limits=pylopdf.DocumentLimits(max_interpretation_size=limit))
    page = target.pages[0]
    original_text = page.get_text()
    original_png = page.get_pixmap(alpha=True).tobytes()
    # Serialize without the options that change the document and its snapshot.
    original_bytes = target.to_bytes(compress=False, prune=False)

    objects: dict[int, str | bytes] = {}
    for object_id in range(6, 38):
        objects[object_id] = f"<< /Kids [{object_id + 1} 0 R] >>"
    objects[38] = "<< /Names [(missing) [3 0 R /Fit]] >>"
    source = pylopdf.open(_build_named_dest_tree_fixture(objects))

    with pytest.raises(pylopdf.PdfError, match="32-level safety limit"):
        target.pages.extend(source.pages)

    assert target.to_bytes(compress=False, prune=False) == original_bytes
    assert page.get_text() == original_text
    assert page.get_pixmap(alpha=True).tobytes() == original_png
