# A7 — effect execution, recovery execution, and the tamper-evident chain

**Status:** Implemented on 2026-08-14. A9 remains unimplemented. §15 acceptance is met:
(1) per-variant effect tests plus `test_coordinator_kill_matrix.py`; (2) resolver and assembly-halt
tests; (3) chain, run, and kill-matrix tests; (4) filesystem/coordinator architecture guards;
(5) the named suites in the deferred-obligation ledger; and (6) `test_docs_status.py` plus the full
pytest, ruff, and pyright gate.

**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md)
§5.5, §7.3–§7.5, §8, §9, §10, §11, §13.2, §13.5, §14, §15 — and, consumed as the
chain's contract, science's `2026-08-03-tamper-evident-log-design.md` §3 and §9
as restated by authority §15.

**Sub-plans below it:** [`2026-08-07-a6-coherent-capture-design.md`](2026-08-07-a6-coherent-capture-design.md),
[`2026-08-02-a5b-recovery-lease-design.md`](2026-08-02-a5b-recovery-lease-design.md),
[`2026-07-31-a5a-metadata-store-design.md`](2026-07-31-a5a-metadata-store-design.md).

---

## 1. Decision

A7 is the stage where the engine finally acts. Everything before it is proof and
evidence machinery waiting on an executor: A2 proves the spec, A4b proves the
project, A5 persists decisions, A6 observes coherently — and `coordinator/lease.py`
still raises `NotImplementedError` the moment a live record exists. A7 fills
authority §14 Plan A item 5 — the five effects and the recovery executor — and
lands the six tamper-evident chain obligations of authority §15 inside the same
durability ordering, because registration and settlement are barriers *in* the
executor's write path, not an annex to it.

