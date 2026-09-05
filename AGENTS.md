# Agent guide — atoms

## What this is

A recoverable filesystem effect engine — the physical durability substrate below
`nodes`. See [`README.md`](README.md) for the layering and `docs/plans/` for the design.

## Status: Plan A in progress

The authority design under `docs/plans/` is approved. Its roadmap (§14) decomposes Plan A
into nine sub-plans; each gets its own reviewed plan document before implementation, and
Plan B is written only after Plan A's interfaces settle.

- **A1 — core model: implemented.** `python/src/atoms/core/` holds the transaction model,
  effect variants, capability vocabulary, scratch grammar, and canonical encode/decode.
  Stdlib only, no filesystem or SQLite dependency.
- **A2 — compilation validation: implemented.** `compile_spec` in `atoms/core/compiler.py` produces
  the pure, filesystem-independent first-stage `CompiledSpec` proof; `paths.py` and `timeline.py`
  support it. A4b must produce the distinct `ProjectApprovedSpec` before A5–A8.
- **A3 — executable recovery reference model: implemented.** The pure production authority exposes
  `build_recovery_snapshot`, `classify_recovery`, `authorize_recovery_step`,
  `reduce_recovery_plan_prefix`, and `apply_recovery_plan` with the closed recovery model, fresh-step
  authorization, and abstract reducer. A5–A8 are implemented; A9 remains unimplemented.
- **A4a — capability backend and project volume binding: implemented on 2026-07-30.**
  `python/src/atoms/fs/`
  holds the `Backend` protocol and its Linux implementation, `ctypes` bindings for `openat2` and
  `renameat2`, mount-identity and durability-configuration resolution, the §5.5 bootstrap under an
  explicit `HeldProjectLock`, the empirical capability probe, and `bind_project_volume`.
  `CERTIFIED_ALLOWLIST` contains the singleton ext4 tuple crash-certified by A8b; every other
  configuration still fails closed.
- **A4b — rooted project approval: implemented on 2026-07-31.** A4b-1 owns
  the resolution mechanism in `atoms/fs/resolve.py` and `atoms/fs/lookup.py` — `PathResolver`,
  lookup-constraint reading, real-filesystem limits, containment, and mount membership — and sees no
  `CompiledSpec`.
  A4b-2 owns the judgment in `atoms/fs/approval.py`, `atoms/fs/judgment.py`, and
  `atoms/fs/topology.py`: `approve_for_project`, `ProjectApprovedSpec`, and ledger entries #2,
  #3 (its part), #4, #5, #6, #10, #11, #16, and #20 are discharged. The factory half of #9
  is complete; every A5–A7 entry point enforces it, and A8 added no production entry point.
  It admits #21, the txid binding, owned by A5.
  A4b-1 approves only non-casefold ext4; XFS, Btrfs, and casefold directories fail closed.
- **A5 — durable metadata store and recovery lease: A5a implemented on 2026-08-01, A5b implemented on
  2026-08-02.** `python/src/atoms/store/` holds the SQLite-WAL store as a mechanism — creation and
  reopen under the verified `metadata_root`, the pinned connection profile, the schema, typed
  record read/write, guarded blob promotion of both preimages and planned postimages bound to the
  COMMIT that references them, and per-txid workspaces. A5b composes it into the recovery-resolve
  lease, discharging #7, #18, #21, #22, and #23; A7b discharged the executor halves it left open.
  Its design is [`docs/plans/2026-08-02-a5b-recovery-lease-design.md`](docs/plans/2026-08-02-a5b-recovery-lease-design.md):
  a new `atoms/coordinator/` package holding the lease, the admission gate, preparation, and A3
  transition persistence. A7b replaces the former build-stage trap with pinned recovery resolution.
- **A6 — coherent capture and the observation mechanism: implemented on 2026-08-07.**
  `python/src/atoms/fs/observe.py` holds `Observation.observe`, the coherent single-descriptor read of
  a held file, directory, or symlink used throughout capture. `python/src/atoms/coordinator/` gains
  `descriptors.py` (`DescriptorTable`, walking guarded traversal from the approved topology and
  re-validating identity, constraints, and mount before handing a descriptor down) and `capture.py`
  (absence inference over both §6 branches, preimage streaming into workspace staging, flush, and the
  manifest `prepare_transaction` consumes). Its design is
  [`docs/plans/2026-08-07-a6-coherent-capture-design.md`](docs/plans/2026-08-07-a6-coherent-capture-design.md).
  A7b discharges the executor halves of ledger entries #1, #3, #13, and #19.
- **A7a — execution substrate: implemented on 2026-08-13.** Its design is
  [`docs/plans/2026-08-13-a7-effect-recovery-execution-design.md`](docs/plans/2026-08-13-a7-effect-recovery-execution-design.md):
  the audited facade, spec/schema v2, `AssemblyHalt`, the tamper-evident chain, and the root and intent commands.
