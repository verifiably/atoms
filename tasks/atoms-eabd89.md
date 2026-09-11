---
id: atoms-eabd89
title: Parallelize the A8b certification guest sweep
status: done
priority: 2
size: m
owner: feat/atoms-eabd89-parallel-certification
created: 2026-08-30T19:56:11Z
updated: 2026-09-11T12:25:26Z
depends: []
tags: [certification, tooling]
---

Run the nine certification scenarios in N concurrent QEMU guests instead of serially. Approved design and verification: docs/plans/2026-08-30-parallel-certification-guests-note.md. Preserve per-scenario workspace isolation, require equal harness evidence across every guest, bound concurrency with --jobs, and refuse any record if a guest fails. Record schema, scenario matrix, and exact-tuple matching are unchanged. The full paired physical wall-time benchmark is deferred; implementation validation and its limits are recorded in the design.

## Notes

- 2026-09-11T12:07:45Z (feat/atoms-eabd89-parallel-certification): Design-gate inspection at 2605839: host data/log images already isolated per scenario; replay-log/initramfs build must finish before fan-out. Exact harness equality currently blocked by random guest mountpoint; propose fixed guest-private mountpoint. self-test is boot/device safety only; extend manual self-check for concurrency and refusal. No production seam or deferred obligation introduced.
- 2026-09-11T12:08:02Z (feat/atoms-eabd89-parallel-certification): Proposed bounded design: stdlib thread pool, positive --jobs bound default min(selected scenarios, max(1, available CPUs // 2)); canonical scenario ordering and retained last-in-matrix QEMU command; compare every guest mkfs/mount/log/cache evidence literally using a fixed guest-private mountpoint; cancel queued work on failure and join running guests before workspace cleanup or record write. Baseline: all 10 manual self-checks and just check passed, tasks check has zero errors/warnings. Awaiting design approval before implementation.
- 2026-09-11T12:08:02Z (feat/atoms-eabd89-parallel-certification): parked (waiting on user): Approve the bounded parallel-guest design presented in chat, then implement and verify in .worktrees/atoms-eabd89.
- 2026-09-11T12:17:06Z (feat/atoms-eabd89-parallel-certification): User approved the bounded design on 2026-09-11. Implemented concurrent guests, literal harness agreement, canonical aggregation, fixed private mountpoint, and serialized output. Manual self-check passes 13 checks; mutation checks caught missing concurrency, evidence equality, queued cancellation, and console serialization. Independent review found no blocking issue; full suite is running before physical sweeps.
- 2026-09-11T12:25:26Z (feat/atoms-eabd89-parallel-certification): Verified implementation cafad73: 13 manual self-checks, four caught mutations, and full suite 6238 passed/7 skipped in 523.35s; two concurrent KVM boot/device checks passed in 13.97s with unchanged images. Interrupted serial replay sampled 29 create + 1 replace prefixes, refused a record and cleaned all workspaces. Full paired nine-scenario benchmark deferred after observed replay rate implied hours; no measured speedup claimed. Task CLI spec-path restriction reported as tasks-f17488; design remains linked in the body.
- 2026-09-11T12:25:26Z (feat/atoms-eabd89-parallel-certification): Implemented bounded parallel certification with equal guest evidence and deterministic records; manual checks, full suite, and concurrent KVM boot checks passed; full sweep timing remains deferred.
