# Plan A4a — Platform capability backend and project volume binding

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build A4a's impure `atoms.fs` layer: `ctypes` syscall bindings, a `Backend` protocol with one
Linux implementation, durability-configuration resolution, the idempotent bootstrap, the empirical
capability probe, and `bind_project_volume` yielding a live `ProjectBinding` over frozen
`VolumeEvidence`.

**Architecture:** A new `atoms.fs` package, the first in this repository that touches a filesystem.
`atoms.core` stays pure and gains an import-allowlist guard proving it. The layer separates the live
resource (descriptors, held lock, backend) from the frozen evidence (configuration tuple, matched
allowlist entry, supplied capabilities), so A4b can compose evidence into `ProjectApprovedSpec` without
embedding a resource.

**Tech Stack:** Python ≥3.11, stdlib only (`ctypes`, `os`, `fcntl`, `sqlite3`, `subprocess`,
`dataclasses`, `enum`), managed with `uv`; `ruff` + `pyright` + `pytest`. All commands run from
`python/`.

**Design authority:**
[`2026-07-29-a4a-capability-backend-design.md`](2026-07-29-a4a-capability-backend-design.md), which
refines [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md)
§§5.4, 5.5, 6, 7, and 13.2. Where they disagree, the authority design wins.

**Status:** Implemented on 2026-07-30. A7b–A8 remain unimplemented;
A4a mutates only engine-owned `metadata_root`, never project paths.

## Global Constraints

- **Python floor:** `requires-python = ">=3.11"`. Do not change it.
- **Runtime dependencies:** stdlib only. Do not add a package or dev dependency. `ctypes`, not `cffi`,
  and no compiled extension.
- **Layout:** implementation under `python/src/atoms/fs/`; tests under `python/tests/`.
- **Purity direction:** `atoms.fs` imports `atoms.core`. `atoms.core` never imports `atoms.fs`.
- **No new error types:** reuse `atoms.core.errors.CapabilityUnavailable` and `ProtocolError`.
- **Availability is not durability:** every probe establishes functional availability only. No probe
  result may be presented as a power-loss guarantee. Only a matched allowlist entry carries that claim.
- **Fail closed:** `CERTIFIED_ALLOWLIST` ships empty. There is no override, no flag, and no uncertified
  binding state.
- **Declaration, not observation:** `StorageProfile` fields are declarations by the trusted composition
  root. Never read `/sys/block`, `rotational`, `write_cache`, or a dm/md topology.
- **Capability judgement belongs to A4b:** `bind_project_volume` reports the supplied capability set and
  never refuses because an optional capability is absent. Only `anchored_traversal`,
  `advisory_project_lock`, and SQLite-WAL hostability refuse in A4a.
- **Factory control:** `VolumeEvidence`, `HeldProjectLock`, and `ProjectBinding` refuse ordinary
  construction and `dataclasses.replace`.
- **Borrowed descriptors:** every descriptor A4a exposes is borrowed and must never be closed by a
  consumer. Every descriptor A4a opens uses `O_CLOEXEC`.
- **`OSError` propagates** except for documented per-operation unsupported errno values, each licensed by
  the §10 probe precondition.
- **Exact runtime types:** closed unions dispatch on `type(x) is T`, never `isinstance`.
- **Enums:** plain `Enum` with string `.value` members. Do not use `str, Enum`.
- **Mutation discipline:** use TDD for every task that changes production behavior. Run the named failing
  test before production edits. Task 7 adds characterization and architecture locks over behavior already
  delivered by Tasks 1–6; its named checkpoint must pass and any failure is a deviation to fix, not a
  fictitious RED state.
- **Commits:** no AI-attribution trailers. Documentation paths use `~/d/atoms/...`, never host-specific
  absolute paths.

## File map

| File | Responsibility |
| --- | --- |
| `python/src/atoms/fs/__init__.py` | Public surface only |
| `python/src/atoms/fs/py.typed` | PEP 561 marker |
| `python/src/atoms/fs/syscalls/__init__.py` | Namespace for raw wrappers |
| `python/src/atoms/fs/syscalls/linux.py` | `ctypes` `openat2`/`renameat2`; no policy, no state |
| `python/src/atoms/fs/backend.py` | `Backend` protocol |
| `python/src/atoms/fs/linux.py` | `LinuxBackend` |
| `python/src/atoms/fs/platform.py` | `select_backend`, architecture table |
| `python/src/atoms/fs/volume.py` | mountinfo/fdinfo, `VolumeConfiguration`, allowlist |
| `python/src/atoms/fs/lock.py` | `HeldProjectLock`, `acquire_project_lock` |
| `python/src/atoms/fs/bootstrap.py` | Metadata layout, reclamation, `verified_child_path` |
| `python/src/atoms/fs/probe.py` | `probe_backend`, SQLite-WAL certification |
| `python/src/atoms/fs/binding.py` | `VolumeEvidence`, `ProjectBinding`, `bind_project_volume` |
| `python/tests/fs_support.py` | Fake backend, mountinfo fixtures, volume resolution, factories |
| `python/tests/conftest.py` | Fixture registry (extended; created by A3) |
| `python/tests/test_fs_syscalls.py` | Raw wrapper behavior and errno propagation |
| `python/tests/test_fs_backend.py` | `LinuxBackend` operations and root establishment |
| `python/tests/test_fs_volume.py` | Parsing, normalization, allowlist matching |
| `python/tests/test_fs_lock.py` | Lock acquisition, guards, factory control |
| `python/tests/test_fs_bootstrap.py` | Layout, reclamation, verified paths, xattr marker |
| `python/tests/test_fs_probe.py` | Capability probes and SQLite-WAL choreography |
| `python/tests/test_fs_binding.py` | Sequence, refusals, lifetimes, evidence, real bind-mount identity |
| `python/tests/test_fs_architecture.py` | Purity allowlist, exports, packaging, no-production-caller |

## Design-to-task map

| Design contract | Task |
| --- | --- |
| §4.2 purity allowlist; §5.2 `ctypes` binding; §5.3 platform selection; packaging | 1 |
| §5.1 `Backend` protocol; §5.4 root establishment; `LinuxBackend` | 2 |
| §6.1 mount identity; §6.2 configuration; §6.3 storage profile; §6.4 allowlist | 3 |
| §7.1 lock; §7.2 layout; §7.3 reclamation; §9.4 `verified_child_path` | 4 |
| §8.1 functional probes; §8.2 per-capability probes; §8.3 SQLite-WAL | 5 |
| §9.1 sequence; §9.2 evidence; §9.3 binding lifetime; §9.4 public method | 6 |
| §10 table-derived errno triage mutations; §11.4 architecture; §12 obligations; status sync | 7 |

## Fixture registry

`python/tests/conftest.py` already exists from A3. Each task below extends it with thin
`@pytest.fixture` adapters over explicit factories in `tests.fs_support`. No test may name a fixture
absent from this registry. The Task 7 architecture test enumerates test signatures and fails on any
non-builtin pytest argument missing from `conftest.py`.

| Fixture | Added | Exact contract |
| --- | --- | --- |
| `test_volume` | Task 2 | `Path` to a writable directory on a supported filesystem; skips if unresolvable |
| `linux_backend` | Task 2 | a `LinuxBackend` instance |
| `mountinfo_text` | Task 3 | callable mapping a case name to captured `mountinfo` text |
| `fdinfo_text` | Task 3 | callable mapping a case name to captured `fdinfo` text |
| `test_storage_profile` | Task 3 | `StorageProfile(profile_id="atoms-test-profile")` |
| `metadata_root` | Task 4 | fresh, non-existent `Path` under `test_volume` for one test |
| `project_root` | Task 4 | fresh, existing `Path` under `test_volume` for one test |
| `held_lock` | Task 4 | callable `(metadata_root) -> HeldProjectLock` context manager |
| `fake_backend` | Task 4 | callable `(supplied, lock_excludes=True, override_names=None, **errno_overrides)` |
| `test_allowlist` | Task 6 | callable `(configuration, storage) -> DurabilityAllowlist` singleton |
| `bound_volume` | Task 6 | callable yielding an active `ProjectBinding` on `test_volume` |
| `distinct_volume` | Task 6 | `Path` on a writable mount with a different mount ID; skips if none |

`fake_backend`'s `supplied` is a `set[Capability]`; each method-level `<operation>_errno` keyword injects
that errno at one concrete backend method, while each `UNSUPPORTED_ERRNO` contract key
(`<contract>_errno`) injects across the method set that implements that capability. `override_names`
narrows either form to specific final components. That narrowing is required wherever a probe reads a
*refusal* as evidence: those probes call the same operation twice, and an unscoped injection lands on the
earlier availability call instead of the step under test.

Not every shared helper is a fixture. `tests.fs_support` also exports plain context managers —
`metadata_layout`, `probe_directory`, `probe_database_path` — imported directly by the tests that need
them. They are not fixtures because they take the *lock* as an argument, and a fixture cannot receive a
value a test constructs. Each exists to give returned descriptors exactly one owner: `ensure_metadata_layout`
hands back one descriptor per layout component, and a test that drops that return value leaks all four.

**Conftest hygiene.** Several tasks below say "append to `conftest.py`". Append the *fixture bodies*,
but consolidate imports into the single existing import block at the top of the file — repeating
`import pytest` or `from pathlib import Path` per task will fail `ruff check`. After each task, run
`uv run ruff check tests/conftest.py` before committing.

Each task's fixture step adds both the support factory and this exact adapter shape:

```python
@pytest.fixture
def fixture_name():
    return make_fixture_name()
```

For a callable fixture, omit the parentheses in the return:

```python
@pytest.fixture
def fixture_name():
    return make_fixture_name
```

---

### Task 1: Package boundary, syscall bindings, and platform selection

Create the impure package and fix the purity boundary in the same commit, per design §4.2. The raw
wrappers hold no policy: they convert arguments, invoke, and raise `OSError` with the true `errno`.

**Files:**
- Create: `python/src/atoms/fs/__init__.py`
- Create: `python/src/atoms/fs/py.typed`
- Create: `python/src/atoms/fs/syscalls/__init__.py`
- Create: `python/src/atoms/fs/syscalls/linux.py`
- Create: `python/src/atoms/fs/platform.py`
- Create: `python/tests/test_fs_syscalls.py`
- Create: `python/tests/test_fs_architecture.py`
- Modify: `python/pyproject.toml`
- Modify: `python/tests/test_packaging.py`
- Modify: `python/tests/test_recovery_architecture.py:228-248`

