#!/usr/bin/env bash
# Build the loadbench variants in WORK_DIR/bin: lopdf main with and without
# rayon, main plus the candidate change, and an evaluation-only build of the
# candidate whose thresholds LOPDF_MIN_PARALLEL overrides.
# Usage: build.sh WORK_DIR
set -euo pipefail

eval_dir="$(cd "$(dirname "$0")" && pwd)"
base=15406c5f4fd2f6b934597eff452fbfb8b8f85545
exe=""
[[ "${OS:-}" == "Windows_NT" ]] && exe=".exe"

mkdir -p "$1/bin"
cd "$1"
git clone --quiet https://github.com/J-F-Liu/lopdf.git lopdf-main
git -C lopdf-main checkout --quiet "${base}"
for copy in lopdf-pr lopdf-env; do
    git clone --quiet lopdf-main "${copy}"
    git -C "${copy}" checkout --quiet "${base}"
done
git -C lopdf-pr apply "${eval_dir}/patches/pr.patch"
git -C lopdf-env apply "${eval_dir}/patches/pr.patch" "${eval_dir}/patches/eval-env.patch"

build() {
    local name="$1" source="$2" features="$3"
    mkdir -p "harness-${name}/src"
    cp "${eval_dir}/loadbench/main.rs" "harness-${name}/src/main.rs"
    cat >"harness-${name}/Cargo.toml" <<TOML
[package]
name = "loadbench"
version = "0.1.0"
edition = "2024"
publish = false

[dependencies]
lopdf = { path = "../${source}", default-features = false }

[features]
rayon = ["lopdf/rayon"]

[workspace]
TOML
    # shellcheck disable=SC2086
    cargo build --quiet --release --manifest-path "harness-${name}/Cargo.toml" \
        --target-dir "target-${name}" ${features}
    cp "target-${name}/release/loadbench${exe}" "bin/${name}${exe}"
}

build main-rayon lopdf-main "--features rayon"
build main-seq lopdf-main ""
build pr-rayon lopdf-pr "--features rayon"
build env-rayon lopdf-env "--features rayon"
ls -l bin