- **A7b — effect and recovery executor: implemented on 2026-08-14.** The coordinator now owns
  the five forward effects, A3-authorized recovery mutations, registration/settlement reconciliation,
  pinned recovery resolution, `run_transaction`, and the fresh-process SIGKILL matrix.
- **A8a — synthetic exerciser and persistence-cut model: implemented on 2026-08-15.** Its design is
  [`docs/plans/2026-08-14-a8-persistence-cut-and-certification-design.md`](docs/plans/2026-08-14-a8-persistence-cut-and-certification-design.md);
  its plan is
  [`docs/plans/2026-08-14-plan-a8a-cut-model.md`](docs/plans/2026-08-14-plan-a8a-cut-model.md).
  Test-only, all in `python/tests/`: the data-declared scenario library, the record–reconstruct–recover
  cut model, the A3 agreement matrix over in-process and subprocess placements with the SIGKILL
  extension, the directed §9.4 tuple and §9.5 identity-injected tests, and the five sabotage arms.
  A8b reuses this scenario and agreement machinery for physical certification.
- **A8b — durability certification: implemented on 2026-08-17.** The ext4 feature-mask resolver,
  QEMU + dm-log-writes harness, canonical certification record, and certified singleton
  `CERTIFIED_ALLOWLIST` are implemented. The physical nine-scenario sweep covered 916 marks and
  3,247 replay prefixes with zero violations.
- **Downstream adoption — Beliefs:** its composition-root corpus writes and family adapters consume
  the certified engine. The holdings slice added `read_path_state` and
  `TransactionOutcome.final_states` from remote `atoms/main` `038513f`, then merged into Beliefs'
  `main` as `35be6ff` on 2026-08-25 and was an ancestor of both its local `main` and tracked
  `origin/main` at the 2026-08-30 audit. Beliefs' adoption ledger owns later consumer status;
  A9 remains this repository's first unimplemented Plan A stage.

Work lives under `python/`. Tests: `just test` runs the suite. `just check` runs the
seconds-long gate (ruff, pyright, `tasks check`); `just gate` runs both. Every recipe
records its run through `tools/tt`, the timing wrapper vendored from the ops repository;
do not call `pytest` directly. The git hooks in `.githooks/` run the same commands; a fresh
clone installs them with `git config core.hooksPath .githooks`. Before removing a
worktree, run `tt-report` (in the ops repository) so its fallback test-timing log is
harvested.
A4a, A5a, and A6 write only to engine-owned paths under `metadata_root`; A7a also writes engine
bookkeeping at the reserved `.#~chain/` leaf, and A7b executes approved effects against project paths.
A8 adds no new transaction writer; it drives the existing public commands and guarded lease.

## Authority order

1. [`docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md`](docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md)
   — **the authority design** for the standalone `atoms` engine (SQLite-in-WAL metadata store; §14 is
   the delivery roadmap: Plan A = engine + synthetic exerciser, Plan B = adoption). Approved.
   When a future `STANDARD`/spec supersedes it, update this note.
   Sub-plans under `docs/plans/` refine it; where a sub-plan and the design disagree, the design wins.
2. The 2026-07-20 design and roadmap are **superseded**, retained only as the historical record of the
   review that hardened the capability, capture, effect, materialization, and recovery contracts.
   Their `Package: science` framing is pre-extraction rationale, not the current package boundary.

## Deferred obligations

[`docs/deferred-obligation-ledger.md`](docs/deferred-obligation-ledger.md) tracks every shape a trust
boundary admits but does not itself execute, and which sub-plan owes the refusal or execution. Add an
entry in the same commit as the admission that creates it; remove one only when its owning sub-plan lands
**and** its verification suite covers it. A sub-plan is not ready for review until every entry naming it
as owner has a stated required behavior.

Every sub-plan from A3 on gets a seam review against this ledger *before* its plan is written, not
after; it is §3 of each design.

## Conventions

- Fail early; no silent fallbacks.
- Composition over inheritance.
- Filepaths in docs use `~/d/atoms/...`.
- No AI-attribution trailers in commit messages, PRs, or comments.
- The roadmap is one fact, in `python/tests/test_docs_status.py`. Landing a sub-plan means moving
  `FIRST_UNIMPLEMENTED` there; the guard then names every document that still disagrees. Do not
  add a per-sub-plan status test — four of those existed, and two ended up pinning claims the next
  sub-plan falsified.

## Tasks workflow

- Run `tasks prime` at the start of a work session and `tasks ready` before choosing work.
- Run `tasks start ID` before implementation, add concise notes as evidence changes, and close the task with a one-line result in the same commit as the work.
- Never edit `tasks/*.md` directly; use the `tasks` CLI for every task mutation.
- Before completion, run `tasks check`. Require zero errors and report every warning. Registration-only `unreachable_dep` and `cycle_unverifiable` warnings are environmental on machines without all referenced projects; resolve every other warning.
