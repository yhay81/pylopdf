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
        (pylopdf.Document, "get_toc", "is Document.outline in pylopdf. Use doc.outline.entries()"),
        (pylopdf.Document, "embfile_add", "Use doc.attachments.add(name, data"),
        (pylopdf.Document().pages.new, "write_text", "Page.write_text in the Writing milestone"),
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
    for cls in (pylopdf.Document, pylopdf.Page, pylopdf.Rect, pylopdf.Matrix, pylopdf.Canvas):
        assert "__getattr__" in vars(cls) or any("__getattr__" in vars(base) for base in cls.__mro__[1:])
    assert len(pylopdf.Document().pages) == 0


def test_pylopdf_015_names_raise_hinted_attribute_errors() -> None:
    with pytest.raises(AttributeError, match=r"peek_metadata is probe in pylopdf"):
        _ = pylopdf.peek_metadata  # ty: ignore[unresolved-attribute]
    with pytest.raises(AttributeError, match=r"Page\.render is Page\.pixmap in pylopdf"):
        _ = pylopdf.Document().pages.new().render  # ty: ignore[unresolved-attribute]
    with pytest.raises(AttributeError, match=r"pylopdf 0\.15's TextPage is Layout"):
        _ = pylopdf.TextPage  # ty: ignore[unresolved-attribute]


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
        ("import fitz as F\nF.open()\n", "import pylopdf as F\nF.Document()\n"),
        ("import pymupdf\npymupdf.Rect()\n", "import pylopdf\npylopdf.Rect()\n"),
        ("from fitz import Rect, Matrix\nm = Matrix()\nm.prescale(2, 3)\n", "Matrix(2, 3) * m"),
        ("import fitz\ndoc.copy_page(0, to=2)\n", "doc.pages.insert(2, doc.pages[0])"),
        ("import fitz\ndoc = fitz.open('a.pdf')\n", "doc = pylopdf.open('a.pdf')"),
        ("import fitz\ndoc = fitz.open(stream=data, filetype='pdf')\n", "doc = pylopdf.open(data)"),
        ("import fitz\ndoc = fitz.Document('pdf', data)\n", "doc = pylopdf.open(data)"),
        ("from fitz import open\ndoc = open(stream=data)\n", "doc = open(data)"),
        ("import fitz\ndoc = fitz.open()\npage = doc[0]\n", "page = doc.pages[0]"),
        ("import fitz\ndoc = fitz.open()\nn = len(doc)\n", "n = len(doc.pages)"),
        (
            "import fitz\ndoc = fitz.open()\nfor page in doc:\n    print(page.number)\n",
            "for page in doc.pages:\n    print(page.index)",
        ),
        (
            "import fitz\ndoc = fitz.open()\npages = [p.parent for p in doc]\n",
            "pages = [p.document for p in doc.pages]",
        ),
        (
            "import fitz\nwith fitz.open() as doc:\n    print(list(enumerate(doc)))\n",
            "print(list(enumerate(doc.pages)))",
        ),
        ("import fitz\nn = doc.page_count\n", "n = len(doc.pages)"),
        ("import fitz\nkind = fitz.PDF_ANNOT_3D\n", "kind = pylopdf.AnnotationType.THREE_D"),
        (
            "import fitz\nr = fitz.EMPTY_RECT() | fitz.INFINITE_IRECT()\nm = fitz.IdentityMatrix() * fitz.Identity\n",
            "r = pylopdf.Rect.EMPTY | pylopdf.IRect.INFINITE\nm = pylopdf.Matrix.IDENTITY * pylopdf.Matrix.IDENTITY",
        ),
        (
            "import fitz\nm = fitz.planish_line(a, b)\nlow = fitz.FZ_MIN_INF_RECT\n",
            "m = pylopdf.Matrix.planish(a, b)\nlow = pylopdf.Rect.INFINITE.x0",
        ),
        (
            (
                "from fitz import EMPTY_QUAD, Identity, planish_line\n"
                "q = EMPTY_QUAD()\nm = planish_line(p, r) @ Identity\n"
            ),
            "from pylopdf import Quad, Matrix\nq = Quad.EMPTY\nm = Matrix.planish(p, r) @ Matrix.IDENTITY",
        ),
        (
            "import fitz\nflags = fitz.PDF_ANNOT_IS_HIDDEN | fitz.PDF_ANNOT_IS_PRINT\n",
            "flags = pylopdf.AnnotationFlags.HIDDEN | pylopdf.AnnotationFlags.PRINT",
        ),
        (
            "from fitz import PDF_ANNOT_TEXT, PDF_ANNOT_INK, Rect\nkinds = (PDF_ANNOT_TEXT, PDF_ANNOT_INK)\n",
            "from pylopdf import AnnotationType, Rect\nkinds = (AnnotationType.TEXT, AnnotationType.INK)",
        ),
        (
            "from fitz import PDF_PERM_PRINT\nfrom fitz import PDF_PERM_COPY as C\nallowed = PDF_PERM_PRINT | C\n",
            "from pylopdf import Permissions\nallowed = Permissions.PRINT | Permissions.COPY",
        ),
        (
            "from fitz import PDF_PERM_PRINT\nfrom fitz import Permissions\nallowed = PDF_PERM_PRINT\n",
            "from pylopdf import Permissions\nallowed = Permissions.PRINT",
        ),
        ("import fitz\nlocked = doc.needs_pass\n", "locked = doc.needs_password"),
        ("import fitz\npage = doc.load_page(3)\n", "page = doc.pages[3]"),
        ("import fitz\npage = doc.new_page(0, width=200, height=300)\n", "page = doc.pages.new((200, 300), index=0)"),
        (
            "import fitz\ndoc.insert_pdf(src, from_page=2, to_page=4, start_at=0)\n",
            "doc.pages.insert(0, src.pages[2:5])",
        ),
        ("import fitz\ndoc.insert_pdf(src, from_page=4, to_page=2)\n", "doc.pages.extend(src.pages[2:5][::-1])"),
        ("import fitz\ndoc.insert_pdf(fitz.open(path))\n", "doc.pages.extend(pylopdf.open(path).pages)"),
        ("import fitz\ndoc.move_page(1, 4)\n", "doc.pages.move(1, 3)"),
        ("import fitz\ndoc.delete_page()\n", "del doc.pages[-1]"),
        ("import fitz\ndoc.delete_pages(1, 3)\n", "del doc.pages[1:4]"),
        ("import fitz\ndoc.delete_pages(from_page=2)\n", "del doc.pages[2:]"),
        (
            "import fitz\ndoc.delete_pages([0, 2])\n",
            "doc.pages.select(index for index in range(len(doc.pages)) if index not in {0, 2})",
        ),
        ("import fitz\ndoc.select([2, 0])\n", "doc.pages.select([2, 0])"),
        ("import fitz\nfor page in doc.pages(1, 5):\n    pass\n", "for page in doc.pages[1:5]:"),
        ("import fitz\nlabels = doc.get_page_numbers('iv', only_one=True)\n", "labels = doc.pages.labeled('iv')[:1]"),
        ("import fitz\nlayout = doc.get_page_text(2, 'dict')\n", "layout = doc.pages[2].layout().to_dict()"),
        ("import fitz\nxref = doc.page_xref(0)\n", "xref = doc.pages[0].xref"),
        ("import fitz\ntry:\n    pass\nexcept fitz.FileDataError:\n    pass\n", "except pylopdf.MalformedPdfError:"),
        ("import fitz\nnames = doc.embfile_names()\n", "names = list(doc.attachments)"),
        (
            "import fitz\ndoc.embfile_add('a', data, ufilename='Ä.txt')\n",
            "doc.attachments.add('a', data, filename='Ä.txt')",
        ),
        ("import fitz\nm.concat(a, b)\n", "m = a * b"),
        ("import fitz\nm.invert(src)\n", "m = ~src"),
        ("import fitz\nm.preshear(1, 2)\n", "m = pylopdf.Matrix(1, 2, 1) * m"),
        ("import fitz\ntry:\n    pass\nexcept fitz.FileNotFoundError:\n    pass\n", "except FileNotFoundError:"),
        (
            "import fitz\ndoc.save('out.pdf', garbage=4, deflate=True)\n",
            "doc.save('out.pdf', dedupe=True, renumber=True)",
        ),
        (
            "import fitz\ndoc.save('out.pdf', garbage=2, deflate=False)\n",
            "doc.save('out.pdf', renumber=True, compress=False)",
        ),
        (
            "import fitz\ndoc.save('out.pdf', garbage=0, use_objstms=1)\n",
            "doc.save('out.pdf', prune=False, object_streams=True)",
        ),
        ("import fitz\ndoc.save('out.pdf', incremental=False, deflate_images=True)\n", "doc.save('out.pdf')"),
        (
            "import fitz\ndoc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw='u', owner_pw='o')\n",
            "doc.save(path, encryption=pylopdf.Encryption(user_password='u', owner_password='o'))",
        ),
        (
            "import fitz as f\ndoc.save(path, encryption=f.PDF_ENCRYPT_AES_256, permissions=f.PDF_PERM_PRINT)\n",
            "doc.save(path, encryption=f.Encryption(permissions=f.Permissions.PRINT))",
        ),
        (
            "import fitz\npix.save('a.png', output='png')\ndata = pix.tobytes('png')\n",
            'pix.save(\'a.png\', format="png")\ndata = pix.to_bytes("png")',
        ),
        ("import fitz\ndoc.save(path, encryption=fitz.PDF_ENCRYPT_NONE, owner_pw='o')\n", "doc.save(path)"),
        ("import fitz\ndata = doc.tobytes(garbage=3)\n", "data = doc.to_bytes(dedupe=True, renumber=True)"),
        ("import fitz\ndoc = fitz.open()\ndata = doc.write()\n", "data = doc.to_bytes()"),
        ("import fitz\ndoc.authenticate(password)\n", "doc.unlock(password)"),
        ("import fitz\nlocked = doc.is_encrypted\n", "locked = doc.needs_password"),
        ("import fitz\ntext = page.get_text()\n", "text = page.text()"),
        ("import fitz\ntext = page.get_text('text', sort=True)\n", "text = page.text()"),
        ("import fitz\nlayout = page.get_text('dict')\n", "layout = page.layout().to_dict()"),
        ("import fitz\nlayout = page.get_text(option='rawdict')\n", "layout = page.layout(chars=True).to_dict()"),
        ("import fitz\nhits = page.search_for('total', hit_max=10)\n", "hits = page.search('total', max_hits=10)"),
        ("import fitz\ntext = doc.get_page_text(2)\n", "text = doc.pages[2].text()"),
        ("import fitz\nwords = [w[4] for w in page.get_text('words')]\n", "words = [w.text for w in page.words()]"),
        (
            "import fitz\nfor w in page.get_text('words'):\n    print(w[:4], w[7])\n",
            "for w in page.words():\n    print(w.bbox, w.index)",
        ),
        (
            "import fitz\nnames = {w for x0, y0, x1, y1, w, *_ in page.get_text('words') if x0 > 10}\n",
            "names = {word.text for word in page.words() if word.bbox.x0 > 10}",
        ),
        (
            "import fitz\ntexts = [b[4] for b in page.get_text('blocks')]\n",
            "texts = [b.text for b in page.layout().blocks]",
        ),
        (
            "import fitz\nfor x0, y0, x1, y1, word, *_ in page.get_text('words'):\n    print(word, x0)\n",
            "for item in page.words():\n    print(item.text, item.bbox.x0)",
        ),
        (
            (
                "import fitz\ntabs = page.find_tables()\n"
                "for tab in tabs.tables:\n    print(tab.extract(), tab.row_count, tab.col_count)\n"
            ),
            "tabs = page.tables()\nfor tab in tabs:\n    print(tab.rows, len(tab.rows), len(tab.rows[0]))",
        ),
        (
            "import fitz\nrows = page.find_tables(clip=r, strategy='text')[0].extract()\n",
            "rows = page.tables(strategy='text', clip=r)[0].rows",
        ),
        ("import fitz\nn = len(page.find_tables().tables)\n", "n = len(page.tables())"),
        (
            "import fitz\nxrefs = [img[0] for img in page.get_images(full=True)]\n",
            "xrefs = [img.xref for img in page.resources.images]",
        ),
        (
            "import fitz\nfor xref, smask, w, h, *_ in doc.get_page_images(0):\n    print(xref, w * h)\n",
            "for image in doc.pages[0].resources.images:\n    print(image.xref, image.width * image.height)",
        ),
        (
            "import fitz\ndata = doc.extract_image(xref)['image']\n",
            "data = doc.images[xref].extract().data",
        ),
        (
            "import fitz\nsize = (doc.extract_image(12)['width'], doc.extract_image(12)['bpc'])\n",
            "size = (doc.images[12].width, doc.images[12].bits)",
        ),
        (
            "import fitz\npix = page.get_pixmap(matrix=fitz.Matrix(2, 2), annots=False, colorspace=fitz.csRGB)\n",
            "pix = page.pixmap(matrix=pylopdf.Matrix(2, 2), annotations=False)",
        ),
        ("import fitz\npix = page.get_pixmap(matrix=m, dpi=150)\n", "pix = page.pixmap(dpi=150)"),
        ("import fitz\npix = page.get_pixmap(matrix=m, dpi=None)\n", "pix = page.pixmap(matrix=m)"),
        ("import fitz\npix = doc.get_page_pixmap(3, dpi=72)\n", "pix = doc.pages[3].pixmap(dpi=72)"),
        ("import fitz\nsvg = page.get_svg_image(matrix=fitz.Identity)\n", "svg = page.svg()"),
        (
            "import pylopdf\npngs = doc.render_pages([0, 1], matrix=m, dpi=144, workers=2)\n",
            "pngs = doc.render([0, 1], dpi=144, workers=2)",
        ),
        (
            "import pylopdf\npng = doc.render_page(0, 2.0, max_size=9)\n",
            "png = doc.pages[0].pixmap(dpi=144, alpha=True).to_bytes(max_size=9)",
        ),
        (
            "import pylopdf\npng = doc.render_page(0, s, background=(1, 2, 3))\n",
            "png = doc.pages[0].pixmap(dpi=72 * (s), alpha=True, background=(1, 2, 3)).to_bytes()",
        ),
        ("import pylopdf\nsvg = doc.render_page_svg(1, max_size=9)\n", "svg = doc.pages[1].svg(max_size=9)"),
        ("import pylopdf\nsvg = page.render_svg()\n", "svg = page.svg()"),
        (
            "import fitz\npix = page.get_pixmap(colorspace=fitz.csGRAY)\n",
            "pix = page.pixmap(colorspace=pylopdf.Colorspace.GRAY)",
        ),
        ("import fitz\nn = (pix.width, pix.samples)\n", "n = (pix.width, bytes(pix))"),
        ("import fitz\nview = pix.samples_mv\n", "view = memoryview(pix)"),
        ("import fitz\npng = pix.tobytes()\n", "png = pix.to_bytes()"),
        ("import fitz\njpeg = pix.tobytes('jpg', jpg_quality=80)\n", 'jpeg = pix.to_bytes("jpeg", quality=80)'),
        ("import fitz\npix.save('out.jpg', jpg_quality=70)\n", "pix.save('out.jpg', quality=70)"),
        ("import fitz\npix.save(path, output='png')\n", 'pix.save(path, format="png")'),
        ("import fitz\ncs = fitz.csGRAY\n", "cs = pylopdf.Colorspace.GRAY"),
        (
            "import fitz\ndoc.set_metadata({'title': t, 'creationDate': d, 'format': 'PDF 1.7'})\n",
            'doc.metadata.update({"title": t, "created": d})',
        ),
        ("import fitz\ndoc.set_metadata({})\n", "doc.metadata.clear()"),
        ("import fitz\ntitle = doc.metadata['title']\n", 'title = doc.metadata.get("title", "")'),
        ("import fitz\nwhen = doc.metadata.get('modDate')\n", 'when = doc.metadata.get("modified", "")'),
        ("import fitz\nversion = doc.metadata['format']\n", 'version = f"PDF {doc.version}"'),
        ("import fitz\nmethod = doc.metadata['encryption']\n", "method = doc.encryption_method"),
        ("import fitz\nxmp = doc.get_xml_metadata()\n", 'xmp = (doc.xmp or "")'),
        ("import fitz\ndoc.set_xml_metadata(packet)\n", "doc.xmp = packet"),
        ("import fitz\ndoc.del_xml_metadata()\n", "del doc.xmp"),
        ("import fitz\nmode = (doc.pagemode, doc.pagelayout)\n", "mode = (doc.page_mode, doc.page_layout)"),
        ("import fitz\ndoc.set_pagemode('/useoutlines')\n", 'doc.page_mode = "UseOutlines"'),
        ("import fitz\ndoc.set_pagelayout('twocolumnleft')\n", 'doc.page_layout = "TwoColumnLeft"'),
        ("import fitz\ndoc.set_language('zh-TW')\n", "doc.language = 'zh-TW'"),
        ("import fitz\ndoc.set_language(None)\n", "del doc.language"),
        (
            "import fitz\ntoc = doc.get_toc()\n",
            (
                "toc = [[entry.level, entry.title, -1 if entry.page is None else entry.page + 1] "
                "for entry in doc.outline.entries()]"
            ),
        ),
        (
            "import fitz\ndoc.set_toc(toc)\n",
            (
                "doc.outline = [pylopdf.OutlineEntry(item[0], item[1], pylopdf.Destination(item[2] - 1) "
                "if item[2] > 0 else None) for item in toc]"
            ),
        ),
        ("import fitz\ndoc = fitz.open()\nlabel = doc[0].get_label()\n", "label = doc.pages[0].label"),
        ("import fitz\ndef f(item: fitz.Outline) -> None: ...\n", "def f(item: pylopdf.OutlineItem) -> None: ..."),
        ("from fitz import Outline\nitem: Outline\n", "from pylopdf import OutlineItem\nitem: OutlineItem"),
        ("import fitz\ndoc = fitz.open()\ndoc[0].set_rotation(90)\n", "doc.pages[0].rotation = 90"),
        ("import fitz\ndoc = fitz.open()\nfor page in doc:\n    page.set_rotation(r)\n", "    page.rotation = r"),
        ("import fitz\nsize = (page.mediabox.width, page.mediabox.height)\n", "page.boxes.media.width"),
        (
            "import fitz\npage.set_mediabox(fitz.Rect(0, 0, 200, 300))\n",
            "page.boxes.media = pylopdf.Rect(0, 0, 200, 300)",
        ),
        (
            'import fitz\npage.insert_link({"kind": fitz.LINK_URI, "from": r, "uri": "https://e.test/"})\n',
            'page.links.add(r, "https://e.test/")',
        ),
        (
            'import fitz\npage.insert_link({"kind": fitz.LINK_GOTO, "from": r, "page": 2, "to": p, "zoom": 2})\n',
            "page.links.add(r, pylopdf.Destination(2, point=p, zoom=2))",
        ),
        (
            'import fitz\npage.insert_link({"kind": fitz.LINK_GOTOR, "from": r, "file": "a.pdf", "page": 3})\n',
            'page.links.add(r, pylopdf.RemoteDestination("a.pdf", page=3))',
        ),
        (
            'import fitz\npage.insert_link({"kind": fitz.LINK_NAMED, "from": r, "name": "NextPage"})\n',
            'page.links.add(r, pylopdf.NamedAction("NextPage"))',
        ),
        ("import fitz\nfound = doc.has_links()\n", "found = any(page.links for page in doc.pages)"),
        ("import fitz\nfound = doc.has_annots()\n", "found = any(page.annotations for page in doc.pages)"),
        (
            "import fitz\nannot = page.add_highlight_annot(hits)\n",
            "annot = page.annotations.add(pylopdf.Highlight(hits))",
        ),
        ("import fitz\npage.add_underline_annot(hits)\n", "page.annotations.add(pylopdf.Underline(hits))"),
        ("import fitz\npage.add_strikeout_annot(quads=q)\n", "page.annotations.add(pylopdf.StrikeOut(q))"),
        (
            "import fitz\npage.add_squiggly_annot(q, color=(0, 0, 1))\n",
            "page.annotations.add(pylopdf.Squiggly(q, color=pylopdf.Color(0, 0, 1)))",
        ),
        ("import fitz\npage.add_text_annot(p, 'hi')\n", "page.annotations.add(pylopdf.Note(p, contents='hi'))"),
        (
            "import fitz\npage.add_text_annot(p, t, icon='Help')\n",
            "page.annotations.add(pylopdf.Note(p, contents=t, icon=pylopdf.NoteIcon.HELP))",
        ),
        (
            "import fitz\npage.add_freetext_annot(r, 'hi', fontsize=9, fill_color=(1, 1, 0))\n",
            "page.annotations.add(pylopdf.FreeText(r, 'hi', size=9, color=pylopdf.Color(1, 1, 0)))",
        ),
        ("import fitz\npage.add_stamp_annot(r)\n", "page.annotations.add(pylopdf.Stamp(r))"),
        (
            "import fitz\npage.add_stamp_annot(r, stamp=fitz.STAMP_TopSecret)\n",
            "page.annotations.add(pylopdf.Stamp(r, icon=pylopdf.StampIcon.TOP_SECRET))",
        ),
        (
            "import fitz\npage.add_stamp_annot(r, 13)\n",
            "page.annotations.add(pylopdf.Stamp(r, icon=pylopdf.StampIcon.DRAFT))",
        ),
        ("import fitz\npage.add_caret_annot(p)\n", "page.annotations.add(pylopdf.Caret(p))"),
        ("import fitz\nfor w in page.widgets():\n    pass\n", "for w in list(page.widgets):\n    pass"),
        ("import fitz\nannot.set_opacity(0.5)\n", "annot.opacity = 0.5"),
        ("import fitz\nannot.set_rect(r)\n", "annot.rect = r"),
        ("import fitz\nannot.set_colors(stroke=(1, 0, 0))\n", "annot.color = pylopdf.Color(1, 0, 0)"),
        ("import fitz\nannot.set_colors(fill=None)\n", "del annot.interior_color"),
        ("import fitz\nannot.set_info(content='x')\n", "annot.contents = 'x'"),
        ("import fitz\nannot.set_border(width=2)\n", "annot.border = pylopdf.Border(2)"),
        (
            "import fitz\nannot.set_line_ends(fitz.PDF_ANNOT_LE_SLASH, fitz.PDF_ANNOT_LE_NONE)\n",
            "annot.line_endings = (pylopdf.LineEnding.SLASH, pylopdf.LineEnding.NONE)",
        ),
        (
            "import fitz\npage.add_redact_annot(q, text='gone')\n",
            "page.annotations.add(pylopdf.Redaction(q, overlay_text='gone', fill=pylopdf.Color(1, 1, 1)))",
        ),
        (
            "import fitz\npage.add_redact_annot(q, fill=(0, 0, 0))\n",
            "page.annotations.add(pylopdf.Redaction(q, fill=pylopdf.Color(0, 0, 0)))",
        ),
        (
            "import fitz\npage.add_file_annot(p, data, 'a.txt', desc='notes')\n",
            "page.annotations.add(pylopdf.FileAttachment(p, data, filename='a.txt', description='notes'))",
        ),
        (
            "import fitz\nending = fitz.PDF_ANNOT_LE_R_CLOSED_ARROW\n",
            "ending = pylopdf.LineEnding.REVERSED_CLOSED_ARROW",
        ),
        ("import fitz\nmode = fitz.PDF_BM_ColorDodge\n", "mode = pylopdf.BlendMode.COLOR_DODGE"),
        ("import fitz\nflag = fitz.SigFlag_AppendOnly\n", "flag = pylopdf.SignatureFlags.APPEND_ONLY"),
        ("import fitz\ndoc.bake()\n", "doc.flatten()"),
        ("import fitz\nannot.set_border(clouds=2)\n", "annot.clouds = 2"),
        ("import fitz\nstyle = fitz.PDF_BORDER_STYLE_DASHED\n", "style = pylopdf.BorderStyle.DASHED"),
        ("import fitz\ndoc.bake(annots=False)\n", "doc.flatten(annotations=False)"),
        ("import fitz\ndoc.bake(True, False)\n", "doc.flatten(annotations=True, widgets=False)"),
        ("import fitz\nicon = fitz.STAMP_NotForPublicRelease\n", "icon = pylopdf.StampIcon.NOT_FOR_PUBLIC_RELEASE"),
        ("import fitz\nannot = page.add_rect_annot(r)\n", "annot = page.annotations.add(pylopdf.Square(r))"),
        ("import fitz\nannot = page.add_circle_annot(rect=r)\n", "annot = page.annotations.add(pylopdf.Circle(r))"),
        ("import fitz\npage.add_line_annot(a, b)\n", "page.annotations.add(pylopdf.LineAnnotation(a, b))"),
        ("import fitz\npage.add_polygon_annot(points)\n", "page.annotations.add(pylopdf.Polygon(points))"),
        ("import fitz\npage.add_polyline_annot(points=ps)\n", "page.annotations.add(pylopdf.PolyLine(ps))"),
        ("import fitz\npage.add_ink_annot(strokes)\n", "page.annotations.add(pylopdf.Ink(strokes))"),
        ("import fitz\npage.delete_annot(annot)\n", "del page.annotations[page.annotations.index(annot)]"),
        ("import fitz\ndef f(annot: fitz.Annot) -> None: ...\n", "def f(annot: pylopdf.Annotation) -> None: ..."),
        ("import fitz\nfont = fitz.Font('helv')\n", 'font = pylopdf.Font.standard("Helvetica")'),
        ("import fitz\nfont = fitz.Font(fontname='times-bold')\n", 'font = pylopdf.Font.standard("Times-Bold")'),
        ("import fitz\nfont = fitz.Font(fontfile=path)\n", "font = pylopdf.Font.from_file(path)"),
        ("import fitz\nfont = fitz.Font(fontbuffer=data, embed=1)\n", "font = pylopdf.Font.from_bytes(data)"),
        (
            "from fitz import Font\nwidth = Font('tibo').text_length(text, fontsize=9)\n",
            'width = Font.standard("Times-Bold").text_width(text, 9)',
        ),
        ("import fitz\nwidth = font.text_length(text)\n", "width = font.text_width(text, 11)"),
        (
            "import fitz\nwidth = fitz.get_text_length(text, fontname='cour', fontsize=10)\n",
            'width = pylopdf.Font.standard("Courier").text_width(text, 10)',
        ),
        ("import fitz\nfound = font.has_glyph(ord(char))\n", "found = font.has_glyph(char)"),
        ("import fitz\nfound = font.has_glyph(0x3042)\n", "found = font.has_glyph(chr(0x3042))"),
        ("import fitz\npage.draw_line(a, b)\n", "with page.draw() as canvas:\n    canvas.line(a, b)\n"),
        (
            "import fitz\npage.draw_line(a, b, color=(1, 0, 0), width=2, dashes='[3 4] 0', lineCap=1)\n",
            (
                "    canvas.line(a, b, stroke=pylopdf.Stroke(paint=pylopdf.Color(1, 0, 0), width=2, "
                "cap=pylopdf.LineCap.ROUND, dashes=(3, 4)))"
            ),
        ),
        (
            "import fitz\npage.draw_rect(r, color=None, fill=(0.9,), overlay=False)\n",
            "with page.draw(underlay=True) as canvas:\n    canvas.rect(r, fill=pylopdf.Color(0.9, 0.9, 0.9))",
        ),
        (
            "import fitz\npage.draw_rect(r, fill=rgb, fill_opacity=0.5)\n",
            "    canvas.rect(r, fill=pylopdf.Color(*rgb, 0.5), stroke=pylopdf.Color(0, 0, 0))",
        ),
        ("import fitz\npage.draw_polyline(points)\n", "    canvas.polygon(points, close=False)"),
        ("import fitz\npage.draw_polyline(points, closePath=True)\n", "    canvas.polygon(points)"),
        (
            "import fitz\npage.draw_circle(c, 5, fill=(1, 1, 1), stroke_opacity=0.3)\n",
            "    canvas.circle(c, 5, fill=pylopdf.Color(1, 1, 1), stroke=pylopdf.Color(0, 0, 0, 0.3))",
        ),
        ("import fitz\npage.draw_oval(r, color=(0, 0, 1))\n", "    canvas.ellipse(r, stroke=pylopdf.Color(0, 0, 1))"),
        ("import fitz\npage.draw_bezier(a, b, c, d)\n", "    canvas.bezier(a, b, c, d)"),
        (
            "import fitz\npage.draw_squiggle(a, b, breadth=4)\n",
            "    canvas.path(pylopdf.PathBuilder().wave(a, b, breadth=4))",
        ),
        ("import fitz as f\npage.draw_zigzag(a, b)\n", "    canvas.path(f.PathBuilder().zigzag(a, b, breadth=2))"),
        (
            "import fitz\ncanvas = None\nfor page in doc:\n    page.draw_line(a, b)\n",
            "    with page.draw() as page_canvas:\n        page_canvas.line(a, b)",
        ),
        (
            "import fitz\n# The rule under the heading.\npage.draw_line(a, b)\n",
            "# The rule under the heading.\nwith page.draw() as canvas:",
        ),
        (
            "import fitz\npage.insert_text((72, 72), 'Hi', fontsize=9, color=(1, 0, 0))\n",
            "with page.draw() as canvas:\n    canvas.text('Hi', (72, 72), size=9, fill=pylopdf.Color(1, 0, 0))\n",
        ),
        ("import fitz\npage.insert_text(p, ['a', 'b'])\n", "    canvas.text(\"\\n\".join(['a', 'b']), p)"),
        (
            "import fitz\npage.insert_text(p, t, fontname='cobo')\n",
            '    canvas.text(t, p, font=pylopdf.Font.standard("Courier-Bold"))',
        ),
        (
            "import fitz\npage.insert_text(p, t, fontfile=path, fontname='F0')\n",
            "canvas.text(t, p, font=pylopdf.Font.from_file(path))",
        ),
        (
            "import pylopdf\npage.insert_text(p, t, font_language='ja', fontname='tiro')\n",
            "canvas.text(t, p, font=pylopdf.Font.installed(language='ja', serif=True))",
        ),
        (
            "import fitz\npage.insert_text(p, t, lineheight=1.5, overlay=False)\n",
            "with page.draw(underlay=True) as canvas:\n    canvas.text(t, p, line_height=1.5)\n",
        ),
        # pymupdf turns text anticlockwise, the canvas clockwise.
        ("import fitz\npage.insert_text(p, t, rotate=90)\n", "    canvas.text(t, p, rotate=270)"),
        ("import fitz\npage.insert_text(p, t, rotate=-90, fontsize=8)\n", "    canvas.text(t, p, size=8, rotate=90)"),
        ("import fitz\npage.insert_text(p, t, rotate=0)\n", "    canvas.text(t, p)\n"),
        ("import fitz\npage.insert_textbox(r, t, rotate=angle)\n", "    canvas.textbox(r, t, rotate=-angle)"),
        (
            "import fitz\npage.insert_textbox(r, t, rotate=270, lineheight=2)\n",
            "    canvas.textbox(r, t, line_height=2, rotate=90)",
        ),
        (
            "import fitz\npage.insert_textbox(r, t, align=fitz.TEXT_ALIGN_CENTER)\n",
            '    canvas.textbox(r, t, align="center")',
        ),
        ("import fitz\npage.insert_textbox(r, t, align=3)\n", '    canvas.textbox(r, t, align="justify")'),
        ("import fitz\npage.insert_textbox(r, buffer=t, expandtabs=4)\n", "    canvas.textbox(r, (t).expandtabs(4))"),
        (
            "import fitz\nspare = page.insert_textbox(r, t, fontsize=10)\n",
            "with page.draw() as canvas:\n    spare = canvas.textbox(r, t, size=10).spare_height\n",
        ),
        (
            "import fitz\npage.insert_image(r, filename='logo.png')\n",
            "with page.draw() as canvas:\n    canvas.image(r, 'logo.png')\n",
        ),
        (
            "import fitz\npage.insert_image(r, stream=data, keep_proportion=False, overlay=False)\n",
            "with page.draw(underlay=True) as canvas:\n    canvas.image(r, data, keep_aspect=False)\n",
        ),
        # pymupdf turns images anticlockwise, the canvas and pylopdf 0.15 clockwise.
        ("import fitz\npage.insert_image(r, pixmap=pix, rotate=90)\n", "    canvas.image(r, pix, rotate=270)"),
        ("import fitz\npage.insert_image(r, pixmap=pix, rotate=angle)\n", "    canvas.image(r, pix, rotate=-angle)"),
        (
            "import pylopdf\npage.insert_image(r, stream=data, rotate=90, max_size=None)\n",
            "canvas.image(r, data, rotate=90, max_size=None)",
        ),
        (
            "import fitz\nxref = page.insert_image(r, filename='logo.png')\n",
            "\nxref = page.content(text_runs=False, paths=False, annotations=False).images[-1].xref\n",
        ),
        (
            "import fitz\nxref = page.insert_image(r, stream=data, overlay=False)\n",
            "xref = page.content(text_runs=False, paths=False, annotations=False).images[0].xref\n",
        ),
        (
            "import fitz\npage.show_pdf_page(r, src, 2)\n",
            "with page.draw() as canvas:\n    canvas.page(r, src.pages[2])\n",
        ),
        (
            "import fitz\npage.show_pdf_page(r, fitz.open(path), keep_proportion=False, overlay=False)\n",
            "    canvas.page(r, pylopdf.open(path).pages[0], keep_aspect=False)\n",
        ),
        ("import pylopdf\npage.show_pdf_page(r, src=other, pno=-1)\n", "    canvas.page(r, other.pages[-1])"),
        # pymupdf turns the page anticlockwise, the canvas clockwise; both clip in the source page's coordinates.
        (
            "import fitz\npage.show_pdf_page(r, src, 1, clip=c, rotate=90, keep_proportion=False)\n",
            "    canvas.page(r, src.pages[1], clip=c, keep_aspect=False, rotate=270)",
        ),
        (
            "import fitz\npage.show_pdf_page(r, src, rotate=angle, clip=None)\n",
            "    canvas.page(r, src.pages[0], rotate=-angle)",
        ),
        (
            "import pylopdf\nwith page.draw() as canvas:\n    canvas.line(a, b)\npage.insert_image(r, stream=d)\n",
            "with page.draw() as canvas:\n    canvas.line(a, b)\nwith page.draw() as canvas:\n    canvas.image(r, d)\n",
        ),
        (
            "import pylopdf\nwith page.draw() as canvas:\n    page.insert_image(r, stream=d)\n",
            "with page.draw() as canvas:\n    with page.draw() as page_canvas:\n        page_canvas.image(r, d)\n",
        ),
        (
            "import fitz\npage.insert_text(a, 'x')\npage.insert_text(b, 'y', color=(1, 0, 0))\n",
            "with page.draw() as canvas:\n    canvas.text('x', a)\n    canvas.text('y', b, fill=pylopdf.Color(1, ",
        ),
    ],
)
def test_codemod_rules(migrate_source: Migrate, source: str, expected: str) -> None:
    result = migrate_source(source)
    assert expected in result.code
    assert result.todos == ()


