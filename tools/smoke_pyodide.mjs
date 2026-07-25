#!/usr/bin/env node
// Install a local wheel into Pyodide and verify bytes-based PDF extraction.

import { readFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

function fail(message) {
  process.stderr.write(`error: ${message}\n`);
  process.exit(1);
}

const [, , runtimeDirectoryArgument, wheelArgument, pdfArgument] = process.argv;
if (!runtimeDirectoryArgument || !wheelArgument || !pdfArgument) {
  fail("usage: smoke_pyodide.mjs RUNTIME_DIRECTORY WHEEL PDF");
}

const runtimeDirectory = path.resolve(runtimeDirectoryArgument);
const wheel = path.resolve(wheelArgument);
const pdf = path.resolve(pdfArgument);
const runtimeModule = pathToFileURL(path.join(runtimeDirectory, "pyodide.mjs")).href;
const { loadPyodide } = await import(runtimeModule);
const pyodide = await loadPyodide({
  indexURL: `${runtimeDirectory}${path.sep}`,
});

const wheelName = path.basename(wheel);
pyodide.FS.writeFile(`/tmp/${wheelName}`, await readFile(wheel));
pyodide.FS.writeFile("/tmp/pylopdf-smoke.pdf", await readFile(pdf));
await pyodide.loadPackage(
  "https://cdn.jsdelivr.net/pyodide/v0.28.3/full/micropip-0.10.1-py3-none-any.whl",
);

const result = await pyodide.runPythonAsync(`
from pathlib import Path

import micropip

await micropip.install("emfs:/tmp/${wheelName}", deps=False)

import pylopdf

source_bytes = Path("/tmp/pylopdf-smoke.pdf").read_bytes()
document = pylopdf.Document(stream=source_bytes)
if document.page_count != 1:
    raise RuntimeError(f"expected one page, found {document.page_count}")
text = document.get_page_text(0)
if "Hello World" not in text:
    raise RuntimeError(f"expected Hello World in extracted text, found {text!r}")
rendered_pages = document.render_pages(workers=4)
if len(rendered_pages) != 1 or not rendered_pages[0].startswith(b"\\x89PNG\\r\\n\\x1a\\n"):
    raise RuntimeError("render_pages did not return one PNG image")

try:
    pylopdf.Document(stream=b"not a PDF")
except pylopdf.PdfError:
    pass
else:
    raise RuntimeError("malformed bytes did not raise PdfError")

second_document = pylopdf.Document(stream=source_bytes)
if second_document.page_count != 1:
    raise RuntimeError("the runtime did not survive the malformed-input error")

f"pylopdf {pylopdf.__version__}: pages={document.page_count}, text={text.strip()!r}"
`);

process.stdout.write(`Pyodide smoke test passed: ${result}\n`);
result.destroy?.();
