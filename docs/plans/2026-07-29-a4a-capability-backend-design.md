# A4a — platform capability backend and project volume binding

**Status:** Draft for owner review. No A4a production code may land until this design is approved.

**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md).
Where this document and the authority design disagree, the authority wins.

## 1. Decision

A4a delivers the first impure layer of the engine: a `Backend` protocol expressed in the §5.5 semantic
capability vocabulary, one Linux implementation of it, resolution of the project volume's durability
configuration, the idempotent bootstrap that §5.5 requires before capability approval, and the empirical
capability probe. Its public result is a live `ProjectBinding` exposing a frozen, factory-issued
`VolumeEvidence`.

A4a decides nothing about a specification. It answers exactly one question — *what does this volume
supply, and is its durability configuration certified?* — and hands the answer to A4b, which alone
composes it with a `CompiledSpec` into `ProjectApprovedSpec`.

The layer exists because A2 derives a required capability set that has never been checked against a real
filesystem, and because every stage after it (A4b's rooted path proof, A5's metadata store, A6's capture,
A7's executor) needs anchored descriptors, a held project lock, and a certified volume before it may
write anything.

## 2. Scope and non-scope

### 2.1 In scope

- A new impure package `atoms/fs/`, with `atoms/core/` remaining pure.
- `ctypes` bindings for the syscalls the stdlib does not expose: `openat2` and `renameat2`.
- A `Backend` protocol covering exactly the operations the §5.5 capabilities are defined over, plus one
  Linux implementation.
- Explicit platform and architecture selection, refusing anything unsupported.
- Mount-identity resolution, `VolumeConfiguration`, `StorageProfile`, and `DurabilityAllowlist`.
- The idempotent bootstrap: `metadata_root` creation, the advisory project lock, the metadata layout, and
  unconditional probe-survivor reclamation.
- The empirical capability probe, including SQLite-WAL hostability certification.
- `bind_project_volume`, `ProjectBinding`, and `VolumeEvidence`.

### 2.2 Not in scope

A4a performs no path-shaped or specification-shaped work. It does not implement `approve_for_project` or
`ProjectApprovedSpec`; it does not check containment, the metadata-root exclusion, `NAME_MAX`/`PATH_MAX`,
per-directory name equivalence, the resolved topology, or scratch-leaf distinctness. All of that is A4b.

It writes no transaction record, defines no SQLite schema, captures nothing, and executes no effect. It
does not adjudicate whether a specification's required capabilities are satisfied — it reports what the
volume supplies and stops.

It makes no claim that any volume is power-loss durable. Its probes establish functional availability
only; the durability claim is carried solely by a matched allowlist entry.

### 2.3 Relationship to A4b

A4a supplies mechanism; A4b supplies judgment. A4b consumes the live `ProjectBinding` — not detached
evidence — and is the first stage that sees a `CompiledSpec`. The split exists so that the syscall,
probing, and durability-configuration surface can be reviewed and verified independently of the
seven-obligation path proof that follows it.

## 3. Seam review against the deferred-obligation ledger

`AGENTS.md` requires this review before the A4a plan is written.

### 3.1 Existing entries

A4a **discharges no existing ledger entry.**

Entry **#6** ("a required-capability set is derived but never checked against a backend") is *not*
discharged here. A4a builds the mechanism, but A4b is the first stage holding the exact
`CompiledSpec` required-capability set, and only it can prove the required subset is supplied before
issuing `ProjectApprovedSpec`. `bind_project_volume` must not refuse because an optional capability is
absent — that would break the progressive capability support the authority requires (§14). Ownership of
#6 therefore moves to A4b, and the ledger is corrected accordingly.

The full A4b ledger set is **#2, A4's part of #3, #4, #5, #6, #9, #10, and #11**.

A4a supplies primitives that several of these depend on: anchored traversal and retained parent
descriptors (#5), the retained `metadata_root` `st_dev`/`st_ino` used for exclusion by identity (#5), and
the backend operations A4b needs to interrogate a concrete parent directory (#2, #11). Supplying a
primitive discharges nothing.

### 3.2 New entries this design creates

Two new ledger entries are added in the same commit as this design.

| Admitted shape | Admitted by | First owner | Required behavior |
| --- | --- | --- | --- |
| `VolumeEvidence` is a detached frozen value that describes a volume but authorizes no access to it | A4a binding contract | A4b | `ProjectApprovedSpec` retains the live `ProjectBinding` or equivalent held descriptors; it may not authorize filesystem access from detached evidence, and an architecture test asserts the retained binding is present |
| A4a holds the project lock as a bare advisory-lock primitive with no lease semantics, and reclaims probe survivors only at its own bind time | A4a bootstrap contract | A5 | Compose the universal recovery-resolve lease over `HeldProjectLock` — acquire at entry, resolve the active transaction, hold across the entire write phase — and invoke probe-survivor reclamation at every lease entry |

### 3.3 Delivery obligations that are not ledger entries

Two consequences of this design are *fail-closed*, so they admit no shape and belong in this document
rather than in the ledger:

- **`CERTIFIED_ALLOWLIST` ships empty.** Production binding therefore refuses every volume until A8
  crash-certifies a configuration and populates it. Nothing unsafe is admitted; the engine simply does
  not run in production yet.
- **No macOS backend exists.** `select_backend` refuses explicitly on any platform but Linux, so the
  protocol's admission of a macOS implementation creates no reachable state.

Both are tracked as delivery obligations in §12.

## 4. Architecture and ownership

### 4.1 Module layout

```text
python/src/atoms/fs/
├── py.typed
├── __init__.py            # public surface only
├── syscalls/
│   ├── __init__.py
│   └── linux.py           # ctypes wrappers; no policy, no retained state
├── backend.py             # Backend protocol
├── linux.py               # LinuxBackend
├── platform.py            # select_backend()
├── volume.py              # VolumeConfiguration, StorageProfile, DurabilityAllowlist,
│                          # CERTIFIED_ALLOWLIST, mountinfo/fdinfo resolution
├── lock.py                # HeldProjectLock, acquire_project_lock
├── bootstrap.py           # metadata layout, reclaim_probe_survivors
├── probe.py               # probe_backend, SQLite-WAL certification
└── binding.py             # VolumeEvidence, ProjectBinding, bind_project_volume
```

Public surface exported from `atoms/fs/__init__.py`:

```text
select_backend, Backend, Capability (re-exported from atoms.core.capabilities)
acquire_project_lock, HeldProjectLock
VolumeConfiguration, StorageProfile, DurabilityAllowlist, AllowlistEntry, CERTIFIED_ALLOWLIST
bind_project_volume, ProjectBinding, VolumeEvidence
reclaim_probe_survivors
```

### 4.2 Dependency direction and the purity boundary

`atoms.fs` depends on `atoms.core`; `atoms.core` never depends on `atoms.fs`. A4a reuses
`atoms.core.capabilities.Capability` and `atoms.core.errors` and introduces no new error type.

The existing purity guard covers only `atoms/core/recovery/`. A4a extends it to walk every module below
`atoms/core` with `rglob("*.py")` — not by importing the package, which would miss an unimported future
submodule — and forbids `ctypes`, `datetime`, `os`, `pathlib`, `random`, `secrets`, `sqlite3`,
`subprocess`, `time`, and `atoms.fs`. The guard passes against `atoms/core` as it stands today; adding it
now fixes the boundary at exactly the commit where impure code first appears.

## 5. Platform primitives

### 5.1 The `Backend` protocol

The protocol's surface is determined by what must be *probed*, not by anticipation of later stages. §5.5
requires empirical probing of `atomic_exchange`, `noclobber_transfer`, and `identity_anchor`, and the
remaining five capabilities can only be reported as supplied by being exercised. The protocol therefore
carries exactly one operation set per §5.5 capability, and every method is called by the probe that
reports it. There are no speculative methods.

```python
class Backend(Protocol):
    # anchored_traversal
    def open_root(self, path: str) -> int: ...                            # establishes the anchor
    def open_child_directory(self, parent_fd: int, name: str) -> int: ... # guarded traversal
    # atomic_exchange
    def exchange(self, parent_fd: int, left: str, right: str) -> None: ...
    # noclobber_transfer
    def transfer_noclobber(self, src_fd: int, src: str, dst_fd: int, dst: str) -> None: ...
    # identity_anchor
    def link_anchor(self, src_fd: int, src: str, dst_fd: int, dst: str) -> None: ...
    # durable_publish
    def flush_file(self, fd: int) -> None: ...
    def flush_directory(self, fd: int) -> None: ...
    # nofollow_coherent_read
    def open_regular_nofollow(self, parent_fd: int, name: str) -> int: ...
    # symlink_fingerprint
    def symlink_fingerprint(self, parent_fd: int, name: str) -> tuple[os.stat_result, str]: ...
    # advisory_project_lock
    def lock_exclusive(self, fd: int) -> None: ...
    def try_lock_exclusive(self, fd: int) -> bool: ...
```

The two traversal operations differ in kind. `open_root` *establishes* the anchor and cannot itself be
anchored — there is no prior descriptor to resolve beneath — so it is a plain `O_DIRECTORY | O_NOFOLLOW`
open of a caller-supplied path. `open_child_directory` is the guarded traversal proper, resolving a single
component beneath a retained descriptor and refusing symlinks and mount crossings. Only the second is
what the `anchored_traversal` probe exercises.

`symlink_fingerprint` returns an `lstat` result and a `readlink` target. It is **`lstat`-coherent, never
descriptor-coherent**: the fingerprint and any subsequent transfer are separate syscalls, so a symlink's
identity contract is the atomically transferred entry validated against the frozen fingerprint (§6 of the
authority), never an open descriptor. The name states this so no caller mistakes it for a coherent read.

`try_lock_exclusive` exists because a successful `lock_exclusive` proves acquisition, not exclusion. The
probe must contend against an independently opened descriptor, and must do so without risking a deadlock.

There is no `supplied_capabilities()` method. A backend that reported its own capabilities would make the
evidence circular — the probe exists precisely to establish what the backend reporting cannot be trusted
to assert. `probe_backend` is the sole producer of the capability set.

`Protocol` rather than a base class keeps this composition-only: the test backend implements it
structurally, with no inheritance and no registration.

Operations that are POSIX-portable and are *not* §5.5 capabilities — `mkdirat`, `unlinkat`, plain
directory `openat` — are called through `os` with `dir_fd` directly and are not abstracted.

### 5.2 `ctypes` binding strategy

Most of what A4a needs is stdlib: `os.open` with `O_NOFOLLOW`, `os.link`/`os.stat`/`os.fsync` with
`dir_fd`, `fcntl.flock`, and — for the eventual macOS backend — `F_FULLFSYNC` through `fcntl.fcntl`. Only
two calls need binding on Linux.

**`openat2`** has no glibc wrapper. There is no useful symbol-first branch; it is invoked through
`libc.syscall` with an explicit architecture table, and the `open_how` struct layout is declared as a
`ctypes.Structure` verified against kernel headers during implementation.

**`renameat2`** has had a glibc wrapper since glibc 2.28, so it is resolved symbol-first with a raw
`libc.syscall` fallback using the same architecture table.

The architecture table covers `x86_64` and `aarch64`. An unlisted architecture refuses explicitly through
`select_backend` rather than guessing a syscall number.

Kernel absence is detected **operationally, by `ENOSYS` from the call itself**, never by parsing a
reported kernel version. A kernel without `openat2` therefore surfaces as `anchored_traversal` being
unavailable, which refuses binding under §7.1; a kernel without `renameat2` surfaces as
`atomic_exchange` and `noclobber_transfer` absent, which is reported and not refused.

Every wrapper raises `OSError` with the true `errno` and performs no policy, no retries, and no
translation. Triage happens at the probe boundary (§10).

### 5.3 Platform selection

`select_backend()` returns a `Backend` or raises `CapabilityUnavailable`. It refuses any platform other
than Linux and any architecture outside the table. It performs no I/O, so it is called before the lock is
acquired and before any descriptor is opened.

## 6. Volume identity and durability configuration

### 6.1 Mount identity

`st_dev` alone is insufficient. Bind mounts can share a device while having distinct mount IDs and
distinct per-mount options, so keying `mountinfo` by device is ambiguous.

A4a resolves the **mount ID of each held descriptor** from `/proc/self/fdinfo/<fd>` and matches that exact
ID against `/proc/self/mountinfo`. The project root and the metadata root must agree on **both** mount ID
and `st_dev`. This is descriptor-bound — it describes the mount the engine actually holds open, not a path
that could be re-resolved differently — and needs no additional syscall wrapper.

The same-volume requirement is not an A4a invention. The authority requires `metadata_root` to resolve
onto the same volume as every effect path, because that is what lets blob promotion and staging
publication reach live targets by atomic hard-link and rename. A4a proves it for the two roots it holds;
A4b proves it for effect paths, which `anchored_traversal` already confines by refusing mount crossings.

Failure to resolve a mount ID, or a mismatch between the roots, refuses with `CapabilityUnavailable`.

### 6.2 `VolumeConfiguration`

```python
@dataclass(frozen=True, slots=True)
class VolumeConfiguration:
    backend_id: str                       # "linux"
    filesystem_type: str                  # from mountinfo
    barrier_options: tuple[str, ...]      # sorted; see below
```

`barrier_options` is **not** the raw mount option list. Recording every option would make the tuple
over-specific — an unrelated `noatime` difference would break a match that should hold. A4a ships an
explicit per-filesystem table of the option names that bear on barrier and `fsync` behavior, and records
only those, normalized and sorted.

A4a ships that table for `ext4`, `xfs`, and `btrfs`. **Any other filesystem type refuses with
`CapabilityUnavailable`**, because the engine has no basis for deciding which of its options bear on
durability. This is what makes §5.5's explicit refusals — tmpfs, network filesystems — fall out
structurally rather than needing a denylist.

The authority also mentions superblock features "where they matter". No certified entry depends on one
yet, so A4a resolves none. Following the same rule adopted for storage assumptions: a runtime-observable
fact is added alongside the first certified entry that depends on it, never speculatively.

### 6.3 `StorageProfile`

```python
@dataclass(frozen=True, slots=True)
class StorageProfile:
    profile_id: str
```

The tuple's other components are runtime-resolvable; this one is not. No syscall can establish whether
device firmware honors a cache flush, which is exactly what crash certification tests. The following
rules are therefore binding:

- `StorageProfile` is immutable and matched by exact equality only.
- Its fields are **declarations, never runtime observations**.
- It is supplied by the trusted composition root as a required, keyword-only argument with no default.
- `VolumeEvidence` surfaces it as `declared_storage_profile`.
- A matched allowlist entry certifies the **complete combination** of runtime-resolved configuration and
  declared profile.
- A4a makes no independent claim that the declaration is true.
- Test bindings pass an explicit test profile inside their injected singleton allowlist.
- If A8 discovers a runtime-observable fact that must constrain certification, that fact is added
  alongside the first certified entry that depends on it — not speculatively here.

A4a deliberately does **not** inspect `/sys/block`. `rotational` describes media classification, not
durability; `write_cache` reports a writable kernel view that can suppress flushes without changing
hardware state; and a dm/md topology describes routing, not a durability verdict. Recording any of them
would manufacture the false durability signal the configuration tuple exists to prevent.

### 6.4 `DurabilityAllowlist`

```python
@dataclass(frozen=True, slots=True)
class AllowlistEntry:
    configuration: VolumeConfiguration
    storage: StorageProfile
    certification_ref: str          # identifies the crash-test record that admitted this entry

@dataclass(frozen=True, slots=True)
class DurabilityAllowlist:
    entries: frozenset[AllowlistEntry]

    def match(self, configuration: VolumeConfiguration,
              storage: StorageProfile) -> AllowlistEntry | None: ...

CERTIFIED_ALLOWLIST = DurabilityAllowlist(entries=frozenset())
```

`ProjectBinding` proves membership in the allowlist supplied by the trusted composition root. The
following rules make that deliberate assumption visible without weakening the binding or creating a
second proof type:

- `DurabilityAllowlist` is immutable.
- The `allowlist` parameter of `bind_project_volume` is **required and keyword-only, with no default**.
- Production composition passes exactly `CERTIFIED_ALLOWLIST`, enforced by an architecture test.
- Tests may inject a singleton allowlist containing the resolved test tuple and an explicit test profile.
- `VolumeEvidence` retains the resolved configuration and the matched entry for diagnostics.
- A4b trusts the factory-issued binding and performs **no second durability check**.
- A8 populates `CERTIFIED_ALLOWLIST`. While it is empty, production binding refuses.

There is no override, no flag, and no uncertified binding state. An empty production allowlist is
fail-closed.

## 7. Bootstrap

The authority requires an idempotent bootstrap phase preceding capability approval, because the probe
cannot certify a volume without writing to it. That bootstrap touches only the engine-owned
`metadata_root` — never a project path and never a transaction record.

### 7.1 `HeldProjectLock`

```python
backend = select_backend()

with acquire_project_lock(backend, metadata_root) as lock:
    ...
```

`acquire_project_lock` creates `metadata_root` if absent, opens `lock`, and takes an exclusive advisory
lock through the backend. The returned `HeldProjectLock` **retains the metadata-root descriptor and the
exact backend**, which is why `bind_project_volume` takes no separate `metadata_root` argument: a lock
acquired for one root cannot be paired with a different root.

The lock is caller-owned and outlives the binding. That is what lets A5's recovery-resolve lease span
resolution plus an entire write phase across more than one binding.

Backend selection precedes lock acquisition because acquiring the lock is itself backend work.

`flock` locks are associated with the open file description and persist until every duplicated descriptor
for that description is closed. (It is `fcntl` record locks that drop when any descriptor for the file is
closed by the process.) `HeldProjectLock` is nevertheless an explicit lifetime proof: operations that
require the lock take it as a parameter, so the requirement is checked by the type system rather than
assumed.

If `advisory_project_lock` is unavailable on the volume, `acquire_project_lock` refuses with
`CapabilityUnavailable`. Nothing downstream is safe without it.

### 7.2 Metadata layout

Under the held metadata-root descriptor, A4a ensures `probe/`, `staging/`, `work/`, and `blobs/sha256/`
exist, using `mkdirat` relative to retained descriptors. It creates no database and defines no schema.

### 7.3 Probe-survivor reclamation

```python
def reclaim_probe_survivors(lock: HeldProjectLock) -> None: ...
```

Reclamation takes the `HeldProjectLock` to force the proof at the type level. It walks `probe/` through
anchored `unlinkat`/`rmdir` relative to retained descriptors, **never following symlinks**, and is
**unconditional** — it does not attempt to distinguish a live probe from debris, because under the held
lock there can be no live probe but its own.

A kill during probing therefore leaves only attributable, mutation-free debris inside a reserved
engine-owned namespace, discarded before any transaction record exists.

Reclamation runs at A4a bind time and, per §3.2, at every A5 lease entry.

## 8. Capability probing

### 8.1 Functional availability, not durability

```python
def probe_backend(backend: Backend, probe_root_fd: int,
                  lock: HeldProjectLock) -> frozenset[Capability]: ...
```

These are **functional probes**, not conformance tests. They establish that an operation is present and
behaves correctly on this volume right now. They cannot establish its power-loss guarantee. In
particular, successfully flushing a file and its parent directory is **availability evidence only**; the
crash claim is carried solely by the matched allowlist entry, never by a probe result.

`probe_backend` is the sole producer of the capability set frozen into `VolumeEvidence`.

### 8.2 Per-capability probes

Each probe runs entirely inside `probe/`, relative to retained descriptors. The one exception is
`advisory_project_lock`, which must contend against the *existing* `lock` file in `metadata_root`; it
creates nothing and leaves no survivor.

| Capability | Probe |
| --- | --- |
| `anchored_traversal` | Open `probe/` with `RESOLVE_BENEATH \| RESOLVE_NO_SYMLINKS \| RESOLVE_NO_XDEV`. Plant a symlink pointing at `..` and require the open to refuse; require a `..` component to refuse. Both the success and the refusals must hold. |
| `advisory_project_lock` | Open a second, independent descriptor to `lock` and require `try_lock_exclusive` to return `False` against the already-held lock. Because `flock` is per-open-file-description, this contends correctly within one process. |
| `atomic_exchange` | Create two files with distinct content, exchange them, verify both names now resolve to the swapped content. |
| `noclobber_transfer` | Create a source and an existing destination; require the transfer to fail with `EEXIST`; remove the destination; require the transfer to succeed. |
| `identity_anchor` | Create a file, link it to a second name, and require equal `st_dev`/`st_ino` and `st_nlink == 2`. |
| `durable_publish` | Flush a file descriptor and its parent directory descriptor. Availability only. |
| `nofollow_coherent_read` | Open a regular file `O_RDONLY \| O_NOFOLLOW`, `fstat` and read from that one descriptor; then require the same open against a symlink leaf to fail with `ELOOP`. |
| `symlink_fingerprint` | Create a symlink, then require `lstat` to report a symlink and `readlink` to return the exact target. |

Every probe cleans up after itself; §7.3 reclamation runs again afterwards regardless.

### 8.3 SQLite-WAL hostability

Opening a database and selecting WAL mode does not prove the required shared-memory path, because WAL can
operate without shared memory when SQLite runs in exclusive locking mode. The probe therefore keeps
**normal** locking mode and:

- requires `PRAGMA journal_mode=WAL` to return `wal`;
- performs and commits a write — `PRAGMA user_version=1` — avoiding any schema;
- keeps the first connection open;
- uses an independent second connection to observe the committed value and exercise locking;
- removes the database, `-wal`, and `-shm` names through anchored probe cleanup.

This is the one place A4a imports `sqlite3`. It opens a throwaway database inside `probe/`, defines no
schema, and owns no store; A5 owns the real one. Without this A4a cannot discharge §5.5's requirement
that the probe certify the volume can host the metadata store.

A volume that cannot host SQLite-WAL refuses with `CapabilityUnavailable` (see §9.1 for why this is
refused while ordinary capability absence is only reported).

## 9. Binding

### 9.1 Composition sequence

```python
backend = select_backend()

with acquire_project_lock(backend, metadata_root) as lock:
    with bind_project_volume(project_root, lock,
                             allowlist=CERTIFIED_ALLOWLIST,
                             storage=STORAGE_PROFILE) as binding:
        ...
```

Inside `bind_project_volume`, in this order:

1. Open the project root anchored; retain the descriptor. (The metadata root is already held by `lock`.)
2. Resolve each held descriptor's mount ID from `/proc/self/fdinfo/<fd>`, match into `mountinfo`, and
   require both roots to agree on mount ID and `st_dev`. Retain the metadata root's `st_dev`/`st_ino`
   for A4b's exclusion check.
3. Resolve `VolumeConfiguration` from the matched `mountinfo` entry.
4. **Reclaim any pre-existing `probe/`**, without creating it if absent.
5. Match `(configuration, storage)` against the supplied allowlist; refuse on no match.
6. Ensure the full metadata layout.
7. Probe capabilities, then certify SQLite-WAL hostability.
8. Reclaim again, leaving `probe/` empty.
9. Build `VolumeEvidence`; return `ProjectBinding`.

The order is load-bearing in two places.

**Reclamation precedes the allowlist refusal (step 4 before step 5).** If refusal came first, a
configuration that was removed from the allowlist could never have its old probe debris reclaimed. The
authority makes reclamation unconditional. Because step 4 does not *create* `probe/`, an unknown
configuration still receives no new probe writes — pre-existing attributable debris is removed, and
nothing further is written.

**Steps 1–5 are read-only beyond the bootstrap §5.5 explicitly permits**, so a non-allowlisted volume is
refused before the probe writes anything.

Two always-required capabilities are bootstrap prerequisites rather than reported evidence:
`anchored_traversal`, without which no root or probe namespace can be opened and retained, and
`advisory_project_lock`, without which reclamation and probing are unsafe. Failure of either refuses in
A4a. This does not violate progressive capability support — they are preconditions for producing evidence
at all, and every transaction requires them.

`durable_publish` remains evidence-only, and A4a explicitly claims no durability for its own bootstrap
writes. A4b refuses if a specification requires it and it is absent.

SQLite-WAL hostability is refused by A4a rather than reported, because it sits outside the §5.5 capability
vocabulary that A2 derives requirements over, is unconditional for every transaction, and leaves A4b no
decision to make. Ordinary capability absence is reported precisely so that exactly one place — A4b —
adjudicates "required ⊆ supplied".

### 9.2 `VolumeEvidence`

```python
@dataclass(frozen=True, slots=True, init=False)
class VolumeEvidence:
    configuration: VolumeConfiguration
    declared_storage_profile: StorageProfile
    matched_entry: AllowlistEntry
    supplied_capabilities: frozenset[Capability]
    metadata_root_device: int
    metadata_root_inode: int
```

Frozen and factory-token-guarded exactly like `CompiledSpec`: direct construction and
`dataclasses.replace` both refuse. `bind_project_volume` is its sole construction authority.

`configuration` and `declared_storage_profile` are necessarily equal to `matched_entry.configuration` and
`matched_entry.storage`, since `match` returns an entry only on exact equality of both. The redundancy is
deliberate — it keeps what was *resolved* and what was *certified* separately legible in a diagnostic —
and a test asserts the invariant so the pair cannot drift. `backend_id` is not repeated here; it lives on
`configuration`.

`VolumeEvidence` is **diagnostic only**. It describes a volume; it authorizes no access to one. It
outlives the binding so a halt diagnostic can record what was proved, but it can never be used to reach
the filesystem. That constraint is what §3.2's first new ledger entry obliges A4b to honor.

### 9.3 `ProjectBinding` lifetime

`ProjectBinding` carries the same factory-token guard but is **not frozen**, because it owns descriptors
and a spent flag. That difference is intentional, not an oversight: `VolumeEvidence` is a value and
`ProjectBinding` is a resource.

It is a context manager. Exit closes its retained descriptors and marks it spent; any subsequent use
raises `ProtocolError`. Its `VolumeEvidence` remains readable afterwards.

It does not own the lock. `HeldProjectLock` is caller-supplied and outlives it.

## 10. Error contract

`CapabilityUnavailable` is raised for: an unsupported platform or architecture; a filesystem type outside
the barrier-option table; an unresolvable mount identity; a mount-identity or `st_dev` mismatch between
the roots; a configuration and profile pair absent from the supplied allowlist; unavailable
`anchored_traversal` or `advisory_project_lock`; and a volume that cannot host SQLite-WAL.

`ProtocolError` is raised for internal contract violations — using a spent binding, or any state A4a's own
construction should have made impossible.

`OSError` **propagates**. A4a does not blanket-convert it. At each probe, only the documented
operation-specific errno values that conclusively mean *unsupported* conclude that a capability is absent;
everything else propagates unchanged. `EBADF`, `EMFILE`, `EFAULT`, malformed arguments, and implementation
faults are bugs or environmental failures, and turning them into `CapabilityUnavailable` would report a
durable volume as capability-poor.

The unsupported-errno sets are declared per operation, for example `ENOSYS`, `EINVAL`, and
`EOPNOTSUPP`/`ENOTSUP` for `renameat2`-backed operations, and `EPERM`/`EOPNOTSUPP` for `link`. The exact
sets are fixed in the plan against the manual pages, and each is exercised by a test.

## 11. Verification

### 11.1 Tier 1 — pure, no filesystem

`mountinfo` and `fdinfo` parsing against captured fixture text, covering the cases a naive parser gets
wrong: octal-escaped mount points (`\040` for a space), the variable-length optional fields terminated by
`-`, and two bind mounts sharing `st_dev` with distinct mount IDs. Barrier-option normalization per
filesystem type, and refusal of an unlisted type. Allowlist matching, including a near-miss differing only
in `declared_storage_profile`. Immutability and exact-match semantics of `VolumeConfiguration`,
`StorageProfile`, and `DurabilityAllowlist`. Factory-guard refusals on `VolumeEvidence` and
`ProjectBinding`, including `dataclasses.replace`.

### 11.2 Tier 2 — fake backend

A capability-restricted test backend implementing `Backend` structurally drives `probe_backend` across
capability subsets, asserting the reported set is exactly the supplied one. The refusal matrix is
exhaustive here: absent `anchored_traversal` or `advisory_project_lock` refuses binding; any optional
capability absent binds successfully with that capability reported missing. The fake backend also forces
`ENOSYS` from `openat2` and `renameat2`, which a current kernel will not produce naturally, and forces
each non-unsupported errno to confirm it propagates as `OSError` rather than becoming
`CapabilityUnavailable`.

### 11.3 Tier 3 — real filesystem

A conftest fixture resolves the test root from `ATOMS_TEST_VOLUME`, defaulting to a directory on the
repository's own ext4 filesystem, because the default `/tmp` is tmpfs and refuses by design. That default
location is added to `.gitignore` in the same commit as the fixture.

Real `flock` contention through a subprocess, in both directions. Real exchange, no-clobber transfer, and
link probes. Real two-connection SQLite-WAL certification. Cross-volume refusal by pointing the metadata
root at `/tmp` and the project root at the test volume — which exercises the mount-identity proof with no
privileges at all.

Reclamation with planted debris: a file, a nested directory, and a symlink pointing *outside* `probe/`,
asserting the debris is removed and the symlink's target survives.

Two ordering locks, both encoding decisions that would otherwise regress silently:

- planting debris and binding with an **empty** allowlist raises `CapabilityUnavailable` **and** leaves
  `probe/` empty;
- a binding that has exited raises `ProtocolError` on use while its `VolumeEvidence` stays readable.

Bind-mount tests require `unshare --mount --map-root-user` and skip when it is unavailable; the
`st_dev`-sharing case is covered unconditionally in Tier 1 against fixture data.

### 11.4 Tier 4 — architecture and packaging

The purity guard extended to `rglob("*.py")` over all of `atoms/core`, forbidding `atoms.fs` and the
impure modules. An assertion that production composition passes exactly `CERTIFIED_ALLOWLIST`. An
assertion that `CERTIFIED_ALLOWLIST` is empty, so its future population is a deliberate act. Packaging
metadata and tests updated so the wheel declares `atoms.fs` and ships its `py.typed`.

## 12. Deferred and delivery obligations

**Ledger entries created** (§3.2): detached `VolumeEvidence` must not authorize access, owned by A4b; the
lock primitive needs lease semantics and reclamation at lease entry, owned by A5.

**Ledger correction** (§3.1): entry #6 moves from A4 to A4b.

**Delivery obligations**, fail-closed and therefore not ledger admissions:

- `CERTIFIED_ALLOWLIST` ships empty; A8 populates it from block-device/VM crash certification, and until
  then production binding refuses every volume.
- No macOS backend exists; `select_backend` refuses off Linux. A later sub-plan implements the protocol
  against `openat` + `O_NOFOLLOW_ANY`, `renamex_np`/`renameatx_np`, and `F_FULLFSYNC`, satisfying the same
  probe suite.
- No superblock-feature or storage-observation resolution exists. Either is added alongside the first
  certified entry that depends on it.

## 13. Acceptance criteria

1. `atoms/fs/` exists with the §4.1 layout; `atoms/core/` remains pure and the extended guard proves it.
2. `select_backend` refuses unsupported platforms and architectures without performing I/O.
3. `openat2` and `renameat2` are bound per §5.2, with `ENOSYS` detected operationally.
4. The `Backend` protocol carries exactly one operation set per §5.5 capability and no
   `supplied_capabilities` method; every method is exercised by the probe that reports it.
5. Mount identity is proved from held descriptors' mount IDs plus `st_dev`, and the roots must agree.
6. An unlisted filesystem type refuses; `ext4`, `xfs`, and `btrfs` barrier options normalize per the
   table.
7. `StorageProfile` is declaration-only, required, keyword-only, exact-match, and surfaced as
   `declared_storage_profile`.
8. The `allowlist` parameter is required and keyword-only; `CERTIFIED_ALLOWLIST` is empty; there is no
   override and no uncertified binding state.
9. Bootstrap creates only engine-owned state under `metadata_root`, and reclamation is unconditional,
   symlink-safe, and takes `HeldProjectLock`.
10. Reclamation of a pre-existing `probe/` precedes allowlist refusal, and refusal writes nothing new.
11. Absent `anchored_traversal` or `advisory_project_lock` refuses; absent optional capabilities are
    reported; absent SQLite-WAL hostability refuses.
12. `VolumeEvidence` is frozen and factory-guarded; `ProjectBinding` is factory-guarded, spent on exit,
    and raises `ProtocolError` afterwards.
13. `OSError` propagates except for documented per-operation unsupported errno values.
14. All four verification tiers pass; Ruff and Pyright are clean; the ledger and `AGENTS.md` are updated
    in the same commit.