The seam that worked four times repeats once more: A4b-1 observed and A4b-2
judged; A5a stored and A5b decided; A6 observes and **A7 acts** — and the chain
witnesses. A3 remains the sole authority over the filesystem plan once a
snapshot is assembled: A7 executes only factory-authorized steps and never
classifies, reclassifies, or invents a second decision table (ledger #14). The
one pre-assembly exception is #19's approval-evidence assembly halt (§9.3),
which is coordinator-owned and reaches no A3 surface.

Three structural decisions shape everything below:

1. **One audited mutation seam.** The `Backend` protocol gains the mutating
   primitives the effects need, and a new audited facade over it *is* authority
   §13.5's always-on interposer. Every engine-issued filesystem mutation —
   effects, chain, bootstrap, probe, lock, blob, workspace, ignore marker —
   goes through the facade; SQLite's own VFS I/O is the sole exclusion.
2. **One plan executor.** Fresh-process recovery, caught-failure rollback, and
   committed cleanup are one code path: assemble a snapshot, let A3 classify,
   then alternate `persist_plan_prefix` with fresh-authorized durable mutation.
   Live rollback is not an in-memory unwind, because an unwind would be a
   second implementation of the recovery tables.
3. **The chain is mechanism; the coordinator is policy.** `atoms/chain/` knows
   entries, linkage, and durable append. It never acquires a lease and never
   imports the coordinator; the coordinator drives it at the pinned barriers
   and owns the public commands.

## 2. Scope and non-scope

**In scope:**

- The extended `Backend` mutating primitives and the audited facade with
  descriptor provenance (§5).
- Forward execution of all five effect variants per authority §9, with the
  per-effect §7.4 durability loop and in-process verification (§6, §7).
- The commit decision path nothing currently owns: `APPLIED`, the two ordered
  proofs over one coherent classification observation, `COMMITTED`, settlement,
  committed cleanup, detach, return (§8).
- Removal of the `_resolve` trap: recovery assembly, chain reconciliation,
  the classify → persist → authorize → execute loop, restartable
  materialization, and halt persistence (§8, §9).
- The chain package and its engine obligations: genesis-with-baseline,
  `registered` before first apply, `settled` as the completion barrier on both
  arms, the intent API, structural GC gates, and the reserved log path as
  engine bookkeeping (§10).
- Store schema v2 and `TransactionSpec` v2 (§11).
- A6's six §13 hardening gaps (§13).

**Non-scope, each with its owner:**

- The persistence-cut model, the synthetic exerciser, the end-to-end recovery
  matrix, and durability-allowlist certification — **A8** (ledger #15).
  `CERTIFIED_ALLOWLIST` ships empty through A7; production binding keeps
  refusing every volume, and A7's real-filesystem suites run under a test
  allowlist.
- The macOS backend — **A9**, added to authority §14's roadmap in this
  design's banking commit so Plan A item 6's "both backends" has an owner
  rather than a strategy.
- Every Plan B adoption item, including the verified-holdings commands
  (science's adoption ledger, artifact 4) — Plan B, after A8.
- Anchor carriage, head capture, verification, and every L-row test — science's
  side of the log design's §9 split.
- Terminal-record garbage collection — no removal command exists and none is
  added; A7 lands the *structural* gate it must someday pass (§10, §11).

## 3. Seam review against the deferred-obligation ledger

Every open entry naming A7, its required behavior, and the mechanism that
discharges it. No entry is discharged by this document; each leaves only when
the implementation lands and the named suite covers it.

| # | Required behavior (A7's half) | Mechanism |
| --- | --- | --- |
| 1 | Materialization hashes the actual stream and compares against the frozen `FileState`; mismatch refuses or halts | Every byte-writing path — forward staging and rollback recreation — streams from `Store.open_blob` while hashing, compares digest and length against the declared state before publication, and re-verifies the published entry through the retained descriptor (§7) |
| 3 | Hand §9.5's published-directory descriptor down at execution | `CreateDirectory` publication verifies identity through the retained descriptor, rebinds its audit provenance to the live path, and threads it to descendant effects as their parent descriptor (§7.5) |
| 8 | Follow the compiled effect sequence; never consult `dependencies` for ordering | The forward loop iterates `approved.compiled` order; `dependencies` is not read by any coordinator execution module, asserted architecturally (§14) |
| 9 | Every transaction entry point accepts `ProjectApprovedSpec` only | New internal proof-accepting functions join `_TRANSACTION_STAGE_ENTRY_POINTS` with `_require_admitted` first; public commands are guarded separately — they must acquire the recovery lease and never accept a proof (§4, §14) |
| 12 | Durable completion of each mutating step; never advance or detach metadata for a step not completed durably | The plan loop executes each authorized mutating step through its full durability obligation before calling `persist_plan_prefix` past it; forward effects reach stable storage before their `DONE` COMMIT (§6, §8) |
| 13 | Complete recovery assembly, committed-cleanup sequencing, fresh authorization observations | Recovery assembly observes every persistent path and required scratch slot in one fresh universe (`build_recovery_snapshot`'s validators enforce coverage); committed cleanup supplies one coherent complete final-surface observation, then slot-exact fresh authorization observations with empty persistent and occupancy coverage (§8, §9) |
| 14 | Execute only factory-authorized steps, with the variant's §9 primitive and durability ordering; on mismatch persist A3's prefix-bound halt | `authorize_recovery_step` gates every recovery mutation; the (variant × `SettlementKind`) table in `coordinator/effects/` is the only primitive mapping; an authorization `HaltPlan` is persisted, never reclassified (§8) |
| 17 | Remove the `NotImplementedError` trap; resolve-and-complete at every lease entry | `_resolve` is widened and implemented (§9); the A5b trap tests are replaced by recovery tests preserving their invariants |
| 19 | Execution-half re-resolution: after a durable record exists, mismatch **halts**, never refuses, never silently reapproves | Recovery compares fresh resolution against the persisted approval evidence and issues a fresh factory-controlled proof only on exact match; mismatch persists a durable halt through a path that does not require the proof whose issuance failed (§9, §12) |

**Six late-captured entries.** Science's log design §9 states its obligations
were entered in this ledger at banking; they were recorded in authority §15
instead. This design's banking commit adds them as entries **#24–#29**,
admitted by the log design's banking (2026-08-03), first owner A7, so they come
under the ledger's landing discipline. Their required behaviors are the six
items of authority §15 verbatim, with #29 (log path as engine bookkeeping)
additionally carrying the reserved-path admission: the chain lives at an
engine-reserved leaf in project space so it travels with copies; A2's grammar
separation is the refusal layer that keeps it unspellable as an effect target
(no new approval refusal is claimed — §10), and the facade admits it as a
bookkeeping target.

**Not A7's:** entry #15 — model/real-filesystem/persistence-cut agreement —
remains A8's in full.

## 4. Architecture and ownership

Six surfaces change; the dependency DAG stays acyclic and each piece lands
where its imports already point:

- **`atoms/core/`** — `TransactionSpec` v2 (`fulfills`, the registered-path
  subset), canonical encoding, `compile_spec` validation of the new members;
  the canonical `AssemblyHalt` value in its own module, outside A3's recovery
  model (§9.3); the `.#~` prefix redefined from "scratch iff" to
  **engine-reserved** (§10, §13).
- **`atoms/fs/backend.py`** — the extended `Backend` protocol (§5).
- **`atoms/fs/audit.py`** — the audited facade and provenance registry; the
  §13.5 interposer (§5).
- **`atoms/chain/`** — entry model, canonical envelope, linkage and tip
  discovery, durable append with restartable staging, genesis. Imports `core`
  and the facade only; never the coordinator, never the store (§10).
- **`atoms/coordinator/`** — `execute.py` (forward spine), `commit.py`
  (proofs, decision, settlement), `recover.py` (assembly, reconciliation, the
  plan loop), `effects/` (five variant modules owning syscall execution only —
  A3's classification remains authoritative), and the public commands
  `run_transaction`, `register_root`, `append_intent`, each acquiring the
  recovery lease internally and never exposing `Lease` (§6–§9).
- **`atoms/store/`** — schema v2: registration and settlement digest columns
  (unique, write-once), canonical recovery-approval evidence, and the
  structural triggers gating `APPLYING`, detachment, and terminal-record
  deletion (§11).

## 5. The audited facade and the extended backend

### 5.1 New Backend primitives

All descriptor-relative — a held parent descriptor plus a single-component
leaf — preserving `anchored_traversal` at mutation time:

- `create_exclusive(parent_fd, name, mode) -> int` — `O_CREAT | O_EXCL |
  O_NOFOLLOW | O_RDWR | O_CLOEXEC`; returns the **retained creating
  descriptor**, making §9.1/§9.2's identity discipline a return-type
  contract. `O_RDWR`, not `O_WRONLY`: the effects verify the published
  postcondition by re-reading through this same descriptor, and a
  write-only descriptor cannot.
- `write(fd, data) -> int` — the audited byte-write primitive. Capture
  streaming, probe writes, blob materialization, and effect staging all
  write bytes; without this primitive they would retain direct `os.write`
  calls and the facade-only claim (§5.2) would be false.
- `set_mode(fd, mode)` — `fchmod` through a retained descriptor, never a path.
- `mkdir_child(parent_fd, name, mode)`.
- `unlink_child(parent_fd, name)` / `rmdir_child(parent_fd, name)`.
- `symlink_child(parent_fd, name, target)` — required by restartable
  materialization when a symlink preimage must be restored to an absent live
  path and no tombstone survives (authority §10's staged-restore case); also
  what the probe already does inline.
- `create_or_open(parent_fd, name, mode) -> int` — the persistent lock file's
  idempotent acquisition, today a raw `os.open` in `fs/lock.py`.
- `open_existing(parent_fd, name, *, read_write=False, nofollow=False) -> int`
  — the non-creating descriptor-relative open used where the probe and store
  resume paths require their former exact access and nofollow flags. It never
  carries `O_CREAT` and is not a new capability.
- `set_marker_xattr(fd, name, value)` — the metadata-root ignore marker,
  today a raw `os.setxattr`.
- `repair_entry_mode(parent_fd, name, mode, *, before_change)` — the store's
  mode-000 database repair idiom (`O_PATH` open, invoke the required fresh
  liveness gate, then chmod immediately through `/proc/self/fd`), beneath the
  facade instead of beside it *(amended 2026-08-13, the A7a plan review: the
  facade-only claim is false while the repair spells its own syscalls; amended
  after Task 4 review so the gate remains between the pin and mutation)*.
- `close_fd(fd)` — descriptor close as a protocol member, so every owner
  typed against `Backend` closes through one seam and the facade's override
  can unregister provenance before the fd number is reusable *(amended
  2026-08-13, the A7a plan review, with the same rationale)*.
- `detach_fd(fd)` — transfer a live descriptor to a caller that owns its raw
  close, unregistering facade provenance without closing the descriptor. The
  raw Linux backend has no registry work to perform.

**No capability-set member and no probe semantics change.** The probed eight
remain exactly the filesystem-specific capabilities; the new primitives are
POSIX-universal and carry no per-volume evidence. The probe's *implementation*
moves onto the facade like every other mutation site. One derivation does
change: once a root is registered, every transaction appends to the chain, and
chain publication uses no-clobber transfer (§10.2) — so `noclobber_transfer`
joins `anchored_traversal`, `durable_publish`, and `advisory_project_lock` in
the always-required set. The enum and probe are untouched; the required-set
computation in preparation is.

### 5.2 The facade is the interposer

`atoms/fs/audit.py` wraps the platform backend and is the only mutation
surface production code may reach; authority §13.5's interposer is this
facade, and the banking commit amends §13.5 to say so (the "wraps `rename`,
`unlink`, …" list describes the audited operation classes, not a
monkey-patched `os`). Mechanism:

- **Provenance registry.** Every descriptor the engine retains is registered
  with its engine-issued logical alias (declared path, scratch slot, metadata
  path, chain path) at open/create time and **unregistered on every close
  path, including close-error paths, before the fd number can be reused**.
  A mutating call against an unregistered descriptor fails the surface
  assertion with `ProtocolError`.
- **Target classes.** A mutation target must resolve — through the provenance
  of its parent descriptor plus the leaf — to a declared effect path, an
  engine-derived scratch path, an exact metadata path, or the reserved chain
  path (bookkeeping). Anything else fails closed.
- **Recording.** Successful mutations are recorded after the syscall returns,
  per §13.5, giving the suites the actual-mutation-surface assertion.
- **Rebinding.** `CreateDirectory` publication rebinds the retained
  descriptor's provenance from its `work/` slot to the verified live path, so
  descendant operations audit against live declared paths (ledger #3).
- **Reachability.** The raw platform backend is constructible only beneath
  the facade; an architecture test asserts no production command reaches an
  unaudited backend, and fault-injection tests inject beneath the facade.

Existing direct mutation sites migrate onto the facade in this stage:
workspace creation and removal, blob promotion and reclamation, capture
staging **including its byte streaming**, the persistent lock, the ignore
marker, and the probe including its writes. SQLite's VFS
I/O — database, WAL, SHM, journal — remains the sole exclusion, bounded by the
pinned profile under the verified `metadata_root`.

## 6. Forward execution

The `run_transaction` spine, every durable barrier named. Steps 1–4 exist
today (step 1's resolve becomes real recovery in this stage); A7 adds 5–15.
Steps 2–4 mutate nothing outside metadata space.

1. Lease entry — bootstrap, probe-survivor reclamation, bind, open store,
   orphan reclamation, **resolve** (§9; the trap becomes real recovery) —
   then the **exact-genesis preflight**: the chain must carry a valid
   genesis (validated per §9.1 phase 1), refused *here*, before any capture,
   workspace, or record write. Only `register_root` admits the empty-chain
   case; `append_intent` performs the same preflight.
2. `admit` → `ProjectApprovedSpec`.
3. `capture_initial_surface` (A6) — the descriptor table outlives capture.
4. `prepare_transaction` — §7.3's single COMMIT publishes `PREPARED` with
   spec, journals, blobs, `active`, and now the canonical recovery-approval
   evidence (§11).
5. **Registration.** Durable `registered` chain append (§10.2) carrying the
   txid, frozen-intent digest, consumer tag, the typed initial/final
   fingerprints of the spec's registered-path subset, and `fulfills` when the
   spec carries it.
6. **Registration binding COMMIT** — the entry digest written into the
   transaction record (write-once column, §11). No digest, no apply.
7. **`APPLYING` COMMIT** — its own barrier; the schema trigger refuses this
   transition while the registration digest is null.
8. Per effect, in compiled order (ledger #8): **`STARTED` COMMIT** → the
   variant module's §9 sequence through the facade against retained
   descriptors, with re-validation before reliance (ledger #19's A6 half is
   already in the descriptor table; execution adds no re-resolution by name)
   → the variant's full durability obligation → in-process verification
   (identity through the retained descriptor *and* exact postcondition, bytes
   hashed against the frozen `FileState`) → **`DONE` COMMIT**.
9. **`APPLIED` COMMIT** after the last `DONE`.
10. **The two proofs, ordered, over one coherent classification observation**
    (§8.4's committed discipline, applied pre-commit): the complete compiled
    final surface first, then the complete scratch vector. A failure here is a
    caught failure — the plan loop takes over and rolls back (§8).
11. **`COMMITTED` COMMIT** — transaction state and commit decision in one
    barrier.
12. **Durable `settled(committed)` append** referencing the registration
    entry's digest and txid.
13. **Settlement binding COMMIT** — the acknowledgement (write-once column).
14. **Committed cleanup and `DetachActive` through the plan loop** (§8):
    scratch-only removals fresh-authorized slot-by-slot, then detach — which
    the executor and the schema both refuse until the settlement is bound.
15. Terminal outcome returned; lease released.

A caught failure anywhere in 5–10 — including `KeyboardInterrupt`,
cancellation, and `SystemExit` — enters the plan loop (§8) before the
exception is re-raised or a refusal is returned; `PreconditionRefused`
surfaces only after restoration is proved (authority §11). **Substrate-invalid
failures are excluded from that catch**: `ChainStateInvalid` and
`MetadataStoreInvalid` prohibit further mutation by definition, so they
propagate with evidence preserved and no rollback is attempted.

## 7. The five effect modules

Each module owns exactly the syscall execution of its variant: the forward
sequence, the in-process verification, and the (variant × `SettlementKind`)
recovery-mutation mapping the plan loop dispatches through. The contracts are
authority §9.1–§9.5 verbatim; what follows is the primitive binding, not a
re-derivation. Scratch leaves come from `approved.scratch` — never re-derived
(A4b proved them distinct; re-derivation would unmoor the proof).

- **`ReplaceFile`** — `create_exclusive` on the staging slot (retain fd) →
  stream postimage bytes from `open_blob(post.content_hash)` hashing as
  written (#1) → `set_mode` → `flush_file` → `exchange(parent, live, staging)`
  → verify the live entry is the retained identity *and* re-reads to the
  exact postcondition, and the displaced staging matches `pre` →
  `flush_directory(parent)` → `DONE`. Mismatch: exchange back if the live
  path still holds our postimage and refuse; both-changed halts.
- **`CreateFileNoClobber`** — build as above on the staging slot →
  `transfer_noclobber(staging → live)` → verify identity + postcondition →
  `flush_directory` → `DONE`. `EEXIST` removes only the attributable staging
  object and raises `PreconditionRefused`; byte-equivalence is never adopted.
- **`DeletePath`** — `transfer_noclobber(live → tombstone slot)` → validate
  the tombstone against `pre` (descriptor-coherent for a file; `lstat` +
  `readlink` fingerprint for a symlink — capture's deferred identity contract
  lands here as destructive-transfer validation) → mismatch transfers back
  no-clobber and refuses, a reappeared live path halts →
  `flush_directory(parent)` → `DONE`.
- **`MoveNoClobber`** — `link_anchor(source → anchor slot)` →
  `flush_directory(source parent)` (the anchor name durable before the move)
  → validate the anchor against `source_pre` → `transfer_noclobber(source →
  destination)` → require destination and anchor to name one inode, still at
  the expected fingerprint → `flush_directory(destination parent)` **then**
  `flush_directory(source parent)` → `DONE`. In-process non-identity renames
  back and refuses, or halts on a reappeared source.
- **`CreateDirectory`** — `mkdir_child` under the surviving `work/<txid>`
  descriptor on the WORK slot → `open_child_directory` (retain) → `set_mode`
  → `flush_file` on the directory descriptor (the inode's own barrier) →
  cross-directory `transfer_noclobber(work → live parent)` →
  `flush_directory(live parent)` → verify identity, mode, and emptiness
  through the retained descriptor → `flush_directory(work_fd)` → `DONE` →
  **rebind provenance and hand the descriptor to descendant effects** (#3).

Committed-cleanup and rollback mutations reuse the same primitives:
`unlink_child` for staging/tombstone/anchor slots, `rmdir_child` for WORK and
quarantined directories, `create_exclusive` + blob streaming + `set_mode` for
staged re-creation, `symlink_child` for a symlink preimage with no surviving
tombstone, `exchange`/`transfer_noclobber` for restores — each invoked only as
the authorized step's `SettlementKind` directs.

## 8. One plan executor

The only component that mutates under recovery rules, invoked from three
places: fresh-process recovery at lease entry, caught-failure rollback, and
clean commit's cleanup. The loop:

1. Assemble a `RecoverySnapshot` (§9) and `classify_recovery` → plan.
2. `persist_plan_prefix(lease, approved, plan, cursor)` — persists
   transitions until it returns the index of the next mutating step (or
   completes the plan).
3. For a mutating step: take **one fresh authorization observation** with
   exactly the step's expected coverage — for committed-cleanup
   `RemoveScratch`, the named slot alone, with empty persistent and occupancy
   coverage — and call `authorize_recovery_step`.
4. Authorized → execute the step durably through the variant module's
   mapping, then continue at `cursor + 1`. Mismatch → the returned `HaltPlan`
   is **persisted, never reclassified** (#14), through the halt path (§9.3).

**`DetachActive` is a prefix stop.** The executor never lets
`persist_plan_prefix` walk from a terminal transition through detach in one
call. At the stop it reconciles settlement — appends the `settled` entry if
missing (idempotent by txid), binds its digest — and executes the detach only
after asserting both the registration and settlement bindings are present.
The schema trigger enforces the same order structurally (§11), and A3's pure
model stays chain-free: the stop is executor policy, not a plan step.

Both terminal arms pass the same barrier: `ROLLED_BACK` (rollback result
atomic with it, per A5b) → `settled(rolled-back)` → binding → detach, exactly
as `COMMITTED` → `settled(committed)` → binding → cleanup → detach.

## 9. Recovery assembly and reconciliation

### 9.1 The widened resolver

`_resolve` takes the binding and the store (the `Lease` construction moves
after resolution completes; the A5b trap-invariant tests convert into
recovery tests preserving record/`active`/lock/descriptor discipline).
Resolution runs in pinned phases; **no phase mutates until every earlier
phase passes**:

1. **Chain validation, read-only.** Walk the complete chain: decode every
   envelope, verify each entry's content name against its bytes, verify
   linkage and linearity, classify every staging survivor into its
   finish/remove action (§10.2), and *derive* §9.2's reconciliation actions
   without performing any. Malformed evidence raises `ChainStateInvalid` —
   nothing is persisted, no store write, no chain write, mutation refused.
2. **Short-circuits, after validation.** A `HALTED` record returns its
   stored diagnostic; a record carrying an assembly halt (§9.3) returns it.
   Both only after phase 1 passes — a stored diagnostic is never returned
   over chain evidence the engine cannot interpret — and neither mutates;
   there is no silent discharge.
3. **Reconciliation writes** — exactly the actions phase 1 derived (§9.2).
4. **Recompile** the frozen spec — `compile_spec` is pure and deterministic.
5. **Approval re-resolution.** Re-resolve the project topology and compare
   **exactly** against the persisted canonical recovery-approval evidence
   (§11): directory identities, lookup constraints, mount membership,
   work-root facts. On exact match, issue a fresh factory-controlled
   `ProjectApprovedSpec`; on any mismatch, **persist the assembly halt**
   (§9.3) — never refuse, never silently reapprove, never substitute the
   newly resolved topology (#19).
6. **Observation** of every persistent path and every effect's required
   scratch slot in one fresh `Observation` universe;
   `build_recovery_snapshot`'s validators enforce complete coverage (#13).
7. **The plan loop** (§8).

### 9.2 Chain reconciliation — exact cases

Derived read-only in phase 1, performed in phase 3, before any
classification. Registration, against the record's stored digest and the
validated chain:

- **Digest present** → it must resolve to a `registered` entry with matching
  txid; a dangling digest, a mismatch, or duplicate txid entries is
  `ChainStateInvalid`.
- **Digest missing, record `PREPARED`, every journal `PENDING`** — the only
  legitimate crash window: a unique matching entry exists → backfill the
  binding, never a second append; no entry → append and bind.
- **Digest missing in any other state** — any journal beyond `PENDING` or
  any transaction state beyond `PREPARED` → `ChainStateInvalid`, *even when
  a matching entry exists in the chain*: §11's triggers make that state
  unreachable through the engine, so it is substrate evidence of raw
  alteration, not a crash window to repair.

Settlement, symmetric:

- **Binding present** → it must resolve to a `settled` entry whose kind
  matches the durable terminal decision, whose registration reference is the
  record's bound registration, and whose txid matches; anything else is
  `ChainStateInvalid`.
- **Binding missing, terminal decision durable** → exactly one matching
  settlement in the chain → backfill the binding; none → append and bind
  (the crash-window backfill of §8's prefix stop). A duplicate settlement,
  wrong kind, wrong registration reference, or digest mismatch is
  `ChainStateInvalid`.
- **A settlement on a record that is neither terminal nor a committed
  halt** → `ChainStateInvalid`. The one legitimate settled-but-`HALTED`
  history is a committed-cleanup failure (authority §8.1: a halt after
  `COMMITTED` preserves the commit decision): a `HALTED` record whose bound
  committed settlement is accepted **iff** the frozen halt diagnostic proves
  `pre_halt_state = COMMITTED`. An uncommitted halt must have no settlement
  — one there is `ChainStateInvalid`.

As implemented on 2026-08-14, reconciliation rebuilds an appended entry from
the durable canonical `spec_json`; completing its byte-identical staging survivor
satisfies that append rather than publishing a duplicate.

### 9.3 The assembly halt

Two failure classes, deliberately separate (§12). Malformed chain evidence
refuses mutation as `ChainStateInvalid` and persists nothing — the store may
not be written on evidence the engine cannot interpret. An approval-evidence
mismatch is different: the substrates are coherent and the *world* moved, so
the finding is persisted durably as an **`AssemblyHalt`** — a new canonical
frozen value, deliberately **not** an A3 `HaltDiagnostic` and not a new
`HaltReason`: the mismatch is pre-classification, no `RecoverySnapshot`
exists or ever will for it, and A3's model, reducer, and diagnostic encoding
are untouched.

- **Shape:** `AssemblyHalt(txid, reason, expected, observed,
  operator_action)` — `reason` a closed coordinator-owned enum with the
  single member `APPROVAL_EVIDENCE_MISMATCH`; `operator_action` a closed
  enum with the single member `RESTORE_APPROVED_TOPOLOGY` (the non-mutating
  next step, per authority §11's diagnostic rule). `expected` is the
  persisted evidence verbatim. `observed` is a tuple of frozen per-node
  findings under a **closed vocabulary** — `NODE_MISSING`,
  `WRONG_ENTRY_KIND`, `IDENTITY_CHANGED`, `CONSTRAINTS_CHANGED`,
  `MOUNT_CHANGED`, `WORK_ROOT_CHANGED` — each carrying the node's path and
  the observed facts for exactly its kind. Same-path handling is
  deterministic: `NODE_MISSING` or `WRONG_ENTRY_KIND`, when it applies, is
  the node's **sole** finding (the facts the other kinds would carry do not
  exist for it); otherwise **every** applicable changed-kind finding is
  emitted. Ordering is by `(path, finding-kind enum order)`, under the same
  canonical encoding as the evidence, so the diff is byte-honest and
  evidence-complete. **Only determinate observations become evidence**: a leaf
  that resolves, or a determinate `ENOENT`, classifies; an indeterminate
  errno — `EIO` and kin — propagates as the error it is and is never
  encoded as drift.
- **Persistence:** a nullable, write-once schema v2 column on the
  transaction record; `StoredRecord` gains the decoded field. Transaction
  state, journals, and `active` are left exactly as found — the halt is
  *about* the world, not the transaction's own history.
- **Surfacing:** persisted through a **narrow store path that requires no
  `ProjectApprovedSpec`** — the proof is exactly what could not be issued —
  recorded as the registry's one exception in the architecture test; then
  raised as `TransactionHalted` carrying the value. Every later lease entry
  short-circuits at §9.1 phase 2 and returns it unchanged: no silent
  discharge, same as an A3 halt.

## 10. The chain

### 10.1 Layout and the engine-reserved prefix

One chain per engine root at the reserved project-space leaf **`.#~chain/`**,
one content-named file per entry; identity is the digest over (previous
digest | genesis, entry class, canonical envelope). Linearity — genesis-
connected, at most one successor, exactly one tip — is verified at every
append and at reconciliation; a sibling branch or orphan file is malformed
(`ChainStateInvalid`), never an ignored fork.

The `.#~` sigil is redefined from "scratch iff" to **engine-reserved**:
`is_engine_reserved_leaf` is the prefix check A2's grammar separation and
reclamation guards consult, and `is_scratch_leaf` becomes the full
`.#~<txid>.<effect_id>.<role>` grammar match. A2 remains the refusal layer
that keeps every `.#~*` leaf unspellable as a declared path — no new approval
refusal exists or is claimed — and slot-exact reclamation never touches the
chain because the chain is not a slot. The chain lives in project space, not
under `metadata_root`, because copies must carry it: a copied root travels
with its history while transaction metadata deliberately does not.

### 10.2 Durable append

**Bootstrap.** The chain directory is created by `register_root` and only
there: create-or-open `.#~chain/` under the held project-root descriptor,
validate it (empty, or a valid chain), and `flush_directory(project root)`
so the directory entry is durable **before** the genesis append begins. Every
other command requires the directory to exist — its absence at preflight is
an unregistered root, refused (§6 step 1); its absence with a live record is
`ChainStateInvalid`.

**Append.** Atomic publication through the facade, restartable at every cut:
write the entry to an engine-reserved staging name inside `.#~chain/` via
`create_exclusive` → `flush_file` → `transfer_noclobber` onto the digest name
→ `flush_directory(.#~chain)`. A crash leaves either nothing, an attributable
staging survivor, or the durable entry. **Staging survivors are classified in
§9.1 phase 1 under a closed rule**: a survivor byte-identical to an entry the
current reconciliation pass itself derives is **finished** by that append
(content-named, so completion is idempotent); every other survivor —
byte-identical to an already-durable entry, partially written, or decodable
but derived by no reconciliation action (an intent append that never became
durable: the caller never received its digest, so nothing was promised) — is
**removed**; staging never counts as chain state, so removal forfeits
nothing durable. Foreign or divergent *digest-named* files remain
`ChainStateInvalid` as below — the closed rule governs the staging name
only. **A no-clobber refusal is not itself idempotent success**: on `EEXIST`
the appender re-reads the existing destination and proves it — exact
canonical bytes whose digest equals the name, decoding to the expected entry
class, txid, and previous-entry linkage — before accepting; only then is an
exact staging survivor removed and the chain directory flushed. A foreign or
divergent digest-named file is `ChainStateInvalid`, never adopted because
its name looked right.

### 10.3 Genesis and `register_root`

`register_root(backend, roots, storage, genesis_payload: bytes,
registered_surface: paths) -> digest` acquires the lease internally, captures
the typed path/state fingerprints of the supplied projection under the lease
(the engine's own state vocabulary — absence, file, symlink, directory), and
appends the genesis entry embedding the consumer payload **opaquely** in the
canonical envelope beside the engine-computed baseline. The three union arms
of the log design — `corpus`/`world`/`store`, `forked_from`, id semantics —
live in the payload and are science's to validate; atoms guarantees exactly
linearity, baseline capture, and durability. Genesis retry is restartable
through §10.2's staging protocol, and a `register_root` call that finds an
existing genesis returns its digest **only after proving the call is a
retry**: the supplied payload bytes must equal the genesis payload and the
supplied projection must equal the genesis baseline's path set — the
fingerprints are the engine's records of registration-time state and are
never recomputed against the current world. Anything else is
`PreconditionRefused`: the root is already registered as something else. **A root with no genesis refuses
`run_transaction` and `append_intent` at the preflight** — immediately after
lease resolution, before any capture, workspace, or record write (§6 step 1)
— once A7 lands: registration is not optional, which is what makes
`noclobber_transfer` always-required (§5.1).

### 10.4 `registered`, `settled`, and the intent API

`registered` and `settled` carry the log design's payloads exactly; the
surface fingerprints cover the transaction's registered-path subset carried
in `TransactionSpec` v2 (§11) — declared by the consumer, validated by
`compile_spec` against the spec's surfaces, so the projection is durable in
`spec_json` and never a submission side channel. `fulfills`, when present, is
carried opaquely into the `registered` entry (obligation #27).

`append_intent(backend, roots, storage, payload: bytes) -> digest` — the one
consumer-facing chain command: opaque bytes embedded **unchanged** in the
canonical envelope (atoms promises no validation of consumer semantics),
appended under the internally-acquired lease so no cooperative race can mint
a sibling, durable before the digest returns. `Lease` is never exposed.

### 10.5 GC gates are structural

No terminal-record deletion path exists; A7 adds the gate that any future one
must pass: schema v2's triggers refuse deleting a transaction record whose
settlement digest is null and refuse clearing `active` before settlement
binding (§11). An assertion about a nonexistent deletion path is not a gate;
a trigger is.

## 11. Store schema v2 and `TransactionSpec` v2

Schema v2, in one bump (Plan A has no production data; `MetadataStoreInvalid`
on an unknown version is unchanged):

- `transaction_record.registration_digest` and
  `transaction_record.settlement_digest` — nullable, **unique, write-once**
  (an `UPDATE` from non-null fails by trigger).
- Canonical recovery-approval evidence, **non-null at insert and
  write-once**, persisted in the `PREPARED` COMMIT: the resolved directory
  identities, lookup constraints, mount membership, and work-root facts
  recovery must compare exactly (#19). Canonical encoding, so the comparison
  is byte-honest.
- The `assembly_halt` column (§9.3) — nullable, write-once.
- Structural triggers, stated as exact predicates rather than one-way
  implications — they are what make §10.5's and acceptance criterion 3's
  claims structural:
  - a journal transition `PENDING → STARTED` requires transaction
    `state = APPLYING` **and** `registration_digest` non-null;
  - writing `registration_digest` is allowed only while `state = PREPARED`
    with every journal `PENDING` — the trigger *is* §9.2's crash window, so
    the unreachable-state claim there is structural, not narrative;
  - **every** transition away from `PREPARED` — `APPLYING` and
    `ROLLING_BACK` alike — requires `registration_digest` non-null;
  - writing `settlement_digest` requires `registration_digest` non-null
    **and** `state ∈ {COMMITTED, ROLLED_BACK}`;
  - **every `UPDATE` of the `active` row is refused** — replacement is not
    a path around the clear predicate: clearing is deleting the singleton
    row, allowed only for `state ∈ {COMMITTED, ROLLED_BACK}` with **both**
    digests non-null and `assembly_halt IS NULL`, and publishing a new
    active is insert-only, possible only after the previous row was validly
    deleted;
  - deleting a `transaction_record` row is allowed only when it is detached
    (`active` does not reference it) **and**
    `state ∈ {COMMITTED, ROLLED_BACK}` with both digests non-null and
    `assembly_halt IS NULL` — `HALTED`, assembly-halted, and nonterminal
    records are never collectible;
  - once `assembly_halt` is non-null the record is frozen: no journal,
    state, or digest transition, no detach, no deletion.

`TransactionSpec` v2: `fulfills: digest | None` (default absent) and the
registered-path subset, both under canonical encoding and `compile_spec`
validation, persisted through `spec_json`.

## 12. Errors

- **`ChainStateInvalid`** (new) — malformed chain evidence: broken linkage, a
  sibling branch or orphan, an undecodable envelope, reconciliation's
  contradictory cases. Same response class as `MetadataStoreInvalid`: stop,
  preserve evidence, refuse mutation; the message distinguishes the finding.
- **Approval-evidence mismatch** — not a chain error and not a refusal: the
  durable, coordinator-owned `AssemblyHalt` (§9.3) with its closed reason
  and canonical expected/observed topology projections, persisted through
  the narrow path and raised as `TransactionHalted`; no A3 `HaltReason` is
  added.
- Everything else maps onto authority §11 unchanged: `PreconditionRefused`
  only after the plan loop proves restoration; `TransactionHalted` preserves
  the record and evidence; `ProtocolError` for engine misuse — including
  every facade surface-assertion failure; `MetadataStoreInvalid` as today.

## 13. Changes to existing code

1. **A6 §13 gap 1** — `DescriptorTable.stops` guarded by `_closed` like
   `fd_for`/`is_unreachable`.
2. **Gap 2** — `DescriptorTable.close` and `Observation.close` unregister
   ownership before each close attempt, attempt **every** descriptor exactly
   once — never retrying an fd whose `close` returned an error, since the fd
   may already be released and reused — and raise the first failure after
   all attempts; unified with the facade's unregister-before-close
   discipline.
3. **Gap 3** — `fd_for` raises `ProtocolError` directly; `capture.py`'s
   translation wrapper is deleted.
4. **Gap 4** — `_verify_stops`' final `else` gains its test (an entry
   appearing between admission and the walk).
5. **Gap 5** — one "modeled children" derivation, living in
   `descriptors.py`, which sits below capture in the import DAG (capture
   already imports it — the reverse delegation would be circular):
   `capture._modeled_under` is deleted, capture calls downward, and A7's
   occupancy consumers read only the shared derivation.
6. **Gap 6** — the streaming read/write loops and `build_relation` wrap
   `EBADF` in the same §9.1 translation the lookups use.
7. `core/scratch.py` — the engine-reserved prefix split (§10.1).
8. `coordinator/lease.py` — the trap removed; `_resolve` widened; `Lease`
   construction moved after resolution.
9. Raw mutation sites migrate onto the facade (§5.2).
10. `test_fs_architecture.py` — the entry-point registry gains the new
    proof-accepting internals; a new guard asserts every public command
    acquires the recovery lease and accepts no proof; a new guard asserts the
    raw backend is unreachable from production commands.
11. **Authority amendments, banking commit:** §13.5 defines the audited
    facade as the interposer; §14 adds **A9 — macOS backend** (Plan A item
    6's "both backends" resolves to Linux at A7/A8, macOS suites at A9); §15
    points at ledger entries #24–#29.
12. `test_docs_status.py` — `STAGES` gains `"A9"`; the label regexes widen to
    `A[1-9]`; at design time AGENTS.md and README.md status sections said A7–A9 remained.
13. The ledger gains #24–#29 (§3).

## 14. Verification strategy

A7 lands with its own suites; the ledger halves it discharges name them.

- **Per-variant real-filesystem tests** on an ext4 test volume under a test
  allowlist: forward success, in-process verification failures (foreign
  swap, mode drift, byte drift), refusal paths, and fault injection beneath
  the facade at **every** barrier seam — before/after each journal COMMIT,
  each atomic transfer, each fsync, registration, settlement, and both
  proofs.
- **Subprocess `SIGKILL` recovery tests**: kill at every stage of the
  forward spine (§6's numbered barriers), of both terminal arms — including
  between settlement append and binding, and between binding and detach —
  and of the plan loop mid-rollback; fresh-process recovery converges or
  preserves an explained halt, and second-pass recovery is idempotent.
- **Chain internals**: every append barrier cut — staging create, staging
  fsync, transfer, directory fsync — **and every bootstrap barrier cut** —
  directory create, project-root flush, cuts inside the genesis append —
  plus the genesis-retry proof (byte-equal payload and path-set → digest
  returned; either differing → `PreconditionRefused`), every staging-survivor
  classification (finished, removed-as-duplicate, removed-as-partial,
  removed-as-underived), `EEXIST` proof-then-accept including the
  foreign-file refusal, tip discovery over crash debris, the genesis
  preflight refusing `run_transaction` and `append_intent` on an
  unregistered root before any metadata write, every §9.2 reconciliation
  case including each `ChainStateInvalid` shape, the assembly-halt freeze
  (every trigger predicate exercised from both sides), and the phase
  discipline — a `HALTED` record's chain is validated before its diagnostic
  is returned, and no reconciliation write precedes full validation.
- **Executor–A3 conformance**: on clean commit, caught rollback, and every
  recovery fixture family, the executor's observed terminal states and
  durable projections equal A3's fixed points (`apply_recovery_plan`); the
  full model/real/persistence-cut agreement matrix remains A8's (#15).
- **Architecture tests**: facade-only mutation (no direct `os.*` outside the
  facade and the SQLite exclusion), provenance registration/unregistration
  balance on every close path, `chain` imports no coordinator/store, public
  commands under the lease guard, proof-accepting internals in the registry
  with `_require_admitted` first, `dependencies` unread by execution
  modules, and the §9.3 narrow halt path as the registry's one recorded
  exception.
- **A6 gap coverage, named**: each of §13 items 1–6 lands with the test that
  was missing — the `stops`-after-close guard, close-failure descriptor
  accounting, `fd_for`'s `ProtocolError`, `_verify_stops`' final `else`, the
  single modeled-children derivation, and `EBADF` translation in the
  streaming loops.

## 15. Acceptance criteria

1. The five effects execute and recover on ext4 under kill-at-every-barrier,
   converging to A3's fixed points or an explained halt.
2. The `NotImplementedError` trap is gone; a live, halted, terminal, or
   pending-registration record at lease entry resolves per §9.
3. Registration precedes application and settlement gates every terminal
   return, both arms, enforced by executor order **and** schema trigger.
4. The audited facade is the only mutation surface; the actual-mutation
   assertion holds across the whole suite.
5. Ledger #1, #3, #8, #12, #13, #14, #17, #19 A7-halves and #24–#29 are
   discharged with named suites; #15 remains open, owned by A8.
6. `FIRST_UNIMPLEMENTED` moves to `"A8"`; `uv run pytest`, `ruff check`, and
   `pyright` are green.

## 16. Known gaps after A8

1. **macOS** (A9) — the facade and primitives are designed
   platform-portably; nothing is probed or implemented for macOS here.
2. **Terminal-record GC** — the structural gate exists (§10.5, §11); the
   command does not.
3. **The L-row tests** — science-side, unexercisable until Plan B adoption
   gives science an atoms-backed boundary.
4. **Chain compaction and size** — one file per entry is unbounded;
   compaction, if ever, is a future design under the log design's anchor
   rules, not an A8 item.

## 2026-08-14 implementation amendments

- §9.1: recovery approval is issued only by factory-owned `_approve_for_recovery`; it admits
  A3-variable planned states and does not repeat forward-planning viability judgment. Recovery
  observation is total: unsupported kinds, lookup contention, and access denial become
  `ObservedUnrecognized(st_mode)`, `ObservedContended`, and `ObservedInaccessible`, encoded as
  `{"kind":"unrecognized","st_mode":<int>}`, `{"kind":"contended"}`, and
  `{"kind":"inaccessible"}`, and halt through the shared guard after a record exists.
- §9.2: reconciliation deterministically rebuilds an append from durable `spec_json`, including the
  spec's consumer tag and intent digest; a validated finished staging survivor satisfies that append.
- §9.3: assembly findings add fact-free `MOUNT_BOUNDARY` and `ACCESS_DENIED`; `.#~work_root` names
  `work/<txid>` and `.#~work_base` names physical `metadata_root/work`.
- §11: recovery-approval evidence records one `path` per directory node; project nodes have exactly
  one route from `directory_paths`, while `WorkRoot` has the closed `null` route.
- §7: recovery mutation is exactly A3's six authorized variant/settlement pairs. The former staged
  re-creation and symlink-restore prose names classifier-unreachable cells and is not implemented.
- Primitive contract: `open_directory_handle` is the guarded
  `O_PATH|O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC` route with beneath/no-symlink/no-cross-mount resolution,
  used when an umask-masked work survivor cannot be read normally.
