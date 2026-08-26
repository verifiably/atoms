# atoms

A recoverable filesystem effect engine: the durable, crash-safe substrate that owns
atomic multi-path filesystem mutation — write-ahead journaling, rollback, and
post-crash recovery — over an OS-neutral capability vocabulary.

`atoms` is a *physical* substrate. It sits below `nodes` (the logical knowledge
substrate) and is consumed by `nodes` and by science:

```
domain profiles    science, mindful v6
logical substrate  nodes  (Node, Relation, shapes, indexes)
physical substrate atoms  (durable atomic filesystem effects)   ← this repo
```

Where `nodes` decides *what* a corpus of entities is, `atoms` guarantees that *writing*
that corpus to disk either lands completely or leaves a state that can be classified
from durable evidence and safely completed, rolled back, or halted — never a silent
half-write.

## Status

Plan A is substantially implemented: A1–A8b are complete, and A9 — the macOS
backend — remains. The engine was originally designed inside science through roughly a
dozen rounds of adversarial contract review; that review capital is carried into the
standalone authority design below. Its roadmap (§14) decomposes Plan A into nine sub-plans,
A1–A9 (A9 was added 2026-08-13 by the A7 design's banking commit, so the macOS arm of
Plan A item 6 has an owner).

The pure core (`atoms.core`) is joined under `python/` by `atoms.fs` (capability backend,
volume binding, project approval) and `atoms.store` (SQLite-in-WAL metadata store) beneath an
`atoms.coordinator` package holding the recovery lease, admission, preparation, forward executor,
and recovery executor. A7a writes engine bookkeeping at the reserved `.#~chain/` leaf; A7b executes
approved effects against project paths.

- **Authority design:** [`docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md`](docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md)
  — standalone `atoms` engine and SQLite-in-WAL metadata store; its originally deferred
  Science adoption has begun with the holdings slice described below.
- **A1 — core model (implemented):** [`docs/plans/2026-07-23-plan-a1-core-model.md`](docs/plans/2026-07-23-plan-a1-core-model.md)
- **A2 — compilation validation (implemented):** [`docs/plans/2026-07-28-plan-a2-compilation-validation.md`](docs/plans/2026-07-28-plan-a2-compilation-validation.md)
  — pure filesystem-independent `CompiledSpec` proof; A4 still owns project/root approval and must
  produce `ProjectApprovedSpec` before A5–A8.
- **A3 — executable recovery reference model (implemented):**
  [`docs/plans/2026-07-28-a3-recovery-reference-model-design.md`](docs/plans/2026-07-28-a3-recovery-reference-model-design.md)
  — pure production recovery authority through `build_recovery_snapshot`, `classify_recovery`,
  `authorize_recovery_step`, `reduce_recovery_plan_prefix`, and `apply_recovery_plan`; implementation plan:
  [`docs/plans/2026-07-28-plan-a3-recovery-reference-model.md`](docs/plans/2026-07-28-plan-a3-recovery-reference-model.md).
- **A4 — capability backend, volume binding, and project approval (implemented):**
  [`docs/plans/2026-07-29-a4a-capability-backend-design.md`](docs/plans/2026-07-29-a4a-capability-backend-design.md),
  [`docs/plans/2026-07-30-a4b1-path-resolution-design.md`](docs/plans/2026-07-30-a4b1-path-resolution-design.md),
  [`docs/plans/2026-07-31-a4b2-project-approval-design.md`](docs/plans/2026-07-31-a4b2-project-approval-design.md)
  — the probed capability backend, anchored path resolution, and the `ProjectApprovedSpec` proof
  with its approved topology and scratch binding.
- **A5 — durable metadata store and recovery lease (implemented):**
  [`docs/plans/2026-07-31-a5a-metadata-store-design.md`](docs/plans/2026-07-31-a5a-metadata-store-design.md),
  [`docs/plans/2026-08-02-a5b-recovery-lease-design.md`](docs/plans/2026-08-02-a5b-recovery-lease-design.md)
  — the SQLite-in-WAL store as a mechanism, composed into the recovery lease, admission, and
  preparation.
- **A6 — coherent capture and the observation mechanism (implemented):**
  [`docs/plans/2026-08-07-a6-coherent-capture-design.md`](docs/plans/2026-08-07-a6-coherent-capture-design.md)
  — the descriptor table, the observation pass, and preimage capture into the workspace staging
  directory.
- **A7a — execution substrate (implemented):**
  [`docs/plans/2026-08-13-a7-effect-recovery-execution-design.md`](docs/plans/2026-08-13-a7-effect-recovery-execution-design.md)
  — the audited facade, spec/schema v2, `AssemblyHalt`, the tamper-evident chain, and the root and intent commands.
