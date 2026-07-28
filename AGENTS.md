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
  support it. A4 must produce the distinct `ProjectApprovedSpec` before A5–A8.
- **A3 — executable recovery reference model: design under owner review.** The draft defines the pure
  production classifier, semantic plan, fresh-step authorization, and abstract reducer. No A3
  production code has landed. A4–A8: not started.

Work lives under `python/` (`uv run pytest`, `uv run ruff check`, `uv run pyright`, all from
`python/`). No code in this repository mutates a filesystem path yet; that begins at A4.

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
