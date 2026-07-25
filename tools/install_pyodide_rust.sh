#!/usr/bin/env bash
set -euo pipefail

readonly rust_toolchain="nightly-2025-02-01"
readonly emscripten_version="4.0.9"
readonly archive_name="emcc-${emscripten_version}_${rust_toolchain}.tar.bz2"
readonly archive_url="https://github.com/pyodide/rust-emscripten-wasm-eh-sysroot/releases/download/emcc-${emscripten_version}_${rust_toolchain}/${archive_name}"
readonly archive_sha256="572731c36ced02d84f2b825a1d91484d48aa5f935d6694d9231369010733fa5b"

rustup toolchain install "${rust_toolchain}" --profile minimal --component rust-src

toolchain_root="$(rustup run "${rust_toolchain}" rustc --print sysroot)"
readonly toolchain_root
readonly rustlib_dir="${toolchain_root}/lib/rustlib"
readonly target_dir="${rustlib_dir}/wasm32-unknown-emscripten"

if compgen -G "${target_dir}/lib/libstd-*.rlib" >/dev/null; then
    exit 0
fi

download_dir="$(mktemp -d)"
readonly download_dir
trap 'rm -rf "${download_dir}"' EXIT

curl --fail --location --silent --show-error "${archive_url}" --output "${download_dir}/${archive_name}"
printf '%s  %s\n' "${archive_sha256}" "${download_dir}/${archive_name}" | shasum --algorithm 256 --check
mkdir -p "${rustlib_dir}"
tar -xjf "${download_dir}/${archive_name}" -C "${rustlib_dir}"

test -d "${target_dir}"
