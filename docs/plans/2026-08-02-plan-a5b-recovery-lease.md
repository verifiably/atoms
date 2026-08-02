# A5b Recovery-Resolve Lease Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `atoms/coordinator/`, the transaction admission boundary — the recovery-resolve lease
over A4a's lock, orphan reclamation at every entry, the `ProjectApprovedSpec`-only gate with its
bounded txid regeneration loop, preparation to the durable publication COMMIT, and A3 transition
persistence that stops structurally at the first step A7 must execute.

**Architecture:** Five modules under a new `atoms/coordinator/` package. `lease.py` owns the `Lease`
value and the reclaim/resolve protocol. `root.py` is a thin composition root: the sole production call
site passing `CERTIFIED_ALLOWLIST`. `admission.py` owns txid generation, the regeneration loop,
re-resolution across three parent branches, and the entry-point gate set. `prepare.py` owns authority
§7.3 steps 2–4. `transitions.py` owns plan-order persistence and the §7.4 barriers. One function is
added to `atoms/fs/resolve.py`, `observe_child`, because scratch leaves cannot go through
`PathResolver`.

**Tech Stack:** Python 3.11+, stdlib only (`secrets`, `contextlib`, `dataclasses`), `pytest`, `ruff`,
`pyright`. Builds on A4a's `acquire_project_lock`, `reclaim_probe_survivors`, `bind_project_volume`,
and `CERTIFIED_ALLOWLIST`; A4b's `approve_for_project` and `ProjectApprovedSpec`; A5a's `Store`; A3's
`classify_recovery` and `reduce_recovery_plan_prefix`.

**Design:** [`2026-08-02-a5b-recovery-lease-design.md`](2026-08-02-a5b-recovery-lease-design.md).
**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md).
Where this plan and either document disagree, the design wins over this plan and the authority wins
over both.

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
  volume until A8 crash-certifies a tuple. `lease.py` takes `allowlist` as a required keyword-only
  parameter so tests can drive the protocol with `build_test_allowlist`; `root.py` passes
  `CERTIFIED_ALLOWLIST` and nothing else. **#18 is therefore an architecture assertion over `root.py`,
  not a runtime path**, exactly as A4a's `test_no_production_caller_of_bind_exists_yet` was.
- **`_recovery_lease` and `Lease` are package-private, and `atoms.coordinator.__all__` is `()`.** A5b
  ships no consumer-facing command — those are authority §12.1's — and both entry points accept a
  `Lease`, so nothing is publicly exported yet (design §5.0).
- **No new exception type and no `errors.py`** (design §4.1, §9). Reuse `PreconditionRefused`,
  `ProtocolError`, `CapabilityUnavailable`, `TransactionHalted`. The A7 trap is a bare
  `NotImplementedError`.
- **The dependency DAG is `coordinator → {store, fs, core}`, `store → {fs, core}`, `fs → core`.**
  `atoms.store` is importable only from `atoms/coordinator/`; Task 9 asserts it.
- **Every reference to the lease's resources is `lease._binding` and `lease._store`.** There is no
  public `binding` or `store` property.