@pytest.mark.parametrize(
    ("source", "blocks"),
    [
        pytest.param("page.insert_text(a, 'x')\npage.insert_textbox(r, 'y')\n", 1, id="one-page"),
        pytest.param("doc[0].insert_text(a, 'x')\ndoc[0].insert_text(b, 'y')\n", 1, id="subscript"),
        pytest.param("page.insert_text(a, 'x')\n\npage.insert_text(b, 'y')\n", 2, id="blank-line"),
        pytest.param("page.insert_text(a, 'x')\n# Then y.\npage.insert_text(b, 'y')\n", 2, id="comment"),
        pytest.param("page.insert_text(a, 'x')\nother.insert_text(b, 'y')\n", 2, id="other-page"),
        pytest.param("page.insert_text(a, 'x')\npage.insert_text(b, 'y', overlay=False)\n", 2, id="underlay"),
        pytest.param("new().insert_text(a, 'x')\nnew().insert_text(b, 'y')\n", 2, id="call-receiver"),
        pytest.param("page.insert_text(a, 'x')\npage.insert_text(b, page.get_text())\n", 2, id="reads-the-page"),
        pytest.param("page.insert_text(a, 'x')\npage.insert_text(b, str(n))\n", 2, id="other-call"),
        pytest.param("page.insert_text(a, 'x')\npage.insert_text(b, ['y', 'z'])\n", 1, id="string-method"),
        pytest.param("page.draw_line(a, b)\npage.insert_text(a, 'x', color=(1, 0, 0))\n", 1, id="path-and-text"),
        pytest.param("page.insert_text(a, 'x')\npage.insert_image(r, filename='a.png')\n", 1, id="text-and-image"),
        # A placed page could be this one, whose earlier drawing a joined block would not show.
        pytest.param("page.insert_text(a, 'x')\npage.show_pdf_page(r, doc, 0)\n", 2, id="placed-page"),
        pytest.param("page.show_pdf_page(r, doc, 0)\npage.insert_text(a, 'x')\n", 1, id="page-then-text"),
        # An assignment reads the page that its block alone leaves.
        pytest.param("page.insert_text(a, 'x')\nxref = page.insert_image(r, stream=d)\n", 2, id="assigned-image"),
    ],
)
def test_codemod_joins_consecutive_canvas_blocks_of_one_page(migrate_source: Migrate, source: str, blocks: int) -> None:
    # A joined block commits once, after its last call, so no later call may
    # be able to read what an earlier one drew.
    result = migrate_source(f"import fitz\n{source}")
    assert result.code.count(".draw(") == blocks, result.code
    assert result.code.count("canvas.") == 2, result.code


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "import pylopdf\ndoc = pylopdf.open(stream=data, max_decompressed_size=9)\n",
            "doc = pylopdf.open(data, limits=pylopdf.DocumentLimits(max_decompressed_size=9))",
        ),
        (
            "import pylopdf\ndoc = pylopdf.Document('a.pdf', password='pw')\n",
            "doc = pylopdf.open('a.pdf', password='pw')",
        ),
        ("import pylopdf as pl\ndoc = pl.Document()\nfirst = doc[0]\n", "first = doc.pages[0]"),
        (
            "from pylopdf import FileDataError\ntry:\n    pass\nexcept FileDataError:\n    pass\n",
            "except MalformedPdfError:",
        ),
        (
            "import pylopdf\nwith pylopdf.open(path) as doc:\n    doc.fullcopy_page(0)\n",
            "doc.pages.extend(doc.pages[0])",
        ),
        (
            "import pylopdf\ndoc.save(path, garbage=1, object_streams=True, user_pw='u')\n",
            "doc.save(path, object_streams=True, encryption=pylopdf.Encryption(user_password='u'))",
        ),
        (
            "import pylopdf\ndoc = pylopdf.open(path)\ndata = doc.tobytes(max_size=None)\n",
            "data = doc.to_bytes(max_size=None)",
        ),
        ("import pylopdf\ndoc = pylopdf.open(path)\ntext = doc.get_text([0, 1])\n", "text = doc.text([0, 1])"),
        (
            "import pylopdf\nmd = doc.to_markdown(pages=[0], table_strategy=None)\n",
            "md = doc.markdown(pages=[0], table_strategy=None)",
        ),
        ("import pylopdf\nhits = page.search_for(text='a', max_hits=None)\n", "hits = page.search('a', max_hits=None)"),
        (
            "import pylopdf\ntable = page.find_tables(strategy=None)[0]\nscore = (table.confidence, table.strategy)\n",
            "table = page.tables()[0]\nscore = (table.diagnostics.confidence, table.diagnostics.strategy)",
        ),
        (
            'import pylopdf\ndoc.set_metadata({"author": "A", "modDate": "D:2026"})\n',
            'doc.metadata.update({"author": "A", "modified": "D:2026"})',
        ),
        ("import pylopdf\ncreated = doc.metadata['creationDate']\n", 'created = doc.metadata.get("created", "")'),
        ("import pylopdf\nlabel = page.get_label()\n", "label = page.label"),
        ("import pylopdf\npage.add_link_annot(rect, uri)\n", "page.links.add(rect, uri)"),
        (
            "import pylopdf\npage.add_highlight_annot(rects, color=(1, 0, 0), content='n')\n",
            "page.annotations.add(pylopdf.Highlight(rects, color=pylopdf.Color(1, 0, 0), contents='n'))",
        ),
        (
            "import pylopdf\npage.add_highlight_annot(rects, color=rgb, opacity=0.5)\n",
            "page.annotations.add(pylopdf.Highlight(rects, color=pylopdf.Color(*rgb), opacity=0.5))",
        ),
        (
            'import pylopdf\npage.insert_link({"kind": pylopdf.LINK_LAUNCH, "from": r, "file": "a.txt"})\n',
            'page.links.add(r, pylopdf.Launch("a.txt"))',
        ),
        ("import pylopdf\ndoc = pylopdf.open(path)\ndoc.set_fallback_font(None)\n", "doc.fallback_fonts = {}"),
        ("import pylopdf\ndoc.set_form_field('name', value=True)\n", "doc.form['name'].value = True"),
        (
            (
                "import pylopdf\ndoc = pylopdf.open(path)\nfacts = doc.complexity\nn = facts['page_count']\n"
                "big = doc.complexity['object_count'] > 1000\n"
            ),
            "n = facts.page_count\nbig = doc.complexity.object_count > 1000",
        ),
        (
            (
                "import pylopdf\nresult = doc.rewrite_images(quality=60)\nsaved = result['bytes_saved']\n"
                "count = doc.rewrite_images(quality=50)['rewritten']\n"
            ),
            "saved = result.bytes_saved\ncount = doc.rewrite_images(quality=50).rewritten",
        ),
        (
            "from pylopdf import INFINITE_RECT, planish_line\nr = INFINITE_RECT()\nm = planish_line(a, b)\n",
            "from pylopdf import Rect, Matrix\nr = Rect.INFINITE\nm = Matrix.planish(a, b)",
        ),
        (
            "import pylopdf\nfields = doc.get_form_fields()\n",
            (
                'fields = [{"name": name, "type": str(field.type), "value": field.value} '
                "for name, field in doc.form.items()]"
            ),
        ),
        ("import pylopdf\ndoc = pylopdf.open(path)\ndoc[0].set_rotation(90)\n", "doc.pages[0].rotation = 90"),
        (
            "import pylopdf\npage.set_cropbox((0, 0, 10, 10))\ncrop = page.cropbox\n",
            "page.boxes.crop = (0, 0, 10, 10)\ncrop = page.boxes.crop",
        ),
        (
            "import pylopdf\nlabels = doc.get_page_labels()\n",
            (
                'labels = [{"startpage": label.start, "style": label.style or "", "prefix": label.prefix, '
                '"firstpagenum": label.first} for label in doc.page_labels]'
            ),
        ),
        (
            "import pylopdf\nfont = pylopdf.Font(language='ja', is_serif=True, is_bold=True)\n",
            "font = pylopdf.Font.installed(language='ja', serif=True, weight=700)",
        ),
        (
            "from pylopdf import Font\nfont = Font(family='Noto Sans Hebrew', weight=650, is_italic=False)\n",
            "font = Font.installed(family='Noto Sans Hebrew', weight=650)",
        ),
        (
            "import pylopdf\nfont = pylopdf.Font(fontfile=p, fontindex=1, weight=700, max_font_size=None)\n",
            "font = pylopdf.Font.from_file(p, index=1, weight=700, max_size=None)",
        ),
        ("import pylopdf\nfont = pylopdf.Font(None, None, data)\n", "font = pylopdf.Font.from_bytes(data)"),
        (
            "import pylopdf\nwidth = font.text_length('x', max_text_size=None)\n",
            "width = font.text_width('x', 11, max_text_size=None)",
        ),
    ],
)
def test_codemod_migrates_pylopdf_015_programs(migrate_source: Migrate, source: str, expected: str) -> None:
    result = migrate_source(source)
    assert expected in result.code
    assert result.todos == ()


