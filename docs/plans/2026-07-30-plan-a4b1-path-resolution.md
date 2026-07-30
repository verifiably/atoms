# A4b-1 Path Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the anchored path-resolution mechanism A4b-2's rooted project proof runs on — a
`PathResolver` that reports the deepest existing directory prefix of one project-relative path, a
discriminated frontier, and the lookup constraints of every directory traversed.

**Architecture:** Two new modules under `atoms/fs/`. `lookup.py` owns the Linux-specific reading of a
directory's lookup constraints and the ext4 inheritance rule. `resolve.py` owns the value types and the
`PathResolver` that walks a path with A4a's guarded traversal primitive, closing each descriptor as
soon as its child opens. Nothing here sees a `CompiledSpec`, builds a topology, or writes to project
space.

**Tech Stack:** Python 3.13, stdlib only (`os`, `fcntl`, `array`, `stat`, `errno`), `pytest`, `ruff`,
`pyright`. Builds on A4a's `ProjectBinding`, `open_child_directory`, `read_mount_id`, and `close_all`.

**Design:** [`2026-07-30-a4b1-path-resolution-design.md`](2026-07-30-a4b1-path-resolution-design.md).
**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md).
Where this plan and either document disagree, the design wins over this plan and the authority wins over both.

## Global Constraints

- Work from `~/d/atoms/python`. Gates are `uv run pytest`, `uv run ruff check`, `uv run pyright`.
- Ruff config sets only `line-length = 120`, so default rules (E4/E7/E9/F) apply. F401, F811, and F821
  are real gate failures.
- **No `except OSError` blanket** in either new module. Exactly five pathname errnos are interpreted —
  `ENOENT`, `ENOTDIR`, `ELOOP`, `EXDEV`, `ENAMETOOLONG` — plus `ENOTTY` in `read_lookup_constraints`.
  Every other `OSError` propagates unwrapped.
- **A4b-1 never writes to project space.** No `mkdir`, `open(O_CREAT)`, `symlink`, or `unlink` under
  the project root in production code. Tests may create fixtures in their own temporary volume.
- Non-casefold ext4 is the only approvable lookup proof. XFS, Btrfs, every other filesystem type, and
  every non-linux backend refuse with `CapabilityUnavailable`.
- Neither new module may import `atoms.core.compiler`, `atoms.core.spec`, or `atoms.core.recovery`.
- Neither `PathResolver` nor `read_lookup_constraints` is exported from `atoms.fs`.
- The `Backend` protocol gains no method; `BACKEND_REVISION` is unchanged.
- Filepaths in docs and comments use `~/d/atoms/...`.
- Conventional commits. No AI-attribution trailer or footer.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/atoms/core/errors.py` | **Modify.** Add `ProjectApprovalRefused`. |
| `src/atoms/fs/lookup.py` | **Create.** `LookupProof`, `DirectoryConstraints`, `read_lookup_constraints`, `inherited_constraints`. Linux/ext4 flag semantics live here and nowhere else. |
| `src/atoms/fs/resolve.py` | **Create.** `FilesystemIdentity`, `DirectoryFacts`, `EntryKind`, frontier types, `ResolvedHop`, `ResolvedPrefix`, `PathResolver`. |
| `src/atoms/fs/bootstrap.py` | **Modify.** Add `WORK_DIRECTORY = "work"` beside `PROBE_DIRECTORY`. |
| `tests/fs_support.py` | **Modify.** ext4 and casefold volume resolution, descriptor counting, namespace helper. |
| `tests/conftest.py` | **Modify.** `ext4_volume`, `casefold_volume` fixtures. |
| `tests/test_fs_lookup.py` | **Create.** Direct tests of `lookup.py` (tiers 1–2). |
| `tests/test_fs_resolve_construction.py` | **Create.** `PathResolver.__init__` refusals and ordering. |
| `tests/test_fs_resolve_walk.py` | **Create.** Observation, frontier matrix, memo, limits (tiers 2–3). |
| `tests/test_fs_resolve_work_base.py` | **Create.** `work_base_facts()` laziness, memo, release. |
| `tests/test_fs_resolve_conformance.py` | **Create.** Tier 3 real-filesystem conformance. |
| `tests/test_fs_resolve_casefold.py` | **Create.** Tier 4 opt-in casefold volume. |
| `tests/test_fs_architecture.py` | **Modify.** Tier 5 import and export guards. |
| `docs/plans/2026-07-23-...-design.md` | **Modify.** §11 gains `ProjectApprovalRefused` and `SpecValidationError`. |

Seven tasks. Each ends with a deliverable a reviewer could reject while approving its neighbour.

---

## Task 1: Lookup constraints

**Files:**
- Modify: `src/atoms/core/errors.py`
- Create: `src/atoms/fs/lookup.py`
- Create: `tests/test_fs_lookup.py`

**Interfaces:**
- Consumes: `atoms.core.errors.CapabilityUnavailable`.
- Produces: `LookupProof` (enum, `EXACT_BYTES` | `UNREPRODUCIBLE_CASEFOLD`);
  `DirectoryConstraints(lookup_proof: LookupProof, name_max: int)` frozen;
  `read_lookup_constraints(fd: int, filesystem_type: str) -> DirectoryConstraints`;
  `inherited_constraints(parent: DirectoryConstraints, filesystem_type: str) -> DirectoryConstraints`;
  `ProjectApprovalRefused` in `atoms.core.errors`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fs_lookup.py`:

```python
"""Direct tests of the lookup-constraint reader (design §5.2-§5.4)."""

from __future__ import annotations

import errno
import os

import pytest

from atoms.core.errors import CapabilityUnavailable
from atoms.fs.lookup import (
    EXT4_NAME_MAX,
    FS_CASEFOLD_FL,
    DirectoryConstraints,
    LookupProof,
    inherited_constraints,
    read_lookup_constraints,
)

NON_EXT4 = ("xfs", "btrfs", "ext2", "tmpfs", "")


@pytest.fixture
def directory_fd(tmp_path):
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    yield fd
    os.close(fd)


@pytest.mark.parametrize("filesystem_type", NON_EXT4)
def test_dispatch_refuses_every_non_ext4_filesystem(directory_fd, filesystem_type):
    with pytest.raises(CapabilityUnavailable) as caught:
        read_lookup_constraints(directory_fd, filesystem_type)
    assert filesystem_type in str(caught.value) or "ext4" in str(caught.value)


@pytest.mark.parametrize("filesystem_type", NON_EXT4)
def test_dispatch_happens_before_any_ioctl(monkeypatch, directory_fd, filesystem_type):
    """A non-ext4 volume must never have ext4 flag semantics applied to it."""
    calls = []
    monkeypatch.setattr(
        "atoms.fs.lookup.fcntl.ioctl",
        lambda *args, **kwargs: calls.append(args) or 0,
    )
    with pytest.raises(CapabilityUnavailable):
        read_lookup_constraints(directory_fd, filesystem_type)
    assert calls == []


def test_clear_casefold_flag_reports_exact_bytes(monkeypatch, directory_fd):
    monkeypatch.setattr("atoms.fs.lookup._casefold_flag", lambda fd: False)
    constraints = read_lookup_constraints(directory_fd, "ext4")
    assert constraints.lookup_proof is LookupProof.EXACT_BYTES


def test_set_casefold_flag_reports_unreproducible(monkeypatch, directory_fd):
    monkeypatch.setattr("atoms.fs.lookup._casefold_flag", lambda fd: True)
    constraints = read_lookup_constraints(directory_fd, "ext4")
    assert constraints.lookup_proof is LookupProof.UNREPRODUCIBLE_CASEFOLD


def test_the_casefold_flag_is_read_from_the_real_ioctl(directory_fd):
    """The bit tested must be FS_CASEFOLD_FL, not some other flag that happens to be clear."""
    from atoms.fs.lookup import _casefold_flag, _raw_flags

    assert _casefold_flag(directory_fd) is bool(_raw_flags(directory_fd) & FS_CASEFOLD_FL)


def test_enotty_from_the_flag_ioctl_refuses_without_fallback(monkeypatch, directory_fd):
    def refuse(*args, **kwargs):
        raise OSError(errno.ENOTTY, "Inappropriate ioctl for device")

    monkeypatch.setattr("atoms.fs.lookup.fcntl.ioctl", refuse)
    with pytest.raises(CapabilityUnavailable):
        read_lookup_constraints(directory_fd, "ext4")


@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO, errno.EPERM])
def test_unexpected_ioctl_errors_propagate_unwrapped(monkeypatch, directory_fd, code):
    def refuse(*args, **kwargs):
        raise OSError(code, "injected")

    monkeypatch.setattr("atoms.fs.lookup.fcntl.ioctl", refuse)
    with pytest.raises(OSError) as caught:
        read_lookup_constraints(directory_fd, "ext4")
    assert type(caught.value) is OSError or not isinstance(caught.value, CapabilityUnavailable)
    assert caught.value.errno == code


@pytest.mark.parametrize("value", [-1, 0, -17])
def test_nonpositive_name_max_refuses(monkeypatch, directory_fd, value):
    monkeypatch.setattr("atoms.fs.lookup._casefold_flag", lambda fd: False)
    monkeypatch.setattr("atoms.fs.lookup.os.fpathconf", lambda fd, name: value)
    with pytest.raises(CapabilityUnavailable) as caught:
        read_lookup_constraints(directory_fd, "ext4")
    assert "PC_NAME_MAX" in str(caught.value)


@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO])
def test_unexpected_fpathconf_errors_propagate_unwrapped(monkeypatch, directory_fd, code):
    monkeypatch.setattr("atoms.fs.lookup._casefold_flag", lambda fd: False)

    def refuse(fd, name):
        raise OSError(code, "injected")

    monkeypatch.setattr("atoms.fs.lookup.os.fpathconf", refuse)
    with pytest.raises(OSError) as caught:
        read_lookup_constraints(directory_fd, "ext4")
    assert caught.value.errno == code


def test_read_constraints_reports_the_real_name_max(directory_fd):
    constraints = read_lookup_constraints(directory_fd, "ext4")
    assert constraints.name_max == os.fpathconf(directory_fd, "PC_NAME_MAX")


def test_inheritance_keeps_the_parent_proof_and_bounds_names_to_ext4_max():
    parent = DirectoryConstraints(lookup_proof=LookupProof.EXACT_BYTES, name_max=17)
    derived = inherited_constraints(parent, "ext4")
    assert derived == DirectoryConstraints(
        lookup_proof=LookupProof.EXACT_BYTES, name_max=EXT4_NAME_MAX
    )


def test_inheritance_does_not_launder_an_unreproducible_parent():
    parent = DirectoryConstraints(
        lookup_proof=LookupProof.UNREPRODUCIBLE_CASEFOLD, name_max=255
    )
    assert inherited_constraints(parent, "ext4").lookup_proof is (
        LookupProof.UNREPRODUCIBLE_CASEFOLD
    )


@pytest.mark.parametrize("filesystem_type", NON_EXT4)
def test_inheritance_refuses_every_non_ext4_filesystem(filesystem_type):
    parent = DirectoryConstraints(lookup_proof=LookupProof.EXACT_BYTES, name_max=255)
    with pytest.raises(CapabilityUnavailable):
        inherited_constraints(parent, filesystem_type)


def test_constraints_are_frozen():
    constraints = DirectoryConstraints(lookup_proof=LookupProof.EXACT_BYTES, name_max=255)
    with pytest.raises(Exception):
        constraints.name_max = 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_lookup.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'atoms.fs.lookup'`.

