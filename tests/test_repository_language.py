from collections.abc import Callable
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import cast

SCRIPT_PATH = Path(__file__).parent.parent / "tools" / "check_repository_language.py"
SPEC = spec_from_file_location("check_repository_language", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    msg = f"could not load {SCRIPT_PATH}"
    raise RuntimeError(msg)
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
_markdown_prose = cast("Callable[[str], list[tuple[int, str]]]", MODULE.__dict__["_markdown_prose"])


def test_markdown_prose_does_not_close_fence_with_other_marker() -> None:
    text = "```\n~~~\n```\n日本語 prose\n"

    assert _markdown_prose(text) == [(4, "日本語 prose")]


def test_markdown_prose_respects_opening_fence_length() -> None:
    text = "````markdown\n```\n日本語 code\n```\n````\n"

    assert _markdown_prose(text) == []
