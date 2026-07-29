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
| `bind_project_volume` takes its durability allowlist as a required parameter, and A4a has no production composition root at which to assert which allowlist is passed | A4a binding contract | A5 | Every production call passes exactly `CERTIFIED_ALLOWLIST`, asserted by an architecture test at the composition root; A4a can prove only that the parameter is required and keyword-only, that the constant is empty, and that no production caller exists yet |

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

The existing purity guard covers only `atoms/core/recovery/` and works by denylist. A4a replaces it with
an **allowlist**, walking every module below `atoms/core` with `rglob("*.py")` — not by importing the
package, which would miss an unimported future submodule.

A denylist is the wrong shape for a standing boundary: it only ever forbids what someone thought to
enumerate, and the obvious omissions here (`fcntl`, `platform`, `shutil`, `tempfile`) are all modules the
new layer actually uses. An allowlist fails closed against every module nobody anticipated.

`atoms/core` may import `atoms.core.*` and exactly these top-level modules, which are the ones it uses
today:

```text
__future__, collections.abc, dataclasses, enum, functools, itertools, json, re, typing, unicodedata
```

Anything else — including `atoms.fs` — fails the guard. Widening the list is a deliberate, reviewable
edit. The guard passes against `atoms/core` as it stands, so adding it now fixes the boundary at exactly
the commit where impure code first appears.

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

The two traversal operations differ in kind, and root establishment is specified in §5.4 rather than left
to the implementation. `open_root` *establishes* the anchor, so it has no prior descriptor to resolve
beneath; but it is emphatically **not** a plain `O_NOFOLLOW` open, which guards only the final component
and would follow every ancestor symlink. `open_child_directory` is guarded traversal proper, resolving a
single component beneath a retained descriptor.

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

### 5.4 Root establishment

`O_NOFOLLOW` guards only the final component; every ancestor is still followed. Establishing a root with
it would therefore admit exactly the pre-existing ancestor symlink the authority requires bootstrap to
catch, so `open_root` is defined here rather than left to the implementation.

**Existing root.** `openat2` over the complete caller-supplied path with `RESOLVE_NO_SYMLINKS`, so no
component — ancestor or leaf — may be a symlink. `RESOLVE_BENEATH` does not apply: there is no prior
anchor to resolve beneath. `RESOLVE_NO_XDEV` is deliberately **not** set, because the target mount has
not been established yet and refusing a crossing here would refuse the legitimate case of a root that
simply lives on its own mount. Mount identity is proved afterwards, from the resulting descriptor
(§6.1). The descriptor is opened `O_DIRECTORY | O_CLOEXEC` and `fstat`-verified to be a directory.

**Missing `metadata_root`.** Only the final leaf is created, and only relative to a descriptor:

1. Guarded-open the parent with the same `RESOLVE_NO_SYMLINKS` walk. A missing parent **refuses** —
   A4a creates no intermediate directories, because a guarded component-by-component walk that also
   creates as it goes has races this layer has no need to take on.
2. `mkdirat` the single leaf name relative to that parent descriptor.
3. Reopen the leaf with `open_child_directory` from the retained parent descriptor and `fstat`-verify it
   is a directory.

Step 3 is not redundant. It is what makes the created root a *guard-checked* descriptor on the same terms
as the existing-root case, rather than one trusted because this process just created it.

Root establishment is exercised by the bootstrap itself on every bind, and its ancestor-symlink refusal is
proved by a dedicated test that constructs a path through a symlinked ancestor (§11.3) — not by the
runtime probe, which has no reason to plant a symlink above a live project root.

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
    backend_id: str                        # "linux"
    backend_revision: str                  # Atoms backend contract revision, e.g. "linux-1"
    kernel_identifier: str                 # exact os.uname().release, e.g. "7.1.5-arch1-1"
    filesystem_type: str                   # from mountinfo
    barrier_options: tuple[str, ...]       # normalized and sorted; see the tables below
    durability_features: tuple[str, ...]   # canonical, sorted; empty until a resolver exists
