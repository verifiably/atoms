# Durability and Crash-Certification Standard

Type: standard; document types 1. Version: 1. Platform capability backends (`atoms.fs`), volume bindings (`VolumeConfiguration`), and storage profiles (`StorageProfile`) conform to this standard when they satisfy the eight primitive filesystem capabilities, pass the runtime volume allowlist and SQLite-WAL coherence probe, and pass the nine-scenario QEMU power-cut replay matrix with zero invariants violated. This standard underpins the on-disk structures in [records.md](records.md) and the transaction and recovery state machines in [protocol.md](protocol.md); see [development.md](development.md) for running the certification harness.

## Capabilities

The coordinator and recovery executor never issue ambient path-based `os.*` mutations against
`project_root`. Every filesystem operation goes through `Backend` (`atoms.fs.backend.Backend`,
audited at runtime by `AuditedBackend`) and relies on eight explicit capabilities defined in
`Capability` (`atoms.core.capabilities`):

| `Capability` enum member | Required by (`ALWAYS_REQUIRED` / `variant_capabilities`) | Semantic guarantee | `LinuxBackend` mechanism (`atoms.fs.linux`) |
|---|---|---|---|
| `ANCHORED_TRAVERSAL` | `ALWAYS_REQUIRED` | Opens and resolves relative paths strictly beneath a pinned root directory descriptor without following symlinks, crossing mount points, or traversing procfs magic links. | `openat2(2)` with `RESOLVE_BENEATH \| RESOLVE_NO_SYMLINKS \| RESOLVE_NO_XDEV \| RESOLVE_NO_MAGICLINKS`. |
| `NOFOLLOW_COHERENT_READ` | `ALWAYS_REQUIRED` | Inspects and reads regular files, directories, and symlinks at the terminal component without following a terminal symlink, returning coherent `(dev, ino, mode, size, sha256)` state. | `fstatat(AT_SYMLINK_NOFOLLOW)`, `readlinkat(2)`, and `openat(O_RDONLY \| O_NOFOLLOW \| O_CLOEXEC)` followed by `fstat(2)` verification. |
| `DURABLE_PUBLISH` | `ALWAYS_REQUIRED` | Flushes file data/metadata and parent directory entries to non-volatile media so a completed rename, link, or unlink survives a subsequent power cut. | `fsync(2)` on regular file descriptors and `O_RDONLY \| O_DIRECTORY` parent directory descriptors. |
| `ADVISORY_PROJECT_LOCK` | `ALWAYS_REQUIRED` | Serializes concurrent coordinator processes operating on the same `<metadata_root>/lock` file across the host. | `flock(fd, LOCK_EX \| LOCK_NB)` on `<metadata_root>/lock`. |
| `NOCLOBBER_TRANSFER` | `CreateFileNoClobber`, `DeletePath`, `MoveNoClobber`, and anchor creation in `ReplaceFile` | Atomically links or renames a directory entry to a destination leaf only when the destination leaf is absent (`EEXIST` on collision), without overwriting an existing entry. | `renameat2(olddirfd, old, newdirfd, new, RENAME_NOREPLACE)` and `linkat(olddirfd, old, newdirfd, new, 0)`. |
| `ATOMIC_EXCHANGE` | `ReplaceFile` (and `MoveNoClobber` rollback when both source and destination exist) | Atomically swaps two existing entries in the same parent directory so neither path is ever observed absent during replacement or rollback. | `renameat2(dirfd, first, dirfd, second, RENAME_EXCHANGE)`. |
| `IDENTITY_ANCHOR` | `ReplaceFile` and `MoveNoClobber` | Preserves a hardlink or staged copy of a preimage entry under `.#~<txid>.<effect_id>.anchor` so recovery can verify `(dev, ino)` identity and restore the exact preimage. | `linkat(2)` for regular files and symlink recreation verified against `(dev, ino)` and `SymlinkState`. |
| `SYMLINK_FINGERPRINT` | Any spec whose `initial_surface` or `final_surface` includes `SymlinkState` | Reads and creates symbolic links without dereferencing their targets and fingerprints `(target, mode)` coherently. | `readlinkat(2)`, `symlinkat(2)`, and `fstatat(AT_SYMLINK_NOFOLLOW)`. |

