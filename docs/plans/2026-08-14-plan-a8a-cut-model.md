# A8a Cut-Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the synthetic exerciser, the persistence-cut model, the A3 agreement matrix with its
named tuples and sabotage arms — every A8 component that lives in `python/tests/` — leaving the
feature resolver, the certification harness, and every status flip to A8b.

**Architecture:** A8 splits at the tests/tooling seam: everything here is test code recording,
reconstructing, and judging the *existing* engine through its public commands and the guarded
lease, while A8b changes `src/` (the ioctl feature resolver, `CERTIFIED_ALLOWLIST` population) and
adds `python/tools/certify/`. The cut model is record–reconstruct–recover: one recorded run per
scenario yields the whole cut matrix offline, each cell reconstructed onto the real filesystem and
recovered through a fresh lease, with A3 as the oracle. The exerciser is a data-declared scenario
library consumed by three drivers (clean/caught, SIGKILL, persistence cut) and, in A8b, by the
certification guest.

**Tech Stack:** Python 3.11+, stdlib only (`os`, `sqlite3`, `hashlib`, `dataclasses`, `enum`,
`json`, `subprocess`, `signal`), `pytest`, `ruff`, `pyright`. Builds on A1–A7 as probed below.

**Spec:** [`2026-08-14-a8-persistence-cut-and-certification-design.md`](2026-08-14-a8-persistence-cut-and-certification-design.md).
**Authority:** [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md).
Where this plan and either document disagree, the design wins over this plan and the authority wins
over both.

## Measured facts this plan is built on

Probed against the live tree on 2026-08-14 (`main` at `e116d79`). **If any turns out false during
implementation, stop and report — do not adapt around it silently.**

| Fact | Where measured |
| --- | --- |
| `Backend` protocol: 22 methods at `fs/backend.py:68-146`; mutating: `create_exclusive`, `write`, `set_mode`, `mkdir_child`, `unlink_child`, `rmdir_child`, `symlink_child`, `set_marker_xattr`, `repair_entry_mode(parent_fd, name, mode, *, before_change)`, `exchange`, `transfer_noclobber(src_fd, src, dst_fd, dst)`, `link_anchor`; barriers: `flush_file(fd)`, `flush_directory(fd)`; opens: `create_or_open`, `open_existing`, `open_root`, `open_child_directory`, `open_directory_handle`, `open_regular_nofollow`; also `symlink_fingerprint`, `close_fd`, `detach_fd`, `lock_exclusive`, `try_lock_exclusive`. The exact 22-name set is pinned by `test_fs_architecture.py:669`. | probe 2026-08-14 |
| The backend is injected: public commands take `backend` first (`coordinator/commands.py:112,161,177`); `root._recovery_lease(backend, project_root, metadata_root, storage)` wraps it in `AuditedBackend` at `root.py:41`. A test backend beneath the facade is the established seam (`KillingBackend`, `RecordingBackend` at `tests/fs_support.py:53,83`). | probe 2026-08-14 |
| Store COMMIT surface: `Store.transaction()` contextmanager at `store/connection.py:785` (`BEGIN IMMEDIATE` … `COMMIT` at `:805`); the full-transaction COMMIT sequence is the 9-element `STORE_BARRIERS` tuple at `tests/test_coordinator_kill_matrix.py:31`: `prepared`, `registration-binding`, `applying`, `e1-started`, `e1-done`, `applied`, `committed`, `settlement-binding`, `detach`. `execute_child._configure_store_cut` (`tests/execute_child.py:48`) patches `Store.transaction` / `_StoreTransaction._run_barrier` by countdown — the existing store-event hook points. | probe 2026-08-14 |
| A3 surface: `build_recovery_snapshot` (`core/recovery/snapshot.py:179`), `classify_recovery` (`classifier.py:128`), `authorize_recovery_step` (`authorization.py:38`), `reduce_recovery_plan_prefix` (`reducer.py:103`), `apply_recovery_plan` (`reducer.py:124`); exactly five, pinned by `test_recovery_architecture.py:55`. Alpha-renaming helpers: `reallocate_joint_identities` (`tests/recovery_support.py:305`), `_reallocate_snapshot_identities` (`:1550`). | probe 2026-08-14 |
| Agreement precedent: `tests/test_coordinator_conformance.py` — `_durable_projection(ingredients, txid)` at `:26` returns `(record.state, record.committed, record.rollback_result, record.journals, active is not None)`; `_model_projection(snapshot, plan)` at `:46` projects `apply_recovery_plan`; snapshot/plan captured by monkeypatching `commit.classify_recovery` / `execute.classify_recovery`; the caught-rollback cell injects by patching `execute.create_file.apply` to raise. Two cells exist today. | read 2026-08-14 |
| Kill matrix idiom: `tests/test_coordinator_kill_matrix.py` — `_child()` `:68` runs `python -m tests.execute_child` with `ATOMS_EXECUTE_CONFIG` (JSON env var), `_recover()` `:83` runs `tests.coordinator_child`, `_world()` `:102` snapshots the project tree skipping `.#~chain`, `_assert_terminal()` `:120` requires two byte-identical recovery passes and `active is None`; the rehearsal idiom records the event stream with `{"record": True}` then derives countdowns. `_enable_commands` is imported from `tests/test_coordinator_commands.py:17`. | read 2026-08-14 |
| `execute_child` config keys today: `record`, `method`+`countdown`, `store_cut`, `umask`, `recover`; spec builders come from `tests/coordinator_support.py`: `create_file_spec`, `replace_spec`, `delete_spec`, `move_spec`, `directory_spec`, plus `AFTER`; payloads via `tests/capture_support.py`'s `DictPayloads` and `digest_of`. `_spec(name)` maps five fixed names. | read 2026-08-14 |
| Fixtures (all in `tests/conftest.py`, enforced by the fixture-registry guards at `test_fs_architecture.py:498`, `test_coordinator_architecture.py:130`, `test_recovery_architecture.py:184`, `test_store_architecture.py:1258`): `ext4_volume` (`:304`, tmpdir on a real ext4 volume or skip), `coordinator_on` (`:562`, returns `ingredients()` → `(LinuxBackend(), str(project_root), str(metadata_root), storage)`), `leased` (`:588`, patches `root.CERTIFIED_ALLOWLIST` via `build_test_allowlist` then enters `root._recovery_lease`), `test_storage_profile` (`:271`, `StorageProfile(profile_id="atoms-test-profile")`). | read 2026-08-14 |
| `build_test_allowlist(lock, project_root, storage)` at `tests/fs_support.py:534` resolves the live mount entry and returns a singleton allowlist with `certification_ref="test-injected-not-crash-certified"`. | read 2026-08-14 |
| Scratch grammar: leaves are `.#~<txid>.<effect_id>.<role>`, roles `staging|tombstone|anchor|work` (`core/scratch.py:56`); the chain lives at `.#~chain` (`:15`); `WorkRoot` slots live under `metadata_root/work/<txid>` — a `CreateDirectory` cut spans two roots. txids are `secrets.token_hex(16)` regenerated per admission attempt (`coordinator/admission.py:38`), so surviving-debris names are nondeterministic across runs. | read 2026-08-14 |
| Effect barrier call sites (the §13.2 orderings): blob promotion flushes at `store/blobs.py:383-384`; capture staging flushes at `coordinator/capture.py:239,286`; per-effect flushes at `effects/create_file.py:54`, `effects/replace_file.py:37`, `effects/delete_path.py:28`, `effects/move.py:32,84-85` (destination `:84` before source `:85`), `effects/create_directory.py:43,64,66`; database publication at `store/connection.py:161-173`. | grep 2026-08-14 |
| `test_docs_status.py`: `STAGES = ("A1","A2","A3","A4a","A4b","A5a","A5b","A6","A7a","A7b","A8","A9")` at `:31`, `FIRST_UNIMPLEMENTED = "A8"` at `:34`; `_stages_of` expands base labels, so splitting the tuple to `"A8a","A8b"` makes existing `"A8"` claims cover both. | read 2026-08-14 |
| The audited facade opens a declared-path mutation window via `set_declared_paths` (`fs/audit.py:143`), opened at `coordinator/execute.py:157` and `coordinator/recover.py:702`; recovery halts persist and are never reclassified (`recover.py:783-786`); recovery classification happens inside `recover.resolve` (`recover.py:605`) — the lease-entry capture point is `recover.classify_recovery`. | read 2026-08-14 |
| SQLite pragmas: `synchronous=FULL`, WAL; the db path is `<metadata_root>/atoms.db`; a second read-only `sqlite3.connect` for the backup API is safe between store transactions (the conformance test already opens a second connection for `_txid` at `test_coordinator_conformance.py:20`). | read 2026-08-14 |

