"""Summarize interleaved A/B rounds: median of per-round medians per variant.

Usage: python ab_summary.py RESULTS_DIR baseline candidate
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

METRICS = [
    ("render_fresh_total", "First page to 2x Pixmap, fresh document, corpus total (14 files)"),
    ("png_fresh_total", "First page to 2x PNG, fresh document, corpus total"),
    ("cjk_render_fresh_total", "Non-embedded CJK fixture to 2x Pixmap, fresh document, 4 fixtures total"),
    ("corpus_loop_fresh", "One pass: open each corpus file, first page to 1x Pixmap"),
    ("batch_fresh_w1", "render_pages 12 usrguide pages 1x, fresh document, 1 worker"),
    ("batch_fresh_w4", "render_pages 12 usrguide pages 1x, fresh document, 4 workers"),
    ("render_extract_fresh_total", "First page to 1x Pixmap then its text, fresh document, corpus total"),
    ("extract_fresh_total", "get_text() of every page, fresh document, corpus total (control)"),
    ("cjk_extract_fresh_total", "get_text() of the CJK fixtures, fresh document (control)"),
    ("open_only_total", "Open only, corpus total (control)"),
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
    print("font providers: " + ", ".join(f"{v}={rs[0]['font_providers']}" for v, rs in rounds.items()))
    print()
    print(f"| Workload (ms) | {base} | {candidate} | change | round range ({candidate}/{base}) |")
    print("|---|---:|---:|---:|---:|")
    for key, label in METRICS:
        medians = {v: statistics.median(r[key] for r in rs) for v, rs in rounds.items()}
        ratios = sorted(c[key] / b[key] for b, c in zip(rounds[base], rounds[candidate], strict=False))
        change = (medians[candidate] / medians[base] - 1) * 100
        print(
            f"| {label} | {medians[base]:.2f} | {medians[candidate]:.2f} | {change:+.1f}% |"
            f" {(ratios[0] - 1) * 100:+.1f}% .. {(ratios[-1] - 1) * 100:+.1f}% |"
        )

    print()
    print(
        f"| Memory scenario (MiB) | {base} before | {base} peak | {base} after |"
        f" {candidate} before | {candidate} peak | {candidate} after |"
    )
    print("|---|---:|---:|---:|---:|---:|---:|")
    for scenario in rounds[base][0]["memory"]:
        cells = []
        for variant in variants:
            for field in ("before_mib", "peak_mib", "after_mib"):
                cells.append(statistics.median(r["memory"][scenario][field] for r in rounds[variant]))
        print(f"| {scenario} | " + " | ".join(f"{value:.1f}" for value in cells) + " |")

    print()
    for key in ("render_fresh", "cjk_render_fresh", "extract_fresh"):
        print(f"{key} per file (median ms):")
        for name in rounds[base][0][key]:
            values = {v: statistics.median(r[key][name] for r in rs) for v, rs in rounds.items()}
            change = (values[candidate] / values[base] - 1) * 100
            print(f"  {name}: {base}={values[base]:.3f} {candidate}={values[candidate]:.3f} ({change:+.1f}%)")
        print()

    mismatches = []
    reference = rounds[base][0]["digests"]
    for variant, results in rounds.items():
        for result in results:
            for name, digests in result["digests"].items():
                if digests != reference[name]:
                    mismatches.append((variant, name))
    if mismatches:
        print(f"OUTPUT MISMATCHES: {sorted(set(mismatches))}")
    else:
        print("output identity: every raster and extracted text matches across variants and rounds")


if __name__ == "__main__":
    main()
