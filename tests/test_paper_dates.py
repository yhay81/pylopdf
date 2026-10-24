"""``PaperSize``, ``pdf_date``, and ``parse_pdf_date``."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta, timezone

import pytest

import pylopdf
from pylopdf import PaperSize, parse_pdf_date, pdf_date


def test_paper_sizes_are_point_pairs() -> None:
    assert PaperSize.A4 == (595, 842)
    assert (PaperSize.A4.width, PaperSize.A4.height) == (595, 842)
    assert PaperSize.A4.landscape == (842, 595)
    assert PaperSize.LETTER == (612, 792)
    assert PaperSize.TABLOID.landscape == (1224, 792)
    assert PaperSize((612, 792)) is PaperSize.LETTER
    assert len(PaperSize) == 38
    assert all(width <= height for width, height in PaperSize)
    assert repr(PaperSize.A4) == "<PaperSize.A4: (595, 842)>"


def test_iso_sizes_halve_from_one_to_the_next() -> None:
    for series in "ABC":
        sizes = [PaperSize[f"{series}{index}"] for index in range(11)]
        for larger, smaller in itertools.pairwise(sizes):
            assert abs(larger.width - smaller.height) <= 1
            assert abs(larger.height / 2 - smaller.width) <= 3  # ISO rounds the halves down to whole millimetres.


def test_pages_take_paper_sizes() -> None:
    doc = pylopdf.Document()

    doc.pages.new(PaperSize.A5)
    doc.pages.new(PaperSize.A5.landscape)

    assert [page.rect for page in doc.pages] == [pylopdf.Rect(0, 0, 420, 595), pylopdf.Rect(0, 0, 595, 420)]


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=9))), "D:20260101120000+09'00'"),
        (datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=-5, minutes=-30))), "D:20260101120000-05'30'"),
        (datetime(2026, 1, 2, 3, 4, 5, 999_999, tzinfo=UTC), "D:20260102030405Z"),
        (datetime(26, 7, 8, 9, 10, 11), "D:00260708091011"),  # noqa: DTZ001 - a naive time has no offset.
    ],
)
def test_pdf_date_formats_a_datetime(value: datetime, text: str) -> None:
    assert pdf_date(value) == text
    assert parse_pdf_date(text) == value.replace(microsecond=0)


def test_pdf_date_without_a_value_is_now_with_its_offset() -> None:
    before = datetime.now().astimezone().replace(microsecond=0)
    text = pdf_date()
    after = datetime.now().astimezone()

    assert before <= parse_pdf_date(text) <= after
    assert text[16:] == "Z" or text[16] in "+-"


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("D:2026", datetime(2026, 1, 1)),  # noqa: DTZ001 - a naive time has no offset.
        ("D:202602", datetime(2026, 2, 1)),  # noqa: DTZ001 - a naive time has no offset.
        ("2026022813", datetime(2026, 2, 28, 13)),  # noqa: DTZ001 - a naive time has no offset.
        ("D:20260228135959z", datetime(2026, 2, 28, 13, 59, 59, tzinfo=UTC)),
        ("D:20260228135959Z00'00'", datetime(2026, 2, 28, 13, 59, 59, tzinfo=UTC)),
        ("D:20260228135959+09", datetime(2026, 2, 28, 13, 59, 59, tzinfo=timezone(timedelta(hours=9)))),
        ("D:20260228135959+0930", datetime(2026, 2, 28, 13, 59, 59, tzinfo=timezone(timedelta(hours=9, minutes=30)))),
        ("  D:20260228135959-08'00  ", datetime(2026, 2, 28, 13, 59, 59, tzinfo=timezone(timedelta(hours=-8)))),
    ],
)
def test_parse_pdf_date_reads_partial_and_lenient_forms(text: str, value: datetime) -> None:
    assert parse_pdf_date(text) == value


@pytest.mark.parametrize(
    "text",
    ["", "D:", "D:26", "D:20261301", "D:20260230", "D:2026010125", "D:20260101+24'00'", "D:2026x", "yesterday"],
)
def test_parse_pdf_date_refuses_other_strings(text: str) -> None:
    with pytest.raises(ValueError, match="not a PDF date"):
        parse_pdf_date(text)


def test_bad_types_are_refused() -> None:
    with pytest.raises(TypeError):
        pdf_date("2026")  # ty: ignore[invalid-argument-type]
    with pytest.raises(TypeError):
        parse_pdf_date(20260101)  # ty: ignore[invalid-argument-type]


def test_dates_round_trip_through_metadata() -> None:
    doc = pylopdf.Document()
    value = datetime(2026, 10, 24, 9, 30, tzinfo=timezone(timedelta(hours=9)))

    doc.metadata["created"] = pdf_date(value)

    assert parse_pdf_date(pylopdf.open(doc.to_bytes()).metadata["created"]) == value
