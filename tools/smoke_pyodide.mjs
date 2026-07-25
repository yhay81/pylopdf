#!/usr/bin/env node
// Install a local wheel into Pyodide and run the shared compatibility suite.

import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { performance } from "node:perf_hooks";
import process from "node:process";
import { pathToFileURL } from "node:url";

function fail(message) {
  process.stderr.write(`error: ${message}\n`);
  process.exit(1);
}

function elapsed(start) {
  return Math.round((performance.now() - start) * 1000) / 1000;
}

function median(values) {
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.floor(sorted.length / 2)];
}

function wasmBytes(pyodide) {
  return pyodide._module?.HEAP8?.buffer?.byteLength ?? null;
}

function nodeRssBytes() {
  return process.memoryUsage().rss;
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
const timings = {};
const memory = {};
const importStart = performance.now();
const { loadPyodide } = await import(runtimeModule);
timings.import_pyodide_js = elapsed(importStart);
const runtimeStart = performance.now();
const pyodide = await loadPyodide({
  indexURL: `${runtimeDirectory}${path.sep}`,
});
timings.load_pyodide_runtime = elapsed(runtimeStart);
memory.after_runtime = {
  wasm_linear_bytes: wasmBytes(pyodide),
  node_rss_bytes: nodeRssBytes(),
};

const wheelName = path.basename(wheel);
const wheelData = await readFile(wheel);
pyodide.FS.writeFile(`/tmp/${wheelName}`, wheelData);
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
const micropipStart = performance.now();
await pyodide.loadPackage(
  "https://cdn.jsdelivr.net/pyodide/v0.28.3/full/micropip-0.10.1-py3-none-any.whl",
);
timings.load_micropip = elapsed(micropipStart);

const installStart = performance.now();
await pyodide.runPythonAsync(`
from pathlib import Path
import sys

import micropip

await micropip.install("emfs:/tmp/${wheelName}", deps=False)
`);
timings.install_wheel = elapsed(installStart);
memory.after_install = {
  wasm_linear_bytes: wasmBytes(pyodide),
  node_rss_bytes: nodeRssBytes(),
};

const pylopdfImportStart = performance.now();
await pyodide.runPythonAsync("import pylopdf");
timings.import_pylopdf = elapsed(pylopdfImportStart);
memory.after_import = {
  wasm_linear_bytes: wasmBytes(pyodide),
  node_rss_bytes: nodeRssBytes(),
};

const installedSizes = JSON.parse(
  await pyodide.runPythonAsync(`
import json
import platform
from pathlib import Path

package_root = Path(pylopdf.__file__).parent
extension = next(package_root.glob("pylopdf_core*.so"))
json.dumps({
    "python": platform.python_version(),
    "extension_bytes": extension.stat().st_size,
    "package_bytes": sum(path.stat().st_size for path in package_root.rglob("*") if path.is_file()),
    "package_files": sum(1 for path in package_root.rglob("*") if path.is_file()),
})
`),
);
const { python: pythonVersion, ...installedArtifactSizes } = installedSizes;

await pyodide.runPythonAsync(`
from pathlib import Path

_pylopdf_measurement_data = Path(
    "${path.posix.join(compatibilityRoot, "tests/assets/real_world/f1040.pdf")}"
).read_bytes()

def _pylopdf_process_measurement():
    with pylopdf.Document(
        stream=_pylopdf_measurement_data,
        limits=pylopdf.DocumentLimits.web(),
    ) as document:
        return len(document.get_page_text(0))
`);
const processFixture = pyodide.globals.get("_pylopdf_process_measurement");
const firstDocumentStart = performance.now();
const firstTextLength = processFixture();
timings.first_document_open_and_extract = elapsed(firstDocumentStart);
if (firstTextLength <= 0) {
  fail("first measured document produced no text");
}
memory.after_first_document = {
  wasm_linear_bytes: wasmBytes(pyodide),
  node_rss_bytes: nodeRssBytes(),
};
const repeatedDocumentTimings = [];
for (let index = 0; index < 5; index += 1) {
  const repeatedStart = performance.now();
  const textLength = processFixture();
  repeatedDocumentTimings.push(elapsed(repeatedStart));
  if (textLength !== firstTextLength) {
    fail("repeated measured document changed extracted text length");
  }
}
timings.repeated_document_open_and_extract_median = median(
  repeatedDocumentTimings,
);
memory.after_repeated_documents = {
  wasm_linear_bytes: wasmBytes(pyodide),
  node_rss_bytes: nodeRssBytes(),
};

const result = await pyodide.runPythonAsync(`
from pathlib import Path
import sys

sys.path.insert(0, "/tmp")
from pyodide_compat import run_suite

run_suite(
    Path("${compatibilityRoot}"),
    ${baselinePath === null ? "None" : `Path("${baselinePath}")`},
)
`);
memory.after_compatibility_suite = {
  wasm_linear_bytes: wasmBytes(pyodide),
  node_rss_bytes: nodeRssBytes(),
};

const parsed = JSON.parse(result);
const ocrBoundary = await pyodide.runPythonAsync(`
import pylopdf

try:
    pylopdf.OcrEngine("/tmp/detector.rten", "/tmp/recognizer.rten", "/tmp/dictionary.txt")
except pylopdf.OcrError as error:
    message = str(error)
    if "unavailable on Emscripten" not in message:
        raise RuntimeError(f"unexpected Emscripten OCR error: {message}")
    message
else:
    raise RuntimeError("native OCR unexpectedly initialized on Emscripten")
message
`);
const wasmBytesBeforeBenchmark =
  pyodide._module?.HEAP8?.buffer?.byteLength ?? null;
const benchmark = await pyodide.runPythonAsync(`
import json
from pathlib import Path

from pyodide_compat import benchmark_limits

json.dumps(
    benchmark_limits(Path("${compatibilityRoot}")),
    sort_keys=True,
    separators=(",", ":"),
)
`);
const wasmBytesAfterBenchmark =
  pyodide._module?.HEAP8?.buffer?.byteLength ?? null;
memory.after_limit_benchmark = {
  wasm_linear_bytes: wasmBytesAfterBenchmark,
  node_rss_bytes: nodeRssBytes(),
};
const benchmarkRecord = {
  ...JSON.parse(benchmark),
  wasm_linear_memory_bytes: {
    before: wasmBytesBeforeBenchmark,
    after: wasmBytesAfterBenchmark,
  },
};
const wasmCheckpoints = Object.values(memory)
  .map((checkpoint) => checkpoint.wasm_linear_bytes)
  .filter((value) => value !== null);
const nodeRssCheckpoints = Object.values(memory).map(
  (checkpoint) => checkpoint.node_rss_bytes,
);
const runtimeMetrics = {
  schema: 1,
  runtime: {
    node: process.version,
    pyodide: "0.28.3",
    python: pythonVersion,
    pylopdf: parsed.pylopdf_version,
  },
  artifact: {
    wheel_bytes: (await stat(wheel)).size,
    ...installedArtifactSizes,
  },
  timing_ms: {
    ...timings,
    repeated_document_open_and_extract_runs: repeatedDocumentTimings,
  },
  memory_checkpoints: memory,
  wasm_linear_memory_high_water_bytes: Math.max(...wasmCheckpoints),
  node_rss_checkpoint_high_water_bytes: Math.max(...nodeRssCheckpoints),
  measured_text_length: firstTextLength,
};
const benchmarkOutput = process.env.PYLOPDF_PYODIDE_BENCHMARK_OUTPUT;
if (benchmarkOutput) {
  const resolvedOutput = path.resolve(benchmarkOutput);
  await mkdir(path.dirname(resolvedOutput), { recursive: true });
  await writeFile(
    resolvedOutput,
    `${JSON.stringify(benchmarkRecord)}\n`,
    "utf8",
  );
}
const metricsOutput = process.env.PYLOPDF_PYODIDE_METRICS_OUTPUT;
if (metricsOutput) {
  const resolvedOutput = path.resolve(metricsOutput);
  await mkdir(path.dirname(resolvedOutput), { recursive: true });
  await writeFile(
    resolvedOutput,
    `${JSON.stringify(runtimeMetrics)}\n`,
    "utf8",
  );
}
process.stdout.write(
  `Pyodide compatibility suite passed: pylopdf ${parsed.pylopdf_version}, schema ${parsed.schema}\n` +
    `Pyodide OCR boundary passed: ${ocrBoundary}\n` +
    `Pyodide size/startup metrics: ${JSON.stringify(runtimeMetrics)}\n` +
    `Pyodide limit benchmark: ${JSON.stringify(benchmarkRecord)}\n` +
    `Pyodide linear memory bytes: before=${wasmBytesBeforeBenchmark}, after=${wasmBytesAfterBenchmark}\n`,
);
result?.destroy?.();
benchmark?.destroy?.();
ocrBoundary?.destroy?.();
processFixture?.destroy?.();
