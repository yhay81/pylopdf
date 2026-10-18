"""Summarize interleaved generation rounds: median of per-round medians.

Usage: python gen_summary.py RESULTS_DIR baseline candidate
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path


def main() -> None:
    root = Path(sys.argv[1])
    base, candidate = sys.argv[2:4]
    rounds = {
        variant: [
            json.loads(line)
            for line in (root / f"{variant}.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        for variant in (base, candidate)
    }
    print("rounds: " + ", ".join(f"{v}={len(r)}" for v, r in rounds.items()))
    print("font providers: " + ", ".join(f"{v}={rs[0]['font_providers']}" for v, rs in rounds.items()))
    print()
    print(f"| Workload (median ms) | {base} | {candidate} | change | round range |")
    print("|---|---:|---:|---:|---:|")
    for key in rounds[base][0]["ms"]:
        medians = {v: statistics.median(r["ms"][key] for r in rs) for v, rs in rounds.items()}
        ratios = sorted(
            c["ms"][key] / b["ms"][key] for b, c in zip(rounds[base], rounds[candidate], strict=False)
        )
        change = (medians[candidate] / medians[base] - 1) * 100
        print(
            f"| {key} | {medians[base]:.3f} | {medians[candidate]:.3f} | {change:+.1f}% |"
            f" {(ratios[0] - 1) * 100:+.1f}% .. {(ratios[-1] - 1) * 100:+.1f}% |"
        )

    mismatches = sorted(
        {
            (variant, name)
            for variant, results in rounds.items()
            for result in results
            for name, value in result["digests"].items()
            if value != rounds[base][0]["digests"][name]
        }
    )
    print()
    if mismatches:
        print(f"OUTPUT MISMATCHES: {mismatches}")
    else:
        print("output identity: every generated document matches across variants and rounds")


if __name__ == "__main__":
    main()
