# A6 Coherent Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill authority §7.3 step 1 — coherently capture and verify the complete initial surface into
`staging/<txid>/` — and deliver the observation mechanism A7 will reuse for recovery, without executing
any effect or mutating any project path.

**Architecture:** One module in `atoms/fs/` and two in `atoms/coordinator/`. `fs/observe.py` holds
`Observation`, a live resource owning a token universe and one pinned descriptor per observed identity;
it imports `fs` and `core.recovery.model` only, so A7 gets it without a lease or a store.
`coordinator/descriptors.py` holds `DescriptorTable`, the held, re-validated, node-keyed descriptor set
that outlives capture and becomes A7's execution anchor. `coordinator/capture.py` holds
`capture_initial_surface`, which drives the observer over the approved surface, stages preimages and
consumer-supplied postimages, and returns the manifest `prepare_transaction` already accepts.

**Tech Stack:** Python 3.11+, stdlib only (`os`, `hashlib`, `errno`, `contextlib`, `dataclasses`,
`typing`), `pytest`, `ruff`, `pyright`. Builds on A4a's `Backend`/`ProjectBinding`, A4b's
`ProjectApprovedSpec` and `read_lookup_constraints`, A5a's `Workspace`/`StagedBlob`/`verify_leaf`,
A5b's `Lease` and `_parent_paths`, and A3's `core.recovery.model`.

**Design:** [`2026-08-07-a6-coherent-capture-design.md`](2026-08-07-a6-coherent-capture-design.md).
**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md).
Where this plan and either document disagree, the design wins over this plan and the authority wins
over both.

## Measured facts this plan is built on

Every shape below was executed against the live tree on 2026-08-07, not read off a type annotation.
Where a task's code depends on one, the task cites it. **If any of these turns out to be false during
implementation, stop and report it — do not adapt around it silently.**

| Fact | Where measured |
| --- | --- |
| For `CreateFileNoClobber("e1", "d/f.txt")` with `d` existing: `paths` is one `ApprovedPath(path='d/f.txt', parent_node=TopologyDirectory(node_id=0), leaf='f.txt')`; `directories` holds `ApprovedExistingDirectory(ProjectRoot())` and `ApprovedExistingDirectory(TopologyDirectory(0))`; `work_base` is `None`. | probe, 2026-08-07 |
| `_parent_paths` for that spec is `{ProjectRoot(): '', PersistentNode('d/f.txt'): 'd/f.txt', TopologyDirectory(0): 'd'}`. **No `ScratchNode` and no `WorkRoot` key.** | probe, 2026-08-07 |
| For `CreateDirectory("e1","d")` + `CreateFileNoClobber("e2","d/f.txt")`: `directories` holds `ApprovedExistingDirectory(ProjectRoot())`, `ApprovedPlannedDirectory(PersistentNode('d'))`, `ApprovedPlannedDirectory(WorkRoot())`; `work_base` is populated; `_parent_paths` is `{ProjectRoot(): '', PersistentNode('d'): 'd', PersistentNode('d/f.txt'): 'd/f.txt'}`. | probe, 2026-08-07 |
| In that spec `ScratchNode('e2', STAGING)` is parented by `PersistentNode('d')` — **a planned directory**. Its slot is unobservable at capture and absent by construction. | probe, 2026-08-07 |
| `open_child_directory(root_fd, name)` raises `ENOTDIR` on a regular file, `ELOOP` on a symlink, `ENOENT` on a missing name. | probe, 2026-08-07 |
| `os.listdir(fd)` works on a directory descriptor; a fresh workspace `staging/` lists `[]`. | probe, 2026-08-07 |
| `backend.flush_file(fd)` succeeds on a **write-only** descriptor. `os.open(name, O_WRONLY\|O_CREAT\|O_EXCL\|O_NOFOLLOW\|O_CLOEXEC, 0o600, dir_fd=staging_fd)` creates mode `0o600`; a second create raises `EEXIST`. | probe, 2026-08-07 |
| `compile_spec` **accepts** one `content_hash` declared at two `byte_len` values, and `referenced_digests` returns both pairs. | probe, 2026-08-07 |
| `referenced_digests` scans only `spec.initial_surface` and `spec.final_surface`. For `ReplaceFile(p, A→B)` + `ReplaceFile(p, B→C)` it returns A and C; **B is absent.** | probe, 2026-08-07; `records.py:412` |
| `connection.py:555` raises `ProtocolError` for any promoted digest outside `referenced_digests`. | `connection.py:547-559` |
| `_preflight` already refuses two `byte_len` for one digest **in one manifest**, and requires `set(os.listdir(staging_fd)) == {entry.name}` exactly. Both fire *after* capture has written the bytes. | `blobs.py:273-285` |
| `verify_leaf(fd, digest, byte_len)` exists in `blobs.py` and is importable by the coordinator. | `blobs.py`, used at `:293` |
| `EntryIdentity()` instances are distinct, `==`-comparable, and hashable. `ObservedSymlink` has **no** `identity` field. | probe, 2026-08-07; `model.py:82-104` |
| `JointObservation(persistent, scratch, parent_occupancy)`; `authorize_recovery_step(plan, step_index, observed)`. | probe, 2026-08-07 |
| `_validate_topology` enforces a **rooted tree**: single parent per node, no parent for `ProjectRoot`, `WorkRoot` parented by `ProjectRoot`, ending on "topology must be an acyclic tree rooted at the project root". | `snapshot.py:276-350` |
| `expected_persistent` is built from **every** timeline, so a declared directory is a `PersistentNode` that can also be a parent. | `snapshot.py:307` |
| `DirectoryConstraints` is `lookup_proof` and `name_max` only — no mount. `read_mount_id(fd)` vs `binding.evidence.mount_id` is a separate check. | `lookup.py:45-47`, `resolve.py:448-453` |
| `resolve._filesystem_type(binding)` is private and is the only route to the type string that also checks the backend is Linux. | `resolve.py:73-81` |
| `ProjectBinding.project_root_fd` and `Workspace.staging_fd`/`work_fd` are public properties returning **borrowed** descriptors. `promote_staging` spends `staging_fd`. | `binding.py:121-124`, `workspace.py:78-93`, `blobs.py:366` |
| `backend.py`'s docstring: "exactly one operation set per design §5.5 capability, and every method is called by the probe that reports it." | `backend.py:1-9` |
| `atoms/core/recovery/__init__.py` re-exports `classify_recovery` and `authorize_recovery_step`, so a blacklist on `snapshot` is insufficient. | `recovery/__init__.py:1-2` |
| A5a's translation rule: "the default is a bare `raise`, so an unrecognized code keeps its own class *and* its traceback." | `store/errors.py:26-46` |
| The test volume resolves to `<repo>/.atoms-test-volume` on ext4 with no env var set. | probe, 2026-08-07 |
| The `leased` fixture patches `root.CERTIFIED_ALLOWLIST` and yields an entered production lease. Test builders live in `tests/coordinator_support.py` as **plain functions**, because the fixture-registry guard requires every fixture to live in `tests/conftest.py`. | `conftest.py:606`, `coordinator_support.py:1-8` |

## Global Constraints

- **Python 3.11+, stdlib only.** No new third-party dependency.
- **Fail early; no silent fallbacks.** Every refusal states what was expected and what was found.
- **Composition over inheritance.**
- **`Backend` gains no method.** Occupancy and the staging sink are `os.listdir` and `os.open`.
- **No project path is mutated.** A6 writes only into `staging/<txid>/`.
- **No new exception type.** `ProtocolError`, `PreconditionRefused`, `CapabilityUnavailable` only.
- **No A6 path raises `TransactionHalted`.**
- **`atoms/fs/observe.py` may import `atoms.core.recovery.model` and no other `core.recovery` module.**
- **Exact-type checks**, not `isinstance`, at trust boundaries — the house pattern (`_require_exact`).
- Docs use `~/d/atoms/...` for filepaths.
- Conventional commits. **No AI-attribution trailer or footer.**
- Gates, all run from `python/`: `uv run pytest`, `uv run ruff format`, `uv run ruff check`,
  `uv run pyright`.

## File Structure

| File | Responsibility |
| --- | --- |
| `python/src/atoms/fs/observe.py` | **Create.** `Observation` (token universe, pinned descriptors, per-kind observation, occupancy, prefix relation) and `translated_lookup`. |
| `python/src/atoms/fs/resolve.py` | **Modify.** `_filesystem_type` → public `filesystem_type_of`. |
| `python/src/atoms/coordinator/descriptors.py` | **Create.** `DescriptorTable`, `WalkStop`, `build_descriptor_table`. |
| `python/src/atoms/coordinator/capture.py` | **Create.** `PayloadSource`, `Captured`, `capture_initial_surface`. |
| `python/src/atoms/coordinator/admission.py` | **Modify.** One new gate site. |
| `python/src/atoms/store/records.py` | **Modify.** Widen `referenced_digests`. |
| `python/tests/test_fs_observe.py` | **Create.** Tier 1 — the observation mechanism. |
| `python/tests/test_coordinator_descriptors.py` | **Create.** Tier 2 — the walk and re-validation. |
| `python/tests/test_coordinator_capture.py` | **Create.** Tier 3 — capture, staging, refusals. |
| `python/tests/test_coordinator_capture_conformance.py` | **Create.** Tier 4 — A3's two routes. |
| `python/tests/capture_support.py` | **Create.** Plain-function builders shared by the capture tiers. |
| `python/tests/test_fs_architecture.py` | **Modify.** The `core.recovery` import whitelist. |
| `python/tests/test_store_records.py` | **Modify.** The widened helper's test. |
| `python/tests/coordinator_child.py` | **Modify.** Its `referenced_digests` call site. |
| `python/tests/test_coordinator_architecture.py` | **Modify.** The A6 status-synchronization test. |

---

## Task 1: `translated_lookup` and the `Observation` resource

**Files:**
- Create: `python/src/atoms/fs/observe.py`
- Create: `python/tests/test_fs_observe.py`

**Interfaces:**
- Consumes: `atoms.fs.backend.Backend`; `atoms.core.recovery.model`'s `ObservedAbsent`,
  `ObservedFile`, `ObservedDirectory`, `ObservedSymlink`, `ObservedEntry`, `EntryIdentity`,
  `OBSERVED_ABSENT`; `atoms.core.fingerprint`'s `FileState`, `DirectoryState`, `SymlinkState`.
- Produces:
  - `translated_lookup(context: str) -> ContextManager[None]`
  - `class Observation:` with `__init__(self, backend: Backend)`, `observe(self, parent_fd: int, leaf: str, *, sink_fd: int | None = None) -> ObservedEntry`, `close(self) -> None`, `__enter__`, `__exit__`.

- [ ] **Step 1: Write the failing test**

Create `python/tests/test_fs_observe.py`:

