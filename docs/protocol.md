# Transaction and Recovery Protocol

Type: protocol; document types 1. Currently unversioned. This document specifies the runtime coordination protocol between callers, the coordinator
(`atoms.coordinator`), the metadata store (`atoms.db`), the project chain (`.#~chain/`), and the filesystem capability backend (`atoms.fs`), operating over
the on-disk structures in [records.md](records.md) and the durability contract in [durability.md](durability.md). For contributor workflows and test
commands, see [development.md](development.md).

## Participants and leases

Every coordinator operation runs across five participants:

1. **Caller** — invokes the public entry points in `atoms.coordinator.commands` with a `Backend`, `project_root`, `metadata_root`, `StorageProfile`, and
   command-specific arguments.
2. **Coordinator (`atoms.coordinator`)** — compiles and approves specifications, acquires project leases, drives capture/prepare/execute/commit/rollback, and
   runs crash recovery on lease entry.
3. **Advisory project lock (`<metadata_root>/lock`)** — an exclusive `flock` descriptor (`HeldProjectLock` in `atoms.fs.lock`) held for the entire duration of
   any mutating or recovery-capable lease.
4. **Metadata store (`<metadata_root>/atoms.db`) and workspace (`blobs/sha256/`, `staging/`, `work/`)** — persists the authoritative transaction state, effect
   journal, preimage blobs, and root-lifecycle binding (`atoms.store`).
5. **Project root (`<project_root>`) and capability backend (`atoms.fs.backend.Backend`)** — executes anchored, nofollow filesystem operations through
   `AuditedBackend` (wrapping `LinuxBackend` on Linux) against `<project_root>` and `<project_root>/.#~chain/`.

Lease context managers in `atoms.coordinator.root` establish the invariants required before a command touches the project tree or store:

| Lease helper | Lock & volume check | Store & lifecycle binding | Recovery on entry | Used by |
| ---|---|---|---|--- |
| `_project_lease` | Calls `bind_project_volume` (`probe_backend` + `certify_volume` against `CERTIFIED_ALLOWLIST` and SQLite-WAL probe on `<metadata_root>/probe/certify.db`) and acquires `<metadata_root>/lock`. | Calls `open_store(binding)` to create or reopen `atoms.db` (`SCHEMA_VERSION = 3`) and yields `Lease(binding, store)`. | None directly; wrapped by `_recovery_lease` or used after lifecycle verification. | `inspect_chain` (writable branch) and inner context for `_recovery_lease` / `_creation_lease` |
| `_recovery_lease` | Enters `_project_lease`. | Opens `atoms.db` under `<metadata_root>/lock`. | Runs `resolve(lease._binding, lease._store)`. | `register_root`, `read_chain` (writable branch), and inner context for `_writable_recovery_lease` |
| `_writable_recovery_lease` | Inspects lifecycle view first (`read_lifecycle_state`), then enters `_recovery_lease` and re-verifies `_require_writable_bound_store` under the held lock. | Requires `LifecycleState.WRITABLE` with `root_lifecycle` matching `(machine_id, root_path)` and no incomplete `root_operation` or surviving `.#~root-claim`. | Runs `resolve(lease._binding, lease._store)`. | `run_transaction`, `append_intent`, `read_preimage`, `read_path_state` (writable branch), `_copy_source` (writable branch) |
| `_claimed_destination_lease` | Verifies `<project_root>/.#~root-claim` matches the expected `operation_id` and `request_hash`, then enters `_recovery_lease`. | Verifies that `root_lifecycle` is still absent (or matches the in-progress `root_operation`). | Runs `resolve(lease._binding, lease._store)` before driving destination copy phases. | `replicate_root`, `fork_root`, `resume_fork_root` |
| `_creation_lease` | Requires a clean root without `.#~root-claim`, then enters `_recovery_lease`. | Requires `root_lifecycle` and `root_operation` to be absent before inserting cold-admission lifecycle rows. | Runs `resolve(lease._binding, lease._store)`. | `grant_read_serviceability` (metadata-less cold-admission branch) |