- [ ] **Step 3: Add the error type**

In `src/atoms/core/errors.py`, after `SpecValidationError`:

```python
class ProjectApprovalRefused(AtomsError):
    """The rooted project proof failed (design §5.4).

    Distinct from SpecValidationError because the two proof stages are deliberately
    non-substitutable: a caller must not be able to catch one type and treat a
    project/root refusal as a compilation refusal.
    """
```

- [ ] **Step 4: Write `lookup.py`**

Create `src/atoms/fs/lookup.py`:

```python
"""Per-directory lookup constraints (A4b-1 design §5).

Linux and ext4 only, deliberately. FS_IOC_GETFLAGS reports *that* an ext4 directory
casefolds but never *which* Unicode version governs the comparison: the encoding lives
in the superblock, is not exported through /sys, and has no statx attribute. A folding
directory is therefore reported as unreproducible rather than modeled, and every other
filesystem refuses until it has a proof mechanism of its own.
"""

from __future__ import annotations

import array
import errno
import fcntl
import os
from dataclasses import dataclass
from enum import Enum

from atoms.core.errors import CapabilityUnavailable

FS_IOC_GETFLAGS = 0x80086601
"""_IOR('f', 1, long) on 64-bit Linux."""

FS_CASEFOLD_FL = 0x40000000
"""Per-directory case-insensitive lookup, settable only on a casefold-enabled ext4."""

EXT4_NAME_MAX = 255
"""ext4 bounds a filename to 255 bytes (kernel ext4 directory format)."""

_SUPPORTED_FILESYSTEM = "ext4"


class LookupProof(Enum):
    """What the engine can *prove* about a directory's lookup relation.

    Not a description of the semantics: UNREPRODUCIBLE_CASEFOLD says the relation
    cannot be reproduced, never that it is known.
    """

    EXACT_BYTES = "exact_bytes"
    UNREPRODUCIBLE_CASEFOLD = "unreproducible_casefold"


@dataclass(frozen=True, slots=True)
class DirectoryConstraints:
    lookup_proof: LookupProof
    name_max: int


def _require_supported(filesystem_type: str) -> None:
    if filesystem_type != _SUPPORTED_FILESYSTEM:
        raise CapabilityUnavailable(
            f"filesystem {filesystem_type!r} has no lookup-proof mechanism; "
            "only non-casefold ext4 can be approved"
        )


def _raw_flags(fd: int) -> int:
    buffer = array.array("l", [0])
    try:
        fcntl.ioctl(fd, FS_IOC_GETFLAGS, buffer, True)
    except OSError as caught:
        if caught.errno == errno.ENOTTY:
            raise CapabilityUnavailable(
                "FS_IOC_GETFLAGS is not implemented for this directory; "
                "the engine cannot determine its lookup relation"
            ) from caught
        raise
    return buffer[0]


def _casefold_flag(fd: int) -> bool:
    return bool(_raw_flags(fd) & FS_CASEFOLD_FL)


def _name_max(fd: int) -> int:
    value = os.fpathconf(fd, "PC_NAME_MAX")
    if value <= 0:
        raise CapabilityUnavailable(
            f"PC_NAME_MAX is indeterminate ({value}); the engine cannot bound component names"
        )
    return value


def read_lookup_constraints(fd: int, filesystem_type: str) -> DirectoryConstraints:
    """Observe one directory's lookup constraints through a held descriptor.

    Dispatch precedes the ioctl so ext4 flag semantics are never applied to a
    filesystem that does not define them.
    """
    _require_supported(filesystem_type)
    proof = (
        LookupProof.UNREPRODUCIBLE_CASEFOLD
        if _casefold_flag(fd)
        else LookupProof.EXACT_BYTES
    )
    return DirectoryConstraints(lookup_proof=proof, name_max=_name_max(fd))


def inherited_constraints(
    parent: DirectoryConstraints, filesystem_type: str
) -> DirectoryConstraints:
    """Constraints a directory created under `parent` will carry.

    ext4 inherits the casefold flag, so the parent's proof carries down unchanged; a
    directory that does not exist yet cannot be queried for PC_NAME_MAX, so the bound
    is ext4's documented 255-byte filename limit.
    """
    _require_supported(filesystem_type)
    return DirectoryConstraints(
        lookup_proof=parent.lookup_proof, name_max=EXT4_NAME_MAX
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fs_lookup.py -q`
Expected: PASS.

Run: `uv run ruff check && uv run pyright`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/atoms/core/errors.py src/atoms/fs/lookup.py tests/test_fs_lookup.py
git commit -m "feat(fs): read per-directory lookup constraints"
```

---

## Task 2: Resolver value types and construction

**Files:**
- Create: `src/atoms/fs/resolve.py`
- Create: `tests/test_fs_resolve_construction.py`

**Interfaces:**
- Consumes: Task 1's `LookupProof`, `DirectoryConstraints`, `read_lookup_constraints`;
  A4a's `ProjectBinding` (`backend`, `project_root_fd`, `metadata_root_fd`, `evidence`).
- Produces: `FilesystemIdentity(device: int, inode: int)`;
  `DirectoryFacts(identity: FilesystemIdentity, constraints: DirectoryConstraints)`;
  `EntryKind` (`DIRECTORY` | `REGULAR_FILE` | `SYMLINK` | `OTHER`); `AbsentFrontier()`;
  `PresentFrontier(identity, kind)`; `Frontier` union; `ResolvedHop(declared_component, facts)`;
  `ResolvedPrefix(root, hops, frontier_name, frontier, remainder)` with a
  `deepest_constraints` property; `PathResolver(binding)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fs_resolve_construction.py`:

```python
"""PathResolver construction: liveness, dispatch, and root refusals (design §6.1)."""

from __future__ import annotations

import dataclasses
import os

import pytest

from atoms.core.errors import (
    CapabilityUnavailable,
    ProjectApprovalRefused,
    ProtocolError,
)
from atoms.fs.lookup import DirectoryConstraints, LookupProof
from atoms.fs.resolve import (
    AbsentFrontier,
    DirectoryFacts,
    FilesystemIdentity,
    PathResolver,
    ResolvedHop,
    ResolvedPrefix,
)


def test_construction_succeeds_on_a_plain_ext4_project_root(bound_volume):
    with bound_volume() as binding:
        resolver = PathResolver(binding)
        assert resolver is not None


def test_a_closed_binding_refuses_construction(bound_volume):
    with bound_volume() as binding:
        pass
    with pytest.raises(ProtocolError):
        PathResolver(binding)


def test_liveness_is_checked_before_any_detached_evidence_is_read(
    monkeypatch, bound_volume
):
    """A closed binding must fail on liveness, never on an evidence-derived refusal.

    `evidence` is a detached value whose property performs no liveness check, so
    reading it first would let a closed binding produce a CapabilityUnavailable.
    """
    with bound_volume() as binding:
        pass
    monkeypatch.setattr(
        type(binding),
        "evidence",
        property(lambda self: pytest.fail("evidence read before the liveness gate")),
    )
    with pytest.raises(ProtocolError):
        PathResolver(binding)


def test_a_non_linux_backend_id_refuses(monkeypatch, bound_volume):
    with bound_volume() as binding:
        configuration = dataclasses.replace(
            binding.evidence.configuration, backend_id="darwin"
        )
        evidence = _evidence_with(binding.evidence, configuration=configuration)
        monkeypatch.setattr(type(binding), "evidence", property(lambda self: evidence))
        with pytest.raises(CapabilityUnavailable) as caught:
            PathResolver(binding)
        assert "darwin" in str(caught.value)


@pytest.mark.parametrize("filesystem_type", ["xfs", "btrfs", "ext2"])
def test_a_non_ext4_filesystem_refuses(monkeypatch, bound_volume, filesystem_type):
    with bound_volume() as binding:
        configuration = dataclasses.replace(
            binding.evidence.configuration, filesystem_type=filesystem_type
        )
        evidence = _evidence_with(binding.evidence, configuration=configuration)
        monkeypatch.setattr(type(binding), "evidence", property(lambda self: evidence))
        with pytest.raises(CapabilityUnavailable) as caught:
            PathResolver(binding)
        assert filesystem_type in str(caught.value)


def test_a_casefold_project_root_refuses(monkeypatch, bound_volume):
    with bound_volume() as binding:
        monkeypatch.setattr(
            "atoms.fs.resolve.read_lookup_constraints",
            lambda fd, filesystem_type: DirectoryConstraints(
                lookup_proof=LookupProof.UNREPRODUCIBLE_CASEFOLD, name_max=255
            ),
        )
        with pytest.raises(ProjectApprovalRefused) as caught:
            PathResolver(binding)
        assert "casefold" in str(caught.value).lower()


