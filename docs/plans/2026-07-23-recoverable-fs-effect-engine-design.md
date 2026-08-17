# Recoverable filesystem effect engine — design

**Date:** 2026-07-23
**Status:** Approved — authority design for `atoms`. Plan A implementation underway; A1–A8b are
implemented (pure model and compilation, recovery reference model, capability backend, path resolution
and project approval, SQLite-WAL metadata store, recovery-resolve lease, coherent capture and the
observation mechanism; A7 adds the audited facade, chain, effect/recovery executor, and public
transaction command; A8 adds the synthetic exerciser, persistence-cut model, and durability
certification); A9 (macOS backend) remains unimplemented.
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

For a `TransactionSpec` carried by a `ProjectApprovedSpec` after both stages of §5.4:

1. **Declared persistent surface.** Every persistent path mutation is represented by an effect and
   agrees with the spec's declared initial/final transition surface.
2. **Staged specification boundary.** The engine executes only a spec whose filesystem-independent
   lexical/model rules were proved by A2 and whose concrete project/root rules were then proved by A4.
   Producing and authenticating the raw spec (e.g. re-deriving a saved plan) is the consumer's
   responsibility; neither A2 proof authenticates consumer intent nor A4 approval repairs an invalid
   consumer plan.
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
- The runtime capability probe proves functional *availability*, not power-loss *durability*. The engine
  trusts the §8–§10 recovery tables only on an allowlisted, crash-tested filesystem type (§5.5, §13.2);
  on a filesystem outside that allowlist it refuses at preparation rather than assume the barrier
  semantics hold.
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
consumer builds a TransactionSpec
    ↓ compile_spec (A2: pure lexical/model proof)
CompiledSpec
    ↓
engine coordinator acquires the project lock and resolves any active transaction
    ↓ approve_for_project (A4: rooted filesystem/project proof)
ProjectApprovedSpec
    ↓
engine prepares the durable record → executes effects → commits or rolls back
    ↓
consumer receives a durable terminal outcome
```

### 4.1 The consumer boundary

The engine's public submission input is a `TransactionSpec` (§5.1). A consumer owns every domain decision that
produces one — entity numbering, rendering, reference selection, destinations, cohort structure, and
whatever saved plan or authentication the consumer maintains. Saved plans, wire formats, and
re-derivation/authentication of frozen intent are **consumer concerns outside this design**; the
engine never reaches back into consumer plan formats. The engine first compiles the value to an A2
`CompiledSpec`, then A4 approves that proof for one held project/root context and returns a
`ProjectApprovedSpec` (§5.4). These proof values are engine-internal; a consumer does not construct or
submit either one.

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

The raw spec's declared initial/final transition surface is the consumer contract.
`CompiledSpec` and `ProjectApprovedSpec` are staged internal evidence that this contract is usable;
neither changes or merges the declared surface. Effect ordering,
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
effect ID. Distinct effect IDs must also be distinct under the portability equivalence
`NFC(casefold(NFC(id)))`: exact-string uniqueness alone is insufficient because `e1` and `E1` produce
scratch leaves that alias in an insensitive parent. No absolute path is serialized. Resolution happens
against the locked project root.

Because the transaction ID is not known at build time, disjointness between scratch names and
persistent paths cannot be established by comparing concrete names then. It is instead a **structural**
guarantee: every same-parent scratch name (file staging, delete tombstone, move anchor) occupies a
**reserved scratch grammar** — a single-component leaf name carrying a reserved sigil that a persistent
project path may never bear (`CreateDirectory` staging instead lives in the protected `work/`
namespace, §9.5). The **discriminating sigil** is the exact leaf prefix `.#~` — three ASCII punctuation bytes (dot,
hash, tilde), none of which has a case or NFC/NFD variant. A leaf is a scratch name **if and only if it
begins with `.#~`**; scratch leaves have the form `.#~<txid>.<effect-id>.<role>`, but only the
letter-free `.#~` prefix participates in classification — the `<txid>.<effect-id>.<role>` remainder may
contain hex or letters, because it is never matched against a persistent path. Because the
discriminating prefix is letter-free, no persistent path can alias it through a case or NFC/NFD variant
on an insensitive volume, as a letter-bearing sigil like `.txn.` (which folds to `.TXN.`) would allow.
Compilation rejects any persistent effect path whose leaf begins with `.#~`, under the same case- and
Unicode-normalization semantics as the metadata-namespace identity check (§5.4), so declared persistent
paths and scratch are disjoint regardless of which transaction ID is later bound.

The grammar forecloses *declared persistent/scratch* collisions, not either kind of concrete scratch
collision. A4 instantiates every effect/role scratch leaf in its actual parent and proves the complete
set pairwise distinct under that parent's real lookup policy. A collision intrinsic to those generated
names is a project-approval refusal; regenerating the transaction ID cannot fix equivalent effect IDs,
because the colliding leaves receive the same new transaction-ID prefix. A2's portability-equivalent
effect-ID uniqueness prevents the fixed NFC/casefold case, but A4's concrete proof remains mandatory
for filesystem equivalences A2 cannot know.

Separately, a noncooperating writer or debris predating this engine can occupy an otherwise distinct
concrete scratch leaf. Preparation checks each instantiated scratch path for absence after binding the
ID; only this **external occupancy** may cause transaction-ID regeneration, which actually changes the
occupied names. If bounded regeneration cannot find an unoccupied set, preparation returns an
external-state refusal — never an internal `ProtocolError`. The absence check is advisory against a
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

### 5.4 Staged specification validation

Validation is two proof-producing stages. A boolean validator or a raw `TransactionSpec` is not
interchangeable with either proof:

```python
compile_spec(spec: TransactionSpec) -> CompiledSpec
approve_for_project(
    compiled: CompiledSpec,
    context: ProjectContext,
) -> ProjectApprovedSpec
```

`compile_spec` is A2's sole public construction authority for `CompiledSpec`.
`approve_for_project` is A4's sole public construction authority for `ProjectApprovedSpec`.
`ProjectApprovedSpec` uses composition: it contains the exact `CompiledSpec` it approved plus A4's
project/root binding and resolved-topology evidence; it is not a subclass of `CompiledSpec`. A4's
reviewed plan owns the concrete fields of `ProjectContext` and that evidence, but `ProjectContext` must
represent the held project-root and metadata-root identities and selected capability backend against
which approval runs.

Both proof types are frozen, factory-controlled dataclasses. Their ordinary public-field constructors
refuse, and `dataclasses.replace(proof, ...)` refuses because it re-enters the same guarded constructor
without the module-private construction authority. This is a conventional Python API boundary, not a
claim of cryptographic unforgeability: hostile code can use private module state, `object.__new__`, or
`object.__setattr__` to manufacture objects. The engine relies on module privacy, type checking, and
architecture tests to prevent ordinary in-repository bypass; it does not present either proof as safe
against arbitrary code executing in the process.

The stages are deliberately non-substitutable:

- A3's filesystem-independent reference model builds pure recovery snapshots around the exact
  `CompiledSpec`. Synthetic model tests may supply a structurally validated logical topology; the
  production snapshot also contains A4's factory-issued resolved topology from `ProjectApprovedSpec`.
- A4 consumes `CompiledSpec` and returns `ProjectApprovedSpec` only after every rooted check below.
- A5–A8 accept `ProjectApprovedSpec`, never raw `TransactionSpec` or raw `CompiledSpec`, for preparation,
  capture, execution, recovery, or the synthetic exerciser.
- There is no union-typed entry point, compatibility adapter, implicit revalidation, or fallback path
  accepting both proof stages.

#### A2: lexical/model proof

A2 is pure and proves only rules decidable from the value:

- the specification declares at least one effect — a zero-effect transaction is refused rather than
  committed as a no-op;
- effect IDs are valid, exact-string unique, and pairwise distinct under
  `portability_equivalence_key(effect_id)`, the portability equivalence used by generated scratch
  leaves;
- dependency endpoints and ordering are complete;
- each effect's shapes are legal for its variant;
- every `FileState.byte_len` fits SQLite's signed `INTEGER` domain:
  `0 <= byte_len <= 2**63 - 1`;
- fingerprint hashes and permission modes are well formed; payload bytes remain A6's stream-verification
  responsibility;
- repeated-path timelines are continuous;
- each path's first and last states match the spec's declared initial/final states;
- the effect surface equals the declared transition surface exactly, with no omitted or undeclared path;
- persistent paths obey the project-relative lexical grammar and cannot occupy the reserved scratch
  grammar;
- distinct persistent path spellings are refused when their **whole-path**
  `portability_equivalence_key(path)` values match;
- the lexical initial/final trees are surface-consistent, and every lexically recognized
  transaction-created directory precedes effects beneath it.

A2's whole-path alias refusal is intentionally stronger than an actual-policy check. For example, it
refuses `a/x` together with `A/x` even on a case-sensitive volume where `a` and `A` are distinct
directories. That conservative loss of expressiveness buys a portable subset: anything A2 compiles is
free of the NFC/casefold aliases it knows about on both sensitive and insensitive volumes. It is still
only a filter. A4 may not treat successful A2 compilation as evidence of actual per-directory
distinctness or topology.

The one shared pure helper is
`portability_equivalence_key(value: str) -> str`, defined as
`NFC(casefold(NFC(value)))`. A2 applies it unchanged to whole paths and effect IDs. There is no
path-specific key alias or compatibility wrapper.

A2's surface-tree and created-directory-order checks must be linear in the total number of characters
and components across their declared inputs. They build a component trie, or use an equivalent
single-pass component traversal, and never materialize every prefix string for every path. Traversal is
iterative so Python's recursion limit does not become an arbitrary lexical path-depth limit. A2 imposes
no `NAME_MAX`, `PATH_MAX`, component-count, or total-path-length limit; real filesystem limits belong to
A4.

Every specification invalid under A2's declared phases is refused explicitly with
`SpecValidationError`. `compile_spec` does not wrap the pipeline in `except Exception`: an unexpected
internal fault propagates unchanged and is never relabeled as caller error. Phase 1 still handles
adversarial model shapes deliberately. A diagnostic type name is obtained through `_type_name`, whose
`try` covers only `type(value).__name__` lookup; exact dataclass instances manufactured with
`object.__new__` are read with `getattr(obj, field_name, _MISSING)` and a missing field is refused
explicitly before later phases run.

Diagnostics for caller-supplied integers are size-independent. An unknown `schema_version` reports the
fixed expected version, an invalid `mode` reports the fixed `0..0o7777` bounds, and an invalid
`byte_len` reports the fixed signed-64-bit bounds. No out-of-domain caller integer is formatted in
decimal, octal, hexadecimal, or binary on its refusal path.

#### A4: rooted project approval

A4 proves the remaining rules against one held project/root context before any transaction-record or
blob write:

- ancestor-resolved, leaf-retaining paths stay inside the held project root;
- no persistent effect path resolves at or below the metadata root — checked by **filesystem identity,
  not spelling**: each effect path's resolved leaf and ancestors are compared against the held metadata
  root descriptor's `st_dev`/`st_ino`;
- every path and component — including every unwalked component after resolution stops at the first
  missing or blocking ancestor — fits the actual or inherited parent directory's `NAME_MAX` and the
  volume's `PATH_MAX` constraints;
- repeated observations of one lexical directory prefix agree on directory identity or compatible
  frontier state; disagreement is approval-time drift, never a later observation silently replacing
  an earlier one;
- each required semantic capability (§5.5), plus the always-required `anchored_traversal`,
  `durable_publish`, and `advisory_project_lock`, is supplied by the selected backend for the
  project-root volume and the resolved configuration tuple is on the durability allowlist;
- all instantiated effect/role scratch leaves are pairwise distinct under the actual lookup policy of
  their concrete parents (§5.1), before the separate external-occupancy check;
- declared persistent paths name pairwise distinct entries under the actual name equivalence of the
  **directory that performs each lookup**; and
- the initial/final surface-tree and transaction-created-directory ordering rules are re-run over the
  **resolved equivalence topology**, not A2's lexical spellings.

The resolved topology requirement is stronger than endpoint distinctness. On an insensitive parent,
declared path `A` and declared descendant `a/x` do not name the same endpoint, so a pairwise endpoint
check passes; nevertheless `A` is the actual ancestor of `a/x`. A4 must represent both through the same
resolved parent node, then re-check that a non-directory `A` constrains `a/x` to `ABSENT` and that a
`CreateDirectory("A")` precedes an effect on `a/x`. It may not reuse A2's lexical tree verdict.
The retained evidence includes a pure logical node/parent topology over every declared persistent path
and effect scratch role. A3 consumes that topology when reconstructing directory occupancy and abstract
rollback convergence; it may not substitute A2's exact-spelling tree in production.

