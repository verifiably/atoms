# A5b — the recovery-resolve lease

**Status:** Implemented on 2026-08-02. A6–A8 remain unimplemented.

**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md)
§4.2, §5.4, §7.1, §7.3, §7.4, §11, §13.3, §13.5.

**Sub-plan below it:** [`2026-07-31-a5a-metadata-store-design.md`](2026-07-31-a5a-metadata-store-design.md).

---

## 1. Decision

A5a is a *mechanism*: it knows how to open, read, and write the engine's durable knowledge, and it
holds no lock, accepts no proof, and cannot know a crash occurred. A5b is the *coordinator* — the
authority's own §4.2 word for the layer that "acquires the project lock first, resolves any active
transaction, and only then accepts the spec for the requested operation against the settled project
state."

A5b composes A4a's lock, A4b's approval, A5a's store, and A3's plans into one critical section, and it
is the transaction admission boundary: the layer at which a `ProjectApprovedSpec` becomes the only
ticket into the engine.

The seam is the one that worked twice already: A4b-1 observed and A4b-2 judged; A5a stored and A5b
decides.

## 2. Scope and non-scope

### 2.1 In scope

- The recovery-resolve lease over `HeldProjectLock` — acquired at entry, held across the consumer
  command's entire write phase, released on every exit path (§5).
- Orphan reclamation at every lease entry: probe survivors, unreferenced workspaces, unindexed blobs
  (§5.2).
- The admission gate: proof-only transaction-stage entry points, txid identity, re-resolution before
  project-space access, and the scratch-occupancy regeneration loop (§6).
- `prepare_transaction` — authority §7.3 steps 2–4, ending at the durable publication COMMIT (§7).
- `persist_plan_prefix` — A3 transition persistence in plan order with the §7.4 barriers, stopping
  structurally at the first step A7 must execute (§8).
- The production composition root: the single call site passing `CERTIFIED_ALLOWLIST` (§4.1).
- Two new filesystem-level observers, `observe_child` and `observe_work_child`, because scratch leaves
  cannot be resolved by the existing resolver (§6.4).

### 2.2 Not in scope

- **Resolving an active transaction.** `classify_recovery` requires filesystem observations, which are
  ledger #13, owned by A6/A7. A live record at lease entry raises a build-stage trap (§5.3).
- **Coherent capture** (authority §6). A6 produces the manifest `prepare_transaction` consumes; A5b
  accepts it as a parameter.
- **Executing effects or recovery steps.** `TransformEffectTuple` and `RemoveScratch` are A7's.
- **Terminal cleanup and garbage collection** (authority §7.5).
- **Consumer-facing commands.** The synthetic exerciser is authority §12.1.

### 2.3 Relationship to A4a, A4b, A5a, and A6/A7

