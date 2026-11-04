# Records and On-Disk Formats

Type: data model; document types 1. `atoms` enforces `TransactionSpec` `schema_version` `2` at admission and SQLite `user_version` `3` (`application_id` `0x61746d73`, `"atms"`, with `migrate_root_to_lifecycle_v3` upgrading a clean `user_version` `2` store to `3`) on `atoms.db`; the `.#~chain/<sha256>` entry JSON envelopes and `.#~root-claim` binding proof are currently unversioned. This document defines the durable on-disk structures and canonical encodings that [protocol.md](protocol.md) reads and mutates under the volume and storage guarantees in [durability.md](durability.md).

## Roots and reserved names

A managed project separates consumer-visible project files from coordinator recovery metadata
across two filesystem directory trees on the same certified volume:

- **`project_root`** — the working directory tree whose files, directories, and symlinks are
  mutated by transactions, together with two durable hidden control artefacts at its root and
  temporary per-effect scratch leaves inside target parent directories.
- **`metadata_root`** — the coordinator's private administrative directory, holding the SQLite
  WAL metadata store (`atoms.db`, `atoms.db-wal`, `atoms.db-shm`), the advisory project lock
  file (`lock`), content-addressed preimage blobs (`blobs/<hex>`), pre-commit blob staging
  (`staging/<txid>/`), and temporary capture workspaces (`work/<txid>/`).

Every name starting with `SCRATCH_SIGIL = ".#~"` (`atoms.core.scratch`) is reserved at every
directory depth inside `project_root`. Consumer specifications, path queries, and preimage
captures cannot name any path containing a `.#~` segment:

| On-disk path | Constant / module | Lifecycle and role |
|---|---|---|
| `<project_root>/.#~root-claim` | `ROOT_CLAIM_LEAF = ".#~root-claim"` (`atoms.coordinator.root`) | Immutable 33-byte ASCII root identity proof (`<32-hex root_id>\n`, mode `0o600`), written via noclobber staging and compared by `(dev, ino)` and content against `root_lifecycle` in `atoms.db`. |
| `<project_root>/.#~chain/` | `CHAIN_LEAF = ".#~chain"` (`atoms.chain.model`) | Content-addressed directory of immutable `<sha256>` JSON chain entries recording root genesis, transaction registration, transaction settlement, and standalone intents. |
| `<project_root>/.#~chain/.#~stage` | `STAGING_LEAF = ".#~stage"` (`atoms.chain.read`) | Temporary staging leaf inside `.#~chain/` written, `fsync`-flushed, and linked or renamed to `<sha256>` during chain publication; ignored by chain readers if left behind by a crash. |
| `<parent>/.#~<txid>.<effect_id>.<role>` | `scratch_leaf(txid, effect_id, role)` (`atoms.core.scratch`) | Per-effect scratch leaf in the effect's target parent directory, where `<role>` is one of `SCRATCH_ROLES = frozenset({"staging", "tombstone", "anchor", "work"})` (`ScratchRole.STAGING`, `ScratchRole.TOMBSTONE`, `ScratchRole.ANCHOR`, `ScratchRole.WORK`). |
| `<metadata_root>/lock` | `acquire_project_lock` (`atoms.coordinator.lease`) | Advisory exclusive lock file (`flock(LOCK_EX)`) serializing all mutating and recovery-lease operations on the root pair. |
| `<metadata_root>/atoms.db` | `open_store` (`atoms.store.connection`) | SQLite 3 metadata store in WAL mode (`synchronous = FULL`, `foreign_keys = ON`, `STRICT` tables). |
| `<metadata_root>/blobs/<64-hex>` | `open_workspace` (`atoms.store.workspace`) | Immutable content-addressed regular-file preimages and payloads referenced by `blob.digest` (`sha256:<64-hex>`). |
| `<metadata_root>/staging/<txid>/` | `Workspace.staging_dir` (`atoms.store.workspace`) | Per-transaction pre-prepare staging directory for captured preimages and `PayloadSource` streams before promotion into `blobs/`. |
| `<metadata_root>/work/<txid>/` | `Workspace.work_dir` (`atoms.store.workspace`) | Per-transaction temporary directory swept during `DetachActive` or subsequent workspace creation. |

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
| `AbsentState` (`ABSENT`) | Singleton (`atoms.core.fingerprint.ABSENT`) | No directory entry exists at the relative path (`ENOENT` on `lstat`). |
| `FileState` | `content_hash: str`, `mode: int`, `byte_len: int` | Regular file (`S_IFREG`) with `content_hash` in `sha256:<64 lowercase hex>` format, permission bits `0 <= mode <= 0o7777` (`stat.S_IMODE`), and `byte_len >= 0`. |
| `DirectoryState` | `mode: int` | Directory (`S_IFDIR`) with permission bits `0 <= mode <= 0o7777`. |
| `SymlinkState` | `target: str`, `mode: int` | Symbolic link (`S_IFLNK`) with non-empty, NUL-free `target` string and `0 <= mode <= 0o7777`. |