@pytest.mark.parametrize(
    ("package", "arguments", "password", "encrypted"),
    [
        ("pylopdf", "user_pw=None", None, False),
        ("pylopdf", "user_pw=None, owner_pw=None", None, False),
        ("pylopdf", "user_pw=None, owner_pw='owner'", "owner", True),
        ("pylopdf", "user_pw='user', owner_pw=None", "user", True),
        ("fitz", "encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=None, owner_pw='owner'", "owner", True),
    ],
)
def test_codemod_preserves_none_passwords(
    migrate_source: Migrate, package: str, arguments: str, password: str | None, *, encrypted: bool
) -> None:
    source = f"import {package}\ndoc = {package}.open()\ndoc.new_page()\ndata = doc.tobytes({arguments})\n"

    result = migrate_source(source)
    namespace: dict[str, object] = {}
    exec(result.code, namespace)  # noqa: S102 - execute the trusted migration fixture.
    data = namespace["data"]

    assert result.todos == ()
    assert isinstance(data, bytes)
    with pylopdf.open(data, password=password) as doc:
        assert len(doc.pages) == 1
        assert (doc.encryption_method is not None) is encrypted


def test_codemod_migrates_outline_and_page_label_calls(migrate_source: Migrate) -> None:
    source = (
        "import fitz\ndoc = fitz.open()\nfor _ in range(4):\n    doc.new_page()\n"
        "doc.set_toc([[1, 'Start', 1], [2, 'Detail', 3], [1, 'Nowhere', 0]])\n"
        "toc = doc.get_toc()\n"
        "doc.set_page_labels([{'startpage': 0, 'style': 'r'}, {'startpage': 2, 'prefix': 'A-', 'style': 'D'}])\n"
        "rules = doc.get_page_labels()\nlabels = [page.get_label() for page in doc]\n"
    )

    result = migrate_source(source)
    namespace: dict[str, object] = {}
    exec(result.code, namespace)  # noqa: S102 - execute the trusted migration fixture.

    assert result.todos == ()
    assert namespace["toc"] == [[1, "Start", 1], [2, "Detail", 3], [1, "Nowhere", -1]]
    assert namespace["rules"] == [
        {"startpage": 0, "style": "r", "prefix": "", "firstpagenum": 1},
        {"startpage": 2, "style": "D", "prefix": "A-", "firstpagenum": 1},
    ]
    assert namespace["labels"] == ["i", "ii", "A-1", "A-2"]


