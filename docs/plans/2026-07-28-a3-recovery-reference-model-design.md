# A3 executable recovery reference model — design

**Date:** 2026-07-28
**Status:** Draft for owner review. The design direction was approved conversationally on 2026-07-28;
the written contract has not yet passed owner review.
**Depends on:** A1 core model and A2 compilation validation (implemented)
**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md)
§8.4 and §13.1
**Successor:** A4 rooted project approval and platform capabilities

## 1. Decision

A3 implements the engine's pure, executable recovery authority. It classifies one immutable logical
snapshot into a frozen semantic recovery plan, authorizes each plan step against a fresh coherent
observation, and applies the same plan to an abstract snapshot for convergence testing:

```python
classify_recovery(snapshot: RecoverySnapshot) -> RecoveryPlan

authorize_recovery_step(
    plan: RecoveryPlan,
    step_index: int,
    observed: JointObservation,
) -> AuthorizedStep | HaltPlan

apply_recovery_plan(
    snapshot: RecoverySnapshot,
    plan: RecoveryPlan,
) -> RecoverySnapshot
```

This is production architecture, not a test-only oracle. A7 consumes A3's decisions and may not
reimplement recovery classification. A7 still owns the durable filesystem operations that realize an
authorized semantic step; A3 contains no syscalls, SQLite, concrete scratch names, capability probes,
or durability barriers.

The pure reducer is part of the normative model. A classifier alone cannot execute §13.1's convergence
and second-pass-idempotence claims. The reducer changes logical observations and journal state only; it
does not prescribe a backend's syscall sequence.

## 2. Scope and non-scope

A3 owns:

- the closed transaction and effect journal-state vocabulary;
- logical observation and identity evidence;
- per-path forward and reverse frontier reconstruction;
- validation of transaction-wide journal topology;
- one joint classification for each in-flight effect;
- complete ordered rollback, refused-rollback, committed-cleanup, terminal-detachment, and halt plans;
- exact step preconditions and authorization;
- structured halt diagnostics;
- a pure abstract interpreter for those plans; and
- exhaustive model and property tests.

A3 does not own:

- project/root containment, path resolution, actual lookup policy, or concrete scratch leaves (A4);
- SQLite rows, transaction leases, or durable metadata transitions (A5);
- coherent filesystem capture, planned bytes, or restartable materialization (A6);
- syscalls, held descriptors, fsync ordering, or effect execution (A7); or
- real-filesystem, subprocess, or persistence-cut conformance (A8).

A3 remains stdlib-only at runtime and performs no filesystem I/O.

## 3. Seam review and authority corrections

The pre-plan seam review found no ledger row whose first owner was A3, but deriving the executable
contract exposed four authority seams that must be corrected with this design:

1. §13.1 still names raw `TransactionSpec` as the classifier input. A3 operates on the exact
   factory-issued `CompiledSpec`, plus logical recovery evidence. It never revalidates or accepts a raw
   spec.
2. `PathState` fingerprints cannot express the same-entry relations required by `MoveNoClobber` and
   `CreateDirectory`. Observations need opaque snapshot-local entry identities.
3. `PathState` also cannot express an attributable file prefix or whether a directory contains
   unmodeled children. Those facts are inputs to §10's recovery rules and require coherent observation
   evidence.
4. A2's exact-spelling tree is insufficient for abstract directory convergence after A4 resolves
   actual per-directory equivalence. A3 needs A4's resolved logical topology in production.

The authority and deferred-obligation ledger change in the same commit as this design. No A3 API may
silently delegate these questions to a later plan.

## 4. Architecture and ownership

A3 is a focused `atoms.core.recovery` package. Its public surface is the three pure operations in §1
and the frozen values needed to call and inspect them. Frontier reconstruction, journal-topology
validation, and variant classifiers are internal modules.

The package boundary is:

```text
CompiledSpec + resolved logical topology + journal state + coherent observations
                                   |
                                   v
                         classify_recovery
                                   |
                                   v
                        frozen RecoveryPlan
                         /                 \
                        v                   v
        authorize_recovery_step       apply_recovery_plan
                        |                   |
                        v                   v
              A7 durable executor     abstract next snapshot
```

