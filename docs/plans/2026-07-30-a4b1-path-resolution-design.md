# A4b-1 — rooted path resolution

**Status:** Designed 2026-07-30. Not implemented. A4b-2 and A5–A8 remain unimplemented;
A4b-1 reads project space and never writes to it.

**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md).
Where this document and the authority design disagree, the authority wins.

## 1. Decision

A4b-1 delivers the resolution mechanism that A4b-2's rooted project proof runs on: an anchored,
component-by-component walk of one project-relative path that reports the deepest directory prefix
that exists today, what stands at the frontier, and the lookup constraints of every directory it
traversed.

A4b-1 decides nothing about a specification. It never sees a `CompiledSpec`, never constructs
`ProjectApprovedSpec`, and builds no topology. It answers exactly one question — *what does this
project-relative path resolve to right now, and under what lookup constraints?* — and hands the answer
to A4b-2, which alone composes those answers with a `CompiledSpec` into the resolved topology and the
approval proof.

Its public result is a frozen `ResolvedPrefix` per path, produced by an approval-scoped `PathResolver`
that borrows a live `ProjectBinding` and owns no descriptors between calls.

The layer is fail-closed by construction. It approves lookup semantics only where it can reproduce
them exactly, which today means non-casefold ext4 and nothing else.

## 2. Scope and non-scope

### 2.1 In scope