## Global Constraints

- Tests-only: **no `src/atoms` change in A8a.** The feature resolver, allowlist population, and
  every status flip belong to A8b — except this plan's final task, which splits the `STAGES`
  tuple and moves `FIRST_UNIMPLEMENTED` to `"A8b"`, which is true only once Tasks 1–9 are merged.
- Stdlib only; no new dependencies (no hypothesis — generators are hand-rolled and deterministic).
- All new fixtures go in `tests/conftest.py`; helper modules are non-`test_`-prefixed so pytest
  does not collect them.
- No silent caps: every bounded enumeration asserts its counts and reports skips by reason.
- The cell oracle is A3. Hand-written expectations appear only in the named directed tests and as
  coverage assertions (`expected classification family`), never as per-cell outcomes.
- Conventional commits, no AI-attribution trailers.
- After every task: `uv run pytest`, `uv run ruff check`, `uv run pyright` from `python/`, all
  green.

## File Structure

```
python/tests/
  exerciser.py                    # create: scenario model + library (Tasks 1-2)
  persistence_model.py            # create: units, recorder, durable state, reconstruction (Tasks 3-5)
  test_exerciser.py               # create: scenario library conformance (Tasks 1-2)
  test_persistence_model.py       # create: recorder/reconstruction unit tests (Tasks 3-5)
  test_persistence_cut_matrix.py  # create: the sweep, named tuples, §9.5 injection, sabotage (Tasks 6-9)
  execute_child.py                # modify: scenario-driven child + projection output (Task 7)
  conftest.py                     # modify: new fixtures (Tasks 1, 3, 6)
  test_docs_status.py             # modify: STAGES split, boundary move (Task 10)
docs/plans/                       # modify: status headers (Task 10)
AGENTS.md, README.md              # modify: A8a implemented (Task 10)
```

---

## Task 1: Scenario model and the five minimal variant scenarios

**Files:**
- Create: `python/tests/exerciser.py`
- Create: `python/tests/test_exerciser.py`
- Modify: `python/tests/conftest.py`

**Interfaces:**
- Produces: `Scenario(name: str, family: str, build_spec: Callable[[], TransactionSpec],
  payloads: Callable[[], DictPayloads], seed_world: Callable[[Path], None],
  drift: Callable[[Path], None] | None = None, inject_failure: str | None = None)` — frozen
  dataclass. `family` is the coverage-only expected classification family, one of
  `{"commit", "rollback", "refusal"}`.
- Produces: `SCENARIOS: tuple[Scenario, ...]` and `scenario(name: str) -> Scenario`.
- Produces: `run_clean(scenario, ingredients, monkeypatch) -> TransactionOutcome` — drives
  `run_transaction` through `_enable_commands`, after `register_root` and `seed_world`.
- Produces (conftest): fixture `exerciser_run(coordinator_on, monkeypatch)` returning a
  `run(scenario_name: str) -> tuple[ingredients, TransactionOutcome]` callable.

- [ ] **Step 1: Write the failing test**

```python
# python/tests/test_exerciser.py
"""The exerciser scenario library: coverage-only families and clean-run conformance."""

import pytest

from tests.exerciser import SCENARIOS, scenario

MINIMAL = ("minimal-create", "minimal-replace", "minimal-delete", "minimal-move", "minimal-mkdir")


def test_the_library_names_each_variant_minimal_scenario():
    names = {entry.name for entry in SCENARIOS}
    assert set(MINIMAL) <= names
    assert all(scenario(name).family == "commit" for name in MINIMAL)


@pytest.mark.parametrize("name", MINIMAL)
def test_each_minimal_scenario_commits_clean(name, exerciser_run):
    ingredients, outcome = exerciser_run(name)
    assert outcome.outcome.name == "COMMITTED"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_exerciser.py -x -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.exerciser'`.

- [ ] **Step 3: Write `tests/exerciser.py` and the fixture**

```python
# python/tests/exerciser.py
"""Data-declared exerciser scenarios (design §6): spec builder, seed world, payloads.

Scenario surfaces are shaped to the documented consumer shapes — corpus writes and
archive/import moves — without importing any consumer. `family` is a coverage
assertion only; A3 remains the matrix's oracle.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from tests.capture_support import DictPayloads, digest_of
from tests.coordinator_support import (
    AFTER,
    create_file_spec,
    delete_spec,
    directory_spec,
    move_spec,
    replace_spec,
)


@dataclass(frozen=True)
class Scenario:
    name: str
    family: str  # "commit" | "rollback" | "refusal" — coverage-only
    build_spec: Callable[[], object]
    payloads: Callable[[], DictPayloads]
    seed_world: Callable[[Path], None]
    drift: Callable[[Path], None] | None = None
    inject_failure: str | None = None  # effect module attribute to fail, e.g. "replace_file"


def _seed_none(project: Path) -> None:
    return None


def _seed_replace(project: Path) -> None:
    (project / "f.txt").write_bytes(b"before\n")


def _seed_delete(project: Path) -> None:
    (project / "f.txt").write_bytes(b"before\n")


def _seed_move(project: Path) -> None:
    (project / "source.txt").write_bytes(b"before\n")


def _with_after() -> DictPayloads:
    return DictPayloads({digest_of(AFTER): AFTER})


def _without_payloads() -> DictPayloads:
    return DictPayloads({})


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("minimal-create", "commit", create_file_spec, _with_after, _seed_none),
    Scenario("minimal-replace", "commit", replace_spec, _with_after, _seed_replace),
    Scenario("minimal-delete", "commit", delete_spec, _without_payloads, _seed_delete),
    Scenario("minimal-move", "commit", move_spec, _without_payloads, _seed_move),
    Scenario("minimal-mkdir", "commit", directory_spec, _with_after, _seed_none),
)


def scenario(name: str) -> Scenario:
    for entry in SCENARIOS:
        if entry.name == name:
            return entry
    raise KeyError(name)


def run_clean(entry: Scenario, ingredients, monkeypatch):
    """Register the root, seed the world, and run the transaction clean."""
    from atoms.coordinator.commands import register_root, run_transaction
    from tests.test_coordinator_commands import _enable_commands

    backend, project_root, metadata_root, storage = ingredients
    _enable_commands(ingredients, monkeypatch)
    entry.seed_world(Path(project_root))
    register_root(
        backend, project_root, metadata_root, storage,
        genesis_payload=b"exerciser-genesis",
        registered_surface=(),
    )
    return run_transaction(
        backend, project_root, metadata_root, storage,
        entry.build_spec(), entry.payloads(),
    )
```

