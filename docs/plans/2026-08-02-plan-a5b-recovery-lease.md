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
| `PathResolver._NAMESPACE_CONTRADICTIONS` has **one** use inside the class — `self._NAMESPACE_CONTRADICTIONS` at `resolve.py:207`. | `resolve.py:144`, `:207` |
| `OBSERVED_ABSENT`, `EntryIdentity`, and `FileBuildRelation` are all re-exported by `atoms.core.recovery`. | package `__init__` |
| `promote_staging` renames each blob into `blobs/` and flushes it **before** `INSERT_BLOB` runs in the SQLite transaction. A rolled-back transaction therefore leaves a real unindexed blob — the only way to produce one. | probe: rollback left one digest in `list_unindexed_blobs()` |
| `bind_project_volume` with the shipping empty allowlist raises `CapabilityUnavailable("volume configuration is not on the supplied durability allowlist: …")` — but **only after** its own step-4 reclamation, so `probe/` is emptied even with no root-level reclamation at all. This refusal cannot discriminate where `root.py` reclaims. | probe, 2026-08-02; `binding.py:192` then `:194` |
| An **absent project root** refuses at `binding.py:177`'s `establish_root(create=False)` — the first statement in `bind_project_volume`, before reclamation — with `FileNotFoundError('[Errno 2] No such file or directory')` and no filename. Assert the type, not the message. | probe, 2026-08-02 |
| `metadata_root/probe/` exists after the first successful bind, so `reclaim_probe_survivors` has somewhere to reclaim from on a later entry. | probe: entries were `blobs, lock, probe, staging, work` |
| A topology that parents `PersistentNode('d/f.txt')` and `ScratchNode('e1', STAGING)` **directly at `ProjectRoot()`** — dropping A4b's intermediate `TopologyDirectory(node_id=0)` — is accepted by both `build_recovery_snapshot` and `classify_recovery` (same `ActionPlan`, 3 steps) and is `!=` the resolved topology. The topology guard therefore has an independently failing case. | probe, 2026-08-02 |
| `coherence_findings` compares a stored `HaltDiagnostic`'s `commit_decision` and `journals` against the durable rows, and **nothing else** — not `paths`, not `pre_halt_state`. `matching_diagnostic("e1")` is therefore committable against the `d/f.txt` record. | `records.py:484-492` |
| No coherence rule couples a journal state to the transaction state, so a journal row may be advanced independently of the record's state. | `records.py:455-500` |
| `HeldProjectLock._lock_fd` holds the `flock`. `os.dup` of it shares the open file description, so the lock survives the original's close — the one-line mutation that leaks the lock. | `lock.py:145-153` |
| A test whose setup enters `_recovery_lease` runs any mutation placed in that function **twice**. Ungated, Task 3 Step 8's mutations 1–3 fire before the `before` snapshot and 5 deadlocks the suite against its own blocking `flock`. Gate every one on `if store.read_active() is not None:`. | Task 3 execution, 2026-08-02 |
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
  `SIM117` refuses nested bare `with` statements — write them as one `with A, B:`. Measured during
  Task 4: it fires only when the outer body is *exactly* the nested `with`; an outer body that also
  contains an `assert` is not combinable and ruff passes it. Do not collapse a `with` pair on the
  assumption that SIM117 demands it — run `uv run ruff check` and find out. `B018` refuses a
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
- **Three tests are expected to fail before Task 9 retires them, and only these three.** Each asserts
  that some part of the coordinator does not exist yet, and each is falsified by the task named:

  | Guard | Red from | Retired by |
  | --- | --- | --- |
  | `test_fs_architecture.py::test_no_production_caller_of_bind_exists_yet` | Task 1 | Task 9 Step 1 |
  | `test_store_architecture.py::test_no_production_module_outside_the_package_imports_the_store` | Task 1 | Task 9 Step 1 |
  | `test_fs_architecture.py::test_no_consumer_of_the_approved_spec_exists_yet` | **Task 5** | Task 9 Step 2 |

  The third scans all of `src/atoms/` for any mention of `ProjectApprovedSpec`, and `admission.py` is
  necessarily its first production consumer; its own docstring already says it is replaced when A5
  lands. Until Task 9, a task's `uv run pytest -q` gate is clean **when the failures are a subset of
  these three** — check the names, not just the count, and report any other failure rather than
  absorbing it into this allowance. Do not touch either guard file before Task 9.
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
    def enter(ingredients=None):
        backend, project_root, metadata_root, storage = ingredients or coordinator_on()
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

`enter(ingredients)` takes the optional tuple because `coordinator_on()` names a **fresh** metadata
root per call. Two `leased()` calls therefore model two projects, never a restart of one. A test that
needs a second entry over the *same* project calls `coordinator_on()` itself and passes the tuple to
both — Task 3's trap-release cases and Task 9's process tier both need that.

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

import hashlib
import os

from atoms.core.canonical import canonical_json
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


def spec_digest(spec: TransactionSpec) -> str:
    """A short stable identity for a spec, for comparison across a process boundary.

    `canonical_json` is the same encoding A5a stores and re-verifies, so two specs share
    a digest exactly when the store would treat them as one. Hashed rather than sent
    whole so the child's JSON stays small and an assertion failure stays readable.
    """
    return hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()


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


def test_reclamation_removes_an_unindexed_blob(leased):
    """A blob is unindexed exactly when it is on disk with no `blob` row.

    `promote_staging` renames each blob into `blobs/` and flushes it BEFORE its
    `INSERT_BLOB` runs, so a transaction that rolls back leaves precisely that. This is
    the only way to produce one, and asserting reclamation drains an empty list would
    prove nothing.
    """
    from atoms.store.blobs import StagedBlob

    from atoms.coordinator.lease import _reclaim_orphans
    from tests.store_support import digest_of, spec_referencing, stage

    content = b"orphaned by a cut before COMMIT"
    digest = digest_of(content)

    with leased() as lease:
        with lease._store.create_workspace("orphan2") as workspace:
            stage(workspace, "b0", content)
            with pytest.raises(RuntimeError), lease._store.transaction() as txn:
                txn.promote_staging(
                    workspace,
                    (StagedBlob(name="b0", digest=digest, byte_len=len(content)),),
                )
                txn.insert_record("orphan2", spec_referencing(content))
                raise RuntimeError("cut before COMMIT")

        assert lease._store.list_unindexed_blobs() == (digest,)

        workspaces, blobs = _reclaim_orphans(lease._store)

        assert workspaces == ("orphan2",)
        assert blobs == (digest,)
        assert lease._store.list_unindexed_blobs() == ()
```

`pytest.raises` is **outer** in that `with`, per the Global Constraints: the body calls a mutating store
method, so an inner `pytest.raises` would let `transaction()`'s generator resume past its `yield` and
raise a second `ProtocolError` outside every `raises` scope.

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

- [ ] **Step 7: Prove reclamation runs even when binding then refuses**

Ledger #17 says *every* lease entry reclaims probe survivors, which holds only if `root.py` reclaims
before the first thing that can refuse.

**Not every refusal can prove that.** `bind_project_volume` reclaims at `binding.py:192` and only then
matches the allowlist at `:194`, so an empty-allowlist refusal empties `probe/` by itself — measured
2026-08-02, with no root-level reclamation in the call at all. A test built on that refusal stays green
wherever `root.py` puts its reclamation, which makes it worthless as a guard.

The refusal must land **before** `binding.py:192`. An absent project root does: `establish_root` at
`binding.py:177` is the function's first statement, and it raises `FileNotFoundError` for a path that
is not there. Nothing has been reclaimed by anyone at that point, so the survivor is gone only if
`root.py` removed it.

```python
def test_probe_survivors_are_reclaimed_before_an_early_bind_refusal(
    coordinator_on, leased
):
    """Ledger #17, at the one refusal that can discriminate.

    The `FileNotFoundError` carries no filename -- measured `'[Errno 2] No such file or
    directory'` -- so the type is the assertion.
    """
    from atoms.coordinator import root

    ingredients = coordinator_on()
    backend, project_root, metadata_root, storage = ingredients

    # One successful entry, so metadata_root/probe/ exists to be reclaimed from.
    with leased(ingredients):
        pass
    probe_dir = Path(metadata_root) / "probe"
    assert probe_dir.is_dir()
    (probe_dir / "survivor.db").write_text("debris", encoding="utf-8")

    absent = f"{project_root}-does-not-exist"
    assert not Path(absent).exists()
    with pytest.raises(FileNotFoundError):
        with root._recovery_lease(backend, absent, metadata_root, storage):
            pass

    assert list(probe_dir.iterdir()) == []