- **pyright type-checks the tests** — `[tool.pyright]` sets no `include`.
- **Every fixture lands in `tests/conftest.py`.** Task 9 extends the fixture-registry guard to
  `test_coordinator_*.py`.
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
| `src/atoms/coordinator/lease.py` | **Create.** `Lease`, `_lease(..., *, allowlist)`, orphan reclamation (§5.2), resolution and the A7 trap (§5.3). |
| `src/atoms/coordinator/root.py` | **Create.** `_recovery_lease(...)` — the sole production `CERTIFIED_ALLOWLIST` call site (#18). |
| `src/atoms/coordinator/admission.py` | **Create.** `new_txid`, `SCRATCH_ATTEMPTS`, `admit`, the three re-resolution branches, resolver translation, and the gate set (§6). |
| `src/atoms/coordinator/prepare.py` | **Create.** `prepare_transaction` — authority §7.3 steps 2–4 (§7). |
| `src/atoms/coordinator/transitions.py` | **Create.** `persist_plan_prefix` — plan-order persistence and the §7.4 barriers (§8). |
| `src/atoms/fs/resolve.py` | **Modify.** Add `ChildObservation` and `observe_child` (§6.4). |
| `tests/coordinator_support.py` | **Create.** Compiled-spec, approval, and plan builders shared by the coordinator tiers. |
| `tests/conftest.py` | **Modify.** `coordinator_on`, `leased`. |
| `tests/test_coordinator_lease.py` | **Create.** Tier 1 — entry order, reclamation, the trap, lock duration. |
| `tests/test_coordinator_admission.py` | **Create.** Tier 2 — txid generation, the loop, the three branches, translation, gates. |
| `tests/test_coordinator_prepare.py` | **Create.** Tier 3 — work-base re-resolution, the publication COMMIT. |
| `tests/test_coordinator_transitions.py` | **Create.** Tier 4 — prefix validation, barrier discipline, the cursor. |
| `tests/test_coordinator_process.py` | **Create.** Tier 5 — fresh-process crash cuts for #23 and publication. |
| `tests/coordinator_child.py` | **Create.** The subprocess tier 5 reads from. |
| `tests/test_coordinator_architecture.py` | **Create.** Tier 6 — import direction, surface, guards. |
| `tests/test_fs_architecture.py` | **Modify.** Narrow the two temporary guards (#9, #18). |
| `tests/test_fs_resolve_walk.py` | **Modify.** `observe_child` cases. |
| `pyproject.toml` | **Modify.** `import-names` gains `atoms.coordinator`. |
| `docs/deferred-obligation-ledger.md` | **Modify.** Discharge #7, #18, #21, #23; relabel #12, #17, #19. |
| `AGENTS.md` | **Modify.** A5 status line. |

**Why `root.py` is separate from `lease.py`.** `CERTIFIED_ALLOWLIST` is empty, so anything importing it
into the protocol would make the protocol untestable. `lease.py` takes the allowlist; `root.py` is the
one module that names the production constant, which is what makes #18's assertion a single, provable
call site.

Nine tasks. Each ends with a deliverable a reviewer could reject while approving its neighbour.

**Tasks 1–3 build the lease** bottom-up: the value and the resource stack, then reclamation, then
resolution and the trap. **Task 4 adds `observe_child`** to `atoms/fs/`, the one change outside the new
package. **Tasks 5–6 build admission.** **Tasks 7–8 build preparation and persistence.** **Task 9** is
the whole-package guard set, the packaging metadata, the ledger, and the status synchronization.

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
  `_lease(backend, project_root, metadata_root, storage, *, allowlist)` as a context manager yielding
  `Lease`; `_recovery_lease(backend, project_root, metadata_root, storage)` in `root.py`.

- [ ] **Step 1: Add the fixtures**

In `tests/conftest.py`, after the existing `store_on` fixture:

```python
@pytest.fixture
def coordinator_on(ext4_volume, ext4_project_root, test_storage_profile):
    """The raw ingredients `_lease` builds its own stack from.

    Unlike `store_on`, this fixture binds nothing: the lease owns lock acquisition,
    probe reclamation, binding, and store opening, and a fixture that pre-bound them
    would leave three of the five entry-order steps unexercised.
    """
    counter = itertools.count()

    def ingredients():
        from atoms.fs.backend import LinuxBackend

        metadata_root = ext4_volume / f"coordinator-metadata-{next(counter)}"
        return (
            LinuxBackend(),
            str(ext4_project_root),
            str(metadata_root),
            test_storage_profile,
        )

    return ingredients


@pytest.fixture
def leased(coordinator_on):
    """An entered lease over a fresh metadata root, with a test allowlist.

    `CERTIFIED_ALLOWLIST` is empty and production binding fails closed, so every test
    drives the protocol through `_lease`'s allowlist parameter. `root.py`'s production
    composition is asserted architecturally instead (ledger #18).
    """
    import contextlib

    from tests.fs_support import build_test_allowlist

    @contextlib.contextmanager
    def enter():
        from atoms.coordinator.lease import _lease

        backend, project_root, metadata_root, storage = coordinator_on()
        from atoms.fs.lock import acquire_project_lock

        with acquire_project_lock(backend, metadata_root) as probe_lock:
            allowlist = build_test_allowlist(probe_lock, project_root, storage)
        with _lease(
            backend, project_root, metadata_root, storage, allowlist=allowlist
        ) as lease:
            yield lease

    return enter
```

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


def test_the_lease_spends_the_store_and_the_binding_on_exit(leased):
    with leased() as lease:
        escaped = lease
    with pytest.raises(ProtocolError):
        escaped._store.read_active()


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

Nothing is exported. A5b ships no consumer-facing command: both entry points accept a
package-private `Lease`, and authority §12.1 owns the first real consumer. `__all__`
stays empty until one exists.
"""

from __future__ import annotations

__all__ = ()
```

- [ ] **Step 5: Write `lease.py`**

```python
"""The recovery-resolve lease (design §5)."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass

from atoms.fs.binding import ProjectBinding, bind_project_volume
from atoms.fs.bootstrap import reclaim_probe_survivors
from atoms.fs.lock import acquire_project_lock
from atoms.fs.volume import DurabilityAllowlist, StorageProfile
from atoms.store.connection import Store, open_store


@dataclass(frozen=True, slots=True)
class Lease:
    """Borrowed resources, not owned ones.

    `_lease` closes both in reverse acquisition order; a `Lease` that outlives its
    `with` block therefore references spent objects, and every A5a call through it
    refuses. The escape is caught by the layer below rather than by a flag here.
    """

    _binding: ProjectBinding
    _store: Store


@contextlib.contextmanager
def _lease(
    backend: object,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
    *,
    allowlist: DurabilityAllowlist,
) -> Iterator[Lease]:
    """Design §5.1's entry order, exactly.

    `allowlist` is a required keyword parameter rather than `CERTIFIED_ALLOWLIST`
    because that constant ships empty and production binding fails closed; `root.py`
    supplies it in production and tests supply their own. Ledger #18 is asserted over
    `root.py` architecturally for the same reason A4a asserted it that way.
    """
    with acquire_project_lock(backend, metadata_root) as lock:
        # Ledger #17 requires reclamation at EVERY lease entry. bind_project_volume
        # reclaims at its own step 4, which it reaches only after checks that can
        # refuse first (binding.py:169-172), so the lease calls it directly.
        reclaim_probe_survivors(lock)
        with bind_project_volume(
            project_root, lock, allowlist=allowlist, storage=storage
        ) as binding:
            with open_store(binding) as store:
                yield Lease(_binding=binding, _store=store)
```

- [ ] **Step 6: Write `root.py`**

```python
"""The production composition root (design §4.1, ledger #18)."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from atoms.coordinator.lease import Lease, _lease
from atoms.fs.volume import CERTIFIED_ALLOWLIST, StorageProfile


@contextlib.contextmanager
def _recovery_lease(
    backend: object,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
) -> Iterator[Lease]:
    """The one production call site that names `CERTIFIED_ALLOWLIST`.

    It is empty until A8 crash-certifies a configuration tuple, so this path refuses
    every volume today. That is the intended fail-closed behaviour, and it is why
    ledger #18 is proved by an architecture assertion over this module rather than by
    running it.
    """
    with _lease(
        backend,
        project_root,
        metadata_root,
        storage,
        allowlist=CERTIFIED_ALLOWLIST,
    ) as lease:
        yield lease
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_coordinator_lease.py -v`
Expected: PASS, 3 tests.

If `open_store` is not a context manager, wrap it instead:

```python
            store = open_store(binding)
            try:
                yield Lease(_binding=binding, _store=store)
            finally:
                store.close()
```

Check `connection.py:825` first — `Store.__enter__` exists, so the `with` form above is correct.

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
- Modify: `src/atoms/coordinator/lease.py`
- Modify: `tests/test_coordinator_lease.py`
- Create: `tests/coordinator_support.py`

**Interfaces:**
- Consumes: A5a `Store.list_workspaces() -> tuple[str, ...]`,
  `Store.reopen_workspace(txid) -> Workspace`, `Store.remove_workspace(workspace) -> None`,
  `Store.list_unindexed_blobs() -> tuple[str, ...]`, `Store.remove_unindexed_blob(digest) -> None`,
  `Store.read_record(txid) -> StoredRecord | None`.
- Produces: `_reclaim_orphans(store) -> tuple[tuple[str, ...], tuple[str, ...]]`, returning the
  removed workspace txids and blob digests, called from `_lease` before resolution.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_coordinator_lease.py`:

```python
def test_a_workspace_no_record_names_is_reclaimed(leased):
    with leased() as lease:
        lease._store.create_workspace("orphan1").close()
        assert "orphan1" in lease._store.list_workspaces()
    # A second lease over the same metadata root is what reclaims it, so the
    # assertion lives in the fresh-process tier (Task 9). Here, prove the helper.


def test_reclamation_removes_orphans_and_spares_referenced_scratch(leased, one_effect_spec):
    from atoms.coordinator.lease import _reclaim_orphans

    with leased() as lease:
        lease._store.create_workspace("orphan1").close()
        referenced = lease._store.create_workspace("kept1")
        referenced.close()
        with lease._store.transaction() as txn:
            txn.insert_record("kept1", one_effect_spec())

        workspaces, blobs = _reclaim_orphans(lease._store)

        assert workspaces == ("orphan1",)
        assert blobs == ()
        assert lease._store.list_workspaces() == ("kept1",)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_coordinator_lease.py -k reclamation -v`
Expected: FAIL — `ImportError: cannot import name '_reclaim_orphans'`

- [ ] **Step 3: Implement `_reclaim_orphans`**

Add to `lease.py`, above `_lease`:

```python
def _reclaim_orphans(store: Store) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Ledger #23: reclaim authority §7.3's pre-COMMIT leaves under the held lock.

    A workspace is orphan exactly when no `transaction_record` row names its txid, and
    `list_unindexed_blobs` already means "no `blob` row" -- so nothing a durable record
    references is ever at risk. Returns what was removed, so a caller can assert it.
    """
    removed_workspaces = []
    for txid in store.list_workspaces():
        if store.read_record(txid) is not None:
            continue
        store.remove_workspace(store.reopen_workspace(txid))
        removed_workspaces.append(txid)

    removed_blobs = []
    for digest in store.list_unindexed_blobs():
        store.remove_unindexed_blob(digest)
        removed_blobs.append(digest)

    return tuple(removed_workspaces), tuple(removed_blobs)
```

- [ ] **Step 4: Call it from `_lease`**

Replace the `open_store` block's body:

```python
            with open_store(binding) as store:
                # Reclamation precedes resolution: orphans are unreferenced by
                # definition, and #23 says "at every lease entry", which holds only if
                # it runs even when resolution then refuses, halts, or traps.
                _reclaim_orphans(store)
                yield Lease(_binding=binding, _store=store)
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_coordinator_lease.py -v`
Expected: PASS.

- [ ] **Step 6: Prove the spare is load-bearing**

Temporarily delete the `if store.read_record(txid) is not None: continue` guard, re-run, and confirm
`test_reclamation_removes_orphans_and_spares_referenced_scratch` fails on
`workspaces == ("orphan1",)`. Restore, confirm `git diff` is empty, and re-run.

- [ ] **Step 7: Commit**

```bash
git add src/atoms/coordinator/lease.py tests/test_coordinator_lease.py tests/coordinator_support.py
git commit -m "feat(coordinator): reclaim orphan scratch at every lease entry"
```

---

## Task 3: Resolution and the A7 trap

**Files:**
- Modify: `src/atoms/coordinator/lease.py`
- Modify: `tests/test_coordinator_lease.py`

**Interfaces:**
- Consumes: A5a `Store.read_active() -> StoredRecord | None`.
- Produces: `_resolve(store) -> None`, raising `NotImplementedError` when a record is live; called
  from `_lease` after `_reclaim_orphans`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_live_record_traps_at_the_next_lease_entry(leased, one_effect_spec):
    from atoms.coordinator.lease import _resolve

    with leased() as lease:
        with lease._store.transaction() as txn:
            txn.insert_record("tx1", one_effect_spec())
            txn.set_active("tx1")

        with pytest.raises(NotImplementedError) as caught:
            _resolve(lease._store)

        assert "recovery execution is not implemented until A7" in str(caught.value)


def test_the_trap_leaves_the_record_and_the_active_row_unchanged(leased, one_effect_spec):
    from atoms.coordinator.lease import _resolve

    with leased() as lease:
        spec = one_effect_spec()
        with lease._store.transaction() as txn:
            txn.insert_record("tx1", spec)
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
    """
    if store.read_active() is not None:
        raise NotImplementedError("recovery execution is not implemented until A7")
```

- [ ] **Step 4: Call it from `_lease`**

```python
                _reclaim_orphans(store)
                _resolve(store)
                yield Lease(_binding=binding, _store=store)
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_coordinator_lease.py -v`
Expected: PASS.

- [ ] **Step 6: Write the lock-duration test**

```python
def test_a_contender_cannot_enter_while_the_lease_is_held(coordinator_on):
    """Authority §7.1: the lock spans the whole write phase, not merely entry."""
    import contextlib

    from atoms.coordinator.lease import _lease
    from atoms.fs.lock import acquire_project_lock
    from tests.fs_support import build_test_allowlist

    backend, project_root, metadata_root, storage = coordinator_on()
    with acquire_project_lock(backend, metadata_root) as probe:
        allowlist = build_test_allowlist(probe, project_root, storage)

    with _lease(backend, project_root, metadata_root, storage, allowlist=allowlist):
        with pytest.raises(BlockingIOError):
            with acquire_project_lock(backend, metadata_root):
                pass

    # After release, the same acquisition succeeds.
    with acquire_project_lock(backend, metadata_root):
        pass
```

If `acquire_project_lock` raises something other than `BlockingIOError` on contention, read
`lock.py:206` and use the type it actually raises; do not weaken the assertion to bare `Exception`.

- [ ] **Step 7: Run the gates and commit**

```bash
uv run ruff check && uv run pyright && uv run pytest -q
git add src/atoms/coordinator/lease.py tests/test_coordinator_lease.py
git commit -m "feat(coordinator): trap on a live record until A7 lands"
```

---

## Task 4: `observe_child`

**Files:**
- Modify: `src/atoms/fs/resolve.py`
- Modify: `tests/test_fs_resolve_walk.py`

**Interfaces:**
- Consumes: `FilesystemIdentity` (`resolve.py:33`), `DirectoryConstraints` and
  `read_lookup_constraints` (`lookup.py:45`), both already imported by `resolve.py`.
- Produces: `ChildObservation(parent_identity, parent_constraints, present)` and
  `observe_child(binding, parent_path, leaf) -> ChildObservation`.

**Why this lives in `atoms/fs/` and not the coordinator.** `PathResolver.resolve()` cannot resolve a
scratch path: `validate_path` rejects any component for which `aliases_scratch_sigil` holds
(`atoms/core/paths.py:40`). And `ApprovedScratch` carries `(effect_id, role, parent_node, leaf)` — a
parent node and a leaf, never a resolvable path. Duplicating traversal inside the coordinator would put
syscalls in a package whose job is judgment.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fs_resolve_walk.py`:

```python
def test_observe_child_reports_absence_with_the_parent_facts(ext4_bound_volume):
    from atoms.fs.resolve import observe_child

    with ext4_bound_volume() as binding:
        observed = observe_child(binding, "", "nothing-here")

        assert observed.present is False
        assert observed.parent_identity.device > 0
        assert observed.parent_constraints.name_max > 0


def test_observe_child_reports_presence_of_an_existing_leaf(ext4_bound_volume):
    import os

    from atoms.fs.resolve import observe_child

    with ext4_bound_volume() as binding:
        root_fd = binding.backend.open_root(binding.project_root_path)
        try:
            os.close(os.open("occupied", os.O_CREAT | os.O_WRONLY, 0o644, dir_fd=root_fd))
        finally:
            os.close(root_fd)

        assert observe_child(binding, "", "occupied").present is True


def test_observe_child_refuses_a_leaf_with_a_separator(ext4_bound_volume):
    from atoms.core.errors import ProtocolError
    from atoms.fs.resolve import observe_child

    with ext4_bound_volume() as binding:
        with pytest.raises(ProtocolError) as caught:
            observe_child(binding, "", "a/b")

        assert "leaf" in str(caught.value)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_fs_resolve_walk.py -k observe_child -v`
Expected: FAIL — `ImportError: cannot import name 'observe_child'`

- [ ] **Step 3: Implement it**

Add to `resolve.py`, beside `FilesystemIdentity`:

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
```

And the observer:

```python
def observe_child(
    binding: ProjectBinding, parent_path: str, leaf: str
) -> ChildObservation:
    """Observe one named child of one project-relative parent.

    `parent_path` is project-relative throughout; the project root is `""`. The leaf is
    not a path and is never split: a scratch leaf aliases the reserved sigil and would
    be refused by `validate_path`, which is the whole reason this function exists.
    """
    if type(leaf) is not str or not leaf or "/" in leaf or leaf in (".", ".."):
        raise ProtocolError(f"leaf {leaf!r} must be a single non-dot path component")

    parent_fd = _open_project_relative(binding, parent_path)
    try:
        info = os.fstat(parent_fd)
        identity = FilesystemIdentity(device=info.st_dev, inode=info.st_ino)
        constraints = read_lookup_constraints(parent_fd)
        try:
            os.lstat(leaf, dir_fd=parent_fd)
        except FileNotFoundError:
            present = False
        else:
            present = True
    finally:
        os.close(parent_fd)

    return ChildObservation(
        parent_identity=identity, parent_constraints=constraints, present=present
    )
```

`_open_project_relative` opens `binding`'s project root and walks `parent_path` with the module's
existing anchored-traversal helper. **Read the resolver's own walk before writing it and reuse that
helper rather than adding a second traversal**; if the existing walk is not callable in isolation,
factor the minimal piece out rather than duplicating it, and say so in the task report.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_fs_resolve_walk.py -k observe_child -v`
Expected: PASS, 3 tests.

- [ ] **Step 5: Run the gates and commit**

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
  `ProjectContext(binding, txid)`; A5a `Store.read_record(txid)`.
- Produces: `new_txid() -> str`, `SCRATCH_ATTEMPTS = 3`,
  `admit(lease, compiled) -> ProjectApprovedSpec`, and `_occupied_scratch(lease, approved)`, stubbed to
  `()` here and filled by Task 6.

- [ ] **Step 1: Write the failing tests**

```python
"""A5b tier 2 -- admission: generation, the loop, re-resolution, and the gates."""

from __future__ import annotations

import pytest

from atoms.core.errors import PreconditionRefused
from atoms.core.identifiers import is_valid_identifier
from tests.coordinator_support import compiled_for


def test_new_txid_is_a_valid_identifier():
    from atoms.coordinator.admission import new_txid

    for _ in range(64):
        assert is_valid_identifier(new_txid())


def test_new_txid_does_not_repeat():
    from atoms.coordinator.admission import new_txid

    assert len({new_txid() for _ in range(512)}) == 512


def test_a_candidate_a_durable_record_owns_is_discarded(leased, monkeypatch, one_effect_spec):
    from atoms.coordinator import admission

    with leased() as lease:
        with lease._store.transaction() as txn:
            txn.insert_record("taken", one_effect_spec())

        issued = iter(["taken", "free"])
        monkeypatch.setattr(admission, "new_txid", lambda: next(issued))

        approved = admission.admit(lease, compiled_for(lease))

        assert approved.txid == "free"


def test_every_candidate_owned_by_a_record_exhausts_the_bound(
    leased, monkeypatch, one_effect_spec
):
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
```

`compiled_for(lease)` goes in `tests/coordinator_support.py`: it returns a `CompiledSpec` from
`compile_spec` over a spec whose declared paths sit inside the fixture project root — reuse the store
suite's `one_effect_spec()` shape rather than inventing a second one.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_coordinator_admission.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atoms.coordinator.admission'`

- [ ] **Step 3: Implement `admission.py`**

```python
"""The transaction admission gate (design §6)."""

from __future__ import annotations

import secrets

from atoms.core.compiler import CompiledSpec
from atoms.core.errors import PreconditionRefused
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
    return ()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_coordinator_admission.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Note the deferred mutation check**

Deleting `occupied = ()` cannot fail any test while `_occupied_scratch` is stubbed to `()`. Record that
in the task report; **Task 6 Step 7 arms and runs it.** Do not claim the reset is proved here.

- [ ] **Step 6: Commit**

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
- Consumes: `observe_child`, `ChildObservation` (Task 4); A4b `ApprovedExistingDirectory`,
  `ApprovedPlannedDirectory`, `ApprovedPath`, `ApprovedScratch`, `ApprovedWorkBase`; A3 `ProjectRoot`,
  `PersistentNode`, `ScratchRole`.
- Produces: a filled `_occupied_scratch`, `_parent_paths(approved)`, `_parent_path(mapping, node)`,
  `_require_work_slot_free(lease, approved)`, and `_translated_resolution()`.

- [ ] **Step 1: Write the failing tests**

```python
def test_an_occupied_scratch_leaf_regenerates_then_succeeds(leased, monkeypatch):
    from atoms.coordinator import admission

    with leased() as lease:
        issued = iter(["first", "second"])
        monkeypatch.setattr(admission, "new_txid", lambda: next(issued))
        seen = []
        real = admission._occupied_scratch

        def occupied_once(lease_, approved):
            seen.append(approved.txid)
            return ("a.txt.#~first.staging",) if len(seen) == 1 else real(lease_, approved)

        monkeypatch.setattr(admission, "_occupied_scratch", occupied_once)

        approved = admission.admit(lease, compiled_for(lease))

        assert approved.txid == "second"
        assert seen == ["first", "second"]


def test_persistent_occupancy_exhausts_and_names_the_leaves(leased, monkeypatch):
    from atoms.coordinator import admission

    with leased() as lease:
        monkeypatch.setattr(
            admission, "_occupied_scratch", lambda lease_, approved: ("x.#~1.staging",)
        )

        with pytest.raises(PreconditionRefused) as caught:
            admission.admit(lease, compiled_for(lease))

        message = str(caught.value)
        assert "no usable txid after 3 attempts" in message
        assert "scratch occupied at x.#~1.staging" in message


def test_a_present_planned_parent_refuses_without_building_an_observation(leased):
    """Design §6.4 branch two: a planned parent has no identity to compare, so no
    ChildObservation is constructed -- inventing one would be a fresh observation
    authorizing itself."""
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

        assert "planned" in str(caught.value)


def test_a_moved_parent_identity_refuses_naming_identity(leased):
    from atoms.coordinator import admission
    from tests.coordinator_support import replace_the_parent_directory

    with leased() as lease:
        approved = admission.admit(lease, compiled_for(lease))
        replace_the_parent_directory(lease, approved)

        with pytest.raises(PreconditionRefused) as caught:
            admission._occupied_scratch(lease, approved)

        assert "identity" in str(caught.value)


def test_an_unmapped_parent_node_is_a_protocol_error(leased):
    from atoms.core.errors import ProtocolError
    from atoms.coordinator.admission import _parent_path, _parent_paths, admit

    with leased() as lease:
        approved = admit(lease, compiled_for(lease))

        with pytest.raises(ProtocolError) as caught:
            _parent_path(_parent_paths(approved), object())

        assert "no parent path" in str(caught.value)
```

Add `compiled_creating_a_directory`, `create_the_planned_directory`, and
`replace_the_parent_directory` to `tests/coordinator_support.py`. The last one removes and recreates the
scratch parent through the binding's backend so its `st_ino` changes while its path does not.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_coordinator_admission.py -v`
Expected: FAIL — the occupancy assertions fail against the `()` stub, and `_parent_paths` is undefined.

- [ ] **Step 3: Implement the parent map**

```python
def _parent_paths(approved: ProjectApprovedSpec) -> dict[object, str]:
    """Design §6.4's node-to-path table, for the existing-project-parent branch only.

    `WorkRoot` is deliberately absent: a `ScratchNode` with `role=WORK` is handled by
    the work-root branch, because `CreateDirectory` stages into
    `metadata_root/work/<txid>/` rather than into project space.
    """
    mapping: dict[object, str] = {ProjectRoot(): ""}
    for entry in approved.paths:
        mapping[PersistentNode(path=entry.path)] = entry.path
        # An ApprovedPath fixes its own parent: the directory is the path minus its
        # trailing leaf. Every STAGING/TOMBSTONE/ANCHOR leaf shares its target's
        # parent, and the target is itself an ApprovedPath, so this is total for the
        # nodes this branch needs.
        mapping[entry.parent_node] = entry.path.removesuffix(entry.leaf).rstrip("/")
    return mapping


def _parent_path(mapping: dict[object, str], node: object) -> str:
    if node not in mapping:
        raise ProtocolError(f"no parent path for topology node {node!r}")
    return mapping[node]
```

- [ ] **Step 4: Implement the three branches**

```python
def _occupied_scratch(lease: Lease, approved: ProjectApprovedSpec) -> tuple[str, ...]:
    """Design §6.4. The proof is the expected baseline and never current authority."""
    mapping = _parent_paths(approved)
    directories = {entry.node: entry for entry in approved.directories}
    occupied: list[str] = []

    with _translated_resolution():
        for scratch in approved.scratch:
            if scratch.role is ScratchRole.WORK:
                _require_work_slot_free(lease, approved)
                continue

            entry = directories.get(scratch.parent_node)
            if type(entry) is ApprovedPlannedDirectory:
                _require_planned_parent_absent(lease, mapping, scratch)
                continue

            parent_path = _parent_path(mapping, scratch.parent_node)
            observed = observe_child(lease._binding, parent_path, scratch.leaf)
            _require_matches_approval(observed, entry, parent_path)
            if observed.present:
                occupied.append(f"{parent_path}/{scratch.leaf}".lstrip("/"))

    return tuple(occupied)
```

Write the three helpers so each names what moved:

- `_require_matches_approval(observed, entry, parent_path)` — compares `observed.parent_identity`
  against `entry.identity` and `observed.parent_constraints` against `entry.constraints`, raising
  `PreconditionRefused` whose message contains the word `identity` or `constraints` accordingly.
- `_require_planned_parent_absent(lease, mapping, scratch)` — re-resolves the planned parent and raises
  `PreconditionRefused` containing `planned` if it now exists. It constructs **no** `ChildObservation`.
- `_require_work_slot_free(lease, approved)` — re-resolves `metadata_root/work` against
  `approved.work_base` and raises if `work/<txid>` already exists.

- [ ] **Step 5: Implement the translation**

```python
@contextlib.contextmanager
def _translated_resolution() -> Iterator[None]:
    """Design §6.4.

    `resolve.py` raises ProjectApprovalRefused and CapabilityUnavailable -- correct at
    approval time, wrong afterwards. A post-approval divergence is drift, and §9
    requires PreconditionRefused. Stated categorically over the refusal types the
    resolver declares, so it cannot drift as `resolve.py` grows, and it never wraps
    `approve_for_project`, whose refusals are genuine.
    """
    try:
        yield
    except (ProjectApprovalRefused, CapabilityUnavailable) as exc:
        raise PreconditionRefused(
            f"post-approval drift during re-resolution: {exc}"
        ) from exc
```

- [ ] **Step 6: Run to verify it passes**

Run: `uv run pytest tests/test_coordinator_admission.py -v`
Expected: PASS.

- [ ] **Step 7: Arm and run Task 5's deferred mutation check**

`_occupied_scratch` is real now. Delete `occupied = ()` from the record-collision branch, run
`test_every_candidate_owned_by_a_record_exhausts_the_bound`, and confirm it fails on
`"scratch occupied" not in message`. Restore, confirm `git status --porcelain` is empty, and re-run.
Report the observed failure message.

- [ ] **Step 8: Commit**

```bash
git add src/atoms/coordinator/admission.py tests/test_coordinator_admission.py tests/coordinator_support.py
git commit -m "feat(coordinator): re-resolve each scratch parent against its approved baseline"
```

---

## Task 7: Preparation

**Files:**
- Create: `src/atoms/coordinator/prepare.py`
- Create: `tests/test_coordinator_prepare.py`

**Interfaces:**
- Consumes: A5a `Store.transaction()`, `_StoreTransaction.promote_staging(workspace, manifest)`,
  `.insert_record(txid, spec)`, `.set_active(txid)`, `Store.create_workspace(txid) -> Workspace`,
  `StagedBlob(name, digest, byte_len)`; `_require_work_slot_free` from Task 6.
- Produces: `open_workspace(lease, approved) -> Workspace` and
  `prepare_transaction(lease, approved, workspace, manifest) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_preparation_publishes_the_record_in_one_commit(leased):
    from atoms.coordinator.prepare import open_workspace, prepare_transaction
    from tests.coordinator_support import admission_for, stage_manifest

    with leased() as lease:
        approved = admission_for(lease)
        workspace = open_workspace(lease, approved)
        manifest = stage_manifest(workspace, [b"after"])

        prepare_transaction(lease, approved, workspace, manifest)

        record = lease._store.read_active()
        assert record is not None
        assert record.txid == approved.txid
        assert record.state is TransactionState.PREPARED


def test_a_workspace_from_another_transaction_is_refused(leased):
    from atoms.core.errors import ProtocolError
    from atoms.coordinator.prepare import prepare_transaction
    from tests.coordinator_support import admission_for

    with leased() as lease:
        approved = admission_for(lease)
        foreign = lease._store.create_workspace("someone-else")

        with pytest.raises(ProtocolError) as caught:
            prepare_transaction(lease, approved, foreign, ())

        message = str(caught.value)
        assert "someone-else" in message
        assert approved.txid in message


def test_a_proof_bound_to_another_binding_is_refused(leased):
    from atoms.core.errors import ProtocolError
    from atoms.coordinator.prepare import prepare_transaction
    from tests.coordinator_support import admission_for

    with leased() as first, leased() as second:
        approved = admission_for(first)
        workspace = second._store.create_workspace(approved.txid)

        with pytest.raises(ProtocolError) as caught:
            prepare_transaction(second, approved, workspace, ())

        assert "binding" in str(caught.value)


def test_a_raw_compiled_spec_is_refused(leased):
    from atoms.core.errors import ProtocolError
    from atoms.coordinator.prepare import prepare_transaction
    from tests.coordinator_support import compiled_for

    with leased() as lease:
        with pytest.raises(ProtocolError) as caught:
            prepare_transaction(lease, compiled_for(lease), None, ())

        assert "ProjectApprovedSpec" in str(caught.value)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_coordinator_prepare.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atoms.coordinator.prepare'`

- [ ] **Step 3: Implement `prepare.py`**

```python
"""Authority §7.3 steps 2-4 (design §7)."""

from __future__ import annotations

from atoms.core.errors import ProtocolError
from atoms.coordinator.admission import _require_work_slot_free
from atoms.coordinator.lease import Lease
from atoms.fs.approval import ProjectApprovedSpec
from atoms.store.blobs import StagedBlob
from atoms.store.workspace import Workspace


def open_workspace(lease: Lease, approved: ProjectApprovedSpec) -> Workspace:
    """Ledger #19: re-resolve the work base BEFORE creating anything under it.

    By `prepare_transaction` the workspace already exists and A6 has written into it,
    so preparation uses those pinned descriptors; re-resolving afterwards could not
    authorize their creation.

    Conditional on `work_base is not None`, which A4b populates only for a spec
    containing a `CreateDirectory` -- the sole effect with a WORK scratch role. When it
    is None, A4b has judged `work/` irrelevant to this transaction and there is no
    baseline to compare against. Treating None as a mismatch would refuse every
    transaction that creates no directory; treating it as an empty baseline would let a
    fresh observation authorize itself.
    """
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
    if type(approved) is not ProjectApprovedSpec:
        raise ProtocolError(
            f"expected exactly ProjectApprovedSpec, got {type(approved).__name__}"
        )
    if approved.binding is not lease._binding:
        raise ProtocolError("the proof's binding is not this lease's binding")
    if workspace.txid != approved.txid:
        raise ProtocolError(
            f"workspace txid {workspace.txid!r} does not match the proof's "
            f"{approved.txid!r}"
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

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_coordinator_prepare.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Prove each gate independently**

For each of the three gates, comment it out, re-run the suite, and confirm the matching test fails **on
its own assertion** rather than incidentally. Restore after each and confirm `git status --porcelain`
is empty. Report which test each gate armed.

- [ ] **Step 6: Commit**

```bash
git add src/atoms/coordinator/prepare.py tests/test_coordinator_prepare.py tests/coordinator_support.py
git commit -m "feat(coordinator): publish a prepared record behind one durable barrier"
```

---

## Task 8: Transition persistence

**Files:**
- Create: `src/atoms/coordinator/transitions.py`
- Create: `tests/test_coordinator_transitions.py`

**Interfaces:**
- Consumes: A3 `classify_recovery`, `build_recovery_snapshot`,
  `reduce_recovery_plan_prefix(snapshot, plan, completed_steps)`, `TransitionTransactionState`,
  `TransitionEffectState`, `TransformEffectTuple`, `RemoveScratch`, `PreserveExternal`,
  `DetachActive`; A5a's typed setters.
- Produces: `persist_plan_prefix(lease, approved, plan, start: int) -> int`.

**Every plan comes from the production `classify_recovery`** over a synthetic
`build_recovery_snapshot`, never hand-assembled. The ordering rules are only meaningful against plans
A3 actually emits, and `ActionPlan.__init__` refuses construction without the classifier's token
anyway (`plan.py:130`).

- [ ] **Step 1: Write the failing tests**

```python
def test_the_cursor_stops_at_the_first_mutating_step(leased):
    from atoms.coordinator.transitions import persist_plan_prefix
    from tests.coordinator_support import prepared_with_plan

    with leased() as lease:
        approved, plan = prepared_with_plan(lease)

        cursor = persist_plan_prefix(lease, approved, plan, 0)

        assert type(plan.steps[cursor]) in {TransformEffectTuple, RemoveScratch}


def test_a_plan_of_only_metadata_steps_runs_to_the_end(leased):
    from atoms.coordinator.transitions import persist_plan_prefix
    from tests.coordinator_support import prepared_with_halt_plan

    with leased() as lease:
        approved, plan = prepared_with_halt_plan(lease)

        assert persist_plan_prefix(lease, approved, plan, 0) == len(plan.steps)


def test_a_record_disagreeing_with_the_reduced_prefix_refuses(leased):
    from atoms.core.errors import ProtocolError
    from atoms.coordinator.transitions import persist_plan_prefix
    from tests.coordinator_support import prepared_with_plan

    with leased() as lease:
        approved, plan = prepared_with_plan(lease)
        with lease._store.transaction() as txn:
            txn.set_commit_decision(approved.txid, CommitDecision.COMMITTED)

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, plan, 0)

        assert "commit decision" in str(caught.value)


def test_no_active_record_refuses(leased):
    from atoms.core.errors import ProtocolError
    from atoms.coordinator.transitions import persist_plan_prefix
    from tests.coordinator_support import prepared_with_plan

    with leased() as lease:
        approved, plan = prepared_with_plan(lease)
        with lease._store.transaction() as txn:
            txn.set_active(None)

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, plan, 0)

        assert "no active record" in str(caught.value)