## Root lifecycle

`atoms.coordinator.lifecycle` and `atoms.coordinator.commands` classify every `(project_root, metadata_root)` pair into one of five `LifecycleState` values
returned by `read_lifecycle_state`:

| `LifecycleState` | Meaning and permitted commands |
| ---|--- |
| `WRITABLE` | `root_lifecycle` has `state = "writable"`, no `root_operation` is pending, `<project_root>/.#~root-claim` is absent, and `(machine_id, root_path)` matches the running host and canonical project root. Admits all read, inspection, intent, and transaction commands. |
| `READ_ONLY_SERVICEABLE` | Replicated or cold-admitted root (`state = "read-only-serviceable"`) with matching `(machine_id, root_path)` and a completed `root_operation` (if any). Admits `read_chain`, `inspect_chain`, `read_path_state`, `replicate_root`, and `fork_root`; refuses `run_transaction`, `append_intent`, and `read_preimage` with `PreconditionRefused`. |
| `READ_ONLY_UNSERVICEABLE` | Newly replicated root (`state = "read-only-unserviceable"`) before `grant_read_serviceability` verifies its chain and tree. Refuses read and write commands with `PreconditionRefused`. |
| `METADATA_LESS` | `<metadata_root>/atoms.db` does not exist and `<project_root>/.#~root-claim` is absent. Supports `inspect_chain_detached` (and `grant_read_serviceability` to cold-admit an existing `.#~chain/`). |
| `BINDING_MISMATCHED` | `root_lifecycle` disagrees with the host's `/etc/machine-id` or canonical `project_root` path, or an incomplete `root_operation` or `.#~root-claim` is present. Refuses commands with `PreconditionRefused`. |

Lifecycle commands:

- **`register_root(backend, project_root, metadata_root, storage, genesis_payload, surface_paths)`** — requires a fresh root without `.#~root-claim` or
  `.#~chain/`; writes `.#~root-claim`, records a `register` `root_operation`, captures the initial baseline for `surface_paths`, publishes
  `GenesisEntry(genesis_payload, baseline)` in `.#~chain/`, inserts `root_lifecycle` (`state = "writable"`, `origin = "register"`), and removes
  `.#~root-claim`.
- **`replicate_root(backend, source_root, source_metadata_root, dest_root, dest_metadata_root, storage, *, source_head)`** — copies a quiescent `WRITABLE` or
  `READ_ONLY_SERVICEABLE` source root at `source_head` into a fresh destination via a four-phase `root_operation` (`recorded -> source-snapshot-durable ->
  tree-durable -> complete`), removing `.#~root-claim` and leaving the destination in `READ_ONLY_UNSERVICEABLE` until `grant_read_serviceability` transitions
  it to `READ_ONLY_SERVICEABLE`.
- **`fork_root` / `resume_fork_root` / `read_pending_fork_operation`** — clones a quiescent source root into a new `WRITABLE` root (`origin = "fork"`) with a
  fresh `GenesisEntry` and optional `DestinationOverride` files; if interrupted after `source-snapshot-durable` is recorded in `root_operation`,
  `resume_fork_root` resumes and completes the fork using the persisted snapshot.
- **`migrate_root_to_lifecycle_v3`** — upgrades a quiescent `user_version = 2` store to `SCHEMA_VERSION = 3` (`origin = "migration-v2"`, `state =
  "writable"`) after verifying a well-formed `.#~chain/` without staging survivors.

## Transactions

`run_transaction(backend, project_root, metadata_root, storage, spec, payloads) -> TransactionOutcome` executes a ten-stage protocol:

