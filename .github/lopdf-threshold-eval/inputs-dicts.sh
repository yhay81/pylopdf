#!/usr/bin/env bash
# Write documents of many small dictionaries into WORK_DIR/dicts: the case
# the second limit (fewer than 48 entries under 32 KiB) handles worst, since
# their objects cost more to parse than page dictionaries and streams.
# Usage: inputs-dicts.sh WORK_DIR REPO_DIR PYTHON
set -euo pipefail

eval_dir="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$2" && pwd)"
python="$3"
exe=""
[[ "${OS:-}" == "Windows_NT" ]] && exe=".exe"

cd "$1"
mkdir -p dicts
mk() { "bin/pr-rayon${exe}" mk "$@"; }
# One page and COUNT text annotations: COUNT + 7 entries.
for count in 8 16 24 32 40; do
    mk dicts "${count}" "dicts/dicts-${count}.pdf"
done
for pages in 8 20; do
    mk pages "${pages}" "dicts/pages-${pages}.pdf"
done
cp "${repo}"/tests/assets/real_world/{pdfium-links-highlights-annots,pdf20-simple,pdfium-type3,pdfium-jpx-lzw,pdfium-smask-blend}.pdf dicts/
rw=dicts
annots="${rw}/pdfium-links-highlights-annots.pdf"
"${python}" "${eval_dir}/make_subsets.py" dicts \
    "simple-annots=${rw}/pdf20-simple.pdf+${annots}+${rw}/pdfium-type3.pdf" \
    "pdfium-x4=${rw}/pdfium-type3.pdf+${rw}/pdfium-jpx-lzw.pdf+${rw}/pdfium-smask-blend.pdf+${annots}" \
    "annots-x2=${annots}+${annots}" \
    "annots-x3=${annots}+${annots}+${annots}" \
    "annots-x4=${annots}+${annots}+${annots}+${annots}"
rm dicts/pdf20-simple.pdf dicts/pdfium-type3.pdf dicts/pdfium-jpx-lzw.pdf dicts/pdfium-smask-blend.pdf
