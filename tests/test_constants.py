"""pymupdf's constant groups as IntEnum and IntFlag members."""

from __future__ import annotations

import enum

import pytest

import pylopdf


@pytest.mark.parametrize(
    ("group", "prefix", "values"),
    [
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


def test_link_kinds_are_target_types() -> None:
    for name in ("LinkKind", "LINK_NONE", "LINK_GOTO", "LINK_URI", "LINK_LAUNCH", "LINK_NAMED", "LINK_GOTOR"):
        assert not hasattr(pylopdf, name)
        assert name not in pylopdf.__all__


def test_text_align_members_and_plain_integers_align_alike() -> None:
    def spare(align: int) -> list[tuple[float, ...]]:
        page = pylopdf.Document().pages.new()
        page.insert_textbox((20, 20, 300, 100), "centered", align=align)
        return [tuple(round(value, 3) for value in word.bbox) for word in page.words()]

    assert spare(pylopdf.TEXT_ALIGN_CENTER) == spare(1)
    assert spare(pylopdf.TEXT_ALIGN_CENTER) != spare(pylopdf.TEXT_ALIGN_LEFT)