Special files (FIFOs, sockets, block or character devices) and hardlinked regular files
(`st_nlink > 1`) have no `PathState` representation and are refused during path capture and
project approval (`ProjectApprovalRefused` or `PreconditionRefused`).

## Effects and transaction specs

A transaction declares an ordered tuple of primitive filesystem effects (`atoms.core.effects`),
each identified by an `effect_id` matching `SAFE_IDENTIFIER = ^[A-Za-z0-9_-]{1,64}$`
(`atoms.core.identifiers`):

| Effect class (`EffectKind`) | Target path(s) | Pre-state -> Post-state | Scratch roles allocated |
|---|---|---|---|
| `ReplaceFile` (`"replace-file"`) | `path` | `FileState(preimage_hash, preimage_mode, preimage_byte_len)` -> `FileState(payload_hash, mode, payload_byte_len)` | `staging`, `anchor` |
| `CreateFileNoClobber` (`"create-file-noclobber"`) | `path` | `ABSENT` -> `FileState(payload_hash, mode, payload_byte_len)` | `staging` |
| `DeletePath` (`"delete-path"`) | `path` | `FileState` or `SymlinkState` (`preimage`) -> `ABSENT` | `tombstone` |
| `MoveNoClobber` (`"move-noclobber"`) | `source_path`, `dest_path` | `source_path: preimage`, `dest_path: ABSENT` -> `source_path: ABSENT`, `dest_path: preimage` (`FileState` or `SymlinkState`, `source_path != dest_path`) | `anchor` |
| `CreateDirectory` (`"create-directory"`) | `path` | `ABSENT` -> `DirectoryState(mode)` | `work` |

`TransactionSpec` (`atoms.core.spec`, constructed directly or via `build_spec`) bundles the
effects with surface declarations and chain lineage metadata:

| Field | Type | Constraint |
|---|---|---|
| `schema_version` | `int` | Must equal `SCHEMA_VERSION = 2`. |
| `consumer_tag` | `str` | Matches `SAFE_IDENTIFIER` (`^[A-Za-z0-9_-]{1,64}$`). |
| `intent_digest` | `str` | Matches `^sha256:[0-9a-f]{64}$`; records the caller's upstream intent digest. |
| `initial_surface` | `tuple[SurfaceEntry, ...]` | Strictly sorted by `path`; covers every effect input path (`path` or `source_path` / `dest_path`) and every read-only `registered_paths` entry. |
| `final_surface` | `tuple[SurfaceEntry, ...]` | Strictly sorted by `path`; matches the post-state produced by applying `effects` to `initial_surface`. |
| `effects` | `tuple[Effect, ...]` | Non-empty tuple of effects with unique `effect_id` values and pairwise distinct target paths (no path is mutated twice or shares an ancestor-descendant pair with another mutated path). |
| `dependencies` | `tuple[Dependency, ...]` | Strictly sorted by `(from_effect_id, to_effect_id)` over declared `effect_id` values; must form a DAG. |
| `fulfills` | `str \| None` | Optional raw 64-hex chain digest (`^[0-9a-f]{64}$`) of a prior `IntentEntry` in `.#~chain/`. |
| `registered_paths` | `tuple[str, ...]` | Strictly sorted tuple of read-only relative paths verified in `initial_surface` and unchanged in `final_surface`. |

