"""Rewrite pymupdf programs for pylopdf: ``python -m pylopdf.migrate PATH...``.

The command renames the ``fitz`` and ``pymupdf`` imports to ``pylopdf``,
rewrites the calls whose replacement the capability map records as
mechanical, and adds a ``# TODO(pylopdf): ...`` comment with the migration
hint above every statement that still needs a person. It rewrites each
Python file in place (directories are searched recursively), then lists the
TODO comments as ``path:line: message``. ``--diff`` prints a unified diff
instead of writing, and ``--check`` writes nothing and exits with status 1
when a file would change. It needs LibCST: ``pip install "pylopdf[migrate]"``.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence


def _python_files(paths: Sequence[str]) -> Iterator[Path]:
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            yield from sorted(path.rglob("*.py"))
        else:
            yield path


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command; return 0, 1 when ``--check`` finds a change, or 2 on a usage error."""
    parser = argparse.ArgumentParser(
        prog="python -m pylopdf.migrate",
        description="Rewrite pymupdf programs for pylopdf and list the steps that remain.",
    )
    parser.add_argument("paths", nargs="+", help="Python files or directories to migrate")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--diff", action="store_true", help="print a unified diff instead of writing")
    mode.add_argument("--check", action="store_true", help="write nothing; exit 1 when a file would change")
    args = parser.parse_args(argv)
    try:
        from pylopdf._codemod import migrate_source  # noqa: PLC0415 - LibCST is an optional extra.
    except ModuleNotFoundError as error:
        if error.name != "libcst":
            raise
        sys.stderr.write('pylopdf.migrate needs LibCST: pip install "pylopdf[migrate]"\n')
        return 2

    changed = 0
    todos = 0
    for path in _python_files(args.paths):
        source = path.read_text(encoding="utf-8")
        result = migrate_source(source)
        if result.code != source:
            changed += 1
            if args.diff:
                sys.stdout.writelines(
                    difflib.unified_diff(
                        source.splitlines(keepends=True),
                        result.code.splitlines(keepends=True),
                        fromfile=str(path),
                        tofile=str(path),
                    )
                )
            elif not args.check:
                path.write_text(result.code, encoding="utf-8")
        for line, message in result.todos:
            todos += 1
            sys.stderr.write(f"{path}:{line}: {message}\n")
    verb = "would change" if args.check or args.diff else "changed"
    sys.stderr.write(f"{changed} file(s) {verb}; {todos} manual step(s) remain\n")
    return 1 if args.check and changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
