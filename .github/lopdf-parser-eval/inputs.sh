#!/usr/bin/env bash
# Write the benchmark inputs into WORK_DIR/pdfs and WORK_DIR/objstm.
# Usage: inputs.sh WORK_DIR REPO_DIR PYTHON
set -euo pipefail

eval_dir="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$2" && pwd)"
python="$3"
exe=""
[[ "${OS:-}" == "Windows_NT" ]] && exe=".exe"

cd "$1"
mkdir -p pdfs objstm
mk() { "bin/main-rayon${exe}" mk "$@"; }
for pages in 1 2 4 8 12 16 24 32 48 64 96 128 192 256 512 1024; do
    mk pages "${pages}" "pdfs/pages-${pages}.pdf"
done
for pages in 4 16 32 64 128 256 1024; do
    mk objstm "${pages}" 100 "pdfs/objstm-${pages}.pdf"
done
for count in 2 4 8 16 32; do
    for kib in 64 512; do
        mk streams "${count}" "${kib}" "pdfs/streams-${count}x${kib}k.pdf"
    done
done
# One object stream holding every non-stream object: pages + 4 members.
for pages in 4 8 16 32 64 128 256 512 1024; do
    mk objstm "${pages}" 100000 "objstm/members-$((pages + 4)).pdf"
done
cp "${repo}"/tests/assets/real_world/*.pdf pdfs/
cp lopdf-main/assets/AnnotationDemo.pdf lopdf-main/assets/test.pdf pdfs/
"${python}" "${eval_dir}/make_large.py" "${repo}/tests/assets/real_world/usrguide.pdf" 20 pdfs/large-usrguide-x20.pdf
"${python}" "${eval_dir}/make_large.py" "${repo}/tests/assets/real_world/f1040.pdf" 10 pdfs/large-f1040-x10.pdf