> **Note for the implementer:** probe `register_root`'s exact signature at
> `coordinator/commands.py:112` before writing — the `genesis_payload` /
> `registered_surface` keyword names above must match the tree, and the kill matrix's
> `_prepare` (`test_coordinator_kill_matrix.py:49-67`) shows the working call shape to copy.
> If they differ, follow the tree and update this plan's later tasks mechanically.

Add to `python/tests/conftest.py` (beside `leased`):

```python
@pytest.fixture
def exerciser_run(coordinator_on, monkeypatch):
    """Run a named exerciser scenario clean; returns (ingredients, outcome)."""

    def run(name: str):
        from tests.exerciser import run_clean, scenario

        ingredients = coordinator_on()
        outcome = run_clean(scenario(name), ingredients, monkeypatch)
        return ingredients, outcome

    return run
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `uv run pytest tests/test_exerciser.py -q`
Expected: PASS (6 tests). Also run `uv run pytest -q` — the fixture-registry guards must accept
`exerciser_run` (it lives in conftest, so they will).

- [ ] **Step 5: Commit**

```bash
git add python/tests/exerciser.py python/tests/test_exerciser.py python/tests/conftest.py
git commit -m "test(exerciser): scenario model and the five minimal variant scenarios"
```

## Task 2: Compound, repeated-path, drift, caught-rollback, and refusal scenarios

**Files:**
- Modify: `python/tests/exerciser.py`
- Modify: `python/tests/test_exerciser.py`

**Interfaces:**
- Produces: scenarios `corpus-write` (ancestor `CreateDirectory` chain + two creates + one
  replace — the corpus-write shape), `archive-move` (move + create at the vacated name — the
  archive/import shape, a repeated path), `caught-rollback` (`inject_failure="replace_file"`,
  family `"rollback"`), `drift-blocker` (a `DeletePath` scenario whose `drift` plants a foreign
  file at the target between seed and run, family `"rollback"`), `refusal-capability`
  (family `"refusal"`, driven only by Task 2's refusal test, never by the matrix).
- Produces: `run_caught(entry, ingredients, monkeypatch) -> TransactionOutcome` — like
  `run_clean` but patches `atoms.coordinator.execute.<inject_failure>.apply` to raise after
  `PREPARED`, following `test_coordinator_conformance.py:110`'s idiom.

- [ ] **Step 1: Write the failing tests**

```python
# append to python/tests/test_exerciser.py
from tests.exerciser import run_caught

def test_compound_scenarios_commit_clean(exerciser_run):
    for name in ("corpus-write", "archive-move"):
        _, outcome = exerciser_run(name)
        assert outcome.outcome.name == "COMMITTED"


def test_caught_rollback_rolls_back(coordinator_on, monkeypatch):
    from tests.exerciser import scenario

    ingredients = coordinator_on()
    outcome = run_caught(scenario("caught-rollback"), ingredients, monkeypatch)
    assert outcome.outcome.name == "ROLLED_BACK"


def test_capability_refusal_precedes_metadata_and_mutation(coordinator_on, monkeypatch):
    """Design §6: a refusal has no recovery row — nothing durable may exist."""
    import sqlite3
    from pathlib import Path

    from atoms.core.errors import CapabilityUnavailable
    from tests.exerciser import run_refused, scenario
    from tests.fs_support import RestrictedBackend

    ingredients = coordinator_on()
    backend, project_root, metadata_root, storage = ingredients
    restricted = RestrictedBackend(backend, withhold=frozenset({"exchange"}))
    with pytest.raises(CapabilityUnavailable):
        run_refused(
            scenario("refusal-capability"),
            (restricted, project_root, metadata_root, storage),
            monkeypatch,
        )
    db = Path(metadata_root) / "atoms.db"
    if db.exists():
        with sqlite3.connect(db) as connection:
            rows = connection.execute("SELECT COUNT(*) FROM transaction_record").fetchone()
        assert rows[0] == 0
    assert sorted(p.name for p in Path(project_root).iterdir() if p.name != ".#~chain") == []
```

> **Probe first:** `RestrictedBackend`'s constructor is at `tests/fs_support.py:270` — confirm the
> withholding parameter name (`override_names` / per-capability errno map) and adjust the call.
> The refusal scenario's spec must *require* the withheld capability: `replace_spec()` requires
> `atomic_exchange`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_exerciser.py -q`
Expected: FAIL — `corpus-write` unknown, `run_caught` / `run_refused` undefined.

- [ ] **Step 3: Implement the scenarios and drivers**

Append to `python/tests/exerciser.py`:

```python
def _corpus_write_spec():
    """Ancestor chain + two creates + a replace: the corpus-write shape (§12.1)."""
    # Build with the same TransactionSpec constructor coordinator_support uses.
    # Probe coordinator_support's builders and compose: CreateDirectory "data",
    # CreateDirectory "data/records", CreateFileNoClobber "data/records/one.txt",
    # CreateFileNoClobber "data/records/two.txt", ReplaceFile "index.txt".
    # The exact member syntax must be copied from coordinator_support.directory_spec
    # and create_file_spec at implementation time; the surfaces and effect order
    # above are the contract this plan pins.
    ...


def _archive_move_spec():
    """Move f.txt -> archive/f.txt, then re-create f.txt: the archive/import shape.

    A repeated path: f.txt appears in the move (as vacated source) and the create
    (as the new occupant). Requires the archive/ directory to pre-exist via seed.
    """
    ...
```

The two `...` bodies are written at implementation time against
`coordinator_support`'s real constructor syntax — they are the only two bodies this plan cannot
spell without the probe, and the *surfaces, effect kinds, order, and repeated path* above are
binding. Then:

```python
def run_caught(entry: Scenario, ingredients, monkeypatch):
    from atoms.coordinator import execute
    from tests.test_coordinator_commands import _enable_commands
    # identical setup to run_clean, then, before run_transaction:
    module = getattr(execute, entry.inject_failure)
    original = module.apply

    calls = {"n": 0}

    def failing(*args, **kwargs):
        calls["n"] += 1
        raise OSError("exerciser-injected failure")

    monkeypatch.setattr(module, "apply", failing)
    ...  # run_transaction; the engine catches, rolls back, returns ROLLED_BACK


def run_refused(entry: Scenario, ingredients, monkeypatch):
    """Like run_clean but expects the command to raise; performs no registration
    of expectations beyond setup."""
    ...
```

