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

Pre-implementation. The founding design was authored inside science (through roughly a
dozen rounds of adversarial contract review) and is carried here verbatim under
`docs/plans/` as the founding spec. No production code exists yet; the design's own
roadmap gates implementation on owner approval.

- Design: [`docs/plans/2026-07-20-recoverable-fs-effect-engine-design.md`](docs/plans/2026-07-20-recoverable-fs-effect-engine-design.md)
- Delivery roadmap (Plan A / Plan B, approval gate): [`docs/plans/2026-07-20-recoverable-fs-effect-engine-implementation.md`](docs/plans/2026-07-20-recoverable-fs-effect-engine-implementation.md)

## Scope

- **Python-first.** `nodes`' Python core is the first intended consumer. A TypeScript
  port is not planned — these are OS-syscall-level primitives with no portable JS
  equivalent, so this deliberately lives *outside* `nodes`' parity-bound kernel.
- **Progressive platform support via the capability model.** Backends probe each mount
  for semantic capabilities (`atomic_exchange`, `noclobber_transfer`, `durable_publish`,
  …) and refuse only the specific effects a mount cannot satisfy. Linux lands complete
  first; macOS fills in capability-by-capability. No all-or-nothing platform gate.

## Open questions (to resolve here, in isolation)

1. **Leaf-primitive sourcing.** Vendor/adapt syscall wrappers (`renameat2`/`openat2`
   on Linux; `renamex_np`/`F_FULLFSYNC` on macOS) rather than depend on the tiny,
   inactive third-party packages; lean on stdlib `os.replace` for the solved
   single-file case.
2. **Durable-metadata delegation.** Spike whether SQLite-in-WAL (stdlib `sqlite3`)
   should own the journal / spec / blob-index — trading a hand-rolled directory-of-JSON
   write-ahead log for a batteries-included one, at the cost of a second durability
   domain the recovery classifier must reconcile against the filesystem.
3. **Data-VCS composition (downstream).** DVC / lakeFS / dolt version data *content*
   for reproducibility; that is orthogonal to atomic mutation, but `atoms` could
   underlie how such a system safely materializes a checkout. Not a driver now.

## Relationship to science

science currently owns its filesystem effects directly and works well enough; this
extraction is deliberate and unhurried so it does not destabilize science. When `atoms`
matures, science adopts it as a dependency — directly, or transitively via `nodes`.