```python
"""A6 tier 1 -- the coherent observation mechanism (design §6)."""

from __future__ import annotations

import errno
import hashlib
import os

import pytest

from atoms.core.errors import CapabilityUnavailable, PreconditionRefused, ProtocolError
from atoms.core.recovery.model import (
    ObservedAbsent,
    ObservedDirectory,
    ObservedFile,
    ObservedSymlink,
)
from atoms.fs.linux import LinuxBackend
from atoms.fs.observe import Observation, translated_lookup


def digest_of(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@pytest.fixture
def project(tmp_path):
    """A plain directory descriptor. Tier 1 needs no lease, store, or approval."""
    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        yield tmp_path, fd
    finally:
        os.close(fd)


def test_a_regular_file_is_observed_from_one_descriptor(project):
    root, fd = project
    (root / "f.txt").write_bytes(b"payload")
    os.chmod(root / "f.txt", 0o644)

    with Observation(LinuxBackend()) as observation:
        entry = observation.observe(fd, "f.txt")

    assert type(entry) is ObservedFile
    assert entry.state.content_hash == digest_of(b"payload")
    assert entry.state.byte_len == 7
    assert entry.state.mode == 0o644


def test_one_entry_reached_twice_yields_one_token(project):
    root, fd = project
    (root / "f.txt").write_bytes(b"payload")
    os.link(root / "f.txt", root / "same.txt")

    with Observation(LinuxBackend()) as observation:
        first = observation.observe(fd, "f.txt")
        second = observation.observe(fd, "same.txt")

    assert first.identity == second.identity


def test_distinct_entries_yield_distinct_tokens(project):
    root, fd = project
    (root / "a.txt").write_bytes(b"a")
    (root / "b.txt").write_bytes(b"b")

    with Observation(LinuxBackend()) as observation:
        a = observation.observe(fd, "a.txt")
        b = observation.observe(fd, "b.txt")

    assert a.identity != b.identity


def test_each_pass_mints_a_fresh_token_universe(project):
    root, fd = project
    (root / "f.txt").write_bytes(b"payload")

    with Observation(LinuxBackend()) as first_pass:
        first = first_pass.observe(fd, "f.txt")
    with Observation(LinuxBackend()) as second_pass:
        second = second_pass.observe(fd, "f.txt")

    # Same file, two passes. A3's identity equality means "same entry, same pass";
    # a token that survived the pass would let A3 conclude more than was observed.
    assert first.identity != second.identity


def test_a_symlink_carries_a_fingerprint_and_no_identity(project):
    root, fd = project
    os.symlink("target", root / "link")

    with Observation(LinuxBackend()) as observation:
        entry = observation.observe(fd, "link")

    assert type(entry) is ObservedSymlink
    assert entry.state.target == "target"
    # Structural, not conventional: the type has no field for one, so §6's rule that a
    # symlink never carries descriptor identity cannot be violated by later code.
    assert not hasattr(entry, "identity")


def test_a_directory_is_observed_with_its_mode(project):
    root, fd = project
    (root / "sub").mkdir(mode=0o750)

    with Observation(LinuxBackend()) as observation:
        entry = observation.observe(fd, "sub")

    assert type(entry) is ObservedDirectory
    assert entry.state.mode == 0o750


def test_an_absent_name_is_observed_as_absent(project):
    _, fd = project

    with Observation(LinuxBackend()) as observation:
        assert type(observation.observe(fd, "missing")) is ObservedAbsent


def test_a_sink_receives_the_bytes_from_the_same_read(project):
    root, fd = project
    (root / "f.txt").write_bytes(b"payload")
    sink_path = root / "sink"
    sink_fd = os.open(str(sink_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with Observation(LinuxBackend()) as observation:
            entry = observation.observe(fd, "f.txt", sink_fd=sink_fd)
    finally:
        os.close(sink_fd)

    assert sink_path.read_bytes() == b"payload"
    assert entry.state.content_hash == digest_of(b"payload")


def test_the_retained_descriptor_pins_the_inode(project):
    """§11.1: assert the pin, not the reuse.

    Unlinking and recreating does not *force* the kernel to reallocate the inode, so a
    test that asserted distinct tokens after a recreate would pass just as readily with
    no pin at all -- it would be testing the allocator's mood. What is assertable is the
    mechanism: while the pass lives, the observation still holds the entry open, so the
    inode cannot be reallocated and the kernel's own no-live-reuse invariant applies.
    """
    root, fd = project
    (root / "f.txt").write_bytes(b"original")

    with Observation(LinuxBackend()) as observation:
        entry = observation.observe(fd, "f.txt")
        os.unlink(root / "f.txt")
        pinned = observation.pinned_descriptor(entry.identity)
        os.lseek(pinned, 0, os.SEEK_SET)
        assert os.read(pinned, 64) == b"original"


def test_closing_the_pass_releases_every_pinned_descriptor(project):
    root, fd = project
    (root / "f.txt").write_bytes(b"payload")

    observation = Observation(LinuxBackend())
    entry = observation.observe(fd, "f.txt")
    observation.close()

    with pytest.raises(ProtocolError, match="closed"):
        observation.pinned_descriptor(entry.identity)


def test_a_namespace_contradiction_refuses(project):
    with pytest.raises(PreconditionRefused, match="while probing"):
        with translated_lookup("probing"):
            raise OSError(errno.ELOOP, "symlink")


def test_an_unsupported_semantic_is_a_capability_refusal(project):
    with pytest.raises(CapabilityUnavailable):
        with translated_lookup("probing"):
            raise OSError(errno.EOPNOTSUPP, "no")


def test_an_undefined_errno_propagates_as_itself(project):
    """Design §9.1: A5a's rule, applied to errno.

    ENOSPC is not external state contradicting the frozen spec -- it is a full disk.
    Reporting it as PreconditionRefused would tell a consumer its intent had drifted
    when the hardware had failed.
    """
    with pytest.raises(OSError) as caught:
        with translated_lookup("probing"):
            raise OSError(errno.ENOSPC, "full")
    assert caught.value.errno == errno.ENOSPC
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd python && uv run pytest tests/test_fs_observe.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'atoms.fs.observe'`.

- [ ] **Step 3: Write the implementation**

Create `python/src/atoms/fs/observe.py`:

```python
"""The coherent observation mechanism (design §6).

One `Observation` is one pass and owns one token universe. It produces the primitive
facts A3 consumes and never a verdict: this module may import `atoms.core.recovery.model`
and no other `core.recovery` module, which is how ledger #13's "may not pre-classify them
into a recovery outcome" becomes a mechanical property rather than a review promise. An
architecture test asserts the whitelist.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import stat
from collections.abc import Iterator

from atoms.core.errors import CapabilityUnavailable, PreconditionRefused, ProtocolError
from atoms.core.fingerprint import DirectoryState, FileState, SymlinkState
from atoms.core.recovery.model import (
    OBSERVED_ABSENT,
    EntryIdentity,
    ObservedDirectory,
    ObservedEntry,
    ObservedFile,
    ObservedSymlink,
)
from atoms.fs.backend import Backend

_READ_CHUNK = 1 << 20

# Errnos with a defined domain meaning after approval (design §9.1). Everything else
# propagates with its own class and traceback, which is A5a's rule for SQLite result
# codes applied to errno: reads, writes, and flushes raise EIO, ENOSPC, EROFS and more,
# and none of those is external state contradicting the frozen spec.
_NAMESPACE_CONTRADICTIONS = frozenset(
    {errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EXDEV}
)
_UNSUPPORTED = frozenset({errno.ENOSYS, errno.EOPNOTSUPP, errno.ENOTSUP})


@contextlib.contextmanager
def translated_lookup(context: str) -> Iterator[None]:
    """Translate one narrow operation's errno. Wrap a statement, never a protocol."""
    try:
        yield
    except OSError as caught:
        if caught.errno in _NAMESPACE_CONTRADICTIONS:
            raise PreconditionRefused(
                f"the namespace no longer matches approval while {context}: {caught}"
            ) from caught
        if caught.errno in _UNSUPPORTED:
            raise CapabilityUnavailable(
                f"the backend cannot supply the semantics needed while {context}: {caught}"
            ) from caught
        if caught.errno == errno.EBADF:
            raise ProtocolError(
                f"a descriptor was already closed while {context}: {caught}"
            ) from caught
        raise


class Observation:
    """One pass, one token universe, one pinned descriptor per observed identity.

    `(st_dev, st_ino)` identifies an entry uniquely only while its inode stays
    allocated. Recording the key and closing the descriptor would let an unlink and a
    create recycle that inode and map two sequentially distinct entries onto one token --
    an identity equality A3 would believe. The retained descriptor makes the reuse
    impossible rather than unlikely.
    """

    __slots__ = ("_backend", "_closed", "_pins", "_tokens")

    def __init__(self, backend: Backend) -> None:
        self._backend = backend
        self._tokens: dict[tuple[int, int], EntryIdentity] = {}
        self._pins: dict[EntryIdentity, int] = {}
        self._closed = False

    def observe(
        self, parent_fd: int, leaf: str, *, sink_fd: int | None = None
    ) -> ObservedEntry:
        self._require_open()
        _require_leaf(leaf)
        with translated_lookup(f"looking up {leaf!r}"):
            try:
                info = os.lstat(leaf, dir_fd=parent_fd)
            except FileNotFoundError:
                return OBSERVED_ABSENT
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode):
            return self._observe_symlink(parent_fd, leaf)
        if stat.S_ISDIR(info.st_mode):
            return self._observe_directory(parent_fd, leaf, mode)
        if stat.S_ISREG(info.st_mode):
            return self._observe_file(parent_fd, leaf, sink_fd)
        raise PreconditionRefused(
            f"{leaf!r} is neither a regular file, directory, nor symlink "
            f"(st_mode {info.st_mode:#o}); no declared state can describe it"
        )

    def pinned_descriptor(self, identity: EntryIdentity) -> int:
        """The retained descriptor for an observed identity. Borrowed, never closed."""
        self._require_open()
        pinned = self._pins.get(identity)
        if pinned is None:
            raise ProtocolError("that identity was not observed in this pass")
        return pinned

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for fd in self._pins.values():
            os.close(fd)
        self._pins.clear()
        self._tokens.clear()

    def __enter__(self) -> Observation:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _observe_file(
        self, parent_fd: int, leaf: str, sink_fd: int | None
    ) -> ObservedFile:
        with translated_lookup(f"opening {leaf!r}"):
            fd = self._backend.open_regular_nofollow(parent_fd, leaf)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(fd)
            raise PreconditionRefused(
                f"{leaf!r} stopped being a regular file between lookup and open"
            )
        digest = hashlib.sha256()
        length = 0
        while True:
            chunk = os.read(fd, _READ_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            length += len(chunk)
            if sink_fd is not None:
                _write_all(sink_fd, chunk)
        state = FileState(
            content_hash="sha256:" + digest.hexdigest(),
            mode=stat.S_IMODE(info.st_mode),
            byte_len=length,
        )
        return ObservedFile(state=state, identity=self._pin(info, fd))

    def _observe_directory(
        self, parent_fd: int, leaf: str, mode: int
    ) -> ObservedDirectory:
        with translated_lookup(f"opening directory {leaf!r}"):
            fd = self._backend.open_child_directory(parent_fd, leaf)
        info = os.fstat(fd)
        return ObservedDirectory(
            state=DirectoryState(mode=stat.S_IMODE(info.st_mode)),
            identity=self._pin(info, fd),
            # Occupancy needs the modeled child names, which only the caller knows.
            # Task 2 replaces this with `occupancy`; a directory observed without one
            # reports no unmodeled child rather than guessing.
            has_unmodeled_child=False,
        )

    def _observe_symlink(self, parent_fd: int, leaf: str) -> ObservedSymlink:
        with translated_lookup(f"fingerprinting symlink {leaf!r}"):
            info, target = self._backend.symlink_fingerprint(parent_fd, leaf)
        # No descriptor and no identity: O_NOFOLLOW fails by design on a symlink leaf,
        # so there is nothing to be coherent about (design §6.2).
        return ObservedSymlink(
            state=SymlinkState(target=target, mode=stat.S_IMODE(info.st_mode))
        )

    def _pin(self, info: os.stat_result, fd: int) -> EntryIdentity:
        key = (info.st_dev, info.st_ino)
        existing = self._tokens.get(key)
        if existing is not None:
            os.close(fd)
            return existing
        token = EntryIdentity()
        self._tokens[key] = token
        self._pins[token] = fd
        return token

    def _require_open(self) -> None:
        if self._closed:
            raise ProtocolError("this observation pass is closed")


def _require_leaf(leaf: str) -> None:
    if type(leaf) is not str:
        raise ProtocolError(f"a leaf must be exactly str, got {type(leaf).__name__}")
    if not leaf or leaf in (".", "..") or "/" in leaf or "\x00" in leaf:
        raise ProtocolError(f"{leaf!r} is not a single pathname component")


def _write_all(fd: int, chunk: bytes) -> None:
    view = memoryview(chunk)
    while view:
        view = view[os.write(fd, view) :]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd python && uv run pytest tests/test_fs_observe.py -q`
Expected: PASS, 13 tests.

- [ ] **Step 5: Run the full gate set**

Run: `cd python && uv run ruff format && uv run ruff check && uv run pyright && uv run pytest -q`
Expected: all green, no regressions.

- [ ] **Step 6: Commit**

```bash
git add python/src/atoms/fs/observe.py python/tests/test_fs_observe.py
git commit -m "feat(observe): add the coherent observation pass and its token pin"
```

---

## Task 2: Occupancy evidence and the planned-blob prefix relation

**Files:**
- Modify: `python/src/atoms/fs/observe.py`
- Modify: `python/tests/test_fs_observe.py`