New scenario rows appended to `SCENARIOS`, exactly as named in **Interfaces**. `drift-blocker`'s
`drift` callable writes a foreign file at the `DeletePath` target after seeding; the matrix (Task
6) applies `drift` between reconstruction and recovery — Task 2 only asserts the clean half.

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `uv run pytest tests/test_exerciser.py -q` then `uv run pytest -q`, `uv run ruff check`,
`uv run pyright`.
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add python/tests/exerciser.py python/tests/test_exerciser.py
git commit -m "test(exerciser): compound, drift, caught-rollback, and refusal scenarios"
```

## Task 3: Durability units and the recording backend

**Files:**
- Create: `python/tests/persistence_model.py`
- Create: `python/tests/test_persistence_model.py`
- Modify: `python/tests/conftest.py`

**Interfaces:**
- Produces (all in `tests/persistence_model.py`):
  - `UnitKey` — frozen union of `("entry", dir_token, name)`, `("data", token)`,
    `("meta", token, field)` tuples (plain tuples; a `NewType` alias is enough).
  - `ModelInode(token: int, kind: str, symlink_target: str | None)` — `kind` in
    `{"file", "directory", "symlink"}`.
  - `Unit(key, change, object_token, payload)` — `change` in
    `{"insert", "remove", "replace", "image", "mode", "xattr"}`; for data units `payload` is the
    **complete current logical byte image** (design §4.1); for meta units the mode int or xattr
    bytes.
  - `Event` variants (dataclasses): `Seed(world_digest, backup_id)`, `Mutation(units)`,
    `Barrier(covered: frozenset[UnitKey])`, `Commit(backup_id)`.
  - `RecordingCutBackend(inner, stream: Stream)` — the `Backend`-shaped recorder.
  - `Stream` — `events: list[Event]`, `backups: dict[int, bytes]` (backup id → db bytes),
    plus the live model tree it maintains.
  - `attach_store_sequencer(stream, monkeypatch, metadata_root)` — wraps `Store.transaction` so
    each successful exit appends `Commit` with a fresh SQLite backup (`sqlite3.connect(...).backup`)
    of `<metadata_root>/atoms.db`.
  - `record_scenario(entry, ingredients, monkeypatch, *, caught: bool = False) -> Stream` — seeds,
    snapshots event 0, runs the scenario through `run_clean`/`run_caught` with the recorder
    injected beneath the command's backend argument, and returns the closed stream.
- Consumes: Task 1-2's `Scenario`, `run_clean`, `run_caught`.

- [ ] **Step 1: Write the failing tests**

```python
# python/tests/test_persistence_model.py
"""Unit decomposition, coverage, replacement, and coalescing (design §4.1-§4.2)."""

from tests.persistence_model import record_scenario
from tests.exerciser import scenario


def test_recording_is_success_only_and_decomposes_renames(persistence_recording):
    stream = persistence_recording("minimal-move")
    renames = [
        event for event in stream.mutations()
        if {unit.change for unit in event.units} == {"remove", "insert"}
    ]
    assert renames, "the move's transfer_noclobber must decompose into remove+insert"
    remove, insert = sorted(renames[-1].units, key=lambda unit: unit.change != "remove")
    assert remove.key[0] == "entry" and insert.key[0] == "entry"
    assert remove.object_token == insert.object_token


def test_writes_coalesce_to_one_pending_image_per_inode(persistence_recording):
    stream = persistence_recording("minimal-create")
    data_keys = [
        unit.key for event in stream.mutations() for unit in event.units
        if unit.key[0] == "data"
    ]
    # However many write() calls streamed the payload, pending state holds one image.
    pending = stream.pending_before(stream.commit_index("e1-done"))
    images = [key for key in pending if key[0] == "data"]
    assert len(images) == len(set(images))


def test_flush_directory_covers_entries_and_directory_metadata(persistence_recording):
    stream = persistence_recording("minimal-mkdir")
    covered = set().union(*(e.covered for e in stream.barriers()))
    assert any(key[0] == "entry" for key in covered)
    assert any(key[0] == "meta" for key in covered)


def test_database_family_is_model_owned(persistence_recording):
    stream = persistence_recording("minimal-create")
    names = {
        unit.key[2] if len(unit.key) > 2 else unit.key[1]
        for event in stream.mutations() for unit in event.units
        if unit.key[0] == "entry"
    }
    assert not any(str(name).startswith("atoms.db") for name in names)


def test_each_store_commit_carries_a_backup(persistence_recording):
    stream = persistence_recording("minimal-create")
    commits = stream.commits()
    assert [c.backup_id for c in commits] == sorted({c.backup_id for c in commits})
    assert all(c.backup_id in stream.backups for c in commits)
