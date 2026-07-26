# pylopdf free-threaded benchmark

- Run at: 2026-07-26 10:28 UTC
- Environment: Windows-11-10.0.26200-SP0 / Python 3.14.6 free-threaded
- pylopdf: 0.11.1
- Input: `tests/assets/real_world/bill-hr815.pdf`
- Workload: 2 independent documents, all-page text extraction
- Repetitions: one warmup + median of 7 paired, alternating-order runs
- Output validation: every parallel result exactly matched the sequential result
- Reproduce: `py -3.14t bench/free_threaded.py --copies 2 --repetitions 7`

| Mode | Workers | Time (ms) | Speedup |
|---|---:|---:|---:|
| Sequential | 1 | 400.8 | 1.00x |
| Parallel | 2 | 235.5 | 1.70x |

This report is generated separately from `bench/results/latest.md` so rerunning
the regular GIL-enabled benchmark cannot discard the free-threaded evidence.