```

Then move `reclaim_probe_survivors(lock)` to *after* the `bind_project_volume` line — where it is
inside the `with` body that never runs — re-run, and confirm this test fails on
`list(probe_dir.iterdir()) == []`. Restore and report the observed failure.

**Also run the mutation against the empty-allowlist shape**, as the negative control: temporarily swap
`absent` for `project_root` and `root.CERTIFIED_ALLOWLIST` for `DurabilityAllowlist(entries=frozenset())`,
keep the moved reclamation, and confirm the test **passes** anyway. **The expected exception changes with
it** — an empty allowlist refuses at `binding.py:194` with `CapabilityUnavailable`, not
`FileNotFoundError`, so the control run must also swap `pytest.raises(FileNotFoundError)` for
`pytest.raises(CapabilityUnavailable)` or it fails at the wrong place and proves nothing. Report both
results together — the pair is what shows the chosen refusal is the load-bearing one. Restore all three
edits.

- [ ] **Step 8: Prove the trap mutates no project path and releases everything**

The process tier cannot prove release: a child exiting frees its descriptors and its `flock` whether or
not the context managers unwound correctly. These three properties are therefore same-process only.

**Path names are not project state.** An overwrite in place, a `chmod`, or a truncation all leave the
tree shape identical, so the comparison snapshots what a mutation would actually move — kind, mode,
size, and content — for every path under the root:

```python
def _project_state(project_root: str) -> dict[str, tuple[object, ...]]:
    """The project root and every path under it, with the state a mutation would move.

    Three things beyond kind/mode/content, each closing a hole the others leave open:

    * `st_dev` and `st_ino`, because a path replaced by an inode of identical kind,
      mode, and content is otherwise invisible. These are already how A4b states path
      identity, so this is the project's own vocabulary rather than a new one.
    * the root itself, keyed `"."`, because nothing under it records a `chmod` on it --
      and against an empty root, nothing under it records anything at all.
    * `lstat` throughout, so a symlink is compared as a symlink rather than followed.

    Content is hashed rather than compared inline so a failure message stays readable.
    """
    state: dict[str, tuple[object, ...]] = {}

    def record(full: str) -> None:
        info = os.lstat(full)
        if stat.S_ISLNK(info.st_mode):
            payload: object = os.readlink(full)
        elif stat.S_ISDIR(info.st_mode):
            payload = None
        else:
            payload = hashlib.sha256(Path(full).read_bytes()).hexdigest()
        state[os.path.relpath(full, project_root)] = (
            stat.S_IFMT(info.st_mode),
            stat.S_IMODE(info.st_mode),
            info.st_dev,
            info.st_ino,
            info.st_size,
            payload,
        )

    record(project_root)
    for directory, directories, files in os.walk(project_root):
        directories.sort()
        for name in sorted(directories) + sorted(files):
            record(os.path.join(directory, name))
    return state


def _trapping_lease(coordinator_on, leased):
    """Ingredients whose project root holds a real file and whose metadata root holds a
    live record, so re-entering `_recovery_lease` over them reaches `_resolve` and traps.

    The file is not incidental: a state comparison over an empty tree has almost nothing
    to compare, and two of the mutations below need an existing path to move.
    """
    from tests.coordinator_support import make_child_directory
    from tests.store_support import one_effect_spec

    ingredients = coordinator_on()
    with leased(ingredients) as lease:
        make_child_directory(lease)
        fd = os.open(
            "d/f.txt",
            os.O_CREAT | os.O_WRONLY,
            0o644,
            dir_fd=lease._binding.project_root_fd,
        )
        try:
            os.write(fd, b"pre-existing")
        finally:
            os.close(fd)
        with lease._store.transaction() as txn:
            txn.insert_record("tx1", one_effect_spec())
            txn.set_active("tx1")
    return ingredients


def test_the_trap_mutates_no_project_path(coordinator_on, leased):
    from atoms.coordinator import root

    backend, project_root, metadata_root, storage = _trapping_lease(
        coordinator_on, leased
    )

    before = _project_state(project_root)
    with pytest.raises(NotImplementedError) as caught:
        with root._recovery_lease(backend, project_root, metadata_root, storage):
            pass

    assert str(caught.value) == "recovery execution is not implemented until A7"
    assert _project_state(project_root) == before


def test_the_trap_leaks_no_descriptor(coordinator_on, leased):
    """The trap raises from inside `_recovery_lease`'s generator, before its `yield`,
    so every `with` in the stack unwinds. One count covers the lock fd, both root
    descriptors, and SQLite's own handles -- a leak of any of them moves it."""
    from atoms.coordinator import root

    backend, project_root, metadata_root, storage = _trapping_lease(
        coordinator_on, leased
    )

    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(NotImplementedError) as caught:
        with root._recovery_lease(backend, project_root, metadata_root, storage):
            pass

    assert str(caught.value) == "recovery execution is not implemented until A7"
    assert len(os.listdir("/proc/self/fd")) == before


def test_the_trap_releases_the_project_lock(coordinator_on, leased):
    """Separate from the descriptor count so a contender proves the `flock` itself is
    gone, not merely that the number of open files came back."""
    from atoms.coordinator import root

    backend, project_root, metadata_root, storage = _trapping_lease(
        coordinator_on, leased
    )

    with pytest.raises(NotImplementedError) as caught:
        with root._recovery_lease(backend, project_root, metadata_root, storage):
            pass

    assert str(caught.value) == "recovery execution is not implemented until A7"
    assert _contend(metadata_root) == 0