def test_a_project_root_identical_to_the_metadata_root_refuses(
    monkeypatch, bound_volume
):
    with bound_volume() as binding:
        info = os.fstat(binding.project_root_fd)
        evidence = _evidence_with(
            binding.evidence,
            metadata_root_device=info.st_dev,
            metadata_root_inode=info.st_ino,
        )
        monkeypatch.setattr(type(binding), "evidence", property(lambda self: evidence))
        with pytest.raises(ProjectApprovalRefused) as caught:
            PathResolver(binding)
        assert "metadata root" in str(caught.value)


@pytest.mark.parametrize("value", [-1, 0, -17])
def test_nonpositive_path_max_refuses(monkeypatch, bound_volume, value):
    with bound_volume() as binding:
        monkeypatch.setattr(
            "atoms.fs.resolve.os.fpathconf",
            lambda fd, name: value if name == "PC_PATH_MAX" else 255,
        )
        with pytest.raises(CapabilityUnavailable) as caught:
            PathResolver(binding)
        assert "PC_PATH_MAX" in str(caught.value)


@pytest.mark.parametrize("code", [1, 5])
def test_unexpected_path_max_errors_propagate_unwrapped(monkeypatch, bound_volume, code):
    with bound_volume() as binding:

        def refuse(fd, name):
            raise OSError(code, "injected")

        monkeypatch.setattr("atoms.fs.resolve.os.fpathconf", refuse)
        with pytest.raises(OSError) as caught:
            PathResolver(binding)
        assert caught.value.errno == code
        assert not isinstance(caught.value, CapabilityUnavailable)


def test_identity_ignores_declared_spelling():
    left = FilesystemIdentity(device=1, inode=2)
    right = FilesystemIdentity(device=1, inode=2)
    assert left == right and hash(left) == hash(right)


def test_deepest_constraints_falls_back_to_the_root_when_there_are_no_hops():
    constraints = DirectoryConstraints(lookup_proof=LookupProof.EXACT_BYTES, name_max=255)
    root = DirectoryFacts(FilesystemIdentity(1, 2), constraints)
    prefix = ResolvedPrefix(
        root=root, hops=(), frontier_name="a", frontier=AbsentFrontier(), remainder=()
    )
    assert prefix.deepest_constraints is constraints


def test_deepest_constraints_uses_the_last_hop_when_there_are_hops():
    root_constraints = DirectoryConstraints(
        lookup_proof=LookupProof.EXACT_BYTES, name_max=255
    )
    deep_constraints = DirectoryConstraints(
        lookup_proof=LookupProof.EXACT_BYTES, name_max=14
    )
    root = DirectoryFacts(FilesystemIdentity(1, 2), root_constraints)
    hop = ResolvedHop("a", DirectoryFacts(FilesystemIdentity(1, 3), deep_constraints))
    prefix = ResolvedPrefix(
        root=root, hops=(hop,), frontier_name="b", frontier=AbsentFrontier(), remainder=()
    )
    assert prefix.deepest_constraints is deep_constraints


def _evidence_with(evidence, **changes):
    """A stand-in VolumeEvidence: the real one refuses construction without its token."""
    fields = {
        "configuration": evidence.configuration,
        "declared_storage_profile": evidence.declared_storage_profile,
        "matched_entry": evidence.matched_entry,
        "supplied_capabilities": evidence.supplied_capabilities,
        "metadata_root_device": evidence.metadata_root_device,
        "metadata_root_inode": evidence.metadata_root_inode,
        "mount_id": evidence.mount_id,
    }
    fields.update(changes)
    return _StandInEvidence(**fields)