def test_codemod_marks_pymupdf_page_boxes_for_their_coordinates(migrate_source: Migrate) -> None:
    result = migrate_source("import fitz\ncrop = page.cropbox\npage.set_trimbox(rect)\nannot.set_rotation(90)\n")

    assert "crop = page.boxes.crop\n" in result.code
    assert "page.boxes.trim = rect\n" in result.code
    assert "annot.set_rotation(90)" in result.code  # Only a page's rotation is an attribute.
    messages = [message for _, message in result.todos]
    assert all("flip y against the top of the MediaBox" in message for message in messages[:2])
    assert "set_rotation" in messages[2]


@pytest.mark.parametrize("legacy", ["fitz", "pymupdf"])
def test_codemod_preserves_pylopdf_outline_class_in_mixed_modules(migrate_source: Migrate, legacy: str) -> None:
    source = (
        f"import {legacy} as legacy\nimport pylopdf as current\n"
        "item_type = legacy.Outline\nsequence_type = current.Outline\n"
    )

    result = migrate_source(source)
    namespace: dict[str, object] = {}
    exec(result.code, namespace)  # noqa: S102 - execute the trusted migration fixture.

    assert result.todos == ()
    assert namespace["item_type"] is pylopdf.OutlineItem
    assert namespace["sequence_type"] is pylopdf.Outline