```

Add `import hashlib` and `import stat` to the module's import block.

**Five mutations, each of which must keep the trap firing.** Deleting `_resolve`'s call site would make
all three tests fail at `pytest.raises` before reaching a single property assertion, which proves only
that the trap exists — something the earlier tests already prove. Each mutation below is inserted into
`_recovery_lease` immediately **before** `_resolve(store)`, where `binding` and `lock` are both in
scope, so the trap still raises and the property is the only thing that changes.

**Every mutation must be gated on `if store.read_active() is not None:` and indented under it.**
Measured 2026-08-02, the hard way: `_trapping_lease` reaches its live record by entering
`_recovery_lease` itself, so an ungated mutation fires **twice** — once during setup, before `before` is
sampled, and once at the measured entry. Ungated, mutations 1 and 2 failed *nothing* (the setup entry
had already made the change, so the snapshot contained it), mutation 3 failed all three tests on a
`FileNotFoundError` because `d/` does not exist yet at the setup entry, and mutation 5 **deadlocked the
suite** — its setup-entry `os.dup(lock._lock_fd)` held the `flock` into the measured entry's blocking
`flock(LOCK_EX)`. The gate is the trap's own condition, and the setup entry has no active record yet, so
it fires exactly when the trap does. With it, all five behave as tabulated.

| # | Mutation | Must fail | Must still pass |
| --- | --- | --- | --- |
| 1 | `os.close(os.open("mutant", os.O_CREAT \| os.O_WRONLY, 0o600, dir_fd=binding.project_root_fd))` | `test_the_trap_mutates_no_project_path` | the other two |
| 2 | `os.fchmod(binding.project_root_fd, 0o701)` | `test_the_trap_mutates_no_project_path`, on the `"."` entry alone | the other two |
| 3 | the inode swap below | `test_the_trap_mutates_no_project_path`, on `st_ino` alone | the other two |
| 4 | `os.dup(binding.project_root_fd)` | `test_the_trap_leaks_no_descriptor` | the other two |
| 5 | `os.dup(lock._lock_fd)` | `test_the_trap_releases_the_project_lock` **and** `test_the_trap_leaks_no_descriptor` | `test_the_trap_mutates_no_project_path` |

**Mutation 1 must close what it opens.** A bare `os.open` also leaks its descriptor and would fail
`test_the_trap_leaks_no_descriptor` too, which would say nothing about whether the project-state
comparison works.

**Mutation 2** must use a mode the root does not already have; if `0o701` happens to be its mode,
pick another and say which.

**Mutation 3** replaces `d/f.txt` with a byte-identical file at a fresh inode, so names, mode, size,
and content hash are all unchanged and `st_ino` is the only field that moves:

```text
            fd = os.open("copy", os.O_CREAT | os.O_WRONLY, 0o644,
                         dir_fd=binding.project_root_fd)
            os.write(fd, b"pre-existing"); os.close(fd)
            os.rename("copy", "d/f.txt", src_dir_fd=binding.project_root_fd,
                      dst_dir_fd=binding.project_root_fd)
```

Mutation 5 failing two tests is expected, not a defect in the split: `flock` is held by the open file
description, so a duplicated lock descriptor keeps the lock alive past the original's close *and* is
itself a leaked descriptor. What the pair shows is that the lock assertion is not vacuous — mutations
1–4 leave it green and 5 does not. Restore after each, confirm `git status --porcelain` is empty, and
report all six observed failures.

- [ ] **Step 9: Run the gates and commit**

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

Delete the class attribute at `resolve.py:144` and change its **one** use inside the class —
`self._NAMESPACE_CONTRADICTIONS` at `resolve.py:207` — to the module-level name. Run the full
`tests/test_fs_resolve_*.py` suite after this edit and before writing anything new; it must stay green.

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

    Measured 2026-08-02 on the ext4 test volume: rmdir followed by mkdir returns the
    *same* `st_ino` every time, because the inode is freed and immediately reallocated.
    Five cycles in a row all reported 60705148, so the obvious spelling of this helper
    leaves identity unchanged and `test_a_moved_scratch_parent_refuses_naming_identity`
    fails with `DID NOT RAISE`. The replacement is therefore built beside `d` while `d`
    still holds its inode -- which forces a distinct one -- and then renamed over the
    emptied name. The spelling is identical either way; only the identity moves, which
    is precisely the drift ledger #19 names. The assert is what keeps a future inode
    allocator from making this test vacuous instead of failing.
    """
    _ = approved
    root_fd = lease._binding.project_root_fd
    before = os.stat("d", dir_fd=root_fd).st_ino
    os.mkdir("d.replacement", dir_fd=root_fd)
    os.rmdir("d", dir_fd=root_fd)
    os.rename("d.replacement", "d", src_dir_fd=root_fd, dst_dir_fd=root_fd)
    assert os.stat("d", dir_fd=root_fd).st_ino != before, (
        "the replacement reused the original inode, so the test would pass vacuously"
    )


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

Design §10's mismatch matrix needs one independent case per comparand, not one case that happens to
trip identity first. Identity is covered above; these are the rest.

```python
def _observation_like(observed, **changes):
    from atoms.fs.resolve import ChildObservation

    fields = {
        "parent_identity": observed.parent_identity,
        "parent_constraints": observed.parent_constraints,
        "present": observed.present,
    }
    return ChildObservation(**{**fields, **changes})


def test_a_changed_name_max_refuses_naming_constraints(leased, monkeypatch):
    """NAME_MAX is half of DirectoryConstraints, and it cannot be changed by any
    syscall a test may issue -- so the comparison is driven directly."""
    from atoms.fs.lookup import DirectoryConstraints
    from atoms.coordinator import admission

    with leased() as lease:
        approved = admission.admit(lease, compiled_for(lease))
        entry = next(
            item
            for item in approved.directories
            if type(item).__name__ == "ApprovedExistingDirectory"
            and item.node == approved.scratch[0].parent_node
        )
        shrunk = DirectoryConstraints(
            lookup_proof=entry.constraints.lookup_proof,
            name_max=entry.constraints.name_max - 1,
        )
        real = admission.observe_child
        monkeypatch.setattr(
            admission,
            "observe_child",
            lambda binding, parent, leaf: _observation_like(
                real(binding, parent, leaf), parent_constraints=shrunk
            ),
        )

        with pytest.raises(PreconditionRefused) as caught:
            admission._occupied_scratch(lease, approved)

        assert "changed lookup constraints since approval" in str(caught.value)


def test_a_changed_lookup_proof_refuses_naming_constraints(leased, monkeypatch):
    """The other half. A directory that became casefold has an unreproducible lookup
    relation, so every approved name under it is meaningless -- but the observed
    NAME_MAX is unchanged, so a test that only moved NAME_MAX would not cover it."""
    from atoms.fs.lookup import DirectoryConstraints, LookupProof
    from atoms.coordinator import admission

    with leased() as lease:
        approved = admission.admit(lease, compiled_for(lease))
        real = admission.observe_child

        def folded(binding, parent, leaf):
            observed = real(binding, parent, leaf)
            return _observation_like(
                observed,
                parent_constraints=DirectoryConstraints(
                    lookup_proof=LookupProof.UNREPRODUCIBLE_CASEFOLD,
                    name_max=observed.parent_constraints.name_max,
                ),
            )

        monkeypatch.setattr(admission, "observe_child", folded)

        with pytest.raises(PreconditionRefused) as caught:
            admission._occupied_scratch(lease, approved)

        message = str(caught.value)
        assert "changed lookup constraints since approval" in message
        assert "unreproducible_casefold" in message


def test_a_parent_that_moved_across_mounts_refuses(leased, monkeypatch):
    """Mount membership. `open_child_directory` carries RESOLVE_NO_XDEV, so a parent
    that became a mount point raises EXDEV; A5b's obligation is that the EXDEV reaches
    the caller as drift rather than as a bare OSError. Injected because mounting
    requires privileges this suite does not assume."""
    import errno

    from atoms.fs.linux import LinuxBackend
    from atoms.coordinator import admission

    with leased() as lease:
        approved = admission.admit(lease, compiled_for(lease))
        real = LinuxBackend.open_child_directory

        def crosses(self, parent_fd, name):
            if name == "d":
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            return real(self, parent_fd, name)

        monkeypatch.setattr(LinuxBackend, "open_child_directory", crosses)

        with pytest.raises(PreconditionRefused) as caught:
            admission._occupied_scratch(lease, approved)

        message = str(caught.value)
        assert "no longer resolves at component 'd'" in message


def test_a_capability_refusal_during_re_resolution_becomes_drift(leased, monkeypatch):
    """The second declared resolver refusal type. §9 requires both to arrive as
    PreconditionRefused once a proof exists, and one type proves only one branch."""
    from atoms.core.errors import CapabilityUnavailable
    from atoms.coordinator import admission

    with leased() as lease:
        approved = admission.admit(lease, compiled_for(lease))

        def unavailable(*_args, **_kwargs):
            raise CapabilityUnavailable("synthetic capability refusal")

        monkeypatch.setattr(admission, "observe_child", unavailable)

        with pytest.raises(PreconditionRefused) as caught:
            admission._occupied_scratch(lease, approved)

        message = str(caught.value)
        assert "post-approval drift during re-resolution" in message
        assert "synthetic capability refusal" in message


