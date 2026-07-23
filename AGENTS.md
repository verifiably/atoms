# Agent guide — atoms

## What this is

A recoverable filesystem effect engine — the physical durability substrate below
`nodes`. See [`README.md`](README.md) for the layering and `docs/plans/` for the design.

## Status: pre-implementation

There is no production code yet. The only authority is the design under `docs/plans/`,
carried verbatim from science. Per the design's own roadmap, do **not** write
implementation plans or production code until the owner approves the replacement
design; after approval, write and review Plan A before implementation, and Plan B only
after Plan A's interfaces settle.

## Authority order

1. [`docs/plans/2026-07-20-recoverable-fs-effect-engine-design.md`](docs/plans/2026-07-20-recoverable-fs-effect-engine-design.md)
   — the design contract under review. When a future `STANDARD`/spec supersedes it, update this note.
2. [`docs/plans/2026-07-20-recoverable-fs-effect-engine-implementation.md`](docs/plans/2026-07-20-recoverable-fs-effect-engine-implementation.md)
   — the delivery roadmap and approval gate.

These are dated historical/founding records. Treat their `Package: science` framing as
rationale from the pre-extraction context, not as the current package boundary.

## Conventions

- Fail early; no silent fallbacks.
- Composition over inheritance.
- Filepaths in docs use `~/d/atoms/...`.
- No AI-attribution trailers in commit messages, PRs, or comments.