@dataclasses.dataclass(frozen=True, slots=True)
class _StandInEvidence:
    configuration: object
    declared_storage_profile: object
    matched_entry: object
    supplied_capabilities: object
    metadata_root_device: int
    metadata_root_inode: int
    mount_id: int
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_resolve_construction.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'atoms.fs.resolve'`.

- [ ] **Step 3: Write the value types and constructor**

Create `src/atoms/fs/resolve.py`:

```python
"""Anchored rooted path resolution (A4b-1 design §6).

Observation only. Nothing here sees a CompiledSpec, builds a topology, or writes to
project space; A4b-2 composes these observations into the approval proof.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from enum import Enum

from atoms.core.errors import (
    CapabilityUnavailable,
    ProjectApprovalRefused,
)
from atoms.fs.binding import ProjectBinding
from atoms.fs.lookup import DirectoryConstraints, LookupProof, read_lookup_constraints

_LINUX = "linux"


@dataclass(frozen=True, slots=True)
class FilesystemIdentity:
    """A directory or entry's identity. Deliberately excludes declared spelling, so
    two spellings reaching one directory compare equal."""

    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class DirectoryFacts:
    identity: FilesystemIdentity
    constraints: DirectoryConstraints


class EntryKind(Enum):
    DIRECTORY = "directory"
    REGULAR_FILE = "regular_file"
    SYMLINK = "symlink"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class AbsentFrontier:
    """Nothing occupies the frontier name."""


@dataclass(frozen=True, slots=True)
class PresentFrontier:
    identity: FilesystemIdentity
    kind: EntryKind


Frontier = AbsentFrontier | PresentFrontier


@dataclass(frozen=True, slots=True)
class ResolvedHop:
    declared_component: str
    facts: DirectoryFacts


@dataclass(frozen=True, slots=True)
class ResolvedPrefix:
    """The deepest existing directory prefix of one declared path.

    `remainder == ()` means `frontier` describes the declared leaf. Otherwise
    `remainder` holds every declared component after the blocking frontier, ending
    with the leaf.
    """

    root: DirectoryFacts
    hops: tuple[ResolvedHop, ...]
    frontier_name: str
    frontier: Frontier
    remainder: tuple[str, ...]

    @property
    def deepest_constraints(self) -> DirectoryConstraints:
        """Derived rather than stored, so it cannot drift out of step with `hops`."""
        if self.hops:
            return self.hops[-1].facts.constraints
        return self.root.constraints


def _identity(info: os.stat_result) -> FilesystemIdentity:
    return FilesystemIdentity(device=info.st_dev, inode=info.st_ino)


def _entry_kind(mode: int) -> EntryKind:
    if stat.S_ISDIR(mode):
        return EntryKind.DIRECTORY
    if stat.S_ISREG(mode):
        return EntryKind.REGULAR_FILE
    if stat.S_ISLNK(mode):
        return EntryKind.SYMLINK
    return EntryKind.OTHER


def _path_max(fd: int) -> int:
    value = os.fpathconf(fd, "PC_PATH_MAX")
    if value <= 0:
        raise CapabilityUnavailable(
            f"PC_PATH_MAX is indeterminate ({value}); the engine cannot bound path lengths"
        )
    return value


class PathResolver:
    """Approval-scoped. Constructed inside approve_for_project and nowhere else.

    Owns no descriptors between calls: every descriptor it opens is released before
    the call returns, so it needs no context manager, spent flag, or release
    discipline of its own. The memo is a plain dict keyed by directory identity.
    """

    __slots__ = (
        "_binding",
        "_facts_by_identity",
        "_filesystem_type",
        "_metadata_identity",
        "_path_max",
        "_root_facts",
        "_work_base",
    )

    def __init__(self, binding: ProjectBinding) -> None:
        # Liveness gate. project_root_fd routes through ProjectBinding._require_active,
        # which checks the binding's own flag AND lock.held. It is read before
        # `evidence`, a detached value whose property performs no such check. One
        # checked property is sufficient here; resolve() and work_base_facts() each
        # read the properties they need behind the same gate.
        root_fd = binding.project_root_fd
        evidence = binding.evidence
        configuration = evidence.configuration
        if configuration.backend_id != _LINUX:
            raise CapabilityUnavailable(
                f"backend {configuration.backend_id!r} is not {_LINUX!r}; "
                "lookup constraints are read with Linux ext4 flag semantics"
            )
        self._binding = binding
        self._filesystem_type = configuration.filesystem_type
        self._metadata_identity = FilesystemIdentity(
            device=evidence.metadata_root_device, inode=evidence.metadata_root_inode
        )
        self._path_max = _path_max(root_fd)
        identity = _identity(os.fstat(root_fd))
        if identity == self._metadata_identity:
            raise ProjectApprovalRefused(
                "the project root and the metadata root are the same directory; "
                "no declared path could avoid the metadata namespace"
            )
        constraints = read_lookup_constraints(root_fd, self._filesystem_type)
        if constraints.lookup_proof is LookupProof.UNREPRODUCIBLE_CASEFOLD:
            raise ProjectApprovalRefused(
                "the project root is a casefold directory; its lookup relation "
                "cannot be reproduced, so no path beneath it can be approved"
            )
        self._root_facts = DirectoryFacts(identity, constraints)
        self._facts_by_identity: dict[FilesystemIdentity, DirectoryFacts] = {
            identity: self._root_facts
        }
        self._work_base: DirectoryFacts | None = None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fs_resolve_construction.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/atoms/fs/resolve.py tests/test_fs_resolve_construction.py
git commit -m "feat(fs): construct an approval-scoped path resolver"
```

---

## Task 3: Entry observation

**Files:**
- Modify: `src/atoms/fs/resolve.py`
- Create: `tests/test_fs_resolve_walk.py`

**Interfaces:**
- Consumes: A4a's `read_mount_id`, `close_all`; Task 2's types.
- Produces: `PathResolver._observe(parent_fd: int, name: str, rel_path: str) -> Frontier`,
  which observes one entry through `O_PATH | O_NOFOLLOW | O_CLOEXEC`, checks metadata-root
  identity and mount membership, and releases the descriptor before returning.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fs_resolve_walk.py` with the observation cases (the walk cases arrive in Task 4):

```python
"""Entry observation and the anchored walk (design §6.3-§6.4)."""

from __future__ import annotations

import errno
import os

import pytest

from atoms.core.errors import (
    PreconditionRefused,
    ProjectApprovalRefused,
    ProtocolError,
)
from atoms.fs.resolve import AbsentFrontier, EntryKind, PathResolver, PresentFrontier


@pytest.fixture
def resolver_on(bound_volume):
    """A resolver plus the project root path its fixtures should populate."""
    import contextlib

    @contextlib.contextmanager
    def build():
        with bound_volume() as binding:
            yield PathResolver(binding), binding

    return build


def test_an_absent_entry_is_reported_absent(resolver_on, project_root):
    with resolver_on() as (resolver, binding):
        frontier = resolver._observe(binding.project_root_fd, "missing", "missing")
        assert isinstance(frontier, AbsentFrontier)


def test_a_regular_file_is_observed_with_its_identity(resolver_on, project_root):
    (project_root / "plain").write_text("x")
    with resolver_on() as (resolver, binding):
        frontier = resolver._observe(binding.project_root_fd, "plain", "plain")
        info = os.lstat(project_root / "plain")
        assert isinstance(frontier, PresentFrontier)
        assert frontier.kind is EntryKind.REGULAR_FILE
        assert (frontier.identity.device, frontier.identity.inode) == (
            info.st_dev,
            info.st_ino,
        )


def test_a_symlink_is_observed_rather_than_followed(resolver_on, project_root):
    (project_root / "target").write_text("x")
    os.symlink("target", project_root / "alias")
    with resolver_on() as (resolver, binding):
        frontier = resolver._observe(binding.project_root_fd, "alias", "alias")
        assert frontier.kind is EntryKind.SYMLINK
        assert frontier.identity.inode == os.lstat(project_root / "alias").st_ino


def test_a_directory_leaf_is_observed_without_being_opened(resolver_on, project_root):
    (project_root / "child").mkdir()
    with resolver_on() as (resolver, binding):
        frontier = resolver._observe(binding.project_root_fd, "child", "child")
        assert frontier.kind is EntryKind.DIRECTORY


def test_a_fifo_is_observed_as_other(resolver_on, project_root):
    os.mkfifo(project_root / "pipe")
    with resolver_on() as (resolver, binding):
        frontier = resolver._observe(binding.project_root_fd, "pipe", "pipe")
        assert frontier.kind is EntryKind.OTHER


def test_an_entry_whose_identity_is_the_metadata_root_refuses(
    resolver_on, project_root
):
    """Identity, not spelling: an entry matching the metadata root must refuse."""
    from atoms.fs.resolve import FilesystemIdentity

    (project_root / "sentinel").write_text("x")
    info = os.lstat(project_root / "sentinel")
    with resolver_on() as (resolver, binding):
        resolver._metadata_identity = FilesystemIdentity(
            device=info.st_dev, inode=info.st_ino
        )
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver._observe(binding.project_root_fd, "sentinel", "sentinel")
        assert "metadata root" in str(caught.value)


def test_an_entry_on_a_different_mount_refuses(monkeypatch, resolver_on, project_root):
    """A bind mount can share st_dev while carrying a distinct mount id, which is
    exactly why the observation goes through O_PATH and read_mount_id."""
    (project_root / "plain").write_text("x")
    with resolver_on() as (resolver, binding):
        monkeypatch.setattr(
            "atoms.fs.resolve.read_mount_id", lambda fd: binding.evidence.mount_id + 1
        )
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver._observe(binding.project_root_fd, "plain", "plain")
        assert "mount" in str(caught.value)


def test_observation_releases_its_descriptor_on_the_success_path(
    resolver_on, project_root
):
    (project_root / "plain").write_text("x")
    with resolver_on() as (resolver, binding):
        before = len(os.listdir("/proc/self/fd"))
        resolver._observe(binding.project_root_fd, "plain", "plain")
        assert len(os.listdir("/proc/self/fd")) == before


def test_observation_releases_its_descriptor_on_the_refusal_path(
    monkeypatch, resolver_on, project_root
):
    (project_root / "plain").write_text("x")
    with resolver_on() as (resolver, binding):
        monkeypatch.setattr(
            "atoms.fs.resolve.read_mount_id", lambda fd: binding.evidence.mount_id + 1
        )
        before = len(os.listdir("/proc/self/fd"))
        with pytest.raises(ProjectApprovalRefused):
            resolver._observe(binding.project_root_fd, "plain", "plain")
        assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO])
def test_unexpected_observation_errors_propagate_unwrapped(
    monkeypatch, resolver_on, code
):
    with resolver_on() as (resolver, binding):
        real_open = os.open

        def refuse(name, flags, *args, **kwargs):
            if flags & os.O_PATH:
                raise OSError(code, "injected")
            return real_open(name, flags, *args, **kwargs)

        monkeypatch.setattr("atoms.fs.resolve.os.open", refuse)
        with pytest.raises(OSError) as caught:
            resolver._observe(binding.project_root_fd, "anything", "anything")
        assert caught.value.errno == code
        assert not isinstance(caught.value, ProjectApprovalRefused)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_resolve_walk.py -q`
Expected: FAIL with `AttributeError: 'PathResolver' object has no attribute '_observe'`.

- [ ] **Step 3: Implement observation**

**Replace** the `atoms.core.errors` import block in `src/atoms/fs/resolve.py` with the four-name
version below, and add the two new imports and `import errno`:

```python
from atoms.core.errors import (
    CapabilityUnavailable,
    PreconditionRefused,
    ProjectApprovalRefused,
    ProtocolError,
)
from atoms.fs.lock import close_all
from atoms.fs.volume import read_mount_id
```

Add to `PathResolver`:

```python
    _OBSERVE_FLAGS = os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC

    def _observe(self, parent_fd: int, name: str, rel_path: str) -> Frontier:
        """Observe one entry coherently: kind, identity, and mount membership.

        O_PATH | O_NOFOLLOW returns the entry itself rather than following a symlink,
        and fdinfo answers for the same descriptor that fstat did — lstat could not
        see a bind mount that shares st_dev with its source.
        """
        try:
            fd = os.open(name, self._OBSERVE_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError:
            return AbsentFrontier()
        except OSError as caught:
            if caught.errno == errno.ENAMETOOLONG:
                raise ProjectApprovalRefused(
                    f"component {name!r} of {rel_path!r} exceeds the filesystem name limit"
                ) from caught
            raise
        try:
            info = os.fstat(fd)
            identity = _identity(info)
            if identity == self._metadata_identity:
                raise ProjectApprovalRefused(
                    f"{rel_path!r} resolves to the metadata root by identity "
                    f"(device {identity.device}, inode {identity.inode})"
                )
            mount = read_mount_id(fd)
            expected = self._binding.evidence.mount_id
            if mount != expected:
                raise ProjectApprovalRefused(
                    f"component {name!r} of {rel_path!r} is on mount {mount}, "
                    f"not the bound volume's mount {expected}"
                )
        finally:
            close_all((fd,))
        return PresentFrontier(identity, _entry_kind(info.st_mode))
```

Add `import errno` to the module imports.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fs_resolve_walk.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/atoms/fs/resolve.py tests/test_fs_resolve_walk.py
git commit -m "feat(fs): observe entries coherently through O_PATH"
```

---

## Task 4: The anchored walk

**Files:**
- Modify: `src/atoms/fs/resolve.py`
- Modify: `tests/test_fs_resolve_walk.py`

**Interfaces:**
- Consumes: `require_rel_path` from `atoms.core.paths`; Task 3's `_observe`;
  A4a's `open_child_directory`.
- Produces: `PathResolver.resolve(rel_path: str) -> ResolvedPrefix`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fs_resolve_walk.py`:

```python
def test_a_fully_existing_chain_resolves_with_an_empty_remainder(
    resolver_on, project_root
):
    (project_root / "a" / "b").mkdir(parents=True)
    (project_root / "a" / "b" / "leaf").write_text("x")
    with resolver_on() as (resolver, _):
        prefix = resolver.resolve("a/b/leaf")
        assert [hop.declared_component for hop in prefix.hops] == ["a", "b"]
        assert prefix.frontier_name == "leaf"
        assert prefix.remainder == ()
        assert prefix.frontier.kind is EntryKind.REGULAR_FILE


def test_an_absent_leaf_yields_an_absent_frontier(resolver_on, project_root):
    (project_root / "a").mkdir()
    with resolver_on() as (resolver, _):
        prefix = resolver.resolve("a/missing")
        assert prefix.remainder == ()
        assert prefix.frontier_name == "missing"
        assert isinstance(prefix.frontier, AbsentFrontier)


def test_an_absent_ancestor_stops_the_walk_and_keeps_the_remainder(
    resolver_on, project_root
):
    with resolver_on() as (resolver, _):
        prefix = resolver.resolve("a/b/leaf")
        assert prefix.hops == ()
        assert prefix.frontier_name == "a"
        assert prefix.remainder == ("b", "leaf")
        assert isinstance(prefix.frontier, AbsentFrontier)


@pytest.mark.parametrize(
    ("maker", "kind"),
    [
        (lambda base: base.write_text("x"), EntryKind.REGULAR_FILE),
        (lambda base: os.mkfifo(base), EntryKind.OTHER),
    ],
)
def test_a_non_directory_ancestor_becomes_a_blocking_frontier(
    resolver_on, project_root, maker, kind
):
    maker(project_root / "a")
    with resolver_on() as (resolver, _):
        prefix = resolver.resolve("a/b/leaf")
        assert prefix.hops == ()
        assert prefix.frontier_name == "a"
        assert prefix.remainder == ("b", "leaf")
        assert prefix.frontier.kind is kind


def test_a_symlink_ancestor_becomes_a_symlink_blocking_frontier(
    resolver_on, project_root
):
    (project_root / "real").mkdir()
    os.symlink("real", project_root / "a")
    with resolver_on() as (resolver, _):
        prefix = resolver.resolve("a/b")
        assert prefix.frontier_name == "a"
        assert prefix.remainder == ("b",)
        assert prefix.frontier.kind is EntryKind.SYMLINK


def test_an_errno_the_observation_contradicts_is_reported_as_drift(
    monkeypatch, resolver_on, project_root
):
    """ENOTDIR with a directory actually present means the entry changed between
    the two calls; that is drift, not a frontier."""
    (project_root / "a").mkdir()
    with resolver_on() as (resolver, binding):
        backend = binding.backend
        real_open_child = backend.open_child_directory

        def refuse(parent_fd, name):
            if name == "a":
                raise OSError(errno.ENOTDIR, "injected")
            return real_open_child(parent_fd, name)

        monkeypatch.setattr(type(backend), "open_child_directory", staticmethod(refuse))
        with pytest.raises(PreconditionRefused) as caught:
            resolver.resolve("a/b")
        assert "changed" in str(caught.value) or "drift" in str(caught.value).lower()


@pytest.mark.parametrize("bad", ["/absolute", "a/../b", ".", "a/", "a//b", "a/\x00b"])
def test_a_malformed_path_is_an_internal_contract_violation(resolver_on, bad):
    with resolver_on() as (resolver, _):
        with pytest.raises(ProtocolError):
            resolver.resolve(bad)


def test_a_malformed_path_is_rejected_before_any_syscall(
    monkeypatch, resolver_on
):
    """The EXDEV interpretation depends on no escape route reaching openat2."""
    with resolver_on() as (resolver, binding):
        calls = []
        backend = binding.backend
        monkeypatch.setattr(
            type(backend),
            "open_child_directory",
            staticmethod(lambda parent_fd, name: calls.append(name)),
        )
        with pytest.raises(ProtocolError):
            resolver.resolve("a/../b")
        assert calls == []


def test_a_component_over_name_max_refuses(resolver_on):
    with resolver_on() as (resolver, _):
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver.resolve("a" * 256)
        assert "NAME_MAX" in str(caught.value) or "name limit" in str(caught.value)


def test_a_path_over_path_max_refuses(resolver_on):
    with resolver_on() as (resolver, _):
        long_path = "/".join(["a" * 200] * 30)
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver.resolve(long_path)
        assert "PATH_MAX" in str(caught.value)


def test_limits_are_measured_in_encoded_bytes_not_characters(resolver_on):
    """A 200-character UTF-8 name can exceed a 255-byte bound."""
    with resolver_on() as (resolver, _):
        name = "é" * 200  # 400 bytes when encoded
        assert len(name) < 255 < len(os.fsencode(name))
        with pytest.raises(ProjectApprovalRefused):
            resolver.resolve(name)


def test_one_constraints_read_serves_every_path_through_a_directory(
    monkeypatch, resolver_on, project_root
):
    """The memo must be populated, not merely consulted."""
    (project_root / "a" / "b").mkdir(parents=True)
    (project_root / "a" / "c").mkdir()
    with resolver_on() as (resolver, _):
        import atoms.fs.resolve as module

        real = module.read_lookup_constraints
        calls = []

        def counted(fd, filesystem_type):
            calls.append(os.fstat(fd).st_ino)
            return real(fd, filesystem_type)

        monkeypatch.setattr(module, "read_lookup_constraints", counted)
        resolver.resolve("a/b/x")
        resolver.resolve("a/c/y")
        assert len(calls) == len(set(calls))
        assert calls.count(os.stat(project_root / "a").st_ino) == 1


def test_a_casefold_directory_mid_walk_refuses(monkeypatch, resolver_on, project_root):
    from atoms.fs.lookup import DirectoryConstraints, LookupProof

    (project_root / "a").mkdir()
    with resolver_on() as (resolver, _):
        target = os.stat(project_root / "a").st_ino
        import atoms.fs.resolve as module

        real = module.read_lookup_constraints

        def folded(fd, filesystem_type):
            if os.fstat(fd).st_ino == target:
                return DirectoryConstraints(
                    lookup_proof=LookupProof.UNREPRODUCIBLE_CASEFOLD, name_max=255
                )
            return real(fd, filesystem_type)

        monkeypatch.setattr(module, "read_lookup_constraints", folded)
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver.resolve("a/b")
        assert "casefold" in str(caught.value).lower()


def test_resolution_after_the_binding_closes_refuses(resolver_on):
    with resolver_on() as (resolver, _):
        pass
    with pytest.raises(ProtocolError):
        resolver.resolve("anything")


def test_the_walk_leaks_no_descriptor_on_the_success_path(resolver_on, project_root):
    (project_root / "a" / "b" / "c").mkdir(parents=True)
    with resolver_on() as (resolver, _):
        before = len(os.listdir("/proc/self/fd"))
        resolver.resolve("a/b/c/leaf")
        assert len(os.listdir("/proc/self/fd")) == before


def test_the_walk_leaks_no_descriptor_on_a_refusal_path(
    monkeypatch, resolver_on, project_root
):
    (project_root / "a" / "b").mkdir(parents=True)
    with resolver_on() as (resolver, binding):
        monkeypatch.setattr(
            "atoms.fs.resolve.read_mount_id", lambda fd: binding.evidence.mount_id + 1
        )
        before = len(os.listdir("/proc/self/fd"))
        with pytest.raises(ProjectApprovalRefused):
            resolver.resolve("a/b/leaf")
        assert len(os.listdir("/proc/self/fd")) == before
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_resolve_walk.py -q`
Expected: FAIL with `AttributeError: 'PathResolver' object has no attribute 'resolve'`.

- [ ] **Step 3: Implement the walk**

Add to `src/atoms/fs/resolve.py`:

```python
from atoms.core.paths import require_rel_path
from atoms.core.errors import SpecValidationError
```

and to `PathResolver`:

```python
    _BLOCKER_KINDS = {
        errno.ENOTDIR: (EntryKind.REGULAR_FILE, EntryKind.OTHER),
        errno.ELOOP: (EntryKind.SYMLINK,),
    }

    def resolve(self, rel_path: str) -> ResolvedPrefix:
        """Observe the deepest existing directory prefix of one declared path.

        Accepts declared persistent paths only. A4b-2 resolves a scratch parent and
        validates generated leaf names against that parent's approved name_max.
        """
        backend = self._binding.backend
        root_fd = self._binding.project_root_fd
        try:
            require_rel_path("path", rel_path)
        except SpecValidationError as caught:
            raise ProtocolError(
                f"resolve() requires a well-formed project-relative path: {caught}"
            ) from caught
        encoded = len(os.fsencode(rel_path)) + 1
        if encoded > self._path_max:
            raise ProjectApprovalRefused(
                f"path {rel_path!r} needs {encoded} bytes including the terminating NUL, "
                f"over the volume PATH_MAX of {self._path_max}"
            )

        components = rel_path.split("/")
        ancestors, leaf = components[:-1], components[-1]
        facts = self._root_facts
        parent_fd = root_fd
        owned: int | None = None
        hops: list[ResolvedHop] = []
        try:
            for index, component in enumerate(ancestors):
                self._require_name_fits(component, facts, rel_path)
                try:
                    child = backend.open_child_directory(parent_fd, component)
                except OSError as caught:
                    return ResolvedPrefix(
                        root=self._root_facts,
                        hops=tuple(hops),
                        frontier_name=component,
                        frontier=self._frontier_from(
                            caught, parent_fd, component, rel_path
                        ),
                        remainder=tuple(components[index + 1 :]),
                    )
                # Reassign before releasing, so a failing close cannot strand `child`.
                previous, owned = owned, child
                parent_fd = child
                if previous is not None:
                    close_all((previous,))
                facts = self._facts_for(child, rel_path)
                hops.append(ResolvedHop(component, facts))

            self._require_name_fits(leaf, facts, rel_path)
            return ResolvedPrefix(
                root=self._root_facts,
                hops=tuple(hops),
                frontier_name=leaf,
                frontier=self._observe(parent_fd, leaf, rel_path),
                remainder=(),
            )
        finally:
            if owned is not None:
                close_all((owned,))

    def _require_name_fits(
        self, component: str, facts: DirectoryFacts, rel_path: str
    ) -> None:
        width = len(os.fsencode(component))
        if width > facts.constraints.name_max:
            raise ProjectApprovalRefused(
                f"component {component!r} of {rel_path!r} is {width} bytes, over its "
                f"parent's NAME_MAX of {facts.constraints.name_max}"
            )

    def _frontier_from(
        self, caught: OSError, parent_fd: int, component: str, rel_path: str
    ) -> Frontier:
        if caught.errno == errno.ENOENT:
            return AbsentFrontier()
        if caught.errno == errno.EXDEV:
            # RESOLVE_BENEATH and RESOLVE_NO_XDEV share EXDEV, but require_rel_path
            # rejected every escape spelling before any syscall and
            # RESOLVE_NO_SYMLINKS turns symlinks into ELOOP, so no escape reaches here.
            raise ProjectApprovalRefused(
                f"component {component!r} of {rel_path!r} crosses a mount boundary"
            ) from caught
        if caught.errno == errno.ENAMETOOLONG:
            raise ProjectApprovalRefused(
                f"component {component!r} of {rel_path!r} exceeds the filesystem name limit"
            ) from caught
        admissible = self._BLOCKER_KINDS.get(caught.errno)
        if admissible is None:
            raise caught
        observed = self._observe(parent_fd, component, rel_path)
        if isinstance(observed, AbsentFrontier) or observed.kind not in admissible:
            raise PreconditionRefused(
                f"component {component!r} of {rel_path!r} changed between the traversal "
                f"attempt and its observation; the engine will not assemble one "
                f"observation from two filesystem moments"
            ) from caught
        return observed

    def _facts_for(self, fd: int, rel_path: str) -> DirectoryFacts:
        identity = _identity(os.fstat(fd))
        if identity == self._metadata_identity:
            raise ProjectApprovalRefused(
                f"{rel_path!r} traverses the metadata root by identity "
                f"(device {identity.device}, inode {identity.inode})"
            )
        facts = self._facts_by_identity.get(identity)
        if facts is None:
            constraints = read_lookup_constraints(fd, self._filesystem_type)
            facts = DirectoryFacts(identity, constraints)
            self._facts_by_identity[identity] = facts
        if facts.constraints.lookup_proof is LookupProof.UNREPRODUCIBLE_CASEFOLD:
            raise ProjectApprovalRefused(
                f"{rel_path!r} traverses a casefold directory whose lookup relation "
                "cannot be reproduced"
            )
        return facts
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fs_resolve_walk.py -q`
Expected: PASS.

Run: `uv run ruff check && uv run pyright`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/atoms/fs/resolve.py tests/test_fs_resolve_walk.py
git commit -m "feat(fs): walk a declared path to its existing prefix"
```

---

## Task 5: Work-base facts

**Files:**
- Modify: `src/atoms/fs/bootstrap.py`
- Modify: `src/atoms/fs/resolve.py`
- Create: `tests/test_fs_resolve_work_base.py`

**Interfaces:**
- Consumes: `WORK_DIRECTORY` from `atoms.fs.bootstrap`.
- Produces: `PathResolver.work_base_facts() -> DirectoryFacts` — private to A4b, lazy,
  memoized on success only.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fs_resolve_work_base.py`:

```python
"""work_base_facts(): the existing metadata_root/work base only (design §6.6)."""

from __future__ import annotations

import contextlib
import errno
import os

import pytest

from atoms.core.errors import ProjectApprovalRefused, ProtocolError
from atoms.fs.resolve import PathResolver


@pytest.fixture
def resolver_on(bound_volume):
    @contextlib.contextmanager
    def build():
        with bound_volume() as binding:
            yield PathResolver(binding), binding

    return build


def test_construction_does_not_touch_the_work_base(monkeypatch, bound_volume):
    """Lazy: an unapprovable work/ must not refuse a spec with no CreateDirectory."""
    with bound_volume() as binding:
        calls = []
        backend = binding.backend
        real = backend.open_child_directory
        monkeypatch.setattr(
            type(backend),
            "open_child_directory",
            staticmethod(
                lambda parent_fd, name: calls.append(name) or real(parent_fd, name)
            ),
        )
        PathResolver(binding)
        assert "work" not in calls


def test_work_base_facts_reports_the_real_directory(resolver_on, metadata_root):
    with resolver_on() as (resolver, _):
        facts = resolver.work_base_facts()
        info = os.stat(metadata_root / "work")
        assert (facts.identity.device, facts.identity.inode) == (
            info.st_dev,
            info.st_ino,
        )


def test_repeated_calls_open_the_directory_once(monkeypatch, resolver_on):
    with resolver_on() as (resolver, binding):
        calls = []
        backend = binding.backend
        real = backend.open_child_directory
        monkeypatch.setattr(
            type(backend),
            "open_child_directory",
            staticmethod(
                lambda parent_fd, name: calls.append(name) or real(parent_fd, name)
            ),
        )
        first = resolver.work_base_facts()
        second = resolver.work_base_facts()
        assert first is second
        assert calls.count("work") == 1


def test_a_cached_result_still_fails_after_the_binding_closes(resolver_on):
    """Liveness is read before the memo, so memoization is not a bypass."""
    with resolver_on() as (resolver, _):
        resolver.work_base_facts()
    with pytest.raises(ProtocolError):
        resolver.work_base_facts()


def test_a_failing_release_is_not_cached(monkeypatch, resolver_on):
    """The cache is populated only after the descriptor is released."""
    with resolver_on() as (resolver, _):
        import atoms.fs.resolve as module

        real_close_all = module.close_all
        failures = []

        def failing(fds):
            failures.append(tuple(fds))
            real_close_all(fds)
            raise OSError(errno.EIO, "injected close failure")

        monkeypatch.setattr(module, "close_all", failing)
        with pytest.raises(OSError):
            resolver.work_base_facts()
        monkeypatch.setattr(module, "close_all", real_close_all)
        # The failed call cached nothing, so this one must re-open and succeed.
        assert resolver.work_base_facts() is not None
        assert len(failures) == 1


@pytest.mark.parametrize(
    "code", [errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EXDEV]
)
def test_a_namespace_contradiction_is_an_internal_error(
    monkeypatch, resolver_on, code
):
    with resolver_on() as (resolver, binding):
        backend = binding.backend

        def refuse(parent_fd, name):
            raise OSError(code, "injected")

        monkeypatch.setattr(type(backend), "open_child_directory", staticmethod(refuse))
        with pytest.raises(ProtocolError) as caught:
            resolver.work_base_facts()
        assert "work" in str(caught.value)


@pytest.mark.parametrize("code", [errno.EIO, errno.EMFILE, errno.EACCES])
def test_unrelated_system_failures_propagate_unchanged(monkeypatch, resolver_on, code):
    """EIO and EMFILE are not violated invariants and must not be relabelled."""
    with resolver_on() as (resolver, binding):
        backend = binding.backend

        def refuse(parent_fd, name):
            raise OSError(code, "injected")

        monkeypatch.setattr(type(backend), "open_child_directory", staticmethod(refuse))
        with pytest.raises(OSError) as caught:
            resolver.work_base_facts()
        assert caught.value.errno == code
        assert not isinstance(caught.value, ProtocolError)


def test_a_work_base_on_another_mount_is_an_internal_error(monkeypatch, resolver_on):
    with resolver_on() as (resolver, binding):
        monkeypatch.setattr(
            "atoms.fs.resolve.read_mount_id", lambda fd: binding.evidence.mount_id + 1
        )
        with pytest.raises(ProtocolError):
            resolver.work_base_facts()


def test_a_casefold_work_base_refuses(monkeypatch, resolver_on):
    from atoms.fs.lookup import DirectoryConstraints, LookupProof

    with resolver_on() as (resolver, _):
        monkeypatch.setattr(
            "atoms.fs.resolve.read_lookup_constraints",
            lambda fd, filesystem_type: DirectoryConstraints(
                lookup_proof=LookupProof.UNREPRODUCIBLE_CASEFOLD, name_max=255
            ),
        )
        with pytest.raises(ProjectApprovalRefused):
            resolver.work_base_facts()


def test_every_failure_path_releases_the_descriptor(monkeypatch, resolver_on):
    with resolver_on() as (resolver, binding):
        monkeypatch.setattr(
            "atoms.fs.resolve.read_mount_id", lambda fd: binding.evidence.mount_id + 1
        )
        before = len(os.listdir("/proc/self/fd"))
        with pytest.raises(ProtocolError):
            resolver.work_base_facts()
        assert len(os.listdir("/proc/self/fd")) == before
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_resolve_work_base.py -q`
Expected: FAIL with `AttributeError: 'PathResolver' object has no attribute 'work_base_facts'`.

- [ ] **Step 3: Add the layout constant**

In `src/atoms/fs/bootstrap.py`, beside `PROBE_DIRECTORY`:

```python
WORK_DIRECTORY = "work"
```

- [ ] **Step 4: Implement `work_base_facts`**

Add `from atoms.fs.bootstrap import WORK_DIRECTORY` to `resolve.py`, then to `PathResolver`:

```python
    _NAMESPACE_CONTRADICTIONS = frozenset(
        {errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EXDEV}
    )

    def work_base_facts(self) -> DirectoryFacts:
        """Facts for the existing metadata_root/work base.

        NOT A3's logical WorkRoot: authority §7 places effect-time staging in
        work/<txid>/, which does not exist at approval time. A4b-2 derives that
        directory's constraints from these through inherited_constraints.
        """
        backend = self._binding.backend  # liveness BEFORE the memo, so a cached
        parent_fd = self._binding.metadata_root_fd  # result still fails after closure
        if self._work_base is not None:
            return self._work_base
        try:
            fd = backend.open_child_directory(parent_fd, WORK_DIRECTORY)
        except OSError as caught:
            if caught.errno in self._NAMESPACE_CONTRADICTIONS:
                raise ProtocolError(
                    f"engine-owned metadata_root/{WORK_DIRECTORY} is missing or "
                    f"malformed: {caught}"
                ) from caught
            raise
        try:
            mount = read_mount_id(fd)
            expected = self._binding.evidence.mount_id
            if mount != expected:
                raise ProtocolError(
                    f"engine-owned metadata_root/{WORK_DIRECTORY} is on mount {mount}, "
                    f"not the bound volume's mount {expected}"
                )
            constraints = read_lookup_constraints(fd, self._filesystem_type)
            if constraints.lookup_proof is LookupProof.UNREPRODUCIBLE_CASEFOLD:
                raise ProjectApprovalRefused(
                    f"metadata_root/{WORK_DIRECTORY} is a casefold directory; its "
                    "lookup relation cannot be reproduced"
                )
            facts = DirectoryFacts(_identity(os.fstat(fd)), constraints)
        except BaseException:
            close_all((fd,))
            raise
        close_all((fd,))  # may raise; nothing is cached if it does
        self._work_base = facts
        return facts
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fs_resolve_work_base.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/atoms/fs/bootstrap.py src/atoms/fs/resolve.py tests/test_fs_resolve_work_base.py
git commit -m "feat(fs): observe the engine-owned work base"
```

---

## Task 6: Real-filesystem conformance

**Files:**
- Modify: `tests/fs_support.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_fs_resolve_conformance.py`

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: `ext4_volume_or_skip_reason()`, `descriptor_count()`,
  `user_namespaces_available()`, `run_in_bind_namespace(script)` in `tests/fs_support.py`;
  the `ext4_volume` fixture in `tests/conftest.py`.

- [ ] **Step 1: Add the fixtures and helpers**

Append to `tests/fs_support.py`:

```python
EXT4 = "ext4"


def ext4_volume_or_skip_reason() -> tuple[Path | None, str]:
    """A4b-1 approves only ext4, while A4a admits ext4, xfs, and btrfs.

    A contributor on btrfs must be told why this suite skips, not merely that it does.
    """
    resolved = resolve_test_volume()
    if resolved is None:
        return None, (
            "no test volume: set ATOMS_TEST_VOLUME to a directory on ext4"
        )
    found = _filesystem_type_for(resolved.parent if not resolved.exists() else resolved)
    if found != EXT4:
        return None, (
            f"A4b-1 approves only ext4; the test volume is {found!r}. "
            "Set ATOMS_TEST_VOLUME to a directory on ext4."
        )
    return resolved, ""


def descriptor_count() -> int:
    """Open descriptors for this process.

    The listing itself opens one descriptor, so this contributes a constant; only
    deltas between counts measured this way are meaningful.
    """
    return len(os.listdir("/proc/self/fd"))


def user_namespaces_available() -> bool:
    import subprocess

    try:
        completed = subprocess.run(
            ["unshare", "--mount", "--map-root-user", "--", "true"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def run_in_bind_namespace(script: str, *args: str):
    """Run `script` under `unshare --mount --map-root-user`.

    A bind mount inside the namespace shares st_dev with its source while carrying a
    distinct mount id, which is the pair no captured fixture can assert about.
    """
    import subprocess
    import sys

    return subprocess.run(
        [
            "unshare",
            "--mount",
            "--map-root-user",
            "--",
            sys.executable,
            "-c",
            script,
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
```

Append to `tests/conftest.py` (and extend the `tests.fs_support` import list):

```python
@pytest.fixture
def ext4_volume(tmp_path_factory):
    base, reason = ext4_volume_or_skip_reason()
    if base is None:
        pytest.skip(reason)
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as directory:
        yield Path(directory)
```

- [ ] **Step 2: Write the conformance tests**

Create `tests/test_fs_resolve_conformance.py`:

```python
"""Tier 3: A4b-1 against a real ext4 volume (design §9.3)."""

from __future__ import annotations

import os
import unicodedata

import pytest

from atoms.core.errors import ProjectApprovalRefused
from atoms.fs.lookup import LookupProof, inherited_constraints, read_lookup_constraints
from atoms.fs.resolve import EntryKind, PathResolver
from tests.fs_support import (
    descriptor_count,
    run_in_bind_namespace,
    user_namespaces_available,
)


@pytest.fixture
def probe_fd(ext4_volume):
    fd = os.open(ext4_volume, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    yield fd
    os.close(fd)


@pytest.mark.parametrize(
    ("made", "sought"),
    [
        ("a", "A"),
        (unicodedata.normalize("NFC", "é"), unicodedata.normalize("NFD", "é")),
        (unicodedata.normalize("NFD", "é"), unicodedata.normalize("NFC", "é")),
    ],
)
def test_a_plain_ext4_directory_compares_names_as_exact_bytes(
    ext4_volume, probe_fd, made, sought
):
    """EXACT_BYTES is a claim about the filesystem, so it is verified against one."""
    fd = os.open(made, os.O_CREAT | os.O_WRONLY, 0o600, dir_fd=probe_fd)
    os.close(fd)
    try:
        with pytest.raises(FileNotFoundError):
            os.stat(sought, dir_fd=probe_fd)
    finally:
        os.unlink(made, dir_fd=probe_fd)


def test_a_plain_ext4_directory_reports_exact_bytes(probe_fd):
    assert read_lookup_constraints(probe_fd, "ext4").lookup_proof is (
        LookupProof.EXACT_BYTES
    )


def test_a_created_directory_matches_the_inheritance_rule(ext4_volume, probe_fd):
    """Without this, §5.4's rule is only prose."""
    parent = read_lookup_constraints(probe_fd, "ext4")
    os.mkdir("child", mode=0o700, dir_fd=probe_fd)
    child_fd = os.open("child", os.O_RDONLY | os.O_DIRECTORY, dir_fd=probe_fd)
    try:
        observed = read_lookup_constraints(child_fd, "ext4")
    finally:
        os.close(child_fd)
    assert observed == inherited_constraints(parent, "ext4")


