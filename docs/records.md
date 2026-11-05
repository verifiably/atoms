# Records and On-Disk Formats

Type: data model; document types 1. `atoms` enforces `TransactionSpec` `schema_version` `2` at admission and SQLite `user_version` `3` (`application_id` `0x61746d73`, `"atms"`, with `migrate_root_to_lifecycle_v3` upgrading a clean `user_version` `2` store to `3`) on `atoms.db`; the `.#~chain/<sha256>` entry JSON envelopes are currently unversioned, while temporary `<project_root>/.#~root-claim` pre-grant records carry `"domain": "atoms.root-claim.v1"`. This document defines the durable on-disk structures and canonical encodings that [protocol.md](protocol.md) reads and mutates under the volume and storage guarantees in [durability.md](durability.md).

## Roots and reserved names

A managed project separates consumer-visible project files from coordinator recovery metadata
across two filesystem directory trees on the same certified volume:

- **`project_root`** — the working directory tree whose files, directories, and symlinks are
  mutated by transactions, together with the durable `.#~chain/` history directory, temporary
  pre-grant `.#~root-claim` bookkeeping at its root during root creation, and temporary per-effect
  scratch leaves inside target parent directories.
- **`metadata_root`** — the coordinator's private administrative directory, holding the SQLite
  WAL metadata store (`atoms.db`, `atoms.db-wal`, `atoms.db-shm`), the advisory project lock
  file (`lock`), the volume/WAL certification probe directory (`probe/certify.db`),
  content-addressed preimage blobs (`blobs/sha256/<64-hex>`), pre-commit blob staging
  (`staging/<txid>/`), and temporary execution work directories (`work/<txid>/`).

Every name starting with `SCRATCH_SIGIL = ".#~"` (`atoms.core.scratch`) is reserved at every
directory depth inside `project_root`. Consumer specifications, path queries, and preimage
captures cannot name any path containing a `.#~` segment:

| On-disk path | Constant / module | Lifecycle and role |
|---|---|---|
| `<project_root>/.#~root-claim` | `ROOT_CLAIM_LEAF = ".#~root-claim"` (`atoms.core.scratch`) | Temporary pre-grant root-creation claim (`{"domain":"atoms.root-claim.v1","operation_id":...,"request_hash":...,"request_json":...}`, mode `0o600`), created via `create_exclusive` during `register_root`, `replicate_root`, or `fork_root` and unlinked (`remove_root_claim`) before completion. |
| `<project_root>/.#~chain/` | `CHAIN_LEAF = ".#~chain"` (`atoms.core.scratch`) | Content-addressed directory of immutable `<sha256>` JSON chain entries recording root genesis, transaction registration, transaction settlement, and standalone intents. |
| `<project_root>/.#~chain/.#~stage` | `STAGING_LEAF = ".#~stage"` (`atoms.chain.read`) | Temporary staging leaf inside `.#~chain/` written, `fsync`-flushed, and linked or renamed to `<sha256>` during chain publication; ignored by chain readers if left behind by a crash. |
| `<parent>/.#~<txid>.<effect_id>.<role>` | `scratch_leaf(txid, effect_id, role)` (`atoms.core.scratch`) | Per-effect scratch leaf in the effect's target or source parent directory, where `<role>` is one of `SCRATCH_ROLES = frozenset({"staging", "tombstone", "anchor", "work"})` (`ScratchRole.STAGING`, `ScratchRole.TOMBSTONE`, `ScratchRole.ANCHOR`, `ScratchRole.WORK` in `atoms.core.recovery.model`). |
| `<metadata_root>/lock` | `acquire_project_lock` (`atoms.fs.lock`) | Advisory exclusive lock file (`HeldProjectLock`, `flock(LOCK_EX)`) serializing all mutating and recovery-lease operations on the root pair. |
| `<metadata_root>/atoms.db` | `open_store` (`atoms.store.connection`) | SQLite 3 metadata store in WAL mode (`synchronous = FULL`, `foreign_keys = ON`, `STRICT` tables). |
| `<metadata_root>/blobs/sha256/<64-hex>` | `BLOBS_PARENT` (`atoms.store.blobs`) | Immutable content-addressed regular-file preimages and payloads referenced by `blob.digest` (`sha256:<64-hex>`). |
| `<metadata_root>/staging/<txid>/` | `Workspace.staging_fd` (`atoms.store.workspace`) | Per-transaction pre-prepare staging directory (`create_workspace` / `reopen_workspace`) for captured preimages before promotion into `blobs/sha256/`. |
| `<metadata_root>/work/<txid>/` | `Workspace.work_fd` (`atoms.store.workspace`) | Per-transaction directory holding `.#~<txid>.<effect_id>.work` directory-deletion scratch slots during `CreateDirectory` rollback. |