def test_preserve_external_advances_without_writing(leased):
    """It carries only topology nodes -- there is nothing durable to write."""
    from atoms.coordinator.transitions import persist_plan_prefix
    from tests.coordinator_support import index_of_preserve_external, prepared_with_preserve_external

    with leased() as lease:
        approved, plan = prepared_with_preserve_external(lease)
        start = index_of_preserve_external(plan)
        before = lease._store.read_record(approved.txid)

        persist_plan_prefix(lease, approved, plan, start)

        assert lease._store.read_record(approved.txid) == before
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_coordinator_transitions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'atoms.coordinator.transitions'`

- [ ] **Step 3: Implement the gates and the walk**

```python
"""A3 transition persistence in plan order (design §8)."""

from __future__ import annotations

from atoms.core.errors import ProtocolError
from atoms.core.recovery.plan import RemoveScratch, TransformEffectTuple
from atoms.core.recovery.reducer import reduce_recovery_plan_prefix
from atoms.coordinator.lease import Lease
from atoms.fs.approval import ProjectApprovedSpec

#: `AuthorizedStep.step` is typed exactly these two (`plan.py:189`), so the stop rule
#: is read off A3's own contract rather than restated here.
_MUTATING = (TransformEffectTuple, RemoveScratch)


def persist_plan_prefix(
    lease: Lease, approved: ProjectApprovedSpec, plan: object, start: int
) -> int:
    """Persist every metadata-only step from `start`, stop at the first step A7 must
    execute, and return that index.

    Reading the active record here is load-bearing three ways: it enforces the binding
    identity, it supplies the independent txid for ledger #21, and it guarantees that
    returning past a metadata step means that step's COMMIT completed.
    """
    if type(approved) is not ProjectApprovedSpec:
        raise ProtocolError(
            f"expected exactly ProjectApprovedSpec, got {type(approved).__name__}"
        )
    if approved.binding is not lease._binding:
        raise ProtocolError("the proof's binding is not this lease's binding")

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
        if isinstance(step, _MUTATING):
            return cursor
        _persist_one(lease, record.txid, plan, step)
        cursor += 1
    return cursor