- **A7b — effect and recovery executor (implemented):**
  [`docs/plans/2026-08-13-plan-a7b-executor.md`](docs/plans/2026-08-13-plan-a7b-executor.md)
  — five forward effects, A3-authorized recovery, chain reconciliation, `run_transaction`, and
  fresh-process crash convergence.
- **A8a — synthetic exerciser and persistence-cut model (implemented):**
  [`docs/plans/2026-08-14-a8-persistence-cut-and-certification-design.md`](docs/plans/2026-08-14-a8-persistence-cut-and-certification-design.md),
  [`docs/plans/2026-08-14-plan-a8a-cut-model.md`](docs/plans/2026-08-14-plan-a8a-cut-model.md)
  — the data-declared scenario library, the record–reconstruct–recover cut model, the A3 agreement
  matrix over in-process and subprocess placements with the SIGKILL extension, and the five sabotage
  arms, all in `python/tests/`.
- **A8b — durability certification (implemented):**
  [`docs/plans/2026-08-14-plan-a8b-certification.md`](docs/plans/2026-08-14-plan-a8b-certification.md)
  — the ext4 feature-mask resolver, QEMU + dm-log-writes certification harness, canonical
  certification record, and singleton `CERTIFIED_ALLOWLIST`; it matches the certified
  configuration/storage tuple exactly and every other tuple fails closed. A9 remains unimplemented.
- Historical (superseded): the science-framed [`2026-07-20-*`](docs/plans/2026-07-20-recoverable-fs-effect-engine-design.md)
  design + roadmap, retained as the record of the review that hardened the effect/recovery contracts.

## Scope

- **Python-first.** `nodes`' Python core is the first intended consumer. A TypeScript
  port is not planned — these are OS-syscall-level primitives with no portable JS
  equivalent, so this deliberately lives *outside* `nodes`' parity-bound kernel.
- **Progressive platform support via the capability model.** Backends probe each mount
  for semantic capabilities (`atomic_exchange`, `noclobber_transfer`, `durable_publish`,
  …) and refuse only the specific effects a mount cannot satisfy. Linux lands complete
  first; macOS fills in capability-by-capability. No all-or-nothing platform gate.

## Design decisions (settled in the authority design)

1. **Durable metadata → SQLite-in-WAL** (stdlib `sqlite3`, `synchronous=FULL` + macOS
   `fullfsync`): a `COMMIT` is the durability barrier and WAL replay is metadata recovery,
   replacing the hand-rolled directory-of-JSON journal. Blob *content*, staging, and work
   dirs stay on the filesystem; only the journal / spec / blob-index / active-pointer live
   in the DB. The one hand-ordered rule is cross-substrate: anything the DB references is
   filesystem-durable before the `COMMIT` that references it.
2. **Leaf-primitive sourcing:** vendor/adapt `renameat2`/`openat2` (Linux) and
   `renamex_np`/`F_FULLFSYNC` (macOS); stdlib `os.replace` for the single-file case. No
   dependency on the tiny, inactive third-party wrappers.
3. **Vertical slice = a synthetic in-repo exerciser** (Plan A); production adoption —
   `nodes` corpus-write first, then science — is deferred to Plan B.
   Science's independently governed adoption has since landed for composition-root corpus
   writes and family adapters, followed by the holdings path-read and post-state evidence slice.

## Open items (to settle in Plan A)

- **SQLite I/O layer:** whether the DB/WAL/SHM get a custom VFS (`openat`-anchored,
  `O_NOFOLLOW`, interposer-visible) or the stdlib default with verified-directory
  resolution and a bounded-surface audit (a pinned SQL profile — `temp_store=MEMORY`, no
  `ATTACH`/`VACUUM` — keeps SQLite's file surface inside the store). Durability itself
  needs no custom VFS — the stock VFS honors `PRAGMA fullfsync`. The stdlib baseline
  trusts cooperating processes not to relocate `metadata_root` mid-lease; the custom VFS
  is what closes that gap.
- **Data-VCS composition (downstream):** DVC / lakeFS / dolt version data *content* —
  orthogonal, but `atoms` could underlie safe checkout materialization. Not a driver now.

## Relationship to science

science now consumes `atoms` directly at its composition root: generic corpus writes and
its supersede, revise, retraction, and import families flow through the certified engine.
The later holdings slice uses `read_path_state` and `TransactionOutcome.final_states` for
intent-bearing store acts, mechanical capture, reduction, receipts, and dataset admission.
That slice merged into Science's local `main` as `35be6ff` on 2026-08-25; it had not been
pushed as of 2026-08-26. Further Science adoption remains governed by Science's adoption
ledger rather than this repository's Plan A roadmap.
