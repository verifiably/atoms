---
id: atoms-eabd89
title: Parallelize the A8b certification guest sweep
status: doing
priority: 2
size: m
owner: feat/atoms-eabd89-parallel-certification
created: 2026-08-30T19:56:11Z
updated: 2026-09-11T12:17:06Z
depends: []
tags: [certification, tooling]
---

Run the nine certification scenarios in N concurrent QEMU guests instead of serially, so a kernel-bump recertification costs minutes of wall time rather than a full serial sweep. Input note: docs/plans/2026-08-30-parallel-certification-guests-note.md — per-scenario workspace isolation, harness evidence required equal across guests (stronger than today's last-guest rule), a --jobs bound, fail-whole-run-before-record. First step is the A8b design-gate pass; the note is input, not an approved design. Record schema, scenario matrix, and exact-tuple matching are unchanged.

## Notes

- 2026-09-11T12:07:45Z (feat/atoms-eabd89-parallel-certification): Design-gate inspection at 2605839: host data/log images already isolated per scenario; replay-log/initramfs build must finish before fan-out. Exact harness equality currently blocked by random guest mountpoint; propose fixed guest-private mountpoint. self-test is boot/device safety only; extend manual self-check for concurrency and refusal. No production seam or deferred obligation introduced.
- 2026-09-11T12:08:02Z (feat/atoms-eabd89-parallel-certification): Proposed bounded design: stdlib thread pool, positive --jobs bound default min(selected scenarios, max(1, available CPUs // 2)); canonical scenario ordering and retained last-in-matrix QEMU command; compare every guest mkfs/mount/log/cache evidence literally using a fixed guest-private mountpoint; cancel queued work on failure and join running guests before workspace cleanup or record write. Baseline: all 10 manual self-checks and just check passed, tasks check has zero errors/warnings. Awaiting design approval before implementation.
- 2026-09-11T12:08:02Z (feat/atoms-eabd89-parallel-certification): parked (waiting on user): Approve the bounded parallel-guest design presented in chat, then implement and verify in .worktrees/atoms-eabd89.
- 2026-09-11T12:17:06Z (feat/atoms-eabd89-parallel-certification): User approved the bounded design on 2026-09-11. Implemented concurrent guests, literal harness agreement, canonical aggregation, fixed private mountpoint, and serialized output. Manual self-check passes 13 checks; mutation checks caught missing concurrency, evidence equality, queued cancellation, and console serialization. Independent review found no blocking issue; full suite is running before physical sweeps.
