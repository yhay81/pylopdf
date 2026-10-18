#!/usr/bin/env bash
# Build the loadbench variants in WORK_DIR/bin from lopdf main (15406c5):
#   pr       main plus the parser change (patches/pr.patch)
#   both     main plus the calling-thread draft (patches/small.patch) and the
#            parser change, with the draft's thresholds (16 entries, 32 members)
#   envnew   both plus the evaluation-only threshold override
#            (patches/eval-env2.patch: LOPDF_MIN_PARALLEL entries,
#            LOPDF_MIN_PARALLEL_MEMBERS object stream members,
#            LOPDF_MIN_PARALLEL_BYTES input bytes)
#   envold   the draft plus the override, without the parser change
# "-rayon" builds use lopdf's default rayon feature, "-seq" builds go without it.
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

tree() {
    local name="$1"
    shift
    git clone --quiet lopdf-main "lopdf-${name}"
    git -C "lopdf-${name}" checkout --quiet "${base}"
    local patch
    for patch in "$@"; do
        git -C "lopdf-${name}" apply "${eval_dir}/patches/${patch}.patch"
    done
}
tree pr pr
tree both small pr
tree envnew small eval-env2 pr
tree envold small eval-env2

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

build pr-rayon lopdf-pr "--features rayon"
build pr-seq lopdf-pr ""
build both-rayon lopdf-both "--features rayon"
build envnew-rayon lopdf-envnew "--features rayon"
build envold-rayon lopdf-envold "--features rayon"
ls -l bin
