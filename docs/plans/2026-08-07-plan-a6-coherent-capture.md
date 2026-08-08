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

**Tech Stack:** Python 3.11+, stdlib only (`os`, `stat`, `hashlib`, `errno`, `contextlib`,
`dataclasses`, `typing`), `pytest`, `ruff`, `pyright`. Builds on A4a's `Backend`/`ProjectBinding`,
A4b's `ProjectApprovedSpec` and `read_lookup_constraints`, A5a's `Workspace`/`StagedBlob`, A5b's
`Lease` and `_parent_paths`, and A3's `core.recovery.model`.

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
| `FileBuildRelation` is `EXACT \| STRICT_PREFIX \| DIVERGED`. **There is no `PREFIX`.** | probe, 2026-08-07; `model.py:55` |
| The occurrence expander is `occurrences`, a `singledispatch` returning `Occurrence(path, pre, post, role)`. There is no `occurrences_of`. | probe, 2026-08-07; `effects.py:69` |
| `TimelineOccurrence` is `(effect_id, effect_index, role, pre, post)`; `PathTimeline` is `(path, occurrences)`. | probe, 2026-08-07; `timeline.py:15` |
| `ABSENT` is `AbsentState()`, a distinct dataclass — **not** `None`, and never equal to a missing attribute. | probe, 2026-08-07; `fingerprint.py` |
| `test_no_unregistered_public_function_accepts_the_proof` asserts `found == registered` over every public `coordinator/*.py` function annotating a `ProjectApprovedSpec` parameter. A new public one **fails the suite** unless registered. | `test_fs_architecture.py:1061-1080` |
| Each name in `_TRANSACTION_STAGE_ENTRY_POINTS` must have `_require_admitted(...)` as its **first statement after the docstring**. | `test_fs_architecture.py:1040-1058` |
| `_TRANSACTION_STAGE_ENTRY_POINTS` is `{"atoms/coordinator/prepare.py": ("open_workspace", "prepare_transaction"), "atoms/coordinator/transitions.py": ("persist_plan_prefix",)}`. | `test_fs_architecture.py:1001` |
| `test_a5_status_is_synchronized_across_authority_documents` asserts the literal `"A5 is implemented; A6–A8 remain unimplemented"` in `AGENTS.md`. Editing that sentence **breaks it**. | `test_store_architecture.py:1269-1288` |
| `_project_state(project_root)` in `test_coordinator_lease.py:186` already records the root itself as `"."`, uses `lstat` throughout, and keys on `(S_IFMT, S_IMODE, st_dev, st_ino, st_size, payload)` — the correct mutation-surface snapshot. | `test_coordinator_lease.py:186` |
| For `CreateFileNoClobber("e1", "d/f.txt")` with `d` existing: one `ApprovedPath(path='d/f.txt', parent_node=TopologyDirectory(node_id=0), leaf='f.txt')`; `directories` is `ApprovedExistingDirectory(ProjectRoot())` and `ApprovedExistingDirectory(TopologyDirectory(0))`; `work_base` is `None`. | probe, 2026-08-07 |
| `_parent_paths` for that spec is `{ProjectRoot(): '', PersistentNode('d/f.txt'): 'd/f.txt', TopologyDirectory(0): 'd'}`. **No `ScratchNode` and no `WorkRoot` key.** | probe, 2026-08-07 |
| For `CreateDirectory("e1","d")` + `CreateFileNoClobber("e2","d/f.txt")`: `directories` is `ApprovedExistingDirectory(ProjectRoot())`, `ApprovedPlannedDirectory(PersistentNode('d'))`, `ApprovedPlannedDirectory(WorkRoot())`; `work_base` populated; `_parent_paths` is `{ProjectRoot(): '', PersistentNode('d'): 'd', PersistentNode('d/f.txt'): 'd/f.txt'}`. | probe, 2026-08-07 |
| In that spec `ScratchNode('e2', STAGING)` is parented by `PersistentNode('d')` — **a planned directory**. Its slot is unobservable at capture and absent by construction. | probe, 2026-08-07 |
| `WorkRoot()`'s approved baseline is `ApprovedPlannedDirectory(WorkRoot(), inherited_constraints(work_base.constraints, filesystem_type))`, recorded in `approved.directories` like every other node's. `approved.work_base` describes `metadata_root/work` — the **parent** of `work/<txid>`, which is what `workspace.work_fd` names. | `approval.py:155`, `topology.py:171-180`, `workspace.py:187` |
| `authorize_recovery_step` compares `_project_identity_relations`, i.e. pairwise `SAME`/`DIFFERENT` among the observation's own tokens — never a raw token. A **fresh** `Observation` (a new token universe) therefore authorizes correctly, which is what makes ledger #13's fresh-observation contract coherent. | `authorization.py:63-75`, `diagnostics.py:175-202` |
| `open_child_directory` raises `ENOTDIR` on a regular file, `ELOOP` on a symlink, `ENOENT` on a missing name. **`ENOTDIR` does not distinguish a regular file from a socket, FIFO, or device node**, which is why the plan never maps an errno to a kind. | probe, 2026-08-07 |
| `os.listdir(fd)` works on a directory descriptor and omits `.` and `..`; a fresh workspace `staging/` lists `[]`. | probe, 2026-08-07 |
| `backend.flush_file(fd)` succeeds on a **write-only** descriptor. `os.open(name, O_WRONLY\|O_CREAT\|O_EXCL\|O_NOFOLLOW\|O_CLOEXEC, 0o600, dir_fd=staging_fd)` creates mode `0o600`; a second create raises `EEXIST`. | probe, 2026-08-07 |
| `compile_spec` **accepts** one `content_hash` declared at two `byte_len` values, and `referenced_digests` returns both pairs. | probe, 2026-08-07 |
| `referenced_digests` scans only the two surfaces. For `ReplaceFile(p, A→B)` + `ReplaceFile(p, B→C)` it returns A and C; **B is absent.** | probe, 2026-08-07; `records.py:412` |
| `connection.py:555` raises `ProtocolError` for any promoted digest outside `referenced_digests`. | `connection.py:547-559` |
| `_preflight` already refuses two `byte_len` for one digest **in one manifest**, and requires `set(os.listdir(staging_fd)) == {entry.name}` exactly. Both fire *after* capture has written the bytes. | `blobs.py:273-285` |
| `EntryIdentity()` instances are distinct, `==`-comparable, and hashable. `ObservedSymlink` has **no** `identity` field. | probe, 2026-08-07; `model.py:82-104` |
| `JointObservation(persistent, scratch, parent_occupancy)`; `authorize_recovery_step(plan, step_index, observed) -> AuthorizedStep \| HaltPlan`. | probe, 2026-08-07 |
| `_validate_topology` enforces a **rooted tree** and builds `expected_persistent` from every timeline, so a declared directory is a `PersistentNode` that can also be a parent. | `snapshot.py:276-350` |
| `DirectoryConstraints` is `lookup_proof` and `name_max` only. `read_mount_id(fd)` vs `binding.evidence.mount_id` is a separate check. | `lookup.py:45-47`, `resolve.py:448-453` |
| `resolve._filesystem_type(binding)` is private and is the only route to the type string that also checks the backend is Linux. | `resolve.py:73-81` |
| `ProjectBinding.project_root_fd` and `Workspace.staging_fd`/`work_fd` are public properties returning **borrowed** descriptors. `promote_staging` spends `staging_fd`. | `binding.py:121`, `workspace.py:78-93`, `blobs.py:366` |
| A5a's translation rule: "the default is a bare `raise`, so an unrecognized code keeps its own class *and* its traceback." | `store/errors.py:26-46` |
| The test volume resolves to `<repo>/.atoms-test-volume` on ext4 with no env var set. | probe, 2026-08-07 |

## Global Constraints

- **Python 3.11+, stdlib only.** No new third-party dependency.
- **Fail early; no silent fallbacks.** A failure to look is never a finding of absence.
- **Composition over inheritance.**
- **`Backend` gains no method.** Occupancy and the staging sink are `os.listdir` and `os.open`.
- **No project path is mutated.** A6 writes only into `staging/<txid>/`.
- **No new exception type.** `ProtocolError`, `PreconditionRefused`, `CapabilityUnavailable` only.
- **No A6 path raises `TransactionHalted`.** Errnos with a defined domain meaning translate; every
  other `OSError` propagates with its own class and traceback.
- **No errno ever selects a state branch.** The declared state does.
- **`atoms/fs/observe.py` may import `atoms.core.recovery.model` and no other `core.recovery` module.**
- **Every commit leaves `uv run pytest` green.** No task commits a test whose subject does not exist.
- Exact-type checks, not `isinstance`, at trust boundaries — the house `_require_exact` pattern.
- Docs use `~/d/atoms/...` for filepaths.
- Conventional commits. **No AI-attribution trailer or footer.**
- Gates, all from `python/`: `uv run pytest`, `uv run ruff check`, `uv run pyright` — the three
  `AGENTS.md` names. **`ruff format` is not a gate here.** `line-length` is configured at 120 but
  the tree is hand-wrapped: `ruff format --check` reports 86 of 124 files would be reformatted,
  and no module in `atoms/fs/` or `atoms/coordinator/` exceeds 92 columns. Running the formatter
  over a new file makes it the only 120-column file in its package. **New code is hand-wrapped to
  match its neighbours — 92 columns is the ceiling.** Closing that repo-wide drift is its own
  change, not A6's.

## File Structure

| File | Responsibility |
| --- | --- |
| `python/src/atoms/fs/observe.py` | **Create.** `Observation` and `translated_lookup`. |
| `python/src/atoms/fs/resolve.py` | **Modify.** `_filesystem_type` → public `filesystem_type_of`. |
| `python/src/atoms/coordinator/descriptors.py` | **Create.** `DescriptorTable`, `WalkStop`, `_build_descriptor_table`. |
| `python/src/atoms/coordinator/capture.py` | **Create.** `PayloadSource`, `Captured`, `capture_initial_surface`. |
| `python/src/atoms/store/records.py` | **Modify.** Widen `referenced_digests`. |
| `python/tests/capture_support.py` | **Create.** Plain-function builders for the capture tiers. |
| `python/tests/coordinator_support.py` | **Modify.** Receives `project_state`, moved from the lease tier. |
| `python/tests/test_coordinator_lease.py` | **Modify.** Imports the moved `project_state`. |
| `python/tests/test_fs_observe.py` | **Create.** Tier 1. |
| `python/tests/test_coordinator_descriptors.py` | **Create.** Tier 2. |
| `python/tests/test_coordinator_capture.py` | **Create.** Tier 3. |
| `python/tests/test_coordinator_capture_conformance.py` | **Create.** Tier 4. |
| `python/tests/test_coordinator_capture_adversarial.py` | **Create.** Tier 5. |
| `python/tests/test_fs_architecture.py` | **Modify.** Import whitelist; register the entry point. |
| `python/tests/test_store_architecture.py` | **Modify.** The A5 status assertion, both halves. |
| `docs/plans/2026-08-02-a5b-recovery-lease-design.md` | **Modify.** Its status header still says A6 is unimplemented. |
| `python/tests/test_coordinator_architecture.py` | **Modify.** The A6 status test. |
| `python/tests/test_store_records.py` | **Modify.** The widened helper's tests. |

---

## Task 1: `translated_lookup` and the `Observation` resource

**Files:**
- Create: `python/src/atoms/fs/observe.py`
- Create: `python/tests/test_fs_observe.py`

**Interfaces:**
- Consumes: `atoms.fs.backend.Backend`; `atoms.core.recovery.model`'s `ObservedAbsent`, `ObservedFile`,
  `ObservedDirectory`, `ObservedSymlink`, `ObservedEntry`, `EntryIdentity`, `FileBuildRelation`,
  `OBSERVED_ABSENT`; `atoms.core.fingerprint`'s `FileState`, `DirectoryState`, `SymlinkState`.
- Produces:
  - `translated_lookup(context: str) -> ContextManager[None]`
  - `class Observation:` — `__init__(self, backend: Backend)`;
    `observe(self, parent_fd: int, leaf: str, *, sink_fd: int | None = None, modeled: frozenset[str] | None = None) -> ObservedEntry`;
    `pinned_descriptor(self, identity: EntryIdentity) -> int`;
    `build_relation(self, staged_fd: int, planned_fd: int) -> FileBuildRelation`;
    `close(self)`, `__enter__`, `__exit__`.

Two rules this task exists to make structural. **A descriptor is either owned by the pass or closed —
never neither**, so every failure between opening and pinning closes what it opened. And **a directory
is never described without being enumerated**: `has_unmodeled_child=False` produced by not looking is a
failure to look reported as a finding of absence, so the route refuses instead.

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
    FileBuildRelation,
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


def open_descriptors() -> int:
    return len(os.listdir("/proc/self/fd"))


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
    assert first.state == second.state


def test_distinct_entries_yield_distinct_tokens(project):
    root, fd = project
    (root / "a.txt").write_bytes(b"a")
    (root / "b.txt").write_bytes(b"b")

    with Observation(LinuxBackend()) as observation:
        assert observation.observe(fd, "a.txt").identity != observation.observe(
            fd, "b.txt"
        ).identity


def test_each_pass_mints_a_fresh_token_universe(project):
    root, fd = project
    (root / "f.txt").write_bytes(b"payload")

    with Observation(LinuxBackend()) as first_pass:
        first = first_pass.observe(fd, "f.txt")
    with Observation(LinuxBackend()) as second_pass:
        second = second_pass.observe(fd, "f.txt")

    # A3's identity equality means "same entry, same pass". A token that survived the
    # pass would let A3 conclude more than was observed.
    assert first.identity != second.identity


def test_a_symlink_carries_a_fingerprint_and_no_identity(project):
    root, fd = project
    os.symlink("target", root / "link")

    with Observation(LinuxBackend()) as observation:
        entry = observation.observe(fd, "link")

    assert type(entry) is ObservedSymlink
    assert entry.state.target == "target"
    # Structural, not conventional: the type has no field for one.
    assert not hasattr(entry, "identity")


def test_a_directory_requires_its_modeled_children(project):
    """Failure to look is not a finding of absence.

    `has_unmodeled_child` is evidence. Producing `False` without enumerating would put a
    fabricated fact into A3's input, so the route refuses rather than guessing.
    """
    root, fd = project
    (root / "sub").mkdir()

    with Observation(LinuxBackend()) as observation:
        with pytest.raises(ProtocolError, match="modeled"):
            observation.observe(fd, "sub")


def test_a_directory_observed_with_modeled_children_reports_occupancy(project):
    root, fd = project
    (root / "sub").mkdir(mode=0o750)
    (root / "sub" / "modeled").write_bytes(b"")
    (root / "sub" / "stranger").write_bytes(b"")

    with Observation(LinuxBackend()) as observation:
        entry = observation.observe(fd, "sub", modeled=frozenset({"modeled"}))

    assert type(entry) is ObservedDirectory
    assert entry.state.mode == 0o750
    assert entry.has_unmodeled_child is True


def test_a_fully_modeled_directory_reports_no_unmodeled_child(project):
    root, fd = project
    (root / "sub").mkdir()
    (root / "sub" / "modeled").write_bytes(b"")

    with Observation(LinuxBackend()) as observation:
        entry = observation.observe(
            fd, "sub", modeled=frozenset({"modeled", "not-present-yet"})
        )

    assert entry.has_unmodeled_child is False


def test_an_empty_directory_reports_no_unmodeled_child(project):
    root, fd = project
    (root / "sub").mkdir()

    with Observation(LinuxBackend()) as observation:
        entry = observation.observe(fd, "sub", modeled=frozenset())

    assert entry.has_unmodeled_child is False


def test_an_absent_name_is_observed_as_absent(project):
    _, fd = project

    with Observation(LinuxBackend()) as observation:
        assert type(observation.observe(fd, "missing")) is ObservedAbsent


