"""Summarize interleaved A/B rounds: median of per-round medians per variant.

Usage: python ab_summary.py RESULTS_DIR baseline candidate

RESULTS_DIR contains <variant>.jsonl files with one ab.py JSON object per round.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

METRICS = [
    ("render_end_to_end_total", "First page to 2x PNG, fresh document, corpus total"),
    ("render_cold_total", "First page to 2x Pixmap, fresh document, corpus total"),
    ("render_warm_total", "First page to 2x Pixmap again, open document, corpus total"),
    ("render_encode_total", "PNG encoding of the 2x Pixmap, corpus total"),
    ("batch_w1", "render_pages 12 usrguide pages, fresh document, 1 worker"),
    ("batch_w4", "render_pages 12 usrguide pages, fresh document, 4 workers"),
    ("batch_open_w1", "render_pages 12 usrguide pages again, open document, 1 worker"),
    ("batch_open_w4", "render_pages 12 usrguide pages again, open document, 4 workers"),
    ("seq_fresh_usrguide_1x", "get_pixmap loop, usrguide 27 pages 1x, fresh document"),
    ("seq_open_usrguide_1x", "get_pixmap loop, usrguide 27 pages 1x, open document"),
    ("seq_fresh_usrguide_2x", "get_pixmap loop, usrguide 27 pages 2x, fresh document"),
    ("seq_open_usrguide_2x", "get_pixmap loop, usrguide 27 pages 2x, open document"),
    ("seq_fresh_bill_half", "get_pixmap loop, bill-hr815 110 pages 0.5x, fresh document"),
    ("seq_open_bill_half", "get_pixmap loop, bill-hr815 110 pages 0.5x, open document"),
]


def main() -> None:
    root = Path(sys.argv[1])
    variants = sys.argv[2:4]
    rounds = {
        variant: [
            json.loads(line)
            for line in (root / f"{variant}.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for variant in variants
    }
    base, candidate = variants
    print("rounds: " + ", ".join(f"{v}={len(r)}" for v, r in rounds.items()))
    print()
    print(f"| Workload (ms) | {base} | {candidate} | change |")
    print("|---|---:|---:|---:|")
    for key, label in METRICS:
        medians = {v: statistics.median(r[key] for r in rs) for v, rs in rounds.items()}
        change = (medians[candidate] / medians[base] - 1) * 100
        print(f"| {label} | {medians[base]:.1f} | {medians[candidate]:.1f} | {change:+.1f}% |")

    print()
    print(f"| Memory scenario (MiB) | {base} before | {base} peak | {base} after |"
          f" {candidate} before | {candidate} peak | {candidate} after |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for scenario in rounds[base][0]["memory"]:
        cells = []
        for variant in variants:
            for field in ("before_mib", "peak_mib", "after_mib"):
                cells.append(statistics.median(r["memory"][scenario][field] for r in rounds[variant]))
        print(f"| {scenario} | " + " | ".join(f"{value:.1f}" for value in cells) + " |")

    print()
    for key in ("render_cold", "render_warm", "render_end_to_end"):
        print(f"{key} per file (median ms):")
        for name in rounds[base][0][key]:
            values = {v: statistics.median(r[key][name] for r in rs) for v, rs in rounds.items()}
            change = (values[candidate] / values[base] - 1) * 100
            print(f"  {name}: {base}={values[base]:.2f} {candidate}={values[candidate]:.2f} ({change:+.1f}%)")
        print()

    mismatches = []
    reference = rounds[base][0]
    for variant, results in rounds.items():
        for result in results:
            for name, (first, again) in result["pixmap_digests"].items():
                expected = reference["pixmap_digests"][name][0]
                if first != expected or again != expected:
                    mismatches.append((variant, name))
            if result["batch_digest"] != reference["batch_digest"]:
                mismatches.append((variant, "render_pages batch"))
    if mismatches:
        print(f"PIXEL MISMATCHES: {sorted(set(mismatches))}")
    else:
        print("pixel identity: every cold, warm, and batch raster matches across variants and rounds")


if __name__ == "__main__":
    main()