- `LookupProof`, `DirectoryConstraints`, and the narrow Linux reader that produces them.
- The exact filesystem dispatch that decides whether a volume's lookup relation is reproducible at all.
- `PathResolver`, `ResolvedPrefix`, and the anchored walk, including frontier classification.
- Real-filesystem `NAME_MAX` and `PATH_MAX` enforcement (ledger #4).
- Containment, metadata-root exclusion by identity, and mount-membership refusal (ledger #5).
- Lookup facts for the physical `work/` directory, for A4b-2's WORK scratch role.
- The constraint-inheritance rule for directories the transaction will create.
- `ProjectApprovalRefused`, and the authority §11 amendment that introduces it.

### 2.2 Not in scope

- `approve_for_project`, `ProjectApprovedSpec`, and any factory guard (ledger #9).
- Endpoint distinctness, scratch-leaf instantiation, and pairwise distinctness (ledger #2, #11).
- Construction of A3's `RecoveryTopology` (ledger #10).
- Re-running surface-tree consistency or created-directory ordering (ledger #3).
- Required-capability adjudication (ledger #6).
- Retention of the live binding in an approval proof (ledger #16).
- Any write to project space, including empirical lookup probing.
- Support for ext4 casefold directories, XFS `ascii-ci`, or Btrfs. These are refused, and future
  support is deliberately outside Plan A.

### 2.3 Relationship to A4b-2

A4b-1 supplies observation; A4b-2 supplies judgment. This is the same seam that separated A4a from
A4b, applied once more: the traversal, ioctl, and limit surface can be reviewed and verified without a
specification in sight, and the nine-obligation approval proof that follows it can be reviewed without
re-deriving how a path is resolved.

A4b-2 consumes `ResolvedPrefix` values, overlays the compiled timeline on them, proves that every
unresolved component is a directory the transaction creates, derives created-directory constraints
through §5.4's rule, and only then builds the topology and issues the proof.

## 3. Seam review against the deferred-obligation ledger

`AGENTS.md` requires this review before the plan is written.

### 3.1 Existing entries

A4b-1 **discharges no existing ledger entry.**

Entries **#4** and **#5** are the two whose mechanism lands here. Neither is discharged: both are
stated as obligations on `approve_for_project`, and A4b-1 has no `approve_for_project`. A4b-1 proves
that a single path's limits and containment are enforced against the real filesystem; A4b-2 proves
that every declared path in a specification has been through that enforcement before approval. Only
the second is what the ledger asks for.

Supplying a primitive discharges nothing. This is the same finding A4a recorded for #6.

The full A4b-2 ledger set is **#2, A4's part of #3, #4, #5, #6, #9, #10, #11, #16, and #20**.

### 3.2 New entries this design creates

Two entries, #19 and #20, are added to the ledger in the same commit as this design.

| # | Admitted shape | Admitted by | First owner | Required behavior |
| --- | --- | --- | --- | --- |
| 19 | Resolve-and-close leaves a window: a directory's identity, `LookupProof`, and mount membership may change between approval and use | A4b-1 resolution contract | A5, A6, A7 | Re-resolve and compare all three against the approved topology before relying on it. Before durable transaction authority exists: refuse. After a durable transaction record exists: halt. Never silently reapprove or substitute the newly resolved topology. A5's obligation holds in either form: it may not use any resolved identity or `LookupProof` from `ProjectApprovedSpec` to authorize project-space access, and if its preparation creates the work root or scratch entries it re-resolves under the held lock first |
| 20 | A4b-1 refuses by raising; nothing forces A4b-2 to surface those refusals rather than catching them | A4b-1/A4b-2 seam | A4b-2 | **`approve_for_project` catches no exception raised by A4b-1.** The obligation is categorical rather than a list, because any enumeration drifts as §7 grows. Propagation tests cover every declared exception type in §7 — `ProjectApprovalRefused`, `PreconditionRefused`, `CapabilityUnavailable`, `ProtocolError`, and a bare `OSError` — and each load-bearing branch that produces one. Removed when those tests land |

A fail-closed platform is **not** an admitted shape. Refusing XFS, Btrfs, and casefold directories
admits nothing, so no ledger entry tracks them; §2.2 records them as non-scope instead.

### 3.3 Delivery obligations that are not ledger entries

- Authority §11 gains `ProjectApprovalRefused` and `SpecValidationError`. The latter is a pre-existing
  omission corrected in the same edit.
- The conformance suite needs an ext4-specific volume fixture distinct from A4a's
  `test_volume_or_skip_reason`, because A4a admits ext4, XFS, and Btrfs while A4b-1 approves only ext4.
- The casefold conformance layer needs a documented privileged setup recipe (§9.4).

## 4. Architecture and ownership

### 4.1 Module layout

```
~/d/atoms/python/src/atoms/fs/lookup.py     LookupProof, DirectoryConstraints,
                                            read_lookup_constraints, inherited_constraints
~/d/atoms/python/src/atoms/fs/resolve.py    FilesystemIdentity, DirectoryFacts, EntryKind,
                                            AbsentFrontier, PresentFrontier, ResolvedHop,
                                            ResolvedPrefix, PathResolver
~/d/atoms/python/src/atoms/core/errors.py   ProjectApprovalRefused (added)
```

`lookup.py` owns every type it returns. `DirectoryConstraints` cannot live in `resolve.py` while
`resolve.py` imports `read_lookup_constraints`; the split above is what keeps the dependency
one-directional.

`read_lookup_constraints` is deliberately **not** added to the `Backend` protocol. That protocol is
the durability capability surface governed by `BACKEND_REVISION`, and bumping the revision
de-certifies every allowlist entry naming the old one (A4a §6.2). Lookup semantics are not a
durability capability, and a directory's fold behavior is not something a crash test certifies.
Keeping the reader in its own module also gives the verification layer a single patch point.

Neither module is exported from `atoms.fs`. `PathResolver` is constructed inside
`approve_for_project` and nowhere else, so callers cannot reuse one across approvals and silently
carry stale cached constraints.

### 4.2 Dependency direction

```
core/errors, core/paths  <--  fs/lookup  <--  fs/resolve  -->  fs/binding, fs/volume, fs/lock
```

`resolve.py` depends on A4a for the binding, `read_mount_id`, `open_child_directory`, and `close_all`.
It depends on A2 only for `require_rel_path`. It has no dependency on `atoms.core.compiler`,
`atoms.core.spec`, or `atoms.core.recovery` — an architecture test asserts this, because a dependency
on any of them would mean the resolution mechanism had begun judging a specification.

## 5. Lookup semantics

### 5.1 Why casefold directories are refused

A2 proves distinctness under a fixed portability key, `NFC(casefold(NFC(value)))`. The authority is
explicit that this is a conservative filter and not evidence of actual per-directory distinctness, so
A4b must establish the real lookup relation.

On ext4 that relation is governed by the superblock's encoding and Unicode version, and the comparison
uses the kernel's own normalization tables — canonical decomposition plus casefold — not Python's.
The kernel documents the dependency in its
[ext4 case-insensitive lookup guide](https://cdn.kernel.org/doc/html/latest/admin-guide/ext4.html#case-insensitive-file-name-lookups).

The governing version is not discoverable by an unprivileged process. Measured on the development
host:

| Probe | Result |
| --- | --- |
| `tune2fs -l` on a `casefold` image | `Character encoding: utf8-12.1` — raw device read, requires root |
| `/sys/fs/ext4/<device>/` | 47 attributes, none naming an encoding |
| `find /sys/fs/ext4 -name '*encoding*'` | nothing |
| `/usr/include/linux/stat.h` | no `STATX_ATTR_` for casefold |
| `FS_IOC_GETFLAGS` | reports *that* a directory folds, never *how* |

So an unprivileged engine can learn that a directory casefolds and cannot learn which Unicode version
decides its comparisons. The gap is not theoretical: that image declares Unicode 12.1.0 tables while
the project interpreter carries a materially later `unicodedata` version, and ext4 folds on canonical
decomposition while A2's key composes.

A4b therefore refuses. It does not guess, and it does not substitute A2's portability key for actual
equivalence. This is the same posture as `CERTIFIED_ALLOWLIST` shipping empty: the mechanism exists,
and nothing is approved until something can certify it.

The complementary claim — that a **non**-casefold ext4 directory compares names as exact bytes — is
verifiable without privileges, and is verified in §9.3.

### 5.2 Filesystem dispatch

`backend_id == "linux"` is necessary and not sufficient. XFS can be formatted with filesystem-wide
ASCII case-insensitive naming, independently of any per-directory flag
([`mkfs.xfs`](https://www.man7.org/linux/man-pages/man8/mkfs.xfs.8.html)), and A4a's
`_BARRIER_OPTIONS` admits `ext4`, `xfs`, and `btrfs`. Reading `FS_CASEFOLD_FL` on an XFS directory
would therefore report "clear" on a volume that folds, and label it `EXACT_BYTES`.

```
backend_id != "linux"  -> CapabilityUnavailable
filesystem_type:
    ext4   -> FS_IOC_GETFLAGS:  FS_CASEFOLD_FL clear -> EXACT_BYTES
                                FS_CASEFOLD_FL set   -> UNREPRODUCIBLE_CASEFOLD
    xfs    -> CapabilityUnavailable   (ascii-ci is filesystem-wide; no proof mechanism)
    btrfs  -> CapabilityUnavailable   (no proof mechanism)
    other  -> CapabilityUnavailable
```

`filesystem_type` comes from `binding.evidence.configuration` and is read once at construction:
`RESOLVE_NO_XDEV` guarantees the whole walk stays on one mount, so one dispatch decision covers every
directory it reaches. Only the ext4 branch performs per-directory ioctl work.

The two deferrals are not equivalent, and the design records the difference rather than burying it.
XFS `ascii-ci` folds ASCII only, which carries no Unicode-version dependency and is therefore exactly
reproducible; its eventual proof mechanism is tractable in a way ext4 casefold's is not. Btrfs is
refused because this design cannot confirm its current kernel casefold status either way — which is
itself the argument for refusing.

### 5.3 `read_lookup_constraints`

```python
def read_lookup_constraints(fd: int, filesystem_type: str) -> DirectoryConstraints
```

Dispatches on `filesystem_type` **before** issuing any ioctl, so a non-ext4 volume never has Linux
ext4 flag semantics applied to it. Then:

- `FS_IOC_GETFLAGS` on `fd`; `ENOTTY` → `CapabilityUnavailable`, with no fallback.
- `fpathconf(fd, "PC_NAME_MAX")`; a return of `-1` or any nonpositive value →
  `CapabilityUnavailable`. POSIX permits an indeterminate result and states these limits may change
  ([POSIX `fpathconf`](https://www.man7.org/linux/man-pages/man3/fpathconf.3p.html),
  [Linux `pathconf`](https://man7.org/linux/man-pages/man3/pathconf.3.html)).
- Any other `OSError` from either call propagates unchanged.

`NAME_MAX` stays per-directory because POSIX scopes it to the queried directory and describes pathname
variables as directory-associated and potentially volatile. `RESOLVE_NO_XDEV` proves one mount; it
does not prove every directory returns the same `_PC_NAME_MAX`.

### 5.4 Constraints for directories the transaction creates

A directory that does not exist yet cannot be queried, so descendants of a transaction-created
directory would have neither a lookup proof nor a usable `NAME_MAX`. The rule is ext4-specific and
lives beside the reader:

```python
def inherited_constraints(
    parent: DirectoryConstraints, filesystem_type: str
) -> DirectoryConstraints
```

For ext4: exact-byte behavior is inherited from an exact-byte parent — ext4 inherits the casefold flag,
so a directory created under a non-casefold parent is non-casefold — and the name bound is 255 bytes,
which the kernel documents in its
[ext4 directory format](https://www.kernel.org/doc/html/latest/filesystems/ext4/directory.html).
Any other `filesystem_type` raises `CapabilityUnavailable`, matching §5.2.

The function is pure, so A4b-2 can apply it without a filesystem. §9.3 locks it against reality by
creating a real directory and asserting its **observed** constraints equal the **derived** ones.

## 6. Resolution

### 6.1 `PathResolver` construction and liveness

```python
PathResolver(binding: ProjectBinding)
```

Construction refuses anything that makes every path unapprovable, in this order:

1. `binding.backend` and `binding.project_root_fd` are read **first**. Both route through
   `_require_active()`, which checks the binding's own flag *and* `lock.held`, so a closed binding or
   a released lock fails here rather than later.
2. `binding.evidence` is read second. It is a detached value whose property performs no liveness
   check, which is exactly why it must not be the first thing touched.
3. `backend_id != "linux"` → `CapabilityUnavailable`.
4. Root `DirectoryFacts`: `fstat` identity plus `read_lookup_constraints`.
5. A private `_path_max` from `fpathconf(project_root_fd, "PC_PATH_MAX")`, read once. `PATH_MAX`
   is exactly the longest relative pathname from the queried directory that does not cross a mount,
   and `RESOLVE_NO_XDEV` guarantees no crossing.
6. Root proof is `UNREPRODUCIBLE_CASEFOLD` → `ProjectApprovalRefused`.
7. Root identity equals `evidence.metadata_root_device`/`_inode` → `ProjectApprovalRefused`. If the
   two roots coincide, no declared path can avoid the metadata namespace.

Every subsequent `resolve()` re-reads `binding.project_root_fd` and `binding.backend` through the
properties rather than caching them, so a binding closed after construction fails on the next call.

### 6.2 Types

```python
# lookup.py
class LookupProof(Enum):
    EXACT_BYTES = "exact_bytes"
    UNREPRODUCIBLE_CASEFOLD = "unreproducible_casefold"

DirectoryConstraints(lookup_proof: LookupProof, name_max: int)

# resolve.py
FilesystemIdentity(device: int, inode: int)
DirectoryFacts(identity: FilesystemIdentity, constraints: DirectoryConstraints)

class EntryKind(Enum):
    DIRECTORY = "directory"
    REGULAR_FILE = "regular_file"
    SYMLINK = "symlink"
    OTHER = "other"

AbsentFrontier()
PresentFrontier(identity: FilesystemIdentity, kind: EntryKind)

ResolvedHop(declared_component: str, facts: DirectoryFacts)

ResolvedPrefix(
    root: DirectoryFacts,
    hops: tuple[ResolvedHop, ...],
    frontier_name: str,
    frontier: AbsentFrontier | PresentFrontier,
    remainder: tuple[str, ...],
)
```

The enum names a **proof status**, not a semantics. `UNREPRODUCIBLE_CASEFOLD` says the engine cannot
reproduce the relation; it never claims to know what the relation is.

`FilesystemIdentity` deliberately excludes the declared spelling, so two spellings reaching one
directory compare equal — the property that lets A4b-2 key topology nodes by identity. Spelling
survives on `ResolvedHop.declared_component` as provenance and diagnostics only.

Identity-keyed merging applies to **directories only**. Two declared paths that are hard links to one
file share a `FilesystemIdentity` while remaining distinct directory entries, and collapsing them
would be a defect.

The project root carries explicit `DirectoryFacts` rather than a `ResolvedHop`, because it has no
declared component.

`remainder == ()` means the frontier is the declared leaf. Otherwise `remainder` holds every declared
component after the blocking frontier, ending with the leaf:

```
a/b, with a a directory:            hops=(a,)  frontier_name=b  remainder=()
a/b, with a absent/file/symlink:    hops=()    frontier_name=a  remainder=(b,)
```

`ResolvedPrefix` exposes the deepest existing directory's constraints as a derived property —
`hops[-1].facts.constraints if hops else root.constraints` — rather than a stored field, so it cannot
drift out of agreement with `hops`.

### 6.3 The walk

```
resolve(rel_path) -> ResolvedPrefix

  require_rel_path("path", rel_path), SpecValidationError translated to ProtocolError
  len(fsencode(rel_path)) + 1 > path_max            -> ProjectApprovalRefused

  facts = root_facts ; fd = binding.project_root_fd        # borrowed, never closed

  for each ancestor component c:
      len(fsencode(c)) > facts.constraints.name_max -> ProjectApprovalRefused
      child = backend.open_child_directory(fd, c)
          ENOENT       -> AbsentFrontier at c;  remainder = the rest
          ENOTDIR      -> observe(c); kind must be REGULAR_FILE or OTHER
          ELOOP        -> observe(c); kind must be SYMLINK
          EXDEV        -> ProjectApprovalRefused (mount crossing)
          ENAMETOOLONG -> ProjectApprovalRefused
          other        -> propagates unwrapped
      identity = FilesystemIdentity(fstat(child))
      identity == metadata root                    -> ProjectApprovalRefused
      facts = memo.get(identity) or DirectoryFacts(identity, read_lookup_constraints(child, fs_type))
      facts.constraints.lookup_proof is UNREPRODUCIBLE_CASEFOLD -> ProjectApprovalRefused
      close(fd) unless fd is the borrowed root ; fd = child

  # ancestors exhausted: the leaf is observed, never opened as a directory
  len(fsencode(leaf)) > facts.constraints.name_max  -> ProjectApprovalRefused
  observe(leaf) -> AbsentFrontier or PresentFrontier ; remainder = ()
```

Reusing A2's `require_rel_path` rather than a second grammar is what makes the EXDEV interpretation
sound. `RESOLVE_BENEATH` and `RESOLVE_NO_XDEV` **share EXDEV**, measured:

| Attempt | Result |
| --- | --- |
| escape via `..` | `EXDEV` |
| mount crossing | `EXDEV` |
| component is a regular file | `ENOTDIR` |
| component is a symlink | `ELOOP` |
| component absent | `ENOENT` |

The walk can attribute EXDEV to a mount crossing only because no escape route survives to reach
`openat2`: `require_rel_path` rejects absolute paths, `.`, `..`, trailing slashes, empty interior
components, NUL bytes, non-UTF-8-encodable values, and scratch-sigil aliases before any syscall, and
`RESOLVE_NO_SYMLINKS` converts every symlink into `ELOOP` first. §9.3 pins that the rejection happens
with zero `openat2` calls.

`resolve()` accepts **declared persistent paths only**. `require_rel_path` rejects any component that
aliases the reserved scratch sigil, which is correct for persistent paths and would lock A4b-2 out of
the scratch distinctness it owns. A4b-2 resolves the scratch *parent* — a persistent path's parent, or
the work root — and validates each generated leaf name against that parent's approved `name_max`.
There is no scratch mode on this signature.

### 6.4 Observing a present entry

`lstat` cannot detect a mount at the declared leaf: a bind mount can share `st_dev` with its source
while carrying a distinct mount ID, which A4a's own conformance work measured directly (`st_dev`
38 == 38, `mnt_id` 599 vs 641). The authority requires a nested or bind mount at any effect path to
refuse during approval, so the observation must be descriptor-coherent.

```
observe(name):
    fd = openat(parent_fd, name, O_PATH | O_NOFOLLOW | O_CLOEXEC)
        ENOENT -> AbsentFrontier
    st = fstat(fd)                              -> FilesystemIdentity + EntryKind from st_mode
    read_mount_id(fd) != evidence.mount_id      -> ProjectApprovalRefused
    identity == metadata root                   -> ProjectApprovalRefused
    close(fd) immediately
```

Measured: an `O_PATH | O_NOFOLLOW` descriptor supports `fstat` — returning `mode 120777` for a
symlink, so the entry is observed rather than followed — and A4a's `read_mount_id` reads
`/proc/self/fdinfo/<fd>` successfully for such a descriptor.

This is leaf-retaining: the declared name is preserved and the entry is never opened as a directory.

Blockers are observed the same way. A bind-mounted blocker must refuse at approval, because deleting a
mount point fails with `EBUSY` at execution.

The agreement rule is the drift guard. `openat2` and the following `openat` are two filesystem
moments, so the observed kind must corroborate the errno:

| errno | admissible kinds |
| --- | --- |
| `ENOTDIR` | `REGULAR_FILE`, `OTHER` |
| `ELOOP` | `SYMLINK` |

A directory, an absence, or any other kind means the entry changed between the two calls, and the
resolver raises `PreconditionRefused` rather than assembling one observation from two instants.

`ENOTDIR` does not imply a regular file — FIFOs, sockets, and device nodes produce it too, and a
stable special entry agrees with the errno rather than indicating drift. `EntryKind.OTHER` keeps that
case distinguishable so A4b-2 can refuse an `OTHER` ancestor on the ground that no closed effect
variant can remove it.

### 6.5 Descriptor discipline

Closing the parent immediately after the child opens bounds the resolver by a constant rather than by
depth: **at most three resolver-opened descriptors exist at any instant, one in steady state, at any
path depth.** The peak occurs during a leaf observation, where the resolver-owned parent, the `O_PATH`
descriptor, and the `/proc/self/fdinfo/<fd>` handle that `read_mount_id` opens internally all coexist.
That third descriptor belongs to A4a's `read_mount_id` rather than to this module, which is exactly why
the bound is stated as three: closing the parent early to reach a bound of two would hide a descriptor
the resolver genuinely causes to exist. The borrowed project-root descriptor is never closed.

Release is exception-safe on every path, using A4a's `close_all` and its first-failure-raised
discipline. Cleanup order is reverse acquisition: child, then the resolver-owned parent, never the
borrowed root.

The component name is checked against the **parent's** `name_max` before the child is opened, which is
what makes the early parent close safe: the parent's facts are consumed before it is released.

Closing does not make a later re-resolution safe on its own, and this design claims no such thing.
The `F` attribute **can be changed** — set or cleared — on an empty directory on a casefold-enabled
filesystem, without changing its inode
([`chattr(1)`](https://man7.org/linux/man-pages/man1/chattr.1.html)). A directory's identity can
therefore remain equal while its `LookupProof` changes, so ledger #19's comparison can genuinely fire.
Its three independent checks — identity, `LookupProof`, and mount membership — are the actual safety
mechanism, and none of them is redundant.

### 6.6 `work_root_facts()`

A4b-2 needs lookup facts for the physical work directory. Non-WORK scratch roles sit beside their
resolved persistent parent, but `CreateDirectory` takes `ScratchRole.WORK`, which lives under
`metadata_root/work` — and `CreateDirectory` is the only effect that takes it.

```
work_root_facts()                        # private, lazy, memoized on success only
    backend = binding.backend            # liveness FIRST, before the memo is consulted, so a
    parent  = binding.metadata_root_fd   # cached result still fails after closure or lock release
    if cached is not None: return cached

    fd = backend.open_child_directory(parent, "work")          # guarded traversal
        ENOENT, ENOTDIR, ELOOP, EXDEV -> ProtocolError
        any other OSError             -> propagates unchanged
    read_mount_id(fd) != evidence.mount_id                     -> ProtocolError
    constraints = read_lookup_constraints(fd, fs_type)
    constraints.lookup_proof is UNREPRODUCIBLE_CASEFOLD        -> ProjectApprovalRefused
    cached = DirectoryFacts(fstat(fd), constraints)            # cached only on success
    close(fd)
```

Only the documented namespace contradictions become `ProtocolError`: `work/` is engine-owned, created
by `ensure_metadata_layout` under the held lock at bind time, so its absence or malformation is a
violated internal invariant. `EIO`, `EMFILE`, and similar system failures are not invariant violations
and propagate unchanged, preserving §7's no-blanket rule.

Liveness is read **before** the memo is consulted, so memoization cannot become a bypass: a resolver
whose binding is closed after the first call must still fail on the second. Only a successful result
is cached, so a refusal is never converted into a cached success on a later call. A casefolded `work/`
raises `ProjectApprovalRefused` like any other unreproducible directory — `metadata_root` is
engine-owned, but its lookup relation is no more reproducible than a project directory's.

It is lazy so that an unapprovable `work/` cannot refuse a specification that contains no
`CreateDirectory`.

A3's `_validate_topology` requires `WorkRoot` be parented by `ProjectRoot` **logically**, while `work/`
sits physically under `metadata_root`. A4b-2 emits the logical edge and takes the physical facts from
here; the two are not in conflict.

## 7. Error contract

| Raised | For |
| --- | --- |
| `ProjectApprovalRefused` *(new)* | mount crossing, mount membership, metadata-root identity, `NAME_MAX`/`PATH_MAX`, `UNREPRODUCIBLE_CASEFOLD` |
| `PreconditionRefused` | errno ↔ observed-kind disagreement at the frontier |
| `CapabilityUnavailable` | non-`linux` backend, non-ext4 filesystem, `ENOTTY` from the flag read, nonpositive `fpathconf` |
| `ProtocolError` | malformed input path, closed binding, released lock, `work/` namespace contradiction |
| bare `OSError` | **everything else, unwrapped** |

Exactly five **pathname** errnos are interpreted as evidence: `ENOENT`, `ENOTDIR`, `ELOOP`, `EXDEV`,
`ENAMETOOLONG`. One further errno is interpreted outside the walk — `ENOTTY` from the flag ioctl in
§5.3, meaning the filesystem does not implement it at all. Nothing else is interpreted anywhere: there
is no `except OSError` blanket in either module, and `ENAMETOOLONG` conversion is scoped around each
pathname operation that can return it rather than around the walk.

`ENAMETOOLONG` stays a refusal rather than an assertion: reaching it means the kernel disagreed with
`fpathconf`, and that is the filesystem's answer, not an engine defect.

`CapabilityUnavailable` keeps its A4a meaning — required semantics cannot be supplied — and is never
used for a path that simply fails the rooted proof.

## 8. Limits

Both limits compare `os.fsencode` byte lengths, not character counts: a 200-character UTF-8 name can
exceed a 255-byte bound.

```
len(os.fsencode(component)) <= name_max          # NAME_MAX excludes the terminating NUL
len(os.fsencode(rel_path)) + 1 <= path_max       # PATH_MAX includes it
```

`name_max` comes from the parent that performs the lookup and is therefore per-directory. `path_max`
is volume-scoped and read once, against the relative spelling from the project root — which is also
the only spelling available, since `ProjectBinding` exposes no project-root pathname.

Measured on the development volume: `PC_NAME_MAX` 255, `PC_PATH_MAX` 4096.

## 9. Verification

Five layers. Tiers 1, 2, and 5 always run. Tier 3 skips without an ext4 volume, and its bind-mount
cases skip additionally where user namespaces are unavailable. Tier 4 skips without the privileged
setup of §9.4. Only tier 4 requires privileges.

### 9.1 Tier 1 — pure

`inherited_constraints` for ext4 and its refusal for every other filesystem type. Frozen-value
behavior of `ResolvedPrefix`, `DirectoryFacts`, `FilesystemIdentity`, and the frontier types.
`ResolvedPrefix`'s derived deepest-constraints property with empty and non-empty `hops`.
`FilesystemIdentity` equality and hashing, including that equal identities with different declared
spellings compare equal.

### 9.2 Tier 2 — injection

`read_lookup_constraints` is tested **directly**, not only patched:

- dispatch occurs before any ioctl — an `xfs`/`btrfs`/`ext2` type refuses without issuing one
- `FS_CASEFOLD_FL` clear → `EXACT_BYTES`; set → `UNREPRODUCIBLE_CASEFOLD`
- `ENOTTY` → `CapabilityUnavailable`, no fallback
- `PC_NAME_MAX` returning `-1`, `0`, or another negative value → `CapabilityUnavailable`
- unexpected ioctl and `PC_NAME_MAX` errors propagate unchanged

The same four `fpathconf` cases are covered independently for `PC_PATH_MAX` at construction — `-1`,
`0`, another negative value, and an unexpected `OSError` — because the two limits are read by
different code at different times, and testing only one leaves the other's handling unproven.

Resolver-level tests then patch that function as the clean seam:

- `UNREPRODUCIBLE_CASEFOLD` at the root refuses at construction; at an intermediate hop refuses at
  that hop with earlier hops already resolved
- `backend_id != "linux"` → `CapabilityUnavailable`
- injected `ENOTDIR` where a directory actually sits → `PreconditionRefused`, not a frontier
- `EACCES`, `EPERM`, `EIO`, `EMFILE` propagate **unwrapped**, asserted by exact type and errno
- closed binding and released lock → `ProtocolError` from `resolve()`
- with a closed binding the failure is the liveness `ProtocolError`, never an evidence-derived
  refusal, pinning the §6.1 read order

Every propagated-error test additionally asserts the backend operation was **called exactly once, with
the valid parent descriptor and the expected component**. A4a's review found a propagation test that
passed because the fake raised before the dependency was ever exercised; this assertion is what
prevents the same blind spot here.

### 9.3 Tier 3 — real ext4

Requires an ext4 volume through an A4b-1-specific fixture whose skip reason names ext4 explicitly,
distinct from A4a's `test_volume_or_skip_reason`.

**Byte-exactness**, because `EXACT_BYTES` is a claim about the filesystem. Create `a`, look up `A`;
create NFC `é`, look up NFD `é`; and the reverse. All three must miss. Measured on the development
volume: all three return `ENOENT`.

**Frontier matrix**, one test per branch, each asserting `frontier_name` and the full `remainder`:
full chain resolves; leaf absent; leaf a regular file; leaf a symlink; leaf a directory; ancestor
blocked by a regular file (`ENOTDIR` → `REGULAR_FILE`); by a symlink (`ELOOP` → `SYMLINK`); by a FIFO
via `os.mkfifo` (`ENOTDIR` → `OTHER`).

**Refusals needing real structure**: a path equal to `metadata_root` and a path beneath it, both
refused by identity; a 255-byte name accepted and a 256-byte name refused; a relative path exceeding
4096 bytes refused; `..` rejected as `ProtocolError` with **zero `openat2` calls**.

**Mount cases**, using the `unshare --mount --map-root-user` recipe A4a's `distinct_volume` fixture
already proved works on this host:

- a bind mount at an **ancestor** → `EXDEV` → refusal
- a bind mount at the **declared leaf** → equal `st_dev`, distinct `mnt_id` → refusal. This is the
  test that justifies `O_PATH` + `read_mount_id` over `lstat`; with `lstat` it passes while the mount
  goes unseen

**Created-directory constraints**: create a real directory, read its observed constraints, and assert
they equal `inherited_constraints` applied to its parent. Without this the §5.4 rule is only prose.

**`work_root_facts()`**: lazy (not called during construction), memoized (one open across repeated
calls), mount-checked, casefold-refusing, and closing its descriptor on every failure path. One test
populates the memo, closes the binding, and calls again — it must raise `ProtocolError`, proving
memoization is not a liveness bypass. Another proves a refusal is not cached: a failing call followed
by a succeeding one must re-open. A4b-2 later locks "called iff a `CreateDirectory` exists."

**Hard links**: two declared paths linked to one file resolve to equal leaf identities with distinct
spelling and parent provenance. Whether topology merges them is an A4b-2 test, not this one.

**Descriptor peak, measured deterministically.** Periodic sampling can miss the interval where
descriptors coexist, so every acquisition point records `/proc/self/fd` immediately after it returns:
the wrapped backend after `open_child_directory`, the wrapped leaf observation after `os.open`, and —
critically — inside `read_mount_id` after it opens `/proc/self/fdinfo/<fd>`. Omitting that third hook
is what would let a false bound of two pass. The maximum delta over baseline is compared for depth 2
and depth 512, must be equal, and must equal three. `/proc/self/fd` rather than an `os.open` patch,
because `open_child_directory` issues raw `openat2` through `ctypes` and is invisible to such a patch.

### 9.4 Tier 4 — real casefold volume

Proves the one thing injection cannot: that `FS_CASEFOLD_FL` corresponds to actual folding.

Skipped only when `ATOMS_CASEFOLD_VOLUME` is **unset**. When it is explicitly supplied, a missing
`chattr`, a failed `+F`, or a non-casefold ext4 image **fails with an actionable message** rather than
skipping — an explicit opt-in that silently does nothing is worse than no opt-in.

One-time setup. `mkfs` is unprivileged; only the mount is not:

```bash
IMG=~/.local/share/atoms/casefold.img
MNT=~/.local/share/atoms/casefold

mkdir -p "$(dirname "$IMG")" "$MNT"
truncate -s 256M "$IMG"
mkfs.ext4 -q -O casefold -F "$IMG"          # unprivileged

sudo mount -o loop "$IMG" "$MNT"            # privileged
sudo chown "$USER" "$MNT"                   # privileged

export ATOMS_CASEFOLD_VOLUME="$MNT"
cd ~/d/atoms/python && uv run pytest
```

The image lives outside the repository so no test artifact can land in `~/d/atoms/`.

The suite creates `plain/` and `folded/`, sets `chattr +F folded/` while it is empty, and asserts:

- `folded/` genuinely folds — create `a`, look up `A`, found
- `plain/`, on the same filesystem, does not
- a path through `folded/` raises `ProjectApprovalRefused`
- a path through `plain/` resolves

This is §13.3 surface 3's mixed-policy case exercised against the **resolution mechanism**. It does
not discharge the surface — §3.1 records that A4b-1 discharges nothing, and the surface is a claim
about approval. A4b-2's public refusal test through `approve_for_project` discharges it; this tier
locks the mechanism that test will depend on. What it does prove directly is the reason the rule is
per-directory rather than per-mount: the policy is decided per directory, and one directory's answer
is never applied to another on the same filesystem.

Created-directory inheritance is also exercised here, under `plain/`, not only on a filesystem that
lacks the casefold feature entirely. That is the stronger case: it proves a directory created beneath
a non-casefold parent on a **casefold-capable** filesystem inherits `EXACT_BYTES`, which is exactly
what §5.4's rule asserts and what a feature-less volume cannot demonstrate.

A separate test creates a directory, creates a child inside it, and asserts `chattr +F` then fails.
That is a distinct claim from the fold test above — which sets the flag while the directory is still
empty — and it is what backs §6.5's rationale that an inode's proof cannot change under a resolver
that re-verifies identity.

Development-host status: the repo volume lacks the `casefold` feature (`chattr +F` returns
`EOPNOTSUPP`) while the kernel supports it (`CONFIG_UNICODE=y`, `/sys/fs/ext4/features/casefold`
present). `mkfs.ext4 -O casefold` succeeds unprivileged; mounting fails under both `unshare
--map-root-user` and `losetup`. So this tier requires the recipe above and nothing less.

### 9.5 Tier 5 — architecture

`resolve.py` and `lookup.py` import nothing from `atoms.core.compiler`, `atoms.core.spec`, or
`atoms.core.recovery`. Neither `PathResolver` nor `read_lookup_constraints` is exported from
`atoms.fs`. No `except OSError` blanket appears in either module. `Backend` gains no method and
`BACKEND_REVISION` is unchanged.

## 10. Deferred and delivery obligations

**Ledger entries created** (§3.2): **#19**, the resolve-and-close re-resolution window, owned by
A5/A6/A7; **#20**, the A4b-1/A4b-2 refusal-propagation seam, owned by A4b-2.

**Ledger entries discharged:** none. #4 and #5 remain open against A4b-2's `approve_for_project`.

**Authority amendment:** §11 gains `ProjectApprovalRefused` and `SpecValidationError`.

**Non-scope, recorded so it is not mistaken for an admission:** ext4 casefold support, XFS `ascii-ci`
support, and Btrfs support. All three are refused, and refusal admits nothing.

## 11. Acceptance criteria

1. `resolve()` returns the deepest existing directory prefix and never requires that every ancestor
   exist.
2. The frontier distinguishes absence from a blocking entry, and blocking entries distinguish
   `REGULAR_FILE`, `SYMLINK`, and `OTHER`.
3. `remainder == ()` iff the frontier is the declared leaf.
4. Non-casefold ext4 is the only approvable lookup proof; XFS, Btrfs, every other filesystem type, and
   every non-linux backend refuse with `CapabilityUnavailable`.
5. A casefold directory refuses with `ProjectApprovalRefused` at whatever depth it appears.
6. Byte-exact lookup is verified against a real ext4 volume for case and for both normalization
   directions.
7. `NAME_MAX` is enforced per directory and `PATH_MAX` per volume, both on encoded byte lengths, both
   verified against real names rather than injected limits.
8. A path equal to the metadata root and a path beneath it both refuse by identity.
9. A bind mount at an ancestor and a bind mount at the declared leaf both refuse, the latter with
   equal `st_dev`.
10. At most three resolver-opened descriptors exist at any instant — resolver-owned parent, `O_PATH`
    observation, and `read_mount_id`'s `fdinfo` handle — verified deterministically with all three
    acquisition points instrumented, at depth 2 and depth 512.
11. Exactly five pathname errnos are interpreted, plus `ENOTTY` in `read_lookup_constraints`; every
    other `OSError` propagates unwrapped, verified with the backend call asserted to have occurred
    exactly once with the valid parent descriptor and component.
12. A closed binding or released lock fails every `resolve()` with `ProtocolError`, before any
    evidence-derived refusal.
13. `work_root_facts()` is lazy, mount-checked, casefold-refusing, releases its descriptor on every
    failure path, caches only successes, and reads liveness before its memo — so a call after the
    binding closes still raises `ProtocolError`.
14. Observed constraints of a really-created directory equal `inherited_constraints` applied to its
    parent.
15. `read_lookup_constraints` is tested directly, not only through the resolver.
16. `ruff check` and `pyright` pass; neither `atoms.fs.resolve` nor `atoms.fs.lookup` imports
    `atoms.core.compiler`, `atoms.core.spec`, or `atoms.core.recovery`.
