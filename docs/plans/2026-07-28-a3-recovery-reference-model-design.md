# A3 executable recovery reference model — design

**Date:** 2026-07-28
**Status:** Approved on 2026-07-28 after owner review. Implementation is governed by
[`2026-07-28-plan-a3-recovery-reference-model.md`](2026-07-28-plan-a3-recovery-reference-model.md),
which remains under review.
**Depends on:** A1 core model and A2 compilation validation (implemented)
**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md)
§8.4 and §13.1
**Successor:** A4 rooted project approval and platform capabilities

## 1. Decision

A3 implements the engine's pure, executable recovery authority. It classifies one immutable logical
snapshot into a frozen semantic recovery plan, authorizes each plan step against a fresh coherent
observation, and applies the same plan to an abstract snapshot for convergence testing:

```python
build_recovery_snapshot(
    *,
    compiled: CompiledSpec,
    topology: RecoveryTopology,
    transaction_state: TransactionState,
    commit_decision: CommitDecision,
    rollback_result: RollbackResult | None,
    halt_diagnostic: HaltDiagnostic | None,
    active: bool,
    journals: tuple[EffectJournalState, ...],
    persistent_observations: tuple[PersistentObservation, ...],
    scratch_observations: tuple[ScratchObservation, ...],
) -> RecoverySnapshot

classify_recovery(snapshot: RecoverySnapshot) -> RecoveryPlan

authorize_recovery_step(
    plan: RecoveryPlan,
    step_index: int,
    observed: JointObservation,
) -> AuthorizedStep | HaltPlan

reduce_recovery_plan_prefix(
    snapshot: RecoverySnapshot,
    plan: RecoveryPlan,
    completed_steps: int,
) -> RecoverySnapshot

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

At the time of the pre-plan seam review, no ledger row named A3 as its first owner. Deriving the
executable contract then exposed five authority seams that this design corrected:

1. §13.1 named raw `TransactionSpec` as the classifier input. A3 instead operates on the exact
   factory-issued `CompiledSpec`, plus logical recovery evidence; the authority now says so. A3 never
   revalidates or accepts a raw spec.
2. `PathState` fingerprints cannot express the same-entry relations required by `MoveNoClobber` and
   `CreateDirectory`. Observations need opaque snapshot-local entry identities.
3. `PathState` also cannot express an attributable file prefix or whether a directory contains
   unmodeled children. Those facts are inputs to §10's recovery rules and require coherent observation
   evidence.
4. A2's exact-spelling tree is insufficient for abstract directory convergence after A4 resolves
   actual per-directory equivalence. A3 needs A4's resolved logical topology in production.
5. Occurrence-local committed tuples are unsound for repeated paths: only the last occurrence's
   post-state is live. Committed cleanup therefore proves the transaction final surface once, then
   classifies scratch independently of occurrence-local persistent evidence.

The authority and deferred-obligation ledger change in the same commit as this design. No A3 API may
silently delegate these questions to a later plan.

## 4. Architecture and ownership

A3 is a focused `atoms.core.recovery` package. Its public surface is the five pure operations in §1
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

`build_recovery_snapshot` is the sole validating construction authority for `RecoverySnapshot`. It
requires exact closed types, exhaustive observation coverage, and a structurally valid topology before
constructing the frozen value. Ordinary construction and `dataclasses.replace` refuse. A snapshot is
still not project approval: pure model tests may pass validated synthetic inputs, while production
mutation requires A4's factory-issued `ProjectApprovedSpec`. A7 calls the same factory with the
compiled proof and topology from that composed approval plus fresh observations. Architecture tests
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

CommitDecision:
    UNCOMMITTED
    COMMITTED

JournalState:
    PENDING
    STARTED
    DONE
    UNDO_STARTED
    UNDONE

RollbackResult:
    RESTORED
    EXTERNAL_DRIFT_PRESERVED

HaltReason:
    JOURNAL_TOPOLOGY_INVALID
    COMMIT_DECISION_CONFLICT
    ACTIVE_BINDING_MISSING
    EFFECT_TUPLE_UNATTRIBUTABLE
    DIRECTORY_NOT_EMPTY
    COMMITTED_SURFACE_MISMATCH
    PLAN_PRECONDITION_CHANGED
```

`CommitDecision` models §7.2's durable `committed` column independently from `TransactionState`. It
changes to `COMMITTED` with the logical commit decision and is never cleared by a later halt. A halt
after commit therefore remains distinguishable from a halt while applying and can never license
rollback.

