# Temporary: lopdf small-document load evaluation

Evaluation branch only; delete after review. It measures a proposed upstream
lopdf change (`patches/pr.patch`, on lopdf `main` 15406c5) that parses
documents with few cross-reference entries, and object streams with few
members, on the calling thread instead of rayon's global pool.
`patches/eval-env.patch` adds `LOPDF_MIN_PARALLEL`, which overrides both
thresholds, so one build can sweep them. It is not part of the proposal.

Builds: `main-rayon` (lopdf main, default rayon), `main-seq` (main without
rayon), `pr-rayon` (main plus the change), `env-rayon` (plus the override).

Modes (`loadbench/main.rs`): `warm` back-to-back loads, `cold` loads 10 ms
apart so the pool's workers are parked, `after` a fixed 128 KiB hash timed
right after each cold load, `first` the first load of a new process, and
`objstm` `ObjectStream::new` on one large object stream from the calling
thread. `ab.py` rotates the build order every round; `summary.py` reports the
median over rounds of the per-round medians.