## Paths and path states

All consumer paths inside `project_root` are validated by `require_rel_path` (`atoms.core.paths`)
before compilation or path capture:

- Paths are relative POSIX strings using forward-slash (`/`) separators; leading or trailing `/`,
  empty string `""`, empty segments (`//`), `.` or `..` segments, NUL bytes (`\x00`), and any
  segment starting with `.#~` raise `SpecValidationError`.
- Two paths conflict as ancestors when one is a strict path prefix of the other (`a` vs `a/b`).

A path's observed or declared state is represented by `PathState` (`atoms.core.fingerprint`):

| Type | Fields and invariants | Meaning |
|---|---|---|
| `AbsentState` (`ABSENT`) | Singleton (`atoms.core.fingerprint.ABSENT`, JSON `{"type": "absent"}`) | No directory entry exists at the relative path (`ENOENT` on `lstat`). |
| `FileState` | `content_hash: str`, `mode: int`, `byte_len: int` (JSON `"type": "file"`) | Regular file (`S_IFREG`) with `content_hash` in `sha256:<64 lowercase hex>` format, permission bits `0 <= mode <= 0o7777` (`stat.S_IMODE`), and `byte_len >= 0`. |
| `DirectoryState` | `mode: int` (JSON `"type": "directory"`) | Directory (`S_IFDIR`) with permission bits `0 <= mode <= 0o7777`. |
| `SymlinkState` | `target: str`, `mode: int` (JSON `"type": "symlink"`) | Symbolic link (`S_IFLNK`) with non-empty, NUL-free `target` string and `0 <= mode <= 0o7777`. |

Special files (FIFOs, sockets, block or character devices) and hardlinked regular files
(`st_nlink > 1`) have no `PathState` representation and are refused during path capture and
project approval (`ProjectApprovalRefused` or `PreconditionRefused`).

## Effects and transaction specs

A transaction declares an ordered tuple of primitive filesystem effects (`atoms.core.effects`),
each identified by an `effect_id` matching `SAFE_IDENTIFIER = ^[A-Za-z0-9_-]{1,64}$`
(`atoms.core.identifiers`):

| Effect class (`EffectVariant`) | Fields | Pre-state -> Post-state | Scratch role allocated (`atoms.core.recovery.snapshot`) |
|---|---|---|---|
| `ReplaceFile` (`"replace_file"`) | `effect_id: str`, `path: str`, `pre: FileState`, `post: FileState` | `path: pre` -> `path: post` | `ScratchRole.STAGING` (`.#~<txid>.<effect_id>.staging`, swapped with `path` via `RENAME_EXCHANGE`) |
| `CreateFileNoClobber` (`"create_file_no_clobber"`) | `effect_id: str`, `path: str`, `post: FileState` | `path: ABSENT` -> `path: post` | `ScratchRole.STAGING` (`.#~<txid>.<effect_id>.staging`) |
| `DeletePath` (`"delete_path"`) | `effect_id: str`, `path: str`, `pre: FileState \| SymlinkState` | `path: pre` -> `path: ABSENT` | `ScratchRole.ANCHOR` when `pre` is `FileState`; `ScratchRole.TOMBSTONE` when `pre` is `SymlinkState` |
| `MoveNoClobber` (`"move_no_clobber"`) | `effect_id: str`, `source: str`, `destination: str`, `source_pre: FileState` | `source: source_pre`, `destination: ABSENT` -> `source: ABSENT`, `destination: source_pre` (regular files only, `source != destination`) | `ScratchRole.ANCHOR` in the `source` parent directory |
| `CreateDirectory` (`"create_directory"`) | `effect_id: str`, `path: str`, `post: DirectoryState` | `path: ABSENT` -> `path: post` | `ScratchRole.WORK` (`work/<txid>/.#~<txid>.<effect_id>.work` for rollback rename) |

`TransactionSpec` (`atoms.core.spec`, constructed directly or via `build_spec` and validated by
`compile_spec` in `atoms.core.compiler`) bundles the effects with surface declarations and chain
lineage metadata:

| Field | Type | Constraint |
|---|---|---|
| `schema_version` | `int` | Must equal `SCHEMA_VERSION = 2`. |
| `consumer_tag` | `str` | Matches `SAFE_IDENTIFIER` (`^[A-Za-z0-9_-]{1,64}$`). |
| `intent_digest` | `str` | Matches `^sha256:[0-9a-f]{64}$`; records the caller's upstream intent digest. |
| `initial_surface` | `tuple[SurfaceEntry, ...]` | Strictly sorted by `path`; must cover the exact set of paths touched by `effects` (untouched paths are rejected with `SpecValidationError`). |
| `final_surface` | `tuple[SurfaceEntry, ...]` | Strictly sorted by `path`; matches the terminal state produced on each touched path by executing `effects` in timeline order. |
| `effects` | `tuple[Effect, ...]` | Non-empty tuple of effects with unique `effect_id` values; multi-effect timelines on the same path or ancestor-descendant paths must be ordered by explicit `dependencies`. |
| `dependencies` | `tuple[Dependency, ...]` | Strictly sorted by `(before, after)` over declared `effect_id` values; every `Dependency(before, after)` must respect `effects` order (`order[before] < order[after]`). |
| `fulfills` | `str \| None` | Optional raw 64-hex chain digest (`^[0-9a-f]{64}$`) of a prior `IntentEntry` in `.#~chain/`. |
| `registered_paths` | `tuple[str, ...]` | Strictly sorted subset of touched paths whose `(initial, final)` surface states are published into the chain's `RegisteredEntry`. |

Canonical JSON serialization (`canonical_json` in `atoms.core.canonical`) follows RFC 8785
(JCS): UTF-8, lexicographically sorted object keys, no whitespace separators, no trailing
newline, and integer-only numbers (floating-point values are rejected). At admission, `new_txid()`
(`atoms.coordinator.admission`) mints a fresh 128-bit random `txid` (`secrets.token_hex(16)`,
32 lowercase hex characters). The canonical `TransactionSpec` is persisted in
`transaction_record.spec_json` alongside `encode_approval_evidence(approved)` in
`transaction_record.approval_evidence`:

```json
{
  "consumer_tag": "ingest",
  "dependencies": [],
  "effects": [
    {
      "effect_id": "write-readme",
      "path": "README.txt",
      "post": {
        "byte_len": 12,
        "content_hash": "sha256:a948904f2f0f479b8f8197694b30184b0d2ed1c1cd2a1ec0fb85d299a192a447",
        "mode": 420,
        "type": "file"
      },
      "type": "CreateFileNoClobber"
    }
  ],
  "final_surface": [
    {
      "path": "README.txt",
      "state": {
        "byte_len": 12,
        "content_hash": "sha256:a948904f2f0f479b8f8197694b30184b0d2ed1c1cd2a1ec0fb85d299a192a447",
        "mode": 420,
        "type": "file"
      }
    }
  ],
  "fulfills": null,
  "initial_surface": [
    {
      "path": "README.txt",
      "state": {
        "type": "absent"
      }
    }
  ],
  "intent_digest": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "registered_paths": [
    "README.txt"
  ],
  "schema_version": 2
}
```

## Project chain

`<project_root>/.#~chain/` stores an append-only, content-addressed, strictly linear chain of
JSON files (`atoms.chain.model`, `atoms.chain.read`, `atoms.chain.append`, `atoms.chain.inspect`).
Each entry is serialized as canonical JSON without a trailing newline (`_canonical_bytes`),
staged at `.#~chain/.#~stage` (`STAGING_LEAF`), flushed with `fsync`, and published under its
lowercase SHA-256 hex digest `<sha256>`. Readers verify that every filename in `.#~chain/`
(excluding `.#~stage`) is a 64-hex digest matching the SHA-256 of its file bytes, reject
duplicate JSON keys, unknown fields, or sibling branches (`DefectKind.SIBLING_BRANCH` /
`ChainStateInvalid`), and validate four `"class"` schemas:

| Entry class (`class`) | JSON fields (`atoms.chain.model`) | Linear chain invariants |
|---|---|---|
| `GenesisEntry` (`"genesis"`) | `class` (`"genesis"`), `payload` (base64 string of raw `bytes`), `baseline` (sorted array of `[path, state_json]` pairs), `previous` (`null`) | Exactly one genesis entry exists at index 0 (`previous: null`); any other entry with `previous: null` or a second genesis entry is rejected. |
| `RegisteredEntry` (`"registered"`) | `class` (`"registered"`), `txid` (32-hex), `intent_digest` (`sha256:<64-hex>`), `consumer_tag`, `initial` (sorted `[path, state_json]` pairs for `registered_paths`), `final` (sorted `[path, state_json]` pairs for `registered_paths`), `fulfills` (64-hex digest of an `IntentEntry` or `null`), `previous` (64-hex digest of the current chain tip) | Appended before any filesystem effect executes; `previous` must name the single predecessor tip in `.#~chain/`. |
| `SettledEntry` (`"settled"`) | `class` (`"settled"`), `txid` (32-hex), `registration` (64-hex digest of the matching `RegisteredEntry`), `outcome` (`"committed"` or `"rolled-back"`), `previous` (64-hex digest of the current chain tip) | Settles an earlier `RegisteredEntry` with the same `txid`; at most one `SettledEntry` may settle a given `registration` or `txid`. |
| `IntentEntry` (`"intent"`) | `class` (`"intent"`), `payload` (base64 string of raw `bytes`), `previous` (64-hex digest of the current chain tip) | Records an out-of-band caller intent without mutating project files; may be fulfilled by at most one committed `RegisteredEntry`. |

