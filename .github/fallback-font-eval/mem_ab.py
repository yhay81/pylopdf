"""Interleave memory.py scenarios for two environments and print medians.

Usage: python mem_ab.py ROUNDS CORPUS_DIR FIXTURE_DIR BASE_PYTHON CANDIDATE_PYTHON

Each scenario runs in a new process per round; variant order alternates by
round. ``after`` is measured once every document has been released, except in
``open_documents``.
"""

from __future__ import annotations

import json
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCENARIOS = ("single_render", "fresh_renders", "open_documents", "extract_only")


def main() -> None:
    rounds = int(sys.argv[1])
    corpus, fixtures = sys.argv[2], sys.argv[3]
    pythons = {"main": sys.argv[4], "shared": sys.argv[5]}
    results: dict[str, dict[str, list[dict[str, float]]]] = {
        variant: {scenario: [] for scenario in SCENARIOS} for variant in pythons
    }
    for round_index in range(rounds):
        order = list(pythons) if round_index % 2 == 0 else list(reversed(pythons))
        for variant in order:
            for scenario in SCENARIOS:
                output = subprocess.run(
                    [pythons[variant], "-W", "ignore", str(HERE / "memory.py"), corpus, fixtures, scenario],
                    capture_output=True,
                    check=True,
                    text=True,
                ).stdout
                results[variant][scenario].append(json.loads(output))
        print(f"round {round_index + 1} done", file=sys.stderr)
    print("| Scenario (median MiB) | main before | main peak | main after | shared before | shared peak | shared after |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for scenario in SCENARIOS:
        cells = [
            statistics.median(row[field] for row in results[variant][scenario])
            for variant in pythons
            for field in ("before_mib", "peak_mib", "after_mib")
        ]
        print(f"| {scenario} | " + " | ".join(f"{value:.1f}" for value in cells) + " |")
    Path("memory-results.json").write_text(json.dumps(results), encoding="utf-8")


if __name__ == "__main__":
    main()
