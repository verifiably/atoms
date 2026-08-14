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
  authorization, and abstract reducer. A5, A6, and A7a are implemented; A7b–A9 remain unimplemented.
- **A4a — capability backend and project volume binding: implemented on 2026-07-30.**
  `python/src/atoms/fs/`
  holds the `Backend` protocol and its Linux implementation, `ctypes` bindings for `openat2` and
  `renameat2`, mount-identity and durability-configuration resolution, the §5.5 bootstrap under an
  explicit `HeldProjectLock`, the empirical capability probe, and `bind_project_volume`.
  `CERTIFIED_ALLOWLIST` ships empty, so production binding refuses every volume until A8
  crash-certifies a configuration tuple.
- **A4b — rooted project approval: implemented on 2026-07-31.** A4b-1 owns
  the resolution mechanism in `atoms/fs/resolve.py` and `atoms/fs/lookup.py` — `PathResolver`,
  lookup-constraint reading, real-filesystem limits, containment, and mount membership — and sees no
  `CompiledSpec`.
  A4b-2 owns the judgment in `atoms/fs/approval.py`, `atoms/fs/judgment.py`, and
  `atoms/fs/topology.py`: `approve_for_project`, `ProjectApprovedSpec`, and ledger entries #2,
  #3 (its part), #4, #5, #6, #10, #11, #16, and #20 are discharged. The factory half of #9
  is complete; A5's and A6's entry points enforce it and A7b–A8's remain open.
  It admits #21, the txid binding, owned by A5.
  A4b-1 approves only non-casefold ext4; XFS, Btrfs, and casefold directories fail closed.
- **A5 — durable metadata store and recovery lease: A5a implemented on 2026-08-01, A5b implemented on
  2026-08-02.** `python/src/atoms/store/` holds the SQLite-WAL store as a mechanism — creation and
  reopen under the verified `metadata_root`, the pinned connection profile, the schema, typed
  record read/write, guarded blob promotion of both preimages and planned postimages bound to the
  COMMIT that references them, and per-txid workspaces. A5b composes it into the recovery-resolve
  lease, discharging #7, #18, #21, #22, and #23 while leaving #9, #12, #17, and #19 open for later stages.
  Its design is [`docs/plans/2026-08-02-a5b-recovery-lease-design.md`](docs/plans/2026-08-02-a5b-recovery-lease-design.md):
  a new `atoms/coordinator/` package holding the lease, the admission gate, preparation, and A3
  transition persistence. Because A7b has no executor yet, a live record at lease entry still raises a
  temporary build-stage trap, so #12 and #17 remain at their write and lease halves. A6 discharged the
  observation half it was waiting on.
- **A6 — coherent capture and the observation mechanism: implemented on 2026-08-07.**
  `python/src/atoms/fs/observe.py` holds `Observation.observe`, the coherent single-descriptor read of
  a held file, directory, or symlink used throughout capture. `python/src/atoms/coordinator/` gains
  `descriptors.py` (`DescriptorTable`, walking guarded traversal from the approved topology and
  re-validating identity, constraints, and mount before handing a descriptor down) and `capture.py`
  (absence inference over both §6 branches, preimage streaming into workspace staging, flush, and the
  manifest `prepare_transaction` consumes). Its design is
  [`docs/plans/2026-08-07-a6-coherent-capture-design.md`](docs/plans/2026-08-07-a6-coherent-capture-design.md).
  Ledger entries #1, #3, #13, and #19 are half-discharged; each keeps an A7b half open.
  A7a closed the design's six further §13 gaps; A7b inherits only the remaining executor work.
- **A7a — execution substrate: implemented on 2026-08-13.** Its design is
  [`docs/plans/2026-08-13-a7-effect-recovery-execution-design.md`](docs/plans/2026-08-13-a7-effect-recovery-execution-design.md):
  the audited facade, spec/schema v2, `AssemblyHalt`, the tamper-evident chain, and the root and intent commands.

Work lives under `python/` (`uv run pytest`, `uv run ruff check`, `uv run pyright`, all from
`python/`).
A4a, A5a, and A6 write only to engine-owned paths under `metadata_root`; A7a also writes engine
bookkeeping at the reserved `.#~chain/` leaf. A7b is the first stage to execute effects against project paths.

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