```

`persistence_recording` is a new conftest fixture combining `coordinator_on` + `monkeypatch`:

```python
@pytest.fixture
def persistence_recording(coordinator_on, monkeypatch):
    def record(name: str, *, caught: bool = False):
        from tests.exerciser import scenario
        from tests.persistence_model import record_scenario

        return record_scenario(scenario(name), coordinator_on(), monkeypatch, caught=caught)

    return record
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_persistence_model.py -q`
fails on the missing module.

- [ ] **Step 3: Implement the model**

The recorder in full (the load-bearing logic; helper accessors `mutations()`, `barriers()`,
`commits()`, `pending_before(i)`, `commit_index(label)` are mechanical):

```python
# python/tests/persistence_model.py (core)
"""The persistence-cut model: units, recording, durable state (design §4)."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

DB_FAMILY = ("atoms.db", "atoms.db-wal", "atoms.db-shm", "atoms.db-journal")


@dataclass(frozen=True)
class Unit:
    key: tuple
    change: str
    object_token: int | None = None
    payload: bytes | int | None = None


# Event dataclasses: Seed, Mutation, Barrier, Commit — as in Interfaces.


class Stream:
    def __init__(self) -> None:
        self.events: list = []
        self.backups: dict[int, bytes] = {}
        self.tokens = iter(range(1, 1 << 30))
        self.fd_dir: dict[int, int] = {}      # fd -> directory token
        self.fd_obj: dict[int, int] = {}      # fd -> object token
        self.fd_offset: dict[int, int] = {}   # fd -> write offset
        self.entries: dict[tuple[int, str], int] = {}  # (dir token, name) -> object token
        self.images: dict[int, bytearray] = {}         # object token -> live image
        self.kinds: dict[int, str] = {}


class RecordingCutBackend:
    """Success-only recorder beneath the audited facade (design §4.1).

    Operands are captured before the delegated call, identities after; a failed
    delegated call appends nothing. Every non-mutating method delegates untouched
    except the open/close family, which maintains the fd tables.
    """

    def __init__(self, inner, stream: Stream) -> None:
        self._inner = inner
        self._stream = stream

    # --- opens maintain fd tables -------------------------------------------
    def open_root(self, path):
        fd = self._inner.open_root(path)
        token = self._stream.tokens.__next__() if fd not in self._stream.fd_dir else None
        # one token per distinct root path; keep a path->token map alongside
        self._register_directory(fd, path)
        return fd

    def open_child_directory(self, parent_fd, name):
        fd = self._inner.open_child_directory(parent_fd, name)
        self._register_child_directory(fd, parent_fd, name)
        return fd

    # create_exclusive / create_or_open / open_existing register fd_obj and, when
    # they create (create_exclusive always; create_or_open only when the model tree
    # shows (dir, name) absent — design §4.1), append the insert Mutation.

    def create_exclusive(self, parent_fd, name, mode):
        stream = self._stream
        parent = stream.fd_dir[parent_fd]
        fd = self._inner.create_exclusive(parent_fd, name, mode)
        token = next(stream.tokens)
        stream.kinds[token] = "file"
        stream.images[token] = bytearray()
        stream.entries[(parent, name)] = token
        stream.fd_obj[fd] = token
        stream.fd_offset[fd] = 0
        self._emit_mutation(
            Unit(("entry", parent, name), "insert", token),
            Unit(("meta", token, "mode"), "mode", token, mode),
        )
        return fd

    def write(self, fd, data):
        written = self._inner.write(fd, data)
        stream = self._stream
        token = stream.fd_obj[fd]
        offset = stream.fd_offset[fd]
        image = stream.images[token]
        image[offset:offset + written] = data[:written]
        stream.fd_offset[fd] = offset + written
        self._emit_mutation(
            Unit(("data", token), "image", token, bytes(image))
        )
        return written

    def transfer_noclobber(self, src_fd, src, dst_fd, dst):
        stream = self._stream
        src_dir, dst_dir = stream.fd_dir[src_fd], stream.fd_dir[dst_fd]
        self._inner.transfer_noclobber(src_fd, src, dst_fd, dst)
        token = stream.entries.pop((src_dir, src))
        stream.entries[(dst_dir, dst)] = token
        self._emit_mutation(
            Unit(("entry", src_dir, src), "remove", token),
            Unit(("entry", dst_dir, dst), "insert", token),
        )

    def flush_file(self, fd):
        self._inner.flush_file(fd)
        token = self._stream.fd_obj.get(fd)
        if token is None:  # a directory descriptor flushed as a file: cover its meta
            token = self._stream.fd_dir.get(fd)
        covered = frozenset(
            key for key in self._pending_keys()
            if key[0] in ("data", "meta") and key[1] == token
        )
        self._emit_barrier(covered)

    def flush_directory(self, fd):
        self._inner.flush_directory(fd)
        directory = self._stream.fd_dir[fd]
        covered = frozenset(
            key for key in self._pending_keys()
            if (key[0] == "entry" and key[1] == directory)
            or (key[0] == "meta" and key[1] == directory)
        )
        self._emit_barrier(covered)

    # exchange -> two "replace" units; link_anchor -> insert(existing token);
    # unlink_child/rmdir_child -> remove; mkdir_child -> new directory token,
    # insert + mode; symlink_child -> new symlink token with target; set_mode /
    # repair_entry_mode -> ("meta", token, "mode"); set_marker_xattr ->
    # ("meta", token, "xattr:" + name). close_fd/detach_fd pop the fd tables.
    # Database-family names in the metadata root are filtered in _emit_mutation:
    # a Unit whose entry name is in DB_FAMILY is dropped (design §4.1).
    # Everything else delegates verbatim via explicit methods (the protocol is
    # nominal in shape: implement all 22 names; the architecture suite pins them).
```

`attach_store_sequencer` wraps `Store.transaction` exactly as `execute_child._configure_store_cut`
wraps it (probe `tests/execute_child.py:48-66` and copy the patch shape), except the wrapper runs
the original to completion and then — success only — backs up
`sqlite3.connect(db_path)` into an in-memory copy (`conn.backup`), serializes with
`Connection.serialize()` (Python ≥3.11), stores it in `stream.backups`, and appends `Commit`.

`record_scenario` seeds the world, snapshots event 0 — the seed tree walk (excluding `DB_FAMILY`)
plus the initial backup if `atoms.db` exists, else backup id 0 maps to `None` meaning "no store
yet; reconstruction creates nothing" — then wraps `ingredients`'s backend in
`RecordingCutBackend` **before** handing it to `run_clean`/`run_caught`, so the recorder sits
beneath the facade the lease constructs.

- [ ] **Step 4: Run the tests and make sure they pass** — `uv run pytest
tests/test_persistence_model.py tests/test_exerciser.py -q`, then the full gates.

- [ ] **Step 5: Commit**

```bash
git add python/tests/persistence_model.py python/tests/test_persistence_model.py python/tests/conftest.py
git commit -m "test(cut-model): durability units, recording backend, store sequencer"
```

## Task 4: Durable-state derivation, reconstruction, and the fidelity self-check

**Files:**
- Modify: `python/tests/persistence_model.py`
- Modify: `python/tests/test_persistence_model.py`

**Interfaces:**
- Produces: `durable_state(stream, cut: int) -> WorldState` — the covered-units world plus the
  latest `Commit` backup at or before `cut`. `WorldState(tree, backup_id)` where `tree` maps
  each root-relative path to `("file", bytes, mode) | ("dir", mode) | ("symlink", target)`,
  with hard-link groups carried as `("link-group", group_id)` annotations.
- Produces: `apply_survivors(state: WorldState, stream, cut, survivors: frozenset[UnitKey])
  -> WorldState | Skip` — `Skip(reason: str)` for unrepresentable subsets (design §4.3).
- Produces: `reconstruct(state: WorldState, project_root: Path, metadata_root: Path) -> None` —
  writes the world onto the real filesystem; installs the backup as `atoms.db` **alone**
  (`sqlite3.Connection.deserialize` into a fresh file connection); reproduces file hard links
  with `os.link`; never writes any other `DB_FAMILY` name.
- Produces: `world_digest(project_root, metadata_root) -> str` — canonical digest of both trees
  (path, kind, mode, content hash, link-group structure; `.#~chain` **included** — the chain is
  world state here, unlike `_world()`'s skip, because chain reconciliation is under test).

- [ ] **Step 1: Write the failing tests**

```python
def test_full_durable_reconstruction_equals_the_live_final_world(
    persistence_recording, ext4_volume
):
    """Design §9's fidelity self-check, run per scenario."""
    from tests.persistence_model import (
        apply_survivors, durable_state, pending_keys_at, reconstruct, world_digest,
    )

    for name in ("minimal-create", "minimal-move", "minimal-mkdir"):
        stream = persistence_recording(name)
        end = len(stream.events)
        state = apply_survivors(
            durable_state(stream, end), stream, end, pending_keys_at(stream, end)
        )
        project = ext4_volume / f"recon-{name}-p"
        metadata = ext4_volume / f"recon-{name}-m"
        project.mkdir(), metadata.mkdir()
        reconstruct(state, project, metadata)
        assert world_digest(project, metadata) == stream.final_world_digest


def test_reconstruction_preserves_hard_link_relations(persistence_recording, ext4_volume):
    """The move's anchor and destination must share one inode after reconstruction."""
    from tests.persistence_model import durable_state, reconstruct
    import os

    stream = persistence_recording("minimal-move")
    # cut chosen between the anchor link and the DONE commit; exact index derived
    # from the stream by locating the link_anchor mutation
    ...
