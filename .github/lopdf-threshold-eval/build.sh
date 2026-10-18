#!/usr/bin/env bash
# Build loadbench variants in WORK_DIR/bin from lopdf main (15406c5):
#   pr        main plus the parser change (patches/pr.patch)
#   both      main plus the calling-thread draft (patches/small.patch) and the
#             parser change, with the draft's thresholds (16 entries, 32 members)
#   envnew    both plus the evaluation-only threshold override
#             (patches/eval-env2.patch: LOPDF_MIN_PARALLEL entries,
#             LOPDF_MIN_PARALLEL_MEMBERS object stream members,
#             LOPDF_MIN_PARALLEL_BYTES input bytes)
#   envold    the draft plus the override, without the parser change
#   tuned     the parser change plus the draft with a second limit
#             (patches/small-tuned.patch: also fewer than 48 entries when the
#             input is under 32 KiB)
#   small     the draft alone, oldtuned the tuned draft alone (old parser)
# "-rayon" builds use lopdf's default rayon feature, "-seq" builds go without it.
# BUILDS selects the builds (pr-rayon is always needed to write the inputs).
# Usage: build.sh WORK_DIR
set -euo pipefail

eval_dir="$(cd "$(dirname "$0")" && pwd)"
base=15406c5f4fd2f6b934597eff452fbfb8b8f85545
exe=""
[[ "${OS:-}" == "Windows_NT" ]] && exe=".exe"
builds="${BUILDS:-pr-rayon pr-seq both-rayon envnew-rayon envold-rayon}"
want() { [[ " ${builds} " == *" $1 "* ]]; }

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
if want pr-rayon || want pr-seq; then tree pr pr; fi
if want both-rayon; then tree both small pr; fi
if want envnew-rayon; then tree envnew small eval-env2 pr; fi
if want envold-rayon; then tree envold small eval-env2; fi
if want tuned-rayon; then tree tuned pr small-tuned; fi
if want small-rayon; then tree small small; fi
if want oldtuned-rayon; then tree oldtuned small-tuned; fi

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

for name in ${builds}; do
    case "${name}" in
        *-rayon) build "${name}" "lopdf-${name%-rayon}" "--features rayon" ;;
        *-seq) build "${name}" "lopdf-${name%-seq}" "" ;;
        *) echo "unknown build ${name}" >&2 && exit 1 ;;
    esac
done
ls -l bin