Canonical JSON serialization (`canonical_json` in `atoms.core.canonical`) follows RFC 8785
(JCS): UTF-8, lexicographically sorted object keys, no whitespace separators, and integer-only
numbers (floating-point values are rejected). At admission, `new_txid()`
(`atoms.coordinator.admission`) mints a fresh 128-bit random `txid` (`secrets.token_hex(16)`,
32 lowercase hex characters). A compiled and project-approved spec is persisted in
`transaction_record.spec_json` with its JCS digest in `transaction_record.spec_hash`:

```json
{
  "consumer_tag": "ingest",
  "dependencies": [],
  "effects": [
    {
      "effect_id": "write-readme",
      "kind": "create-file-noclobber",
      "mode": 420,
      "path": "README.txt",
      "payload_byte_len": 12,
      "payload_hash": "sha256:a948904f2f0f479b8f8197694b30184b0d2ed1c1cd2a1ec0fb85d299a192a447"
    }
  ],
  "final_surface": [
    {
      "path": "README.txt",
      "state": {
        "byte_len": 12,
        "content_hash": "sha256:a948904f2f0f479b8f8197694b30184b0d2ed1c1cd2a1ec0fb85d299a192a447",
        "kind": "file",
        "mode": 420
      }
    }
  ],
  "fulfills": null,
  "initial_surface": [
    {
      "path": "README.txt",
      "state": {"kind": "absent"}
    }
  ],
  "intent_digest": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "registered_paths": [],
  "schema_version": 2
}
```

## Project chain

`<project_root>/.#~chain/` stores an append-only, content-addressed DAG of JSON files
(`atoms.chain.model`, `atoms.chain.read`, `atoms.chain.append`). Each entry is serialized as
canonical JSON (`json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"`),
staged at `.#~chain/.#~stage` (`STAGING_LEAF`), flushed with `fsync`, and published under its
lowercase SHA-256 hex digest `<sha256>`. Readers verify that every filename in `.#~chain/`
(excluding `.#~stage`) is a 64-hex digest matching the SHA-256 of its file bytes, reject
duplicate JSON keys or unknown fields (`ChainStateInvalid`), and validate four `"kind"` schemas:

| Entry class (`kind`) | Required JSON fields | Topological invariants |
|---|---|---|
| `GenesisEntry` (`"genesis"`) | `kind`, `root_id` (32-hex), `created_at` (ISO 8601 UTC string) | Exactly one genesis entry exists in a valid chain; its `root_id` matches `.#~root-claim` (`root_lifecycle.root_id` in `atoms.db`). |
| `RegisteredEntry` (`"registered"`) | `kind`, `txid` (32-hex), `spec_digest` (`sha256:<64-hex>`), `consumer_tag`, `intent_digest` (`sha256:<64-hex>`), `parents` (sorted list of 64-hex chain digests), `registered_paths` (sorted list of relative paths), optional `fulfills` (64-hex digest of an `IntentEntry`) | Written before any filesystem effect executes; `parents` must be non-empty and exist in `.#~chain/`. |
| `SettledEntry` (`"settled"`) | `kind`, `txid` (32-hex), `registration` (64-hex digest of the matching `RegisteredEntry`), `outcome` (`"committed"` or `"rolled-back"`), `parents` (`[registration]`) | Settles a `RegisteredEntry`; at most one `SettledEntry` may reference a given `registration` or `txid`. |
| `IntentEntry` (`"intent"`) | `kind`, `consumer_tag`, `intent_digest` (`sha256:<64-hex>`), `parents` (non-empty sorted list of 64-hex digests), optional `fulfills` (64-hex digest of an earlier `IntentEntry`) | Records an out-of-band intent without mutating project files; may be fulfilled by a later `IntentEntry` or `RegisteredEntry`. |

