# Agent guide — atoms

## What this is

A recoverable filesystem effect engine — the physical durability substrate below
`nodes`. See [`README.md`](README.md) for the layering and `docs/plans/` for the design.

## Status: Plan A in progress

The authority design under `docs/plans/` is approved. Its roadmap (§14) decomposes Plan A
into eight sub-plans; each gets its own reviewed plan document before implementation, and
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
  authorization, and abstract reducer. A5a is implemented; A5b–A8 remain unimplemented, and no
  project mutation code has landed.
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
  is complete; enforcement at the future A5–A8 entry points remains open.
  It admits #21, the txid binding, owned by A5.
  A4b-1 approves only non-casefold ext4; XFS, Btrfs, and casefold directories fail closed.
- **A5 — durable metadata store and recovery lease: A5a implemented on 2026-08-01, A5b not yet
  designed.** `python/src/atoms/store/` holds the SQLite-WAL store as a mechanism — creation and
  reopen under the verified `metadata_root`, the pinned connection profile, the schema, typed
  record read/write, guarded blob promotion of both preimages and planned postimages bound to the
  COMMIT that references them, and per-txid workspaces. It discharges ledger #22 and admits #23. A5b composes it into the recovery-resolve
  lease and owns entries #7, #9's enforcement half, #12, #17, #18, #19's part, #21, and #23.

Work lives under `python/` (`uv run pytest`, `uv run ruff check`, `uv run pyright`, all from
`python/`).
A4a mutates only engine-owned `metadata_root`, never project paths.

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

Sub-plans A3 and A4 get a seam review against this ledger *before* their plans are written, not after.

## Conventions

- Fail early; no silent fallbacks.
- Composition over inheritance.
- Filepaths in docs use `~/d/atoms/...`.
- No AI-attribution trailers in commit messages, PRs, or comments.
