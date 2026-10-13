#!/usr/bin/env bash
# Set up managed Python for workflow scripts.
set -euo pipefail

version="${1:?provide a Python version}"
uv python install "$version"
interpreter="$(uv python find --managed-python "$version")"
dirname "$interpreter" >> "${GITHUB_PATH:?run this script in GitHub Actions}"