The snapshot also records whether this transaction is the active binding. That fact is needed to model
terminal detachment and a second recovery pass. `RollbackResult` is absent until the durable
`ROLLED_BACK` transition; that transition records exactly one result so a crash before active
detachment cannot erase whether the engine owes a clean rollback outcome or `PreconditionRefused`.

`HaltDiagnostic` is absent before the first halt and present exactly when `transaction_state` is
`HALTED`. It freezes the pre-halt transaction state, the unchanged `CommitDecision`, the durable
journal state vector in compiled effect order, any separately labeled projected cursor state,
token-free expected and observed evidence, one closed `HaltReason`, and the operator action. A second
pass returns that stored diagnostic rather than recomputing it with `HALTED` as the origin state.

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
ObservedSymlink(state: SymlinkState)
ObservedDirectory(
    state: DirectoryState,
    identity: EntryIdentity,
    has_unmodeled_child: bool,
)
```

`EntryIdentity` is an opaque, snapshot-local equality token carried only by regular files and
directories. A3 may only compare two identities for equality. Tokens are not serialized, logged
through arbitrary `repr`, or interpreted as `st_dev` / `st_ino`.

Within one coherent observation, A6/A7 allocate exactly one token for each observed underlying entry
and reuse it in every named slot that denotes that entry. Distinct entries receive distinct tokens.
Every separately captured observation creates a fresh token universe: identity meaning is the
equality partition among named slots, never equality with a token from an earlier observation.
Classification and plan construction are identity-conservative: they may copy or move tokens already
present in the source snapshot, but never allocate an `EntryIdentity`. Only an observation producer
creates tokens.

A symlink carries no A3 identity. Its observation is the weaker `lstat` + `readlink`
`symlink_fingerprint` contract from authority §§5.5 and 6; it is not descriptor-coherent and A3 never
uses symlink identity for an ownership decision. Delete/restore classification compares the exact
`SymlinkState` and retained tombstone tuple instead.

Directory emptiness is derived from:

- present modeled direct children in `RecoveryTopology`; and
- `has_unmodeled_child`.

This lets the reducer prove that reversing declared descendants makes a transaction-created directory
empty while still refusing removal when any undeclared entry remains. The directory evidence is
obtained from one descriptor-coherent enumeration by A6/A7.

Some forward-construction `STAGING` observations also carry their coherent relation to that effect's
planned postimage:

```text
FileBuildRelation:
    EXACT
    STRICT_PREFIX
    DIVERGED