def test_an_approve_for_project_refusal_is_never_translated(leased, monkeypatch):
    """The translation covers re-resolution only. Approval's own refusals are genuine
    approval refusals and must reach the caller with their type intact -- wrapping them
    would tell a caller that external state drifted when the spec was simply refused."""
    from atoms.core.errors import ProjectApprovalRefused
    from atoms.coordinator import admission

    with leased() as lease:
        compiled = compiled_for(lease)

        def refuse(*_args, **_kwargs):
            raise ProjectApprovalRefused("synthetic approval refusal")

        monkeypatch.setattr(admission, "approve_for_project", refuse)

        with pytest.raises(ProjectApprovalRefused) as caught:
            admission.admit(lease, compiled)

        assert "synthetic approval refusal" in str(caught.value)
```

The last case is the reason `_translated_resolution` wraps only the `_occupied_scratch` body and never
the `approve_for_project` call above it; move the `with` to enclose the whole loop and this test turns
`PreconditionRefused`, which is Step 9's mutation.

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
    """`node` stays `object` so the unmapped-node test can pass a bare `object()`.

    Scanned rather than indexed: `dict.__contains__` accepts `object` but
    `dict.__getitem__` does not, and pyright does not narrow `object` to `TopologyNode`
    from a `not in` test, so `return mapping[node]` after the guard is a
    reportArgumentType error. The map holds at most four entries.
    """
    for candidate, path in mapping.items():
        if candidate == node:
            return path
    raise ProtocolError(f"no parent path for topology node {node!r}")


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

`_occupied_scratch` is real now — but **neither existing exhaustion test can arm this mutation**, and
reusing one would produce a false negative. Measured 2026-08-02 during Task 5's review:

- `test_every_candidate_owned_by_a_record_exhausts_the_bound` collides on a durable record at every
  attempt, so `continue` fires each time and `_occupied_scratch` is never called. `occupied` never
  leaves its `()` initializer, with or without the reset.
- `test_persistent_occupancy_exhausts_and_names_the_leaves` has the mirror problem: no record
  collisions at all, so nothing ever needs clearing.

Only a **mixed** sequence distinguishes the two versions. Add one — a first candidate whose scratch is
occupied, followed by candidates that collide with durable records, then exhaustion:

```python
def test_a_record_collision_clears_an_earlier_candidates_occupancy(leased, monkeypatch):
    """The `occupied = ()` reset in `admit`'s record-collision branch.

    Without it, a leaf found occupied on an early attempt is still named in the final
    refusal even though the attempt that exhausted the bound was a record collision --
    reporting external occupancy for a candidate whose scratch was never examined.
    """
    from atoms.coordinator import admission
    from tests.coordinator_support import compiled_for
    from tests.store_support import one_effect_spec

    with leased() as lease:
        compiled = compiled_for(lease)
        # "a" has no record, so its scratch is consulted; "b" and "c" collide first.
        for txid in ("b", "c"):
            with lease._store.transaction() as txn:
                txn.insert_record(txid, one_effect_spec())
        issued = iter(["a", "b", "c"])
        monkeypatch.setattr(admission, "new_txid", lambda: next(issued))
        monkeypatch.setattr(
            admission, "_occupied_scratch", lambda lease, approved: ("stale.leaf",)
        )

        with pytest.raises(PreconditionRefused) as caught:
            admission.admit(lease, compiled)

        message = str(caught.value)
        assert "no usable txid after 3 attempts" in message
        assert "scratch occupied" not in message
```

`_occupied_scratch` is patched rather than driven through real occupancy because this case is about the
reset alone: it needs occupancy on attempt 1 and record collisions afterwards, and the real function
would have to be fed a leaf derived from the txid `new_txid` happens to issue. The neighbouring
`test_persistent_occupancy_exhausts_and_names_the_leaves` already exercises the real one. If you can
build the same shape without the patch, prefer that and say so.

Then delete `occupied = ()` from the record-collision branch in `admit`, re-run, and confirm this test
— and only this test — fails on `"scratch occupied" not in message`. The reviewer's probe observed
`no usable txid after 3 attempts; scratch occupied at stale.leaf`. Restore, confirm
`git status --porcelain` is empty, re-run, and report the observed message.

- [ ] **Step 9: Prove the translation's scope and the planned branch's restraint**

Two mutations, both one line:

1. Move `with _translated_resolution():` out of `_occupied_scratch` and around `admit`'s loop body so
   it also encloses `approve_for_project`. Re-run
   `test_an_approve_for_project_refusal_is_never_translated` and confirm it fails with
   `PreconditionRefused` where `ProjectApprovalRefused` was expected. Restore.
2. Prove the planned branch never compares a planned identity:

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

*(Task 5's header listed `tests/coordinator_support.py` as Modify but described no change to it and
needed none — `compiled_for` had already landed. That header was stale; this one is not.)*

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


def test_nothing_is_durable_until_the_publication_commit(leased, monkeypatch):
    """The single barrier authority §7.3 step 4 requires.

    The cut is driven THROUGH `prepare_transaction`, not by repeating its body: a test
    that re-implements the transaction stays green when the production function's own
    ordering is wrong, which is the whole thing this asserts. Patching the last call
    inside the body is the smallest cut that leaves the earlier writes staged.
    """
    from atoms.store.connection import _StoreTransaction

    from atoms.coordinator.prepare import open_workspace, prepare_transaction

    with leased() as lease:
        approved = admission_for(lease)
        workspace = open_workspace(lease, approved)
        manifest = stage_manifest(workspace)

        def cut(self, txid):
            raise RuntimeError("cut inside prepare_transaction, before COMMIT")

        monkeypatch.setattr(_StoreTransaction, "set_active", cut)

        with pytest.raises(RuntimeError) as caught:
            prepare_transaction(lease, approved, workspace, manifest)

        workspace.close()
        assert "before COMMIT" in str(caught.value)
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
def flattened_topology(approved: ProjectApprovedSpec) -> RecoveryTopology:
    """The proof's own nodes, re-parented directly at the project root.

    A4b resolves `d/f.txt` through an intermediate `TopologyDirectory(node_id=0)`;
    dropping it and parenting both the persistent and the scratch node at `ProjectRoot()`
    keeps every rule `_validate_topology` enforces -- exact persistent and scratch
    coverage, a shared resolved parent, an acyclic tree -- while producing a topology
    that is `!=` the resolved one. Measured 2026-08-02: `build_recovery_snapshot` and
    `classify_recovery` both accept it and yield the same `ActionPlan` shape.

    This is the only way to reach `_require_projection_matches`'s topology comparison,
    since the compiled comparison runs first and a different spec would trip that.
    """
    project = ProjectRoot()
    return RecoveryTopology(
        parents=tuple(
            TopologyParent(node=edge.node, parent=project)
            for edge in approved.topology.parents
            if type(edge.node) in (PersistentNode, ScratchNode)
        )
    )


def snapshot_for(
    approved: ProjectApprovedSpec,
    *,
    state: TransactionState,
    journal: JournalState,
    live: ObservedEntry,
    staged: ObservedEntry,
    relation: FileBuildRelation | None = None,
    path: str = "d/f.txt",
    topology: RecoveryTopology | None = None,
) -> RecoverySnapshot:
    """A snapshot over the PROOF's compiled spec and topology.

    Built from `approved` rather than from a parallel fixture so that
    `_require_projection_matches`'s compiled and topology equality checks are satisfied
    by construction: a snapshot describing a different spec is exactly the mismatch that
    guard exists to catch. `topology` overrides only that half, for the one test that
    needs the compiled halves to agree while the topologies differ.

    `relation` must be None outside JournalState.STARTED -- `build_recovery_snapshot`
    refuses `file_build_relation is present in the wrong construction state` otherwise.
    """
    return build_recovery_snapshot(
        compiled=approved.compiled,
        topology=approved.topology if topology is None else topology,
        transaction_state=state,
        commit_decision=CommitDecision.UNCOMMITTED,
        rollback_result=None,
        halt_diagnostic=None,
        active=True,
        journals=(EffectJournalState("e1", journal),),
        persistent_observations=(PersistentObservation(path, live),),
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


def other_file_spec() -> TransactionSpec:
    """A second spec over the same existing parent, for the record-spec mismatch case."""
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "3" * 64,
        initial_surface={"d/g.txt": ABSENT},
        final_surface={"d/g.txt": POST},
        effects=[CreateFileNoClobber(effect_id="e1", path="d/g.txt", post=POST)],
    )


def reapproved_under(
    lease: Lease, txid: str, compiled: CompiledSpec
) -> ProjectApprovedSpec:
    """A second proof for a chosen txid.

    `approve_for_project` takes the txid from its `ProjectContext`, so two proofs can
    name one transaction while describing different specs. That is the only way to make
    the durable record's spec disagree with the plan prefix while every other field --
    txid, state, commit decision, journals -- still matches, which is what isolates the
    spec comparison from its neighbours.
    """
    from atoms.fs.approval import ProjectContext, approve_for_project

    return approve_for_project(compiled, ProjectContext(lease._binding, txid))
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
    OBSERVED_ABSENT,
    CommitDecision,
    EffectJournalState,
    EntryIdentity,
    FileBuildRelation,
    JournalState,
    ObservedEntry,
    ObservedFile,
    PersistentNode,
    PersistentObservation,
    ProjectRoot,
    RecoverySnapshot,
    RecoveryTopology,
    ScratchNode,
    ScratchObservation,
    ScratchRole,
    TopologyParent,
    TransactionState,
    build_recovery_snapshot,
    classify_recovery,
)
from atoms.core.recovery.plan import RecoveryPlan
```

