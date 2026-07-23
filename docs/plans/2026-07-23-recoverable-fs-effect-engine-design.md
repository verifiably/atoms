# Recoverable filesystem effect engine — design

**Date:** 2026-07-23
**Status:** Draft for owner review — authority design for `atoms`
**Repository:** `atoms` (`~/d/atoms`) — Python-first physical durability substrate below `nodes`
**Supersedes:** the science-framed [`2026-07-20-recoverable-fs-effect-engine-design.md`](2026-07-20-recoverable-fs-effect-engine-design.md), retained as the historical, review-hardened record.

> This is the authority design for `atoms` as a standalone engine. It recenters the 2026-07-20
> design — which was written to converge science's archive/import/supersede/cohort families onto one
> executor — on the engine's own contract. Two things change from that document. First, framing: the
> engine's public boundary is a validated `TransactionSpec`; how a consumer produces one (compiling
> from a saved plan, authenticating it) is the consumer's concern, out of engine scope. Consumers are
> now `nodes` and science, adopted later (§12, §14). Second, the durable metadata store is **SQLite in
> WAL mode** (§7), replacing the hand-rolled directory-of-JSON write-ahead log. The capability
> vocabulary (§5.5), coherent capture (§6), the per-effect contracts (§9), restartable materialization
> (§10), and the recovery classifier (§8.4, §13.1) are carried near-verbatim from the reviewed design;
> they are defined over semantic filesystem capabilities and were already domain-neutral.

## 1. Decision

Build a **recoverable filesystem effect engine** — a Python library that owns durable, crash-safe
mutation of a set of filesystem paths.

A consumer describes intent as a validated, internal `TransactionSpec` over a closed set of typed
effects. A single engine exclusively owns filesystem mutation, write-ahead journaling, durability,
rollback, recovery, and scratch paths. The engine is domain-neutral: it names no knowledge kinds and
embeds no consumer's plan format. It sits **below** `nodes` (the logical knowledge substrate) and is
consumed by `nodes` and science alike (§12).

The filesystem does not provide atomic visibility across multiple paths. The engine therefore does
not claim ACID transactions. Its contract is:

> Every interruption leaves a state that can be classified from durable evidence, safely completed or
> rolled back when attributable, and otherwise halted without overwriting unknown data.

The default recovery policy is conventional write-ahead-log policy, qualified by external drift:

- no durable `COMMITTED` decision → undo every transaction-owned mutation; preserve a safely
  classified no-clobber blocker and finish with a refused outcome;
- durable `COMMITTED` decision → preserve the final state and finish cleanup;
- any unattributable state → halt without mutation and preserve evidence.

## 2. Problem

A consumer performs coordinated changes across multiple files and directories — writing a corpus of
entities and rebuilt indexes, publishing an archived tree, importing records. Between the moment
intent is frozen and the moment every change is durable, independent events intervene:

- another process (a sync client, a concurrent tool) can modify a path after intent is frozen or
  after capture;
- a Python exception can occur after some effects have landed;
- the process can be killed between a mutating syscall and its durability step;
- power loss can retain either side of a publication boundary;
- a later invocation must distinguish this transaction's work from an unrelated writer's work.

Without a single durable executor, each consumer re-implements ordering, ownership tracking, and
rollback, and a later durability layer becomes a *second* execution authority beside an in-memory
tracker. That split is the defect this engine removes: the journal and executor own execution from
the start. Shared primitives alone are not the transaction boundary.

## 3. Guarantees and non-guarantees

### 3.1 Guarantees

For a valid `TransactionSpec` that passes compilation validation (§5.4):

1. **Declared persistent surface.** Every persistent path mutation is represented by an effect and
   agrees with the spec's declared initial/final transition surface.
2. **Validated specification boundary.** The engine executes only a spec that passes compilation
   validation. Producing and authenticating that spec (e.g. re-deriving a saved plan) is the
   consumer's responsibility; the engine trusts the validated spec's provenance to the consumer and
   owns everything after it.
3. **Coherent rollback material.** A regular file's retained bytes and fingerprint come from one
   no-follow file descriptor.
4. **Optimistic concurrency without destructive check/use gaps.** Capture verifies the frozen
   precondition. Replacement, deletion, and movement then transfer the actual live directory entry
   atomically into an engine-owned destination before validating it, so a last-instant writer is
   preserved rather than overwritten or unlinked. Symmetrically, staging-to-live publication is
   validated *after* the transfer — by identity against the retained staging descriptor — so a swap of
   the engine's staging entry between check and publication is detected, not marked complete.
5. **No-clobber creation.** File, directory, and move destinations are never silently replaced.
6. **Write-ahead ownership.** Durable effect state makes every interrupted effect conservatively
   classifiable without relying on a process-local callback.
7. **Durability ordering.** Data is durable before publication; moved destinations are fsynced before
   source parents; journal intent is durable before mutation; anything the metadata store references
   is durable on the filesystem before the commit that references it (§7.3).
8. **Restartable rollback.** Restoration uses staged publication or exchange for files and symlinks,
   quarantines created directories before exact-empty removal, and reconciles attributable scratch
   survivors.
9. **Evidence-preserving halt.** An observed state outside an effect's declared state machine is not
   overwritten or deleted automatically.
10. **Mechanically enforced choke point.** Consumer code cannot directly invoke filesystem mutation
    APIs or private effect primitives.

### 3.2 Non-guarantees

- Noncooperating writers are not locked out. They can cause clean refusal or a recovery halt.
- A noncooperating writer that renames a held ancestor *directory* during a transaction can redirect a
  leaf mutation to wherever it moved the directory, including outside the declared surface. A
  pre-opened parent descriptor pins the directory object, not its location, and POSIX provides no
  atomic path-containment for an open descriptor. The engine's cooperating-process model — project
  lock plus sync-ignored metadata — prevents this among cooperating processes; an actively-relocating
  external adversary is out of scope.
- Multiple persistent paths are not instantaneously visible as one atomic update.
- Fingerprint-equivalent delete-and-recreate activity by an external writer is not distinguishable
  from the declared state; authority is defined by the frozen state contract, not inode provenance.
- A writer holding an open descriptor to an entry after atomic displacement can continue changing the
  quarantined inode. Divergence observed at the final pre-removal check preserves the quarantine
  object instead of deleting it, but there is no atomic compare-and-unlink for a regular file: a
  modification made through such a descriptor strictly between that check and the `unlink` is not
  detected. (Directory quarantines are exempt — `rmdir` atomically refuses a non-empty directory.)
  The engine cannot make that writer participate in the transaction.
- Arbitrary corruption or deletion of the metadata root (§7) is not automatically repaired.
- A `ReplaceFile` that refuses on concurrent drift briefly publishes its own postimage to the single
  live path during the atomic exchange, before exchanging the concurrent writer's entry back. A reader
  or sync client observing that path between the two exchanges can see content from a transaction that
  ultimately refused. No multi-path or committed visibility is implied.
- The engine runs only where the active platform backend supplies every capability a transaction
  requires (§5.5). On a platform or filesystem lacking a required capability, the operation refuses at
  preparation rather than degrading to a weaker executor. The supported-platform set, and which
  backends ship, is a delivery decision (§14) — not a property of the transaction model.
- Concurrent application from two hosts over a synced project tree (Dropbox and similar) is not
  supported. The advisory lock serializes cooperating processes on one host only, and transaction
  metadata is marked sync-ignored (§7) precisely because a cross-machine sync client supplies none of
  the ordering or exclusion the engine relies on.

## 4. Architecture boundary

```text
consumer builds a validated TransactionSpec
    ↓
engine coordinator acquires the project lock and resolves any active transaction
    ↓
engine prepares the durable record → executes effects → commits or rolls back
    ↓
consumer receives a durable terminal outcome
```

### 4.1 The consumer boundary

The engine's public input is a `TransactionSpec` (§5.1). A consumer owns every domain decision that
produces one — entity numbering, rendering, reference selection, destinations, cohort structure, and
whatever saved plan or authentication the consumer maintains. Saved plans, wire formats, and
re-derivation/authentication of frozen intent are **consumer concerns outside this design**; the
engine begins at a validated spec and never reaches back into consumer plan formats.

Every fingerprint in the spec, including each path's expected initial state, comes from the consumer's
frozen intent, not from a live filesystem read at spec-build time. That is what keeps a re-built spec
identical under external drift. Detecting divergence between the frozen initial state and the live
entry is capture's job (§6), not the spec builder's.