```

(The second test's cut-locating helper `stream.index_after(change="insert", link=True)` is added
alongside; assert `os.stat(dest).st_ino == os.stat(anchor).st_ino` in the reconstruction.)

- [ ] **Step 2: Run to verify failure** — the functions do not exist.

- [ ] **Step 3: Implement**

`durable_state` folds events 0..cut: seed tree, then each `Mutation`'s units enter a pending map
(keyed replacement — design §4.1), each `Barrier` moves its covered keys into the durable tree,
each `Commit` advances `backup_id`. `apply_survivors` applies the chosen pending keys **in stream
order** against the durable tree; a `remove`/`replace` whose target entry is absent, or an
`image`/`mode` whose object token has no durable or included name anywhere (an unreachable inode
carries no observable state), returns `Skip(reason)` with reasons drawn from the closed set
`{"remove-without-target", "replace-without-target", "orphan-object-state"}`.
`record_scenario` computes `stream.final_world_digest = world_digest(project, metadata)` after the
run, before returning.

- [ ] **Step 4: Run and pass** — `uv run pytest tests/test_persistence_model.py -q` plus gates.

- [ ] **Step 5: Commit**

```bash
git add python/tests/persistence_model.py python/tests/test_persistence_model.py
git commit -m "test(cut-model): durable-state derivation, reconstruction, fidelity self-check"
```

## Task 5: Cut and survivor enumeration with skip accounting

**Files:**
- Modify: `python/tests/persistence_model.py`
- Modify: `python/tests/test_persistence_model.py`

**Interfaces:**
- Produces: `enumerate_cells(stream, *, pending_cap: int = 12) -> Iterator[Cell]` where
  `Cell(cut: int, survivors: frozenset[UnitKey], state: WorldState)`; deduplicates identical
  `(state digest, backup_id)` worlds; raises `PendingCapExceeded` (loud, never sampling) past
  the cap; returns alongside a `SweepAccounting(cells: int, deduped: int,
  skips: dict[str, int])`.
- Produces: `named_tuples(stream) -> dict[str, Cell]` — locates `dual-name-forward`,
  `anchor-only-forward` in a move scenario's stream (and the reverse pair in a caught-rollback
  move stream): the cut after `transfer_noclobber` with survivors `{insert}` (dual-name) and
  `{remove}` (anchor-only). Raises `KeyError` if a required tuple was skipped or never generated
  (design §4.3: generated, never skipped).

- [ ] **Step 1: Write the failing tests**

```python
def test_move_generates_all_named_forward_tuples(persistence_recording):
    from tests.persistence_model import named_tuples

    stream = persistence_recording("minimal-move")
    cells = named_tuples(stream)
    assert {"dual-name-forward", "anchor-only-forward"} <= cells.keys()


def test_reverse_move_tuples_come_from_the_caught_stream(persistence_recording):
    from tests.persistence_model import named_tuples

    stream = persistence_recording("caught-rollback-move", caught=True)
    cells = named_tuples(stream)
    assert {"dual-name-reverse", "anchor-only-reverse"} <= cells.keys()


def test_enumeration_counts_and_reports_skips_by_reason(persistence_recording):
    from tests.persistence_model import enumerate_cells

    stream = persistence_recording("minimal-create")
    cells, accounting = enumerate_cells(stream)
    assert accounting.cells > 0
    assert set(accounting.skips) <= {
        "remove-without-target", "replace-without-target", "orphan-object-state",
    }


def test_the_pending_cap_fails_loud(persistence_recording):
    import pytest
    from tests.persistence_model import PendingCapExceeded, enumerate_cells

    stream = persistence_recording("corpus-write")
    with pytest.raises(PendingCapExceeded):
        list(enumerate_cells(stream, pending_cap=0))
```

A `caught-rollback-move` scenario (a move whose *second* effect fails, so rollback re-moves the
landed move — producing `UNDO_STARTED` reverse traffic) joins `SCENARIOS` in this task; its
builder composes `move_spec`'s effect followed by a failing `replace_file` occupant, and its
`inject_failure` targets the second effect.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** — `enumerate_cells` walks every cut index; at each, computes pending
keys, iterates `itertools.chain.from_iterable(combinations(keys, r) for r in range(len(keys)+1))`,
applies `apply_survivors`, dedupes on `(digest(state.tree), state.backup_id)`. `named_tuples`
locates the move's transfer mutation event(s) by shape (a two-unit remove+insert sharing an
object token whose stream also carries a `link_anchor` insert of that token) and pulls the two
single-survivor cells at the cut immediately after; direction comes from whether the mutation
precedes the first `UNDO`-era commit (probe: the stream's `Commit` labels are positional —
carry the `STORE_BARRIERS` label onto `Commit` events in `attach_store_sequencer` by counting,
so `commit_index("e1-done")` and friends work and direction is "after `applied`" vs before).

- [ ] **Step 4: Run and pass**, full gates.

- [ ] **Step 5: Commit**

```bash
git add python/tests/persistence_model.py python/tests/test_persistence_model.py python/tests/exerciser.py
git commit -m "test(cut-model): cell enumeration, skip accounting, named move tuples"
```

## Task 6: The cell runner and the A3 agreement on minimal scenarios

**Files:**
- Create: `python/tests/test_persistence_cut_matrix.py`
- Modify: `python/tests/persistence_model.py` (the runner)
- Modify: `python/tests/conftest.py`

**Interfaces:**
- Produces: `run_cell(cell, stream, ext4_volume, storage, monkeypatch) -> CellResult` in
  `persistence_model.py`:
  1. reconstructs into fresh roots under `ext4_volume`;
  2. applies the scenario's `drift` callable if the cell family requests it;
  3. patches `atoms.coordinator.recover.classify_recovery` with a capturing spy (first
     invocation's `(snapshot, plan)`), patches `root.CERTIFIED_ALLOWLIST` via
     `build_test_allowlist`, and enters `root._recovery_lease` — recovery runs at entry;
  4. reads the durable projection with `_durable_projection`'s exact shape **plus
     `halt_diagnostic`** (design §5): `(state, committed, rollback_result, halt_diagnostic,
     journals, active is not None)`;
  5. computes the model projection via `apply_recovery_plan` on the captured pair;
  6. runs the **mandatory second pass** — a second fresh lease entry — and asserts the canonical
     durable projection and `world_digest` are unchanged;
  7. runs the side assertions A3 does not model: the chain parses and its
     registration/settlement pairing holds (reuse `atoms.chain.read`), no
     `.#~<txid>.*` scratch survives a terminal state, `work/` slots are empty, and no
     unreferenced blob remains after reclamation;
  8. returns `CellResult(agrees, halted, projection, model_projection, world, counts)` —
     `world` is the post-recovery tree mapping of Task 4's `WorldState.tree` shape, consumed by
     Task 8's directed repair assertions.
- Cells with **no durable record** (cuts before `prepared`) skip steps 3-5's A3 comparison and
  instead assert: empty store (or none), world equals the seed world with external state
  preserved, scratch and unreferenced blobs reclaimed.
- Produces: `SweepReport(cells, deduped, skips, disagreements, second_pass_violations,
  side_assertion_failures, named_tuple_cells_ran, preserved_drift_cells, subprocess_cells,
  subprocess_disagreements, designated_failures)` — later tasks only *fill* fields this task
  creates empty (`designated_failures = ()` on healthy sweeps).
- Produces (conftest): fixture `cut_matrix(persistence_recording, ext4_volume,
  test_storage_profile, monkeypatch)` returning a `Sweeper` object:
  `__call__(scenario_name, *, caught=False, drift=False, subprocess_subset=False,
  sabotage: str | None = None) -> SweepReport`, and
  `named_cell(scenario_name, tuple_name, *, caught=False) -> NamedCell` where
  `NamedCell.result` is the tuple cell's `CellResult` (consumed by Task 8).

- [ ] **Step 1: Write the failing test**

