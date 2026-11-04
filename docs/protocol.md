# Transaction and Recovery Protocol

Type: protocol; document types 1. Currently unversioned. This document specifies the runtime coordination protocol between callers, the coordinator (`atoms.coordinator`), the metadata store (`atoms.db`), the project chain (`.#~chain/`), and the filesystem capability backend (`atoms.fs`), operating over the on-disk structures in [records.md](records.md) and the durability contract in [durability.md](durability.md). For contributor workflows and test commands, see [development.md](development.md).

## Participants and leases

Every coordinator operation runs across five participants:

1. **Caller** — invokes the public entry points in `atoms.coordinator.commands` with a `Backend`,
   `project_root`, `metadata_root`, `StorageProfile`, and command-specific arguments.
2. **Coordinator (`atoms.coordinator`)** — compiles and approves specifications, acquires project
   leases, drives capture/prepare/execute/commit/rollback, and runs crash recovery on lease entry.
3. **Advisory project lock (`<metadata_root>/lock`)** — an exclusive `flock` descriptor
   (`ProjectLock` in `atoms.coordinator.lease`) held for the entire duration of any mutating or
   recovery-capable lease.
4. **Metadata store (`<metadata_root>/atoms.db`) and workspace (`blobs/`, `staging/`, `work/`)** —
   persists the authoritative transaction state, effect journal, preimage blobs, and root-lifecycle
   binding (`atoms.store`).
5. **Project root (`<project_root>`) and capability backend (`atoms.fs.backend.Backend`)** —
   executes anchored, nofollow filesystem operations through `AuditedBackend` (wrapping
   `LinuxBackend` on Linux) against `<project_root>` and `<project_root>/.#~chain/`.

Lease context managers in `atoms.coordinator.root` establish the invariants required before a
command touches the project tree or store:

| Lease helper | Lock & volume check | Store & root-claim binding | Recovery on entry | Used by |
|---|---|---|---|---|
| `_creation_lease` | Acquires `<metadata_root>/lock`; certifies `(project_root, metadata_root)` against `CERTIFIED_ALLOWLIST` and runs the SQLite-WAL probe. | Opens or initializes `atoms.db` (`SCHEMA_VERSION = 3`). | None (fresh root initialization). | `register_root`, `replicate_root`, `fork_root` |
| `_project_lease` | Acquires `<metadata_root>/lock`; certifies volume binding and SQLite-WAL probe; opens `ProjectBinding`. | Opens `atoms.db` (`require_existing=True`). | Caller invokes `resolve` or lifecycle reconciliation explicitly. | `resume_fork_root`, `grant_read_serviceability`, `migrate_root_to_lifecycle_v3` |
| `_recovery_lease` | Enters `_project_lease`. | Verifies `root_lifecycle` (`WRITABLE` or `READ_ONLY_SERVICEABLE`) and `<project_root>/.#~root-claim` `(dev, ino)` + `root_id`, refusing incomplete `root_operation` rows. | Runs `resolve(lease._binding, lease._store)`. | `read_chain`, `inspect_chain`, `capture_states`, `read_path_state`, `read_preimage` |
| `_writable_recovery_lease` | Enters `_project_lease`. | Requires `LifecycleState.WRITABLE` and matching `.#~root-claim`, refusing read-only or binding-mismatched roots with `PreconditionRefused`. | Runs `resolve(lease._binding, lease._store)`. | `run_transaction`, `append_intent` |
| `_claimed_destination_lease` | Acquires `<metadata_root>/lock`; certifies volume binding and opens `ProjectBinding` + `atoms.db`. | Verifies an in-progress `root_operation` row (`replicate` or `fork`). | Resumes or rolls back the interrupted destination root operation. | `resume_fork_root` |

## Root lifecycle

`atoms.coordinator.lifecycle` and `atoms.coordinator.root` classify every
`(project_root, metadata_root)` pair into one of five `LifecycleState` values returned by
`read_lifecycle_state`:

| `LifecycleState` | Meaning and permitted commands |
|---|---|
| `WRITABLE` | `root_lifecycle` has `mode = "writable"`, `serviceable = 1`, no `root_operation` is pending, and `<project_root>/.#~root-claim` matches `(root_id, claim_dev, claim_ino)`. Admits all read, inspection, intent, and transaction commands. |
| `READ_ONLY_SERVICEABLE` | Replicated root (`mode = "read-only"`, `serviceable = 1`) with matching `.#~root-claim` and chain genesis. Admits read and inspection commands (`_recovery_lease`); refuses `run_transaction` and `append_intent` with `PreconditionRefused`. |
| `READ_ONLY_UNSERVICEABLE` | Newly replicated root (`mode = "read-only"`, `serviceable = 0`) before `grant_read_serviceability` verifies its chain and store. Refuses all recovery-lease commands with `PreconditionRefused`. |
| `METADATA_LESS` | `project_root` holds a `.#~chain/` directory, no `atoms.db` exists at `metadata_root`, and no `.#~root-claim` is present. Supports only `inspect_chain_detached`. |
| `BINDING_MISMATCHED` | `<project_root>/.#~root-claim` is missing, unreadable, or disagrees with `root_lifecycle` on `root_id`, `claim_dev`, or `claim_ino`. Refuses all commands with `PreconditionRefused`. |

Lifecycle commands:

- **`register_root`** — requires an empty `metadata_root` and a `project_root` with neither `.#~root-claim` nor `.#~chain/`; mints a 32-hex `root_id`, writes `.#~root-claim` and the `GenesisEntry` in `.#~chain/`, and commits `root_lifecycle` (`WRITABLE`).
- **`replicate_root`** — copies a quiescent `WRITABLE` or `READ_ONLY_SERVICEABLE` source root into fresh destination roots, recording a `root_operation` (`operation = "replicate"`) and leaving the destination in `READ_ONLY_UNSERVICEABLE` until `grant_read_serviceability` promotes it to `READ_ONLY_SERVICEABLE`.
- **`fork_root` / `resume_fork_root` / `read_pending_fork_operation`** — clones a quiescent source root into a new `WRITABLE` lineage with a fresh `root_id` and `GenesisEntry` via a two-phase `root_operation` (`preparing -> cutting-over`); if interrupted after cut-over begins, `resume_fork_root` completes the fork, whereas interruption during `preparing` rolls back the destination cleanly.
- **`migrate_root_to_lifecycle_v3`** — upgrades a quiescent `user_version = 2` store to `SCHEMA_VERSION = 3` by matching the existing chain `GenesisEntry` and `.#~root-claim`.

## Transactions

`run_transaction(backend, project_root, metadata_root, storage, spec, payloads) -> TransactionOutcome`
executes a ten-stage protocol:

```
compile_spec(spec)
  -> _writable_recovery_lease (lock + volume/WAL probe + resolve(binding, store))
  -> _registered_root + _require_no_pending + _require_admissible_fulfills
  -> admit(lease, compiled) [new_txid() + approve_for_project up to SCRATCH_ATTEMPTS = 3]
  -> open_workspace + capture_initial_surface (stage preimages & PayloadSource bytes)
  -> prepare_transaction (promote blobs + SQLite PREPARED + active row)
  -> append_registered (.#~chain/<reg_sha256>) + SQLite APPLYING (registration_digest)
  -> execute_effects (per ordinal: JournalState.STARTED -> mutate -> JournalState.DONE)
     + SQLite APPLIED + verify_committed_surface
  -> finalize_commit:
       1. SQLite atomic CommitDecision.COMMITTED + TransactionState.COMMITTED
       2. append_settled (.#~chain/<settle_sha256>, ChainOutcome.COMMITTED)
       3. SQLite persist_settlement_digest
       4. observe recovery snapshot -> run_plan (post-commit scratch cleanup + DetachActive)
```

1. **Compilation (`compile_spec`)** — validates `TransactionSpec` (`schema_version == 2`,
   identifiers, digests, disjoint target paths, acyclic `dependencies`, and exact state transitions
   from `initial_surface` to `final_surface`), producing a `CompiledSpec` with a deterministic
   topological execution order.