**Interfaces:**
- Consumes: Task 1's `Observation`.
- Produces:
  - `Observation.occupancy(self, dir_fd: int, modeled: frozenset[str]) -> bool`
  - `Observation.observe_directory_with_occupancy(self, parent_fd: int, leaf: str, modeled: frozenset[str]) -> ObservedEntry`
  - `Observation.build_relation(self, staged_fd: int, planned_fd: int) -> FileBuildRelation`

- [ ] **Step 1: Write the failing test**

Append to `python/tests/test_fs_observe.py`:

```python
from atoms.core.recovery.model import FileBuildRelation


def test_occupancy_reports_an_unmodeled_child(project):
    root, fd = project
    (root / "sub").mkdir()
    (root / "sub" / "modeled").write_bytes(b"")
    (root / "sub" / "stranger").write_bytes(b"")

    with Observation(LinuxBackend()) as observation:
        entry = observation.observe_directory_with_occupancy(
            fd, "sub", frozenset({"modeled"})
        )

    assert entry.has_unmodeled_child is True


def test_occupancy_reports_none_when_every_child_is_modeled(project):
    root, fd = project
    (root / "sub").mkdir()
    (root / "sub" / "modeled").write_bytes(b"")

    with Observation(LinuxBackend()) as observation:
        entry = observation.observe_directory_with_occupancy(
            fd, "sub", frozenset({"modeled", "not-present-yet"})
        )

    assert entry.has_unmodeled_child is False


def test_an_empty_directory_has_no_unmodeled_child(project):
    root, fd = project
    (root / "sub").mkdir()

    with Observation(LinuxBackend()) as observation:
        entry = observation.observe_directory_with_occupancy(fd, "sub", frozenset())

    assert entry.has_unmodeled_child is False


def _relation(root, staged: bytes, planned: bytes) -> FileBuildRelation:
    (root / "staged").write_bytes(staged)
    (root / "planned").write_bytes(planned)
    staged_fd = os.open(str(root / "staged"), os.O_RDONLY)
    planned_fd = os.open(str(root / "planned"), os.O_RDONLY)
    try:
        with Observation(LinuxBackend()) as observation:
            return observation.build_relation(staged_fd, planned_fd)
    finally:
        os.close(staged_fd)
        os.close(planned_fd)


def test_identical_bytes_are_an_exact_relation(project):
    root, _ = project
    assert _relation(root, b"payload", b"payload") is FileBuildRelation.EXACT


def test_a_truncated_staging_object_is_a_prefix(project):
    root, _ = project
    assert _relation(root, b"pay", b"payload") is FileBuildRelation.PREFIX


def test_differing_bytes_are_diverged(project):
    root, _ = project
    assert _relation(root, b"paZload", b"payload") is FileBuildRelation.DIVERGED


def test_a_staging_object_longer_than_the_plan_is_diverged(project):
    root, _ = project
    assert _relation(root, b"payload+", b"payload") is FileBuildRelation.DIVERGED
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd python && uv run pytest tests/test_fs_observe.py -q -k "occupancy or relation or prefix or diverged or exact"`
Expected: FAIL, `AttributeError: 'Observation' object has no attribute 'occupancy'`.

- [ ] **Step 3: Confirm the `FileBuildRelation` member names before writing code**

Run: `cd python && uv run python -c "from atoms.core.recovery.model import FileBuildRelation as R; print(list(R))"`
Expected: three members. **If the names are not `EXACT`, `PREFIX`, `DIVERGED`, stop and report it** —
the test above and the code below both assume those, and inventing a fourth state would put A6 in the
business of classifying, which ledger #13 forbids.

- [ ] **Step 4: Write the implementation**

Add to `python/src/atoms/fs/observe.py`, inside `Observation`:

```python
    def observe_directory_with_occupancy(
        self, parent_fd: int, leaf: str, modeled: frozenset[str]
    ) -> ObservedEntry:
        """A directory observed with its occupancy evidence in the same pass."""
        entry = self.observe(parent_fd, leaf)
        if type(entry) is not ObservedDirectory:
            return entry
        pinned = self.pinned_descriptor(entry.identity)
        return ObservedDirectory(
            state=entry.state,
            identity=entry.identity,
            has_unmodeled_child=self.occupancy(pinned, modeled),
        )

    def occupancy(self, dir_fd: int, modeled: frozenset[str]) -> bool:
        """One descriptor-relative enumeration, reconciled against the modeled names.

        `os.listdir` on a descriptor omits `.` and `..`, so no filtering is needed --
        measured, not assumed.
        """
        self._require_open()
        with translated_lookup("enumerating a directory"):
            present = os.listdir(dir_fd)
        return any(name not in modeled for name in present)

    def build_relation(self, staged_fd: int, planned_fd: int) -> FileBuildRelation:
        """Compare a staged object with the planned blob (design §6.3).

        The planned blob arrives as an OPEN DESCRIPTOR supplied by the caller, never a
        digest this module resolves: that is both the coherence rule and what keeps
        `atoms.store` out of `atoms/fs/`.
        """
        self._require_open()
        os.lseek(staged_fd, 0, os.SEEK_SET)
        os.lseek(planned_fd, 0, os.SEEK_SET)
        while True:
            staged = os.read(staged_fd, _READ_CHUNK)
            planned = os.read(planned_fd, _READ_CHUNK)
            if not staged and not planned:
                return FileBuildRelation.EXACT
            if not staged:
                return FileBuildRelation.PREFIX
            if not planned:
                return FileBuildRelation.DIVERGED
            shortest = min(len(staged), len(planned))
            if staged[:shortest] != planned[:shortest]:
                return FileBuildRelation.DIVERGED
            if len(staged) < len(planned):
                # A short read is not end-of-file; re-align by seeking the planned side
                # back to where the staged side actually reached.
                os.lseek(planned_fd, shortest - len(planned), os.SEEK_CUR)
            elif len(planned) < len(staged):
                os.lseek(staged_fd, shortest - len(staged), os.SEEK_CUR)
```

Extend the import from `atoms.core.recovery.model` to include `FileBuildRelation`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd python && uv run pytest tests/test_fs_observe.py -q`
Expected: PASS, 21 tests.

- [ ] **Step 6: Commit**

```bash
git add python/src/atoms/fs/observe.py python/tests/test_fs_observe.py
git commit -m "feat(observe): add occupancy evidence and the planned-blob prefix relation"
```

---

## Task 3: `DescriptorTable` — the walk, re-validation, and ownership

**Files:**
- Modify: `python/src/atoms/fs/resolve.py` (rename `_filesystem_type` → `filesystem_type_of`)
- Create: `python/src/atoms/coordinator/descriptors.py`
- Create: `python/tests/capture_support.py`
- Create: `python/tests/test_coordinator_descriptors.py`

**Interfaces:**
- Consumes: `Lease`, `ProjectApprovedSpec`, `Workspace`, `_parent_paths`,
  `read_lookup_constraints`, `read_mount_id`, `filesystem_type_of`.
- Produces:
  - `class WalkStop:` frozen, `node: TopologyNode`, `parent_fd: int`, `component: str`, `blocker: EntryKind | None`
  - `class DescriptorTable:` with `fd_for(self, node: TopologyNode) -> int`, `stops: tuple[WalkStop, ...]`, `close(self)`, `__enter__`, `__exit__`
  - `build_descriptor_table(lease: Lease, approved: ProjectApprovedSpec, workspace: Workspace) -> DescriptorTable`

- [ ] **Step 1: Make the filesystem-type helper public**

In `python/src/atoms/fs/resolve.py`, rename `_filesystem_type` to `filesystem_type_of` and update its
two in-module call sites (`observe_child`, `observe_work_child`).

Run: `cd python && grep -rn "_filesystem_type" src/ tests/`
Expected: no matches remain.

- [ ] **Step 2: Write the failing test**

Create `python/tests/capture_support.py`:

```python
"""Builders shared by the A6 capture tiers.

Plain functions, not fixtures: the fixture-registry guard requires every fixture to live
in `tests/conftest.py`, and these are values a test constructs rather than resources a
test needs torn down. Same rule `tests/coordinator_support.py` follows.
"""

from __future__ import annotations

import hashlib
import os

from atoms.core.compiler import CompiledSpec, compile_spec
from atoms.core.effects import CreateDirectory, CreateFileNoClobber, DeletePath, ReplaceFile
from atoms.core.fingerprint import ABSENT, DirectoryState, FileState
from atoms.core.spec import TransactionSpec, build_spec
from atoms.coordinator.lease import Lease

DIRECTORY_POST = DirectoryState(mode=0o755)


def digest_of(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def state_of(payload: bytes, mode: int = 0o644) -> FileState:
    return FileState(content_hash=digest_of(payload), mode=mode, byte_len=len(payload))


def write_project_file(lease: Lease, path: str, payload: bytes, mode: int = 0o644) -> None:
    """Create a real file in project space, making its parents as needed."""
    root_fd = lease._binding.project_root_fd
    parts = path.split("/")
    for index in range(1, len(parts)):
        try:
            os.mkdir("/".join(parts[:index]), dir_fd=root_fd)
        except FileExistsError:
            pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode, dir_fd=root_fd)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    os.chmod(path, mode, dir_fd=root_fd)


BEFORE = b"before"
AFTER = b"after"


def replace_spec() -> TransactionSpec:
    """One `ReplaceFile` under an existing directory `d`.

    The preimage is real and must be captured; the postimage must be supplied.
    """
    pre, post = state_of(BEFORE), state_of(AFTER)
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "a" * 64,
        initial_surface={"d/f.txt": pre},
        final_surface={"d/f.txt": post},
        effects=[ReplaceFile(effect_id="e1", path="d/f.txt", pre=pre, post=post)],
    )


def compiled_replace(lease: Lease) -> CompiledSpec:
    write_project_file(lease, "d/f.txt", BEFORE)
    return compile_spec(replace_spec())


def approved_replace(lease: Lease):
    from atoms.coordinator.admission import admit

    return admit(lease, compiled_replace(lease))
```

Create `python/tests/test_coordinator_descriptors.py`:

```python
"""A6 tier 2 -- the descriptor table (design §5)."""

from __future__ import annotations

import os

import pytest

from atoms.core.errors import PreconditionRefused
from atoms.core.recovery.model import ProjectRoot, WorkRoot
from atoms.core.recovery.snapshot import TopologyDirectory
from tests.capture_support import approved_replace
from tests.coordinator_support import admission_for, compiled_creating_a_directory


def test_the_table_holds_one_descriptor_per_approved_directory(leased):
    from atoms.coordinator.descriptors import build_descriptor_table
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            with build_descriptor_table(lease, approved, workspace) as table:
                # Measured: this spec's directories are ProjectRoot and
                # TopologyDirectory(0) for `d`.
                assert isinstance(table.fd_for(ProjectRoot()), int)
                assert isinstance(table.fd_for(TopologyDirectory(node_id=0)), int)


def test_the_root_descriptors_are_borrowed_and_survive_close(leased):
    """§5.5: ProjectBinding and Workspace own theirs; the table closes only its own."""
    from atoms.coordinator.descriptors import build_descriptor_table
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            with build_descriptor_table(lease, approved, workspace) as table:
                assert table.fd_for(ProjectRoot()) == lease._binding.project_root_fd
            # Still usable: closing the table must not have closed the binding's fd.
            assert os.fstat(lease._binding.project_root_fd).st_ino > 0


def test_the_work_root_is_present_only_when_the_topology_has_one(leased):
    from atoms.coordinator.admission import admit
    from atoms.coordinator.descriptors import build_descriptor_table
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        without = approved_replace(lease)
        with open_workspace(lease, without) as workspace:
            with build_descriptor_table(lease, without, workspace) as table:
                assert without.work_base is None
                with pytest.raises(KeyError):
                    table.fd_for(WorkRoot())

    with leased() as lease:
        with_work = admit(lease, compiled_creating_a_directory(lease))
        with open_workspace(lease, with_work) as workspace:
            with build_descriptor_table(lease, with_work, workspace) as table:
                assert with_work.work_base is not None
                assert table.fd_for(WorkRoot()) == workspace.work_fd


