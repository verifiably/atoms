---
id: atoms-83ae5c
title: Add a measured opt-in fast Python test loop
status: todo
priority: 2
size: s
created: 2026-09-02T03:41:26Z
updated: 2026-09-02T03:41:26Z
depends: []
tags: [performance, testing, tooling]
---

Why: the exhaustive Python gate takes 492.69s while collection takes about 1.6s. Most time is physical persistence and kill-matrix work; the 3,524-case recovery property module is not the bottleneck.

Change: document a zero-dependency focused workflow first using pytest node/keyword and last-failed selection. On a multicore host, benchmark pytest-xdist distribution compatible with fixture scope. Separately trial coverage-based changed-test selection for local use only and keep it only if recall checks and median savings justify the dependency.

Done when: one documented opt-in command provides a materially faster local loop; full pytest remains the required certification gate; benchmark method and limitations are recorded; no probabilistic omission is used for required verification.