def test_a_255_byte_name_is_accepted_and_256_refuses(bound_volume, project_root):
    with bound_volume() as binding:
        resolver = PathResolver(binding)
        assert resolver.resolve("a" * 255).frontier_name == "a" * 255
        with pytest.raises(ProjectApprovalRefused):
            resolver.resolve("a" * 256)


def test_two_hard_links_share_an_identity_with_distinct_provenance(
    bound_volume, project_root
):
    """A4b-1 reports the shared identity; whether topology merges them is A4b-2's."""
    (project_root / "left").write_text("x")
    os.link(project_root / "left", project_root / "right")
    with bound_volume() as binding:
        resolver = PathResolver(binding)
        left = resolver.resolve("left")
        right = resolver.resolve("right")
    assert left.frontier.identity == right.frontier.identity
    assert left.frontier_name != right.frontier_name


def test_a_path_equal_to_the_metadata_root_refuses(bound_volume, metadata_root):
    with bound_volume() as binding:
        resolver = PathResolver(binding)
        relative = os.path.relpath(metadata_root, start=os.readlink(f"/proc/self/fd/{binding.project_root_fd}"))
        if relative.startswith(".."):
            pytest.skip("the metadata root is not inside the project root here")
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver.resolve(relative)
        assert "metadata root" in str(caught.value)