A `RegisteredEntry` with no matching `SettledEntry` is a pending registration
(`PendingRegistration`). A new transaction or standalone intent refuses to proceed with
`PendingUnresolved` if any `RegisteredEntry` remains unsettled after recovery.

## Metadata store

`<metadata_root>/atoms.db` is a SQLite 3 database configured with `PRAGMA journal_mode = WAL`,
`PRAGMA synchronous = FULL`, `PRAGMA foreign_keys = ON`, `PRAGMA application_id = 0x61746d73`
(`1635019635`, ASCII `"atms"`), and `PRAGMA user_version = 3` (`SCHEMA_VERSION = 3` in
`atoms.store.schema`). Opening a store with a mismatched `application_id`, a non-WAL journal
mode, an unknown `user_version`, or missing/extra tables or columns raises
`MetadataStoreInvalid`. All six tables use SQLite `STRICT` typing:

| Table | Primary key | Columns and constraints | Purpose |
|---|---|---|---|
| `transaction_record` | `txid TEXT` | `state TEXT` (`PREPARED`, `APPLYING`, `APPLIED`, `COMMITTED`, `ROLLING_BACK`, `ROLLED_BACK`, `HALTED`), `commit_decision TEXT` (`NONE`, `COMMITTED`), `spec_json TEXT`, `spec_hash TEXT`, `consumer_tag TEXT`, `intent_digest TEXT`, `chain_parents_json TEXT`, `fulfills TEXT`, `registration_digest TEXT`, `settlement_digest TEXT`, `recovery_plan_json TEXT`, `halt_diagnostic_json TEXT` | Durable transaction state machine, canonical `ProjectApprovedSpec` payload, chain linkage digests, persisted recovery plan prefix, and halt diagnostic. |
| `effect` | `(txid, ordinal)` | `txid TEXT REFERENCES transaction_record(txid)`, `ordinal INTEGER`, `effect_id TEXT` (`UNIQUE(txid, effect_id)`), `kind TEXT`, `target_path TEXT`, `source_path TEXT`, `scratch_json TEXT`, `preimage_json TEXT`, `postimage_json TEXT`, `journal_state TEXT` (`PENDING`, `STARTED`, `DONE`) | Per-effect execution journal (`PENDING -> STARTED -> DONE`), scratch leaf assignments, and expected preimage/postimage fingerprints. |
| `blob` | `digest TEXT` | `byte_len INTEGER NOT NULL CHECK (byte_len >= 0)` | Index of content-addressed regular-file preimages and payloads stored under `<metadata_root>/blobs/<64-hex>` (keyed in `blob` as `sha256:<64-hex>`). |
| `active` | `singleton INTEGER` | `CHECK (singleton = 1)`, `txid TEXT NOT NULL UNIQUE REFERENCES transaction_record(txid)` | Single-row pointer naming the currently active (unsettled or uncleaned) transaction; cleared only when `DetachActive` completes. |
| `root_lifecycle` | `singleton INTEGER` | `CHECK (singleton = 1)`, `root_id TEXT`, `claim_dev INTEGER`, `claim_ino INTEGER`, `mode TEXT` (`writable`, `read-only`), `serviceable INTEGER` (`0` or `1`), `origin TEXT` (`registered`, `replicated`, `forked`), `genesis_digest TEXT` | Binds `metadata_root` to `<project_root>/.#~root-claim` by `root_id` and `(claim_dev, claim_ino)` and records the root's lifecycle mode and read serviceability. |
| `root_operation` | `singleton INTEGER` | `CHECK (singleton = 1)`, `operation TEXT` (`replicate`, `fork`), `phase TEXT` (`preparing`, `cutting-over`), `root_id TEXT`, `source_project_root TEXT`, `dest_project_root TEXT`, `dest_metadata_root TEXT`, `source_genesis_digest TEXT`, `source_head_digests_json TEXT`, `fork_genesis_digest TEXT`, `claim_dev INTEGER`, `claim_ino INTEGER` | Crash-recovery journal for multi-step `replicate_root` and `fork_root` operations on a destination `metadata_root`. |
