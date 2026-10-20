"""Changelog fragments: validation, the pull request rule, and assembly (F2)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "changelog.py"

CHANGELOG = """# Changelog

Intro text.

## [Unreleased]

### Added
- Existing entry
  that continues.

### Fixed
- Existing fix.

## [0.1.0] - 2026-01-01

### Added
- First release.
"""


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed repository script under the active interpreter.
        [sys.executable, str(TOOL), "--root", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "changelog.d").mkdir(parents=True)
    (root / "changelog.d" / "README.md").write_text("# Changelog fragments\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")
    return root


def _fragment(root: Path, name: str, text: str) -> Path:
    path = root / "changelog.d" / name
    path.write_text(text, encoding="utf-8")
    return path


def test_the_repository_fragments_are_valid() -> None:
    result = _run(ROOT, "check")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("name", "text", "message"),
    [
        ("Bad_Name.added.md", "Entry.", "lowercase, hyphenated slug"),
        ("entry.md", "Entry.", "lowercase, hyphenated slug"),
        ("entry.improved.md", "Entry.", "unknown type 'improved'"),
        ("entry.added.md", "  \n", "the fragment is empty"),
        ("entry.added.md", "- Entry.", "leave out the list marker"),
        ("entry.added.md", "Entry.\n\n## Heading", "no headings"),
    ],
)
def test_invalid_fragments_are_refused(tmp_path: Path, name: str, text: str, message: str) -> None:
    root = _repository(tmp_path)
    _fragment(root, name, text)

    for command in ("check", "preview", "assemble"):
        result = _run(root, command)
        assert result.returncode == 1
        assert message in result.stderr
    assert (root / "CHANGELOG.md").read_text(encoding="utf-8") == CHANGELOG


def test_preview_folds_fragments_without_writing(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    _fragment(root, "b-second.added.md", "Second entry\nwrapped onto two lines.\n")
    _fragment(root, "a-first.added.md", "First entry.\n")
    _fragment(root, "speed.performance.md", "Faster.\n")
    _fragment(root, "old-api.deprecated.md", "Deprecated API.\n")

    result = _run(root, "preview")

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "## [Unreleased]\n"
        "\n"
        "### Added\n"
        "- Existing entry\n"
        "  that continues.\n"
        "- First entry.\n"
        "- Second entry\n"
        "  wrapped onto two lines.\n"
        "\n"
        "### Deprecated\n"
        "- Deprecated API.\n"
        "\n"
        "### Fixed\n"
        "- Existing fix.\n"
        "\n"
        "### Performance\n"
        "- Faster.\n"
    )
    assert (root / "CHANGELOG.md").read_text(encoding="utf-8") == CHANGELOG
    assert len(list((root / "changelog.d").glob("*.md"))) == 5


def test_assemble_without_fragments_keeps_the_changelog_byte_for_byte(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    shutil.copy(ROOT / "CHANGELOG.md", root / "CHANGELOG.md")

    result = _run(root, "assemble")

    assert result.returncode == 0, result.stderr
    assert (root / "CHANGELOG.md").read_bytes() == (ROOT / "CHANGELOG.md").read_bytes()


def test_assemble_keeps_crlf_line_endings(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "CHANGELOG.md").write_bytes(CHANGELOG.replace("\n", "\r\n").encode())
    _fragment(root, "entry.fixed.md", "A fix\nover two lines.\n")

    result = _run(root, "assemble")

    assert result.returncode == 0, result.stderr
    data = (root / "CHANGELOG.md").read_bytes()
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert b"- Existing fix.\r\n- A fix\r\n  over two lines.\r\n\r\n## [0.1.0]" in data


def test_assemble_releases_a_version_and_deletes_fragments(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    fragment = _fragment(root, "garbage-levels.added.md", "Garbage levels.\n")

    result = _run(root, "assemble", "--version", "0.2.0", "--date", "2026-10-21")

    assert result.returncode == 0, result.stderr
    assert not fragment.exists()
    assert (root / "changelog.d" / "README.md").exists()
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.startswith(
        "# Changelog\n\nIntro text.\n\n## [Unreleased]\n\n## [0.2.0] - 2026-10-21\n\n### Added\n"
    )
    assert "- Existing entry\n  that continues.\n- Garbage levels.\n\n### Fixed\n" in changelog
    assert changelog.endswith("## [0.1.0] - 2026-01-01\n\n### Added\n- First release.\n")
    # A second release of the same version is refused.
    again = _run(root, "assemble", "--version", "0.2.0", "--date", "2026-10-21")
    assert again.returncode == 1
    assert "already has a [0.2.0] release" in again.stderr


@pytest.mark.parametrize("version", ["1.0", "v1.0.0", "1.0.0 beta"])
def test_assemble_refuses_malformed_versions_and_dates(tmp_path: Path, version: str) -> None:
    root = _repository(tmp_path)
    _fragment(root, "entry.added.md", "Entry.\n")

    result = _run(root, "assemble", "--version", version, "--date", "2026-10-21")
    assert result.returncode == 1
    bad_date = _run(root, "assemble", "--version", "1.0.0", "--date", "21.10.2026")
    assert bad_date.returncode == 1
    assert (root / "CHANGELOG.md").read_text(encoding="utf-8") == CHANGELOG
    assert (root / "changelog.d" / "entry.added.md").exists()


def _git(root: Path, *args: str) -> None:
    # Ignore the developer's global configuration, such as commit signing.
    empty = root.parent / "empty.gitconfig"
    empty.touch()
    environment = {**os.environ, "GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(  # noqa: S603 - fixed git arguments in a temporary repository.
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args],  # noqa: S607
        cwd=root,
        env=environment,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def branched(tmp_path: Path) -> Path:
    """Return a repository whose HEAD branched from the branch `base`."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    root = _repository(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "guide.md").write_text("Guide.\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "branch", "base")
    return root


def _commit(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "change")


def test_a_shipped_change_requires_a_fragment(branched: Path) -> None:
    (branched / "src" / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    _commit(branched)

    missing = _run(branched, "check", "--base", "base")
    assert missing.returncode == 1
    assert "adds no changelog.d/<slug>.<type>.md fragment" in missing.stderr

    for labels in ("no-changelog", "bug,no-changelog", "dependencies"):
        assert _run(branched, "check", "--base", "base", "--labels", labels).returncode == 0

    _fragment(branched, "value.changed.md", "VALUE is 2.\n")
    _commit(branched)
    assert _run(branched, "check", "--base", "base").returncode == 0


def test_changes_outside_what_ships_need_no_fragment(branched: Path) -> None:
    (branched / "docs" / "guide.md").write_text("Better guide.\n", encoding="utf-8")
    (branched / "changelog.d" / "README.md").write_text("# Fragments\n", encoding="utf-8")
    _commit(branched)

    result = _run(branched, "check", "--base", "base")

    assert result.returncode == 0, result.stderr


def test_editing_only_the_fragment_readme_does_not_count_as_a_fragment(branched: Path) -> None:
    (branched / "src" / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
    (branched / "changelog.d" / "README.md").write_text("# Fragments\n", encoding="utf-8")
    _commit(branched)

    assert _run(branched, "check", "--base", "base").returncode == 1