`OBSERVED_ABSENT`, `EntryIdentity`, and `FileBuildRelation` are all re-exported by
`atoms.core.recovery`, so they belong in the single import above rather than reaching into
`atoms.core.recovery.model`.

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


def test_a_plan_bound_to_another_compiled_spec_refuses(leased):
    """The plan and the proof must describe one transaction before the record is even
    consulted. Same lease, same txid, different spec -- so the binding and txid gates
    both pass and this check is the only one left to fire."""
    from atoms.core.compiler import compile_spec
    from atoms.coordinator.transitions import persist_plan_prefix
    from tests.coordinator_support import (
        other_file_spec,
        reapproved_under,
        snapshot_for,
    )
    from atoms.core.recovery import (
        OBSERVED_ABSENT,
        JournalState,
        TransactionState,
        classify_recovery,
    )

    with leased() as lease:
        approved, _ = prepared_metadata_only(lease)
        other = reapproved_under(lease, approved.txid, compile_spec(other_file_spec()))
        foreign_plan = classify_recovery(
            snapshot_for(
                other,
                state=TransactionState.PREPARED,
                journal=JournalState.PENDING,
                live=OBSERVED_ABSENT,
                staged=OBSERVED_ABSENT,
                path="d/g.txt",
            )
        )

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, foreign_plan, 0)

        assert "different compiled spec" in str(caught.value)


def test_a_plan_bound_to_another_topology_refuses(leased):
    """The topology comparison in isolation, with the compiled halves identical.

    A4b resolves `d/f.txt` through an intermediate `TopologyDirectory`; this snapshot
    parents the same two nodes at the project root instead. Measured 2026-08-02:
    structurally valid, accepted by `build_recovery_snapshot` and `classify_recovery`,
    and `!=` the resolved topology -- so the compiled equality passes and this check is
    the only one left to fire.
    """
    from atoms.coordinator.transitions import persist_plan_prefix
    from tests.coordinator_support import flattened_topology, snapshot_for
    from atoms.core.recovery import (
        OBSERVED_ABSENT,
        JournalState,
        TransactionState,
        classify_recovery,
    )

    with leased() as lease:
        approved, _ = prepared_metadata_only(lease)
        rearranged = flattened_topology(approved)
        assert rearranged != approved.topology

        plan = classify_recovery(
            snapshot_for(
                approved,
                state=TransactionState.PREPARED,
                journal=JournalState.PENDING,
                live=OBSERVED_ABSENT,
                staged=OBSERVED_ABSENT,
                topology=rearranged,
            )
        )

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, plan, 0)

        assert "different topology" in str(caught.value)


def test_a_record_whose_spec_disagrees_with_the_prefix_refuses(leased):
    """The spec comparison in isolation: the plan and the proof agree with each other,
    and every other durable field matches, but the record on disk was published from a
    different spec."""
    from atoms.core.compiler import compile_spec
    from atoms.coordinator.transitions import persist_plan_prefix
    from tests.coordinator_support import (
        other_file_spec,
        reapproved_under,
        snapshot_for,
    )
    from atoms.core.recovery import (
        OBSERVED_ABSENT,
        JournalState,
        TransactionState,
        classify_recovery,
    )

    with leased() as lease:
        published, _ = prepared_metadata_only(lease)
        other = reapproved_under(
            lease, published.txid, compile_spec(other_file_spec())
        )
        plan = classify_recovery(
            snapshot_for(
                other,
                state=TransactionState.PREPARED,
                journal=JournalState.PENDING,
                live=OBSERVED_ABSENT,
                staged=OBSERVED_ABSENT,
                path="d/g.txt",
            )
        )

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, other, plan, 0)

        assert "spec disagrees with the plan prefix" in str(caught.value)


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

**Both comparisons have their own failing case.** The compiled one is
`test_a_plan_bound_to_another_compiled_spec_refuses`; the topology one is
`test_a_plan_bound_to_another_topology_refuses`, which keeps `approved.compiled` and substitutes
`flattened_topology(approved)`. Design §8.2 requires the topology comparison, and it is not vacuous:
`_validate_topology` constrains coverage and parentage but not *which* intermediate directory nodes a
tree uses, so A4b's resolved arrangement and the flattened one are both valid for the same spec.
Measured 2026-08-02 — `build_recovery_snapshot` and `classify_recovery` accept the flattened form and
return the same `ActionPlan` shape.

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
Expected: PASS, 13 tests.

- [ ] **Step 8: Prove the projection check is complete, field by field**

**The cursor matters here.** The comparison runs in order — transaction state, commit decision,
rollback result, halt diagnostic, journals — so a case that sets the record to `ROLLED_BACK` or
`HALTED` and then calls with `start=0` reports a *transaction-state* mismatch and never reaches the
field it meant to test. Each case uses the cursor at which the prefix already projects that state:

| Field | Plan | `start` | What the prefix projects there | Case |
| --- | --- | --- | --- | --- |
| transaction state | halt | 0 | `PREPARED` | `test_the_first_halt_diagnostic_wins` (exists) |
| commit decision | metadata-only | 0 | `UNCOMMITTED` | `test_a_record_disagreeing_with_the_reduced_prefix_refuses` (exists) |
| spec | metadata-only | 0 | the proof's spec | `test_a_record_whose_spec_disagrees_with_the_prefix_refuses` (exists) |
| rollback result | metadata-only | **2** | `ROLLED_BACK` + `RESTORED`, still active | new, below |
| halt diagnostic | halt | **1** | `HALTED` + the plan's own diagnostic | new, below |
| journals | remove-scratch | 0 | `APPLYING` + `STARTED` | new, below |
| active status | metadata-only | **3** | `DetachActive` applied — a detached transaction | new, below |

`test_no_active_record_refuses` does **not** cover the last row. It exits at the "no active record"
gate, before `_require_projection_matches` is called at all, so deleting the `if not expected.active`
branch leaves it — and every other case here — green.

