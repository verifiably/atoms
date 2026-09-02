---
id: atoms-6c3692
title: Avoid duplicate persistence-cut scenario sweeps
status: todo
priority: 1
size: m
created: 2026-09-02T03:41:26Z
updated: 2026-09-02T03:41:26Z
depends: []
tags: [performance, testing, certification]
---

Why: tests/test_persistence_cut_matrix.py repeats at about 203s. Base scenario sweeps take 11-22s each, and subprocess-placement tests rerun the complete minimal-move and minimal-replace sweeps before checking selected cells.

Change: share immutable recorded/swept evidence or combine assertions so each scenario is swept once per module while preserving fresh-world and placement guarantees. Do not cache mutable worlds or weaken the matrix.

Done when: every current cut and placement assertion remains covered; record three before/after module timings and per-scenario reports; demonstrate fewer scenario executions; run the Python repository gates.
