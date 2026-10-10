# Plan A roadmap

The [authority design](2026-07-23-recoverable-fs-effect-engine-design.md)'s §14
decomposes Plan A into nine sub-plans, A1–A9, each with its own reviewed design or plan
in this directory. This file is the reader's map of what exists.

## Status

Plan A is substantially implemented: A1–A8b are complete, and A9 — the macOS backend —
remains.

- **Authority design:** [`docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md)
  — standalone `atoms` engine and SQLite-in-WAL metadata store; its originally deferred
  Beliefs adoption first landed at the composition root and family adapters, followed by
  the holdings slice; Beliefs' adoption ledger records both.
- **A1 — core model (implemented):** [`docs/plans/2026-07-23-plan-a1-core-model.md`](2026-07-23-plan-a1-core-model.md)
- **A2 — compilation validation (implemented):** [`docs/plans/2026-07-28-plan-a2-compilation-validation.md`](2026-07-28-plan-a2-compilation-validation.md)
  — pure filesystem-independent `CompiledSpec` proof; A4 still owns project/root approval and must
  produce `ProjectApprovedSpec` before A5–A8.
- **A3 — executable recovery reference model (implemented):**
  [`docs/plans/2026-07-28-a3-recovery-reference-model-design.md`](2026-07-28-a3-recovery-reference-model-design.md)
  — pure production recovery authority through `build_recovery_snapshot`, `classify_recovery`,
  `authorize_recovery_step`, `reduce_recovery_plan_prefix`, and `apply_recovery_plan`; implementation plan:
  [`docs/plans/2026-07-28-plan-a3-recovery-reference-model.md`](2026-07-28-plan-a3-recovery-reference-model.md).
- **A4 — capability backend, volume binding, and project approval (implemented):**
  [`docs/plans/2026-07-29-a4a-capability-backend-design.md`](2026-07-29-a4a-capability-backend-design.md),
  [`docs/plans/2026-07-30-a4b1-path-resolution-design.md`](2026-07-30-a4b1-path-resolution-design.md),
  [`docs/plans/2026-07-31-a4b2-project-approval-design.md`](2026-07-31-a4b2-project-approval-design.md)
  — the probed capability backend, anchored path resolution, and the `ProjectApprovedSpec` proof
  with its approved topology and scratch binding.
- **A5 — durable metadata store and recovery lease (implemented):**
  [`docs/plans/2026-07-31-a5a-metadata-store-design.md`](2026-07-31-a5a-metadata-store-design.md),
  [`docs/plans/2026-08-02-a5b-recovery-lease-design.md`](2026-08-02-a5b-recovery-lease-design.md)
  — the SQLite-in-WAL store as a mechanism, composed into the recovery lease, admission, and
  preparation.
- **A6 — coherent capture and the observation mechanism (implemented):**
  [`docs/plans/2026-08-07-a6-coherent-capture-design.md`](2026-08-07-a6-coherent-capture-design.md)
  — the descriptor table, the observation pass, and preimage capture into the workspace staging
  directory.
- **A7a — execution substrate (implemented):**
  [`docs/plans/2026-08-13-a7-effect-recovery-execution-design.md`](2026-08-13-a7-effect-recovery-execution-design.md)
  — the audited facade, spec/schema v2, `AssemblyHalt`, the tamper-evident chain, and the root and intent commands.
- **A7b — effect and recovery executor (implemented):**
  [`docs/plans/2026-08-13-plan-a7b-executor.md`](2026-08-13-plan-a7b-executor.md)
  — five forward effects, A3-authorized recovery, chain reconciliation, `run_transaction`, and
  fresh-process crash convergence.
- **A8a — synthetic exerciser and persistence-cut model (implemented):**
  [`docs/plans/2026-08-14-a8-persistence-cut-and-certification-design.md`](2026-08-14-a8-persistence-cut-and-certification-design.md),
  [`docs/plans/2026-08-14-plan-a8a-cut-model.md`](2026-08-14-plan-a8a-cut-model.md)
  — the data-declared scenario library, the record–reconstruct–recover cut model, the A3 agreement
  matrix over in-process and subprocess placements with the SIGKILL extension, and the five sabotage
  arms, all in `python/tests/`.
- **A8b — durability certification (implemented):**
  [`docs/plans/2026-08-14-plan-a8b-certification.md`](2026-08-14-plan-a8b-certification.md)
  — the ext4 feature-mask resolver, QEMU + dm-log-writes certification harness, canonical
  certification record, and singleton `CERTIFIED_ALLOWLIST`; it matches the certified
  configuration/storage tuple exactly and every other tuple fails closed. A9 remains unimplemented.
- Historical (superseded): the science-framed [`2026-07-20-*`](2026-07-20-recoverable-fs-effect-engine-design.md)
  design + roadmap, retained as the record of the review that hardened the effect/recovery contracts.

A4a, A5a, and A6 write only to engine-owned paths under `metadata_root`; A7a also writes engine
bookkeeping at the reserved `.#~chain/` leaf, and A7b executes approved effects against project paths.
A8 adds no new transaction writer; it drives the existing public commands and guarded lease.

Beliefs' adoption of atoms is governed by Beliefs' adoption ledger, not by this roadmap.