### 4.2 Coordinator, executor, and recovery engine

The coordinator acquires the project lock first, resolves any active transaction, and only then
accepts the spec for the requested operation against the settled project state. A successful recovery
may be followed by the requested operation; a recovery halt prevents execution.

Only the engine may:

- create or mutate transaction metadata;
- capture rollback contents;
- derive staging paths;
- call effect implementations;
- update execution state;
- roll back or recover an interrupted transaction.

Consumer code receives a domain-level outcome only after the transaction reaches a durable terminal
decision. It never receives mutable ownership state and never supplies commit callbacks.

### 4.3 Public versus internal contracts

The spec's declared initial/final transition surface is the engine's input contract. Effect ordering,
intermediate states, scratch paths, journal state, and recovery strategies are internal and are
frozen in the durable record only when an application begins. This keeps engine mechanics out of any
consumer's saved format and prevents observable internal ordering from becoming an accidental
compatibility contract.

## 5. Transaction model

### 5.1 `TransactionSpec`

The internal specification contains:

- a schema version;
- a consumer tag and a digest of the consumer's frozen intent (opaque to the engine);
- project-relative declared initial/final transitions;
- an ordered sequence of typed effects with stable effect IDs;
- effect dependencies where sequence alone is insufficient;
- referenced content hashes and exact modes;
- the required filesystem capability set, computed as the always-required capabilities (§5.5) plus the
  union of the semantic capabilities named by the spec's effect variants.

The compiled specification is deterministic and contains no runtime transaction ID. Preparation binds
it to a fresh transaction ID in the durable record; scratch paths derive from that ID plus the stable
effect ID. No absolute path is serialized. Resolution happens against the locked project root.

Because the transaction ID is not known at build time, disjointness between scratch names and
persistent paths cannot be established by comparing concrete names then. It is instead a **structural**
guarantee: every same-parent scratch name (file staging, delete tombstone, move anchor) occupies a
**reserved scratch grammar** — a single-component leaf name carrying a reserved sigil that a persistent
project path may never bear (`CreateDirectory` staging instead lives in the protected `work/`
namespace, §9.5). The sigil is chosen to be **invariant under the backend's name equivalence**: grammar
matching uses the same case- and Unicode-normalization semantics as the metadata-namespace identity
check (§5.4), and the sigil itself is composed only of equivalence-invariant characters — an
ASCII-punctuation-and-digit prefix with no letters (for example `.#txn.<txid>.<effect-id>.<role>`) — so
a persistent path cannot alias a scratch name through a case or NFC/NFD variant on an insensitive
volume, as a letter-bearing prefix like `.txn.` would allow. Compilation rejects any persistent effect
path matching the grammar under those semantics, so declared persistent paths and scratch are disjoint
regardless of which transaction ID is later bound.

The grammar forecloses *declared* collisions, not concrete ones: a noncooperating writer, or debris
predating this engine, can still create a concrete scratch leaf. Preparation therefore checks each
instantiated scratch path for absence after binding the ID; on a collision it **regenerates the
transaction ID** (yielding fresh scratch names), or, failing that, returns an external-state refusal —
never an internal `ProtocolError`, because an occupied scratch name is external state under the
noncooperating-writer model, not a contract violation. This absence check is advisory against a
post-check race: the authoritative guard is the `O_EXCL` creation or no-clobber transfer at the moment
each scratch object is materialized, which fails closed if the name was taken between check and use.

### 5.2 Effect variants

The closed initial effect set is:

```python
Effect = (
    ReplaceFile
    | CreateFileNoClobber
    | DeletePath
    | MoveNoClobber
    | CreateDirectory
)
```

Each effect names all persistent paths it can mutate, exact before/after fingerprints, any content
payload, and every recognized multi-path intermediate state. A move is one effect over a source and
destination, not two transitions connected through callbacks.

The initial variants do not recursively snapshot directory trees. `CreateDirectory` is
absent-to-empty-directory, `MoveNoClobber` accepts regular files, and `DeletePath` accepts file or
symlink entries. Arbitrary directory-tree movement, replacement, or deletion requires a later effect
variant with an explicit recursive content model.

Restoration is an engine operation, not a forward consumer effect. It consumes journaled state and the
same atomic publication kernel.

### 5.3 Repeated paths

The model permits a path to appear in multiple ordered effects. Validation requires a continuous
timeline: an earlier occurrence's post-state equals the next occurrence's pre-state. The record
retains the content needed to materialize every state in that timeline.

The initial rollback surface is separate from occurrence-local preconditions. This removes any
single-snapshot ambiguity that would force globally unique `rel_path` values.

### 5.4 Compilation validation

Before any metadata or blob write, validation proves:

- effect IDs are unique and ordering is complete;
- each effect's shapes are legal for its variant;
- payload hashes and modes match the declared postconditions;
- repeated-path timelines are continuous;
- each path's first and last states match the spec's declared initial/final states;
- the effect surface equals the declared transition surface exactly;
- no persistent path is omitted or undeclared;
- ancestor-resolved, leaf-retaining paths stay inside the project root;
- no persistent effect path resolves at or below the metadata root — checked by **filesystem identity,
  not spelling**: each effect path's resolved leaf and ancestors are compared against the held metadata
  root descriptor's `st_dev`/`st_ino`, so a case- or Unicode-normalization alias (an upper-case spelling
  on a case-insensitive volume — the macOS default — or an NFC/NFD variant) cannot slip an effect into
  the metadata store that a lexical prefix check would miss;
- every engine scratch name occupies the reserved scratch grammar (§5.1), and no persistent path
  matches that grammar, so scratch and persistent paths are provably disjoint independently of the
  runtime transaction ID;
- every semantic capability (§5.5) named by the spec's effects, plus the always-required
  `anchored_traversal`, `durable_publish`, and `advisory_project_lock`, is supplied by the active
  backend for the project-root volume; a missing capability refuses before any metadata or blob write.

After this boundary, internal execution code trusts the specification.

### 5.5 Filesystem capability vocabulary

The transaction model is defined over **semantic filesystem capabilities**, not platform syscalls. A
`TransactionSpec` requires a set of these capabilities; each supported backend satisfies them. A
backend is conformant if and only if it upholds the recovery tables in §8–§10 — the reference syscalls
below are informative, not the contract.

Capabilities are probed **per mount**, not per operating system: atomic exchange, no-clobber transfer,
and hard-link identity are filesystem-specific (`RENAME_EXCHANGE` is unavailable on some Linux
filesystems; macOS swap/exclusive rename is gated by `volumeSupportsSwapRenaming` /
`volumeSupportsExclusiveRenaming`). Because `anchored_traversal` refuses to cross a mount boundary,
every effect path resolves on the project-root volume, so preparation validates every required
capability against that one volume and refuses if any is missing. A path or move that would cross into
a nested or bind mount refuses at preparation rather than escaping the probed capability set.

Probing `atomic_exchange`, `noclobber_transfer`, and `identity_anchor` is empirical — it creates and
mutates test entries on the project-root volume — so it cannot be purely read-only, and a kill
mid-probe happens before any transaction record exists. The probe therefore runs inside an
engine-owned, anchored namespace `<metadata_root>/probe/` (opened from the held metadata-root
descriptor under `anchored_traversal`, never a persistent or effect path), and **every probe survivor
is unconditionally reclaimed under the project lock at lease entry, before transaction preparation
begins**. A kill during probing thus leaves only attributable, mutation-free debris in that reserved
namespace, discarded before any record is written; it can never be mistaken for transaction state.

The probe also certifies that the metadata-root volume can host the **SQLite-WAL** metadata store
(§7): working POSIX advisory locking and shared-memory-backed WAL. A volume that cannot is refused
with `CapabilityUnavailable`, like any other missing capability; there is no fallback store.