**Interfaces:**
- Consumes: `atoms.core.errors.CapabilityUnavailable`.
- Produces:
  - `atoms.fs.syscalls.linux.openat2(dirfd: int, path: bytes, flags: int, mode: int, resolve: int) -> int`
  - `atoms.fs.syscalls.linux.renameat2(olddirfd: int, oldpath: bytes, newdirfd: int,
    newpath: bytes, flags: int) -> None`
  - constants `RESOLVE_NO_XDEV`, `RESOLVE_NO_SYMLINKS`, `RESOLVE_BENEATH`, `RENAME_NOREPLACE`,
    `RENAME_EXCHANGE`
  - `atoms.fs.platform.select_backend() -> Backend` (returns `LinuxBackend` from Task 2; this task
    ships the refusal paths and a placeholder import guarded by Task 2's arrival)

- [ ] **Step 1: Write the failing syscall tests**

Create `python/tests/test_fs_syscalls.py`:

```python
import ctypes
import errno
import os

import pytest

from atoms.fs.syscalls import linux


def test_resolve_and_rename_constants_match_kernel_values():
    assert linux.RESOLVE_NO_XDEV == 0x01
    assert linux.RESOLVE_NO_SYMLINKS == 0x04
    assert linux.RESOLVE_BENEATH == 0x08
    assert linux.RENAME_NOREPLACE == 1
    assert linux.RENAME_EXCHANGE == 2


def test_open_how_struct_is_three_u64_fields():
    assert ctypes.sizeof(linux.OpenHow) == 24
    assert [name for name, _ in linux.OpenHow._fields_] == ["flags", "mode", "resolve"]


def test_openat2_opens_a_directory_relative_to_a_descriptor(tmp_path):
    (tmp_path / "child").mkdir()
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        fd = linux.openat2(
            parent_fd,
            b"child",
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
            0,
            linux.RESOLVE_BENEATH | linux.RESOLVE_NO_SYMLINKS,
        )
        try:
            assert os.fstat(fd).st_ino == os.stat(tmp_path / "child").st_ino
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)


def test_openat2_refuses_a_symlink_component_under_no_symlinks(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to("real")
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        with pytest.raises(OSError) as caught:
            linux.openat2(
                parent_fd,
                b"link",
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
                0,
                linux.RESOLVE_BENEATH | linux.RESOLVE_NO_SYMLINKS,
            )
        assert caught.value.errno == errno.ELOOP
    finally:
        os.close(parent_fd)


def test_openat2_propagates_enoent_with_true_errno(tmp_path):
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        with pytest.raises(OSError) as caught:
            linux.openat2(parent_fd, b"missing", os.O_RDONLY, 0, linux.RESOLVE_BENEATH)
        assert caught.value.errno == errno.ENOENT
    finally:
        os.close(parent_fd)


def test_renameat2_noreplace_refuses_an_existing_destination(tmp_path):
    (tmp_path / "src").write_text("s")
    (tmp_path / "dst").write_text("d")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        with pytest.raises(OSError) as caught:
            linux.renameat2(fd, b"src", fd, b"dst", linux.RENAME_NOREPLACE)
        assert caught.value.errno == errno.EEXIST
        assert (tmp_path / "src").read_text() == "s"
    finally:
        os.close(fd)


def test_renameat2_exchange_swaps_two_entries(tmp_path):
    (tmp_path / "a").write_text("A")
    (tmp_path / "b").write_text("B")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        linux.renameat2(fd, b"a", fd, b"b", linux.RENAME_EXCHANGE)
    finally:
        os.close(fd)
    assert (tmp_path / "a").read_text() == "B"
    assert (tmp_path / "b").read_text() == "A"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_syscalls.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atoms.fs'`

- [ ] **Step 3: Create the package and the raw wrappers**

Create `python/src/atoms/fs/py.typed` as an empty file.

Create `python/src/atoms/fs/__init__.py`:

```python
"""Atoms filesystem layer: platform capabilities and project volume binding."""
```

Create `python/src/atoms/fs/syscalls/__init__.py`:

```python
"""Raw syscall wrappers. No policy, no retained state."""
```

Create `python/src/atoms/fs/syscalls/linux.py`:

```python
"""ctypes bindings for the Linux syscalls the stdlib does not expose (design §5.2)."""

from __future__ import annotations

import ctypes
import os
import platform

RESOLVE_NO_XDEV = 0x01
RESOLVE_NO_MAGICLINKS = 0x02
RESOLVE_NO_SYMLINKS = 0x04
RESOLVE_BENEATH = 0x08

RENAME_NOREPLACE = 1
RENAME_EXCHANGE = 2

# Syscall numbers are per-architecture. An unlisted architecture refuses in platform.py
# rather than guessing a number.
_SYSCALL_NUMBERS = {
    "x86_64": {"openat2": 437, "renameat2": 316},
    "aarch64": {"openat2": 437, "renameat2": 276},
}


class OpenHow(ctypes.Structure):
    """struct open_how from linux/openat2.h: three u64 fields, 24 bytes."""

    _fields_ = [
        ("flags", ctypes.c_uint64),
        ("mode", ctypes.c_uint64),
        ("resolve", ctypes.c_uint64),
    ]


_libc = ctypes.CDLL(None, use_errno=True)
_syscall = _libc.syscall
_syscall.restype = ctypes.c_long


def syscall_numbers() -> dict[str, int]:
    """Return this architecture's syscall numbers, or an empty mapping if unlisted."""
    return _SYSCALL_NUMBERS.get(platform.machine(), {})


def _raise_errno() -> None:
    code = ctypes.get_errno()
    raise OSError(code, os.strerror(code))


def openat2(dirfd: int, path: bytes, flags: int, mode: int, resolve: int) -> int:
    """openat2(2). No glibc wrapper exists, so this always goes through syscall()."""
    how = OpenHow(flags=flags, mode=mode, resolve=resolve)
    ctypes.set_errno(0)
    result = _syscall(
        ctypes.c_long(syscall_numbers()["openat2"]),
        ctypes.c_int(dirfd),
        ctypes.c_char_p(path),
        ctypes.byref(how),
        ctypes.c_size_t(ctypes.sizeof(OpenHow)),
    )
    if result < 0:
        _raise_errno()
    return int(result)


def _resolve_renameat2():
    """glibc has exposed renameat2 since 2.28; fall back to raw syscall otherwise."""
    entry = getattr(_libc, "renameat2", None)
    if entry is not None:
        entry.restype = ctypes.c_int
        entry.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        return entry
    return None


_renameat2_symbol = _resolve_renameat2()


def renameat2(olddirfd: int, oldpath: bytes, newdirfd: int, newpath: bytes, flags: int) -> None:
    ctypes.set_errno(0)
    if _renameat2_symbol is not None:
        result = _renameat2_symbol(olddirfd, oldpath, newdirfd, newpath, flags)
    else:
        result = _syscall(
            ctypes.c_long(syscall_numbers()["renameat2"]),
            ctypes.c_int(olddirfd),
            ctypes.c_char_p(oldpath),
            ctypes.c_int(newdirfd),
            ctypes.c_char_p(newpath),
            ctypes.c_uint(flags),
        )
    if result < 0:
        _raise_errno()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fs_syscalls.py -v`
Expected: PASS. Every test in the named files must pass; the only acceptable non-pass is a
`test_volume`/`distinct_volume` skip with its stated reason. Do not accept a bare count as
evidence — read the skip lines.

- [ ] **Step 5: Write the failing platform-selection and purity tests**

Create `python/tests/test_fs_architecture.py`:

```python
import ast
from pathlib import Path

import pytest

from atoms.core.errors import CapabilityUnavailable
from atoms.fs import platform as fs_platform

_CORE_IMPORT_ALLOWLIST = {
    "__future__",
    "atoms",
    "collections",
    "dataclasses",
    "enum",
    "functools",
    "itertools",
    "json",
    "re",
    "typing",
    "unicodedata",
}


def _top_level_imports(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module.split(".", 1)[0])
    return names


def _core_modules() -> list[Path]:
    root = Path(__file__).parents[1] / "src" / "atoms" / "core"
    return sorted(root.rglob("*.py"))


def test_core_imports_only_the_allowlisted_modules():
    # An allowlist, not a denylist: a denylist only ever forbids what someone
    # thought to enumerate, and fcntl/platform/shutil/tempfile were all omitted.
    assert _core_modules(), "expected to find modules under atoms/core"
    for source_path in _core_modules():
        disallowed = _top_level_imports(source_path) - _CORE_IMPORT_ALLOWLIST
        assert not disallowed, f"{source_path} imports {sorted(disallowed)}"


def test_core_never_imports_the_filesystem_layer():
    for source_path in _core_modules():
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                assert not node.module.startswith("atoms.fs"), source_path
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("atoms.fs"), source_path


def test_select_backend_refuses_a_non_linux_platform(monkeypatch):
    monkeypatch.setattr(fs_platform.sys, "platform", "darwin")
    with pytest.raises(CapabilityUnavailable, match="platform"):
        fs_platform.select_backend()


def test_select_backend_refuses_an_unlisted_architecture(monkeypatch):
    monkeypatch.setattr(fs_platform.sys, "platform", "linux")
    monkeypatch.setattr(fs_platform.platform, "machine", lambda: "riscv64")
    with pytest.raises(CapabilityUnavailable, match="architecture"):
        fs_platform.select_backend()


def test_select_backend_performs_no_io(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("select_backend must not touch the filesystem")

    monkeypatch.setattr(fs_platform.os, "open", explode, raising=False)
    monkeypatch.setattr(fs_platform.sys, "platform", "linux")
    monkeypatch.setattr(fs_platform.platform, "machine", lambda: "riscv64")
    with pytest.raises(CapabilityUnavailable):
        fs_platform.select_backend()
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_architecture.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atoms.fs.platform'`

- [ ] **Step 7: Implement platform selection**

Create `python/src/atoms/fs/platform.py`:

```python
"""Explicit platform and architecture selection (design §5.3)."""

from __future__ import annotations

import os  # noqa: F401  # referenced by the no-I/O architecture test
import platform
import sys

from atoms.core.errors import CapabilityUnavailable
from atoms.fs.syscalls import linux

BACKEND_REVISION = "linux-1"
"""Atoms backend contract revision (design §6.2).

Bump this deliberately when the backend's syscall selection, flag set, or durability ordering
changes. Bumping de-certifies every allowlist entry naming the old revision, which is intended:
a crash test certifies a volume configuration *and* the backend code that issued the syscalls.
"""


def select_backend():
    """Return a Backend for this host, or refuse. Performs no I/O."""
    if sys.platform != "linux":
        raise CapabilityUnavailable(
            f"unsupported platform {sys.platform!r}: only linux is implemented"
        )
    machine = platform.machine()
    if not linux.syscall_numbers():
        raise CapabilityUnavailable(
            f"unsupported architecture {machine!r}: no syscall table entry"
        )
    from atoms.fs.linux import LinuxBackend

    return LinuxBackend()
```

- [ ] **Step 8: Replace the recovery purity denylist**

In `python/tests/test_recovery_architecture.py`, delete
`test_recovery_package_has_no_io_or_sqlite_imports` (lines 228-248). Its denylist is superseded by the
allowlist in `test_fs_architecture.py`, which covers all of `atoms/core` including `recovery/` and
forbids `atoms.fs` besides — a strict superset, so nothing is lost by removing it.

Deleting that test leaves `_python_imports` (line 39) with no caller. Delete it in the same edit: a
helper kept "in case" is how a denylist grows a second life. `ast` stays imported — other tests in the
file use it.

- [ ] **Step 9: Declare the package for packaging**

In `python/pyproject.toml`, change:

```toml
import-names = ["atoms.core"]
```

to:

```toml
import-names = ["atoms.core", "atoms.fs"]
```

In `python/tests/test_packaging.py`, append:

```python
def test_py_typed_marker_present_in_fs_package():
    import atoms.fs

    marker = Path(atoms.fs.__file__).with_name("py.typed")
    assert marker.is_file(), "PEP 561 py.typed marker must sit in atoms.fs"
```

- [ ] **Step 10: Run the full suite**

Run: `uv run pytest -q && uv run ruff check && uv run pyright`
Expected: all pass. `select_backend` will fail to import `LinuxBackend` until Task 2; the four
architecture tests above monkeypatch before reaching that import, so they pass.

- [ ] **Step 11: Commit**

```bash
git add src/atoms/fs tests/test_fs_syscalls.py tests/test_fs_architecture.py \
        tests/test_packaging.py tests/test_recovery_architecture.py pyproject.toml
git commit -m "feat(fs): bind linux syscalls and fix the purity boundary"
```

---

### Task 2: `Backend` protocol, `LinuxBackend`, and root establishment

The protocol carries exactly one operation set per §5.5 capability and no `supplied_capabilities`
method — a backend that reported its own capabilities would make the evidence circular. Root
establishment is specified here because `O_NOFOLLOW` guards only the final component.

`UNSUPPORTED_ERRNO` lives in `backend.py` alongside the protocol rather than in `probe.py`, because it is
part of the operation contract and has **two** consumers: the probe, and the bootstrap path in Task 4,
which converts the traversal and lock entries to `CapabilityUnavailable` per design §10. `probe.py`
imports `lock.py`, so a table defined in `probe.py` could not be reached from `lock.py` without a cycle,
and a second copy would drift.

**Files:**
- Create: `python/src/atoms/fs/backend.py`
- Create: `python/src/atoms/fs/linux.py`
- Create: `python/tests/fs_support.py`
- Create: `python/tests/test_fs_backend.py`
- Modify: `python/tests/conftest.py`

**Interfaces:**
- Consumes: `atoms.fs.syscalls.linux`; `atoms.core.capabilities.Capability`.
- Produces:
  - `Backend` protocol with `open_root(path: str) -> int`,
    `open_child_directory(parent_fd: int, name: str) -> int`,
    `exchange(parent_fd: int, left: str, right: str) -> None`,
    `transfer_noclobber(src_fd: int, src: str, dst_fd: int, dst: str) -> None`,
    `link_anchor(src_fd: int, src: str, dst_fd: int, dst: str) -> None`,
    `flush_file(fd: int) -> None`, `flush_directory(fd: int) -> None`,
    `open_regular_nofollow(parent_fd: int, name: str) -> int`,
    `symlink_fingerprint(parent_fd: int, name: str) -> tuple[os.stat_result, str]`,
    `lock_exclusive(fd: int) -> None`, `try_lock_exclusive(fd: int) -> bool`
  - `UNSUPPORTED_ERRNO: dict[str, frozenset[int]]` in `atoms.fs.backend`, keyed by operation name
  - `LinuxBackend` implementing it
  - `tests.fs_support.resolve_test_volume() -> Path | None`

- [ ] **Step 1: Write the failing backend tests**

Create `python/tests/test_fs_backend.py`:

```python
import errno
import os
import stat

import pytest

from atoms.fs.backend import UNSUPPORTED_ERRNO


def test_unsupported_errno_sets_exclude_ambiguous_generic_failures():
    # A generic failure must never be readable as "this volume lacks the operation":
    # that would report a durable volume as capability-poor (design §10).
    for operation, codes in UNSUPPORTED_ERRNO.items():
        assert errno.EBADF not in codes, operation
        assert errno.EMFILE not in codes, operation
        assert errno.EFAULT not in codes, operation
    # flock(2) defines ENOLCK as exhaustion of kernel lock-record memory, not lack
    # of advisory-lock support. It is environmental failure and must propagate.
    assert errno.ENOLCK not in UNSUPPORTED_ERRNO["lock"]


def test_unsupported_errno_covers_every_probed_operation():
    # The bootstrap path in Task 4 reads "traversal" and "lock" from this same table.
    assert set(UNSUPPORTED_ERRNO) == {
        "exchange",
        "flush",
        "link_anchor",
        "lock",
        "open_regular_nofollow",
        "symlink_fingerprint",
        "transfer_noclobber",
        "traversal",
    }


def test_open_root_opens_an_existing_directory(test_volume, linux_backend):
    root = test_volume / "root"
    root.mkdir()
    fd = linux_backend.open_root(str(root))
    try:
        assert stat.S_ISDIR(os.fstat(fd).st_mode)
        assert os.fstat(fd).st_ino == os.stat(root).st_ino
    finally:
        os.close(fd)


def test_open_root_refuses_a_symlinked_ancestor(test_volume, linux_backend):
    # O_NOFOLLOW would guard only the final component and admit this exact case.
    real = test_volume / "real"
    (real / "inner").mkdir(parents=True)
    (test_volume / "aliased").symlink_to(real)
    with pytest.raises(OSError) as caught:
        linux_backend.open_root(str(test_volume / "aliased" / "inner"))
    assert caught.value.errno == errno.ELOOP


def test_open_root_refuses_a_symlinked_leaf(test_volume, linux_backend):
    (test_volume / "target").mkdir()
    (test_volume / "leaf").symlink_to(test_volume / "target")
    with pytest.raises(OSError) as caught:
        linux_backend.open_root(str(test_volume / "leaf"))
    assert caught.value.errno == errno.ELOOP


def test_open_root_refuses_a_non_directory(test_volume, linux_backend):
    plain = test_volume / "plain"
    plain.write_text("x")
    with pytest.raises(OSError) as caught:
        linux_backend.open_root(str(plain))
    assert caught.value.errno == errno.ENOTDIR


def test_open_root_uses_cloexec(test_volume, linux_backend):
    fd = linux_backend.open_root(str(test_volume))
    try:
        assert os.get_inheritable(fd) is False
    finally:
        os.close(fd)


def test_open_child_directory_refuses_a_symlink(test_volume, linux_backend):
    (test_volume / "real").mkdir()
    (test_volume / "link").symlink_to("real")
    parent = linux_backend.open_root(str(test_volume))
    try:
        with pytest.raises(OSError) as caught:
            linux_backend.open_child_directory(parent, "link")
        assert caught.value.errno == errno.ELOOP
    finally:
        os.close(parent)


def test_open_child_directory_refuses_a_parent_component(test_volume, linux_backend):
    # RESOLVE_BENEATH reports an escape as EXDEV. The exact code matters: the probe
    # treats this refusal as evidence the guard works, so any other errno there is
    # an unrelated failure and must propagate rather than be read as success.
    parent = linux_backend.open_root(str(test_volume))
    try:
        with pytest.raises(OSError) as caught:
            linux_backend.open_child_directory(parent, "..")
        assert caught.value.errno == errno.EXDEV
    finally:
        os.close(parent)


def test_exchange_swaps_entries_in_one_parent(test_volume, linux_backend):
    (test_volume / "a").write_text("A")
    (test_volume / "b").write_text("B")
    fd = linux_backend.open_root(str(test_volume))
    try:
        linux_backend.exchange(fd, "a", "b")
    finally:
        os.close(fd)
    assert (test_volume / "a").read_text() == "B"
    assert (test_volume / "b").read_text() == "A"


def test_transfer_noclobber_across_distinct_parents(test_volume, linux_backend):
    # The distinct-parent form is what blob promotion and staging publication use.
    (test_volume / "src").mkdir()
    (test_volume / "dst").mkdir()
    (test_volume / "src" / "f").write_text("payload")
    (test_volume / "dst" / "f").write_text("occupied")
    src_fd = linux_backend.open_root(str(test_volume / "src"))
    dst_fd = linux_backend.open_root(str(test_volume / "dst"))
    try:
        with pytest.raises(OSError) as caught:
            linux_backend.transfer_noclobber(src_fd, "f", dst_fd, "f")
        assert caught.value.errno == errno.EEXIST
        os.unlink(test_volume / "dst" / "f")
        linux_backend.transfer_noclobber(src_fd, "f", dst_fd, "f")
    finally:
        os.close(src_fd)
        os.close(dst_fd)
    assert (test_volume / "dst" / "f").read_text() == "payload"
    assert not (test_volume / "src" / "f").exists()


def test_link_anchor_across_distinct_parents_shares_identity(test_volume, linux_backend):
    (test_volume / "src").mkdir()
    (test_volume / "dst").mkdir()
    (test_volume / "src" / "f").write_text("payload")
    src_fd = linux_backend.open_root(str(test_volume / "src"))
    dst_fd = linux_backend.open_root(str(test_volume / "dst"))
    try:
        linux_backend.link_anchor(src_fd, "f", dst_fd, "anchor")
    finally:
        os.close(src_fd)
        os.close(dst_fd)
    source = os.stat(test_volume / "src" / "f")
    anchor = os.stat(test_volume / "dst" / "anchor")
    assert (source.st_dev, source.st_ino) == (anchor.st_dev, anchor.st_ino)
    assert source.st_nlink == 2


def test_open_regular_nofollow_reads_and_refuses_a_symlink_leaf(test_volume, linux_backend):
    (test_volume / "f").write_text("payload")
    (test_volume / "link").symlink_to("f")
    parent = linux_backend.open_root(str(test_volume))
    try:
        fd = linux_backend.open_regular_nofollow(parent, "f")
        try:
            assert os.read(fd, 16) == b"payload"
            assert stat.S_ISREG(os.fstat(fd).st_mode)
        finally:
            os.close(fd)
        with pytest.raises(OSError) as caught:
            linux_backend.open_regular_nofollow(parent, "link")
        assert caught.value.errno == errno.ELOOP
    finally:
        os.close(parent)


def test_symlink_fingerprint_is_lstat_coherent(test_volume, linux_backend):
    (test_volume / "link").symlink_to("../target")
    parent = linux_backend.open_root(str(test_volume))
    try:
        info, target = linux_backend.symlink_fingerprint(parent, "link")
    finally:
        os.close(parent)
    assert stat.S_ISLNK(info.st_mode)
    assert target == "../target"


def test_flush_file_and_directory_succeed(test_volume, linux_backend):
    (test_volume / "f").write_text("payload")
    parent = linux_backend.open_root(str(test_volume))
    fd = os.open(test_volume / "f", os.O_RDONLY | os.O_CLOEXEC)
    try:
        linux_backend.flush_file(fd)
        linux_backend.flush_directory(parent)
    finally:
        os.close(fd)
        os.close(parent)


def test_try_lock_exclusive_contends_across_open_file_descriptions(test_volume, linux_backend):
    # flock is per open file description, so two opens in one process contend.
    path = test_volume / "lock"
    path.write_text("")
    first = os.open(path, os.O_RDWR | os.O_CLOEXEC)
    second = os.open(path, os.O_RDWR | os.O_CLOEXEC)
    try:
        linux_backend.lock_exclusive(first)
        assert linux_backend.try_lock_exclusive(second) is False
    finally:
        os.close(first)
        os.close(second)
```

- [ ] **Step 2: Add the volume fixture and support module**

Create `python/tests/fs_support.py`:

```python
"""Shared factories for the A4a filesystem-layer tests."""

from __future__ import annotations

import os
from pathlib import Path

SUPPORTED_FILESYSTEMS = frozenset({"ext4", "xfs", "btrfs"})


def _filesystem_type_for(path: Path) -> str | None:
    """Return the filesystem type backing `path`, from /proc/self/mountinfo."""
    target = os.stat(path)
    device = f"{os.major(target.st_dev)}:{os.minor(target.st_dev)}"
    with open("/proc/self/mountinfo", encoding="utf-8") as handle:
        for line in handle:
            fields = line.split()
            separator = fields.index("-")
            if fields[2] == device:
                return fields[separator + 1]
    return None


def resolve_test_volume() -> Path | None:
    """Resolve a writable directory on a supported filesystem, or None.

    Tier 3 must not assume this machine's layout: /tmp is tmpfs on most Linux
    systems and refuses by design, so pytest's default tmp_path is unusable here.
    """
    declared = os.environ.get("ATOMS_TEST_VOLUME")
    if declared:
        return Path(declared)
    repository = Path(__file__).parents[2]
    if _filesystem_type_for(repository) in SUPPORTED_FILESYSTEMS:
        return repository / ".atoms-test-volume"
    return None


def test_volume_or_skip_reason() -> tuple[Path | None, str]:
    resolved = resolve_test_volume()
    if resolved is not None:
        return resolved, ""
    repository = Path(__file__).parents[2]
    found = _filesystem_type_for(repository)
    return None, (
        f"no supported test volume: repository filesystem is {found!r}; "
        "set ATOMS_TEST_VOLUME to a directory on ext4, xfs, or btrfs"
    )
```

Append to `python/tests/conftest.py`:

```python
import pytest

from atoms.fs.linux import LinuxBackend
from tests.fs_support import test_volume_or_skip_reason


@pytest.fixture
def test_volume(tmp_path_factory):
    base, reason = test_volume_or_skip_reason()
    if base is None:
        pytest.skip(reason)
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=base))


@pytest.fixture
def linux_backend():
    return LinuxBackend()
```

Add `import tempfile` and `from pathlib import Path` to the conftest imports.

Add `.atoms-test-volume/` to the repository `.gitignore`.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_backend.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atoms.fs.linux'`

- [ ] **Step 4: Write the protocol**

Create `python/src/atoms/fs/backend.py`:

```python
"""The semantic filesystem capability protocol (design §5.1).

The surface is determined by what must be *probed*, not by anticipation of later
stages: exactly one operation set per design §5.5 capability, and every method is
called by the probe that reports it. There is deliberately no supplied_capabilities
method — a backend that reported its own capabilities would make the evidence
circular, when the probe exists precisely to establish what the backend cannot be
trusted to assert.
"""

from __future__ import annotations

import errno
import os
from typing import Protocol

# Errno values that conclusively mean "this volume does not support the operation".
#
# EINVAL from renameat2 and EPERM from link are ambiguous in general — they equally
# signal a malformed argument or a permission failure. What disambiguates them is
# the probe precondition: every probe constructs its own operands, inside a
# directory it created, under the held project lock, immediately before the call.
# Arguments are therefore valid and permissions guaranteed by construction, so
# neither interpretation is reachable. The same errno from any other call site
# propagates.
#
# The table lives here, beside the protocol, because it has two consumers: probe.py
# reports absence from it, and lock.py converts the "traversal" and "lock" entries to
# CapabilityUnavailable on the bootstrap path (design §10). probe.py imports lock.py,
# so defining it there would force either a cycle or a second copy that drifts.
#
# Primary manual bases, recorded per operation rather than inferred:
# - renameat2(2): EINVAL when the filesystem does not support a requested flag.
# - link(2): EPERM when the filesystem does not support hard links.
# - fsync(2): EINVAL when the descriptor's object does not support synchronization.
# - symlink(2): EPERM when the filesystem does not support symbolic-link creation;
#   the probe stages creation inside the symlink_fingerprint capability check.
# - errno(3): ENOSYS is "function not implemented"; ENOTSUP is "operation not
#   supported". ENOTSUP and EOPNOTSUPP have the same numeric value on Linux, but
#   both spellings remain because a structural Backend may come from another OS.
# - flock(2): ENOLCK means lock-record memory exhaustion, so it is deliberately
#   absent here and propagates.
UNSUPPORTED_ERRNO: dict[str, frozenset[int]] = {
    # renameat2(2) RENAME_EXCHANGE.
    "exchange": frozenset({errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.ENOTSUP}),
    # renameat2(2) RENAME_NOREPLACE.
    "transfer_noclobber": frozenset(
        {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.ENOTSUP}
    ),
    # link(2), plus the semantic Backend "operation not supported" result.
    "link_anchor": frozenset({errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP}),
    # fsync(2), plus the semantic Backend "operation not supported" result.
    "flush": frozenset({errno.EINVAL, errno.EOPNOTSUPP, errno.ENOTSUP}),
    # open(2) has no Linux-specific unsupported O_NOFOLLOW result; this is the
    # semantic Backend result only.
    "open_regular_nofollow": frozenset({errno.EOPNOTSUPP, errno.ENOTSUP}),
    # symlink(2), plus the semantic Backend "operation not supported" result.
    "symlink_fingerprint": frozenset({errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP}),
    # Semantic Backend result only. flock(2) ENOLCK is transient exhaustion.
    "lock": frozenset({errno.EOPNOTSUPP, errno.ENOTSUP}),
    # ENOSYS is the kernel without openat2. EOPNOTSUPP/ENOTSUP is a backend that
    # cannot supply the guarded walk at all — the shape a restricted backend takes.
    "traversal": frozenset({errno.ENOSYS, errno.EOPNOTSUPP, errno.ENOTSUP}),
}


class Backend(Protocol):
    # anchored_traversal
    def open_root(self, path: str) -> int:
        """Establish an anchor. Refuses a symlink at *any* component, not just the leaf."""

    def open_child_directory(self, parent_fd: int, name: str) -> int:
        """Guarded traversal of one component beneath a retained descriptor."""

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
    def symlink_fingerprint(self, parent_fd: int, name: str) -> tuple[os.stat_result, str]:
        """lstat + readlink. Deliberately NOT descriptor-coherent (design §5.1)."""

    # advisory_project_lock
    def lock_exclusive(self, fd: int) -> None: ...

    def try_lock_exclusive(self, fd: int) -> bool:
        """Non-blocking acquisition. A successful lock proves acquisition, not exclusion."""
```

- [ ] **Step 5: Implement `LinuxBackend`**

Create `python/src/atoms/fs/linux.py`:

```python
"""Linux implementation of the capability protocol (design §5.1, §5.4)."""

from __future__ import annotations

import errno
import fcntl
import os

from atoms.fs.syscalls import linux as sys_linux

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC


class LinuxBackend:
    def open_root(self, path: str) -> int:
        # RESOLVE_NO_SYMLINKS over the complete path: O_NOFOLLOW would guard only
        # the final component and follow every ancestor symlink. RESOLVE_NO_XDEV is
        # deliberately unset — the target mount is not established yet, and refusing
        # a crossing here would refuse a root that simply lives on its own mount.
        return sys_linux.openat2(
            -100,  # AT_FDCWD
            os.fsencode(path),
            _DIR_FLAGS,
            0,
            sys_linux.RESOLVE_NO_SYMLINKS,
        )

    def open_child_directory(self, parent_fd: int, name: str) -> int:
        return sys_linux.openat2(
            parent_fd,
            os.fsencode(name),
            _DIR_FLAGS,
            0,
            sys_linux.RESOLVE_BENEATH | sys_linux.RESOLVE_NO_SYMLINKS | sys_linux.RESOLVE_NO_XDEV,
        )

    def exchange(self, parent_fd: int, left: str, right: str) -> None:
        sys_linux.renameat2(
            parent_fd, os.fsencode(left), parent_fd, os.fsencode(right), sys_linux.RENAME_EXCHANGE
        )

    def transfer_noclobber(self, src_fd: int, src: str, dst_fd: int, dst: str) -> None:
        sys_linux.renameat2(
            src_fd, os.fsencode(src), dst_fd, os.fsencode(dst), sys_linux.RENAME_NOREPLACE
        )

    def link_anchor(self, src_fd: int, src: str, dst_fd: int, dst: str) -> None:
        os.link(src, dst, src_dir_fd=src_fd, dst_dir_fd=dst_fd, follow_symlinks=False)

    def flush_file(self, fd: int) -> None:
        os.fsync(fd)

    def flush_directory(self, fd: int) -> None:
        os.fsync(fd)

    def open_regular_nofollow(self, parent_fd: int, name: str) -> int:
        return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)

    def symlink_fingerprint(self, parent_fd: int, name: str) -> tuple[os.stat_result, str]:
        info = os.lstat(name, dir_fd=parent_fd)
        return info, os.readlink(name, dir_fd=parent_fd)

    def lock_exclusive(self, fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def try_lock_exclusive(self, fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as caught:
            if caught.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                return False
            raise
        return True
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fs_backend.py tests/test_fs_architecture.py -v`
Expected: PASS. Every test in the named files must pass; the only acceptable non-pass is a
`test_volume`/`distinct_volume` skip with its stated reason. Do not accept a bare count as
evidence — read the skip lines.

- [ ] **Step 7: Commit**

```bash
git add src/atoms/fs/backend.py src/atoms/fs/linux.py tests/fs_support.py \
        tests/test_fs_backend.py tests/conftest.py ../.gitignore
git commit -m "feat(fs): add the capability protocol and its linux backend"
```

---

### Task 3: Mount identity, configuration tuple, and the durability allowlist

Parsing is pure over captured text, so this task is fixture-driven and needs no filesystem. The two
`mountinfo` option fields are distinct: field 6 holds per-mount VFS flags, field 11 holds superblock
options, and every filesystem-specific durability value lives in the latter.

**Files:**
- Create: `python/src/atoms/fs/volume.py`
- Create: `python/tests/test_fs_volume.py`
- Modify: `python/tests/fs_support.py`
- Modify: `python/tests/conftest.py`

**Interfaces:**
- Consumes: `atoms.fs.platform.BACKEND_REVISION`; `atoms.core.errors.CapabilityUnavailable`.
- Produces:
  - `MountEntry(mount_id: int, device: str, mount_point: str, mount_options: tuple[str, ...],
    filesystem_type: str, super_options: tuple[str, ...])`
  - `parse_mountinfo(text: str) -> tuple[MountEntry, ...]`
  - `parse_mount_id(fdinfo_text: str) -> int`
  - `read_mount_id(fd: int) -> int`
  - `resolve_mount_entry(fd: int, mountinfo_text: str) -> MountEntry`
  - `VolumeConfiguration(backend_id, backend_revision, kernel_identifier, filesystem_type,
    barrier_options, durability_features)`
  - `build_configuration(entry: MountEntry, kernel_identifier: str) -> VolumeConfiguration`
  - `StorageProfile(profile_id: str)`
  - `AllowlistEntry(configuration, storage, certification_ref)`
  - `DurabilityAllowlist(entries)` with `match(configuration, storage) -> AllowlistEntry | None`
  - `CERTIFIED_ALLOWLIST: DurabilityAllowlist`

- [ ] **Step 1: Write the failing parsing and normalization tests**

Create `python/tests/test_fs_volume.py`:

```python
import dataclasses

import pytest

from atoms.core.errors import CapabilityUnavailable
from atoms.fs.volume import (
    _BARRIER_OPTIONS,
    CERTIFIED_ALLOWLIST,
    AllowlistEntry,
    DurabilityAllowlist,
    StorageProfile,
    VolumeConfiguration,
    build_configuration,
    parse_mount_id,
    parse_mountinfo,
    resolve_mount_entry,
)


def test_parse_mountinfo_separates_per_mount_and_super_options(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("ext4_writeback"))
    entry = next(item for item in entries if item.mount_point == "/data")
    assert entry.mount_options == ("rw", "noatime")
    assert entry.filesystem_type == "ext4"
    assert "data=writeback" in entry.super_options
    assert "data=writeback" not in entry.mount_options


def test_parse_mountinfo_unescapes_octal_mount_points(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("escaped_space"))
    assert any(entry.mount_point == "/mnt/my volume" for entry in entries)


def test_parse_mountinfo_handles_variable_optional_fields(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("optional_fields"))
    shared = next(item for item in entries if item.mount_point == "/shared")
    assert shared.filesystem_type == "ext4"
    assert shared.mount_id == 41


def test_parse_mountinfo_distinguishes_bind_mounts_sharing_a_device(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("bind_same_device"))
    binds = [item for item in entries if item.device == "259:2"]
    assert len(binds) == 2
    assert binds[0].mount_id != binds[1].mount_id


def test_parse_mount_id_reads_the_fdinfo_field(fdinfo_text):
    assert parse_mount_id(fdinfo_text("plain")) == 41


def test_resolve_mount_entry_matches_on_mount_id_not_device(monkeypatch, mountinfo_text):
    # st_dev alone is ambiguous: bind mounts share a device but differ in mount ID.
    monkeypatch.setattr("atoms.fs.volume.read_mount_id", lambda fd: 43)
    entry = resolve_mount_entry(7, mountinfo_text("bind_same_device"))
    assert entry.mount_id == 43
    assert entry.mount_point == "/data/bind"


def test_resolve_mount_entry_refuses_an_unresolvable_mount(monkeypatch, mountinfo_text):
    monkeypatch.setattr("atoms.fs.volume.read_mount_id", lambda fd: 9999)
    with pytest.raises(CapabilityUnavailable, match="mount"):
        resolve_mount_entry(7, mountinfo_text("bind_same_device"))


def test_build_configuration_normalizes_absent_ext4_options(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("ext4_defaults"))
    entry = next(item for item in entries if item.mount_point == "/data")
    configuration = build_configuration(entry, "7.1.5-arch1-1")
    assert configuration.barrier_options == (
        "async",
        "barrier=1",
        "commit=5",
        "data=ordered",
    )


def test_build_configuration_reads_super_only_values(mountinfo_text):
    # A field-6-only parser finds no data= at all and would normalize this
    # explicit data=writeback to the safe data=ordered default.
    entries = parse_mountinfo(mountinfo_text("ext4_writeback"))
    entry = next(item for item in entries if item.mount_point == "/data")
    configuration = build_configuration(entry, "7.1.5-arch1-1")
    assert "data=writeback" in configuration.barrier_options
    assert "data=ordered" not in configuration.barrier_options


def test_build_configuration_reads_per_mount_only_values(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("ext4_sync"))
    entry = next(item for item in entries if item.mount_point == "/data")
    configuration = build_configuration(entry, "7.1.5-arch1-1")
    assert "sync" in configuration.barrier_options
    assert "dirsync" in configuration.barrier_options


def test_build_configuration_normalizes_absent_xfs_options(mountinfo_text):
    # Exact tuple, not membership: shipping a barrier table for a filesystem means
    # shipping a durability claim about it, and a silently added or dropped option
    # changes which allowlist entry a real volume matches.
    entries = parse_mountinfo(mountinfo_text("xfs_defaults"))
    entry = next(item for item in entries if item.mount_point == "/data")
    configuration = build_configuration(entry, "7.1.5-arch1-1")
    assert configuration.barrier_options == ("async", "barrier=1")


def test_build_configuration_reads_xfs_super_only_values(mountinfo_text):
    # wsync appears only in field 11, so a field-6-only parser reports the default.
    entries = parse_mountinfo(mountinfo_text("xfs_wsync"))
    entry = next(item for item in entries if item.mount_point == "/data")
    configuration = build_configuration(entry, "7.1.5-arch1-1")
    assert configuration.barrier_options == ("async", "barrier=1", "wsync")


def test_build_configuration_normalizes_absent_btrfs_options(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("btrfs_defaults"))
    entry = next(item for item in entries if item.mount_point == "/data")
    configuration = build_configuration(entry, "7.1.5-arch1-1")
    assert configuration.barrier_options == ("barrier=1", "commit=30", "noflushoncommit")


def test_build_configuration_reads_btrfs_super_only_values(mountinfo_text):
    # An explicit flushoncommit and a non-default commit interval must both survive;
    # normalizing either to its default would silently widen the certified claim.
    entries = parse_mountinfo(mountinfo_text("btrfs_flushoncommit"))
    entry = next(item for item in entries if item.mount_point == "/data")
    configuration = build_configuration(entry, "7.1.5-arch1-1")
    assert configuration.barrier_options == ("barrier=1", "commit=15", "flushoncommit")


def test_every_supported_filesystem_has_normalization_coverage(mountinfo_text):
    # The production table is the source of truth. A hard-coded loop over the three
    # current filesystems would keep passing when a fourth table entry was added.
    super_only_cases = {
        "ext4": "ext4_writeback",
        "xfs": "xfs_wsync",
        "btrfs": "btrfs_flushoncommit",
    }
    assert set(_BARRIER_OPTIONS) == set(super_only_cases)
    for filesystem in _BARRIER_OPTIONS:
        defaults = parse_mountinfo(mountinfo_text(f"{filesystem}_defaults"))
        default_entry = next(item for item in defaults if item.mount_point == "/data")
        default_configuration = build_configuration(default_entry, "7.1.5-arch1-1")
        assert default_configuration.filesystem_type == filesystem

        super_only = parse_mountinfo(mountinfo_text(super_only_cases[filesystem]))
        super_entry = next(item for item in super_only if item.mount_point == "/data")
        super_configuration = build_configuration(super_entry, "7.1.5-arch1-1")
        assert super_configuration.filesystem_type == filesystem
        # The coverage fixture must actually isolate a non-default value in field 11.
        # Mapping a filesystem to its defaults fixture would otherwise satisfy the
        # key-set check without exercising super-options parsing.
        assert super_entry.mount_options == default_entry.mount_options
        assert super_entry.super_options != default_entry.super_options
        assert super_configuration.barrier_options != default_configuration.barrier_options


def test_build_configuration_carries_the_exact_kernel_and_backend_revision(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("ext4_defaults"))
    entry = next(item for item in entries if item.mount_point == "/data")
    configuration = build_configuration(entry, "7.1.5-arch1-1")
    # Not a major.minor truncation: one crash test must not certify a whole kernel line.
    assert configuration.kernel_identifier == "7.1.5-arch1-1"
    assert configuration.backend_revision == "linux-1"


def test_build_configuration_refuses_an_unlisted_filesystem(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("tmpfs"))
    entry = next(item for item in entries if item.mount_point == "/tmp")
    with pytest.raises(CapabilityUnavailable, match="tmpfs"):
        build_configuration(entry, "7.1.5-arch1-1")


def test_durability_features_are_empty_until_a_resolver_exists(mountinfo_text):
    entries = parse_mountinfo(mountinfo_text("ext4_defaults"))
    entry = next(item for item in entries if item.mount_point == "/data")
    assert build_configuration(entry, "7.1.5-arch1-1").durability_features == ()


def test_certified_allowlist_ships_empty():
    # Fail closed: production binding refuses every volume until A8 certifies one.
    assert CERTIFIED_ALLOWLIST.entries == frozenset()


def test_allowlist_matches_only_on_exact_configuration_and_profile(test_storage_profile):
    configuration = VolumeConfiguration(
        backend_id="linux",
        backend_revision="linux-1",
        kernel_identifier="7.1.5-arch1-1",
        filesystem_type="ext4",
        barrier_options=("async", "barrier=1", "commit=5", "data=ordered"),
        durability_features=(),
    )
    entry = AllowlistEntry(
        configuration=configuration,
        storage=test_storage_profile,
        certification_ref="crash-2026-07-29-a",
    )
    allowlist = DurabilityAllowlist(entries=frozenset({entry}))
    assert allowlist.match(configuration, test_storage_profile) is entry
    assert allowlist.match(configuration, StorageProfile(profile_id="other")) is None
    widened = dataclasses.replace(configuration, kernel_identifier="7.1.6-arch1-1")
    assert allowlist.match(widened, test_storage_profile) is None


def test_value_types_are_frozen(test_storage_profile):
    with pytest.raises(dataclasses.FrozenInstanceError):
        test_storage_profile.profile_id = "mutated"
```

- [ ] **Step 2: Add the fixture data**

Append to `python/tests/fs_support.py`:

```python
_MOUNTINFO_CASES = {
    "ext4_defaults": (
        "25 30 259:1 / / rw,relatime shared:1 - ext4 /dev/nvme0n1p1 rw\n"
        "41 25 259:2 / /data rw,noatime shared:2 - ext4 /dev/nvme0n1p2 rw\n"
    ),
    "ext4_writeback": (
        "41 25 259:2 / /data rw,noatime shared:2 - ext4 /dev/nvme0n1p2 "
        "rw,data=writeback\n"
    ),
    "ext4_sync": (
        "41 25 259:2 / /data rw,sync,dirsync shared:2 - ext4 /dev/nvme0n1p2 rw\n"
    ),
    # Every filesystem in the barrier table gets a defaults fixture and a
    # super-options-only fixture. Shipping a table without both would ship an
    # untested durability claim (design §11.1).
    "xfs_defaults": (
        "41 25 259:2 / /data rw,noatime shared:2 - xfs /dev/nvme0n1p2 "
        "rw,attr2,inode64,logbufs=8,logbsize=32k,noquota\n"
    ),
    "xfs_wsync": (
        "41 25 259:2 / /data rw,noatime shared:2 - xfs /dev/nvme0n1p2 "
        "rw,wsync,attr2,inode64,noquota\n"
    ),
    "btrfs_defaults": (
        "41 25 0:33 /@ /data rw,noatime shared:2 - btrfs /dev/nvme0n1p2 "
        "rw,space_cache=v2,subvolid=256,subvol=/@\n"
    ),
    "btrfs_flushoncommit": (
        "41 25 0:33 /@ /data rw,noatime shared:2 - btrfs /dev/nvme0n1p2 "
        "rw,flushoncommit,commit=15,space_cache=v2,subvolid=256,subvol=/@\n"
    ),
    "escaped_space": (
        "41 25 259:2 / /mnt/my\\040volume rw,noatime shared:2 - ext4 /dev/nvme0n1p2 rw\n"
    ),
    "optional_fields": (
        "41 25 259:2 / /shared rw,noatime shared:2 master:7 propagate_from:3 "
        "- ext4 /dev/nvme0n1p2 rw\n"
    ),
    "bind_same_device": (
        "41 25 259:2 / /data rw,noatime shared:2 - ext4 /dev/nvme0n1p2 rw\n"
        "43 25 259:2 /sub /data/bind rw,noatime shared:2 - ext4 /dev/nvme0n1p2 rw\n"
    ),
    "tmpfs": "22 25 0:21 / /tmp rw,nosuid,nodev - tmpfs tmpfs rw,inode64\n",
}

_FDINFO_CASES = {
    "plain": "pos:\t0\nflags:\t02000000\nmnt_id:\t41\nino:\t131074\n",
}


def make_mountinfo_text():
    def lookup(case: str) -> str:
        return _MOUNTINFO_CASES[case]

    return lookup


def make_fdinfo_text():
    def lookup(case: str) -> str:
        return _FDINFO_CASES[case]

    return lookup
```

Append to `python/tests/conftest.py`:

```python
from atoms.fs.volume import StorageProfile
from tests.fs_support import make_fdinfo_text, make_mountinfo_text


@pytest.fixture
def mountinfo_text():
    return make_mountinfo_text()


@pytest.fixture
def fdinfo_text():
    return make_fdinfo_text()


@pytest.fixture
def test_storage_profile():
    return StorageProfile(profile_id="atoms-test-profile")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_volume.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atoms.fs.volume'`

- [ ] **Step 4: Implement volume resolution**

Create `python/src/atoms/fs/volume.py`:

```python
"""Mount identity, durability configuration, and the certified allowlist (design §6)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from atoms.core.errors import CapabilityUnavailable
from atoms.fs.platform import BACKEND_REVISION

_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")


@dataclass(frozen=True, slots=True)
class MountEntry:
    mount_id: int
    device: str
    mount_point: str
    mount_options: tuple[str, ...]
    filesystem_type: str
    super_options: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VolumeConfiguration:
    backend_id: str
    backend_revision: str
    kernel_identifier: str
    filesystem_type: str
    barrier_options: tuple[str, ...]
    durability_features: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StorageProfile:
    """A declaration by the trusted composition root, never a runtime observation.

    No syscall can establish whether device firmware honors a cache flush; that is
    what crash certification tests. /sys/block is deliberately not consulted:
    `rotational` describes media classification, `write_cache` is a writable kernel
    view that can suppress flushes without changing hardware, and a dm/md topology
    describes routing — none is a durability verdict.
    """

    profile_id: str


@dataclass(frozen=True, slots=True)
class AllowlistEntry:
    configuration: VolumeConfiguration
    storage: StorageProfile
    certification_ref: str


@dataclass(frozen=True, slots=True)
class DurabilityAllowlist:
    entries: frozenset[AllowlistEntry] = field(default_factory=frozenset)

    def match(
        self, configuration: VolumeConfiguration, storage: StorageProfile
    ) -> AllowlistEntry | None:
        for entry in self.entries:
            if entry.configuration == configuration and entry.storage == storage:
                return entry
        return None


CERTIFIED_ALLOWLIST = DurabilityAllowlist(entries=frozenset())
"""Empty until A8 crash-certifies a configuration tuple. Production binding fails closed."""


# Per-filesystem barrier-relevant options: the exact mountinfo field each is read
# from, and the value it normalizes to when absent. Field 6 is per-mount (VFS
# flags); field 11 is superblock options, where filesystem-specific durability
# values live. Reading only field 6 would find no data= at all.
_SUPER = "super"
_PER_MOUNT = "mount"

_BARRIER_OPTIONS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "ext4": (
        ("barrier", _SUPER, "barrier=1"),
        ("data", _SUPER, "data=ordered"),
        ("journal_async_commit", _SUPER, ""),
        ("commit", _SUPER, "commit=5"),
        ("sync", _PER_MOUNT, "async"),
        ("dirsync", _PER_MOUNT, ""),
    ),
    "xfs": (
        ("barrier", _SUPER, "barrier=1"),
        ("wsync", _SUPER, ""),
        ("sync", _PER_MOUNT, "async"),
    ),
    "btrfs": (
        ("barrier", _SUPER, "barrier=1"),
        ("flushoncommit", _SUPER, "noflushoncommit"),
        ("commit", _SUPER, "commit=30"),
        ("notreelog", _SUPER, ""),
    ),
}


def _unescape(value: str) -> str:
    return _OCTAL_ESCAPE.sub(lambda found: chr(int(found.group(1), 8)), value)


def parse_mountinfo(text: str) -> tuple[MountEntry, ...]:
    entries = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        separator = fields.index("-")
        entries.append(
            MountEntry(
                mount_id=int(fields[0]),
                device=fields[2],
                mount_point=_unescape(fields[4]),
                mount_options=tuple(fields[5].split(",")),
                filesystem_type=fields[separator + 1],
                super_options=tuple(fields[separator + 3].split(",")),
            )
        )
    return tuple(entries)


def parse_mount_id(fdinfo_text: str) -> int:
    for line in fdinfo_text.splitlines():
        if line.startswith("mnt_id:"):
            return int(line.split()[1])
    raise CapabilityUnavailable("fdinfo carries no mnt_id field")


def read_mount_id(fd: int) -> int:
    with open(f"/proc/self/fdinfo/{fd}", encoding="utf-8") as handle:
        return parse_mount_id(handle.read())


def resolve_mount_entry(fd: int, mountinfo_text: str) -> MountEntry:
    """Resolve the mount backing a held descriptor.

    Keyed on mount ID rather than st_dev: bind mounts can share a device while
    carrying distinct per-mount options, so device-keying is ambiguous.
    """
    wanted = read_mount_id(fd)
    for entry in parse_mountinfo(mountinfo_text):
        if entry.mount_id == wanted:
            return entry
    raise CapabilityUnavailable(f"no mount entry for mount id {wanted}")


def _select(name: str, source: str, absent: str, entry: MountEntry) -> str:
    options = entry.super_options if source == _SUPER else entry.mount_options
    for option in options:
        if option == name or option.startswith(f"{name}="):
            return option
        if option == f"no{name}":
            return option
    return absent


def build_configuration(entry: MountEntry, kernel_identifier: str) -> VolumeConfiguration:
    table = _BARRIER_OPTIONS.get(entry.filesystem_type)
    if table is None:
        raise CapabilityUnavailable(
            f"filesystem {entry.filesystem_type!r} has no barrier-option table; "
            "the engine cannot decide which of its options bear on durability"
        )
    values = {_select(name, source, absent, entry) for name, source, absent in table}
    values.discard("")
    return VolumeConfiguration(
        backend_id="linux",
        backend_revision=BACKEND_REVISION,
        kernel_identifier=kernel_identifier,
        filesystem_type=entry.filesystem_type,
        barrier_options=tuple(sorted(values)),
        # No certified entry references a superblock feature yet, and an entry may
        # not reference a feature whose resolver does not exist. Empty means
        # "nothing claimed", not "unresolved".
        durability_features=(),
    )


def read_mountinfo() -> str:
    with open("/proc/self/mountinfo", encoding="utf-8") as handle:
        return handle.read()


def kernel_identifier() -> str:
    return os.uname().release
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fs_volume.py -v`
Expected: PASS. Every test in the named files must pass; the only acceptable non-pass is a
`test_volume`/`distinct_volume` skip with its stated reason. Do not accept a bare count as
evidence — read the skip lines.

- [ ] **Step 6: Commit**

```bash
git add src/atoms/fs/volume.py tests/test_fs_volume.py tests/fs_support.py tests/conftest.py
git commit -m "feat(fs): resolve mount identity and durability configuration"
```

---

### Task 4: Bootstrap — lock, metadata layout, reclamation, verified paths

`metadata_root` is engine-owned space held under the project lock, so every name in it is verified
rather than adopted. `HeldProjectLock` is factory-controlled because downstream signatures treat its
type as proof a lock is held.

Two things arrive here that a reader might expect later. The **restricted backend** comes in this task,
not Task 5, because acquisition is the first place an absent capability must be refused. And root
establishment must **not** normalize before the guarded walk: `os.path.abspath` calls `normpath`, which
collapses `..` lexically, so `aliased/../elsewhere` becomes `elsewhere` and `RESOLVE_NO_SYMLINKS` never
sees `aliased`. Normalization is therefore deferred until the walk has proved the path symlink-free.

**Files:**
- Create: `python/src/atoms/fs/lock.py`
- Create: `python/src/atoms/fs/bootstrap.py`
- Create: `python/tests/test_fs_lock.py`
- Create: `python/tests/test_fs_bootstrap.py`
- Modify: `python/tests/fs_support.py`
- Modify: `python/tests/conftest.py`

**Interfaces:**
- Consumes: `Backend`, `UNSUPPORTED_ERRNO`, `atoms.fs.platform.select_backend`.
- Produces:
  - `HeldProjectLock` with `backend`, `metadata_root_fd`, `metadata_root_path`, `held`,
    `__enter__`, `__exit__`
  - `acquire_project_lock(backend: Backend, metadata_root: str) -> HeldProjectLock`, raising
    `CapabilityUnavailable` when `anchored_traversal` or `advisory_project_lock` is unavailable
  - `establish_root(backend, path: str, create: bool) -> tuple[int, str, bool]` returning
    `(fd, normalized_path, created)`, where normalization happens only after the guarded walk
  - `close_all(fds: Iterable[int]) -> None`, attempting every close and raising the first failure. Every
    multi-descriptor release goes through it: `HeldProjectLock.__exit__`, `acquire_project_lock`'s
    unwind, `ensure_metadata_layout`'s unwind, and `close_layout`. Callers pass descriptors in reverse
    acquisition order; `close_all` does not reorder.
  - `ensure_metadata_layout(lock: HeldProjectLock) -> dict[str, int]`
  - `close_layout(retained: dict[str, int]) -> None`, releasing the retained set in reverse opening
    order — the sole owner of that ordering, used by the binding and by the test helper
  - `reclaim_probe_survivors(lock: HeldProjectLock) -> None`
  - `verified_child_path(metadata_root_fd, metadata_root_path, expected_device, expected_inode, name) -> str`
  - `METADATA_LAYOUT = ("probe", "staging", "work", "blobs/sha256")`
  - `SYNC_IGNORE_ATTRIBUTE = "user.com.dropbox.ignored"`

- [ ] **Step 1: Write the failing lock tests**

Create `python/tests/test_fs_lock.py`:

```python
import dataclasses
import errno
import os
import subprocess
import sys

import pytest

from atoms.core.capabilities import Capability
from atoms.core.errors import CapabilityUnavailable, ProtocolError
from atoms.fs.lock import (
    HeldProjectLock,
    acquire_project_lock,
    close_all,
    establish_root,
)


def test_acquire_creates_metadata_root_and_lock(linux_backend, metadata_root):
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        assert lock.held is True
        assert (metadata_root / "lock").is_file()
        assert os.fstat(lock.metadata_root_fd).st_ino == os.stat(metadata_root).st_ino


def test_acquire_is_idempotent_over_an_existing_root(linux_backend, metadata_root):
    with acquire_project_lock(linux_backend, str(metadata_root)):
        pass
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        assert lock.held is True


def test_metadata_root_path_is_normalized_and_absolute(linux_backend, metadata_root):
    unnormalized = f"{metadata_root}/./"
    with acquire_project_lock(linux_backend, unnormalized) as lock:
        assert lock.metadata_root_path == os.path.abspath(str(metadata_root))


def test_establish_root_normalizes_only_after_the_guarded_walk(linux_backend, test_volume):
    # os.path.abspath calls normpath, which collapses 'aliased/..' lexically to
    # test_volume — a real directory that exists — so a normalize-first
    # implementation would succeed here and never show the kernel the symlink.
    # The path the caller wrote must reach openat2 with its spelling intact.
    real = test_volume / "real"
    real.mkdir()
    (test_volume / "aliased").symlink_to(real)
    with pytest.raises(OSError) as caught:
        establish_root(linux_backend, str(test_volume / "aliased" / ".."), create=False)
    assert caught.value.errno == errno.ELOOP


def test_establish_root_resolves_a_parent_component_after_a_real_directory(
    linux_backend, test_volume
):
    # The converse of the test above, so the guard is not merely refusing every '..':
    # with no symlink in the path, the lexical and kernel resolutions agree, and
    # normalization after the walk is what produces the returned pathname.
    (test_volume / "real").mkdir()
    fd, normalized, created = establish_root(
        linux_backend, str(test_volume / "real" / ".."), create=False
    )
    try:
        assert created is False
        assert normalized == os.path.abspath(str(test_volume))
        assert os.fstat(fd).st_ino == os.stat(test_volume).st_ino
    finally:
        os.close(fd)


def test_establish_root_parent_close_failure_releases_the_child(
    linux_backend, metadata_root, monkeypatch
):
    # Once open_child_directory returns, the child must be owned before releasing its
    # parent: a failed parent close otherwise exits with no returned fd and leaks the
    # child. The injected close really releases the parent before raising, so the fd
    # count isolates the child rather than counting an intentionally failed release.
    real_close = os.close
    calls = 0

    def fail_first_close(fd):
        nonlocal calls
        calls += 1
        real_close(fd)
        if calls == 1:
            raise OSError(errno.EIO, "injected parent release failure")

    before = len(os.listdir("/proc/self/fd"))
    with monkeypatch.context() as patched:
        patched.setattr("atoms.fs.lock.os.close", fail_first_close)
        with pytest.raises(OSError) as caught:
            establish_root(linux_backend, str(metadata_root), create=True)
    assert caught.value.errno == errno.EIO
    assert calls == 2, "the child must be released after the parent release fails"
    assert len(os.listdir("/proc/self/fd")) == before


def test_establish_root_fstat_failure_releases_the_child(
    linux_backend, metadata_root, monkeypatch
):
    # Validation can raise rather than merely report the wrong type. The fresh child
    # descriptor is already owned when fstat runs, so both outcomes release it.
    def fail_fstat(fd):
        raise OSError(errno.EIO, "injected child validation failure")

    before = len(os.listdir("/proc/self/fd"))
    with monkeypatch.context() as patched:
        patched.setattr("atoms.fs.lock.os.fstat", fail_fstat)
        with pytest.raises(OSError) as caught:
            establish_root(linux_backend, str(metadata_root), create=True)
    assert caught.value.errno == errno.EIO
    assert len(os.listdir("/proc/self/fd")) == before


def test_acquire_refuses_a_parent_component_as_the_leaf(linux_backend, metadata_root):
    # Because normalization is deferred, '..' can still be the final component when
    # the creation branch is reached. mkdir('..') is not a coherent request.
    with pytest.raises(ProtocolError, match="final component"):
        acquire_project_lock(linux_backend, str(metadata_root / "absent" / ".."))


def test_acquire_refuses_a_symlink_at_lock(linux_backend, metadata_root):
    metadata_root.mkdir(parents=True)
    (metadata_root / "lock").symlink_to("/etc/passwd")
    with pytest.raises(OSError) as caught:
        with acquire_project_lock(linux_backend, str(metadata_root)):
            pass
    assert caught.value.errno == errno.ELOOP


def test_acquire_refuses_a_directory_at_lock(linux_backend, metadata_root):
    metadata_root.mkdir(parents=True)
    (metadata_root / "lock").mkdir()
    with pytest.raises((OSError, ProtocolError)):
        with acquire_project_lock(linux_backend, str(metadata_root)):
            pass


def test_acquire_refuses_a_missing_parent(linux_backend, metadata_root):
    # A4a creates only the final leaf, never intermediate directories.
    deep = metadata_root / "absent" / "store"
    with pytest.raises(OSError) as caught:
        with acquire_project_lock(linux_backend, str(deep)):
            pass
    assert caught.value.errno == errno.ENOENT


def test_absent_anchored_traversal_refuses_with_capability_unavailable(
    metadata_root, fake_backend
):
    # Design §10 assigns an unavailable bootstrap prerequisite to
    # CapabilityUnavailable, not a bare OSError: nothing has gone wrong at the
    # syscall level, the volume simply cannot supply the guarded walk. This is also
    # the shape a kernel without openat2 takes, which reports ENOSYS.
    backend = fake_backend(supplied=set(Capability) - {Capability.ANCHORED_TRAVERSAL})
    with pytest.raises(CapabilityUnavailable, match="anchored_traversal"):
        acquire_project_lock(backend, str(metadata_root))


def test_openat2_enosys_refuses_with_capability_unavailable(metadata_root, fake_backend):
    # A current test kernel supplies openat2, so force its operational absence.
    # This must take the shared traversal conversion path, not escape as OSError.
    backend = fake_backend(supplied=set(Capability), traversal_errno=errno.ENOSYS)
    with pytest.raises(CapabilityUnavailable, match="anchored_traversal"):
        acquire_project_lock(backend, str(metadata_root))


def test_absent_advisory_lock_refuses_with_capability_unavailable(
    metadata_root, fake_backend
):
    backend = fake_backend(supplied=set(Capability) - {Capability.ADVISORY_PROJECT_LOCK})
    with pytest.raises(CapabilityUnavailable, match="advisory_project_lock"):
        acquire_project_lock(backend, str(metadata_root))


def test_sets_the_sync_ignore_marker_on_creation(linux_backend, metadata_root):
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        try:
            value = os.getxattr(lock.metadata_root_fd, "user.com.dropbox.ignored")
        except OSError as caught:
            # getxattr(2): ENOTSUP/EOPNOTSUPP means xattrs are unsupported or
            # disabled. ENODATA means the implementation failed to create this
            # specific marker and must fail rather than laundering the defect as a
            # platform skip.
            if caught.errno in {errno.ENOTSUP, errno.EOPNOTSUPP}:
                pytest.skip("filesystem does not support user extended attributes")
            raise
        assert value == b"1"


def test_sync_ignore_marker_failure_does_not_fail_bootstrap(
    linux_backend, metadata_root, monkeypatch
):
    def refuse(*args, **kwargs):
        raise OSError(95, "Operation not supported")

    monkeypatch.setattr("atoms.fs.lock.os.setxattr", refuse)
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        assert lock.held is True


def test_accessors_refuse_after_release(linux_backend, metadata_root):
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        pass
    assert lock.held is False
    with pytest.raises(ProtocolError):
        lock.metadata_root_fd
    with pytest.raises(ProtocolError):
        lock.metadata_root_path
    with pytest.raises(ProtocolError):
        lock.backend


def test_release_is_idempotent(linux_backend, metadata_root):
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        pass
    lock.__exit__(None, None, None)
    assert lock.held is False


def test_exceptional_exit_still_releases(linux_backend, metadata_root):
    with pytest.raises(RuntimeError):
        with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
            raise RuntimeError("boom")
    assert lock.held is False


def test_close_all_attempts_every_descriptor_and_raises_the_first_failure(test_volume):
    # A failed close does not un-open the descriptors after it, so a loop that lets the
    # first failure escape leaks every later one for the process lifetime — and in the
    # lock's case `held` is already False by then, so nothing ever retries. This is the
    # release path every multi-descriptor site delegates to: HeldProjectLock.__exit__,
    # ensure_metadata_layout's unwind, and bind_project_volume's finally.
    #
    # The failure is real rather than monkeypatched: closing an already-closed
    # descriptor fails with EBADF, and patching os.close would replace it process-wide
    # for everything running inside the window.
    first = os.open(str(test_volume), os.O_RDONLY | os.O_CLOEXEC)
    second = os.open(str(test_volume), os.O_RDONLY | os.O_CLOEXEC)
    os.close(first)

    with pytest.raises(OSError) as caught:
        close_all((first, second))
    assert caught.value.errno == errno.EBADF
    # fstat rather than a second close: it proves `second` was reached without risking
    # closing whatever might have taken that number.
    with pytest.raises(OSError) as reached:
        os.fstat(second)
    assert reached.value.errno == errno.EBADF


def test_close_all_raises_the_first_of_several_failures(monkeypatch):
    # The test above has one real failure, so it proves "keep going" but not the stated
    # precedence. Two failures with distinct errnos make "the FIRST failure" observable:
    # a `first = caught` that kept overwriting would surface ENOSPC instead.
    #
    # The descriptors are fictitious numbers, never closed, because failing_close raises
    # before touching them. The patch is scoped to one call: `atoms.fs.lock.os` IS the
    # os module, so a wider window would replace close for everything inside it.
    codes = {4242: errno.EIO, 4243: errno.ENOSPC}
    attempted = []

    def failing_close(fd):
        attempted.append(fd)
        raise OSError(codes[fd], "injected")

    with monkeypatch.context() as patched:
        patched.setattr("atoms.fs.lock.os.close", failing_close)
        with pytest.raises(OSError) as caught:
            close_all((4242, 4243))
    assert caught.value.errno == errno.EIO, "the first failure is what propagates"
    assert attempted == [4242, 4243], "a failure must not stop the remaining closes"


def test_lock_refuses_ordinary_construction():
    # Downstream signatures treat the type as proof a real lock is held, so a bare
    # object must not be able to fabricate that proof. Matches the CompiledSpec and
    # RecoveryPlan guards, which also raise TypeError.
    with pytest.raises(TypeError, match="acquire_project_lock"):
        HeldProjectLock()


def test_lock_refuses_dataclass_replacement():
    # HeldProjectLock is deliberately NOT a dataclass — it owns descriptors and a
    # spent flag, which are not value semantics — so replace() refuses for want of
    # __dataclass_fields__ rather than for want of the token. Either way, no copy of
    # a held lock can be fabricated, which is what downstream signatures rely on.
    with pytest.raises(TypeError):
        dataclasses.replace(object.__new__(HeldProjectLock))


_CONTENDER = (
    "import fcntl, sys\n"
    "handle = open(sys.argv[1], 'r+')\n"
    "try:\n"
    "    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "except BlockingIOError:\n"
    "    sys.exit(3)\n"
    "sys.exit(0)\n"
)


def test_a_second_process_cannot_acquire_the_same_lock(linux_backend, metadata_root):
    with acquire_project_lock(linux_backend, str(metadata_root)):
        finished = subprocess.run(
            [sys.executable, "-c", _CONTENDER, str(metadata_root / "lock")], timeout=30
        )
    assert finished.returncode == 3


def test_a_second_process_can_acquire_once_the_lock_is_released(
    linux_backend, metadata_root
):
    # The other direction, which design §11.3 requires: without it, a lock that
    # never grants to anyone would satisfy the exclusion test above.
    with acquire_project_lock(linux_backend, str(metadata_root)):
        pass
    finished = subprocess.run(
        [sys.executable, "-c", _CONTENDER, str(metadata_root / "lock")], timeout=30
    )
    assert finished.returncode == 0
```

- [ ] **Step 2: Add the root fixtures, the restricted backend, and the layout owner**

The restricted backend arrives here rather than in Task 5 because Task 4 is the first task that must
refuse on an absent capability, and its tests need a backend that withholds one.

Append to `python/tests/fs_support.py`:

```python
import errno as _errno

from atoms.core.capabilities import Capability
from atoms.fs.linux import LinuxBackend


def make_metadata_root(base):
    """A path that does NOT yet exist, so bootstrap creation is exercised."""
    return base / "metadata"


def make_project_root(base):
    root = base / "project"
    root.mkdir()
    return root


class RestrictedBackend:
    """A capability-restricted backend proving the protocol admits a non-Linux one.

    It delegates to a real LinuxBackend for supplied capabilities and raises a
    chosen errno for absent ones, so every refusal branch is reachable without a
    filesystem that genuinely lacks the operation.

    A method-level key such as `open_child_directory_errno` targets one concrete
    method. A contract-level key from UNSUPPORTED_ERRNO, such as `traversal_errno`
    or `flush_errno`, targets every method implementing that capability. The latter
    is what lets the table-derived mutation matrix cover every operation key.

    `override_names` narrows either form to specific final components. This is
    load-bearing, not a convenience: a probe whose evidence is a refusal calls the
    same operation twice — once to establish availability, once to require the
    refusal — and an unscoped override fails the FIRST call, so the test would pass
    through the availability path and keep passing if the refusal check were
    weakened to accept any OSError. Scoping by name puts the injection at the step
    under test. A method that passes no `name` is never overridden while
    `override_names` is set, which is the intended reading of "only these names".
    """

    _ABSENT_ERRNO = _errno.EOPNOTSUPP

    def __init__(self, supplied, lock_excludes=True, override_names=None, **errno_overrides):
        self._supplied = set(supplied)
        self._lock_excludes = lock_excludes
        self._override_names = None if override_names is None else frozenset(override_names)
        self._overrides = errno_overrides
        self._real = LinuxBackend()

    def _dispatch(self, capability, operation, *args, contract=None, name=None):
        # Method-level overrides preserve the named-refusal injection used by the
        # exact-errno guard tests. Contract-level overrides key directly from
        # UNSUPPORTED_ERRNO and drive its complete generated matrix.
        override = self._overrides.get(f"{operation}_errno")
        if override is None and contract is not None:
            override = self._overrides.get(f"{contract}_errno")
        if override is not None and (
            self._override_names is None or name in self._override_names
        ):
            raise OSError(override, "injected")
        if capability not in self._supplied:
            raise OSError(self._ABSENT_ERRNO, "capability withheld")
        return getattr(self._real, operation)(*args)

    def open_root(self, path):
        return self._dispatch(
            Capability.ANCHORED_TRAVERSAL,
            "open_root",
            path,
            contract="traversal",
            name=path,
        )

    def open_child_directory(self, parent_fd, name):
        return self._dispatch(
            Capability.ANCHORED_TRAVERSAL,
            "open_child_directory",
            parent_fd,
            name,
            contract="traversal",
            name=name,
        )

    def exchange(self, parent_fd, left, right):
        return self._dispatch(
            Capability.ATOMIC_EXCHANGE,
            "exchange",
            parent_fd,
            left,
            right,
            contract="exchange",
        )

    def transfer_noclobber(self, src_fd, src, dst_fd, dst):
        return self._dispatch(
            Capability.NOCLOBBER_TRANSFER,
            "transfer_noclobber",
            src_fd,
            src,
            dst_fd,
            dst,
            contract="transfer_noclobber",
        )

    def link_anchor(self, src_fd, src, dst_fd, dst):
        return self._dispatch(
            Capability.IDENTITY_ANCHOR,
            "link_anchor",
            src_fd,
            src,
            dst_fd,
            dst,
            contract="link_anchor",
        )

    def flush_file(self, fd):
        return self._dispatch(
            Capability.DURABLE_PUBLISH, "flush_file", fd, contract="flush"
        )

    def flush_directory(self, fd):
        return self._dispatch(
            Capability.DURABLE_PUBLISH, "flush_directory", fd, contract="flush"
        )

    def open_regular_nofollow(self, parent_fd, name):
        return self._dispatch(
            Capability.NOFOLLOW_COHERENT_READ,
            "open_regular_nofollow",
            parent_fd,
            name,
            contract="open_regular_nofollow",
            name=name,
        )

    def symlink_fingerprint(self, parent_fd, name):
        return self._dispatch(
            Capability.SYMLINK_FINGERPRINT,
            "symlink_fingerprint",
            parent_fd,
            name,
            contract="symlink_fingerprint",
            name=name,
        )

    def lock_exclusive(self, fd):
        return self._dispatch(
            Capability.ADVISORY_PROJECT_LOCK,
            "lock_exclusive",
            fd,
            contract="lock",
        )

    def try_lock_exclusive(self, fd):
        if not self._lock_excludes:
            # A filesystem where flock succeeds but does not actually exclude —
            # the real case on NFS without a working lock daemon.
            return True
        return self._dispatch(
            Capability.ADVISORY_PROJECT_LOCK,
            "try_lock_exclusive",
            fd,
            contract="lock",
        )


def make_fake_backend():
    def build(supplied, lock_excludes=True, override_names=None, **errno_overrides):
        return RestrictedBackend(
            supplied,
            lock_excludes=lock_excludes,
            override_names=override_names,
            **errno_overrides,
        )

    return build
```

Append to `python/tests/conftest.py`:

```python
from atoms.fs.lock import acquire_project_lock
from tests.fs_support import make_fake_backend, make_metadata_root, make_project_root


@pytest.fixture
def metadata_root(test_volume):
    return make_metadata_root(test_volume)


@pytest.fixture
def project_root(test_volume):
    return make_project_root(test_volume)


@pytest.fixture
def held_lock(linux_backend):
    def acquire(metadata_root):
        return acquire_project_lock(linux_backend, str(metadata_root))

    return acquire


@pytest.fixture
def fake_backend():
    return make_fake_backend()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_lock.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atoms.fs.lock'`

- [ ] **Step 4: Implement the lock and root establishment**

Create `python/src/atoms/fs/lock.py`:

```python
"""Root establishment and the advisory project lock (design §5.4, §7.1)."""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterable

from atoms.core.errors import CapabilityUnavailable, ProtocolError
from atoms.fs.backend import UNSUPPORTED_ERRNO, Backend

SYNC_IGNORE_ATTRIBUTE = "user.com.dropbox.ignored"

_TOKEN = object()


def close_all(fds: Iterable[int]) -> None:
    """Close every descriptor, attempting each, then raise the first failure.

    A failed close does not un-open the descriptors after it, so a plain loop that
    lets the first failure escape leaks every later one for the process lifetime.
    The first failure is the one raised, because it is the one with a cause; later
    ones are almost always the same cause repeated.

    Used wherever more than one descriptor is released at once — the lock's two, and
    the layout's one per component — so the rule lives in one place rather than being
    re-derived at each site. Called from a `finally` or an unwind path, a failure here
    propagates and the original exception becomes its `__context__`.
    """
    first: OSError | None = None
    for fd in fds:
        try:
            os.close(fd)
        except OSError as caught:
            if first is None:
                first = caught
    if first is not None:
        raise first


def _guarded_spelling(path: str) -> str:
    """Absolute form of `path` with empty and '.' components dropped, '..' KEPT.

    os.path.abspath must NOT be used before the guarded walk: it calls normpath,
    which collapses '..' lexically, so 'aliased/../elsewhere' becomes 'elsewhere'
    and RESOLVE_NO_SYMLINKS never sees 'aliased' at all. Dropping '' and '.' is safe
    under any symlink arrangement — neither changes which entry a path names — but
    every '..' must reach the kernel, which refuses it exactly when an earlier
    component is a symlink and resolves it normally otherwise.

    os.getcwd() is itself symlink-free, so prefixing it introduces no component the
    kernel would have to resolve.
    """
    absolute = path if os.path.isabs(path) else os.path.join(os.getcwd(), path)
    kept = [component for component in absolute.split(os.sep) if component not in ("", ".")]
    return os.sep + os.sep.join(kept)


def _guarded_open(backend: Backend, path: str) -> int:
    """open_root, converting an unavailable guarded walk per design §10.

    Reads the shared backend table rather than a local copy, so a kernel without
    openat2 surfaces identically here and in the probe.
    """
    try:
        return backend.open_root(path)
    except OSError as caught:
        if caught.errno in UNSUPPORTED_ERRNO["traversal"]:
            raise CapabilityUnavailable(
                f"anchored_traversal is unavailable, so no root can be guarded: {path!r}"
            ) from caught
        raise


def establish_root(backend: Backend, path: str, create: bool) -> tuple[int, str, bool]:
    """Open a root through guarded traversal, optionally creating its final leaf.

    Returns (descriptor, normalized path, created). Normalization runs only AFTER
    the guarded walk succeeds, and that ordering is the whole point: the walk proves
    no component is a symlink, and only then can collapsing '..' not change which
    entry the path names.
    """
    spelled = _guarded_spelling(path)
    try:
        fd = _guarded_open(backend, spelled)
    except OSError as caught:
        if not create or caught.errno != errno.ENOENT:
            raise
    else:
        return fd, os.path.normpath(spelled), False
    parent, leaf = os.path.split(spelled)
    if not leaf or leaf == "..":
        raise ProtocolError(
            f"cannot create a root whose final component is {leaf!r}: {path!r}"
        )
    # Only the final leaf is created, and only relative to a guarded parent
    # descriptor. A missing parent refuses: a component walk that creates as it
    # goes has races this layer has no need to take on.
    parent_fd = _guarded_open(backend, parent)
    try:
        os.mkdir(leaf, mode=0o700, dir_fd=parent_fd)
        # Reopen through guarded traversal even though we just created it, so the
        # descriptor is guard-checked on the same terms as the existing-root case.
        fd = backend.open_child_directory(parent_fd, leaf)
    except BaseException:
        os.close(parent_fd)
        raise
    # `fd` is owned from here on, so BOTH remaining steps run under that ownership.
    # A `finally: os.close(parent_fd)` around the block above would leak `fd` if that
    # close failed, and an `fstat` that raises leaves `fd` exactly as open as one
    # reporting the wrong type — the leak is in the validation, not just its verdict.
    try:
        os.close(parent_fd)
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise ProtocolError(f"created metadata root is not a directory: {spelled!r}")
    except BaseException:
        os.close(fd)
        raise
    return fd, os.path.normpath(spelled), True


class HeldProjectLock:
    """Proof that the project lock is held, with the metadata root open.

    Factory-controlled: acquire_project_lock is the sole construction authority,
    because reclaim_probe_survivors, probe_backend, and bind_project_volume all
    treat this type as proof rather than re-checking.
    """

    __slots__ = ("_backend", "_root_fd", "_root_path", "_lock_fd", "_held")

    def __init__(self, *, _construction_token: object | None = None, **kwargs) -> None:
        if _construction_token is not _TOKEN:
            raise TypeError("HeldProjectLock values are created only by acquire_project_lock")
        self._backend = kwargs["backend"]
        self._root_fd = kwargs["root_fd"]
        self._root_path = kwargs["root_path"]
        self._lock_fd = kwargs["lock_fd"]
        self._held = True

    def _require_held(self) -> None:
        if not self._held:
            raise ProtocolError("the project lock has been released")

    @property
    def backend(self) -> Backend:
        self._require_held()
        return self._backend

    @property
    def metadata_root_fd(self) -> int:
        self._require_held()
        return self._root_fd

    @property
    def metadata_root_path(self) -> str:
        self._require_held()
        return self._root_path

    @property
    def held(self) -> bool:
        return self._held

    def __enter__(self) -> HeldProjectLock:
        return self

    def __exit__(self, *exc) -> None:
        if not self._held:
            return
        self._held = False
        # Both descriptors are released even if the first close fails. A failed close
        # does not un-open the second, and stopping there would leak the metadata root
        # for the process lifetime — while `held` already reads False, so nothing would
        # ever retry it. The lock descriptor goes first: closing it is what releases
        # flock, and that must not depend on the root closing cleanly.
        close_all((self._lock_fd, self._root_fd))


def acquire_project_lock(backend: Backend, metadata_root: str) -> HeldProjectLock:
    root_fd, root_path, created = establish_root(backend, metadata_root, create=True)
    try:
        if created:
            # Best-effort: the metadata store is single-host by construction, so a
            # synced copy is neither required nor trusted. Any failure is swallowed
            # and weakens no single-host guarantee.
            try:
                os.setxattr(root_fd, SYNC_IGNORE_ATTRIBUTE, b"1")
            except OSError:
                pass
        lock_fd = os.open(
            "lock",
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=root_fd,
        )
    except BaseException:
        os.close(root_fd)
        raise
    try:
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise ProtocolError("metadata_root/lock is not a regular file")
        try:
            backend.lock_exclusive(lock_fd)
        except OSError as caught:
            if caught.errno in UNSUPPORTED_ERRNO["lock"]:
                raise CapabilityUnavailable(
                    "advisory_project_lock is unavailable on this volume, so access to "
                    "metadata_root cannot be serialized"
                ) from caught
            raise
    except BaseException:
        # One pass, not two sequential closes: a failure closing `lock_fd` must not
        # abandon `root_fd`, and this unwind runs on the CapabilityUnavailable path
        # that an unlockable volume takes, where a leak would be permanent.
        close_all((lock_fd, root_fd))
        raise
    return HeldProjectLock(
        _construction_token=_TOKEN,
        backend=backend,
        root_fd=root_fd,
        root_path=root_path,
        lock_fd=lock_fd,
    )
```

- [ ] **Step 5: Run the lock tests to verify they pass**

Run: `uv run pytest tests/test_fs_lock.py -v`
Expected: PASS. Every test in the named files must pass; the only acceptable non-pass is a
`test_volume`/`distinct_volume` skip with its stated reason. Do not accept a bare count as
evidence — read the skip lines.

- [ ] **Step 6: Add the layout owner and write the failing bootstrap tests**

`ensure_metadata_layout` returns one retained descriptor per component, so every **successful** caller
must own the returned mapping. Successful tests go through a context manager that closes each descriptor
exactly once. A failure-path test may call it directly only when it requires an exception before any
mapping is returned and verifies that the function unwound every descriptor it had acquired.

Append to `python/tests/fs_support.py`:

```python
import contextlib

from atoms.fs.bootstrap import close_layout, ensure_metadata_layout


@contextlib.contextmanager
def metadata_layout(lock):
    """Own the descriptors ensure_metadata_layout returns; close each exactly once.

    A test that drops the return value leaks one descriptor per layout component and
    silently violates the plan's own close-exactly-once audit, which is why the audit
    gets a helper rather than a reminder. Release goes through the same close_layout
    production uses, so the helper cannot pass while production releases in a
    different order or with a weaker guarantee.
    """
    retained = ensure_metadata_layout(lock)
    try:
        yield retained
    finally:
        close_layout(retained)
```

Create `python/tests/test_fs_bootstrap.py`:

```python
import errno
import os
import stat

import pytest

from atoms.core.errors import ProtocolError
from atoms.fs.bootstrap import (
    METADATA_LAYOUT,
    ensure_metadata_layout,
    reclaim_probe_survivors,
    verified_child_path,
)
from atoms.fs.lock import close_all
from tests.fs_support import metadata_layout


def test_layout_creates_every_directory(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        with metadata_layout(lock):
            pass
    for relative in METADATA_LAYOUT:
        assert (metadata_root / relative).is_dir()


def test_layout_returns_one_owned_descriptor_per_component(held_lock, metadata_root):
    # The return value is the ownership contract: one descriptor per component,
    # each a directory, each O_CLOEXEC, and each the caller's to close.
    with held_lock(metadata_root) as lock:
        with metadata_layout(lock) as retained:
            assert sorted(retained) == sorted(METADATA_LAYOUT)
            for relative, fd in retained.items():
                assert stat.S_ISDIR(os.fstat(fd).st_mode), relative
                assert os.get_inheritable(fd) is False, relative


def test_layout_descriptors_are_released_in_reverse_opening_order(
    held_lock, metadata_root, monkeypatch
):
    # Design §7.2 and §9.3: nested descriptors are released child-before-parent, so no
    # release depends on one already gone. `retained` is insertion-ordered by
    # METADATA_LAYOUT, which makes values() OPENING order — passing it straight to
    # close_all reverses nothing and reads as correct, which is why the order is
    # pinned here rather than left to the comment in close_layout.
    calls = []

    def recording(fds):
        order = list(fds)
        calls.append(order)
        close_all(order)

    monkeypatch.setattr("atoms.fs.bootstrap.close_all", recording)
    with held_lock(metadata_root) as lock:
        with metadata_layout(lock) as retained:
            opening_order = [retained[relative] for relative in METADATA_LAYOUT]
    # The last close_all is close_layout's; earlier ones released the intermediate
    # `blobs` descriptor on the way to `blobs/sha256`.
    assert calls[-1] == list(reversed(opening_order))
    assert calls[-1] != opening_order, "four distinct descriptors, so this is not a tie"


def test_failed_intermediate_release_unwinds_the_retained_layout(
    held_lock, metadata_root, monkeypatch
):
    # `blobs/sha256` opens an intermediate `blobs` descriptor before the retained
    # leaf. If releasing that intermediate fails after the leaf enters `retained`,
    # ensure_metadata_layout returns no mapping for the caller to own; it must unwind
    # the whole retained set itself.
    real_close_all = close_all
    injected = False

    def fail_nonempty_intermediate(fds):
        nonlocal injected
        order = list(fds)
        real_close_all(order)
        if order and not injected:
            injected = True
            raise OSError(errno.EIO, "injected intermediate release failure")

    with held_lock(metadata_root) as lock:
        before = len(os.listdir("/proc/self/fd"))
        monkeypatch.setattr("atoms.fs.bootstrap.close_all", fail_nonempty_intermediate)
        with pytest.raises(OSError) as caught:
            ensure_metadata_layout(lock)
        assert caught.value.errno == errno.EIO
        assert injected is True
        assert len(os.listdir("/proc/self/fd")) == before


def test_layout_fstat_failure_releases_every_owned_descriptor(
    held_lock, metadata_root, monkeypatch
):
    # _open_or_create_child owns each fresh descriptor before validating it. A
    # validation syscall that raises therefore releases the fresh descriptor plus
    # every descriptor retained by earlier layout components.
    real_fstat = os.fstat
    calls = 0

    def fail_second_validation(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(errno.EIO, "injected layout validation failure")
        return real_fstat(fd)

    with held_lock(metadata_root) as lock:
        before = len(os.listdir("/proc/self/fd"))
        monkeypatch.setattr("atoms.fs.bootstrap.os.fstat", fail_second_validation)
        with pytest.raises(OSError) as caught:
            ensure_metadata_layout(lock)
        assert caught.value.errno == errno.EIO
        assert len(os.listdir("/proc/self/fd")) == before


def test_layout_is_idempotent_over_existing_directories(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        with metadata_layout(lock):
            pass
        with metadata_layout(lock) as retained:
            assert sorted(retained) == sorted(METADATA_LAYOUT)
    assert (metadata_root / "blobs" / "sha256").is_dir()


def test_layout_refuses_a_symlink_occupying_a_name(held_lock, metadata_root):
    # Tolerating EEXIST without reopening would adopt whatever occupies the name.
    # Failure-path tests call ensure_metadata_layout directly legitimately: it
    # raises, returns nothing, and closes what it had already opened, so there is no
    # descriptor mapping for a caller to own.
    with held_lock(metadata_root) as lock:
        os.symlink("/etc", "staging", dir_fd=lock.metadata_root_fd)
        with pytest.raises((OSError, ProtocolError)):
            ensure_metadata_layout(lock)


def test_layout_refuses_a_regular_file_occupying_a_name(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        fd = os.open("work", os.O_CREAT | os.O_WRONLY, 0o600, dir_fd=lock.metadata_root_fd)
        os.close(fd)
        with pytest.raises((OSError, ProtocolError)):
            ensure_metadata_layout(lock)


def test_reclamation_is_a_noop_when_probe_is_absent(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        reclaim_probe_survivors(lock)
        assert not (metadata_root / "probe").exists()


def test_reclamation_empties_a_real_probe_directory(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        with metadata_layout(lock):
            probe = metadata_root / "probe"
            (probe / "nested").mkdir()
            (probe / "nested" / "file").write_text("debris")
            (probe / "loose").write_text("debris")
            reclaim_probe_survivors(lock)
            assert probe.is_dir()
            assert list(probe.iterdir()) == []


def test_reclamation_does_not_follow_a_symlink_out_of_probe(held_lock, metadata_root, test_volume):
    outside = test_volume / "outside"
    outside.write_text("must survive")
    with held_lock(metadata_root) as lock:
        with metadata_layout(lock):
            (metadata_root / "probe" / "escape").symlink_to(outside)
            reclaim_probe_survivors(lock)
    assert outside.read_text() == "must survive"
    assert list((metadata_root / "probe").iterdir()) == []


def test_reclamation_refuses_a_symlink_at_probe_itself(held_lock, metadata_root, test_volume):
    # Reclamation runs before layout creation, so it, not the EEXIST path,
    # meets an anomalous probe/ first. It refuses rather than unlinking a leaf
    # A4a did not create.
    elsewhere = test_volume / "elsewhere"
    elsewhere.mkdir()
    with held_lock(metadata_root) as lock:
        os.symlink(str(elsewhere), "probe", dir_fd=lock.metadata_root_fd)
        with pytest.raises((OSError, ProtocolError)):
            reclaim_probe_survivors(lock)
        assert os.readlink("probe", dir_fd=lock.metadata_root_fd) == str(elsewhere)


def test_reclamation_refuses_a_regular_file_at_probe(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        fd = os.open("probe", os.O_CREAT | os.O_WRONLY, 0o600, dir_fd=lock.metadata_root_fd)
        os.close(fd)
        with pytest.raises((OSError, ProtocolError)):
            reclaim_probe_survivors(lock)


def test_verified_child_path_joins_after_confirming_identity(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        info = os.fstat(lock.metadata_root_fd)
        resolved = verified_child_path(
            lock.metadata_root_fd, lock.metadata_root_path, info.st_dev, info.st_ino, "atoms.db"
        )
    assert resolved == os.path.join(os.path.abspath(str(metadata_root)), "atoms.db")


def test_verified_child_path_refuses_a_non_component(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        info = os.fstat(lock.metadata_root_fd)
        for name in ("../escape", "nested/child", "", "."):
            with pytest.raises(ProtocolError):
                verified_child_path(
                    lock.metadata_root_fd,
                    lock.metadata_root_path,
                    info.st_dev,
                    info.st_ino,
                    name,
                )


def test_verified_child_path_refuses_an_identity_mismatch(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        info = os.fstat(lock.metadata_root_fd)
        with pytest.raises(ProtocolError, match="identity"):
            verified_child_path(
                lock.metadata_root_fd,
                lock.metadata_root_path,
                info.st_dev,
                info.st_ino + 1,
                "atoms.db",
            )
```

- [ ] **Step 7: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_bootstrap.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atoms.fs.bootstrap'`

- [ ] **Step 8: Implement bootstrap**

Create `python/src/atoms/fs/bootstrap.py`:

```python
"""Metadata layout, probe reclamation, and verified pathnames (design §7.2, §7.3, §9.4)."""

from __future__ import annotations

import os
import stat

from atoms.core.errors import ProtocolError
from atoms.fs.lock import HeldProjectLock, close_all

METADATA_LAYOUT = ("probe", "staging", "work", "blobs/sha256")
PROBE_DIRECTORY = "probe"


def _open_or_create_child(backend, parent_fd: int, name: str) -> int:
    """mkdirat, tolerate EEXIST, then ALWAYS reopen through guarded traversal.

    Step two runs on both paths, created and pre-existing, and that is the point:
    tolerating EEXIST without reopening would accept whatever already occupies the
    name — a symlink pointing out of the store, or a regular file — as though we
    had created it.
    """
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    fd = backend.open_child_directory(parent_fd, name)
    # The verification runs under the descriptor's ownership, not beside it: an fstat
    # that raises leaves `fd` exactly as open as one that reports the wrong type, so
    # closing only on the wrong-type branch leaks on the other.
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise ProtocolError(f"metadata layout component is not a directory: {name!r}")
    except BaseException:
        os.close(fd)
        raise
    return fd


def close_layout(retained: dict[str, int]) -> None:
    """Release retained layout descriptors in reverse opening order (design §7.2, §9.3).

    `retained` is insertion-ordered by METADATA_LAYOUT, so `values()` is *opening*
    order and must be reversed. Reverse order is the rule for nested descriptors: a
    child is released before the parent it was reached through, so no release ever
    depends on a descriptor that is already gone.

    This is the single place that knows the ordering. Production and the test helper
    both call it, so neither can release in an order the other does not.
    """
    close_all(reversed(list(retained.values())))


def ensure_metadata_layout(lock: HeldProjectLock) -> dict[str, int]:
    """Create and verify every layout directory. Returns retained descriptors."""
    backend = lock.backend
    retained: dict[str, int] = {}
    for relative in METADATA_LAYOUT:
        parent_fd = lock.metadata_root_fd
        opened: list[int] = []
        try:
            for component in relative.split("/"):
                fd = _open_or_create_child(backend, parent_fd, component)
                opened.append(fd)
                parent_fd = fd
        except BaseException:
            # Every descriptor opened so far is released in one pass, so a close that
            # fails part-way does not abandon the rest. If a close does fail, that
            # failure propagates with the original refusal as its __context__ — the
            # same rule the binding's final reclamation follows.
            #
            # Acquisition order is `retained` then this component's `opened` chain,
            # parent to child, so reversing the concatenation is reverse acquisition
            # order: deepest first, and the lock's metadata root — which we never
            # opened — untouched.
            close_all(reversed([*retained.values(), *opened]))
            raise
        retained[relative] = opened[-1]
        # The intermediate ancestors of the retained leaf, deepest first.
        try:
            close_all(reversed(opened[:-1]))
        except BaseException:
            # The leaf is already in `retained`, so ownership cannot be returned to a
            # caller when an intermediate release fails. Unwind the complete retained
            # set before propagating that release failure.
            close_layout(retained)
            raise
    return retained


def _remove_tree(backend, parent_fd: int, name: str) -> None:
    try:
        entry = os.lstat(name, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(entry.st_mode):
        # Never follow a symlink: unlink the link itself.
        os.unlink(name, dir_fd=parent_fd)
        return
    child_fd = backend.open_child_directory(parent_fd, name)
    try:
        for inner in os.listdir(child_fd):
            _remove_tree(backend, child_fd, inner)
    finally:
        os.close(child_fd)
    os.rmdir(name, dir_fd=parent_fd)


def reclaim_probe_survivors(lock: HeldProjectLock) -> None:
    """Empty `probe/` unconditionally. Never creates it.

    Under the held lock there can be no live probe but our own, so no attempt is
    made to distinguish a live probe from debris. `probe/` itself is different:
    a symlink or non-directory there is refused rather than unlinked, because
    A4a should not destroy something it did not create, and because metadata_root
    is engine-owned space whose invariant is already broken if that occurs.
    """
    root_fd = lock.metadata_root_fd
    try:
        entry = os.lstat(PROBE_DIRECTORY, dir_fd=root_fd)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(entry.st_mode):
        raise ProtocolError(
            f"metadata_root/{PROBE_DIRECTORY} is not a directory; refusing to unlink it"
        )
    probe_fd = lock.backend.open_child_directory(root_fd, PROBE_DIRECTORY)
    try:
        for inner in os.listdir(probe_fd):
            _remove_tree(lock.backend, probe_fd, inner)
    finally:
        os.close(probe_fd)


def verified_child_path(
    metadata_root_fd: int,
    metadata_root_path: str,
    expected_device: int,
    expected_inode: int,
    name: str,
) -> str:
    """Return `<verified metadata_root>/<name>` after re-confirming the root's identity.

    SQLite is the authority's one path-addressed exception: sqlite3.connect opens
    by pathname through its own VFS, so no descriptor can participate. This is the
    verify-then-open the authority requires, performed at the moment of use rather
    than trusted from bootstrap. Both the SQLite probe and A5 go through it.
    """
    if not name or name in (".", "..") or "/" in name or os.sep in name:
        raise ProtocolError(f"not a single path component: {name!r}")
    info = os.fstat(metadata_root_fd)
    if (info.st_dev, info.st_ino) != (expected_device, expected_inode):
        raise ProtocolError("metadata root identity changed since binding")
    return os.path.join(metadata_root_path, name)
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fs_bootstrap.py tests/test_fs_lock.py -v`
Expected: PASS. Every test in the named files must pass; the only acceptable non-pass is a
`test_volume`/`distinct_volume` skip with its stated reason. Do not accept a bare count as
evidence — read the skip lines.

- [ ] **Step 10: Commit**

```bash
git add src/atoms/fs/lock.py src/atoms/fs/bootstrap.py tests/test_fs_lock.py \
        tests/test_fs_bootstrap.py tests/fs_support.py tests/conftest.py
git commit -m "feat(fs): bootstrap the engine-owned metadata root under the project lock"
```

---

### Task 5: Capability probing and SQLite-WAL certification

Probes establish **functional availability only**. The crash claim is carried solely by a matched
allowlist entry. Each probe constructs its own operands inside a directory it created under the held
lock, which is what licenses an ambiguous errno to conclude "absent".

**Files:**
- Create: `python/src/atoms/fs/probe.py`
- Create: `python/tests/test_fs_probe.py`
- Modify: `python/tests/fs_support.py`

**Interfaces:**
- Consumes: `Backend`, `UNSUPPORTED_ERRNO`, `HeldProjectLock`, `bootstrap.verified_child_path`,
  `atoms.core.capabilities.Capability`.
- Produces:
  - `probe_backend(backend: Backend, probe_root_fd: int, lock: HeldProjectLock) -> frozenset[Capability]`
  - `certify_sqlite_wal(database_path: str, cleanup: bool = False) -> None` raising
    `CapabilityUnavailable`, including when a certification child exceeds its bounded timeout
  - `ChildExit` with `OK`, `STALE_READ`, `ACQUIRED_WHILE_HELD`, `WRONG_REFUSAL`
  - `tests.fs_support.probe_directory(lock)` and `tests.fs_support.probe_database_path(lock)`
    context managers

- [ ] **Step 1: Add the probe-directory owner**

Append to `python/tests/fs_support.py`:

```python
@contextlib.contextmanager
def probe_directory(lock):
    """Yield an owned descriptor to `probe/` with the whole layout owned around it.

    Building the layout and then reopening `probe/` separately would strand the four
    layout descriptors, so the probe descriptor is taken from the layout itself.
    """
    with metadata_layout(lock) as retained:
        yield retained["probe"]


@contextlib.contextmanager
def probe_database_path(lock):
    """Yield the verified pathname of a throwaway database inside `probe/`.

    Goes through verified_child_path rather than joining, because that is the only
    sanctioned way a pathname escapes the descriptor discipline (design §9.4).
    """
    with metadata_layout(lock):
        info = os.fstat(lock.metadata_root_fd)
        probe_dir = verified_child_path(
            lock.metadata_root_fd, lock.metadata_root_path, info.st_dev, info.st_ino, "probe"
        )
        yield os.path.join(probe_dir, "certify.db")
```

Add `from atoms.fs.bootstrap import ensure_metadata_layout, verified_child_path` to the existing
`fs_support.py` bootstrap import rather than adding a second import line.

- [ ] **Step 2: Write the failing probe tests**

Create `python/tests/test_fs_probe.py`:

```python
import errno
import os
import sqlite3
import subprocess

import pytest

from atoms.core.capabilities import Capability
from atoms.core.errors import CapabilityUnavailable
from atoms.fs.probe import ChildExit, certify_sqlite_wal, probe_backend
from tests.fs_support import probe_database_path, probe_directory


def test_real_volume_supplies_every_capability(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        with probe_directory(lock) as probe_fd:
            supplied = probe_backend(lock.backend, probe_fd, lock)
    assert supplied == frozenset(Capability)


def test_probe_leaves_no_survivors(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        with probe_directory(lock) as probe_fd:
            probe_backend(lock.backend, probe_fd, lock)
            assert os.listdir(probe_fd) == []


def test_missing_exchange_is_reported_not_raised(held_lock, metadata_root, fake_backend):
    backend = fake_backend(supplied=set(Capability) - {Capability.ATOMIC_EXCHANGE})
    with held_lock(metadata_root) as lock:
        with probe_directory(lock) as probe_fd:
            supplied = probe_backend(backend, probe_fd, lock)
    assert supplied == frozenset(set(Capability) - {Capability.ATOMIC_EXCHANGE})


_OPTIONAL_CAPABILITIES = tuple(
    capability
    for capability in Capability
    if capability
    not in {Capability.ANCHORED_TRAVERSAL, Capability.ADVISORY_PROJECT_LOCK}
)


@pytest.mark.parametrize("absent", _OPTIONAL_CAPABILITIES)
def test_each_optional_capability_can_be_absent(held_lock, metadata_root, fake_backend, absent):
    backend = fake_backend(supplied=set(Capability) - {absent})
    with held_lock(metadata_root) as lock:
        with probe_directory(lock) as probe_fd:
            supplied = probe_backend(backend, probe_fd, lock)
    assert supplied == frozenset(set(Capability) - {absent})


def test_unexpected_errno_propagates_rather_than_reporting_absence(
    held_lock, metadata_root, fake_backend
):
    # EBADF is a bug or an environmental failure, never an unsupported operation.
    backend = fake_backend(supplied=set(Capability), exchange_errno=errno.EBADF)
    with held_lock(metadata_root) as lock:
        with probe_directory(lock) as probe_fd:
            with pytest.raises(OSError) as caught:
                probe_backend(backend, probe_fd, lock)
            assert caught.value.errno == errno.EBADF


@pytest.mark.parametrize("code", [errno.EBADF, errno.EIO])
@pytest.mark.parametrize("refused", ["escape", ".."])
def test_a_traversal_refusal_with_the_wrong_errno_propagates(
    held_lock, metadata_root, fake_backend, refused, code
):
    # The traversal probe reads two refusals as evidence its guard works — exactly
    # ELOOP for the symlink component, exactly EXDEV for the escape. Accepting any
    # OSError there would let a volume failing for an unrelated reason report
    # anchored_traversal present, which is the §10 propagation rule read backwards.
    #
    # override_names is what makes this test discriminate. Injected unconditionally,
    # the errno would arrive at the EARLIER availability open of "real" and propagate
    # from _supported, so the assertion would hold without _refused_with ever running
    # — and would keep holding if _refused_with were weakened to accept every OSError.
    # Scoped to one refusal target, each site is exercised on its own.
    backend = fake_backend(
        supplied=set(Capability),
        open_child_directory_errno=code,
        override_names={refused},
    )
    with held_lock(metadata_root) as lock:
        with probe_directory(lock) as probe_fd:
            with pytest.raises(OSError) as caught:
                probe_backend(backend, probe_fd, lock)
            assert caught.value.errno == code


@pytest.mark.parametrize("code", [errno.EBADF, errno.EIO])
def test_a_nofollow_refusal_with_the_wrong_errno_propagates(
    held_lock, metadata_root, fake_backend, code
):
    # Scoped to "alias", the symlink leaf whose ELOOP refusal is the evidence; the
    # "payload" open that establishes availability still succeeds.
    backend = fake_backend(
        supplied=set(Capability),
        open_regular_nofollow_errno=code,
        override_names={"alias"},
    )
    with held_lock(metadata_root) as lock:
        with probe_directory(lock) as probe_fd:
            with pytest.raises(OSError) as caught:
                probe_backend(backend, probe_fd, lock)
            assert caught.value.errno == code


def test_a_failed_second_child_open_leaks_no_descriptor(held_lock, metadata_root, fake_backend):
    # Both child descriptors are acquired inside the cleanup scope, so failing on the
    # second still releases the first. The distinct-parent probes are the only place
    # two are held at once, and a `src_fd = ...; dst_fd = ...; try:` prologue leaks
    # src_fd on exactly this path — invisibly, because the probe still refuses
    # correctly and only the descriptor count betrays it.
    backend = fake_backend(
        supplied=set(Capability),
        open_child_directory_errno=errno.EIO,
        override_names={"dst"},
    )
    with held_lock(metadata_root) as lock:
        with probe_directory(lock) as probe_fd:
            # /proc/self/fd includes the descriptor listdir itself holds, which is the
            # lowest free one in both calls, so the count is stable without a leak.
            before = len(os.listdir("/proc/self/fd"))
            with pytest.raises(OSError) as caught:
                probe_backend(backend, probe_fd, lock)
            assert caught.value.errno == errno.EIO
            assert len(os.listdir("/proc/self/fd")) == before
            assert os.listdir(probe_fd) == []


def test_sqlite_wal_certification_succeeds_on_the_test_volume(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        with probe_database_path(lock) as database:
            certify_sqlite_wal(database)


def test_sqlite_certification_observes_the_commit_across_processes(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        with probe_database_path(lock) as database:
            certify_sqlite_wal(database)
            # The second child's committed user_version=2 must be what survives.
            connection = sqlite3.connect(database)
            try:
                assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
            finally:
                connection.close()


def test_certification_uses_two_children_so_the_parent_can_release_between_them(
    held_lock, metadata_root, monkeypatch
):
    # The parent must release the write lock BETWEEN the contending child and the
    # writing child. One blocking child cannot express that: it would wait for the
    # parent's commit while the parent waited for it to exit, and the choreography
    # would resolve only by one side timing out. Two invocations make the release
    # point explicit, and this test is what stops a later "simplification" back to
    # one child from looking harmless.
    real_run = subprocess.run
    scripts = []

    def recording_run(argv, **kwargs):
        scripts.append(argv[2])
        return real_run(argv, **kwargs)

    monkeypatch.setattr("atoms.fs.probe.subprocess.run", recording_run)
    with held_lock(metadata_root) as lock:
        with probe_database_path(lock) as database:
            certify_sqlite_wal(database)
    assert len(scripts) == 2
    assert "user_version=2" not in scripts[0], "the contending child must not write"
    assert "user_version=2" in scripts[1], "the writing child must run after the release"


@pytest.mark.parametrize(("failing_child", "phase"), [(0, "contention"), (1, "write")])
def test_a_certification_child_that_times_out_refuses(
    held_lock, metadata_root, monkeypatch, failing_child, phase
):
    # A child that never finishes is the failure mode a volume with broken locking is
    # most likely to produce. TimeoutExpired escaping would put it outside the §10
    # contract, where every other certification failure already lands, and would hand
    # A5 a third exception type to know about. The phase must be named: "the write
    # child hung" and "the contention child hung" are different volume diagnoses.
    real_run = subprocess.run
    calls = []

    def timing_out_run(argv, **kwargs):
        calls.append(argv)
        if len(calls) - 1 == failing_child:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return real_run(argv, **kwargs)

    monkeypatch.setattr("atoms.fs.probe.subprocess.run", timing_out_run)
    with held_lock(metadata_root) as lock:
        with probe_database_path(lock) as database:
            with pytest.raises(CapabilityUnavailable, match=phase):
                certify_sqlite_wal(database)


def test_sqlite_certification_removes_its_files(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        with probe_database_path(lock) as database:
            certify_sqlite_wal(database, cleanup=True)
            # All three of the database, -wal, and -shm names must be gone, so the
            # assertion is on the directory rather than on the three names.
            assert os.listdir(os.path.dirname(database)) == []


def test_sqlite_certification_refuses_when_wal_is_unavailable(tmp_path, monkeypatch):
    class Cursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchone(self):
            return self._rows[0] if self._rows else None

    class RefusingConnection:
        def execute(self, statement, *args):
            # Production calls .fetchone() on what execute returns, so the fake must
            # be cursor-shaped. Returning a bare list raises AttributeError and the
            # test would pass for the wrong reason.
            if "journal_mode" in statement:
                return Cursor([("delete",)])
            return Cursor([])

        def close(self):
            pass

    monkeypatch.setattr("atoms.fs.probe.sqlite3.connect", lambda *a, **k: RefusingConnection())
    with pytest.raises(CapabilityUnavailable, match="WAL"):
        certify_sqlite_wal(str(tmp_path / "x.db"))


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (ChildExit.STALE_READ, "could not read"),
        (ChildExit.ACQUIRED_WHILE_HELD, "acquired the write lock"),
        (ChildExit.WRONG_REFUSAL, "SQLITE_BUSY"),
    ],
)
def test_each_child_verdict_refuses_with_its_own_reason(
    held_lock, metadata_root, monkeypatch, code, expected
):
    # Exit codes are named, not literal, so a reordering cannot silently remap
    # 'refused with the wrong result code' onto 'read the wrong value'. WRONG_REFUSAL
    # is the one that matters most: it is how a volume whose WAL index never opens
    # is kept from masquerading as one that correctly excludes a second writer.
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, code, b"", b"")

    monkeypatch.setattr("atoms.fs.probe.subprocess.run", fake_run)
    with held_lock(metadata_root) as lock:
        with probe_database_path(lock) as database:
            with pytest.raises(CapabilityUnavailable, match=expected):
                certify_sqlite_wal(database)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_probe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atoms.fs.probe'`

- [ ] **Step 4: Implement the capability probes**

Create `python/src/atoms/fs/probe.py`:

```python
"""Empirical capability probing (design §8).

These are FUNCTIONAL probes, not conformance tests. They establish that an
operation is present and behaves correctly on this volume right now; they cannot
establish its power-loss guarantee. Flushing a file and its parent successfully is
availability evidence only. The crash claim is carried solely by a matched
allowlist entry.
"""

from __future__ import annotations

import contextlib
import errno
import os
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Iterator

from atoms.core.capabilities import Capability
from atoms.core.errors import CapabilityUnavailable
from atoms.fs.backend import UNSUPPORTED_ERRNO, Backend
from atoms.fs.lock import HeldProjectLock


class ChildExit:
    """Exit codes the §8.3 certification children use to report a specific verdict.

    Named rather than literal so a later reordering cannot silently remap "refused
    with the wrong result code" onto "read the wrong value" — two very different
    conclusions about a volume.
    """

    OK = 0
    STALE_READ = 3
    ACQUIRED_WHILE_HELD = 4
    WRONG_REFUSAL = 5


def _supported(operation: str, probe) -> bool:
    """Run `probe`; report absence only for an errno that conclusively means it.

    This decides *availability*. It is not the right tool for a probe step whose
    evidence is a refusal — see `_refused_with`.
    """
    try:
        probe()
    except OSError as caught:
        if caught.errno in UNSUPPORTED_ERRNO[operation]:
            return False
        raise
    return True


def _refused_with(expected: int, open_attempt) -> bool:
    """True if `open_attempt` refused with exactly `expected`; False if it succeeded.

    A guard is proved by the exact errno it refuses with, never by "some OSError
    happened". Accepting any error here would let a volume failing for an unrelated
    reason — EBADF from a descriptor bug, EIO from failing media — report the guard
    as working, which is design §10's propagation rule read backwards. Anything
    other than `expected` therefore propagates.

    `open_attempt` returns a descriptor when the guard fails to refuse; it is closed
    here so a failed guard does not also leak.
    """
    try:
        opened = open_attempt()
    except OSError as caught:
        if caught.errno == expected:
            return True
        raise
    os.close(opened)
    return False


def _write(parent_fd: int, name: str, payload: bytes) -> None:
    fd = os.open(name, os.O_CREAT | os.O_WRONLY | os.O_EXCL | os.O_CLOEXEC, 0o600, dir_fd=parent_fd)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)


def _read(parent_fd: int, name: str) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC, dir_fd=parent_fd)
    try:
        return os.read(fd, 64)
    finally:
        os.close(fd)


def _clear(parent_fd: int) -> None:
    for name in os.listdir(parent_fd):
        info = os.lstat(name, dir_fd=parent_fd)
        if stat.S_ISDIR(info.st_mode):
            os.rmdir(name, dir_fd=parent_fd)
        else:
            os.unlink(name, dir_fd=parent_fd)


@contextlib.contextmanager
def _staged(probe_fd: int) -> Iterator[None]:
    """Empty `probe_fd` on the way out, whatever the body managed to create in it.

    Entered BEFORE anything is created, and that is the whole point: a probe that
    writes two operands and fails on the second must not leave the first behind.
    `_clear` walks `listdir` rather than a list of expected names, so it removes
    exactly what exists however far staging got.
    """
    try:
        yield
    finally:
        _clear(probe_fd)


@contextlib.contextmanager
def _child_pair(backend: Backend, probe_fd: int) -> Iterator[tuple[int, int]]:
    """Two distinct child directories under `probe_fd`, released in reverse order.

    Both descriptors are acquired *inside* the stack, so failing to open the second
    still closes the first — the leak a `src_fd = ...; dst_fd = ...; try:` prologue
    quietly takes on. ExitStack unwinds in reverse registration order, so each
    directory is emptied before its own descriptor closes and `probe/` is emptied
    last, once `src` and `dst` are empty enough to remove. It also keeps unwinding
    after a callback raises, so one failing release cannot strand the others.

    This is heterogeneous cleanup, not a `close_all` batch: ExitStack preserves its
    standard chained-exception behavior if more than one clear/close callback fails.
    The first-failure precedence contract applies only within one explicit descriptor
    batch passed to `close_all`.
    """
    with contextlib.ExitStack() as stack:
        stack.callback(_clear, probe_fd)
        os.mkdir("src", mode=0o700, dir_fd=probe_fd)
        os.mkdir("dst", mode=0o700, dir_fd=probe_fd)
        src_fd = backend.open_child_directory(probe_fd, "src")
        stack.callback(os.close, src_fd)
        stack.callback(_clear, src_fd)
        dst_fd = backend.open_child_directory(probe_fd, "dst")
        stack.callback(os.close, dst_fd)
        stack.callback(_clear, dst_fd)
        yield src_fd, dst_fd


def _probe_traversal(backend: Backend, probe_fd: int) -> bool:
    with _staged(probe_fd):
        os.mkdir("real", mode=0o700, dir_fd=probe_fd)
        os.symlink("real", "escape", dir_fd=probe_fd)
        # One open, not two: the availability check and the descriptor it produces are
        # the same call. Opening again to "get a real one" would discard a descriptor.
        opened: list[int] = []

        def attempt():
            opened.append(backend.open_child_directory(probe_fd, "real"))

        if not _supported("traversal", attempt):
            return False
        os.close(opened[0])
        # RESOLVE_NO_SYMLINKS reports a symlink component as ELOOP; RESOLVE_BENEATH
        # reports an escape as EXDEV. Both refusals must arrive with exactly that
        # code, or the guard is not what proved itself.
        for refused, expected in (("escape", errno.ELOOP), ("..", errno.EXDEV)):
            if not _refused_with(
                expected, lambda name=refused: backend.open_child_directory(probe_fd, name)
            ):
                return False
        return True


def _probe_lock(backend: Backend, lock: HeldProjectLock) -> bool:
    # flock is per open file description, so a second open in this process
    # contends correctly against the already-held lock.
    contender = os.open("lock", os.O_RDWR | os.O_CLOEXEC, dir_fd=lock.metadata_root_fd)
    try:
        acquired = None

        def attempt():
            nonlocal acquired
            acquired = backend.try_lock_exclusive(contender)

        if not _supported("lock", attempt):
            return False
        return acquired is False
    finally:
        os.close(contender)


def _probe_exchange(backend: Backend, probe_fd: int) -> bool:
    with _staged(probe_fd):
        _write(probe_fd, "left", b"L")
        _write(probe_fd, "right", b"R")
        if not _supported("exchange", lambda: backend.exchange(probe_fd, "left", "right")):
            return False
        return _read(probe_fd, "left") == b"R" and _read(probe_fd, "right") == b"L"


def _probe_transfer(backend: Backend, probe_fd: int) -> bool:
    # The distinct-parent form is what blob promotion and staging publication use.
    with _child_pair(backend, probe_fd) as (src_fd, dst_fd):
        _write(src_fd, "payload", b"P")
        _write(dst_fd, "payload", b"occupied")
        blocked = False
        try:
            backend.transfer_noclobber(src_fd, "payload", dst_fd, "payload")
        except OSError as caught:
            if caught.errno in UNSUPPORTED_ERRNO["transfer_noclobber"]:
                return False
            if caught.errno != errno.EEXIST:
                raise
            blocked = True
        if not blocked:
            return False
        os.unlink("payload", dir_fd=dst_fd)
        backend.transfer_noclobber(src_fd, "payload", dst_fd, "payload")
        return _read(dst_fd, "payload") == b"P"


def _probe_link(backend: Backend, probe_fd: int) -> bool:
    with _child_pair(backend, probe_fd) as (src_fd, dst_fd):
        _write(src_fd, "payload", b"P")
        if not _supported(
            "link_anchor", lambda: backend.link_anchor(src_fd, "payload", dst_fd, "anchor")
        ):
            return False
        source = os.stat("payload", dir_fd=src_fd)
        anchor = os.stat("anchor", dir_fd=dst_fd)
        return (source.st_dev, source.st_ino) == (anchor.st_dev, anchor.st_ino) and (
            source.st_nlink == 2
        )


def _probe_flush(backend: Backend, probe_fd: int) -> bool:
    with _staged(probe_fd):
        _write(probe_fd, "payload", b"P")
        fd = os.open("payload", os.O_RDONLY | os.O_CLOEXEC, dir_fd=probe_fd)
        try:

            def attempt():
                backend.flush_file(fd)
                backend.flush_directory(probe_fd)

            return _supported("flush", attempt)
        finally:
            # Inside _staged, so the descriptor closes before probe/ is emptied.
            os.close(fd)


def _probe_nofollow_read(backend: Backend, probe_fd: int) -> bool:
    with _staged(probe_fd):
        _write(probe_fd, "payload", b"P")
        os.symlink("payload", "alias", dir_fd=probe_fd)
        opened: list[int] = []

        def attempt():
            opened.append(backend.open_regular_nofollow(probe_fd, "payload"))

        if not _supported("open_regular_nofollow", attempt):
            return False
        try:
            if not stat.S_ISREG(os.fstat(opened[0]).st_mode):
                return False
            if os.read(opened[0], 8) != b"P":
                return False
        finally:
            os.close(opened[0])
        # O_NOFOLLOW on a symlink leaf refuses with exactly ELOOP. Any other errno
        # is an unrelated failure and must not be read as a working guard.
        return _refused_with(
            errno.ELOOP, lambda: backend.open_regular_nofollow(probe_fd, "alias")
        )


def _probe_symlink_fingerprint(backend: Backend, probe_fd: int) -> bool:
    with _staged(probe_fd):
        # symlink(2) reports EPERM when this filesystem cannot create symlinks,
        # and EPERM is part of symlink_fingerprint's own unsupported set. That is
        # why creation belongs inside this capability's _supported call. This is
        # specific to that documented basis, not a general staging rule for the
        # traversal and nofollow probes.
        if not _supported(
            "symlink_fingerprint",
            lambda: os.symlink("../target", "alias", dir_fd=probe_fd),
        ):
            return False
        captured: list[tuple] = []

        def attempt():
            captured.append(backend.symlink_fingerprint(probe_fd, "alias"))

        if not _supported("symlink_fingerprint", attempt):
            return False
        info, target = captured[0]
        return stat.S_ISLNK(info.st_mode) and target == "../target"


def probe_backend(
    backend: Backend, probe_root_fd: int, lock: HeldProjectLock
) -> frozenset[Capability]:
    """Return exactly the capabilities this volume supplies. The sole producer."""
    _clear(probe_root_fd)
    supplied: set[Capability] = set()
    if _probe_traversal(backend, probe_root_fd):
        supplied.add(Capability.ANCHORED_TRAVERSAL)
    if _probe_lock(backend, lock):
        supplied.add(Capability.ADVISORY_PROJECT_LOCK)
    if _probe_exchange(backend, probe_root_fd):
        supplied.add(Capability.ATOMIC_EXCHANGE)
    if _probe_transfer(backend, probe_root_fd):
        supplied.add(Capability.NOCLOBBER_TRANSFER)
    if _probe_link(backend, probe_root_fd):
        supplied.add(Capability.IDENTITY_ANCHOR)
    if _probe_flush(backend, probe_root_fd):
        supplied.add(Capability.DURABLE_PUBLISH)
    if _probe_nofollow_read(backend, probe_root_fd):
        supplied.add(Capability.NOFOLLOW_COHERENT_READ)
    if _probe_symlink_fingerprint(backend, probe_root_fd):
        supplied.add(Capability.SYMLINK_FINGERPRINT)
    _clear(probe_root_fd)
    return frozenset(supplied)


# Design §8.3 steps 4-5: read the parent's committed state concurrently with the
# parent's held write lock, then require the write lock to refuse. busy_timeout is 0
# so the refusal is immediate rather than a wait.
_CHILD_CONTENDER = f"""
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1], timeout=0, isolation_level=None)
if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
    sys.exit({ChildExit.STALE_READ})
try:
    connection.execute("BEGIN IMMEDIATE")
except sqlite3.OperationalError as caught:
    # Only SQLITE_BUSY proves cross-process write exclusion. SQLite distinguishes it
    # from I/O, protocol, permission, and internal errors, and a volume whose WAL
    # index never opens at all would raise one of those — indistinguishable from
    # correct exclusion if any OperationalError were accepted. Compare the PRIMARY
    # code so an extended SQLITE_BUSY_* variant still counts.
    if caught.sqlite_errorcode & 0xFF != sqlite3.SQLITE_BUSY:
        sys.exit({ChildExit.WRONG_REFUSAL})
    connection.close()
    sys.exit({ChildExit.OK})
sys.exit({ChildExit.ACQUIRED_WHILE_HELD})
"""

# Design §8.3 step 7, run only after the parent has committed. A SEPARATE invocation:
# the parent cannot wait for a child that is itself waiting for the parent's commit.
_CHILD_WRITER = f"""
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1], timeout=30, isolation_level=None)
connection.execute("BEGIN IMMEDIATE")
connection.execute("PRAGMA user_version=2")
connection.execute("COMMIT")
connection.close()
sys.exit({ChildExit.OK})
"""


_CHILD_TIMEOUT_SECONDS = 60


def _run_child(script: str, database_path: str, phase: str) -> subprocess.CompletedProcess:
    """Run one certification child under a bounded timeout.

    A child that does not finish is a REFUSAL, not an escaping error. Design §8.3 puts
    every certification failure under CapabilityUnavailable, and a TimeoutExpired
    reaching the caller would put the one failure mode a broken-locking volume is most
    likely to produce outside the §10 contract — handing A5 a third exception type to
    know about for no gain. Only TimeoutExpired is caught: an OSError from spawning the
    interpreter is a bug in this process, not a verdict about the volume.

    Each child re-resolves the database by pathname. That is acceptable only because
    probe/ is engine-owned, sits under the held project lock, and contains no
    transaction state; the exemption extends to nothing outside probe/.
    """
    try:
        return subprocess.run(
            [sys.executable, "-c", script, database_path],
            timeout=_CHILD_TIMEOUT_SECONDS,
            capture_output=True,
        )
    except subprocess.TimeoutExpired as caught:
        raise CapabilityUnavailable(
            f"the SQLite-WAL {phase} child did not finish within "
            f"{_CHILD_TIMEOUT_SECONDS}s, so cross-process WAL coordination is unproven"
        ) from caught


def certify_sqlite_wal(database_path: str, cleanup: bool = False) -> None:
    """Certify the volume can host the SQLite-WAL metadata store (design §8.3).

    Opening a database and selecting WAL mode is insufficient: WAL can operate
    without shared memory when SQLite runs in exclusive locking mode. The second
    reader must be a separate PROCESS, because a same-process connection exercises
    WAL but not SQLite's cross-process POSIX locking contract, and the shared-memory
    WAL index exists precisely to coordinate readers across processes.

    The choreography runs as TWO child invocations. The parent must release its write
    lock between the contending read (steps 4-5) and the child write (step 7), and a
    single blocking child cannot express that: the parent would block waiting for a
    child that is blocked waiting for the parent's commit, and the sequence would
    resolve only by one side timing out. Splitting at the release point makes the
    ordering explicit and the outcome deterministic.
    """
    parent = sqlite3.connect(database_path, isolation_level=None)
    try:
        mode = parent.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if str(mode).lower() != "wal":
            raise CapabilityUnavailable(
                f"volume cannot host SQLite in WAL mode (journal_mode={mode!r})"
            )
        parent.execute("PRAGMA synchronous=FULL")
        parent.execute("PRAGMA user_version=1")

        parent.execute("BEGIN IMMEDIATE")
        try:
            contended = _run_child(_CHILD_CONTENDER, database_path, "contention")
        finally:
            # Release before inspecting the verdict, so no refusal path can leave the
            # write lock held while the second child needs it.
            parent.execute("COMMIT")
        if contended.returncode == ChildExit.STALE_READ:
            raise CapabilityUnavailable(
                "a second process could not read the committed WAL state while a "
                "writer held the lock"
            )
        if contended.returncode == ChildExit.ACQUIRED_WHILE_HELD:
            raise CapabilityUnavailable(
                "a second process acquired the write lock while it was held"
            )
        if contended.returncode == ChildExit.WRONG_REFUSAL:
            raise CapabilityUnavailable(
                "a second process was refused the write lock with something other "
                "than SQLITE_BUSY, so cross-process exclusion is unproven"
            )
        if contended.returncode != ChildExit.OK:
            raise CapabilityUnavailable(
                f"SQLite-WAL contention child failed: {contended.stderr!r}"
            )

        wrote = _run_child(_CHILD_WRITER, database_path, "write")
        if wrote.returncode != ChildExit.OK:
            raise CapabilityUnavailable(
                f"a second process could not write once the lock was released: "
                f"{wrote.stderr!r}"
            )
        observed = parent.execute("PRAGMA user_version").fetchone()[0]
        if observed != 2:
            raise CapabilityUnavailable(
                f"the child's committed write was not observed (user_version={observed})"
            )
    finally:
        parent.close()
    if cleanup:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(database_path + suffix)
            except FileNotFoundError:
                pass
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fs_probe.py -v`
Expected: PASS. Every test in the named files must pass; the only acceptable non-pass is a
`test_volume`/`distinct_volume` skip with its stated reason. Do not accept a bare count as
evidence — read the skip lines.

- [ ] **Step 6: Commit**

```bash
git add src/atoms/fs/probe.py tests/test_fs_probe.py tests/fs_support.py tests/conftest.py
git commit -m "feat(fs): probe volume capabilities and certify sqlite-wal hosting"
```

---

### Task 6: `bind_project_volume`, evidence, and the binding lifetime

The order of the binding sequence is load-bearing in two places: reclamation must precede the allowlist
refusal, and steps 1-5 must stay read-only beyond the permitted bootstrap.

**Files:**
- Create: `python/src/atoms/fs/binding.py`
- Create: `python/tests/test_fs_binding.py`
- Modify: `python/src/atoms/fs/__init__.py`
- Modify: `python/tests/fs_support.py`
- Modify: `python/tests/conftest.py`

**Interfaces:**
- Consumes: everything from Tasks 2-5.
- Produces:
  - `VolumeEvidence(configuration, declared_storage_profile, matched_entry, supplied_capabilities,
    metadata_root_device, metadata_root_inode, mount_id)`
  - `ProjectBinding` with `backend`, `project_root_fd`, `metadata_root_fd`, `evidence`, `active`,
    `verified_metadata_path(name)`, `__enter__`, `__exit__`
  - `bind_project_volume(project_root: str, lock: HeldProjectLock, *,
    allowlist: DurabilityAllowlist, storage: StorageProfile) -> ProjectBinding`

- [ ] **Step 1: Write the failing binding tests**

Create `python/tests/test_fs_binding.py`:

```python
import dataclasses
import errno
import os
import shutil
import subprocess
import sys

import pytest

from atoms.core.capabilities import Capability
from atoms.core.errors import CapabilityUnavailable, ProtocolError
from atoms.fs.binding import ProjectBinding, VolumeEvidence, bind_project_volume
from atoms.fs.bootstrap import close_layout
from atoms.fs.lock import acquire_project_lock
from atoms.fs.volume import CERTIFIED_ALLOWLIST, DurabilityAllowlist

_OPTIONAL_CAPABILITIES = tuple(
    capability
    for capability in Capability
    if capability
    not in {Capability.ANCHORED_TRAVERSAL, Capability.ADVISORY_PROJECT_LOCK}
)

_BIND_MOUNT_CHILD = r"""
import os
import subprocess
import sys

from atoms.core.errors import CapabilityUnavailable
from atoms.fs.binding import bind_project_volume
from atoms.fs.linux import LinuxBackend
from atoms.fs.lock import acquire_project_lock
from atoms.fs.volume import DurabilityAllowlist, StorageProfile, read_mount_id

source, target, metadata_root, mount_program = sys.argv[1:]
mounted = subprocess.run(
    [mount_program, "--bind", source, target],
    capture_output=True,
    text=True,
)
if mounted.returncode != 0:
    print(mounted.stderr.strip(), file=sys.stderr)
    sys.exit(77)

backend = LinuxBackend()
source_fd = backend.open_root(source)
target_fd = backend.open_root(target)
try:
    if os.fstat(source_fd).st_dev != os.fstat(target_fd).st_dev:
        print("bind mount did not preserve st_dev", file=sys.stderr)
        sys.exit(10)
    if read_mount_id(source_fd) == read_mount_id(target_fd):
        print("bind mount did not create a distinct mount ID", file=sys.stderr)
        sys.exit(11)
finally:
    os.close(target_fd)
    os.close(source_fd)

with acquire_project_lock(backend, metadata_root) as lock:
    try:
        bind_project_volume(
            os.path.join(target, "project"),
            lock,
            allowlist=DurabilityAllowlist(entries=frozenset()),
            storage=StorageProfile(profile_id="bind-mount-test"),
        )
    except CapabilityUnavailable as caught:
        if "same volume" not in str(caught):
            print(f"wrong refusal: {caught}", file=sys.stderr)
            sys.exit(12)
    else:
        print("equal-st_dev roots with distinct mount IDs were admitted", file=sys.stderr)
        sys.exit(13)
"""


def test_binding_succeeds_and_reports_supplied_capabilities(bound_volume):
    with bound_volume() as binding:
        assert binding.active is True
        assert binding.evidence.supplied_capabilities == frozenset(Capability)
        assert binding.evidence.matched_entry.certification_ref


def test_evidence_configuration_equals_the_matched_entry(bound_volume):
    # Deliberate redundancy: what was resolved and what was certified stay
    # separately legible. match() returns an entry only on exact equality of both.
    with bound_volume() as binding:
        evidence = binding.evidence
        assert evidence.configuration == evidence.matched_entry.configuration
        assert evidence.declared_storage_profile == evidence.matched_entry.storage


def test_empty_allowlist_refuses(project_root, held_lock, metadata_root, test_storage_profile):
    with held_lock(metadata_root) as lock:
        with pytest.raises(CapabilityUnavailable, match="allowlist"):
            bind_project_volume(
                str(project_root),
                lock,
                allowlist=CERTIFIED_ALLOWLIST,
                storage=test_storage_profile,
            )


def test_certified_allowlist_is_the_empty_production_constant():
    assert CERTIFIED_ALLOWLIST == DurabilityAllowlist(entries=frozenset())


def test_refusal_reclaims_existing_debris_but_writes_nothing_new(
    project_root, held_lock, metadata_root, test_storage_profile
):
    # Reclamation precedes the allowlist refusal: if refusal came first, a
    # configuration removed from the allowlist could never have its debris cleared.
    with held_lock(metadata_root) as lock:
        os.mkdir("probe", mode=0o700, dir_fd=lock.metadata_root_fd)
        probe_fd = lock.backend.open_child_directory(lock.metadata_root_fd, "probe")
        try:
            fd = os.open("debris", os.O_CREAT | os.O_WRONLY, 0o600, dir_fd=probe_fd)
            os.close(fd)
            with pytest.raises(CapabilityUnavailable):
                bind_project_volume(
                    str(project_root),
                    lock,
                    allowlist=CERTIFIED_ALLOWLIST,
                    storage=test_storage_profile,
                )
            assert os.listdir(probe_fd) == []
        finally:
            os.close(probe_fd)
        assert "staging" not in os.listdir(lock.metadata_root_fd)


def test_cross_volume_roots_refuse(
    held_lock, metadata_root, distinct_volume, test_storage_profile
):
    with held_lock(metadata_root) as lock:
        with pytest.raises(CapabilityUnavailable, match="volume"):
            bind_project_volume(
                str(distinct_volume),
                lock,
                allowlist=DurabilityAllowlist(entries=frozenset()),
                storage=test_storage_profile,
            )


def test_real_bind_mount_with_equal_device_and_distinct_mount_id_refuses(test_volume):
    # The captured mountinfo fixture proves the parser distinction everywhere. This
    # Tier 3 test proves the live descriptor path and binding decision when namespace
    # support is available.
    unshare = shutil.which("unshare")
    mount_program = shutil.which("mount")
    if unshare is None or mount_program is None:
        missing = "unshare" if unshare is None else "mount"
        pytest.skip(f"real bind-mount test requires the {missing!r} program")

    namespace_probe = subprocess.run(
        [unshare, "--mount", "--map-root-user", "--", "true"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if namespace_probe.returncode != 0:
        reason = namespace_probe.stderr.strip() or "no diagnostic"
        pytest.skip(f"user/mount namespaces unavailable: {reason}")

    source = test_volume / "bind-source"
    target = test_volume / "bind-target"
    source.mkdir()
    target.mkdir()
    (source / "project").mkdir()
    metadata_root = source / "metadata"

    finished = subprocess.run(
        [
            unshare,
            "--mount",
            "--map-root-user",
            "--",
            sys.executable,
            "-c",
            _BIND_MOUNT_CHILD,
            str(source),
            str(target),
            str(metadata_root),
            mount_program,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if finished.returncode == 77:
        reason = finished.stderr.strip() or "no diagnostic"
        pytest.skip(f"isolated bind mount unavailable: {reason}")
    assert finished.returncode == 0, finished.stderr


def test_a_lock_that_does_not_exclude_refuses_binding(
    project_root, metadata_root, fake_backend, test_allowlist, test_storage_profile
):
    # The bootstrap-prerequisite check is not dead code. flock can succeed while
    # failing to exclude — the real case on NFS without a working lock daemon — so
    # the probe reports advisory_project_lock absent even though acquisition worked.
    # (The refusals for a prerequisite that is unavailable *at acquisition* live in
    # test_fs_lock.py, since acquire_project_lock is where they are converted.)
    backend = fake_backend(supplied=set(Capability), lock_excludes=False)
    with acquire_project_lock(backend, str(metadata_root)) as lock:
        allowlist = test_allowlist(lock, project_root, test_storage_profile)
        with pytest.raises(CapabilityUnavailable, match="advisory_project_lock"):
            bind_project_volume(
                str(project_root),
                lock,
                allowlist=allowlist,
                storage=test_storage_profile,
            )


@pytest.mark.parametrize("absent", _OPTIONAL_CAPABILITIES)
def test_each_absent_optional_capability_binds_and_reports_exactly(bound_volume, absent):
    with bound_volume(withhold={absent}) as binding:
        assert binding.evidence.supplied_capabilities == frozenset(
            set(Capability) - {absent}
        )
        assert binding.active is True


def test_accessors_refuse_after_exit(bound_volume):
    with bound_volume() as binding:
        pass
    assert binding.active is False
    for accessor in ("backend", "project_root_fd", "metadata_root_fd"):
        with pytest.raises(ProtocolError):
            getattr(binding, accessor)


def test_evidence_survives_exit(bound_volume):
    with bound_volume() as binding:
        expected = binding.evidence
    assert binding.evidence is expected
    assert binding.evidence.supplied_capabilities


def test_exit_is_idempotent(bound_volume):
    with bound_volume() as binding:
        pass
    binding.__exit__(None, None, None)
    assert binding.active is False


def test_descriptors_are_cloexec(bound_volume):
    with bound_volume() as binding:
        assert os.get_inheritable(binding.project_root_fd) is False


def test_accessors_refuse_once_the_lock_is_released(
    project_root, held_lock, metadata_root, test_allowlist, test_storage_profile
):
    # Without this, metadata_root_fd could hand back a descriptor the lock already
    # closed — an out-of-order exit surfacing as EBADF somewhere far away.
    lock = held_lock(metadata_root)
    binding = bind_project_volume(
        str(project_root),
        lock,
        allowlist=test_allowlist(lock, project_root, test_storage_profile),
        storage=test_storage_profile,
    )
    lock.__exit__(None, None, None)
    with pytest.raises(ProtocolError):
        binding.metadata_root_fd


def test_verified_metadata_path_delegates_to_the_shared_verifier(bound_volume, metadata_root):
    with bound_volume() as binding:
        resolved = binding.verified_metadata_path("atoms.db")
        assert resolved.endswith("/atoms.db")
        with pytest.raises(ProtocolError):
            binding.verified_metadata_path("nested/child")


def test_guarded_types_refuse_ordinary_construction(bound_volume):
    with pytest.raises(TypeError, match="bind_project_volume"):
        VolumeEvidence(
            configuration=None,
            declared_storage_profile=None,
            matched_entry=None,
            supplied_capabilities=frozenset(),
            metadata_root_device=0,
            metadata_root_inode=0,
            mount_id=0,
        )
    with pytest.raises(TypeError, match="bind_project_volume"):
        ProjectBinding()
    with bound_volume() as binding:
        # replace() calls __init__ without the token, so it refuses too.
        with pytest.raises(TypeError, match="bind_project_volume"):
            dataclasses.replace(binding.evidence, mount_id=1)


def test_binding_refuses_dataclass_replacement(bound_volume):
    # ProjectBinding is deliberately NOT a dataclass — it owns a descriptor and a
    # spent flag, which are not value semantics — so replace() refuses for want of
    # __dataclass_fields__ rather than for want of the token. Either way no copy of a
    # live binding can be fabricated, which is what A4b will rely on (ledger #16).
    with bound_volume() as binding:
        with pytest.raises(TypeError):
            dataclasses.replace(binding)


def test_evidence_is_frozen(bound_volume):
    with bound_volume() as binding:
        with pytest.raises(dataclasses.FrozenInstanceError):
            binding.evidence.mount_id = 1


def test_a_certification_failure_still_reclaims_probe_debris(
    project_root, metadata_root, linux_backend, test_allowlist, test_storage_profile, monkeypatch
):
    # Reclamation is in a finally, not on the success path. A SQLite refusal is the
    # exact shape that skips a success-only cleanup, and the debris it would strand
    # sits in engine-owned space under the lock — where the next lease entry would
    # find it and have to guess whose it was.
    def refuse(database_path, cleanup=False):
        os.close(os.open(database_path, os.O_CREAT | os.O_WRONLY | os.O_CLOEXEC, 0o600))
        raise CapabilityUnavailable("injected certification failure")

    monkeypatch.setattr("atoms.fs.binding.certify_sqlite_wal", refuse)
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        allowlist = test_allowlist(lock, project_root, test_storage_profile)
        with pytest.raises(CapabilityUnavailable, match="injected"):
            bind_project_volume(
                str(project_root), lock, allowlist=allowlist, storage=test_storage_profile
            )
        probe_fd = lock.backend.open_child_directory(lock.metadata_root_fd, "probe")
        try:
            assert os.listdir(probe_fd) == []
        finally:
            os.close(probe_fd)


def test_a_descriptor_release_failure_still_reclaims(
    project_root, metadata_root, linux_backend, test_allowlist, test_storage_profile, monkeypatch
):
    # Reclamation sits in the OUTER finally. Releasing the layout descriptors and
    # emptying probe/ are independent obligations, and the one that leaves state on
    # disk must not become conditional on the one that does not: `close_all(...)`
    # followed by `reclaim_probe_survivors(lock)` in a single finally would skip
    # reclamation on exactly this path.
    def leaves_debris(database_path, cleanup=False):
        os.close(os.open(database_path, os.O_CREAT | os.O_WRONLY | os.O_CLOEXEC, 0o600))

    def failing_release(retained):
        close_layout(retained)
        raise OSError(errno.EIO, "injected release failure")

    monkeypatch.setattr("atoms.fs.binding.certify_sqlite_wal", leaves_debris)
    monkeypatch.setattr("atoms.fs.binding.close_layout", failing_release)
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        allowlist = test_allowlist(lock, project_root, test_storage_profile)
        with pytest.raises(OSError) as caught:
            bind_project_volume(
                str(project_root), lock, allowlist=allowlist, storage=test_storage_profile
            )
        assert caught.value.errno == errno.EIO
        probe_fd = lock.backend.open_child_directory(lock.metadata_root_fd, "probe")
        try:
            assert os.listdir(probe_fd) == [], "reclamation must run despite the release failure"
        finally:
            os.close(probe_fd)
```

- [ ] **Step 2: Add the binding fixtures**

Append to `python/tests/fs_support.py`:

`contextlib` is already imported by Task 4's Step 6 append to this file; do not add it again. Add the
following import to the consolidated import block:

```python
from atoms.fs.lock import acquire_project_lock
```

```python
from atoms.fs.volume import (
    AllowlistEntry,
    DurabilityAllowlist,
    build_configuration,
    kernel_identifier,
    read_mountinfo,
    resolve_mount_entry,
)


def build_test_allowlist(lock, project_root, storage):
    """A singleton allowlist naming the resolved tuple of the actual test volume.

    This is the deliberate test assumption made visible: production passes
    CERTIFIED_ALLOWLIST, which ships empty. Tuple-resolution correctness is proved
    separately against fixture mountinfo text, never by this live-volume path.
    """
    backend = lock.backend
    fd = backend.open_root(str(project_root))
    try:
        entry = resolve_mount_entry(fd, read_mountinfo())
    finally:
        os.close(fd)
    configuration = build_configuration(entry, kernel_identifier())
    return DurabilityAllowlist(
        entries=frozenset(
            {
                AllowlistEntry(
                    configuration=configuration,
                    storage=storage,
                    certification_ref="test-injected-not-crash-certified",
                )
            }
        )
    )


def make_test_allowlist():
    return build_test_allowlist


def make_bound_volume(backend_factory, project_root, metadata_root, storage):
    from atoms.fs.binding import bind_project_volume

    @contextlib.contextmanager
    def bind(withhold=frozenset()):
        from atoms.core.capabilities import Capability

        backend = (
            backend_factory(supplied=set(Capability) - set(withhold))
            if withhold
            else LinuxBackend()
        )
        with acquire_project_lock(backend, str(metadata_root)) as lock:
            allowlist = build_test_allowlist(lock, project_root, storage)
            with bind_project_volume(
                str(project_root), lock, allowlist=allowlist, storage=storage
            ) as binding:
                yield binding

    return bind


def find_distinct_mount(base):
    """A writable directory on a mount whose ID differs from `base`'s, or None."""
    base_fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        from atoms.fs.volume import read_mount_id

        base_id = read_mount_id(base_fd)
    finally:
        os.close(base_fd)
    for candidate in ("/tmp", "/dev/shm", "/run/user/%d" % os.getuid()):
        path = Path(candidate)
        if not path.is_dir() or not os.access(path, os.W_OK):
            continue
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            if read_mount_id(fd) != base_id:
                return path
        finally:
            os.close(fd)
    return None
```

Append to `python/tests/conftest.py`. Task 4 already imported `make_fake_backend` here, so **extend** that
existing `from tests.fs_support import ...` line with the three new names rather than adding a second
import — a duplicate binding is `F811` and fails `ruff check`. The resulting single line is:

```python
from tests.fs_support import (
    find_distinct_mount,
    make_bound_volume,
    make_fake_backend,
    make_metadata_root,
    make_project_root,
    make_test_allowlist,
)
```

Then append the fixture bodies:

```python
@pytest.fixture
def test_allowlist():
    return make_test_allowlist()


@pytest.fixture
def bound_volume(project_root, metadata_root, test_storage_profile):
    return make_bound_volume(
        make_fake_backend(), project_root, metadata_root, test_storage_profile
    )


@pytest.fixture
def distinct_volume(test_volume):
    found = find_distinct_mount(test_volume)
    if found is None:
        pytest.skip("no writable mount with a distinct mount id is available")
    return Path(tempfile.mkdtemp(dir=found))
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_binding.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'atoms.fs.binding'`

- [ ] **Step 4: Implement the binding**

Create `python/src/atoms/fs/binding.py`:

```python
"""Project volume binding: the live resource and its frozen evidence (design §9)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from atoms.core.capabilities import Capability
from atoms.core.errors import CapabilityUnavailable, ProtocolError
from atoms.fs.backend import Backend
from atoms.fs.bootstrap import (
    PROBE_DIRECTORY,
    close_layout,
    ensure_metadata_layout,
    reclaim_probe_survivors,
    verified_child_path,
)
from atoms.fs.lock import HeldProjectLock, establish_root
from atoms.fs.probe import certify_sqlite_wal, probe_backend
from atoms.fs.volume import (
    AllowlistEntry,
    DurabilityAllowlist,
    StorageProfile,
    VolumeConfiguration,
    build_configuration,
    kernel_identifier,
    read_mount_id,
    read_mountinfo,
    resolve_mount_entry,
)

_TOKEN = object()

_BOOTSTRAP_PREREQUISITES = frozenset(
    {Capability.ANCHORED_TRAVERSAL, Capability.ADVISORY_PROJECT_LOCK}
)


@dataclass(frozen=True, slots=True, init=False)
class VolumeEvidence:
    """Diagnostic only. Describes a volume; authorizes no access to one.

    Guarded exactly like CompiledSpec and RecoveryPlan: an explicit __init__ that
    demands the construction token, so ordinary construction AND dataclasses.replace
    both refuse — replace() calls __init__ without the token.
    """

    configuration: VolumeConfiguration
    declared_storage_profile: StorageProfile
    matched_entry: AllowlistEntry
    supplied_capabilities: frozenset[Capability]
    metadata_root_device: int
    metadata_root_inode: int
    mount_id: int

    def __init__(
        self,
        *,
        configuration: VolumeConfiguration,
        declared_storage_profile: StorageProfile,
        matched_entry: AllowlistEntry,
        supplied_capabilities: frozenset[Capability],
        metadata_root_device: int,
        metadata_root_inode: int,
        mount_id: int,
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _TOKEN:
            raise TypeError("VolumeEvidence values are created only by bind_project_volume")
        object.__setattr__(self, "configuration", configuration)
        object.__setattr__(self, "declared_storage_profile", declared_storage_profile)
        object.__setattr__(self, "matched_entry", matched_entry)
        object.__setattr__(self, "supplied_capabilities", supplied_capabilities)
        object.__setattr__(self, "metadata_root_device", metadata_root_device)
        object.__setattr__(self, "metadata_root_inode", metadata_root_inode)
        object.__setattr__(self, "mount_id", mount_id)


class ProjectBinding:
    """A live resource: descriptors, backend, and the lock it borrows from.

    Not frozen, because it owns descriptors and a spent flag. VolumeEvidence is a
    value; this is a resource. Every descriptor it exposes is BORROWED — a consumer
    must never close one.
    """

    __slots__ = ("_lock", "_project_root_fd", "_evidence", "_active")

    def __init__(self, *, _construction_token: object | None = None, **kwargs) -> None:
        if _construction_token is not _TOKEN:
            raise TypeError("ProjectBinding values are created only by bind_project_volume")
        self._lock = kwargs["lock"]
        self._project_root_fd = kwargs["project_root_fd"]
        self._evidence = kwargs["evidence"]
        self._active = True

    def _require_active(self) -> None:
        if not self._active:
            raise ProtocolError("the project binding has been closed")
        if not self._lock.held:
            raise ProtocolError("the project lock was released before the binding")

    @property
    def backend(self) -> Backend:
        self._require_active()
        return self._lock.backend

    @property
    def project_root_fd(self) -> int:
        self._require_active()
        return self._project_root_fd

    @property
    def metadata_root_fd(self) -> int:
        self._require_active()
        return self._lock.metadata_root_fd

    @property
    def evidence(self) -> VolumeEvidence:
        return self._evidence

    @property
    def active(self) -> bool:
        return self._active

    def verified_metadata_path(self, name: str) -> str:
        self._require_active()
        return verified_child_path(
            self._lock.metadata_root_fd,
            self._lock.metadata_root_path,
            self._evidence.metadata_root_device,
            self._evidence.metadata_root_inode,
            name,
        )

    def __enter__(self) -> ProjectBinding:
        return self

    def __exit__(self, *exc) -> None:
        if not self._active:
            return
        self._active = False
        # Closes only what it opened; the lock owns the metadata-root descriptor.
        os.close(self._project_root_fd)


def bind_project_volume(
    project_root: str,
    lock: HeldProjectLock,
    *,
    allowlist: DurabilityAllowlist,
    storage: StorageProfile,
) -> ProjectBinding:
    """Bind one project volume. The allowlist parameter is required and keyword-only.

    Steps 1-5 are read-only beyond the bootstrap design §5.5 permits, so a
    non-allowlisted volume is refused before the probe writes anything. Reclamation
    at step 4 nevertheless precedes that refusal, so pre-existing attributable
    debris is still removed from a configuration that is no longer certified.
    """
    backend = lock.backend
    metadata_root_fd = lock.metadata_root_fd

    project_root_fd, _, _ = establish_root(backend, project_root, create=False)
    try:
        metadata_info = os.fstat(metadata_root_fd)
        project_info = os.fstat(project_root_fd)
        metadata_mount = read_mount_id(metadata_root_fd)
        project_mount = read_mount_id(project_root_fd)
        if (metadata_mount, metadata_info.st_dev) != (project_mount, project_info.st_dev):
            raise CapabilityUnavailable(
                "project root and metadata root are not on the same volume: "
                f"mount {project_mount} vs {metadata_mount}"
            )

        entry = resolve_mount_entry(metadata_root_fd, read_mountinfo())
        configuration = build_configuration(entry, kernel_identifier())

        reclaim_probe_survivors(lock)

        matched = allowlist.match(configuration, storage)
        if matched is None:
            raise CapabilityUnavailable(
                "volume configuration is not on the supplied durability allowlist: "
                f"{configuration.filesystem_type} {configuration.barrier_options} "
                f"profile={storage.profile_id!r}"
            )

        retained = ensure_metadata_layout(lock)
        try:
            probe_fd = retained[PROBE_DIRECTORY]
            supplied = probe_backend(backend, probe_fd, lock)
            missing = _BOOTSTRAP_PREREQUISITES - supplied
            if missing:
                raise CapabilityUnavailable(
                    "bootstrap prerequisites unavailable: "
                    + ", ".join(sorted(item.value for item in missing))
                )
            probe_dir = verified_child_path(
                metadata_root_fd,
                lock.metadata_root_path,
                metadata_info.st_dev,
                metadata_info.st_ino,
                PROBE_DIRECTORY,
            )
            certify_sqlite_wal(os.path.join(probe_dir, "certify.db"), cleanup=True)
        finally:
            # Design §9.1 step 8 runs in a finally, not on the success path. A SQLite
            # refusal, a subprocess timeout, or an unexpected errno are exactly the
            # paths that skip a success-only cleanup, and each would strand debris in
            # engine-owned space. If reclamation itself fails while another exception
            # is unwinding, that failure surfaces with the original as its __context__:
            # a broken metadata_root is worth reporting, and the next lease entry
            # (ledger #17) reclaims again under the same held lock.
            #
            # Reclamation sits in the OUTER finally so a failing descriptor release
            # cannot skip it — releasing and reclaiming are independent obligations,
            # and the one that leaves state on disk is the one that must not be
            # conditional on the other. close_layout likewise attempts every
            # descriptor rather than abandoning the rest after the first failure, and
            # releases them in reverse opening order per design §9.3.
            try:
                close_layout(retained)
            finally:
                reclaim_probe_survivors(lock)

        evidence = VolumeEvidence(
            configuration=configuration,
            declared_storage_profile=storage,
            matched_entry=matched,
            supplied_capabilities=supplied,
            metadata_root_device=metadata_info.st_dev,
            metadata_root_inode=metadata_info.st_ino,
            mount_id=metadata_mount,
            _construction_token=_TOKEN,
        )
    except BaseException:
        os.close(project_root_fd)
        raise

    return ProjectBinding(
        _construction_token=_TOKEN,
        lock=lock,
        project_root_fd=project_root_fd,
        evidence=evidence,
    )
```

- [ ] **Step 5: Export the public surface**

Replace `python/src/atoms/fs/__init__.py`:

```python
"""Atoms filesystem layer: platform capabilities and project volume binding."""

from atoms.core.capabilities import Capability
from atoms.fs.backend import Backend
from atoms.fs.binding import ProjectBinding, VolumeEvidence, bind_project_volume
from atoms.fs.bootstrap import reclaim_probe_survivors
from atoms.fs.lock import HeldProjectLock, acquire_project_lock
from atoms.fs.platform import select_backend
from atoms.fs.volume import (
    CERTIFIED_ALLOWLIST,
    AllowlistEntry,
    DurabilityAllowlist,
    StorageProfile,
    VolumeConfiguration,
)

__all__ = [
    "CERTIFIED_ALLOWLIST",
    "AllowlistEntry",
    "Backend",
    "Capability",
    "DurabilityAllowlist",
    "HeldProjectLock",
    "ProjectBinding",
    "StorageProfile",
    "VolumeConfiguration",
    "VolumeEvidence",
    "acquire_project_lock",
    "bind_project_volume",
    "reclaim_probe_survivors",
    "select_backend",
]
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_fs_binding.py -v`
Expected: PASS. Every test in the named files must pass; the only acceptable non-pass is a
`test_volume`/`distinct_volume` skip with its stated reason or the real bind-mount test's precise
missing-program, namespace-unavailable, or isolated-mount-unavailable reason. Do not accept a bare count
as evidence — read the skip lines.

- [ ] **Step 7: Commit**

```bash
git add src/atoms/fs/binding.py src/atoms/fs/__init__.py tests/test_fs_binding.py \
        tests/fs_support.py tests/conftest.py
git commit -m "feat(fs): bind a certified project volume to frozen evidence"
```

---

### Task 7: Errno mutations, architecture enforcement, and status sync

Close the boundary with table-derived characterization tests that prove unsupported errno values are
classified only under their valid probe/bootstrap preconditions, and update the ledger and `AGENTS.md`
in the same commit. Tasks 1–6 already implement the behavior, so this task locks it without inventing a
production RED state.

**Files:**
- Create: `python/tests/architecture_support.py`
- Modify: `python/tests/test_recovery_architecture.py`
- Modify: `python/tests/test_fs_architecture.py`
- Modify: `python/tests/test_fs_probe.py`
- Modify: `docs/deferred-obligation-ledger.md`
- Modify: `AGENTS.md`
- Modify: `docs/plans/2026-07-29-plan-a4a-capability-backend.md`

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: no new production interface.

- [ ] **Step 1: Add the table-derived errno characterization and mutation matrix**

Append to `python/tests/test_fs_probe.py`:

```python
_OPERATION_CAPABILITY = {
    "exchange": Capability.ATOMIC_EXCHANGE,
    "transfer_noclobber": Capability.NOCLOBBER_TRANSFER,
    "link_anchor": Capability.IDENTITY_ANCHOR,
    "flush": Capability.DURABLE_PUBLISH,
    "open_regular_nofollow": Capability.NOFOLLOW_COHERENT_READ,
    "symlink_fingerprint": Capability.SYMLINK_FINGERPRINT,
    "lock": Capability.ADVISORY_PROJECT_LOCK,
    "traversal": Capability.ANCHORED_TRAVERSAL,
}

# Generated from production, with ENOTSUP/EOPNOTSUPP deduplicated by numeric value
# on Linux. A copied list could silently miss a newly admitted table entry.
_EFFECTIVE_UNSUPPORTED_PAIRS = tuple(
    (operation, code)
    for operation, codes in UNSUPPORTED_ERRNO.items()
    for code in sorted(codes)
)

_BOOTSTRAP_OPERATIONS = frozenset({"lock", "traversal"})


def test_operational_enosys_cases_are_in_the_generated_matrix():
    # A current kernel will not naturally return these, so the fake must keep the
    # openat2 and both renameat2 operational-absence paths reachable.
    for operation in ("traversal", "exchange", "transfer_noclobber"):
        assert (operation, errno.ENOSYS) in _EFFECTIVE_UNSUPPORTED_PAIRS


@pytest.mark.parametrize(
    ("operation", "code"),
    [
        pair
        for pair in _EFFECTIVE_UNSUPPORTED_PAIRS
        if pair[0] not in _BOOTSTRAP_OPERATIONS
    ],
)
def test_each_effective_unsupported_errno_removes_exactly_its_capability(
    held_lock, metadata_root, fake_backend, operation, code
):
    # This is the licensed half: the probe owns valid operands under the lock.
    backend = fake_backend(supplied=set(Capability), **{f"{operation}_errno": code})
    with held_lock(metadata_root) as lock, probe_directory(lock) as probe_fd:
        supplied = probe_backend(backend, probe_fd, lock)
    assert supplied == frozenset(
        set(Capability) - {_OPERATION_CAPABILITY[operation]}
    )


@pytest.mark.parametrize(
    ("operation", "code"),
    [
        pair
        for pair in _EFFECTIVE_UNSUPPORTED_PAIRS
        if pair[0] in _BOOTSTRAP_OPERATIONS
    ],
)
def test_each_bootstrap_unsupported_errno_refuses_before_probing(
    metadata_root, fake_backend, operation, code
):
    backend = fake_backend(supplied=set(Capability), **{f"{operation}_errno": code})
    expected = (
        "anchored_traversal"
        if operation == "traversal"
        else "advisory_project_lock"
    )
    with pytest.raises(CapabilityUnavailable, match=expected):
        acquire_project_lock(backend, str(metadata_root))


def _invoke_with_invalid_descriptor(backend, operation, invalid_fd):
    # Non-empty components are load-bearing: an empty pathname produces ENOENT
    # before the kernel consults the invalid descriptor. These calls exercise the
    # real Linux backend outside probe_backend's licensed precondition.
    if operation == "exchange":
        return backend.exchange(invalid_fd, "left", "right")
    if operation == "transfer_noclobber":
        return backend.transfer_noclobber(
            invalid_fd, "source", invalid_fd, "destination"
        )
    if operation == "link_anchor":
        return backend.link_anchor(invalid_fd, "source", invalid_fd, "anchor")
    if operation == "flush":
        return backend.flush_file(invalid_fd)
    if operation == "open_regular_nofollow":
        return backend.open_regular_nofollow(invalid_fd, "entry")
    if operation == "symlink_fingerprint":
        return backend.symlink_fingerprint(invalid_fd, "entry")
    if operation == "lock":
        return backend.try_lock_exclusive(invalid_fd)
    if operation == "traversal":
        return backend.open_child_directory(invalid_fd, "entry")
    raise AssertionError(f"unmapped operation: {operation}")


@pytest.mark.parametrize("operation", tuple(_OPERATION_CAPABILITY))
def test_linux_backend_propagates_ebadf_outside_probe_precondition(
    linux_backend, operation
):
    # This half observes the real backend; dictating an errno through RestrictedBackend
    # would only prove that the test double can raise. EBADF is never availability
    # evidence, so every operation must expose it unchanged.
    assert all(errno.EBADF not in codes for codes in UNSUPPORTED_ERRNO.values())
    with pytest.raises(OSError) as caught:
        # A closed positive descriptor reaches every real syscall. CPython rejects
        # a negative descriptor before fsync/flock with ValueError.
        invalid_fd = os.open(__file__, os.O_RDONLY | os.O_CLOEXEC)
        os.close(invalid_fd)
        _invoke_with_invalid_descriptor(linux_backend, operation, invalid_fd)
    assert caught.value.errno == errno.EBADF
```

Add `from atoms.fs.backend import UNSUPPORTED_ERRNO` and
`from atoms.fs.lock import acquire_project_lock` to the imports of
`python/tests/test_fs_probe.py`. Task 4's restricted backend accepts both method-level overrides used by
the named-refusal tests and the contract-level keys generated by the classification half. The propagation
half intentionally uses the real `linux_backend` fixture, so an injected exception cannot bypass the
operation under test.

- [ ] **Step 2: Extract the shared architecture scanner**

The fixture-registry guard A3 already carries handles two things a fresh implementation gets wrong: a
test's arguments are not all fixtures, and `pytest.mark.parametrize` names must be subtracted first.
Reuse it rather than writing a second one — a hand-rolled scanner would reject A4a's `absent`,
`operation`, and `code` parameters as missing fixtures.

Create `python/tests/architecture_support.py` and **move** these into it verbatim from
`python/tests/test_recovery_architecture.py`, renaming each to drop the leading underscore since they
are now imported across modules: `_PYTEST_BUILTINS` → `PYTEST_BUILTINS`, `_decorator_name` →
`decorator_name`, `_fixture_names` → `fixture_names`, `_parametrize_names` → `parametrize_names`,
`_collected_test_functions` → `collected_test_functions`, and `_unregistered_test_arguments` →
`unregistered_test_arguments`. The module needs `import ast` and `from pathlib import Path`.

Give `unregistered_test_arguments` a third parameter so each suite scans its own files:

```python
def unregistered_test_arguments(
    tests_root: Path,
    registered: set[str],
    pattern: str,
) -> set[str]:
    """Test arguments in `pattern` files that name no fixture in `registered`.

    `pattern` is a parameter because two suites now share this scanner. Subtracting
    parametrize_names first is not an optimization: a parametrized value is an
    argument that is deliberately NOT a fixture, and reporting it missing would make
    the guard fail on correct tests.
    """
    missing: set[str] = set()
    for test_path in tests_root.glob(pattern):
        tree = ast.parse(test_path.read_text(encoding="utf-8"))
        for node, receivers in collected_test_functions(tree):
            arguments = {
                argument.arg
                for argument in (
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                )
            }
            missing.update(
                arguments
                - parametrize_names(node)
                - registered
                - PYTEST_BUILTINS
                - receivers
            )
    return missing
```

Then in `python/tests/test_recovery_architecture.py`, delete the moved helpers, add
`from tests.architecture_support import fixture_names, unregistered_test_arguments`, and pass the glob
at both existing call sites:

```python
def test_recovery_fixture_registry_covers_every_test_argument():
    tests_root = Path(__file__).parent
    registered = fixture_names(tests_root / "conftest.py")
    assert unregistered_test_arguments(tests_root, registered, "test_recovery_*.py") == set()


def test_recovery_fixture_registry_scans_test_class_methods(tmp_path):
    (tmp_path / "test_recovery_nested.py").write_text(
        "class TestNested:\n"
        "    def test_uses_fixture(self, missing_fixture):\n"
        "        pass\n",
        encoding="utf-8",
    )

    assert unregistered_test_arguments(tmp_path, set(), "test_recovery_*.py") == {
        "missing_fixture"
    }
```

Run `uv run pytest tests/test_recovery_architecture.py -v` and confirm it still passes before moving on.
This step changes no A4a behavior; it exists so the next step has a correct scanner to call.

- [ ] **Step 3: Add the architecture characterization tests**

Append to `python/tests/test_fs_architecture.py`:

```python
import inspect

import atoms.fs
from atoms.fs.volume import CERTIFIED_ALLOWLIST, DurabilityAllowlist


def test_bind_requires_a_keyword_only_allowlist_with_no_default():
    signature = inspect.signature(atoms.fs.bind_project_volume)
    parameter = signature.parameters["allowlist"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty
    storage = signature.parameters["storage"]
    assert storage.kind is inspect.Parameter.KEYWORD_ONLY
    assert storage.default is inspect.Parameter.empty


def test_certified_allowlist_is_empty_so_population_is_deliberate():
    assert CERTIFIED_ALLOWLIST == DurabilityAllowlist(entries=frozenset())


_BIND_PROJECT_VOLUME_TARGETS = {
    "atoms.fs.bind_project_volume",
    "atoms.fs.binding.bind_project_volume",
}


def _source_module(source_root: Path, source_path: Path) -> tuple[str, str]:
    parts = source_path.relative_to(source_root).with_suffix("").parts
    if parts[-1] == "__init__":
        module = ".".join(parts[:-1])
        return module, module
    module = ".".join(parts)
    return module, module.rpartition(".")[0]


def _dotted_name(expression: ast.expr) -> str | None:
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Attribute):
        parent = _dotted_name(expression.value)
        if parent is not None:
            return f"{parent}.{expression.attr}"
    return None


def _import_aliases(tree: ast.Module, *, module: str, package: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    if module == "atoms.fs.binding":
        aliases["bind_project_volume"] = "atoms.fs.binding.bind_project_volume"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                local = imported.asname or imported.name.split(".", 1)[0]
                aliases[local] = imported.name if imported.asname else local
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        imported_from = (
            resolve_name(
                f"{'.' * node.level}{node.module or ''}",
                package,
            )
            if node.level
            else node.module or ""
        )
        for imported in node.names:
            if imported.name == "*":
                if imported_from in {"atoms.fs", "atoms.fs.binding"}:
                    aliases["bind_project_volume"] = (
                        f"{imported_from}.bind_project_volume"
                    )
                continue
            aliases[imported.asname or imported.name] = (
                f"{imported_from}.{imported.name}"
            )
    return aliases


def _calls_bind_project_volume(
    tree: ast.Module,
    *,
    module: str,
    package: str,
) -> bool:
    aliases = _import_aliases(tree, module=module, package=package)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = _dotted_name(node.func)
        if called is None:
            continue
        head, separator, tail = called.partition(".")
        resolved_head = aliases.get(head, head)
        resolved = (
            f"{resolved_head}.{tail}"
            if separator
            else resolved_head
        )
        if resolved in _BIND_PROJECT_VOLUME_TARGETS:
            return True
    return False


def _production_bind_callers(source_root: Path) -> set[Path]:
    callers: set[Path] = set()
    for source_path in source_root.rglob("*.py"):
        module, package = _source_module(source_root, source_path)
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        if _calls_bind_project_volume(tree, module=module, package=package):
            callers.add(source_path)
    return callers


def test_no_production_caller_of_bind_exists_yet():
    # A4a has no production composition root, so it cannot assert which allowlist
    # is passed. That call-site assertion is ledger entry #18, owned by A5.
    source_root = Path(__file__).parents[1] / "src"
    assert _production_bind_callers(source_root) == set()


def test_verified_child_path_is_not_exported():
    assert not hasattr(atoms.fs, "verified_child_path")


def test_public_surface_is_exactly_the_documented_names():
    assert sorted(atoms.fs.__all__) == [
        "AllowlistEntry",
        "Backend",
        "CERTIFIED_ALLOWLIST",
        "Capability",
        "DurabilityAllowlist",
        "HeldProjectLock",
        "ProjectBinding",
        "StorageProfile",
        "VolumeConfiguration",
        "VolumeEvidence",
        "acquire_project_lock",
        "bind_project_volume",
        "reclaim_probe_survivors",
        "select_backend",
    ]


def test_fs_fixture_registry_covers_every_test_argument():
    tests_root = Path(__file__).parent
    registered = fixture_names(tests_root / "conftest.py")
    assert unregistered_test_arguments(tests_root, registered, "test_fs_*.py") == set()


def test_fs_fixture_registry_subtracts_parametrized_arguments(tmp_path):
    # The A4a suite parametrizes on 'absent', 'operation', 'code', and 'expected'.
    # A scanner that treated every argument as a fixture would reject all four, so
    # this pins the behavior that makes reusing A3's scanner worth the extraction.
    (tmp_path / "test_fs_parametrized.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize(('operation', 'code'), [('exchange', 22)])\n"
        "def test_uses_parametrized_values(operation, code):\n"
        "    pass\n"
        "@pytest.mark.parametrize('absent', [1])\n"
        "def test_uses_one_parametrized_value(absent):\n"
        "    pass\n",
        encoding="utf-8",
    )

    assert unregistered_test_arguments(tmp_path, set(), "test_fs_*.py") == set()


def test_fs_fixture_registry_still_catches_a_genuine_omission(tmp_path):
    # The converse, so the test above cannot pass by the scanner finding nothing.
    (tmp_path / "test_fs_missing.py").write_text(
        "def test_uses_unregistered(nonexistent_fixture):\n    pass\n",
        encoding="utf-8",
    )

    assert unregistered_test_arguments(tmp_path, set(), "test_fs_*.py") == {
        "nonexistent_fixture"
    }
```

Add `from tests.architecture_support import fixture_names, unregistered_test_arguments` to the imports
of `python/tests/test_fs_architecture.py`. The `ast` import Task 1 added is still used by
`test_core_never_imports_the_filesystem_layer`; no `re` import is needed.

Review fix round 1 adds synthetic caller mutations in `atoms/app/__init__.py`,
`atoms/other/binding.py`, imported-alias and module-alias forms, plus calls inside the exact defining
and re-export modules. Pure definition and re-export nodes remain non-callers. A separate architecture
assertion requires this plan, the A4a design, and `AGENTS.md` to agree on the 2026-07-30 implementation
date, the remaining unimplemented sub-plans, and the metadata-root-only mutation scope.

- [ ] **Step 4: Run the characterization checkpoint**

Run: `uv run pytest tests/test_fs_architecture.py tests/test_fs_probe.py -v`
Expected: PASS. Tasks 1–6 already built the signature, exports, fixture adapters, contract-level errno
injection, and exact-errno guards these tests characterize. Read every result: this is a confirmation
checkpoint, not a red-to-green cycle.

- [ ] **Step 5: Resolve any characterization deviation at its owning task**

If Step 4 passes, make no edit in this step. If it fails, stop Task 7 and repair the deviation in the
earlier task that owns the asserted behavior, then rerun Step 4. A missing fixture is repaired by adding
the adapter to `python/tests/conftest.py`, never by renaming the test argument. Task 7 must not manufacture
a production change merely to create a RED/GREEN story.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q && uv run ruff check && uv run pyright`
Expected: all pass, with only the explicitly named `test_volume`, `distinct_volume`, xattr-unsupported,
or real-bind-mount environment skips accepted after reading their reasons.

- [ ] **Step 7: Update the ledger**

In `docs/deferred-obligation-ledger.md`, leave entries 16, 17, and 18 open — their owners (A4b, A5)
remain outstanding. Entry 6 stays open and owned by A4b: A4a supplies only the mechanism.

Add no discharge rows. A4a discharges no existing entry.

- [ ] **Step 8: Update `AGENTS.md`**

Replace the A4a bullet with:

```markdown
- **A4a — capability backend and project volume binding: implemented on 2026-07-30.**
  `python/src/atoms/fs/`
  holds the `Backend` protocol and its Linux implementation, `ctypes` bindings for `openat2` and
  `renameat2`, mount-identity and durability-configuration resolution, the §5.5 bootstrap under an
  explicit `HeldProjectLock`, the empirical capability probe, and `bind_project_volume`.
  `CERTIFIED_ALLOWLIST` ships empty, so production binding refuses every volume until A8
  crash-certifies a configuration tuple. **A4b — rooted project approval** remains unimplemented and
  owns `approve_for_project`, `ProjectApprovedSpec`, and ledger entries #2, #3 (its part), #4, #5,
  #6, #9, #10, #11, and #16.
```

Also update the trailing sentence: "No code in this repository mutates a filesystem path yet; that
begins at A4a." becomes "A4a mutates only engine-owned `metadata_root`, never project paths."

- [ ] **Step 9: Update this plan's status**

Change the `**Status:**` line at the top of this plan to:

```markdown
**Status:** Implemented on 2026-07-30. A4b and A5–A8 remain unimplemented;
A4a mutates only engine-owned `metadata_root`, never project paths.
```

- [ ] **Step 10: Commit**

```bash
git add tests/architecture_support.py tests/test_recovery_architecture.py \
        tests/test_fs_architecture.py tests/test_fs_probe.py \
        ../docs/deferred-obligation-ledger.md ../AGENTS.md \
        ../docs/plans/2026-07-29-plan-a4a-capability-backend.md
git commit -m "test(fs): lock the errno contract and the a4a boundary"
```

---

## Consolidated final-review corrections (2026-07-30)

These corrections supersede the earlier task code excerpts where they differ; the implemented status and
open ledger obligations are unchanged.

- Empty and NUL-containing logical root spellings refuse with `ProtocolError` before guarded spelling or
  traversal. `verified_child_path` and `ProjectBinding.verified_metadata_path` likewise refuse a
  NUL-containing component before identity syscalls.
- `LinuxBackend` rejects NUL after filesystem encoding, and the raw `openat2` and `renameat2` wrappers
  independently reject NUL in every operand with `ValueError` before either libc route. Tests cover the
  reproduced prefix-truncation case and both rename operands/routes without a syscall or mutation.
- `mountinfo` and `fdinfo` parsing validates required fields, separators, field counts, unsigned-decimal
  grammar, unique mount IDs, and exactly one one-token `mnt_id:` record. Proc reads use
  `errors="surrogateescape"`. Mount fields decode only `\040`, `\011`, `\012`, and `\134`, once; malformed
  records refuse through contextual `CapabilityUnavailable`.
- Parent `sqlite3.OperationalError` at connection, WAL selection, synchronous setup, initial transaction,
  `BEGIN`, `COMMIT`, and final verification becomes phase-specific `CapabilityUnavailable` with the
  original error as `__cause__`; programming faults are not caught. `certify_sqlite_wal(cleanup=True)`
  attempts database/WAL/SHM cleanup on success, refusal, and timeout, attempts all three names, and
  preserves an in-flight certification failure if cleanup also fails. Binding's anchored outer
  reclamation remains authoritative.
- The non-Linux reload test restores the `atoms.fs.platform` package attribute; both filesystem-volume
  fixtures yield from guaranteed temporary-directory teardown; the early-lock-release binding is closed
  in `finally`; every public `__all__` name is required to exist; the actual child script is driven with a
  non-`SQLITE_BUSY` operational failure; and `select_backend() -> Backend` imports only the protocol
  eagerly, leaving Linux/syscall loading lazy on unsupported hosts.

## Self-review checklist

Run before declaring A4a complete.

- [ ] Every design §13 acceptance criterion maps to a passing test:
  1 → `test_core_imports_only_the_allowlisted_modules`; 2 → `test_select_backend_refuses_*`;
  3 → `test_fs_syscalls.py`, `test_operational_enosys_cases_are_in_the_generated_matrix`;
  4 → `test_fs_backend.py` plus `test_fs_probe.py`;
  5 → `test_open_root_refuses_a_symlinked_ancestor`,
  `test_establish_root_normalizes_only_after_the_guarded_walk`,
  `test_establish_root_resolves_a_parent_component_after_a_real_directory`,
  `test_acquire_refuses_a_missing_parent`, `test_acquire_refuses_a_parent_component_as_the_leaf`;
  6 → `test_cross_volume_roots_refuse`, `test_resolve_mount_entry_matches_on_mount_id_not_device`,
  `test_real_bind_mount_with_equal_device_and_distinct_mount_id_refuses`;
  7 → `test_build_configuration_*` for all three filesystems plus
  `test_every_supported_filesystem_has_normalization_coverage`;
  8 → `test_allowlist_matches_only_on_exact_configuration_and_profile`;
  9 → `test_bind_requires_a_keyword_only_allowlist_with_no_default`, `test_no_production_caller_*`;
  10 → `test_fs_lock.py` plus `test_fs_bootstrap.py`, including
  `test_layout_returns_one_owned_descriptor_per_component`,
  `test_sets_the_sync_ignore_marker_on_creation`,
  `test_sync_ignore_marker_failure_does_not_fail_bootstrap`;
  11 → `test_refusal_reclaims_existing_debris_but_writes_nothing_new`,
  `test_a_certification_failure_still_reclaims_probe_debris`,
  `test_a_descriptor_release_failure_still_reclaims`,
  `test_a_failed_second_child_open_leaks_no_descriptor`,
  `test_close_all_attempts_every_descriptor_and_raises_the_first_failure`,
  `test_close_all_raises_the_first_of_several_failures`,
  `test_layout_descriptors_are_released_in_reverse_opening_order`,
  `test_establish_root_parent_close_failure_releases_the_child`,
  `test_establish_root_fstat_failure_releases_the_child`,
  `test_failed_intermediate_release_unwinds_the_retained_layout`,
  `test_layout_fstat_failure_releases_every_owned_descriptor`;
  12 → `test_absent_anchored_traversal_refuses_with_capability_unavailable`,
  `test_absent_advisory_lock_refuses_with_capability_unavailable`,
  `test_a_lock_that_does_not_exclude_refuses_binding`,
  `test_each_absent_optional_capability_binds_and_reports_exactly`,
  `test_openat2_enosys_refuses_with_capability_unavailable`,
  `test_operational_enosys_cases_are_in_the_generated_matrix`,
  `test_each_effective_unsupported_errno_removes_exactly_its_capability`,
  `test_sqlite_certification_observes_the_commit_across_processes`,
  `test_certification_uses_two_children_so_the_parent_can_release_between_them`,
  `test_each_child_verdict_refuses_with_its_own_reason`,
  `test_a_certification_child_that_times_out_refuses`,
  `test_transfer_noclobber_across_distinct_parents`, `test_link_anchor_across_distinct_parents_*`;
  13 → `test_guarded_types_refuse_ordinary_construction`, `test_lock_refuses_ordinary_construction`,
  `test_lock_refuses_dataclass_replacement`, `test_binding_refuses_dataclass_replacement`,
  `test_accessors_refuse_*`, `test_descriptors_are_cloexec`, `test_exit_is_idempotent`;
  14 → `test_verified_child_path_*`, `test_verified_metadata_path_delegates_to_the_shared_verifier`;
  15 → `test_each_effective_unsupported_errno_removes_exactly_its_capability`,
  `test_each_bootstrap_unsupported_errno_refuses_before_probing`,
  `test_linux_backend_propagates_ebadf_outside_probe_precondition`,
  `test_a_traversal_refusal_with_the_wrong_errno_propagates`,
  `test_a_nofollow_refusal_with_the_wrong_errno_propagates`,
  `test_unsupported_errno_sets_exclude_ambiguous_generic_failures`;
  16 → `test_volume` skip path, `distinct_volume` skip path,
  `test_real_bind_mount_with_equal_device_and_distinct_mount_id_refuses` namespace skip paths;
  17 → the full-suite step of Task 7.
- [ ] No probe result is described anywhere in code or comments as a durability guarantee.
- [ ] `bind_project_volume` refuses only on: platform, architecture, unlisted filesystem, unresolvable
      mount, cross-volume roots, allowlist miss, bootstrap prerequisites, SQLite-WAL. Never on an
      optional capability.
- [ ] No `isinstance` on a closed union; no new error type; no runtime dependency added.
- [ ] `atoms/core` imports nothing outside the allowlist, and never `atoms.fs`.
- [ ] Every descriptor A4a opens uses `O_CLOEXEC` and is closed exactly once by its owner. Every
      successful test call to `ensure_metadata_layout` goes through `metadata_layout`,
      `probe_directory`, or `probe_database_path`; a direct failure-path call requires an exception
      before return and verifies complete unwind.
- [ ] Every probe step whose evidence is a *refusal* asserts the exact errno. `grep -n "except OSError"`
      over `src/atoms/fs/` should show no bare `except OSError:` that concludes success.
- [ ] Every mutation test for a **refusal-as-evidence** step passes `override_names`. An unscoped
      injection lands on the earlier availability call and the test stops discriminating — it would
      still pass with `_refused_with` weakened to accept any `OSError`, which is the only thing it exists
      to catch. The table-derived availability matrix is deliberately unscoped and separately pairs each
      effective errno with an invalid direct-call propagation test.
- [ ] Reclamation sits in the **outer** `finally` of `bind_project_volume`, so a failing descriptor
      release cannot skip it, and the layout closes sit in a `finally` rather than on the success path.
- [ ] Every explicit multi-descriptor batch release goes through `close_all`. `grep -n "os.close"
      src/atoms/fs/` should show only *single*-descriptor closes outside `close_all` itself — no two
      adjacent `os.close` calls, anywhere, including unwind blocks. `grep -n "retained.values()"
      src/atoms/fs/` should show exactly two hits, both in `bootstrap.py` and both inside a
      `reversed(...)`: `close_layout` and the layout acquisition unwind. Any hit in `binding.py` or in a
      test means the reversal was bypassed. Every probe that creates or opens more than one thing stages
      it inside `_staged` or `_child_pair`, so failing on the second releases the first. `_child_pair`
      remains the documented heterogeneous `ExitStack` exception to batch-level first-failure precedence.
- [ ] Every descriptor opened through a parent is owned before it is validated. `os.fstat` on a fresh
      descriptor sits inside a `try` whose handler closes it, and a parent released after a child was
      opened through it is released under that child's ownership — a `finally: os.close(parent)` that
      leaves the child unowned is the defect.
- [ ] No failure of a certification child escapes as `subprocess.TimeoutExpired`; `grep -n "timeout"
      src/atoms/fs/probe.py` shows the bound and its single conversion site.
- [ ] `uv run pytest -q`, `uv run ruff check`, and `uv run pyright` are clean; no line exceeds 120
      columns; the worktree is clean.

## Execution handoff

Two execution options:

1. **Subagent-Driven (recommended)** — a fresh subagent per task, with review between tasks.
2. **Inline Execution** — batch execution in this session with checkpoints.
