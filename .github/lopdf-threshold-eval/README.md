# Temporary: lopdf calling-thread threshold evaluation

Evaluation branch only; delete after review. It sweeps the thresholds of a
pending lopdf draft (`patches/small.patch`, on lopdf `main` 15406c5) that
parses documents with fewer than 16 cross-reference entries (and less than
1 MiB of input) and object streams with fewer than 32 members on the calling
thread instead of rayon's pool, for the case where a pending parser speedup
(`patches/pr.patch`) is also applied. Faster parsing moves the point where
the pool pays for its handoff, so the draft's thresholds may be too low on
top of it. `patches/eval-env2.patch` is an evaluation-only override:
`LOPDF_MIN_PARALLEL` sets the entry threshold, `LOPDF_MIN_PARALLEL_MEMBERS`
the member threshold and `LOPDF_MIN_PARALLEL_BYTES` the byte threshold.

Builds (`build.sh`): `pr` (main plus the parser change), `both` (plus the
draft with its thresholds), `envnew` (`both` plus the override) and `envold`
(the draft plus the override, without the parser change). `-rayon` builds
use lopdf's default `rayon` feature; `pr-seq` goes without it.

Variants (the workflow's `LOAD_VARIANTS` and `OBJSTM_VARIANTS`): `par` and
`seq` force one `envnew` build onto the pool and onto the calling thread,
`old-par` and `old-seq` the same without the parser change, `eE` runs
`envnew` with E entries (and E members, but 32 for `e16` and `e24`), and
`mM` sets only the member threshold for `ObjectStream::new`.

Inputs (`inputs.sh`): synthetic page, object-stream and image documents with
7 to 517 entries, the real-world files of `tests/assets/real_world`, four
lopdf assets, and first pages of the real-world files or several of them
joined (`make_subsets.py`, saved by lopdf through pylopdf 0.14.0), most with
16 to 128 entries.

Modes (`loadbench/main.rs`): `warm` back-to-back loads, `cold` loads 10 ms
apart so the pool's workers are parked, `first` the first load of a new
process, and `objstm` `ObjectStream::new` on every object stream of a
document from the calling thread. `ab.py` rotates the variant order every
round; `summary.py` reports the median over rounds of the per-round medians.
`check.py` requires every variant to load every input to the same exact
digest of the document (objects with raw names, strings and stream content,
trailer, cross-reference table, `max_id` and `xref_start`).