```

**Implementation discriminators.** `backend_id` and `filesystem_type` alone would let one crash-tested
Linux/ext4 build certify every Linux/ext4 implementation ever compiled, which is precisely the over-broad
claim the tuple design exists to prevent. Two fields close that, and both are matched exactly.

`kernel_identifier` is the **complete** `os.uname().release`, not a truncation. Truncating to a major.minor
line would silently certify every future kernel in that line from a single crash test — the same
over-broad claim in a smaller form. A certified entry names the kernel that was actually tested.

`backend_revision` versions the Atoms backend implementation itself, which `backend_id="linux"` does not.
A crash test certifies a *pair*: a volume configuration and the backend code that issued the syscalls
against it. Changing the backend's syscall selection, flag set, or durability ordering invalidates that
evidence, so the revision is a deliberately bumped constant, and bumping it de-certifies every entry
naming the old one — which is the intended effect, not a hazard.

The consequence is that certification is narrow by default: an entry certifies one kernel and one backend
revision. **Widening to a family must be explicit** — A8 either enumerates additional entries or
introduces an explicit family field carrying its own certification record. Widening is never obtained by
truncating a string.

**`barrier_options` is not the raw mount option list.** Recording every option would make the tuple
over-specific — an unrelated `noatime` difference would break a match that should hold. A4a records only
the options that bear on barrier and `fsync` behavior, each **normalized to an explicit value even when
absent**, so a tuple never depends on whether an option happened to be spelled out:

`mountinfo` carries **two distinct option fields**, and conflating them is a correctness bug, not a
formatting detail. Field 6 holds the per-mount options (the VFS flags: `ro`/`rw`, `sync`, `dirsync`,
`noatime`), while field 11 — after the `-` separator, filesystem type, and source — holds the
**superblock** options, which is where every filesystem-specific durability value lives. Reading only
field 6 would find no `data=` at all and normalize an explicitly mounted `data=writeback` to its safe
`data=ordered` default, certifying a volume as durable on evidence it never supplied.

| Filesystem | Option | `mountinfo` field | Normalized when absent |
| --- | --- | --- | --- |
| `ext4` | `barrier` | 11 (super) | `barrier=1` |
| `ext4` | `data` | 11 (super) | `data=ordered` |
| `ext4` | `journal_async_commit` | 11 (super) | absent (off) |
| `ext4` | `commit` | 11 (super) | `commit=5` |
| `ext4` | `sync` | 6 (per-mount) | `async` |
| `ext4` | `dirsync` | 6 (per-mount) | absent (off) |
| `xfs` | `barrier` | 11 (super) | `barrier=1` — the option was removed in Linux 4.19 and barriers are unconditional since, which normalizes to the same value |
| `xfs` | `wsync` | 11 (super) | absent (off) |
| `xfs` | `sync` | 6 (per-mount) | `async` |
| `btrfs` | `barrier` | 11 (super) | `barrier=1` |
| `btrfs` | `flushoncommit` | 11 (super) | `noflushoncommit` |
| `btrfs` | `commit` | 11 (super) | `commit=30` |
| `btrfs` | `notreelog` | 11 (super) | absent (tree-log on) |

Each option name, the exact field it is read from, and its absent-default are fixed by this table. The
normalizer reads each option **only** from its stated field; a value found in the other field is not a
substitute. Tier 1 fixtures include, for each filesystem, a mount whose non-default durability value
appears **only** in the super-options field — the case a field-6-only parser silently normalizes away.

The plan verifies every default against `mount(8)` and the per-filesystem kernel documentation before
implementing the normalizer; a correction there is a plan-level fix to a stated default, not a licence to
invent policy.

**Any other filesystem type refuses with `CapabilityUnavailable`**, because the engine has no basis for
deciding which of its options bear on durability. This is what makes §5.5's explicit refusals — tmpfs,
network filesystems — fall out structurally rather than needing a denylist.

**`durability_features`** carries the authority's superblock features. The field exists now, with a
canonical sorted representation, because A4b and A5 consume `VolumeConfiguration` long before A8
populates the allowlist, and adding a required field to a frozen exact-match dataclass later would break
both. It is resolved by a per-filesystem resolver and is currently **empty for every filesystem**, since
none of the three resolvers is implemented — reading ext4 superblock features needs access A4a cannot
assume it has. The binding rule is: **a certified allowlist entry may not reference a feature whose
resolver does not exist.** A8 implements the resolver alongside the first entry that depends on it. An
empty tuple is therefore honest — it says "no feature is being claimed" — rather than a stand-in for
unresolved data.

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
- Production composition passes exactly `CERTIFIED_ALLOWLIST`. A4a cannot assert this — it has no
  production composition root — so the call-site assertion is ledger entry #18, owned by A5. A4a proves
  only that the parameter is required and keyword-only, that the constant is empty, and that no production
  caller exists yet (§11.4).
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

`acquire_project_lock` establishes `metadata_root` per §5.4, opens `lock`, and takes an exclusive advisory
lock through the backend. The returned `HeldProjectLock` **retains the metadata-root descriptor, its
verified pathname, and the exact backend**, which is why `bind_project_volume` takes no separate
`metadata_root` argument: a lock acquired for one root cannot be paired with a different root.

```python
class HeldProjectLock:
    @property
    def backend(self) -> Backend: ...              # requires held
    @property
    def metadata_root_fd(self) -> int: ...         # borrowed; requires held
    @property
    def metadata_root_path(self) -> str: ...       # verified, normalized; requires held
    @property
    def held(self) -> bool: ...                    # always readable
    def __enter__(self) -> HeldProjectLock: ...
    def __exit__(self, *exc) -> None: ...          # releases; idempotent