| Capability | Required guarantee | Reference backends |
| --- | --- | --- |
| `atomic_exchange` | Atomically swap two existing directory entries in one parent with no observable interval in which either name is absent or duplicated; survives process death. | Linux `renameat2(RENAME_EXCHANGE)`; macOS `renamex_np(RENAME_SWAP)` |
| `noclobber_transfer` | Atomically move a source entry onto a destination name, failing without effect if the destination exists; never replaces. | Linux `renameat2(RENAME_NOREPLACE)`; macOS `renamex_np(RENAME_EXCL)` |
| `identity_anchor` | Give a regular file a second durable name sharing the same underlying object identity, so a moved entry is distinguishable from a byte-equivalent foreign entry across process death. | POSIX `link`/`linkat`, one filesystem |
| `anchored_traversal` | Resolve and open each effect path's **parent** through guarded traversal from a durably-held project-root descriptor — refusing any ancestor symlink or mount crossing — retain those parent descriptors, and issue every capture, staging, and publication as a single-component leaf operation relative to them. Containment is thereby enforced at mutation time, not merely at validation time, and no ancestor is re-resolved by the mutating syscall — within the cooperating-process model; a held ancestor directory actively relocated by a noncooperating writer is the documented exception (§3.2). | Linux `openat2(RESOLVE_BENEATH \| RESOLVE_NO_SYMLINKS \| RESOLVE_NO_XDEV)` to open parents, then `*at` operations with single-component names; macOS `openat` + `O_NOFOLLOW_ANY` with per-component `st_dev` checks, then `renameatx_np` relative to the parent descriptors |
| `durable_publish` | Flush an entry's data to true stable storage before publication, and flush the parent directory entry after a create/rename, so both survive power loss. | Linux `fsync` on file and parent-directory descriptors; macOS `fcntl(F_FULLFSYNC)` — plain `fsync` does **not** guarantee power-loss durability on macOS |
| `nofollow_coherent_read` | Open a **regular file** without following a final symlink, then stat and read type, mode, and bytes from that one descriptor. | POSIX `O_RDONLY \| O_NOFOLLOW` + `fstat` (fails by design on a symlink leaf) |
| `symlink_fingerprint` | Capture a symlink's type, mode, and target without following it. This is `lstat`-coherent, not descriptor-coherent: the fingerprint and the subsequent atomic transfer are separate syscalls, so a symlink's identity contract is the atomically transferred entry validated against the frozen fingerprint (§6), never an open descriptor. | POSIX `lstat` + `readlink` |
| `advisory_project_lock` | A durable lock file with OS advisory locking that serializes cooperating processes on one host (never claimed as exclusion of arbitrary writers, §7.1). | POSIX `flock`/`fcntl` |

`anchored_traversal`, `durable_publish`, and `advisory_project_lock` are required by every
transaction. Each effect variant then declares the additional capabilities it consumes:

| Effect | Additional required capabilities |
| --- | --- |
| `ReplaceFile` | `atomic_exchange`, `nofollow_coherent_read` |
| `CreateFileNoClobber` | `noclobber_transfer` |
| `DeletePath` | `noclobber_transfer`; `nofollow_coherent_read` for a regular-file precondition or `symlink_fingerprint` for a symlink precondition |
| `MoveNoClobber` | `identity_anchor`, `noclobber_transfer`, `nofollow_coherent_read` |
| `CreateDirectory` | `noclobber_transfer` |

A spec's required capability set is the always-required trio plus the union over its effects. A spec
containing only `ReplaceFile` therefore does not require `identity_anchor` or `noclobber_transfer`. If
the active backend cannot supply a required capability against the project-root volume, preparation
raises `CapabilityUnavailable` before any project mutation (§11); there is no weaker fallback executor.

## 6. Path resolution and coherent capture

Containment is enforced by `anchored_traversal` (§5.5), not by validation-time resolution alone. The
engine holds a descriptor to the project root for the transaction's lifetime and, for each effect
path, opens the path's **parent** through guarded traversal from that root — refusing any ancestor
symlink or mount crossing — and retains the parent descriptor. Every capture, staging, and publication
is then a single-component leaf operation issued relative to a held parent descriptor, never a
multi-component path. Passing a multi-component name to `renameat2` / `renameatx_np` would reopen the
check/use race: the kernel re-resolves intermediate components at the syscall, so a noncooperating
writer could swap one to a symlink between validation and the syscall and redirect the mutation outside
the root. Leaf names against pre-opened, guarded parent descriptors close that race, because no
ancestor is resolved at mutation time. This closes the ancestor *symlink-swap* race but not directory
*relocation*: a held descriptor pins the directory object, not its namespace location, so a
noncooperating writer that renames a held ancestor directory — even outside the root — redirects the
leaf mutation with it. POSIX offers no atomic path-containment for an open descriptor, so this is a
non-guarantee (§3.2); it does not arise among cooperating processes, which the project lock serializes.
The leaf pathname itself is retained rather than resolved, because following the final symlink would
turn a transition *of the symlink* into a transition of its target and could escape the declared
surface.

When an effect path lies beneath an ancestor that an earlier `CreateDirectory` effect will create, its
parent does not exist at capture time and cannot be opened. Absence is then captured at the **first
missing component**: the engine opens the deepest existing ancestor through guarded traversal and
confirms, with a no-follow lookup relative to that descriptor, that the first missing component is
absent. The compiler orders ancestor creation outer-to-inner (§5.4, §9.5), so by the time such an
effect executes, each `CreateDirectory` that produced one of its ancestors has already retained a
descriptor to the directory it published and handed it down as the descendant's parent descriptor.
Descendant effects therefore mutate relative to a descriptor the engine itself created, never by
re-resolving the ancestor chain from the root.

For a regular file:

1. open with `O_RDONLY | O_NOFOLLOW`;
2. obtain type and mode with `fstat`;
3. stream bytes from that same descriptor into a per-transaction preparation-staging file (§7),
   computing the content hash over the stream so the retained bytes and fingerprint still derive from
   one descriptor (Guarantee 3);
4. leave the completed staging file in the preparation namespace; capture never holds an entire file
   body in memory. Each completed staging file is promoted to its content-addressed blob only once the
   complete initial surface has been captured and verified (§7.3), so a partial or later-refused
   capture never leaves a promoted blob mid-surface — the staging file stays mutation-free scratch
   until then, and the immutable blob is the retained rollback material.

Directories and absence retain `lstat`-based fingerprints and no file content. A symlink precondition
uses `symlink_fingerprint` (§5.5) — `lstat` plus `readlink` — which is not descriptor-coherent: its
identity contract is the atomically transferred entry validated against the frozen fingerprint (the
destructive-operation rule below), never an open descriptor, since `O_NOFOLLOW` fails by design on a
symlink leaf. Capture refuses if the observed state does not match the effect timeline's initial
precondition.

Capture-time verification is not treated as compare-and-swap authority. For replacement, deletion, and
movement, the effect atomically transfers the actual live entry into an engine-owned staging,
tombstone, or destination path and then validates that transferred object against the frozen
occurrence-local precondition. If it differs, the engine restores it when the declared live path is
still safe or halts while preserving both entries. This closes the destructive check/use gap that an
adjacent fingerprint recheck alone cannot close.

The constructive direction is validated symmetrically: a pre-publication check of the staging object
is not trusted alone, because a noncooperating writer could swap the engine's staging entry between
that check and the publishing rename or exchange. The engine opens and retains a descriptor to the
staging object before publishing, and after the transfer verifies the live entry carries that
descriptor's identity (equal `st_dev`/`st_ino`), or its exact state where no descriptor applies, before
marking the effect `DONE`. A staging-name swap therefore surfaces as a mismatch — refused or halted —
never published as success. Rollback publication is validated the same way before `UNDONE` (§10), and
the complete declared initial surface is verified before `ROLLED_BACK` (§7.5).

## 7. Durable metadata store (SQLite-in-WAL)

The engine's durable bookkeeping — the transaction record, per-effect journal state, the blob index,
and the active-transaction pointer — lives in a single **SQLite database in WAL mode**. Blob *content
bytes*, capture staging, and effect-time staging remain files on disk, because SQLite is not a store
for large payloads and because the atomic publication kernel (§9) operates on real directory entries.
The metadata layout is:

```text
<metadata_root>/               # caller-supplied; MUST be on the same volume as every target path
├── lock                       # advisory project lock (flock), §7.1
├── atoms.db                   # SQLite (WAL): transaction, effect, blob, active rows
├── atoms.db-wal
├── atoms.db-shm
├── staging/<txid>/            # preparation-only capture scratch (pre-commit)
├── work/<txid>/               # effect-time engine staging (e.g. directory builds, §9.5)
├── probe/                     # capability-probe scratch (reclaimed at lease entry, §5.5)
└── blobs/
    └── sha256/<digest>        # immutable content-addressed blobs
```