def test_codemod_migrates_a_document_named_pages(migrate_source: Migrate) -> None:
    source = (
        "import fitz\npages = fitz.open()\npages.new_page(width=100, height=200)\n"
        "pages.new_page(width=300, height=400)\npages.select([1])\n"
    )

    result = migrate_source(source)
    namespace: dict[str, object] = {}
    exec(result.code, namespace)  # noqa: S102 - execute the trusted migration fixture.
    doc = namespace["pages"]

    assert result.todos == ()
    assert isinstance(doc, pylopdf.Document)
    assert len(doc.pages) == 1
    assert doc.pages[0].rect == pylopdf.Rect(0, 0, 300, 400)


@pytest.mark.parametrize(
    ("imports", "factory"),
    [
        ("import fitz", "fitz.Document()"),
        ("import fitz as pdf", "pdf.open(stream=source_pdf)"),
        ("from fitz import open", "open(stream=source_pdf)"),
        ("from fitz import Document as Pdf", "Pdf()"),
        ("import pylopdf", "pylopdf.Document()"),
    ],
)
def test_codemod_migrates_inline_document_serialization(migrate_source: Migrate, imports: str, factory: str) -> None:
    source = f"{imports}\ndata = {factory}.tobytes()\n"

    result = migrate_source(source)
    namespace: dict[str, object] = {"source_pdf": pylopdf.Document().to_bytes()}
    exec(result.code, namespace)  # noqa: S102 - execute the trusted migration fixture.

    assert result.todos == ()
    assert isinstance(namespace["data"], bytes)
    assert migrate_source(result.code).code == result.code