```

`HeldProjectLock` is **factory-controlled** with the same construction token as `VolumeEvidence`.
`acquire_project_lock` is its sole construction authority. This matters because every downstream signature
that takes a `HeldProjectLock` — `reclaim_probe_survivors`, `probe_backend`, `bind_project_volume` —
treats the *type* as proof that a real lock is held. An ordinarily constructible class would let that
proof be fabricated with a bare object.

Descriptors are exposed only through properties that check `held` and raise `ProtocolError` otherwise —
never as public integer attributes, which would stay readable after release and quietly contradict the
guard. `__exit__` releases the advisory lock and closes the `lock` and metadata-root descriptors, in that
order, and is idempotent: a second exit is a no-op, not an error. An exceptional exit still releases and
closes, then propagates.

**Opening `lock`.** The lock file is opened **relative to the held metadata-root descriptor** with
`O_CREAT | O_RDWR | O_NOFOLLOW | O_CLOEXEC`, never by path, then `fstat`-verified to be a regular file. A
symlink at that name fails the open; anything else that is not a regular file refuses with
`ProtocolError`. Taking an advisory lock on a symlink target or a device node outside `metadata_root`
would serialize nothing.

**The verified pathname.** §5.4's walk resolves the caller-supplied path with `RESOLVE_NO_SYMLINKS`, so no
component is a symlink. That is what makes the path safe to *normalize lexically*: with no symlink in it,
collapsing `.` and `..` and making it absolute against the current directory cannot change which entry it
names. The result is retained as `metadata_root_path`. It is not the caller's string — §9.4 explains why
that distinction is load-bearing for A5.

**The sync-ignore marker.** When — and only when — A4a *creates* `metadata_root`, it best-effort requests
that cross-machine sync clients ignore the directory, setting the extended attribute
`user.com.dropbox.ignored=1` on the held descriptor. The authority requires this at creation because the
metadata store is single-host by construction and a synced copy is neither required nor trusted. It is
**best-effort**: any failure, including a filesystem without extended attributes, is swallowed and
weakens no single-host guarantee. A test asserts both that the attribute is set on a fresh
`metadata_root` where the filesystem supports it, and that a `setxattr` failure does not fail the
bootstrap.

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
exist. Each component, at every level, follows one rule:

1. `mkdirat` the single component relative to the retained parent descriptor. `EEXIST` is **expected and
   tolerated** — the bootstrap is idempotent by design and normally runs against an existing store.
2. **Always reopen** the component through `open_child_directory` from that same parent descriptor, and
   retain the resulting descriptor.
3. Refuse if the reopen fails or the result is not a directory.

Step 2 runs on both paths, created and pre-existing, and that is the point. Tolerating `EEXIST` without
reopening would accept whatever already occupies the name — a symlink pointing out of the store, or a
regular file — as though A4a had created it. Because `open_child_directory` refuses symlinks and mount
crossings, an occupied name can only pass by being a real directory beneath the metadata root.

A4a creates no database and defines no schema.

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

Each probe runs entirely inside `probe/`, relative to retained descriptors. `transfer_noclobber` and
`link_anchor` accept **distinct** source and destination parent descriptors, so their probes must use two
separate directories under `probe/` — that is the form A5's blob promotion and A7's staging publication
actually depend on, and a same-directory probe would leave it unproven. The one exception to running
inside `probe/` is
`advisory_project_lock`, which must contend against the *existing* `lock` file in `metadata_root`; it
creates nothing and leaves no survivor.

| Capability | Probe |
| --- | --- |
| `anchored_traversal` | Open `probe/` with `RESOLVE_BENEATH \| RESOLVE_NO_SYMLINKS \| RESOLVE_NO_XDEV`. Plant a symlink pointing at `..` and require the open to refuse; require a `..` component to refuse. Both the success and the refusals must hold. |
| `advisory_project_lock` | Open a second, independent descriptor to `lock` and require `try_lock_exclusive` to return `False` against the already-held lock. Because `flock` is per-open-file-description, this contends correctly within one process. |
| `atomic_exchange` | Create two files with distinct content, exchange them, verify both names now resolve to the swapped content. |
| `noclobber_transfer` | **Across two distinct directories** under `probe/`: create a source in one and an existing destination in the other; require the transfer to fail with `EEXIST`; remove the destination; require the transfer to succeed. |
| `identity_anchor` | **Across two distinct directories** under `probe/`: create a file in one, link it into the other, and require equal `st_dev`/`st_ino` and `st_nlink == 2`. |
| `durable_publish` | Flush a file descriptor and its parent directory descriptor. Availability only. |
| `nofollow_coherent_read` | Open a regular file `O_RDONLY \| O_NOFOLLOW`, `fstat` and read from that one descriptor; then require the same open against a symlink leaf to fail with `ELOOP`. |
| `symlink_fingerprint` | Create a symlink, then require `lstat` to report a symlink and `readlink` to return the exact target. |

Every probe cleans up after itself; §7.3 reclamation runs again afterwards regardless.

### 8.3 SQLite-WAL hostability

Opening a database and selecting WAL mode does not prove the required shared-memory path, because WAL can
operate without shared memory when SQLite runs in exclusive locking mode. The probe therefore keeps
**normal** locking mode and:

The choreography is fixed here rather than described loosely, because "exercise reader/writer locking"
admits materially different tests with materially different evidence. Every step is required:

| # | Actor | Action | Required outcome |
| --- | --- | --- | --- |
| 1 | parent | open the database, `PRAGMA journal_mode=WAL` | returns `wal` |
| 2 | parent | `PRAGMA synchronous=FULL`, then commit `PRAGMA user_version=1` | committed |
| 3 | parent | `BEGIN IMMEDIATE`, holding the write lock; keep the connection open | acquired |
| 4 | child | open the same database, read `PRAGMA user_version` | returns `1` — a cross-process read **concurrent with a held writer**, the property the shared-memory WAL index exists to provide |
| 5 | child | `BEGIN IMMEDIATE` with `busy_timeout=0` | fails `SQLITE_BUSY` — cross-process write exclusion |
| 6 | parent | `COMMIT` | released |
| 7 | child | `BEGIN IMMEDIATE` with an explicit bounded `busy_timeout`, write `PRAGMA user_version=2`, commit, exit `0` | succeeds |
| 8 | parent | read `PRAGMA user_version` in a fresh read transaction | returns `2` |

Step 4 is the one that distinguishes this from a same-process test, and step 5 is the one that proves
locking rather than merely concurrency. The child runs under a bounded subprocess timeout in addition to
its `busy_timeout`, so a volume with broken locking fails the probe instead of hanging it. Afterwards the
database, `-wal`, and `-shm` names are removed through anchored probe cleanup.

The second reader must be a subprocess, not a second connection in this process. A same-process
connection exercises WAL but not SQLite's cross-process POSIX locking contract, and the shared-memory
WAL index exists precisely to coordinate readers across processes — which is the property §5.5 requires
certified. The child is spawned as `sys.executable -c` with a short inline reader.

This forces one documented exemption. The child cannot inherit an anchored descriptor, because SQLite
opens by path and every descriptor A4a creates is `O_CLOEXEC`; so the child receives the probe database's
path and re-resolves it. That is acceptable **only** because `probe/` is engine-owned, sits under the held
project lock, and contains no transaction state. The exemption is scoped to this one probe and extends to
nothing outside `probe/`.

This is also the one place A4a imports `sqlite3` and `subprocess`. It opens a throwaway database inside
`probe/`, defines no schema, and owns no store; A5 owns the real one. Without this A4a cannot discharge
§5.5's requirement that the probe certify the volume can host the metadata store.

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
    mount_id: int
```