`metadata_root` is supplied by the consumer and **must** resolve onto the same volume as every effect
path. Because `anchored_traversal` (§5.5) refuses mount crossings, all effect paths already share the
project-root volume; requiring `metadata_root` on that same volume is what lets blob promotion and
staging publication reach live targets by atomic hard-link and rename. Preparation probes the volume
once and **refuses** (`CapabilityUnavailable`) if it cannot host the required primitives — including
SQLite-WAL itself (§5.5). There is no fallback to a weaker store.

The metadata namespace is anchored exactly like effect paths (§6): the engine opens `metadata_root`
and its subdirectories from a durably-held descriptor through guarded traversal — refusing any symlink
or mount crossing — retains those descriptors, and issues every lock, staging, blob, and
database-file operation relative to a held descriptor, never a re-resolved absolute path. Without this,
a pre-existing symlink or an ancestor swap could place the database on another path or volume while
effects still mutate the intended project, and fresh-process recovery could then open a different store
than the interrupted run wrote. Recovery reacquires these descriptors by the same guarded traversal
before opening the database (subject to the ancestor-relocation non-guarantee, §3.2). Compilation
additionally rejects any persistent effect path at or below `metadata_root` by filesystem identity
(§5.4), so no consumer effect can target the engine's own storage.

This metadata is single-host by construction. On creation of `metadata_root`, the engine best-effort
**requests** that cross-machine sync clients ignore the directory — via the platform's Dropbox ignore
marker (Linux extended attribute `user.com.dropbox.ignored=1`; macOS `com.dropbox.ignored`, or the
File Provider `com.apple.fileprovider.ignore#P`) — so the database and blobs are not propagated
off-host. The durability model is defined against one local POSIX filesystem; a synced copy of this
directory is neither required nor trusted, and a failure to set the ignore marker does not weaken any
single-host guarantee.

### 7.1 Advisory lock and the universal recovery lease

`lock` is a persistent file held with OS advisory locking (`advisory_project_lock`, §5.5). It
serializes cooperating processes on one host and is never used as evidence that a sync client or other
arbitrary process is excluded. The project lock is a **separate flock, not a SQLite transaction lock** —
the lease holds it across a consumer's entire write phase, which a held `BEGIN EXCLUSIVE` could not
span without blocking the engine's own journal commits.

Every mutating consumer command runs inside a **recovery-resolve lease**: a context that acquires the
project lock at entry, resolves and completes or rolls back any active transaction, and *holds the lock
across the command's entire write phase*, releasing it when that write phase completes. The lock spans
resolution and mutation as one critical section, so no other cooperating process can begin a
transaction between a command's recovery step and its writes. Only one transaction may be active per
project, recorded by the singleton `active` row (§7.2). Unsupported locking or publication capabilities
cause an early refusal rather than weaker behavior. An architecture test asserts every mutator entry
point enters the lease, and a concurrency test asserts the lock is held for the whole write phase, not
merely acquired at entry (§13.5).

### 7.2 Database schema and durability

The database is opened with `journal_mode=WAL`, `synchronous=FULL`, and — on macOS — `fullfsync=1`, so
each `COMMIT` flushes the WAL to true stable storage (plain fsync is not power-loss durable on macOS,
§5.5). **A `COMMIT` is the engine's durability barrier**, replacing the numbered-generation-file fsyncs
of the earlier design. Table shapes (informative):

- `transaction(txid PRIMARY KEY, schema_version, spec_json, state, committed, …)` — one row per
  transaction; `state` is the §8.1 machine; `spec_json` is the immutable canonical `TransactionSpec`,
  written once.
- `effect(txid, effect_id, variant, journal_state, …, PRIMARY KEY(txid, effect_id))` — per-effect
  forward/reverse journal state (§8.2–§8.3).
- `blob(digest PRIMARY KEY, byte_len, refcount)` — the content-addressed blob index; the bytes live
  under `blobs/sha256/`.
- `active(singleton INTEGER PRIMARY KEY CHECK(singleton = 0), txid)` — at most one row, enforcing a
  single active transaction and naming it.

Execution state advances by updating `transaction.state` and `effect.journal_state` inside SQLite
transactions. There is no destructive-replacement hazard to hand-manage and no append-only generation
chain to validate: SQLite gives atomic, crash-consistent updates, and WAL replay on open reconstructs a
single consistent metadata state. The earlier "longest contiguous valid hash chain from generation
zero" recovery is therefore removed — that machinery existed only to give the hand-rolled JSON journal
the atomicity SQLite provides natively.

### 7.3 Preparation order and the cross-substrate durability rule

Because blob and staging bytes live on the filesystem while the record lives in the database, the one
ordering the engine must enforce by hand is: **anything the database references must be durable on the
filesystem before the COMMIT that references it.** Preparation is:

1. Coherently capture and verify the complete initial surface, streaming each captured file into
   `staging/<txid>/` (§6).
2. Promote captured staging files to content-addressed blobs (no-clobber rename from `staging/<txid>/`
   into `blobs/sha256/`) and write or verify all planned postimage blobs.
3. Flush `blobs/sha256/` **and** `staging/<txid>/`, then remove the emptied `staging/<txid>/` and flush
   its parent. Promotion is a cross-directory move: flushing only the blob directory could leave both
   the blob and its `staging/` source name durable after power loss, resurrecting "preparation-only"
   staging. After this step every blob the record will reference is durable on disk.
4. In one SQLite transaction, insert the `transaction` row as `PREPARED` with its immutable
   `spec_json`, the `effect` rows as `PENDING`, any new `blob` index rows, and the singleton `active`
   row; **COMMIT**. This single durable barrier publishes the record atomically: there is no window in
   which `active` names a transaction whose spec or referenced blobs are not also durable.
5. Begin effects.

A crash before step 4's COMMIT leaves only mutation-free orphan scratch on the filesystem
(`staging/`, `work/`, promoted blobs with no referencing row) and no committed record; recovery
reclaims the scratch and unreferenced blobs under the lock, regardless of count. A crash after it has a
durable, self-consistent record with `active` naming it — `active` can never resolve to a missing
record or a record missing its spec.

### 7.4 Per-effect durability ordering

Each effect keeps the forward ordering the recovery tables depend on, with COMMIT in place of a
generation fsync:

- **COMMIT** `effect.journal_state = STARTED` before invoking the effect;
- perform the filesystem mutation and its data/parent durability obligations (§9) — reaching true
  stable storage;
- only then **COMMIT** `effect.journal_state = DONE`.

Because the filesystem mutation is durable before the `DONE` commit, and the `STARTED` commit is
durable before the mutation, every crash leaves exactly the evidence tuple the §8.4/§9 classifier
already handles: a `STARTED` row with the live tuple at pre-state, a declared intermediate, or
post-state; a `DONE` row with the live tuple at post-state. SQLite changes *where* the journal state is
read from, not *what* the classifier decides.

### 7.5 Terminal cleanup

After `COMMITTED` is durable (`transaction.state = COMMITTED`), the engine verifies and removes
effect-owned displaced preimages and tombstones, fsyncing their parents, then clears the `active` row —
all under the lock. `ROLLED_BACK` is committed only after every transaction-owned mutation is undone,
the complete declared initial surface is verified present (except where a proved external blocker is
preserved as drift), and all rollback scratch is removed and fsynced; its outcome records either
restored initial surface or preserved external drift. A crash during committed cleanup leaves
`transaction.state = COMMITTED` with `active` still set, so fresh-process recovery finishes cleanup
without reconsidering the commit decision. Detached terminal records, `work/` survivors, and
unreferenced blobs are garbage-collected later under the lock; garbage collection is not part of
transaction correctness — interruption can only ever leave terminal records or immutable blobs.

## 8. Durable state machine

### 8.1 Transaction states

```text
PREPARED → APPLYING → APPLIED → COMMITTED
    └──────────────→ ROLLING_BACK → ROLLED_BACK

recovery classification ───────→ HALTED
```

`COMMITTED` is the only logical commit decision. It is persisted only after every effect is durable,
the persistent final surface matches, and every retained scratch object matches its exact committed
cleanup contract. The command returns success only after committed scratch cleanup, active-pointer
detachment, and their directory fsyncs complete.