2. **Lease entry and recovery (`_writable_recovery_lease`)** — acquires `<metadata_root>/lock`,
   verifies the volume allowlist and SQLite-WAL probe, verifies the `WRITABLE` root binding, and
   runs `resolve(binding, store)` so any prior interrupted transaction is settled before new work is
   considered.
3. **Chain gate (`_registered_root`, `_require_no_pending`, `_require_admissible_fulfills`)** —
   reads `.#~chain/`, verifies that its `GenesisEntry` matches `root_lifecycle`, refuses with
   `PendingUnresolved` if any `RegisteredEntry` is unsettled, and verifies that `spec.fulfills` (if
   set) names an existing, unfulfilled `IntentEntry`.
4. **Admission and scratch approval (`admit`)** — mints `txid = new_txid()`
   (`secrets.token_hex(16)`) and calls `approve_for_project(compiled, ProjectContext(binding,
   txid))` up to `SCRATCH_ATTEMPTS = 3` times, verifying that all parent directories exist and are
   non-casefold, no `.#~<txid>.<effect_id>.<role>` scratch leaf collides on disk, and all required
   backend capabilities are present (`ProjectApprovedSpec`).
5. **Coherent capture (`capture_initial_surface`)** — opens `staging/<txid>/` and `work/<txid>/`
   under `metadata_root`, captures every path in `initial_surface` without following symlinks,
   verifies exact `PathState` equality against `initial_surface` (raising `PreconditionRefused` on
   mismatch), stages regular-file preimages into `staging/<txid>/`, and stages caller-supplied
   `PayloadSource` bytes (translating a missing digest `KeyError` into `PreconditionRefused`).
6. **Preparation (`prepare_transaction`)** — promotes staged preimage and payload blobs into
   `<metadata_root>/blobs/<hex>`, fsyncs them, and commits `transaction_record`
   (`TransactionState.PREPARED`, `CommitDecision.NONE`), `effect` rows (`JournalState.PENDING`),
   `blob` rows, and the singleton `active` row in one `atoms.db` transaction.
7. **Chain registration (`append_registered` + `persist_applying`)** — appends `RegisteredEntry` to
   `.#~chain/` using the current chain heads as `parents` and persists `registration_digest` and
   `TransactionState.APPLYING` in `atoms.db`.
8. **Effect execution and surface verification (`execute_effects` + `verify_committed_surface`)** —
   for each effect in topological order, persists `JournalState.STARTED`, executes the forward
   filesystem mutation (staging payloads or anchors under `.#~<txid>.<effect_id>.*` and applying
   `renameat2` / `mkdirat` / `unlinkat` with parent directory `fsync`), and persists
   `JournalState.DONE`. Once all effects reach `DONE`, persists `TransactionState.APPLIED` and
   re-observes every path in `final_surface` (`verify_committed_surface`, raising `EffectMismatch`
   on any discrepancy).
9. **Commit finalization (`finalize_commit`)** — (a) atomically persists `CommitDecision.COMMITTED`
   and `TransactionState.COMMITTED` in one `atoms.db` transaction (the durable commit point); (b)
   appends `SettledEntry(outcome=ChainOutcome.COMMITTED)` to `.#~chain/`; (c) persists
   `settlement_digest` in `atoms.db`; (d) observes a recovery snapshot and runs `run_plan(lease,
   approved, table, classify_recovery(snapshot))` to remove anchors/tombstones/work directories and
   detach `active` (raising `TransactionHalted` on `HaltPlan`), returning `TransactionOutcome(txid,
   registration_digest, settlement_digest, final_surface)`.
10. **Caught execution rollback (`_roll_back`)** — if any exception other than `ChainStateInvalid`
   or `MetadataStoreInvalid` is raised after `prepare_transaction` and before
   `CommitDecision.COMMITTED`, `_roll_back` reconciles chain registration, observes a recovery
   snapshot, and runs `run_plan(lease, approved, table, classify_recovery(snapshot))`. That plan
   persists `TransactionState.ROLLING_BACK`, executes reverse undo transforms and scratch removals
   in reverse topological order, persists `TransactionState.ROLLED_BACK`, appends
   `SettledEntry(outcome=ChainOutcome.ROLLED_BACK)` and persists `settlement_digest` during
   `DetachActive`, and clears `active`. `_roll_back` then re-raises the caught exception
   (translating `EffectMismatch` to `PreconditionRefused`, or raising `TransactionHalted` if
   classification returns `HaltPlan`).