```

`_require_projection_matches` calls
`reduce_recovery_plan_prefix(plan.bound_snapshot, plan, completed_steps=start)` and compares the live
record's **complete** durable projection field by field — `record.spec` against
`approved.compiled.spec`, `record.state` against `expected.transaction_state`, `record.committed`
against `expected.commit_decision`, `record.rollback_result`, `record.halt_diagnostic`,
`record.journals`, and active status against `expected.active` — raising `ProtocolError` naming the
field that disagreed, in the words the tests assert (`"commit decision"`, `"halt diagnostic"`, …). It
also requires `plan.bound_snapshot.compiled == approved.compiled` and
`plan.bound_snapshot.topology == approved.topology`.

Checking `plan.bound_snapshot.transaction_state` alone would be both incomplete and wrong whenever
`start > 0`, because the record has legitimately advanced past the bound snapshot by then.

- [ ] **Step 4: Implement the barrier discipline**

One SQLite barrier per writable step (authority §7.4):

```python
def _persist_one(lease: Lease, txid: str, plan: object, step: object) -> None:
    if isinstance(step, PreserveExternal):
        return  # No durable representation: it carries only topology nodes.

    if isinstance(step, DetachActive):
        with lease._store.transaction() as txn:
            txn.set_active(None)
        return

    if isinstance(step, TransitionEffectState):
        with lease._store.transaction() as txn:
            txn.set_journal_state(txid, step.effect_id, step.state)
        return

    if isinstance(step, TransitionTransactionState):
        with lease._store.transaction() as txn:
            txn.set_transaction_state(txid, step.state)
            # ROLLED_BACK and its result are ONE atomic pair -- ledger #12 says
            # "atomically with", so the result is never a second transaction. HALTED
            # and its diagnostic likewise, and the diagnostic is first-wins because
            # #12 requires the FIRST halt to freeze the pre-halt state. `committed` is
            # preserved across the halt, never cleared.
            if step.state is TransactionState.ROLLED_BACK:
                txn.set_rollback_result(txid, plan.rollback_result)
            elif step.state is TransactionState.HALTED:
                existing = lease._store.read_record(txid)
                if existing is not None and existing.halt_diagnostic is None:
                    txn.set_halt_diagnostic(txid, plan.diagnostic)
        return

    raise ProtocolError(f"unhandled plan step {type(step).__name__}")