def test_a_replaced_directory_refuses_on_identity(leased):
    """Ledger #19: compare against the approved baseline, never reapprove."""
    from atoms.coordinator.descriptors import build_descriptor_table
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        root_fd = lease._binding.project_root_fd
        os.rename("d", "d-moved", src_dir_fd=root_fd, dst_dir_fd=root_fd)
        os.mkdir("d", dir_fd=root_fd)

        with open_workspace(lease, approved) as workspace:
            with pytest.raises(PreconditionRefused, match="identity"):
                build_descriptor_table(lease, approved, workspace)


def test_the_walk_stops_at_a_planned_directory(leased):
    from atoms.coordinator.admission import admit
    from atoms.coordinator.descriptors import build_descriptor_table
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = admit(lease, compiled_creating_a_directory(lease))
        with open_workspace(lease, approved) as workspace:
            with build_descriptor_table(lease, approved, workspace) as table:
                stopped = {stop.component for stop in table.stops}
                assert stopped == {"d"}
                assert all(stop.blocker is None for stop in table.stops)


def test_a_non_directory_blocker_is_reported_not_raised(leased):
    """§8.2: the walk reports the blocker; capture adjudicates it against the timeline."""
    from atoms.coordinator.admission import admit
    from atoms.coordinator.descriptors import build_descriptor_table
    from atoms.coordinator.prepare import open_workspace
    from atoms.fs.resolve import EntryKind

    with leased() as lease:
        approved = admit(lease, compiled_creating_a_directory(lease))
        root_fd = lease._binding.project_root_fd
        fd = os.open("d", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=root_fd)
        os.close(fd)

        with open_workspace(lease, approved) as workspace:
            with build_descriptor_table(lease, approved, workspace) as table:
                (stop,) = [s for s in table.stops if s.component == "d"]
                assert stop.blocker in (EntryKind.REGULAR_FILE, EntryKind.OTHER)


def test_closing_the_table_releases_only_what_it_opened(leased):
    from atoms.coordinator.descriptors import build_descriptor_table
    from atoms.coordinator.prepare import open_workspace
    from tests.fs_support import descriptor_count

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            before = descriptor_count()
            table = build_descriptor_table(lease, approved, workspace)
            assert descriptor_count() > before
            table.close()
            assert descriptor_count() == before
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd python && uv run pytest tests/test_coordinator_descriptors.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'atoms.coordinator.descriptors'`.

- [ ] **Step 4: Write the implementation**

Create `python/src/atoms/coordinator/descriptors.py`:

```python
"""The held, re-validated descriptor table (design §5).

`RecoveryTopology.parents` is a rooted tree, so the table is a walk of it: each directory
node is opened from its parent's held descriptor with ONE `open_child_directory` call,
which is already RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_XDEV. No
multi-component path is ever assembled -- passing one to a syscall would reopen the
check/use race, because the kernel re-resolves intermediate components at the syscall.

The tree gives structure, not spelling: `TopologyDirectory(node_id)` carries no name, so
the walk reads its components from A5b's `_parent_paths`.
"""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass

from atoms.core.errors import PreconditionRefused, ProtocolError
from atoms.core.recovery.snapshot import (
    ProjectRoot,
    TopologyNode,
    WorkRoot,
)
from atoms.coordinator.admission import _parent_paths, _require_admitted
from atoms.coordinator.lease import Lease
from atoms.fs.approval import ProjectApprovedSpec
from atoms.fs.lookup import read_lookup_constraints
from atoms.fs.observe import translated_lookup
from atoms.fs.resolve import EntryKind, FilesystemIdentity, filesystem_type_of
from atoms.fs.topology import ApprovedExistingDirectory, ApprovedPlannedDirectory
from atoms.fs.volume import read_mount_id
from atoms.store.workspace import Workspace

# Measured: open_child_directory raises ENOTDIR on a regular file and ELOOP on a symlink.
_BLOCKER_KINDS = {
    errno.ENOTDIR: EntryKind.REGULAR_FILE,
    errno.ELOOP: EntryKind.SYMLINK,
}


@dataclass(frozen=True, slots=True)
class WalkStop:
    """Where the walk stopped, and why.

    `blocker is None` means the node is a planned directory that does not exist yet.
    A blocker kind means something occupies the name. Neither is adjudicated here:
    capture verifies both against the timeline's first declared state (design §8).
    """

    node: TopologyNode
    parent_fd: int
    component: str
    blocker: EntryKind | None


class DescriptorTable:
    """A live resource. Borrows the roots; owns only what it opened.

    It outlives capture: §6 requires the engine to hold a descriptor to the project root
    for the transaction's lifetime, and §9.5 hands each published directory's descriptor
    down to its descendants. A table that died at capture's return would force A7 to
    re-resolve, reopening the race the whole section exists to close.
    """

    __slots__ = ("_borrowed", "_closed", "_fds", "_owned", "stops")

    def __init__(
        self,
        *,
        fds: dict[TopologyNode, int],
        owned: tuple[int, ...],
        stops: tuple[WalkStop, ...],
    ) -> None:
        self._fds = fds
        self._owned = owned
        self.stops = stops
        self._closed = False

    def fd_for(self, node: TopologyNode) -> int:
        if self._closed:
            raise ProtocolError("this descriptor table is closed")
        return self._fds[node]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for fd in self._owned:
            os.close(fd)
        self._fds.clear()

    def __enter__(self) -> DescriptorTable:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def build_descriptor_table(
    lease: Lease, approved: ProjectApprovedSpec, workspace: Workspace
) -> DescriptorTable:
    _require_admitted(lease, approved)
    if workspace.txid != approved.txid:
        raise ProtocolError(
            f"workspace txid {workspace.txid!r} does not match the proof's "
            f"{approved.txid!r}"
        )

    binding = lease._binding
    filesystem_type = filesystem_type_of(binding)
    expected_mount = binding.evidence.mount_id
    paths = _parent_paths(approved)
    planned = {
        entry.node
        for entry in approved.directories
        if type(entry) is ApprovedPlannedDirectory
    }
    existing = {
        entry.node: entry
        for entry in approved.directories
        if type(entry) is ApprovedExistingDirectory
    }

    fds: dict[TopologyNode, int] = {}
    owned: list[int] = []
    stops: list[WalkStop] = []

    def validate(fd: int, node: TopologyNode) -> None:
        """Ledger #19: identity, constraints, and mount, against the approved baseline.

        DirectoryConstraints carries lookup_proof and name_max only, so mount membership
        is a separate read -- a constraints comparison alone would pass a directory
        replaced by a bind mount.
        """
        constraints = read_lookup_constraints(fd, filesystem_type)
        mount = read_mount_id(fd)
        if mount != expected_mount:
            raise PreconditionRefused(
                f"{node!r} is on mount {mount}, not the bound volume's {expected_mount}"
            )
        baseline = existing.get(node)
        if baseline is not None:
            info = os.fstat(fd)
            actual = FilesystemIdentity(device=info.st_dev, inode=info.st_ino)
            if actual != baseline.identity:
                raise PreconditionRefused(
                    f"{node!r} has identity {actual}, not the approved "
                    f"{baseline.identity}; approval is not reapproved here"
                )
            if constraints != baseline.constraints:
                raise PreconditionRefused(
                    f"{node!r} has constraints {constraints}, not the approved "
                    f"{baseline.constraints}"
                )
            return
        # WorkRoot has no approved identity -- A4b retained facts about metadata_root/work
        # as ApprovedWorkBase, not as an ApprovedExistingDirectory. Constraints and mount
        # are what there is to check.
        if approved.work_base is not None and node == WorkRoot():
            if constraints != approved.work_base.constraints:
                raise PreconditionRefused(
                    f"the work root has constraints {constraints}, not the approved "
                    f"{approved.work_base.constraints}"
                )

    try:
        # Root 1: the project root, borrowed. Retention is not discharge -- lookup_proof
        # and name_max are mutable directory properties, so it is re-validated too.
        root_fd = binding.project_root_fd
        validate(root_fd, ProjectRoot())
        fds[ProjectRoot()] = root_fd

        # Root 2: the work root, borrowed, and present only when the topology has one.
        # The logical WorkRoot -> ProjectRoot edge is NOT physically traversed: the work
        # root lives under metadata_root, not beneath the project root.
        if approved.work_base is not None:
            validate(workspace.work_fd, WorkRoot())
            fds[WorkRoot()] = workspace.work_fd

        for node in _walk_order(approved, paths):
            parent = _parent_of(approved, node)
            parent_fd = fds.get(parent)
            if parent_fd is None:
                # An ancestor already stopped; this node is unreachable by construction.
                continue
            component = _component(paths, parent, node)
            if node in planned:
                stops.append(
                    WalkStop(
                        node=node, parent_fd=parent_fd, component=component, blocker=None
                    )
                )
                continue
            try:
                fd = binding.backend.open_child_directory(parent_fd, component)
            except OSError as caught:
                blocker = _BLOCKER_KINDS.get(caught.errno)
                if blocker is None:
                    with translated_lookup(f"opening {component!r}"):
                        raise
                stops.append(
                    WalkStop(
                        node=node,
                        parent_fd=parent_fd,
                        component=component,
                        blocker=blocker,
                    )
                )
                continue
            owned.append(fd)
            validate(fd, node)
            fds[node] = fd
    except BaseException:
        for fd in owned:
            os.close(fd)
        raise

    return DescriptorTable(fds=fds, owned=tuple(owned), stops=tuple(stops))


def _walk_order(
    approved: ProjectApprovedSpec, paths: dict[TopologyNode, str]
) -> tuple[TopologyNode, ...]:
    """Directory nodes, shallowest first, so every parent is open before its child."""
    directories = [
        entry.node
        for entry in approved.directories
        if entry.node not in (ProjectRoot(), WorkRoot())
    ]
    return tuple(sorted(directories, key=lambda node: paths[node].count("/")))


def _parent_of(approved: ProjectApprovedSpec, node: TopologyNode) -> TopologyNode:
    for edge in approved.topology.parents:
        if edge.node == node:
            return edge.parent
    raise ProtocolError(f"{node!r} has no parent edge in the approved topology")


def _component(
    paths: dict[TopologyNode, str], parent: TopologyNode, node: TopologyNode
) -> str:
    child_path, parent_path = paths[node], paths[parent]
    remainder = child_path[len(parent_path) :].lstrip("/")
    if not remainder or "/" in remainder:
        raise ProtocolError(
            f"{child_path!r} is not one component below {parent_path!r}; the walk "
            "would have to assemble a multi-component path"
        )
    return remainder
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd python && uv run pytest tests/test_coordinator_descriptors.py -q`
Expected: PASS, 7 tests.

- [ ] **Step 6: Run the full gate set**

Run: `cd python && uv run ruff format && uv run ruff check && uv run pyright && uv run pytest -q`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add python/src/atoms/fs/resolve.py python/src/atoms/coordinator/descriptors.py \
        python/tests/capture_support.py python/tests/test_coordinator_descriptors.py
git commit -m "feat(coordinator): hold a re-validated descriptor table across the walk"
```

---

## Task 4: The `referenced_digests` repair

**Files:**
- Modify: `python/src/atoms/store/records.py:412`
- Modify: `python/tests/test_store_records.py:239`
- Modify: `python/tests/coordinator_child.py:48`

**Interfaces:**
- Produces: `referenced_digests(spec: TransactionSpec) -> tuple[tuple[str, int], ...]` covering
  every `FileState` the spec states — both surfaces **and** every effect occurrence — still as pairs.

This task comes before capture because Task 6 stages "every distinct required digest", and that set is
exactly what this helper returns.

- [ ] **Step 1: Write the failing test**

In `python/tests/test_store_records.py`, rename
`test_referenced_digests_include_initial_and_final_file_surfaces` to
`test_referenced_digests_include_every_declared_file_state` and add:

```python
def test_referenced_digests_include_an_intermediate_postimage():
    """Measured: `ReplaceFile(p, A->B)` + `ReplaceFile(p, B->C)` puts B in neither surface.

    The initial surface has A and the final has C. Before this repair the helper missed
    B entirely, so `connection.py`'s barrier refused a promoted B as unreferenced -- and
    not promoting it would leave A7 without the bytes it must publish.
    """
    import hashlib

    from atoms.core.effects import ReplaceFile
    from atoms.core.fingerprint import FileState
    from atoms.core.spec import build_spec

    def state(payload: bytes) -> FileState:
        return FileState(
            content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
            mode=0o644,
            byte_len=len(payload),
        )

    a, b, c = state(b"aaa"), state(b"bbb"), state(b"ccc")
    spec = build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "3" * 64,
        initial_surface={"p": a},
        final_surface={"p": c},
        effects=[
            ReplaceFile(effect_id="e1", path="p", pre=a, post=b),
            ReplaceFile(effect_id="e2", path="p", pre=b, post=c),
        ],
    )

    digests = {digest for digest, _ in referenced_digests(spec)}
    assert digests == {a.content_hash, b.content_hash, c.content_hash}


def test_referenced_digests_keep_conflicting_lengths_as_distinct_pairs():
    """Measured: `compile_spec` accepts one content_hash at two byte_len values.

    Collapsing to digests would erase the contradiction capture must detect (design
    §7.2), and would do so in the one helper positioned to see every declared FileState.
    """
    import hashlib

    from atoms.core.effects import ReplaceFile
    from atoms.core.fingerprint import FileState
    from atoms.core.spec import build_spec

    payload = b"aaa"
    honest = FileState(
        content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
        mode=0o644,
        byte_len=3,
    )
    liar = FileState(content_hash=honest.content_hash, mode=0o644, byte_len=99)
    other = FileState(
        content_hash="sha256:" + hashlib.sha256(b"zzz").hexdigest(),
        mode=0o644,
        byte_len=3,
    )
    spec = build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "5" * 64,
        initial_surface={"p": other, "q": other},
        final_surface={"p": honest, "q": liar},
        effects=[
            ReplaceFile(effect_id="e1", path="p", pre=other, post=honest),
            ReplaceFile(effect_id="e2", path="q", pre=other, post=liar),
        ],
    )

    lengths = sorted(n for d, n in referenced_digests(spec) if d == honest.content_hash)
    assert lengths == [3, 99]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd python && uv run pytest tests/test_store_records.py -q -k referenced_digests`
Expected: `test_referenced_digests_include_an_intermediate_postimage` FAILS — B is missing. The
conflicting-lengths test already passes; it is a regression guard for the widening.

- [ ] **Step 3: Write the implementation**

Replace `referenced_digests` in `python/src/atoms/store/records.py`:

```python
def referenced_digests(spec: TransactionSpec) -> tuple[tuple[str, int], ...]:
    """Every FileState the spec states, as (digest, byte_len) pairs.

    Both surfaces AND every effect occurrence. An intermediate postimage -- the B of
    `ReplaceFile(p, A->B)` followed by `ReplaceFile(p, B->C)` -- appears in neither
    surface, so a surfaces-only scan made `connection.py`'s barrier refuse a promoted B
    as unreferenced while A7 still needed the bytes.

    Pairs, not digests: `compile_spec` validates each byte_len's range and the empty-hash
    correspondence but never cross-checks that one content_hash carries one byte_len, so
    a spec may declare the same digest at two lengths. Collapsing here would erase the
    contradiction capture refuses on (A6 design §7.2).
    """
    states = [
        entry.state
        for surface in (spec.initial_surface, spec.final_surface)
        for entry in surface
    ]
    for effect in spec.effects:
        for occurrence in occurrences_of(effect):
            states.extend((occurrence.pre, occurrence.post))
    return tuple(sorted({
        (state.content_hash, state.byte_len)
        for state in states
        if isinstance(state, FileState)
    }))
```

Import `occurrences_of` from `atoms.core.effects` at the top of the module.

- [ ] **Step 4: Confirm the occurrence helper's name**

Run: `cd python && uv run python -c "import atoms.core.effects as e; print([n for n in dir(e) if 'occur' in n.lower()])"`
Expected: the exported helper that expands one effect into its `Occurrence` values. **If it is named
something other than `occurrences_of`, use the real name** and correct the import above.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd python && uv run pytest tests/test_store_records.py -q`
Expected: PASS.

- [ ] **Step 6: Fix the downstream call site and run the full suite**

`python/tests/coordinator_child.py:48` iterates `referenced_digests(active.spec)`. It needs no change
if it already unpacks pairs; confirm and adjust only if it does not.

Run: `cd python && uv run pytest -q`
Expected: all green. **If a store or coordinator test now fails because more digests are referenced,
stop and report it** — that would mean the barrier was relying on the narrow set.

- [ ] **Step 7: Commit**

```bash
git add python/src/atoms/store/records.py python/tests/test_store_records.py \
        python/tests/coordinator_child.py
git commit -m "fix(store): reference every declared FileState, not only the two surfaces"
```

---

## Task 5: Absence inference — the two branches

**Files:**
- Create: `python/src/atoms/coordinator/capture.py` (the inference half only)
- Create: `python/tests/test_coordinator_capture.py`

**Interfaces:**
- Consumes: Task 3's `DescriptorTable` and `WalkStop`; Task 1's `Observation`.
- Produces: `verify_absence_below(observation: Observation, table: DescriptorTable, approved: ProjectApprovedSpec) -> None` — refuses if any stop cannot justify the absence of what lies below it.

- [ ] **Step 1: Write the failing test**

Create `python/tests/test_coordinator_capture.py`:

```python
"""A6 tier 3 -- capture: absence inference, staging, and refusals (design §7, §8)."""

from __future__ import annotations

import os

import pytest

from atoms.core.errors import PreconditionRefused
from tests.capture_support import approved_replace, state_of


def test_a_missing_ancestor_justifies_its_descendants_absence(leased):
    """§8.1: the walk stops at the planned directory; the first missing component is
    confirmed by a no-follow lookup relative to the deepest held descriptor."""
    from atoms.coordinator.admission import admit
    from atoms.coordinator.capture import verify_absence_below
    from atoms.coordinator.descriptors import build_descriptor_table
    from atoms.coordinator.prepare import open_workspace
    from atoms.fs.observe import Observation
    from tests.coordinator_support import compiled_creating_a_directory

    with leased() as lease:
        approved = admit(lease, compiled_creating_a_directory(lease))
        with open_workspace(lease, approved) as workspace:
            with build_descriptor_table(lease, approved, workspace) as table:
                with Observation(lease._binding.backend) as observation:
                    verify_absence_below(observation, table, approved)


def test_an_occupied_planned_name_refuses(leased):
    from atoms.coordinator.admission import admit
    from atoms.coordinator.capture import verify_absence_below
    from atoms.coordinator.descriptors import build_descriptor_table
    from atoms.coordinator.prepare import open_workspace
    from atoms.fs.observe import Observation
    from tests.coordinator_support import compiled_creating_a_directory

    with leased() as lease:
        approved = admit(lease, compiled_creating_a_directory(lease))
        os.mkdir("d", dir_fd=lease._binding.project_root_fd)
        with open_workspace(lease, approved) as workspace:
            with pytest.raises(PreconditionRefused):
                with build_descriptor_table(lease, approved, workspace) as table:
                    with Observation(lease._binding.backend) as observation:
                        verify_absence_below(observation, table, approved)
```

The remaining §8.2 cases need a spec whose timeline is `FILE → ABSENT → DIRECTORY`. Add to
`python/tests/capture_support.py`:

```python
def blocker_spec(pre) -> TransactionSpec:
    """`DeletePath("p")`, `CreateDirectory("p")`, `CreateFileNoClobber("p/q")`.

    `p`'s timeline is continuous (FILE -> ABSENT -> DIRECTORY), and `p/q` is absent
    precisely BECAUSE `p` is a file -- the shape design §8.2 exists for. `pre` is either
    a FileState or a SymlinkState, which selects the branch under test.
    """
    post = state_of(AFTER)
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "b" * 64,
        initial_surface={"p": pre, "p/q": ABSENT},
        final_surface={"p": DIRECTORY_POST, "p/q": post},
        effects=[
            DeletePath(effect_id="e1", path="p", pre=pre),
            CreateDirectory(effect_id="e2", path="p", post=DIRECTORY_POST),
            CreateFileNoClobber(effect_id="e3", path="p/q", post=post),
        ],
    )
```

Then add to `python/tests/test_coordinator_capture.py`:

```python
def _blocked(lease, pre):
    from atoms.coordinator.admission import admit
    from atoms.core.compiler import compile_spec
    from tests.capture_support import blocker_spec

    return admit(lease, compile_spec(blocker_spec(pre)))


def test_a_regular_file_blocker_matching_its_declared_state_justifies_absence(leased):
    from atoms.coordinator.capture import verify_absence_below
    from atoms.coordinator.descriptors import build_descriptor_table
    from atoms.coordinator.prepare import open_workspace
    from atoms.fs.observe import Observation
    from tests.capture_support import BEFORE, write_project_file

    with leased() as lease:
        write_project_file(lease, "p", BEFORE)
        approved = _blocked(lease, state_of(BEFORE))
        with open_workspace(lease, approved) as workspace:
            with build_descriptor_table(lease, approved, workspace) as table:
                with Observation(lease._binding.backend) as observation:
                    verify_absence_below(observation, table, approved)


def test_a_blocker_that_is_not_the_declared_file_refuses(leased):
    """§8.2: ENOTDIR does not distinguish a regular file from a socket or FIFO.

    A4b rejects OTHER at approval, but capture-time drift introduces one here. The
    branch is selected by the declared FileState, never by the errno.
    """
    from atoms.coordinator.capture import verify_absence_below
    from atoms.coordinator.descriptors import build_descriptor_table
    from atoms.coordinator.prepare import open_workspace
    from atoms.fs.observe import Observation
    from tests.capture_support import BEFORE, write_project_file

    with leased() as lease:
        write_project_file(lease, "p", BEFORE)
        approved = _blocked(lease, state_of(BEFORE))
        root_fd = lease._binding.project_root_fd
        os.unlink("p", dir_fd=root_fd)
        os.mkfifo("p", 0o644, dir_fd=root_fd)

        with open_workspace(lease, approved) as workspace:
            with pytest.raises(PreconditionRefused, match="declared"):
                with build_descriptor_table(lease, approved, workspace) as table:
                    with Observation(lease._binding.backend) as observation:
                        verify_absence_below(observation, table, approved)


def test_a_symlink_blocker_must_match_its_declared_symlink_state(leased):
    """A drifted symlink is a symlink but not THIS symlink."""
    from atoms.core.fingerprint import SymlinkState
    from atoms.coordinator.capture import verify_absence_below
    from atoms.coordinator.descriptors import build_descriptor_table
    from atoms.coordinator.prepare import open_workspace
    from atoms.fs.observe import Observation

    with leased() as lease:
        root_fd = lease._binding.project_root_fd
        os.symlink("elsewhere", "p", dir_fd=root_fd)
        declared = SymlinkState(target="elsewhere", mode=0o777)
        approved = _blocked(lease, declared)

        os.unlink("p", dir_fd=root_fd)
        os.symlink("drifted", "p", dir_fd=root_fd)

        with open_workspace(lease, approved) as workspace:
            with pytest.raises(PreconditionRefused, match="declared"):
                with build_descriptor_table(lease, approved, workspace) as table:
                    with Observation(lease._binding.backend) as observation:
                        verify_absence_below(observation, table, approved)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd python && uv run pytest tests/test_coordinator_capture.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'atoms.coordinator.capture'`.

- [ ] **Step 3: Write the implementation**

Create `python/src/atoms/coordinator/capture.py` with the inference half:

```python
"""Authority §7.3 step 1 -- coherent capture (design §7, §8)."""

from __future__ import annotations

import os
import stat

from atoms.core.errors import PreconditionRefused
from atoms.core.fingerprint import FileState, SymlinkState
from atoms.core.recovery.model import ObservedFile, ObservedSymlink
from atoms.coordinator.descriptors import DescriptorTable, WalkStop
from atoms.fs.approval import ProjectApprovedSpec
from atoms.fs.observe import Observation, translated_lookup
from atoms.fs.resolve import EntryKind


def verify_absence_below(
    observation: Observation, table: DescriptorTable, approved: ProjectApprovedSpec
) -> None:
    """Justify the absence of everything beneath each stop (design §8).

    Two routes, two code paths. Conflating them would silently grant a symlink the file
    branch's descriptor coherence.
    """
    declared = _first_states(approved)
    for stop in table.stops:
        if stop.blocker is None:
            _confirm_missing(observation, stop)
        else:
            _confirm_blocker(observation, stop, declared)


