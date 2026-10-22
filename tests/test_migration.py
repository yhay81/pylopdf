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
        (pylopdf.Document().pages.new, "get_textpage", "Page.get_textpage in the Reading milestone"),
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
            "doc.save(path, encryption=f.Encryption(permissions=f.PDF_PERM_PRINT))",
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
    ],
)
def test_codemod_rules(migrate_source: Migrate, source: str, expected: str) -> None:
    result = migrate_source(source)
    assert expected in result.code
    assert result.todos == ()


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
        ("import pylopdf\ndoc = pylopdf.open(path)\ndoc.set_fallback_font(None)\n", "doc.fallback_fonts = {}"),
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