Read the measured step vectors at the top of this plan to confirm the two shifted cursors: the
metadata-only plan's step 2 is `DetachActive`, so reducing to 2 has applied both transitions but not
the detach; the halt plan has exactly one step, so reducing to 1 has applied it.

The journals case uses the remove-scratch fixture rather than the metadata-only one so that `APPLYING`
+ `STARTED` is the projected baseline and `DONE` is a forward move from it. Measured: no coherence rule
couples a journal state to the transaction state (`records.py:455-500`), so the write commits.

Append to `tests/test_coordinator_transitions.py`:

```python
def test_a_record_whose_rollback_result_disagrees_refuses(leased):
    """Cursor 2. Both transitions have been applied by then, so transaction state and
    commit decision both match and this is the first field that can disagree. At
    `start=0` the transaction-state check would fire instead and this would prove
    nothing."""
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_metadata_only(lease)
        # Two barriers: A5a refuses a ROLLED_BACK row with no rollback_result, so the
        # intermediate state cannot carry one and the terminal one must.
        with lease._store.transaction() as txn:
            txn.set_transaction_state(approved.txid, TransactionState.ROLLING_BACK)
        with lease._store.transaction() as txn:
            txn.set_transaction_state(approved.txid, TransactionState.ROLLED_BACK)
            txn.set_rollback_result(
                approved.txid, RollbackResult.EXTERNAL_DRIFT_PRESERVED
            )

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, plan, 2)

        message = str(caught.value)
        assert "rollback result" in message
        assert "external_drift_preserved" in message.lower()


def test_a_record_whose_halt_diagnostic_disagrees_refuses(leased):
    """Cursor 1. The halt plan's single step has been applied by then, so the record is
    legitimately HALTED and the diagnostic is the only field left to disagree."""
    from atoms.coordinator.transitions import persist_plan_prefix
    from tests.store_support import matching_diagnostic

    with leased() as lease:
        approved, plan = prepared_with_halt(lease)
        other = matching_diagnostic("e1")
        # Without this the case is vacuous: two equal diagnostics disagree about nothing.
        assert other != plan.diagnostic

        with lease._store.transaction() as txn:
            txn.set_transaction_state(approved.txid, TransactionState.HALTED)
            txn.set_halt_diagnostic(approved.txid, other)

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, plan, 1)

        assert "halt diagnostic" in str(caught.value)


def test_a_record_whose_journals_disagree_refuses(leased):
    """Cursor 0 on the remove-scratch plan, whose bound snapshot is APPLYING/STARTED --
    so every scalar field still matches and only the journal vector has moved."""
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_with_remove_scratch(lease)
        with lease._store.transaction() as txn:
            txn.set_journal_state(approved.txid, "e1", JournalState.DONE)

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, plan, 0)

        assert "journals" in str(caught.value)


def test_a_record_still_active_past_its_detach_refuses(leased):
    """Cursor 3: the whole metadata-only plan, `DetachActive` included, so the prefix
    projects a detached transaction. The record is walked to the same terminal state but
    left active, which makes active status the only field that can disagree.

    Nothing else in this module reaches that branch: `test_no_active_record_refuses`
    exits at the earlier "no active record" gate, before the projection check runs.
    """
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_metadata_only(lease)
        assert type(plan.steps[2]) is DetachActive

        with lease._store.transaction() as txn:
            txn.set_transaction_state(approved.txid, TransactionState.ROLLING_BACK)
        with lease._store.transaction() as txn:
            txn.set_transaction_state(approved.txid, TransactionState.ROLLED_BACK)
            txn.set_rollback_result(approved.txid, RollbackResult.RESTORED)
        # set_active is deliberately NOT called: that is the whole disagreement.

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, plan, len(plan.steps))

        assert "projects a detached transaction" in str(caught.value)
```

`start=3` is in range — `test_a_start_outside_the_step_range_refuses` shows the bound is
`len(plan.steps) + 1` — and the walk from there does nothing, so without this branch the call would
return `3` and report success over a record that should already have been detached.

`matching_diagnostic` is `tests/store_support.py:352` and exists precisely so a coherent HALTED record
can be written before something about it is broken. Measured: `coherence_findings` compares a stored
diagnostic's `commit_decision` and `journals` against the durable rows and nothing else
(`records.py:484-492`), so `matching_diagnostic("e1")`'s `paths=("a.txt",)` — which names no path in
this spec — does not block the write. It is that mismatch in every *other* field which makes it a
different diagnostic from the plan's.

Then sweep every comparison in `_require_projection_matches`, deleting one at a time, re-running, and
confirming **only that comparison's own test fails**. A deletion that breaks two tests means two cases
are firing the same gate and one of them is not proving what it claims. Nine deletions:

| Deleted | Expected sole failure |
| --- | --- |
| `snapshot.compiled != approved.compiled` | `test_a_plan_bound_to_another_compiled_spec_refuses` |
| `snapshot.topology != approved.topology` | `test_a_plan_bound_to_another_topology_refuses` |
| the `canonical_json` spec comparison | `test_a_record_whose_spec_disagrees_with_the_prefix_refuses` |
| tuple row: transaction state | `test_the_first_halt_diagnostic_wins` |
| tuple row: commit decision | `test_a_record_disagreeing_with_the_reduced_prefix_refuses` |
| tuple row: rollback result | `test_a_record_whose_rollback_result_disagrees_refuses` |
| tuple row: halt diagnostic | `test_a_record_whose_halt_diagnostic_disagrees_refuses` |
| tuple row: journals | `test_a_record_whose_journals_disagree_refuses` |
| `if not expected.active` | `test_a_record_still_active_past_its_detach_refuses` |

Restore after each and report the nine observed failures.

Run: `uv run pytest tests/test_coordinator_transitions.py -v`. Expected: PASS, 17 tests.

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

The first step's COMMIT survived a failure during the second — which is only possible if each step
opened and closed its own transaction.

**Do not try to mutate this by wrapping the walk in one outer transaction.** A5a's
`_require_no_transaction` refuses a nested `BEGIN` outright (`connection.py:703`), so that edit fails
on the nested-transaction error rather than on this test's assertion, and would prove nothing about
barrier granularity. The assertion above is the proof: a single shared transaction would have rolled
back the first step's write along with the second's.

Run: `uv run pytest tests/test_coordinator_transitions.py -v`. Expected: PASS, 18 tests.

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

- [ ] **Step 1: Replace the two superseded "nothing here yet" guards**

**These two have been red since Task 1 landed, by construction.** Both assert that the coordinator does
not exist; Task 1 makes it exist. They are the plan's only expected interim failures, and this step is
where they are retired:

| Guard | File | Superseded by |
| --- | --- | --- |
| `test_no_production_caller_of_bind_exists_yet` | `tests/test_fs_architecture.py:364` | the two #18 guards below |
| `test_no_production_module_outside_the_package_imports_the_store` | `tests/test_store_architecture.py:1182` | `test_only_the_coordinator_and_the_store_itself_import_the_store` (Step 3) |

Delete the second one outright: Step 3's replacement scans the same population with the coordinator
exemption the DAG now requires, and keeping a second copy of the same scan with a stale exemption set is
how two guards drift apart. Say in the task report that it was removed and by what.

A **third** guard, `test_no_consumer_of_the_approved_spec_exists_yet`, has been red since Task 5 landed
`admission.py` — the first production consumer of `ProjectApprovedSpec`. Step 2 below replaces it. All
three are retired by the end of this task and the suite must be fully green at Step 8.

For the first, replace `test_no_production_caller_of_bind_exists_yet` in
`tests/test_fs_architecture.py`. **Do not delete it** — it is replaced, not removed. The scanner
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