```

**Read `TransitionTransactionState` and `TransitionEffectState`'s actual field names at
`plan.py:60-74` before writing this**, and use those names rather than the ones sketched here. Report
any divergence in the task report.

`DetachActive` must be reachable only after every earlier step has been persisted; the walk above gives
that for free, since it advances strictly in plan order.

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_coordinator_transitions.py -v`
Expected: PASS, 5 tests.

- [ ] **Step 6: Prove the stop rule covers both variants**

Add one case per mutating variant — a plan whose first mutating step is a `TransformEffectTuple`, and
one whose first is a `RemoveScratch` — asserting the returned cursor indexes it. A stop rule proved
against one variant is not proved.

- [ ] **Step 7: Prove the first-wins diagnostic**

Persist a `HALTED` transition twice with different diagnostics and assert the stored one is the first.
Then remove the `existing.halt_diagnostic is None` condition, re-run, confirm the test fails, restore,
and confirm a clean `git status --porcelain`.

- [ ] **Step 8: Commit**

```bash
git add src/atoms/coordinator/transitions.py tests/test_coordinator_transitions.py tests/coordinator_support.py
git commit -m "feat(coordinator): persist a plan prefix and stop before A7's work"
```

---

## Task 9: Architecture guards, packaging, the ledger, and the status