def _confirm_missing(observation: Observation, stop: WalkStop) -> None:
    """§8.1: a no-follow lookup of the first missing component.

    The deepest existing ancestor is the last node the walk opened before stopping, and
    the table already holds its descriptor.
    """
    entry = observation.observe(stop.parent_fd, stop.component)
    if type(entry).__name__ != "ObservedAbsent":
        raise PreconditionRefused(
            f"{stop.component!r} was approved as a planned directory but something "
            f"occupies it now: {entry!r}"
        )


def _confirm_blocker(
    observation: Observation, stop: WalkStop, declared: dict[str, object]
) -> None:
    """§8.2: absence is INFERRED from the ancestor's verified state, not probed.

    Traversal failed at the blocker itself, so there is no descriptor against which the
    descendant could be looked up. Neither a regular file nor a symlink can hold
    directory entries, so an ancestor verified to be either one proves nothing exists
    beneath it -- but the two verifications differ in strength and must not be conflated.
    """
    expected = declared.get(_path_of(stop))
    entry = observation.observe(stop.parent_fd, stop.component)

    if type(expected) is FileState:
        # Descriptor-coherent: type, mode, and hash all from one open descriptor. The
        # inference is STRONGER than the negative lookup it replaces.
        if type(entry) is not ObservedFile or entry.state != expected:
            raise PreconditionRefused(
                f"{stop.component!r} blocks traversal but is not the declared "
                f"{expected!r}; observed {entry!r}"
            )
        return

    if type(expected) is SymlinkState:
        # NOT descriptor-coherent: lstat + readlink, no descriptor, no identity. The
        # absence inference holds because a symlink holds no entries; the identity
        # contract is deferred to A7's destructive transfer.
        if type(entry) is not ObservedSymlink or entry.state != expected:
            raise PreconditionRefused(
                f"{stop.component!r} blocks traversal but is not the declared "
                f"{expected!r}; observed {entry!r}"
            )
        return

    raise PreconditionRefused(
        f"{stop.component!r} blocks traversal ({stop.blocker}) and no declared file or "
        "symlink state describes it"
    )


def _path_of(stop: WalkStop) -> str:
    node = stop.node
    return getattr(node, "path", stop.component)


def _first_states(approved: ProjectApprovedSpec) -> dict[str, object]:
    """Each path's FIRST precondition -- the declared initial surface.

    Later occurrence-local preconditions describe intermediate states no initial capture
    can observe, and checking them here would refuse correct transactions.
    """
    return {
        timeline.path: timeline.occurrences[0].pre
        for timeline in approved.compiled.timelines
    }
```

- [ ] **Step 4: Confirm the timeline field names**

Run: `cd python && uv run python -c "
import dataclasses as d
from atoms.core.timeline import PathTimeline, TimelineOccurrence
print([f.name for f in d.fields(PathTimeline)])
print([f.name for f in d.fields(TimelineOccurrence)])"`
Expected: `['path', 'occurrences']` and a field holding the precondition. **If the precondition field
is not named `pre`, correct `_first_states` to the real name.**

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd python && uv run pytest tests/test_coordinator_capture.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 6: Commit**

```bash
git add python/src/atoms/coordinator/capture.py python/tests/capture_support.py \
        python/tests/test_coordinator_capture.py
git commit -m "feat(capture): infer descendant absence from a verified ancestor"
```

---

## Task 6: `PayloadSource`, the digest-length precheck, and preimage staging

**Files:**
- Modify: `python/src/atoms/coordinator/capture.py`
- Modify: `python/tests/test_coordinator_capture.py`
- Modify: `python/tests/capture_support.py`

