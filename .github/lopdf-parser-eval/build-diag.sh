#!/usr/bin/env bash
# Build the sequential loadbench variants for the large-stream diagnosis in
# WORK_DIR/bin: lopdf main, the change's first one to four commits, the
# change, and its earlier version. Usage: build-diag.sh WORK_DIR
set -euo pipefail

eval_dir="$(cd "$(dirname "$0")" && pwd)"
base=15406c5f4fd2f6b934597eff452fbfb8b8f85545

mkdir -p "$1/bin"
cd "$1"
git clone --quiet https://github.com/J-F-Liu/lopdf.git lopdf-main
git -C lopdf-main checkout --quiet "${base}"
for name in c1 c2 c3 c4 pr prv1; do
    git clone --quiet lopdf-main "lopdf-${name}"
    git -C "lopdf-${name}" checkout --quiet "${base}"
    git -C "lopdf-${name}" apply "${eval_dir}/patches/${name}.patch"
done

for name in main c1 c2 c3 c4 pr prv1; do
    mkdir -p "harness-${name}/src"
    cp "${eval_dir}/loadbench/main.rs" "harness-${name}/src/main.rs"
    cat >"harness-${name}/Cargo.toml" <<TOML
[package]
name = "loadbench"
version = "0.1.0"
edition = "2024"
publish = false

[dependencies]
lopdf = { path = "../lopdf-${name}", default-features = false }

[workspace]
TOML
    cargo build --quiet --release --manifest-path "harness-${name}/Cargo.toml" --target-dir "target-${name}"
    cp "target-${name}/release/loadbench" "bin/${name}-seq"
done
ls -l bin