def test_a_path_beneath_the_metadata_root_refuses(bound_volume, metadata_root):
    with bound_volume() as binding:
        resolver = PathResolver(binding)
        root_path = os.readlink(f"/proc/self/fd/{binding.project_root_fd}")
        relative = os.path.relpath(metadata_root, start=root_path)
        if relative.startswith(".."):
            pytest.skip("the metadata root is not inside the project root here")
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver.resolve(f"{relative}/staging")
        assert "metadata root" in str(caught.value)


def test_the_descriptor_peak_is_three_at_any_depth(monkeypatch, bound_volume, project_root):
    """Every acquisition point samples immediately after it returns.

    Omitting the read_mount_id hook is what would let a false bound of two pass:
    its /proc/self/fdinfo handle coexists with the parent and the O_PATH descriptor.
    """
    import atoms.fs.volume as volume_module

    deep = project_root
    for index in range(512):
        deep = deep / f"d{index}"
    deep.mkdir(parents=True)
    shallow = project_root / "s0"
    shallow.mkdir()

    samples: list[int] = []
    real_open = os.open
    real_builtin_open = volume_module.open

    def sampling_open(*args, **kwargs):
        fd = real_open(*args, **kwargs)
        samples.append(descriptor_count())
        return fd

    def sampling_builtin_open(*args, **kwargs):
        handle = real_builtin_open(*args, **kwargs)
        samples.append(descriptor_count())
        return handle

    with bound_volume() as binding:
        resolver = PathResolver(binding)
        backend = binding.backend
        real_child = backend.open_child_directory

        def sampling_child(parent_fd, name):
            fd = real_child(parent_fd, name)
            samples.append(descriptor_count())
            return fd

        monkeypatch.setattr(
            type(backend), "open_child_directory", staticmethod(sampling_child)
        )
        monkeypatch.setattr("atoms.fs.resolve.os.open", sampling_open)
        monkeypatch.setattr(volume_module, "open", sampling_builtin_open)

        baseline = descriptor_count()
        samples.clear()
        resolver.resolve("s0/leaf")
        shallow_peak = max(samples) - baseline

        samples.clear()
        resolver.resolve("/".join(f"d{index}" for index in range(512)) + "/leaf")
        deep_peak = max(samples) - baseline

    assert shallow_peak == deep_peak == 3


