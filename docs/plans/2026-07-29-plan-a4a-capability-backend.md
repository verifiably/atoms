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

**Status:** Draft for owner review. No A4a production code may land until this plan is approved.

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
- **Mutation discipline:** use TDD for every task. Run the named failing test before production edits.
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
| `python/tests/test_fs_binding.py` | Sequence, refusals, lifetimes, evidence |
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
| §10 errno triage mutations; §11.4 architecture; §12 obligations; status sync | 7 |

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
| `fake_backend` | Task 5 | callable `(supplied: set[Capability], **errno_overrides) -> Backend` |
| `test_allowlist` | Task 6 | callable `(configuration, storage) -> DurabilityAllowlist` singleton |
| `bound_volume` | Task 6 | callable yielding an active `ProjectBinding` on `test_volume` |
| `distinct_volume` | Task 6 | `Path` on a writable mount with a different mount ID; skips if none |

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
Expected: PASS (7 tests)

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
allowlist in `test_fs_architecture.py`, which covers all of `atoms/core` including `recovery/`.

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
  - `LinuxBackend` implementing it
  - `tests.fs_support.resolve_test_volume() -> Path | None`

- [ ] **Step 1: Write the failing backend tests**

Create `python/tests/test_fs_backend.py`:

```python
import errno
import os
import stat

import pytest

from atoms.fs.linux import LinuxBackend


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
    parent = linux_backend.open_root(str(test_volume))
    try:
        with pytest.raises(OSError):
            linux_backend.open_child_directory(parent, "..")
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

import os
from typing import Protocol


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
Expected: PASS (18 tests)

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
Expected: PASS (16 tests)

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

**Files:**
- Create: `python/src/atoms/fs/lock.py`
- Create: `python/src/atoms/fs/bootstrap.py`
- Create: `python/tests/test_fs_lock.py`
- Create: `python/tests/test_fs_bootstrap.py`
- Modify: `python/tests/fs_support.py`
- Modify: `python/tests/conftest.py`

**Interfaces:**
- Consumes: `Backend`, `atoms.fs.platform.select_backend`.
- Produces:
  - `HeldProjectLock` with `backend`, `metadata_root_fd`, `metadata_root_path`, `held`,
    `__enter__`, `__exit__`
  - `acquire_project_lock(backend: Backend, metadata_root: str) -> HeldProjectLock`
  - `establish_root(backend, path: str, create: bool) -> tuple[int, str, bool]` returning
    `(fd, normalized_path, created)`
  - `ensure_metadata_layout(lock: HeldProjectLock) -> dict[str, int]`
  - `reclaim_probe_survivors(lock: HeldProjectLock) -> None`
  - `verified_child_path(metadata_root_fd, metadata_root_path, expected_device, expected_inode, name) -> str`
  - `METADATA_LAYOUT = ("probe", "staging", "work", "blobs/sha256")`
  - `SYNC_IGNORE_ATTRIBUTE = "user.com.dropbox.ignored"`

- [ ] **Step 1: Write the failing lock tests**

Create `python/tests/test_fs_lock.py`:

```python
import dataclasses
import os
import subprocess
import sys

import pytest

from atoms.core.errors import ProtocolError
from atoms.fs.lock import HeldProjectLock, acquire_project_lock


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


def test_acquire_refuses_a_symlink_at_lock(linux_backend, metadata_root):
    metadata_root.mkdir(parents=True)
    (metadata_root / "lock").symlink_to("/etc/passwd")
    with pytest.raises(OSError):
        with acquire_project_lock(linux_backend, str(metadata_root)):
            pass


def test_acquire_refuses_a_directory_at_lock(linux_backend, metadata_root):
    metadata_root.mkdir(parents=True)
    (metadata_root / "lock").mkdir()
    with pytest.raises((OSError, ProtocolError)):
        with acquire_project_lock(linux_backend, str(metadata_root)):
            pass


def test_acquire_refuses_a_missing_parent(linux_backend, metadata_root):
    # A4a creates only the final leaf, never intermediate directories.
    deep = metadata_root / "absent" / "store"
    with pytest.raises(OSError):
        with acquire_project_lock(linux_backend, str(deep)):
            pass


def test_sets_the_sync_ignore_marker_on_creation(linux_backend, metadata_root):
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        try:
            value = os.getxattr(lock.metadata_root_fd, "user.com.dropbox.ignored")
        except OSError:
            pytest.skip("filesystem does not support user extended attributes")
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


def test_lock_refuses_ordinary_construction():
    # Downstream signatures treat the type as proof a real lock is held, so a bare
    # object must not be able to fabricate that proof. Matches the CompiledSpec and
    # RecoveryPlan guards, which also raise TypeError.
    with pytest.raises(TypeError, match="acquire_project_lock"):
        HeldProjectLock()