```
compile_spec(spec)
  -> _writable_recovery_lease (bind_project_volume + lock + resolve(binding, store))
  -> _registered_root + _require_no_pending + _require_admissible_fulfills
  -> admit(lease, compiled) [new_txid() + approve_for_project up to SCRATCH_ATTEMPTS = 3]
  -> create_workspace + capture_initial_surface (verify initial_surface & stage preimages)
  -> prepare_transaction (promote_staging + SQLite prepared/uncommitted + active row)
  -> append_registered (.#~chain/<reg_sha256>) + SQLite applying (registration_digest)
  -> apply_effects (per effect: JournalState.STARTED -> mutate -> JournalState.DONE)
     + SQLite applied + verify_prepared_postimages
  -> finalize_commit:
       1. SQLite atomic CommitDecision.COMMITTED + TransactionState.COMMITTED
       2. append_settled (.#~chain/<settle_sha256>, ChainOutcome.COMMITTED)
       3. SQLite persist_settlement_digest
       4. observe recovery snapshot -> run_plan (post-commit scratch cleanup + detach_active)
```

1. **Compilation (`compile_spec`)** — validates `TransactionSpec` (`schema_version == 2`, identifiers, digests, exact surface coverage of touched paths,
   `dependencies` respecting `spec.effects` order `order[before] < order[after]`, and exact per-path timeline transitions from `initial_surface` to
   `final_surface`), producing a `CompiledSpec`.
2. **Lease entry and recovery (`_writable_recovery_lease`)** — certifies the volume and SQLite-WAL probe, acquires `<metadata_root>/lock`, verifies
   `LifecycleState.WRITABLE`, and runs `resolve(binding, store)` so any prior interrupted transaction is settled before new work is considered.
3. **Chain gate (`_registered_root`, `_require_no_pending`, `_require_admissible_fulfills`)** — reads and validates `.#~chain/`, refuses with
   `PendingUnresolved` if any `RegisteredEntry` is unsettled, and verifies that `spec.fulfills` (if set) names an existing `IntentEntry` in `.#~chain/` not
   already fulfilled by a committed settlement.
4. **Admission and scratch approval (`admit`)** — mints `txid = new_txid()` (`secrets.token_hex(16)`) and calls `approve_for_project(compiled,
   ProjectContext(binding, txid))` up to `SCRATCH_ATTEMPTS = 3` times, verifying that all parent directories exist and are non-casefold, no
   `.#~<txid>.<effect_id>.<role>` scratch leaf collides on disk, and all required backend capabilities are present (`ProjectApprovedSpec`).
5. **Coherent capture (`capture_initial_surface`)** — creates `staging/<txid>/` and `work/<txid>/` under `metadata_root` (`create_workspace`), captures every
   path in `initial_surface` without following symlinks, verifies exact `PathState` equality against `initial_surface` (raising `PreconditionRefused` on
   mismatch), stages regular-file preimages into `staging/<txid>/`, and stages caller-supplied `PayloadSource` streams into per-effect `.staging` leaves
   (raising `ProtocolError` if `PayloadSource.open` raises `KeyError`).
6. **Preparation (`prepare_transaction`)** — promotes staged preimage blobs from `staging/<txid>/` into `<metadata_root>/blobs/sha256/<hex>`
   (`promote_staging`) and commits `transaction_record` (`TransactionState.PREPARED`, `CommitDecision.UNCOMMITTED`), `effect` rows (`JournalState.PENDING`),
   `blob` rows, and the singleton `active` row in one `atoms.db` transaction.
7. **Chain registration (`append_registered` + `persist_applying`)** — appends `RegisteredEntry` to `.#~chain/` with `previous` set to the current chain tip
   and persists `registration_digest` and `TransactionState.APPLYING` in `atoms.db`.
8. **Effect execution and postimage verification (`apply_effects` + `verify_prepared_postimages`)** — for each effect in `spec.effects` order, persists
   `JournalState.STARTED`, executes the forward filesystem mutation (applying `exchange`, `transfer_noclobber`, `link_anchor`, `mkdir_leaf`, or `unlink_child`
   with parent directory `fsync`), and persists `JournalState.DONE`. Once all effects reach `DONE`, persists `TransactionState.APPLIED` and verifies
   postimages (`verify_prepared_postimages`, raising `EffectMismatch` on any discrepancy).