The kernel release is **not** repeated here; it is matched, so it lives on `configuration` as
`kernel_identifier`.

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

```python
class ProjectBinding:
    @property
    def backend(self) -> Backend: ...            # requires active
    @property
    def project_root_fd(self) -> int: ...        # borrowed; requires active
    @property
    def metadata_root_fd(self) -> int: ...       # borrowed from the lock; requires active
    @property
    def evidence(self) -> VolumeEvidence: ...    # readable after close
    @property
    def active(self) -> bool: ...                # always readable
    def verified_metadata_path(self, name: str) -> str: ...   # see §9.4; requires active
    def __enter__(self) -> ProjectBinding: ...
    def __exit__(self, *exc) -> None: ...        # closes what it opened; idempotent
```

The rules that make this a real guard rather than a convention:

- **No public descriptor attributes.** Every descriptor is reached through a property that checks
  `active` and raises `ProtocolError` otherwise. `evidence` and `active` are the only members readable
  after exit.
- **All descriptors are borrowed.** A consumer must never close one. The binding closes exactly the
  descriptors it opened — the project root, and any directory descriptor it retained under
  `metadata_root` — in reverse order of opening. It **never** closes the lock's metadata-root descriptor,
  which the lock owns.
- **Every descriptor A4a creates is opened `O_CLOEXEC`**, so a subprocess — including the SQLite probe's
  child (§8.3) — cannot inherit and outlive them.