def test_a_kind_with_no_declarable_state_refuses(project):
    """A socket, FIFO, or device node. No declared state can describe one."""
    root, fd = project
    os.mkfifo(root / "pipe")

    with Observation(LinuxBackend()) as observation:
        with pytest.raises(PreconditionRefused, match="neither"):
            observation.observe(fd, "pipe")


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


def test_a_failing_sink_leaks_no_descriptor(project):
    """Ownership is transferred or the descriptor is closed -- never neither."""
    root, fd = project
    (root / "f.txt").write_bytes(b"payload")
    readonly = os.open(str(root / "readonly"), os.O_RDONLY | os.O_CREAT, 0o400)
    try:
        before = open_descriptors()
        observation = Observation(LinuxBackend())
        with pytest.raises(OSError):
            observation.observe(fd, "f.txt", sink_fd=readonly)
        observation.close()
        assert open_descriptors() == before
    finally:
        os.close(readonly)


def test_the_retained_descriptor_pins_the_inode(project):
    """§11.1: assert the pin, not the reuse.

    Unlinking and recreating does not *force* the kernel to reallocate the inode, so a
    test that asserted distinct tokens after a recreate would pass just as readily with
    no pin at all -- it would be testing the allocator's mood. What is assertable is the
    mechanism: while the pass lives the observation still holds the entry open, so the
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
    (root / "a.txt").write_bytes(b"a")
    (root / "b.txt").write_bytes(b"b")

    before = open_descriptors()
    observation = Observation(LinuxBackend())
    entry = observation.observe(fd, "a.txt")
    observation.observe(fd, "b.txt")
    observation.close()

    assert open_descriptors() == before
    with pytest.raises(ProtocolError, match="closed"):
        observation.pinned_descriptor(entry.identity)


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


def test_identical_bytes_are_exact(project):
    root, _ = project
    assert _relation(root, b"payload", b"payload") is FileBuildRelation.EXACT


def test_a_truncated_staging_object_is_a_strict_prefix(project):
    root, _ = project
    assert _relation(root, b"pay", b"payload") is FileBuildRelation.STRICT_PREFIX


def test_an_empty_staging_object_is_a_strict_prefix(project):
    root, _ = project
    assert _relation(root, b"", b"payload") is FileBuildRelation.STRICT_PREFIX


def test_differing_bytes_are_diverged(project):
    root, _ = project
    assert _relation(root, b"paZload", b"payload") is FileBuildRelation.DIVERGED


def test_a_staging_object_longer_than_the_plan_is_diverged(project):
    root, _ = project
    assert _relation(root, b"payload+", b"payload") is FileBuildRelation.DIVERGED


def test_a_namespace_contradiction_refuses():
    with pytest.raises(PreconditionRefused, match="while probing"):
        with translated_lookup("probing"):
            raise OSError(errno.ELOOP, "symlink")


def test_an_unsupported_semantic_is_a_capability_refusal():
    with pytest.raises(CapabilityUnavailable):
        with translated_lookup("probing"):
            raise OSError(errno.EOPNOTSUPP, "no")


def test_an_undefined_errno_propagates_as_itself():
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
architecture test asserts the whitelist over both import forms.
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
    FileBuildRelation,
    ObservedDirectory,
    ObservedEntry,
    ObservedFile,
    ObservedSymlink,
)
from atoms.fs.backend import Backend

_READ_CHUNK = 1 << 20

# Errnos with a defined domain meaning after approval (design §9.1). Everything else
# propagates with its own class and traceback -- A5a's rule for SQLite result codes,
# applied to errno. Reads, writes, and flushes raise EIO, ENOSPC, EROFS and more, and
# none of those is external state contradicting the frozen spec.
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
                f"the backend cannot supply the semantics needed while {context}: "
                f"{caught}"
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

    Every descriptor this class opens is either handed to `_pin` -- which owns it from
    that moment -- or closed on the way out. There is no path on which one is neither.
    """

    __slots__ = ("_backend", "_closed", "_pins", "_tokens")

    def __init__(self, backend: Backend) -> None:
        self._backend = backend
        self._tokens: dict[tuple[int, int], EntryIdentity] = {}
        self._pins: dict[EntryIdentity, int] = {}
        self._closed = False

    def observe(
        self,
        parent_fd: int,
        leaf: str,
        *,
        sink_fd: int | None = None,
        modeled: frozenset[str] | None = None,
    ) -> ObservedEntry:
        """One entry, observed coherently.

        `modeled` is REQUIRED when the entry turns out to be a directory: occupancy is
        evidence, and reporting `has_unmodeled_child=False` without enumerating would be
        a failure to look recorded as a finding of absence.
        """
        self._require_open()
        _require_leaf(leaf)
        with translated_lookup(f"looking up {leaf!r}"):
            try:
                info = os.lstat(leaf, dir_fd=parent_fd)
            except FileNotFoundError:
                return OBSERVED_ABSENT
        if stat.S_ISLNK(info.st_mode):
            return self._observe_symlink(parent_fd, leaf)
        if stat.S_ISDIR(info.st_mode):
            if modeled is None:
                raise ProtocolError(
                    f"{leaf!r} is a directory; observing one requires its modeled child "
                    "names, because occupancy evidence may not be fabricated"
                )
            return self._observe_directory(parent_fd, leaf, modeled)
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
            staged = _read_exactly(staged_fd, _READ_CHUNK)
            planned = _read_exactly(planned_fd, _READ_CHUNK)
            if staged == planned:
                if not staged:
                    return FileBuildRelation.EXACT
                continue
            shortest = min(len(staged), len(planned))
            if staged[:shortest] != planned[:shortest]:
                return FileBuildRelation.DIVERGED
            # One side ran out first. A short read cannot cause this: `_read_exactly`
            # returns fewer bytes only at end of file.
            return (
                FileBuildRelation.STRICT_PREFIX
                if len(staged) < len(planned)
                else FileBuildRelation.DIVERGED
            )

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
        identity, info = self._open_and_pin(
            lambda: self._backend.open_regular_nofollow(parent_fd, leaf),
            leaf,
            stat.S_ISREG,
            "a regular file",
        )
        # Stream from the PINNED descriptor: one descriptor per identity, and any
        # failure below leaves it owned by the pass rather than orphaned.
        pinned = self._pins[identity]
        os.lseek(pinned, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        length = 0
        while True:
            chunk = os.read(pinned, _READ_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            length += len(chunk)
            if sink_fd is not None:
                _write_all(sink_fd, chunk)
        return ObservedFile(
            state=FileState(
                content_hash="sha256:" + digest.hexdigest(),
                mode=stat.S_IMODE(info.st_mode),
                byte_len=length,
            ),
            identity=identity,
        )

    def _observe_directory(
        self, parent_fd: int, leaf: str, modeled: frozenset[str]
    ) -> ObservedDirectory:
        identity, info = self._open_and_pin(
            lambda: self._backend.open_child_directory(parent_fd, leaf),
            leaf,
            stat.S_ISDIR,
            "a directory",
        )
        with translated_lookup(f"enumerating {leaf!r}"):
            present = os.listdir(self._pins[identity])
        return ObservedDirectory(
            state=DirectoryState(mode=stat.S_IMODE(info.st_mode)),
            identity=identity,
            has_unmodeled_child=any(name not in modeled for name in present),
        )

    def _observe_symlink(self, parent_fd: int, leaf: str) -> ObservedSymlink:
        with translated_lookup(f"fingerprinting symlink {leaf!r}"):
            info, target = self._backend.symlink_fingerprint(parent_fd, leaf)
        # No descriptor and no identity: O_NOFOLLOW fails by design on a symlink leaf,
        # so there is nothing to be coherent about (design §6.2).
        return ObservedSymlink(
            state=SymlinkState(target=target, mode=stat.S_IMODE(info.st_mode))
        )

    def _open_and_pin(
        self, opener, leaf: str, predicate, description: str
    ) -> tuple[EntryIdentity, os.stat_result]:
        """Open, confirm the kind, and transfer ownership -- or close and raise."""
        with translated_lookup(f"opening {leaf!r}"):
            fd = opener()
        try:
            info = os.fstat(fd)
            if not predicate(info.st_mode):
                raise PreconditionRefused(
                    f"{leaf!r} stopped being {description} between lookup and open"
                )
        except BaseException:
            os.close(fd)
            raise
        return self._pin(info, fd), info

    def _pin(self, info: os.stat_result, fd: int) -> EntryIdentity:
        """Takes ownership of `fd` unconditionally: it is retained or closed here."""
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


def _read_exactly(fd: int, size: int) -> bytes:
    """Read up to `size`, returning short only at end of file."""
    parts: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.read(fd, remaining)
        if not chunk:
            break
        parts.append(chunk)
        remaining -= len(chunk)
    return b"".join(parts)


def _write_all(fd: int, chunk: bytes) -> None:
    view = memoryview(chunk)
    while view:
        view = view[os.write(fd, view) :]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd python && uv run pytest tests/test_fs_observe.py -q`
Expected: PASS, 23 tests.

- [ ] **Step 5: Run the full gate set**

Run: `cd python && uv run ruff format && uv run ruff check && uv run pyright && uv run pytest -q`
Expected: all green, no regressions.

- [ ] **Step 6: Commit**

```bash
git add python/src/atoms/fs/observe.py python/tests/test_fs_observe.py
git commit -m "feat(observe): add the coherent observation pass and its token pin"
```

---

## Task 2: The `referenced_digests` repair

**Files:**
- Modify: `python/src/atoms/store/records.py:412`
- Modify: `python/tests/test_store_records.py:239`

**Interfaces:**
- Produces: `referenced_digests(spec) -> tuple[tuple[str, int], ...]` covering every `FileState` the
  spec states — both surfaces **and** every effect occurrence — still as pairs.

This lands before capture because the staging set is exactly what this helper returns. It has no
dependency on Task 1.

**The helper has two callers of opposite polarity, and widening it moves both.** This plan
originally reasoned about only one. `connection.py:552` is an **admission ceiling** — a promoted
digest must be *in* the set, so widening admits an intermediate postimage that was previously
refused. `records.py:492` is a **presence floor** — every digest *in* the set must have a blob
row, so widening makes those same bytes *mandatory* for coherence. Ruled during execution:
**one helper, and the floor widens with it.** A record whose effects name bytes with no blob row
cannot be executed by A7, so flagging it is the coherence check doing its job, not collateral
damage. The measured blast radius is one fixture — `non_compiling_spec()` declares `a.txt` ABSENT
in both surfaces while its `CreateFileNoClobber` carries `post=file_state(b"after")`, a postimage
in no surface — and Step 5 below repairs it. Nothing else in the suite moves.

- [ ] **Step 1: Write the failing test**

In `python/tests/test_store_records.py`, rename
`test_referenced_digests_include_initial_and_final_file_surfaces` to
`test_referenced_digests_include_every_declared_file_state`, then add:

```python
def _state(payload: bytes, byte_len: int | None = None):
    import hashlib

    from atoms.core.fingerprint import FileState

    return FileState(
        content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
        mode=0o644,
        byte_len=len(payload) if byte_len is None else byte_len,
    )


def test_referenced_digests_include_an_intermediate_postimage():
    """Measured: `ReplaceFile(p, A->B)` + `ReplaceFile(p, B->C)` puts B in neither surface.

    The initial surface has A and the final has C. Before this repair the helper missed B
    entirely, so `connection.py`'s barrier refused a promoted B as unreferenced -- and not
    promoting it would leave A7 without the bytes it must publish.
    """
    from atoms.core.effects import ReplaceFile
    from atoms.core.spec import build_spec

    a, b, c = _state(b"aaa"), _state(b"bbb"), _state(b"ccc")
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

    assert {digest for digest, _ in referenced_digests(spec)} == {
        a.content_hash,
        b.content_hash,
        c.content_hash,
    }


def test_referenced_digests_keep_conflicting_lengths_as_distinct_pairs():
    """Measured: `compile_spec` accepts one content_hash at two byte_len values.

    Collapsing to digests would erase the contradiction capture must detect (A6 design
    §7.2), in the one helper positioned to see every declared FileState at once.
    """
    from atoms.core.effects import ReplaceFile
    from atoms.core.spec import build_spec

    honest = _state(b"aaa")
    liar = _state(b"aaa", byte_len=99)
    other = _state(b"zzz")
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

    assert sorted(
        n for d, n in referenced_digests(spec) if d == honest.content_hash
    ) == [3, 99]
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
        for occurrence in occurrences(effect):
            states.extend((occurrence.pre, occurrence.post))
    return tuple(sorted({
        (state.content_hash, state.byte_len)
        for state in states
        if isinstance(state, FileState)
    }))
```

Add `occurrences` to the existing `from atoms.core.effects import ...` at the top of the module.
Measured: the helper is named `occurrences` and is a `singledispatch` returning
`Occurrence(path, pre, post, role)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd python && uv run pytest tests/test_store_records.py -q`
Expected: PASS.

- [ ] **Step 5: Repair the one corruption fixture the widened floor moves**

`CROSS_ROW_CASES`' `RULE_SPEC_COMPILES` row plants `non_compiling_spec()`, whose
`CreateFileNoClobber` states `post=file_state(b"after")` while both surfaces say `ABSENT`. The
record it overwrites was committed from `_only_spec()`, which references nothing and therefore
wrote no blob. Widened, the planted spec references `digest_of(b"after")` with no blob row, so
`coherence_findings` returns two findings and `test_cross_row_corruption_refuses_on_load` fails its
`assert len(findings) == 1`.

That assertion is the matrix's whole point: one planted corruption, one finding, so each row proves
its own rule fires and no other. Keep it. The fixture, not the assertion, is what is now
incomplete — it must leave the record coherent in every respect *except* the rule under test, and
that now includes the blob row its planted spec references. Give the row's `corrupt` lambda a named
function that plants the spec and then inserts the blob:

```python
def _plant_non_compiling_spec(raw) -> None:
    """The planted spec states a postimage in neither surface, so it references a
    digest the committed record never wrote. Insert the row it needs: this case
    corrupts `spec_json_compiles` and nothing else."""
    _plant_spec_json(raw, canonical_json(non_compiling_spec()), CREATE_FILE_ROW)
    raw.execute(
        "INSERT INTO blob VALUES (?, ?)",
        (digest_of(b"after"), len(b"after")),
    )
```

and reference it in `CROSS_ROW_CASES` in place of the inline lambda:

```python
    (RULE_SPEC_COMPILES, _only_spec, _plant_non_compiling_spec),
```

`blob` is `(digest TEXT PRIMARY KEY, byte_len INTEGER NOT NULL CHECK (byte_len >= 0))`
(`schema.py:62-65`).

- [ ] **Step 6: Run the full suite**

`python/tests/coordinator_child.py:48` already unpacks `(digest, byte_len)` pairs and needs no change.

Run: `cd python && uv run pytest -q`
Expected: all green. Exactly two parametrizations moved —
`test_cross_row_corruption_refuses_on_load[spec_json_compiles-…]` and
`test_every_reachable_cross_row_rule_refuses_on_a_write[spec_json_compiles-…]` — and Step 5 repairs
both. **If any other store or coordinator test fails because more digests are referenced, stop and
report it**; that would mean something beyond this fixture was relying on the narrow set.

- [ ] **Step 7: Commit**

```bash
git add python/src/atoms/store/records.py python/tests/test_store_records.py
git commit -m "fix(store): reference every declared FileState, not only the two surfaces"
```

---

## Task 3: `DescriptorTable` — the walk, re-validation, and coherent blockers

**Files:**
- Modify: `python/src/atoms/fs/resolve.py`
- Create: `python/src/atoms/coordinator/descriptors.py`
- Create: `python/tests/capture_support.py`
- Create: `python/tests/test_coordinator_descriptors.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True, slots=True) class WalkStop:` — `node: TopologyNode`, `path: str`,
    `parent_fd: int`, `component: str`, `observed: ObservedEntry`
  - `class DescriptorTable:` — `fd_for(self, node: TopologyNode) -> int`,
    `is_unreachable(self, node: TopologyNode) -> bool`, `stops: tuple[WalkStop, ...]`,
    `close(self)`, `__enter__`, `__exit__`
  - `_build_descriptor_table(lease, approved, workspace, observation) -> DescriptorTable`

Two rulings from the design shape this task. **No errno selects a state branch** — the walk observes
the blocker's actual kind through the same `Observation` pass and reports the `ObservedEntry`, leaving
adjudication to Task 4. And **a planned directory is looked up, not assumed** — recording it as absent
without a lookup would mean a matching file or symlink blocker is never reported at all.

- [ ] **Step 1: Make the filesystem-type helper public**

In `python/src/atoms/fs/resolve.py`, rename `_filesystem_type` to `filesystem_type_of` and update its
two in-module call sites (`observe_child`, `observe_work_child`).

Run: `cd python && grep -rn "_filesystem_type" src/ tests/ && echo FOUND || echo CLEAN`
Expected: `CLEAN`.

- [ ] **Step 2: Write the shared builders**

Create `python/tests/capture_support.py`:

```python
"""Builders shared by the A6 capture tiers.

Plain functions, not fixtures: the fixture-registry guard requires every fixture to live
in `tests/conftest.py`, and these are values a test constructs rather than resources a
test needs torn down. Same rule `tests/coordinator_support.py` follows.
"""

from __future__ import annotations

import hashlib
import io
import os

from atoms.core.compiler import CompiledSpec, compile_spec
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    ReplaceFile,
)
from atoms.core.fingerprint import ABSENT, DirectoryState, FileState, SymlinkState
from atoms.core.spec import TransactionSpec, build_spec
from atoms.coordinator.lease import Lease

