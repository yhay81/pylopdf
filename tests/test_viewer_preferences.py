"""``Document.viewer_preferences``: the catalog's ``/ViewerPreferences``."""

from __future__ import annotations

import dataclasses

import pytest
from conftest import build_raw_pdf

import pylopdf
from pylopdf import Duplex, PageBoundary, PageMode, PrintScaling, ReadingDirection, ViewerPreferences


def _pdf(preferences: str) -> bytes:
    return build_raw_pdf(
        {
            1: f"<< /Type /Catalog /Pages 2 0 R /ViewerPreferences {preferences} >>",
            2: "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
        }
    )


def test_every_entry_reads_as_a_typed_field() -> None:
    preferences = (
        "<< /HideToolbar true /HideMenubar false /HideWindowUI true /FitWindow true /CenterWindow false "
        "/DisplayDocTitle true /NonFullScreenPageMode /UseOutlines /Direction /R2L /ViewArea /CropBox "
        "/ViewClip /MediaBox /PrintArea /BleedBox /PrintClip /ArtBox /PrintScaling /None "
        "/Duplex /DuplexFlipLongEdge /PickTrayByPDFSize true /PrintPageRange [1 3 5 5] /NumCopies 2 "
        "/Enforce [/PrintScaling] >>"
    )
    doc = pylopdf.open(_pdf(preferences))

    assert doc.viewer_preferences == ViewerPreferences(
        hide_toolbar=True,
        hide_menubar=False,
        hide_window_ui=True,
        fit_window=True,
        center_window=False,
        display_title=True,
        non_full_screen_page_mode=PageMode.USE_OUTLINES,
        direction=ReadingDirection.R2L,
        view_area=PageBoundary.CROP,
        view_clip=PageBoundary.MEDIA,
        print_area=PageBoundary.BLEED,
        print_clip=PageBoundary.ART,
        print_scaling=PrintScaling.NONE,
        duplex=Duplex.FLIP_LONG_EDGE,
        pick_tray_by_pdf_size=True,
        print_page_ranges=((0, 2), (4, 4)),
        copies=2,
        enforce=("PrintScaling",),
    )


def test_assigning_a_record_replaces_the_dictionary_and_round_trips() -> None:
    doc = pylopdf.open(_pdf("<< /HideToolbar true /Unknown 5 >>"))
    preferences = ViewerPreferences(
        display_title=True,
        direction=ReadingDirection.R2L,
        duplex=Duplex.SIMPLEX,
        print_page_ranges=((0, 0), (2, 9)),
        copies=3,
        enforce=("PrintScaling",),
    )

    doc.viewer_preferences = preferences

    stored = doc.catalog["ViewerPreferences"]
    assert sorted(stored) == ["Direction", "DisplayDocTitle", "Duplex", "Enforce", "NumCopies", "PrintPageRange"]
    assert list(stored["PrintPageRange"]) == [1, 1, 3, 10]
    reopened = pylopdf.open(doc.to_bytes())
    assert reopened.viewer_preferences == preferences
    assert reopened.viewer_preferences.direction is ReadingDirection.R2L

    reopened.viewer_preferences = dataclasses.replace(preferences, copies=None, print_page_ranges=())
    assert reopened.viewer_preferences == dataclasses.replace(preferences, copies=None, print_page_ranges=())
    del reopened.viewer_preferences
    assert reopened.viewer_preferences is None
    del reopened.viewer_preferences


def test_an_empty_record_writes_an_empty_dictionary() -> None:
    doc = pylopdf.Document()

    assert doc.viewer_preferences is None
    doc.viewer_preferences = ViewerPreferences()

    assert doc.viewer_preferences == ViewerPreferences()
    assert dict(doc.catalog["ViewerPreferences"]) == {}


def test_malformed_entries_read_as_absent() -> None:
    preferences = (
        "<< /HideToolbar 1 /Direction /Up /NonFullScreenPageMode /FullScreen /PrintScaling (None) "
        "/PrintPageRange [1 2 3] /NumCopies 0 /Enforce [(PrintScaling)] /FitWindow true >>"
    )
    doc = pylopdf.open(_pdf(preferences))

    assert doc.viewer_preferences == ViewerPreferences(fit_window=True)
    assert pylopdf.open(_pdf("<< /Enforce [/PrintScaling 42] >>")).viewer_preferences == ViewerPreferences()
    assert pylopdf.open(_pdf("<< /Enforce [] >>")).viewer_preferences == ViewerPreferences(enforce=())
    assert pylopdf.open(_pdf("[/FitWindow]")).viewer_preferences is None
    assert pylopdf.open(_pdf("<< /PrintPageRange [3 1] >>")).viewer_preferences == ViewerPreferences()


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"hide_toolbar": 1}, TypeError),
        ({"direction": "up"}, ValueError),
        ({"non_full_screen_page_mode": PageMode.FULL_SCREEN}, ValueError),
        ({"print_page_ranges": [(2, 1)]}, ValueError),
        ({"print_page_ranges": [(-1, 1)]}, ValueError),
        ({"print_page_ranges": [(0, 1, 2)]}, ValueError),
        ({"print_page_ranges": [(0, 0)] * 4097}, ValueError),
        ({"copies": 0}, ValueError),
        ({"copies": True}, ValueError),
        ({"enforce": "PrintScaling"}, TypeError),
        ({"enforce": ("Print Scaling",)}, ValueError),
    ],
)
def test_fields_are_validated(fields: dict[str, object], error: type[Exception]) -> None:
    with pytest.raises(error):
        ViewerPreferences(**fields)  # ty: ignore[invalid-argument-type]


def test_assignments_need_a_record_and_the_core_repeats_the_checks() -> None:
    doc = pylopdf.open(_pdf("<< >>"))

    with pytest.raises(TypeError):
        doc.viewer_preferences = {"fit_window": True}  # ty: ignore[invalid-assignment]
    core = doc._doc  # noqa: SLF001 - the core boundary.
    refused: tuple[list[tuple[str, object]], ...] = (
        [("FitWindow", 1)],
        [("Direction", "Up")],
        [("Unknown", True)],
        [("NumCopies", 0)],
        [("PrintPageRange", [(2, 1)])],
        [("Enforce", ["a b"])],
    )
    for entries in refused:
        with pytest.raises(pylopdf.PdfError):
            core.set_viewer_preferences(entries)
    assert doc.viewer_preferences == ViewerPreferences()
    assert not doc.is_dirty
