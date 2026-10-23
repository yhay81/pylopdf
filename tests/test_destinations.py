"""doc.destinations: the named destinations of a document."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import Destination, Point

ASSETS = Path(__file__).parent / "assets" / "real_world"


def _named(names: str, legacy: str = "", kids: dict[int, str] | None = None) -> pylopdf.Document:
    catalog = "<< /Type /Catalog /Pages 2 0 R /Names << /Dests 5 0 R >> " + legacy + ">>"
    return pylopdf.open(
        build_raw_pdf(
            {
                1: catalog,
                2: "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
                3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] >>",
                4: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] /Rotate 90 >>",
                5: names,
                **(kids or {}),
            }
        )
    )


def test_named_destinations_resolve_to_display_space() -> None:
    doc = _named(
        "<< /Kids [6 0 R] >>",
        "/Dests << /legacy [3 0 R /FitH 50] /intro [4 0 R /Fit] >> ",
        {
            6: (
                "<< /Names [(intro) [3 0 R /XYZ 10 90 2] (later) << /D [4 0 R /Fit] >> "
                "(nowhere) [99 0 R /Fit] (intro) [4 0 R /Fit]] >>"
            )
        },
    )

    destinations = doc.destinations

    assert list(destinations) == ["intro", "later", "nowhere", "legacy"]
    assert destinations["intro"] == Destination(0, Point(10, 10), 2)
    assert destinations["later"] == Destination(1)
    assert destinations["nowhere"] is None
    assert destinations["legacy"] == Destination(0, Point(0, 50))
    assert len(destinations) == 4
    assert "missing" not in destinations
    with pytest.raises(KeyError):
        destinations["missing"]
    with pytest.raises(TypeError):
        destinations["intro"] = Destination(0)  # ty: ignore[invalid-assignment]


def test_a_document_without_names_has_none() -> None:
    doc = pylopdf.Document()
    doc.pages.new()

    assert dict(doc.destinations) == {}
    assert repr(doc.destinations) == "Destinations({})"


def test_links_and_named_destinations_agree() -> None:
    doc = pylopdf.open(ASSETS / "usrguide.pdf")

    destinations = doc.destinations

    assert len(destinations) == 61
    assert destinations["Doc-Start"] == Destination(0, Point(124.802001953125, 125.79803466796875))
    assert destinations["Hfootnote.1"] is not None
    assert destinations["Hfootnote.1"].page == 25


def test_named_destination_reads_are_bounded() -> None:
    names = " ".join(f"(n{index}) [3 0 R /Fit]" for index in range(65537))
    doc = _named(f"<< /Names [{names}] >>")

    with pytest.raises(pylopdf.LimitError) as raised:
        len(doc.destinations)
    assert raised.value.code == "named_destination_count"


def test_closed_documents_refuse() -> None:
    doc = pylopdf.Document()
    doc.close()

    with pytest.raises(pylopdf.DocumentClosedError):
        _ = doc.destinations