In addition to checking `backend.capabilities()`, `LinuxBackend` queries `FS_IOC_GETFLAGS` on
every traversed directory and refuses any directory with `FS_CASEFOLD_FL` (`0x40000000`) set
(`CapabilityUnavailable`), preventing case-insensitive filename collisions between distinct POSIX
paths or `.#~` scratch leaves.

## Volume binding and allowlist

Before acquiring a `ProjectBinding`, `certify_volume` (`atoms.fs.probe`) inspects the live kernel
and mount configuration for both `project_root` and `metadata_root` and constructs a
`VolumeConfiguration` (`atoms.fs.volume`):

- Both `project_root` and `metadata_root` must reside on the same mounted filesystem (`st_dev`
  equality) or each match the certified allowlist entry under the caller's declared
  `StorageProfile`.
- `VolumeConfiguration` captures six fields from the host and `/proc/self/mountinfo` +
  `/sys/fs/ext4/<dev>/`:
  - `backend_id`: `"linux"`
  - `backend_revision`: `"linux-4"` (`LINUX_BACKEND_REVISION` in `atoms.fs.linux`)
  - `kernel_identifier`: `os.uname().release` (for example, `"7.2.2-arch1-1"`)
  - `filesystem_type`: `"ext4"`
  - `barrier_options`: sorted tuple of mount durability options
    (`("async", "barrier=1", "commit=5", "data=ordered")`)
  - `durability_features`: sorted tuple of ext4 superblock feature bitmasks
    (`("compat=0x3c", "incompat=0x246", "ro_compat=0x46b")`)
- `StorageProfile` (`atoms.fs.volume`) declares the underlying block-device contract. The
  production profile `FLUSH_HONORING_DISK_V1` has `profile_id = "flush-honoring-disk.v1"` and
  contract `"completed FLUSH makes all previously completed writes durable; a completed FUA write is durable at completion"`.
- `CERTIFIED_ALLOWLIST` (`atoms.fs.volume`) pairs each accepted
  `(VolumeConfiguration, StorageProfile)` tuple with its `certification_ref`
  (`"docs/certification/<date>-ext4-linux-<kernel>.json"`). Any mismatch on kernel release,
  filesystem type, mount barrier options, ext4 feature masks, or storage profile raises
  `CapabilityUnavailable` before the coordinator opens `atoms.db` or mutates `project_root`.

## SQLite-WAL probe

Allowlist membership alone does not prove that the running kernel, filesystem, and SQLite build
honor cross-process WAL visibility on the target mount. During `certify_volume`,
`certify_sqlite_wal` (`atoms.fs.probe`) runs a deterministic two-child subprocess probe inside
`<metadata_root>/.#~wal-probe.<pid>.<token>/` (cached per `(st_dev, VolumeConfiguration)`
in-process):

1. **Writer child (`_wal_probe_writer`)** — opens a fresh SQLite database in `WAL` mode with
   `synchronous = FULL`, inserts a sentinel row without checkpointing, flushes the commit, and
   exits immediately without running SQLite connection teardown (`os._exit(0)`), leaving the
   committed frame solely in the `-wal` file.
2. **Reader and checkpoint child (`_wal_probe_reader`)** — opens the database in a fresh
   subprocess, verifies that the sentinel row is visible through WAL recovery, runs
   `PRAGMA wal_checkpoint(TRUNCATE)`, and verifies that the row remains durable after WAL
   truncation.

If either child fails, times out, or observes stale state, `certify_sqlite_wal` cleans up the
temporary probe directory and raises `CapabilityUnavailable`.

## Crash-certification matrix

A `(VolumeConfiguration, StorageProfile)` tuple may be added to `CERTIFIED_ALLOWLIST` only after
passing the QEMU `dm-log-writes` crash-certification harness (`python/tools/certify/`). The
harness boots a QEMU/KVM guest running the exact host kernel and `e2fsprogs`, records every
block-layer `WRITE`, `FLUSH`, and `FUA` operation via `dm-log-writes` across nine required
scenarios (`CERTIFICATION_SCENARIOS` in `tools/certify/record.py`), and replays every crash
prefix on fresh ext4 images:

| Scenario name | Workload exercised across power-cut prefixes |
|---|---|
| `minimal-create` | Single-file `CreateFileNoClobber` commit and post-commit cleanup. |
| `minimal-replace` | Single-file `ReplaceFile` atomic exchange (`RENAME_EXCHANGE`), anchor retention, and commit. |
| `minimal-delete` | Single-file `DeletePath` tombstone link/rename and commit. |
| `minimal-move` | Single-file `MoveNoClobber` anchor creation, noclobber transfer, and commit. |
| `minimal-mkdir` | Single-directory `CreateDirectory` work-directory setup, creation, and commit. |
| `corpus-write` | Multi-effect transaction combining file creation, replacement, move, deletion, and directory creation with DAG dependencies. |
| `archive-move` | Cross-directory `MoveNoClobber` and `ReplaceFile` batch exercising multiple parent-directory `fsync` barriers. |
| `caught-rollback` | Multi-effect transaction interrupted by an injected mid-execution failure and rolled back via `_roll_back`. |
| `caught-rollback-move` | Cross-directory move and replacement transaction rolled back via `_roll_back` and verified back at `initial_surface`. |

For every replayed FLUSH/FUA prefix across all nine scenarios, the harness runs
`resolve(binding, store)` and verifies that:

- Neither `MetadataStoreInvalid`, `ChainStateInvalid`, nor `TransactionHalted` is raised.
- After recovery settles, every path in `project_root` matches either `initial_surface` (when
  settled `"rolled-back"` or before registration) or `final_surface` (when settled
  `"committed"`), with no leftover `.#~<txid>.*` scratch leaves.
- `.#~chain/` and `atoms.db` agree on the transaction's terminal outcome.

The resulting canonical JSON evidence file (`record_version = 1`, integer counts only,
`zero_violations = true`, verified by `python/tests/test_certification_record.py` against
`CERTIFIED_ALLOWLIST`) is stored in the project's records repository under
`records/atoms/docs/certification/<date>-ext4-linux-<kernel>.json`:

| Top-level JSON field | Type and schema constraint |
|---|---|
| `record_version` | Integer `1`. |
| `date` | ISO 8601 `YYYY-MM-DD` string. |
| `configuration` | Object with `backend_id`, `backend_revision`, `kernel_identifier`, `filesystem_type`, `barrier_options`, `durability_features` matching `VolumeConfiguration`. |
| `storage` | Object with `profile_id` and `contract` matching `StorageProfile`. |
| `harness` | Object with `qemu_command`, `cache_mode` (`"writeback"`), `mkfs_command`, `mount_command`, `versions` (`qemu`, `e2fsprogs`, `kernel`), `replay_log_commit`, `log_format_version`. |
| `atoms_commit` | Non-empty git commit SHA string tested by the sweep. |
| `scenarios` | 9-element array in `CERTIFICATION_SCENARIOS` order; each row has `name`, `marks`, `prefixes`, `violations` (`0`), and `declared_cap`. |
| `zero_violations` | Boolean `true`. |

Each scenario row in `scenarios` records the exact crash-prefix replay counts:

```json
{
  "declared_cap": null,
  "marks": 90,
  "name": "minimal-create",
  "prefixes": 310,
  "violations": 0
}
```

When a certification run detects any invariant violation (`violations > 0`), the writer sets
`zero_violations = false` so `test_certification_record.py` and the allowlist verifier reject the
artifact immediately.

## Platform scope

- **Certified production tuple:** Linux (`backend_id = "linux"`, `backend_revision = "linux-4"`)
  on non-casefold `ext4` mounted with `barrier=1,data=ordered` over `flush-honoring-disk.v1`
  storage, for the kernel release pinned in `CERTIFIED_ALLOWLIST`.
- **Uncertified Linux configurations:** Other kernels, `xfs` or `btrfs` volumes (supported for
  functional developer testing via `.atoms-test-volume`, not production allowlisted), `nobarrier`
  mounts, and casefold-enabled (`+F`) directories are refused at runtime by `certify_volume` or
  `LinuxBackend`.
- **Other operating systems:** No macOS (`Darwin`) or Windows capability backend is implemented;
  `LinuxBackend` refuses non-Linux hosts at initialization.

## Versions

- **1 — 2026-11-04:** Initial public durability and crash-certification standard extracted from
  the Linux capability backend (`linux-4`), runtime volume/WAL probes, and nine-scenario QEMU
  `dm-log-writes` certification harness.