Name equivalence is **per parent directory, not per volume**. ext4 enables case-insensitive lookup with
the per-directory `+F` (`FS_CASEFOLD_FL`) attribute on a `casefold`-enabled filesystem, so one
filesystem can hold case-sensitive and case-insensitive directories at once
([ext4 admin guide](https://cdn.kernel.org/doc/html/latest/admin-guide/ext4.html#case-insensitive-file-name-lookups)).
A result obtained in the metadata root or a probe directory therefore cannot establish the lookup
policy of any target parent.

Folding governs lookup of one component within its parent. A4 consequently walks the declared tree
component by component, applying each held or inherited parent policy. For a directory the transaction
creates, the policy is inherited from the deepest existing ancestor where the platform defines it that
way (ext4 inherits `+F`). A parent that neither exists nor is created by the transaction cannot be
captured and is refused before approval.

How a backend determines a directory's policy is platform-specific and therefore informative, not
contractual — reading a directory attribute where exposed, or probing empirically within the reserved
scratch grammar (§5.1). What is contractual is that approval determines the policy for every directory
this transaction looks up or writes into.

The check covers paths declared **absent**, where identity-by-`st_dev`/`st_ino` does not apply.
Mutation-time no-clobber is not a substitute: `create x`, `delete x`, `create y` can make every
no-clobber operation succeed while aliasing `x` and `y` leave the declared final states unsatisfiable.
Actual equivalence and the resolved topology are therefore established before capture or mutation.

Only a factory-issued `ProjectApprovedSpec` crosses into A5–A8. After that boundary, those stages may
trust both the A2 value proof and the A4 project/root proof without re-deriving either.

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

**Bootstrap precedes approval.** Creating or opening `metadata_root`, acquiring the project lock, and
running this probe form an idempotent bootstrap phase that necessarily precedes capability approval —
the probe cannot certify a volume without writing to it. That bootstrap touches only the engine-owned
`metadata_root` (the `lock` and `probe/`), never a project path or a transaction record, and its
survivors are reclaimed under the lock at lease entry (above). The "refuses before any write" guarantee
(§5.4) is scoped accordingly: **transaction-record metadata (spec, journal, blobs) and project
mutation** never begin until every required capability, and the volume's ability to host the store, is
approved.

**Availability is not durability.** The probe proves a capability is *functionally* present (e.g.
`RENAME_EXCHANGE` succeeds and swaps), but it cannot prove the volume upholds the durability guarantees
in this table under real power loss — a filesystem can offer atomic exchange yet lie about `fsync`,
reorder across a barrier, or be mounted `nobarrier`. Power-loss correctness is therefore **not**
established at runtime. The engine additionally restricts `metadata_root`'s volume to a **known-durable
configuration allowlist** and refuses an unknown or known-non-durable configuration (tmpfs, network
filesystems, `nobarrier`/`barrier=0` mounts) with `CapabilityUnavailable`. The allowlist, not any
per-run probe, carries the durability guarantee; admitting a configuration to it requires block-device/VM
crash certification (§13.2).

The allowlist is keyed on a **supported configuration tuple**, not on `statfs` `f_type` alone, because
`f_type` is too coarse to carry a durability claim: ext2, ext3, and ext4 all report `0xef53` despite
different journaling guarantees, and barrier-defeating mount options (`nobarrier`, `barrier=0`) are not
reflected in `statfs` at all. A tuple is (backend/OS, filesystem implementation, the feature and mount
options that bear on barrier/`fsync` behavior, and the tested storage assumptions). Runtime validation
resolves the metadata volume to its actual mount and inspects that tuple — on Linux, the filesystem type
and mount options from `/proc/self/mountinfo` (and superblock features where they matter), not `f_type` —
and refuses any volume whose tuple is not a crash-certified entry. A crash test therefore certifies one
tuple, never every volume that merely shares an `f_type`.

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

A second case reaches the same place by a different route: the ancestor **exists but is not a
directory**. A transaction may declare an ancestor as a file or symlink initially, remove it with
`DeletePath` (or, for a regular file, as the source of `MoveNoClobber`), and create a directory in its
place, then act on a path beneath it — for example `DeletePath("p")`, `CreateDirectory("p")`,
`CreateFileNoClobber("p/q")`. Compilation admits this: `p`'s timeline is continuous
(`FILE → ABSENT → DIRECTORY`), and the surface rule requiring a declared descendant of a non-directory to
be declared absent is satisfied, since `p/q` is absent precisely *because* `p` is a file. The
first-missing-component procedure above does not apply, because no component of `p/q` is missing where
traversal stops — guarded traversal fails at `p` itself with `ENOTDIR`, and there is no descriptor
against which `q` could be looked up. `ENOTDIR` establishes only that the blocker is not a directory —
it does not distinguish a regular file from a socket, FIFO, or device node. Neither branch is selected
by the errno: the regular-file branch is selected by verifying the blocker against the timeline's first
`FileState`, and the symlink branch by verifying it against the timeline's first `SymlinkState`. A
blocker matching neither declared state refuses — including a symlink whose target or mode has drifted,
which is a symlink but not the declared one.

The descendant's absence is therefore **inferred from the ancestor's verified state rather than probed**.
Neither a regular file nor a symlink can contain directory entries, so an ancestor verified to be either
one proves nothing exists beneath it. The strength of that verification differs by kind, and the two
branches must not be conflated:

- **A regular-file ancestor is descriptor-coherent.** Capture opens it with `O_RDONLY | O_NOFOLLOW`,
  takes its type and mode from `fstat` on that same descriptor, and hashes its contents from it. The
  inference is then stronger than the negative lookup it replaces, because it rests on a single
  descriptor's coherent observation rather than on a name resolved twice.
- **A symlink ancestor is not.** `symlink_fingerprint` is `lstat` plus `readlink` (§5.5), explicitly
  *not* descriptor-coherent — `O_NOFOLLOW` fails by design on a symlink leaf, so no descriptor to the
  link itself is available. The absence inference still holds, since a symlink is not a directory, but
  its identity contract is the one this section states for every symlink precondition: the destructive
  operation atomically transfers the live entry into the tombstone and validates that transferred object
  against the frozen fingerprint. The guarantee comes from that validation, never from a descriptor.

As everywhere in this section, capture-time verification is not compare-and-swap authority; if the
ancestor is swapped for a directory before execution, the destructive transfer that deletes it validates
the transferred object against the frozen fingerprint and refuses or halts rather than proceeding.
Execution ordering then follows the missing-ancestor case exactly:
timeline continuity forces the ancestor's deletion and re-creation to precede any descendant effect, and
§9.5 hands the newly published directory's verified descriptor down as the descendant's parent
descriptor, so the descendant never re-resolves the ancestor chain.

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

The engine's **own** metadata operations — the `lock`, `staging/`, `work/`, blob promotion, and
directory fsyncs — are anchored exactly like effect paths (§6): the engine opens `metadata_root` and
its subdirectories from a durably-held descriptor through guarded traversal, refusing any symlink or
mount crossing, retains those descriptors, and issues each as a single-component leaf operation relative
to a held descriptor, never a re-resolved absolute path.

The **SQLite database files** (`atoms.db`, `-wal`, `-shm`) are the exception, and the design does not
claim otherwise: stdlib `sqlite3.connect()` opens by pathname and SQLite performs all database and
sidecar I/O through its own VFS, so those operations are neither issued through the engine's `openat`
traversal nor visible to the in-process interposer (§13.5). They are handled as follows.

- *Anchoring by verified identity, not by descriptor.* At bootstrap the engine resolves `metadata_root`
  once through guarded traversal, records its `st_dev`/`st_ino`, confirms it is a real directory on the
  probed, allowlisted volume, and opens the database by that verified path. A pre-existing symlink or
  ancestor swap that would place the store elsewhere is caught at that resolution. Fresh-process recovery
  repeats the same verified resolution before opening the database. This resolution is verify-then-open,
  not a held-descriptor anchor: stdlib `sqlite3` re-resolves `metadata_root`'s pathname whenever it opens
  the database or a sidecar, so — unlike an effect path pinned to a held parent descriptor — no descriptor
  participates and the §3.2 held-directory reasoning does not carry over. The stdlib baseline therefore
  makes an explicit cooperating-process assumption, broader than §3.2's relocation case: **mutating or
  replacing `metadata_root`, any of its ancestors, or any of the `atoms.db{,-wal,-shm,-journal}`
  entries while a lease is active voids the recovery guarantee.** The database entries are named
  alongside the directory because verifying them is a time-of-check operation like any other: A5a
  refuses a symlink or non-regular file at each before SQLite opens it, which proves what was there,
  not what will be. The project lock serializes cooperating processes, for which this never arises; defending
  against an adversary who substitutes the store's path mid-lease requires the optional hardened VFS
  below, which opens the database through `openat`-anchored, `O_NOFOLLOW` descriptors.
- *Bounded SQLite surface under a pinned profile, not per-syscall audit.* SQLite may touch more than
  `atoms.db{,-wal,-shm}` — it documents several temporary-file kinds (rollback and statement journals,
  temp databases, materializations) whose presence and location it explicitly disclaims as an application
  contract. The engine therefore does **not** name a fixed three-file allowlist; it **bounds** the
  surface with a pinned SQL/configuration profile and treats whatever files that profile can produce
  under the verified `metadata_root` as the excluded SQLite surface (§13.5). The profile pins the SQLite
  capabilities the engine relies on, sets `temp_store=MEMORY`, and forbids `ATTACH` and `VACUUM`.
  `temp_store=MEMORY` governs temp tables, indices, and materializations; it does not govern rollback,
  super-, or statement journals, and **no per-connection temp-directory redirect is available** — the
  `SQLITE_TMPDIR` environment variable is process-global and hostile in a library, and
  `temp_store_directory` is deprecated. The engine therefore minimizes transients rather than claiming
  every SQLite transient is `metadata_root`-local: SQLite documents that a statement journal may use a
  randomized path outside the database directory, and reserves the right to change its temporary-file
  behavior — which is the same disclaimer this paragraph opens with. One transient is placed
  definitely: the rollback journal SQLite writes while first switching the database into WAL mode is
  colocated with `atoms.db` as `atoms.db-journal`, and is consumed before any effect runs. That
  transition belongs to **A5a**, which creates and opens `atoms.db`; §5.5's bootstrap switches only
  `probe/certify.db`. It is not the *only* file SQLite places — the WAL and shared-memory files are
  persistent companions, and a statement journal may appear elsewhere. The interposer does not audit SQLite's
  internal C-level I/O — exactly as the persistence-cut model does not re-verify SQLite's WAL atomicity
  (§13.2); the engine trusts the library on a certified volume. Its obligation is to prove no *effect*
  mutation targets the store, not to enumerate the library's own writes.
- *No custom VFS is required for durability.* SQLite's stock unix VFS honors `PRAGMA fullfsync` (macOS
  `F_FULLFSYNC`), so `durable_publish` for the store is met by configuration (§7.2). A custom VFS —
  giving `openat`-anchored, `O_NOFOLLOW` opens and interposer-visible I/O, via a maintained binding
  such as `apsw` or a small vendored shim — is an **optional Plan A hardening**, specified there with its
  own auditing and recovery tests, not a correctness prerequisite for the cooperating-process model.

Compilation additionally rejects any persistent effect path at or below `metadata_root` by filesystem
identity (§5.4), so no consumer effect can target the engine's own storage.

This metadata is single-host by construction. On creation of `metadata_root`, the engine best-effort
**requests** that cross-machine sync clients ignore the directory — via the platform's Dropbox ignore
marker (Linux extended attribute `user.com.dropbox.ignored=1`; macOS `com.dropbox.ignored`, or the
File Provider `com.apple.fileprovider.ignore#P`) — so the database and blobs are not propagated
off-host. The durability model is defined against one local POSIX filesystem; a synced copy of this
directory is neither required nor trusted, and a failure to set the ignore marker does not weaken any
single-host guarantee.

Because transaction metadata never travels with the content it governs, a project tree that arrives
*without* its metadata root — copied, restored from backup, or freshly cloned — is a normal cold
bootstrap, not corruption: the engine recreates the store idempotently, and any grammar-matched
scratch debris such a copy carries is external occupancy handled at preparation (§5.1). A metadata
root restored *alongside* content captured at a different instant (a non-atomic backup) is an
ordinary interruption to classify: attributable states resolve, unattributable states halt (§8.4).

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

- `transaction(txid PRIMARY KEY, spec_json, state, committed, rollback_result,
  halt_diagnostic, …)` — one row per transaction; `state` is the §8.1 machine; `committed` is the
  separate durable commit decision retained through `HALTED`; `halt_diagnostic` is A3's token-free
  stable diagnostic shape, not a serialization of snapshot-local identity tokens; `spec_json` is the
  immutable canonical `TransactionSpec`, written once. A5a names the table `transaction_record`,
  since `transaction` is a SQL keyword. The store's DDL version lives in `PRAGMA user_version`; a
  per-row `schema_version` is migration scaffolding and is not carried until a second version needs
  per-record provenance.
- `effect(txid, effect_id, variant, journal_state, …, PRIMARY KEY(txid, effect_id))` — per-effect
  forward/reverse journal state (§8.2–§8.3).
- `blob(digest PRIMARY KEY, byte_len)` — the content-addressed blob index; the bytes live
  under `blobs/sha256/`. No refcount: its only consumer is the garbage collection §7.5 excludes from
  transaction correctness, a blob is referenced iff a live transaction record names it, and a
  maintained counter that reaches zero early deletes a blob a live transaction still references.
- `active(singleton INTEGER PRIMARY KEY CHECK(singleton = 0), txid)` — at most one row, enforcing a
  single active transaction and naming it.

Execution state advances by updating `transaction.state` and `effect.journal_state` inside SQLite
transactions. There is no destructive-replacement hazard to hand-manage and no append-only generation
chain to validate: SQLite gives atomic, crash-consistent updates, and WAL replay on open reconstructs a
single consistent metadata state. The earlier "longest contiguous valid hash chain from generation
zero" recovery is therefore removed — that machinery existed only to give the hand-rolled JSON journal
the atomicity SQLite provides natively.

### 7.3 Preparation order and the cross-substrate durability rule

Preparation accepts only a factory-issued `ProjectApprovedSpec` (§5.4). Because blob and staging bytes
live on the filesystem while the record lives in the database, the one
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

After `COMMITTED` is durable (`transaction.state = COMMITTED`), committed cleanup is
transaction-level. Under the lock, the engine first compares the one current observation for every
compiled persistent path with the complete compiled final surface. Only after that proof succeeds does
it validate the complete scratch vector and build cleanup steps in compiled effect order:

- exact retained `ReplaceFile.STAGING`, `DeletePath.TOMBSTONE`, and
  `MoveNoClobber.ANCHOR` state is removed; absence means that cleanup already landed;
- `CreateFileNoClobber.STAGING` and `CreateDirectory.WORK` must already be absent at `DONE`; and
- a retained state/kind mismatch or any create staging/`WORK` survivor halts and preserves evidence.

The final-surface proof is the sole persistent predicate for committed cleanup. In particular, cleanup
does not compare a repeated path's final live state with each earlier occurrence's postcondition.
Each resulting `RemoveScratch` step carries and fresh-authorizes only its exact scratch slot; the
reducer permits this scratch-only coverage only for a `COMMITTED` transaction's `DONE` retained-scratch
effect. After removals are fsynced, the engine clears the `active` row. `ROLLED_BACK` is committed only
after every transaction-owned mutation is undone, the complete declared initial surface is verified
present (except where a proved external blocker is preserved as drift), and all rollback scratch is
removed and fsynced; its outcome records either restored initial surface or preserved external drift.
A crash during committed cleanup leaves `transaction.state = COMMITTED` with `active` still set, so
fresh-process recovery repeats the final-surface proof and finishes cleanup without reconsidering the
commit decision. Detached terminal records and unreferenced blobs are garbage-collected later under
the lock; garbage collection is not part of transaction correctness — interruption can only ever
leave terminal records or immutable blobs.

## 8. Durable state machine

### 8.1 Transaction states

```text
PREPARED → APPLYING → APPLIED → COMMITTED
    └──────────────→ ROLLING_BACK → ROLLED_BACK

recovery classification ───────→ HALTED
```

`COMMITTED` is the only logical commit decision. It is persisted only after every effect is durable,
the complete persistent final surface matches, every retained replace/delete/move scratch object
matches its exact cleanup state, and create-file staging plus create-directory `WORK` are absent. A
restart repeats one complete final-surface proof before resuming scratch-only cleanup. The command
returns success only after committed scratch cleanup, active-pointer detachment, and their directory
fsyncs complete.

A transaction may enter `ROLLING_BACK` because of a caught application failure or because recovery
finds any noncommitted active transaction. `HALTED` preserves the record, scratch objects, the last
durable commit decision (if any), and diagnostic classification. A halt after `COMMITTED` never
licenses rollback; it preserves the final state and reports incomplete cleanup. The halt transition
does not clear the separate `committed` value. Its diagnostic freezes the pre-halt transaction state
and commit decision, the full durable per-effect journal vector, any separately labeled pure-planning
cursor state that produced the conflict, token-free expected/observed state, and the named-slot
identity relations used by classification — never the snapshot-local tokens themselves. A projected
cursor is diagnostic evidence, not a claim that its semantic steps became durable. Later recovery
returns that stored diagnostic rather than recomputing an origin state of `HALTED`.

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

The legal global journal shapes while rolling back are
`DONE* STARTED? PENDING*` or `DONE* UNDO_STARTED? UNDONE* PENDING*`. The branches are exclusive: the
highest executed forward `STARTED` effect must be settled before any reverse effect, so `STARTED`
followed by `UNDONE` is not a reachable history.

### 8.4 Recovery classification

Uncommitted recovery is a two-level operation. First it reconstructs, **per path**, where that path's
timeline stands; then it classifies the at-most-one in-flight effect **jointly over all of its paths**.
Its inputs
are the exact A2 `CompiledSpec`, A4's resolved logical topology in production, the active binding, the
`transaction.state`, separate commit decision, optional rollback result, optional frozen halt
diagnostic, the per-effect `journal_state` rows, and coherent logical observations of every persistent
path and effect scratch role. Regular-file and directory observations carry opaque snapshot-local entry
identities; symlinks carry only their `lstat` + `readlink` fingerprint and are never identity-decided.
File staging observations carry their exact/prefix/diverged relation to the planned postimage only for
a present `CreateFileNoClobber` staging file while `STARTED`, or a present `ReplaceFile` staging file
while `STARTED` and live is exact `pre`. Those are the cases where the forward classifier can consume
construction evidence. Displaced preimages and reverse quarantines are decided from exact
fingerprints and atomic tuples without an unnecessary planned-blob comparison. After the
transaction-wide final-surface proof, committed-cleanup scratch is decided from its exact logical
scratch slot alone.
Directory observations identify children outside the resolved topology. Because SQLite gives a single
crash-consistent metadata state on open, there is no partial journal to reconcile before
classification begins. Comparing each occurrence independently against the single live entry would
misread a repeated-path timeline (§5.3), and classifying a multi-path effect (e.g. `MoveNoClobber` over
source, destination, and anchor, §9.4) per path could yield contradictory verdicts. Journal state gates
attribution: an effect can only have mutated a path once it is durably `STARTED`.

For `COMMITTED` with all effects `DONE`, the engine does not reconstruct occurrence frontiers or feed
the single final live entry through each effect's ordinary joint classifier. It first proves the
complete compiled final surface once, then validates the complete scratch vector. Exact retained
replace/delete/move scratch is removable and absence is already cleaned; create-file staging and
create-directory `WORK` must be absent. A final-surface mismatch is
`COMMITTED_SURFACE_MISMATCH`; a scratch state or shape mismatch is
`EFFECT_TUPLE_UNATTRIBUTABLE`. Only after the whole vector passes does the plan contain scratch-only
removals followed by active detachment. This ordering preserves repeated-path timelines: the live
entry need equal only the timeline's final state, not every earlier occurrence's post-state.

The frontier and joint-tuple rules below govern uncommitted recovery.

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

**Joint effect classification.** The frontiers identify the transaction's at-most-one in-flight effect — a
forward `STARTED` (whether `APPLYING`, or a failure-interrupted `STARTED` under `ROLLING_BACK`), or a
reverse `UNDO_STARTED`. That effect is classified once over the union of its persistent and scratch
paths, using the variant's tuple rule, and the one decision applies to every path it owns:

- exact pre-state (forward) or restored pre-state (reverse), no engine scratch survivor → it did not
  land / was fully undone;
- the variant's complete post tuple → it landed (subject to the
  fingerprint-equivalent-recreation non-guarantee). A persistent post fingerprint alone is
  insufficient when the variant requires a retained displaced entry, tombstone, anchor, or work-name
  relation; `CreateFileNoClobber` is the explicit no-staging exception in §9.2;
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

