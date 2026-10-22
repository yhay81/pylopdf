"""Migration tooling: hints, the codemod, and the example corpus."""

from __future__ import annotations

import pickle
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import pylopdf

if TYPE_CHECKING:
    from pylopdf._codemod import Migration

ROOT = Path(__file__).resolve().parent.parent
CORPUS = Path(__file__).parent / "assets" / "migration"
PROGRAMS = sorted(path.name for path in (CORPUS / "pymupdf").glob("*.py"))
#: Corpus programs that keep manual steps and therefore are not run.
NEEDS_REVIEW = {"needs_review.py"}


def _run(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - the active interpreter with fixed arguments.
        [sys.executable, *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_hints_module_matches_the_manifest() -> None:
    result = _run(str(ROOT / "tools" / "generate_migration_hints.py"), "--check")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("owner", "name", "expected"),
    [
        (pylopdf.Document, "get_toc", "is Document.get_outline in pylopdf. pylopdf's get_outline uses zero-based"),
        (pylopdf.Document, "embfile_add", "Use doc.attachments.add(name, data"),
        (lambda: pylopdf.Document().new_page(), "get_textpage", "Page.get_textpage in the Reading milestone"),
        (lambda: pylopdf.Rect(0, 0, 1, 1), "include_rect", "Use r | other"),
        (lambda: pylopdf.IRect(0, 0, 1, 1), "intersect", "Use r & other, which returns a new IRect."),
        (pylopdf.Matrix, "prerotate", "pylopdf.Matrix(deg) * m"),
        (pylopdf.Point, "transform", "Use p * m"),
        (lambda: pylopdf, "TOOLS", "does not provide pymupdf's TOOLS."),
        (lambda: pylopdf, "PDF_REDACT_IMAGE_NONE", "capability of pymupdf's PDF_REDACT_* in the Rewriting milestone"),
    ],
)
def test_missing_pymupdf_names_raise_hinted_attribute_errors(
    owner: Callable[[], object], name: str, expected: str
) -> None:
    with pytest.raises(AttributeError) as caught:
        getattr(owner(), name)
    assert expected in str(caught.value)


def test_hinted_errors_keep_python_semantics() -> None:
    doc = pylopdf.Document()
    with pytest.raises(AttributeError) as caught:
        _ = doc.get_toc  # ty: ignore[unresolved-attribute]
    assert (caught.value.name, caught.value.obj) == ("get_toc", doc)
    assert str(caught.value).startswith("'Document' object has no attribute 'get_toc'. ")
    with pytest.raises(AttributeError, match=r"^'Document' object has no attribute 'nothing_like_this'$"):
        _ = doc.nothing_like_this  # ty: ignore[unresolved-attribute]
    with pytest.raises(AttributeError, match=r"^module 'pylopdf' has no attribute '__nothing__'$"):
        _ = pylopdf.__nothing__  # ty: ignore[unresolved-attribute]
    assert not hasattr(doc, "get_toc")
    assert getattr(doc, "set_toc", None) is None
    rect = pylopdf.Rect(1, 2, 3, 4)
    assert pickle.loads(pickle.dumps(rect)) == rect  # noqa: S301 - a value pylopdf just pickled.
    with pytest.raises(ImportError):
        from pylopdf import get_toc  # noqa: F401, PLC0415  # ty: ignore[unresolved-import]


def test_successful_lookups_never_reach_the_hints() -> None:
    for cls in (pylopdf.Document, pylopdf.Page, pylopdf.Rect, pylopdf.Matrix, pylopdf.Shape):
        assert "__getattr__" in vars(cls) or any("__getattr__" in vars(base) for base in cls.__mro__[1:])
    assert pylopdf.Document().page_count == 0


Migrate = Callable[[str], "Migration"]


@pytest.fixture(scope="module")
def migrate_source() -> Migrate:
    pytest.importorskip("libcst")
    from pylopdf._codemod import migrate_source  # noqa: PLC0415 - needs the optional LibCST.

    return migrate_source


@pytest.mark.parametrize("name", PROGRAMS)
def test_codemod_migrates_the_corpus(migrate_source: Migrate, name: str) -> None:
    source = (CORPUS / "pymupdf" / name).read_text(encoding="utf-8")
    expected = (CORPUS / "pylopdf" / name).read_text(encoding="utf-8")

    result = migrate_source(source)

    assert result.code == expected
    assert result.rewrites > 0
    assert bool(result.todos) == (name in NEEDS_REVIEW)


@pytest.mark.parametrize("name", sorted(set(PROGRAMS) - NEEDS_REVIEW))
def test_migrated_corpus_programs_pass(name: str, tmp_path: Path) -> None:
    result = _run(str(CORPUS / "pylopdf" / name), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"{name.removesuffix('.py')} ok"


def test_codemod_lists_the_manual_steps(migrate_source: Migrate) -> None:
    source = (CORPUS / "pymupdf" / "needs_review.py").read_text(encoding="utf-8")

    todos = migrate_source(source).todos

    assert [line for line, _ in todos] == [5, 9, 11, 14]
    assert "TOOLS" in todos[0][1]
    assert "Page.get_textpage" in todos[1][1]
    assert "TextPage.extractBLOCKS" in todos[2][1]
    assert "rotation_matrix" in todos[3][1]


def test_codemod_leaves_other_modules_alone(migrate_source: Migrate) -> None:
    source = "import json\n\ndoc.get_toc()\nrect.include_rect(other)\n"

    result = migrate_source(source)

    assert (result.code, result.rewrites, result.todos) == (source, 0, ())


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import fitz as F\nF.open()\n", "import pylopdf as F\nF.open()\n"),
        ("import pymupdf\npymupdf.Rect()\n", "import pylopdf\npylopdf.Rect()\n"),
        ("from fitz import Rect, Matrix\nm = Matrix()\nm.prescale(2, 3)\n", "Matrix(2, 3) * m"),
        ("import fitz\ndoc.copy_page(0, to=2)\n", "doc.fullcopy_page(0, to=2)"),
        ("import fitz\nnames = doc.embfile_names()\n", "names = list(doc.attachments)"),
        (
            "import fitz\ndoc.embfile_add('a', data, ufilename='Ä.txt')\n",
            "doc.attachments.add('a', data, filename='Ä.txt')",
        ),
        ("import fitz\nm.concat(a, b)\n", "m = a * b"),
        ("import fitz\nm.invert(src)\n", "m = ~src"),
        ("import fitz\nm.preshear(1, 2)\n", "m = pylopdf.Matrix(1, 2, 1) * m"),
        ("import fitz\ntry:\n    pass\nexcept fitz.FileNotFoundError:\n    pass\n", "except FileNotFoundError:"),
    ],
)
def test_codemod_rules(migrate_source: Migrate, source: str, expected: str) -> None:
    result = migrate_source(source)
    assert expected in result.code
    assert result.todos == ()


