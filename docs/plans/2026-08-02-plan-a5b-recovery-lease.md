# A5b Recovery-Resolve Lease Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `atoms/coordinator/`, the transaction admission boundary — the recovery-resolve lease
over A4a's lock, orphan reclamation at every entry, the `ProjectApprovedSpec`-only gate with its
bounded txid regeneration loop, preparation to the durable publication COMMIT, and A3 transition
persistence that stops structurally at the first step A7 must execute.

**Architecture:** Five modules under a new `atoms/coordinator/` package. `root.py` owns the whole
package-private resource stack — lock, probe reclamation, binding, store — and is the sole production
`bind_project_volume` call site, passing `CERTIFIED_ALLOWLIST`. `lease.py` owns the `Lease` value and
the protocol that runs over it. `admission.py` owns txid generation, the regeneration loop,
re-resolution across three parent branches, and the shared proof gate. `prepare.py` owns authority
§7.3 steps 2–4. `transitions.py` owns plan-order persistence and the §7.4 barriers. Two functions are
added to `atoms/fs/resolve.py`, `observe_child` and `observe_work_child`, because scratch leaves cannot
go through `PathResolver`.

**Tech Stack:** Python 3.11+, stdlib only (`secrets`, `contextlib`, `dataclasses`), `pytest`, `ruff`,
`pyright`. Builds on A4a's `acquire_project_lock`, `reclaim_probe_survivors`, `bind_project_volume`,
and `CERTIFIED_ALLOWLIST`; A4b's `approve_for_project` and `ProjectApprovedSpec`; A5a's `Store`; A3's
`classify_recovery` and `reduce_recovery_plan_prefix`.

**Design:** [`2026-08-02-a5b-recovery-lease-design.md`](2026-08-02-a5b-recovery-lease-design.md).
**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md).
Where this plan and either document disagree, the design wins over this plan and the authority wins
over both.

## Measured facts this plan is built on

Every shape below was executed against the live tree on 2026-08-02, not read off a type annotation.
Where a task's code depends on one, the task cites it. **If any of these turns out to be false during
implementation, stop and report it — do not adapt around it silently.**

| Fact | Where measured |
| --- | --- |
| `acquire_project_lock` takes `lock_exclusive` → blocking `flock(LOCK_EX)`. A same-process second acquisition **deadlocks**; it does not raise. | `linux.py:81`, `lock.py:230` |
| `read_lookup_constraints(fd, filesystem_type)` takes **two** arguments. | `lookup.py:85` |
| `Store._read_transaction` refuses any public read while the store owns a write transaction. | `connection.py:712-717` |
| `TransitionTransactionState(from_state, to_state, rollback_result, halt_diagnostic)`; `TransitionEffectState(effect_id, from_state, to_state)`. There is no `.state`. | `plan.py:60-74` |
| `_TRANSACTION_RECOVERY_EDGES` excludes `(HALTED, HALTED)`, and `_transition_transaction` refuses a step whose `from_state` is not the current state — so the reduced prefix rejects a second halt. | `reducer.py:210-247` |
| `one_effect_spec` is a **function in `tests/store_support.py`**, not a fixture. | `store_support.py:54` |
| `coherence_findings` requires a `blob` row for every referenced digest, and requires `rollback_result` present exactly when `ROLLED_BACK` and `halt_diagnostic` present exactly when `HALTED`. | `records.py:472-483` |
| For `CreateFileNoClobber("e1", "d/f.txt")` with `d` existing: parent node is `TopologyDirectory(node_id=0)` (**not** `PersistentNode`), scratch leaf is `.#~<txid>.e1.staging`, `work_base` is `None`. | probe, 2026-08-02 |
| For `CreateDirectory("e1", "d")` + `CreateFileNoClobber("e2", "d/f.txt")`: `directories` holds `ApprovedExistingDirectory(ProjectRoot())`, `ApprovedPlannedDirectory(PersistentNode('d'))`, `ApprovedPlannedDirectory(WorkRoot())`; `work_base` is populated. All three §6.4 branches come from this one spec. | probe, 2026-08-02 |
| `classify_recovery` step shapes for the `d/f.txt` spec — the four plans Task 8 uses. | probe, 2026-08-02 |

The four measured plans, verbatim:

| Snapshot | Plan | Steps |
| --- | --- | --- |
| PREPARED, PENDING, live absent, scratch absent | `ActionPlan` ROLL_BACK | `[0]` PREPARED→ROLLING_BACK, `[1]` ROLLING_BACK→ROLLED_BACK (`RESTORED`), `[2]` `DetachActive` |
| PREPARED, PENDING, live **drifted** file, scratch absent | `ActionPlan` ROLL_BACK_REFUSED | `[0]` PREPARED→ROLLING_BACK, `[1]` `PreserveExternal`, `[2]` ROLLING_BACK→ROLLED_BACK, `[3]` `DetachActive` |
| PREPARED, PENDING, scratch **present** | `HaltPlan` | `[0]` PREPARED→HALTED with `halt_diagnostic` |
| APPLYING, STARTED, scratch present | `ActionPlan` ROLL_BACK | `[0]` transition, `[1]` effect, **`[2]` `RemoveScratch`**, `[3]`, `[4]`, `[5]` |
| APPLYING, DONE, live present | `ActionPlan` ROLL_BACK | `[0]` transition, `[1]` effect, **`[2]` `TransformEffectTuple`**, `[3]`–`[6]` |

## Global Constraints

- Work from `~/d/atoms/python`. Gates are `uv run pytest`, `uv run ruff check`, `uv run pyright`.
- **Ruff's default rule set is much broader than `E4/E7/E9/F`** — isort (`I001`), bugbear,
  flake8-simplify, bandit (`S`), `RUF012`, blind-except (`BLE001`). Run `uv run ruff check <file>`
  right after creating each file rather than only at the task gate.
- **One expected transient:** ruff's isort classifies a module as first-party by *path existence*, so a
  test importing `atoms.coordinator.*` before that module exists reports `I001`. Between "write the
  failing test" and "write the module" this is expected; **do not reorder imports to satisfy it.**
- **Three rules the test code here is written around**, all enabled in this project's ruff (`0.16`):
  `SIM117` refuses nested bare `with` statements — write them as one `with A, B:`; `B018` refuses a
  bare attribute expression, so a `pytest.raises` body that only reads an anchor is `_ = lease._store`;
  and `PYI034` refuses `def __enter__(self) -> Lease:` — annotate `Self`.
- **A refusal test must assert which refusal fired.** Asserting only that an exception type was raised
  proves that *something* refused. Where a different defect in the same path would raise the same type,
  the case must also assert the field, constraint, or member its own refusal names. This is carried
  forward from A5a, where it was measured twice: a malformed-payload matrix stayed **8 of 13** green
  when the wrong check was forced to fire, and a six-case setter matrix stayed **6 of 6** green with its
  validator neutered to the identity function. A bare `pytest.raises(Exception)` never satisfies this —
  bind it (`as caught`), which also drops ruff's `B017`.
- **`pytest.raises` goes *outer* in a `with A, B:` whenever the body calls a mutating store method.**
  With `pytest.raises` inner, its `__exit__` suppresses the exception, `transaction()`'s generator
  resumes past its `yield`, `_require_not_poisoned` fires, and that second `ProtocolError` escapes
  outside every `pytest.raises` scope. Measured during A5a.
- **`CERTIFIED_ALLOWLIST` ships empty** (`volume.py:78`) and production binding therefore refuses every
  volume until A8 crash-certifies a tuple. `root.py` names it directly at its single
  `bind_project_volume` call; tests reach the protocol by patching `root.CERTIFIED_ALLOWLIST`, never by
  introducing a parallel allowlist parameter. **#18 stays an architecture assertion over that one call
  site** — as A4a's `test_no_production_caller_of_bind_exists_yet` was — because an empty constant
  cannot be exercised end-to-end until A8.
- **`_recovery_lease` and `Lease` are package-private, and `atoms.coordinator.__all__` is `()`.** A5b
  ships no consumer-facing command — those are authority §12.1's — so nothing is publicly exported yet
  (design §5.0).
- **No new exception type and no `errors.py`** (design §4.1, §9). Reuse `PreconditionRefused`,
  `ProtocolError`, `CapabilityUnavailable`, `TransactionHalted`. The A7 trap is a bare
  `NotImplementedError`.
- **The dependency DAG is `coordinator → {store, fs, core}`, `store → {fs, core}`, `fs → core`.**
  `atoms.store` is importable only from `atoms/coordinator/` and from `atoms/store/` itself; Task 9
  asserts it with both exemptions.
- **Every reference to the lease's resources is `lease._binding` and `lease._store`.** There is no
  public `binding` or `store` property.
- **pyright type-checks the tests** — `[tool.pyright]` sets no `include`. Annotate parameters with the
  real protocol types (`Backend`, `RecoveryPlan`, `Workspace`), never `object`; a deliberate
  wrong-type test passes its argument through `typing.cast`.
- **Every fixture lands in `tests/conftest.py`.** Task 9 adds a fixture-registry guard over
  `test_coordinator_*.py`. Shared *builders* are plain functions in `tests/coordinator_support.py` and
  are imported, not injected — `one_effect_spec` is such a function, not a fixture.
- **No raw `os.fsync`.** Every durability barrier goes through `Backend.flush_file` or
  `Backend.flush_directory`.