9. **Commit finalization (`finalize_commit`)** — (a) atomically persists `CommitDecision.COMMITTED` and `TransactionState.COMMITTED` in one `atoms.db`
   transaction (the durable commit point); (b) appends `SettledEntry(outcome=ChainOutcome.COMMITTED)` to `.#~chain/`; (c) persists `settlement_digest` in
   `atoms.db`; (d) observes a recovery snapshot and runs `run_plan(lease, approved, table, classify_recovery(snapshot))` to remove anchors/tombstones/work
   directories and detach `active` (raising `TransactionHalted` on `HaltPlan`), returning `TransactionOutcome(txid, outcome, registration, settlement,
   final_states)`.
10. **Caught execution rollback (`_roll_back`)** — if any exception other than `ChainStateInvalid` or `MetadataStoreInvalid` is raised after
    `prepare_transaction` and before `CommitDecision.COMMITTED`, `_roll_back` reconciles chain registration, observes a recovery snapshot, and runs
    `run_plan(lease, approved, table, classify_recovery(snapshot))`. That plan persists `TransactionState.ROLLING_BACK`, executes reverse undo transforms and
    scratch removals, persists `TransactionState.ROLLED_BACK`, appends `SettledEntry(outcome=ChainOutcome.ROLLED_BACK)` and persists `settlement_digest` during
    `DetachActive`, and clears `active`. `_roll_back` then re-raises the caught exception (translating `EffectMismatch` to `PreconditionRefused`, or raising
    `TransactionHalted` if classification returns `HaltPlan`).

Standalone `append_intent(backend, project_root, metadata_root, storage, payload) -> str` enters `_writable_recovery_lease`, verifies no pending
registration exists, appends an `IntentEntry(payload)` after the current chain tip, and returns its 64-hex digest.

## Recovery

Every entry into `_recovery_lease` or `_writable_recovery_lease` invokes `resolve(binding, store)` (`atoms.coordinator.recover`) before returning control
to the command:

1. **Active check** — queries `active` in `atoms.db`. If `active` is empty, `resolve` sweeps any leftover unreferenced `staging/<txid>/` or `work/<txid>/`
   workspaces and returns `None`.
2. **Chain reconciliation (`_derive_reconciliation`)** — compares `.#~chain/` against the active `transaction_record`. If a matching `RegisteredEntry` or
   `SettledEntry` exists on disk in `.#~chain/` whose digest is not yet recorded in `transaction_record`, `resolve` backfills `registration_digest` or
   `settlement_digest` into `atoms.db`. If `state == PREPARED` and no `RegisteredEntry` exists on disk while all effect journals are `PENDING`, `resolve`
   appends the missing `RegisteredEntry` and records `registration_digest` before rolling back.
3. **Snapshot observation (`_observe_snapshot`)** — decodes `ProjectApprovedSpec` from `transaction_record.spec_json` and `approval_evidence`, and inspects
   the live `PathState` and inode identity (`(dev, ino)`) of every effect target path, source path, and `.#~<txid>.<effect_id>.<role>` scratch path, together
   with preimage availability in `<metadata_root>/blobs/sha256/`.
4. **Pure classification (`classify_recovery` in `atoms.core.recovery.classify`)** — maps the observed `RecoverySnapshot` and each effect's `(JournalState, live tuple)` against the per-effect recovery state tables:
   - If `committed == CommitDecision.COMMITTED`, classification produces forward cleanup steps (`RemoveScratch` for remaining anchors/tombstones/work slots, transition to `COMMITTED` if needed, and `DetachActive`).
   - If `committed == CommitDecision.UNCOMMITTED`, classification produces reverse rollback steps (`TransitionTransactionState(ROLLING_BACK)`, per-effect `TransformEffectTuple` / `RemoveScratch` in reverse order, `TransitionTransactionState(ROLLED_BACK)`, and `DetachActive`).
   - If any effect tuple does not match an attributable pre-step, in-flight, or post-step state (or a required preimage blob is corrupt or missing), `classify_recovery` returns `HaltPlan(reason, diagnostic)`.

