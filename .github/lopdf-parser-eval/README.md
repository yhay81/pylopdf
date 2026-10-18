# Temporary: lopdf object parser evaluation

Evaluation branch only; delete after review. It measures a proposed upstream
lopdf change (`patches/pr.patch`, on lopdf `main` 15406c5) that speeds up the
object parser: an indirect object's dictionary is parsed once, character
classes and white space are tested directly, names are copied a run at a
time, integers are read without `FromStr`, and the first byte selects the
direct object parser. `patches/c1.patch` to `c4.patch` hold the change's
first one to four commits. `patches/small.patch` is the pending
calling-thread draft for small documents, and `patches/eval-env.patch` its
evaluation-only threshold override (`LOPDF_MIN_PARALLEL`,
`LOPDF_MIN_PARALLEL_BYTES`), neither part of this proposal.

Builds: `main` (lopdf main), `pr` (main plus the change), `c1`..`c4`,
`small` (main plus the draft), `both` (the draft plus the change), `env`
(both plus the override, run as `par` and `seq` to force one path).
`-rayon` builds use lopdf's default `rayon` feature, `-seq` builds go
without it.

Modes (`loadbench/main.rs`): `warm` back-to-back loads, `cold` loads 10 ms
apart so the pool's workers are parked, `first` the first load of a new
process, and `objstm` `ObjectStream::new` on one large object stream from
the calling thread. `ab.py` rotates the build order every round;
`summary.py` reports the median over rounds of the per-round medians.
`check.py` requires every build to load every input to the same exact
digest of the document (objects with raw names, strings and stream content,
trailer, cross-reference table, `max_id` and `xref_start`).