```

A3 cannot derive `STRICT_PREFIX` from a digest and byte length. A6/A7 compares the candidate stream
against the planned blob and produces this evidence from the same opened object used for the state and
identity observation. The relation is evidence, not a recovery verdict: A3 decides whether it denotes
incomplete construction, divergence, or an unattributable object in the current joint tuple.

The relation is required only where the forward classifier can consume construction evidence:

- a present file in `CreateFileNoClobber`'s `STAGING` while that effect is `STARTED`; or
- a present file in `ReplaceFile`'s `STAGING` while that effect is `STARTED` and the live entry is the
  exact declared `pre`.

It is absent everywhere else. In particular, a displaced preimage retained by a completed
`ReplaceFile`, a quarantined postimage during reverse settlement, and committed-cleanup scratch are
not stream-compared against the planned blob. Uncommitted settlement uses the complete atomic tuple;
after the transaction-wide final-surface proof, committed cleanup uses only the exact retained scratch
state.

### 5.5 Recovery snapshot

The factory's repeated members are frozen exact dataclasses:

```text
EffectJournalState(effect_id: str, state: JournalState)
PersistentObservation(path: RelPath, entry: ObservedEntry)
ScratchObservation(
    effect_id: str,
    role: ScratchRole,
    entry: ObservedEntry,
    file_build_relation: FileBuildRelation | None,
)
```

`file_build_relation` is present exactly in the two forward-construction cases in §5.4 and absent for
every other entry/role/journal combination. The snapshot factory validates this invariant rather than
allowing irrelevant evidence or omitting evidence the classifier will read.

`RecoverySnapshot` is frozen and complete:

- it contains the exact `CompiledSpec`;
- journal rows occur exactly once in compiled effect order;
- persistent observations cover exactly the union of compiled timeline paths;
- scratch observations cover exactly the required effect/role pairs;
- repeated paths have one current live observation interpreted through the whole timeline;
- the topology covers the same persistent and scratch nodes;
- all observation evidence is internally coherent; and
- the commit decision, active-binding state, optional terminal rollback result, and optional frozen
  halt diagnostic are explicit.

Missing, extra, duplicated, or wrongly typed members are refused as malformed internal input. Exact
runtime types define membership in every closed union; subclasses are not members. For a `HALTED`
input, the current commit decision and journal vector must equal the values frozen in
`halt_diagnostic`; a mismatch is malformed persisted input and is refused as `ProtocolError`, not
normalized into a second halt.

### 5.6 Joint observation

`JointObservation` is the frozen fresh evidence for one filesystem-mutating plan step. It contains
exactly the persistent and scratch node observations named by that step's expected before-tuple, plus
the affected parent-directory occupancy evidence named by the step. It contains no journal or
transaction state: A5 supplies those durable facts, while A7 supplies the descriptor-coherent
filesystem evidence.

Ordinary rollback steps name the effect's complete persistent-and-scratch coverage. The deliberate
exception is a committed cleanup `RemoveScratch`: after the transaction classifier has proved the
complete final surface once, its `JointObservation` contains exactly one retained scratch slot and no
persistent or occupancy entries. The reducer accepts that shape only for an exact `COMMITTED`
transaction, the named effect at exact `DONE`, and the effect's exact required retained-scratch
key/role. Every noncommitted `RemoveScratch` still requires complete effect coverage.

`authorize_recovery_step` validates the joint observation's exact node coverage and closed runtime
types. Missing, extra, duplicated, or incoherent evidence is `ProtocolError`; well-shaped evidence
that differs from the plan precondition yields `PLAN_PRECONDITION_CHANGED`. Agreement means exact
equality of every non-identity field plus equality of the named-slot identity partition. It never
compares an expected token with a freshly observed token, because those values belong to different
token universes.

## 6. Transaction classifier

`classify_recovery` first validates transaction authority, then selects one of two fixed semantic
pipelines.

For an uncommitted transaction the pipeline:

1. validate the transaction/journal topology;
2. reconstruct every declared path's forward or reverse frontier;
3. validate all persistent and scratch evidence for every effect;
4. classify the at-most-one in-flight effect jointly across its persistent paths and scratch role; and
5. emit the complete ordered plan only after every observation has been classified.

For `COMMITTED` with all effects `DONE`, it bypasses frontier reconstruction and the ordinary
`classify_effect` path:

1. compare the one current observation for every compiled path with the complete compiled final
   surface;
2. only after that succeeds, classify the complete scratch vector through the proof-gated committed
   cleanup helper;
3. accept exact retained replace/delete/move scratch for removal or absence as already cleaned;
4. require create-file staging and create-directory `WORK` to be absent; and
5. after every scratch slot passes, emit scratch-only removals in compiled order and detach.

Any retained scratch state/kind mismatch or create staging/`WORK` survivor halts without cleanup
steps. The complete final-surface proof is the sole persistent predicate for this branch, so repeated
paths are not compared against every occurrence-local post-state.

No step is emitted incrementally while later evidence remains unchecked. Recovery therefore cannot
destroy early evidence before discovering a later conflict.

### 6.1 Transaction-state authority

| Transaction state | Legal journal shape | Decision |
| --- | --- | --- |
| `PREPARED` | all `PENDING` | No project mutation; record rollback, preserve any external drift, detach |
| `APPLYING` | `DONE* STARTED? PENDING*` | Settle the in-flight effect, undo attributable effects in reverse order |
| `APPLIED` | all `DONE` | No commit exists; undo every effect in reverse order |
| `ROLLING_BACK` | `DONE* STARTED? PENDING*` **or** `DONE* UNDO_STARTED? UNDONE* PENDING*` | Resume from the one forward-or-reverse frontier |
| `COMMITTED` | all `DONE` | Rollback forbidden; prove the complete final surface once, validate scratch transaction-wide, clean exact retained scratch, detach |
| `ROLLED_BACK` | `UNDONE* PENDING*` | Detach metadata only; never mutate project or scratch |
| `HALTED` | not re-derived; the current vector must equal the diagnostic's frozen full journal vector | Stable halt; no further state change |

The two `ROLLING_BACK` branches are exclusive. A forward `STARTED` is the highest executed effect and
must be settled before any reverse effect, so `STARTED` followed by `UNDONE` is unreachable.
`UNDO_STARTED` is the single reverse frontier between effects not yet reversed and effects already
reversed. A well-typed but illegal combination produces a halt plan and preserves evidence; it is
never normalized into a plausible history.

Once the reverse language reaches its `PENDING*` tail, no later `UNDONE` is legal. Thus
`PENDING UNDONE` and `UNDONE PENDING UNDONE` are contradictions, not alternative reverse frontiers.

The state, commit decision, and active binding are validated jointly:

- `PREPARED`, `APPLYING`, `APPLIED`, `ROLLING_BACK`, and `ROLLED_BACK` require `UNCOMMITTED`;
- `COMMITTED` requires `COMMITTED`;
- `HALTED` preserves whichever commit decision existed at the first halt;
- an active `COMMITTED` or `ROLLED_BACK` record resumes cleanup or detachment;
- a detached `COMMITTED` or `ROLLED_BACK` record is `NO_RECOVERY`; and
- a detached `PREPARED`, `APPLYING`, `APPLIED`, or `ROLLING_BACK` record produces
  `ACTIVE_BINDING_MISSING` and never reattaches itself.

Any state/commit-decision combination outside that matrix produces a halt with
`COMMIT_DECISION_CONFLICT`. Any journal vector outside the selected state's legal language produces
`JOURNAL_TOPOLOGY_INVALID`. `HaltReason` is the closed reason-code vocabulary in §5.1; classifiers may
not invent free-form codes.

An already `HALTED` snapshot returns its frozen halt diagnostic without changing project state,
scratch state, commit decision, or diagnostic. If its active binding is unexpectedly absent, A3 still
does not invent or attach a new binding. This stable-halt decision occurs before selecting a journal
language: the vector was frozen and validated against the diagnostic by the snapshot factory and is
not re-derived as a forward or reverse history.

The compiled effect sequence is the only execution order. `dependencies` carry no scheduling
information and never affect A3 classification or A7 execution.

### 6.2 Frontier rules

Forward and reverse path frontiers follow authority §8.4. A `PENDING` frontier whose live entry differs
from the preceding post-state is external drift. It is preserved and produces a refused rollback
outcome. A completed frontier whose live or retained scratch evidence is inconsistent is
unattributable and halts.

Each reconstructed `PathFrontier` carries both a continuity baseline and the closed set of states
admissible at the selected occurrence. For an in-flight `STARTED` occurrence the baseline is still one
state, while the admissible set contains pre/intermediate/post as the variant defines. Consumers never
treat the baseline as the whole admissible set.

An all-`PENDING` forward timeline is `INITIAL` at its first declared pre-state. Production
`classify_recovery` changes every uncommitted active transaction to `ROLLING_BACK` before it
reconstructs effect frontiers. Committed cleanup does not reconstruct frontiers; direct variant tests
still exercise the forward in-flight vocabulary.

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

`ActionPlan`, `HaltPlan`, and `NoRecoveryPlan` are the three exact dataclass variants.
`ROLL_BACK`, `HALT`, and the other uppercase names are values of their closed disposition field, not
classes or subclasses.

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

`TransitionTransactionState` contains `from_state`, `to_state`, and closed optional payloads. A
transition to `ROLLED_BACK` carries exactly one `RollbackResult`; no other transition carries a
rollback result. A transition to `HALTED` carries exactly one frozen `HaltDiagnostic`, including the
pre-halt state and unchanged commit decision; no other transition carries a halt diagnostic. These
payload rules make the metadata ordering in ledger entry 12 expressible rather than implicit.

`TransformEffectTuple` carries:

- the effect ID and exact variant;
- a closed settlement kind;
- the complete expected persistent-and-scratch before-tuple;
- the resulting logical after-tuple; and
- required identity, prefix, and occupancy relations.

Its expected before-tuple is the complete logical observation immediately before that filesystem
step, after every earlier metadata and semantic step in the plan. If `STARTED -> UNDO_STARTED` makes
a construction-only `file_build_relation` irrelevant, the bound step precondition carries `None`,
even though the classification evidence that selected the settlement retains the original relation.
Plan construction binds or rebases semantic steps against this post-transition pure cursor; it never
weakens reducer or authorization equality.

`RemoveScratch` normally carries the same complete effect coverage. For committed cleanup only, after
the transaction-wide final-surface proof, it carries exactly the one retained scratch slot and empty
persistent/occupancy tuples. This is not a general partial-observation fallback: the reducer permits it
only while the snapshot is exact `COMMITTED`, the named journal is exact `DONE`, and the key and role
are exactly the compiled retained-scratch slot.

Settlement kinds express intent — restore pre-state, remove an attributable creation, repair an
intermediate to pre-state, or finish an already-landed undo. They do not name `renameat2`, `unlink`,
`rmdir`, `fsync`, or another backend primitive.

Journal and transaction transitions occur explicitly in the same order A5/A7 must make durable.
Skipping, duplicating, or reordering a step is `ProtocolError`.

### 7.3 Source binding and fresh authorization

Every plan is bound by immutable value equality (`==`) to its exact source snapshot, not by Python
object identity. Applying it to a value-unequal snapshot is `ProtocolError`; plans are not reusable
capabilities.

Before each filesystem-mutating step, A7 obtains one fresh coherent joint observation and calls
`authorize_recovery_step`. Exact state/occupancy/build-relation agreement plus the same named-slot
identity partition returns a factory-controlled `AuthorizedStep`; raw tokens are never compared across
the plan and fresh-observation universes. Any mismatch returns a halt plan with stable reason
`PLAN_PRECONDITION_CHANGED`. A7 may not silently retry, reclassify, or reinterpret the stale step.

For committed cleanup, the complete final surface is authorized once before any cleanup step is
planned. Each later `RemoveScratch` fresh-authorizes only its exact retained scratch slot. Unrelated
post-commit live drift therefore cannot strand engine scratch after the transaction-level proof; it
also cannot license deleting a scratch slot whose own state or shape changed.

`step_index` must select a filesystem-mutating `TransformEffectTuple` or `RemoveScratch`. Naming
`TransitionTransactionState`, `TransitionEffectState`, `PreserveExternal`, or `DetachActive` is
`ProtocolError`; those semantic/metadata steps do not consume fresh filesystem authorization.

The call has one durable precondition: every plan step before `step_index` completed in order and no
later step began. A5/A7 establish that fact from the persisted metadata and executor position.
`reduce_recovery_plan_prefix(snapshot, plan, completed_steps)` validates source binding and returns the
logical snapshot after exactly that many steps; `apply_recovery_plan` is the special case that reduces
the full step count. Prefix reduction is pure and grants no execution authority.

Authorization binds the plan, mutating step index, derived prefix snapshot, and fresh evidence. A
mismatch halt uses
`reduce_recovery_plan_prefix(plan.bound_snapshot, plan, completed_steps=step_index)` as that prefix,
where `plan.bound_snapshot` denotes the plan's value-equal bound source snapshot. The conflicting
observation is substituted at the selected step; the mismatch halt is not incorrectly bound to the
plan's original, unreduced source state. Before the conflicting prefix snapshot is rebuilt, its
conditional `file_build_relation` fields are normalized against the prefix journal and conflicting
live/staging tuple by the same helper as ordinary reduction. Held descriptors and atomic capabilities
close the remaining observation-to-operation window according to §§6 and 9 of the authority design.

### 7.4 Halt diagnostics

A halt plan contains no project or scratch mutation. A first halt may persist only:

- the transition to transaction state `HALTED`; and
- its structured diagnostic.

The diagnostic contains the durable source's pre-halt transaction state, preserved commit decision,
and full journal state vector in compiled effect order. When classification discovers a contradiction
after advancing its pure planning cursor, the diagnostic also names the cursor's completed semantic
prefix and labels its expected and observed tuples as projected conflict evidence; it never presents
that cursor as durable state. The diagnostic additionally carries the effect and logical paths when
applicable, one `HaltReason`, and a non-mutating operator action. Tuple projections retain exact path
states, entry kinds, prefix/occupancy facts, and the identity relations the classifier used between
named slots:

```text
DiagnosticIdentityRelation(left_slot, right_slot, relation: SAME | DIFFERENT)
```

They never retain, serialize, log, or later compare `EntryIdentity` tokens themselves. Named-slot
relations are stable data, round-trip through A5, and remain comparable across process restarts even
though a new recovery observation creates a new token universe. Human prose is derived from this
structure and is not the machine contract. Classification of an already halted snapshot reuses this
exact stored diagnostic; it never recomputes the origin state as `HALTED` or mixes diagnostic evidence
with the current snapshot's identity tokens.

## 8. Abstract reducer

`apply_recovery_plan` checks source binding, then interprets the ordered steps:

- entry transfers preserve opaque identity;
- removal produces `ObservedAbsent`;
- preserved external entries remain unchanged;
- directory occupancy is recomputed through the resolved topology;
- journal and transaction states advance only through allowed edges;
- the terminal rollback transition records its exact `RollbackResult`;
- the first halt preserves `CommitDecision` and stores its exact `HaltDiagnostic`; and
- terminal detachment clears the abstract active binding.

After every step, the reducer reconstructs conditional `file_build_relation` evidence from the
resulting journal state and live/staging tuple before invoking the snapshot factory. A transition out
of `STARTED` drops construction-only evidence; a transition or tuple transformation into one of
§5.4's two construction cases installs the relation carried by that step's result. No intermediate
snapshot retains evidence that its new state makes irrelevant.

It produces these fixed points:

| Outcome | Result |
| --- | --- |
| successful uncommitted recovery | `ROLLED_BACK`, `RESTORED`, detached, initial surface restored |
| refused rollback | `ROLLED_BACK`, `EXTERNAL_DRIFT_PRESERVED`, detached, proved external drift preserved |
| `PREPARED` with external drift | `ROLLED_BACK`, `EXTERNAL_DRIFT_PRESERVED`, detached, no project mutation |
| committed cleanup | `COMMITTED`, detached, final surface retained |
| terminal detachment | existing terminal state, detached |
| first uncommitted halt | observations unchanged, active retained, `UNCOMMITTED`, transaction `HALTED`, frozen diagnostic |
| first committed halt | observations unchanged, active retained, `COMMITTED`, transaction `HALTED`, frozen diagnostic; rollback forbidden |
| already halted | identical commit decision, diagnostic, evidence, and halt disposition |
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

That equality is literal for repeated pure reduction of one snapshot. Across a process restart, newly
captured `EntryIdentity` values may be alpha-renamed; A8 compares surface evidence up to that renaming
while requiring exact equality of the token-free frozen diagnostic.

## 9. Variant classifiers

Notation below:

- `A` — absent;
- `pre` / `post` — exact declared state;
- `prefix` — proved strict prefix of the planned file;
- `X` — another present state;
- `==` between present entries — equal opaque identity.

For uncommitted recovery, an entry is **attributable** only when it occupies the effect's A4-approved
logical scratch slot and
the complete joint observation gives that slot one of the shapes permitted by the variant and current
journal state. Constructive evidence is an exact expected fingerprint, a proved prefix of the planned
blob, or an expected identity relation. Transfer evidence is a variant-specific atomic tuple, such as
`ReplaceFile (post, X)`, proving that the operation placed the unvalidated displaced entry in
`STAGING`. Occupying a reserved-looking slot by itself is never attribution; a shape outside these
rules is foreign or unattributable and is preserved.

Every uncommitted tuple not listed is unattributable and halts.

Committed cleanup has a separate transaction-level table. `classify_recovery` first proves the
complete compiled final surface from the transaction's one current observation per persistent path.
Only after that proof does its internal committed-cleanup helper inspect each scratch slot; it never
compares the final live entry with an earlier occurrence's post-state:

| `DONE` effect scratch | Committed decision |
| --- | --- |
| `ReplaceFile.STAGING` exact declared `pre` | remove staging |
| `DeletePath.TOMBSTONE` exact declared `pre` | remove tombstone |
| `MoveNoClobber.ANCHOR` exact declared `source_pre` | remove anchor; no current destination/anchor identity predicate |
| retained replace/delete/move scratch absent | already cleaned |
| `CreateFileNoClobber.STAGING` absent | valid; no cleanup step |
| `CreateDirectory.WORK` absent | valid; no cleanup step |
| any wrong retained entry state/kind, or any create staging/`WORK` survivor | halt and preserve evidence |

All retained removals use a scratch-only `JointObservation`. This table is available only behind the
successful final-surface proof; ordinary `classify_effect` retains the complete occurrence-local tuple
rules below for uncommitted settlement and conservative direct classification.

### 9.1 `ReplaceFile`: `(live, staging)`

When `pre == post`, this table uses the name `same` and does not dispatch through the overlapping
general rows below:

| No-op tuple | Classification and settlement |
| --- | --- |
| `(same, A)` while forward `STARTED` | no project mutation is required |
| `(same, prefix)` or `(same, same)` while forward `STARTED` | remove attributable construction staging and leave live unchanged |
| `(same, same)` while `UNDO_STARTED` or uncommitted `DONE` | remove staging and leave live unchanged |
| `(same, A)` while `UNDO_STARTED` | reverse settlement is already complete |
| `(same, A)` while uncommitted `DONE` | halt; forward `DONE` requires the retained displaced entry even when its fingerprint equals `post` |
| `(same, X)` | halt and preserve both; with no state change, the tuple does not prove that exchange displaced `X` |

This precedence restores the declared state for both sides of the otherwise indistinguishable
exchange. It deliberately makes no inode-provenance promise, matching authority §3.2. The general rows
below apply only when `pre != post`.

Forward `STARTED`:

| Tuple | Classification and settlement |
| --- | --- |
| `(pre, A)` | exchange did not land; undo without project mutation |
| `(pre, prefix)` or `(pre, post)` | unpublished staging; remove it and undo |
| `(post, pre)` | exchange landed; exchange back, validate `pre`, remove displaced `post` |
| `(post, X)` | unvalidated entry was displaced; exchange it back while live remains exact `post`, preserve the restored entry as external drift, validate and remove the engine postimage now in `STAGING`, finish refused rollback |

Reverse `UNDO_STARTED` accepts both atomic sides:

| Tuple | Settlement |
| --- | --- |
| `(post, pre)` | retry exchange |
| `(pre, post)` | exchange landed; finish scratch removal |
| `(pre, A)` | undo and cleanup already landed |

`DONE` under uncommitted rollback accepts only `(post, pre)`. It transitions to `UNDO_STARTED` and
uses the ordinary landed rollback above. `(post, A)` is a contradiction before a commit decision
because terminal scratch cleanup was not yet licensed.

Committed cleanup follows the transaction-level scratch table above. `live=post` without the displaced
entry is not attributed during uncommitted recovery.

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

A changed live target during undo or foreign staging evidence halts. `DONE` under uncommitted rollback
accepts only `(post, A)`, transitions to `UNDO_STARTED`, and performs the ordinary landed rollback.
Committed cleanup uses the transaction-level absent-staging rule above.

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

`DONE` under uncommitted rollback accepts only `(A, pre)`, transitions to `UNDO_STARTED`, and restores
the retained tombstone. `(A, A)` is not accepted before commit.

Committed cleanup uses the transaction-level tombstone rule above; the current live path may reflect a
later occurrence.

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
converges to `(pre, A, pre)` with source `==` anchor and then removes the anchor.

`DONE` under uncommitted rollback accepts only `(A, pre, pre)` with destination `==` anchor,
transitions to `UNDO_STARTED`, and performs the ordinary landed rollback. A missing anchor is a
contradiction before commit.

Committed cleanup uses the transaction-level anchor rule above and does not require the current
destination to retain an earlier occurrence's anchor identity. Source reappearance or destination
divergence never licenses uncommitted rollback.

### 9.5 `CreateDirectory`: `(live, work)`

The classifier evaluates modeled children through `RecoveryTopology` before deciding whether eventual
empty removal is possible:

| Tuple and identity | Classification and settlement |
| --- | --- |
| `(A, A)` | nothing landed |
| `(A, attributable work)` | publication did not land; remove empty work directory |
| `(post, A)` | publication landed; quarantine live into `WORK`, validate emptiness, remove |
| `(post, post)`, live `==` work | publication landed but work-name removal did not; after declared descendants are reversed and the shared directory is empty, remove the stale `WORK` entry first, yielding `(post, A)`, then perform the ordinary landed rollback |
| live blocker plus attributable different-identity work | publication did not land; preserve blocker, remove work, refuse |

A live divergence without surviving work is unattributable. Any unmodeled child that would prevent
eventual removal halts before recovery mutation.

`UNDO_STARTED` accepts live-only, work-only, same-identity dual-name, and fully absent atomic endpoints,
converging to `(A, A)`.

`DONE` under uncommitted rollback accepts only `(post, A)`, transitions to `UNDO_STARTED`, and performs
the ordinary landed rollback after declared descendants have been reversed. During committed cleanup,
the transaction-wide final-surface proof owns the persistent predicate and `WORK` must be absent. Work
removal is durable before `DONE`; a work survivor beside `DONE` is a contradiction, not cleanup.

### 9.6 Intentional differences across variants

Scratch absence does not have one global meaning:

- `CreateFileNoClobber` can classify its exact postimage with no staging survivor as landed, subject to
  the authority's fingerprint-equivalent-recreation non-guarantee.
- Replace and delete need the displaced entry or tombstone to prove their uncommitted joint tuple.
- Move needs the anchor for uncommitted identity attribution.
- Create-directory work must already be absent at `DONE`.
- Only behind a durable commit decision and successful complete final-surface proof, retained replace
  staging, delete tombstones, and move anchors may be absent because cleanup itself can have landed
  before a crash.

A shared "scratch missing means landed" fallback is forbidden outside that proof-gated committed
table.

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
- every transaction-state × commit-decision × active-binding combination;
- repeated-path timelines;
- `ReplaceFile` with both distinct and equal `pre`/`post`;
- present and absent `file_build_relation` across every variant/journal/live-state combination;
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
- explicit `DONE`-under-rollback acceptance without reusing `UNDO_STARTED` tuples;
- rollback restoration or explicit preserved drift;
- committed recovery never emits rollback;
- committed repeated-path cleanup proves the complete final surface before scratch decisions and never
  compares that final live state with every occurrence-local postimage;
- committed retained scratch accepts only exact expected state or absence, while create-file staging
  and create-directory `WORK` survivors halt;
- committed cleanup bypasses the ordinary per-effect classifier and produces scratch-only
  `RemoveScratch` authorization;
- a committed halt retains its commit decision and frozen first-halt diagnostic;
- halt diagnostics round-trip without identity tokens and remain equal across a regenerated token
  universe;
- a mid-plan contradiction keeps durable source journals separate from labeled projected cursor
  journals;
- halt preserves filesystem evidence;
- `dependencies` never affect ordering;
- plan/source binding and exact step authorization;
- fresh authorization succeeds across consistently regenerated identity tokens;
- construction evidence is required only in §5.4's two forward cases;
- each mutating step's expected tuple equals its reduced post-metadata prefix;
- a mismatch halt normalizes conditional construction evidence without erasing diagnostic evidence;
- identity-token renaming invariance;
- classification and plan construction allocate no identity tokens;
- reducer convergence and second-pass idempotence;
- totality for every well-formed generated snapshot; and
- internal faults are not normalized into domain refusals.

Mutation checks must demonstrate that the tests fail when:

- a move is classified per path rather than jointly;
- committed and applied states share a decision;
- a `ROLLING_BACK` history accepts `STARTED` followed by `UNDONE`;
- a `HALTED` vector is reinterpreted through an ordinary journal language;
- an already halted snapshot recomputes its diagnostic from state `HALTED`;
- a stale step is authorized;
- fresh but alpha-renamed identity evidence is rejected;
- a non-mutating step receives filesystem authorization;
- a symlink is given decisive opaque identity;
- a no-op replace is dispatched through the overlapping general rows;
- a completed replace requires a planned-blob relation for its displaced preimage;
- committed cleanup compares an earlier repeated-path effect with the final live entry;
- committed cleanup invokes the ordinary occurrence-local effect classifier;
- the reducer accepts scratch-only `RemoveScratch` outside exact `COMMITTED`/`DONE` retained cleanup;
- a step builder allocates a new identity token;
- resolved topology is replaced by lexical topology;
- scratch absence is treated uniformly across variants; or
- the reducer omits a reverse intermediate.

## 12. Deferred obligations created or refined

This design refines existing ledger entries for ancestor topology, dependencies, and resolved
equivalence. It also creates downstream obligations:

- A4 constructs and retains the resolved logical topology A3 consumes in production.
- A5 persists A3's transaction/effect transitions, separate commit decision, rollback result, and
  token-free frozen first-halt diagnostic in plan order.
- A6/A7 produce coherent identity, prefix, and directory-occupancy evidence, allocating one token per
  observed entry within each observation and a fresh token universe for every new observation.
- A7 executes only A3-authorized steps and does not rederive recovery decisions.
- A8 tests the real executor against A3's decisions and fixed points.

They remain open until the owning implementation and its verification land.

## 13. Acceptance criteria

A3 is complete when:

- all five pure APIs and their closed types are implemented;
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