A transaction may enter `ROLLING_BACK` because of a caught application failure or because recovery
finds any noncommitted active transaction. `HALTED` preserves the record, scratch objects, the last
durable commit decision (if any), and diagnostic classification. A halt after `COMMITTED` never
licenses rollback; it preserves the final state and reports incomplete cleanup.

### 8.2 Forward effect states

```text
PENDING → STARTED → DONE
```

The executor must durably persist `STARTED` (COMMIT the `effect` row) before invoking the effect. It
persists `DONE` only after the effect's data and directory-entry durability obligations complete.

These states derive execution ownership:

| Effect state | Meaning |
| --- | --- |
| `PENDING` | `NOT_WRITTEN` |
| `STARTED` | `MAY_HAVE_WRITTEN` |
| `DONE` | `WRITTEN` |

An in-memory view may expose these meanings for diagnostics, but it is not independent mutable
authority — the `effect` rows are.

### 8.3 Reverse effect states

Rollback visits effects in reverse order and durably records:

```text
DONE or attributable STARTED → UNDO_STARTED → UNDONE
```

`UNDO_STARTED` is persisted before restoration or removal begins. A crash during undo is classified
from the same exact path-state contract and restartable restore survivors. An already-initial path is
idempotently accepted; an unattributable path halts.

### 8.4 Recovery classification

Recovery is a two-level operation. First it reconstructs, **per path**, where that path's timeline
stands; then it classifies the single in-flight effect **jointly over all of its paths**. Its inputs
are the `transaction.state`, the per-effect `journal_state` rows, and the observed live path tuples;
because SQLite gives a single crash-consistent metadata state on open, there is no partial journal to
reconcile before classification begins. Comparing each occurrence independently against the single live
entry would misread a repeated-path timeline (§5.3), and classifying a multi-path effect (e.g.
`MoveNoClobber` over source, destination, and anchor, §9.4) per path could yield contradictory
verdicts. Journal state gates attribution: an effect can only have mutated a path once it is durably
`STARTED`.

**Per-path frontier.** For each path the engine gathers that path's ordered occurrences with their
durable journal states; the direction depends on the transaction state (§8.1):

