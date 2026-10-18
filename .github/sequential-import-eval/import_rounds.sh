#!/usr/bin/env bash
# Run import_ab.py for each variant in rotating order and append one JSON line per round.
# Usage: import_rounds.sh OUT_DIR ROUNDS REPEATS VENV_PREFIX ASSETS_DIR LARGE_DIR MODE variant...
set -euo pipefail

out_dir="$1"
rounds="$2"
repeats="$3"
venv_prefix="$4"
assets_dir="$5"
large_dir="$6"
mode="$7"
shift 7
variants=("$@")
script_dir="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "${out_dir}"

count="${#variants[@]}"
for ((round = 0; round < rounds; round++)); do
    for ((slot = 0; slot < count; slot++)); do
        variant="${variants[$(((round + slot) % count))]}"
        python_bin="${venv_prefix}${variant}/bin/python"
        [[ -x "${python_bin}" ]] || python_bin="${venv_prefix}${variant}/Scripts/python.exe"
        load="$(uptime 2>/dev/null | sed 's/.*load average[s]*: //' || true)"
        "${python_bin}" -W ignore "${script_dir}/import_ab.py" "${assets_dir}" "${large_dir}" "${repeats}" "${mode}" \
            >>"${out_dir}/${variant}.jsonl"
        printf 'round %d %s done (load %s)\n' "$((round + 1))" "${variant}" "${load}"
    done
done
