#!/usr/bin/env bash
# Install a checksum-verified official actionlint release for Linux.
set -euo pipefail

if [[ "$#" -gt 1 ]]; then
    printf 'usage: %s [installation-directory]\n' "$0" >&2
    exit 1
fi
# Official release and published checksums, pinned together:
# https://github.com/rhysd/actionlint/releases/tag/v1.7.12
# https://github.com/rhysd/actionlint/releases/download/v1.7.12/actionlint_1.7.12_checksums.txt
actionlint_version="1.7.12"
case "$(uname -s)/$(uname -m)" in
    Linux/x86_64|Linux/amd64)
        actionlint_architecture=amd64
        actionlint_sha256="8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"
        ;;
    Linux/aarch64|Linux/arm64)
        actionlint_architecture=arm64
        actionlint_sha256="325e971b6ba9bfa504672e29be93c24981eeb1c07576d730e9f7c8805afff0c6"
        ;;
    *) printf 'actionlint installer requires Linux x86-64 or ARM64\n' >&2; exit 1 ;;
esac
actionlint_archive="actionlint_${actionlint_version}_linux_${actionlint_architecture}.tar.gz"
temporary_root="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
installation_directory="${1:-${temporary_root}/pylopdf-actionlint-${actionlint_version}}"
download_directory="$(mktemp -d "${temporary_root}/pylopdf-actionlint-download.XXXXXX")"
trap 'rm -rf -- "$download_directory"' EXIT

curl --fail --silent --show-error --location --retry 3 \
    --connect-timeout 20 --max-time 120 \
    --output "${download_directory}/${actionlint_archive}" \
    "https://github.com/rhysd/actionlint/releases/download/v${actionlint_version}/${actionlint_archive}"
printf '%s  %s\n' "$actionlint_sha256" "${download_directory}/${actionlint_archive}" \
    | sha256sum --check --strict -
tar --extract --gzip --file "${download_directory}/${actionlint_archive}" \
    --directory "$download_directory" actionlint

mkdir -p -- "$installation_directory"
installation_directory="$(cd "$installation_directory" && pwd -P)"
install --mode=0755 "${download_directory}/actionlint" "${installation_directory}/actionlint"
if [[ -n "${GITHUB_PATH:-}" ]]; then
    printf '%s\n' "$installation_directory" >> "$GITHUB_PATH"
fi
"${installation_directory}/actionlint" -version