def test_codemod_marks_probe_results_for_review(migrate_source: Migrate) -> None:
    result = migrate_source("import pylopdf\ninfo = pylopdf.peek_metadata(stream=data)\n")

    assert "info = pylopdf.probe(data)" in result.code
    assert "Probe record" in result.todos[0][1]


@pytest.mark.parametrize(
    "source",
    [
        # pymupdf's in-place geometry rules leave pylopdf's own methods alone.
        "import pylopdf\nwith page.draw() as canvas:\n    canvas.transform((1, 0, 0, 1, 5, 5))\n",
        # A name bound to something other than a document is not indexed through pages.
        "import pylopdf\ndoc = pylopdf.open(path)\ndoc = {}\nvalue = doc[0]\n",
        # A probe or a builder keeps its own page_count.
        "import pylopdf\nprobe = pylopdf.probe(path)\nn = probe.page_count\n",
        "import pylopdf\nbuilder = pylopdf.create()\nn = builder.page_count\n",
        # Calls that already use doc.pages stay as they are.
        "import pylopdf\ndoc = pylopdf.open(path)\ndoc.pages.select([0])\nfor page in doc.pages:\n    pass\n",
        # Saving with the current options, and another class's save and tobytes, stay as they are.
        "import pylopdf\ndoc.save(path, compress=False, encryption=pylopdf.Encryption(user_password='u'))\n",
        "import pylopdf\npixmap.save(path)\npixmap.save(path, format='png')\ndata = pixmap.to_bytes()\n",
        "import pylopdf\ndoc.save(path, encryption=pylopdf.Encryption(user_password='u'))\n",
        "import pylopdf\ndoc.save(path, encryption=None)\ndata = pixmap.to_bytes(max_size=None)\n",
        "import pylopdf\npages = doc.pages\npages.select([0])\n",
        "import pylopdf\ntext = page.text()\nmd = doc.markdown()\nhits = page.search('a')\n",
        (
            "import pylopdf\nfor table in page.tables():\n    print(table.rows, table.cells)\n"
            "page.apply_ocr(words=page.ocr())\n"
        ),
        (
            "import pylopdf\ndoc.metadata['title'] = doc.metadata['author']\ndel doc.metadata['subject']\n"
            "doc.page_mode = doc.page_layout = None\ndel doc.xmp\nlanguage = doc.language\n"
        ),
        (
            "import pylopdf\nentries = doc.outline.entries()\ndoc.outline = entries\nfirst: pylopdf.Outline\n"
            "for item in doc.outline.walk():\n    item.title = item.title.upper()\nlabels = doc.page_labels\n"
        ),
        (
            "import pylopdf\nfont = pylopdf.Font.from_file(path, weight=700)\n"
            "base = pylopdf.Font.standard('Helvetica')\n"
            "width = font.text_width('x', 11) + font.text_height('x', 11, width=100)\nfound = font.has_glyph('x')\n"
        ),
        (
            "import pylopdf\nwidget.set_opacity(0.5)\nshape.set_rect(r)\nother.set_colors(stroke=c)\n"
            "note = page.annotations.add(pylopdf.Note((1, 2), icon=pylopdf.NoteIcon.KEY))\nnote.opacity = 0.5\n"
        ),
        (
            "import pylopdf\ndoc = pylopdf.open(path)\nfacts = doc.complexity\n"
            "n = doc.complexity.page_count + facts.page_count + doc.rewrite_images(quality=50).rewritten\n"
        ),
    ],
)
def test_codemod_leaves_current_pylopdf_code_alone(migrate_source: Migrate, source: str) -> None:
    result = migrate_source(source)
    assert (result.code, result.rewrites, result.todos) == (source, 0, ())


