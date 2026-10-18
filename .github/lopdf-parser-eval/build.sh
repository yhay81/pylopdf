#!/usr/bin/env bash
# Build the loadbench variants in WORK_DIR/bin from lopdf main (15406c5):
#   main          lopdf main
#   pr            main plus the parser change (patches/pr.patch)
#   c1..c4        main plus the first N commits of the change, for attribution
#   prv1          an earlier version of the change that also rewrote the
#                 character classes with matches!
#   small         main plus the pending calling-thread draft (patches/small.patch)
#   both          small plus the parser change
#   envold        small plus the evaluation-only threshold override
#   envnew        envold plus the parser change
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
tree c1 c1
tree c2 c2
tree c3 c3
tree c4 c4
tree prv1 prv1
tree small small
tree both small pr
tree envold small eval-env
tree envnew small eval-env pr

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
build pr-seq lopdf-pr ""
build c1-seq lopdf-c1 ""
build c2-seq lopdf-c2 ""
build c3-seq lopdf-c3 ""
build c4-seq lopdf-c4 ""
build prv1-seq lopdf-prv1 ""
build small-rayon lopdf-small "--features rayon"
build both-rayon lopdf-both "--features rayon"
build envold-rayon lopdf-envold "--features rayon"
build envnew-rayon lopdf-envnew "--features rayon"
ls -l bin