- **`__exit__` is idempotent**; a second exit is a no-op. An exceptional exit still closes and marks the
  binding spent before propagating.
- **The lock must outlive the binding.** Accessors additionally verify `lock.held`, raising
  `ProtocolError` if the lock was released first. Without this, `metadata_root_fd` could hand back a
  descriptor the lock had already closed — an out-of-order exit that would otherwise surface as `EBADF`
  somewhere far away.

It does not own the lock. `HeldProjectLock` is caller-supplied and outlives it.

### 9.4 Verified pathnames for the SQLite surface

Everything else in the engine is issued as a single-component operation relative to a held descriptor.
SQLite is the authority's stated exception: stdlib `sqlite3.connect()` opens by pathname and performs all
database and sidecar I/O through its own VFS, so no descriptor can participate. The authority's
replacement is *anchoring by verified identity* — resolve `metadata_root` once through guarded traversal,
record its `st_dev`/`st_ino`, confirm it is a real directory on the allowlisted volume, and open the
database by that verified path.

A4a already performs every part of that resolution. If it exposed only descriptors, A5 would have to
reconstruct the pathname from the original unbound caller string — discarding the guarantee and
re-introducing the ancestor-swap the guarded walk exists to catch. So the binding exposes it:

```python
def verified_metadata_path(self, name: str) -> str:
    """Return `<verified metadata_root>/<name>`, re-confirming the root's identity first."""
```

The operation requires an active binding, rejects any `name` that is not a single path component, and
before returning **re-confirms** that the retained metadata-root descriptor still reports the
`st_dev`/`st_ino` recorded in `VolumeEvidence`, raising `ProtocolError` on a mismatch. That is the
verify-then-open the authority requires, performed at the moment of use rather than trusted from
bootstrap.

Both consumers go through it: the §8.3 SQLite probe uses it for the database it hands its child, and A5
uses it for `atoms.db`. Neither builds a path any other way.