@pytest.mark.parametrize(
    ("source", "hint"),
    [
        ("import fitz\ntoc = doc.get_toc(False)\n", "Use doc.outline.entries()"),
        ("import fitz\ndata = doc.embfile_get(0)\n", "doc.attachments[name].read()"),
        ("import fitz\ninfo = doc.embfile_info('a')\n", "Read name, filename, description"),
        ("import fitz\nresult = rect.include_rect(other)\n", "Use r | other"),
        ("import fitz\ndoc.set_toc(toc, collapse=0)\n", "Assign a list of OutlineEntry"),
        ("import fitz\nfrom fitz import Story\n", "Use typst for HTML and CSS pagination"),
        ("import fitz.utils\n", "pylopdf has no fitz.utils module"),
        ("from fitz import Rect\nm.prerotate(90)\n", "pylopdf.Matrix(deg) * m"),
        ("import fitz\ndoc.save(path, garbage=4, clean=True)\n", "pylopdf writes AES-256 encryption only"),
        ("import fitz\ndoc.save(path, encryption=fitz.PDF_ENCRYPT_RC4_128)\n", "encryption=Encryption("),
        ("import fitz\ndoc.save(path, encryption=method)\n", "encryption=Encryption("),
        ("import fitz\ndata = doc.tobytes(encryption=method)\n", "encryption=Encryption("),
        ("import fitz\nif not doc.authenticate(password):\n    pass\n", "raises PasswordError for a wrong password"),
        ("import fitz\nwords = page.get_text('words')\n", "Word records with text, bbox, block, line, and index"),
        ("import fitz\nblocks = page.get_text('blocks')\n", "Block records with text, bbox, and lines"),
        (
            "import fitz\nnumbers = [b[5] for b in page.get_text('blocks')]\n",
            "Block records with text, bbox, and lines",
        ),
        ("import fitz\nfor w in page.get_text('words'):\n    print(w)\n", "Word records with text, bbox, block"),
        ("import fitz\nhtml = page.get_text('html')\n", "Page.get_text is Page.text in pylopdf"),
        ("import fitz\nhits = page.search_for('a', quads=True)\n", "Page.search_for is Page.search in pylopdf"),
        ("import fitz\ntables = page.find_tables(vertical_strategy='text')\n", "Page.find_tables is Page.tables"),
        ("import fitz\ntables = page.find_tables(strategy='explicit')\n", "Page.find_tables is Page.tables"),
        ("import fitz\nfor tab in page.find_tables():\n    frame = tab.to_pandas()\n", "come with the Understanding"),
        ("import fitz\ntab = page.find_tables()[0]\nnames = tab.header.names\n", "come with the Understanding"),
        ("import pylopdf\nwords = page.get_text_ocr(dpi=200)\n", "read word.text, word.bbox, and word.confidence"),
        ("import pylopdf\npage.insert_ocr_text_layer(words)\n", "page.apply_ocr(words=[OcrWord("),
        ("import pylopdf\nfinder = page.find_tables()\nregion = finder.clip\n", "keep the clip you passed"),
        ("import pylopdf\ntable = page.find_tables()[0]\nsource = table.page\n", "Keep the page whose tables()"),
        ("import fitz\ninfos = page.get_image_info(hashes=True)\n", "Page.get_image_info is Page.content"),
        ("import fitz\npaths = page.get_cdrawings(callback=print)\n", "Read page.content().paths"),
        ("import fitz\nimages = page.get_images()\n", "page.resources.images returns ImageObject handles"),
        ("import fitz\nimage = doc.extract_image(xref)\n", "doc.images[xref].extract() returns an EncodedImage"),
        ("import fitz\nmask = doc.extract_image(xref)['smask']\n", "returns an EncodedImage"),
        ("import fitz\nsvg = page.get_svg_image(text_as_path=False)\n", "Page.get_svg_image is Page.svg"),
        ("import fitz\nchannels = pix.n\n", "Pixmap.n is Pixmap.channels"),
        ("import fitz\ncs = fitz.csCMYK\n", "csCMYK"),
        ("import fitz\ndata = pix.tobytes('pnm')\n", "to_bytes"),
        ("import fitz\ndoc.set_metadata(values)\n", "doc.metadata is a live mapping"),
        ("import fitz\ndoc.set_metadata({'title': ''})\n", "del a key to remove it"),
        ("import fitz\ndoc.set_pagelayout(layout)\n", "Assign doc.page_layout"),
        ("import fitz\nflags = doc.markinfo\n", "MarkInfo record"),
        ("import fitz\ndoc.set_markinfo({'Marked': True})\n", "Assign doc.mark_info = MarkInfo("),
        ("import pylopdf\nclaim = doc.get_pdfa_claim()\n", "PdfaClaim record"),
        ("import pylopdf\nfrom pylopdf import DocumentMetadata\n", "DocumentMetadata is Metadata"),
        ("import fitz\nfor link in page.get_links():\n    print(link['uri'])\n", "read link.rect and link.target"),
        ("import pylopdf\nlinks = page.get_links()\n", "page.links lists Link handles"),
        ("import fitz\nfor link in page.links():\n    print(link.uri)\n", "in place of pymupdf's uri, dest"),
        ("import fitz\nuris = page.links(kinds=[fitz.LINK_URI])\n", "test the type of link.target"),
        ("import fitz\nlink = page.first_link\n", "in place of pymupdf's uri, dest"),
        ("import fitz\npage.insert_link(link_dict)\n", "page.links.add(rect, target)"),
        ("import fitz\npage.delete_link(link_dict)\n", "del page.links[index]"),
        ("import fitz\npage.update_link(link_dict)\n", "Assign link.rect and link.target"),
        ("import fitz\nif link['kind'] == fitz.LINK_GOTO:\n    pass\n", "isinstance(link.target, Destination)"),
        ("import pylopdf\nfrom pylopdf import LinkInfo\n", "page.links lists Link handles"),
        ("import fitz\nfor annot in page.annots():\n    print(annot.info)\n", "page.annotations lists Annotation"),
        (
            "import fitz\nnotes = page.annots(types=[fitz.PDF_ANNOT_TEXT])\n",
            "page.annotations lists Annotation handles",
        ),
        ("import fitz\nannot = page.first_annot\n", "Annotation handles have typed properties"),
        ("import fitz\nannot = page.add_highlight_annot(start=a, stop=b)\n", "page.annotations.add(Highlight("),
        ("import pylopdf\nannots = page.annots()\n", "links are in page.links"),
        ("import fitz\nfont = fitz.Font('cjk')\n", "pymupdf's other built-in fonts"),
        ("import fitz\nfont = fitz.Font(ordering=0)\n", "pymupdf's other built-in fonts"),
        ("import fitz\nfont = fitz.Font(language='ja')\n", "Font.installed(language=..., family=...)"),
        ("import pylopdf\nfont = pylopdf.Font(language='ja', allow_regular_fallback=True)\n", "Font.installed("),
        ("import pylopdf\nfont = pylopdf.Font(family='Noto Serif JP', is_serif=True)\n", "Font.installed("),
        ("import fitz\nfound = font.has_glyph(code)\n", "returns whether the face has a glyph for it"),
        ("import fitz\nwidth = font.text_length(text, wmode=1)\n", "Font.text_length is Font.text_width"),
        ("import fitz\nnames = fitz.Base14_fontnames\n", "typing.get_args(pylopdf.StandardFontName)"),
        ("import fitz\npage.draw_rect(r, radius=0.1)\n", "whose radius is in points"),
        ("import fitz\npage.insert_image(r, filename=f, mask=m)\n", "Page.insert_image is Canvas.image"),
        ("import fitz\npage.insert_image(r, xref=x)\n", "Page.insert_image is Canvas.image"),
        ("import fitz\npage.insert_image(r, filename=f, stream=s)\n", "Page.insert_image is Canvas.image"),
        ("import fitz\nxref = doc.new_page().insert_image(r, stream=d)\n", "Page.insert_image is Canvas.image"),
        ("import fitz\npage.show_pdf_page(r, src, oc=5)\n", "Page.show_pdf_page is Canvas.page"),
        ("import fitz\nform = page.show_pdf_page(r, src)\n", "Page.show_pdf_page is Canvas.page"),
        ("import fitz\npage.insert_text(p, t, render_mode=3)\n", "Page.insert_text is Canvas.text"),
        ("import fitz\npage.insert_text(p, t, fontname='china-s')\n", "Page.insert_text is Canvas.text"),
        ("import fitz\nlines = page.insert_text(p, t)\n", "Page.insert_text is Canvas.text"),
        ("import fitz\npage.insert_textbox(r, t, align=align)\n", "Page.insert_textbox is Canvas.textbox"),
        ("import fitz\npage.insert_textbox(r, t, morph=(p, m))\n", "Page.insert_textbox is Canvas.textbox"),
        ("import fitz\npage.draw_line(a, b, color=(0, 0, 0, 1))\n", "Page.draw_line is Canvas.line"),
        ("import fitz\nend = page.draw_line(a, b)\n", "Page.draw_line is Canvas.line"),
        ("import fitz\npage.draw_line(a, b, morph=(p, m))\n", "Page.draw_line is Canvas.line"),
        ("import fitz\npage.draw_quad(quad)\n", "canvas.polygon((quad.ul, quad.ur, quad.lr, quad.ll)"),
        ("from fitz import Rect\npage.draw_zigzag(a, b)\n", "PathBuilder().zigzag(p1, p2, breadth=2)"),
        ("import fitz\npage.draw_sector(c, p, 90)\n", "PathBuilder().sector(center, radius, start, sweep"),
        ("import fitz\npage.draw_curve(a, b, c)\n", "with the curve's control points"),
        ("import fitz\nshape = page.new_shape()\n", "Page.new_shape is Page.draw"),
        (
            "import fitz\nwith page.new_shape() as shape:\n    shape.draw_rect(r)\n    shape.finish(color=(1, 0, 0))\n",
            "Page.new_shape is Page.draw",
        ),
        ("import pylopdf\nshape = page.new_shape()\nshape.commit()\n", "Shape.commit is Canvas"),
        ("import fitz\ndef f(shape: fitz.Shape) -> None: ...\n", "page.draw() returns a Canvas"),
    ],
)
def test_codemod_marks_what_it_cannot_rewrite(migrate_source: Migrate, source: str, hint: str) -> None:
    result = migrate_source(source)
    assert any(hint in message for _, message in result.todos), result.code


