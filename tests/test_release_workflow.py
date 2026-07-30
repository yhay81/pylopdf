from __future__ import annotations

import re
from pathlib import Path

import pylopdf

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW = _ROOT / ".github" / "workflows" / "release.yml"
_FONTS_WORKFLOW = _ROOT / ".github" / "workflows" / "release-fonts.yml"
_DOC_CONFIGS = (
    _ROOT / "mkdocs.yml",
    _ROOT / "mkdocs.ja.yml",
    _ROOT / "mkdocs.zh-cn.yml",
    _ROOT / "mkdocs.ko.yml",
)
_NATIVE_PLATFORMS = [
    ("ubuntu-latest", "x86_64-unknown-linux-gnu"),
    ("ubuntu-24.04-arm", "aarch64-unknown-linux-gnu"),
    ("macos-latest", "aarch64-apple-darwin"),
    ("macos-15-intel", "x86_64-apple-darwin"),
    ("windows-latest", "x86_64-pc-windows-msvc"),
]
_MATRIX_ENTRY = re.compile(r"^\s+- \{ runner: ([^,]+), target: ([^ }]+) \}$", re.MULTILINE)


def _job(workflow: str, name: str) -> str:
    start = workflow.index(f"  {name}:")
    match = re.search(r"^  [a-z][a-z0-9-]*:$", workflow[start + 1 :], re.MULTILINE)
    if match is None:
        return workflow[start:]
    return workflow[start : start + 1 + match.start()]


def test_every_native_release_wheel_runs_on_its_own_architecture() -> None:
    workflow = _WORKFLOW.read_text(encoding="utf-8")

    for job_name in ("build-wheels", "build-free-threaded-wheels"):
        job = _job(workflow, job_name)
        assert _MATRIX_ENTRY.findall(job) == _NATIVE_PLATFORMS
        assert "matrix.platform.smoke" not in job
        assert "python tools/smoke_artifact.py dist" in job


def test_documentation_announcement_tracks_package_version() -> None:
    version = pylopdf.__version__
    expected_prefix = f'announcement: "pylopdf {version} ·'
    expected_url = f'announcement_url: "https://github.com/yhay81/pylopdf/releases/tag/v{version}"'

    for config_path in _DOC_CONFIGS:
        config = config_path.read_text(encoding="utf-8")
        assert expected_prefix in config, config_path.name
        assert expected_url in config, config_path.name


def test_font_release_attests_a_content_complete_reproducible_sbom() -> None:
    workflow = _FONTS_WORKFLOW.read_text(encoding="utf-8")

    assert "python tools/generate_font_sbom.py" in workflow
    assert '--source-commit "$GITHUB_SHA"' in workflow
    assert '--source-date-epoch "$(git show -s --format=%ct "$GITHUB_SHA")"' in workflow
    assert "sbom-path: release/sbom.spdx.json" in workflow
    assert "anchore/sbom-action" not in workflow
