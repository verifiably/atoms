---
id: atoms-83ae5c
title: Add a measured opt-in fast Python test loop
status: done
priority: 2
size: s
owner: test/front-door
created: 2026-09-02T03:41:26Z
updated: 2026-09-05T13:17:04Z
depends: [atoms-fde058, atoms-6c3692]
tags: [performance, testing, tooling]
---

Why: developers need an explicit fast local loop, but its benchmark target must be measured after rehearsal and duplicate-sweep removal.

Change: first document the zero-dependency deterministic workflow using pytest node ids, -k, --lf, and --ff. Re-benchmark after the two prerequisite fixes. Only then trial fixture-compatible pytest-xdist on a multicore host or coverage-based changed-test selection if the native loop remains insufficient.

Done when: one documented opt-in command materially improves local iteration; full pytest remains the required certification gate; benchmark method and limitations are recorded; no probabilistic omission is used.

## Notes

- 2026-09-02T10:38:53Z (perf/revise-test-tasks): Reordered behind the two measured duplication fixes; native deterministic selection remains first.
- 2026-09-05T13:08:58Z (test/front-door): Benchmarks 2026-09-05 (after both memoizations; wall seconds): full suite 494.6/510.6 plain, 635.9 under testmon tracing (cold). testmon warm: no change 0.6s (0 tests); +1 module-level stmt in fs/lookup.py -> 392 selected, 291s; in coordinator/recover.py -> 411 selected, 506s; in tests/execute_child.py (child-only code) -> 0 selected: testmon is blind to code run only in fresh-process children. Native: tests/test_fs_lookup.py 38 tests 0.4s; test_recovery_properties.py 3524 tests 5.3s; everything except kill_matrix+persistence_cut_matrix+exerciser 6139 tests 106s. The three subprocess suites are ~400 of ~500s and depend on nearly every source module, so testmon alone does not shorten a source edit's loop.
- 2026-09-05T13:17:04Z (test/front-door): just test-fast = pytest --testmon minus the three fresh-process suites (kill matrix, cut matrix, exerciser). Same fs/lookup.py edit: 291s plain testmon -> 53s (361 tests); no change 0.6s; in-process ceiling 106s vs ~500s full. Deterministic omissions named in the justfile and ops design §4.5; just test stays the gate. Limitation: testmon cannot see child-only code (execute_child.py edit selects 0). Method and numbers in the notes.