| Layer | A5b uses it for |
| --- | --- |
| A4a `acquire_project_lock` | The lease's critical section (#17) |
| A4a `reclaim_probe_survivors` | Called directly at lease entry, not left to binding (§5.1) |
| A4a `bind_project_volume` | The composition root's one production call (#18) |
| A4b `approve_for_project` | Called *inside* the lease, once per regeneration attempt (§6.3) |
| A4b `ApprovedWorkBase` | The expected baseline for work-base re-resolution (#19) |
| A5a `Store` | Every durable read and write |
| A3 `classify_recovery`, `reduce_recovery_plan_prefix` | Plan shapes and prefix validation (§8.2) |

A6 and A7 later enter the *same* lease rather than taking their own lock, which is what makes
resolution and mutation one critical section (authority §7.1).

## 3. Seam review against the deferred-obligation ledger

### 3.1 Existing entries

**This table states the expected ledger state _after_ implementation.** The ledger's own rule is that an
entry is removed only when its owning sub-plan lands *and* its verification suite covers it. The design
commit discharges nothing.

| # | Expected outcome | Why |
| --- | --- | --- |
| 7 | **Discharged** | Re-resolved occupancy check, bounded regeneration, `PreconditionRefused` on exhaustion (§6.3) |
| 18 | **Discharged** | `root.py` is the sole production bind caller, asserted to pass `CERTIFIED_ALLOWLIST` |
| 21 | **Discharged** | The gate set at every post-approval entry point (§6.5) |
| 23 | **Discharged** | Reclamation at every lease entry under the held lock, proved by a crash-cut test |
| 9 | **Open** | A5b converts the no-consumer guard and covers its own entry points; the entry requires A6–A8 to extend it as they land |
| 12 | **Open (write half)** | Plan-order persistence and barriers land; durable completion of each mutating step is A7's |
| 17 | **Open (lease half)** | Acquire, hold, and reclaim land; "resolves and completes or rolls back any active transaction" waits on A7 |
| 19 | **Open (A5's part done)** | A6 and A7 remain owners for capture and execution |

### 3.2 New entries this design creates

None. Three candidates were considered and rejected:

- *The A7 trap.* It is #17's open half, already tracked. A second entry would drift from the first.
- *`observe_child`.* A mechanism A5b consumes, not a boundary it admits. It refuses nothing on A5b's
  behalf and defers nothing to a later sub-plan.
- *txid collision.* Caught immediately by the durable-record check and the occupancy check that
  precede every use of a candidate (§6.3). A shape a sub-plan handles at once is not an admitted shape.

### 3.3 Authority amendments in this commit

**Authority §11, the `PreconditionRefused` bullet.** The current text scopes the type to "concurrent
drift was detected." Pre-existing external occupancy of an engine-derived scratch leaf is neither
concurrent nor drift — the leaf may predate this attempt entirely. The bullet becomes:

> **`PreconditionRefused`** — external state prevents clean execution; the transaction refuses cleanly.
> Concurrent drift is one case: during approval, when two observations of one directory or entry
> disagree within a single approval; at capture; or by validating an atomically displaced entry.
> Pre-existing external occupancy of an engine-derived scratch leaf is another, and need not be
> concurrent — such a leaf may predate this attempt. Approval holds no transaction record and has
> mutated nothing, so it refuses directly. Once mutation may have begun, the executor returns this
> refusal only after the current effect and every earlier effect have been restored; inability to prove
> that restoration becomes `TransactionHalted`.

"Refuses cleanly" rather than "refuses without mutation": capture may create temporary engine-owned
scratch, and a post-mutation refusal requires restoration rather than the absence of writes.

`atoms/core/errors.py`'s `PreconditionRefused` docstring changes to match.

**Status synchronization.** `AGENTS.md`'s A5 paragraph becomes "A5b designed, unimplemented," and
`test_a5_status_is_synchronized_across_authority_documents`
(`tests/test_store_architecture.py:1285`) is updated to the new strings in the same commit.

## 4. Architecture and ownership

### 4.1 Module layout

New package `atoms/coordinator/`.

| Module | Owns |
| --- | --- |
| `root.py` | The package-private resource stack: `_recovery_lease(backend, project_root, metadata_root, storage)`, and the sole production `bind_project_volume` call, passing `CERTIFIED_ALLOWLIST` |
| `lease.py` | The `Lease` value and the internal reclamation/resolution protocol over its `_binding` and `_store` |
| `admission.py` | `new_txid`, the regeneration loop, re-resolution, occupancy, and the entry-point gate set |
| `prepare.py` | `prepare_transaction` — authority §7.3 steps 2–4 |
| `transitions.py` | `persist_plan_prefix` — plan-order persistence and the §7.4 barriers |

There is no `errors.py` and no new exception type. The existing hierarchy already carries every
meaning A5b needs (§9), and a temporary class for the A7 trap would be a domain refusal that is not
one.

### 4.2 Dependency direction

A DAG, not a chain. `coordinator` is the only node with an edge to `store`, and the only one naming
both `ProjectApprovedSpec` and `Store`:

```
coordinator → {store, fs, core}
store       → {fs, core}
fs          → {core}
```

An architecture test asserts `atoms.store` is importable only from `atoms/coordinator/`, keeping the
store's single consumer provable as A6–A8 land.

### 4.3 Packaging metadata

`pyproject.toml` gains the fourth package. PEP 794 asks for the shortest exclusive import names;
`atoms` stays the implicit namespace, and the fields are retained rather than deleted because the
distribution name differs from its import names.

```toml
import-names = ["atoms.coordinator", "atoms.core", "atoms.fs", "atoms.store"]
import-namespaces = ["atoms"]
```

## 5. The lease

The lease is entered by the coordinator command **on the consumer's behalf**. Consumer code never
receives the `Store`, the `ProjectBinding`, or any other mutable ownership state (authority §4.2).

`root.py` owns the package-private entry; `lease.py` owns the protocol over what it produces.

### 5.0 The lease value

`_recovery_lease` yields a `Lease`. **Both are package-private.** A5b ships no consumer-facing
command — those are authority §12.1's, out of scope here — so nothing in A5b's public surface returns
or accepts a `Lease`. A future coordinator command enters it; external consumers never do.

```python
@dataclass(frozen=True, slots=True)
class Lease:
    _binding: ProjectBinding
    _store: Store
```

Every reference in this design is `lease._binding` and `lease._store`. There is no public `binding` or
`store` property; adding one would be the whole boundary undone in a single line.

Borrowed, not owned: the lease does not close either resource on its own. `_recovery_lease`'s
`finally` closes them in reverse acquisition order, so a `Lease` that outlives its `with` block
references spent objects and every A5a call through it refuses — A5a already spends its `Store` on
close and refuses afterwards, so the escape is caught by the layer below rather than by a flag here.

**Absence from `__all__` is not enforcement**, since `atoms.coordinator.root._recovery_lease` remains
importable by anyone willing to spell it. The leading underscore states the contract, and the
architecture test enforces what it can: no module outside `atoms/coordinator/` imports `_recovery_lease`
or `Lease`, and neither name — nor `ProjectBinding` or `Store` — appears in
`atoms.coordinator.__all__`. That makes authority §4.2's "consumer code never receives mutable
ownership state" structural for every in-tree caller, which is the population the guard can speak for.

### 5.1 Entry and exit order

```text
acquire_project_lock(backend, metadata_root)
→ reclaim_probe_survivors(lock)
→ bind_project_volume(project_root, lock,
                      allowlist=CERTIFIED_ALLOWLIST, storage=storage)
→ open_store(binding)
→ reclaim unreferenced workspaces and unindexed blobs
→ read_active  →  trap if a record is live
→ yield the lease            # held across the entire write phase
→ close Store → ProjectBinding → HeldProjectLock
```

**Probe reclamation is called directly, not left to binding.** `bind_project_volume` reclaims at its
own step 4, which it reaches only after earlier checks that can refuse first
(`atoms/fs/binding.py:169-172`). Ledger #17 requires reclamation at *every* lease entry, so the
coordinator calls it itself, immediately after the lock is held.

### 5.2 Reclamation precedes resolution

Orphans are unreferenced by definition, so removing them is safe before anything has been decided —
and #23 says "at every lease entry," which holds only if reclamation runs even when resolution then
refuses, halts, or traps.

Referenced scratch is never at risk. A workspace is orphan exactly when no `transaction_record` row
names its txid; `list_unindexed_blobs()` already means "no `blob` row."

### 5.3 The A7 trap

A live record at lease entry raises:

```python
raise NotImplementedError("recovery execution is not implemented until A7")
```

This is a **temporary build-stage trap, not a seventh domain refusal**. It adds nothing to the error
hierarchy, and its removal is the signal that the A6/A7 seam closed.

Its guarantee is scoped to logical transaction state, because binding, probing, SQLite reopening, and
orphan reclamation all legitimately mutate engine-owned metadata on the way in:

> No coordinator-directed transaction mutation occurs after detecting the active record; its record and
> `active` row remain unchanged, and no project path is touched.

The lease still releases cleanly: store closed, binding closed, lock released, so the next process can
enter.

**#17 is therefore `#17 (lease half)`.** A5b delivers "acquire at entry, hold across the write phase"
and "invoke probe-survivor reclamation at every lease entry, always under that held lock" in full. It
does not deliver "resolves and completes or rolls back any active transaction." A7 integration removes
the trap and discharges the entry.

## 6. Admission

### 6.1 Two tiers

| Tier | Members | Takes a proof? |
| --- | --- | --- |
| Pre-approval | `root.py`'s resource stack, `lease.py`'s reclaim/resolve protocol | No — authority §4.2 orders lock and recovery *before* `approve_for_project`, so no proof exists yet |
| Post-approval transaction-stage | `prepare_transaction`, `persist_plan_prefix` | Yes — `ProjectApprovedSpec` and nothing weaker |

Ledger #9's guard asserts over exactly the second tier, exempting `atoms/fs/approval.py` where the
type is defined.

### 6.2 txid generation

The coordinator owns txid generation outright: it is the only layer that can know a regeneration is
needed, and the consumer never holds one. The admitted txid is reported in the command's outcome for
correlation, never surrendered as ownership state.

```python
def new_txid() -> str:
    return secrets.token_hex(16)
```

Thirty-two hex characters satisfy A1's `SAFE_IDENTIFIER` (`^[A-Za-z0-9_-]{1,64}$`). Tests drive
deterministic sequences by patching `new_txid`.

### 6.3 The regeneration loop

Approval lives *inside* the loop, because ledger #21 says regeneration voids the proof — each attempt
must produce a wholly fresh one. Names are never substituted into an existing proof.

```python
SCRATCH_ATTEMPTS = 3

def admit(lease, compiled) -> ProjectApprovedSpec:
    occupied: tuple[str, ...] = ()
    for _ in range(SCRATCH_ATTEMPTS):
        txid = new_txid()
        if lease._store.read_record(txid) is not None:
            occupied = ()                  # durable record already owns it
            continue
        approved = approve_for_project(compiled, ProjectContext(lease._binding, txid))
        occupied = occupied_scratch(lease, approved)
        if not occupied:
            return approved
    raise PreconditionRefused(
        f"no usable txid after {SCRATCH_ATTEMPTS} attempts"
        + (f"; scratch occupied at {', '.join(occupied)}" if occupied else "")
    )
```

`occupied` is bound before the loop and reset on the durable-record path deliberately. Without both,
three consecutive record collisions would raise `NameError` instead of refusing, and a collision on
the final attempt would report the *previous* attempt's occupied leaves — a refusal message naming
paths that are not why it refused. The message distinguishes the two exhaustion causes rather than
assuming occupancy.

**The durable-record check is not redundant with the occupancy check.** A *detached terminal* record —
committed or rolled back, its `active` row cleared, all scratch already removed — owns its txid
permanently while leaving no scratch behind. Occupancy would find nothing, and the collision would
surface much later as a primary-key failure inside preparation's publication COMMIT. Checking
`read_record` first discards the candidate and counts the attempt.

**Only confirmed occupancy enters regeneration.** A re-resolution mismatch raises immediately; it is
not a reason to try another name.

### 6.4 Re-resolution

Ledger #19's A5 clause: the coordinator "may not use any resolved identity or `LookupProof` from
`ProjectApprovedSpec` to authorize project-space access."

The proof is read as the **expected baseline** and never trusted as current authority. In order:

1. Compare fresh directory identity and lookup constraints against `approved.directories`.
2. Verify mount membership against the live binding.
3. Only then inspect occupancy.

A mismatch is **pre-publication** — no durable record exists yet — so it refuses rather than halts,
per #19's "before durable transaction authority exists: refuse."

**`observe_child(parent_path, leaf)`, new at the filesystem level.** `PathResolver.resolve()` cannot
resolve a scratch path: `validate_path` rejects any component for which `aliases_scratch_sigil` holds
(`atoms/core/paths.py:40`). And `ApprovedScratch` carries `(effect_id, role, parent_node, leaf)` — a
parent node and a leaf, never a resolvable path. A minimal descriptor-anchored child observer draws
the occupancy observation and the parent facts from one open parent, rather than duplicating traversal
inside the coordinator.

**`observe_work_child(binding, leaf)`, its metadata-space sibling.** The `WorkRoot` branch below must
re-resolve `metadata_root/work` and ask about one child of it, and that is a different namespace from
project space: containment rules that apply to one do not apply to the other. One function switching
coordinate systems on a magic `parent_path` value would be exactly the ambiguity §9.4 exists to
prevent, so the work base gets its own entry point sharing the same core and returning the same
`ChildObservation`. Its parent facts are the pair `PathResolver.work_base_facts()` records in
`ApprovedWorkBase`, so the comparison is like with like.

Both return one frozen value, so a single open parent answers both questions:

```python
@dataclass(frozen=True, slots=True)
class ChildObservation:
    parent_identity: FilesystemIdentity
    parent_constraints: DirectoryConstraints
    present: bool
```

`DirectoryConstraints` already bundles `lookup_proof` and `name_max`, so one comparison against
`approved.directories` covers identity, `LookupProof`, and `NAME_MAX` together.

**`parent_path` is project-relative, always.** `ProjectRoot` is `""`. There is exactly one coordinate
system because `observe_child` is reached from exactly one of the three branches below — the one whose
parent lives in project space.

**Three branches, because one shape cannot carry all three.** `ChildObservation.parent_identity` is
mandatory, and an `ApprovedPlannedDirectory` deliberately has none; forcing a value there would mean
inventing an identity from the live filesystem, which is a fresh observation authorizing itself.

| Parent | What A5b does |
| --- | --- |
| Existing project directory (`ApprovedExistingDirectory`) | `observe_child(parent_path, leaf)`; compare `parent_identity` and `parent_constraints` against the approved entry; then read `present` for occupancy |
| Planned project directory (`ApprovedPlannedDirectory`) | Re-resolve the parent and require it **remains absent**. No `ChildObservation` is constructed: an absent parent has no identity to compare and no child to be occupied. A parent that is present now is post-approval drift and refuses |
| `WorkRoot` | `observe_work_child(binding, txid)`; compare its parent facts against `approved.work_base`, then read `present`. An occupied `work/<txid>` is regenerable occupancy; a moved or re-flagged work base is drift and refuses. `store.create_workspace` then owns the slot. Never enters `observe_child`, and the project-containment rules never apply to it |

**Mapping a `TopologyNode` to a project-relative parent path**, for the first branch only:

| Node | Parent path |
| --- | --- |
| `ProjectRoot` | `""` |
| `PersistentNode(path)` | that path |
| `TopologyDirectory(node_id)` | derived from `approved.paths`: an `ApprovedPath(path, parent_node, leaf)` whose `parent_node` is this node fixes the directory as `path` minus its trailing `leaf` |

The derivation is total for the nodes this branch needs. A `STAGING`, `TOMBSTONE`, or `ANCHOR` leaf
sits beside its target, so its `parent_node` is that target's `parent_node` — and the target is itself
an `ApprovedPath`, which supplies the mapping. A node with no mapping is a `ProtocolError`, never a
silently skipped check. `WorkRoot` is absent from this table by construction: a `ScratchNode` with
`role=WORK` is handled by the third branch, since `CreateDirectory` stages into
`metadata_root/work/<txid>/` rather than into project space.

**Refusals from the resolver are translated.** `atoms/fs/resolve.py` raises `ProjectApprovalRefused`
at twelve sites and `CapabilityUnavailable` at two — correct at approval
time, wrong afterwards. A mount change, a casefold-attribute change, or any other post-approval
divergence found during re-resolution is drift, and §9 requires `PreconditionRefused` for it. A5b
therefore catches both types around its re-resolution and re-raises `PreconditionRefused` with the
original chained, so a caller cannot mistake post-approval drift for an approval that never succeeded.
This translation applies **only** to A5b's own re-resolution; it never wraps `approve_for_project`,
whose refusals are genuine approval refusals and must surface unchanged. The counts are current at
time of writing and are not a contract — the requirement is categorical, covering every refusal type
the resolver declares, so it cannot drift as `resolve.py` grows.

### 6.5 The entry-point gate set

`prepare_transaction(lease, approved, workspace, manifest)`:

```text
approved is exactly ProjectApprovedSpec
approved.binding is lease._binding
workspace.txid == approved.txid
```

The workspace supplies the independent txid; no mutable `lease.admitted_txid` field is needed. A
mismatch is a `ProtocolError` — the only way to trip it is handing in a foreign proof or a workspace
belonging to a different admitted transaction, which is exactly the shape #21 admits and nothing in
A4b-2 prevents.

`persist_plan_prefix` compares `approved.txid` against the txid of the active `StoredRecord` it reads
itself (§8.1).

## 7. Preparation

`prepare_transaction(lease, approved, workspace, manifest)` performs authority §7.3 steps 2–4 after
its gates pass. All of it happens inside **one** A5a transaction, because `promote_staging` is a method
of `_StoreTransaction` — obtainable only by entering `Store.transaction()` — and it writes the `blob`
index rows itself:

```python
with lease._store.transaction() as txn:
    txn.promote_staging(workspace, manifest)          # §7.3 steps 2-3
    txn.insert_record(approved.txid, approved.compiled.spec)
    txn.set_active(approved.txid)
# COMMIT on exit -- §7.3 step 4, the single durable barrier
```

`insert_record` derives the `effect` rows as `PENDING` from the spec itself, so they are never passed
separately. `promote_staging` performs its cross-directory flush during the transaction body, which is
what satisfies the cross-substrate rule: every blob is durable on the filesystem *before* the COMMIT
that references it, even though both happen within one `with`.

Step 1 of authority §7.3 — coherently capturing and verifying the initial surface — is A6's. The
manifest is a `tuple[StagedBlob, ...]`, a plain value naming files already written into the workspace's
`staging/`, so A5b's tests stage real bytes and build real manifests rather than faking an interface.

**Work-base re-resolution happens before `store.create_workspace()`, not inside
`prepare_transaction`.** By the time preparation runs, the workspace already exists and A6 has written
into it; preparation uses those pinned descriptors, and re-resolving afterward could not authorize
their creation. The coordinator re-resolves `metadata_root/work` against `approved.work_base` under
the held lock, then creates the workspace — which is precisely why A4b-1 retained `ApprovedWorkBase`
rather than consuming it.

**The comparison is conditional on `work_base is not None`.** `ProjectApprovedSpec.work_base` is
`ApprovedWorkBase | None` and is populated only when the spec contains a `CreateDirectory`, the sole
effect with a `WORK` scratch role. When it is `None`, A4b has deliberately judged `work/` irrelevant to
this transaction, and A5b compares nothing — it still creates the workspace, whose `staging/` half every
transaction needs. Treating `None` as a comparison failure would refuse every transaction that creates
no directory; treating it as a baseline of "no facts" would let a fresh observation authorize itself,
which is the exact shape #19 forbids.

## 8. Transition persistence

### 8.1 Signature and cursor

```python
def persist_plan_prefix(lease, approved, plan, start: int) -> int: ...
```

It takes the lease rather than a caller-owned transaction and owns its own boundaries, and it reads
the active `StoredRecord` itself. That reading is load-bearing three ways: it enforces
`approved.binding is lease._binding`, it obtains the independent txid for the #21 comparison, and it
guarantees that returning past a metadata step means that step's COMMIT completed.

If no active record is found, it refuses with `ProtocolError` rather than proceeding — a plan being
persisted against a detached transaction has nothing to advance.

The function walks `plan.steps[start:]`, persisting each metadata-only step in plan order, and returns
the index of the first `TransformEffectTuple` or `RemoveScratch` — or `len(plan.steps)` if none
remains. Those two are exactly the mutating set: `AuthorizedStep.step` is typed
`TransformEffectTuple | RemoveScratch`, so the stop rule is read off A3's own contract rather than
restated.

A7 drives the loop: execute the mutating step durably, call again from `cursor + 1`.

### 8.2 Prefix validation

Checking `plan.bound_snapshot.transaction_state` alone is both incomplete and wrong whenever
`start > 0`, because the record has legitimately advanced past the bound snapshot by then. The gate is
the reduced prefix:

```python
expected = reduce_recovery_plan_prefix(
    plan.bound_snapshot, plan, completed_steps=start
)
```

The live record's **complete durable projection** must equal `expected`: spec, transaction state,
commit decision, rollback result, halt diagnostic, journals, and active status. The plan's compiled
spec and topology must equal `approved.compiled` and `approved.topology`.

### 8.3 Barrier discipline

Each writable metadata step gets its own SQLite barrier (authority §7.4). Within that:

- `ROLLED_BACK` and its `rollback_result` are one atomic pair — ledger #12 says "atomically with," so
  the result is never a second transaction.
- `HALTED` and its `halt_diagnostic` are likewise one atomic pair. The diagnostic is **first-wins**: an
  existing one is never overwritten, because #12 requires the *first* halt to freeze the pre-halt
  state. `committed` is preserved across the halt, never cleared.
- `DetachActive` clears the `active` row in a **later** transaction, after every earlier step in the
  plan has been persisted.
- `PreserveExternal` has no durable representation — it carries only `tuple[TopologyNode, ...]` — so it
  advances the cursor without writing anything.

### 8.4 What A5b cannot prove

The cursor makes A5b stop structurally before a mutating step. It cannot prove that a later `start`
supplied by A7 follows a durably completed effect. That remains A7's obligation, which is why #12 is
`#12 (write half)`.

## 9. Errors

No new type. The existing hierarchy carries every meaning, with §3.3's amendment broadening
`PreconditionRefused` to "external state prevents clean execution." **Durable txid occupancy is part of
that meaning**, as *pre-existing state* rather than external state: the record was written by this
engine, not by anything outside it, so calling it external would be wrong. What it shares with an
occupied scratch leaf is the property the type actually turns on — it is not concurrent, not drift,
predates this attempt, and blocks clean execution without any mutation having occurred.

| Situation | Type |
| --- | --- |
| Scratch still occupied after the attempt bound (#7) | `PreconditionRefused` |
| Every candidate txid owned by a durable record, after the attempt bound (#7) — pre-existing, not external | `PreconditionRefused` |
| Re-resolution mismatch, no durable record yet (#19) | `PreconditionRefused` |
| Post-approval resolver refusal translated from `ProjectApprovalRefused` / `CapabilityUnavailable` (§6.4) | `PreconditionRefused` |
| No active record when persisting a plan (§8.1) | `ProtocolError` |
| A scratch `parent_node` with no path mapping (§6.4) | `ProtocolError` |
| Re-resolution mismatch, durable record exists (#19) | `TransactionHalted` — **not reachable in A5b**, see below |
| txid ≠ the proof's txid (#21) | `ProtocolError` |
| Anything but a `ProjectApprovedSpec` at a transaction-stage entry point (#9) | `ProtocolError` |
| Required capability absent at lease entry | `CapabilityUnavailable` |
| Active record at lease entry, A7 absent | `NotImplementedError` (temporary trap, §5.3) |

**The halt row is listed but unreachable in A5b, deliberately.** #19's post-publication clause requires
a halt rather than a refusal once a durable record exists — but A5b performs project-space
re-resolution only during admission, which is pre-publication by construction, and
`persist_plan_prefix` touches no project path at all. The row records the contract A6/A7 inherit; A5b
ships no code raising it and no test claiming to reach it. Listing it without that caveat would invite
a test that appears to cover the case while exercising something else.

## 10. Verification strategy

On a real ext4 volume, using the existing binding fixtures.

**The lease**

- **#23 crash-cut.** A fresh process reopening a project retains no orphan `staging/`, `work/`, or
  unindexed blob — while a workspace a durable record *does* reference survives untouched.
- **Probe survivors** are reclaimed at lease entry even when binding would have refused before its own
  step 4.
- **Lock duration.** A contender cannot enter while the lease is held, and succeeds after release —
  proving the lock spans the entire write phase, not merely entry (authority §7.1, §13.5).
- **The trap's properties.** Record and `active` row unchanged, no project path touched, no
  coordinator-directed transaction mutation, lease released cleanly.

**Admission**

- **Exhaustion.** Patching `new_txid` to a deterministic occupied sequence drives all three attempts
  and reaches the `PreconditionRefused`, so the refusal path is provable rather than theoretical.
- **Detached-record collision.** A terminal record with no surviving scratch causes its txid to be
  discarded and the attempt counted.
- **#19 mismatches**, one case each: directory identity, lookup constraints, `LookupProof`, mount
  membership, and work-base re-resolution before workspace creation.
- **The three parent branches**, each reached and each refusing for its own reason: an existing parent
  whose identity or constraints moved; a *planned* parent that is present when approval said it would
  not be; and a `WorkRoot` whose `work/<txid>` already exists. The planned-parent case additionally
  asserts no `ChildObservation` is constructed, since there is no identity to put in one.
- **Resolver translation**, covering every refusal type `resolve.py` declares rather than an
  enumerated subset: each surfaces as `PreconditionRefused` with the original chained, and
  `approve_for_project`'s own refusals pass through untranslated.
- **Pre-publication refusal** rather than halt, and re-resolution mismatch raising immediately instead
  of entering regeneration.

**Preparation and persistence**

- **Publication atomicity.** A crash cut before step 4's COMMIT leaves no record and no `active` row;
  after it, `active` resolves to a record with its spec and every referenced blob durable.
- **Full projection mismatch gates.** Each field of the durable projection — spec, state, commit
  decision, rollback result, halt diagnostic, journals, active status — is independently perturbed and
  the call refuses. Success-path comparison alone would not prove the gate fires.
- **Barrier discipline.** One barrier per writable metadata step; `ROLLED_BACK`+result and
  `HALTED`+diagnostic each atomic; first-wins diagnostic; preserved commit decision; no write for
  `PreserveExternal`; `DetachActive` in a later transaction; and stopping at **both** mutating
  variants.
- Every plan shape comes from the production `classify_recovery`, and the record's projection is
  compared against `reduce_recovery_plan_prefix` after each call, so ordering is checked against A3's
  own reducer rather than a restatement of it.

**Architecture**

- `test_no_production_caller_of_bind_exists_yet` → the production bind-caller set is exactly
  `{atoms/coordinator/root.py}`, passing `CERTIFIED_ALLOWLIST`.
- `test_no_consumer_of_the_approved_spec_exists_yet` → `ProjectApprovedSpec` appears only under
  `atoms/coordinator/` and `atoms/fs/approval.py`, and every post-approval transaction-stage entry
  point takes one.
- `atoms.store` is importable only from `atoms/coordinator/`.
- No module outside `atoms/coordinator/` imports `_recovery_lease` or `Lease`, and none of
  `Lease`, `ProjectBinding`, or `Store` appears in `atoms.coordinator.__all__`. The guard speaks for
  in-tree callers, which is the population it can speak for; the leading underscore states the rest.
- `test_a5_status_is_synchronized_across_authority_documents` reflects "A5b designed, unimplemented."

## 11. Acceptance criteria

1. The lease acquires the lock, reclaims probe survivors directly, binds with `CERTIFIED_ALLOWLIST`,
   opens the store, reclaims unreferenced workspaces and unindexed blobs, and resolves — in that order.
2. Reclamation runs at every lease entry, including entries that then refuse, halt, or trap.
3. Scratch a durable record references is never removed.
4. The lock is held across the entire write phase and released on every exit path.
5. A live record at lease entry raises the A7 trap without coordinator-directed transaction mutation.
6. A candidate txid owned by a durable record is discarded and the attempt counted.
7. Admission makes at most `SCRATCH_ATTEMPTS` candidate/approval attempts — that is, at most
   `SCRATCH_ATTEMPTS - 1` regenerations after the initial candidate — producing a wholly fresh proof
   each time, then refuses with `PreconditionRefused` distinguishing occupancy from durable-record
   collision.
8. Re-resolution compares against the proof as baseline and never authorizes from it; a mismatch
   raises immediately, pre-publication refusals being `PreconditionRefused`.
9. Every post-approval transaction-stage entry point accepts only a `ProjectApprovedSpec` whose txid
   and binding match the lease's, refusing with `ProtocolError` otherwise.
10. Preparation re-resolves the work base before workspace creation, promotes staged blobs, and
    publishes the record in one COMMIT.
11. `persist_plan_prefix` validates the full durable projection against the reduced prefix, persists
    each writable metadata step at its own barrier with atomic terminal payloads, and returns the index
    of the first mutating step.
12. `atoms.store` is imported only from `atoms/coordinator/`, and `root.py` is the only production
    caller of `bind_project_volume`.