Transaction state, commit decision, and active binding are classified jointly. A detached `COMMITTED`
or `ROLLED_BACK` record needs no recovery. A detached `PREPARED`, `APPLYING`, `APPLIED`, or
`ROLLING_BACK` record is contradictory durable metadata: recovery records a halt if possible, never
reattaches the active pointer, and performs no project or scratch mutation.
Any transaction-state/commit-decision mismatch likewise halts with A3's closed
`COMMIT_DECISION_CONFLICT` reason; an illegal journal vector halts with
`JOURNAL_TOPOLOGY_INVALID`.

Recovery classifies every uncommitted path's whole timeline and every in-flight effect's joint tuple,
or the committed transaction's complete final surface and scratch vector, before returning any
mutation, so an early repair cannot destroy evidence needed to recognize a later conflict.

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
exchanges the retained preimage back when the live postimage is still authoritative. After the
transaction-wide final-surface proof, committed cleanup removes staging only if it still has the exact
declared preimage state, or accepts absence as already cleaned; any other state or kind halts. It does
not compare the current live entry with this occurrence's postimage, because a later occurrence on the
same path may have superseded it.

If the declared `pre` and `post` fingerprints are equal, recovery cannot tell the two sides of a
completed exchange apart by state. It therefore leaves the exact live state in place and removes the
exact staging state, which restores the declared surface without promising inode provenance (§3.2).
A different staging state that is not attributable planned-post construction evidence is not treated
as transfer evidence in this no-op case; both entries are preserved and recovery halts.

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