**Files:**
- Create: `tests/test_coordinator_architecture.py`, `tests/coordinator_child.py`,
  `tests/test_coordinator_process.py`
- Modify: `tests/test_fs_architecture.py`, `tests/test_store_architecture.py`, `pyproject.toml`,
  `docs/deferred-obligation-ledger.md`, `docs/plans/2026-08-02-a5b-recovery-lease-design.md`,
  `AGENTS.md`

- [ ] **Step 1: Narrow the two temporary guards**

`test_fs_architecture.py:364` — `test_no_production_caller_of_bind_exists_yet` becomes an assertion
that the production bind-caller set is exactly `{src/atoms/coordinator/root.py}` and that the call
passes `CERTIFIED_ALLOWLIST`. Rename it `test_the_only_production_bind_caller_is_the_coordinator_root`.

`test_fs_architecture.py:981` — `test_no_consumer_of_the_approved_spec_exists_yet` becomes an assertion
that `ProjectApprovedSpec` appears only under `src/atoms/coordinator/` and in `src/atoms/fs/approval.py`
where it is defined, and that `prepare_transaction` and `persist_plan_prefix` each take one. Rename it
`test_only_the_coordinator_consumes_the_approved_spec`.

**Do not delete either test.** `test_no_consumer_of_the_approved_spec_exists_yet`'s own docstring says
it "is replaced by one asserting A5-A8 accept only this proof" when A5 lands. Replacement, not removal.

