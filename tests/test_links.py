"""Tests for Page.links: reading, adding, editing, and removing link annotations.

Resolve named destinations through a /Names tree in the real-world
pdfTeX/hyperref usrguide.pdf, and direct /Dest arrays in minimal fixtures.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import build_pdf, build_raw_pdf

import pylopdf
from pylopdf import Destination, Launch, NamedAction, Point, Rect, RemoteDestination

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
    """Resolve a direct /Dest array to a page and a target point."""
    doc = pylopdf.open(_build_direct_dest_fixture())
    (link,) = doc.pages[0].links
    # Display coordinates for crop [0,0,200,200] without rotation: (x, 200-y).
    assert link.rect == Rect(10.0, 170.0, 100.0, 190.0)
    assert link.target == Destination(1, Point(5.0, 5.0))  # A null zoom keeps the viewer's.
    assert link.xref == 5
    assert link.page == doc.pages[0]
    assert len(doc.pages[1].links) == 0
    assert not doc.pages[1].links
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
        (2, None),
        (7, None),
        (-1, None),
        # Former truncating casts wrapped these to page -1, 0, and 1.
        (2**32 - 1, None),
        (2**32, None),
        (2**32 + 1, None),
        (2**63 - 1, None),
    ],
)
@pytest.mark.parametrize("entry", ["/Dest [{index} /XYZ 5 195 null]", "/A << /S /GoTo /D [{index} /XYZ 5 195 null] >>"])
def test_integer_destination_page_is_checked_against_the_page_count(
    index: int, expected: int | None, entry: str
) -> None:
    doc = pylopdf.open(_build_integer_dest_fixture(entry.format(index=index)))
    (link,) = doc.pages[0].links

    assert link.target == (None if expected is None else Destination(expected, Point(5.0, 5.0)))


def test_named_destination_with_out_of_range_integer_page_leads_nowhere() -> None:
    doc = pylopdf.open(
        _build_integer_dest_fixture(
            "/Dest (far)",
            " /Names << /Dests << /Names [(far) [4294967297 /Fit] (near) [1 /Fit]] >> >>",
        )
    )
    (link,) = doc.pages[0].links

    assert link.target is None


@pytest.mark.parametrize(
    ("destination", "expected"),
    [
        ("[4 0 R /XYZ null null 2]", Destination(1, zoom=2.0)),
        ("[4 0 R /XYZ 5 null 0]", Destination(1, Point(5, 0))),
        ("[4 0 R /FitH null]", Destination(1)),
        ("[4 0 R /FitH 150]", Destination(1, Point(0, 50))),
        ("[4 0 R /FitV 20]", Destination(1, Point(20, 0))),
        ("[4 0 R /FitR 1 2 3 4]", Destination(1)),
    ],
)
def test_a_destination_without_coordinates_has_no_point(destination: str, expected: Destination) -> None:
    doc = pylopdf.open(_build_integer_dest_fixture(f"/Dest {destination}"))

    assert doc.pages[0].links[0].target == expected


def test_uri_link_roundtrip() -> None:
    doc = pylopdf.open(build_pdf(["Hello link"]))
    page = doc.pages[0]
    rect = Rect(10.0, 20.0, 110.0, 40.0)

    link = page.links.add(rect, "https://example.com/")

    assert link.rect == rect
    assert link.target == "https://example.com/"
    assert list(page.links) == [link]
    reopened = pylopdf.open(doc.to_bytes()).pages[0].links[0]
    assert (reopened.rect, reopened.target) == (rect, "https://example.com/")
    doc.close()


def test_usrguide_named_destinations() -> None:
    """Resolve every named destination in a pdfTeX/hyperref PDF."""
    doc = pylopdf.open(ASSETS / "usrguide.pdf")
    targets = [link.target for page in doc.pages for link in page.links]
    goto = [target for target in targets if isinstance(target, Destination)]
    uri = [target for target in targets if isinstance(target, str)]
    assert len(goto) == 40
    assert len(uri) == 2
    assert len(targets) == 42
    # Resolve all entries through the two-level /Names tree.
    assert all(isinstance(target.point, Point) for target in goto)
    assert goto[0].page == 1
    assert all(target.startswith(("http://", "https://")) for target in uri)
    doc.close()


def test_named_destination_reference_cycle_is_visited_once() -> None:
    doc = pylopdf.open(_build_named_dest_tree_fixture({6: "<< /Kids [6 0 R] >>"}))
    (link,) = doc.pages[0].links
    assert link.target is None


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
    assert [link.target for link in doc.pages[0].links] == [Destination(0), Destination(0)]


def test_named_destination_tree_rejects_excessive_depth() -> None:
    objects: dict[int, str | bytes] = {}
    for object_id in range(6, 38):
        objects[object_id] = f"<< /Kids [{object_id + 1} 0 R] >>"
    objects[38] = "<< /Names [(target) [3 0 R /Fit]] >>"

    doc = pylopdf.open(_build_named_dest_tree_fixture(objects))
    with pytest.raises(pylopdf.PdfError, match="32-level safety limit"):
        list(doc.pages[0].links)


def test_named_destination_tree_rejects_excessive_entries() -> None:
    pairs = " ".join(f"(key{index}) [3 0 R /Fit]" for index in range(MAX_NAMED_DEST_ENTRIES + 1))
    doc = pylopdf.open(_build_named_dest_tree_fixture({6: f"<< /Names [{pairs}] >>"}))
    with pytest.raises(pylopdf.LimitError, match="65536-entry safety limit") as caught:
        list(doc.pages[0].links)
    assert caught.value.code == "named_destination_count"


def test_named_destination_tree_rejects_excessive_nodes() -> None:
    kids = "<< >> " * MAX_NAMED_DEST_NODES
    doc = pylopdf.open(_build_named_dest_tree_fixture({6: f"<< /Kids [{kids}] >>"}))
    with pytest.raises(pylopdf.LimitError, match="65536-node safety limit") as caught:
        list(doc.pages[0].links)
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
        list(doc.pages[0].links)
    assert caught.value.code == "named_destination_count"


def test_named_destination_tree_rejects_excessive_key_bytes() -> None:
    key = "x" * 1024
    pairs = f"({key}) [3 0 R /Fit] " * (MAX_NAMED_DEST_TEXT_BYTES // len(key) + 1)
    doc = pylopdf.open(_build_named_dest_tree_fixture({6: f"<< /Names [{pairs}] >>"}))
    with pytest.raises(pylopdf.LimitError, match="16777216-byte safety limit") as caught:
        list(doc.pages[0].links)
    assert caught.value.code == "named_destination_text_size"


def _page_of(link: pylopdf.Link) -> int | None:
    target = link.target
    return target.page if isinstance(target, Destination) else None


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
        assert [link.target for link in merged.pages[1].links] == [
            Destination(2, Point(5, 5), 1.25),
            "https://example.com/",
            RemoteDestination("remote.pdf", name="section"),
        ]
    assert source.to_bytes() == source_bytes
    assert source.pages[0].links[0].target == Destination(1, Point(5, 5), 1.25)


def test_insert_pdf_named_destination_collision_stays_source_local() -> None:
    source = pylopdf.open(_build_import_link_fixture())
    target = pylopdf.open(_build_import_link_fixture())

    target.pages.extend(source.pages)

    assert _page_of(target.pages[0].links[0]) == 1
    assert _page_of(target.pages[2].links[0]) == 3
    assert _page_of(pylopdf.open(target.to_bytes()).pages[2].links[0]) == 3


def test_insert_pdf_reversed_range_remaps_internal_link() -> None:
    source = pylopdf.open(_build_import_link_fixture())
    target = pylopdf.open(build_pdf(["Before", "After"]))

    target.pages.insert(1, source.pages[0:2][::-1])

    assert target.pages[2].links[0].target == Destination(1, Point(5, 5), 1.25)
    assert "Before" in target.pages[0].text()
    assert "After" in target.pages[3].text()


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

    assert target.pages[1].links[0].target == Destination(0, Point(5, 5))


@pytest.mark.parametrize("named", [False, True])
def test_insert_pdf_omits_links_outside_imported_range(*, named: bool) -> None:
    source = pylopdf.open(_build_import_link_fixture() if named else _build_direct_dest_fixture())
    source_bytes = source.to_bytes()
    target = pylopdf.Document()

    target.pages.extend(source.pages[0:1])

    expected = ["https://example.com/", RemoteDestination("remote.pdf", name="section")] if named else []
    assert [link.target for link in target.pages[0].links] == expected
    assert [link.target for link in pylopdf.open(target.to_bytes()).pages[0].links] == expected
    assert source.to_bytes() == source_bytes


def test_insert_pdf_unresolved_name_does_not_bind_to_destination_catalog() -> None:
    source = pylopdf.open(_build_named_dest_tree_fixture({6: "<< /Names [(other) [3 0 R /Fit]] >>"}))
    target = pylopdf.open(_build_named_dest_tree_fixture({6: "<< /Names [(missing) [3 0 R /Fit]] >>"}))

    target.pages.extend(source.pages)

    assert target.pages[0].links[0].target == Destination(0)
    assert len(target.pages[1].links) == 0


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
        assert "Unchanged" in page.text()


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
        assert _page_of(existing.links[0]) == 1
    assert source.to_bytes() == source_before


def test_usrguide_named_destinations_survive_merge() -> None:
    source = pylopdf.open(ASSETS / "usrguide.pdf")
    original = [[(link.rect, link.target) for link in page.links] for page in source.pages]
    target = pylopdf.Document()
    target.pages.new()

    target.pages.extend(source.pages)

    merged = pylopdf.open(target.to_bytes())
    for page_number, expected in enumerate(original, start=1):
        actual = [(link.rect, link.target) for link in merged.pages[page_number].links]
        assert len(actual) == len(expected)
        for (before_rect, before), (after_rect, after) in zip(expected, actual, strict=True):
            assert after_rect == pytest.approx(before_rect)
            if isinstance(before, Destination):
                assert after == Destination(before.page + 1, before.point, before.zoom)
            else:
                assert after == before


def test_insert_pdf_refusal_preserves_original_interpretation_fast_path() -> None:
    """Keep a bounded original snapshot usable after source-navigation refusal."""
    compressed = pylopdf.open(build_pdf(["Unchanged"] * 8)).to_bytes(object_streams=True)
    limit = len(compressed) + 10
    assert len(pylopdf.open(compressed).to_bytes()) > limit
    target = pylopdf.open(compressed, limits=pylopdf.DocumentLimits(max_interpretation_size=limit))
    page = target.pages[0]
    original_text = page.text()
    original_png = page.pixmap(alpha=True).to_bytes()
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
    assert page.text() == original_text
    assert page.pixmap(alpha=True).to_bytes() == original_png


def _three_page_document() -> pylopdf.Document:
    doc = pylopdf.Document()
    for _ in range(3):
        doc.pages.new((300, 400))
    return doc


TARGETS = [
    "https://example.com/a?b=c",
    Destination(2),
    Destination(1, Point(20, 30)),
    Destination(0, Point(5, 6), 1.5),
    Destination(2, zoom=3.0),
    RemoteDestination("other.pdf", 4),
    RemoteDestination("other.pdf", name="chapter-2"),
    Launch("notes.txt"),
    NamedAction("NextPage"),
]


@pytest.mark.parametrize("target", TARGETS, ids=repr)
def test_every_target_kind_round_trips(target: pylopdf.Target) -> None:
    doc = _three_page_document()
    rect = Rect(10, 20, 60, 40)

    link = doc.pages[0].links.add(rect, target)

    for page in (doc.pages[0], pylopdf.open(doc.to_bytes()).pages[0]):
        (read,) = page.links
        assert (read.rect, read.target) == (rect, target)
    assert link.obj["Subtype"] == "Link"
    assert list(link.obj["Border"]) == [0, 0, 0]


def test_links_are_edited_in_place() -> None:
    doc = _three_page_document()
    page = doc.pages[0]
    link = page.links.add((10, 20, 60, 40), "https://example.com/")
    xref = link.xref

    link.target = Destination(2, Point(1, 2))
    link.rect = (100, 200, 150, 220)

    assert (link.rect, link.target) == (Rect(100, 200, 150, 220), Destination(2, Point(1, 2)))
    assert link.xref == xref
    assert "A" not in link.obj
    reopened = pylopdf.open(doc.to_bytes()).pages[0].links[0]
    assert (reopened.rect, reopened.target) == (Rect(100, 200, 150, 220), Destination(2, Point(1, 2)))

    link.target = NamedAction("FirstPage")
    assert "Dest" not in link.obj
    assert link.target == NamedAction("FirstPage")
    assert doc.is_dirty


def test_links_on_a_rotated_page_use_display_coordinates() -> None:
    doc = _three_page_document()
    page = doc.pages[0]
    page.rotation = 90

    link = page.links.add((10, 20, 60, 40), Destination(1, Point(15, 25)))

    assert link.rect == Rect(10, 20, 60, 40)
    assert link.target == Destination(1, Point(15, 25))
    assert list(link.obj["Rect"]) == pytest.approx([20, 10, 40, 60])


def test_deleting_links_removes_them_from_the_page() -> None:
    doc = _three_page_document()
    page = doc.pages[0]
    page.annotations.add(pylopdf.Highlight((0, 0, 10, 10)))
    links = [page.links.add((10, 10 + 30 * row, 60, 30 + 30 * row), f"https://e.test/{row}") for row in range(4)]

    del page.links[1]

    assert list(page.links) == [links[0], links[2], links[3]]
    # Handles find their annotation again after an earlier one went.
    assert links[3].target == "https://e.test/3"
    with pytest.raises(pylopdf.StaleObjectError):
        _ = links[1].rect
    assert repr(links[1]) == "<stale Link>"

    del page.links[::2]

    assert [link.target for link in page.links] == ["https://e.test/2"]
    assert len(page.annotations) == 1  # The highlight stays.
    with pytest.raises(IndexError):
        del page.links[5]
    reopened = pylopdf.open(doc.to_bytes()).pages[0]
    assert [link.target for link in reopened.links] == ["https://e.test/2"]


def test_link_handles_compare_by_page_and_annotation() -> None:
    doc = _three_page_document()
    link = doc.pages[0].links.add((10, 20, 60, 40), "https://example.com/")

    assert doc.pages[0].links[0] == link
    assert hash(doc.pages[0].links[0]) == hash(link)
    assert link != doc.pages[1]
    assert repr(doc.pages[0].links) == f"Links([{link!r}])"
    assert repr(link) == "Link(rect=Rect(x0=10.0, y0=20.0, x1=60.0, y1=40.0), target='https://example.com/')"


def test_a_page_edit_makes_link_handles_stale() -> None:
    doc = _three_page_document()
    link = doc.pages[0].links.add((10, 20, 60, 40), "https://example.com/")

    doc.pages.new()

    with pytest.raises(pylopdf.StalePageError):
        _ = link.target
    with pytest.raises(pylopdf.StalePageError):
        link.target = "https://other.test/"
    assert repr(link) == "<stale Link>"


def _shared_annotations_document() -> pylopdf.Document:
    """Two pages share one indirect /Annots array holding a direct link."""
    return pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 200 200] >>",
                3: "<< /Type /Page /Parent 2 0 R /Annots 5 0 R >>",
                4: "<< /Type /Page /Parent 2 0 R /Annots 5 0 R >>",
                5: (
                    "[6 0 R << /Type /Annot /Subtype /Link /Rect [10 40 100 60] "
                    "/A << /S /URI /URI (https://direct.test/) >> >>]"
                ),
                6: (
                    "<< /Type /Annot /Subtype /Link /Rect [10 10 100 30] "
                    "/A << /S /URI /URI (https://shared.test/) >> >>"
                ),
            }
        )
    )


def test_editing_a_direct_link_leaves_pages_that_share_the_array_alone() -> None:
    doc = _shared_annotations_document()
    first, second = doc.pages[0], doc.pages[1]
    direct = first.links[1]
    assert direct.xref == 0

    direct.target = "https://edited.test/"
    direct.rect = (0, 0, 20, 20)

    # The edit stored the link as an object of its own; the other page keeps its dictionary.
    assert direct.xref > 0
    assert second.links[1].xref == 0

    assert [link.target for link in first.links] == ["https://shared.test/", "https://edited.test/"]
    assert first.links[1].rect == Rect(0, 0, 20, 20)
    assert [link.target for link in second.links] == ["https://shared.test/", "https://direct.test/"]
    assert direct.obj["A"]["URI"] == pylopdf.String(b"https://edited.test/")

    del second.links[0]

    assert [link.target for link in first.links] == ["https://shared.test/", "https://edited.test/"]
    assert [link.target for link in second.links] == ["https://direct.test/"]
    reopened = pylopdf.open(doc.to_bytes())
    assert [link.target for link in reopened.pages[0].links] == ["https://shared.test/", "https://edited.test/"]
    assert [link.target for link in reopened.pages[1].links] == ["https://direct.test/"]


def test_an_action_pylopdf_does_not_read_has_no_target() -> None:
    doc = pylopdf.open(_build_integer_dest_fixture("/A << /S /JavaScript /JS (app.alert(1)) >>"))
    (link,) = doc.pages[0].links

    assert link.target is None
    link.target = "https://example.com/"
    assert link.target == "https://example.com/"


@pytest.mark.parametrize(
    ("rect", "target", "error", "match"),
    [
        ((10, 20, 5, 40), "https://e.test/", ValueError, "x0 < x1"),
        ((10, 20, 60), "https://e.test/", ValueError, "4 numbers"),
        ((10, 20, 60, 40), None, TypeError, "a link needs a target"),
        ((10, 20, 60, 40), "", ValueError, "must not be empty"),
        ((10, 20, 60, 40), Destination(3), ValueError, "out of range"),
        ((10, 20, 60, 40), 5, TypeError, "a target is a Destination"),
    ],
)
def test_adding_an_invalid_link_changes_nothing(
    rect: object, target: object, error: type[Exception], match: str
) -> None:
    doc = _three_page_document()
    before = doc.to_bytes()

    with pytest.raises(error, match=match):
        doc.pages[0].links.add(rect, target)  # ty: ignore[invalid-argument-type]

    assert doc.to_bytes() == before


def test_link_text_is_bounded_before_any_change() -> None:
    doc = _three_page_document()
    link = doc.pages[0].links.add((10, 20, 60, 40), "https://example.com/")
    before = doc.to_bytes()
    long = "https://e.test/" + "x" * (1024 * 1024)

    with pytest.raises(pylopdf.LimitError, match="1048576-byte") as caught:
        doc.pages[0].links.add((10, 20, 60, 40), long)
    assert caught.value.code == "annotation_input_size"
    with pytest.raises(pylopdf.LimitError):
        link.target = RemoteDestination(long, name="x")

    assert doc.to_bytes() == before
    assert link.target == "https://example.com/"


def test_link_files_keep_non_ascii_names() -> None:
    doc = _three_page_document()
    target = RemoteDestination("報告書 – Résumé.pdf", 1)

    doc.pages[0].links.add((10, 20, 60, 40), target)
    doc.pages[0].links.add((10, 50, 60, 70), Launch("ノート.txt"))

    reopened = pylopdf.open(doc.to_bytes()).pages[0]
    assert [link.target for link in reopened.links] == [target, Launch("ノート.txt")]


@pytest.mark.parametrize("target", [Destination(0, (4e38, 10)), Destination(0, zoom=4e38)], ids=["point", "zoom"])
def test_link_destinations_outside_the_pdf_real_range_change_nothing(target: Destination) -> None:
    doc = _three_page_document()
    link = doc.pages[0].links.add((10, 20, 60, 40), "https://example.com/")
    before = doc.to_bytes()

    with pytest.raises(pylopdf.PdfError, match="PDF real-number range"):
        doc.pages[0].links.add((10, 20, 60, 40), target)
    with pytest.raises(pylopdf.PdfError, match="PDF real-number range"):
        link.target = target

    assert doc.to_bytes() == before


def _direct_links_document() -> pylopdf.Document:
    entries = " ".join(
        f"<< /Type /Annot /Subtype /Link /Rect [10 {10 + 30 * row} 100 {30 + 30 * row}] "
        f"/A << /S /URI /URI (https://e.test/{row}) >> >>"
        for row in range(4)
    )
    return pylopdf.open(
        build_raw_pdf(
            {
                1: "<< /Type /Catalog /Pages 2 0 R >>",
                2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 200 200] >>",
                3: f"<< /Type /Page /Parent 2 0 R /Annots [{entries}] >>",
            }
        )
    )


def test_handles_of_direct_links_never_name_another_link() -> None:
    doc = _direct_links_document()
    page = doc.pages[0]
    links = list(page.links)
    assert [link.xref for link in links] == [0, 0, 0, 0]

    del page.links[1]

    # Removing a link stored every link as an object, so the old handles,
    # which found their links by position, are stale rather than shifted.
    for link in links:
        with pytest.raises(pylopdf.StaleObjectError):
            _ = link.target
    current = list(page.links)
    assert [link.target for link in current] == ["https://e.test/0", "https://e.test/2", "https://e.test/3"]
    assert all(link.xref > 0 for link in current)
    current[0].target = "https://edited.test/"
    assert current[1].target == "https://e.test/2"


def test_deleting_several_direct_links_at_once() -> None:
    doc = _direct_links_document()
    page = doc.pages[0]

    del page.links[::2]

    assert [link.target for link in page.links] == ["https://e.test/1", "https://e.test/3"]
    reopened = pylopdf.open(doc.to_bytes()).pages[0]
    assert [link.target for link in reopened.links] == ["https://e.test/1", "https://e.test/3"]