- **No blanket `OSError` handler** and **no `except sqlite3.Error`**, extending A5a's guards.
- Filepaths in docs and comments use `~/d/atoms/...`.
- Conventional commits. No AI-attribution trailer or footer.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `src/atoms/coordinator/__init__.py` | **Create.** `__all__ = ()`. Nothing is publicly exported until a consumer command exists. |
| `src/atoms/coordinator/py.typed` | **Create.** Empty marker, matching `atoms/core/`, `atoms/fs/`, `atoms/store/`. |
| `src/atoms/coordinator/root.py` | **Create.** `_recovery_lease(...)` — the whole resource stack and the sole production `CERTIFIED_ALLOWLIST` call site (#18). |
| `src/atoms/coordinator/lease.py` | **Create.** `Lease`, `_reclaim_orphans` (§5.2), `_resolve` and the A7 trap (§5.3). |
| `src/atoms/coordinator/admission.py` | **Create.** `new_txid`, `SCRATCH_ATTEMPTS`, `admit`, the three re-resolution branches, resolver translation, and `_require_admitted` (§6). |
| `src/atoms/coordinator/prepare.py` | **Create.** `open_workspace`, `prepare_transaction` — authority §7.3 steps 2–4 (§7). |
| `src/atoms/coordinator/transitions.py` | **Create.** `persist_plan_prefix` — plan-order persistence and the §7.4 barriers (§8). |
| `src/atoms/fs/resolve.py` | **Modify.** Add `ChildObservation`, `observe_child`, `observe_work_child` (§6.4). |
| `tests/coordinator_support.py` | **Create.** Spec, approval, snapshot, and plan builders shared by the coordinator tiers. |
| `tests/conftest.py` | **Modify.** `coordinator_on`, `leased`. |
| `tests/test_coordinator_lease.py` | **Create.** Tier 1 — entry order, reclamation, the trap, lock duration. |
| `tests/test_coordinator_admission.py` | **Create.** Tier 2 — txid generation, the loop, the three branches, translation. |
| `tests/test_coordinator_prepare.py` | **Create.** Tier 3 — the gates, work-base re-resolution, the publication COMMIT. |
| `tests/test_coordinator_transitions.py` | **Create.** Tier 4 — prefix validation, barrier discipline, the cursor. |
| `tests/coordinator_child.py` | **Create.** The subprocess tier 5 reads from. |
| `tests/test_coordinator_process.py` | **Create.** Tier 5 — fresh-process reclamation (#23) and the trap across a restart. |
| `tests/test_coordinator_architecture.py` | **Create.** Tier 6 — import direction, surface, the #9 entry-point guard, fixture registry. |
| `tests/test_fs_architecture.py` | **Modify.** Replace the two temporary guards (#9, #18). |
| `tests/test_fs_resolve_walk.py` | **Modify.** `observe_child` / `observe_work_child` cases. |
| `pyproject.toml` | **Modify.** `import-names` gains `atoms.coordinator`. |
| `docs/deferred-obligation-ledger.md` | **Modify.** Discharge #7, #18, #21, #23; relabel #12, #17, #19. |
| `AGENTS.md`, the A5b design | **Modify.** A5 status lines. |

**Why `root.py` is separate from `lease.py`.** `root.py` is a composition root: it names the production
constant and owns acquisition and release order. `lease.py` is the protocol that runs over what
`root.py` produced. Keeping the whole stack in `root.py` is what makes #18's assertion a single,
provable call site — a second module that also called `bind_project_volume` would make the guard's
expected set ambiguous.

Nine tasks. Each ends with a deliverable a reviewer could reject while approving its neighbour.

**Tasks 1–3 build the lease** bottom-up: the value and the resource stack, then reclamation, then
resolution and the trap. **Task 4 adds the two observers** to `atoms/fs/`, the one change outside the
new package. **Tasks 5–6 build admission.** **Tasks 7–8 build preparation and persistence.** **Task 9**
is the whole-package guard set, the process tier, the packaging metadata, the ledger, and the status
synchronization.

---

## Task 1: The package, the `Lease`, and the resource stack

**Files:**
- Create: `src/atoms/coordinator/__init__.py`, `src/atoms/coordinator/py.typed`
- Create: `src/atoms/coordinator/lease.py`, `src/atoms/coordinator/root.py`
- Create: `tests/test_coordinator_lease.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_coordinator_lease.py`

**Interfaces:**
- Consumes: A4a `acquire_project_lock(backend, metadata_root) -> HeldProjectLock`,
  `reclaim_probe_survivors(lock) -> None`,
  `bind_project_volume(project_root, lock, *, allowlist, storage) -> ProjectBinding`,
  `CERTIFIED_ALLOWLIST`; A5a `open_store(binding) -> Store`. All three of `HeldProjectLock`,
  `ProjectBinding`, and `Store` are context managers.
- Produces: `Lease` with `_binding: ProjectBinding` and `_store: Store`;
  `_recovery_lease(backend, project_root, metadata_root, storage)` in `root.py`, a context manager
  yielding `Lease`.

- [ ] **Step 1: Add the fixtures**

In `tests/conftest.py`, after the existing `store_on` fixture. `itertools` is already imported there.

```python
@pytest.fixture
def coordinator_on(ext4_volume, ext4_project_root, test_storage_profile):
    """The raw ingredients `_recovery_lease` builds its own stack from.

    Unlike `store_on`, this fixture binds nothing: the lease owns lock acquisition,
    probe reclamation, binding, and store opening, and a fixture that pre-bound them
    would leave four of the six entry-order steps unexercised. Each call names a fresh
    metadata root under the same ext4 volume, so two calls model two projects rather
    than a restart of one.
    """
    counter = itertools.count()

    def ingredients():
        from atoms.fs.linux import LinuxBackend

        metadata_root = ext4_volume / f"coordinator-metadata-{next(counter)}"
        return (
            LinuxBackend(),
            str(ext4_project_root),
            str(metadata_root),
            test_storage_profile,
        )

    return ingredients


@pytest.fixture
def leased(coordinator_on, monkeypatch):
    """An entered production lease, with `CERTIFIED_ALLOWLIST` patched for the volume.

    The constant ships empty and production binding fails closed, so every test drives
    the real composition root with the module attribute replaced. Patching the constant
    rather than threading a parameter keeps `root.py` the single bind call site that
    ledger #18 asserts over; a test-only allowlist parameter would create a second one.
    """
    import contextlib

    from atoms.coordinator import root
    from atoms.fs.lock import acquire_project_lock
    from tests.fs_support import build_test_allowlist

    @contextlib.contextmanager
    def enter():
        backend, project_root, metadata_root, storage = coordinator_on()
        with acquire_project_lock(backend, metadata_root) as probe:
            allowlist = build_test_allowlist(probe, project_root, storage)
        monkeypatch.setattr(root, "CERTIFIED_ALLOWLIST", allowlist)
        with root._recovery_lease(
            backend, project_root, metadata_root, storage
        ) as lease:
            yield lease

    return enter
```

The probe lock is acquired and released before the lease: `build_test_allowlist` needs a
`HeldProjectLock` to reach the backend, and `flock` is released on exit, so the lease's own
acquisition is uncontended.

- [ ] **Step 2: Write the failing test**

Create `tests/test_coordinator_lease.py`:

```python
"""A5b tier 1 -- the lease's entry order, reclamation, resolution, and duration."""

from __future__ import annotations

import pytest

from atoms.core.errors import ProtocolError


def test_the_lease_yields_a_working_store(leased):
    with leased() as lease:
        assert lease._store.read_active() is None


def test_the_lease_spends_the_store_on_exit(leased):
    with leased() as lease:
        escaped = lease
    with pytest.raises(ProtocolError) as caught:
        escaped._store.read_active()
    assert "closed" in str(caught.value)


def test_the_lease_holds_no_public_binding_or_store(leased):
    with leased() as lease:
        assert not hasattr(lease, "binding")
        assert not hasattr(lease, "store")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_coordinator_lease.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atoms.coordinator'`

- [ ] **Step 4: Create the package marker files**

`src/atoms/coordinator/py.typed` — empty.

`src/atoms/coordinator/__init__.py`:

```python
"""A5b -- the coordinator (design §4.1).

Nothing is exported. A5b ships no consumer-facing command: every entry point accepts a
package-private `Lease`, and authority §12.1 owns the first real consumer. `__all__`
stays empty until one exists.
"""

from __future__ import annotations

__all__ = ()
```

- [ ] **Step 5: Write `lease.py`**

```python
"""The lease value and the protocol that runs over it (design §5)."""

from __future__ import annotations

from dataclasses import dataclass

from atoms.fs.binding import ProjectBinding
from atoms.store.connection import Store


@dataclass(frozen=True, slots=True)
class Lease:
    """Borrowed resources, not owned ones.

    `_recovery_lease` closes both in reverse acquisition order; a `Lease` that outlives
    its `with` block therefore references spent objects, and every A5a call through it
    refuses. The escape is caught by the layer below rather than by a flag here.
    """

    _binding: ProjectBinding
    _store: Store
```

- [ ] **Step 6: Write `root.py`**

```python
"""The production composition root and the lease's resource stack (design §4.1, §5.1)."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from atoms.coordinator.lease import Lease
from atoms.fs.backend import Backend
from atoms.fs.binding import bind_project_volume
from atoms.fs.bootstrap import reclaim_probe_survivors
from atoms.fs.lock import acquire_project_lock
from atoms.fs.volume import CERTIFIED_ALLOWLIST, StorageProfile
from atoms.store.connection import open_store


@contextlib.contextmanager
def _recovery_lease(
    backend: Backend,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
) -> Iterator[Lease]:
    """Design §5.1's entry order, exactly.

    The one production call site that names `CERTIFIED_ALLOWLIST`. It is empty until A8
    crash-certifies a configuration tuple, so this path refuses every real volume today.
    That is the intended fail-closed behaviour, and it is why ledger #18 is proved by an
    architecture assertion over this call rather than by an end-to-end run.
    """
    with acquire_project_lock(backend, metadata_root) as lock:
        # Ledger #17 requires reclamation at EVERY lease entry. bind_project_volume
        # reclaims at its own step 4, which it reaches only after checks that can
        # refuse first (binding.py:169-172), so the lease calls it directly.
        reclaim_probe_survivors(lock)
        with bind_project_volume(
            project_root, lock, allowlist=CERTIFIED_ALLOWLIST, storage=storage
        ) as binding, open_store(binding) as store:
            yield Lease(_binding=binding, _store=store)
```

Reclamation and resolution join this body in Tasks 2 and 3.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_coordinator_lease.py -v`
Expected: PASS, 3 tests.

- [ ] **Step 8: Run the gates**

Run: `uv run ruff check && uv run pyright && uv run pytest -q`
Expected: all clean.

- [ ] **Step 9: Commit**

```bash
git add src/atoms/coordinator tests/test_coordinator_lease.py tests/conftest.py
git commit -m "feat(coordinator): open the lease over lock, binding, and store"
```

---

## Task 2: Orphan reclamation

**Files:**
- Modify: `src/atoms/coordinator/lease.py`, `src/atoms/coordinator/root.py`
- Modify: `tests/test_coordinator_lease.py`
- Create: `tests/coordinator_support.py`

**Interfaces:**
- Consumes: A5a `Store.list_workspaces() -> tuple[str, ...]`,
  `Store.reopen_workspace(txid) -> Workspace`, `Store.remove_workspace(workspace) -> None`,
  `Store.list_unindexed_blobs() -> tuple[str, ...]`, `Store.remove_unindexed_blob(digest) -> None`,
  `Store.read_record(txid) -> StoredRecord | None`.
- Produces: `_reclaim_orphans(store) -> tuple[tuple[str, ...], tuple[str, ...]]`, returning the
  removed workspace txids and blob digests, called from `_recovery_lease` before resolution.

- [ ] **Step 1: Create `tests/coordinator_support.py`**

Start it with only what this task needs; later tasks extend it.

```python
"""Builders shared by every coordinator tier.

Plain functions, not fixtures: the fixture-registry guard requires every fixture to live
in `tests/conftest.py`, and these are values a test constructs rather than resources a
test needs torn down.
"""

from __future__ import annotations

import os

from atoms.core.compiler import CompiledSpec, compile_spec
from atoms.core.effects import CreateDirectory, CreateFileNoClobber
from atoms.core.fingerprint import ABSENT, DirectoryState
from atoms.core.spec import TransactionSpec, build_spec
from atoms.coordinator.lease import Lease
from tests.store_support import file_state

AFTER = b"after"
POST = file_state(AFTER)
DIRECTORY_POST = DirectoryState(mode=0o755)


def make_child_directory(lease: Lease, name: str = "d") -> None:
    """Create one directory in project space, tolerating an existing one.

    `compiled_for` declares `d/f.txt`, whose parent must already exist for A4b to
    approve it as an `ApprovedExistingDirectory` -- the branch of §6.4 that has an
    identity to compare against.
    """
    try:
        os.mkdir(name, dir_fd=lease._binding.project_root_fd)
    except FileExistsError:
        pass


def file_spec() -> TransactionSpec:
    """One `CreateFileNoClobber` under an existing directory.

    Measured shape: parent node `TopologyDirectory(node_id=0)`, scratch leaf
    `.#~<txid>.e1.staging`, `work_base` None.
    """
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "1" * 64,
        initial_surface={"d/f.txt": ABSENT},
        final_surface={"d/f.txt": POST},
        effects=[CreateFileNoClobber(effect_id="e1", path="d/f.txt", post=POST)],
    )


def directory_spec() -> TransactionSpec:
    """A created directory with a child, so all three §6.4 branches appear at once.

    Measured shape: `ApprovedExistingDirectory(ProjectRoot())`,
    `ApprovedPlannedDirectory(PersistentNode('d'))`,
    `ApprovedPlannedDirectory(WorkRoot())`, and a populated `work_base`.
    """
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "2" * 64,
        initial_surface={"d": ABSENT, "d/f.txt": ABSENT},
        final_surface={"d": DIRECTORY_POST, "d/f.txt": POST},
        effects=[
            CreateDirectory(effect_id="e1", path="d", post=DIRECTORY_POST),
            CreateFileNoClobber(effect_id="e2", path="d/f.txt", post=POST),
        ],
    )


def compiled_for(lease: Lease) -> CompiledSpec:
    make_child_directory(lease)
    return compile_spec(file_spec())


def compiled_creating_a_directory(lease: Lease) -> CompiledSpec:
    """`d` must NOT exist: A4b approves it as planned only while it is absent."""
    _ = lease
    return compile_spec(directory_spec())
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_coordinator_lease.py`:

```python
def test_reclamation_removes_orphans_and_spares_referenced_scratch(leased):
    from atoms.coordinator.lease import _reclaim_orphans
    from tests.store_support import one_effect_spec

    with leased() as lease:
        lease._store.create_workspace("orphan1").close()
        lease._store.create_workspace("kept1").close()
        with lease._store.transaction() as txn:
            txn.insert_record("kept1", one_effect_spec())

        workspaces, blobs = _reclaim_orphans(lease._store)

        assert workspaces == ("orphan1",)
        assert blobs == ()
        assert lease._store.list_workspaces() == ("kept1",)


def test_reclamation_removes_an_unindexed_blob(leased, promoted_blob_digest):
    from atoms.coordinator.lease import _reclaim_orphans

    with leased() as lease:
        assert lease._store.list_unindexed_blobs() == ()
```

`promoted_blob_digest` does not exist — delete that second test and instead cover the blob half
concretely, because a blob is unindexed exactly when it is on disk with no `blob` row, which the store
only produces by a crash cut. Replace it with:

```python
def test_reclamation_reports_both_kinds_of_orphan(leased):
    """`list_unindexed_blobs` already means 'no blob row', so nothing a durable record
    references can appear in it; the helper's contract is to drain whatever it returns."""
    from atoms.coordinator.lease import _reclaim_orphans

    with leased() as lease:
        lease._store.create_workspace("orphan1").close()

        workspaces, blobs = _reclaim_orphans(lease._store)

        assert workspaces == ("orphan1",)
        assert blobs == ()
        assert lease._store.list_workspaces() == ()
        assert lease._store.list_unindexed_blobs() == ()
```

The blob path is proved end-to-end in Task 9's process tier, where a real cut leaves one behind.

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_coordinator_lease.py -k reclamation -v`
Expected: FAIL — `ImportError: cannot import name '_reclaim_orphans'`

- [ ] **Step 4: Implement `_reclaim_orphans`**

Add to `lease.py`, below `Lease`:

```python
def _reclaim_orphans(store: Store) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Ledger #23: reclaim authority §7.3's pre-COMMIT leaves under the held lock.

    A workspace is orphan exactly when no `transaction_record` row names its txid, and
    `list_unindexed_blobs` already means "no `blob` row" -- so nothing a durable record
    references is ever at risk. Returns what was removed, so a caller can assert it.
    """
    removed_workspaces: list[str] = []
    for txid in store.list_workspaces():
        if store.read_record(txid) is not None:
            continue
        store.remove_workspace(store.reopen_workspace(txid))
        removed_workspaces.append(txid)

    removed_blobs: list[str] = []
    for digest in store.list_unindexed_blobs():
        store.remove_unindexed_blob(digest)
        removed_blobs.append(digest)

    return tuple(removed_workspaces), tuple(removed_blobs)
```

- [ ] **Step 5: Call it from `_recovery_lease`**

In `root.py`, import `_reclaim_orphans` alongside `Lease` and replace the innermost body. This is a
fragment of the existing `with` statement, not a standalone block — keep its indentation:

```text
        ) as binding, open_store(binding) as store:
            # Reclamation precedes resolution: orphans are unreferenced by definition,
            # and #23 says "at every lease entry", which holds only if it runs even
            # when resolution then refuses, halts, or traps.
            _reclaim_orphans(store)
            yield Lease(_binding=binding, _store=store)
```

- [ ] **Step 6: Run to verify it passes**

Run: `uv run pytest tests/test_coordinator_lease.py -v`
Expected: PASS.

- [ ] **Step 7: Prove the spare is load-bearing**

Temporarily delete the `if store.read_record(txid) is not None: continue` guard, re-run, and confirm
`test_reclamation_removes_orphans_and_spares_referenced_scratch` fails on `workspaces == ("orphan1",)`.
Restore, confirm `git status --porcelain` is empty, and re-run. Report the observed failure message.

- [ ] **Step 8: Commit**

```bash
git add src/atoms/coordinator tests/test_coordinator_lease.py tests/coordinator_support.py
git commit -m "feat(coordinator): reclaim orphan scratch at every lease entry"
```

---

## Task 3: Resolution, the A7 trap, and lock duration

**Files:**
- Modify: `src/atoms/coordinator/lease.py`, `src/atoms/coordinator/root.py`
- Modify: `tests/test_coordinator_lease.py`

**Interfaces:**
- Consumes: A5a `Store.read_active() -> StoredRecord | None`.
- Produces: `_resolve(store) -> None`, raising `NotImplementedError` when a record is live; called
  from `_recovery_lease` after `_reclaim_orphans`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_live_record_traps_at_the_next_lease_entry(leased):
    from atoms.coordinator.lease import _resolve
    from tests.store_support import one_effect_spec

    with leased() as lease:
        with lease._store.transaction() as txn:
            txn.insert_record("tx1", one_effect_spec())
            txn.set_active("tx1")

        with pytest.raises(NotImplementedError) as caught:
            _resolve(lease._store)

        assert str(caught.value) == "recovery execution is not implemented until A7"


def test_the_trap_leaves_the_logical_transaction_state_unchanged(leased):
    from atoms.coordinator.lease import _resolve
    from tests.store_support import one_effect_spec

    with leased() as lease:
        with lease._store.transaction() as txn:
            txn.insert_record("tx1", one_effect_spec())
            txn.set_active("tx1")
        before = lease._store.read_active()

        with pytest.raises(NotImplementedError):
            _resolve(lease._store)

        assert lease._store.read_active() == before


def test_no_active_record_resolves_quietly(leased):
    from atoms.coordinator.lease import _resolve

    with leased() as lease:
        assert _resolve(lease._store) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_coordinator_lease.py -k trap -v`
Expected: FAIL — `ImportError: cannot import name '_resolve'`

- [ ] **Step 3: Implement `_resolve`**

```python
def _resolve(store: Store) -> None:
    """Design §5.3 -- a temporary build-stage trap, not a domain refusal.

    `classify_recovery` needs filesystem observations (ledger #13, owned by A6) and a
    plan's mutating steps need an executor (A7). Neither exists, so a live record
    cannot be resolved and must not be advanced past. Removing this raise is what
    discharges ledger #17's second half.

    The guarantee is scoped to logical transaction state: nothing here writes a record,
    an effect row, or the active pointer. Reclamation already ran and did remove
    unreferenced scratch, which is not transaction state.
    """
    if store.read_active() is not None:
        raise NotImplementedError("recovery execution is not implemented until A7")
```

- [ ] **Step 4: Call it from `_recovery_lease`**

Another fragment of the same `with` body — `_resolve` goes between reclamation and the yield:

```text
            _reclaim_orphans(store)
            _resolve(store)
            yield Lease(_binding=binding, _store=store)
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_coordinator_lease.py -v`
Expected: PASS.

- [ ] **Step 6: Write the lock-duration test**

`acquire_project_lock` takes `Backend.lock_exclusive`, which is a **blocking** `flock(LOCK_EX)`
(`linux.py:81`). A second acquisition in this process would deadlock, not raise, so contention is
proved from a second process — the same `_CONTENDER` shape `tests/test_fs_lock.py:430` already uses.

```python
_CONTENDER = (
    "import fcntl, sys\n"
    "handle = open(sys.argv[1], 'r+')\n"
    "try:\n"
    "    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "except BlockingIOError:\n"
    "    sys.exit(3)\n"
    "sys.exit(0)\n"
)


def _contend(metadata_root: str) -> int:
    return subprocess.run(
        [sys.executable, "-c", _CONTENDER, str(Path(metadata_root) / "lock")],
        check=False,
        timeout=30,
    ).returncode


def test_the_lease_holds_the_lock_for_its_whole_duration(leased):
    """Authority §7.1: the lock spans the whole write phase, not merely entry."""
    with leased() as lease:
        metadata_root = os.readlink(
            f"/proc/self/fd/{lease._binding.metadata_root_fd}"
        )
        assert _contend(metadata_root) == 3
        # Still held after a store write, not merely at entry.
        lease._store.create_workspace("held").close()
        assert _contend(metadata_root) == 3

    assert _contend(metadata_root) == 0
```

Add `import os`, `import subprocess`, `import sys`, and `from pathlib import Path` to the module's
import block.

- [ ] **Step 7: Run the gates and commit**

```bash
uv run ruff check && uv run pyright && uv run pytest -q
git add src/atoms/coordinator tests/test_coordinator_lease.py
git commit -m "feat(coordinator): trap on a live record until A7 lands"
```

---

## Task 4: `observe_child` and `observe_work_child`

**Files:**
- Modify: `src/atoms/fs/resolve.py`
- Modify: `tests/test_fs_resolve_walk.py`

**Interfaces:**
- Consumes: `FilesystemIdentity` and `_identity` (`resolve.py:33`, `:103`), `DirectoryConstraints` and
  `read_lookup_constraints(fd, filesystem_type)` (`lookup.py:45`, `:85`), `close_all`,
  `WORK_DIRECTORY` — all already imported by `resolve.py`.
- Produces: `ChildObservation(parent_identity, parent_constraints, present)`,
  `observe_child(binding, parent_path, leaf) -> ChildObservation`, and
  `observe_work_child(binding, leaf) -> ChildObservation`.

**Why this lives in `atoms/fs/` and not the coordinator.** `PathResolver.resolve()` cannot resolve a
scratch path: `validate_path` rejects any component for which `aliases_scratch_sigil` holds
(`atoms/core/paths.py:40`). And `ApprovedScratch` carries `(effect_id, role, parent_node, leaf)` — a
parent node and a leaf, never a resolvable path. Duplicating traversal inside the coordinator would put
syscalls in a package whose job is judgment.

**Why two functions rather than one.** `observe_child` is project-relative, rooted at
`binding.project_root_fd`. `observe_work_child` is rooted at engine-owned
`metadata_root/`+`WORK_DIRECTORY`. These are different namespaces with different containment rules; one
function switching coordinate systems on a magic argument value is exactly the ambiguity design §9.4
exists to prevent. Design §6.4's table already says the `WorkRoot` branch "never enters
`observe_child`" — this names the mechanism it uses instead.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fs_resolve_walk.py`. Check the module's existing imports before adding; it
already imports `os` and `pytest`.

```python
def test_observe_child_reports_absence_with_the_parent_facts(ext4_bound_volume):
    from atoms.fs.resolve import observe_child

    with ext4_bound_volume() as binding:
        observed = observe_child(binding, "", "nothing-here")

        assert observed.present is False
        assert observed.parent_identity.device > 0
        assert observed.parent_constraints.name_max > 0


def test_observe_child_reports_presence_of_an_existing_leaf(ext4_bound_volume):
    from atoms.fs.resolve import observe_child

    with ext4_bound_volume() as binding:
        os.close(
            os.open(
                "occupied",
                os.O_CREAT | os.O_WRONLY,
                0o644,
                dir_fd=binding.project_root_fd,
            )
        )

        assert observe_child(binding, "", "occupied").present is True


def test_observe_child_reports_a_leaf_under_a_nested_parent(ext4_bound_volume):
    from atoms.fs.resolve import observe_child

    with ext4_bound_volume() as binding:
        os.mkdir("d", dir_fd=binding.project_root_fd)
        root_identity = observe_child(binding, "", "d").parent_identity

        observed = observe_child(binding, "d", ".#~tx01.e1.staging")

        assert observed.present is False
        assert observed.parent_identity != root_identity


def test_observe_child_accepts_a_leaf_the_path_grammar_refuses(ext4_bound_volume):
    """The whole reason this function exists: `validate_path` rejects the sigil."""
    from atoms.core.errors import SpecValidationError
    from atoms.core.paths import require_rel_path
    from atoms.fs.resolve import observe_child

    with ext4_bound_volume() as binding:
        with pytest.raises(SpecValidationError):
            require_rel_path("path", ".#~tx01.e1.staging")

        assert observe_child(binding, "", ".#~tx01.e1.staging").present is False


@pytest.mark.parametrize(
    "leaf", ["", "a/b", ".", "..", "/"], ids=["empty", "separator", "dot", "dotdot", "slash"]
)
def test_observe_child_refuses_a_leaf_that_is_not_one_component(ext4_bound_volume, leaf):
    from atoms.core.errors import ProtocolError
    from atoms.fs.resolve import observe_child

    with ext4_bound_volume() as binding:
        with pytest.raises(ProtocolError) as caught:
            observe_child(binding, "", leaf)

        assert "single non-dot path component" in str(caught.value)


def test_observe_child_refuses_a_vanished_parent(ext4_bound_volume):
    from atoms.core.errors import PreconditionRefused
    from atoms.fs.resolve import observe_child

    with ext4_bound_volume() as binding:
        with pytest.raises(PreconditionRefused) as caught:
            observe_child(binding, "gone", "leaf")

        assert "gone" in str(caught.value)


def test_observe_work_child_reads_the_engine_owned_work_base(ext4_bound_volume):
    from atoms.fs.resolve import PathResolver, observe_work_child

    with ext4_bound_volume() as binding:
        expected = PathResolver(binding).work_base_facts()

        observed = observe_work_child(binding, "tx01")

        assert observed.present is False
        assert observed.parent_identity == expected.identity
        assert observed.parent_constraints == expected.constraints
```

The last case is the one that matters for #19: it pins `observe_work_child` to the *same* facts A4b
recorded in `ApprovedWorkBase`, so a comparison against that baseline is a comparison of like with
like.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_fs_resolve_walk.py -k "observe_child or observe_work_child" -v`
Expected: FAIL — `ImportError: cannot import name 'observe_child'`

- [ ] **Step 3: Lift the contradiction set to module scope**

`PathResolver._NAMESPACE_CONTRADICTIONS` is a class attribute (`resolve.py:145`). Both new functions
need the same set. Move it to module level next to `_LINUX` and have the class reference the module
constant, rather than defining a second copy:

```python
_NAMESPACE_CONTRADICTIONS = frozenset(
    {errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EXDEV}
)
```

Then in `PathResolver`, replace the class-attribute assignment with
`_NAMESPACE_CONTRADICTIONS = _NAMESPACE_CONTRADICTIONS` — no: delete the class attribute entirely and
change its two uses inside the class to the module name. Run the full `tests/test_fs_resolve_*.py`
suite after this edit and before writing anything new; it must stay green.

- [ ] **Step 4: Implement the observation core**

Add to `resolve.py`, after `DirectoryFacts`:

```python
@dataclass(frozen=True, slots=True)
class ChildObservation:
    """One open parent answers both questions design §6.4 asks of it.

    `DirectoryConstraints` already bundles `lookup_proof` and `name_max`, so a single
    comparison against an approved directory covers identity, `LookupProof`, and
    `NAME_MAX` together.
    """

    parent_identity: FilesystemIdentity
    parent_constraints: DirectoryConstraints
    present: bool


def _require_leaf(leaf: str) -> None:
    if type(leaf) is not str or not leaf or "/" in leaf or leaf in {".", ".."}:
        raise ProtocolError(
            f"leaf {leaf!r} must be a single non-dot path component; it is never "
            "split, because a scratch leaf aliases the reserved sigil and the path "
            "grammar would refuse it"
        )


def _filesystem_type(binding: ProjectBinding) -> str:
    configuration = binding.evidence.configuration
    if configuration.backend_id != _LINUX:
        raise CapabilityUnavailable(
            f"backend {configuration.backend_id!r} is not {_LINUX!r}; lookup "
            "constraints are read with Linux ext4 flag semantics"
        )
    return configuration.filesystem_type


def _observe_open_child(
    parent_fd: int, filesystem_type: str, leaf: str
) -> ChildObservation:
    """Both observers' shared core. The descriptor is borrowed, never closed here."""
    identity = _identity(os.fstat(parent_fd))
    constraints = read_lookup_constraints(parent_fd, filesystem_type)
    try:
        os.lstat(leaf, dir_fd=parent_fd)
    except FileNotFoundError:
        present = False
    else:
        present = True
    return ChildObservation(
        parent_identity=identity, parent_constraints=constraints, present=present
    )
```

- [ ] **Step 5: Implement the project-relative walk**

`PathResolver.resolve()`'s walk is not callable in isolation — it accumulates `ResolvedHop`s and
returns a `ResolvedPrefix` at the first missing component, which is the opposite of what a re-resolution
needs. Reuse its *primitive* instead: `backend.open_child_directory`, the same guarded
`RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_XDEV` traversal (`linux.py:35-43`), so a symlink, a
mount crossing, or an escape refuses here exactly as it would there.

```python
def _open_project_relative(
    binding: ProjectBinding, parent_path: str
) -> tuple[int, bool]:
    """A descriptor for one project-relative directory, and whether the caller owns it.

    `""` is the project root, whose descriptor the binding owns; returning it with
    `owned=False` is what stops this function from closing a resource it borrowed.
    """
    root_fd = binding.project_root_fd
    if parent_path == "":
        return root_fd, False
    try:
        require_rel_path("parent_path", parent_path)
    except SpecValidationError as caught:
        raise ProtocolError(
            f"observe_child requires a well-formed project-relative parent: {caught}"
        ) from caught

    backend = binding.backend
    parent_fd = root_fd
    owned: int | None = None
    try:
        for component in parent_path.split("/"):
            try:
                child = backend.open_child_directory(parent_fd, component)
            except OSError as caught:
                if caught.errno in _NAMESPACE_CONTRADICTIONS:
                    raise PreconditionRefused(
                        f"the approved parent {parent_path!r} no longer resolves at "
                        f"component {component!r}: {caught}"
                    ) from caught
                raise
            # Reassign before releasing, so a failing close cannot strand `child`.
            previous, owned = owned, child
            parent_fd = child
            if previous is not None:
                close_all((previous,))
    except BaseException:
        if owned is not None:
            close_all((owned,))
        raise
    return parent_fd, True


def observe_child(
    binding: ProjectBinding, parent_path: str, leaf: str
) -> ChildObservation:
    """Observe one named child of one project-relative parent.

    `parent_path` is project-relative throughout; the project root is `""`. The leaf is
    not a path and is never split.
    """
    _require_leaf(leaf)
    filesystem_type = _filesystem_type(binding)
    parent_fd, owned = _open_project_relative(binding, parent_path)
    try:
        return _observe_open_child(parent_fd, filesystem_type, leaf)
    finally:
        if owned:
            close_all((parent_fd,))
```

- [ ] **Step 6: Implement the work-base observer**

```python
def observe_work_child(binding: ProjectBinding, leaf: str) -> ChildObservation:
    """Observe one named child of engine-owned `metadata_root/work`.

    Ledger #19's mechanism for the `WorkRoot` branch: the returned parent facts are the
    same pair `PathResolver.work_base_facts()` recorded in `ApprovedWorkBase`, so the
    coordinator compares like with like. A separate entry point from `observe_child`
    because this is the metadata namespace, where project containment does not apply.
    """
    _require_leaf(leaf)
    filesystem_type = _filesystem_type(binding)
    backend = binding.backend
    try:
        fd = backend.open_child_directory(
            binding.metadata_root_fd, WORK_DIRECTORY
        )
    except OSError as caught:
        if caught.errno in _NAMESPACE_CONTRADICTIONS:
            raise ProtocolError(
                f"engine-owned metadata_root/{WORK_DIRECTORY} is missing or "
                f"malformed: {caught}"
            ) from caught
        raise
    try:
        return _observe_open_child(fd, filesystem_type, leaf)
    finally:
        close_all((fd,))
```

A missing `work/` is a `ProtocolError`, not a `PreconditionRefused`: `ensure_metadata_layout` created it
under the lock this lease still holds, so its absence is an engine bug rather than external drift. This
matches `PathResolver.work_base_facts()`'s own choice (`resolve.py:207-211`).

- [ ] **Step 7: Run to verify it passes**

Run: `uv run pytest tests/test_fs_resolve_walk.py -v`
Expected: PASS, including every pre-existing case in the module.

- [ ] **Step 8: Prove the root descriptor is not closed**

Add:

```python
def test_observe_child_does_not_close_the_borrowed_root(ext4_bound_volume):
    from atoms.fs.resolve import observe_child

    with ext4_bound_volume() as binding:
        before = os.fstat(binding.project_root_fd)
        observe_child(binding, "", "nothing-here")
        assert os.fstat(binding.project_root_fd) == before
```

Then force the bug: change `if owned:` to `if True:` in `observe_child`, re-run, and confirm this test
fails with `OSError: [Errno 9] Bad file descriptor`. Restore and confirm `git status --porcelain` is
clean apart from the intended additions. Report the observed failure.

- [ ] **Step 9: Run the gates and commit**

```bash
uv run ruff check && uv run pyright && uv run pytest -q
git add src/atoms/fs/resolve.py tests/test_fs_resolve_walk.py
git commit -m "feat(fs): observe one named child of one open parent"
```

---

## Task 5: txid generation and the bounded loop

**Files:**
- Create: `src/atoms/coordinator/admission.py`
- Create: `tests/test_coordinator_admission.py`
- Modify: `tests/coordinator_support.py`

**Interfaces:**
- Consumes: A1 `is_valid_identifier`; A4b `approve_for_project(compiled, context)`,
  `ProjectContext(binding, txid)`, `ProjectApprovedSpec`; A5a `Store.read_record(txid)`.
- Produces: `new_txid() -> str`, `SCRATCH_ATTEMPTS = 3`,
  `admit(lease, compiled) -> ProjectApprovedSpec`, `_require_admitted(lease, approved) -> None`, and
  `_occupied_scratch(lease, approved) -> tuple[str, ...]`, stubbed to `()` here and filled by Task 6.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_coordinator_admission.py`:

```python
"""A5b tier 2 -- admission: generation, the loop, re-resolution, and translation."""

from __future__ import annotations

import pytest

from atoms.core.errors import PreconditionRefused, ProtocolError
from atoms.core.identifiers import is_valid_identifier
from tests.coordinator_support import compiled_for
from tests.store_support import one_effect_spec


def test_new_txid_is_a_valid_identifier():
    from atoms.coordinator.admission import new_txid

    for _ in range(64):
        assert is_valid_identifier(new_txid())


def test_new_txid_does_not_repeat():
    from atoms.coordinator.admission import new_txid

    assert len({new_txid() for _ in range(512)}) == 512


def test_a_candidate_a_durable_record_owns_is_discarded(leased, monkeypatch):
    from atoms.coordinator import admission

    with leased() as lease:
        with lease._store.transaction() as txn:
            txn.insert_record("taken", one_effect_spec())

        issued = iter(["taken", "free"])
        monkeypatch.setattr(admission, "new_txid", lambda: next(issued))

        approved = admission.admit(lease, compiled_for(lease))

        assert approved.txid == "free"


def test_every_candidate_owned_by_a_record_exhausts_the_bound(leased, monkeypatch):
    from atoms.coordinator import admission

    with leased() as lease:
        for txid in ("a", "b", "c"):
            with lease._store.transaction() as txn:
                txn.insert_record(txid, one_effect_spec())

        issued = iter(["a", "b", "c"])
        monkeypatch.setattr(admission, "new_txid", lambda: next(issued))

        with pytest.raises(PreconditionRefused) as caught:
            admission.admit(lease, compiled_for(lease))

        message = str(caught.value)
        assert "no usable txid after 3 attempts" in message
        # It must NOT claim scratch occupancy, which is not why it refused.
        assert "scratch occupied" not in message


def test_the_proof_binds_to_the_lease_that_issued_it(leased):
    from atoms.coordinator.admission import _require_admitted, admit

    with leased() as lease:
        approved = admit(lease, compiled_for(lease))

        assert _require_admitted(lease, approved) is None


def test_a_proof_from_another_binding_is_refused(leased):
    from atoms.coordinator.admission import _require_admitted, admit

    with leased() as first, leased() as second:
        approved = admit(first, compiled_for(first))

        with pytest.raises(ProtocolError) as caught:
            _require_admitted(second, approved)

        assert "binding" in str(caught.value)


def test_a_raw_compiled_spec_is_refused(leased):
    from typing import cast

    from atoms.fs.approval import ProjectApprovedSpec

    from atoms.coordinator.admission import _require_admitted

    with leased() as lease:
        raw = cast(ProjectApprovedSpec, compiled_for(lease))

        with pytest.raises(ProtocolError) as caught:
            _require_admitted(lease, raw)

        assert "ProjectApprovedSpec" in str(caught.value)
```

The `cast` is deliberate and required: pyright type-checks the tests, and passing a `CompiledSpec`
where a `ProjectApprovedSpec` is annotated is exactly the mistake the gate exists to catch at runtime.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_coordinator_admission.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atoms.coordinator.admission'`

- [ ] **Step 3: Implement `admission.py`**

```python
"""The transaction admission gate (design §6)."""

from __future__ import annotations

import secrets

from atoms.core.compiler import CompiledSpec
from atoms.core.errors import PreconditionRefused, ProtocolError
from atoms.coordinator.lease import Lease
from atoms.fs.approval import ProjectApprovedSpec, ProjectContext, approve_for_project

#: Design §6.3. A constant so a test can drive the loop to exhaustion; an unbounded
#: loop would have no reachable refusal to test.
SCRATCH_ATTEMPTS = 3


def new_txid() -> str:
    """Thirty-two hex characters satisfy A1's `^[A-Za-z0-9_-]{1,64}$`.

    The coordinator owns generation because it is the only layer that can know a
    regeneration is needed; the consumer never holds one.
    """
    return secrets.token_hex(16)


def _require_admitted(lease: Lease, approved: ProjectApprovedSpec) -> None:
    """Ledger #9's enforcement half, in one place.

    Every post-approval transaction-stage entry point opens with this call, and Task 9's
    architecture guard asserts that it is each one's first statement. Stated once rather
    than repeated so a later entry point cannot implement a subtly weaker version.
    """
    if type(approved) is not ProjectApprovedSpec:
        raise ProtocolError(
            f"expected exactly ProjectApprovedSpec, got {type(approved).__name__}; a "
            "raw TransactionSpec, CompiledSpec, or synthetic A3 snapshot carries no "
            "rooted project proof"
        )
    if approved.binding is not lease._binding:
        raise ProtocolError(
            "the proof's binding is not this lease's binding; a proof resolved against "
            "one project volume authorizes nothing on another"
        )


def admit(lease: Lease, compiled: CompiledSpec) -> ProjectApprovedSpec:
    """Design §6.3.

    Approval lives inside the loop because ledger #21 says regeneration voids the
    proof: each attempt produces a wholly fresh one, and names are never substituted
    into an existing proof.
    """
    occupied: tuple[str, ...] = ()
    for _ in range(SCRATCH_ATTEMPTS):
        txid = new_txid()
        if lease._store.read_record(txid) is not None:
            # A detached terminal record owns its txid permanently while leaving no
            # scratch behind, so occupancy would find nothing and the collision would
            # surface much later as a primary-key failure inside the publication
            # COMMIT. Reset `occupied` so the refusal cannot name a previous attempt's
            # leaves as the reason it gave up.
            occupied = ()
            continue
        approved = approve_for_project(compiled, ProjectContext(lease._binding, txid))
        occupied = _occupied_scratch(lease, approved)
        if not occupied:
            return approved
    raise PreconditionRefused(
        f"no usable txid after {SCRATCH_ATTEMPTS} attempts"
        + (f"; scratch occupied at {', '.join(occupied)}" if occupied else "")
    )


def _occupied_scratch(lease: Lease, approved: ProjectApprovedSpec) -> tuple[str, ...]:
    """Task 6 fills this in.

    Returning `()` here means Task 5's tests exercise the durable-record path only,
    which is exactly what they assert.
    """
    _ = (lease, approved)
    return ()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_coordinator_admission.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Prove each gate in `_require_admitted` independently**

Comment out the type gate, re-run, and confirm `test_a_raw_compiled_spec_is_refused` fails on its own
assertion. Restore. Repeat for the binding gate against `test_a_proof_from_another_binding_is_refused`.
Confirm `git status --porcelain` is clean after each. Report which test each gate armed.

- [ ] **Step 6: Note the deferred mutation check**

Deleting `occupied = ()` cannot fail any test while `_occupied_scratch` is stubbed to `()`. Record that
in the task report; **Task 6 Step 8 arms and runs it.** Do not claim the reset is proved here.

- [ ] **Step 7: Commit**

```bash
git add src/atoms/coordinator/admission.py tests/test_coordinator_admission.py tests/coordinator_support.py
git commit -m "feat(coordinator): generate txids and bound the admission loop"
```

---

## Task 6: Re-resolution and the three parent branches

**Files:**
- Modify: `src/atoms/coordinator/admission.py`
- Modify: `tests/test_coordinator_admission.py`, `tests/coordinator_support.py`

**Interfaces:**
- Consumes: `observe_child`, `observe_work_child`, `ChildObservation` (Task 4); A4b
  `ApprovedExistingDirectory`, `ApprovedPlannedDirectory`, `ApprovedPath`, `ApprovedScratch`; A3
  `ProjectRoot`, `WorkRoot`, `PersistentNode`, `ScratchRole`, `TopologyNode`.
- Produces: a filled `_occupied_scratch`; `_parent_paths(approved)`, `_parent_path(mapping, node)`,
  `_approved_path_for(approved, path)`, `_require_matches_approval(observed, entry, label)`,
  `_require_planned_absent(lease, approved, mapping, directories, node)`,
  `_observe_work_slot(lease, approved) -> bool`, `_require_work_slot_free(lease, approved) -> None`,
  and `_translated_resolution()`.

**Measured node shapes this task is written against.** For `directory_spec()`:

```text
directories: ApprovedExistingDirectory(ProjectRoot())
             ApprovedPlannedDirectory(PersistentNode('d'))
             ApprovedPlannedDirectory(WorkRoot())
paths:       ApprovedPath('d',       parent_node=ProjectRoot(),          leaf='d')
             ApprovedPath('d/f.txt', parent_node=PersistentNode('d'),    leaf='f.txt')
scratch:     ApprovedScratch('e1', WORK,    parent_node=WorkRoot(),       leaf='.#~<txid>.e1.work')
             ApprovedScratch('e2', STAGING, parent_node=PersistentNode('d'), leaf='.#~<txid>.e2.staging')
work_base:   ApprovedWorkBase(identity=..., constraints=...)
```

For `file_spec()` with `d` already existing, the scratch parent is `TopologyDirectory(node_id=0)` and
`work_base` is `None`.

- [ ] **Step 1: Extend `tests/coordinator_support.py`**

```python
def admission_for(lease: Lease) -> ProjectApprovedSpec:
    from atoms.coordinator.admission import admit

    return admit(lease, compiled_for(lease))


def create_the_planned_directory(lease: Lease, approved: ProjectApprovedSpec) -> None:
    """Make an ApprovedPlannedDirectory exist after its proof was issued."""
    _ = approved
    os.mkdir("d", dir_fd=lease._binding.project_root_fd)


def replace_the_parent_directory(lease: Lease, approved: ProjectApprovedSpec) -> None:
    """Give the approved scratch parent a new inode at the same path.

    Removing and recreating is the cheapest way to change `st_ino` while leaving the
    spelling identical, which is precisely the drift ledger #19 names.
    """
    _ = approved
    root_fd = lease._binding.project_root_fd
    os.rmdir("d", dir_fd=root_fd)
    os.mkdir("d", dir_fd=root_fd)


def occupy_the_scratch_leaf(lease: Lease, approved: ProjectApprovedSpec) -> str:
    """Create the file the proof's staging leaf names, and return its relative path."""
    scratch = next(
        entry for entry in approved.scratch if entry.role is ScratchRole.STAGING
    )
    relative = f"d/{scratch.leaf}"
    os.close(
        os.open(
            relative,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
            dir_fd=lease._binding.project_root_fd,
        )
    )
    return relative
```

Add `from atoms.core.recovery import ScratchRole` and
`from atoms.fs.approval import ProjectApprovedSpec` to the module's imports.

- [ ] **Step 2: Write the failing tests**

```python
def test_an_occupied_scratch_leaf_regenerates_then_succeeds(leased, monkeypatch):
    from atoms.coordinator import admission
    from tests.coordinator_support import occupy_the_scratch_leaf

    with leased() as lease:
        first = admission.admit(lease, compiled_for(lease))
        occupied_path = occupy_the_scratch_leaf(lease, first)

        issued = iter([first.txid, "second"])
        monkeypatch.setattr(admission, "new_txid", lambda: next(issued))

        approved = admission.admit(lease, compiled_for(lease))

        assert approved.txid == "second"
        assert occupied_path.endswith(f".#~{first.txid}.e1.staging")


def test_persistent_occupancy_exhausts_and_names_the_leaves(leased, monkeypatch):
    from atoms.coordinator import admission

    with leased() as lease:
        monkeypatch.setattr(
            admission,
            "_occupied_scratch",
            lambda lease_, approved: (f"d/.#~{approved.txid}.e1.staging",),
        )

        with pytest.raises(PreconditionRefused) as caught:
            admission.admit(lease, compiled_for(lease))

        message = str(caught.value)
        assert "no usable txid after 3 attempts" in message
        assert "scratch occupied at d/.#~" in message


def test_a_present_planned_parent_refuses_without_comparing_its_identity(leased):
    """Design §6.4 branch two: a planned parent has no approved identity, so nothing
    can be compared -- and a fresh observation would be authorizing itself."""
    from atoms.coordinator import admission
    from tests.coordinator_support import (
        compiled_creating_a_directory,
        create_the_planned_directory,
    )

    with leased() as lease:
        approved = admission.admit(lease, compiled_creating_a_directory(lease))
        create_the_planned_directory(lease, approved)

        with pytest.raises(PreconditionRefused) as caught:
            admission._occupied_scratch(lease, approved)

        message = str(caught.value)
        assert "planned parent directory 'd'" in message
        assert "no approved identity" in message


def test_a_moved_scratch_parent_refuses_naming_identity(leased):
    from atoms.coordinator import admission
    from tests.coordinator_support import replace_the_parent_directory

    with leased() as lease:
        approved = admission.admit(lease, compiled_for(lease))
        replace_the_parent_directory(lease, approved)

        with pytest.raises(PreconditionRefused) as caught:
            admission._occupied_scratch(lease, approved)

        assert "changed identity since approval" in str(caught.value)


def test_a_vanished_scratch_parent_is_translated_to_a_precondition_refusal(leased):
    """`resolve.py` raises PreconditionRefused here already; the translation covers the
    approval-time refusal types, which are wrong once a proof exists."""
    import os

    from atoms.coordinator import admission

    with leased() as lease:
        approved = admission.admit(lease, compiled_for(lease))
        os.rmdir("d", dir_fd=lease._binding.project_root_fd)

        with pytest.raises(PreconditionRefused) as caught:
            admission._occupied_scratch(lease, approved)

        assert "no longer resolves" in str(caught.value)


def test_an_approval_refusal_during_re_resolution_becomes_drift(leased, monkeypatch):
    from atoms.core.errors import ProjectApprovalRefused
    from atoms.coordinator import admission

    with leased() as lease:
        approved = admission.admit(lease, compiled_for(lease))

        def refuse(*_args, **_kwargs):
            raise ProjectApprovalRefused("synthetic approval-time refusal")

        monkeypatch.setattr(admission, "observe_child", refuse)

        with pytest.raises(PreconditionRefused) as caught:
            admission._occupied_scratch(lease, approved)

        message = str(caught.value)
        assert "post-approval drift during re-resolution" in message
        assert "synthetic approval-time refusal" in message


def test_the_work_slot_is_reported_as_occupancy_not_a_refusal(leased):
    from atoms.coordinator import admission
    from tests.coordinator_support import compiled_creating_a_directory

    with leased() as lease:
        approved = admission.admit(lease, compiled_creating_a_directory(lease))
        lease._store.create_workspace(approved.txid).close()

        assert admission._occupied_scratch(lease, approved) == (
            f"work/{approved.txid}",
        )


def test_a_moved_work_base_refuses_rather_than_regenerating(leased, monkeypatch):
    from atoms.fs.resolve import ChildObservation, FilesystemIdentity
    from atoms.coordinator import admission
    from tests.coordinator_support import compiled_creating_a_directory

    with leased() as lease:
        approved = admission.admit(lease, compiled_creating_a_directory(lease))
        assert approved.work_base is not None
        moved = ChildObservation(
            parent_identity=FilesystemIdentity(device=1, inode=1),
            parent_constraints=approved.work_base.constraints,
            present=False,
        )
        monkeypatch.setattr(
            admission, "observe_work_child", lambda binding, leaf: moved
        )

        with pytest.raises(PreconditionRefused) as caught:
            admission._occupied_scratch(lease, approved)

        assert "metadata_root/work changed identity" in str(caught.value)


def test_an_unmapped_parent_node_is_a_protocol_error(leased):
    from atoms.coordinator.admission import _parent_path, _parent_paths, admit

    with leased() as lease:
        approved = admit(lease, compiled_for(lease))

        with pytest.raises(ProtocolError) as caught:
            _parent_path(_parent_paths(approved), object())

        assert "no parent path" in str(caught.value)
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_coordinator_admission.py -v`
Expected: FAIL — the occupancy assertions fail against the `()` stub, and `_parent_paths` is undefined.

- [ ] **Step 4: Implement the parent map**

```python
def _parent_paths(approved: ProjectApprovedSpec) -> dict[TopologyNode, str]:
    """Design §6.4's node-to-path table, for the project-space branches only.

    Every scratch leaf shares its target's parent, and that target is itself an
    `ApprovedPath`, so the second assignment is what makes this table total: a parent
    node's path is its child's path minus the child's leaf. `WorkRoot` is deliberately
    absent -- it is metadata space and has its own branch.

    Measured: the parent of `d/f.txt` under an existing `d` is
    `TopologyDirectory(node_id=0)`, which no other rule would name.
    """
    mapping: dict[TopologyNode, str] = {ProjectRoot(): ""}
    for entry in approved.paths:
        mapping[PersistentNode(path=entry.path)] = entry.path
        mapping[entry.parent_node] = entry.path.removesuffix(entry.leaf).rstrip("/")
    return mapping


def _parent_path(mapping: dict[TopologyNode, str], node: object) -> str:
    if node not in mapping:
        raise ProtocolError(f"no parent path for topology node {node!r}")
    return mapping[node]


def _approved_path_for(approved: ProjectApprovedSpec, path: str) -> ApprovedPath:
    for entry in approved.paths:
        if entry.path == path:
            return entry
    raise ProtocolError(
        f"the proof declares no path {path!r}; a planned parent must be declared to "
        "have been approved as planned"
    )
```

- [ ] **Step 5: Implement the three branches**

```python
def _require_matches_approval(
    observed: ChildObservation, entry: object, label: str
) -> None:
    """Ledger #19: the proof is the expected baseline, never current authority."""
    if type(entry) is not ApprovedExistingDirectory:
        raise ProtocolError(
            f"parent {label!r} is not approved as an existing directory; the proof "
            f"carries {type(entry).__name__}"
        )
    if observed.parent_identity != entry.identity:
        raise PreconditionRefused(
            f"parent {label!r} changed identity since approval: approved "
            f"{entry.identity}, observed {observed.parent_identity}"
        )
    if observed.parent_constraints != entry.constraints:
        raise PreconditionRefused(
            f"parent {label!r} changed lookup constraints since approval: approved "
            f"{entry.constraints}, observed {observed.parent_constraints}"
        )


def _require_planned_absent(
    lease: Lease,
    approved: ProjectApprovedSpec,
    mapping: dict[TopologyNode, str],
    directories: dict[TopologyNode, object],
    node: TopologyNode,
) -> None:
    """Design §6.4 branch two.

    An `ApprovedPlannedDirectory` carries constraints but no identity, because the
    directory did not exist when the proof was issued. If it exists now there is
    nothing to compare it against, so this refuses rather than observing it as a
    parent. It observes the planned directory only as a *child* of its own parent, and
    recurses when that parent is planned too: only the outermost planned ancestor has
    an existing parent whose identity can be checked, and an absent ancestor makes
    everything beneath it absent as well.
    """
    path = _parent_path(mapping, node)
    declared = _approved_path_for(approved, path)
    grandparent = directories.get(declared.parent_node)
    if type(grandparent) is ApprovedPlannedDirectory:
        _require_planned_absent(
            lease, approved, mapping, directories, declared.parent_node
        )
        return

    grandparent_path = _parent_path(mapping, declared.parent_node)
    observed = observe_child(lease._binding, grandparent_path, declared.leaf)
    _require_matches_approval(observed, grandparent, grandparent_path or ".")
    if observed.present:
        raise PreconditionRefused(
            f"the planned parent directory {path!r} exists now but was absent when the "
            "proof was issued; a planned directory has no approved identity, so its "
            "lookup relation cannot be compared against anything"
        )


def _observe_work_slot(lease: Lease, approved: ProjectApprovedSpec) -> bool:
    """Ledger #19 for metadata space: re-resolve `work/` against `approved.work_base`.

    Returns whether `work/<txid>` is present. Identity or constraint drift raises
    instead: a moved work base is not something a fresh txid would fix, so it must not
    feed the regeneration loop.
    """
    base = approved.work_base
    if base is None:
        raise ProtocolError(
            "the proof carries no approved work base, so the work-root branch has no "
            "baseline to re-resolve against"
        )
    observed = observe_work_child(lease._binding, approved.txid)
    if observed.parent_identity != base.identity:
        raise PreconditionRefused(
            f"metadata_root/work changed identity since approval: approved "
            f"{base.identity}, observed {observed.parent_identity}"
        )
    if observed.parent_constraints != base.constraints:
        raise PreconditionRefused(
            f"metadata_root/work changed lookup constraints since approval: approved "
            f"{base.constraints}, observed {observed.parent_constraints}"
        )
    return observed.present


def _require_work_slot_free(lease: Lease, approved: ProjectApprovedSpec) -> None:
    """The same re-resolution, at a point where the txid is already fixed.

    Preparation cannot regenerate, so an occupied slot is a refusal there rather than a
    reason to try again.
    """
    if _observe_work_slot(lease, approved):
        raise PreconditionRefused(
            f"work/{approved.txid} already exists; this transaction's engine-derived "
            "work directory is occupied by pre-existing state"
        )


def _occupied_scratch(lease: Lease, approved: ProjectApprovedSpec) -> tuple[str, ...]:
    """Design §6.4. The proof is the expected baseline and never current authority."""
    mapping = _parent_paths(approved)
    directories: dict[TopologyNode, object] = {
        entry.node: entry for entry in approved.directories
    }
    occupied: list[str] = []

    with _translated_resolution():
        # Every WORK scratch leaf lives inside work/<txid>, which create_workspace has
        # not made yet, so the slot's own presence is the whole question -- and asking
        # once avoids naming it twice for a spec with two created directories.
        if approved.work_base is not None and _observe_work_slot(lease, approved):
            occupied.append(f"work/{approved.txid}")

        for scratch in approved.scratch:
            if scratch.parent_node == WorkRoot():
                continue

            entry = directories.get(scratch.parent_node)
            if type(entry) is ApprovedPlannedDirectory:
                _require_planned_absent(
                    lease, approved, mapping, directories, scratch.parent_node
                )
                continue

            parent_path = _parent_path(mapping, scratch.parent_node)
            observed = observe_child(lease._binding, parent_path, scratch.leaf)
            _require_matches_approval(observed, entry, parent_path or ".")
            if observed.present:
                occupied.append(f"{parent_path}/{scratch.leaf}".lstrip("/"))

    return tuple(occupied)
```

- [ ] **Step 6: Implement the translation**

```python
@contextlib.contextmanager
def _translated_resolution() -> Iterator[None]:
    """Design §6.4.

    `resolve.py` raises `ProjectApprovalRefused` and `CapabilityUnavailable` -- correct
    at approval time, wrong afterwards. A post-approval divergence is drift, and §9
    requires `PreconditionRefused`. Stated categorically over the refusal types the
    resolver declares, so it cannot drift as `resolve.py` grows, and it never wraps
    `approve_for_project`, whose refusals are genuine. `PreconditionRefused` and
    `ProtocolError` pass through untouched: the first is already the right type, and
    the second names an engine bug that must not be recoloured as external state.
    """
    try:
        yield
    except (ProjectApprovalRefused, CapabilityUnavailable) as exc:
        raise PreconditionRefused(
            f"post-approval drift during re-resolution: {exc}"
        ) from exc
```

Add to the module imports:

```python
import contextlib
from collections.abc import Iterator

from atoms.core.errors import (
    CapabilityUnavailable,
    PreconditionRefused,
    ProjectApprovalRefused,
    ProtocolError,
)
from atoms.core.recovery import PersistentNode, ProjectRoot, TopologyNode, WorkRoot
from atoms.fs.resolve import ChildObservation, observe_child, observe_work_child
from atoms.fs.topology import (
    ApprovedExistingDirectory,
    ApprovedPath,
    ApprovedPlannedDirectory,
)
```

`observe_child` and `observe_work_child` are imported as module attributes so
`monkeypatch.setattr(admission, ...)` in the tests reaches the names this module actually calls.

- [ ] **Step 7: Run to verify it passes**

Run: `uv run pytest tests/test_coordinator_admission.py -v`
Expected: PASS.

- [ ] **Step 8: Arm and run Task 5's deferred mutation check**

`_occupied_scratch` is real now. Delete `occupied = ()` from the record-collision branch in `admit`,
run `test_every_candidate_owned_by_a_record_exhausts_the_bound`, and confirm it fails on
`"scratch occupied" not in message`. Restore, confirm `git status --porcelain` is empty, and re-run.
Report the observed failure message.

- [ ] **Step 9: Prove the planned branch never compares a planned identity**

Add an assertion that the planned refusal is reached without touching
`_require_matches_approval` on the planned node itself: temporarily change
`_require_planned_absent`'s branch to call `_require_matches_approval(observed, directories.get(node),
path)` instead of raising, re-run
`test_a_present_planned_parent_refuses_without_comparing_its_identity`, and confirm the failure is now
a `ProtocolError` naming `ApprovedPlannedDirectory` rather than the `PreconditionRefused` the test
expects — which is exactly the "fresh observation authorizing itself" the design forbids. Restore and
report.

- [ ] **Step 10: Commit**

```bash
git add src/atoms/coordinator/admission.py tests/test_coordinator_admission.py tests/coordinator_support.py
git commit -m "feat(coordinator): re-resolve each scratch parent against its approved baseline"
```

---

## Task 7: Preparation

**Files:**
- Create: `src/atoms/coordinator/prepare.py`
- Create: `tests/test_coordinator_prepare.py`
- Modify: `tests/coordinator_support.py`

**Interfaces:**
- Consumes: A5a `Store.transaction()`, `_StoreTransaction.promote_staging(workspace, manifest)`,
  `.insert_record(txid, spec)`, `.set_active(txid)`, `Store.create_workspace(txid) -> Workspace`,
  `StagedBlob(name, digest, byte_len)`; `_require_admitted` and `_require_work_slot_free` from
  Tasks 5–6.
- Produces: `open_workspace(lease, approved) -> Workspace` and
  `prepare_transaction(lease, approved, workspace, manifest) -> None`.

**Both are transaction-stage entry points** and both therefore open with `_require_admitted`. Ledger
#9's guard covers all three coordinator entry points, not only the two that write.

- [ ] **Step 1: Extend `tests/coordinator_support.py`**

```python
def stage_manifest(
    workspace: Workspace, contents: tuple[bytes, ...] = (AFTER,)
) -> tuple[StagedBlob, ...]:
    """Stage each content through the workspace and describe it for promotion.

    A5a's coherence barrier requires a `blob` row for every digest the record
    references, so a spec whose final surface names a file cannot be published without
    this.
    """
    manifest = []
    for index, content in enumerate(contents):
        name = f"blob-{index}"
        stage(workspace, name, content)
        manifest.append(
            StagedBlob(name=name, digest=digest_of(content), byte_len=len(content))
        )
    return tuple(manifest)


def prepared(lease: Lease) -> ProjectApprovedSpec:
    """An admitted, prepared, published transaction with its workspace released."""
    from atoms.coordinator.prepare import open_workspace, prepare_transaction

    approved = admission_for(lease)
    workspace = open_workspace(lease, approved)
    try:
        prepare_transaction(lease, approved, workspace, stage_manifest(workspace))
    finally:
        workspace.close()
    return approved
```

Add `from atoms.store.blobs import StagedBlob`, `from atoms.store.workspace import Workspace`, and
`from tests.store_support import digest_of, file_state, stage` to the imports.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_coordinator_prepare.py`:

```python
"""A5b tier 3 -- preparation: the gates, work-base re-resolution, publication."""

from __future__ import annotations

from typing import cast

import pytest

from atoms.core.errors import PreconditionRefused, ProtocolError
from atoms.core.recovery import CommitDecision, TransactionState
from atoms.fs.approval import ProjectApprovedSpec
from tests.coordinator_support import (
    admission_for,
    compiled_creating_a_directory,
    compiled_for,
    stage_manifest,
)


def test_preparation_publishes_the_record_in_one_commit(leased):
    from atoms.coordinator.prepare import open_workspace, prepare_transaction

    with leased() as lease:
        approved = admission_for(lease)
        workspace = open_workspace(lease, approved)
        manifest = stage_manifest(workspace)

        prepare_transaction(lease, approved, workspace, manifest)
        workspace.close()

        record = lease._store.read_active()
        assert record is not None
        assert record.txid == approved.txid
        assert record.state is TransactionState.PREPARED
        assert record.committed is CommitDecision.UNCOMMITTED


def test_nothing_is_durable_until_the_publication_commit(leased):
    """The single barrier authority §7.3 step 4 requires: a body that raises before the
    COMMIT leaves no record, no effect rows, and no active pointer."""
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = admission_for(lease)
        workspace = open_workspace(lease, approved)
        manifest = stage_manifest(workspace)

        with pytest.raises(RuntimeError), lease._store.transaction() as txn:
            txn.promote_staging(workspace, manifest)
            txn.insert_record(approved.txid, approved.compiled.spec)
            raise RuntimeError("cut before set_active")

        workspace.close()
        assert lease._store.read_record(approved.txid) is None
        assert lease._store.read_active() is None


def test_a_workspace_from_another_transaction_is_refused(leased):
    from atoms.coordinator.prepare import prepare_transaction

    with leased() as lease:
        approved = admission_for(lease)
        foreign = lease._store.create_workspace("someone-else")

        with pytest.raises(ProtocolError) as caught:
            prepare_transaction(lease, approved, foreign, ())

        foreign.close()
        message = str(caught.value)
        assert "someone-else" in message
        assert approved.txid in message


def test_a_proof_bound_to_another_binding_is_refused(leased):
    from atoms.coordinator.prepare import prepare_transaction

    with leased() as first, leased() as second:
        approved = admission_for(first)
        workspace = second._store.create_workspace(approved.txid)

        with pytest.raises(ProtocolError) as caught:
            prepare_transaction(second, approved, workspace, ())

        workspace.close()
        assert "binding" in str(caught.value)


def test_open_workspace_refuses_a_raw_compiled_spec(leased):
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        raw = cast(ProjectApprovedSpec, compiled_for(lease))

        with pytest.raises(ProtocolError) as caught:
            open_workspace(lease, raw)

        assert "ProjectApprovedSpec" in str(caught.value)


def test_open_workspace_re_resolves_the_work_base_before_creating_anything(leased):
    from atoms.coordinator import admission
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = admission.admit(lease, compiled_creating_a_directory(lease))
        assert approved.work_base is not None
        lease._store.create_workspace(approved.txid).close()

        with pytest.raises(PreconditionRefused) as caught:
            open_workspace(lease, approved)

        assert f"work/{approved.txid} already exists" in str(caught.value)


def test_a_spec_without_a_work_base_skips_the_comparison(leased):
    """`work_base` is None unless the spec contains a CreateDirectory. Treating None as
    a mismatch would refuse every transaction that creates no directory; treating it as
    an empty baseline would let a fresh observation authorize itself."""
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = admission_for(lease)
        assert approved.work_base is None

        workspace = open_workspace(lease, approved)
        assert workspace.txid == approved.txid
        workspace.close()
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_coordinator_prepare.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atoms.coordinator.prepare'`

- [ ] **Step 4: Implement `prepare.py`**

```python
"""Authority §7.3 steps 2-4 (design §7)."""

from __future__ import annotations

from atoms.core.errors import ProtocolError
from atoms.coordinator.admission import _require_admitted, _require_work_slot_free
from atoms.coordinator.lease import Lease
from atoms.fs.approval import ProjectApprovedSpec
from atoms.store.blobs import StagedBlob
from atoms.store.workspace import Workspace


def open_workspace(lease: Lease, approved: ProjectApprovedSpec) -> Workspace:
    """Ledger #19: re-resolve the work base BEFORE creating anything under it.

    By `prepare_transaction` the workspace already exists and A6 has written into it,
    so preparation uses those pinned descriptors; re-resolving afterwards could not
    authorize their creation.

    The comparison is conditional on `work_base is not None`, which A4b populates only
    for a spec containing a `CreateDirectory` -- the sole effect with a WORK scratch
    role. When it is None, A4b has judged `work/` irrelevant to this transaction and
    there is no baseline to compare against.
    """
    _require_admitted(lease, approved)
    if approved.work_base is not None:
        _require_work_slot_free(lease, approved)
    return lease._store.create_workspace(approved.txid)


def prepare_transaction(
    lease: Lease,
    approved: ProjectApprovedSpec,
    workspace: Workspace,
    manifest: tuple[StagedBlob, ...],
) -> None:
    """Design §6.5's gate set, then authority §7.3 steps 2-4 in one transaction."""
    _require_admitted(lease, approved)
    if workspace.txid != approved.txid:
        raise ProtocolError(
            f"workspace txid {workspace.txid!r} does not match the proof's "
            f"{approved.txid!r}; ledger #21 forbids executing a proof under any other "
            "txid"
        )

    # promote_staging is a method of _StoreTransaction -- obtainable only by entering
    # Store.transaction() -- and it writes the blob index rows itself; insert_record
    # derives the effect rows from the spec. Its cross-directory flush lands inside
    # this body, so every blob is durable on the filesystem before the COMMIT that
    # references it, even though both happen within one `with`.
    with lease._store.transaction() as txn:
        txn.promote_staging(workspace, manifest)
        txn.insert_record(approved.txid, approved.compiled.spec)
        txn.set_active(approved.txid)
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_coordinator_prepare.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 6: Prove each gate independently**

For the workspace-txid gate and the `work_base is not None` condition, comment each out, re-run, and
confirm the matching test fails **on its own assertion** rather than incidentally. Restore after each
and confirm `git status --porcelain` is empty. `_require_admitted`'s two gates were already proved in
Task 5; note in the report which test in *this* module they arm
(`test_open_workspace_refuses_a_raw_compiled_spec`, `test_a_proof_bound_to_another_binding_is_refused`).

- [ ] **Step 7: Commit**

```bash
git add src/atoms/coordinator/prepare.py tests/test_coordinator_prepare.py tests/coordinator_support.py
git commit -m "feat(coordinator): publish a prepared record behind one durable barrier"
```

---

## Task 8: Transition persistence

**Files:**
- Create: `src/atoms/coordinator/transitions.py`
- Create: `tests/test_coordinator_transitions.py`
- Modify: `tests/coordinator_support.py`

**Interfaces:**
- Consumes: A3 `classify_recovery`, `build_recovery_snapshot`,
  `reduce_recovery_plan_prefix(snapshot, plan, completed_steps)`, `RecoveryPlan`, `RecoveryStep`,
  `TransitionTransactionState`, `TransitionEffectState`, `TransformEffectTuple`, `RemoveScratch`,
  `PreserveExternal`, `DetachActive`; A5a's typed setters; `_require_admitted` from Task 5.
- Produces: `persist_plan_prefix(lease, approved, plan, start: int) -> int` and
  `_persist_one(lease, txid, step) -> None`.

**Every plan comes from the production `classify_recovery`** over a snapshot built from the *approved*
`compiled` and `topology`, never hand-assembled. `ActionPlan.__init__` refuses construction without the
classifier's token anyway (`plan.py:130`), and building the snapshot from the proof is what makes
`_require_projection_matches`'s compiled/topology equality checks satisfiable.

**Three A5a facts this task must respect:**
1. `Store.read_record` / `read_active` **refuse while a write transaction is open** (`connection.py:712`).
   Nothing inside a `with lease._store.transaction()` body may read the store.
2. There is no `step.state`. The field names are `to_state`, `rollback_result`, `halt_diagnostic`
   (`plan.py:60-74`), and the terminal payload is carried by the step itself — never fetched from the
   plan.
3. First-wins needs no extra read. A second halt cannot pass `_require_projection_matches`: the record
   is already HALTED, and the prefix reduced to the same `start` still projects the pre-halt state.

- [ ] **Step 1: Extend `tests/coordinator_support.py`**

```python
def snapshot_for(
    approved: ProjectApprovedSpec,
    *,
    state: TransactionState,
    journal: JournalState,
    live: ObservedEntry,
    staged: ObservedEntry,
    relation: FileBuildRelation | None = None,
) -> RecoverySnapshot:
    """A snapshot over the PROOF's compiled spec and topology.

    Built from `approved` rather than from a parallel fixture so that
    `_require_projection_matches`'s compiled and topology equality checks are satisfied
    by construction: a snapshot describing a different spec is exactly the mismatch that
    guard exists to catch.

    `relation` must be None outside JournalState.STARTED -- `build_recovery_snapshot`
    refuses `file_build_relation is present in the wrong construction state` otherwise.
    """
    return build_recovery_snapshot(
        compiled=approved.compiled,
        topology=approved.topology,
        transaction_state=state,
        commit_decision=CommitDecision.UNCOMMITTED,
        rollback_result=None,
        halt_diagnostic=None,
        active=True,
        journals=(EffectJournalState("e1", journal),),
        persistent_observations=(PersistentObservation("d/f.txt", live),),
        scratch_observations=(
            ScratchObservation("e1", ScratchRole.STAGING, staged, relation),
        ),
    )


def prepared_with(
    lease: Lease,
    *,
    state: TransactionState = TransactionState.PREPARED,
    journal: JournalState = JournalState.PENDING,
    live: ObservedEntry = OBSERVED_ABSENT,
    staged: ObservedEntry = OBSERVED_ABSENT,
    relation: FileBuildRelation | None = None,
) -> tuple[ProjectApprovedSpec, RecoveryPlan]:
    """A published record advanced to `state`/`journal`, and A3's plan for it.

    The store's durable rows and the snapshot are moved together, because
    `persist_plan_prefix` refuses when the active record disagrees with the plan prefix
    reduced to `start` -- which is the point of that check.
    """
    approved = prepared(lease)
    if state is not TransactionState.PREPARED or journal is not JournalState.PENDING:
        with lease._store.transaction() as txn:
            txn.set_transaction_state(approved.txid, state)
            txn.set_journal_state(approved.txid, "e1", journal)
    snapshot = snapshot_for(
        approved,
        state=state,
        journal=journal,
        live=live,
        staged=staged,
        relation=relation,
    )
    return approved, classify_recovery(snapshot)


def observed_file(content: bytes = AFTER) -> ObservedFile:
    return ObservedFile(file_state(content), EntryIdentity())
```

Named wrappers, one per measured plan shape:

```python
def prepared_metadata_only(lease: Lease):
    """ActionPlan ROLL_BACK: [transition, transition+RESTORED, DetachActive]."""
    return prepared_with(lease)


def prepared_with_preserve_external(lease: Lease):
    """ActionPlan ROLL_BACK_REFUSED: PreserveExternal at index 1, no mutating step."""
    return prepared_with(lease, live=observed_file(b"someone else's bytes"))


def prepared_with_halt(lease: Lease):
    """HaltPlan: one PREPARED->HALTED transition carrying its diagnostic."""
    return prepared_with(lease, staged=observed_file())


def prepared_with_remove_scratch(lease: Lease):
    """ActionPlan ROLL_BACK whose first mutating step is RemoveScratch, at index 2."""
    return prepared_with(
        lease,
        state=TransactionState.APPLYING,
        journal=JournalState.STARTED,
        staged=observed_file(),
        relation=FileBuildRelation.EXACT,
    )


def prepared_with_transform(lease: Lease):
    """ActionPlan ROLL_BACK whose first mutating step is TransformEffectTuple, index 2."""
    return prepared_with(
        lease,
        state=TransactionState.APPLYING,
        journal=JournalState.DONE,
        live=observed_file(),
    )
```

Add to the imports:

```python
from atoms.core.recovery import (
    CommitDecision,
    EffectJournalState,
    JournalState,
    ObservedEntry,
    ObservedFile,
    PersistentObservation,
    RecoverySnapshot,
    ScratchObservation,
    ScratchRole,
    TransactionState,
    build_recovery_snapshot,
    classify_recovery,
)
from atoms.core.recovery.model import (
    OBSERVED_ABSENT,
    EntryIdentity,
    FileBuildRelation,
)
from atoms.core.recovery.plan import RecoveryPlan
```

Verify each of these names is exported where the import says before relying on it; `OBSERVED_ABSENT`,
`EntryIdentity`, and `FileBuildRelation` live in `atoms.core.recovery.model`
(`model.py:99-107`) and may or may not be re-exported from the package `__init__`. Report what you find.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_coordinator_transitions.py`:

```python
"""A5b tier 4 -- transition persistence: prefix validation, barriers, the cursor."""

from __future__ import annotations

import pytest

from atoms.core.errors import ProtocolError
from atoms.core.recovery import CommitDecision, JournalState, TransactionState
from atoms.core.recovery.model import RollbackResult
from atoms.core.recovery.plan import (
    DetachActive,
    PreserveExternal,
    RemoveScratch,
    TransformEffectTuple,
)
from tests.coordinator_support import (
    prepared_metadata_only,
    prepared_with_halt,
    prepared_with_preserve_external,
    prepared_with_remove_scratch,
    prepared_with_transform,
)


def test_a_metadata_only_plan_runs_to_the_end(leased):
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_metadata_only(lease)
        assert type(plan.steps[-1]) is DetachActive

        assert persist_plan_prefix(lease, approved, plan, 0) == len(plan.steps)

        record = lease._store.read_record(approved.txid)
        assert record is not None
        assert record.state is TransactionState.ROLLED_BACK
        assert record.rollback_result is RollbackResult.RESTORED
        assert lease._store.read_active() is None


def test_the_cursor_stops_at_a_remove_scratch(leased):
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_with_remove_scratch(lease)

        cursor = persist_plan_prefix(lease, approved, plan, 0)

        assert cursor == 2
        assert type(plan.steps[cursor]) is RemoveScratch
        record = lease._store.read_record(approved.txid)
        assert record is not None
        assert record.state is TransactionState.ROLLING_BACK
        assert record.journals[0].state is JournalState.UNDO_STARTED
        assert lease._store.read_active() is not None


def test_the_cursor_stops_at_a_transform(leased):
    """A stop rule proved against one mutating variant is not proved."""
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_with_transform(lease)

        cursor = persist_plan_prefix(lease, approved, plan, 0)

        assert cursor == 2
        assert type(plan.steps[cursor]) is TransformEffectTuple


def test_preserve_external_writes_nothing_durable(leased):
    """It carries only topology nodes; there is no durable field to write."""
    from atoms.coordinator.transitions import _persist_one

    with leased() as lease:
        approved, plan = prepared_with_preserve_external(lease)
        step = plan.steps[1]
        assert type(step) is PreserveExternal
        before = lease._store.read_record(approved.txid)

        _persist_one(lease, approved.txid, step)

        assert lease._store.read_record(approved.txid) == before


def test_a_preserve_external_plan_still_reaches_its_terminal_state(leased):
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_with_preserve_external(lease)

        assert persist_plan_prefix(lease, approved, plan, 0) == len(plan.steps)

        record = lease._store.read_record(approved.txid)
        assert record is not None
        assert record.state is TransactionState.ROLLED_BACK


def test_a_halt_persists_its_diagnostic_with_its_state(leased):
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_with_halt(lease)

        assert persist_plan_prefix(lease, approved, plan, 0) == len(plan.steps)

        record = lease._store.read_record(approved.txid)
        assert record is not None
        assert record.state is TransactionState.HALTED
        assert record.halt_diagnostic == plan.diagnostic
        assert record.committed is CommitDecision.UNCOMMITTED


def test_the_first_halt_diagnostic_wins(leased):
    """Ledger #12: the FIRST halt freezes the pre-halt state. The second attempt cannot
    reach `_persist_one` at all -- the record is already HALTED, and the prefix reduced
    to the same start still projects PREPARED."""
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_with_halt(lease)
        persist_plan_prefix(lease, approved, plan, 0)
        first = lease._store.read_record(approved.txid)

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, plan, 0)

        assert "transaction state" in str(caught.value)
        assert lease._store.read_record(approved.txid) == first


def test_a_record_disagreeing_with_the_reduced_prefix_refuses(leased):
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_metadata_only(lease)
        with lease._store.transaction() as txn:
            txn.set_commit_decision(approved.txid, CommitDecision.COMMITTED)

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, plan, 0)

        assert "commit decision" in str(caught.value)


def test_a_plan_bound_to_another_spec_refuses(leased):
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as first, leased() as second:
        approved, _ = prepared_metadata_only(first)
        _, foreign_plan = prepared_metadata_only(second)

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(first, approved, foreign_plan, 0)

        assert "compiled spec" in str(caught.value) or "topology" in str(caught.value)


def test_no_active_record_refuses(leased):
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_metadata_only(lease)
        with lease._store.transaction() as txn:
            txn.set_active(None)

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, plan, 0)

        assert "no active record" in str(caught.value)


def test_a_start_outside_the_step_range_refuses(leased):
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_metadata_only(lease)

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, plan, len(plan.steps) + 1)

        assert "outside the plan step range" in str(caught.value)
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_coordinator_transitions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atoms.coordinator.transitions'`

- [ ] **Step 4: Implement the gates and the walk**

```python
"""A3 transition persistence in plan order (design §8)."""

from __future__ import annotations

from atoms.core.canonical import canonical_json
from atoms.core.errors import ProtocolError
from atoms.core.recovery.plan import (
    DetachActive,
    PreserveExternal,
    RecoveryPlan,
    RecoveryStep,
    RemoveScratch,
    TransformEffectTuple,
    TransitionEffectState,
    TransitionTransactionState,
)
from atoms.core.recovery.reducer import reduce_recovery_plan_prefix
from atoms.coordinator.admission import _require_admitted
from atoms.coordinator.lease import Lease
from atoms.fs.approval import ProjectApprovedSpec
from atoms.store.records import StoredRecord

#: `AuthorizedStep.step` is typed exactly these two (`plan.py:189`), so the stop rule is
#: read off A3's own contract rather than restated here.
_MUTATING = (TransformEffectTuple, RemoveScratch)


def persist_plan_prefix(
    lease: Lease, approved: ProjectApprovedSpec, plan: RecoveryPlan, start: int
) -> int:
    """Persist every metadata-only step from `start`, stop at the first step A7 must
    execute, and return that index.

    Reading the active record here is load-bearing three ways: it enforces the binding
    identity, it supplies the independent txid ledger #21 requires, and -- because each
    step below commits before the next begins -- returning past a metadata step means
    that step's COMMIT completed.
    """
    _require_admitted(lease, approved)
    if type(start) is not int or not 0 <= start <= len(plan.steps):
        raise ProtocolError(
            f"start {start!r} is outside the plan step range 0..{len(plan.steps)}"
        )

    record = lease._store.read_active()
    if record is None:
        raise ProtocolError("no active record to advance")
    if record.txid != approved.txid:
        raise ProtocolError(
            f"active record {record.txid!r} is not the proof's {approved.txid!r}"
        )

    _require_projection_matches(record, plan, start, approved)

    cursor = start
    while cursor < len(plan.steps):
        step = plan.steps[cursor]
        if type(step) in _MUTATING:
            return cursor
        _persist_one(lease, record.txid, step)
        cursor += 1
    return cursor
```

- [ ] **Step 5: Implement the full projection check**

```python
def _require_projection_matches(
    record: StoredRecord,
    plan: RecoveryPlan,
    start: int,
    approved: ProjectApprovedSpec,
) -> None:
    """Compare the live record against the plan prefix reduced to `start`.

    Checking `plan.bound_snapshot.transaction_state` alone would be both incomplete and
    wrong whenever `start > 0`, because the record has legitimately advanced past the
    bound snapshot by then. Reducing gives the state the record SHOULD be in at exactly
    this cursor, and every durable field is compared, not merely the one a caller
    happened to think of.
    """
    snapshot = plan.bound_snapshot
    if snapshot.compiled != approved.compiled:
        raise ProtocolError(
            "the plan's bound snapshot names a different compiled spec than the proof"
        )
    if snapshot.topology != approved.topology:
        raise ProtocolError(
            "the plan's bound snapshot names a different topology than the proof"
        )

    expected = reduce_recovery_plan_prefix(snapshot, plan, completed_steps=start)
    if canonical_json(record.spec) != canonical_json(expected.compiled.spec):
        raise ProtocolError(
            f"the active record's spec disagrees with the plan prefix reduced to step "
            f"{start}"
        )
    for label, stored, projected in (
        ("transaction state", record.state, expected.transaction_state),
        ("commit decision", record.committed, expected.commit_decision),
        ("rollback result", record.rollback_result, expected.rollback_result),
        ("halt diagnostic", record.halt_diagnostic, expected.halt_diagnostic),
        ("journals", record.journals, expected.journals),
    ):
        if stored != projected:
            raise ProtocolError(
                f"the active record's {label} disagrees with the plan prefix reduced "
                f"to step {start}: stored {stored!r}, projected {projected!r}"
            )
    if not expected.active:
        raise ProtocolError(
            f"the plan prefix reduced to step {start} projects a detached transaction, "
            "but this record is still the active one"
        )
```

`canonical_json` is used for the spec rather than `==` because the record's spec was decoded from
durable JSON; comparing the canonical encodings is the same comparison A5a's own coherence rules make.

- [ ] **Step 6: Implement the barrier discipline**

One SQLite barrier per writable step (authority §7.4):

```python
def _persist_one(lease: Lease, txid: str, step: RecoveryStep) -> None:
    """Exactly one transaction per step that has anything durable to write.

    Nothing here reads the store: A5a refuses a public read while the store owns a write
    transaction (`connection.py:712`), and every value this needs is already on the step.
    """
    if type(step) is PreserveExternal:
        # No durable representation: it carries only topology nodes. An empty
        # transaction would claim a write happened.
        return

    if type(step) is DetachActive:
        with lease._store.transaction() as txn:
            txn.set_active(None)
        return

    if type(step) is TransitionEffectState:
        with lease._store.transaction() as txn:
            txn.set_journal_state(txid, step.effect_id, step.to_state)
        return

    if type(step) is TransitionTransactionState:
        # State and terminal payload are ONE transaction: ledger #12 says "atomically
        # with", and A5a's coherence barrier refuses a ROLLED_BACK row without a
        # rollback_result or a HALTED row without a diagnostic (`records.py:480-483`),
        # so a second transaction could not commit the first half anyway. The reducer
        # guarantees exactly one payload is present for a terminal state and neither for
        # a non-terminal one (`reducer.py:225-239`), so both conditions are read off the
        # step rather than off the destination state. `committed` is never touched, so
        # it is preserved across the halt.
        with lease._store.transaction() as txn:
            txn.set_transaction_state(txid, step.to_state)
            if step.rollback_result is not None:
                txn.set_rollback_result(txid, step.rollback_result)
            if step.halt_diagnostic is not None:
                txn.set_halt_diagnostic(txid, step.halt_diagnostic)
        return

    raise ProtocolError(
        f"step {type(step).__name__} is neither mutating nor persistable; the walk "
        "should have returned before reaching it"
    )
```

`DetachActive` is reachable only after every earlier step has been persisted; the walk gives that for
free, since it advances strictly in plan order.

- [ ] **Step 7: Run to verify it passes**

Run: `uv run pytest tests/test_coordinator_transitions.py -v`
Expected: PASS, 11 tests.

- [ ] **Step 8: Prove the projection check is complete, field by field**

For each of the five labelled fields, force a disagreement and confirm the refusal names **that**
field, not a neighbour:

| Field | How to force it |
| --- | --- |
| transaction state | already covered by `test_the_first_halt_diagnostic_wins` |
| commit decision | already covered by `test_a_record_disagreeing_with_the_reduced_prefix_refuses` |
| rollback result | `txn.set_rollback_result(txid, RollbackResult.EXTERNAL_DRIFT_PRESERVED)` after advancing state to `ROLLED_BACK`, then persist with `start=0` |
| halt diagnostic | `txn.set_halt_diagnostic(txid, matching_diagnostic("e1"))` after advancing state to `HALTED` |
| journals | `txn.set_journal_state(txid, "e1", JournalState.DONE)` against a `PENDING` prefix |

`matching_diagnostic` is `tests/store_support.py:352` and exists precisely so a coherent HALTED record
can be written before something about it is broken. Add a test for each of the last three. Then delete
that field's row from the comparison tuple, re-run, and confirm only its own test fails. Restore after
each and report the five observed failures.

- [ ] **Step 9: Prove one barrier per writable step**

Add:

```python
def test_each_writable_step_commits_before_the_next_begins(leased, monkeypatch):
    """Authority §7.4. If two steps shared a transaction, the cut below would lose both."""
    from atoms.coordinator import transitions

    with leased() as lease:
        approved, plan = prepared_metadata_only(lease)
        real = transitions._persist_one
        calls = []

        def cut_after_the_first(lease_, txid, step):
            calls.append(step)
            if len(calls) == 2:
                raise RuntimeError("cut between steps")
            real(lease_, txid, step)

        monkeypatch.setattr(transitions, "_persist_one", cut_after_the_first)

        with pytest.raises(RuntimeError):
            transitions.persist_plan_prefix(lease, approved, plan, 0)

        record = lease._store.read_record(approved.txid)
        assert record is not None
        assert record.state is TransactionState.ROLLING_BACK
        assert record.rollback_result is None
```

The first step's COMMIT survived a failure during the second. Then merge the first two steps into one
transaction by hand — wrap the `while` body's `_persist_one` calls in a single outer
`with lease._store.transaction()` — re-run, and confirm this test fails because the record is still
`PREPARED`. Restore and report.

- [ ] **Step 10: Commit**

```bash
git add src/atoms/coordinator/transitions.py tests/test_coordinator_transitions.py tests/coordinator_support.py
git commit -m "feat(coordinator): persist a plan prefix and stop before A7's work"
```

---

## Task 9: Architecture guards, the process tier, packaging, the ledger, and the status

**Files:**
- Create: `tests/test_coordinator_architecture.py`, `tests/coordinator_child.py`,
  `tests/test_coordinator_process.py`
- Modify: `tests/test_fs_architecture.py`, `tests/test_store_architecture.py`, `pyproject.toml`,
  `docs/deferred-obligation-ledger.md`, `docs/plans/2026-08-02-a5b-recovery-lease-design.md`,
  `AGENTS.md`

- [ ] **Step 1: Replace the #18 guard**

In `tests/test_fs_architecture.py`, replace `test_no_production_caller_of_bind_exists_yet`
(line 364). **Do not delete it** — it is replaced, not removed. The scanner
(`_production_bind_callers`) and its four self-tests stay exactly as they are.

```python
def test_the_only_production_bind_caller_is_the_coordinator_root():
    source_root = Path(__file__).parents[1] / "src"
    assert _production_bind_callers(source_root) == {
        source_root / "atoms" / "coordinator" / "root.py"
    }


def test_the_production_bind_call_passes_the_certified_allowlist():
    """Ledger #18. The constant ships empty, so this is the assertion that can be made:
    the one production call site names it, and names nothing else."""
    path = Path(__file__).parents[1] / "src" / "atoms" / "coordinator" / "root.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _called_name(node) == "bind_project_volume"
    ]
    assert len(calls) == 1
    keywords = {keyword.arg: keyword.value for keyword in calls[0].keywords}
    allowlist = keywords["allowlist"]
    assert isinstance(allowlist, ast.Name)
    assert allowlist.id == "CERTIFIED_ALLOWLIST"
    assert "atoms.fs.volume.CERTIFIED_ALLOWLIST" in _resolved_imports(
        tree, package="atoms.coordinator"
    )
    assert CERTIFIED_ALLOWLIST == DurabilityAllowlist(entries=frozenset())
```

The last line keeps the existing statement of *why* this is an architecture assertion in the same test
that makes it, so the two cannot drift apart. `CERTIFIED_ALLOWLIST` and `DurabilityAllowlist` are
already imported at `test_fs_architecture.py:15`.

- [ ] **Step 2: Replace the #9 guard**

Replace `test_no_consumer_of_the_approved_spec_exists_yet` (line 981). Its own docstring says it "is
replaced by one asserting A5-A8 accept only this proof" when A5 lands.

```python
_TRANSACTION_STAGE_ENTRY_POINTS = {
    "atoms/coordinator/prepare.py": ("open_workspace", "prepare_transaction"),
    "atoms/coordinator/transitions.py": ("persist_plan_prefix",),
}


def _first_statement(function: ast.FunctionDef) -> ast.stmt:
    body = function.body
    if (
        isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1]
    return body[0]


def test_only_the_coordinator_consumes_the_approved_spec():
    """Ledger #9's enforcement half, part one: nothing outside the boundary sees it."""
    consumers = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path.name == "approval.py" or path.parts[-2] == "coordinator":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        _, package = _source_module(SOURCE_ROOT.parent, path)
        if "atoms.fs.approval.ProjectApprovedSpec" in _resolved_imports(
            tree, package=package
        ) or any(
            (isinstance(node, ast.Name) and node.id == "ProjectApprovedSpec")
            or (isinstance(node, ast.Attribute) and node.attr == "ProjectApprovedSpec")
            for node in ast.walk(tree)
        ):
            consumers.append(str(path.relative_to(SOURCE_ROOT)))
    assert consumers == []


def test_every_transaction_stage_entry_point_opens_with_the_proof_gate():
    """Part two: each entry point's FIRST statement is the shared gate.

    First rather than merely present: a gate reached after a store write would refuse
    an unauthorized proof only once it had already changed durable state.
    """
    for relative, names in _TRANSACTION_STAGE_ENTRY_POINTS.items():
        tree = ast.parse(
            (SOURCE_ROOT.parent / relative).read_text(encoding="utf-8")
        )
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
        }
        assert set(names) <= set(functions), relative
        for name in names:
            first = _first_statement(functions[name])
            assert isinstance(first, ast.Expr), f"{relative}::{name}"
            assert isinstance(first.value, ast.Call), f"{relative}::{name}"
            assert _called_name(first.value) == "_require_admitted", (
                f"{relative}::{name}"
            )


def test_no_unregistered_public_function_accepts_the_proof():
    """Part three: the registry above cannot go stale as A6-A8 add entry points."""
    registered = {
        f"{relative}::{name}"
        for relative, names in _TRANSACTION_STAGE_ENTRY_POINTS.items()
        for name in names
    }
    found = set()
    for path in sorted((SOURCE_ROOT / "coordinator").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative = str(path.relative_to(SOURCE_ROOT.parent))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
                continue
            if any(
                isinstance(argument.annotation, ast.Name)
                and argument.annotation.id == "ProjectApprovedSpec"
                for argument in node.args.args
            ):
                found.add(f"{relative}::{node.name}")
    assert found == registered
```

`admit` is not in the registry and must not be: it *returns* a proof rather than accepting one, and the
completeness check keys on parameter annotations for exactly that reason.

- [ ] **Step 3: Write the coordinator architecture tier**

Create `tests/test_coordinator_architecture.py`:

```python
"""A5b tier 6 -- import direction, package surface, and the fixture registry."""

from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path

from tests.architecture_support import (
    decorator_name,
    fixture_names,
    unregistered_test_arguments,
)

SOURCE_ROOT = Path(__file__).parents[1] / "src" / "atoms"
TESTS = Path(__file__).parent

#: `coordinator` may import the store because it is the DAG's only consumer; `store`
#: appears because a package importing itself is not a boundary violation.
_STORE_IMPORT_EXEMPT = {"coordinator", "store"}


def _resolved_imports(tree: ast.Module, *, package: str) -> set[str]:
    targets: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        module = node.module or ""
        imported_from = (
            resolve_name(f"{'.' * node.level}{module}", package)
            if node.level
            else module
        )
        targets.add(imported_from)
        targets.update(
            f"{imported_from}.{alias.name}"
            for alias in node.names
            if alias.name != "*"
        )
    return targets


def _store_importers(source_root: Path) -> list[str]:
    offenders = []
    for path in sorted(source_root.rglob("*.py")):
        parts = path.relative_to(source_root).parts
        if parts[0] in _STORE_IMPORT_EXEMPT:
            continue
        package = ".".join(("atoms", *parts[:-1]))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            name == "atoms.store" or name.startswith("atoms.store.")
            for name in _resolved_imports(tree, package=package)
        ):
            offenders.append(str(path.relative_to(source_root)))
    return offenders


def test_only_the_coordinator_and_the_store_itself_import_the_store():
    """The DAG is coordinator -> {store, fs, core}, store -> {fs, core}, fs -> core."""
    assert _store_importers(SOURCE_ROOT) == []


def test_the_store_import_scanner_finds_a_planted_offender(tmp_path):
    """A guard that cannot fail proves nothing. Plant one of each shape."""
    root = tmp_path / "atoms"
    for relative, source in (
        ("fs/leak.py", "from atoms.store import Store\n"),
        ("core/leak.py", "import atoms.store.connection\n"),
        ("store/records.py", "from atoms.store.schema import variant_of\n"),
        ("coordinator/root.py", "from atoms.store.connection import open_store\n"),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    assert _store_importers(root) == ["core/leak.py", "fs/leak.py"]


def test_the_coordinator_exports_nothing():
    import atoms.coordinator as package

    assert package.__all__ == ()
    assert not hasattr(package, "Lease")
    assert not hasattr(package, "_recovery_lease")


def test_no_module_outside_the_coordinator_names_the_lease():
    """Absence from __all__ is not enforcement -- `atoms.coordinator.root` is still
    importable. The underscore states the contract; this guard covers the population it
    can speak for: in-tree callers."""
    offenders = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path.relative_to(SOURCE_ROOT).parts[0] == "coordinator":
            continue
        source = path.read_text(encoding="utf-8")
        if "_recovery_lease" in source or "coordinator.lease" in source:
            offenders.append(str(path.relative_to(SOURCE_ROOT)))
    assert offenders == []


def test_the_coordinator_fixture_registry_covers_every_test_argument():
    registered = fixture_names(TESTS / "conftest.py")
    assert (
        unregistered_test_arguments(TESTS, registered, "test_coordinator_*.py") == set()
    )
    misplaced = sorted(
        f"{path.name}::{node.name}"
        for path in TESTS.glob("test_coordinator_*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and any(
            decorator_name(decorator) == "fixture" for decorator in node.decorator_list
        )
    )
    assert misplaced == [], f"fixtures must be declared in conftest.py: {misplaced}"
```

- [ ] **Step 4: Write the fresh-process tier**

Create `tests/coordinator_child.py`:

```python
"""The fresh-process half of A5b's #17 and #23 claims.

Run as `python -m tests.coordinator_child <project_root> <metadata_root>`; enters the
production lease with `CERTIFIED_ALLOWLIST` replaced for the test volume and prints one
JSON object describing what a second process sees. A module rather than an inline `-c`
string because it re-runs the whole entry order, including lock acquisition and
reclamation, which is precisely the part a same-process re-entry skips.
"""

from __future__ import annotations

import json
import sys

from atoms.coordinator import root
from atoms.fs.linux import LinuxBackend
from atoms.fs.lock import acquire_project_lock
from atoms.fs.volume import StorageProfile
from tests.fs_support import build_test_allowlist

STORAGE = StorageProfile(profile_id="atoms-test-profile")


def main(project_root: str, metadata_root: str) -> int:
    backend = LinuxBackend()
    with acquire_project_lock(backend, metadata_root) as probe:
        root.CERTIFIED_ALLOWLIST = build_test_allowlist(probe, project_root, STORAGE)
    try:
        with root._recovery_lease(
            backend, project_root, metadata_root, STORAGE
        ) as lease:
            active = lease._store.read_active()
            payload = {
                "trapped": None,
                "workspaces": list(lease._store.list_workspaces()),
                "unindexed_blobs": list(lease._store.list_unindexed_blobs()),
                "active": None if active is None else active.txid,
            }
    except NotImplementedError as caught:
        payload = {"trapped": str(caught)}
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
```

Create `tests/test_coordinator_process.py`:

```python
"""A5b tier 5 -- what a second process sees (design §10)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.coordinator_support import prepared
from tests.store_support import one_effect_spec

ROOT = Path(__file__).resolve().parents[1]


def _second_process(project_root: str, metadata_root: str) -> dict:
    finished = subprocess.run(
        [sys.executable, "-m", "tests.coordinator_child", project_root, metadata_root],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
    )
    return json.loads(finished.stdout)


def _roots(lease) -> tuple[str, str]:
    return (
        os.readlink(f"/proc/self/fd/{lease._binding.project_root_fd}"),
        os.readlink(f"/proc/self/fd/{lease._binding.metadata_root_fd}"),
    )


def test_a_second_lease_reclaims_orphan_scratch_and_spares_the_referenced(leased):
    """Ledger #23's crash-cut claim: the first process exits leaving pre-COMMIT leaves
    behind, and the second process's lease entry drains exactly the unreferenced ones."""
    with leased() as lease:
        project_root, metadata_root = _roots(lease)
        lease._store.create_workspace("orphan").close()
        lease._store.create_workspace("kept").close()
        with lease._store.transaction() as txn:
            txn.insert_record("kept", one_effect_spec())

    seen = _second_process(project_root, metadata_root)

    assert seen["trapped"] is None
    assert seen["workspaces"] == ["kept"]
    assert seen["unindexed_blobs"] == []
    assert seen["active"] is None


def test_a_published_record_traps_a_fresh_lease(leased):
    """Ledger #17's enforcement half survives a restart: the trap is a property of lease
    entry, not of the process that published the record. Blob durability across a
    process boundary is A5a's claim (#22) and is not re-tested here."""
    with leased() as lease:
        project_root, metadata_root = _roots(lease)
        prepared(lease)

    seen = _second_process(project_root, metadata_root)

    assert seen["trapped"] == "recovery execution is not implemented until A7"
```

Run: `uv run pytest tests/test_coordinator_process.py -v`. Expected: PASS, 2 tests.

**If the second test fails because the child never reaches the trap**, the cause is that
`_reclaim_orphans` removed the prepared transaction's workspace before `_resolve` ran — check that
`prepared` published a record naming that txid, since reclamation spares exactly the referenced ones.
Report either way.

- [ ] **Step 5: Update the packaging metadata**

`pyproject.toml:3`:

```toml
import-names = ["atoms.coordinator", "atoms.core", "atoms.fs", "atoms.store"]
import-namespaces = ["atoms"]
```

`tests/test_packaging.py` asserts these; run it and update its expectations in the same commit if it
enumerates the list.

- [ ] **Step 6: Update the ledger**

Move #7, #18, #21, and #23 from the open table into the discharged table, appending
`| A5b | 2026-08-02 | <suites> |` to each row:

| # | Suites |
| --- | --- |
| 7 | `tests/test_coordinator_admission.py`, `tests/test_coordinator_prepare.py` |
| 18 | `tests/test_fs_architecture.py::test_the_only_production_bind_caller_is_the_coordinator_root`, `::test_the_production_bind_call_passes_the_certified_allowlist` |
| 21 | `tests/test_coordinator_admission.py`, `tests/test_coordinator_prepare.py`, `tests/test_coordinator_transitions.py` |
| 23 | `tests/test_coordinator_lease.py`, `tests/test_coordinator_process.py::test_a_second_lease_reclaims_orphan_scratch_and_spares_the_referenced` |

**#7 needs one sentence of its own** in the discharged row's required-behavior column, because A5b
regenerates on two conditions rather than one: *scratch occupancy* (the admitted shape) and *a durable
record already owning the candidate txid*. The second is pre-existing state that leaves no scratch
behind, so occupancy alone would not see it; both return `PreconditionRefused` on exhaustion, and
neither returns `ProtocolError`, which is what the entry forbids.

Relabel the three that stay open, in place:

- **#12** — append: *A5b landed the write half (plan-order persistence, one §7.4 barrier per writable
  step, terminal payload atomic with its state, first-halt-wins enforced by prefix projection); A7 owns
  durable completion of each mutating step.*
