---
id: atoms-fde058
title: Reduce kill-matrix recovery process launches
status: todo
priority: 1
size: m
created: 2026-09-02T03:41:26Z
updated: 2026-09-02T03:41:26Z
depends: []
tags: [performance, testing, certification]
---

Why: the compound SIGKILL test repeats at 86.06-87.67s. Each cut starts a killed child and then two recovery subprocesses; the second recovery exists to prove convergence under a fresh lease.

Change: preserve a fresh process for first recovery, but evaluate whether both recovery lease entries can run in that one child and return both observations. Apply the same proven helper to coordinator kill matrices only if their contract matches.

Done when: recovery freshness and convergence guarantees remain explicit; add a check that would fail if the second lease pass diverges; record three before/after compound-test timings and subprocess counts; run the Python repository gates.
