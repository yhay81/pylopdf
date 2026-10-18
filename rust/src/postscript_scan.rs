//! Iterative brace-depth scan for Type 4 (PostScript calculator) functions.
//!
//! hayro 0.7 parses a Type 4 function with `parse_procedure_inner`, which calls
//! itself for every `{` it meets between two operators and has no depth bound
//! (hayro 0.8 has none either). A program such as `"{" * 300_000 + "}" *
//! 300_000` overflows the stack of the interpreting thread, and a stack
//! overflow kills the process instead of raising. The same nesting drives
//! recursion when hayro evaluates the parsed program and when it drops it.
//! [`exceeds_depth`] decides, without recursing itself, whether hayro's parser
//! can be driven deeper than [`MAX_BRACE_DEPTH`] by the given bytes.
//!
//! The grammar needs no model beyond hayro's own trivia rules:
//!
//! * hayro skips whitespace (NUL, tab, LF, FF, CR, space) and comments (`%` up
//!   to the next CR or LF) before it reads a token, and the parse begins only
//!   if the first token is a `{`;
//! * `{` and `}` are delimiters, so an operator or a number ends before one
//!   and no other token can contain one. Comments are the only construct that
//!   hides a brace;
//! * the parse ends where the first procedure closes, and hayro ignores what
//!   follows.
//!
//! The scan does not model hayro's other reasons to abandon a parse, such as
//! an unknown operator or a string. Continuing past them can only count more
//! levels than hayro reaches.

/// Deepest `{ }` nesting that may reach hayro's recursive parser.
///
/// Real programs nest a handful of `if` and `ifelse` levels (at most three in
/// the 5,683 real PDFs that hayro interpreted). The bound matches the one for
/// nested arrays and dictionaries, and hayro's parse, evaluation, and drop
/// together measured about 430 bytes of stack per level, so the limit costs
/// under 30 KiB.
pub(crate) const MAX_BRACE_DEPTH: u32 = 64;

/// Whether the bytes of a Type 4 function nest procedures deeper than
/// [`MAX_BRACE_DEPTH`] before hayro's parse ends.
pub(crate) fn exceeds_depth(program: &[u8]) -> bool {
    let mut depth = 0u32;
    let mut bytes = program.iter();
    while let Some(&byte) = bytes.next() {
        match byte {
            b'{' => {
                depth += 1;
                if depth > MAX_BRACE_DEPTH {
                    return true;
                }
            }
            b'}' if depth > 1 => depth -= 1,
            // The first procedure closed, or the program did not open one.
            b'}' => return false,
            b'%' => {
                // A comment hides everything up to the next CR or LF.
                bytes.by_ref().find(|&&next| next == b'\n' || next == b'\r');
            }
            0x00 | 0x09 | 0x0a | 0x0c | 0x0d | 0x20 => {}
            // hayro rejects a program whose first token is not a `{`.
            _ if depth == 0 => return false,
            _ => {}
        }
    }
    false
}