This does not close the authority's cooperating-process gap, and does not claim to. Mutating or replacing
`metadata_root` or an ancestor while a lease is active still voids the recovery guarantee; the project
lock serializes the cooperating processes for which this never arises, and defending against an adversary
substituting the path mid-lease requires the optional hardened VFS the authority describes.

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

Several errno values that *do* signal "unsupported" — `EINVAL` from `renameat2` for a flag the filesystem
rejects, `EPERM` from `link` on a filesystem without hard links — are ambiguous in general, because they
equally signal a malformed argument or a permission failure. What disambiguates them is not the errno but
the **probe precondition**:

> Every probe constructs its own operands, inside a directory it created, under the held project lock,
> immediately before the call. Arguments are therefore valid and permissions are guaranteed by
> construction, so neither a malformed-argument nor a permission-denied interpretation is reachable.

That precondition is what licenses an ambiguous errno to conclude "absent", and it holds only for the
probe. The same errno arriving from any other call site propagates.

Two consequences bind the plan. Each operation's unsupported-errno set is fixed against its manual page
with the specific documented sentence recorded beside it — not inferred. And each set is proved by a
**mutation test**: for every errno in it, a call made with the precondition deliberately violated
(malformed argument, wrong descriptor) must propagate as `OSError` rather than being reported as an
absent capability.

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

Tier 3 must not assume this machine's layout. A conftest fixture resolves the test root as follows, and
**skips with a precise reason** rather than failing when the environment cannot support it:

1. If `ATOMS_TEST_VOLUME` is set, use it.
2. Otherwise inspect the mount backing the repository itself. If its filesystem type is in the
   barrier-option table, use a gitignored directory there.
3. Otherwise skip, naming the resolved filesystem type and saying `ATOMS_TEST_VOLUME` is required.

The default location is added to `.gitignore` in the same commit as the fixture.

Real `flock` contention through a subprocess, in both directions. Real exchange, and real no-clobber
transfer and link probes **across distinct parent directories**. Real cross-process SQLite-WAL
certification through the §8.3 choreography, including the `SQLITE_BUSY` step. Root establishment through
a **symlinked ancestor**, asserting refusal — the §5.4 guarantee that no runtime probe covers.
Establishment of a missing `metadata_root` leaf, and refusal when its parent is missing.

The bootstrap trust boundary gets direct coverage, since each case is a name an attacker or an accident
could already occupy: a symlink planted at `lock` refuses; a non-regular file at `lock` refuses; a symlink
planted at `probe/`, `staging/`, `work/`, or `blobs/` refuses on the `EEXIST` path rather than being
adopted; a real pre-existing directory at each of those names is reopened and accepted. The sync-ignore
marker is asserted present on a freshly created `metadata_root` where the filesystem supports extended
attributes, and a forced `setxattr` failure is asserted not to fail bootstrap.

`verified_metadata_path` is exercised for its component check, its active-only guard, and its
`st_dev`/`st_ino` re-confirmation.

Cross-volume refusal likewise resolves dynamically: the fixture scans `mountinfo` for a writable mount
with a mount ID distinct from the test volume's, and skips if none exists. It does not assume `/tmp` is a
separate filesystem, which is true here but not portable.

Reclamation with planted debris: a file, a nested directory, and a symlink pointing *outside* `probe/`,
asserting the debris is removed and the symlink's target survives.

Two ordering locks, both encoding decisions that would otherwise regress silently:

- planting debris and binding with an **empty** allowlist raises `CapabilityUnavailable` **and** leaves
  `probe/` empty;
- a binding that has exited raises `ProtocolError` on use while its `VolumeEvidence` stays readable.

Bind-mount tests require `unshare --mount --map-root-user` and skip when it is unavailable; the
`st_dev`-sharing case is covered unconditionally in Tier 1 against fixture data.

### 11.4 Tier 4 — architecture and packaging

The purity guard rewritten as the §4.2 allowlist and applied by `rglob("*.py")` over all of `atoms/core`.
Packaging metadata and tests updated so the wheel declares `atoms.fs` and ships its `py.typed`.

