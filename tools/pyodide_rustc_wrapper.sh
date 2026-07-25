#!/usr/bin/env bash
set -euo pipefail

# Pyodide 0.28.3's Rust 1.86 predates syntax/library features used by current
# PDF dependencies. The standard library already declares some of these
# features, so apply them only to non-sysroot crates while Cargo rebuilds std.
actual_rustc="$1"
shift

crate_name=""
previous=""
for argument in "$@"; do
    if [[ "${previous}" == "--crate-name" ]]; then
        crate_name="${argument}"
        break
    fi
    previous="${argument}"
done

case "${crate_name}" in
    alloc | compiler_builtins | core | panic_abort | panic_unwind | proc_macro | std | std_detect | test | unwind | rustc_*)
        exec "${actual_rustc}" "$@"
        ;;
    *)
        exec "${actual_rustc}" "$@" \
            -Zcrate-attr=feature\(cfg_boolean_literals\) \
            -Zcrate-attr=feature\(integer_sign_cast\) \
            -Zcrate-attr=feature\(let_chains\) \
            -Zcrate-attr=feature\(slice_as_chunks\) \
            -Zcrate-attr=feature\(unsigned_is_multiple_of\)
        ;;
esac
