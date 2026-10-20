"""Check and assemble changelog fragments (roadmap item F2).

A pull request that changes what pylopdf ships records each user-visible
change as one fragment, ``changelog.d/<slug>.<type>.md``. ``<slug>`` is a
lowercase, hyphenated name, and ``<type>`` names the ``CHANGELOG.md``
section the entry belongs to:

- ``added``, ``changed``, ``deprecated``, ``removed``, ``fixed``, and
  ``security``, as in Keep a Changelog;
- ``performance`` and ``documentation``, which earlier releases also use.

The file holds the entry's Markdown text without the leading ``- `` of the
list item; continuation lines need no indentation. One entry per file keeps
concurrent pull requests from touching the same lines of ``CHANGELOG.md``.

Commands:

- ``check`` validates every fragment's name and text. With ``--base REF``,
  it also requires a fragment from a pull request whose changes since
  ``REF`` reach what pylopdf ships (``src/``, ``rust/``, ``fonts/``,
  ``models/``, or ``pyproject.toml``), unless ``--labels`` includes
  ``no-changelog`` or ``dependencies``.
- ``preview`` prints the ``[Unreleased]`` section with the fragments folded
  in, without writing anything.
- ``assemble`` folds the fragments into ``CHANGELOG.md`` and deletes them.
  With ``--version X.Y.Z`` it also turns ``[Unreleased]`` into
  ``[X.Y.Z] - DATE``, where ``--date`` defaults to today in UTC, and opens a
  new empty ``[Unreleased]`` section above it.

Fragments are folded in file-name order and appended after the entries that
the ``[Unreleased]`` section already holds, so the section keeps the order in
which changes were written down by hand before this tool existed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRAGMENT_DIRECTORY = "changelog.d"
CHANGELOG = "CHANGELOG.md"

# Section types in the order a new section is placed in.
SECTIONS = {
    "added": "Added",
    "changed": "Changed",
    "deprecated": "Deprecated",
    "removed": "Removed",
    "fixed": "Fixed",
    "security": "Security",
    "performance": "Performance",
    "documentation": "Documentation",
}
FRAGMENT_NAME = re.compile(r"^(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)\.(?P<type>[a-z]+)\.md$")
# Files in the fragment directory that are not fragments.
NON_FRAGMENTS = frozenset({"README.md", ".gitkeep"})
SHIPPED_PREFIXES = ("src/", "rust/", "fonts/", "models/")
SHIPPED_FILES = frozenset({"pyproject.toml"})
EXEMPT_LABELS = frozenset({"no-changelog", "dependencies"})
UNRELEASED = "## [Unreleased]"
VERSION = re.compile(r"^\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?$")


@dataclass(frozen=True)
class Fragment:
    """One changelog entry read from a fragment file."""

    path: Path
    section: str
    text: str

    def entry(self) -> str:
        """Render the fragment as a Markdown list item."""
        lines = self.text.strip("\n").splitlines()
        rendered = [f"- {lines[0]}"]
        rendered.extend(f"  {line}" if line.strip() else "" for line in lines[1:])
        return "\n".join(rendered)


def fragment_paths(directory: Path) -> list[Path]:
    """Return every file in the fragment directory, sorted by name."""
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if path.name not in NON_FRAGMENTS)


def read_fragment(path: Path) -> tuple[Fragment | None, list[str]]:
    """Parse one fragment, returning it or the problems that prevent it."""
    match = FRAGMENT_NAME.match(path.name)
    if not path.is_file() or match is None:
        return None, [
            f"{path.name}: name a fragment <slug>.<type>.md with a lowercase, hyphenated slug",
        ]
    kind = match.group("type")
    if kind not in SECTIONS:
        return None, [f"{path.name}: unknown type {kind!r}; use one of {', '.join(SECTIONS)}"]
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None, [f"{path.name}: write the fragment as UTF-8 text"]
    errors = []
    stripped = text.strip()
    if not stripped:
        errors.append(f"{path.name}: the fragment is empty")
    elif stripped.startswith(("- ", "* ")):
        errors.append(f"{path.name}: leave out the list marker; the tool adds it")
    elif any(line.startswith("#") for line in stripped.splitlines()):
        errors.append(f"{path.name}: a fragment holds one entry and no headings")
    if errors:
        return None, errors
    return Fragment(path, SECTIONS[kind], stripped), []


def read_fragments(directory: Path) -> tuple[list[Fragment], list[str]]:
    """Parse every fragment, collecting the problems of all of them."""
    fragments: list[Fragment] = []
    errors: list[str] = []
    for path in fragment_paths(directory):
        fragment, problems = read_fragment(path)
        errors.extend(problems)
        if fragment is not None:
            fragments.append(fragment)
    return fragments, errors


def changed_paths(root: Path, base: str) -> list[str]:
    """Return the repository paths that changed on HEAD since it left `base`."""
    result = subprocess.run(  # noqa: S603 - fixed git arguments; `base` is a revision name.
        ["git", "diff", "--name-only", "--no-renames", f"{base}...HEAD"],  # noqa: S607 - git on PATH.
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [line for line in result.stdout.splitlines() if line]


def ships(path: str) -> bool:
    """Return whether a changed path reaches what pylopdf ships."""
    return path in SHIPPED_FILES or path.startswith(SHIPPED_PREFIXES)


def missing_fragment(paths: list[str], labels: set[str]) -> bool:
    """Return whether a pull request with these changes needs a fragment it lacks."""
    if labels & EXEMPT_LABELS:
        return False
    if not any(ships(path) for path in paths):
        return False
    prefix = f"{FRAGMENT_DIRECTORY}/"
    return not any(path.startswith(prefix) and path.removeprefix(prefix) not in NON_FRAGMENTS for path in paths)


def fold(changelog: str, fragments: list[Fragment]) -> str:
    """Append each fragment to its section of the `[Unreleased]` release.

    Existing sections keep their order and text; a section that does not
    exist yet is placed before the first later section in `SECTIONS` order.
    Without fragments the changelog is returned unchanged.
    """
    if not fragments:
        return changelog
    lines = changelog.split("\n")
    start, end = unreleased_bounds(lines)
    preamble: list[str] = []
    names: list[str] = []
    sections: dict[str, list[str]] = {}
    current = preamble
    for line in lines[start + 1 : end]:
        if line.startswith("### "):
            name = line[4:].strip()
            names.append(name)
            current = sections.setdefault(name, [])
        else:
            current.append(line)
    order = list(SECTIONS.values())
    for fragment in fragments:
        if fragment.section not in sections:
            rank = order.index(fragment.section)
            position = next(
                (index for index, name in enumerate(names) if name in order and order.index(name) > rank),
                len(names),
            )
            names.insert(position, fragment.section)
            sections[fragment.section] = []
        entries = sections[fragment.section]
        while entries and not entries[-1].strip():
            entries.pop()
        entries.append(fragment.entry())
    rebuilt = [UNRELEASED, ""]
    text = strip_blank(preamble)
    if text:
        rebuilt.extend([*text, ""])
    for name in names:
        rebuilt.extend([f"### {name}", *strip_blank(sections[name]), ""])
    return "\n".join([*lines[:start], *rebuilt, *lines[end:]])


def strip_blank(lines: list[str]) -> list[str]:
    """Drop blank lines at both ends."""
    first = 0
    last = len(lines)
    while first < last and not lines[first].strip():
        first += 1
    while last > first and not lines[last - 1].strip():
        last -= 1
    return lines[first:last]


def unreleased_bounds(lines: list[str]) -> tuple[int, int]:
    """Return the index of the `[Unreleased]` heading and of the next release."""
    try:
        start = lines.index(UNRELEASED)
    except ValueError:
        msg = f"{CHANGELOG} has no '{UNRELEASED}' heading"
        raise SystemExit(msg) from None
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    return start, end


def release(changelog: str, version: str, date: str) -> str:
    """Turn `[Unreleased]` into `[version] - date` under a new empty `[Unreleased]`."""
    if not VERSION.match(version):
        msg = f"version {version!r} is not X.Y.Z"
        raise SystemExit(msg)
    if f"## [{version}]" in changelog:
        msg = f"{CHANGELOG} already has a [{version}] release"
        raise SystemExit(msg)
    return changelog.replace(UNRELEASED, f"{UNRELEASED}\n\n## [{version}] - {date}", 1)


def unreleased_section(changelog: str) -> str:
    """Return the `[Unreleased]` section of a changelog."""
    lines = changelog.split("\n")
    start, end = unreleased_bounds(lines)
    return "\n".join(lines[start:end]).rstrip() + "\n"


def parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="validate fragments and require one when needed")
    check.add_argument("--base", help="revision the pull request branched from, such as origin/main")
    check.add_argument("--labels", default="", help="comma-separated pull request labels")
    commands.add_parser("preview", help="print the [Unreleased] section with fragments folded in")
    assemble = commands.add_parser("assemble", help="fold fragments into CHANGELOG.md and delete them")
    assemble.add_argument("--version", help="release version that replaces [Unreleased]")
    assemble.add_argument("--date", help="release date, YYYY-MM-DD; today in UTC by default")
    return parser.parse_args(argv)


def check(root: Path, fragments: list[Fragment], base: str | None, labels: str) -> int:
    """Require a fragment when the changes since `base` reach what pylopdf ships."""
    names = {label.strip() for label in labels.split(",") if label.strip()}
    if base and missing_fragment(changed_paths(root, base), names):
        sys.stderr.write(
            f"error: this pull request changes what pylopdf ships but adds no "
            f"{FRAGMENT_DIRECTORY}/<slug>.<type>.md fragment; add one per user-visible "
            "change, or label the pull request no-changelog when nothing changes for users\n",
        )
        return 1
    sys.stdout.write(f"{len(fragments)} changelog fragment(s) valid\n")
    return 0


def assemble(root: Path, fragments: list[Fragment], version: str | None, date: str | None) -> int:
    """Fold the fragments into the changelog, release `version` if given, and delete them."""
    if version is None and not fragments:
        sys.stdout.write("no changelog fragments to fold\n")
        return 0
    date = date or dt.datetime.now(tz=dt.UTC).date().isoformat()
    if version is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        sys.stderr.write(f"error: date {date!r} is not YYYY-MM-DD\n")
        return 1
    path = root / CHANGELOG
    changelog, crlf = read_changelog(path)
    changelog = fold(changelog, fragments)
    if version is not None:
        changelog = release(changelog, version, date)
    if crlf:
        changelog = changelog.replace("\n", "\r\n")
    path.write_bytes(changelog.encode("utf-8"))
    for fragment in fragments:
        fragment.path.unlink()
    sys.stdout.write(f"folded {len(fragments)} fragment(s) into {CHANGELOG}\n")
    return 0


def read_changelog(path: Path) -> tuple[str, bool]:
    """Read the changelog with LF line endings, and whether the file uses CRLF.

    A Windows checkout can have CRLF line endings, which writing keeps.
    """
    raw = path.read_bytes()
    return raw.decode("utf-8").replace("\r\n", "\n"), b"\r\n" in raw


def main(argv: list[str] | None = None) -> int:
    """Run one command and return the process exit status."""
    args = parse_arguments(argv)
    root: Path = args.root
    fragments, errors = read_fragments(root / FRAGMENT_DIRECTORY)
    if errors:
        sys.stderr.writelines(f"error: {FRAGMENT_DIRECTORY}/{error}\n" for error in errors)
        return 1
    if args.command == "check":
        return check(root, fragments, args.base, args.labels)
    if args.command == "preview":
        changelog, _crlf = read_changelog(root / CHANGELOG)
        sys.stdout.write(unreleased_section(fold(changelog, fragments)))
        return 0
    return assemble(root, fragments, args.version, args.date)


if __name__ == "__main__":
    raise SystemExit(main())