@pytest.mark.skipif(
    not user_namespaces_available(), reason="user namespaces are unavailable"
)
def test_a_bind_mount_at_an_ancestor_refuses():
    script = """
import os, subprocess, sys, tempfile
sys.path.insert(0, os.environ["ATOMS_SRC"])
from atoms.fs.linux import LinuxBackend
base = tempfile.mkdtemp(dir=os.environ["ATOMS_VOLUME"])
os.mkdir(f"{base}/source"); os.mkdir(f"{base}/ancestor")
subprocess.run(["mount", "--bind", f"{base}/source", f"{base}/ancestor"], check=True)
fd = LinuxBackend().open_root(base)
try:
    LinuxBackend().open_child_directory(fd, "ancestor")
    print("NO_REFUSAL")
except OSError as caught:
    print(os.strerror(caught.errno), caught.errno)
finally:
    os.close(fd)
"""
    completed = run_in_bind_namespace(script)
    assert "18" in completed.stdout, completed.stderr  # EXDEV


@pytest.mark.skipif(
    not user_namespaces_available(), reason="user namespaces are unavailable"
)
def test_a_bind_mount_at_the_leaf_shares_st_dev_but_not_the_mount_id():
    """The test that justifies O_PATH + read_mount_id over lstat."""
    script = """
import os, subprocess, sys, tempfile
sys.path.insert(0, os.environ["ATOMS_SRC"])
from atoms.fs.volume import read_mount_id
base = tempfile.mkdtemp(dir=os.environ["ATOMS_VOLUME"])
os.mkdir(f"{base}/source"); os.mkdir(f"{base}/leaf")
subprocess.run(["mount", "--bind", f"{base}/source", f"{base}/leaf"], check=True)
left = os.open(f"{base}/source", os.O_PATH | os.O_NOFOLLOW)
right = os.open(f"{base}/leaf", os.O_PATH | os.O_NOFOLLOW)
print(os.fstat(left).st_dev == os.fstat(right).st_dev,
      read_mount_id(left) != read_mount_id(right))
os.close(left); os.close(right)
"""
    completed = run_in_bind_namespace(script)
    assert "True True" in completed.stdout, completed.stderr
```

- [ ] **Step 3: Wire the environment for the namespace children**

In `tests/fs_support.py`, extend `run_in_bind_namespace` to pass `ATOMS_SRC` and
`ATOMS_VOLUME` through `env`:

```python
    environment = dict(os.environ)
    environment["ATOMS_SRC"] = str(Path(__file__).parents[1] / "src")
    environment["ATOMS_VOLUME"] = str(resolve_test_volume())