In `WellFormedChain` (`atoms.chain.inspect`), `pending` is a `tuple[tuple[str, str], ...]` of
`(txid, registration_digest)` pairs for every `RegisteredEntry` with no matching `SettledEntry`.
A new transaction, standalone intent, or `capture_states` call on a registered root refuses to
proceed with `PendingUnresolved` if `pending` is non-empty after recovery.

## Metadata store

`<metadata_root>/atoms.db` is a SQLite 3 database configured with `PRAGMA journal_mode = WAL`,
`PRAGMA synchronous = FULL`, `PRAGMA foreign_keys = ON`, `PRAGMA application_id = 0x61746d73`
(`1635019635`, ASCII `"atms"`), and `PRAGMA user_version = 3` (`SCHEMA_VERSION = 3` in
`atoms.store.schema`). Opening a store with a mismatched `application_id`, a non-WAL journal
mode, an unknown `user_version`, or missing/extra tables, columns, or triggers raises
`MetadataStoreInvalid`. All six tables use SQLite `STRICT` typing:

| Table | Primary key | Columns and `CHECK` constraints (`atoms.store.schema`) | Purpose |
|---|---|---|---|
| `transaction_record` | `txid TEXT` | `spec_json TEXT NOT NULL`, `state TEXT NOT NULL` (`'prepared'`, `'applying'`, `'applied'`, `'committed'`, `'rolling_back'`, `'rolled_back'`, `'halted'`), `committed TEXT NOT NULL` (`'uncommitted'`, `'committed'`), `rollback_result TEXT` (`'clean'`, `'halted'`), `halt_diagnostic TEXT`, `registration_digest TEXT`, `settlement_digest TEXT`, `approval_evidence TEXT NOT NULL`, `assembly_halt TEXT` | Durable transaction state machine, canonical `TransactionSpec` (`spec_json`), encoded project approval evidence, chain linkage digests, and halt diagnostics. |
| `effect` | `(txid, effect_id)` | `txid TEXT NOT NULL REFERENCES transaction_record(txid)`, `effect_id TEXT NOT NULL`, `variant TEXT NOT NULL` (`'create_directory'`, `'create_file_no_clobber'`, `'delete_path'`, `'move_no_clobber'`, `'replace_file'`), `journal_state TEXT NOT NULL` (`'done'`, `'pending'`, `'started'`, `'undone'`, `'undo_started'`) | Per-effect execution and rollback journal (`pending -> started -> done` and `undo_started -> undone`). |
| `blob` | `digest TEXT` | `byte_len INTEGER NOT NULL CHECK (byte_len >= 0)` | Index of content-addressed regular-file preimages stored under `<metadata_root>/blobs/sha256/<64-hex>` (keyed in `blob` as `sha256:<64-hex>`). |
| `active` | `singleton INTEGER` | `CHECK (singleton = 0)`, `txid TEXT NOT NULL REFERENCES transaction_record(txid)` | Single-row pointer (`singleton = 0`) naming the currently active transaction; cleared only when `DetachActive` (`detach_active`) completes. |
| `root_lifecycle` | `singleton INTEGER` | `CHECK (singleton = 0)`, `state TEXT NOT NULL` (`'writable'`, `'read-only-serviceable'`, `'read-only-unserviceable'`), `machine_id TEXT NOT NULL` (32-hex `/etc/machine-id`), `root_path TEXT NOT NULL`, `origin TEXT NOT NULL` (`'register'`, `'replicate'`, `'fork'`, `'read-serviceability'`, `'migration-v2'`) | Binds `metadata_root` to `(machine_id, root_path)` and records the root's lifecycle state and creation origin. |
| `root_operation` | `singleton INTEGER` | `CHECK (singleton = 0)`, `operation_id TEXT NOT NULL`, `kind TEXT NOT NULL` (`'register'`, `'replicate'`, `'fork'`), `phase TEXT NOT NULL` (`'recorded'`, `'source-snapshot-durable'`, `'tree-durable'`, `'complete'`), `request_json TEXT NOT NULL`, `request_hash TEXT NOT NULL`, `source_snapshot_json TEXT`, `destination_snapshot_json TEXT`, `genesis_digest TEXT` | Crash-recovery journal for multi-step `register_root`, `replicate_root`, and `fork_root` operations. |
