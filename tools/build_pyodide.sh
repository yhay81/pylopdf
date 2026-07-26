#!/usr/bin/env bash
# Build and smoke-test the wheel for Pyodide 0.28.3 / Cloudflare Workers.

set -euo pipefail

readonly PYODIDE_VERSION="0.28.3"
readonly PYTHON_VERSION="3.13.2"
readonly EMSCRIPTEN_VERSION="4.0.9"
readonly NODE_VERSION="20.18.0"
readonly PYODIDE_ABI="2025_0"
readonly PYODIDE_PLATFORM="pyodide_${PYODIDE_ABI}_wasm32"
readonly PYEMSCRIPTEN_PLATFORM="pyemscripten_${PYODIDE_ABI}_wasm32"
readonly RUST_TOOLCHAIN="1.95.0"
readonly PYODIDE_BUILD_VERSION="0.30.7"
readonly MATURIN_VERSION="1.14.1"
readonly EMSDK_COMMIT="3bcf1dcd01f040f370e10fe673a092d9ed79ebb5"
readonly XBUILDENV_SHA256="7c1229a3d634e07a440fc916d56738ac4db61410dc6f50eb59a11e4a30f1b56a"

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly BUILD_ROOT="${PYLOPDF_PYODIDE_BUILD_ROOT:-${REPOSITORY_ROOT}/.tmp/pyodide-${PYODIDE_VERSION}}"
readonly OUTPUT_DIR="${PYLOPDF_PYODIDE_OUTPUT_DIR:-${REPOSITORY_ROOT}/dist/pyodide-${PYODIDE_VERSION}}"
readonly PYTHON_BIN="${PYLOPDF_PYODIDE_PYTHON:-python3.13}"
readonly XBUILDENV_CACHE="${BUILD_ROOT}/xbuildenv-cache"
readonly XBUILDENV_VERSION_ROOT="${XBUILDENV_CACHE}/${PYODIDE_VERSION}"
readonly EMSDK_ROOT="${BUILD_ROOT}/emsdk"
readonly DOWNLOADS="${BUILD_ROOT}/downloads"
readonly TOOL_REQUIREMENTS="${SCRIPT_DIR}/pyodide-build-requirements.txt"

readonly XBUILDENV_URL="https://github.com/pyodide/pyodide/releases/download/${PYODIDE_VERSION}/xbuildenv-${PYODIDE_VERSION}.tar.bz2"

cd -- "${REPOSITORY_ROOT}"

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

download_checked() {
    local url="$1"
    local expected_sha256="$2"
    local destination="$3"

    if [[ -f "${destination}" ]] && printf '%s  %s\n' "${expected_sha256}" "${destination}" | sha256sum --check --status; then
        return
    fi
    rm -f -- "${destination}.partial"
    curl --fail --location --retry 3 --output "${destination}.partial" "${url}"
    printf '%s  %s\n' "${expected_sha256}" "${destination}.partial" | sha256sum --check --status \
        || fail "SHA-256 mismatch for ${url}"
    mv -- "${destination}.partial" "${destination}"
}

[[ "$(uname -s)" == "Linux" ]] || fail "Pyodide builds are supported only from Linux"
for command_name in curl git rustup sha256sum tar; do
    require_command "${command_name}"
done
require_command "${PYTHON_BIN}"

actual_python_version="$("${PYTHON_BIN}" -c 'import platform; print(platform.python_version())')"
[[ "${actual_python_version}" == "${PYTHON_VERSION}" ]] \
    || fail "host Python must be ${PYTHON_VERSION}, found ${actual_python_version}"
read -r tool_requirements_sha _ < <(sha256sum "${TOOL_REQUIREMENTS}")
readonly TOOL_VENV="${BUILD_ROOT}/tool-venv-${tool_requirements_sha:0:16}"

mkdir -p -- "${BUILD_ROOT}" "${DOWNLOADS}" "${OUTPUT_DIR}"

project_version="$("${PYTHON_BIN}" -c 'import pathlib, tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])')"
runtime_wheel="${OUTPUT_DIR}/pylopdf-${project_version}-cp310-abi3-${PYODIDE_PLATFORM}.whl"
release_wheel="${OUTPUT_DIR}/pylopdf-${project_version}-cp310-abi3-${PYEMSCRIPTEN_PLATFORM}.whl"
# Remove only the two exact artifacts this invocation owns so repeated builds
# cannot accidentally validate a wheel from an earlier source tree.
rm -f -- "${runtime_wheel}" "${release_wheel}"

