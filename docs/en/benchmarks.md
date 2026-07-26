---
title: Benchmarks
description: Reproducible pylopdf benchmarks for extraction, merging and rendering, with wins and losses published together.
---

# Benchmarks

pylopdf publishes **wins and losses together**. These measurements are a
snapshot of one machine and corpus—not a universal ranking. Use them to choose
what to measure in your own workload.

!!! info "Latest run"
    **2026-07-26 08:18 UTC** · Windows 11 · Python 3.14.6 · AMD64<br>
    pylopdf 0.11.0 · pymupdf 1.28.0 · pypdf 6.14.2 · pdfplumber 0.11.10<br>
    One warm-up plus five measured runs; tables show median milliseconds.

## At a glance { #overview }

| Workload | What the latest corpus shows |
|---|---|
| Merge 10 real-world PDFs | pylopdf **36.6 ms**, pymupdf 131.8 ms, pypdf 426.3 ms |
| Render first page at 2× | pylopdf led on all ten corpus files |
| Render 12 pages at 2× | `render_pages()` scaled from 317.4 ms (1 worker) to 81.5 ms (8 workers), a **3.89× speedup** |
| Extract all text | pylopdf led on four files; pymupdf led on six |
| Extraction fidelity proxy | Similarity ranged from 0.121 to 1.000 depending on reading-order conventions |

## Text extraction { #text-extraction }

All pages, milliseconds; lower is faster.

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | 191.6 | **183.2** | 689.7 | 9997.1 |
| bunka-kokugo-series-019-p4.pdf | 1.9 | **0.5** | 1.0 | 1.8 |
| f1040.pdf | **26.7** | 66.9 | 230.3 | 704.7 |
| mhlw-doc.pdf | 18.4 | **11.5** | 114.3 | 263.3 |
| nics-background-checks-2015-11.pdf | 16.1 | **10.8** | 177.5 | 524.7 |
| patent-us223898.pdf | 33.3 | **6.3** | 79.2 | 493.7 |
| pdf20-simple.pdf | **0.3** | 1.2 | 1.7 | 2.4 |
| senate-expenditures.pdf | **6.6** | 7.2 | 132.2 | 374.1 |
| usrguide.pdf | 163.0 | **54.5** | 673.6 | 2050.7 |
| wdl6812-manuscript.pdf | **0.3** | 0.8 | 1.3 | 2.3 |

## Extraction content { #extraction-content }

This is a proxy, not a correctness score. Text is whitespace-normalized and
compared with pymupdf. Lower similarity for forms and OCR layers can reflect a
different reading order or whitespace policy even when character counts match.

| File | pylopdf characters | pymupdf characters | Similarity |
|---|---:|---:|---:|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| bunka-kokugo-series-019-p4.pdf | 0 | 0 | 1.000 |
| f1040.pdf | 10156 | 10156 | 0.683 |
| mhlw-doc.pdf | 1264 | 1251 | 0.961 |
| nics-background-checks-2015-11.pdf | 5650 | 5650 | 0.121 |
| patent-us223898.pdf | 11218 | 11218 | 0.320 |
| pdf20-simple.pdf | 11 | 11 | 1.000 |
| senate-expenditures.pdf | 4516 | 4516 | 0.443 |
| usrguide.pdf | 55624 | 55560 | 0.996 |
| wdl6812-manuscript.pdf | 0 | 0 | 1.000 |

## Merge { #merge }

| Task | pylopdf | pymupdf | pypdf |
|---|---:|---:|---:|
| Merge all 10 corpus files | **36.6** | 131.8 | 426.3 |

## Rendering { #rendering }

First page to a 2× PNG, milliseconds; lower is faster.

| File | pylopdf | pymupdf |
|---|---:|---:|
| bill-hr815.pdf | **41.1** | 88.9 |
| bunka-kokugo-series-019-p4.pdf | **48.2** | 110.5 |
| f1040.pdf | **49.5** | 97.1 |
| mhlw-doc.pdf | **35.5** | 71.2 |
| nics-background-checks-2015-11.pdf | **54.2** | 72.6 |
| patent-us223898.pdf | **32.3** | 68.8 |
| pdf20-simple.pdf | **8.0** | 19.9 |
| senate-expenditures.pdf | **55.2** | 56.8 |
| usrguide.pdf | **28.3** | 54.9 |
| wdl6812-manuscript.pdf | **42.9** | 83.3 |

## Parallel rendering { #parallel-rendering }

First 12 pages of `usrguide.pdf` to 2× PNG, milliseconds; lower is faster.
The batch preserves input order and uses one immutable document snapshot.

| Workers | Time | Speedup vs 1 worker |
|---:|---:|---:|
| 1 | 317.4 | 1.00× |
| 2 | 179.6 | 1.77× |
| 4 | 99.1 | 3.20× |
| 8 | 81.5 | 3.89× |

Actual concurrency is bounded by both the requested worker count and an
estimated 512 MB of live rendering memory.

## Free-threaded extraction { #free-threaded-extraction }

Two independent copies of `bill-hr815.pdf`, all-page text extraction on
CPython 3.14.6 free-threaded for Windows 11. One warmup plus the median of seven
paired runs, alternating which mode runs first:

| Mode | Workers | Time | Speedup |
|---|---:|---:|---:|
| Sequential | 1 | 400.8 ms | 1.00× |
| Parallel | 2 | 235.5 ms | 1.70× |

Both copies produced exactly the same output in every run, and the interpreter
reported that the GIL remained disabled after import.

## Reproduce it { #reproduce }

The corpus lives in `tests/assets/real_world`; its sources and licenses are
recorded alongside the files.

```bash
uv sync --all-extras --group bench
uv run python bench/run.py
uv run python tools/pyodide_compat.py --root . --benchmark-only \
  --benchmark-output .tmp/limits-benchmark.json
# With a free-threaded CPython 3.14 interpreter on Windows:
py -3.14t bench/free_threaded.py
# On POSIX:
python3.14t bench/free_threaded.py
```

The generated source report is committed at
[`bench/results/latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md).
The separately generated free-threaded report is committed at
[`bench/results/free-threaded-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/free-threaded-latest.md);
rerunning `bench/run.py` cannot overwrite its CPython 3.14t evidence.
The native/Pyodide resource-policy baseline is committed separately at
[`bench/results/limits-latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/limits-latest.md).
The second command measures bounded open/extract and controlled rejection.
CI runs the same cases inside Pyodide and records Wasm linear-memory growth;
those timing and memory values are trends, not native/Wasm performance claims.
When quoting a number, include the environment and corpus.
