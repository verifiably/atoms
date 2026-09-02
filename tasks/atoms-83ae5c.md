---
id: atoms-83ae5c
title: Add a measured opt-in fast Python test loop
status: todo
priority: 2
size: s
created: 2026-09-02T03:41:26Z
updated: 2026-09-02T10:38:53Z
depends: [atoms-fde058, atoms-6c3692]
tags: [performance, testing, tooling]
---

Why: developers need an explicit fast local loop, but its benchmark target must be measured after rehearsal and duplicate-sweep removal.

Change: first document the zero-dependency deterministic workflow using pytest node ids, -k, --lf, and --ff. Re-benchmark after the two prerequisite fixes. Only then trial fixture-compatible pytest-xdist on a multicore host or coverage-based changed-test selection if the native loop remains insufficient.

Done when: one documented opt-in command materially improves local iteration; full pytest remains the required certification gate; benchmark method and limitations are recorded; no probabilistic omission is used.

## Notes

- 2026-09-02T10:38:53Z (perf/revise-test-tasks): Reordered behind the two measured duplication fixes; native deterministic selection remains first.
