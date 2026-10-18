"""Summarize interleaved rounds: median of per-round medians against a baseline.

Usage: python import_summary.py RESULTS_DIR baseline candidate...
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path


def main() -> None:
    root = Path(sys.argv[1])
    base, *candidates = sys.argv[2:]
    variants = [base, *candidates]
    rounds = {
        variant: [
            json.loads(line)
            for line in (root / f"{variant}.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for variant in variants
    }
    print("rounds: " + ", ".join(f"{v}={len(r)}" for v, r in rounds.items()))
    print()
    header = f"| Workload (median ms) | {base} |"
    rule = "|---|---:|"
    for candidate in candidates:
        header += f" {candidate} | change | round range |"
        rule += "---:|---:|---:|"
    print(header)
    print(rule)
    for key in rounds[base][0]["ms"]:
        base_median = statistics.median(r["ms"][key] for r in rounds[base])
        row = f"| {key} | {base_median:.3f} |"
        for candidate in candidates:
            median = statistics.median(r["ms"][key] for r in rounds[candidate])
            ratios = sorted(c["ms"][key] / b["ms"][key] for b, c in zip(rounds[base], rounds[candidate], strict=False))
            change = (median / base_median - 1) * 100
            row += f" {median:.3f} | {change:+.1f}% | {(ratios[0] - 1) * 100:+.1f}% .. {(ratios[-1] - 1) * 100:+.1f}% |"
        print(row)

    reference = rounds[base][0]["digests"]
    mismatches = sorted(
        {
            (variant, name)
            for variant, results in rounds.items()
            for result in results
            for name, value in result["digests"].items()
            if value != reference[name]
        }
    )
    print()
    if mismatches:
        print(f"OUTPUT MISMATCHES: {mismatches}")
    else:
        print("output identity: every produced document matches across variants and rounds")


if __name__ == "__main__":
    main()