@pytest.mark.parametrize(
    ("source", "expected", "hint"),
    [
        (
            "import fitz\npaths = page.get_drawings()\n",
            "paths = page.content(text_runs=False, images=False).paths",
            "returns Path records",
        ),
        (
            "import fitz\npaths = page.get_cdrawings(extended=None)\n",
            "paths = page.content(text_runs=False, images=False).paths",
            "returns Path records",
        ),
        (
            "import fitz\nitems = page.get_drawings(extended=True)\n",
            "items = page.content(text_runs=False, images=False).paths",
            "which extended=True listed",
        ),
        (
            "import fitz\ninfos = page.get_image_info(xrefs=True)\n",
            "infos = page.content(text_runs=False, paths=False).images",
            "returns Image records",
        ),
        (
            "import pylopdf\npaths = page.get_drawings()\n",
            "paths = page.content(text_runs=False, images=False).paths",
            "returns Path records",
        ),
    ],
)
def test_codemod_moves_drawings_and_image_info_to_the_content_model(
    migrate_source: Migrate, source: str, expected: str, hint: str
) -> None:
    result = migrate_source(source)
    assert expected in result.code
    assert any(hint in message for _, message in result.todos), result.code


def test_codemod_places_hints_inside_compound_statements(migrate_source: Migrate) -> None:
    source = "import fitz\n\ndef run(page):\n    if page.rotation_matrix:\n        page.write_text()\n"

    code = migrate_source(source).code

    assert code.splitlines()[3].lstrip().startswith("# TODO(pylopdf): pylopdf extracts and inserts")
    planned = code.splitlines()[5].lstrip()
    assert planned.startswith("# TODO(pylopdf): pylopdf plans the capability of pymupdf's Page.write_text")


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