| `EffectVariant` | `JournalState` | Attributable live tuple states (`atoms.core.recovery.variants`) | Rollback / post-commit action |
| ---|---|---|--- |
| `replace_file` | `PENDING` / `STARTED` / `DONE` | Target at `pre` (with `.staging` holding `post`) or target swapped to `post` with `.#~<txid>.<id>.staging` holding `pre`. | Rollback swaps `.staging` back via `RENAME_EXCHANGE` (or restores from preimage blob) and removes `.staging`; post-commit removes `.staging`. |
| `create_file_no_clobber` | `PENDING` / `STARTED` / `DONE` | Target `ABSENT` (with `.staging` absent or holding `post`) or target at `post` (`FileState`). | Rollback transfers target back to `.staging` and removes `.staging`; post-commit removes `.staging`. |
| `delete_path` | `PENDING` / `STARTED` / `DONE` | Target at `pre` (`.anchor`/`.tombstone` absent or present) or target `ABSENT` with `.anchor` (`FileState`) or `.tombstone` (`SymlinkState`) holding `pre`. | Rollback restores target from `.anchor`/`.tombstone` (or preimage blob) and removes scratch; post-commit removes `.anchor`/`.tombstone`. |
| `move_no_clobber` | `PENDING` / `STARTED` / `DONE` | `source` at `source_pre` and `destination` `ABSENT`, or `source` `ABSENT` and `destination` at `source_pre` with `.anchor` retained. | Rollback moves `destination` or links `.anchor` back to `source` and removes `.anchor`; post-commit removes `.anchor`. |
| `create_directory` | `PENDING` / `STARTED` / `DONE` | Target `ABSENT` or empty `DirectoryState(mode)` with `work/<txid>/.#~<txid>.<id>.work` slot. | Rollback moves empty target directory into `.work` and `rmdir`s it; post-commit removes `.work`. |

5. **Plan execution (`run_plan`)** — if classification returns `HaltPlan`, `run_plan` persists `TransactionState.HALTED` and `halt_diagnostic` in `atoms.db`
   (keeping `active` attached) and raises `TransactionHalted`. Otherwise `run_plan` executes `persist_plan_prefix` (`atoms.coordinator.transitions`,
   persisting leading `TransitionEffectState` and `TransitionTransactionState` steps into `effect` and `transaction_record`), runs each authorized
   `TransformEffectTuple` and `RemoveScratch` mutation idempotently, and at `DetachActive` ensures the matching `SettledEntry` (`COMMITTED` or `ROLLED_BACK`)
   is durable in `.#~chain/` and `settlement_digest` is recorded before deleting the `active` row and removing `staging/<txid>/` and `work/<txid>/`.

## Inspection and reads

Read and inspection commands in `atoms.coordinator.commands` never mutate project files, though `_recovery_lease` and `_writable_recovery_lease` commands
finish recovering an interrupted transaction on entry:

- **`read_chain(backend, project_root, metadata_root, storage) -> ChainView`** — on a `WRITABLE` root, enters `_recovery_lease` and returns
  `ChainView(genesis_digest, entries, tip)` (`atoms.chain.read`); on a `READ_ONLY_SERVICEABLE` root, validates and returns the quiescent chain view.
- **`inspect_chain(backend, project_root, metadata_root, storage) -> ChainInspection`** — enters `_project_lease` (after verifying lifecycle state) and
  returns `WellFormedChain(genesis_digest, entries, tip, pending, fulfilled_intents)` or `MalformedChain(defect)` (`atoms.chain.inspect`).
- **`inspect_chain_detached(backend, project_root) -> ChainInspection`** — inspects `<project_root>/.#~chain/` directly without opening `metadata_root` or
  acquiring a project lock.
- **`capture_states(backend, root, paths) -> tuple[tuple[str, PathState], ...]`** — takes a 3-argument `(backend, root, paths)` call (where `paths` is a
  duplicate-free `tuple[str, ...]`) without acquiring a metadata lease, and returns `(path, PathState)` pairs in the caller's tuple order.
