"""Interleave gen_memory.py scenarios for two environments and print medians.

Usage: python gen_mem_ab.py ROUNDS BASE_PYTHON CANDIDATE_PYTHON [OUT_JSON]

Each scenario runs in a new process per round; variant order alternates by
round. ``after`` is measured once every document has been released.
"""

from __future__ import annotations

import json
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCENARIOS = (
    "ja_fresh_texts_x30",
    "zh_serif_fresh_texts_x30",
    "mixed_scripts_reused_x60",
    "ja_form_4_widgets_x20",
    "helv_fresh_texts_x30",
)


def main() -> None:
    rounds = int(sys.argv[1])
    pythons = {"base": sys.argv[2], "shared": sys.argv[3]}
    out = Path(sys.argv[4]) if len(sys.argv) > 4 else Path("memory-results.json")
    results: dict[str, dict[str, list[dict[str, float]]]] = {
        variant: {scenario: [] for scenario in SCENARIOS} for variant in pythons
    }
    for round_index in range(rounds):
        order = list(pythons) if round_index % 2 == 0 else list(reversed(pythons))
        for variant in order:
            for scenario in SCENARIOS:
                output = subprocess.run(
                    [pythons[variant], "-W", "ignore", str(HERE / "gen_memory.py"), scenario],
                    capture_output=True,
                    check=True,
                    text=True,
                ).stdout
                results[variant][scenario].append(json.loads(output))
        print(f"round {round_index + 1} done", file=sys.stderr)
    print("| Scenario (median MiB) | base before | base peak | base after | shared before | shared peak | shared after |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for scenario in SCENARIOS:
        cells = [
            statistics.median(row[field] for row in results[variant][scenario])
            for variant in pythons
            for field in ("before_mib", "peak_mib", "after_mib")
        ]
        print(f"| {scenario} | " + " | ".join(f"{value:.1f}" for value in cells) + " |")
    out.write_text(json.dumps(results), encoding="utf-8")


if __name__ == "__main__":
    main()
