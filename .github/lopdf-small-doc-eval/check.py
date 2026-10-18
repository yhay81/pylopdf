"""Load every input with every build and require identical documents.

Prints one Markdown row per input (bytes, cross-reference entries, object
stream members) and exits non-zero if any build loads a different document.

Usage: python check.py BIN_DIR "binary..." FILE...
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    bin_dir = Path(sys.argv[1])
    binaries = sys.argv[2].split()
    files = sys.argv[3:]
    suffix = ".exe" if os.name == "nt" else ""
    results: dict[str, list[dict]] = {}
    for binary in binaries:
        out = subprocess.run(
            [str(bin_dir / f"{binary}{suffix}"), "info", *files], check=True, capture_output=True, text=True
        ).stdout
        results[binary] = [json.loads(line) for line in out.splitlines() if line.strip()]
    mismatches = 0
    print("| file | bytes | entries | normal | compressed | object stream members | digest |")
    print("|---|---|---|---|---|---|---|")
    for index, record in enumerate(results[binaries[0]]):
        digests = {binary: results[binary][index]["digest"] for binary in binaries}
        same = len(set(digests.values())) == 1
        mismatches += not same
        members = record["objstm_members"]
        summary = f"{len(members)} streams, {sum(members)}" if members else ""
        print(
            f"| {record['file']} | {record['bytes']} | {record['entries']} | {record['normal']} | "
            f"{record['compressed']} | {summary} | {'same' if same else digests} |"
        )
    if mismatches:
        raise SystemExit(f"{mismatches} inputs load differently across builds")


if __name__ == "__main__":
    main()
