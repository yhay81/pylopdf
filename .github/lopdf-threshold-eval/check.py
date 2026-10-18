"""Load every input with every variant and require identical documents.

Prints one Markdown row per input (bytes, cross-reference entries, object
stream members) and exits non-zero if any variant loads a different document.
A variant is a build, optionally with environment variables, as in ab.py.

Usage: python check.py BIN_DIR "variant..." FILE...
  variant: NAME=BINARY[,ENV=VALUE...]  (BINARY in BIN_DIR, .exe added on Windows)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    bin_dir = Path(sys.argv[1])
    variants = sys.argv[2].split()
    files = sys.argv[3:]
    suffix = ".exe" if os.name == "nt" else ""
    results: dict[str, list[dict]] = {}
    for spec in variants:
        name, _, rest = spec.partition("=")
        binary, *assignments = (rest or name).split(",")
        env = dict(os.environ, **dict(assignment.split("=", 1) for assignment in assignments))
        out = subprocess.run(
            [str(bin_dir / f"{binary}{suffix}"), "info", *files], env=env, check=True, capture_output=True, text=True
        ).stdout
        results[name] = [json.loads(line) for line in out.splitlines() if line.strip()]
    names = list(results)
    mismatches = 0
    print(f"{len(names)} variants: {' '.join(names)}")
    print()
    print("| file | bytes | entries | normal | compressed | object stream members | digest |")
    print("|---|---|---|---|---|---|---|")
    for index, record in enumerate(results[names[0]]):
        digests = {name: results[name][index]["digest"] for name in names}
        same = len(set(digests.values())) == 1
        mismatches += not same
        members = record["objstm_members"]
        summary = f"{len(members)} streams, {sum(members)}" if members else ""
        print(
            f"| {record['file']} | {record['bytes']} | {record['entries']} | {record['normal']} | "
            f"{record['compressed']} | {summary} | {'same' if same else digests} |"
        )
    if mismatches:
        raise SystemExit(f"{mismatches} inputs load differently across variants")


if __name__ == "__main__":
    main()