```python
# python/tests/test_persistence_cut_matrix.py
"""The persistence-cut sweep: A3 agreement over every reconstructible survivor (design §5)."""

import pytest

MINIMAL = ("minimal-create", "minimal-replace", "minimal-delete", "minimal-move", "minimal-mkdir")


@pytest.mark.parametrize("name", MINIMAL)
def test_every_cell_of_the_minimal_scenarios_agrees_with_a3(name, cut_matrix):
    report = cut_matrix(name)
    assert report.cells > 0
    assert report.disagreements == ()
    assert report.second_pass_violations == ()
    assert report.side_assertion_failures == ()
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement `run_cell` and the sweep** as specified in Interfaces. The A3
comparison follows `test_coordinator_conformance.py`'s two projections verbatim, widened with
`halt_diagnostic`; a cell where A3 emits `HaltPlan` asserts the executor's persisted diagnostic
equals the plan's token-free diagnostic and that the second pass preserves it exactly. Identity
comparisons on captured snapshots use `reallocate_joint_identities` before equality.

- [ ] **Step 4: Run and pass.** This is the first big sweep; record its wall-clock. Expected
rough magnitude from the design's analysis: tens of cuts × small survivor sets per minimal
scenario — hundreds of cells, seconds-to-a-minute each scenario. **Print the per-scenario
`SweepReport` counts in the pytest header (a `-s` run) and paste them into the A8b sizing note in
Task 10's status prose.** If a minimal scenario exceeds ~5,000 cells or ~120 s, stop and report —
the model or the engine changed shape.

- [ ] **Step 5: Commit**

```bash
git add python/tests/test_persistence_cut_matrix.py python/tests/persistence_model.py python/tests/conftest.py
git commit -m "test(cut-matrix): per-cell recovery, A3 agreement, second pass, side assertions"
```

## Task 7: The full §13.4 matrix — compound sweep, placements, SIGKILL extension

**Files:**
- Modify: `python/tests/test_persistence_cut_matrix.py`
- Modify: `python/tests/execute_child.py`
- Modify: `python/tests/test_exerciser.py`

**Interfaces:**
- Produces (execute_child): config keys `{"scenario": <name>}` — build spec/payloads/seed from
  `tests.exerciser` instead of `_spec`; `{"projection": true}` — after the outcome, reopen the
  store read-only and print the canonical durable projection (the Task 6 shape) into the JSON
  result, so clean/caught **subprocess placement** cells return their captured projection
  (design §6: the whole cell runs in the child).
- Produces: the persistence-cut **subprocess placement subset** — for each scenario: every named
  tuple cell, every A3-halt cell, and the first cell after each `STORE_BARRIERS` label — run via
  `tests.coordinator_child` against the reconstructed roots, asserting the persisted token-free
  halt diagnostic exactly and the projection up to the child's serialization.
- Produces: the SIGKILL arm over `corpus-write` and `archive-move` — rehearsal via
  `{"record": True, "scenario": name}`, then a kill at each recorded flush/exchange/transfer
  event, then `_recover()` twice — the existing `_assert_terminal` contract, unchanged, applied
  to exerciser scenarios.

- [ ] **Step 1: Write the failing tests**

```python
def test_compound_scenarios_sweep_clean(cut_matrix):
    for name in ("corpus-write", "archive-move", "caught-rollback", "caught-rollback-move"):
        report = cut_matrix(name, caught=name.startswith("caught"))
        assert report.disagreements == ()
        assert report.named_tuple_cells_ran > 0


def test_drift_cells_preserve_external_blockers(cut_matrix):
    report = cut_matrix("drift-blocker", drift=True)
    assert report.disagreements == ()
    assert report.preserved_drift_cells > 0


def test_subprocess_placement_matches_in_process(cut_matrix):
    report = cut_matrix("minimal-move", subprocess_subset=True)
    assert report.subprocess_cells > 0
    assert report.subprocess_disagreements == ()


def test_clean_and_caught_whole_cell_subprocess_placement(exerciser_child):
    for name, family in (("minimal-create", "commit"), ("caught-rollback", "rollback")):
        projection = exerciser_child(name)
        assert projection["active"] is False
        assert (projection["state"] == "COMMITTED") == (family == "commit")


def test_sigkill_arm_covers_the_compound_scenarios(exerciser_kill_matrix):
    for name in ("corpus-write", "archive-move"):
        exerciser_kill_matrix(name)  # asserts _assert_terminal internally per cut
```

(`exerciser_child` and `exerciser_kill_matrix` are conftest fixtures wrapping the extended
`execute_child` / the kill-matrix helpers; the kill fixture reuses `_child`, `_recover`,
`_world`, `_assert_terminal` by importing them from `tests.test_coordinator_kill_matrix`.)

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement** — extend `execute_child.main` where `_spec(name)` is chosen:

```python
    if "scenario" in config:
        from tests.exerciser import scenario as exerciser_scenario

        entry = exerciser_scenario(config["scenario"])
        spec = entry.build_spec()
        payloads = entry.payloads()
        entry.seed_world(Path(project_root))
    else:
        spec = _spec(config["variant"])
        payloads = _payloads(config["variant"])
```

and after the outcome, when `config.get("projection")`, print the projection dict read through a
fresh `bind_project_volume`/`open_store` (copy `_durable_projection`'s body). The sweep's
`subprocess_subset` selection is **declared, not sampled** (design §8): the subset rule is stated
in `run_cell`'s docstring and its size asserted nonzero per scenario.

- [ ] **Step 4: Run and pass.** Record the full-suite wall-clock. If the compound sweeps push the
suite past ~10 minutes total, mark the compound-scenario sweep tests `@pytest.mark.slow` and
register the marker in `pyproject.toml` — an explicit tier, with the minimal sweeps always-on
(design §8's plan-time cost decision, made here with the measured numbers, recorded in the
commit message).

- [ ] **Step 5: Commit**

```bash
git add python/tests/test_persistence_cut_matrix.py python/tests/execute_child.py python/tests/test_exerciser.py python/tests/conftest.py
git commit -m "test(cut-matrix): compound sweeps, subprocess placements, SIGKILL extension"
```

## Task 8: The named directed tuple tests and the §9.5 identity injection

**Files:**
- Modify: `python/tests/test_persistence_cut_matrix.py`

**Interfaces:**
- Consumes: Task 5's `named_tuples`, Task 6's `run_cell`.
- Produces: four directed tests asserting the designed repair (authority §9.4) per named tuple,
  and the §9.5 identity-injected executor test.

- [ ] **Step 1: Write the failing tests**

```python
def test_dual_name_tuples_repair_by_removing_the_destination(cut_matrix):
    for direction, caught in (("forward", False), ("reverse", True)):
        cell = cut_matrix.named_cell(
            "minimal-move" if direction == "forward" else "caught-rollback-move",
            f"dual-name-{direction}", caught=caught,
        )
        result = cell.result
        # repaired to pre-state: source present, destination absent, anchor cleaned
        assert result.world["source.txt"][0] == "file"
        assert "destination.txt" not in result.world


def test_anchor_only_tuples_repair_by_restoring_the_source(cut_matrix):
    for direction, caught in (("forward", False), ("reverse", True)):
        cell = cut_matrix.named_cell(
            "minimal-move" if direction == "forward" else "caught-rollback-move",
            f"anchor-only-{direction}", caught=caught,
        )
        assert cell.result.world["source.txt"][0] == "file"
        assert "destination.txt" not in cell.result.world


