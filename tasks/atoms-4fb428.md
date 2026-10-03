---
id: atoms-4fb428
title: test-fast shows testmon's header so tt v4 records widened runs
status: todo
priority: 3
size: xs
complexity: low
process: direct
created: 2026-10-03T11:39:28Z
updated: 2026-10-03T11:39:28Z
depends: []
tags: [testing]
source: ops-a0c1f5
agent: claude-code/claude-opus-5-5
---

tt v4 (ops-a0c1f5) reads testmon's 'testmon: …' report-header line to record a run's selection; tt-latency sets new-DB and package-change runs aside. pytest prints the header only at verbosity ≥ 0. Recommended: add -v to the pytest invocation in fast_cmd, which cancels an ini addopts -q (one line per file, as -q's dots are now). Drop an explicit -q from fast_cmd instead where it has one. test and test-one are unchanged. Check: just test-fast under an agent records a line with selection set (jq '.selection' on the last line of the shared log).