def test_a_second_process_cannot_acquire_the_same_lock(linux_backend, metadata_root):
    with acquire_project_lock(linux_backend, str(metadata_root)):
        script = (
            "import fcntl, sys\n"
            f"handle = open({str(metadata_root / 'lock')!r}, 'r+')\n"
            "try:\n"
            "    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "except OSError:\n"
            "    sys.exit(3)\n"
            "sys.exit(0)\n"
        )
        finished = subprocess.run([sys.executable, "-c", script], timeout=30)
    assert finished.returncode == 3
```

- [ ] **Step 2: Add the root fixtures**

Append to `python/tests/fs_support.py`:

```python
def make_metadata_root(base):
    """A path that does NOT yet exist, so bootstrap creation is exercised."""
    return base / "metadata"


def make_project_root(base):
    root = base / "project"
    root.mkdir()
    return root
```

Append to `python/tests/conftest.py`:

```python
from atoms.fs.lock import acquire_project_lock
from tests.fs_support import make_metadata_root, make_project_root


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

from atoms.core.errors import ProtocolError
from atoms.fs.backend import Backend

SYNC_IGNORE_ATTRIBUTE = "user.com.dropbox.ignored"

_TOKEN = object()


def establish_root(backend: Backend, path: str, create: bool) -> tuple[int, str, bool]:
    """Open a root through guarded traversal, optionally creating its final leaf.

    Returns (descriptor, normalized path, created). The normalization is lexical,
    which is safe precisely because the guarded walk refuses a symlink at every
    component: with no symlink in the path, collapsing '.' and '..' cannot change
    which entry it names.
    """
    normalized = os.path.abspath(path)
    try:
        return backend.open_root(normalized), normalized, False
    except OSError as caught:
        if not create or caught.errno != errno.ENOENT:
            raise
    parent, leaf = os.path.split(normalized)
    if not leaf:
        raise ProtocolError(f"cannot create a root without a final component: {path!r}")
    # Only the final leaf is created, and only relative to a guarded parent
    # descriptor. A missing parent refuses: a component walk that creates as it
    # goes has races this layer has no need to take on.
    parent_fd = backend.open_root(parent)
    try:
        os.mkdir(leaf, mode=0o700, dir_fd=parent_fd)
        # Reopen through guarded traversal even though we just created it, so the
        # descriptor is guard-checked on the same terms as the existing-root case.
        fd = backend.open_child_directory(parent_fd, leaf)
    finally:
        os.close(parent_fd)
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        os.close(fd)
        raise ProtocolError(f"created metadata root is not a directory: {normalized!r}")
    return fd, normalized, True


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
        os.close(self._lock_fd)
        os.close(self._root_fd)


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
        backend.lock_exclusive(lock_fd)
    except BaseException:
        os.close(lock_fd)
        os.close(root_fd)
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
Expected: PASS (13 tests)

- [ ] **Step 6: Write the failing bootstrap tests**

Create `python/tests/test_fs_bootstrap.py`:

```python
import os

import pytest

from atoms.core.errors import ProtocolError
from atoms.fs.bootstrap import (
    METADATA_LAYOUT,
    ensure_metadata_layout,
    reclaim_probe_survivors,
    verified_child_path,
)


def test_layout_creates_every_directory(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        ensure_metadata_layout(lock)
    for relative in METADATA_LAYOUT:
        assert (metadata_root / relative).is_dir()


def test_layout_is_idempotent_over_existing_directories(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        ensure_metadata_layout(lock)
        ensure_metadata_layout(lock)
    assert (metadata_root / "blobs" / "sha256").is_dir()


def test_layout_refuses_a_symlink_occupying_a_name(held_lock, metadata_root):
    # Tolerating EEXIST without reopening would adopt whatever occupies the name.
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
        ensure_metadata_layout(lock)
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
        ensure_metadata_layout(lock)
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

import errno
import os
import stat

from atoms.core.errors import ProtocolError
from atoms.fs.lock import HeldProjectLock

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
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        os.close(fd)
        raise ProtocolError(f"metadata layout component is not a directory: {name!r}")
    return fd


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
            for fd in opened:
                os.close(fd)
            for fd in retained.values():
                os.close(fd)
            raise
        retained[relative] = opened[-1]
        for fd in opened[:-1]:
            os.close(fd)
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
Expected: PASS (25 tests)

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
- Modify: `python/tests/conftest.py`

**Interfaces:**
- Consumes: `Backend`, `HeldProjectLock`, `bootstrap.verified_child_path`,
  `atoms.core.capabilities.Capability`.
- Produces:
  - `probe_backend(backend: Backend, probe_root_fd: int, lock: HeldProjectLock) -> frozenset[Capability]`
  - `certify_sqlite_wal(database_path: str) -> None` raising `CapabilityUnavailable`
  - `UNSUPPORTED_ERRNO: dict[str, frozenset[int]]`

- [ ] **Step 1: Write the failing probe tests**

Create `python/tests/test_fs_probe.py`:

```python
import errno
import os
import sqlite3