if [[ ! -d "${EMSDK_ROOT}/.git" ]]; then
    [[ ! -e "${EMSDK_ROOT}" ]] || fail "${EMSDK_ROOT} exists but is not an emsdk checkout"
    git clone --filter=blob:none https://github.com/emscripten-core/emsdk.git "${EMSDK_ROOT}"
fi
git -C "${EMSDK_ROOT}" fetch --depth 1 origin "refs/tags/${EMSCRIPTEN_VERSION}:refs/tags/${EMSCRIPTEN_VERSION}"
git -C "${EMSDK_ROOT}" checkout --detach "refs/tags/${EMSCRIPTEN_VERSION}"
actual_emsdk_commit="$(git -C "${EMSDK_ROOT}" rev-parse HEAD)"
[[ "${actual_emsdk_commit}" == "${EMSDK_COMMIT}" ]] \
    || fail "emsdk tag ${EMSCRIPTEN_VERSION} resolved to ${actual_emsdk_commit}, expected ${EMSDK_COMMIT}"
"${EMSDK_ROOT}/emsdk" install "${EMSCRIPTEN_VERSION}"
"${EMSDK_ROOT}/emsdk" activate "${EMSCRIPTEN_VERSION}"
# shellcheck disable=SC1091
source "${EMSDK_ROOT}/emsdk_env.sh" >/dev/null
emcc_version="$(emcc --version | head -n 1)"
[[ "${emcc_version}" == *"${EMSCRIPTEN_VERSION}"* ]] \
    || fail "expected Emscripten ${EMSCRIPTEN_VERSION}, found ${emcc_version}"
[[ -x "${EMSDK_NODE:-}" ]] || fail "emsdk did not expose its Node.js runtime"
actual_node_version="$("${EMSDK_NODE}" --version)"
[[ "${actual_node_version}" == "v${NODE_VERSION}" ]] \
    || fail "expected emsdk Node.js ${NODE_VERSION}, found ${actual_node_version}"

export RUSTUP_HOME="${BUILD_ROOT}/rustup"
export CARGO_TARGET_DIR="${BUILD_ROOT}/cargo-target"
export CARGO_BUILD_JOBS="${PYLOPDF_PYODIDE_BUILD_JOBS:-2}"
rustup toolchain install "${RUST_TOOLCHAIN}" --profile minimal
rustup target add wasm32-unknown-emscripten --toolchain "${RUST_TOOLCHAIN}"

xbuildenv_marker="${XBUILDENV_VERSION_ROOT}/.pylopdf-sha256"
if [[ ! -f "${xbuildenv_marker}" ]] || [[ "$(<"${xbuildenv_marker}")" != "${XBUILDENV_SHA256}" ]]; then
    [[ ! -e "${XBUILDENV_VERSION_ROOT}" ]] \
        || fail "${XBUILDENV_VERSION_ROOT} is incomplete or has an unexpected checksum marker; remove it and retry"
    xbuildenv_archive="${DOWNLOADS}/xbuildenv-${PYODIDE_VERSION}.tar.bz2"
    download_checked "${XBUILDENV_URL}" "${XBUILDENV_SHA256}" "${xbuildenv_archive}"
    mkdir -p -- "${XBUILDENV_VERSION_ROOT}"
    tar -xjf "${xbuildenv_archive}" --directory "${XBUILDENV_VERSION_ROOT}"
    printf '%s\n' "${XBUILDENV_SHA256}" >"${xbuildenv_marker}"
fi

"${PYTHON_BIN}" -m venv "${TOOL_VENV}"
"${TOOL_VENV}/bin/python" -m pip install \
    --disable-pip-version-check \
    --require-hashes \
    --requirement "${TOOL_REQUIREMENTS}"
export PATH="${TOOL_VENV}/bin:${PATH}"
"${TOOL_VENV}/bin/pyodide" xbuildenv install "${PYODIDE_VERSION}" \
    --path "${XBUILDENV_CACHE}" \
    --force

