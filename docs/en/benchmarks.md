---
title: Benchmarks
description: Reproducible pylopdf benchmarks for extraction, merging and rendering, with wins and losses published together.
---

# Benchmarks

pylopdf publishes **wins and losses together**. These measurements are a
snapshot of one machine and corpus—not a universal ranking. Use them to choose
what to measure in your own workload.

!!! info "Latest run"
    **2026-07-25 12:00 UTC** · Windows 11 · Python 3.14.6 · AMD64<br>
    pylopdf 0.10.0 · pymupdf 1.28.0 · pypdf 6.14.2 · pdfplumber 0.11.10<br>
    One warm-up plus five measured runs; tables show median milliseconds.

## At a glance { #overview }

| Workload | What the latest corpus shows |
|---|---|
| Merge 9 real-world PDFs | pylopdf **42.4 ms**, pymupdf 131.5 ms, pypdf 452.6 ms |
| Render first page at 2× | pylopdf led on all nine corpus files |
| Render 12 pages at 2× | `render_pages()` scaled from 386.6 ms (1 worker) to 86.8 ms (8 workers), a **4.46× speedup** |
| Extract all text | pylopdf led on five files; pymupdf led on four |
| Extraction fidelity proxy | Similarity ranged from 0.121 to 1.000 depending on reading-order conventions |

## Text extraction { #text-extraction }

All pages, milliseconds; lower is faster.

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **160.5** | 162.9 | 638.3 | 8842.6 |
| f1040.pdf | **16.1** | 33.4 | 155.8 | 499.3 |
| mhlw-doc.pdf | 14.6 | **10.0** | 80.7 | 173.2 |
| nics-background-checks-2015-11.pdf | 9.3 | **6.1** | 113.5 | 285.7 |
| patent-us223898.pdf | 32.5 | **6.9** | 76.4 | 394.5 |
| pdf20-simple.pdf | **0.2** | 0.7 | 1.2 | 1.8 |
| senate-expenditures.pdf | **4.8** | 6.2 | 110.8 | 282.1 |
| usrguide.pdf | 117.2 | **42.1** | 583.7 | 1667.3 |
| wdl6812-manuscript.pdf | **0.3** | 0.8 | 1.4 | 2.4 |

## Extraction content { #extraction-content }

This is a proxy, not a correctness score. Text is whitespace-normalized and
compared with pymupdf. Lower similarity for forms and OCR layers can reflect a
different reading order or whitespace policy even when character counts match.

| File | pylopdf characters | pymupdf characters | Similarity |
|---|---:|---:|---:|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| f1040.pdf | 10156 | 10156 | 0.680 |
| mhlw-doc.pdf | 1264 | 1251 | 0.961 |
| nics-background-checks-2015-11.pdf | 5650 | 5650 | 0.121 |
| patent-us223898.pdf | 11207 | 11218 | 0.292 |
| pdf20-simple.pdf | 11 | 11 | 1.000 |
| senate-expenditures.pdf | 4516 | 4516 | 0.443 |
| usrguide.pdf | 55624 | 55560 | 0.996 |
| wdl6812-manuscript.pdf | 0 | 0 | 1.000 |

## Merge { #merge }

| Task | pylopdf | pymupdf | pypdf |
|---|---:|---:|---:|
| Merge all 9 corpus files | **42.4** | 131.5 | 452.6 |

## Rendering { #rendering }

First page to a 2× PNG, milliseconds; lower is faster.

| File | pylopdf | pymupdf |
|---|---:|---:|
| bill-hr815.pdf | **42.9** | 117.0 |
| f1040.pdf | **64.9** | 129.2 |
| mhlw-doc.pdf | **46.0** | 85.2 |
| nics-background-checks-2015-11.pdf | **75.4** | 95.8 |
| patent-us223898.pdf | **45.0** | 71.6 |
| pdf20-simple.pdf | **11.0** | 21.9 |
| senate-expenditures.pdf | **61.8** | 65.9 |
| usrguide.pdf | **37.1** | 64.4 |
| wdl6812-manuscript.pdf | **52.3** | 109.8 |

## Parallel rendering { #parallel-rendering }

First 12 pages of `usrguide.pdf` to 2× PNG, milliseconds; lower is faster.
The batch preserves input order and uses one immutable document snapshot.

| Workers | Time | Speedup vs 1 worker |
|---:|---:|---:|
| 1 | 386.6 | 1.00× |
| 2 | 194.0 | 1.99× |
| 4 | 109.7 | 3.52× |
| 8 | 86.8 | 4.46× |

Actual concurrency is bounded by both the requested worker count and an
estimated 512 MB of live rendering memory.

## Free-threaded extraction { #free-threaded-extraction }

Two independent copies of `bill-hr815.pdf`, all-page text extraction on
CPython 3.14.6 free-threaded for Windows 11. One warmup plus the median of seven
paired runs, alternating which mode runs first:

| Mode | Workers | Time | Speedup |
|---|---:|---:|---:|
| Sequential | 1 | 280.3 ms | 1.00× |
| Parallel | 2 | 160.8 ms | 1.74× |

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
# With a free-threaded CPython 3.14 interpreter:
python3.14t bench/free_threaded.py
```

The generated source report is committed at
[`bench/results/latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md).
The second command measures bounded open/extract and controlled rejection.
CI runs the same cases inside Pyodide and records Wasm linear-memory growth;
those timing and memory values are trends, not native/Wasm performance claims.
When quoting a number, include the environment and corpus.
