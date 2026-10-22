"""pymupdf's constant groups as IntEnum and IntFlag members."""

from __future__ import annotations

import enum

import pytest
from conftest import build_pdf

import pylopdf


@pytest.mark.parametrize(
    ("group", "prefix", "values"),
    [
        (pylopdf.LinkKind, "LINK_", {"NONE": 0, "GOTO": 1, "URI": 2, "LAUNCH": 3, "NAMED": 4, "GOTOR": 5}),
        (pylopdf.TextAlign, "TEXT_ALIGN_", {"LEFT": 0, "CENTER": 1, "RIGHT": 2, "JUSTIFY": 3}),
        (
            pylopdf.Permissions,
            "PDF_PERM_",
            {
                "PRINT": 4,
                "MODIFY": 8,
                "COPY": 16,
                "ANNOTATE": 32,
                "FORM": 256,
                "ACCESSIBILITY": 512,
                "ASSEMBLE": 1024,
                "PRINT_HQ": 2048,
            },
        ),
    ],
)
def test_module_constants_are_members_with_pymupdf_values(
    group: type[enum.IntEnum | enum.IntFlag],
    prefix: str,
    values: dict[str, int],
) -> None:
    assert group.__module__ == "pylopdf"
    for name, value in values.items():
        member = getattr(pylopdf, prefix + name)
        assert member is group[name]
        assert member == value
        assert isinstance(member, int)
        assert prefix + name in pylopdf.__all__


def test_permissions_drop_the_old_member_names() -> None:
    assert sum(member.value for member in pylopdf.Permissions if member.name != "ALL") == pylopdf.Permissions.ALL
    for old in ("FILL_FORMS", "COPY_FOR_ACCESSIBILITY", "PRINT_HIGH_QUALITY"):
        assert not hasattr(pylopdf.Permissions, old)


def test_get_links_reports_link_kind_members() -> None:
    page = pylopdf.open(build_pdf(["Link"])).pages[0]
    page.insert_link({"kind": pylopdf.LINK_URI, "from": (10, 10, 100, 30), "uri": "https://example.com/"})

    (link,) = page.get_links()

    assert link["kind"] is pylopdf.LINK_URI
    assert repr(link["kind"]) == "<LinkKind.URI: 2>"


def test_text_align_members_and_plain_integers_align_alike() -> None:
    def spare(align: int) -> list[tuple[float, ...]]:
        page = pylopdf.Document().pages.new()
        page.insert_textbox((20, 20, 300, 100), "centered", align=align)
        return [tuple(round(value, 3) for value in word[:4]) for word in page.get_text("words")]

    assert spare(pylopdf.TEXT_ALIGN_CENTER) == spare(1)
    assert spare(pylopdf.TEXT_ALIGN_CENTER) != spare(pylopdf.TEXT_ALIGN_LEFT)