**Interfaces:**
- Produces:
  - `class PayloadSource(Protocol):` with `open(self, digest: str) -> IO[bytes]`
  - `require_one_length_per_digest(pairs: tuple[tuple[str, int], ...]) -> dict[str, int]`
  - `stage_preimages(...) -> list[StagedBlob]` (module-private; exercised through Task 7's entry point)

- [ ] **Step 1: Write the failing test**

Add to `python/tests/capture_support.py`:

```python
class DictPayloads:
    """A PayloadSource over an in-memory map.

    `open` returns a FRESH stream each call, which capture owns and closes. A source that
    handed back a shared or already-read stream would make a second staging attempt
    silently produce a short blob.
    """

    def __init__(self, contents: dict[str, bytes]) -> None:
        self._contents = contents
        self.requested: list[str] = []

    def open(self, digest: str):
        import io

        self.requested.append(digest)
        payload = self._contents.get(digest)
        if payload is None:
            raise KeyError(digest)
        return io.BytesIO(payload)
```

Add to `python/tests/test_coordinator_capture.py`:

```python
def test_one_digest_at_two_lengths_refuses_before_anything_is_written():
    """Design §7.2. Measured: `compile_spec` accepts the contradiction.

    The staging name is `digest_to_leaf(digest)` -- one name per digest -- so two lengths
    would collide and whichever wrote second would publish a blob one effect's FileState
    disagrees with. `_preflight` catches it too, but only after both files exist.
    """
    from atoms.core.errors import ProtocolError
    from atoms.coordinator.capture import require_one_length_per_digest

    with pytest.raises(ProtocolError, match="two byte_len"):
        require_one_length_per_digest((("sha256:" + "a" * 64, 3), ("sha256:" + "a" * 64, 99)))


def test_one_length_per_digest_passes_through():
    from atoms.coordinator.capture import require_one_length_per_digest

    pairs = (("sha256:" + "a" * 64, 3), ("sha256:" + "b" * 64, 99))
    assert require_one_length_per_digest(pairs) == {
        "sha256:" + "a" * 64: 3,
        "sha256:" + "b" * 64: 99,
    }


def test_a_preimage_is_staged_and_verified_from_one_read(leased):
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace
    from atoms.store.blobs import digest_to_leaf
    from tests.capture_support import AFTER, BEFORE, DictPayloads, digest_of

    with leased() as lease:
        approved = approved_replace(lease)
        payloads = DictPayloads({digest_of(AFTER): AFTER})
        with open_workspace(lease, approved) as workspace:
            with capture_initial_surface(lease, approved, workspace, payloads) as captured:
                names = {entry.name for entry in captured.manifest}
                assert digest_to_leaf(digest_of(BEFORE)) in names
                assert set(os.listdir(workspace.staging_fd)) == names


def test_a_drifted_preimage_refuses(leased):
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace
    from tests.capture_support import AFTER, DictPayloads, digest_of, write_project_file

    with leased() as lease:
        approved = approved_replace(lease)
        write_project_file(lease, "d/f.txt", b"drifted")
        payloads = DictPayloads({digest_of(AFTER): AFTER})
        with open_workspace(lease, approved) as workspace:
            with pytest.raises(PreconditionRefused):
                with capture_initial_surface(lease, approved, workspace, payloads):
                    pass


def test_a_symlink_preimage_is_verified_and_not_staged(leased):
    """§7 step 3: a symlink retains no content.

    `referenced_digests` filters on FileState and never names one, and §10's rollback
    material for a symlink is the atomically transferred tombstone, which only A7 creates.
    """
    from atoms.core.fingerprint import SymlinkState
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace
    from tests.capture_support import DictPayloads, delete_symlink_approved

    with leased() as lease:
        approved = delete_symlink_approved(lease)
        with open_workspace(lease, approved) as workspace:
            with capture_initial_surface(
                lease, approved, workspace, DictPayloads({})
            ) as captured:
                assert captured.manifest == ()
                assert os.listdir(workspace.staging_fd) == []
```

Add the supporting builder to `python/tests/capture_support.py`:

```python
def delete_symlink_spec(target: str = "elsewhere") -> TransactionSpec:
    from atoms.core.fingerprint import SymlinkState

    pre = SymlinkState(target=target, mode=0o777)
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "c" * 64,
        initial_surface={"link": pre},
        final_surface={"link": ABSENT},
        effects=[DeletePath(effect_id="e1", path="link", pre=pre)],
    )


def delete_symlink_approved(lease: Lease, target: str = "elsewhere"):
    from atoms.coordinator.admission import admit

    root_fd = lease._binding.project_root_fd
    try:
        os.unlink("link", dir_fd=root_fd)
    except FileNotFoundError:
        pass
    os.symlink(target, "link", dir_fd=root_fd)
    return admit(lease, compile_spec(delete_symlink_spec(target)))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd python && uv run pytest tests/test_coordinator_capture.py -q`
Expected: FAIL, `ImportError: cannot import name 'require_one_length_per_digest'`.

- [ ] **Step 3: Write the implementation**

Add to `python/src/atoms/coordinator/capture.py`:

```python
from typing import IO, Protocol

from atoms.core.errors import ProtocolError
from atoms.store.blobs import StagedBlob, digest_to_leaf
from atoms.store.records import referenced_digests


class PayloadSource(Protocol):
    """The consumer's planned-postimage bytes (design §7.1).

    Authority §4.1 forbids the engine from reaching back into consumer plan formats and
    §4.2 reserves staging-path derivation to the engine, so the bytes arrive
    content-addressed: two effects writing identical content are supplied once, and the
    consumer never learns a staging path.

    `open` returns a FRESH binary stream, owned by capture, which closes it whether the
    stream is consumed, refused, or abandoned by an earlier failure.
    """

    def open(self, digest: str) -> IO[bytes]: ...


def require_one_length_per_digest(
    pairs: tuple[tuple[str, int], ...],
) -> dict[str, int]:
    """One byte_len per digest, checked BEFORE anything is written (design §7.2).

    `compile_spec` validates each byte_len's range and the empty-hash correspondence but
    never cross-checks that one content_hash carries one byte_len -- measured. The
    staging name is one name per digest, so two lengths collide on it. `_preflight`
    refuses this too, but only after capture has already written both files.

    This is engine misuse surfacing at the first layer that can see it, not external
    drift: the contradiction is in the frozen spec and no filesystem state is involved.
    """
    lengths: dict[str, int] = {}
    for digest, byte_len in pairs:
        previous = lengths.setdefault(digest, byte_len)
        if previous != byte_len:
            raise ProtocolError(
                f"digest {digest} is declared at two byte_len values, {previous} and "
                f"{byte_len}; both would stage under one name"
            )
    return lengths
```

- [ ] **Step 4: Run the two precheck tests**

Run: `cd python && uv run pytest tests/test_coordinator_capture.py -q -k length`
Expected: PASS, 2 tests. The staging tests still fail — Task 7 adds `capture_initial_surface`.

- [ ] **Step 5: Commit**

```bash
git add python/src/atoms/coordinator/capture.py python/tests/capture_support.py \
        python/tests/test_coordinator_capture.py
git commit -m "feat(capture): add the payload contract and the digest-length precheck"
```

---

## Task 7: `capture_initial_surface`, flush ordering, and the manifest

**Files:**
- Modify: `python/src/atoms/coordinator/capture.py`
- Modify: `python/src/atoms/coordinator/admission.py`
- Modify: `python/tests/test_coordinator_capture.py`

**Interfaces:**
- Produces:
  - `class Captured:` live resource with `manifest: tuple[StagedBlob, ...]`, `descriptors: DescriptorTable`, `close()`, `__enter__`, `__exit__`
  - `capture_initial_surface(lease: Lease, approved: ProjectApprovedSpec, workspace: Workspace, payloads: PayloadSource) -> Captured`

- [ ] **Step 1: Write the failing test**

Add to `python/tests/test_coordinator_capture.py`:

```python
def test_capture_composes_with_preparation(leased):
    """The whole seam: open_workspace -> capture -> prepare_transaction."""
    from atoms.core.recovery import TransactionState
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace, prepare_transaction
    from tests.capture_support import AFTER, DictPayloads, digest_of

    with leased() as lease:
        approved = approved_replace(lease)
        payloads = DictPayloads({digest_of(AFTER): AFTER})
        with open_workspace(lease, approved) as workspace:
            with capture_initial_surface(lease, approved, workspace, payloads) as captured:
                prepare_transaction(lease, approved, workspace, captured.manifest)

        record = lease._store.read_active()
        assert record is not None
        assert record.txid == approved.txid
        assert record.state is TransactionState.PREPARED


def test_the_descriptor_table_outlives_capture(leased):
    """§5.5: the table is what A7 executes against; capture returning a bare tuple would
    force A7 to re-resolve and reopen the race §6 closes."""
    from atoms.core.recovery.model import ProjectRoot
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace, prepare_transaction
    from tests.capture_support import AFTER, DictPayloads, digest_of

    with leased() as lease:
        approved = approved_replace(lease)
        payloads = DictPayloads({digest_of(AFTER): AFTER})
        with open_workspace(lease, approved) as workspace:
            with capture_initial_surface(lease, approved, workspace, payloads) as captured:
                prepare_transaction(lease, approved, workspace, captured.manifest)
                # Still live AFTER preparation -- this is the whole point.
                assert isinstance(captured.descriptors.fd_for(ProjectRoot()), int)


def test_every_staged_file_is_flushed_before_its_sink_closes(leased, monkeypatch):
    """§7.3 ordering. `promote_staging` flushes DIRECTORIES only, so nothing else makes
    the contents durable; a lost flush is invisible until a crash."""
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace
    from atoms.fs.linux import LinuxBackend
    from tests.capture_support import AFTER, DictPayloads, digest_of

    events: list[tuple[str, int]] = []
    real_flush = LinuxBackend.flush_file
    real_close = os.close

    def spy_flush(self, fd):
        events.append(("flush", fd))
        return real_flush(self, fd)

    def spy_close(fd):
        events.append(("close", fd))
        return real_close(fd)

    monkeypatch.setattr(LinuxBackend, "flush_file", spy_flush)

    with leased() as lease:
        approved = approved_replace(lease)
        payloads = DictPayloads({digest_of(AFTER): AFTER})
        with open_workspace(lease, approved) as workspace:
            monkeypatch.setattr(os, "close", spy_close)
            with capture_initial_surface(lease, approved, workspace, payloads) as captured:
                monkeypatch.undo()
                count = len(captured.manifest)

    flushed = {fd for kind, fd in events if kind == "flush"}
    assert len(flushed) >= count
    for fd in flushed:
        order = [i for i, (_, seen) in enumerate(events) if seen == fd]
        kinds = [events[i][0] for i in order]
        assert kinds.index("flush") < kinds.index("close"), f"fd {fd} closed before flush"


def test_a_payload_whose_bytes_disagree_refuses(leased):
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace
    from tests.capture_support import AFTER, DictPayloads, digest_of

    with leased() as lease:
        approved = approved_replace(lease)
        # Right key, wrong bytes: external state diverging from frozen intent.
        payloads = DictPayloads({digest_of(AFTER): b"not-after"})
        with open_workspace(lease, approved) as workspace:
            with pytest.raises(PreconditionRefused, match="digest"):
                with capture_initial_surface(lease, approved, workspace, payloads):
                    pass


def test_a_missing_payload_binding_is_a_protocol_error(leased):
    """§9: absent or malformed is a broken submission, not external state."""
    from atoms.core.errors import ProtocolError
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace
    from tests.capture_support import DictPayloads

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            with pytest.raises(ProtocolError, match="payload"):
                with capture_initial_surface(
                    lease, approved, workspace, DictPayloads({})
                ):
                    pass


def test_an_extra_payload_binding_is_not_an_error(leased):
    """§9: `open(digest)` cannot be enumerated, so capture never learns of extras.

    Detecting them would mean adding enumeration machinery for a condition that harms
    nothing -- an unrequested payload is never opened, staged, or promoted.
    """
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace
    from tests.capture_support import AFTER, DictPayloads, digest_of

    with leased() as lease:
        approved = approved_replace(lease)
        payloads = DictPayloads({digest_of(AFTER): AFTER, digest_of(b"junk"): b"junk"})
        with open_workspace(lease, approved) as workspace:
            with capture_initial_surface(lease, approved, workspace, payloads) as captured:
                assert digest_of(b"junk") not in payloads.requested
                assert captured.manifest


def test_a_refusal_leaves_reclaimable_scratch_not_a_halt(leased):
    """§7.4: cleanliness is scoped to success.

    Partial workspace scratch is mutation-free, no durable record exists, and A5b's
    reclamation removes it at the next lease entry.
    """
    from atoms.core.errors import TransactionHalted
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace
    from tests.capture_support import AFTER, DictPayloads, digest_of

    with leased() as lease:
        approved = approved_replace(lease)
        payloads = DictPayloads({digest_of(AFTER): b"wrong"})
        with open_workspace(lease, approved) as workspace:
            with pytest.raises(PreconditionRefused):
                with capture_initial_surface(lease, approved, workspace, payloads):
                    pass
        assert lease._store.read_active() is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd python && uv run pytest tests/test_coordinator_capture.py -q -k capture_composes`
Expected: FAIL, `ImportError: cannot import name 'capture_initial_surface'`.

- [ ] **Step 3: Write the implementation**

Add to `python/src/atoms/coordinator/capture.py`:

```python
class Captured:
    """A live resource: the manifest, and the descriptor table that outlives capture."""

    __slots__ = ("_closed", "descriptors", "manifest")

    def __init__(
        self, *, manifest: tuple[StagedBlob, ...], descriptors: DescriptorTable
    ) -> None:
        self.manifest = manifest
        self.descriptors = descriptors
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.descriptors.close()

    def __enter__(self) -> Captured:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def capture_initial_surface(
    lease: Lease,
    approved: ProjectApprovedSpec,
    workspace: Workspace,
    payloads: PayloadSource,
) -> Captured:
    """Authority §7.3 step 1 (design §7).

    Everything here happens BEFORE the durable record exists, so no path halts.
    """
    _require_admitted(lease, approved)
    if workspace.txid != approved.txid:
        raise ProtocolError(
            f"workspace txid {workspace.txid!r} does not match the proof's "
            f"{approved.txid!r}"
        )

    lengths = require_one_length_per_digest(referenced_digests(approved.compiled.spec))
    backend = lease._binding.backend
    table = build_descriptor_table(lease, approved, workspace)
    manifest: list[StagedBlob] = []
    try:
        with Observation(backend) as observation:
            verify_absence_below(observation, table, approved)
            retained = _observe_and_verify(observation, table, approved, workspace, backend)
            manifest.extend(retained)
        manifest.extend(
            _stage_payloads(
                backend, workspace, payloads, lengths, {e.digest for e in manifest}
            )
        )
    except BaseException:
        table.close()
        raise
    return Captured(manifest=tuple(manifest), descriptors=table)
```

And the two staging helpers:

```python
def _observe_and_verify(
    observation: Observation,
    table: DescriptorTable,
    approved: ProjectApprovedSpec,
    workspace: Workspace,
    backend,
) -> list[StagedBlob]:
    """Verify every declared path against its timeline's FIRST precondition, staging
    each regular-file preimage through the same descriptor it was hashed from."""
    declared = _first_states(approved)
    staged: list[StagedBlob] = []
    seen: set[str] = set()
    for path_entry in approved.paths:
        parent_fd = _parent_fd_or_none(table, path_entry.parent_node)
        if parent_fd is None:
            continue  # Below a stop; §8 already justified its absence.
        expected = declared[path_entry.path]
        if type(expected) is FileState and expected.content_hash not in seen:
            seen.add(expected.content_hash)
            name = digest_to_leaf(expected.content_hash)
            sink = _open_sink(workspace, name)
            try:
                entry = observation.observe(parent_fd, path_entry.leaf, sink_fd=sink)
                _require_state(path_entry.path, entry, expected)
                backend.flush_file(sink)
            finally:
                os.close(sink)
            staged.append(
                StagedBlob(
                    name=name,
                    digest=expected.content_hash,
                    byte_len=expected.byte_len,
                )
            )
            continue
        entry = observation.observe(parent_fd, path_entry.leaf)
        _require_state(path_entry.path, entry, expected)
    return staged


def _stage_payloads(
    backend,
    workspace: Workspace,
    payloads: PayloadSource,
    lengths: dict[str, int],
    already: set[str],
) -> list[StagedBlob]:
    staged: list[StagedBlob] = []
    for digest in sorted(set(lengths) - already):
        name = digest_to_leaf(digest)
        try:
            stream = payloads.open(digest)
        except Exception as caught:
            raise ProtocolError(
                f"the payload source supplied no usable binding for {digest}: {caught}"
            ) from caught
        sink = _open_sink(workspace, name)
        try:
            observed, byte_len = _stream_into(stream, sink)
            if observed != digest or byte_len != lengths[digest]:
                raise PreconditionRefused(
                    f"the payload for {digest} hashes to {observed} at {byte_len} "
                    f"bytes, not {digest} at {lengths[digest]}"
                )
            backend.flush_file(sink)
        finally:
            stream.close()
            os.close(sink)
        staged.append(StagedBlob(name=name, digest=digest, byte_len=lengths[digest]))
    return staged
```

Plus the three small helpers `_open_sink`, `_stream_into`, `_require_state`, and
`_parent_fd_or_none`:

```python
def _open_sink(workspace: Workspace, name: str) -> int:
    """The staging sink. O_EXCL, so external occupancy of the leaf surfaces as EEXIST."""
    flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    try:
        return os.open(name, flags, 0o600, dir_fd=workspace.staging_fd)
    except FileExistsError as caught:
        raise PreconditionRefused(
            f"staging/{workspace.txid}/{name} is already occupied; authority §11 names "
            "pre-existing external occupancy of an engine-derived scratch leaf a clean "
            "refusal, and it need not be concurrent"
        ) from caught


def _stream_into(stream: IO[bytes], sink_fd: int) -> tuple[str, int]:
    import hashlib

    digest = hashlib.sha256()
    length = 0
    while True:
        chunk = stream.read(1 << 20)
        if not chunk:
            break
        digest.update(chunk)
        length += len(chunk)
        offset = 0
        view = memoryview(chunk)
        while offset < len(chunk):
            offset += os.write(sink_fd, view[offset:])
    return "sha256:" + digest.hexdigest(), length


def _require_state(path: str, entry, expected) -> None:
    observed = getattr(entry, "state", None)
    if observed != expected:
        raise PreconditionRefused(
            f"{path!r} is {observed!r}, not the declared initial {expected!r}"
        )


def _parent_fd_or_none(table: DescriptorTable, node) -> int | None:
    try:
        return table.fd_for(node)
    except KeyError:
        return None
```

- [ ] **Step 4: Add the admission gate**

In `python/src/atoms/coordinator/admission.py`, extend the entry-point gate-set docstring and any
gate-set registry to name `capture_initial_surface` as A6's **one** new entry (ledger #9). The
descriptor-table builder stays package-private and is reached only through it, so it is not a second
gate site.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd python && uv run pytest tests/test_coordinator_capture.py -q`
Expected: PASS, 14 tests.

- [ ] **Step 6: Run the full gate set**

Run: `cd python && uv run ruff format && uv run ruff check && uv run pyright && uv run pytest -q`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add python/src/atoms/coordinator/capture.py python/src/atoms/coordinator/admission.py \
        python/tests/test_coordinator_capture.py
git commit -m "feat(capture): stage the initial surface behind one flushed manifest"
```

---

## Task 8: Conformance against A3's two routes

**Files:**
- Create: `python/tests/test_coordinator_capture_conformance.py`

**Interfaces:**
- Consumes: everything above; adds no production code.

The **`Observation` mechanism's** outputs feed two different A3 entry points, and one route cannot
exercise both. `Captured` exposes no observations — it carries the manifest and the descriptor table —
so both routes drive the observer directly. Scratch relations come from the real observer over actual
files and descriptors; building a `ScratchObservation` by hand and feeding it to A3 tests A3, not A6.

- [ ] **Step 1: Write the test**

Create `python/tests/test_coordinator_capture_conformance.py`:

```python
"""A6 tier 4 -- the observer's output against A3's two entry points (design §11.4)."""

from __future__ import annotations

import os

from atoms.core.recovery import (
    CommitDecision,
    EffectJournalState,
    JournalState,
    PersistentObservation,
    ScratchObservation,
    ScratchRole,
    TransactionState,
    build_recovery_snapshot,
)
from atoms.core.recovery.plan import JointObservation
from atoms.fs.observe import Observation
from tests.capture_support import BEFORE, approved_replace, write_project_file


def test_a_complete_observation_is_accepted_by_build_recovery_snapshot(leased):
    """Proves the observer satisfies A3's coverage and shape validators without A7."""
    from atoms.coordinator.descriptors import build_descriptor_table
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            with build_descriptor_table(lease, approved, workspace) as table:
                with Observation(lease._binding.backend) as observation:
                    (path_entry,) = approved.paths
                    live = observation.observe(
                        table.fd_for(path_entry.parent_node), path_entry.leaf
                    )
                    scratch_entry = observation.observe(
                        table.fd_for(approved.scratch[0].parent_node),
                        approved.scratch[0].leaf,
                    )

                snapshot = build_recovery_snapshot(
                    compiled=approved.compiled,
                    topology=approved.topology,
                    transaction_state=TransactionState.PREPARED,
                    commit_decision=CommitDecision.UNCOMMITTED,
                    rollback_result=None,
                    halt_diagnostic=None,
                    active=True,
                    journals=(EffectJournalState("e1", JournalState.PENDING),),
                    persistent_observations=(
                        PersistentObservation(path_entry.path, live),
                    ),
                    scratch_observations=(
                        ScratchObservation("e1", ScratchRole.STAGING, scratch_entry, None),
                    ),
                )

    assert snapshot.persistent_observations[0].entry is live


def test_a_scratch_only_observation_forms_a_joint_observation(leased):
    """The committed-cleanup route: exactly the named retained scratch slot, with empty
    persistent and occupancy coverage. `authorize_recovery_step` consumes this, not a
    RecoverySnapshot, so one conformance route cannot test both."""
    from atoms.coordinator.descriptors import build_descriptor_table
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        scratch = approved.scratch[0]
        with open_workspace(lease, approved) as workspace:
            with build_descriptor_table(lease, approved, workspace) as table:
                parent_fd = table.fd_for(scratch.parent_node)
                fd = os.open(
                    scratch.leaf,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=parent_fd,
                )
                os.write(fd, b"partial")
                os.close(fd)
                try:
                    with Observation(lease._binding.backend) as observation:
                        entry = observation.observe(parent_fd, scratch.leaf)
                        joint = JointObservation(
                            persistent=(),
                            scratch=(
                                ScratchObservation(
                                    scratch.effect_id, scratch.role, entry, None
                                ),
                            ),
                            parent_occupancy=(),
                        )
                finally:
                    os.unlink(scratch.leaf, dir_fd=parent_fd)

    assert joint.persistent == ()
    assert joint.scratch[0].effect_id == scratch.effect_id


def test_the_prefix_relation_comes_from_real_files(leased):
    """§11.3: over actual files and descriptors, not hand-built model values.

    A `ScratchObservation` constructed by hand and fed to A3 tests A3.
    """
    from atoms.core.recovery.model import FileBuildRelation
    from atoms.coordinator.descriptors import build_descriptor_table
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            with build_descriptor_table(lease, approved, workspace) as table:
                staging_fd = workspace.staging_fd
                for name, payload in (("staged", b"pay"), ("planned", b"payload")):
                    fd = os.open(
                        name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=staging_fd,
                    )
                    os.write(fd, payload)
                    os.close(fd)
                staged_fd = os.open("staged", os.O_RDONLY, dir_fd=staging_fd)
                planned_fd = os.open("planned", os.O_RDONLY, dir_fd=staging_fd)
                try:
                    with Observation(lease._binding.backend) as observation:
                        relation = observation.build_relation(staged_fd, planned_fd)
                finally:
                    os.close(staged_fd)
                    os.close(planned_fd)
                    os.unlink("staged", dir_fd=staging_fd)
                    os.unlink("planned", dir_fd=staging_fd)

    assert relation is FileBuildRelation.PREFIX
```

- [ ] **Step 2: Run the tests**

Run: `cd python && uv run pytest tests/test_coordinator_capture_conformance.py -q`
Expected: PASS, 3 tests.

- [ ] **Step 3: Commit**

```bash
git add python/tests/test_coordinator_capture_conformance.py
git commit -m "test(capture): conform the observer to A3's snapshot and joint routes"
```

---

## Task 9: Architecture guards, the adversarial tier, and the documents

**Files:**
- Modify: `python/tests/test_fs_architecture.py`
- Modify: `python/tests/test_coordinator_architecture.py`
- Modify: `python/tests/test_coordinator_capture.py`
- Modify: `docs/deferred-obligation-ledger.md`
- Modify: `docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md`
- Modify: `AGENTS.md`, `README.md`
- Modify: `docs/plans/2026-08-07-a6-coherent-capture-design.md` (status header)

- [ ] **Step 1: Write the import whitelist test**

Add to `python/tests/test_fs_architecture.py`:

```python
def test_observe_imports_only_the_recovery_model():
    """Ledger #13's "may not pre-classify" as a mechanical property.

    A blacklist on `snapshot` would be insufficient: `atoms/core/recovery/__init__.py`
    re-exports `classify_recovery` and `authorize_recovery_step`, so a classifier is
    reachable through the package facade. Whitelist `model`, refuse everything else.
    """
    import ast
    from pathlib import Path

    source = Path(__file__).parents[1] / "src" / "atoms" / "fs" / "observe.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    offenders = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").startswith("atoms.core.recovery")
        and node.module != "atoms.core.recovery.model"
    ]
    assert offenders == []
```

- [ ] **Step 2: Write the adversarial tests**

Add to `python/tests/test_coordinator_capture.py`:

```python
def test_a_leaf_swapped_for_a_symlink_between_observation_and_use_refuses(leased):
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace
    from tests.capture_support import AFTER, DictPayloads, digest_of

    with leased() as lease:
        approved = approved_replace(lease)
        root_fd = lease._binding.project_root_fd
        os.unlink("d/f.txt", dir_fd=root_fd)
        os.symlink("/etc/passwd", "d/f.txt", dir_fd=root_fd)
        payloads = DictPayloads({digest_of(AFTER): AFTER})
        with open_workspace(lease, approved) as workspace:
            with pytest.raises(PreconditionRefused):
                with capture_initial_surface(lease, approved, workspace, payloads):
                    pass


def test_an_externally_occupied_staging_leaf_refuses(leased):
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace
    from atoms.store.blobs import digest_to_leaf
    from tests.capture_support import AFTER, BEFORE, DictPayloads, digest_of

    with leased() as lease:
        approved = approved_replace(lease)
        payloads = DictPayloads({digest_of(AFTER): AFTER})
        with open_workspace(lease, approved) as workspace:
            squatter = os.open(
                digest_to_leaf(digest_of(BEFORE)),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=workspace.staging_fd,
            )
            os.close(squatter)
            with pytest.raises(PreconditionRefused, match="occupied"):
                with capture_initial_surface(lease, approved, workspace, payloads):
                    pass


def test_no_project_path_is_mutated_by_capture(leased):
    """Authority §13.5: the mutation surface. A6 writes only into staging/<txid>/."""
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace
    from tests.capture_support import AFTER, DictPayloads, digest_of

    def snapshot(root_fd):
        seen = {}
        for base, _dirs, files in os.walk("/proc/self/fd/%d" % root_fd):
            for name in files:
                target = os.path.join(base, name)
                info = os.lstat(target)
                seen[target] = (info.st_mtime_ns, info.st_size, info.st_ino)
        return seen

    with leased() as lease:
        approved = approved_replace(lease)
        root_fd = lease._binding.project_root_fd
        before = snapshot(root_fd)
        payloads = DictPayloads({digest_of(AFTER): AFTER})
        with open_workspace(lease, approved) as workspace:
            with capture_initial_surface(lease, approved, workspace, payloads):
                pass
        assert snapshot(root_fd) == before
```

- [ ] **Step 3: Run both new tiers**

Run: `cd python && uv run pytest tests/test_fs_architecture.py tests/test_coordinator_capture.py -q`
Expected: PASS.

- [ ] **Step 4: Land the two authority amendments**

In `docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md`:

**§14, Plan A item 4** — replace with:

> 4. Coherent capture and the observation mechanism (§6, and §10's coherent-observation contract).
>    Restartable materialization's staging-object classification ships with the effects that create the
>    objects it classifies (item 5).

**§6, second absence-capture case** — append to the `ENOTDIR` paragraph:

> `ENOTDIR` establishes only that the blocker is not a directory — it does not distinguish a regular
> file from a socket, FIFO, or device node. Neither branch is selected by the errno: the regular-file
> branch is selected by verifying the blocker against the timeline's first `FileState`, and the symlink
> branch by verifying it against the timeline's first `SymlinkState`. A blocker matching neither
> declared state refuses — including a symlink whose target or mode has drifted, which is a symlink but
> not the declared one.

- [ ] **Step 5: Update the deferred-obligation ledger**

In `docs/deferred-obligation-ledger.md`, amend entries 1, 3, 13, and 19 to record the discharged half
and the named A7 residue. Do **not** remove any of the four — each keeps work A7 owns:

| # | New required-behavior text |
| --- | --- |
| 1 | Capture half discharged. A7 owns materialization's half. |
| 3 | Capture/inference half discharged. A7 owns handing §9.5's published-directory descriptor down at execution. |
| 13 | Observation-mechanism half discharged. A7 owns complete recovery assembly, committed-cleanup sequencing, and fresh authorization observations. |
| 19 | A6's half discharged. A7's execution half remains. |

- [ ] **Step 6: Synchronize the status strings**

Add A6's state to `AGENTS.md` (the A3 and A5 paragraphs both claim "A6–A8 remain unimplemented") and
to the `README.md` layering note. Change the A6 design doc's status header from "Designed on
2026-08-07, unimplemented" to "Implemented on 2026-08-07. A7–A8 remain unimplemented."

Add to `python/tests/test_coordinator_architecture.py`, following `test_a4a_…`, `test_a4b_…`, and
`test_a5_…`:

```python
def test_a6_status_is_synchronized_across_authority_documents():
    from pathlib import Path

    docs = Path(__file__).parents[2]
    agents = (docs / "AGENTS.md").read_text(encoding="utf-8")
    design = (
        docs / "docs" / "plans" / "2026-08-07-a6-coherent-capture-design.md"
    ).read_text(encoding="utf-8")

    assert "A6 — coherent capture" in agents or "A6 -- coherent capture" in agents
    assert "A6–A8 remain unimplemented" not in agents
    assert "A7–A8 remain unimplemented" in design
```

- [ ] **Step 7: Run the full gate set**

Run: `cd python && uv run ruff format && uv run ruff check && uv run pyright && uv run pytest -q`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(a6): land coherent capture with its ledger and authority amendments"
```

---

## Self-Review

**Spec coverage.** Every design section maps to a task: §5 → Task 3; §6 → Tasks 1–2; §7 → Tasks 6–7;
§7.2 → Task 6; §7.3 → Task 7; §8 → Task 5; §9/§9.1 → Task 1 (`translated_lookup`) with the
refusal table exercised across Tasks 5–7; §10.1 → Task 7 Step 4; §10.2 → Task 3 Step 1; §10.3 → Task 4;
§11.1–§11.6 → Tasks 1, 2, 7, 8, 9; §12's fourteen criteria → the tasks listed beside each.

**Two spec items that needed a task and now have one:** §10.2's `filesystem_type_of` rename (Task 3
Step 1, surfaced while probing — `read_lookup_constraints` needs the type string and the only
backend-checked route was private) and §11.6's mutation-surface assertion (Task 9 Step 2).

**Type consistency.** `Observation.observe(parent_fd, leaf, *, sink_fd)` is called with the same
signature in Tasks 2, 5, 7, and 8. `DescriptorTable.fd_for(node)` raises `KeyError` for an absent node
in Task 3's test and is caught as `KeyError` by `_parent_fd_or_none` in Task 7. `WalkStop.blocker` is
`EntryKind | None` in Task 3 and matched against `EntryKind` in Task 5. `StagedBlob(name, digest,
byte_len)` matches `blobs.py:35`. `Captured.manifest` is the `tuple[StagedBlob, ...]`
`prepare_transaction` takes.

**Three places the plan tells the implementer to stop rather than adapt:** Task 2 Step 3
(`FileBuildRelation` member names), Task 4 Step 4 (the occurrence-expansion helper's name), and Task 5
Step 4 (`TimelineOccurrence`'s precondition field). Each is a name this plan asserts but did not
measure directly, and guessing wrong would put A6 in the business of classifying.