- **#17** — append: *A5b landed the lease half (acquire, hold across the write phase, reclaim at every
  entry); the resolve-and-complete half waits on A7, which removes the `NotImplementedError` trap in
  `coordinator/lease.py`.*
- **#19** — append: *A5's part is done — re-resolution against the approved baseline in both project
  space and `metadata_root/work`, before creating anything under either; A6 and A7 remain owners for
  capture and execution.*
- **#9** stays as written — A5b covers its own three entry points and A6–A8 extend
  `_TRANSACTION_STAGE_ENTRY_POINTS` as they land.

- [ ] **Step 7: Update the status in both documents**

`AGENTS.md`'s A5 paragraph becomes "A5a implemented on 2026-08-01, A5b implemented on 2026-08-02", and
the A5b design's `**Status:**` line becomes "Implemented on 2026-08-02. A6–A8 remain unimplemented."
`test_a5_status_is_synchronized_across_authority_documents`
(`tests/test_store_architecture.py:1285`) reads **both** documents already; update its expected strings
in the same commit, including the `"A5b implemented" not in agents` negative assertion, which now
inverts.

- [ ] **Step 8: Run the full gates**

Run: `uv run pytest -q && uv run ruff check && uv run pyright`
Expected: all clean.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat(coordinator): guard the boundary and discharge #7, #18, #21, and #23"
```

---

## Self-Review

**Spec coverage.** Every design section maps to a task: §5.0–§5.1 → Task 1; §5.2 → Task 2; §5.3 and
authority §7.1's lock duration → Task 3; §6.4's observers → Task 4; §6.2–§6.3 and §6.5's gate → Task 5;
§6.4's three branches and the translation → Task 6; §7 → Task 7; §8 → Task 8; §10's architecture and
process tiers plus §3.1's ledger outcomes → Task 9. §9's error table is distributed across the tasks
that raise each type, and no task introduces a new one. §9's `TransactionHalted` row is deliberately
unimplemented — the design marks it unreachable in A5b, and no task claims to reach it.

**The design deviation from the previous revision is gone.** `root.py` now owns the whole resource
stack, exactly as design §4.1 says, and tests reach the protocol by patching
`root.CERTIFIED_ALLOWLIST`. There is no `_lease` and no allowlist parameter, so `_production_bind_callers`
has exactly one member to find.

**One addition to the design, flagged for the author's ruling.** Design §6.4's table says the `WorkRoot`
branch "never enters `observe_child`" and must "re-resolve `metadata_root/work` against
`approved.work_base`", but names no mechanism. This plan adds `observe_work_child(binding, leaf)` beside
`observe_child` rather than putting metadata-space syscalls in the coordinator. The design is amended in
the same commit as this plan, per the authority-order rule.

**Type consistency.** `Lease._binding` / `Lease._store` are spelled identically in every task.
`_require_admitted` is defined once in `admission.py` and called by all three entry points.
`_require_work_slot_free` is defined in Task 6 and consumed by Task 7's `open_workspace`.
`prepare_transaction(lease, approved, workspace, manifest)` and
`persist_plan_prefix(lease, approved, plan, start)` match design §6.5 and §8.1 exactly. `_persist_one`
takes `(lease, txid, step)` — no `plan`, because every payload it writes is on the step.

**One deferred proof, tracked across two tasks.** Task 5's `occupied = ()` reset cannot be proved
load-bearing while `_occupied_scratch` is stubbed. Task 5 Step 6 records that rather than claiming it,
and Task 6 Step 8 arms and runs the mutation.

**Two places the implementer must read the code rather than trust this plan**, both flagged inline:
Task 8 Step 1's `OBSERVED_ABSENT` / `EntryIdentity` / `FileBuildRelation` import locations, and Task 4
Step 3's lift of `_NAMESPACE_CONTRADICTIONS` out of `PathResolver`, which touches existing code and must
leave `tests/test_fs_resolve_*.py` green before anything new is written.

**Everything the plan asserts about A4b's and A3's output shapes was executed, not inferred.** The
approval shapes, the scratch leaf format, the `work_base is None` case, and all five plan step vectors
in the Measured Facts table came from probe runs against a real ext4 volume on 2026-08-02. Where a
measured fact contradicts a task's code, the fact wins and the implementer reports it.
