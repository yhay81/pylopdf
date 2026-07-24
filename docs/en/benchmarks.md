---
title: Benchmarks
description: Reproducible pylopdf benchmarks for extraction, merging and rendering, with wins and losses published together.
---

# Benchmarks

pylopdf publishes **wins and losses together**. These measurements are a
snapshot of one machine and corpus—not a universal ranking. Use them to choose
what to measure in your own workload.

!!! info "Latest run"
    **2026-07-24 12:59 UTC** · Windows 11 · Python 3.14.6 · AMD64<br>
    pylopdf 0.9.0 · pymupdf 1.28.0 · pypdf 6.14.2 · pdfplumber 0.11.10<br>
    One warm-up plus five measured runs; tables show median milliseconds.

## At a glance { #overview }

| Workload | What the latest corpus shows |
|---|---|
| Merge 7 real-world PDFs | pylopdf **40.6 ms**, pymupdf 127.1 ms, pypdf 366.3 ms |
| Render first page at 2× | pylopdf led on all seven corpus files |
| Render 12 pages at 2× | `render_pages()` scaled from 400.8 ms (1 worker) to 83.6 ms (8 workers), a **4.80× speedup** |
| Extract all text | pylopdf led on four files; pymupdf led on three |
| Extraction fidelity proxy | Similarity ranged from 0.292 to 1.000 depending on reading-order conventions |

## Text extraction { #text-extraction }

All pages, milliseconds; lower is faster.

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **138.1** | 179.5 | 848.4 | 9850.9 |
| f1040.pdf | **16.8** | 33.4 | 176.3 | 572.9 |
| mhlw-doc.pdf | 18.4 | **11.3** | 109.3 | 195.3 |
| patent-us223898.pdf | 29.5 | **6.8** | 81.4 | 512.9 |
| pdf20-simple.pdf | **0.3** | 1.1 | 1.8 | 2.2 |
| usrguide.pdf | 144.9 | **50.7** | 665.2 | 1756.4 |
| wdl6812-manuscript.pdf | **0.3** | 0.7 | 1.4 | 2.2 |

## Extraction content { #extraction-content }

This is a proxy, not a correctness score. Text is whitespace-normalized and
compared with pymupdf. Lower similarity for forms and OCR layers can reflect a
different reading order or whitespace policy even when character counts match.

| File | pylopdf characters | pymupdf characters | Similarity |
|---|---:|---:|---:|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| f1040.pdf | 10156 | 10156 | 0.680 |
| mhlw-doc.pdf | 1264 | 1251 | 0.961 |
| patent-us223898.pdf | 11207 | 11218 | 0.292 |
| pdf20-simple.pdf | 11 | 11 | 1.000 |
| usrguide.pdf | 55624 | 55560 | 0.996 |
| wdl6812-manuscript.pdf | 0 | 0 | 1.000 |

## Merge { #merge }

| Task | pylopdf | pymupdf | pypdf |
|---|---:|---:|---:|
| Merge all 7 corpus files | **40.6** | 127.1 | 366.3 |

## Rendering { #rendering }

First page to a 2× PNG, milliseconds; lower is faster.

| File | pylopdf | pymupdf |
|---|---:|---:|
| bill-hr815.pdf | **38.2** | 86.2 |
| f1040.pdf | **53.1** | 94.7 |
| mhlw-doc.pdf | **35.7** | 70.1 |
| patent-us223898.pdf | **36.4** | 69.0 |
| pdf20-simple.pdf | **9.0** | 18.9 |
| usrguide.pdf | **31.8** | 56.6 |
| wdl6812-manuscript.pdf | **45.5** | 87.0 |

## Parallel rendering { #parallel-rendering }

First 12 pages of `usrguide.pdf` to 2× PNG, milliseconds; lower is faster.
The batch preserves input order and uses one immutable document snapshot.

| Workers | Time | Speedup vs 1 worker |
|---:|---:|---:|
| 1 | 400.8 | 1.00× |
| 2 | 200.5 | 2.00× |
| 4 | 118.5 | 3.38× |
| 8 | 83.6 | 4.80× |

Actual concurrency is bounded by both the requested worker count and an
estimated 512 MB of live rendering memory.

## Reproduce it { #reproduce }

The corpus lives in `tests/assets/real_world`; its sources and licenses are
recorded alongside the files.

```bash
uv sync --all-extras --group bench
uv run python bench/run.py
```

The generated source report is committed at
[`bench/results/latest.md`](https://github.com/yhay81/pylopdf/blob/main/bench/results/latest.md).
When quoting a number, include the environment and corpus.