```

and pass `env=environment` to `subprocess.run`.

- [ ] **Step 4: Run the suite**

Run: `uv run pytest tests/test_fs_resolve_conformance.py -q`
Expected: PASS, with the two bind-mount tests running rather than skipping on a host with
user namespaces.

- [ ] **Step 5: Commit**

```bash
git add tests/fs_support.py tests/conftest.py tests/test_fs_resolve_conformance.py
git commit -m "test(fs): verify resolution against a real ext4 volume"
```

---

## Task 7: Casefold tier, architecture guards, and documentation

**Files:**
- Modify: `tests/fs_support.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_fs_resolve_casefold.py`
- Modify: `tests/test_fs_architecture.py`
- Modify: `docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: everything from Tasks 1–6.
- Produces: `casefold_volume_or_reason()` in `tests/fs_support.py`; the `casefold_volume`
  fixture; the tier-5 architecture assertions.

- [ ] **Step 1: Add the casefold fixture**

Append to `tests/fs_support.py`:

```python
CASEFOLD_ENVIRONMENT = "ATOMS_CASEFOLD_VOLUME"


def casefold_volume_or_reason() -> tuple[Path | None, str, bool]:
    """Resolve the opt-in casefold volume.

    Returns (path, reason, is_error). An unset variable skips; an explicitly supplied
    variable that does not work is an error, because an opt-in that silently does
    nothing is worse than no opt-in at all.
    """
    declared = os.environ.get(CASEFOLD_ENVIRONMENT)
    if not declared:
        return None, (
            f"{CASEFOLD_ENVIRONMENT} is unset; see the A4b-1 design §9.4 for the "
            "one-time setup recipe"
        ), False
    base = Path(declared)
    if not base.is_dir():
        return None, f"{CASEFOLD_ENVIRONMENT}={declared!r} is not a directory", True
    found = _filesystem_type_for(base)
    if found != EXT4:
        return None, (
            f"{CASEFOLD_ENVIRONMENT}={declared!r} is {found!r}, not ext4"
        ), True
    if shutil.which("chattr") is None:
        return None, "chattr is not installed; the casefold tier cannot run", True
    return base, "", False
```

Add `import shutil` to the module imports. Append to `tests/conftest.py`:

```python
@pytest.fixture
def casefold_volume():
    base, reason, is_error = casefold_volume_or_reason()
    if base is None:
        if is_error:
            pytest.fail(reason)
        pytest.skip(reason)
    with tempfile.TemporaryDirectory(dir=base) as directory:
        yield Path(directory)
```

- [ ] **Step 2: Write the casefold tests**

Create `tests/test_fs_resolve_casefold.py`:

```python
"""Tier 4: the one thing injection cannot prove (design §9.4).

FS_CASEFOLD_FL corresponds to actual folding. Requires ATOMS_CASEFOLD_VOLUME.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from atoms.fs.lookup import LookupProof, inherited_constraints, read_lookup_constraints


@pytest.fixture
def mixed_policy(casefold_volume):
    """A folded directory and a plain sibling on one filesystem."""
    plain = casefold_volume / "plain"
    folded = casefold_volume / "folded"
    plain.mkdir()
    folded.mkdir()
    completed = subprocess.run(
        ["chattr", "+F", str(folded)], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        pytest.fail(
            f"chattr +F failed on {folded}: {completed.stderr.strip()}. "
            "The volume is probably not formatted with -O casefold."
        )
    return plain, folded


def test_the_folded_directory_really_folds(mixed_policy):
    _, folded = mixed_policy
    (folded / "a").write_text("x")
    assert (folded / "A").exists()


def test_the_plain_sibling_does_not_fold(mixed_policy):
    plain, _ = mixed_policy
    (plain / "a").write_text("x")
    assert not (plain / "A").exists()


def test_the_flag_and_the_behaviour_agree(mixed_policy):
    plain, folded = mixed_policy
    for path, expected in ((plain, LookupProof.EXACT_BYTES),
                           (folded, LookupProof.UNREPRODUCIBLE_CASEFOLD)):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            assert read_lookup_constraints(fd, "ext4").lookup_proof is expected
        finally:
            os.close(fd)


def test_a_directory_created_under_the_plain_sibling_inherits_exact_bytes(mixed_policy):
    """Stronger than a feature-less volume: the filesystem *can* casefold here."""
    plain, _ = mixed_policy
    (plain / "child").mkdir()
    parent_fd = os.open(plain, os.O_RDONLY | os.O_DIRECTORY)
    child_fd = os.open(plain / "child", os.O_RDONLY | os.O_DIRECTORY)
    try:
        parent = read_lookup_constraints(parent_fd, "ext4")
        assert read_lookup_constraints(child_fd, "ext4") == inherited_constraints(
            parent, "ext4"
        )
    finally:
        os.close(parent_fd)
        os.close(child_fd)


def test_the_casefold_flag_cannot_be_set_on_a_non_empty_directory(casefold_volume):
    """Backs §6.5: this is a distinct claim from the fold test, which sets +F while
    the directory is still empty."""
    occupied = casefold_volume / "occupied"
    occupied.mkdir()
    (occupied / "child").write_text("x")
    completed = subprocess.run(
        ["chattr", "+F", str(occupied)], capture_output=True, text=True, check=False
    )
    assert completed.returncode != 0
```

- [ ] **Step 3: Add the architecture guards**

Append to `tests/test_fs_architecture.py`:

```python
FORBIDDEN_FOR_RESOLUTION = ("atoms.core.compiler", "atoms.core.spec", "atoms.core.recovery")


@pytest.mark.parametrize("module_name", ["resolve", "lookup"])
def test_resolution_modules_judge_no_specification(module_name):
    """A dependency on any of these would mean the mechanism had begun judging."""
    source = (SOURCE_ROOT / "fs" / f"{module_name}.py").read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert not any(
        name.startswith(forbidden)
        for name in imported
        for forbidden in FORBIDDEN_FOR_RESOLUTION
    )


@pytest.mark.parametrize("name", ["PathResolver", "read_lookup_constraints"])
def test_resolution_internals_are_not_exported(name):
    import atoms.fs as package

    assert name not in package.__all__
    assert not hasattr(package, name)


@pytest.mark.parametrize("module_name", ["resolve", "lookup"])
def test_no_blanket_oserror_handler(module_name):
    """§7: exactly five pathname errnos plus ENOTTY are interpreted, nothing else."""
    source = (SOURCE_ROOT / "fs" / f"{module_name}.py").read_text()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ExceptHandler) or node.type is None:
            continue
        names = (
            [node.type] if not isinstance(node.type, ast.Tuple) else list(node.type.elts)
        )
        for entry in names:
            if isinstance(entry, ast.Name) and entry.id == "OSError":
                body = ast.dump(ast.Module(body=node.body, type_ignores=[]))
                assert "errno" in body, (
                    f"{module_name}.py catches OSError without discriminating on errno"
                )


def test_the_backend_protocol_gained_no_method():
    from atoms.fs.backend import Backend
    from atoms.fs.platform import BACKEND_REVISION

    assert BACKEND_REVISION == "linux-1"
    assert not hasattr(Backend, "read_lookup_constraints")
```

- [ ] **Step 4: Amend the authority design**

In `docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md` §11, after the
`PreconditionRefused` bullet, insert:

```markdown
- **`SpecValidationError`** — a `TransactionSpec` failed A2's pure lexical/model proof. Raised by
  `compile_spec` before any project context exists.
- **`ProjectApprovalRefused`** — the rooted project proof failed. Raised during approval, before any
  transaction-record or blob write and before any project mutation.
```

- [ ] **Step 5: Update the status note**

In `AGENTS.md`, replace the A4b bullet's first sentence with:

```markdown
- **A4b — rooted project approval: A4b-1 implemented, A4b-2 unimplemented.** A4b-1 owns the
  resolution mechanism in `atoms/fs/resolve.py` and `atoms/fs/lookup.py` — `PathResolver`,
  lookup-constraint reading, real-filesystem limits, containment, and mount membership — and sees no
  `CompiledSpec`.
```

- [ ] **Step 6: Run every gate**

Run: `uv run pytest -q && uv run ruff check && uv run pyright`
Expected: all pass. The casefold tier skips unless `ATOMS_CASEFOLD_VOLUME` is set.

- [ ] **Step 7: Commit**

```bash
git add tests/ docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md AGENTS.md
git commit -m "test(fs): lock the casefold correspondence and the a4b-1 boundary"
```

---

## Self-review

**Design coverage.** §5.2 dispatch → Task 1. §5.3 reader → Task 1. §5.4 inheritance → Task 1, locked
against reality in Tasks 6 and 7. §6.1 construction → Task 2. §6.2 types → Task 2. §6.3 walk → Task 4.
§6.4 observation → Task 3. §6.5 descriptor discipline → Task 4 (implementation), Task 6 (peak of
three). §6.6 work base → Task 5. §7 error contract → Tasks 1–5, guarded in Task 7. §8 limits → Task 4.
§9.1–§9.5 → Tasks 1–7. §10 authority amendment → Task 7.

**Acceptance criteria.** 1–3 Task 4; 4 Tasks 1–2; 5 Tasks 2 and 4; 6 Task 6; 7 Tasks 4 and 6; 8 Task
6; 9 Task 6; 10 Task 6; 11 Tasks 1, 3, 4, 5, 7; 12 Tasks 2 and 4; 13 Task 5; 14 Tasks 6 and 7; 15 Task
1; 16 Task 4; 17 Task 7.

**Ledger.** Entries #19 and #20 stay open — both name later stages as owners. No entry is discharged
by this plan, matching design §3.1.

**Two deliberate readings of the design, flagged for the reviewer.** Design §6.1 says construction
reads `binding.backend` and `binding.project_root_fd` before `evidence`; the implementation reads
`project_root_fd` alone, because that property already routes through `_require_active()` and a second
read would be a bare expression with no effect. The gate the design asks for — liveness before
detached evidence — holds. Second, `_path_max` lives in `resolve.py` while `_name_max` lives in
`lookup.py`, because `PATH_MAX` is volume-scoped and read once at construction while `NAME_MAX` is
per-directory and belongs with the constraints it accompanies.

---

## Execution Handoff

Plan complete and saved to `~/d/atoms/docs/plans/2026-07-30-plan-a4b1-path-resolution.md`. Two
execution options:

**1. Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with
checkpoints for review.

Which approach?