def test_same_inode_work_survivor_is_landed_not_blocker(ext4_volume, coordinator_on, monkeypatch):
    """Design §4.5: the §9.5 tuple at the observation seam.

    The physical world holds the published live directory and a distinct empty
    work/ survivor directory; the injected observation reports both with one
    identity. Recovery must remove the stale work/ name and treat the effect as
    landed — never misread it as a blocker. The tuple only coexists with an empty
    published directory, so this world models exactly the reachable state.
    """
    # Build: run minimal-mkdir recorded; take the cut after the live-parent flush
    # with the work-removal unit NOT surviving; reconstruct — this yields live d
    # and work/<txid>/.#~....work as *separate* inodes (reconstruction cannot
    # hard-link directories). Then patch the observation seam so both report the
    # same identity token, and run recovery.
    ...
    # Assertions: recovery converges (no halt); live "d" survives with its declared
    # mode; the work slot is empty; the second pass is NO_RECOVERY-clean.
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement the injection.** The seam: `atoms.fs.observe.Observation` mints
identity tokens at `_pin` (`fs/observe.py:302`). Patch at the *recovery assembly* level instead —
`coordinator/recover.py`'s observation collection — replacing the two observations' identity
values for the live directory and the work survivor with one shared token after observation and
before classification (a `monkeypatch.setattr` wrapper around `recover`'s snapshot-building
function; probe `build_recovery_snapshot`'s call site in `recover.py` around `:605-700` and wrap
the function it calls with a post-processor). The directed test asserts the wrapper actually
fired (a call counter), so the test cannot pass vacuously if the seam moves.

- [ ] **Step 4: Run and pass**, full gates.

- [ ] **Step 5: Commit**

```bash
git add python/tests/test_persistence_cut_matrix.py
git commit -m "test(cut-matrix): directed 9.4 tuple repairs and the 9.5 identity injection"
```

## Task 9: The five sabotage arms

**Files:**
- Modify: `python/tests/test_persistence_cut_matrix.py`

**Interfaces:**
- Consumes: everything above.
- Produces: five tests, one per design-§9 arm, each suppressing one barrier via monkeypatch
  during a fresh recorded run and asserting **its designated failure and only by that check**
  (design §9: never "any cell differs").

- [ ] **Step 1: Write the failing tests** — the five arms, exactly as design §9 names them:

```python
def _suppress(monkeypatch, module, name):
    original = getattr(module, name)
    monkeypatch.setattr(module, name, lambda *a, **k: None)
    return original


def test_sabotage_1_blob_flush_before_prepared(cut_matrix, monkeypatch):
    """Suppressed blob-directory flush -> the PREPARED-cut cell reports a
    record-referenced blob missing from the reconstructed store."""
    report = cut_matrix("minimal-create", sabotage="blob-flush")
    assert report.designated_failures == ("blob-integrity",)


def test_sabotage_2_mutation_durable_before_done(cut_matrix):
    report = cut_matrix("minimal-create", sabotage="pre-done-flush")
    assert "done-meets-pre-state-halt" in report.designated_failures


def test_sabotage_3_move_destination_flush(cut_matrix):
    # Reordering the two 9.4 flushes is deliberately NOT the arm (design §9):
    # either order yields an attributable repairable tuple; the load-bearing
    # property is both flushes preceding DONE.
    report = cut_matrix("minimal-move", sabotage="move-destination-flush")
    assert "done-meets-absent-destination-halt" in report.designated_failures


def test_sabotage_4_mkdir_live_parent_flush(cut_matrix):
    report = cut_matrix("minimal-mkdir", sabotage="live-parent-flush")
    assert "done-meets-absent-directory-halt" in report.designated_failures


def test_sabotage_5_committed_decision(cut_matrix):
    """Returned-outcome permanence: a COMMITTED return must never resolve
    ROLLED_BACK on recovery."""
    report = cut_matrix("minimal-create", sabotage="committed-decision")
    assert "returned-outcome-permanence" in report.designated_failures


def test_unsabotaged_sweeps_raise_no_designated_failure(cut_matrix):
    for name in ("minimal-create", "minimal-move", "minimal-mkdir"):
        assert cut_matrix(name).designated_failures == ()
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement the sabotage hooks in the sweep fixture.** Each `sabotage=` value maps
to a targeted suppression applied during the *recording* run:

| arm | suppression | designated check in `run_cell` |
| --- | --- | --- |
| `blob-flush` | the recording backend swallows the `flush_directory` covering `blobs/sha256` (match by directory token of the blobs dir) | at the cut **at** the `prepared` commit with blob data units dropped: every blob digest the record references resolves in the reconstructed store — else `blob-integrity` |
| `pre-done-flush` | swallow the effect's parent-directory flush between `e1-started` and `e1-done` | the cut **at** `e1-done` with mutation units dropped must halt (`DONE` meets pre-state); convergence there is the failure |
| `move-destination-flush` | swallow `effects/move.py:84`'s destination flush (the first post-transfer `flush_directory` on the destination token) | the cut at the move's `DONE` with the insertion dropped must halt |
| `live-parent-flush` | swallow `create_directory.py:64`'s live-parent flush | the cut at the mkdir's `DONE` with the live insertion dropped and the work removal durable must halt |
| `committed-decision` | `attach_store_sequencer` drops the `committed` commit's backup advance (the store still commits, but the model's durable metadata stays at `applied` — modeling return-before-durable) | any cell after committed cleanup: if the recorded run returned `COMMITTED`, its recovery must not project `ROLLED_BACK` — else `returned-outcome-permanence` |

Suppression happens in the *model's* view where noted (arms 1-4 swallow the barrier event so the
covered units stay pending; the real filesystem still flushed — the model is what decides
survivor worlds, so the designated cell materializes exactly the world the missing barrier
permits). `designated_failures` is a tuple on `SweepReport`, empty on healthy sweeps.

- [ ] **Step 4: Run and pass**, full gates.

- [ ] **Step 5: Commit**

```bash
git add python/tests/test_persistence_cut_matrix.py python/tests/persistence_model.py python/tests/conftest.py
git commit -m "test(cut-matrix): five sabotage arms with designated failures"
```

## Task 10: A8a status flip

**Files:**
- Modify: `python/tests/test_docs_status.py:31,34`
- Modify: `AGENTS.md`, `README.md`
- Modify: `docs/plans/2026-08-14-a8-persistence-cut-and-certification-design.md` (status header)
- Modify: any live doc `test_docs_status.py` flags

**Steps:**

- [ ] **Step 1:** Split the tuple: `STAGES = (..., "A7a", "A7b", "A8a", "A8b", "A9")`;
  `FIRST_UNIMPLEMENTED = "A8b"`.
- [ ] **Step 2:** Run `uv run pytest tests/test_docs_status.py -q`; it will name every document
  whose status prose must change. Fix each: the design's header gains
  "A8a — exerciser, cut model, matrices — implemented <date>; A8b unimplemented", `AGENTS.md` and
  `README.md` list A8a, and the measured sweep counts from Tasks 6-7 are recorded in the design
  header's implementation note.
- [ ] **Step 3:** Full gates: `uv run pytest && uv run ruff check && uv run pyright`.
- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "docs(a8a): move the roadmap boundary to A8b"
```