- Not yet `ROLLING_BACK` (`APPLYING`/`APPLIED`): occurrence states are monotonic forward — an
  occurrence reaches `DONE` before the next `STARTED` — so the **forward frontier** F is the last
  `DONE` occurrence. Everything after F is `PENDING` except at most one in-flight `STARTED`. The
  expected live state is F's post-state when the occurrence after F is `PENDING` (equivalently that
  occurrence's pre-state, and the timeline's initial state when no occurrence is `DONE`), or, when that
  occurrence is `STARTED`, any of {F's post-state, the variant's declared intermediate, the
  occurrence's post-state}.
- Already `ROLLING_BACK`: occurrences carry forward `STARTED`/`DONE` not yet undone and reverse
  `UNDO_STARTED`/`UNDONE` (§8.3). Rollback runs in reverse order, so the **reverse frontier** R is the
  *latest* occurrence still applied or in flight — the last one in {`DONE`, `STARTED`,
  `UNDO_STARTED`}; `UNDONE` occurrences are already reversed, and never-executed `PENDING` occurrences
  are ignored. The expected live state is R's post-state when R is `DONE` (not yet undone); the forward
  variant tuple {pre-state, intermediate, post-state} when R is a forward `STARTED` effect the failure
  interrupted; and {post-state, the variant's declared reverse intermediate, pre-state} when R is
  `UNDO_STARTED`. A path whose occurrences are all `UNDONE` or `PENDING` is at its initial state and is
  idempotently accepted.

**Joint effect classification.** The frontiers identify the transaction's single in-flight effect — a
forward `STARTED` (whether `APPLYING`, or a failure-interrupted `STARTED` under `ROLLING_BACK`), or a
reverse `UNDO_STARTED`. That effect is classified once over the union of its persistent and scratch
paths, using the variant's tuple rule, and the one decision applies to every path it owns:

- exact pre-state (forward) or restored pre-state (reverse), no engine scratch survivor → it did not
  land / was fully undone;
- exact post-state → it landed (subject to the fingerprint-equivalent-recreation non-guarantee);
- a variant-declared intermediate, including an unvalidated displaced entry → apply that variant's
  settlement rule;
- a no-clobber blocker whose variant-specific joint tuple proves it did not land → preserve it and
  record a refused outcome;
- anything else → halt.

Two frontier cases need no in-flight effect: a `PENDING` frontier whose live state is *not* F's
post-state is a concurrent external write — never transaction-owned, never deleted on rollback —
preserved as drift with a refused outcome; a fully `DONE` (or fully `UNDONE`) path whose live entry
deviates from the expected frontier state halts.

A `PREPARED` transaction — every effect `PENDING`, `active` published but nothing `STARTED` — mutates
no project path, so rollback has nothing to undo. But because `active` references it, recovery durably
records `ROLLED_BACK`, then clears `active` and reclaims per §7.5, so no crash can leave a dangling
pointer.

Recovery classifies every path's whole timeline and every in-flight effect's joint tuple before
performing any mutation, so an early repair cannot destroy evidence needed to recognize a later
conflict.

No durable `COMMITTED` record means undo transaction-owned effects even when all forward effects appear
complete. This normally restores the initial surface; a proved no-clobber blocker is preserved as
external drift. A durable `COMMITTED` record means preserve the final surface and finish cleanup.

## 9. Effect contracts

Effect contracts below are written in terms of the semantic capabilities of §5.5. Where a step names a
syscall (`renameat2(RENAME_EXCHANGE)`, hard link, `fsync`), that names the Linux reference backend; a
conformant backend on another platform substitutes its own primitive for the same capability and must
satisfy the identical recovery table. Every operation runs against the effect's held parent descriptors
under `anchored_traversal` (§5.5); "same-directory" below means same-parent relative to a held parent
descriptor with single-component leaf names, not a re-resolved absolute path. "Persist `STARTED`/`DONE`"
means COMMIT the corresponding `effect.journal_state` (§7.4).

### 9.1 `ReplaceFile`

Requires an existing regular-file precondition and exact file postcondition. Persist `STARTED`, then
create a same-directory staging file by exclusive creation (`O_CREAT | O_EXCL`) and **retain that
creating descriptor**; write the bytes through it, set the exact mode, and fsync it. The staging file
is never reopened by name, so the descriptor the engine trusts for identity is provably the object it
created — a foreign object swapped onto the staging name cannot become the retained identity, because
the engine never resolves that name again. Use `renameat2(RENAME_EXCHANGE)` to atomically swap the
staging and live entries. The live path now holds the complete postimage and the staging path preserves
the exact entry that was displaced.

Verify the live entry carries the retained staging descriptor's identity **and matches the exact
regular-file postcondition (declared bytes and mode) read through that descriptor** — proving the
exchange published our postimage, not a foreign object swapped onto the staging name between fsync and
the exchange. Identity alone is insufficient: a noncooperating writer could open the engine-created
staging inode by name and mutate its bytes or mode without changing its identity, so an identity-only
check would mark `DONE` for the wrong postimage. Also validate the displaced entry against the expected
precondition. On all matching, fsync the parent and mark the effect `DONE`; retain the displaced
preimage until the terminal decision. On a mismatch, exchange it back if the live path still matches our
postimage, fsync, and refuse. If the live path also changed, halt with both entries preserved.

A crash at any point leaves a classifiable tuple of live path plus stable staging path. Rollback
exchanges the retained preimage back when the live postimage is still authoritative. Committed cleanup
removes the displaced entry only if it still matches the validated preimage; divergence is preserved
and reported rather than deleted.

### 9.2 `CreateFileNoClobber`

Requires absence before and an exact regular-file postcondition. Persist `STARTED`, create the staging
file by exclusive creation (`O_CREAT | O_EXCL`) and **retain that creating descriptor**, write its
bytes and set the exact mode with `fchmod` through that descriptor — never relying on the `O_CREAT`
mode, which `umask` perturbs — then fsync the descriptor, all without reopening it by name. Publish
using `renameat2(RENAME_NOREPLACE)`. Because the retained descriptor is the creating descriptor, no
foreign object can be swapped onto the staging name and adopted as the trusted identity. After the
rename, verify the live destination carries that descriptor's identity **and matches the exact
regular-file postcondition (declared bytes and mode)**, then fsync the destination parent, and only
then persist `DONE`. A staging-name swap between build and publish, or any deviation from the declared
postcondition, is thereby detected rather than published as success. Never stream bytes into the live
destination and never fall back to overwriting rename.

If another writer creates the destination first, clean up only the attributable staging object and
raise `PreconditionRefused`. An existing byte-equivalent file is not silently adopted as success for a
create effect.

For recovery from `STARTED`, a surviving attributable staging object plus any existing destination
proves our no-clobber publish did not land; so does a destination that differs from the postimage. The
engine preserves that blocker, removes only its own staging object, and rolls back earlier effects with
a refused outcome. An exact postimage with no staging survivor is classified as landed, subject to the
fingerprint-equivalent recreation non-guarantee.

The compiler rejects duplicate destinations within the transaction; a concurrent creator loses at
no-clobber publication and the transaction rolls back. There is no live reservation sentinel and no
partial-destination hard-halt.

### 9.3 `DeletePath`

Requires an exact present file or symlink precondition and absence after. Persist `STARTED`, then
atomically transfer the live entry with `renameat2(RENAME_NOREPLACE)` to an effect-derived tombstone in
the same parent. The declared path becomes absent while the removed object remains recoverable.

Validate the tombstone against the expected precondition. On mismatch, rename it back no-clobber and
refuse; if the declared path has independently reappeared, halt and preserve both. On match, fsync the
parent and mark `DONE`. Rollback renames the tombstone back no-clobber. If the declared path has
independently reappeared under a foreign entry during rollback, that no-clobber restore cannot land
without clobbering it — and the tombstone is the removed object's **only surviving name**, so deleting
it as terminal scratch (§7.5) would discard the original. Recovery therefore halts with both the live
blocker and the tombstone preserved, so the original survives as evidence rather than being destroyed,
symmetric to the reappeared-source move case (§9.4). Committed cleanup deletes the tombstone only after
the durable commit decision and only while it still matches the validated preimage.

Removal of an engine-created directory during rollback remains a separate exact-empty-directory
operation; it is not compiled as a general forward `DeletePath`.

### 9.4 `MoveNoClobber`

One effect owns both source and destination. Its pre-state is `(source=present, destination=absent)`;
its post-state is `(source=absent, destination=source state)`.

Persist `STARTED`, then create an effect-derived hard-link ownership anchor to the regular file source
and fsync the **anchor's parent directory**, so the anchor name is durable before the move (fsyncing
the anchor inode alone would not persist its directory entry). Coherently validate the anchor against
the expected source precondition. Use `renameat2(RENAME_NOREPLACE)` to atomically transfer the actual
source entry to the absent destination, then require the destination and anchor to name the same inode
and still match the expected source fingerprint. The identity relation survives process death and
distinguishes the engine's moved entry from a byte-equivalent external entry.

When the syscall returns success in-process but the destination is not the anchor's entry, rename it
back no-clobber and refuse, or halt if the source independently reappeared. On an identity match, fsync
the destination parent before the source parent, then mark `DONE`. Retain the anchor until the terminal
decision. Two power-loss intermediates arise from the unflushed rename. **Before** the
destination-parent fsync, both directory updates are uncommitted, so the persistence model may persist
the source removal while dropping the destination insertion — leaving source and destination both
absent and only the anchor naming the original inode. **Between** the destination-parent and
source-parent fsyncs, the destination entry is durable while the source removal is not — leaving source,
destination, and anchor all naming the original inode. Both are normal, attributable intermediates, not
faults: the durable anchor is the surviving proof of the original inode in either.

There is no link/unlink fallback. Cross-filesystem movement or a platform without no-clobber rename
raises `CapabilityUnavailable` before preparation. Recovery of an uncommitted move first handles the
two attributable power-loss intermediates above. When source, destination, and anchor all name the
original inode (the dual-name tuple — destination-parent flush persisted, source-parent flush not), the
effect is repaired to its pre-state by removing the destination, restoring `(source present,
destination absent)`. When both persistent paths are absent but the anchor is present (the anchor-only
tuple — the source-removal update persisted while the destination-insertion update did not), the anchor
is the last surviving name of the original inode, and recovery restores the source from it (no-clobber
rename or link of the anchor to the source name), likewise reaching `(source present, destination
absent)`. Otherwise, with the source absent and the destination naming the anchor's inode, recovery
renames the anchor-owned destination back to an absent source no-clobber. The reverse move is ordered
symmetrically to the forward move — fsync the restored-source parent before the old-destination parent —
so its `UNDO_STARTED` power loss yields the same two tuples: the dual-name tuple (the restored-source
insertion persisted, the old-destination removal not), and the anchor-only tuple (the old-destination
removal persisted while the restored-source insertion did not). Each is repaired to the pre-state the
same way: remove the destination, or restore the source from the anchor.

If the source path has reappeared under a foreign entry, the move cannot be undone without clobbering
it, and removing the anchor-owned destination would destroy the last durable names of the original
inode (the destination and anchor are its only two names); recovery therefore halts with the
destination and anchor preserved, so the original contents survive as evidence rather than being
discarded by terminal scratch cleanup (§7.5). A diverged anchor or destination also halts. If the source
still names the unchanged anchor and the destination is foreign, the move did not land; preserve the
destination and return a refused outcome. A tuple in which a persistent path is present but carries a
foreign, non-anchor identity is unattributable and halts with the anchor preserved.

### 9.5 `CreateDirectory`

Requires absence before and an exact directory mode after. Persist `STARTED`, then build the staging
directory **inside the protected transaction namespace** (`work/<txid>/`, §7) rather than in the live
parent: `mkdir` it there, open and retain a descriptor to it, and set the exact mode with `fchmod`
through that descriptor. Building in the engine-owned, anchored namespace — which no cooperating process
writes to and which is reached only through held descriptors under the project lock — makes the
descriptor provably the directory the engine created: the mkdir→open swap that a live-parent staging
name would expose cannot occur, because no other writer operates in that namespace (a noncooperating
writer inside the engine's own metadata tree remains the §3.2 exception). A same-parent staging
directory could not offer this — `mkdir` returns no descriptor, so reopening its name in a shared
directory could resolve a *substituted empty directory* that the mode and emptiness checks would not
distinguish, after which `fchmod` would mutate a foreign inode and the later identity check would merely
prove that foreign inode was published. Durably flush the retained descriptor after `fchmod`: a
parent-entry flush persists the name but not the child inode's own mode metadata, so the staging
directory needs its own durability barrier (`durable_publish`, §5.5) before publication. Then publish by
**cross-directory** no-clobber rename (`renameat2(RENAME_NOREPLACE)`, `olddirfd` = the `work/`
descriptor, `newdirfd` = the live parent descriptor, single-component leaf names on both ends).
Platforms without the required directory publication primitive refuse early. This is a cross-directory
rename, so its two directory updates — the live parent gaining the entry and `work/` losing it — are
separately durable and must be ordered like the move (§9.4): **fsync the live parent first**, publishing
the directory durably, then verify identity and postcondition, then **fsync `work/`** so the
staging-name removal is durable, and only then mark `DONE`. After the rename the engine verifies the
published live directory carries the retained descriptor's identity (equal `st_dev`/`st_ino`) **and
matches the exact directory postcondition — mode and emptiness read through that descriptor** — before
proceeding to the `work/` flush and `DONE`. Because the descriptor is provably the engine-created
directory, this confirms the published entry is exactly that directory in its declared state, and
`RENAME_NOREPLACE` refuses if the live name is occupied, so publication never overwrites a concurrent
entry.

The verified descriptor is then handed to any descendant effect as that descendant's parent descriptor
(§6), threading engine-verified descriptors inward without re-resolving ancestors. On publication the
engine **rebinds the descriptor's audit provenance** (§13.5) from its `work/` scratch path to the
verified live path: the same descriptor now designates a live directory, so descendant `openat` /
`mkdirat` operations issued relative to it must be audited against live declared paths, not attributed
to engine scratch — otherwise an undeclared live descendant mutation could pass as a scratch write.
Because descriptors cannot span a crash, fresh-process recovery reacquires each parent descriptor by
guarded traversal from the root before resuming (subject to the ancestor-relocation non-guarantee,
§3.2).

Recovery distinguishes publication from a blocker by inode identity, because the ordered cross-directory
flush has an intermediate where the live directory is durable while the `work/` staging name is not yet
durably removed. A surviving `work/` staging directory whose inode is the **same** as the live
directory's means the rename published our inode but its `work/` old-name removal was not yet durable
(the intermediate between the live-parent and `work/` flushes); recovery removes the stale `work/` entry
and treats the effect as landed, subject to the transaction's forward/rollback decision. A surviving
`work/` staging directory together with a live directory that is a **different** (foreign) inode proves
publication did not land: the engine preserves the live blocker, removes its own `work/` staging, and
finishes rollback with a refused outcome. The persistence-cut model generates the same-inode
intermediate and asserts recovery does not misread it as a blocker.

Missing ancestors compile as explicit outer-to-inner effects. Rollback removes them inner-to-outer by
atomically quarantining each directory, validating its exact mode and emptiness, and then using `rmdir`
on the quarantine. A concurrent child makes `rmdir` refuse; the engine restores the quarantined
directory when the live name remains absent or halts while preserving both.

## 10. Restartable materialization

Rollback materializes through stable, effect-derived staging paths:

- present file or symlink preimage over an expected live postimage: stage the exact object; exchange it
  with the live entry; validate and retain the displaced postimage until the rollback decision is
  durable;
- present preimage over an absent live path: publish the staged object no-clobber (normally the
  effect's retained delete tombstone already provides this object);
- absent file/symlink preimage: atomically quarantine the live entry, validate it against the effect's
  postcondition, then delete and fsync the quarantine before marking the undo complete; a write through
  a pre-existing descriptor strictly between validation and delete is undetectable (§3.2);
- absent directory preimage: atomically quarantine the live directory, validate its expected mode and
  emptiness, then use `rmdir` on the quarantine; atomic nonempty refusal preserves any concurrently
  added child.

At entry, restoration classifies a surviving staging or tombstone object:

- complete and attributable → publish it;
- attributable file prefix → remove, recreate, and publish;
- attributable wrong-mode staging directory → finish its mode and publish;
- validated displaced preimage or delete tombstone → restore by no-clobber rename or exchange;
- unvalidated displaced entry → restore it if the live path is still the engine's exact postimage;
- undo quarantine for an absent preimage → validate, delete, and fsync it before `UNDONE`;
- foreign object or changed live target → halt and preserve evidence.

The authority check precedes every staging-object mutation, including prefix removal. Atomic live
publication means a crash during restoration leaves the target at the effect state or restored state,
never at an in-place partial state.

Reverse publication is validated symmetrically to the forward direction (§6): after restoring an object
to a live path, the engine verifies that path holds the expected restored state — by identity against
the retained preimage or staging descriptor, or exact fingerprint — before marking the effect `UNDONE`.
A foreign object published onto a restored name is refused or halted, never marked `UNDONE`, so a
staging swap during rollback cannot be laundered into a false restoration.

## 11. Refusal and failure semantics

- **`PreconditionRefused`** — concurrent drift was detected either at capture or by validating an
  atomically displaced entry. The executor returns this refusal only after the current effect and every
  earlier effect have been restored; inability to prove that restoration becomes `TransactionHalted`.
- **`CapabilityUnavailable`** — required semantics cannot be supplied. Raised during preparation before
  project mutation.
- **`TransactionHalted`** — the journal, live state, or rollback survivor is unattributable. The engine
  preserves the active record and evidence.
- **`ProtocolError`** — an internal contract was violated. Attempt rollback if mutation may have begun;
  retain the record if a complete rollback cannot be proved.

Caught process-local failures, including cancellation, `KeyboardInterrupt`, and `SystemExit`, enter
rollback before being re-raised. `SIGKILL`, power loss, and machine failure do not unwind Python and
are exercised only through fresh-process recovery tests.

Recovery and rollback diagnostics identify the transaction, effect, paths, journal state, expected
states, observed states, and the non-mutating operator action required next. There is no silent
fallback or automatic discharge of a halt.

## 12. Consumers

The engine's only public boundary is a validated `TransactionSpec` and the recovery-resolve lease. It
ships with no consumer-specific compilation baked in. Two vehicles exercise and adopt it.

### 12.1 Synthetic exerciser (the vertical slice)

Plan A (§14) ships, alongside the engine, a small in-repo **exerciser**: a self-contained harness that
builds `TransactionSpec`s directly — no external consumer — driving all five effect variants, repeated
paths, capability probing, and the full interruption/recovery matrix (§13). It is the engine's first
"consumer," chosen so the engine matures in isolation with a fast feedback loop and no cross-repo
dependency. To keep it from drifting from real needs, its effect surface is shaped to cover the
documented shapes of the deferred real consumers (§12.2) — corpus writes and archive/import moves —
without importing either.

### 12.2 Deferred production consumers

Real adoption is Plan B (§14), one consumer at a time, each on its own clock and each responsible for
producing and authenticating its own frozen intent (§4.1):

- **`nodes` corpus-write** — writing a corpus of entity files and rebuilt indexes durably. The likely
  first production consumer: greenfield, no saved-plan authentication legacy.
- **science** — the archive, import, cohort-import, and supersede families, whose mutation shapes the
  engine was originally derived from. Each family keeps its planner and saved-plan authentication and
  compiles, after authentication, into a `TransactionSpec`. This adoption, and the deletion of
  science's existing execution dialects, is science's own hard cut — outside this repo's authority.

No consumer adoption may redefine the engine protocol, add a feature flag, or introduce a runtime
transaction-dialect choice.

## 13. Verification strategy

### 13.1 Executable reference model

Recovery is specified as a **top-level transaction classifier** with a subordinate variant classifier:

```text
transaction: (TransactionSpec, transaction state, per-effect journal states, observed path tuples)
                 → per-effect recovery decisions
variant:     (variant, effect journal state, effect's joint persistent-and-scratch tuple)
                 → effect decision
```

The transaction state (§8.1) is a required input: `APPLIED` and `COMMITTED` can present identical `DONE`
effects and the same final surface yet demand opposite decisions — rollback versus committed cleanup —
so the classifier cannot be a pure function of effect states and observed tuples alone. The transaction
classifier reconstructs each path's frontier (forward or reverse per that state) and invokes the
variant classifier once per in-flight effect over its joint tuple (§8.4). Table and property tests cover
every variant, forward/reverse state, named intermediate, and unattributable state. Generated valid
effect sequences prove:

- path timelines are continuous;
- recovery reconstructs each path's timeline frontier, forward and reverse, and never misreads a
  mid-timeline live state as external drift;
- a multi-path effect is decided once over its joint tuple, never contradictorily per path;
- uncommitted recovery removes every attributable mutation and preserves proved external blockers;
- committed recovery retains the final surface;
- recovery never mutates an unattributable state;
- a second recovery pass is idempotent.

This model is the normative recovery specification.

### 13.2 Real-filesystem effect tests

Run each implementation with faults before and after journal commits, atomic transfer, displaced-entry
validation, settlement, file fsync, parent fsync, and terminal scratch cleanup. Verify bytes, modes,
types, symlink targets, directory entries, and ordering.

Python exception tests exercise caught rollback. Subprocess `SIGKILL` tests exercise true hard halt and
fresh-process recovery. They do not share assertions that depend on Python unwinding.

`SIGKILL` exercises process death but leaves the kernel page cache intact, so it does not test
power-loss durability: a real power loss can discard or reorder writes not covered by a modeled barrier.
A deterministic **persistence-cut model** therefore drives durability testing — a backend that, at each
modeled barrier, drops or reorders writes not yet made durable and replays recovery from the surviving
state.

Metadata durability itself is SQLite's, under `synchronous=FULL` (and macOS `fullfsync`), so the
persistence-cut model does **not** re-verify the journal's internal atomicity. It targets the
**cross-substrate ordering** the design depends on:

- the preparation rule that every referenced blob is durable on disk before the `PREPARED` COMMIT
  (blob and `staging/` directories flushed and `staging/` removed first, §7.3);
- per-effect ordering: the filesystem mutation and its fsyncs are durable before the `DONE` COMMIT, and
  the `STARTED` COMMIT is durable before the mutation (§7.4);
- moved-destination-before-source-parent fsync (§9.4);
- the `CreateDirectory` cross-directory publication — live-parent flush before the `work/` flush, with
  the same-inode `work/` survivor classified as landed rather than a blocker (§9.5);
- the `COMMITTED` decision.

For moves it must generate, in both the forward and the reverse (`UNDO_STARTED`) directions, the two
attributable power-loss tuples of §9.4: the **dual-name** tuple (the insertion update persisted but the
removal update did not — source, destination, and anchor all naming the original inode) and the
**anchor-only** tuple (the removal update persisted but the insertion update did not — both persistent
paths absent, only the durable anchor surviving). It asserts recovery repairs the dual-name tuple by
removing the destination and the anchor-only tuple by restoring the source from the anchor, each
reaching the pre-state. Where feasible it is complemented by VM or block-device crash testing.

### 13.3 Spec and compiler conformance

Build a spec, validate it twice, and require identical canonical output. Reject missing effects, extra
effects, invalid ordering, malformed timelines, payload/mode mismatches, path escapes, and initial/final
surface divergence. Apply the same case- and Unicode (NFC/NFD) alias tests to **both** name-equivalence
surfaces on case- and normalization-insensitive volumes: the metadata namespace (a persistent path
spelled as an upper-case or NFC/NFD variant of `metadata_root`, which the identity-based check of §5.4
must reject where a lexical prefix check would not) and the reserved scratch grammar (a persistent path
aliasing the scratch sigil through a case or normalization variant, which the equivalence-aware grammar
match of §5.1 must reject). A letter-free sigil and equivalence-aware matching must both pass these
tests.

### 13.4 End-to-end recovery

The synthetic exerciser (and later each real consumer) covers clean commit, caught rollback, kill during
each effect, kill during rollback, kill during a journal commit, kill after commit before cleanup, and
external drift at destructive boundaries. Restart happens in a fresh process and must converge or
preserve an explained halt.

### 13.5 Actual mutation surface

An always-on in-process interposer records successful mutating operations only after the syscall
succeeds. Every target must be:

- a declared effect path;
- an engine-derived staging path; or
- an exact metadata path (including the SQLite database files and the ignore-marker `setxattr` on the
  metadata root).

It wraps `rename`, `replace`, `unlink`, `mkdir`, `rmdir`, `symlink`, `chmod`, **`fchmod`**, `link`,
mutating `open`, the `*at` and descriptor-relative variants (`renameat2`, `openat`, `linkat`,
`fchmodat`), and `setxattr`/`fsetxattr` applied to the metadata root, so no metadata mutation escapes
the audit. Because the effects set modes with `fchmod` through retained descriptors, the interposer
resolves descriptor-relative mutations through **descriptor provenance**: it tracks the engine-issued
path each retained descriptor was opened against, attributes a mutation through that descriptor to its
declared or engine-derived target exactly as a path-based mutation, and fails the surface assertion on
an operation against a descriptor of unknown provenance. Provenance is **the descriptor's current
authorized logical alias, not merely the path it was first opened against**: when an effect republishes
a descriptor's object to a new path — `CreateDirectory` renaming its `work/` staging directory to the
live parent (§9.5) — the engine rebinds that descriptor's provenance to the verified live path, so
descendant `openat` / `mkdirat` operations issued relative to it are audited against live declared paths
rather than stale scratch, closing the gap where an undeclared live descendant mutation would pass as a
scratch write. An optional external trace detects future fresh `ctypes` or extension bypasses where
supported.

Targets are evaluated by the path the engine issued each operation against; the interposer cannot
observe a held descriptor's current namespace location after an external relocation, so this surface
check holds within the cooperating-process model — an ancestor directory relocated by a noncooperating
writer (§3.2) can carry a leaf mutation off-surface undetected by the interposer.

An architectural test rejects direct mutation calls and private effect imports from consumer modules. A
second architectural test asserts every mutating command entry point enters the recovery-resolve lease,
and a concurrency test asserts the project lock is held across the entire write phase, not merely
acquired at entry (§7.1).

## 14. Delivery decomposition

One design governs two implementation plans.

**Supported backends: Linux and macOS.** Both satisfy the §5.5 capability vocabulary with identical
recovery tables — Linux via `openat2`/`renameat2`/`link`/`fsync`, macOS via
`openat`+`O_NOFOLLOW_ANY`/`renamex_np`/`renameatx_np`/`link`/`fcntl(F_FULLFSYNC)`. Capabilities are
probed **per project-root volume**, not per OS, because atomic exchange, no-clobber transfer, and
hard-link identity are filesystem-specific on both platforms, and plain `fsync` is not power-loss
durable on macOS. Delivery is **progressive**: Linux lands complete first; macOS fills in
capability-by-capability, and any capability a mount cannot supply refuses only the effects that need it
(§5.5) — not an all-or-nothing platform gate. Windows is out of scope: it has no `atomic_exchange`
primitive with the same crash-recovery table and no explicit directory-entry durability barrier, so a
Windows backend would need a divergent recovery classification; a Windows operation refuses at
preparation with `CapabilityUnavailable`. A Windows backend may be added later without changing the
transaction model.

### Plan A — engine core and synthetic exerciser

1. Pure transaction/effect model and validators (§5).
2. A platform capability backend layer resolving the §5.5 vocabulary behind one interface: a Linux
   backend (`openat2` anchored traversal, `renameat2`, `fsync`) and a macOS backend
   (`openat`+`O_NOFOLLOW_ANY`, `renamex_np`/`renameatx_np`, `fcntl(F_FULLFSYNC)`), plus a per-volume
   capability probe that refuses an unsupported platform, filesystem, or SQLite-WAL-incapable volume
   before any mutation. Leaf syscall wrappers are **vendored/adapted** (not depended on) for `renameat2`
   / `openat2` on Linux and `renamex_np` / `F_FULLFSYNC` on macOS; the solved single-file case uses
   stdlib `os.replace`.
3. The SQLite-WAL metadata store (§7): schema, the project lock and universal recovery-resolve lease,
   blobs, and the preparation/per-effect commit ordering.
4. Coherent capture and restartable atomic materialization (§6, §10).
5. Five effects and the recovery executor (§8–§9).
6. Model, real-filesystem, subprocess-recovery, and persistence-cut suites, run on both backends (§13).
7. The synthetic exerciser and the end-to-end recovery matrix (§12.1, §13.4).

### Plan B — production adoption

Written only after Plan A's interfaces settle, and it must not redefine the engine protocol. One
consumer at a time:

1. `nodes` corpus-write adapter and its acceptance suite.
2. science family adapters (supersede first, then archive, then import/cohort), each keeping its
   planner and Gate-B authentication and compiling into a `TransactionSpec`, plus deletion of science's
   superseded execution dialects. This is science's hard cut, tracked in science.

The README platform-support statement lands with the first real consumer's hard cut — once the engine
actually gates that consumer's mutating commands — not before, so the README never documents behavior
that has not shipped. There is no feature flag, compatibility executor, or runtime transaction-dialect
choice.

## 15. Out of scope and future extension

This design does not add a Git commit participant, content-versioning/data-VCS integration (DVC,
lakeFS, dolt — orthogonal; the engine could later underlie such a system's safe checkout
materialization, per the README), or a general user-facing transaction API beyond `TransactionSpec`.

A later design may add a commit participant between filesystem `APPLIED` and transaction `COMMITTED`.
That participant must have its own durable decision and recovery table. It must not change consumer
adapters or reintroduce a second filesystem executor.

Directory-tree recursive effects (recursive move/replace/delete) require a later effect variant with an
explicit recursive content model (§5.2); they are not implied by the initial closed effect set.

## 16. Acceptance criteria

The architecture is complete when:

- the synthetic exerciser drives every effect variant through the engine and mutates only through the
  executor;
- every mutating entry point runs inside the recovery-resolve lease, holding the project lock across its
  whole write phase, enforced by architecture and concurrency tests;
- a `PREPARED` record is durable in SQLite before the first project mutation, with every referenced blob
  durable on disk first (§7.3);
- every effect and recovery decision conforms to the executable model (§13.1);
- caught failure undoes every transaction-owned mutation, preserving external drift as an explicit
  refusal or leaving an explained halt;
- fresh-process recovery rolls back every uncommitted transaction and preserves every committed one;
- the persistence-cut model exercises the cross-substrate ordering (§13.2) and recovery converges from
  every generated cut;
- actual persistent, scratch, and metadata mutations stay within their declared surfaces, absent a §3.2
  ancestor relocation by a noncooperating writer;
- the full engine test suite, lint (`ruff`), and type checks (`pyright`) pass on both backends.