#: The module that DEFINES the proof, exempt by identity. Exempting every file merely
#: *named* `approval.py` would silently excuse a future `atoms/store/approval.py`.
_DEFINING_MODULE = SOURCE_ROOT / "fs" / "approval.py"


def test_only_the_coordinator_consumes_the_approved_spec():
    """Ledger #9's enforcement half, part one: nothing outside the boundary sees it."""
    consumers = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path == _DEFINING_MODULE:
            continue
        if path.relative_to(SOURCE_ROOT).parts[0] == "coordinator":
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


#: What outsiders may not reach: the lease type and the composition root. NOT the whole
#: package -- design §4.1 scopes the restriction to `Lease` and `_recovery_lease`, and
#: A6's and A7's sibling packages will legitimately import `admission`, `prepare`, and
#: `transitions`. Forbidding `atoms.coordinator` wholesale would block those seams.
_PRIVATE_COORDINATOR_MODULES = ("atoms.coordinator.lease", "atoms.coordinator.root")


def _private_coordinator_importers(source_root: Path) -> list[str]:
    """Modules outside the coordinator that import `lease` or `root`.

    A substring scan for `"coordinator.lease"` misses `from atoms.coordinator import
    lease`, whose text never contains that spelling. Resolving each import target
    catches every spelling: that form yields `atoms.coordinator.lease` as a target
    because the imported name is appended to the module it came from.
    """
    offenders = []
    for path in sorted(source_root.rglob("*.py")):
        parts = path.relative_to(source_root).parts
        if parts[0] == "coordinator":
            continue
        package = ".".join(("atoms", *parts[:-1]))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(
            name == private or name.startswith(f"{private}.")
            for name in _resolved_imports(tree, package=package)
            for private in _PRIVATE_COORDINATOR_MODULES
        ):
            offenders.append(str(path.relative_to(source_root)))
    return offenders


def test_no_module_outside_the_coordinator_imports_the_lease_or_the_root():
    """Absence from __all__ is not enforcement -- `atoms.coordinator.root` is still
    importable. The underscore states the contract; this guard covers the population it
    can speak for: in-tree callers."""
    assert _private_coordinator_importers(SOURCE_ROOT) == []


