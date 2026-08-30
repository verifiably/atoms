---
id: atoms-eabd89
title: Parallelize the A8b certification guest sweep
status: todo
priority: 2
size: m
created: 2026-08-30T19:56:11Z
updated: 2026-08-30T19:56:11Z
depends: []
tags: [certification, tooling]
---

Run the nine certification scenarios in N concurrent QEMU guests instead of serially, so a kernel-bump recertification costs minutes of wall time rather than a full serial sweep. Input note: docs/plans/2026-08-30-parallel-certification-guests-note.md — per-scenario workspace isolation, harness evidence required equal across guests (stronger than today's last-guest rule), a --jobs bound, fail-whole-run-before-record. First step is the A8b design-gate pass; the note is input, not an approved design. Record schema, scenario matrix, and exact-tuple matching are unchanged.
