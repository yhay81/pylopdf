"""pymupdf's constant groups as IntEnum and IntFlag members."""

from __future__ import annotations

import enum

import pytest

import pylopdf


@pytest.mark.parametrize(
    ("group", "prefix", "values"),
    [
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


def test_text_alignment_is_an_option_of_the_canvas_textbox() -> None:
    for name in ("TextAlign", "TEXT_ALIGN_LEFT", "TEXT_ALIGN_CENTER", "TEXT_ALIGN_RIGHT", "TEXT_ALIGN_JUSTIFY"):
        assert not hasattr(pylopdf, name)
        assert name not in pylopdf.__all__
    with pytest.raises(AttributeError, match='align="center"'):
        _ = pylopdf.TEXT_ALIGN_CENTER  # ty: ignore[unresolved-attribute]