import pytest

from atoms.core.capabilities import Capability
from atoms.core.errors import CapabilityUnavailable
from atoms.fs.bootstrap import ensure_metadata_layout, verified_child_path
from atoms.fs.probe import UNSUPPORTED_ERRNO, certify_sqlite_wal, probe_backend


def _probe_fd(lock):
    ensure_metadata_layout(lock)
    return lock.backend.open_child_directory(lock.metadata_root_fd, "probe")


def test_real_volume_supplies_every_capability(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        probe_fd = _probe_fd(lock)
        try:
            supplied = probe_backend(lock.backend, probe_fd, lock)
        finally:
            os.close(probe_fd)
    assert supplied == frozenset(Capability)


def test_probe_leaves_no_survivors(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        probe_fd = _probe_fd(lock)
        try:
            probe_backend(lock.backend, probe_fd, lock)
            assert os.listdir(probe_fd) == []
        finally:
            os.close(probe_fd)


def test_missing_exchange_is_reported_not_raised(held_lock, metadata_root, fake_backend):
    backend = fake_backend(supplied=set(Capability) - {Capability.ATOMIC_EXCHANGE})
    with held_lock(metadata_root) as lock:
        probe_fd = _probe_fd(lock)
        try:
            supplied = probe_backend(backend, probe_fd, lock)
        finally:
            os.close(probe_fd)
    assert Capability.ATOMIC_EXCHANGE not in supplied
    assert Capability.NOCLOBBER_TRANSFER in supplied


@pytest.mark.parametrize(
    "absent",
    [
        Capability.NOCLOBBER_TRANSFER,
        Capability.IDENTITY_ANCHOR,
        Capability.DURABLE_PUBLISH,
        Capability.NOFOLLOW_COHERENT_READ,
        Capability.SYMLINK_FINGERPRINT,
    ],
)
def test_each_optional_capability_can_be_absent(held_lock, metadata_root, fake_backend, absent):
    backend = fake_backend(supplied=set(Capability) - {absent})
    with held_lock(metadata_root) as lock:
        probe_fd = _probe_fd(lock)
        try:
            supplied = probe_backend(backend, probe_fd, lock)
        finally:
            os.close(probe_fd)
    assert absent not in supplied


def test_unexpected_errno_propagates_rather_than_reporting_absence(
    held_lock, metadata_root, fake_backend
):
    # EBADF is a bug or an environmental failure, never an unsupported operation.
    backend = fake_backend(supplied=set(Capability), exchange_errno=errno.EBADF)
    with held_lock(metadata_root) as lock:
        probe_fd = _probe_fd(lock)
        try:
            with pytest.raises(OSError) as caught:
                probe_backend(backend, probe_fd, lock)
            assert caught.value.errno == errno.EBADF
        finally:
            os.close(probe_fd)


def test_unsupported_errno_sets_exclude_ambiguous_generic_failures():
    for operation, codes in UNSUPPORTED_ERRNO.items():
        assert errno.EBADF not in codes, operation
        assert errno.EMFILE not in codes, operation
        assert errno.EFAULT not in codes, operation


def test_sqlite_wal_certification_succeeds_on_the_test_volume(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        ensure_metadata_layout(lock)
        info = os.fstat(lock.metadata_root_fd)
        probe_dir = verified_child_path(
            lock.metadata_root_fd, lock.metadata_root_path, info.st_dev, info.st_ino, "probe"
        )
        certify_sqlite_wal(os.path.join(probe_dir, "certify.db"))


def test_sqlite_certification_observes_the_commit_across_processes(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        ensure_metadata_layout(lock)
        info = os.fstat(lock.metadata_root_fd)
        probe_dir = verified_child_path(
            lock.metadata_root_fd, lock.metadata_root_path, info.st_dev, info.st_ino, "probe"
        )
        database = os.path.join(probe_dir, "certify.db")
        certify_sqlite_wal(database)
        # The child's committed user_version=2 must be what survives.
        connection = sqlite3.connect(database)
        try:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        finally:
            connection.close()


def test_sqlite_certification_removes_its_files(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        ensure_metadata_layout(lock)
        probe_fd = lock.backend.open_child_directory(lock.metadata_root_fd, "probe")
        try:
            info = os.fstat(lock.metadata_root_fd)
            probe_dir = verified_child_path(
                lock.metadata_root_fd, lock.metadata_root_path, info.st_dev, info.st_ino, "probe"
            )
            certify_sqlite_wal(os.path.join(probe_dir, "certify.db"), cleanup=True)
            assert os.listdir(probe_fd) == []
        finally:
            os.close(probe_fd)


def test_sqlite_certification_refuses_when_wal_is_unavailable(tmp_path, monkeypatch):
    class RefusingConnection:
        def execute(self, statement, *args):
            if "journal_mode" in statement:
                return [("delete",)]
            return []

        def close(self):
            pass

    monkeypatch.setattr("atoms.fs.probe.sqlite3.connect", lambda *a, **k: RefusingConnection())
    with pytest.raises(CapabilityUnavailable, match="WAL"):
        certify_sqlite_wal(str(tmp_path / "x.db"))
```

- [ ] **Step 2: Add the fake backend**

Append to `python/tests/fs_support.py`:

```python
import errno as _errno

from atoms.core.capabilities import Capability
from atoms.fs.linux import LinuxBackend


class RestrictedBackend:
    """A capability-restricted backend proving the protocol admits a non-Linux one.

    It delegates to a real LinuxBackend for supplied capabilities and raises a
    chosen errno for absent ones, so every refusal branch is reachable without a
    filesystem that genuinely lacks the operation.
    """

    _ABSENT_ERRNO = _errno.EOPNOTSUPP

    def __init__(self, supplied, lock_excludes=True, **errno_overrides):
        self._supplied = set(supplied)
        self._lock_excludes = lock_excludes
        self._overrides = errno_overrides
        self._real = LinuxBackend()

    def _dispatch(self, capability, operation, *args):
        override = self._overrides.get(operation)
        if override is not None:
            raise OSError(override, "injected")
        if capability not in self._supplied:
            raise OSError(self._ABSENT_ERRNO, "capability withheld")
        return getattr(self._real, operation)(*args)

    def open_root(self, path):
        return self._dispatch(Capability.ANCHORED_TRAVERSAL, "open_root", path)

    def open_child_directory(self, parent_fd, name):
        return self._dispatch(
            Capability.ANCHORED_TRAVERSAL, "open_child_directory", parent_fd, name
        )

    def exchange(self, parent_fd, left, right):
        return self._dispatch(Capability.ATOMIC_EXCHANGE, "exchange", parent_fd, left, right)

    def transfer_noclobber(self, src_fd, src, dst_fd, dst):
        return self._dispatch(
            Capability.NOCLOBBER_TRANSFER, "transfer_noclobber", src_fd, src, dst_fd, dst
        )

    def link_anchor(self, src_fd, src, dst_fd, dst):
        return self._dispatch(Capability.IDENTITY_ANCHOR, "link_anchor", src_fd, src, dst_fd, dst)

    def flush_file(self, fd):
        return self._dispatch(Capability.DURABLE_PUBLISH, "flush_file", fd)

    def flush_directory(self, fd):
        return self._dispatch(Capability.DURABLE_PUBLISH, "flush_directory", fd)

    def open_regular_nofollow(self, parent_fd, name):
        return self._dispatch(
            Capability.NOFOLLOW_COHERENT_READ, "open_regular_nofollow", parent_fd, name
        )

    def symlink_fingerprint(self, parent_fd, name):
        return self._dispatch(
            Capability.SYMLINK_FINGERPRINT, "symlink_fingerprint", parent_fd, name
        )

    def lock_exclusive(self, fd):
        return self._dispatch(Capability.ADVISORY_PROJECT_LOCK, "lock_exclusive", fd)

    def try_lock_exclusive(self, fd):
        if not self._lock_excludes:
            # A filesystem where flock succeeds but does not actually exclude —
            # the real case on NFS without a working lock daemon.
            return True
        return self._dispatch(Capability.ADVISORY_PROJECT_LOCK, "try_lock_exclusive", fd)


def make_fake_backend():
    def build(supplied, lock_excludes=True, **errno_overrides):
        return RestrictedBackend(supplied, lock_excludes=lock_excludes, **errno_overrides)

    return build
```

Append to `python/tests/conftest.py`:

```python
from tests.fs_support import make_fake_backend


@pytest.fixture
def fake_backend():
    return make_fake_backend()
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

import errno
import os
import sqlite3
import stat
import subprocess
import sys

from atoms.core.capabilities import Capability
from atoms.core.errors import CapabilityUnavailable
from atoms.fs.backend import Backend
from atoms.fs.lock import HeldProjectLock

# Errno values that conclusively mean "this volume does not support the operation".
#
# EINVAL from renameat2 and EPERM from link are ambiguous in general — they equally
# signal a malformed argument or a permission failure. What disambiguates them is
# the probe precondition: every probe constructs its own operands, inside a
# directory it created, under the held project lock, immediately before the call.
# Arguments are therefore valid and permissions guaranteed by construction, so
# neither interpretation is reachable. The same errno from any other call site
# propagates.
UNSUPPORTED_ERRNO: dict[str, frozenset[int]] = {
    "exchange": frozenset({errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.ENOTSUP}),
    "transfer_noclobber": frozenset(
        {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.ENOTSUP}
    ),
    "link_anchor": frozenset({errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP}),
    "flush": frozenset({errno.EINVAL, errno.EOPNOTSUPP, errno.ENOTSUP}),
    "open_regular_nofollow": frozenset({errno.EOPNOTSUPP, errno.ENOTSUP}),
    "symlink_fingerprint": frozenset({errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP}),
    "lock": frozenset({errno.ENOLCK, errno.EOPNOTSUPP, errno.ENOTSUP}),
    "traversal": frozenset({errno.ENOSYS}),
}


def _supported(operation: str, probe) -> bool:
    try:
        probe()
    except OSError as caught:
        if caught.errno in UNSUPPORTED_ERRNO[operation]:
            return False
        raise
    return True


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


def _probe_traversal(backend: Backend, probe_fd: int) -> bool:
    os.mkdir("real", mode=0o700, dir_fd=probe_fd)
    os.symlink("real", "escape", dir_fd=probe_fd)
    try:
        if not _supported("traversal", lambda: backend.open_child_directory(probe_fd, "real")):
            return False
        opened = backend.open_child_directory(probe_fd, "real")
        os.close(opened)
        for refused in ("escape", ".."):
            try:
                fd = backend.open_child_directory(probe_fd, refused)
            except OSError:
                continue
            os.close(fd)
            return False
        return True
    finally:
        os.unlink("escape", dir_fd=probe_fd)
        os.rmdir("real", dir_fd=probe_fd)


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
    _write(probe_fd, "left", b"L")
    _write(probe_fd, "right", b"R")
    try:
        if not _supported("exchange", lambda: backend.exchange(probe_fd, "left", "right")):
            return False
        return _read(probe_fd, "left") == b"R" and _read(probe_fd, "right") == b"L"
    finally:
        _clear(probe_fd)


def _probe_transfer(backend: Backend, probe_fd: int) -> bool:
    # The distinct-parent form is what blob promotion and staging publication use.
    os.mkdir("src", mode=0o700, dir_fd=probe_fd)
    os.mkdir("dst", mode=0o700, dir_fd=probe_fd)
    src_fd = backend.open_child_directory(probe_fd, "src")
    dst_fd = backend.open_child_directory(probe_fd, "dst")
    try:
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
    finally:
        _clear(src_fd)
        _clear(dst_fd)
        os.close(src_fd)
        os.close(dst_fd)
        _clear(probe_fd)


def _probe_link(backend: Backend, probe_fd: int) -> bool:
    os.mkdir("src", mode=0o700, dir_fd=probe_fd)
    os.mkdir("dst", mode=0o700, dir_fd=probe_fd)
    src_fd = backend.open_child_directory(probe_fd, "src")
    dst_fd = backend.open_child_directory(probe_fd, "dst")
    try:
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
    finally:
        _clear(src_fd)
        _clear(dst_fd)
        os.close(src_fd)
        os.close(dst_fd)
        _clear(probe_fd)


def _probe_flush(backend: Backend, probe_fd: int) -> bool:
    _write(probe_fd, "payload", b"P")
    fd = os.open("payload", os.O_RDONLY | os.O_CLOEXEC, dir_fd=probe_fd)
    try:

        def attempt():
            backend.flush_file(fd)
            backend.flush_directory(probe_fd)

        return _supported("flush", attempt)
    finally:
        os.close(fd)
        _clear(probe_fd)


def _probe_nofollow_read(backend: Backend, probe_fd: int) -> bool:
    _write(probe_fd, "payload", b"P")
    os.symlink("payload", "alias", dir_fd=probe_fd)
    try:
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
        try:
            leaked = backend.open_regular_nofollow(probe_fd, "alias")
        except OSError:
            return True
        os.close(leaked)
        return False
    finally:
        _clear(probe_fd)


def _probe_symlink_fingerprint(backend: Backend, probe_fd: int) -> bool:
    os.symlink("../target", "alias", dir_fd=probe_fd)
    try:
        captured: list[tuple] = []

        def attempt():
            captured.append(backend.symlink_fingerprint(probe_fd, "alias"))

        if not _supported("symlink_fingerprint", attempt):
            return False
        info, target = captured[0]
        return stat.S_ISLNK(info.st_mode) and target == "../target"
    finally:
        _clear(probe_fd)


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


_CHILD_READER = """
import sqlite3, sys
database = sys.argv[1]
connection = sqlite3.connect(database, timeout=0, isolation_level=None)
if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
    sys.exit(3)
try:
    connection.execute("BEGIN IMMEDIATE")
except sqlite3.OperationalError:
    pass
else:
    sys.exit(4)
connection.execute("PRAGMA busy_timeout=10000")
connection.execute("BEGIN IMMEDIATE")
connection.execute("PRAGMA user_version=2")
connection.execute("COMMIT")
connection.close()
sys.exit(0)
"""


def certify_sqlite_wal(database_path: str, cleanup: bool = False) -> None:
    """Certify the volume can host the SQLite-WAL metadata store (design §8.3).

    Opening a database and selecting WAL mode is insufficient: WAL can operate
    without shared memory when SQLite runs in exclusive locking mode. The second
    reader must be a separate PROCESS, because a same-process connection exercises
    WAL but not SQLite's cross-process POSIX locking contract, and the shared-memory
    WAL index exists precisely to coordinate readers across processes.

    The child receives a pathname and re-resolves it. That is acceptable only
    because probe/ is engine-owned, sits under the held project lock, and contains
    no transaction state; the exemption extends to nothing outside probe/.
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
        finished = subprocess.run(
            [sys.executable, "-c", _CHILD_READER, database_path],
            timeout=60,
            capture_output=True,
        )
        parent.execute("COMMIT")
        if finished.returncode == 3:
            raise CapabilityUnavailable(
                "a second process could not read the committed WAL state"
            )
        if finished.returncode == 4:
            raise CapabilityUnavailable(
                "a second process acquired the write lock while it was held"
            )
        if finished.returncode != 0:
            raise CapabilityUnavailable(
                f"SQLite-WAL certification child failed: {finished.stderr!r}"
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
Expected: PASS (16 tests)

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
import os

import pytest

from atoms.core.capabilities import Capability
from atoms.core.errors import CapabilityUnavailable, ProtocolError
from atoms.fs.binding import ProjectBinding, VolumeEvidence, bind_project_volume
from atoms.fs.volume import CERTIFIED_ALLOWLIST, DurabilityAllowlist


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


def test_absent_anchored_traversal_refuses_before_a_lock_is_even_held(
    metadata_root, fake_backend
):
    from atoms.fs.lock import acquire_project_lock

    backend = fake_backend(supplied=set(Capability) - {Capability.ANCHORED_TRAVERSAL})
    with pytest.raises(OSError):
        acquire_project_lock(backend, str(metadata_root))


def test_a_lock_that_does_not_exclude_refuses_binding(
    project_root, metadata_root, fake_backend, test_allowlist, test_storage_profile
):
    # The bootstrap-prerequisite check is not dead code. flock can succeed while
    # failing to exclude — the real case on NFS without a working lock daemon — so
    # the probe reports advisory_project_lock absent even though acquisition worked.
    from atoms.fs.lock import acquire_project_lock

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


def test_absent_optional_capability_binds_and_reports(bound_volume):
    with bound_volume(withhold={Capability.ATOMIC_EXCHANGE}) as binding:
        assert Capability.ATOMIC_EXCHANGE not in binding.evidence.supplied_capabilities
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


def test_evidence_is_frozen(bound_volume):
    with bound_volume() as binding:
        with pytest.raises(dataclasses.FrozenInstanceError):
            binding.evidence.mount_id = 1
```

- [ ] **Step 2: Add the binding fixtures**

Append to `python/tests/fs_support.py`:

```python
import contextlib

from atoms.fs.lock import acquire_project_lock
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

Append to `python/tests/conftest.py`:

```python
from tests.fs_support import (
    find_distinct_mount,
    make_bound_volume,
    make_fake_backend,
    make_test_allowlist,
)


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
        probe_fd = retained[PROBE_DIRECTORY]
        try:
            supplied = probe_backend(backend, probe_fd, lock)
            missing = _BOOTSTRAP_PREREQUISITES - supplied
            if missing:
                raise CapabilityUnavailable(
                    "bootstrap prerequisites unavailable: "
                    + ", ".join(sorted(item.value for item in missing))
                )
            database = verified_child_path(
                metadata_root_fd,
                lock.metadata_root_path,
                metadata_info.st_dev,
                metadata_info.st_ino,
                PROBE_DIRECTORY,
            )
            certify_sqlite_wal(os.path.join(database, "certify.db"), cleanup=True)
        finally:
            for fd in retained.values():
                os.close(fd)

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
Expected: PASS (16 tests)

- [ ] **Step 7: Commit**

```bash
git add src/atoms/fs/binding.py src/atoms/fs/__init__.py tests/test_fs_binding.py \
        tests/fs_support.py tests/conftest.py
git commit -m "feat(fs): bind a certified project volume to frozen evidence"
```

---

### Task 7: Errno mutations, architecture enforcement, and status sync

Close the boundary with the mutation tests that prove ambiguous errno values are licensed only by the
probe precondition, and update the ledger and `AGENTS.md` in the same commit.

**Files:**
- Modify: `python/tests/test_fs_architecture.py`
- Modify: `python/tests/test_fs_probe.py`
- Modify: `docs/deferred-obligation-ledger.md`
- Modify: `AGENTS.md`
- Modify: `docs/plans/2026-07-29-plan-a4a-capability-backend.md`

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: no new production interface.

- [ ] **Step 1: Write the failing errno mutation tests**

Append to `python/tests/test_fs_probe.py`:

```python
@pytest.mark.parametrize(
    ("operation", "code"),
    [
        ("exchange", errno.EINVAL),
        ("transfer_noclobber", errno.EINVAL),
        ("link_anchor", errno.EPERM),
    ],
)
def test_ambiguous_errno_is_licensed_only_by_the_probe_precondition(
    held_lock, metadata_root, fake_backend, operation, code
):
    # EINVAL and EPERM are ambiguous in general. Inside the probe they conclude
    # "absent" because operands are constructed by the probe itself; the same
    # errno from any other call site must propagate.
    assert code in UNSUPPORTED_ERRNO[operation]
    backend = fake_backend(supplied=set(Capability), **{f"{operation}_errno": code})
    with held_lock(metadata_root) as lock:
        probe_fd = _probe_fd(lock)
        try:
            supplied = probe_backend(backend, probe_fd, lock)
        finally:
            os.close(probe_fd)
    assert len(supplied) < len(frozenset(Capability))


@pytest.mark.parametrize("code", [errno.EBADF, errno.EMFILE, errno.EFAULT])
@pytest.mark.parametrize("operation", ["exchange", "transfer_noclobber", "link_anchor"])
def test_precondition_violating_errno_propagates(
    held_lock, metadata_root, fake_backend, operation, code
):
    backend = fake_backend(supplied=set(Capability), **{f"{operation}_errno": code})
    with held_lock(metadata_root) as lock:
        probe_fd = _probe_fd(lock)
        try:
            with pytest.raises(OSError) as caught:
                probe_backend(backend, probe_fd, lock)
            assert caught.value.errno == code
        finally:
            os.close(probe_fd)
```

The `RestrictedBackend._dispatch` override key is the operation name, so update `fs_support.py` to strip
the `_errno` suffix:

```python
    def _dispatch(self, capability, operation, *args):
        override = self._overrides.get(f"{operation}_errno")
        if override is not None:
            raise OSError(override, "injected")
        if capability not in self._supplied:
            raise OSError(self._ABSENT_ERRNO, "capability withheld")
        return getattr(self._real, operation)(*args)
```

Update `test_unexpected_errno_propagates_rather_than_reporting_absence` in
`python/tests/test_fs_probe.py` to pass `exchange_errno=errno.EBADF`, matching the new key.

- [ ] **Step 2: Write the failing architecture tests**

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


def test_no_production_caller_of_bind_exists_yet():
    # A4a has no production composition root, so it cannot assert which allowlist
    # is passed. That call-site assertion is ledger entry #18, owned by A5.
    source_root = Path(__file__).parents[1] / "src"
    callers = [
        path
        for path in source_root.rglob("*.py")
        if "bind_project_volume(" in path.read_text(encoding="utf-8")
        and path.name != "binding.py"
        and path.name != "__init__.py"
    ]
    assert callers == []


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


def test_every_test_fixture_is_registered():
    conftest = (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")
    registered = set(re.findall(r"^def (\w+)\(", conftest, flags=re.MULTILINE))
    builtins = {
        "monkeypatch",
        "pytest",
        "request",
        "tmp_path",
        "tmp_path_factory",
        "capsys",
        "caplog",
    }
    for path in Path(__file__).parent.glob("test_fs_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            for argument in node.args.args:
                if argument.arg in builtins or argument.arg in registered:
                    continue
                raise AssertionError(f"{path.name}::{node.name} uses unregistered {argument.arg!r}")
```

Add `import re` to the imports of `python/tests/test_fs_architecture.py`.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fs_architecture.py tests/test_fs_probe.py -v`
Expected: FAIL on the new architecture and mutation assertions.

- [ ] **Step 4: Make the tests pass**

Apply the `fs_support.py` `_dispatch` change from Step 1. No other production change should be
required; if `test_every_test_fixture_is_registered` fails, add the missing adapter to
`python/tests/conftest.py` rather than renaming the test argument.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q && uv run ruff check && uv run pyright`
Expected: all pass.

- [ ] **Step 6: Update the ledger**

In `docs/deferred-obligation-ledger.md`, leave entries 16, 17, and 18 open — their owners (A4b, A5)
remain outstanding. Entry 6 stays open and owned by A4b: A4a supplies only the mechanism.

Add no discharge rows. A4a discharges no existing entry.

- [ ] **Step 7: Update `AGENTS.md`**

Replace the A4a bullet with:

```markdown
- **A4a — capability backend and project volume binding: implemented.** `python/src/atoms/fs/`
  holds the `Backend` protocol and its Linux implementation, `ctypes` bindings for `openat2` and
  `renameat2`, mount-identity and durability-configuration resolution, the §5.5 bootstrap under an
  explicit `HeldProjectLock`, the empirical capability probe, and `bind_project_volume`.
  `CERTIFIED_ALLOWLIST` ships empty, so production binding refuses every volume until A8
  crash-certifies a configuration tuple. **A4b — rooted project approval** remains unimplemented and
  owns `approve_for_project`, `ProjectApprovedSpec`, and ledger entries #2, #3 (its part), #4, #5,
  #6, #9, #10, #11, and #16.
```

Also update the trailing sentence: "No code in this repository mutates a filesystem path yet; that
begins at A4a." becomes "A4a is the first layer that touches a filesystem; it writes only inside the
engine-owned `metadata_root`, never a project path."

- [ ] **Step 8: Update this plan's status**

Change the `**Status:**` line at the top of this plan to:

```markdown
**Status:** Implemented on 2026-07-29. A4b and A5–A8 remain unimplemented; no code in this
repository mutates a project path.
```

- [ ] **Step 9: Commit**

```bash
git add tests/test_fs_architecture.py tests/test_fs_probe.py tests/fs_support.py \
        ../docs/deferred-obligation-ledger.md ../AGENTS.md \
        ../docs/plans/2026-07-29-plan-a4a-capability-backend.md
git commit -m "test(fs): lock the errno contract and the a4a boundary"
```

---

## Self-review checklist

Run before declaring A4a complete.

- [ ] Every design §13 acceptance criterion maps to a passing test:
  1 → `test_core_imports_only_the_allowlisted_modules`; 2 → `test_select_backend_refuses_*`;
  3 → `test_fs_syscalls.py`; 4 → `test_fs_backend.py` plus `test_fs_probe.py`;
  5 → `test_open_root_refuses_a_symlinked_ancestor`, `test_acquire_refuses_a_missing_parent`;
  6 → `test_cross_volume_roots_refuse`, `test_resolve_mount_entry_matches_on_mount_id_not_device`;
  7 → `test_build_configuration_*`; 8 → `test_allowlist_matches_only_on_exact_configuration_and_profile`;
  9 → `test_bind_requires_a_keyword_only_allowlist_with_no_default`, `test_no_production_caller_*`;
  10 → `test_fs_lock.py` plus `test_fs_bootstrap.py`;
  11 → `test_refusal_reclaims_existing_debris_but_writes_nothing_new`;
  12 → `test_absent_anchored_traversal_refuses_before_a_lock_is_even_held`,
  `test_a_lock_that_does_not_exclude_refuses_binding`,
  `test_absent_optional_capability_binds_and_reports`,
  `test_sqlite_certification_observes_the_commit_across_processes`,
  `test_transfer_noclobber_across_distinct_parents`, `test_link_anchor_across_distinct_parents_*`;
  13 → `test_guarded_types_refuse_ordinary_construction`, `test_lock_refuses_ordinary_construction`,
  `test_accessors_refuse_*`, `test_descriptors_are_cloexec`, `test_exit_is_idempotent`;
  14 → `test_verified_child_path_*`, `test_verified_metadata_path_delegates_to_the_shared_verifier`;
  15 → `test_precondition_violating_errno_propagates`,
  `test_ambiguous_errno_is_licensed_only_by_the_probe_precondition`;
  16 → `test_volume` skip path, `distinct_volume` skip path;
  17 → the full-suite step of Task 7.
- [ ] No probe result is described anywhere in code or comments as a durability guarantee.
- [ ] `bind_project_volume` refuses only on: platform, architecture, unlisted filesystem, unresolvable
      mount, cross-volume roots, allowlist miss, bootstrap prerequisites, SQLite-WAL. Never on an
      optional capability.
- [ ] No `isinstance` on a closed union; no new error type; no runtime dependency added.
- [ ] `atoms/core` imports nothing outside the allowlist, and never `atoms.fs`.
- [ ] Every descriptor A4a opens uses `O_CLOEXEC` and is closed exactly once by its owner.
- [ ] `uv run pytest -q`, `uv run ruff check`, and `uv run pyright` are clean; no line exceeds 120
      columns; the worktree is clean.

## Execution handoff

Two execution options:

1. **Subagent-Driven (recommended)** — a fresh subagent per task, with review between tasks.
2. **Inline Execution** — batch execution in this session with checkpoints.