def test_the_private_coordinator_scanner_finds_a_planted_offender(tmp_path):
    """Three spellings the replaced substring scan would have split on, plus the two
    imports that must stay legal: the coordinator importing itself, and a sibling
    package reaching a public seam."""
    root = tmp_path / "atoms"
    for relative, source in (
        ("fs/leak.py", "from atoms.coordinator import lease\n"),
        ("core/leak.py", "from atoms.coordinator.root import _recovery_lease\n"),
        ("store/leak.py", "import atoms.coordinator.lease\n"),
        ("coordinator/root.py", "from atoms.coordinator.lease import Lease\n"),
        ("capture/reader.py", "from atoms.coordinator.prepare import open_workspace\n"),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    assert _private_coordinator_importers(root) == [
        "core/leak.py",
        "fs/leak.py",
        "store/leak.py",
    ]


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

Run as `python -m tests.coordinator_child <project_root> <metadata_root>`; prints one
JSON object describing what a second process sees. A module rather than an inline `-c`
string because it re-runs the whole entry order, including lock acquisition and
reclamation, which is precisely the part a same-process re-entry skips.

Two phases, because the lease traps on a live record and therefore cannot itself report
what that record contains:

1. Enter the production lease with `CERTIFIED_ALLOWLIST` replaced for the test volume.
   Report reclamation's outcome, or the trap if one fired.
2. Release it, then bind and open the store directly to report the durable state. This
   phase makes no A5b claim -- it is the observer for the publication cut, and it is
   the same plain bind/open `tests/store_child.py` already uses.
"""

from __future__ import annotations

import json
import os
import sys

from atoms.coordinator import root
from atoms.fs.binding import bind_project_volume
from atoms.fs.linux import LinuxBackend
from atoms.fs.lock import acquire_project_lock
from atoms.fs.volume import StorageProfile
from atoms.store.connection import open_store
from atoms.store.records import referenced_digests
from tests.coordinator_support import spec_digest
from tests.fs_support import build_test_allowlist

STORAGE = StorageProfile(profile_id="atoms-test-profile")


def _lease_phase(backend, project_root: str, metadata_root: str) -> dict:
    try:
        with root._recovery_lease(
            backend, project_root, metadata_root, STORAGE
        ) as lease:
            active = lease._store.read_active()
            return {
                "trapped": None,
                "workspaces": list(lease._store.list_workspaces()),
                "unindexed_blobs": list(lease._store.list_unindexed_blobs()),
                "active": None if active is None else active.txid,
            }
    except NotImplementedError as caught:
        return {"trapped": str(caught)}


def _durable_phase(backend, project_root: str, metadata_root: str) -> dict:
    with acquire_project_lock(backend, metadata_root) as lock:
        allowlist = build_test_allowlist(lock, project_root, STORAGE)
        with bind_project_volume(
            project_root, lock, allowlist=allowlist, storage=STORAGE
        ) as binding, open_store(binding) as store:
            active = store.read_active()
            if active is None:
                return {"active": None, "state": None, "spec": None, "blobs": {}}
            blobs = {}
            for digest, byte_len in referenced_digests(active.spec):
                fd = store.open_blob(digest)
                try:
                    blobs[digest] = len(os.read(fd, byte_len + 1))
                finally:
                    os.close(fd)
            return {
                "active": active.txid,
                "state": active.state.value,
                # The blob list above is derived from whatever spec is on disk, so it
                # cannot tell a right spec from a wrong one. This digest can.
                "spec": spec_digest(active.spec),
                "blobs": blobs,
            }


def main(project_root: str, metadata_root: str) -> int:
    backend = LinuxBackend()
    with acquire_project_lock(backend, metadata_root) as probe:
        root.CERTIFIED_ALLOWLIST = build_test_allowlist(probe, project_root, STORAGE)
    # No `contextlib.suppress` here. `_durable_phase` is a plain bind-and-open with no
    # A7 trap in it; a `NotImplementedError` escaping it would be an unexplained
    # failure, and swallowing one would hand the parent a payload with no "durable" key
    # and no reason why.
    print(
        json.dumps(
            {
                "lease": _lease_phase(backend, project_root, metadata_root),
                "durable": _durable_phase(backend, project_root, metadata_root),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
```

Phase 2 running at all is itself evidence for Task 3's release property from the far side: a lease that
had not released its `flock` would make `acquire_project_lock` here block until the timeout.

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


def test_a_second_lease_reclaims_both_kinds_of_orphan_and_spares_the_referenced(leased):
    """Ledger #23's crash-cut claim.

    The unindexed blob is produced the only way one can be: `promote_staging` renames
    each blob into `blobs/` and flushes it BEFORE its `INSERT_BLOB` runs, so a
    transaction that rolls back leaves the file on disk with no row. A test that merely
    asserted the child sees none would pass against a store that never had one.
    """
    from atoms.store.blobs import StagedBlob
    from tests.store_support import digest_of, spec_referencing, stage

    content = b"orphaned by a cut before COMMIT"
    digest = digest_of(content)

    with leased() as lease:
        project_root, metadata_root = _roots(lease)
        lease._store.create_workspace("kept").close()
        with lease._store.transaction() as txn:
            txn.insert_record("kept", one_effect_spec())

        with lease._store.create_workspace("orphan") as workspace:
            stage(workspace, "b0", content)
            with pytest.raises(RuntimeError), lease._store.transaction() as txn:
                txn.promote_staging(
                    workspace,
                    (StagedBlob(name="b0", digest=digest, byte_len=len(content)),),
                )
                txn.insert_record("orphan", spec_referencing(content))
                raise RuntimeError("cut before COMMIT")

        # Both orphans exist in THIS process, before any restart.
        assert lease._store.read_record("orphan") is None
        assert lease._store.list_unindexed_blobs() == (digest,)
        assert set(lease._store.list_workspaces()) == {"kept", "orphan"}

    seen = _second_process(project_root, metadata_root)["lease"]

    assert seen["trapped"] is None
    assert seen["workspaces"] == ["kept"]
    assert seen["unindexed_blobs"] == []
    assert seen["active"] is None


def test_a_published_record_traps_a_fresh_lease_and_survives_intact(leased):
    """Ledger #17's enforcement half survives a restart: the trap is a property of lease
    entry, not of the process that published the record. The second phase confirms the
    record and its blobs are readable afterwards -- a trap that had damaged them would
    be worse than no trap.

    The spec digest is the load-bearing half of that. The child's blob list is derived
    from whichever spec it finds on disk, so it agrees with itself no matter which spec
    that is; only comparing the durable spec against the proof's own can catch a wrong
    one."""
    with leased() as lease:
        project_root, metadata_root = _roots(lease)
        approved = prepared(lease)

    seen = _second_process(project_root, metadata_root)

    assert seen["lease"]["trapped"] == "recovery execution is not implemented until A7"
    assert seen["durable"]["active"] == approved.txid
    assert seen["durable"]["state"] == "prepared"
    assert seen["durable"]["spec"] == spec_digest(approved.compiled.spec)
    assert list(seen["durable"]["blobs"].values()) == [len(AFTER)]


def test_a_cut_inside_preparation_publishes_nothing_across_a_restart(
    leased, monkeypatch
):
    """Design §10's before-state for publication, proved through the production
    function rather than a re-implementation of its body."""
    from atoms.store.connection import _StoreTransaction

    from atoms.coordinator.prepare import open_workspace, prepare_transaction
    from tests.coordinator_support import admission_for, stage_manifest

    with leased() as lease:
        project_root, metadata_root = _roots(lease)
        approved = admission_for(lease)
        workspace = open_workspace(lease, approved)
        manifest = stage_manifest(workspace)

        def cut(self, txid):
            raise RuntimeError("cut inside prepare_transaction, before COMMIT")

        monkeypatch.setattr(_StoreTransaction, "set_active", cut)
        with pytest.raises(RuntimeError):
            prepare_transaction(lease, approved, workspace, manifest)
        workspace.close()

    seen = _second_process(project_root, metadata_root)

    assert seen["lease"]["trapped"] is None
    assert seen["lease"]["active"] is None
    assert seen["durable"]["active"] is None
    assert seen["durable"]["spec"] is None
    # Reclamation also drained the blob the cut orphaned in blobs/.
    assert seen["lease"]["unindexed_blobs"] == []
```

Add `import pytest` and `from tests.coordinator_support import AFTER, prepared, spec_digest` to the
module's imports.

Run: `uv run pytest tests/test_coordinator_process.py -v`. Expected: PASS, 3 tests.

**Then prove the spec comparison is load-bearing.** In `prepare.py`, change `txn.insert_record(...)` to
publish `other_file_spec()` instead of `approved.compiled.spec`, re-run
`test_a_published_record_traps_a_fresh_lease_and_survives_intact`, and confirm it fails on the `spec`
assertion — *not* on `blobs`, which will have silently followed the wrong spec. That contrast is the
reason the digest is there. Restore and report both the failure and which assertions stayed green.

**If the trap test fails because the child never reaches it**, the cause is that `_reclaim_orphans`
removed the prepared transaction's workspace before `_resolve` ran — check that `prepared` published a
record naming that txid, since reclamation spares exactly the referenced ones. Report either way.

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
| 23 | `tests/test_coordinator_lease.py`, `tests/test_coordinator_process.py::test_a_second_lease_reclaims_both_kinds_of_orphan_and_spares_the_referenced` |

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

**One addition to the design, approved.** Design §6.4's table said the `WorkRoot` branch "never enters
`observe_child`" and must "re-resolve `metadata_root/work` against `approved.work_base`", but named no
mechanism. This plan adds `observe_work_child(binding, leaf)` beside `observe_child` rather than putting
metadata-space syscalls in the coordinator — separate project-space and metadata-space entry points over
one shared observation core. The design was amended in the same commit as this plan, per the
authority-order rule.

**Type consistency.** `Lease._binding` / `Lease._store` are spelled identically in every task.
`_require_admitted` is defined once in `admission.py` and called by all three entry points.
`_require_work_slot_free` is defined in Task 6 and consumed by Task 7's `open_workspace`.
`prepare_transaction(lease, approved, workspace, manifest)` and
`persist_plan_prefix(lease, approved, plan, start)` match design §6.5 and §8.1 exactly. `_persist_one`
takes `(lease, txid, step)` — no `plan`, because every payload it writes is on the step.

**One deferred proof, tracked across two tasks.** Task 5's `occupied = ()` reset cannot be proved
load-bearing while `_occupied_scratch` is stubbed. Task 5 Step 6 records that rather than claiming it,
and Task 6 Step 8 arms and runs the mutation.

**Every test drives production code, not a re-implementation of it.** Two cases were rewritten for
this: the publication cut now patches `_StoreTransaction.set_active` and calls `prepare_transaction`,
so a wrong ordering inside the production function fails it; and both unindexed-blob cases produce a
real orphan through a rolled-back `promote_staging` rather than asserting that an empty list is empty.

**Nothing an observer derives from the thing under test is used to check it.** The fresh-process
durable phase reports a `spec_digest` alongside its blob list, because the blob list is computed from
whichever spec is on disk and therefore agrees with a wrong one; the digest is compared against
`approved.compiled.spec`. Likewise the trap's project-state comparison hashes content and records mode
and kind, since path names alone survive an overwrite, a `chmod`, or a truncation. And
`coordinator_child.py` no longer suppresses `NotImplementedError` around its durable phase: that phase
contains no A7 trap, so one escaping it is an unexplained failure the child must surface.

**Design §10's mismatch matrix has one independent case per comparand.** Identity, `NAME_MAX`,
`LookupProof`, and mount membership each have their own case, and both declared resolver refusal types
— `ProjectApprovalRefused` and `CapabilityUnavailable` — are translated separately, with a fifth case
asserting `approve_for_project`'s own refusals are **not** translated. The projection matrix reduces to
the cursor at which the prefix already projects each field's state (`start=2` for rollback result,
`start=1` for halt diagnostic), so no case is silently absorbed by the transaction-state check that
runs before it.

**Every guard has a test that can fail it, and every mutation preserves the property it is not
testing.** The topology comparison design §8.2 requires now has `test_a_plan_bound_to_another_topology_refuses`,
built on a measured fact: `_validate_topology` constrains coverage and parentage but not which
intermediate directory nodes a tree uses, so the flattened arrangement is valid for the same compiled
spec. The reclamation guard uses an absent project root rather than the empty allowlist, because
`bind_project_volume` reclaims at `binding.py:192` *before* the allowlist match at `:194` and so cleans
`probe/` by itself — measured, and run as a negative control. The trap's five mutations are each
inserted before `_resolve(store)` so the trap still fires and only the asserted property moves,
producing six expected failures across the three trap tests; deleting the trap instead would fail all
three at `pytest.raises` and prove nothing.

**One place the implementer must read the code rather than trust this plan:** Task 4 Step 3's lift of
`_NAMESPACE_CONTRADICTIONS` out of `PathResolver`, which touches existing code and must leave
`tests/test_fs_resolve_*.py` green before anything new is written. Its one class use is at
`resolve.py:207`.

**Everything the plan asserts about A4b's and A3's output shapes was executed, not inferred.** The
approval shapes, the scratch leaf format, the `work_base is None` case, and all five plan step vectors
in the Measured Facts table came from probe runs against a real ext4 volume on 2026-08-02. Where a
measured fact contradicts a task's code, the fact wins and the implementer reports it.
