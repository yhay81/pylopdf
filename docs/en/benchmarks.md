---
title: Benchmarks
description: Reproducible pylopdf benchmarks for extraction, merging and rendering, with wins and losses published together.
---

# Benchmarks

pylopdf publishes **wins and losses together**. These measurements are a
snapshot of one machine and corpus—not a universal ranking. Use them to choose
what to measure in your own workload.

!!! info "Latest run"
    **2026-07-23 04:47 UTC** · Windows 11 · Python 3.14.6 · AMD64<br>
    pylopdf 0.9.0 · pymupdf 1.28.0 · pypdf 6.14.2 · pdfplumber 0.11.10<br>
    One warm-up plus five measured runs; tables show median milliseconds.

## At a glance { #overview }

| Workload | What the latest corpus shows |
|---|---|
| Merge 7 real-world PDFs | pylopdf **30.1 ms**, pymupdf 122.2 ms, pypdf 325.3 ms |
| Render first page at 2× | pylopdf led on all seven corpus files |
| Extract all text | pylopdf led on four files; pymupdf led on three |
| Extraction fidelity proxy | Similarity ranged from 0.292 to 1.000 depending on reading-order conventions |

## Text extraction { #text-extraction }

All pages, milliseconds; lower is faster.

| File | pylopdf | pymupdf | pypdf | pdfplumber |
|---|---:|---:|---:|---:|
| bill-hr815.pdf | **131.6** | 150.7 | 631.4 | 8652.7 |
| f1040.pdf | **16.0** | 32.9 | 155.6 | 506.2 |
| mhlw-doc.pdf | 11.8 | **10.3** | 84.2 | 175.7 |
| patent-us223898.pdf | 26.3 | **6.0** | 83.4 | 390.2 |
| pdf20-simple.pdf | **0.3** | 0.8 | 1.2 | 1.9 |
| usrguide.pdf | 108.2 | **42.7** | 579.3 | 1673.5 |
| wdl6812-manuscript.pdf | **0.4** | 1.0 | 1.4 | 2.6 |

## Extraction content { #extraction-content }

This is a proxy, not a correctness score. Text is whitespace-normalized and
compared with pymupdf. Lower similarity for forms and OCR layers can reflect a
different reading order or whitespace policy even when character counts match.

| File | pylopdf characters | pymupdf characters | Similarity |
|---|---:|---:|---:|
| bill-hr815.pdf | 300559 | 300559 | 1.000 |
| f1040.pdf | 10158 | 10156 | 0.680 |
| mhlw-doc.pdf | 1264 | 1251 | 0.961 |
| patent-us223898.pdf | 11207 | 11218 | 0.292 |
| pdf20-simple.pdf | 11 | 11 | 1.000 |
| usrguide.pdf | 55624 | 55560 | 0.996 |
| wdl6812-manuscript.pdf | 0 | 0 | 1.000 |

## Merge { #merge }

| Task | pylopdf | pymupdf | pypdf |
|---|---:|---:|---:|
| Merge all 7 corpus files | **30.1** | 122.2 | 325.3 |

## Rendering { #rendering }

First page to a 2× PNG, milliseconds; lower is faster.

| File | pylopdf | pymupdf |
|---|---:|---:|
| bill-hr815.pdf | **40.8** | 84.0 |
| f1040.pdf | **49.9** | 92.1 |
| mhlw-doc.pdf | **33.8** | 68.7 |
| patent-us223898.pdf | **34.7** | 64.1 |
| pdf20-simple.pdf | **9.0** | 18.9 |
| usrguide.pdf | **30.7** | 54.6 |
| wdl6812-manuscript.pdf | **43.4** | 83.8 |

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