- **`read_path_state(backend, project_root, metadata_root, storage, path) -> PathReadResult`** — inspects root lifecycle and recovery state before returning
  `PathObserved(state)` (when the root is resolved and no unsettled registration touches `path`), `ReadNotAttempted(reason, lifecycle_state, detail)` (when
  the root or path cannot be resolved or recovery is halted), or `ReadUnestablished(reason, detail)` (when an unsettled registration touches `path` or an
  ancestor).
- **`read_preimage(backend, project_root, metadata_root, storage, txid, path, *, max_bytes) -> bytes`** — enters `_writable_recovery_lease`, looks up the
  settled (`COMMITTED` or `ROLLED_BACK`) `transaction_record` and `RegisteredEntry` for `txid`, verifies that `path` was a regular `FileState` in the
  registered `initial_surface` with `byte_len <= max_bytes`, reads and verifies the content-addressed blob from `<metadata_root>/blobs/sha256/<digest>`, and
  returns its raw `bytes` (raising `PreconditionRefused` if `txid`, `path`, or the blob is unavailable or exceeds `max_bytes`).

## Failure

All domain errors inherit from `AtomsError` (`atoms.core.errors`):

| Exception class | Module | When raised and protocol post-condition |
| ---|---|--- |
| `ProtocolError` | `atoms.core.errors` | Caller violated a synchronous API contract: passed non-exact argument types, omitted a required `PayloadSource` digest (`KeyError`), attempted a nested store write transaction, or passed an unapproved or mismatched `ProjectApprovedSpec`. |
| `SpecValidationError` | `atoms.core.errors` | Malformed `TransactionSpec`, invalid relative path or identifier, non-canonical digest/mode, surface coverage mismatch, or dependency ordering violation during `compile_spec`. Raised before acquiring a project lease. |
| `CapabilityUnavailable` | `atoms.core.errors` | Volume tuple is not in `CERTIFIED_ALLOWLIST`, SQLite-WAL probe fails, casefold directory flag (`FS_CASEFOLD_FL`) is detected, or required `Capability` is missing. Raised before any project mutation. |
| `ProjectApprovalRefused` | `atoms.core.errors` | Target parent directory is missing or not a directory, a symlink or mount boundary is crossed during anchored traversal, a regular file has `st_nlink > 1`, or scratch leaf allocation fails after `SCRATCH_ATTEMPTS = 3`. Raised before `prepare_transaction`. |
| `PreconditionRefused` | `atoms.core.errors` | Root lifecycle state or `(machine_id, root_path)` binding refuses the operation (including subclasses `RootOperationMismatch` and `SourceSnapshotMoved` in `atoms.coordinator.lifecycle`; note `RootOperationInvalid` subclasses `AtomsError` directly when on-disk root-operation state is malformed), live `PathState` mismatches `initial_surface`, `read_preimage` cannot serve a requested preimage, or `verify_prepared_postimages` detects an `EffectMismatch` (after `_roll_back` settles `ROLLED_BACK`). |
| `PendingUnresolved` | `atoms.chain.errors` | `.#~chain/` contains an unsettled `RegisteredEntry` that recovery could not resolve. Refuses new transactions and intents. |
| `TransactionHalted` | `atoms.core.errors` | Recovery classification returned `HaltPlan` because on-disk state or blob integrity violated the recovery state table. Persists `TransactionState.HALTED` and `halt_diagnostic` in `atoms.db` with `active` still attached; subsequent lease entries fail closed with `TransactionHalted` until administrative repair. |
| `MetadataStoreInvalid` | `atoms.store.errors` | `atoms.db` fails SQLite header, pragma, schema (`SCHEMA_VERSION = 3`), foreign-key, or row-decode validation. Propagates immediately without attempting rollback. |
| `ChainStateInvalid` | `atoms.chain.errors` | `.#~chain/` is missing on a registered root, contains non-canonical JSON, duplicate keys, unknown fields, digest mismatches, sibling branches, or conflicting settlements. Propagates immediately without attempting rollback. |
