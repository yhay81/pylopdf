#!/usr/bin/env bash
# Write the benchmark inputs into WORK_DIR/pdfs (loads) and WORK_DIR/objstm
# (ObjectStream::new). Most load inputs have 16 to 128 cross-reference
# entries and less than 1 MiB, where the swept thresholds decide the path.
# Usage: inputs.sh WORK_DIR REPO_DIR PYTHON
set -euo pipefail

eval_dir="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$2" && pwd)"
python="$3"
exe=""
[[ "${OS:-}" == "Windows_NT" ]] && exe=".exe"

cd "$1"
mkdir -p pdfs objstm
mk() { "bin/pr-rayon${exe}" mk "$@"; }
# Page documents: 2 * pages + 5 entries, 7 to 133, and two larger controls.
for pages in 1 2 4 6 8 10 12 14 16 20 24 28 32 48 64 128 256; do
    mk pages "${pages}" "pdfs/pages-${pages}.pdf"
done
# The same with one object stream for every 100 objects: 14 to 134 entries.
for pages in 4 8 12 16 20 24 32 64; do
    mk objstm "${pages}" 100 "pdfs/objstm-${pages}.pdf"
done
# One page drawing COUNT image XObjects of KIB KiB each: COUNT + 8 entries.
for spec in 2x64 4x64 8x64 12x64 16x64 24x32 40x16 56x8 2x512 4x512; do
    mk streams "${spec%x*}" "${spec#*x}" "pdfs/streams-${spec}k.pdf"
done
# One object stream holding every non-stream object: pages + 4 members.
for pages in 4 8 12 16 20 24 28 32 36 40 44 52 60 64 92 128 256; do
    mk objstm "${pages}" 100000 "objstm/members-$((pages + 4)).pdf"
done
cp "${repo}"/tests/assets/real_world/*.pdf pdfs/
for asset in AnnotationDemo Incremental example unicode; do
    cp "lopdf-main/assets/${asset}.pdf" pdfs/
done
# First pages of the real-world documents, and some joined together.
rw="${repo}/tests/assets/real_world"
senate="${rw}/senate-expenditures.pdf"
nics="${rw}/nics-background-checks-2015-11.pdf"
patent="${rw}/patent-us223898.pdf"
"${python}" "${eval_dir}/make_subsets.py" pdfs \
    "${patent}:2,3,2s,3s,4s" \
    "${rw}/bill-hr815.pdf:1,2,4,6,10,16,1s,4s,10s" \
    "${nics}:1s" \
    "${senate}:1s" \
    "pdfium-x4=${rw}/pdfium-type3.pdf+${rw}/pdfium-jpx-lzw.pdf+${rw}/pdfium-smask-blend.pdf+${rw}/pdfium-links-highlights-annots.pdf" \
    "simple-annots=${rw}/pdf20-simple.pdf+${rw}/pdfium-links-highlights-annots.pdf+${rw}/pdfium-type3.pdf" \
    "senate-simple=${senate}+${rw}/pdf20-simple.pdf" \
    "senate-wdl=${senate}+${rw}/wdl6812-manuscript.pdf" \
    "senate-nics=${senate}+${nics}" \
    "senate-nics-objstm=${senate}+${nics}" \
    "nics-x2=${nics}+${nics}" \
    "nics-patent=${nics}+${patent}"