Standalone `append_intent(backend, project_root, metadata_root, storage, *, consumer_tag, intent_digest, fulfills=None) -> str`
enters `_writable_recovery_lease`, verifies no pending registration exists and `fulfills` is
admissible, appends an `IntentEntry` above current chain heads, and returns its 64-hex digest.

## Recovery

Every entry into `_recovery_lease` or `_writable_recovery_lease` invokes `resolve(binding, store)`
(`atoms.coordinator.recover`) before returning control to the command:

1. **Active check** — queries `active` in `atoms.db`. If `active` is empty, `resolve` returns `None`
   immediately (any unreferenced `staging/` or `work/` directories are swept when workspaces open).
2. **Chain reconciliation (`_derive_reconciliation`)** — scans `.#~chain/` against the active
   `transaction_record`. If a matching `RegisteredEntry` or `SettledEntry` exists on disk in
   `.#~chain/` whose digest is not yet recorded in `transaction_record`, `resolve` backfills
   `registration_digest` or `settlement_digest` into `atoms.db`. If `state == PREPARED` and no
   `RegisteredEntry` exists on disk, `resolve` synthesizes the missing `RegisteredEntry` when
   rolling back a prepared transaction that already has chain parents, or detaches a
   pre-registration `PREPARED` record cleanly.
3. **Snapshot observation (`_observe_snapshot`)** — reconstructs `ProjectApprovedSpec` from
   `transaction_record.spec_json` and inspects the live `PathState` and inode identity (`(dev,
   ino)`) of every effect target path, source path, and `.#~<txid>.<effect_id>.<role>` scratch path,
   together with blob availability in `<metadata_root>/blobs/`.
4. **Pure classification (`classify_recovery` in `atoms.core.recovery.classify`)** — maps the
   observed `RecoverySnapshot` and each effect's `(JournalState, live tuple)` against the per-effect
   recovery state tables:
   - If `commit_decision == COMMITTED`, classification produces forward cleanup steps
     (`RemoveScratch` for remaining anchors/tombstones, transition to `COMMITTED` if needed, and
     `DetachActive`).
   - If `commit_decision == NONE`, classification produces reverse rollback steps
     (`TransitionTransactionState(ROLLING_BACK)`, per-effect `TransformEffectTuple` /
     `RemoveScratch` in reverse order, `TransitionTransactionState(ROLLED_BACK)`, and
     `DetachActive`).
   - If any effect tuple does not match an attributable pre-step, in-flight, or post-step state
     (or a required preimage blob is corrupt or missing), `classify_recovery` returns
     `HaltPlan(reason, diagnostic)`.
5. **Plan execution (`run_plan`)** — if classification returns `HaltPlan`, `run_plan` persists
   `TransactionState.HALTED` and `halt_diagnostic_json` in `atoms.db` (keeping `active` attached)
   and raises `TransactionHalted`. Otherwise `run_plan` persists the `RecoveryPlan` prefix in
   `transaction_record.recovery_plan_json`, executes each `TransformEffectTuple` and `RemoveScratch`
   step idempotently, and at `DetachActive` ensures the matching `SettledEntry` (`COMMITTED` or
   `ROLLED_BACK`) is durable in `.#~chain/` and `settlement_digest` is recorded before deleting the
   `active` row and removing `staging/<txid>/` and `work/<txid>/`.

## Inspection and reads

Read and inspection commands in `atoms.coordinator.commands` never mutate project files, though
`_recovery_lease` commands may finish recovering an interrupted transaction on entry:

- **`read_chain(backend, project_root, metadata_root, storage) -> ProjectChain`** — enters `_recovery_lease` and returns the validated `ProjectChain` (`genesis`, `entries`, `heads`, `pending`, `fulfilled_by`).
- **`inspect_chain(backend, project_root, metadata_root, storage) -> ChainInspection`** — enters `_recovery_lease` and returns a consumer-facing `ChainInspection` summary (`atoms.chain.inspect`) with topological history, head digests, pending registrations, and intent fulfillment map.
- **`inspect_chain_detached(backend, project_root) -> ChainInspection`** — reads `<project_root>/.#~chain/` directly without a `metadata_root`, requiring `LifecycleState.METADATA_LESS` (refusing if `.#~root-claim` exists on `project_root`).
- **`capture_states(backend, project_root, metadata_root, storage, paths) -> Mapping[str, PathState]`**
  — enters `_recovery_lease`, validates that no pending chain registrations remain, and returns a mapping of each requested relative path to its observed `PathState`.
- **`read_path_state(backend, project_root, metadata_root, storage, path) -> PathReadResult`** — enters `_project_lease` and attempts non-mutating recovery classification before returning `PathObserved(path, state)`, `ReadNotAttempted(path, reason)` (when recovery halts or a precondition fails before reading), or `ReadUnestablished(path, reason)` (when a pending registration touches `path`).
- **`read_preimage(backend, project_root, metadata_root, storage, *, txid, path, destination) -> FileState`**
  — enters `_recovery_lease`, looks up the settled or active `transaction_record` for `txid`, locates the `FileState` preimage recorded for `path`, streams and verifies the content-addressed blob from `<metadata_root>/blobs/<digest>` into `destination`, and returns its `FileState` (raising `PreconditionRefused` if `txid`, `path`, or the blob is absent).

## Failure

All domain errors inherit from `AtomsError` (`atoms.core.errors`):

| Exception class | Module | When raised and protocol post-condition |
|---|---|---|
| `SpecValidationError` | `atoms.core.errors` | Malformed `TransactionSpec`, invalid relative path or identifier, non-canonical digest/mode, surface mismatch, or dependency cycle during `compile_spec`. Raised before acquiring a project lease. |
| `CapabilityUnavailable` | `atoms.core.errors` | Volume tuple is not in `CERTIFIED_ALLOWLIST`, SQLite-WAL probe fails, casefold directory flag (`FS_CASEFOLD_FL`) is detected, or required `Capability` is missing. Raised before any project mutation. |
| `ProjectApprovalRefused` | `atoms.core.errors` | Target parent directory is missing or not a directory, a symlink or mount boundary is crossed during anchored traversal, a regular file has `st_nlink > 1`, or scratch leaf allocation fails after `SCRATCH_ATTEMPTS = 3`. Raised before `prepare_transaction`. |
| `PreconditionRefused` | `atoms.core.errors` | Root lifecycle state or `.#~root-claim` binding refuses the operation (including subclasses `RootOperationInvalid`, `RootOperationMismatch`, `SourceSnapshotMoved` in `atoms.coordinator.lifecycle`), live `PathState` mismatches `initial_surface`, `PayloadSource` raises `KeyError`, `read_preimage` cannot serve a requested preimage, or `verify_committed_surface` detects an `EffectMismatch` (after `_roll_back` settles `ROLLED_BACK`). |
| `PendingUnresolved` | `atoms.chain.errors` | `.#~chain/` contains an unsettled `RegisteredEntry` that recovery could not resolve (for example, an entry not tracked by `atoms.db`). Refuses new transactions, intents, and `capture_states`. |
| `TransactionHalted` | `atoms.core.errors` | Recovery classification returned `HaltPlan` because on-disk state or blob integrity violated the recovery state table. Persists `TransactionState.HALTED` and `halt_diagnostic_json` in `atoms.db` with `active` still attached; subsequent lease entries fail closed with `TransactionHalted` until administrative repair. |
| `MetadataStoreInvalid` | `atoms.store.errors` | `atoms.db` fails SQLite header, pragma, schema (`SCHEMA_VERSION = 3`), foreign-key, or row-decode validation. Propagates immediately without attempting rollback. |
| `ChainStateInvalid` | `atoms.chain.errors` | `.#~chain/` is missing on a registered root, contains non-canonical JSON, duplicate keys, unknown fields, digest mismatches, broken parent links, or conflicting settlements. Propagates immediately without attempting rollback. |
