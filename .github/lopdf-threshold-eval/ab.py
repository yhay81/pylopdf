"""Interleaved A/B rounds for loadbench variants.

Every round runs each variant once per mode, rotating the variant order by one
slot per round so that no variant always runs first or last. Each run is a new
process; its per-file medians are appended to OUT/<variant>.jsonl.

Usage: python ab.py OUT ROUNDS BIN_DIR "variant..." "mode..." FILE...
  variant: NAME=BINARY[,ENV=VALUE...]  (BINARY in BIN_DIR, .exe added on Windows)
  mode:    warm:ITERS  cold:ITERS:GAP_MS  after:ITERS:GAP_MS  first:REPEATS  objstm:ITERS:GAP_MS
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def parse_variant(spec: str, bin_dir: Path) -> tuple[str, Path, dict[str, str]]:
    name, _, rest = spec.partition("=")
    binary, *assignments = (rest or name).split(",")
    suffix = ".exe" if os.name == "nt" else ""
    env = dict(assignment.split("=", 1) for assignment in assignments)
    return name, bin_dir / f"{binary}{suffix}", env


def run(binary: Path, name: str, extra_env: dict[str, str], args: list[str]) -> list[dict]:
    env = dict(os.environ, LOADBENCH_VARIANT=name, **extra_env)
    out = subprocess.run([str(binary), *args], env=env, check=True, capture_output=True, text=True).stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def main() -> None:
    out_dir = Path(sys.argv[1])
    rounds = int(sys.argv[2])
    bin_dir = Path(sys.argv[3])
    variants = [parse_variant(spec, bin_dir) for spec in sys.argv[4].split()]
    modes = sys.argv[5].split()
    files = sys.argv[6:]
    out_dir.mkdir(parents=True, exist_ok=True)
    for round_index in range(rounds):
        shift = round_index % len(variants)
        order = variants[shift:] + variants[:shift]
        for mode in modes:
            kind, *params = mode.split(":")
            for name, binary, env in order:
                records: list[dict] = []
                if kind == "warm":
                    records = run(binary, name, env, ["warm", params[0], *files])
                elif kind == "cold":
                    records = run(binary, name, env, ["cold", params[0], params[1], *files])
                elif kind == "after":
                    records = run(binary, name, env, ["after", params[0], params[1], *files])
                elif kind == "first":
                    for file in files:
                        times = sorted(
                            run(binary, name, env, ["first", file])[0]["median_ns"] for _ in range(int(params[0]))
                        )
                        records.append(
                            {
                                "variant": name,
                                "mode": "first",
                                "file": Path(file).name,
                                "median_ns": times[len(times) // 2],
                                "iters": len(times),
                            }
                        )
                elif kind == "objstm":
                    for file in files:
                        records += run(binary, name, env, ["objstm", params[0], params[1], file])
                else:
                    raise SystemExit(f"unknown mode {mode}")
                with (out_dir / f"{name}.jsonl").open("a") as handle:
                    for record in records:
                        record["round"] = round_index
                        handle.write(json.dumps(record) + "\n")
        load = f"{os.getloadavg()[0]:.2f}" if hasattr(os, "getloadavg") else "n/a"
        print(f"round {round_index + 1}/{rounds} done (load {load})", flush=True)


if __name__ == "__main__":
    main()