At committed `DONE`, staging must be absent. A survivor is contradictory even after the complete
transaction final surface has been proved.

Two `CreateFileNoClobber` occurrences of the *same* destination are legal only when separated by an
intervening deletion — a continuous `absent → file → absent → file` timeline (§5.3). There is no
separate global duplicate-destination ban; it is the §5.3 continuity check that rejects two
`absent → file` occurrences of one destination with no `file → absent` between them, because their
timelines would not be continuous. (A concurrent *external* creator instead loses at no-clobber
publication and the transaction rolls back.) There is no live reservation sentinel and no
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
symmetric to the reappeared-source move case (§9.4). After the durable commit decision and complete
final-surface proof, committed cleanup deletes an exact retained tombstone or accepts its absence as
already cleaned. It does not require this occurrence's live path to remain absent; a later occurrence
may validly have recreated it.

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

After the transaction-wide committed final-surface proof, cleanup removes an anchor that still has the
exact declared source state, or accepts absence as already cleaned. It does not require the current
destination to retain this earlier occurrence's identity: a later effect may have superseded that
destination.

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

`WORK` must be absent before `DONE`. A `WORK` survivor beside a committed `DONE` journal is a
contradiction and halts; it is not deferred garbage collection.

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
finishes rollback with a refused outcome. The persistence-cut model covers the same-inode
intermediate by identity injection at recovery's two observation seams — the
state is not host-reconstructible and ext4's journaled rename never splits it,
so no replay generates it physically (A8 design §4.5) — and asserts recovery
does not misread it as a blocker.

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

The recovery observation that feeds A3 is coherent. A regular file's or directory's state and identity
come from the same opened object; a file prefix relation is computed by comparing that object with the
planned blob; and a directory's occupancy evidence comes from one descriptor-relative enumeration
reconciled against A4's resolved persistent-and-scratch topology. A symlink instead carries the weaker
`lstat` + `readlink` fingerprint above and no A3 identity. A3 receives those primitive facts and owns
the verdict. A6/A7 may not pre-classify them into a recovery outcome.

The authority check precedes every staging-object mutation, including prefix removal. Atomic live
publication means a crash during restoration leaves the target at the effect state or restored state,
never at an in-place partial state.

Reverse publication is validated symmetrically to the forward direction (§6): after restoring an object
to a live path, the engine verifies that path holds the expected restored state — by identity against
the retained preimage or staging descriptor, or exact fingerprint — before marking the effect `UNDONE`.
A foreign object published onto a restored name is refused or halted, never marked `UNDONE`, so a
staging swap during rollback cannot be laundered into a false restoration.

## 11. Refusal and failure semantics

