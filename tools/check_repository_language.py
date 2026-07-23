"""Enforce English as the canonical language for repository-facing prose."""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NON_ENGLISH_CJK = re.compile(r"[\u3041-\u3096\u30a1-\u30fa\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7a3]")
INLINE_CODE = re.compile(r"`[^`]*`")
FENCED_CODE_BLOCK = re.compile(r" {0,3}(?P<marker>`{3,}|~{3,})(?P<rest>.*)")
TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".rs",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
LOCALIZED_FILES = {
    Path("README.ja.md"),
    Path("mkdocs.ja.yml"),
    Path("mkdocs.ko.yml"),
    Path("mkdocs.zh-cn.yml"),
}
LOCALIZED_DIRECTORIES = {
    Path("docs/ja"),
    Path("docs/ko"),
    Path("docs/zh-cn"),
}
LANGUAGE_SELECTOR_NAMES = {
    "- name: \u65e5\u672c\u8a9e",
    "- name: \u7b80\u4f53\u4e2d\u6587",
    "- name: \ud55c\uad6d\uc5b4",
}


def _repository_files() -> list[Path]:
    git = shutil.which("git")
    if git is None:
        msg = "git is required for the repository language check"
        raise RuntimeError(msg)
    result = subprocess.run(  # noqa: S603 - resolved Git executable with fixed arguments
        [git, "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [Path(line) for line in result.stdout.splitlines() if line]


def _is_localized(path: Path) -> bool:
    return path in LOCALIZED_FILES or any(path.is_relative_to(directory) for directory in LOCALIZED_DIRECTORIES)


def _matching_lines(text: str) -> list[tuple[int, str]]:
    return [
        (number, line.strip()) for number, line in enumerate(text.splitlines(), start=1) if NON_ENGLISH_CJK.search(line)
    ]


def _markdown_prose(text: str) -> list[tuple[int, str]]:
    """Return CJK prose outside fenced and inline code."""
    matches: list[tuple[int, str]] = []
    fence: tuple[str, int] | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        fence_match = FENCED_CODE_BLOCK.fullmatch(line)
        if fence is not None:
            if fence_match is not None:
                marker = fence_match.group("marker")
                rest = fence_match.group("rest")
                if marker[0] == fence[0] and len(marker) >= fence[1] and not rest.strip():
                    fence = None
            continue
        if fence_match is not None:
            marker = fence_match.group("marker")
            rest = fence_match.group("rest")
            if marker[0] == "~" or "`" not in rest:
                fence = (marker[0], len(marker))
                continue
        prose = INLINE_CODE.sub("", line)
        if NON_ENGLISH_CJK.search(prose):
            matches.append((number, line.strip()))
    return matches


def _python_prose(path: Path, text: str) -> list[tuple[int, str]]:
    """Return CJK comments and docstrings while allowing fixture strings."""
    tokens = tokenize.generate_tokens(iter(text.splitlines(keepends=True)).__next__)
    matches = [
        (token.start[0], token.string.strip())
        for token in tokens
        if token.type == tokenize.COMMENT and NON_ENGLISH_CJK.search(token.string)
    ]

    tree = ast.parse(text, filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Constant):
            continue
        if isinstance(node.value.value, str) and NON_ENGLISH_CJK.search(node.value.value):
            matches.append((node.lineno, node.value.value.splitlines()[0].strip()))
    return matches


def main() -> int:
    """Report CJK prose outside localized documentation and fixture data."""
    violations: list[tuple[Path, int, str]] = []
    for relative_path in _repository_files():
        if _is_localized(relative_path) or relative_path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        absolute_path = ROOT / relative_path
        try:
            text = absolute_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        if relative_path.suffix == ".py" and relative_path.parts[0] == "tests":
            matches = _python_prose(relative_path, text)
        elif relative_path.suffix == ".md":
            matches = _markdown_prose(text)
        else:
            matches = _matching_lines(text)
        if relative_path == Path("mkdocs.yml"):
            matches = [(line, excerpt) for line, excerpt in matches if excerpt not in LANGUAGE_SELECTOR_NAMES]
        violations.extend((relative_path, line, excerpt) for line, excerpt in matches)

    if not violations:
        sys.stdout.write("Repository language check passed.\n")
        return 0

    details = "\n".join(f"{path}:{line}: {excerpt}" for path, line, excerpt in violations)
    sys.stderr.write(f"CJK prose found outside localized documentation or test fixture strings:\n{details}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
