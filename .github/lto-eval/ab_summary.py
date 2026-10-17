"""Summarize interleaved A/B rounds: median of per-round medians per variant.

Usage: python ab_summary.py RESULTS_DIR [baseline thin fat]

RESULTS_DIR contains <variant>.jsonl files with one ab.py JSON object per round.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

METRICS = [
    ("extract_total", "Text extraction, corpus total"),
    ("markdown_total", "to_markdown, corpus total"),
    ("merge", "Merge x14 + tobytes"),
    ("merge_open_all", "Merge phase: open 14 inputs"),
    ("merge_import_all", "Merge phase: open + insert_pdf"),
    ("merge_tobytes", "Merge phase: tobytes of merged"),
    ("render_end_to_end_total", "Render 2x PNG end to end, total"),
    ("render_cold_total", "Raster 2x cold, total"),
    ("render_warm_total", "Raster 2x warm, total"),
    ("render_encode_total", "PNG encode, total"),
    ("batch_w1", "render_pages 12 pages, 1 worker"),
    ("batch_w4", "render_pages 12 pages, 4 workers"),
]


def main() -> None:
    root = Path(sys.argv[1])
    variants = sys.argv[2:] or ["baseline", "thin", "fat"]
    rounds = {
        variant: [json.loads(line) for line in (root / f"{variant}.jsonl").read_text().splitlines() if line.strip()]
        for variant in variants
    }
    header = "| Workload | " + " | ".join(variants) + " | " + " | ".join(f"{v} vs {variants[0]}" for v in variants[1:]) + " |"
    print("rounds: " + ", ".join(f"{v}={len(r)}" for v, r in rounds.items()))
    print(header)
    print("|---|" + "---:|" * (2 * len(variants) - 1))
    for key, label in METRICS:
        if any(key not in r for rs in rounds.values() for r in rs):
            continue
        medians = {v: statistics.median(r[key] for r in rs) for v, rs in rounds.items()}
        base = medians[variants[0]]
        cells = [f"{medians[v]:.1f}" for v in variants]
        cells += [f"{(medians[v] / base - 1) * 100:+.1f}%" for v in variants[1:]]
        print(f"| {label} | " + " | ".join(cells) + " |")
    # Per-file extraction and cold raster ratios for the largest contributors.
    for key in ("extract", "render_cold", "render_warm"):
        print(f"\n{key} per file (median ms):")
        names = rounds[variants[0]][0][key].keys()
        for name in names:
            values = {v: statistics.median(r[key][name] for r in rs) for v, rs in rounds.items()}
            base = values[variants[0]]
            ratio = " ".join(f"{v}={values[v]:.2f}({(values[v] / base - 1) * 100:+.0f}%)" for v in variants[1:])
            print(f"  {name}: {variants[0]}={base:.2f} {ratio}")


if __name__ == "__main__":
    main()