- **`PreconditionRefused`** — external state prevents clean execution; the transaction refuses cleanly.
  Concurrent drift is one case: during approval, when two observations of
  one directory or entry disagree within a single approval; at capture; or by validating an atomically
  displaced entry. Pre-existing external occupancy of an engine-derived scratch leaf is another, and
  need not be concurrent — such a leaf may predate this attempt. Approval holds no transaction record
  and has mutated nothing, so it refuses
  directly. Once mutation may have begun, the executor returns this refusal only after the current
  effect and every earlier effect have been restored; inability to prove that restoration becomes
  `TransactionHalted`.
- **`SpecValidationError`** — a `TransactionSpec` failed A2's pure lexical/model proof. Raised by
  `compile_spec` before any project context exists.
- **`ProjectApprovalRefused`** — the rooted project proof failed. Raised during approval, before any
  transaction-record or blob write and before any project mutation.
- **`CapabilityUnavailable`** — required semantics cannot be supplied. Raised during preparation before
  project mutation.
- **`TransactionHalted`** — the journal, live state, or rollback survivor is unattributable. The engine
  preserves the active record and evidence.
- **`MetadataStoreInvalid`** — the durable metadata store cannot be safely interpreted: a failed
  integrity or foreign-key check, a schema that does not match its recorded version, a foreign
  database, a non-canonical `spec_json`, a blob whose bytes do not match its digest, or a store
  version this build does not know. Corruption and forward incompatibility share one type because
  they share one correct response — stop and preserve evidence — and the message distinguishes them.
  Raised by the store layer, never as a substitute for `ProtocolError`, which tells a caller to fix
  its call.
- **`ProtocolError`** — an internal contract was violated. Attempt rollback if mutation may have begun;
  retain the record if a complete rollback cannot be proved.

Caught process-local failures, including cancellation, `KeyboardInterrupt`, and `SystemExit`, enter
rollback before being re-raised. `SIGKILL`, power loss, and machine failure do not unwind Python and
are exercised only through fresh-process recovery tests.

Recovery and rollback diagnostics identify the transaction, effect, paths, durable journal vector,
any separately labeled projected conflict vector, token-free expected and observed states, named-slot
identity relations, A3's closed halt reason, and the non-mutating operator action required next. They
never serialize snapshot-local identity tokens. There is no silent fallback or automatic discharge of
a halt.

## 12. Consumers

The engine's consumer submission boundary is a `TransactionSpec`; its mutation boundary is a
`ProjectApprovedSpec` plus the recovery-resolve lease (§5.4). It ships with no consumer-specific
compilation baked in. Two vehicles exercise and adopt it.

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
producing and authenticating its own frozen intent (§4.1). Science's substrate-consolidation design
(science `2026-08-02-substrate-consolidation-design.md`) rules that durability and concurrency belong
to this engine and that no interim transaction layer is built anywhere else; it also surfaces a
constraint this section previously ignored: `nodes` holds normative Python/TypeScript parity while
this engine is Python-only, so portable `nodes` should not depend on it until a language-neutral
execution seam exists (a serialized spec plus out-of-process executor is the plausible shape, §15).

- **science's composition root** — the likely first production consumer: Science's Python composition
  root combines `nodes` and this engine ("Science as a `nodes` profile over `atoms`"), so corpus
  writes flow through that root rather than through a `nodes`-internal adapter. Under science's world
  model the write unit is a **corpus root**: the consumer keys the engine root — and therefore the
  lock and metadata root — on the corpus, not on a "project" that merely contributes to one.
- **science's plan families** — archive, import, cohort-import, and supersede, whose mutation shapes
  the engine was originally derived from. Each family keeps its planner and saved-plan authentication
  and compiles, after authentication, into a `TransactionSpec`. This adoption, and the deletion of
  science's existing execution dialects, is science's own hard cut — outside this repo's authority.
- **`nodes` directly** — deferred behind the language-neutral seam above; adopting this engine from
  portable `nodes` before that seam exists would break parity or force a second engine implementation.

No consumer adoption may redefine the engine protocol, add a feature flag, or introduce a runtime
transaction-dialect choice.

## 13. Verification strategy

### 13.1 Executable reference model

Recovery is specified as a production **top-level transaction classifier** with subordinate variant
classifiers, fresh step authorization, and a pure abstract reducer:

```text
snapshot:    (CompiledSpec, resolved logical topology, active binding,
              transaction state, commit decision, rollback result, frozen halt diagnostic,
              per-effect journal states, logical observations)
                 → validated RecoverySnapshot
transaction: RecoverySnapshot
                 → frozen ordered RecoveryPlan
variant:     (uncommitted variant, effect journal state,
              effect's joint persistent-and-scratch observation)
                 → effect settlement decision
committed:   (COMMITTED/all-DONE snapshot after complete final-surface proof,
              one exact scratch slot)
                 → committed scratch decision
authorize:   (RecoveryPlan, step index, fresh coherent observation)
                 → AuthorizedStep | HaltPlan
prefix:      (RecoverySnapshot, RecoveryPlan, completed step count)
                 → prefix RecoverySnapshot
reduce:      (recovery snapshot, RecoveryPlan)
                 → next recovery snapshot
```

The transaction state (§8.1) is a required input: `APPLIED` and `COMMITTED` can present identical `DONE`
effects and the same final surface yet demand opposite decisions — rollback versus committed cleanup —
so the classifier cannot be a pure function of effect states and observed tuples alone. The transaction
classifier reconstructs each uncommitted path's frontier (forward or reverse per that state) and
invokes the ordinary variant classifier once per in-flight effect over its joint tuple (§8.4). For a
committed transaction it instead proves the complete final surface once, then invokes the
proof-gated committed scratch classifier without occurrence-local persistent evidence. It validates
every relevant observation before emitting any mutating step. A7 consumes this plan as the production
recovery authority and does not implement a second classifier. Before each filesystem mutation, A7
obtains a fresh coherent observation. Ordinary steps authorize exact non-identity agreement plus the
same named-slot identity partition; committed cleanup `RemoveScratch` authorizes only its one scratch
slot. Tokens are freshly allocated per observation and are never compared across token universes. Any
mismatch produces a halt plan rather than silent reclassification.

The reducer applies the same semantic steps to the logical snapshot. It models identity-preserving
transfers, removals, preserved external blockers, resolved directory occupancy, journal transitions,
the separate commit decision, the token-free frozen first-halt diagnostic, and active detachment, but
no syscall or durability barrier. Applying classification and reduction twice reaches a fixed point: a
detached terminal snapshot is `NO_RECOVERY`, and an already halted snapshot preserves the exact commit
decision and token-free diagnostic from its first halt. Across a process restart, surface evidence is
compared up to renaming of regenerated snapshot-local identity tokens; the diagnostic itself remains
exactly equal.

Classification and semantic-plan construction are identity-conservative: they reuse the tokens in
the coherent source observation and never allocate a new token. A mutating step's expected tuple is
bound after all preceding metadata steps, including normalization of construction-only evidence when
`STARTED` becomes `UNDO_STARTED`.

Table and property tests cover every variant, forward/reverse state, named intermediate, and
unattributable state. Generated valid effect sequences prove:

- path timelines are continuous;
- recovery reconstructs each path's timeline frontier, forward and reverse, and never misreads a
  mid-timeline live state as external drift;
- a multi-path effect is decided once over its joint tuple, never contradictorily per path;
- uncommitted recovery removes every attributable mutation and preserves proved external blockers;
- committed recovery retains the final surface and cleans repeated-path scratch without comparing the
  final live entry to every occurrence-local postimage;