- [ ] **Step 2: Write the new guards**

```python
def test_only_the_coordinator_imports_the_store():
    """The DAG is coordinator -> {store, fs, core}; nothing else may reach the store."""
    offenders = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path.parent.name == "coordinator":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        _, package = _source_module(SOURCE_ROOT.parent, path)
        if any(
            name.startswith("atoms.store")
            for name in _resolved_imports(tree, package=package)
        ):
            offenders.append(str(path.relative_to(SOURCE_ROOT)))
    assert offenders == []


def test_the_coordinator_exports_nothing():
    import atoms.coordinator as package

    assert package.__all__ == ()
    for name in ("Lease", "ProjectBinding", "Store", "_recovery_lease"):
        assert name not in package.__all__


def test_no_module_outside_the_coordinator_names_the_lease():
    """Absence from __all__ is not enforcement -- the underscore states the contract
    and this guard covers the population it can speak for: in-tree callers."""
    offenders = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path.parent.name == "coordinator":
            continue
        source = path.read_text(encoding="utf-8")
        if "_recovery_lease" in source or "coordinator.lease" in source:
            offenders.append(str(path.relative_to(SOURCE_ROOT)))
    assert offenders == []
```

- [ ] **Step 3: Write the fresh-process tier**

`tests/coordinator_child.py` binds the volume itself, enters `_lease` with a test allowlist, and prints
what a fresh process sees. `tests/test_coordinator_process.py` drives it for two cases:

- **#23 crash cut** — a first process creates an orphan workspace and an unindexed blob and exits
  without a record; the second process's lease entry removes both, while a workspace a durable record
  names survives untouched.
- **Publication atomicity** — a cut before the publication COMMIT leaves no record and no `active` row;
  after it, `active` resolves to a record whose spec and referenced blobs are all durable.

- [ ] **Step 4: Update the packaging metadata**

`pyproject.toml:3`:

```toml
import-names = ["atoms.coordinator", "atoms.core", "atoms.fs", "atoms.store"]
import-namespaces = ["atoms"]
```

- [ ] **Step 5: Update the ledger**

Move #7, #18, #21, and #23 into the discharged table with the date and the suites that cover them.
Relabel the three that stay open, in place:

- **#12** — append: *A5b landed the write half (plan-order persistence and the §7.4 barriers); A7 owns
  durable completion of each mutating step.*
- **#17** — append: *A5b landed the lease half (acquire, hold across the write phase, reclaim at every
  entry); the resolve-and-complete half waits on A7, which removes the `NotImplementedError` trap in
  `coordinator/lease.py`.*
- **#19** — append: *A5's part is done; A6 and A7 remain owners for capture and execution.*
- **#9** stays as written — A5b covers its own entry points and A6–A8 extend the guard as they land.

- [ ] **Step 6: Update the status in both documents**

`AGENTS.md`'s A5 paragraph becomes "A5b implemented on 2026-08-02", and the A5b design's `**Status:**`
line becomes "Implemented." `test_a5_status_is_synchronized_across_authority_documents` already reads
both documents, so update its expected strings in the same commit.

- [ ] **Step 7: Extend the fixture-registry guard**

A5a's guard covers `test_store_*.py`. Extend its file list to `test_coordinator_*.py` so `coordinator_on`
and `leased` are required to live in `tests/conftest.py`.

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

**Spec coverage.** Every design section maps to a task: §5.0–§5.1 → Task 1; §5.2 → Task 2; §5.3 → Task
3; §6.4's observer → Task 4; §6.2–§6.3 → Task 5; §6.4's branches and translation → Task 6; §6.5 and §7
→ Task 7; §8 → Task 8; §10's architecture and process tiers plus §3.1's ledger outcomes → Task 9. §9's
error table is distributed across the tasks that raise each type, and no task introduces a new one.
§9's `TransactionHalted` row is deliberately unimplemented — the design marks it unreachable in A5b,
and no task claims to reach it.

**The one deviation from the design, and why.** Design §4.1 gives `root.py` "the package-private
resource stack" and `lease.py` "the protocol." This plan splits the allowlist between them —
`_lease(..., *, allowlist)` in `lease.py`, `CERTIFIED_ALLOWLIST` named only in `root.py` — because
`CERTIFIED_ALLOWLIST` is empty (`volume.py:78`) and production binding fails closed, so a protocol that
hardcoded it could never be executed by a test. This follows A4a's own precedent: `bind_project_volume`
takes the allowlist as a required keyword parameter for exactly this reason, and
`build_test_allowlist`'s docstring says so outright. Ledger #18 remains provable as an architecture
assertion over `root.py`'s single call site — which is how A4a proved its half too.

**Type consistency.** `Lease._binding` / `Lease._store` are spelled identically in Tasks 2, 3, 5, 6, 7,
and 8. `new_txid`, `SCRATCH_ATTEMPTS`, `admit`, `_occupied_scratch`, `_parent_paths`, `_parent_path`,
and `_require_work_slot_free` keep one spelling throughout; `_require_work_slot_free` is defined in
Task 6 and consumed by Task 7's `open_workspace`, which is why Task 7 imports it from `admission`.
`prepare_transaction(lease, approved, workspace, manifest)` and
`persist_plan_prefix(lease, approved, plan, start)` match design §6.5 and §8.1 exactly.

**One deferred proof, tracked across two tasks.** Task 5's `occupied = ()` reset cannot be proved
load-bearing while `_occupied_scratch` is stubbed. Task 5 Step 5 records that rather than claiming it,
and Task 6 Step 7 arms and runs the mutation. A plan that asserted the proof in Task 5 would be
asserting something untrue.

**Two places the implementer must read the code rather than trust this plan**, both flagged inline:
`_open_project_relative` in Task 4, where the resolver's existing anchored walk should be reused rather
than duplicated; and `TransitionTransactionState` / `TransitionEffectState`'s field names in Task 8.