A4a **cannot** assert that production composition passes exactly `CERTIFIED_ALLOWLIST`, because no
production composition root exists until A5; such a test would be vacuous or would force an out-of-scope
entry point into this sub-plan. A4a therefore asserts only what is true at its own boundary: that the
`allowlist` parameter is required and keyword-only, that `CERTIFIED_ALLOWLIST` is empty so its future
population is a deliberate act, and that no production caller of `bind_project_volume` exists yet. The
call-site assertion becomes ledger entry #18, owned by A5.

## 12. Deferred and delivery obligations

**Ledger entries created** (§3.2): detached `VolumeEvidence` must not authorize access, owned by A4b; the
lock primitive needs lease semantics and reclamation at lease entry, owned by A5; and the
production-allowlist call-site assertion, owned by A5.

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

1. `atoms/fs/` exists with the §4.1 layout; the §4.2 import **allowlist** guard covers all of
   `atoms/core` by `rglob` and passes.
2. `select_backend` refuses unsupported platforms and architectures without performing I/O.
3. `openat2` and `renameat2` are bound per §5.2, with `ENOSYS` detected operationally.
4. The `Backend` protocol carries exactly one operation set per §5.5 capability and no
   `supplied_capabilities` method. Every method is exercised: the eight capability operations by the
   runtime probe that reports them, and `open_root` by the bootstrap plus its §5.4 ancestor-symlink test.
5. `open_root` resolves with `RESOLVE_NO_SYMLINKS` over the whole path and refuses a symlinked ancestor;
   a missing `metadata_root` leaf is created relative to a guarded parent descriptor and reverified, and
   a missing parent refuses.
6. Mount identity is proved from held descriptors' mount IDs plus `st_dev`, and the roots must agree.
7. An unlisted filesystem type refuses. `ext4`, `xfs`, and `btrfs` options normalize per the §6.2 table,
   each read from its stated `mountinfo` field, with fixtures where a non-default value appears only in
   super-options. `VolumeConfiguration` carries `backend_revision`, the exact `kernel_identifier`, and
   `durability_features`; no certified entry references a feature without a resolver, and no widening is
   obtained by truncation.
8. `StorageProfile` is declaration-only, required, keyword-only, exact-match, and surfaced as
   `declared_storage_profile`.
9. The `allowlist` parameter is required and keyword-only; `CERTIFIED_ALLOWLIST` is empty; there is no
   override and no uncertified binding state. A4a asserts no production call site (ledger #18).
10. Bootstrap creates only engine-owned state under `metadata_root`, and reclamation is unconditional,
    symlink-safe, and takes `HeldProjectLock`. `lock` is opened descriptor-relative with `O_NOFOLLOW` and
    verified to be a regular file; every layout directory is reopened through `open_child_directory` on
    both the created and `EEXIST` paths; the sync-ignore marker is set best-effort on creation and its
    failure does not fail bootstrap.
11. Reclamation of a pre-existing `probe/` precedes allowlist refusal, and refusal writes nothing new.
12. Absent `anchored_traversal` or `advisory_project_lock` refuses; absent optional capabilities are
    reported; absent SQLite-WAL hostability refuses, certified across **two processes** through the §8.3
    choreography. `transfer_noclobber` and `link_anchor` are probed across distinct parent directories.
13. `VolumeEvidence` and `HeldProjectLock` are factory-guarded, as is `ProjectBinding`. All three expose
    the §7.1/§9.3 surfaces: no public descriptor attributes, borrowed descriptors, `O_CLOEXEC`, idempotent
    exit, and `ProtocolError` from every accessor once spent or once the lock is released — **except**
    `evidence` and `active` on the binding and `held` on the lock, which are deliberately readable
    afterwards so a diagnostic can inspect a released resource.
14. `verified_metadata_path` returns a single-component child of the verified, normalized metadata-root
    pathname, re-confirming `st_dev`/`st_ino` at each call; the §8.3 probe and A5 use no other means of
    constructing a database path.
15. `OSError` propagates except for the documented per-operation unsupported errno values, each licensed
    by the §10 probe precondition and proved by a mutation test.
16. Tier 3 resolves its volume portably and skips with a precise reason rather than assuming this
    machine's ext4-and-tmpfs layout.
17. All four verification tiers pass; Ruff and Pyright are clean; the ledger and `AGENTS.md` are updated
    in the same commit.