- recovery never mutates an unattributable state;
- a stale plan step is never authorized; and
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
reaching the pre-state. VM or block-device crash testing is the **certification** method for the
durability configuration allowlist (§5.5): the persistence-cut model exercises the engine's ordering
logic on every run, while the crash-test matrix is what admits a **configuration tuple** — backend/OS,
filesystem implementation, barrier-relevant feature and mount options, and storage assumptions — to the
allowlist. Because one `statfs` `f_type` spans several such tuples (ext2/3/4 all report `0xef53`, and
`nobarrier` is invisible to `statfs`), certification is keyed on the tuple, and a volume whose resolved
tuple the engine has not crash-certified is refused at preparation, not trusted.

### 13.3 Spec and compiler conformance

Build a spec, compile it twice, and require equal `CompiledSpec` values and identical canonical output.
The A2 suite additionally proves:

- `compile_spec` is the ordinary construction authority: direct `CompiledSpec(...)` and
  `dataclasses.replace(compiled, ...)` raise `TypeError`, while field assignment raises the exact
  `dataclasses.FrozenInstanceError`;
- `schema_version`, `mode`, and `FileState.byte_len` each reject an integer with thousands of digits
  without formatting that integer, under both Python's default `int_max_str_digits` limit and the
  disabled (`0`) setting; their diagnostics name only the fixed expected version or fixed bounds.
  `byte_len` accepts `2**63 - 1`, rejects `2**63`, and the accepted maximum survives canonical
  encode/decode;
- distinct effect IDs that share `portability_equivalence_key(id)` are refused, even though their
  exact strings differ;
- surface-tree and created-directory-order validation use iterative component tries (or an equivalent
  traversal), visit each input component a constant number of times, and accept a lexically valid path
  deeper than Python's recursion limit; the compiler has no `ancestors` attribute after the rewrite,
  and the dead `ancestors()` API and its tests are removed rather than retained behind a wrapper;
- paths and effect IDs use the single `portability_equivalence_key` helper; the old
  `path_equivalence_key` name is absent rather than retained as an alias;
- hostile `type(value).__name__` behavior and exact dataclass instances with uninitialized slots are
  refused by phase 1 with `SpecValidationError`, while a test-injected unexpected internal exception
  escapes `compile_spec` unchanged, proving that the pipeline has no blanket exception normalization;
  and
- tests pin only the load-bearing refusal precedence declared by A2: structural typing before any field
  interpretation, duplicate effect IDs before dependency resolution, duplicate surfaces before map
  construction, and exact coverage before endpoint lookup. The thirteen phase numbers do not promise
  error precedence between independent rules, so no change-detector tests freeze those adjacencies.

Reject missing effects, extra effects, invalid ordering, malformed timelines, fingerprint/mode
mismatches, path escapes, and initial/final surface divergence. A4's conformance suite constructs a
`ProjectApprovedSpec` only through `approve_for_project`; direct construction and
`dataclasses.replace` refuse just as for the A2 proof. A5–A8 type/architecture tests reject raw
`TransactionSpec` and raw `CompiledSpec` at their preparation and execution boundaries.

Apply case- and Unicode (NFC/NFD) alias tests to all name-equivalence surfaces. A2 runs the fixed
portability-key cases without a filesystem; A4 runs actual-policy cases on case- and
normalization-insensitive directories:

1. **The metadata namespace** — a persistent path spelled as an upper-case or NFC/NFD variant of
   `metadata_root`, which the identity-based check of §5.4 must reject where a lexical prefix check would
   not.
2. **The reserved scratch grammar** — a persistent path aliasing the scratch sigil through a case or
   normalization variant, which the equivalence-aware grammar match of §5.1 must reject. A letter-free
   sigil and equivalence-aware matching must both pass these tests.