@pytest.mark.parametrize(
    ("source", "hint"),
    [
        ("import fitz\ntoc = doc.get_toc(False)\n", "get_outline uses zero-based pages"),
        ("import fitz\ndata = doc.embfile_get(0)\n", "doc.attachments[name].read()"),
        ("import fitz\ninfo = doc.embfile_info('a')\n", "Read name, filename, description"),
        ("import fitz\nresult = rect.include_rect(other)\n", "Use r | other"),
        ("import fitz\ndoc.set_toc(toc, collapse=0)\n", "set_outline uses zero-based pages"),
        ("import fitz\nfrom fitz import Story\n", "Use typst for HTML and CSS pagination"),
        ("import fitz.utils\n", "pylopdf has no fitz.utils module"),
        ("from fitz import Rect\nm.prerotate(90)\n", "pylopdf.Matrix(deg) * m"),
    ],
)
def test_codemod_marks_what_it_cannot_rewrite(migrate_source: Migrate, source: str, hint: str) -> None:
    result = migrate_source(source)
    assert any(hint in message for _, message in result.todos), result.code


def test_codemod_places_hints_inside_compound_statements(migrate_source: Migrate) -> None:
    source = "import fitz\n\ndef run(page):\n    if page.rotation_matrix:\n        page.get_textpage()\n"

    code = migrate_source(source).code

    assert code.splitlines()[3].lstrip().startswith("# TODO(pylopdf): pylopdf extracts and inserts")
    planned = code.splitlines()[5].lstrip()
    assert planned.startswith("# TODO(pylopdf): pylopdf plans the capability of pymupdf's Page.get_textpage")


def test_migrate_command_writes_checks_and_diffs(tmp_path: Path) -> None:
    pytest.importorskip("libcst")
    program = tmp_path / "program.py"
    program.write_text("import fitz\nprint(fitz.Document().get_toc())\n", encoding="utf-8")

    check = _run("-m", "pylopdf.migrate", "--check", str(tmp_path))
    assert check.returncode == 1
    assert "1 file(s) would change; 0 manual step(s) remain" in check.stderr
    diff = _run("-m", "pylopdf.migrate", "--diff", str(program))
    assert diff.returncode == 0
    assert "+import pylopdf" in diff.stdout
    assert program.read_text(encoding="utf-8").startswith("import fitz")

    written = _run("-m", "pylopdf.migrate", str(program))

    assert written.returncode == 0, written.stderr
    assert program.read_text(encoding="utf-8").startswith("import pylopdf\n")
    assert _run(str(program)).stdout == "[]\n"
    assert _run("-m", "pylopdf.migrate", "--check", str(program)).returncode == 0
