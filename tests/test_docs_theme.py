"""Regression checks for documentation theme color contrast."""

from __future__ import annotations

import re
from pathlib import Path

CSS = Path("docs/overrides/partials/living-document.css").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{(.*?)\}}", CSS, re.DOTALL)
    assert match is not None
    return match.group(1)


def _variables(selector: str) -> dict[str, str]:
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6});", _rule(selector)))


def _relative_luminance(color: str) -> float:
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(left: str, right: str) -> float:
    lighter, darker = sorted((_relative_luminance(left), _relative_luminance(right)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_footer_and_selection_use_explicit_theme_colors() -> None:
    footer = _rule(".md-footer")
    selection = _rule("::selection")

    assert "background: var(--living-ink);" in footer
    assert "color: var(--living-paper);" in footer
    assert "--md-primary-bg-color: var(--living-paper);" in footer
    assert "background: var(--living-selection-bg);" in selection
    assert "color: var(--living-selection-fg);" in selection
    assert ".md-typeset :not(pre) > code" in CSS
    assert ".md-typeset a code" in CSS
    assert "html .md-footer-meta.md-typeset .md-copyright" in CSS
    assert "html .md-footer-meta.md-typeset a.md-social__link" in CSS
    assert ".md-typeset a::selection" in CSS
    assert ".md-footer a::selection" in CSS


def test_footer_and_selection_meet_wcag_aa_in_both_schemes() -> None:
    root = _variables(":root")
    slate = root | _variables('[data-md-color-scheme="slate"]')

    for colors in (root, slate):
        assert _contrast(colors["--living-ink"], colors["--living-paper"]) >= 4.5
        assert _contrast(colors["--living-selection-bg"], colors["--living-selection-fg"]) >= 4.5
        assert _contrast(colors["--living-ink"], colors["--living-footer-link-hover"]) >= 4.5
        assert _contrast(colors["--living-paper-deep"], colors["--living-ink"]) >= 4.5
        assert _contrast(colors["--living-paper-deep"], colors["--living-cobalt-dark"]) >= 4.5