export RUSTUP_TOOLCHAIN="${RUST_TOOLCHAIN}"
# Emscripten 4.0.9 rejects legacy Rust export names containing `$u7b$`.
# Rust v0 mangling keeps linker-visible names valid without changing the ABI.
export RUSTFLAGS="-C symbol-mangling-version=v0 -C link-arg=-sSIDE_MODULE=2"
# Optimize the single cdylib as one linked unit. This affects only the
# PyEmscripten artifact: native maturin builds retain Cargo's default release
# profile and compile-time tradeoff.
export CARGO_PROFILE_RELEASE_LTO="fat"
export CARGO_PROFILE_RELEASE_CODEGEN_UNITS="1"
source_date_epoch="${PYLOPDF_SOURCE_DATE_EPOCH:-}"
if [[ -z "${source_date_epoch}" ]]; then
    source_date_epoch="$(git log -1 --format=%ct)" \
        || fail "cannot determine SOURCE_DATE_EPOCH; set PYLOPDF_SOURCE_DATE_EPOCH outside a Git checkout"
fi
[[ "${source_date_epoch}" =~ ^[0-9]+$ ]] || fail "SOURCE_DATE_EPOCH must be an integer"
export SOURCE_DATE_EPOCH="${source_date_epoch}"
"${TOOL_VENV}/bin/pyodide" build "${REPOSITORY_ROOT}" \
    --outdir "${OUTPUT_DIR}" \
    --xbuildenv-path "${XBUILDENV_CACHE}" \
    --no-isolation \
    --skip-dependency-check

[[ -f "${runtime_wheel}" ]] || fail "Pyodide build did not produce ${runtime_wheel}"
"${PYTHON_BIN}" "${SCRIPT_DIR}/verify_pyodide_wheel.py" \
    "${runtime_wheel}" \
    --version "${project_version}" \
    --platform "${PYODIDE_PLATFORM}"

if [[ "${PYLOPDF_PYODIDE_SKIP_SMOKE:-0}" != "1" ]]; then
    compatibility_assets="$("${PYTHON_BIN}" "${SCRIPT_DIR}/pyodide_compat.py" --list-assets)"
    smoke_arguments=(
        "${XBUILDENV_CACHE}/xbuildenv/xbuildenv/pyodide-root/dist"
        "${runtime_wheel}"
        "${REPOSITORY_ROOT}"
        "${compatibility_assets}"
    )
    native_results="${PYLOPDF_PYODIDE_NATIVE_RESULTS:-}"
    if [[ -n "${native_results}" ]]; then
        [[ -f "${native_results}" ]] || fail "native compatibility baseline is unavailable: ${native_results}"
        smoke_arguments+=("${native_results}")
    fi
    "${EMSDK_NODE}" "${SCRIPT_DIR}/smoke_pyodide.mjs" \
        "${smoke_arguments[@]}"
fi

"${TOOL_VENV}/bin/wheel" tags \
    --platform-tag="${PYEMSCRIPTEN_PLATFORM}" \
    --remove \
    "${runtime_wheel}"
[[ -f "${release_wheel}" ]] || fail "wheel retagging did not produce ${release_wheel}"
"${PYTHON_BIN}" "${SCRIPT_DIR}/verify_pyodide_wheel.py" \
    "${release_wheel}" \
    --version "${project_version}" \
    --platform "${PYEMSCRIPTEN_PLATFORM}"

mapfile -t wheels < <(find "${OUTPUT_DIR}" -maxdepth 1 -type f -name "pylopdf-${project_version}-*.whl" -print)
[[ "${#wheels[@]}" -eq 1 ]] \
    || fail "expected one publishable pylopdf ${project_version} wheel in ${OUTPUT_DIR}, found ${#wheels[@]}"
[[ "${wheels[0]}" == "${release_wheel}" ]] || fail "unexpected publishable artifact: ${wheels[0]}"

printf 'Pyodide %s build completed with Python %s, Emscripten %s, Node.js %s, Rust %s, pyodide-build %s, and maturin %s\n' \
    "${PYODIDE_VERSION}" \
    "${PYTHON_VERSION}" \
    "${EMSCRIPTEN_VERSION}" \
    "${NODE_VERSION}" \
    "${RUST_TOOLCHAIN}" \
    "${PYODIDE_BUILD_VERSION}" \
    "${MATURIN_VERSION}"