3. **Persistent-path distinctness and topology** — two declared persistent paths that the *lookup
   directory* folds together, which A4 approval must reject, plus spellings that resolve to one
   ancestor/descendant topology without naming the same endpoint. Because that rule is scoped per
   parent directory rather than per volume, its conformance suite must cover:
   - **Mixed policies on one filesystem.** A `casefold`-enabled ext4 volume carrying both a plain parent
     and a `+F` (`FS_CASEFOLD_FL`) parent, proving a policy determined in one directory is never applied
     to another. This case is the reason the rule is not mount-scoped
     ([ext4 admin guide](https://cdn.kernel.org/doc/html/latest/admin-guide/ext4.html#case-insensitive-file-name-lookups)).
   - **Transaction-created parent inheritance.** A `CreateDirectory` beneath a `+F` parent, proving the
     created directory is evaluated under the inherited policy and not under the volume default.
   - **Absent-path alias refusal.** Two aliasing paths both declared `ABSENT`, refused **before capture**
     — the case where no inode exists to compare and identity alone cannot decide.
   - **The create/delete/re-create counterexample.** The `create x`, `delete x`, `create y` sequence of
     §5.4, asserting that A2 refuses fixed-key aliases and A4 refuses any remaining actual-policy alias.
     Every `O_EXCL` and no-clobber transfer in that sequence succeeds, so a suite that only exercises
     mutation-time guards would pass while the declared final states remain jointly unsatisfiable.
   - **Resolved ancestor topology.** On an insensitive parent, `A` and `a/x`, proving A4 re-runs both
     surface-tree consistency and created-directory-before-descendant ordering over resolved
     equivalence nodes rather than accepting A2's lexical-tree verdict.
4. **Concrete scratch distinctness** — all instantiated effect/role scratch leaves in each actual
   parent are pairwise distinct under that parent's policy. An intrinsic collision refuses approval and
   is unchanged by transaction-ID regeneration; separate external occupancy may trigger the bounded
   regeneration policy of §5.1.

### 13.4 End-to-end recovery

The synthetic exerciser (and later each real consumer) covers clean commit, caught rollback, kill during
each effect, kill during rollback, kill during a journal commit, kill after commit before cleanup, and
external drift at destructive boundaries. Restart happens in a fresh process and must converge or
preserve an explained halt.

### 13.5 Actual mutation surface

An always-on in-process interposer records successful mutating operations only after the syscall
succeeds. *(Amended 2026-08-13, the A7 design: the interposer is realized as the audited backend
facade — the single mutation surface production code can reach — never an `os`-level patch; the
operation list below names the audited operation classes that facade covers.)* Every target must be:

- a declared effect path;
- an engine-derived staging path; or
- an exact metadata path (including the SQLite database files and the ignore-marker `setxattr` on the
  metadata root).

It wraps `rename`, `replace`, `unlink`, `mkdir`, `rmdir`, `symlink`, `chmod`, **`fchmod`**, `link`,
mutating `open`, the `*at` and descriptor-relative variants (`renameat2`, `openat`, `linkat`,
`fchmodat`), and `setxattr`/`fsetxattr` applied to the metadata root, so no *engine-issued* metadata
mutation escapes the audit. SQLite's own I/O — the database, WAL, SHM, and any temporary file its pinned
profile (§7) can create — goes through its VFS and is **not** observable to this in-process interposer
(§7); that whole SQLite surface, bounded by the profile under the verified `metadata_root`, is excluded
from the audit, and the interposer's obligation there is only to prove no *effect* mutation targets it,
not to enumerate the library's internal writes. Because the effects set modes with `fchmod` through retained descriptors, the interposer
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
(§5.5) — not an all-or-nothing platform gate. The macOS arm is its own sub-plan, **A9**, following A8
*(amended 2026-08-13, the A7 design's banking commit: "progressive" was a strategy with no owner while
Plan A item 6 promised suites on both backends)*. Windows is out of scope: it has no `atomic_exchange`
primitive with the same crash-recovery table and no explicit directory-entry durability barrier, so a
Windows backend would need a divergent recovery classification; a Windows operation refuses at
preparation with `CapabilityUnavailable`. A Windows backend may be added later without changing the
transaction model.

A7 is delivered as **A7a** (the substrate: audited facade, spec/schema v2, `AssemblyHalt`, the chain,
the root and intent commands) and **A7b** (the executor: forward spine, five effects, plan executor,
trap removal), split 2026-08-13 at plan time on the design's §1 layering.

### Plan A — engine core and synthetic exerciser

1. Pure transaction/effect model, A2's factory-controlled `CompiledSpec`, and the executable recovery
   reference model (§5, §8.4, §13.1). `CompiledSpec` carries only filesystem-independent lexical/model
   proof.
2. A platform capability backend layer resolving the §5.5 vocabulary behind one interface: a Linux
   backend (`openat2` anchored traversal, `renameat2`, `fsync`) and a macOS backend
   (`openat`+`O_NOFOLLOW_ANY`, `renamex_np`/`renameatx_np`, `fcntl(F_FULLFSYNC)`), plus a per-volume
   capability probe that separates functional-availability probing from power-loss durability, and
   refuses an unsupported platform, a volume whose configuration tuple is outside the crash-tested
   durability allowlist (§5.5), or a SQLite-WAL-incapable volume before any transaction-record metadata
   or project mutation (the idempotent bootstrap of §5.5 necessarily precedes this refusal). Leaf syscall
   wrappers are **vendored/adapted**
   (not depended on) for `renameat2`
   / `openat2` on Linux and `renamex_np` / `F_FULLFSYNC` on macOS; the solved single-file case uses
   stdlib `os.replace`. This layer owns A4's factory-controlled `ProjectApprovedSpec`, concrete
   scratch-name distinctness, and resolved per-directory equivalence topology; A5–A8 accept that proof
   rather than raw `CompiledSpec`.
3. The SQLite-WAL metadata store (§7): schema, the project lock and universal recovery-resolve lease,
   blobs, and the preparation/per-effect commit ordering — including the idempotent bootstrap phase
   (§5.5) that precedes capability approval, and the **metadata-store I/O-layer decision** (§7): stdlib
   `sqlite3` with verified-directory resolution and an allowlisted-surface audit, versus an optional
   custom VFS (`openat`-anchored, `O_NOFOLLOW`, interposer-visible).
4. Coherent capture and the observation mechanism (§6, and §10's coherent-observation contract).
   Restartable materialization's staging-object classification ships with the effects that create the
   objects it classifies (item 5).
5. Five effects and the recovery executor (§8–§9).
6. Model, real-filesystem, subprocess-recovery, and persistence-cut suites, run on both backends (§13)
   — Linux at A7–A8; the macOS backend and its suite runs land with A9 *(amended 2026-08-13)*.
7. The synthetic exerciser and the end-to-end recovery matrix (§12.1, §13.4).

### Plan B — production adoption

Written only after Plan A's interfaces settle, and it must not redefine the engine protocol. One
consumer at a time, in the order §12.2 records (science's composition root, then science's plan
families; direct `nodes` adoption waits on a language-neutral execution seam):

1. science composition-root corpus-write adapter and its acceptance suite.
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

**One transaction, one root.** A `TransactionSpec` addresses a single engine root. Multi-corpus
operations at the consumer layer — science's merge, a cross-corpus entity move — are consumer-composed
sequences of per-root transactions; science's world-addressing design itself rules a merge non-atomic
over a world larger than the checkout, with correctness carried by the redirect record rather than by
atomicity. No multi-root transaction, cross-root lock ordering, or best-effort effect tier will be
added.

**The transaction is the publish, not the computation.** A long-running external computation
(science's execution boundary running a workflow engine for hours into a boundary-owned output root)
is not an engine transaction. The consumer completes and freezes its output manifest first and
compiles the spec from that settled surface, so the lease's write phase — and the lock hold — remains
a publication, never a computation.

**Tamper-evident mutation log (future obligation, not built here).** Science's epistemic-kernel and
computation-reproducibility designs name this engine as the eventual owner of a general tamper-evident
mutation log: every mutation durably registered *before* it is applied, in a sequence whose *removal
is detectable*. That contract is stricter than crash recovery — a recovery journal that can itself be
deleted is not tamper evidence — and it requires its own design, whose first question is where such a
log lives and how removal is detected across checkouts (an anchor outside the deletable set), not its
registration API. Two present-tense choices keep that path open. Terminal records and preimage blobs
are the natural witnesses of "the prior state, before the write," so their garbage collection (§7.5)
remains an explicit consumer policy, never an assumed cleanup. And the spec already carries a consumer
tag and frozen-intent digest (§5.1), persisted in the durable record, so a recovery-completed publish
remains attributable to the intent that authorized it.

**Designed 2026-08-03** (science's `2026-08-03-tamper-evident-log-design.md`; its §9 enumerates the
engine obligations, restated here so this document carries its own contract). The engine owns
registration — a per-engine-root hash chain at a reserved in-corpus path — and the obligations land
with A7–A8, not before (written as A6–A8; A6 landed 2026-08-08 carrying none of them), and are
recorded as deferred-obligation ledger entries #24–#29 *(captured 2026-08-13, the A7 design's
banking commit — the log design's §9 said they were entered at banking, and they had been
restated here instead)*:

1. **Pinned registration order, idempotent under recovery.** Durable `PREPARED` → durable
   `registered(txid, …)` chain entry → the transaction record durably stores the entry digest → first
   apply. Recovery finding a `PREPARED` transaction with an entry already appended appends **no second
   registration** — one registration per transaction id.
2. **Settlement is the completion barrier, both arms.** Durable terminal decision → durable
   `settled(committed | rolled-back)` append (ids must match the registration) → the transaction
   record durably binds the settlement digest — the acknowledgement — and only then is the terminal
   outcome returned and the lease released, for commit and rollback alike. Recovery backfills the
   binding; exactly one settlement per registration.
3. **Genesis carries a baseline.** Registering a root mints the chain genesis committing a baseline —
   sorted typed path/state fingerprints of the registered surface at registration — so pre-log history
   is reachable once anchored.
4. **The intent API is serialized and durable before return.** A consumer-authored intent entry is
   appended under the root's lease, durably, before the call returns its digest; the engine carries a
   boundary-supplied `fulfills` reference opaquely into the fulfilling transaction's registered entry.
5. **Terminal-record GC gates on settlement.** A terminal record is collectible only after its
   settlement entry is appended and bound (extending §7.5's explicit-consumer-policy rule).
6. **The log path is engine bookkeeping.** Appending the chain is not itself a registered mutation,
   and the log path sits outside the registered surface.

## 16. Acceptance criteria

The architecture is complete when:

- ordinary construction and `dataclasses.replace` cannot manufacture either staged proof, and every
  A5–A8 transaction entry point requires `ProjectApprovedSpec`;
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

## 2026-08-14 A7b amendments

Restartable `CreateDirectory` materialization uses a `0o700` scaffold: mkdir, entry-mode repair,
then umask-immune descriptor chmod to the approved mode. An observed-empty work-slot directory whose
mode is a subset of `0o700` is attributable construction debris; an opaque survivor halts.

Compilation requires `DirectoryState.mode & 0o700 == 0o700` and requires the postimage of
`CreateFileNoClobber` or `ReplaceFile` to include `0o400`. The engine refuses to construct a tree it
cannot later re-observe without mutation. The fresh-process acceptance arm therefore reads:
recovery rolls back every uncommitted transaction or leaves an explained halt when the world
withholds evidence rollback needs. The caught-failure criterion has the same explained-halt arm.