`RecoveryPlan` is a closed union of factory-controlled `ActionPlan`, `HaltPlan`, and
`NoRecoveryPlan` dataclasses; it is not an inheritance hierarchy. `AuthorizedStep` is likewise
factory-controlled. Ordinary public-field construction and `dataclasses.replace` refuse. This is the
same conventional Python boundary as `CompiledSpec`: it prevents ordinary in-repository fabrication,
not hostile code using private state or `object.__new__`.

`RecoverySnapshot` is not project approval. Pure model tests may construct validated synthetic
snapshots. Production mutation still requires A4's factory-issued `ProjectApprovedSpec`; A7 constructs
the production snapshot from that composed proof and its fresh observations. Architecture tests must
reject any A7 entry point accepting raw `TransactionSpec`, raw `CompiledSpec`, or a synthetic snapshot
without the approved project context.

## 5. Closed state and evidence model

### 5.1 Durable states

A3 defines closed string enums whose values A5 later persists verbatim:

```text
TransactionState:
    PREPARED
    APPLYING
    APPLIED
    COMMITTED
    ROLLING_BACK
    ROLLED_BACK
    HALTED

JournalState:
    PENDING
    STARTED
    DONE
    UNDO_STARTED
    UNDONE

RollbackResult:
    RESTORED
    EXTERNAL_DRIFT_PRESERVED
```

The snapshot also records whether this transaction is the active binding. That fact is needed to model
terminal detachment and a second recovery pass. `RollbackResult` is absent until the durable
`ROLLED_BACK` transition; that transition records exactly one result so a crash before active
detachment cannot erase whether the engine owes a clean rollback outcome or `PreconditionRefused`.

### 5.2 Logical scratch roles

Each effect has one logical scratch slot:

| Variant | `ScratchRole` | Location class |
| --- | --- | --- |
| `ReplaceFile` | `STAGING` | same parent as the persistent target |
| `CreateFileNoClobber` | `STAGING` | same parent as the persistent target |
| `DeletePath` | `TOMBSTONE` | same parent as the persistent target |
| `MoveNoClobber` | `ANCHOR` | source-side effect parent selected by A4/A7 |
| `CreateDirectory` | `WORK` | protected metadata `work/<txid>/` namespace |

Reverse settlement reuses the same effect-derived slot. Create rollback quarantines a live entry into
`STAGING`; directory rollback returns a live directory to `WORK`. A3 introduces no second undo name
that A4 did not approve.

The roles are logical. A3 neither constructs nor sees `.#~<txid>.<effect-id>.<role>` leaves.

### 5.3 Resolved logical topology

`RecoveryTopology` is a pure node/parent relation covering:

- every persistent path in the compiled timelines;
- every effect/scratch-role slot; and
- the project and protected-work roots needed to distinguish their parent domains.

It retains A4's resolved ancestor relation after actual per-directory name equivalence is applied. It
contains no descriptors, absolute paths, device numbers, inode numbers, or backend objects.

A3 validates structural properties that are decidable from the value: complete coverage, one parent
per non-root node, acyclicity, endpoint distinctness, and agreement with the compiled effect/scratch
role set. Only A4 can prove that the topology matches one real held project context. A4 therefore
becomes the sole production construction authority and stores this topology inside
`ProjectApprovedSpec`.

### 5.4 Entry observations

An observed entry is a closed union:

```text
ObservedAbsent
ObservedFile(state: FileState, identity: EntryIdentity)
ObservedSymlink(state: SymlinkState, identity: EntryIdentity)
ObservedDirectory(
    state: DirectoryState,
    identity: EntryIdentity,
    has_unmodeled_child: bool,
)
```

`EntryIdentity` is an opaque, snapshot-local equality token. A3 may only compare two identities for
equality. Tokens are not serialized, logged through arbitrary `repr`, or interpreted as `st_dev` /
`st_ino`.

Directory emptiness is derived from:

- present modeled direct children in `RecoveryTopology`; and
- `has_unmodeled_child`.

This lets the reducer prove that reversing declared descendants makes a transaction-created directory
empty while still refusing removal when any undeclared entry remains. The directory evidence is
obtained from one descriptor-coherent enumeration by A6/A7.

Every file-valued `STAGING` observation also carries its coherent relation to that effect's planned
postimage:

```text
FileBuildRelation:
    EXACT
    STRICT_PREFIX
    DIVERGED
```

A3 cannot derive `STRICT_PREFIX` from a digest and byte length. A6/A7 compares the candidate stream
against the planned blob and produces this evidence from the same opened object used for the state and
identity observation. The relation is evidence, not a recovery verdict: A3 decides whether it denotes
incomplete construction, a displaced preimage, divergence, or an unattributable object in the current
joint tuple.

### 5.5 Recovery snapshot

`RecoverySnapshot` is frozen and complete:

- it contains the exact `CompiledSpec`;
- journal rows occur exactly once in compiled effect order;
- persistent observations cover exactly the union of compiled timeline paths;
- scratch observations cover exactly the required effect/role pairs;
- repeated paths have one current live observation interpreted through the whole timeline;
- the topology covers the same persistent and scratch nodes;
- all observation evidence is internally coherent; and
- the active-binding state and optional terminal rollback result are explicit.

Missing, extra, duplicated, or wrongly typed members are refused as malformed internal input. Exact
runtime types define membership in every closed union; subclasses are not members.

## 6. Transaction classifier

`classify_recovery` runs a fixed semantic pipeline:

1. validate the transaction/journal topology;
2. reconstruct every declared path's forward or reverse frontier;
3. validate all persistent and scratch evidence for every effect;
4. classify the one in-flight effect jointly across its persistent paths and scratch role; and
5. emit the complete ordered plan only after every observation has been classified.

No step is emitted incrementally while later evidence remains unchecked. Recovery therefore cannot
destroy early evidence before discovering a later conflict.

### 6.1 Transaction-state authority

| Transaction state | Legal journal shape | Decision |
| --- | --- | --- |
| `PREPARED` | all `PENDING` | No project mutation; record rollback, preserve any external drift, detach |
| `APPLYING` | `DONE* STARTED? PENDING*` | Settle the in-flight effect, undo attributable effects in reverse order |
| `APPLIED` | all `DONE` | No commit exists; undo every effect in reverse order |
| `ROLLING_BACK` | `DONE* (STARTED \| UNDO_STARTED)? UNDONE* PENDING*` | Resume from the one forward-or-reverse frontier |
| `COMMITTED` | all `DONE` | Rollback forbidden; verify final surface, clean retained scratch, detach |
| `ROLLED_BACK` | `UNDONE* PENDING*` | Detach metadata only; never mutate project or scratch |
| `HALTED` | the frozen journal topology recorded by the first halt | Stable halt; no further state change |

The optional `STARTED` or `UNDO_STARTED` is the single frontier between effects not yet reversed and
effects already reversed. `STARTED` represents a forward effect interrupted before rollback could
claim it; `UNDO_STARTED` represents an interrupted reverse settlement. A well-typed but illegal
combination produces a halt plan and preserves evidence; it is never normalized into a plausible
history.

The compiled effect sequence is the only execution order. `dependencies` carry no scheduling
information and never affect A3 classification or A7 execution.

### 6.2 Frontier rules

Forward and reverse path frontiers follow authority §8.4. A `PENDING` frontier whose live entry differs
from the preceding post-state is external drift. It is preserved and produces a refused rollback
outcome. A completed frontier whose live or retained scratch evidence is inconsistent is
unattributable and halts.

All persistent paths owned by one effect are classified together. A move can never be "landed" for
its destination while independently "not landed" for its source.

## 7. Recovery plan and authorization

### 7.1 Plan dispositions

The closed plan union is:

```text
ActionPlan:
    ROLL_BACK
    ROLL_BACK_REFUSED
    COMMITTED_CLEANUP
    DETACH_TERMINAL

HaltPlan:
    HALT

NoRecoveryPlan:
    NO_RECOVERY
```

`ROLL_BACK_REFUSED` means the transaction reaches durable rollback while preserving proved external
drift. An action plan fixes the corresponding terminal `RollbackResult`; A5 must persist that value in
the same metadata transaction as `ROLLED_BACK`. `NO_RECOVERY` is the fixed point for a detached
terminal transaction.

### 7.2 Ordered semantic steps

Plan steps form a closed union:

```text
TransitionTransactionState
TransitionEffectState
TransformEffectTuple
RemoveScratch
PreserveExternal
DetachActive
```

`TransformEffectTuple` carries:

- the effect ID and exact variant;
- a closed settlement kind;
- the complete expected persistent-and-scratch before-tuple;
- the resulting logical after-tuple; and
- required identity, prefix, and occupancy relations.

Settlement kinds express intent — restore pre-state, remove an attributable creation, repair an
intermediate to pre-state, or finish an already-landed undo. They do not name `renameat2`, `unlink`,
`rmdir`, `fsync`, or another backend primitive.

Journal and transaction transitions occur explicitly in the same order A5/A7 must make durable.
Skipping, duplicating, or reordering a step is `ProtocolError`.

### 7.3 Source binding and fresh authorization

Every plan is bound to the exact source snapshot. Applying it to another snapshot is `ProtocolError`;
plans are not reusable capabilities.

Before each filesystem-mutating step, A7 obtains one fresh coherent joint observation and calls
`authorize_recovery_step`. Exact agreement returns a factory-controlled `AuthorizedStep`. Any mismatch
returns a halt plan with stable reason `PLAN_PRECONDITION_CHANGED`. A7 may not silently retry,
reclassify, or reinterpret the stale step.

The authorization binds the plan, step index, and fresh evidence. A mismatch halt is bound to the
logical prefix snapshot obtained by reducing every already-completed plan step, with the fresh
conflicting observation substituted at the selected step. It is not incorrectly bound to the plan's
original source snapshot. Held descriptors and atomic capabilities close the remaining
observation-to-operation window according to §§6 and 9 of the authority design.

### 7.4 Halt diagnostics

A halt plan contains no project or scratch mutation. A first halt may persist only:

- the transition to transaction state `HALTED`; and
- its structured diagnostic.

The diagnostic contains the transaction state, effect and logical paths when applicable, journal
state, expected and observed tuples, a stable reason code, and a non-mutating operator action. Human
prose is derived from this structure and is not the machine contract.

## 8. Abstract reducer

`apply_recovery_plan` checks source binding, then interprets the ordered steps:

- entry transfers preserve opaque identity;
- removal produces `ObservedAbsent`;
- preserved external entries remain unchanged;
- directory occupancy is recomputed through the resolved topology;
- journal and transaction states advance only through allowed edges; and
- the terminal rollback transition records its exact `RollbackResult`; and
- terminal detachment clears the abstract active binding.

It produces these fixed points:

| Outcome | Result |
| --- | --- |
| successful uncommitted recovery | `ROLLED_BACK`, `RESTORED`, detached, initial surface restored |
| refused rollback | `ROLLED_BACK`, `EXTERNAL_DRIFT_PRESERVED`, detached, proved external drift preserved |
| committed cleanup | `COMMITTED`, detached, final surface retained |
| terminal detachment | existing terminal state, detached |
| first halt | observations unchanged, active retained, transaction `HALTED` |
| already halted | identical stable halt |
| detached terminal snapshot | `NO_RECOVERY`, identity reduction |

The core property is:

```python
next_snapshot = apply_recovery_plan(snapshot, classify_recovery(snapshot))
fixed_snapshot = apply_recovery_plan(
    next_snapshot,
    classify_recovery(next_snapshot),
)
assert fixed_snapshot == next_snapshot
```

## 9. Variant classifiers

Notation below:

- `A` — absent;
- `pre` / `post` — exact declared state;
- `prefix` — proved strict prefix of the planned file;
- `X` — another present state;
- `==` between present entries — equal opaque identity.

Every tuple not listed is unattributable and halts.

### 9.1 `ReplaceFile`: `(live, staging)`

Forward `STARTED`:

| Tuple | Classification and settlement |
| --- | --- |
| `(pre, A)` | exchange did not land; undo without project mutation |
| `(pre, prefix)` or `(pre, post)` | unpublished staging; remove it and undo |
| `(post, pre)` | exchange landed; exchange back, validate `pre`, remove displaced `post` |
| `(post, X)` | unvalidated entry was displaced; exchange it back while live remains exact `post`, preserve it as external drift, finish refused rollback |

Reverse `UNDO_STARTED` accepts both atomic sides:

| Tuple | Settlement |
| --- | --- |
| `(post, pre)` | retry exchange |
| `(pre, post)` | exchange landed; finish scratch removal |
| `(pre, A)` | undo and cleanup already landed |

Committed cleanup accepts `(post, pre)` or `(post, A)`, where absent scratch means cleanup already
landed. `live=post` without the displaced entry is not attributed during uncommitted recovery.

### 9.2 `CreateFileNoClobber`: `(live, staging)`

Forward `STARTED`:

| Tuple | Classification and settlement |
| --- | --- |
| `(A, A)`, `(A, prefix)`, `(A, post)` | publication did not land; clean attributable staging |
| `(post, A)` | publication landed; quarantine and remove the live postimage |
| `(post, present staging)` | surviving staging proves publication did not land; preserve byte-equivalent blocker, clean staging, refuse |
| `(X, attributable staging or A)` | differing destination proves no-clobber publication did not land; preserve blocker, clean attributable staging, refuse |

Reverse `UNDO_STARTED`:

| Tuple | Settlement |
| --- | --- |
| `(post, A)` | quarantine has not landed |
| `(A, post)` | quarantine landed; finish validated removal |
| `(A, A)` | removal already landed |

A changed live target during undo or foreign staging evidence halts. Committed state requires
`(post, A)`.

### 9.3 `DeletePath`: `(live, tombstone)`

Forward `STARTED`:

| Tuple | Classification and settlement |
| --- | --- |
| `(pre, A)` | transfer did not land |
| `(A, pre)` | deletion landed; restore tombstone no-clobber |
| `(X, pre)` | original survives but restoration is blocked; halt with both preserved |

Reverse `UNDO_STARTED` accepts `(A, pre)` to retry restoration and `(pre, A)` as already restored.
`(A, A)` does not prove a completed deletion: absence without the retained tombstone is
unattributable.

Committed cleanup accepts `(A, pre)` or `(A, A)`.

### 9.4 `MoveNoClobber`: `(source, destination, anchor)`

Every attributable present entry is exact `pre`; identity is decisive:

| Tuple and identity | Classification and settlement |
| --- | --- |
| `(pre, A, A)` | nothing landed |
| `(pre, A, pre)`, source `==` anchor | anchor established, move did not land; remove anchor |
| `(A, pre, pre)`, destination `==` anchor | move landed; restore source from anchor-owned destination |
| `(pre, pre, pre)`, all equal | dual-name persistence intermediate; remove destination |
| `(A, A, pre)` | anchor-only intermediate; restore source from anchor |
| source `==` anchor with a foreign destination | move did not land; preserve destination, remove anchor, refuse |

A foreign source, diverged anchor, non-anchor destination where ownership is required, or postimage
without anchor halts.

`UNDO_STARTED` accepts the same atomic endpoints and two persistence intermediates. Every repair
converges to `(pre, A, anchor)` and then removes the anchor.

Committed cleanup requires `(A, pre, anchor)` with destination `==` anchor, or `(A, pre, A)` after
anchor cleanup. Source reappearance or destination divergence never licenses rollback.

### 9.5 `CreateDirectory`: `(live, work)`

The classifier evaluates modeled children through `RecoveryTopology` before deciding whether eventual
empty removal is possible:

| Tuple and identity | Classification and settlement |
| --- | --- |
| `(A, A)` | nothing landed |
| `(A, attributable work)` | publication did not land; remove empty work directory |
| `(post, A)` | publication landed; quarantine live into `WORK`, validate emptiness, remove |
| `(post, post)`, live `==` work | publication landed but work-name removal did not; rollback removes both logical names and the directory |
| live blocker plus attributable different-identity work | publication did not land; preserve blocker, remove work, refuse |

A live divergence without surviving work is unattributable. Any unmodeled child that would prevent
eventual removal halts before recovery mutation.

`UNDO_STARTED` accepts live-only, work-only, same-identity dual-name, and fully absent atomic endpoints,
converging to `(A, A)`.

A `DONE` or `COMMITTED` directory effect requires `(post, A)`. Work removal is durable before `DONE`;
a work survivor beside `DONE` is a contradiction, not committed cleanup.

### 9.6 Intentional differences across variants

Scratch absence does not have one global meaning:

- `CreateFileNoClobber` can classify its exact postimage with no staging survivor as landed, subject to
  the authority's fingerprint-equivalent-recreation non-guarantee.
- Replace and delete need the displaced entry or tombstone to prove their uncommitted joint tuple.
- Move needs the anchor for uncommitted identity attribution.
- Create-directory work must already be absent at `DONE`.
- Retained replace staging, delete tombstones, and move anchors may be absent during committed cleanup
  because cleanup itself can have landed before a crash.

A shared "scratch missing means landed" fallback is forbidden.

## 10. Error and totality contract

Every well-formed snapshot produces a plan, including refused and halted outcomes.

- Semantically contradictory durable states produce `HaltPlan`.
- Malformed internal Python values, wrong exact runtime types, forged plans, skipped steps, and
  source-binding violations raise `ProtocolError`.
- Unexpected implementation exceptions propagate unchanged. There is no pipeline-wide catch or
  relabeling as a domain refusal.
- `PreconditionRefused` and `TransactionHalted` are surfaced by the coordinator only after the
  corresponding A3 plan reaches its durable outcome.

Closed variants dispatch by exact runtime type, keeping the gate and lookup table identical. Error
messages do not interpolate arbitrary identity-token representations or other unbounded
caller-controlled values.

## 11. Verification

A3 tests are deterministic and filesystem-free.

### 11.1 Table coverage

Table tests cover every variant across:

- forward `STARTED`;
- reverse `UNDO_STARTED`;
- completed-effect rollback;
- committed cleanup;
- named forward and reverse intermediates;
- no-clobber blockers;
- missing retained evidence;
- foreign or diverged evidence; and
- idempotently completed cleanup.

### 11.2 Finite generators

Bounded exhaustive generators cover:

- every legal and illegal journal topology for short transactions;
- repeated-path timelines;
- every equality partition of move and directory identity tokens;
- exact, prefix, diverged, absent, and wrong-kind observations;
- lexical and A4-resolved ancestor topologies; and
- declared-only versus unmodeled directory occupancy.

Identity tokens are generated as equality partitions rather than filesystem numbers. Renaming every
token while preserving the partition must preserve the classification.

### 11.3 Properties

Properties lock:

- deterministic classification;
- whole-effect joint decisions;
- all-evidence-before-any-action;
- rollback restoration or explicit preserved drift;
- committed recovery never emits rollback;
- halt preserves filesystem evidence;
- `dependencies` never affect ordering;
- plan/source binding and exact step authorization;
- identity-token renaming invariance;
- reducer convergence and second-pass idempotence;
- totality for every well-formed generated snapshot; and
- internal faults are not normalized into domain refusals.

Mutation checks must demonstrate that the tests fail when:

- a move is classified per path rather than jointly;
- committed and applied states share a decision;
- a stale step is authorized;
- resolved topology is replaced by lexical topology;
- scratch absence is treated uniformly across variants; or
- the reducer omits a reverse intermediate.

## 12. Deferred obligations created or refined

This design refines existing ledger entries for ancestor topology, dependencies, and resolved
equivalence. It also creates downstream obligations:

- A4 constructs and retains the resolved logical topology A3 consumes in production.
- A5 persists A3's transaction/effect transitions and structured halt diagnostics in plan order.
- A6/A7 produce coherent identity, prefix, and directory-occupancy evidence.
- A7 executes only A3-authorized steps and does not rederive recovery decisions.
- A8 tests the real executor against A3's decisions and fixed points.

They remain open until the owning implementation and its verification land.

## 13. Acceptance criteria

A3 is complete when:

- all three pure APIs and their closed types are implemented;
- all five variant tables are executable;
- legal and illegal journal topologies are exhaustive and tested;
- classifier output is deterministic and factory-controlled;
- step authorization refuses stale evidence;
- the abstract reducer reaches the specified fixed points;
- every generated well-formed snapshot classifies without an unexpected exception;
- the A3 suite, the full repository suite, Ruff, and Pyright pass; and
- status docs name A3 as implemented while leaving A4–A8 visibly unimplemented.

No filesystem call, SQLite layer, concrete scratch-name binding, `ProjectApprovedSpec`
implementation, compatibility API, or A7 effect operation belongs in A3.
