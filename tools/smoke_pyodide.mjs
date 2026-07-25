#!/usr/bin/env node
// Install a local wheel into Pyodide and run the shared compatibility suite.

import { readFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

function fail(message) {
  process.stderr.write(`error: ${message}\n`);
  process.exit(1);
}

const [
  ,
  ,
  runtimeDirectoryArgument,
  wheelArgument,
  repositoryRootArgument,
  assetListArgument,
  nativeBaselineArgument,
] = process.argv;
if (
  !runtimeDirectoryArgument ||
  !wheelArgument ||
  !repositoryRootArgument ||
  !assetListArgument
) {
  fail(
    "usage: smoke_pyodide.mjs RUNTIME_DIRECTORY WHEEL REPOSITORY_ROOT ASSET_LIST_JSON [NATIVE_BASELINE]",
  );
}

const runtimeDirectory = path.resolve(runtimeDirectoryArgument);
const wheel = path.resolve(wheelArgument);
const repositoryRoot = path.resolve(repositoryRootArgument);
const assets = JSON.parse(assetListArgument);
if (!Array.isArray(assets) || !assets.every((item) => typeof item === "string")) {
  fail("asset list must be a JSON array of paths");
}
const runtimeModule = pathToFileURL(path.join(runtimeDirectory, "pyodide.mjs")).href;
const { loadPyodide } = await import(runtimeModule);
const pyodide = await loadPyodide({
  indexURL: `${runtimeDirectory}${path.sep}`,
});

const wheelName = path.basename(wheel);
pyodide.FS.writeFile(`/tmp/${wheelName}`, await readFile(wheel));
pyodide.FS.writeFile(
  "/tmp/pyodide_compat.py",
  await readFile(path.join(repositoryRoot, "tools", "pyodide_compat.py")),
);
const compatibilityRoot = "/tmp/pylopdf-compat";
for (const relativePath of assets) {
  const destination = path.posix.join(compatibilityRoot, ...relativePath.split(/[\\/]/));
  pyodide.FS.mkdirTree(path.posix.dirname(destination));
  pyodide.FS.writeFile(
    destination,
    await readFile(path.join(repositoryRoot, relativePath)),
  );
}
let baselinePath = null;
if (nativeBaselineArgument) {
  baselinePath = "/tmp/native-compat.json";
  pyodide.FS.writeFile(
    baselinePath,
    await readFile(path.resolve(nativeBaselineArgument)),
  );
}
await pyodide.loadPackage(
  "https://cdn.jsdelivr.net/pyodide/v0.28.3/full/micropip-0.10.1-py3-none-any.whl",
);

const result = await pyodide.runPythonAsync(`
from pathlib import Path
import sys

import micropip

await micropip.install("emfs:/tmp/${wheelName}", deps=False)

sys.path.insert(0, "/tmp")
from pyodide_compat import run_suite

run_suite(
    Path("${compatibilityRoot}"),
    ${baselinePath === null ? "None" : `Path("${baselinePath}")`},
)
`);

const parsed = JSON.parse(result);
process.stdout.write(
  `Pyodide compatibility suite passed: pylopdf ${parsed.pylopdf_version}, schema ${parsed.schema}\n`,
);
result.destroy?.();