DIRECTORY_POST = DirectoryState(mode=0o755)
BEFORE = b"before"
AFTER = b"after"


def digest_of(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def state_of(payload: bytes, mode: int = 0o644) -> FileState:
    return FileState(content_hash=digest_of(payload), mode=mode, byte_len=len(payload))


def write_project_file(
    lease: Lease, path: str, payload: bytes, mode: int = 0o644
) -> None:
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


def blocker_spec(pre) -> TransactionSpec:
    """`DeletePath("p")`, `CreateDirectory("p")`, `CreateFileNoClobber("p/q")`.

    `p`'s timeline is continuous (FILE -> ABSENT -> DIRECTORY) and `p/q` is absent
    precisely BECAUSE `p` is a file -- the shape design §8.2 exists for. `pre` is either a
    FileState or a SymlinkState, which selects the branch under test.
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


def approved_blocked(lease: Lease, pre):
    from atoms.coordinator.admission import admit

    return admit(lease, compile_spec(blocker_spec(pre)))


def delete_symlink_spec(target: str = "elsewhere") -> TransactionSpec:
    pre = SymlinkState(target=target, mode=0o777)
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "c" * 64,
        initial_surface={"link": pre},
        final_surface={"link": ABSENT},
        effects=[DeletePath(effect_id="e1", path="link", pre=pre)],
    )


def approved_delete_symlink(lease: Lease, target: str = "elsewhere"):
    from atoms.coordinator.admission import admit

    root_fd = lease._binding.project_root_fd
    try:
        os.unlink("link", dir_fd=root_fd)
    except FileNotFoundError:
        pass
    os.symlink(target, "link", dir_fd=root_fd)
    return admit(lease, compile_spec(delete_symlink_spec(target)))


def superseded_spec() -> TransactionSpec:
    """`DeletePath("a.txt")` then `CreateFileNoClobber("a.txt")`.

    Committed, this is the committed-cleanup shape: e1's tombstone is retained scratch
    awaiting removal while the live path already holds the final surface. It mirrors
    `recovery_support.make_committed_superseded_cleanup_case("delete_then_create")`,
    but over real approved scratch names and real files.
    """
    pre, post = state_of(BEFORE), state_of(AFTER)
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "d" * 64,
        initial_surface={"a.txt": pre},
        final_surface={"a.txt": post},
        effects=[
            DeletePath(effect_id="e1", path="a.txt", pre=pre),
            CreateFileNoClobber(effect_id="e2", path="a.txt", post=post),
        ],
    )


def approved_superseded(lease: Lease):
    """Approved with the FINAL surface already live, as after a committed run."""
    from atoms.coordinator.admission import admit

    write_project_file(lease, "a.txt", BEFORE)
    approved = admit(lease, compile_spec(superseded_spec()))
    write_project_file(lease, "a.txt", AFTER)
    return approved


class DictPayloads:
    """A PayloadSource over an in-memory map.

    `open` returns a FRESH stream each call, which capture owns and closes. A source that
    handed back a shared or already-read stream would make a second staging attempt
    silently produce a short blob. An unknown digest raises `KeyError` -- the one signal
    capture translates into `ProtocolError`.
    """

    def __init__(self, contents: dict[str, bytes]) -> None:
        self._contents = contents
        self.requested: list[str] = []

    def open(self, digest: str) -> io.BytesIO:
        self.requested.append(digest)
        return io.BytesIO(self._contents[digest])
```

- [ ] **Step 3: Write the failing test**

Create `python/tests/test_coordinator_descriptors.py`:

```python
"""A6 tier 2 -- the descriptor table (design §5)."""

from __future__ import annotations

import errno
import os

import pytest

from atoms.core.errors import PreconditionRefused
from atoms.core.recovery.model import ObservedAbsent, ObservedFile, ObservedSymlink
from atoms.core.recovery.snapshot import ProjectRoot, TopologyDirectory, WorkRoot
from atoms.fs.linux import LinuxBackend
from atoms.fs.observe import Observation
from atoms.fs.topology import ApprovedPlannedDirectory
from tests.capture_support import BEFORE, approved_replace, state_of, write_project_file
from tests.coordinator_support import compiled_creating_a_directory


def _table(lease, approved, workspace, observation):
    from atoms.coordinator.descriptors import _build_descriptor_table

    return _build_descriptor_table(lease, approved, workspace, observation)


def test_the_table_holds_one_descriptor_per_approved_directory(leased):
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            with Observation(LinuxBackend()) as observation:
                with _table(lease, approved, workspace, observation) as table:
                    # Measured: this spec's directories are ProjectRoot and
                    # TopologyDirectory(0) for `d`.
                    assert isinstance(table.fd_for(ProjectRoot()), int)
                    assert isinstance(table.fd_for(TopologyDirectory(node_id=0)), int)


def test_the_root_descriptors_are_borrowed_and_survive_close(leased):
    """§5.5: ProjectBinding and Workspace own theirs; the table closes only its own."""
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            with Observation(LinuxBackend()) as observation:
                with _table(lease, approved, workspace, observation) as table:
                    assert table.fd_for(ProjectRoot()) == lease._binding.project_root_fd
            assert os.fstat(lease._binding.project_root_fd).st_ino > 0
            assert os.fstat(workspace.staging_fd).st_ino > 0


def test_closing_the_table_releases_only_what_it_opened(leased):
    from atoms.coordinator.prepare import open_workspace
    from tests.fs_support import descriptor_count

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            with Observation(LinuxBackend()) as observation:
                before = descriptor_count()
                table = _table(lease, approved, workspace, observation)
                assert descriptor_count() > before
                table.close()
                assert descriptor_count() == before


def test_the_work_root_is_present_only_when_the_topology_has_one(leased):
    from atoms.coordinator.admission import admit
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        without = approved_replace(lease)
        with open_workspace(lease, without) as workspace:
            with Observation(LinuxBackend()) as observation:
                with _table(lease, without, workspace, observation) as table:
                    assert without.work_base is None
                    with pytest.raises(KeyError):
                        table.fd_for(WorkRoot())

    with leased() as lease:
        with_work = admit(lease, compiled_creating_a_directory(lease))
        with open_workspace(lease, with_work) as workspace:
            with Observation(LinuxBackend()) as observation:
                with _table(lease, with_work, workspace, observation) as table:
                    assert with_work.work_base is not None
                    assert table.fd_for(WorkRoot()) == workspace.work_fd


def test_the_work_root_baseline_is_its_own_record_not_the_work_base(leased):
    """`workspace.work_fd` names work/<txid>; `approved.work_base` describes work/.

    A4b records the child as `ApprovedPlannedDirectory(WorkRoot(),
    inherited_constraints(work_base.constraints, filesystem_type))` (`approval.py:155`,
    `topology.py:180`), so the child's baseline lives in `approved.directories` like every
    other node's, and the parent's retained facts are the wrong thing to compare a child
    descriptor against.

    On ext4 the two carry equal values, so the only way to make the choice observable is
    to make them differ -- which means editing the proof. `ProjectApprovedSpec` is
    token-guarded so that nothing outside A4b constructs one, and `dataclasses.replace`
    refuses for the same reason (`approval.py:69-84`); a sabotage fixture is the one place
    that has to go around it. `object.__setattr__` is what the factory's own `__init__`
    uses, and each doctored proof is discarded with its lease.

    Both directions are asserted, because either alone is satisfiable by the wrong code:
    a wrong parent must NOT refuse, and a wrong child record MUST.
    """
    import dataclasses

    from atoms.coordinator.admission import admit
    from atoms.coordinator.prepare import open_workspace
    from atoms.fs.lookup import inherited_constraints
    from atoms.fs.resolve import filesystem_type_of
    from atoms.fs.topology import ApprovedWorkBase

    # Arm 1: sabotage the PARENT's retained facts. The table must still build.
    with leased() as lease:
        approved = admit(lease, compiled_creating_a_directory(lease))
        assert approved.work_base is not None
        record = next(e for e in approved.directories if e.node == WorkRoot())
        assert type(record) is ApprovedPlannedDirectory
        assert record.constraints == inherited_constraints(
            approved.work_base.constraints, filesystem_type_of(lease._binding)
        )

        wrong = dataclasses.replace(record.constraints, name_max=8)
        assert wrong != record.constraints
        with open_workspace(lease, approved) as workspace:
            object.__setattr__(
                approved,
                "work_base",
                ApprovedWorkBase(
                    identity=approved.work_base.identity, constraints=wrong
                ),
            )
            with Observation(LinuxBackend()) as observation:
                with _table(lease, approved, workspace, observation) as table:
                    assert table.fd_for(WorkRoot()) == workspace.work_fd

    # Arm 2: sabotage the CHILD's own record. The table must refuse -- proving the record
    # is read, and not skipped merely because a planned directory has no identity.
    with leased() as lease:
        approved = admit(lease, compiled_creating_a_directory(lease))
        record = next(e for e in approved.directories if e.node == WorkRoot())
        wrong = dataclasses.replace(record.constraints, name_max=8)
        with open_workspace(lease, approved) as workspace:
            object.__setattr__(
                approved,
                "directories",
                tuple(
                    ApprovedPlannedDirectory(node=WorkRoot(), constraints=wrong)
                    if entry.node == WorkRoot()
                    else entry
                    for entry in approved.directories
                ),
            )
            with Observation(LinuxBackend()) as observation:
                with pytest.raises(PreconditionRefused, match="name_max=8"):
                    _table(lease, approved, workspace, observation)


def test_a_replaced_directory_refuses_on_identity(leased):
    """Ledger #19: compare against the approved baseline, never reapprove."""
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        root_fd = lease._binding.project_root_fd
        os.rename("d", "d-moved", src_dir_fd=root_fd, dst_dir_fd=root_fd)
        os.mkdir("d", dir_fd=root_fd)

        with open_workspace(lease, approved) as workspace:
            with Observation(LinuxBackend()) as observation:
                with pytest.raises(PreconditionRefused, match="identity"):
                    _table(lease, approved, workspace, observation)


def test_the_project_root_is_revalidated_too(leased, monkeypatch):
    """§5.3: retention is not discharge.

    The binding holds the root descriptor across approval so its identity cannot drift,
    but lookup_proof and name_max are mutable directory properties and ledger #19's rule
    is that a resolved fact is compared against its approved baseline before being
    relied on. A held descriptor is not an exception the ledger grants.
    """
    from atoms.coordinator import descriptors
    from atoms.coordinator.prepare import open_workspace
    from atoms.fs.lookup import DirectoryConstraints, LookupProof

    with leased() as lease:
        approved = approved_replace(lease)
        drifted = DirectoryConstraints(
            lookup_proof=LookupProof.EXACT_BYTES, name_max=64
        )
        monkeypatch.setattr(
            descriptors, "read_lookup_constraints", lambda fd, kind: drifted
        )
        with open_workspace(lease, approved) as workspace:
            with Observation(LinuxBackend()) as observation:
                with pytest.raises(PreconditionRefused, match="constraints"):
                    _table(lease, approved, workspace, observation)


def test_an_absent_planned_directory_is_a_stop_with_an_absent_observation(leased):
    from atoms.coordinator.admission import admit
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = admit(lease, compiled_creating_a_directory(lease))
        with open_workspace(lease, approved) as workspace:
            with Observation(LinuxBackend()) as observation:
                with _table(lease, approved, workspace, observation) as table:
                    (stop,) = [s for s in table.stops if s.component == "d"]
                    assert stop.path == "d"
                    assert type(stop.observed) is ObservedAbsent


def test_an_occupied_planned_directory_reports_what_occupies_it(leased):
    """A planned directory is LOOKED UP, not assumed absent.

    Recording it as missing without a lookup would mean a matching file or symlink
    blocker is never reported, and §8.2's whole branch would be unreachable.
    """
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        write_project_file(lease, "p", BEFORE)
        approved = approved_blocked_file(lease)
        with open_workspace(lease, approved) as workspace:
            with Observation(LinuxBackend()) as observation:
                with _table(lease, approved, workspace, observation) as table:
                    (stop,) = [s for s in table.stops if s.component == "p"]
                    assert type(stop.observed) is ObservedFile
                    assert stop.observed.state == state_of(BEFORE)


def test_a_fifo_blocker_is_not_reported_as_a_regular_file(leased):
    """ENOTDIR does not distinguish a regular file from a socket, FIFO, or device node.

    An errno-to-kind table would have called this one REGULAR_FILE, and §8.2's
    verification against the declared FileState would then compare a fabricated kind.
    """
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        write_project_file(lease, "p", BEFORE)
        approved = approved_blocked_file(lease)
        root_fd = lease._binding.project_root_fd
        os.unlink("p", dir_fd=root_fd)
        os.mkfifo("p", 0o644, dir_fd=root_fd)

        with open_workspace(lease, approved) as workspace:
            with Observation(LinuxBackend()) as observation:
                # The observer refuses a kind no declared state can describe, rather
                # than inventing one from the errno.
                with pytest.raises(PreconditionRefused, match="neither"):
                    _table(lease, approved, workspace, observation)


def test_a_symlink_blocker_is_reported_as_a_symlink(leased):
    from atoms.core.fingerprint import SymlinkState
    from atoms.coordinator.prepare import open_workspace
    from tests.capture_support import approved_blocked

    with leased() as lease:
        root_fd = lease._binding.project_root_fd
        os.symlink("elsewhere", "p", dir_fd=root_fd)
        approved = approved_blocked(lease, SymlinkState(target="elsewhere", mode=0o777))
        with open_workspace(lease, approved) as workspace:
            with Observation(LinuxBackend()) as observation:
                with _table(lease, approved, workspace, observation) as table:
                    (stop,) = [s for s in table.stops if s.component == "p"]
                    assert type(stop.observed) is ObservedSymlink


def test_a_vanished_existing_directory_refuses_rather_than_becoming_a_stop(leased):
    """Only PLANNED directories produce stops.

    A `TopologyDirectory` has no declared state, so §8's branches could never rule on
    one. Approval said it exists; if it no longer opens, that is drift and it refuses.
    """
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        root_fd = lease._binding.project_root_fd
        os.unlink("d/f.txt", dir_fd=root_fd)
        os.rmdir("d", dir_fd=root_fd)

        with open_workspace(lease, approved) as workspace:
            with Observation(LinuxBackend()) as observation:
                with pytest.raises(PreconditionRefused, match="namespace"):
                    _table(lease, approved, workspace, observation)


def test_an_undefined_errno_from_the_walk_propagates(leased, monkeypatch):
    """§9.1: only errnos with a defined domain meaning translate.

    Swallowing every OSError would report a failing disk as drift.
    """
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        real_open = LinuxBackend.open_child_directory

        def flaky(self, parent_fd, name):
            if name == "d":
                raise OSError(errno.EIO, "I/O error")
            return real_open(self, parent_fd, name)

        monkeypatch.setattr(LinuxBackend, "open_child_directory", flaky)
        with open_workspace(lease, approved) as workspace:
            with Observation(LinuxBackend()) as observation:
                with pytest.raises(OSError) as caught:
                    _table(lease, approved, workspace, observation)
                assert caught.value.errno == errno.EIO


def test_an_occupied_planned_directory_stops_the_walk_for_its_descendants(leased):
    """The occupied branch must stop too.

    A file holds no directory entries, so `p/q` is absent -- which is exactly §8.2's
    inference. Leaving the node unstopped would make `p/q` neither resolvable nor
    unreachable, and capture would raise ProtocolError on the case §8.2 accepts.
    """
    from atoms.core.recovery.snapshot import PersistentNode
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        write_project_file(lease, "p", BEFORE)
        approved = approved_blocked_file(lease)
        with open_workspace(lease, approved) as workspace:
            with Observation(LinuxBackend()) as observation:
                with _table(lease, approved, workspace, observation) as table:
                    assert table.is_unreachable(PersistentNode(path="p"))
                    assert table.is_unreachable(PersistentNode(path="p/q"))


def test_a_node_beneath_a_stop_is_unreachable_not_missing(leased):
    """Two different facts. The table must not conflate them.

    A node proved unreachable beneath a verified stop needs no observation; a node simply
    absent from the table is an internal defect, and returning None for both would let
    the second pass silently as the first.
    """
    from atoms.coordinator.admission import admit
    from atoms.coordinator.prepare import open_workspace
    from atoms.core.recovery.snapshot import PersistentNode

    with leased() as lease:
        approved = admit(lease, compiled_creating_a_directory(lease))
        with open_workspace(lease, approved) as workspace:
            with Observation(LinuxBackend()) as observation:
                with _table(lease, approved, workspace, observation) as table:
                    assert table.is_unreachable(PersistentNode(path="d"))
                    assert not table.is_unreachable(ProjectRoot())


def approved_blocked_file(lease):
    from tests.capture_support import approved_blocked

    return approved_blocked(lease, state_of(BEFORE))
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd python && uv run pytest tests/test_coordinator_descriptors.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'atoms.coordinator.descriptors'`.

- [ ] **Step 5: Write the implementation**

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

Nothing here classifies. Only a PLANNED directory produces a `WalkStop`, recording the
ObservedEntry actually found there, and capture adjudicates that against the timeline's
first declared state. No errno is ever mapped to a kind: ENOTDIR does not distinguish a
regular file from a socket, FIFO, or device node.

An approved-EXISTING directory that no longer opens is drift, not a stop -- a
`TopologyDirectory` has no declared state for §8 to rule against -- so it refuses through
the narrow errno translation, and an undefined errno such as EIO propagates as itself.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from atoms.core.errors import PreconditionRefused, ProtocolError
from atoms.core.recovery.model import ObservedEntry
from atoms.core.recovery.snapshot import ProjectRoot, TopologyNode, WorkRoot
from atoms.coordinator.admission import _parent_paths
from atoms.coordinator.lease import Lease
from atoms.fs.approval import ProjectApprovedSpec
from atoms.fs.lookup import read_lookup_constraints
from atoms.fs.observe import Observation, translated_lookup
from atoms.fs.resolve import FilesystemIdentity, filesystem_type_of
from atoms.fs.topology import ApprovedExistingDirectory, ApprovedPlannedDirectory
from atoms.fs.volume import read_mount_id
from atoms.store.workspace import Workspace


@dataclass(frozen=True, slots=True)
class WalkStop:
    """Where the walk stopped, and what was actually there.

    `observed` is an `ObservedAbsent` when nothing occupies the name, and the real
    observed entry otherwise. Neither is adjudicated here: capture verifies both against
    the timeline's first declared state (design §8).
    """

    node: TopologyNode
    path: str
    parent_fd: int
    component: str
    observed: ObservedEntry


class DescriptorTable:
    """A live resource. Borrows the roots; owns only what it opened.

    It outlives capture: §6 requires the engine to hold a descriptor to the project root
    for the transaction's lifetime, and §9.5 hands each published directory's descriptor
    down to its descendants. A table that died at capture's return would force A7 to
    re-resolve, reopening the race the whole section exists to close.
    """

    __slots__ = ("_closed", "_fds", "_owned", "_unreachable", "stops")

    def __init__(
        self,
        *,
        fds: dict[TopologyNode, int],
        owned: tuple[int, ...],
        stops: tuple[WalkStop, ...],
        unreachable: frozenset[TopologyNode],
    ) -> None:
        self._fds = fds
        self._owned = owned
        self._unreachable = unreachable
        self.stops = stops
        self._closed = False

    def fd_for(self, node: TopologyNode) -> int:
        if self._closed:
            raise ProtocolError("this descriptor table is closed")
        return self._fds[node]

    def is_unreachable(self, node: TopologyNode) -> bool:
        """Proved to lie beneath a stop -- distinct from merely absent from the table."""
        if self._closed:
            raise ProtocolError("this descriptor table is closed")
        return node in self._unreachable

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


def _build_descriptor_table(
    lease: Lease,
    approved: ProjectApprovedSpec,
    workspace: Workspace,
    observation: Observation,
) -> DescriptorTable:
    """Package-private: reached only through `capture_initial_surface`.

    Kept private deliberately. `test_no_unregistered_public_function_accepts_the_proof`
    asserts that every public coordinator function annotating a ProjectApprovedSpec is a
    registered transaction-stage entry point, and this is a step inside one, not an
    entry of its own.
    """
    binding = lease._binding
    filesystem_type = filesystem_type_of(binding)
    expected_mount = binding.evidence.mount_id
    paths = _parent_paths(approved)
    # One map, both kinds. Every descriptor-bearing node has an approved record in
    # `approved.directories`, including WorkRoot: A4b builds the topology with
    # ApprovedPlannedDirectory(WorkRoot(), inherited_constraints(work_base.constraints,
    # filesystem_type)) (`approval.py:155`, `topology.py:180`). `approved.work_base`
    # describes metadata_root/work -- the PARENT of work/<txid>, which is what
    # `workspace.work_fd` names -- so it is the wrong baseline to compare against here.
    directories = {entry.node: entry for entry in approved.directories}
    planned = {
        node
        for node, entry in directories.items()
        if type(entry) is ApprovedPlannedDirectory
    }

    fds: dict[TopologyNode, int] = {}
    owned: list[int] = []
    stops: list[WalkStop] = []
    stopped_nodes: set[TopologyNode] = set()

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
        baseline = directories.get(node)
        if baseline is None:
            raise ProtocolError(
                f"{node!r} bears a descriptor but has no record in the proof's approved "
                "directories; the topology and the approval disagree"
            )
        if constraints != baseline.constraints:
            raise PreconditionRefused(
                f"{node!r} has constraints {constraints}, not the approved "
                f"{baseline.constraints}"
            )
        # A planned directory has no approved identity -- it did not exist at approval,
        # so there is nothing to compare an inode against. Constraints and mount are the
        # whole of its baseline.
        if type(baseline) is ApprovedExistingDirectory:
            info = os.fstat(fd)
            actual = FilesystemIdentity(device=info.st_dev, inode=info.st_ino)
            if actual != baseline.identity:
                raise PreconditionRefused(
                    f"{node!r} has identity {actual}, not the approved "
                    f"{baseline.identity}; approval is not reapproved here"
                )

    try:
        # Root 1: the project root, borrowed. Retention is not discharge -- lookup_proof
        # and name_max are mutable directory properties, so it is re-validated too.
        validate(binding.project_root_fd, ProjectRoot())
        fds[ProjectRoot()] = binding.project_root_fd

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
                # A parent may lack a descriptor for exactly one reason: it stopped.
                # `_walk_order` is shallowest-first, so any other absence is a walk-order
                # defect, and calling it a stop would manufacture an absence inference
                # out of a bug -- §8's whole basis is a *verified* blocker.
                if parent not in stopped_nodes:
                    raise ProtocolError(
                        f"{node!r} was reached before its parent {parent!r} was opened "
                        "or stopped; the walk is not visiting parents first"
                    )
                stopped_nodes.add(node)
                continue
            component = _component(paths, parent, node)
            if node in planned:
                # Looked up, not assumed. An occupied planned name must reach §8.2.
                observed = observation.observe(
                    parent_fd, component, modeled=_modeled_children(paths, node)
                )
                stops.append(
                    WalkStop(
                        node=node,
                        path=paths[node],
                        parent_fd=parent_fd,
                        component=component,
                        observed=observed,
                    )
                )
                # BOTH outcomes stop the walk. Absent, nothing is below it; occupied by
                # a file or symlink, nothing is below it either -- neither holds
                # directory entries, which is the whole basis of §8.2's inference. A
                # planned node left unstopped would leave its descendants neither
                # resolvable nor unreachable, and capture would raise ProtocolError on
                # the very case §8.2 exists to accept.
                stopped_nodes.add(node)
                continue
            # An approved-EXISTING directory that no longer opens is drift, not a stop.
            # There is no declared state for a TopologyDirectory to be adjudicated
            # against, so §8's branches could never rule on it; it refuses here.
            with translated_lookup(f"opening {component!r} for {node!r}"):
                fd = binding.backend.open_child_directory(parent_fd, component)
            owned.append(fd)
            validate(fd, node)
            fds[node] = fd
    except BaseException:
        for fd in owned:
            os.close(fd)
        raise

    unreachable = _closure(approved, stopped_nodes)
    return DescriptorTable(
        fds=fds, owned=tuple(owned), stops=tuple(stops), unreachable=unreachable
    )


def _modeled_children(paths: dict[TopologyNode, str], node: TopologyNode) -> frozenset[str]:
    """Every declared name directly beneath `node`, for occupancy evidence."""
    prefix = paths[node]
    base = f"{prefix}/" if prefix else ""
    return frozenset(
        path[len(base) :]
        for path in paths.values()
        if path.startswith(base) and path != prefix and "/" not in path[len(base) :]
    )


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


def _closure(
    approved: ProjectApprovedSpec, stopped: set[TopologyNode]
) -> frozenset[TopologyNode]:
    """Every node at or beneath a stop. The topology is a tree, so this terminates."""
    unreachable = set(stopped)
    changed = True
    while changed:
        changed = False
        for edge in approved.topology.parents:
            if edge.parent in unreachable and edge.node not in unreachable:
                unreachable.add(edge.node)
                changed = True
    return frozenset(unreachable)


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

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd python && uv run pytest tests/test_coordinator_descriptors.py -q`
Expected: PASS, 15 tests.

- [ ] **Step 7: Run the full gate set**

Run: `cd python && uv run ruff format && uv run ruff check && uv run pyright && uv run pytest -q`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add python/src/atoms/fs/resolve.py python/src/atoms/coordinator/descriptors.py \
        python/tests/capture_support.py python/tests/test_coordinator_descriptors.py
git commit -m "feat(coordinator): hold a re-validated descriptor table across the walk"
```

---

## Task 4: Capture — absence inference, staging, flush, and the manifest

**Files:**
- Create: `python/src/atoms/coordinator/capture.py`
- Create: `python/tests/test_coordinator_capture.py`
- Modify: `python/tests/test_fs_architecture.py` (register the entry point)

**Interfaces:**
- Produces:
  - `class PayloadSource(Protocol):` — `open(self, digest: str) -> IO[bytes]`, raising `KeyError` for
    an unknown digest
  - `require_one_length_per_digest(pairs) -> dict[str, int]`
  - `class Captured:` — `manifest: tuple[StagedBlob, ...]`, `descriptors: DescriptorTable`, `close()`,
    `__enter__`, `__exit__`
  - `capture_initial_surface(lease, approved, workspace, payloads) -> Captured`

Tasks 4-and-5 of the previous revision are merged: the payload contract had no independently green
commit, because its tests exercised an entry point that did not exist yet.

- [ ] **Step 1: Write the failing test**

Create `python/tests/test_coordinator_capture.py`:

```python
"""A6 tier 3 -- capture: absence, staging, flush, and refusals (design §7, §8, §9)."""

from __future__ import annotations

import itertools
import os

import pytest

from atoms.core.errors import PreconditionRefused, ProtocolError
from atoms.core.fingerprint import SymlinkState
from atoms.store.blobs import digest_to_leaf
from tests.capture_support import (
    AFTER,
    BEFORE,
    DictPayloads,
    approved_blocked,
    approved_delete_symlink,
    approved_replace,
    digest_of,
    state_of,
    write_project_file,
)
from tests.coordinator_support import compiled_creating_a_directory


def payloads_for_replace() -> DictPayloads:
    return DictPayloads({digest_of(AFTER): AFTER})


# --- the digest-length precheck (design §7.2) ---------------------------------------


def test_one_digest_at_two_lengths_refuses_before_anything_is_written():
    """Measured: `compile_spec` accepts the contradiction.

    The staging name is `digest_to_leaf(digest)` -- one name per digest -- so two lengths
    collide and whichever wrote second would publish a blob one effect's FileState
    disagrees with. `_preflight` catches it too, but only after both files exist.
    """
    from atoms.coordinator.capture import require_one_length_per_digest

    with pytest.raises(ProtocolError, match="two byte_len"):
        require_one_length_per_digest(
            (("sha256:" + "a" * 64, 3), ("sha256:" + "a" * 64, 99))
        )


def test_one_length_per_digest_passes_through():
    from atoms.coordinator.capture import require_one_length_per_digest

    pairs = (("sha256:" + "a" * 64, 3), ("sha256:" + "b" * 64, 99))
    assert require_one_length_per_digest(pairs) == {
        "sha256:" + "a" * 64: 3,
        "sha256:" + "b" * 64: 99,
    }


# --- absence inference (design §8) ---------------------------------------------------


def test_a_missing_ancestor_justifies_its_descendants_absence(leased):
    from atoms.coordinator.admission import admit
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = admit(lease, compiled_creating_a_directory(lease))
        with open_workspace(lease, approved) as workspace:
            with capture_initial_surface(
                lease, approved, workspace, payloads_for_replace()
            ) as captured:
                assert captured.manifest


def test_a_regular_file_blocker_matching_its_declared_state_justifies_absence(leased):
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        write_project_file(lease, "p", BEFORE)
        approved = approved_blocked(lease, state_of(BEFORE))
        with open_workspace(lease, approved) as workspace:
            with capture_initial_surface(
                lease, approved, workspace, payloads_for_replace()
            ):
                pass


def test_a_drifted_regular_file_blocker_refuses(leased):
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        write_project_file(lease, "p", BEFORE)
        approved = approved_blocked(lease, state_of(BEFORE))
        write_project_file(lease, "p", b"drifted")
        with open_workspace(lease, approved) as workspace:
            with pytest.raises(PreconditionRefused, match="declared"):
                with capture_initial_surface(
                    lease, approved, workspace, payloads_for_replace()
                ):
                    pass


def test_a_drifted_symlink_blocker_refuses(leased):
    """A drifted symlink is a symlink but not THIS symlink."""
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        root_fd = lease._binding.project_root_fd
        os.symlink("elsewhere", "p", dir_fd=root_fd)
        approved = approved_blocked(lease, SymlinkState(target="elsewhere", mode=0o777))
        os.unlink("p", dir_fd=root_fd)
        os.symlink("drifted", "p", dir_fd=root_fd)

        with open_workspace(lease, approved) as workspace:
            with pytest.raises(PreconditionRefused, match="declared"):
                with capture_initial_surface(
                    lease, approved, workspace, payloads_for_replace()
                ):
                    pass


def test_a_top_level_declared_absent_path_is_accepted(leased):
    """ABSENT is `AbsentState()`, not None.

    An observed-to-declared comparison that reached for a missing `.state` attribute
    would compare None against AbsentState and refuse every correct absent path.
    """
    from atoms.coordinator.admission import admit
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace
    from tests.coordinator_support import compiled_for

    with leased() as lease:
        approved = admit(lease, compiled_for(lease))  # CreateFileNoClobber d/f.txt
        with open_workspace(lease, approved) as workspace:
            with capture_initial_surface(
                lease, approved, workspace, payloads_for_replace()
            ) as captured:
                # Nothing to retain: the only declared initial state is ABSENT.
                assert {e.digest for e in captured.manifest} == {digest_of(AFTER)}


# --- staging (design §7) --------------------------------------------------------------


def test_a_preimage_is_staged_under_its_digest_leaf(leased):
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            with capture_initial_surface(
                lease, approved, workspace, payloads_for_replace()
            ) as captured:
                names = {entry.name for entry in captured.manifest}
                assert digest_to_leaf(digest_of(BEFORE)) in names
                assert digest_to_leaf(digest_of(AFTER)) in names
                assert set(os.listdir(workspace.staging_fd)) == names


def test_a_drifted_preimage_refuses(leased):
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        write_project_file(lease, "d/f.txt", b"drifted")
        with open_workspace(lease, approved) as workspace:
            with pytest.raises(PreconditionRefused):
                with capture_initial_surface(
                    lease, approved, workspace, payloads_for_replace()
                ):
                    pass


def test_a_symlink_preimage_is_verified_and_not_staged(leased):
    """§7 step 3: a symlink retains no content.

    `referenced_digests` filters on FileState and never names one, and §10's rollback
    material for a symlink is the atomically transferred tombstone, which only A7 creates.
    """
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_delete_symlink(lease)
        with open_workspace(lease, approved) as workspace:
            with capture_initial_surface(
                lease, approved, workspace, DictPayloads({})
            ) as captured:
                assert captured.manifest == ()
                assert os.listdir(workspace.staging_fd) == []


def test_capture_composes_with_preparation(leased):
    """The whole seam: open_workspace -> capture -> prepare_transaction."""
    from atoms.core.recovery import TransactionState
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace, prepare_transaction

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            with capture_initial_surface(
                lease, approved, workspace, payloads_for_replace()
            ) as captured:
                prepare_transaction(lease, approved, workspace, captured.manifest)

        record = lease._store.read_active()
        assert record is not None
        assert record.txid == approved.txid
        assert record.state is TransactionState.PREPARED


def test_the_descriptor_table_outlives_capture(leased):
    """§5.5: the table is what A7 executes against."""
    from atoms.core.recovery.snapshot import ProjectRoot
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace, prepare_transaction

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            with capture_initial_surface(
                lease, approved, workspace, payloads_for_replace()
            ) as captured:
                prepare_transaction(lease, approved, workspace, captured.manifest)
                # Still live AFTER preparation -- this is the whole point.
                assert isinstance(captured.descriptors.fd_for(ProjectRoot()), int)


def test_a_stray_staging_entry_refuses_rather_than_surviving_success(leased):
    """§7.4 and criterion 10: on success, staging holds EXACTLY the manifest.

    `promote_staging` would refuse the stray later, but capture must not report success
    with an unrelated leaf still present.
    """
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            fd = os.open(
                "stray",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=workspace.staging_fd,
            )
            os.close(fd)
            with pytest.raises(PreconditionRefused, match="stray"):
                with capture_initial_surface(
                    lease, approved, workspace, payloads_for_replace()
                ):
                    pass


# --- durability (design §7.3) ---------------------------------------------------------


def test_every_staged_file_is_flushed_before_its_sink_closes(leased, monkeypatch):
    """§7.3 ordering. `promote_staging` flushes DIRECTORIES only, so nothing else makes
    the contents durable, and a lost flush is invisible until a crash.

    Events are keyed by GENERATION, not by file descriptor: numeric descriptors are
    reused, so a closed sink and a later one that happened to get the same number would
    be conflated and the ordering assertion would pass on a broken build.
    """
    from atoms.coordinator import capture as capture_module
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace
    from atoms.fs.linux import LinuxBackend

    generations: dict[int, int] = {}
    events: list[tuple[str, int]] = []
    counter = itertools.count()
    real_open_sink = capture_module._open_sink
    real_flush = LinuxBackend.flush_file
    real_close = os.close

    def spy_open_sink(workspace, name):
        fd = real_open_sink(workspace, name)
        generations[fd] = next(counter)
        events.append(("open", generations[fd]))
        return fd

    def spy_flush(self, fd):
        if fd in generations:
            events.append(("flush", generations[fd]))
        return real_flush(self, fd)

    def spy_close(fd):
        generation = generations.pop(fd, None)
        if generation is not None:
            events.append(("close", generation))
        return real_close(fd)

    monkeypatch.setattr(capture_module, "_open_sink", spy_open_sink)
    monkeypatch.setattr(LinuxBackend, "flush_file", spy_flush)
    monkeypatch.setattr(os, "close", spy_close)

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            with capture_initial_surface(
                lease, approved, workspace, payloads_for_replace()
            ) as captured:
                count = len(captured.manifest)
    monkeypatch.undo()

    opened = [g for kind, g in events if kind == "open"]
    assert len(opened) == count
    for generation in opened:
        sequence = [kind for kind, g in events if g == generation]
        assert sequence == ["open", "flush", "close"], (generation, sequence)


# --- payload contract (design §7.1, §9) -----------------------------------------------


def test_a_payload_whose_bytes_disagree_refuses(leased):
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        payloads = DictPayloads({digest_of(AFTER): b"not-after"})
        with open_workspace(lease, approved) as workspace:
            with pytest.raises(PreconditionRefused, match="hashes to"):
                with capture_initial_surface(lease, approved, workspace, payloads):
                    pass


def test_a_missing_payload_binding_is_a_protocol_error(leased):
    """§9: absent or malformed is a broken submission, not external state."""
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            with pytest.raises(ProtocolError, match="no binding"):
                with capture_initial_surface(
                    lease, approved, workspace, DictPayloads({})
                ):
                    pass


def test_a_payload_stream_error_propagates_as_itself(leased):
    """§9.1: only the missing-binding signal is translated.

    An `except Exception` around `open()` would convert EIO, ENOSPC, and outright bugs in
    the consumer's source into ProtocolError, which is exactly the overreach A5a's rule
    forbids.
    """
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    class Exploding:
        def open(self, digest):
            raise OSError(5, "EIO")

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            with pytest.raises(OSError) as caught:
                with capture_initial_surface(lease, approved, workspace, Exploding()):
                    pass
            assert caught.value.errno == 5


def test_a_payload_stream_yielding_text_refuses(leased):
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    class TextSource:
        def open(self, digest):
            import io

            return io.StringIO("not bytes")

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            with pytest.raises(ProtocolError, match="bytes"):
                with capture_initial_surface(lease, approved, workspace, TextSource()):
                    pass


@pytest.mark.parametrize("first", [None, ""])
def test_a_payload_stream_yielding_a_falsy_non_bytes_refuses(leased, first):
    """The type check must precede the falsiness check.

    `None` and `""` are both falsy, so a `if not chunk: break` placed first accepts a
    malformed stream as a clean end of file -- and for a declared EMPTY file that hashes
    to the empty digest at length 0 and validates. The bug is invisible in every test
    whose payload is non-empty.
    """
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    class FalsySource:
        def open(self, digest):
            class Stream:
                def read(self, size):
                    return first

                def close(self):
                    return None

            return Stream()

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            with pytest.raises(ProtocolError, match="bytes"):
                with capture_initial_surface(lease, approved, workspace, FalsySource()):
                    pass


def test_a_declared_file_that_drifted_into_a_directory_refuses(leased):
    """External state diverging from frozen intent is a refusal, not a protocol error.

    `Observation` requires `modeled` for a directory, so a preimage route that omitted it
    would surface this drift as ProtocolError("modeled...") -- blaming the engine for the
    filesystem.
    """
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        root_fd = lease._binding.project_root_fd
        os.unlink("d/f.txt", dir_fd=root_fd)
        os.mkdir("d/f.txt", dir_fd=root_fd)

        with open_workspace(lease, approved) as workspace:
            with pytest.raises(PreconditionRefused, match="declared initial"):
                with capture_initial_surface(
                    lease, approved, workspace, payloads_for_replace()
                ):
                    pass


def test_an_extra_payload_binding_is_not_an_error(leased):
    """§9: `open(digest)` cannot be enumerated, so capture never learns of extras.

    Detecting them would mean adding enumeration machinery for a condition that harms
    nothing -- an unrequested payload is never opened, staged, or promoted.
    """
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        payloads = DictPayloads({digest_of(AFTER): AFTER, digest_of(b"junk"): b"junk"})
        with open_workspace(lease, approved) as workspace:
            with capture_initial_surface(lease, approved, workspace, payloads) as captured:
                assert digest_of(b"junk") not in payloads.requested
                assert captured.manifest


def test_a_refusal_leaves_reclaimable_scratch_and_no_record(leased):
    """§7.4: cleanliness is scoped to success.

    Partial workspace scratch is mutation-free, no durable record exists, and A5b's
    reclamation removes it at the next lease entry.
    """
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        payloads = DictPayloads({digest_of(AFTER): b"wrong"})
        with open_workspace(lease, approved) as workspace:
            with pytest.raises(PreconditionRefused):
                with capture_initial_surface(lease, approved, workspace, payloads):
                    pass
        assert lease._store.read_active() is None


# --- workspace authentication, before the first write --------------------------------


def test_a_workspace_from_another_store_refuses_before_anything_is_staged(leased):
    """A matching txid is not a matching workspace.

    `promote_staging` makes exactly this check (`blobs.py:334`), but it runs inside
    `prepare_transaction` -- by then capture has already streamed this transaction's
    preimages into a foreign store's staging directory. Two `leased()` contexts are two
    independent projects and metadata roots, so the foreign store is real, and
    `create_workspace` takes the txid it is given: the collision is constructible.
    """
    from atoms.coordinator.capture import capture_initial_surface

    with leased() as lease:
        approved = approved_replace(lease)
        with leased() as other:
            foreign = other._store.create_workspace(approved.txid)
            try:
                assert foreign.txid == approved.txid
                assert foreign._store is not lease._store
                with pytest.raises(ProtocolError, match="different Store"):
                    capture_initial_surface(
                        lease, approved, foreign, payloads_for_replace()
                    )
                # The refusal came before the first write, not after it.
                assert os.listdir(foreign.staging_fd) == []
            finally:
                foreign.close()


def test_a_duck_typed_workspace_refuses_before_anything_is_staged(leased):
    """The impostor satisfies every duck check, so only the exact-type gate stops it.

    It carries the lease's own store, the proof's own txid, and the real descriptors --
    which is the point: `workspace._store is lease._store` and the txid comparison both
    pass, and capture would write through `staging_fd` into a workspace whose `close`
    and spent-flag discipline nothing owns.
    """
    from typing import cast

    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace
    from atoms.store.workspace import Workspace

    class Impostor:
        def __init__(self, real: Workspace) -> None:
            self._store = real._store
            self.txid = real.txid
            self.staging_fd = real.staging_fd
            self.work_fd = real.work_fd

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            impostor = Impostor(workspace)
            with pytest.raises(ProtocolError, match="exactly Workspace"):
                capture_initial_surface(
                    lease,
                    approved,
                    cast(Workspace, impostor),
                    payloads_for_replace(),
                )
            assert os.listdir(workspace.staging_fd) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd python && uv run pytest tests/test_coordinator_capture.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'atoms.coordinator.capture'`.

- [ ] **Step 3: Write the implementation**

Create `python/src/atoms/coordinator/capture.py`:

```python
"""Authority §7.3 step 1 -- coherent capture (design §7, §8, §9).

Everything here happens BEFORE the durable record exists, so no path halts. Errnos with a
defined domain meaning translate (`translated_lookup`); every other OSError propagates
with its own class and traceback.
"""

from __future__ import annotations

import hashlib
import os
from typing import IO, Protocol

from atoms.core.errors import PreconditionRefused, ProtocolError
from atoms.core.fingerprint import ABSENT, DirectoryState, FileState, SymlinkState
from atoms.core.recovery.model import (
    ObservedAbsent,
    ObservedDirectory,
    ObservedEntry,
    ObservedFile,
    ObservedSymlink,
)
from atoms.coordinator.admission import _require_admitted
from atoms.coordinator.descriptors import DescriptorTable, WalkStop, _build_descriptor_table
from atoms.coordinator.lease import Lease
from atoms.fs.approval import ProjectApprovedSpec
from atoms.fs.observe import Observation
from atoms.store.blobs import StagedBlob, digest_to_leaf
from atoms.store.records import referenced_digests
from atoms.store.workspace import Workspace

_READ_CHUNK = 1 << 20


class PayloadSource(Protocol):
    """The consumer's planned-postimage bytes (design §7.1).

    Authority §4.1 forbids the engine from reaching back into consumer plan formats and
    §4.2 reserves staging-path derivation to the engine, so the bytes arrive
    content-addressed: two effects writing identical content are supplied once, and the
    consumer never learns a staging path.

    `open` returns a FRESH binary stream, owned by capture, which closes it whether the
    stream is consumed, refused, or abandoned by an earlier failure. It raises `KeyError`
    -- and only `KeyError` -- when it has no binding for a digest. That is the one signal
    capture translates; every other exception is the source's own and propagates.
    """

    def open(self, digest: str) -> IO[bytes]: ...


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
    """Authority §7.3 step 1 (design §7)."""
    _require_admitted(lease, approved)
    # The workspace is authenticated before anything is written into it. `promote_staging`
    # already makes exactly these two checks (`blobs.py:332-335`), but it runs inside
    # `prepare_transaction`, long after capture has streamed preimages and payloads into
    # `workspace.staging_fd`. A duck-typed value, or a real Workspace issued by a
    # different Store with the same txid, would receive those bytes and only be rejected
    # afterwards -- so the check belongs at the first function that writes.
    if type(workspace) is not Workspace:
        raise ProtocolError(
            f"expected exactly Workspace, got {type(workspace).__name__}"
        )
    if workspace._store is not lease._store:
        raise ProtocolError("this workspace belongs to a different Store")
    if workspace.txid != approved.txid:
        raise ProtocolError(
            f"workspace txid {workspace.txid!r} does not match the proof's "
            f"{approved.txid!r}"
        )

    lengths = require_one_length_per_digest(referenced_digests(approved.compiled.spec))
    backend = lease._binding.backend
    manifest: list[StagedBlob] = []
    table: DescriptorTable | None = None
    try:
        with Observation(backend) as observation:
            table = _build_descriptor_table(lease, approved, workspace, observation)
            _verify_stops(table, approved)
            manifest.extend(
                _stage_preimages(observation, table, approved, workspace, backend)
            )
        manifest.extend(
            _stage_payloads(
                backend, workspace, payloads, lengths, {e.digest for e in manifest}
            )
        )
        _require_staging_matches(workspace, manifest)
    except BaseException:
        if table is not None:
            table.close()
        raise
    return Captured(manifest=tuple(manifest), descriptors=table)


def require_one_length_per_digest(
    pairs: tuple[tuple[str, int], ...],
) -> dict[str, int]:
    """One byte_len per digest, checked BEFORE anything is written (design §7.2).

    `compile_spec` validates each byte_len's range and the empty-hash correspondence but
    never cross-checks that one content_hash carries one byte_len -- measured. The staging
    name is one name per digest, so two lengths collide on it. `_preflight` refuses this
    too, but only after capture has already written both files.

    Engine misuse surfacing at the first layer that can see it, not external drift: the
    contradiction is in the frozen spec and no filesystem state is involved.
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


def _verify_stops(table: DescriptorTable, approved: ProjectApprovedSpec) -> None:
    """Justify the absence of everything beneath each stop (design §8).

    Two routes, two branches, both selected by the DECLARED state and never by an errno.
    Conflating them would silently grant a symlink the file branch's coherence.
    """
    declared = _first_states(approved)
    for stop in table.stops:
        if type(stop.observed) is ObservedAbsent:
            # §8.1: the planned directory's name is genuinely free.
            continue
        expected = declared.get(stop.path)
        if type(expected) is FileState:
            # §8.2, descriptor-coherent: type, mode, and hash all from one descriptor.
            # Stronger than the negative lookup it replaces.
            _require_declared(stop, ObservedFile, expected)
        elif type(expected) is SymlinkState:
            # §8.2, NOT descriptor-coherent: lstat + readlink, no descriptor, no
            # identity. The absence inference holds because a symlink holds no entries;
            # the identity contract is deferred to A7's destructive transfer.
            _require_declared(stop, ObservedSymlink, expected)
        else:
            raise PreconditionRefused(
                f"{stop.path!r} blocks traversal but no declared file or symlink state "
                f"describes it; observed {stop.observed!r}"
            )


def _require_declared(stop: WalkStop, expected_type: type, expected) -> None:
    if type(stop.observed) is not expected_type or stop.observed.state != expected:
        raise PreconditionRefused(
            f"{stop.path!r} blocks traversal but is not the declared {expected!r}; "
            f"observed {stop.observed!r}"
        )


def _stage_preimages(
    observation: Observation,
    table: DescriptorTable,
    approved: ProjectApprovedSpec,
    workspace: Workspace,
    backend,
) -> list[StagedBlob]:
    """Verify every reachable declared path against its timeline's FIRST precondition.

    Later occurrence-local preconditions describe intermediate states no initial capture
    can observe, and checking them here would refuse correct transactions.
    """
    declared = _first_states(approved)
    staged: list[StagedBlob] = []
    seen: set[str] = set()
    for path_entry in approved.paths:
        parent = path_entry.parent_node
        if table.is_unreachable(parent):
            continue  # §8 already justified the absence of everything below the stop.
        try:
            parent_fd = table.fd_for(parent)
        except KeyError as caught:
            raise ProtocolError(
                f"{parent!r} is neither in the descriptor table nor proved unreachable; "
                "the walk and the approved paths disagree"
            ) from caught
        expected = declared[path_entry.path]
        if type(expected) is FileState and expected.content_hash not in seen:
            seen.add(expected.content_hash)
            name = digest_to_leaf(expected.content_hash)
            sink = _open_sink(workspace, name)
            try:
                # `modeled` is passed on this route too. A declared file that drifted
                # into a directory would otherwise make `Observation` raise
                # ProtocolError for a missing argument, when the honest answer is that
                # external state diverged from the frozen spec -- PreconditionRefused.
                entry = observation.observe(
                    parent_fd,
                    path_entry.leaf,
                    sink_fd=sink,
                    modeled=_modeled_under(approved, path_entry.path),
                )
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
        entry = observation.observe(
            parent_fd,
            path_entry.leaf,
            modeled=_modeled_under(approved, path_entry.path),
        )
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
        try:
            stream = payloads.open(digest)
        except KeyError as caught:
            raise ProtocolError(
                f"the payload source has no binding for {digest}, which the frozen spec "
                "declares as a planned postimage"
            ) from caught
        # Two independent owners: a failure to open the sink must not leak the stream,
        # and a stream that raises on close must not skip closing the sink.
        try:
            sink = _open_sink(workspace, digest_to_leaf(digest))
            try:
                observed, byte_len = _stream_into(stream, sink)
                if observed != digest or byte_len != lengths[digest]:
                    raise PreconditionRefused(
                        f"the payload for {digest} hashes to {observed} at {byte_len} "
                        f"bytes, not {digest} at {lengths[digest]}"
                    )
                backend.flush_file(sink)
            finally:
                os.close(sink)
        finally:
            stream.close()
        staged.append(
            StagedBlob(
                name=digest_to_leaf(digest), digest=digest, byte_len=lengths[digest]
            )
        )
    return staged


def _require_staging_matches(
    workspace: Workspace, manifest: list[StagedBlob]
) -> None:
    """On success, staging holds EXACTLY the manifest (design §7.4, criterion 10)."""
    present = set(os.listdir(workspace.staging_fd))
    expected = {entry.name for entry in manifest}
    if present != expected:
        raise PreconditionRefused(
            f"staging/{workspace.txid}/ holds stray entries "
            f"{sorted(present - expected)} and is missing {sorted(expected - present)}"
        )


def _open_sink(workspace: Workspace, name: str) -> int:
    """The staging sink. O_EXCL, so external occupancy of the leaf surfaces as EEXIST."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        return os.open(name, flags, 0o600, dir_fd=workspace.staging_fd)
    except FileExistsError as caught:
        raise PreconditionRefused(
            f"staging/{workspace.txid}/{name} is already occupied; authority §11 names "
            "pre-existing external occupancy of an engine-derived scratch leaf a clean "
            "refusal, and it need not be concurrent"
        ) from caught


def _stream_into(stream: IO[bytes], sink_fd: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    length = 0
    while True:
        chunk = stream.read(_READ_CHUNK)
        # Type BEFORE falsiness. A stream returning None or "" is malformed, not at end
        # of file, and testing falsiness first would accept it as a clean EOF -- which
        # for an empty declared file hashes to the empty digest and validates.
        if type(chunk) is not bytes:
            raise ProtocolError(
                f"a payload stream yielded {type(chunk).__name__}, not bytes"
            )
        if not chunk:
            break
        digest.update(chunk)
        length += len(chunk)
        view = memoryview(chunk)
        while view:
            view = view[os.write(sink_fd, view) :]
    return "sha256:" + digest.hexdigest(), length


def _require_state(path: str, entry: ObservedEntry, expected) -> None:
    """Compare an observation with a declared state.

    Explicit per kind. `ABSENT` is `AbsentState()`, not None, so reaching for a missing
    `.state` attribute would compare None against AbsentState and refuse every correct
    declared-absent path.
    """
    matches = (
        (type(entry) is ObservedAbsent and expected is ABSENT)
        or (type(entry) is ObservedFile and type(expected) is FileState)
        or (type(entry) is ObservedSymlink and type(expected) is SymlinkState)
        or (type(entry) is ObservedDirectory and type(expected) is DirectoryState)
    )
    if not matches:
        raise PreconditionRefused(
            f"{path!r} is {entry!r}, which is not the declared initial {expected!r}"
        )
    if type(entry) is not ObservedAbsent and entry.state != expected:
        raise PreconditionRefused(
            f"{path!r} is {entry.state!r}, not the declared initial {expected!r}"
        )


def _modeled_under(approved: ProjectApprovedSpec, path: str) -> frozenset[str]:
    base = f"{path}/"
    return frozenset(
        entry.path[len(base) :]
        for entry in approved.paths
        if entry.path.startswith(base) and "/" not in entry.path[len(base) :]
    )


def _first_states(approved: ProjectApprovedSpec) -> dict[str, object]:
    """Each path's FIRST precondition -- the declared initial surface.

    Measured: `PathTimeline` is `(path, occurrences)` and `TimelineOccurrence` carries
    `pre`.
    """
    return {
        timeline.path: timeline.occurrences[0].pre
        for timeline in approved.compiled.timelines
    }
```

- [ ] **Step 4: Register the entry point**

In `python/tests/test_fs_architecture.py`, extend `_TRANSACTION_STAGE_ENTRY_POINTS`:

```python
_TRANSACTION_STAGE_ENTRY_POINTS = {
    "atoms/coordinator/capture.py": ("capture_initial_surface",),
    "atoms/coordinator/prepare.py": ("open_workspace", "prepare_transaction"),
    "atoms/coordinator/transitions.py": ("persist_plan_prefix",),
}
```

Measured: `test_no_unregistered_public_function_accepts_the_proof` asserts `found == registered` over
every public `coordinator/*.py` function annotating a `ProjectApprovedSpec` parameter, and
`test_every_transaction_stage_entry_point_opens_with_the_proof_gate` requires `_require_admitted(...)`
as the first statement after the docstring. `capture_initial_surface` satisfies both;
`_build_descriptor_table` is private and therefore neither found nor registered.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd python && uv run pytest tests/test_coordinator_capture.py tests/test_fs_architecture.py -q`
Expected: PASS, 25 capture cases (24 test functions; one is parametrized over two falsy stream values) plus the architecture tier.

- [ ] **Step 6: Run the full gate set**

Run: `cd python && uv run ruff format && uv run ruff check && uv run pyright && uv run pytest -q`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add python/src/atoms/coordinator/capture.py python/tests/test_coordinator_capture.py \
        python/tests/test_fs_architecture.py
git commit -m "feat(capture): stage the initial surface behind one flushed manifest"
```

---

## Task 5: Conformance against A3's two routes

**Files:**
- Create: `python/tests/test_coordinator_capture_conformance.py`

Adds no production code. The **`Observation` mechanism's** outputs feed two different A3 entry points
and one route cannot exercise both; `Captured` exposes no observations, so both routes drive the
observer directly. Scratch relations come from the real observer over actual files and descriptors —
building a `ScratchObservation` by hand and feeding it to A3 tests A3, not A6.

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
    TransactionState,
    authorize_recovery_step,
    build_recovery_snapshot,
    classify_recovery,
)
from atoms.core.recovery.model import FileBuildRelation
from atoms.core.recovery.plan import ActionPlan, AuthorizedStep, JointObservation
from atoms.fs.linux import LinuxBackend
from atoms.fs.observe import Observation
from tests.capture_support import approved_replace, digest_of


def _observed(lease, approved, workspace):
    from atoms.coordinator.descriptors import _build_descriptor_table

    with Observation(LinuxBackend()) as observation:
        table = _build_descriptor_table(lease, approved, workspace, observation)
        try:
            (path_entry,) = approved.paths
            scratch = approved.scratch[0]
            live = observation.observe(
                table.fd_for(path_entry.parent_node), path_entry.leaf
            )
            slot = observation.observe(
                table.fd_for(scratch.parent_node), scratch.leaf
            )
        finally:
            table.close()
    return path_entry, scratch, live, slot


def test_a_complete_observation_is_accepted_by_build_recovery_snapshot(leased):
    """Proves the observer satisfies A3's coverage and shape validators without A7."""
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            path_entry, scratch, live, slot = _observed(lease, approved, workspace)

            snapshot = build_recovery_snapshot(
                compiled=approved.compiled,
                topology=approved.topology,
                transaction_state=TransactionState.PREPARED,
                commit_decision=CommitDecision.UNCOMMITTED,
                rollback_result=None,
                halt_diagnostic=None,
                active=True,
                journals=(EffectJournalState("e1", JournalState.PENDING),),
                persistent_observations=(PersistentObservation(path_entry.path, live),),
                scratch_observations=(
                    ScratchObservation("e1", scratch.role, slot, None),
                ),
            )

    assert snapshot.persistent_observations[0].entry is live


def test_a_scratch_only_observation_authorizes_a_committed_cleanup_step(leased):
    """The committed-cleanup route, driven to an exact verdict.

    Ledger #13's contract for this route is specific: after one coherent complete
    final-surface observation, each authorization observation is a *fresh* one covering
    exactly the named retained scratch slot, with empty persistent and occupancy
    coverage. A PREPARED/UNCOMMITTED snapshot carrying persistent evidence exercises a
    different route entirely, and `assert outcome is not None` accepts a `HaltPlan` --
    which is the failure this arm exists to catch.

    The freshness is load-bearing and is asserted, not assumed: the authorization runs on
    a second `Observation` -- a new token universe -- taken after classification, while
    the tombstone is still on disk. Reusing the snapshot's `retained` would authorize
    against evidence gathered before the plan existed and against an entry that the
    cleanup has no proof is still there.
    """
    from atoms.core.recovery.plan import PlanDisposition, RemoveScratch
    from atoms.coordinator.descriptors import _build_descriptor_table
    from atoms.coordinator.prepare import open_workspace
    from tests.capture_support import AFTER, BEFORE, approved_superseded

    with leased() as lease:
        # DeletePath("a.txt") then CreateFileNoClobber("a.txt"): committed, so e1's
        # tombstone is retained scratch awaiting cleanup while the live path already
        # holds the final surface.
        approved = approved_superseded(lease)
        tombstone, staging = approved.scratch
        with open_workspace(lease, approved) as workspace:
            with Observation(LinuxBackend()) as complete:
                table = _build_descriptor_table(lease, approved, workspace, complete)
                root_fd = table.fd_for(tombstone.parent_node)
                try:
                    fd = os.open(
                        tombstone.leaf,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o644,
                        dir_fd=root_fd,
                    )
                    os.write(fd, BEFORE)
                    os.close(fd)
                    live = complete.observe(root_fd, "a.txt")
                    retained = complete.observe(root_fd, tombstone.leaf)
                    consumed = complete.observe(root_fd, staging.leaf)

                    snapshot = build_recovery_snapshot(
                        compiled=approved.compiled,
                        topology=approved.topology,
                        transaction_state=TransactionState.COMMITTED,
                        commit_decision=CommitDecision.COMMITTED,
                        rollback_result=None,
                        halt_diagnostic=None,
                        active=True,
                        journals=(
                            EffectJournalState("e1", JournalState.DONE),
                            EffectJournalState("e2", JournalState.DONE),
                        ),
                        persistent_observations=(
                            PersistentObservation("a.txt", live),
                        ),
                        scratch_observations=(
                            ScratchObservation("e1", tombstone.role, retained, None),
                            ScratchObservation("e2", staging.role, consumed, None),
                        ),
                    )

                    assert live.state.content_hash == digest_of(AFTER)
                    plan = classify_recovery(snapshot)
                    assert type(plan) is ActionPlan
                    assert plan.disposition is PlanDisposition.COMMITTED_CLEANUP

                    index, step = next(
                        (i, s)
                        for i, s in enumerate(plan.steps)
                        if type(s) is RemoveScratch
                    )
                    assert step.effect_id == "e1"

                    # The fresh pass. Same held descriptor, same name, new tokens.
                    with Observation(LinuxBackend()) as authorization:
                        reobserved = authorization.observe(root_fd, tombstone.leaf)

                    # Genuinely a new universe, and genuinely the same entry.
                    assert reobserved.identity is not retained.identity
                    assert reobserved.state == retained.state

                    # Exactly the named slot. Empty persistent and occupancy coverage is
                    # the contract, not an omission. This authorizes because
                    # `_project_identity_relations` compares pairwise SAME/DIFFERENT
                    # among the observation's own tokens, never a raw token against the
                    # snapshot's -- which is what makes a fresh pass admissible at all.
                    joint = JointObservation(
                        persistent=(),
                        scratch=(
                            ScratchObservation("e1", tombstone.role, reobserved, None),
                        ),
                        parent_occupancy=(),
                    )
                    outcome = authorize_recovery_step(plan, index, joint)

                    assert type(outcome) is AuthorizedStep
                finally:
                    os.unlink(tombstone.leaf, dir_fd=root_fd)
                    table.close()


def test_a_wrong_mode_staging_directory_is_reported_with_its_actual_mode(leased):
    """§10's wrong-mode staging directory, observed rather than judged.

    A6's obligation is to report the mode and occupancy it actually found; deciding that
    the mode is *wrong* is A3's, and an observer that normalised or corrected it would
    hide the case A7 has to repair.
    """
    from atoms.core.recovery.model import ObservedDirectory
    from atoms.coordinator.descriptors import _build_descriptor_table
    from atoms.coordinator.prepare import open_workspace
    from tests.capture_support import DIRECTORY_POST

    with leased() as lease:
        approved = approved_replace(lease)
        scratch = approved.scratch[0]
        with open_workspace(lease, approved) as workspace:
            with Observation(LinuxBackend()) as observation:
                table = _build_descriptor_table(lease, approved, workspace, observation)
                try:
                    parent_fd = table.fd_for(scratch.parent_node)
                    os.mkdir(scratch.leaf, 0o700, dir_fd=parent_fd)
                    entry = observation.observe(
                        parent_fd, scratch.leaf, modeled=frozenset()
                    )
                finally:
                    os.rmdir(scratch.leaf, dir_fd=parent_fd)
                    table.close()

    assert type(entry) is ObservedDirectory
    assert entry.state.mode == 0o700
    assert entry.state.mode != DIRECTORY_POST.mode
    assert entry.has_unmodeled_child is False


def test_the_prefix_relation_comes_from_real_files(leased):
    """§11.3: over actual files and descriptors, not hand-built model values."""
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        with open_workspace(lease, approved) as workspace:
            staging_fd = workspace.staging_fd
            for name, payload in (("staged", b"pay"), ("planned", b"payload")):
                fd = os.open(
                    name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=staging_fd
                )
                os.write(fd, payload)
                os.close(fd)
            staged_fd = os.open("staged", os.O_RDONLY, dir_fd=staging_fd)
            planned_fd = os.open("planned", os.O_RDONLY, dir_fd=staging_fd)
            try:
                with Observation(LinuxBackend()) as observation:
                    relation = observation.build_relation(staged_fd, planned_fd)
            finally:
                os.close(staged_fd)
                os.close(planned_fd)
                os.unlink("staged", dir_fd=staging_fd)
                os.unlink("planned", dir_fd=staging_fd)

    assert relation is FileBuildRelation.STRICT_PREFIX
```

- [ ] **Step 2: Run the tests**

Run: `cd python && uv run pytest tests/test_coordinator_capture_conformance.py -q`
Expected: PASS, 4 tests. **If `classify_recovery` returns a `HaltPlan` for this snapshot, stop and
report it** — the first fixture is a clean PREPARED/PENDING transaction and should classify as a
rollback, and the second is a committed supersede that should classify as `COMMITTED_CLEANUP`.

- [ ] **Step 3: Run the full gate set**

Run: `cd python && uv run ruff format && uv run ruff check && uv run pyright && uv run pytest -q`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add python/tests/test_coordinator_capture_conformance.py python/tests/capture_support.py
git commit -m "test(capture): conform the observer to A3's snapshot and authorization routes"
```

---

## Task 6: The adversarial tier and the mutation surface

**Files:**
- Modify: `python/tests/coordinator_support.py` (receives `project_state`)
- Modify: `python/tests/test_coordinator_lease.py` (imports the moved helper)
- Create: `python/tests/test_coordinator_capture_adversarial.py`

Scoped to A6. Symlink validation after a destructive transfer, and effect-staging swaps between a
pre-publication check and the publishing rename, are **A7 obligations** — the objects do not exist
until an effect creates them.

- [ ] **Step 1: Move the mutation-surface helper**

Cut `_project_state` from `python/tests/test_coordinator_lease.py:186` into
`python/tests/coordinator_support.py` as a public `project_state`, keeping its docstring verbatim. In
`test_coordinator_lease.py`, import it and replace the local calls.

Measured: it already records the root itself as `"."`, uses `lstat` throughout so a symlink is
compared as a symlink rather than followed, and keys on `(S_IFMT, S_IMODE, st_dev, st_ino, st_size,
payload)` — which is why A6 reuses it rather than writing a weaker `os.walk` over files only.

Run: `cd python && uv run pytest tests/test_coordinator_lease.py -q`
Expected: PASS, unchanged.

- [ ] **Step 2: Write the adversarial tests**

Create `python/tests/test_coordinator_capture_adversarial.py`:

```python
"""A6 tier 5 -- adversarial, scoped to A6 (design §11.6)."""

from __future__ import annotations

import os

import pytest

from atoms.core.errors import PreconditionRefused
from tests.capture_support import (
    AFTER,
    DictPayloads,
    approved_replace,
    digest_of,
)
from tests.coordinator_support import project_state


def payloads() -> DictPayloads:
    return DictPayloads({digest_of(AFTER): AFTER})


def test_no_project_path_is_mutated_by_capture(leased, ext4_project_root):
    """Authority §13.5. A6 writes only into staging/<txid>/.

    `project_state` records the root itself, every descendant, `lstat` rather than
    `stat`, and st_dev/st_ino -- so a path replaced by an inode of identical kind, mode,
    and content is still visible, and a chmod on the root is not invisible.
    """
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        before = project_state(str(ext4_project_root))
        with open_workspace(lease, approved) as workspace:
            with capture_initial_surface(lease, approved, workspace, payloads()):
                pass
        assert project_state(str(ext4_project_root)) == before


def test_no_project_path_is_mutated_by_a_refused_capture(leased, ext4_project_root):
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        before = project_state(str(ext4_project_root))
        bad = DictPayloads({digest_of(AFTER): b"wrong"})
        with open_workspace(lease, approved) as workspace:
            with pytest.raises(PreconditionRefused):
                with capture_initial_surface(lease, approved, workspace, bad):
                    pass
        assert project_state(str(ext4_project_root)) == before


def test_a_leaf_swapped_between_the_walk_and_the_observation_refuses(leased, monkeypatch):
    """Between two A6 steps, not before capture begins.

    The table opens and validates `d`; the observation then looks `f.txt` up beneath the
    held descriptor. Swapping the leaf in the window between them is the race A6 itself
    can close -- a swap after observation is A7's destructive-transfer validation, and
    asserting it here would prove nothing about the code that will own it.
    """
    from atoms.coordinator import capture as capture_module
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        root_fd = lease._binding.project_root_fd
        real_verify = capture_module._verify_stops

        def swap_then_verify(table, spec):
            os.unlink("d/f.txt", dir_fd=root_fd)
            os.symlink("/etc/passwd", "d/f.txt", dir_fd=root_fd)
            return real_verify(table, spec)

        monkeypatch.setattr(capture_module, "_verify_stops", swap_then_verify)
        with open_workspace(lease, approved) as workspace:
            with pytest.raises(PreconditionRefused):
                with capture_initial_surface(lease, approved, workspace, payloads()):
                    pass


def test_a_mount_crossing_mid_walk_refuses(leased, monkeypatch):
    """DirectoryConstraints carries lookup_proof and name_max only.

    A constraints comparison alone would pass a directory replaced by a bind mount, so
    mount membership is read separately and compared with the bound volume's.
    """
    from atoms.coordinator import descriptors
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = approved_replace(lease)
        real_read = descriptors.read_mount_id
        root_fd = lease._binding.project_root_fd

        def foreign_mount(fd):
            # The project root itself keeps its real mount; the child `d` reports a
            # different one, which is the shape a bind mount over `d` would produce.
            return real_read(fd) if fd == root_fd else real_read(fd) + 1_000_000

        monkeypatch.setattr(descriptors, "read_mount_id", foreign_mount)
        with open_workspace(lease, approved) as workspace:
            with pytest.raises(PreconditionRefused, match="mount"):
                with capture_initial_surface(lease, approved, workspace, payloads()):
                    pass


def test_project_root_constraint_drift_after_approval_refuses(leased, monkeypatch):
    """§5.3: retention is not discharge."""
    from atoms.coordinator import descriptors
    from atoms.coordinator.capture import capture_initial_surface
    from atoms.coordinator.prepare import open_workspace
    from atoms.fs.lookup import DirectoryConstraints, LookupProof

    with leased() as lease:
        approved = approved_replace(lease)
        drifted = DirectoryConstraints(lookup_proof=LookupProof.EXACT_BYTES, name_max=64)
        monkeypatch.setattr(
            descriptors, "read_lookup_constraints", lambda fd, kind: drifted
        )
        with open_workspace(lease, approved) as workspace:
            with pytest.raises(PreconditionRefused, match="constraints"):
                with capture_initial_surface(lease, approved, workspace, payloads()):
                    pass
```

- [ ] **Step 3: Run the tests**

Run: `cd python && uv run pytest tests/test_coordinator_capture_adversarial.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 4: Run the full gate set**

Run: `cd python && uv run ruff format && uv run ruff check && uv run pyright && uv run pytest -q`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add python/tests/coordinator_support.py python/tests/test_coordinator_lease.py \
        python/tests/test_coordinator_capture_adversarial.py
git commit -m "test(capture): assert the mutation surface and the A6-scoped races"
```

---

## Task 7: The import whitelist

**Files:**
- Modify: `python/tests/test_fs_architecture.py`

- [ ] **Step 1: Write the test**

Add to `python/tests/test_fs_architecture.py`:

```python
def test_observe_imports_only_the_recovery_model():
    """Ledger #13's "may not pre-classify" as a mechanical property.

    A blacklist on `snapshot` would be insufficient: `atoms/core/recovery/__init__.py`
    re-exports `classify_recovery` and `authorize_recovery_step`, so a classifier is
    reachable through the package facade.

    The scan reuses `_resolved_imports`, which already resolves plain `import`, aliased
    `from atoms.core import recovery`, and relative forms through `resolve_name`. A
    hand-rolled scanner over `node.module` alone would miss all three: `from atoms.core
    import recovery` names the parent package, and a relative import names nothing that
    starts with `atoms`.
    """
    source = SOURCE_ROOT / "fs" / "observe.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    permitted = "atoms.core.recovery.model"
    offenders = sorted(
        name
        for name in _resolved_imports(tree, package="atoms.fs")
        if name.startswith("atoms.core.recovery")
        and name != permitted
        and not name.startswith(f"{permitted}.")
    )
    assert offenders == []
```

- [ ] **Step 2: Verify it fails on all four planted violations**

Plant each of these in `observe.py` in turn, run the test, and confirm it FAILS each time. **A test
that passes on any one of them is not guarding anything — stop and fix it before removing the plant.**
Remove every plant afterwards.

| Plant | Why it must be caught |
| --- | --- |
| `from atoms.core.recovery import classify_recovery` | The package facade re-exports the classifier |
| `import atoms.core.recovery` | Attribute access reaches everything |
| `from atoms.core import recovery` | Names the parent package, not the module |
| `from ..core.recovery import classify_recovery` | Relative; the raw `node.module` is `core.recovery` |

Run each time: `cd python && uv run pytest tests/test_fs_architecture.py -q -k observe_imports`

- [ ] **Step 3: Run the full gate set**

Run: `cd python && uv run ruff format && uv run ruff check && uv run pyright && uv run pytest -q`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add python/tests/test_fs_architecture.py
git commit -m "test(fs): whitelist the recovery imports observe.py may make"
```

---

## Task 8: The ledger, the authority amendments, and status synchronization

**Files:**
- Modify: `docs/deferred-obligation-ledger.md`
- Modify: `docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md`
- Modify: `docs/plans/2026-08-07-a6-coherent-capture-design.md`
- Modify: `docs/plans/2026-08-02-a5b-recovery-lease-design.md` — its status header still says
  `"A6–A8 remain unimplemented."`, and `test_a5_status_is_synchronized_across_authority_documents`
  asserts that string, so it moves with A6 or the suite is red.
- Modify: `AGENTS.md`, `README.md`
- Modify: `python/tests/test_store_architecture.py`
- Modify: `python/tests/test_coordinator_architecture.py`

- [ ] **Step 1: Land the authority amendments**

In `docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md`:

**The status header** (line 4) — it currently reads:

> **Status:** Approved — authority design for `atoms`. Plan A implementation underway; A1–A5b are
> implemented (pure model and compilation, recovery reference model, capability backend, path
> resolution and project approval, SQLite-WAL metadata store, recovery-resolve lease); A6–A8
> (coherent capture, effect/recovery execution, synthetic exerciser) remain.

Replace with:

> **Status:** Approved — authority design for `atoms`. Plan A implementation underway; A1–A6 are
> implemented (pure model and compilation, recovery reference model, capability backend, path
> resolution and project approval, SQLite-WAL metadata store, recovery-resolve lease, coherent
> capture and the observation mechanism); A7–A8 (effect/recovery execution, synthetic exerciser)
> remain.

Note the wording differs from every other document's — it spells the remainder as
`"A6–A8 (coherent capture, …) remain"`, not `"A6–A8 remain unimplemented"`, so the closing grep
would **not** have caught it. This is why the status test asserts the authority header positively
rather than relying on a single forbidden string.

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

- [ ] **Step 2: Update the deferred-obligation ledger**

In `docs/deferred-obligation-ledger.md`, amend entries 1, 3, 13, and 19. Do **not** remove any of the
four — each keeps work A7 owns:

| # | Amended required-behavior text |
| --- | --- |
| 1 | Capture half discharged: capture hashes the actual stream from one descriptor and compares against the frozen `FileState`. A7 owns materialization's half. |
| 3 | Capture/inference half discharged: both §8 branches infer descendant absence from the ancestor's verified state, each selected by the declared state. A7 owns handing §9.5's published-directory descriptor down at execution. |
| 13 | Observation-mechanism half discharged: state, identity, prefix relation, and occupancy from held descriptors under the token discipline, with a whitelist making pre-classification unreachable. A7 owns complete recovery assembly, committed-cleanup sequencing, and fresh authorization observations. |
| 19 | A6's half discharged: the descriptor table re-resolves identity, constraints, and mount against the approved baseline before relying on any of it. A7's execution half remains. |

- [ ] **Step 3: Synchronize the status strings**

Measured: `test_a5_status_is_synchronized_across_authority_documents` asserts the literal
`"A5 is implemented; A6–A8 remain unimplemented"` in `AGENTS.md`. Editing that sentence **breaks that
test**, so both change together.

1. In `AGENTS.md`, change the A3 paragraph's `"A5 is implemented; A6–A8 remain unimplemented"` to
   `"A5 and A6 are implemented; A7–A8 remain unimplemented"`, and add an A6 paragraph describing
   `atoms/fs/observe.py` and `atoms/coordinator/{descriptors,capture}.py`.
2. In `AGENTS.md`, repair the A5 paragraph's justification of the build-stage trap. It currently
   reads *"Because A6 supplies no observations and A7 no executor yet, a live record at lease entry
   raises a temporary build-stage trap"* — the first half stops being true here. Measured: the trap
   is `lease.py:59`'s `if store.read_active() is not None: raise NotImplementedError`, and A6 adds
   no executor, so the trap **stays** and only its reason narrows. Replace with:

   > Because A7 has no executor yet, a live record at lease entry still raises a temporary
   > build-stage trap, so #12 and #17 remain at their write and lease halves. A6 discharged the
   > observation half it was waiting on.

3. In `docs/plans/2026-08-02-a5b-recovery-lease-design.md`, change its status header's
   `"A6–A8 remain unimplemented."` to `"A7–A8 remain unimplemented."` **This document is easy to
   miss:** `test_a5_status_is_synchronized_across_authority_documents` reads it as its second half and
   asserts the old string, so A6 landing without touching it leaves a banked design claiming A6 does
   not exist.
4. In `python/tests/test_store_architecture.py:1269`, update **both** halves — the `AGENTS.md`
   sentence and the A5b design's `"A6–A8 remain unimplemented."` assertion. Drop
   `assert "A5–A8 remain unimplemented" not in agents` only if it no longer applies.
5. In `README.md`, repair the whole `## Status` section. It is stale by more than A6: its second
   paragraph says the core *"has no filesystem, SQLite, or platform dependency yet — no path is
   mutated by any code in this repository today"* (`atoms.fs` and `atoms.store` both exist and A4a
   mutates engine-owned `metadata_root`), and its roadmap list stops at A3 and closes with
   `"A4–A8 remain unimplemented, and no filesystem mutation code has landed."` — false for A4a, A4b,
   A5a, and A5b, which landed before this plan. Replace the second paragraph and the A3 bullet's
   trailing sentence, then add the missing bullets:

   Second paragraph becomes:

   > The pure core (`atoms.core`) is joined under `python/` by `atoms.fs` (capability backend,
   > volume binding, project approval) and `atoms.store` (SQLite-in-WAL metadata store) beneath an
   > `atoms.coordinator` package holding the recovery lease, admission, and preparation. No project
   > path is mutated by any code in this repository today; the only paths written are engine-owned,
   > under `metadata_root`.

   The A3 bullet's last sentence `"A4–A8 remain unimplemented, and no filesystem mutation code has
   landed."` is deleted — the roadmap bullets below now carry the status — and these follow it:

   > - **A4 — capability backend, volume binding, and project approval (implemented):**
   >   [`docs/plans/2026-07-29-a4a-capability-backend-design.md`](docs/plans/2026-07-29-a4a-capability-backend-design.md),
   >   [`docs/plans/2026-07-30-a4b1-path-resolution-design.md`](docs/plans/2026-07-30-a4b1-path-resolution-design.md),
   >   [`docs/plans/2026-07-31-a4b2-project-approval-design.md`](docs/plans/2026-07-31-a4b2-project-approval-design.md)
   >   — the probed capability backend, anchored path resolution, and the `ProjectApprovedSpec` proof
   >   with its approved topology and scratch binding.
   > - **A5 — durable metadata store and recovery lease (implemented):**
   >   [`docs/plans/2026-07-31-a5a-metadata-store-design.md`](docs/plans/2026-07-31-a5a-metadata-store-design.md),
   >   [`docs/plans/2026-08-02-a5b-recovery-lease-design.md`](docs/plans/2026-08-02-a5b-recovery-lease-design.md)
   >   — the SQLite-in-WAL store as a mechanism, composed into the recovery lease, admission, and
   >   preparation.
   > - **A6 — coherent capture and the observation mechanism (implemented):**
   >   [`docs/plans/2026-08-07-a6-coherent-capture-design.md`](docs/plans/2026-08-07-a6-coherent-capture-design.md)
   >   — the descriptor table, the observation pass, and preimage capture into the workspace staging
   >   directory. A7–A8 remain unimplemented: nothing yet executes an effect against a project path.

   Those five filenames were listed from `docs/plans/` on 2026-08-07 — verify they still resolve
   before committing rather than trusting this plan for a path.

6. In `docs/plans/2026-08-07-a6-coherent-capture-design.md`, change the status header from
   `"Designed on 2026-08-07, unimplemented. A7–A8 remain unimplemented."` to
   `"Implemented on 2026-08-07. A7–A8 remain unimplemented."`

Run:

```bash
grep -nE "A6–A8 remain unimplemented|A1–A5b are" \
  AGENTS.md README.md \
  docs/deferred-obligation-ledger.md \
  docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md \
  docs/plans/2026-08-02-a5b-recovery-lease-design.md
```

Expected: no matches, exit status 1. The second alternative catches the authority header, whose
wording is its own and which the first alternative would sail past.

**The file list is explicit, and three kinds of file are deliberately outside it.** A whole-file scan
is sound only where a file has no legitimate reason to *contain* the string. These do:

- **Records of past moments.** `docs/plans/2026-08-02-plan-a5b-recovery-lease.md:4205` records what
  A5b's status line said when it landed, and *this* plan quotes the strings throughout the steps that
  retire them.
- **The amendment record.** The A6 design's §3.3 prints the authority's old header verbatim —
  `"A1–A5b are implemented … A6–A8 (coherent capture, …) remain"` — and names the
  `"A6–A8 remain unimplemented"` sentence to explain why the authority needs its own assertion. That
  before/after is what makes the amendment auditable.
- **The guards themselves.** `test_coordinator_architecture.py` and `test_store_architecture.py` name
  these strings as the strings they forbid. A guard cannot be its own subject: scanning it means the
  check fails precisely because it was written.

Editing any of them to satisfy a grep would falsify the record, or delete the check, to make the check
green — the exact inversion the guard exists to prevent. Nothing is lost by excluding them: the A6
design's *header* is its current claim and Step 4's test reads it directly, and a stale assertion in
either test file turns the suite red the moment the document it asserts over changes.

- [ ] **Step 4: Add the A6 status test**

Add `import re` to `python/tests/test_coordinator_architecture.py`'s imports (it currently imports
`ast`, `resolve_name`, and `Path`), then add the following, following `test_a4a_…`, `test_a4b_…`, and
`test_a5_…`:

```python
def _flat(lines: list[str]) -> str:
    return " ".join(" ".join(lines).split())


def _status_section(text: str) -> str:
    """The `## Status…` section: its heading through the next `## ` heading.

    Whitespace is flattened because both AGENTS.md and README.md wrap their status
    sentences across lines, and a reflow must not silently disable a guard.
    """
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## Status"))
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return _flat(lines[start:end])


def _status_field(text: str) -> str:
    """A design's `**Status:**` header field, bounded by a blank line or the next field.

    Scoped to the header on purpose. A design's status is a claim about the present; its
    body may legitimately QUOTE a status string while recording an amendment, and A6's
    §3.3 does exactly that -- it prints the authority's old header verbatim so the
    before/after is auditable. A whole-file scan conflates the claim with the record of
    the claim changing, and would force the record to be edited to keep the guard green:
    the precise inversion this guard exists to prevent.
    """
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("**Status:**"))
    field = [lines[start]]
    for line in lines[start + 1 :]:
        if not line.strip() or re.match(r"\*\*[A-Za-z][^*]*:\*\*", line):
            break
        field.append(line)
    return _flat(field)


def test_a6_status_is_synchronized_across_authority_documents():
    root = Path(__file__).parents[2]

    def read(name: str) -> str:
        return (root / name).read_text(encoding="utf-8")

    agents = _status_section(read("AGENTS.md"))
    readme = _status_section(read("README.md"))
    a6 = _status_field(read("docs/plans/2026-08-07-a6-coherent-capture-design.md"))
    a5b = _status_field(read("docs/plans/2026-08-02-a5b-recovery-lease-design.md"))
    authority = _status_field(
        read("docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md")
    )

    assert "A5 and A6 are implemented; A7–A8 remain unimplemented" in agents
    assert "A6 — coherent capture" in agents
    assert "**Status:** Implemented on 2026-08-07." in a6
    # No current status claim may still say A6 is unimplemented. A5b's design carries the
    # same sentence and is the one easiest to leave behind.
    for region in (agents, readme, a6, a5b, authority):
        assert "A6–A8 remain unimplemented" not in region
    assert "A7–A8 remain unimplemented" in a5b

    # The authority header spells its remainder differently -- "A6–A8 (coherent capture,
    # ...) remain" -- so the shared forbidden string cannot police it. Assert the header
    # positively, and forbid the span it replaces.
    assert "A1–A6 are implemented" in authority
    assert "A7–A8 (effect/recovery execution, synthetic exerciser) remain." in authority
    assert "A1–A5b are implemented" not in authority
    assert "A6–A8 (coherent capture" not in authority

    # The README's roadmap is the reader's map of what exists; it was three sub-plans
    # stale before A6 and must not be left that way.
    assert "A4–A8 remain unimplemented" not in readme
    assert "no filesystem mutation code has landed" not in readme
    for heading in (
        "**A4 — capability backend, volume binding, and project approval (implemented):**",
        "**A5 — durable metadata store and recovery lease (implemented):**",
        "**A6 — coherent capture and the observation mechanism (implemented):**",
    ):
        assert heading in readme
```

**Both extractors must find their anchor.** `next(...)` without a default raises `StopIteration` if a
document loses its `## Status` heading or `**Status:**` field, which fails the test rather than
vacuously passing it — a renamed heading is exactly the drift this is guarding.

- [ ] **Step 5: Run the full gate set**

Run: `cd python && uv run ruff format && uv run ruff check && uv run pyright && uv run pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "docs(a6): discharge the ledger halves and amend the authority design"
```

---

## Self-Review

**Spec coverage.** §5 → Task 3; §6 → Task 1; §7, §7.1–§7.4 → Task 4; §8 → Tasks 3 (observation) and 4
(adjudication); §9/§9.1 → Task 1's `translated_lookup` with the refusal table exercised in Task 4;
§10.1 → Task 4 Step 4; §10.2 → Task 3 Step 1; §10.3 → Task 2; §11.1–§11.6 → Tasks 1, 4, 5, 6, 7;
§12's fourteen criteria → the tasks beside each.

**Every deferred probe is resolved.** `FileBuildRelation.STRICT_PREFIX` (not `PREFIX`), `occurrences`
(not `occurrences_of`), and `TimelineOccurrence.pre` are now measured facts, and the code and tests use
them. Three "stop and report" steps remain, but each is a genuine judgment the plan cannot make for the
implementer rather than a name it failed to look up: an unexpected `classify_recovery` verdict (Task 5),
a suite that regresses on the widened digest set (Task 2), and a whitelist test that survives a planted
violation (Task 7).

**Every commit leaves the suite green.** The previous revision's Task 6 committed tests against an
entry point Task 7 would introduce; those tasks are merged into Task 4.

**Type consistency.** `Observation.observe(parent_fd, leaf, *, sink_fd=None, modeled=None)` is called
with that signature in Tasks 3, 4, 5, and 6. `WalkStop` is `(node, path, parent_fd, component,
observed)` in Task 3 and read as such by `_verify_stops` in Task 4. `DescriptorTable.fd_for` raises
`KeyError`, which Task 4 catches and re-raises as `ProtocolError`, while `is_unreachable` answers the
different question. `_build_descriptor_table` is private in both its definition and all four call
sites. `StagedBlob(name, digest, byte_len)` matches `blobs.py:35`, and `Captured.manifest` is the
`tuple[StagedBlob, ...]` `prepare_transaction` takes.

**Claims the earlier revisions made that the code now actually supports.**

- A descriptor is owned or closed, never neither (`_open_and_pin`).
- A directory is never described without enumeration (`modeled` is required — and passed on the
  file-preimage route too, so a file that drifted into a directory refuses rather than raising
  `ProtocolError` for a missing argument).
- No errno selects a state branch. Only a **planned** directory produces a `WalkStop`, and its
  observed entry is what §8 adjudicates. An approved-existing directory that no longer opens is drift
  and refuses through `translated_lookup`, so `EIO` propagates as itself rather than being swallowed
  into a stop.
- **Both** planned-directory outcomes stop the walk. The occupied branch previously did not, which
  left `p/q` neither resolvable nor unreachable and made the plan's own matching-blocker test end in
  `ProtocolError` — the one case §8.2 exists to accept.
- `_stream_into` checks type before falsiness, so a stream returning `None` or `""` is malformed
  rather than an end of file that would validate against a declared empty file.
- The authorization arm builds the **committed-cleanup** fixture ledger #13 actually describes,
  selects the `RemoveScratch` step, supplies exactly the named slot with empty persistent and
  occupancy coverage, and asserts `AuthorizedStep` — not `is not None`, which accepted `HaltPlan`.
- That arm's authorization observation is **fresh**, as ledger #13 requires: a second `Observation`
  taken after classification, from the same held descriptor, while the tombstone is still on disk.
  The earlier revision reused the snapshot's `retained` and unlinked the entry first, which proved
  nothing about a re-observation. Freshness is asserted (`reobserved.identity is not
  retained.identity`) rather than assumed, and it authorizes because
  `_project_identity_relations` compares pairwise relations among an observation's own tokens, never
  a raw token across passes.
- The descriptor table validates every node against **its own** record in `approved.directories`,
  including `WorkRoot()`. `approved.work_base` describes `metadata_root/work`, the parent of the
  `work/<txid>` that `workspace.work_fd` names, so it was the wrong baseline. On ext4 the two carry
  equal values, so the test doctors the proof to make them differ and then drives `_table` **both
  ways**: a wrong parent must not refuse, a wrong child record must. An assertion-only test over
  `approved.directories` passed against the old implementation too, which is why it was replaced.
- `capture_initial_surface` authenticates the workspace — exact type and owning store — **before the
  first write**. `promote_staging` makes the same two checks (`blobs.py:332-335`) but runs inside
  `prepare_transaction`, after capture has already streamed preimages and payloads into
  `staging_fd`. A foreign-store workspace under the same txid is constructible (`create_workspace`
  takes the txid it is given), and both tests assert the staging directory is still empty after the
  refusal.
- A missing parent descriptor is a stop **only if the parent actually stopped**; otherwise it is a
  walk-order defect and raises `ProtocolError`. Silently calling it a stop would manufacture an
  absence inference out of a bug, and §8's basis is a verified blocker.
- The import whitelist reuses `_resolved_imports`, and Task 7 plants four violation forms including
  `from atoms.core import recovery` and a relative import.

**Test counts are measured, not estimated:** 23, 2, 15, 25 cases from 24 functions, 4, 5, 1, 1.

**Every commit runs the full gate set**, including Tasks 5 and 7, which previously committed after a
single file's tests.

**Status synchronization covers the whole current status surface.** `AGENTS.md`, the A6 design, and
A5b's design all carry "A6–A8 remain unimplemented", the last of which
`test_a5_status_is_synchronized_across_authority_documents` asserts — so landing A6 without touching
it leaves a banked design claiming A6 does not exist. `AGENTS.md` separately justifies the
build-stage trap with "A6 supplies no observations", which stops being true here even though the trap
itself stays (A7 owns the executor). And the `README.md` roadmap was already three sub-plans stale
before A6 — it stops at A3, claims A4–A8 are unimplemented, and says no path is mutated — so Task 8
repairs it rather than adding A6 on top of a false list.

The authority design is the fifth document, and the one a forbidden-string grep would have missed: its
header spells the remainder as "A6–A8 (coherent capture, effect/recovery execution, synthetic
exerciser) remain", not "A6–A8 remain unimplemented". Step 1 rewrites it alongside the §6 and §14
amendments, the grep gained a second alternative for it, and the status test asserts the new header
positively over whitespace-flattened text so a reflow cannot break the check.

**Both status guards are scoped to status claims, not to whole files.** The earlier revision scanned
entire documents and could not pass: the A6 design's §3.3 quotes both retired spellings verbatim
(that record is what makes the amendment auditable), and the architecture test files name the
forbidden strings as the strings they forbid. A whole-file scan makes a document fail *because* it
records the change, and makes a guard fail *because* it was written — and the only ways to go green
are to delete the record or drop the check. The test now reads each document's `**Status:**` field or
`## Status` section, and the grep covers only files with no such quotation.

Both were executed against a simulated post-Task-8 tree before this plan was banked: the grep exits 1,
every assertion passes, and each of the five documents left stale in turn — A5b's header, the
authority header, AGENTS, README, the A6 design's header — fails the test. `next(...)` carries no
default, so a renamed heading raises rather than vacuously passing.

Task 8's closing `grep` names its files explicitly and is **not** a `-r` sweep. Two documents legitimately
still contain the string: the historical A5b implementation plan, which records what A5b's status line
said when it landed, and this plan, which quotes it in the steps that retire it. Both are records of past
moments; rewriting either to satisfy a recursive grep would falsify the record, and a grep that can never
pass is worse than no grep at all.
