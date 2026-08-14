# Plan A3 — Executable recovery reference model

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build A3's pure production recovery authority: validated logical snapshots, exhaustive
journal and variant classification, frozen semantic plans, fresh-step authorization, and a reducer
whose fixed points make recovery convergence executable.

**Architecture:** Add a focused `atoms.core.recovery` package. Public frozen values and five pure
operations live at the package boundary; internal modules separately own snapshot validation,
journal/frontier reconstruction, variant tables, transaction planning, reduction, and authorization.
A7 must consume these decisions later and may not duplicate them.

**Tech Stack:** Python ≥3.11, stdlib only (`dataclasses`, `enum`, `collections`), managed with `uv`;
`ruff` + `pyright` + `pytest`. All commands run from `python/`.

**Design authority:**
[`2026-07-28-a3-recovery-reference-model-design.md`](2026-07-28-a3-recovery-reference-model-design.md),
which refines [`2026-07-23-recoverable-fs-effect-engine-design.md`](2026-07-23-recoverable-fs-effect-engine-design.md)
§§5.4, 7.2, 8.1–8.4, 9, 10, and 13.1. Where they disagree, the authority design wins.

**Status:** Implemented on 2026-07-29. A7b–A8 remain unimplemented; A7a writes engine
bookkeeping at the reserved `.#~chain/` leaf, while A7b has not yet executed an effect against a project path.

## Global Constraints

- **Python floor:** `requires-python = ">=3.11"`. Do not change it.
- **Runtime dependencies:** stdlib only. Do not add a package or dev dependency.
- **Layout:** implementation under `python/src/atoms/core/recovery/`; tests under `python/tests/`.
- **Pure boundary:** no filesystem calls, SQLite, absolute paths, descriptors, concrete scratch leaves,
  platform probes, clocks, randomness, or durability barriers.
- **Closed model:** exact runtime types define every union. Subclasses are not members.
- **Enums:** use plain `Enum` with string `.value` members. Do not use `str, Enum`; enum members must
  not compare equal to caller-supplied strings.
- **Factory control:** `RecoverySnapshot`, `RecoveryPlan` variants, and `AuthorizedStep` refuse ordinary
  construction and `dataclasses.replace`.
- **Input proof:** production-facing recovery accepts exact factory-issued `CompiledSpec`; never raw
  `TransactionSpec`.
- **Error boundary:** malformed internal values raise `ProtocolError`; semantic contradictions yield
  `HaltPlan`; unexpected implementation exceptions propagate unchanged.
- **Diagnostics:** `HaltDiagnostic` is token-free and persistable. It stores named-slot identity
  relations, never `EntryIdentity` tokens or arbitrary token `repr`.
- **Identity universes:** one coherent observation reuses one token for every slot naming the same
  entry and uses distinct tokens for distinct entries. Every fresh observation regenerates all tokens;
  cross-observation comparison uses non-identity fields plus named-slot identity partitions.
- **Identity conservation:** classifiers, plan builders, diagnostics, authorization, and the reducer
  never allocate `EntryIdentity`; they only retain, move, compare, or project observation tokens.
- **Ordering:** compiled effect order is authoritative. `dependencies` never schedule recovery.
- **Mutation discipline:** use TDD for every task. Run the named failing test before production edits.
- **Commits:** no AI-attribution trailers. Documentation paths use `~/d/atoms/...`, never host-specific
  absolute paths.

## File map

| File | Responsibility |
| --- | --- |
| `python/src/atoms/core/recovery/__init__.py` | The five public operations and public frozen values |
| `python/src/atoms/core/recovery/model.py` | Closed enums, observations, identities, diagnostics |
| `python/src/atoms/core/recovery/snapshot.py` | Logical topology validation and `build_recovery_snapshot` |
| `python/src/atoms/core/recovery/plan.py` | Guarded plan/step values and internal plan factories |
| `python/src/atoms/core/recovery/journal.py` | Transaction-state matrix, journal languages, path frontiers |
| `python/src/atoms/core/recovery/variants.py` | Five joint effect classifiers |
| `python/src/atoms/core/recovery/diagnostics.py` | Shared token-free tuple and identity projections |
| `python/src/atoms/core/recovery/classifier.py` | All-evidence-first transaction planning |
| `python/src/atoms/core/recovery/reducer.py` | Prefix and full abstract plan reduction |
| `python/src/atoms/core/recovery/authorization.py` | Fresh observation comparison and authorized-step factory |
| `python/tests/recovery_support.py` | Valid compiled specs, topologies, snapshots, identity partitions |
| `python/tests/conftest.py` | Explicit pytest fixture registry over `recovery_support` factories |
| `python/tests/test_recovery_model.py` | Closed/frozen/token-free value tests |
| `python/tests/test_recovery_snapshot.py` | Snapshot/topology factory validation |
| `python/tests/test_recovery_plan.py` | Guarded plan/step construction |
| `python/tests/test_recovery_journal.py` | Journal matrix and frontier reconstruction |
| `python/tests/test_recovery_variants_files.py` | Replace/create-file tables |
| `python/tests/test_recovery_variants_paths.py` | Delete/move/create-directory tables |
| `python/tests/test_recovery_classifier.py` | Transaction-wide disposition and step ordering |
| `python/tests/test_recovery_reducer.py` | Prefix/full reduction and fixed points |
| `python/tests/test_recovery_authorization.py` | Fresh-step authorization and prefix-bound halt |
| `python/tests/test_recovery_properties.py` | Exhaustive finite properties and mutations |
| `python/tests/test_recovery_architecture.py` | Import, dependency, and boundary checks |

## Design-to-task map

| Design contract | Task |
| --- | --- |
| Closed state/evidence vocabulary, token-free diagnostics | 1 |
| Resolved topology and validating snapshot factory | 2 |
| Guarded plan, step, and authorization values | 3 |
| Prefix/full abstract reducer kernel | 4 |
| State × commit × active matrix, journal languages, frontiers | 5 |
| `ReplaceFile`, `CreateFileNoClobber` | 6 |
| `DeletePath`, `MoveNoClobber`, `CreateDirectory` | 7 |
| Complete transaction classification, reducer-backed planning, and halt reasons | 8 |
| Fresh observation authorization | 9 |
| Exhaustive generators, architecture checks, status sync | 10 |

## Fixture registry

`python/tests/conftest.py` is created in Task 2 and extended by the task that first needs each
fixture. It contains only thin `@pytest.fixture` adapters over explicit factories in
`tests.recovery_support`; the factory is where construction logic lives and can also be called
directly by non-pytest checks. No test may name a fixture absent from this registry.

| Fixture | Added | Exact contract |
| --- | --- | --- |
| `terminal_snapshot` | Task 4 | active, validated `ROLLED_BACK` snapshot accepted by `DetachActive` |
| `reducer_step_cases` | Task 4 | callable mapping six step-case names to `(source, step, expected)` |
| `replace_started_case` | Task 4 | callable returning a STARTED replace snapshot and its `UNDO_STARTED` transition |
| `replace_transform_case` | Task 4 | callable returning a STARTED replace whose tuple moves live off `pre` |
| `three_effect_snapshot` | Task 5 | callable returning a three-effect snapshot for a state/journal vector |
| `halted_authority_snapshot` | Task 5 | callable halted snapshot with matching frozen vector and active flag |
| `replace_case` | Task 6 | callable building live/staging/journal/frontiers, optionally committed |
| `noop_replace_case` | Task 6 | same as `replace_case`, with exact `pre == post` |
| `create_file_case` | Task 6 | callable building live/staging/journal/frontiers, optionally committed |
| `pending_drift_case` | Task 6 | callable returning a pending create with external live drift |
| `pending_clean_case` | Task 6 | callable returning a pending create at its initial tuple |
| `pending_scratch_case` | Task 6 | callable returning a pending create with surviving staging |
| `undone_drift_case` | Task 6 | callable returning an undone create with non-initial live evidence |
| `delete_case` | Task 7 | callable building live/tombstone/journal/frontiers, optionally committed |
| `move_case` | Task 7 | callable building move states/identity partition, optionally committed |
| `directory_case` | Task 7 | callable building directory states/occupancy, optionally committed |
| `two_effect_snapshot` | Task 8 | callable selecting a named decision case for each of two effects |
| `committed_snapshot` | Task 8 | committed all-DONE snapshot with removable terminal scratch |
| `committed_repeated_replace_snapshot` | Task 8 | committed repeated-path `F→G→H` replace chain with retained `F`/`G` staging |
| `committed_superseded_cleanup_case` | Task 8 | callable committed Delete→Create and Move→Replace cases with superseded persistent evidence |
| `prepared_drift_snapshot` | Task 8 | prepared all-PENDING snapshot with external live drift |
| `halted_snapshot` | Task 8 | validated HALTED snapshot carrying its frozen first-halt diagnostic |
| `recovery_case` | Task 8 | callable returning named restored/refused/committed/halt fixed-point sources |
| `committed_halt_source` | Task 8 | committed source whose cleanup tuple requires a halt |
| `repeated_path_mid_plan_halt` | Task 8 | earlier effect halts after a later repair projection |
| `snapshot_pair_differing_only_dependencies` | Task 8 | dependency-only compiled difference; observations shared |
| `classifier_plan` | Task 9 | action plan containing at least one filesystem-mutating step |
| `generated_snapshots` | Task 10 | finite generator exposing every method Task 10 names |
| `identity_case` | Task 10 | pair of snapshots differing only by a consistent identity-token alpha-renaming |
| `halt_restart_case` | Task 10 | pair of halted snapshots with equal diagnostics and regenerated observation tokens |

Each task's fixture step must add both the support factory and this exact adapter shape:

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

The Task 10 architecture test enumerates test function signatures and fails if any non-builtin pytest
argument is absent from `conftest.py`; this keeps the registry complete as tests evolve.

---

### Task 1: Closed recovery values and token-free diagnostics

Create the closed vocabulary before any classifier. Identity tokens exist only for equality inside one
snapshot; diagnostic projections contain states and named-slot relations but cannot contain tokens.

**Files:**
- Create: `python/src/atoms/core/recovery/__init__.py`
- Create: `python/src/atoms/core/recovery/model.py`
- Test: `python/tests/test_recovery_model.py`

**Interfaces:**
- Consumes: `FileState`, `DirectoryState`, `SymlinkState`, `PathState` from
  `atoms.core.fingerprint`; `RelPath` from `atoms.core.effects`.
- Produces:
  - closed enums `TransactionState`, `CommitDecision`, `JournalState`, `RollbackResult`,
    `HaltReason`, `ScratchRole`, `FileBuildRelation`, `IdentityRelation`, `OperatorAction`;
  - `EntryIdentity`, `ObservedAbsent`, `ObservedFile`, `ObservedSymlink`,
    `ObservedDirectory`, `ObservedEntry`;
  - `EffectJournalState`, `PersistentObservation`, `ScratchObservation`;
  - token-free `DiagnosticEntry`, `DiagnosticIdentityRelation`, `HaltDiagnostic`;
  - `OBSERVED_ABSENT`.

- [ ] **Step 1: Write the failing closed-value tests**

Create `python/tests/test_recovery_model.py`:

```python
from dataclasses import FrozenInstanceError, fields

import pytest

from atoms.core.recovery import (
    EntryIdentity,
    HaltDiagnostic,
    HaltReason,
    IdentityRelation,
    JournalState,
    ObservedFile,
    TransactionState,
)
from tests.support import F


def test_state_and_reason_values_are_closed_enums_with_string_values():
    assert [state.value for state in TransactionState] == [
        "prepared",
        "applying",
        "applied",
        "committed",
        "rolling_back",
        "rolled_back",
        "halted",
    ]
    assert HaltReason.ACTIVE_BINDING_MISSING.value == "active_binding_missing"
    assert HaltReason.COMMIT_DECISION_CONFLICT.value == "commit_decision_conflict"
    assert TransactionState.PREPARED != "prepared"


def test_entry_identity_is_opaque_snapshot_local_and_repr_safe():
    first = EntryIdentity()
    same = first
    other = EntryIdentity()
    assert first == same
    assert first != other
    assert repr(first) == "<entry-identity>"
    assert fields(EntryIdentity)[0].repr is False


def test_observations_are_frozen():
    observed = ObservedFile(state=F, identity=EntryIdentity())
    with pytest.raises(FrozenInstanceError):
        observed.state = F  # type: ignore[misc]


def test_halt_diagnostic_has_no_identity_token_field():
    field_types = {field.name: str(field.type) for field in fields(HaltDiagnostic)}
    assert all("EntryIdentity" not in field_type for field_type in field_types.values())
    assert "identity_relations" in field_types
    assert "projected_journals" in field_types
    assert IdentityRelation.SAME.value == "same"
    assert JournalState.STARTED.value == "started"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_recovery_model.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'atoms.core.recovery'`.

- [ ] **Step 3: Implement the closed model**

Create `python/src/atoms/core/recovery/model.py` with exact string enums and frozen dataclasses:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from atoms.core.effects import RelPath
from atoms.core.fingerprint import DirectoryState, FileState, PathState, SymlinkState


class TransactionState(Enum):
    PREPARED = "prepared"
    APPLYING = "applying"
    APPLIED = "applied"
    COMMITTED = "committed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    HALTED = "halted"


class CommitDecision(Enum):
    UNCOMMITTED = "uncommitted"
    COMMITTED = "committed"


class JournalState(Enum):
    PENDING = "pending"
    STARTED = "started"
    DONE = "done"
    UNDO_STARTED = "undo_started"
    UNDONE = "undone"


class RollbackResult(Enum):
    RESTORED = "restored"
    EXTERNAL_DRIFT_PRESERVED = "external_drift_preserved"


class HaltReason(Enum):
    JOURNAL_TOPOLOGY_INVALID = "journal_topology_invalid"
    COMMIT_DECISION_CONFLICT = "commit_decision_conflict"
    ACTIVE_BINDING_MISSING = "active_binding_missing"
    EFFECT_TUPLE_UNATTRIBUTABLE = "effect_tuple_unattributable"
    DIRECTORY_NOT_EMPTY = "directory_not_empty"
    COMMITTED_SURFACE_MISMATCH = "committed_surface_mismatch"
    PLAN_PRECONDITION_CHANGED = "plan_precondition_changed"


class ScratchRole(Enum):
    STAGING = "staging"
    TOMBSTONE = "tombstone"
    ANCHOR = "anchor"
    WORK = "work"


class FileBuildRelation(Enum):
    EXACT = "exact"
    STRICT_PREFIX = "strict_prefix"
    DIVERGED = "diverged"


class IdentityRelation(Enum):
    SAME = "same"
    DIFFERENT = "different"


class OperatorAction(Enum):
    INSPECT_PRESERVED_EVIDENCE = "inspect_preserved_evidence"
    REPAIR_DURABLE_METADATA = "repair_durable_metadata"


@dataclass(frozen=True, slots=True, repr=False, init=False)
class EntryIdentity:
    _token: object = field(repr=False)

    def __init__(self) -> None:
        object.__setattr__(self, "_token", object())

    def __repr__(self) -> str:
        return "<entry-identity>"


@dataclass(frozen=True, slots=True)
class ObservedAbsent:
    pass


@dataclass(frozen=True, slots=True)
class ObservedFile:
    state: FileState
    identity: EntryIdentity


@dataclass(frozen=True, slots=True)
class ObservedSymlink:
    state: SymlinkState


@dataclass(frozen=True, slots=True)
class ObservedDirectory:
    state: DirectoryState
    identity: EntryIdentity
    has_unmodeled_child: bool


ObservedEntry = ObservedAbsent | ObservedFile | ObservedSymlink | ObservedDirectory
OBSERVED_ABSENT = ObservedAbsent()


@dataclass(frozen=True, slots=True)
class EffectJournalState:
    effect_id: str
    state: JournalState


@dataclass(frozen=True, slots=True)
class PersistentObservation:
    path: RelPath
    entry: ObservedEntry


@dataclass(frozen=True, slots=True)
class ScratchObservation:
    effect_id: str
    role: ScratchRole
    entry: ObservedEntry
    file_build_relation: FileBuildRelation | None


@dataclass(frozen=True, slots=True)
class DiagnosticEntry:
    slot: str
    state: PathState
    has_unmodeled_child: bool | None
    file_build_relation: FileBuildRelation | None


@dataclass(frozen=True, slots=True)
class DiagnosticIdentityRelation:
    left_slot: str
    right_slot: str
    relation: IdentityRelation


@dataclass(frozen=True, slots=True)
class HaltDiagnostic:
    pre_halt_state: TransactionState
    commit_decision: CommitDecision
    journals: tuple[EffectJournalState, ...]
    projected_transaction_state: TransactionState
    projected_journals: tuple[EffectJournalState, ...]
    effect_id: str | None
    paths: tuple[RelPath, ...]
    expected: tuple[DiagnosticEntry, ...]
    observed: tuple[DiagnosticEntry, ...]
    identity_relations: tuple[DiagnosticIdentityRelation, ...]
    reason: HaltReason
    operator_action: OperatorAction
```

Create `python/src/atoms/core/recovery/__init__.py` and explicitly re-export these names. Do not use
star imports or introduce aliases outside the closed enums. Define an explicit `__all__` tuple at the
same time; every later task extends it in the commit that adds a public symbol.

- [ ] **Step 4: Run focused tests and static checks**

Run:

```bash
uv run pytest tests/test_recovery_model.py -v
uv run ruff check src/atoms/core/recovery tests/test_recovery_model.py
uv run pyright
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/atoms/core/recovery/__init__.py src/atoms/core/recovery/model.py tests/test_recovery_model.py
git commit -m "feat(recovery): add closed recovery value model"
```

---

### Task 2: Resolved topology and validating snapshot factory

Build the sole `RecoverySnapshot` construction boundary. It validates exact types, topology coverage,
observation coverage, scratch roles, conditional construction evidence, and halted diagnostic
coherence before classification sees a value.

**Files:**
- Create: `python/src/atoms/core/recovery/snapshot.py`
- Create: `python/tests/recovery_support.py`
- Create: `python/tests/conftest.py`
- Create: `python/tests/test_recovery_snapshot.py`
- Modify: `python/src/atoms/core/recovery/__init__.py`

**Interfaces:**
- Consumes: `CompiledSpec`, exact A1 effects/states, Task 1 observations and enums.
- Produces:
  - `ProjectRoot`, `WorkRoot`, `TopologyDirectory`, `PersistentNode`, `ScratchNode`, `TopologyNode`;
  - `TopologyParent(node: TopologyNode, parent: TopologyNode)`;
  - `RecoveryTopology(parents: tuple[TopologyParent, ...])`;
  - guarded `RecoverySnapshot`;
  - `build_recovery_snapshot` with the exact typed keyword-only signature in Step 3;
  - internal `persistent_map`, `scratch_map`, `effect_index`, `required_scratch_role`.

- [ ] **Step 1: Write failing factory and topology tests**

Create `python/tests/recovery_support.py` with reusable exact fixtures:

```python
from atoms.core.compiler import compile_spec
from atoms.core.recovery import (
    CommitDecision,
    EffectJournalState,
    EntryIdentity,
    FileBuildRelation,
    JournalState,
    OBSERVED_ABSENT,
    ObservedFile,
    PersistentObservation,
    RecoveryTopology,
    RollbackResult,
    ScratchObservation,
    ScratchRole,
    TopologyParent,
    TransactionState,
    build_recovery_snapshot,
)
from tests.support import F, valid_spec

_DEFAULT = object()


def compiled_create():
    return compile_spec(valid_spec())


def create_topology():
    from atoms.core.recovery import PersistentNode, ProjectRoot, ScratchNode

    project = ProjectRoot()
    live = PersistentNode("a.txt")
    scratch = ScratchNode("e1", ScratchRole.STAGING)
    return RecoveryTopology(
        parents=(
            TopologyParent(node=live, parent=project),
            TopologyParent(node=scratch, parent=project),
        )
    )


def create_snapshot(
    *,
    state=TransactionState.APPLYING,
    journal=JournalState.STARTED,
    live=OBSERVED_ABSENT,
    staging=None,
    relation=_DEFAULT,
    commit_decision=CommitDecision.UNCOMMITTED,
    rollback_result=_DEFAULT,
    halt_diagnostic=None,
    active=True,
):
    staged = ObservedFile(F, EntryIdentity()) if staging is None else staging
    actual_relation = (
        FileBuildRelation.EXACT
        if (
            relation is _DEFAULT
            and journal is JournalState.STARTED
            and type(staged) is ObservedFile
        )
        else None
        if relation is _DEFAULT
        else relation
    )
    actual_rollback_result = (
        RollbackResult.RESTORED
        if rollback_result is _DEFAULT and state is TransactionState.ROLLED_BACK
        else None
        if rollback_result is _DEFAULT
        else rollback_result
    )
    return build_recovery_snapshot(
        compiled=compiled_create(),
        topology=create_topology(),
        transaction_state=state,
        commit_decision=commit_decision,
        rollback_result=actual_rollback_result,
        halt_diagnostic=halt_diagnostic,
        active=active,
        journals=(EffectJournalState("e1", journal),),
        persistent_observations=(PersistentObservation("a.txt", live),),
        scratch_observations=(
            ScratchObservation("e1", ScratchRole.STAGING, staged, actual_relation),
        ),
    )
```

Create `python/tests/test_recovery_snapshot.py`:

```python
from dataclasses import FrozenInstanceError, replace

import pytest

from atoms.core.errors import ProtocolError
from atoms.core.recovery import (
    CommitDecision,
    EffectJournalState,
    FileBuildRelation,
    JournalState,
    OBSERVED_ABSENT,
    PersistentObservation,
    RecoverySnapshot,
    ScratchObservation,
    ScratchRole,
    TransactionState,
    build_recovery_snapshot,
)
from tests.recovery_support import compiled_create, create_snapshot, create_topology


def test_valid_snapshot_is_frozen_and_factory_controlled():
    snapshot = create_snapshot()
    with pytest.raises(FrozenInstanceError):
        snapshot.active = False  # type: ignore[misc]
    with pytest.raises(TypeError, match="build_recovery_snapshot"):
        RecoverySnapshot(
            compiled=snapshot.compiled,
            topology=snapshot.topology,
            transaction_state=snapshot.transaction_state,
            commit_decision=snapshot.commit_decision,
            rollback_result=snapshot.rollback_result,
            halt_diagnostic=snapshot.halt_diagnostic,
            active=snapshot.active,
            journals=snapshot.journals,
            persistent_observations=snapshot.persistent_observations,
            scratch_observations=snapshot.scratch_observations,
        )
    with pytest.raises(TypeError, match="build_recovery_snapshot"):
        replace(snapshot)


@pytest.mark.parametrize("missing", ["journal", "persistent", "scratch"])
def test_snapshot_requires_exact_coverage(missing):
    compiled = compiled_create()
    journals = (EffectJournalState("e1", JournalState.STARTED),)
    persistent = (PersistentObservation("a.txt", OBSERVED_ABSENT),)
    scratch = (
        ScratchObservation(
            "e1",
            ScratchRole.STAGING,
            OBSERVED_ABSENT,
            None,
        ),
    )
    values = {"journal": journals, "persistent": persistent, "scratch": scratch}
    values[missing] = ()
    with pytest.raises(ProtocolError, match=missing):
        build_recovery_snapshot(
            compiled=compiled,
            topology=create_topology(),
            transaction_state=TransactionState.APPLYING,
            commit_decision=CommitDecision.UNCOMMITTED,
            rollback_result=None,
            halt_diagnostic=None,
            active=True,
            journals=values["journal"],
            persistent_observations=values["persistent"],
            scratch_observations=values["scratch"],
        )


def test_started_create_requires_relation_for_present_staging():
    with pytest.raises(ProtocolError, match="file_build_relation"):
        create_snapshot(relation=None)


def test_absent_staging_forbids_relation():
    with pytest.raises(ProtocolError, match="file_build_relation"):
        create_snapshot(staging=OBSERVED_ABSENT, relation=FileBuildRelation.EXACT)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_recovery_snapshot.py -v`

Expected: FAIL because the topology and snapshot types are not exported.

- [ ] **Step 3: Implement topology nodes and the guarded snapshot**

Create `python/src/atoms/core/recovery/snapshot.py`. Use exact dataclasses:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

from atoms.core.compiler import CompiledSpec
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.errors import ProtocolError
from atoms.core.recovery.model import (
    CommitDecision,
    EffectJournalState,
    HaltDiagnostic,
    JournalState,
    ObservedAbsent,
    ObservedFile,
    PersistentObservation,
    RollbackResult,
    ScratchObservation,
    ScratchRole,
    TransactionState,
)


@dataclass(frozen=True, slots=True)
class ProjectRoot:
    pass


@dataclass(frozen=True, slots=True)
class WorkRoot:
    pass


@dataclass(frozen=True, slots=True)
class TopologyDirectory:
    node_id: int


@dataclass(frozen=True, slots=True)
class PersistentNode:
    path: str


@dataclass(frozen=True, slots=True)
class ScratchNode:
    effect_id: str
    role: ScratchRole


TopologyNode = ProjectRoot | WorkRoot | TopologyDirectory | PersistentNode | ScratchNode


@dataclass(frozen=True, slots=True)
class TopologyParent:
    node: TopologyNode
    parent: TopologyNode


@dataclass(frozen=True, slots=True)
class RecoveryTopology:
    parents: tuple[TopologyParent, ...]


_SNAPSHOT_TOKEN = object()


def _fail(message: str) -> NoReturn:
    raise ProtocolError(message)


def _require_exact(value: object, expected: type, label: str) -> None:
    if type(value) is not expected:
        _fail(f"{label} has the wrong exact runtime type")


def _require_optional_exact(value: object, expected: type, label: str) -> None:
    if value is not None and type(value) is not expected:
        _fail(f"{label} has the wrong exact runtime type")


def _require_tuple(value: object, label: str) -> None:
    if type(value) is not tuple:
        _fail(f"{label} must be an exact tuple")


@dataclass(frozen=True, slots=True, init=False)
class RecoverySnapshot:
    compiled: CompiledSpec
    topology: RecoveryTopology
    transaction_state: TransactionState
    commit_decision: CommitDecision
    rollback_result: RollbackResult | None
    halt_diagnostic: HaltDiagnostic | None
    active: bool
    journals: tuple[EffectJournalState, ...]
    persistent_observations: tuple[PersistentObservation, ...]
    scratch_observations: tuple[ScratchObservation, ...]

    def __init__(
        self,
        *,
        compiled: CompiledSpec,
        topology: RecoveryTopology,
        transaction_state: TransactionState,
        commit_decision: CommitDecision,
        rollback_result: RollbackResult | None,
        halt_diagnostic: HaltDiagnostic | None,
        active: bool,
        journals: tuple[EffectJournalState, ...],
        persistent_observations: tuple[PersistentObservation, ...],
        scratch_observations: tuple[ScratchObservation, ...],
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _SNAPSHOT_TOKEN:
            raise TypeError("RecoverySnapshot values are created only by build_recovery_snapshot")
        object.__setattr__(self, "compiled", compiled)
        object.__setattr__(self, "topology", topology)
        object.__setattr__(self, "transaction_state", transaction_state)
        object.__setattr__(self, "commit_decision", commit_decision)
        object.__setattr__(self, "rollback_result", rollback_result)
        object.__setattr__(self, "halt_diagnostic", halt_diagnostic)
        object.__setattr__(self, "active", active)
        object.__setattr__(self, "journals", journals)
        object.__setattr__(self, "persistent_observations", persistent_observations)
        object.__setattr__(self, "scratch_observations", scratch_observations)
```

Add these exact helpers:

```python
_ROLE_BY_EFFECT = {
    ReplaceFile: ScratchRole.STAGING,
    CreateFileNoClobber: ScratchRole.STAGING,
    DeletePath: ScratchRole.TOMBSTONE,
    MoveNoClobber: ScratchRole.ANCHOR,
    CreateDirectory: ScratchRole.WORK,
}


def required_scratch_role(effect: object) -> ScratchRole:
    try:
        return _ROLE_BY_EFFECT[type(effect)]
    except KeyError as exc:
        raise ProtocolError("compiled effect variant is outside A3's closed set") from exc


def persistent_map(snapshot: RecoverySnapshot):
    return {item.path: item.entry for item in snapshot.persistent_observations}


def scratch_map(snapshot: RecoverySnapshot):
    return {(item.effect_id, item.role): item for item in snapshot.scratch_observations}


def effect_index(snapshot: RecoverySnapshot):
    return {effect.effect_id: index for index, effect in enumerate(snapshot.compiled.spec.effects)}
```

Implement `build_recovery_snapshot` with the §1 signature and this validation order:

```python
def build_recovery_snapshot(
    *,
    compiled: CompiledSpec,
    topology: RecoveryTopology,
    transaction_state: TransactionState,
    commit_decision: CommitDecision,
    rollback_result: RollbackResult | None,
    halt_diagnostic: HaltDiagnostic | None,
    active: bool,
    journals: tuple[EffectJournalState, ...],
    persistent_observations: tuple[PersistentObservation, ...],
    scratch_observations: tuple[ScratchObservation, ...],
) -> RecoverySnapshot:
    _require_exact(compiled, CompiledSpec, "compiled")
    _require_exact(topology, RecoveryTopology, "topology")
    _require_exact(transaction_state, TransactionState, "transaction_state")
    _require_exact(commit_decision, CommitDecision, "commit_decision")
    _require_optional_exact(rollback_result, RollbackResult, "rollback_result")
    _require_optional_exact(halt_diagnostic, HaltDiagnostic, "halt_diagnostic")
    _require_exact(active, bool, "active")
    _require_tuple(journals, "journals")
    _require_tuple(persistent_observations, "persistent_observations")
    _require_tuple(scratch_observations, "scratch_observations")

    _validate_journal_coverage(compiled, journals)
    _validate_persistent_coverage(compiled, persistent_observations)
    _validate_scratch_coverage(compiled, scratch_observations)
    _validate_topology(compiled, topology)
    _validate_observations(compiled, transaction_state, journals, persistent_observations, scratch_observations)
    _validate_halt_diagnostic(halt_diagnostic)
    _validate_terminal_payloads(transaction_state, commit_decision, rollback_result, halt_diagnostic, journals)

    return RecoverySnapshot(
        compiled=compiled,
        topology=topology,
        transaction_state=transaction_state,
        commit_decision=commit_decision,
        rollback_result=rollback_result,
        halt_diagnostic=halt_diagnostic,
        active=active,
        journals=journals,
        persistent_observations=persistent_observations,
        scratch_observations=scratch_observations,
        _construction_token=_SNAPSHOT_TOKEN,
    )
```

The validation helpers must enforce these exact rules:

| Helper | Required verdict |
| --- | --- |
| `_validate_journal_coverage` | exact effect IDs once, in compiled order; exact `EffectJournalState` |
| `_validate_persistent_coverage` | exact union of compiled timeline paths once |
| `_validate_scratch_coverage` | exact effect/role pair once for every compiled effect |
| `_validate_topology` | iterative exact coverage, unique IDs, one parent, root and cycle rules |
| `_validate_observations` | exact observation variants and scalar types; no symlink identity |
| `_validate_halt_diagnostic` | exact nested types, durable/projected vectors, sorted slots/relations, no tokens |
| construction relation | only the two started construction cases; absent otherwise |
| terminal payload | rollback result exactly for `ROLLED_BACK`; diagnostic exactly for `HALTED` |
| halted coherence | current commit decision and full journal vector equal the diagnostic |

Raise `ProtocolError` with fixed labels; do not include an identity token or arbitrary value `repr`.
Export every public name through `recovery/__init__.py`.

`TopologyDirectory` represents a resolved intermediate parent that is not itself a declared
persistent endpoint. It is a pure opaque node label, not a path, descriptor, device, or inode. Add a
nested-path test proving two endpoints share their actual parent node and that endpoints under
different parents do not collapse onto `ProjectRoot`. `_validate_topology` must use an explicit
indegree/worklist traversal; recursion is forbidden so a 1,100-component topology remains valid.

Create `python/tests/conftest.py` with only the module docstring
`"""Explicit recovery-model fixture registry."""`; importing unused `pytest` here would fail Ruff.
Task 4 adds the first adapter and the `pytest` import. Tasks 5–10 add each later adapter in the same
commit as its `make_*` support factory. The architecture test in Task 10 checks this registry.

- [ ] **Step 4: Add adversarial factory tests**

Extend `test_recovery_snapshot.py` with exact-type subclasses, duplicated coverage, cycles, wrong roles,
irrelevant file relations on completed replace, malformed nested diagnostic members, and halted
diagnostic mismatch. Use parameterization:

```python
@pytest.mark.parametrize(
    "field",
    [
        "compiled",
        "topology",
        "transaction_state",
        "commit_decision",
        "active",
        "journals",
        "persistent_observations",
        "scratch_observations",
    ],
)
def test_snapshot_refuses_subclass_or_wrong_exact_type(field):
    snapshot = create_snapshot()
    values = {
        "compiled": snapshot.compiled,
        "topology": snapshot.topology,
        "transaction_state": snapshot.transaction_state,
        "commit_decision": snapshot.commit_decision,
        "rollback_result": snapshot.rollback_result,
        "halt_diagnostic": snapshot.halt_diagnostic,
        "active": snapshot.active,
        "journals": snapshot.journals,
        "persistent_observations": snapshot.persistent_observations,
        "scratch_observations": snapshot.scratch_observations,
    }
    values[field] = [] if field.endswith("s") else object()
    with pytest.raises(ProtocolError, match=field):
        build_recovery_snapshot(**values)
```

- [ ] **Step 5: Run focused and existing tests**

Run:

```bash
uv run pytest tests/test_recovery_snapshot.py -v
uv run pytest
uv run ruff check .
uv run pyright
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/atoms/core/recovery tests/conftest.py tests/recovery_support.py tests/test_recovery_snapshot.py
git commit -m "feat(recovery): validate logical recovery snapshots"
```

---
### Task 3: Guarded semantic plans, steps, and joint observations

Define the complete plan language before classification. Plans bind their source snapshot by value;
only internal factories construct plan variants and authorized steps.

**Files:**
- Create: `python/src/atoms/core/recovery/plan.py`
- Create: `python/tests/test_recovery_plan.py`
- Modify: `python/src/atoms/core/recovery/__init__.py`

**Interfaces:**
- Consumes: Task 1 model values and Task 2 `RecoverySnapshot`/topology nodes.
- Produces:
  - `PlanDisposition`, `SettlementKind`, `EffectVariant`;
  - `JointObservation`;
  - step union `TransitionTransactionState | TransitionEffectState | TransformEffectTuple |
    RemoveScratch | PreserveExternal | DetachActive`;
  - guarded `ActionPlan`, `HaltPlan`, `NoRecoveryPlan`, `RecoveryPlan`, `AuthorizedStep`;
  - internal factories `_new_action_plan`, `_new_halt_plan`, `_new_no_recovery_plan`,
    `_new_authorized_step`.

- [ ] **Step 1: Write failing plan-value tests**

Create `python/tests/test_recovery_plan.py`:

```python
from dataclasses import FrozenInstanceError, replace

import pytest

from atoms.core.recovery import (
    ActionPlan,
    HaltPlan,
    JointObservation,
    NoRecoveryPlan,
    PlanDisposition,
    RollbackResult,
)
from atoms.core.recovery.plan import _new_action_plan, _new_no_recovery_plan
from tests.recovery_support import create_snapshot


def test_action_plan_is_source_bound_frozen_and_factory_controlled():
    snapshot = create_snapshot()
    plan = _new_action_plan(
        bound_snapshot=snapshot,
        disposition=PlanDisposition.ROLL_BACK,
        steps=(),
    )
    assert plan.bound_snapshot == snapshot
    with pytest.raises(FrozenInstanceError):
        plan.steps = ()  # type: ignore[misc]
    with pytest.raises(TypeError, match="classify_recovery"):
        ActionPlan(
            bound_snapshot=snapshot,
            disposition=PlanDisposition.ROLL_BACK,
            steps=(),
            rollback_result=RollbackResult.RESTORED,
        )
    with pytest.raises(TypeError, match="classify_recovery"):
        replace(plan)


def test_no_recovery_plan_is_a_distinct_variant():
    plan = _new_no_recovery_plan(create_snapshot(active=False))
    assert type(plan) is NoRecoveryPlan
    assert plan.disposition is PlanDisposition.NO_RECOVERY
    assert not isinstance(plan, (ActionPlan, HaltPlan))


def test_joint_observation_is_exact_node_evidence():
    snapshot = create_snapshot()
    observed = JointObservation(
        persistent=snapshot.persistent_observations,
        scratch=snapshot.scratch_observations,
        parent_occupancy=(),
    )
    assert observed.persistent == snapshot.persistent_observations
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_recovery_plan.py -v`

Expected: FAIL because `atoms.core.recovery.plan` does not exist.

- [ ] **Step 3: Implement the plan language**

Create `python/src/atoms/core/recovery/plan.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from atoms.core.errors import ProtocolError
from atoms.core.recovery.model import (
    DiagnosticIdentityRelation,
    HaltDiagnostic,
    JournalState,
    PersistentObservation,
    RollbackResult,
    ScratchObservation,
    ScratchRole,
    TransactionState,
)
from atoms.core.recovery.snapshot import RecoverySnapshot, TopologyNode


class PlanDisposition(Enum):
    ROLL_BACK = "roll_back"
    ROLL_BACK_REFUSED = "roll_back_refused"
    COMMITTED_CLEANUP = "committed_cleanup"
    DETACH_TERMINAL = "detach_terminal"
    HALT = "halt"
    NO_RECOVERY = "no_recovery"


class SettlementKind(Enum):
    RESTORE_PRE = "restore_pre"
    REMOVE_ATTRIBUTABLE_CREATION = "remove_attributable_creation"
    REPAIR_INTERMEDIATE = "repair_intermediate"
    FINISH_LANDED_UNDO = "finish_landed_undo"
    REMOVE_COMMITTED_SCRATCH = "remove_committed_scratch"


class EffectVariant(Enum):
    REPLACE_FILE = "replace_file"
    CREATE_FILE_NO_CLOBBER = "create_file_no_clobber"
    DELETE_PATH = "delete_path"
    MOVE_NO_CLOBBER = "move_no_clobber"
    CREATE_DIRECTORY = "create_directory"


@dataclass(frozen=True, slots=True)
class ParentOccupancy:
    parent: TopologyNode
    present_children: tuple[TopologyNode, ...]
    has_unmodeled_child: bool


@dataclass(frozen=True, slots=True)
class JointObservation:
    persistent: tuple[PersistentObservation, ...]
    scratch: tuple[ScratchObservation, ...]
    parent_occupancy: tuple[ParentOccupancy, ...]


@dataclass(frozen=True, slots=True)
class TransitionTransactionState:
    from_state: TransactionState
    to_state: TransactionState
    rollback_result: RollbackResult | None
    halt_diagnostic: HaltDiagnostic | None


@dataclass(frozen=True, slots=True)
class TransitionEffectState:
    effect_id: str
    from_state: JournalState
    to_state: JournalState


@dataclass(frozen=True, slots=True)
class TransformEffectTuple:
    effect_id: str
    variant: EffectVariant
    settlement: SettlementKind
    expected_before: JointObservation
    result_after: JointObservation
    identity_relations: tuple[DiagnosticIdentityRelation, ...]


@dataclass(frozen=True, slots=True)
class RemoveScratch:
    effect_id: str
    role: ScratchRole
    expected_before: JointObservation
    result_after: JointObservation


@dataclass(frozen=True, slots=True)
class PreserveExternal:
    nodes: tuple[TopologyNode, ...]


@dataclass(frozen=True, slots=True)
class DetachActive:
    pass


RecoveryStep = (
    TransitionTransactionState
    | TransitionEffectState
    | TransformEffectTuple
    | RemoveScratch
    | PreserveExternal
    | DetachActive
)

_PLAN_TOKEN = object()


@dataclass(frozen=True, slots=True, init=False)
class ActionPlan:
    bound_snapshot: RecoverySnapshot
    disposition: PlanDisposition
    steps: tuple[RecoveryStep, ...]
    rollback_result: RollbackResult | None

    def __init__(
        self,
        *,
        bound_snapshot: RecoverySnapshot,
        disposition: PlanDisposition,
        steps: tuple[RecoveryStep, ...],
        rollback_result: RollbackResult | None,
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _PLAN_TOKEN:
            raise TypeError("RecoveryPlan values are created only by classify_recovery")
        object.__setattr__(self, "bound_snapshot", bound_snapshot)
        object.__setattr__(self, "disposition", disposition)
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "rollback_result", rollback_result)


@dataclass(frozen=True, slots=True, init=False)
class HaltPlan:
    bound_snapshot: RecoverySnapshot
    disposition: PlanDisposition = field(init=False)
    diagnostic: HaltDiagnostic
    steps: tuple[TransitionTransactionState, ...]

    def __init__(
        self,
        *,
        bound_snapshot: RecoverySnapshot,
        diagnostic: HaltDiagnostic,
        steps: tuple[TransitionTransactionState, ...],
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _PLAN_TOKEN:
            raise TypeError("RecoveryPlan values are created only by classify_recovery")
        object.__setattr__(self, "bound_snapshot", bound_snapshot)
        object.__setattr__(self, "disposition", PlanDisposition.HALT)
        object.__setattr__(self, "diagnostic", diagnostic)
        object.__setattr__(self, "steps", steps)


@dataclass(frozen=True, slots=True, init=False)
class NoRecoveryPlan:
    bound_snapshot: RecoverySnapshot
    disposition: PlanDisposition = field(init=False)
    steps: tuple[()] = field(init=False)

    def __init__(
        self,
        *,
        bound_snapshot: RecoverySnapshot,
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _PLAN_TOKEN:
            raise TypeError("RecoveryPlan values are created only by classify_recovery")
        object.__setattr__(self, "bound_snapshot", bound_snapshot)
        object.__setattr__(self, "disposition", PlanDisposition.NO_RECOVERY)
        object.__setattr__(self, "steps", ())


RecoveryPlan = ActionPlan | HaltPlan | NoRecoveryPlan

_AUTHORIZED_TOKEN = object()


@dataclass(frozen=True, slots=True, init=False)
class AuthorizedStep:
    plan: RecoveryPlan
    step_index: int
    step: TransformEffectTuple | RemoveScratch

    def __init__(
        self,
        *,
        plan: RecoveryPlan,
        step_index: int,
        step: TransformEffectTuple | RemoveScratch,
        _construction_token: object | None = None,
    ) -> None:
        if _construction_token is not _AUTHORIZED_TOKEN:
            raise TypeError("AuthorizedStep values are created only by authorize_recovery_step")
        object.__setattr__(self, "plan", plan)
        object.__setattr__(self, "step_index", step_index)
        object.__setattr__(self, "step", step)
```

Add the four internal factories. Each validates the disposition/payload combination before passing its
module token. `_new_action_plan` permits only the four action dispositions. `ROLL_BACK` requires
`RESTORED`; `ROLL_BACK_REFUSED` requires `EXTERNAL_DRIFT_PRESERVED`; `COMMITTED_CLEANUP` and
`DETACH_TERMINAL` derive `None`; callers do not supply a redundant result. `_new_halt_plan` accepts
exactly one
`TransitionTransactionState` from the bound snapshot's current state to `HALTED` for a first halt, or
no steps when the bound snapshot is already `HALTED` and supplies the same frozen diagnostic.
`_new_no_recovery_plan` has no steps; `_new_authorized_step` accepts only `TransformEffectTuple` or
`RemoveScratch`.

Use these factory bodies:

```python
_STEP_TYPES = {
    TransitionTransactionState,
    TransitionEffectState,
    TransformEffectTuple,
    RemoveScratch,
    PreserveExternal,
    DetachActive,
}


def _validate_steps(steps: tuple[RecoveryStep, ...]) -> None:
    if type(steps) is not tuple or any(type(step) not in _STEP_TYPES for step in steps):
        raise ProtocolError("plan steps must be an exact tuple of closed step variants")
    for step in steps:
        if type(step) is TransformEffectTuple and type(step.variant) is not EffectVariant:
            raise ProtocolError("transform variant must be an exact EffectVariant")
        if type(step) is not TransitionTransactionState:
            continue
        has_result = step.rollback_result is not None
        has_diagnostic = step.halt_diagnostic is not None
        if step.to_state is TransactionState.ROLLED_BACK:
            valid = has_result and not has_diagnostic
        elif step.to_state is TransactionState.HALTED:
            valid = has_diagnostic and not has_result
        else:
            valid = not has_result and not has_diagnostic
        if not valid:
            raise ProtocolError("transaction transition has invalid terminal payloads")


def _new_action_plan(
    *,
    bound_snapshot: RecoverySnapshot,
    disposition: PlanDisposition,
    steps: tuple[RecoveryStep, ...],
) -> ActionPlan:
    _validate_steps(steps)
    expected_result = {
        PlanDisposition.ROLL_BACK: RollbackResult.RESTORED,
        PlanDisposition.ROLL_BACK_REFUSED: RollbackResult.EXTERNAL_DRIFT_PRESERVED,
        PlanDisposition.COMMITTED_CLEANUP: None,
        PlanDisposition.DETACH_TERMINAL: None,
    }
    if disposition not in expected_result:
        raise ProtocolError("action plan has a non-action disposition")
    rollback_result = expected_result[disposition]
    return ActionPlan(
        bound_snapshot=bound_snapshot,
        disposition=disposition,
        steps=steps,
        rollback_result=rollback_result,
        _construction_token=_PLAN_TOKEN,
    )


def _new_halt_plan(
    *,
    bound_snapshot: RecoverySnapshot,
    diagnostic: HaltDiagnostic,
    steps: tuple[TransitionTransactionState, ...],
) -> HaltPlan:
    if bound_snapshot.transaction_state is TransactionState.HALTED:
        valid = not steps and bound_snapshot.halt_diagnostic == diagnostic
    else:
        valid = (
            len(steps) == 1
            and steps[0].from_state is bound_snapshot.transaction_state
            and steps[0].to_state is TransactionState.HALTED
            and steps[0].rollback_result is None
            and steps[0].halt_diagnostic == diagnostic
        )
    if not valid:
        raise ProtocolError("halt plan has an invalid transition or diagnostic")
    return HaltPlan(
        bound_snapshot=bound_snapshot,
        diagnostic=diagnostic,
        steps=steps,
        _construction_token=_PLAN_TOKEN,
    )


def _new_no_recovery_plan(bound_snapshot: RecoverySnapshot) -> NoRecoveryPlan:
    return NoRecoveryPlan(
        bound_snapshot=bound_snapshot,
        _construction_token=_PLAN_TOKEN,
    )


def _new_authorized_step(
    plan: RecoveryPlan,
    step_index: int,
    step: TransformEffectTuple | RemoveScratch,
) -> AuthorizedStep:
    if type(step) not in {TransformEffectTuple, RemoveScratch}:
        raise ProtocolError("authorized step must be filesystem-mutating")
    return AuthorizedStep(
        plan=plan,
        step_index=step_index,
        step=step,
        _construction_token=_AUTHORIZED_TOKEN,
    )
```

- [ ] **Step 4: Test invalid construction and payload combinations**

Add parameterized tests proving:

```python
@pytest.mark.parametrize(
    "disposition",
    [
        PlanDisposition.ROLL_BACK,
        PlanDisposition.ROLL_BACK_REFUSED,
        PlanDisposition.COMMITTED_CLEANUP,
        PlanDisposition.DETACH_TERMINAL,
    ],
)
def test_action_plan_factory_derives_result_from_disposition(disposition):
    plan = _new_action_plan(
        bound_snapshot=create_snapshot(),
        disposition=disposition,
        steps=(),
    )
    assert plan.disposition is disposition
```

Also assert direct construction and both `replace(plan)` forms fail for all three variants and
`AuthorizedStep`, always with `match="classify_recovery"` (or
`match="authorize_recovery_step"` for `AuthorizedStep`). `HaltPlan` marks only its derived
`disposition` as `init=False`; `steps` remains an ordinary field because its guarded `__init__`
requires it. `NoRecoveryPlan` marks both derived fields `init=False`. These declarations make
`replace` supply exactly the guarded constructor's required keywords and reach the token check.

- [ ] **Step 5: Run focused and full checks**

Run:

```bash
uv run pytest tests/test_recovery_plan.py -v
uv run pytest
uv run ruff check .
uv run pyright
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/atoms/core/recovery tests/test_recovery_plan.py
git commit -m "feat(recovery): define guarded semantic plans"
```

---

### Task 4: Prefix and full abstract reducer

Interpret the same semantic steps A7 will later execute. Prefix reduction is normative: it gives stale
authorization a precise logical source and models crash points between durable steps.

**Files:**
- Create: `python/src/atoms/core/recovery/reducer.py`
- Create: `python/tests/test_recovery_reducer.py`
- Modify: `python/tests/recovery_support.py`
- Modify: `python/tests/conftest.py`
- Modify: `python/src/atoms/core/recovery/__init__.py`

**Interfaces:**
- Consumes: guarded plans/steps and `build_recovery_snapshot`.
- Produces:
  - `reduce_recovery_plan_prefix(snapshot, plan, completed_steps) -> RecoverySnapshot`;
  - `apply_recovery_plan(snapshot, plan) -> RecoverySnapshot`;
  - internal `_apply_steps(snapshot, steps) -> RecoverySnapshot`, reused by Task 8's pure planning
    cursor so classification and execution cannot diverge;
  - internal `_normalize_joint_observation(snapshot, observation) -> JointObservation`, reused by
    Tasks 8 and 9 whenever evidence is rebound to a different journal prefix.

- [ ] **Step 1: Write failing prefix and source-binding tests**

Create `python/tests/test_recovery_reducer.py`:

```python
import pytest

from atoms.core.errors import ProtocolError
from atoms.core.recovery import (
    DetachActive,
    PlanDisposition,
    TransactionState,
    TransitionTransactionState,
    apply_recovery_plan,
    reduce_recovery_plan_prefix,
)
from atoms.core.recovery.plan import _new_action_plan
from atoms.core.recovery.reducer import _apply_steps
from tests.recovery_support import create_snapshot


def transition_plan(snapshot):
    step = TransitionTransactionState(
        from_state=TransactionState.APPLYING,
        to_state=TransactionState.ROLLING_BACK,
        rollback_result=None,
        halt_diagnostic=None,
    )
    return _new_action_plan(
        bound_snapshot=snapshot,
        disposition=PlanDisposition.ROLL_BACK,
        steps=(step,),
    )


def test_prefix_zero_is_the_exact_source_snapshot():
    snapshot = create_snapshot()
    plan = transition_plan(snapshot)
    assert reduce_recovery_plan_prefix(snapshot, plan, 0) is snapshot


def test_prefix_reduction_advances_exactly_named_steps():
    snapshot = create_snapshot()
    plan = transition_plan(snapshot)
    first = reduce_recovery_plan_prefix(snapshot, plan, 1)
    assert first.transaction_state is TransactionState.ROLLING_BACK
    assert first.journals == snapshot.journals


def test_plan_refuses_value_unequal_source():
    snapshot = create_snapshot()
    plan = transition_plan(snapshot)
    other = create_snapshot(active=False)
    with pytest.raises(ProtocolError, match="bound source"):
        apply_recovery_plan(other, plan)


@pytest.mark.parametrize("count", [-1, 10**6, True])
def test_completed_step_count_is_bounded_exact_int(count):
    snapshot = create_snapshot()
    plan = transition_plan(snapshot)
    with pytest.raises(ProtocolError, match="completed_steps"):
        reduce_recovery_plan_prefix(snapshot, plan, count)
```

- [ ] **Step 2: Write failing exact-step tests**

```python
def test_internal_step_reducer_advances_without_fabricating_a_plan():
    snapshot = create_snapshot()
    step = transition_plan(snapshot).steps[0]
    reduced = _apply_steps(snapshot, (step,))
    assert reduced.transaction_state is TransactionState.ROLLING_BACK
    assert reduced.journals == snapshot.journals


def test_detach_step_changes_only_active_binding(terminal_snapshot):
    reduced = _apply_steps(terminal_snapshot, (DetachActive(),))
    assert not reduced.active
    assert reduced.transaction_state is terminal_snapshot.transaction_state
    assert reduced.persistent_observations == terminal_snapshot.persistent_observations
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_recovery_reducer.py -v`

Expected: FAIL because reducer functions are not exported.

- [ ] **Step 4: Implement source and prefix validation**

Create `python/src/atoms/core/recovery/reducer.py`:

```python
def reduce_recovery_plan_prefix(
    snapshot: RecoverySnapshot,
    plan: RecoveryPlan,
    completed_steps: int,
) -> RecoverySnapshot:
    if type(snapshot) is not RecoverySnapshot:
        raise ProtocolError("snapshot must be a factory-issued RecoverySnapshot")
    if type(plan) not in {ActionPlan, HaltPlan, NoRecoveryPlan}:
        raise ProtocolError("plan must be a factory-issued RecoveryPlan")
    if snapshot != plan.bound_snapshot:
        raise ProtocolError("plan does not match its bound source snapshot")
    if type(completed_steps) is not int:
        raise ProtocolError("completed_steps must be an exact integer")
    if not 0 <= completed_steps <= len(plan.steps):
        raise ProtocolError("completed_steps is outside the plan step range")
    if completed_steps == 0:
        return snapshot

    return _apply_steps(snapshot, plan.steps[:completed_steps])


def apply_recovery_plan(
    snapshot: RecoverySnapshot,
    plan: RecoveryPlan,
) -> RecoverySnapshot:
    return reduce_recovery_plan_prefix(snapshot, plan, len(plan.steps))


def _apply_steps(
    snapshot: RecoverySnapshot,
    steps: tuple[RecoveryStep, ...],
) -> RecoverySnapshot:
    current = snapshot
    for step in steps:
        current = _apply_step(current, step)
    return current
```

- [ ] **Step 5: Implement every semantic step**

`_apply_step` dispatches by exact runtime type from a table whose keys are the six step classes.

```text
TransitionTransactionState:
    require current state == from_state;
    validate rollback-result/halt-diagnostic payload;
    replace transaction state and terminal payloads.
TransitionEffectState:
    require exact effect ID/current state;
    replace that journal row, then normalize construction-only build relations against the new state.
TransformEffectTuple:
    require current joint observation == expected_before;
    merge result_after persistent/scratch/occupancy evidence, then normalize construction-only build
    relations against the effect's unchanged journal state and new live/staging tuple.
RemoveScratch:
    require expected_before;
    merge result_after, whose named scratch entry is absent.
PreserveExternal:
    verify named nodes exist; observations remain unchanged.
DetachActive:
    require active; set active false.
```

Before calling `build_recovery_snapshot` after each step, run
`_normalize_file_build_relations(compiled, journals, persistent, scratch)`. It preserves a relation
only for a present create staging at `STARTED`, or a present replace staging at `STARTED` while live is
exact `pre`; otherwise it replaces the relation with `None`. A tuple step entering a required case
must carry the new relation in `result_after`; normalization must not invent `EXACT`, `STRICT_PREFIX`,
or `DIVERGED`. This makes `STARTED -> UNDO_STARTED` valid even when the staging entry remains present,
and makes a replace tuple moving live away from exact `pre` drop now-irrelevant evidence.

Then call `build_recovery_snapshot` with the normalized exact values so every intermediate remains
validated. A mismatch is `ProtocolError`, not a semantic halt: a factory plan applied in order to its
bound logical source cannot legitimately disagree with itself.

`_normalize_joint_observation(snapshot, observation)` applies the identical rule to a step-sized
observation. It preserves every entry, identity token, occupancy fact, and required relation, changing
only a relation that the snapshot's journal state and the observation's live/staging tuple make
irrelevant to `None`. It never allocates identity or invents a build relation. Task 8 uses it while
binding provisional variant steps to their post-transition cursor; Task 9 uses it before rebuilding a
mismatch snapshot.

For directory observations, recompute modeled occupancy from `RecoveryTopology` after each
`TransformEffectTuple`; preserve `has_unmodeled_child`.

- [ ] **Step 6: Add one test for every step variant**

`reducer_step_cases` yields a validated source snapshot, one exact step, and the expected snapshot
projection. Cover all six variants:

```python
@pytest.mark.parametrize(
    "case",
    [
        "transaction_transition",
        "effect_transition",
        "transform_tuple",
        "remove_scratch",
        "preserve_external",
        "detach_active",
    ],
)
def test_each_step_has_one_exact_logical_reduction(reducer_step_cases, case):
    snapshot, step, expected = reducer_step_cases(case)
    assert _apply_steps(snapshot, (step,)) == expected
```

Add two relation-transition locks:

```python
def test_effect_transition_drops_construction_relation(replace_started_case):
    source, transition = replace_started_case()
    reduced = _apply_steps(source, (transition,))
    staging = reduced.scratch_observations[0]
    assert staging.entry == source.scratch_observations[0].entry
    assert staging.file_build_relation is None


def test_replace_tuple_leaving_pre_drops_construction_relation(replace_transform_case):
    source, transform = replace_transform_case()
    reduced = _apply_steps(source, (transform,))
    assert reduced.scratch_observations[0].file_build_relation is None
```

Task 4 adds `terminal_snapshot`, `reducer_step_cases`, `replace_started_case`, and
`replace_transform_case` adapters to `conftest.py`; their `make_*` factories live in
`recovery_support.py`.

- [ ] **Step 7: Run focused and full checks**

Run:

```bash
uv run pytest tests/test_recovery_reducer.py -v
uv run pytest
uv run ruff check .
uv run pyright
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/atoms/core/recovery/reducer.py src/atoms/core/recovery/__init__.py \
  tests/conftest.py tests/test_recovery_reducer.py tests/recovery_support.py
git commit -m "feat(recovery): reduce semantic recovery plans"
```

---

### Task 5: Transaction authority, journal languages, and path frontiers

Implement the state × commit × active matrix and the only legal journal languages. Reconstruct one
frontier per compiled path from every occurrence's journal state; never read `dependencies`.

**Files:**
- Create: `python/src/atoms/core/recovery/journal.py`
- Create: `python/tests/test_recovery_journal.py`
- Modify: `python/tests/recovery_support.py`
- Modify: `python/tests/conftest.py`

**Interfaces:**
- Consumes: `RecoverySnapshot`, compiled `PathTimeline`, Task 1 states/reasons.
- Produces:
  - `AuthorityDecision(kind: AuthorityKind, halt_reason: HaltReason | None)`;
  - `AuthorityKind {CLASSIFY, DETACH, NO_RECOVERY, STABLE_HALT, HALT}`;
  - `FrontierDirection {FORWARD, REVERSE, INITIAL}`;
  - `PathFrontier(path, direction, effect_index, effect_id, journal_state, expected_state,
    admissible_states)`;
  - `classify_transaction_authority(snapshot) -> AuthorityDecision`;
  - `reconstruct_frontiers(snapshot) -> tuple[PathFrontier, ...]`.

- [ ] **Step 1: Write the journal-language tests**

Create `python/tests/test_recovery_journal.py`:

```python
import pytest

from atoms.core.recovery import (
    CommitDecision,
    EffectJournalState,
    HaltReason,
    JournalState,
    TransactionState,
)
from atoms.core.recovery.journal import (
    AuthorityKind,
    FrontierDirection,
    classify_transaction_authority,
    reconstruct_frontiers,
)
from tests.recovery_support import create_snapshot


@pytest.mark.parametrize(
    ("state", "journals", "expected"),
    [
        (TransactionState.PREPARED, (JournalState.PENDING,), AuthorityKind.CLASSIFY),
        (TransactionState.APPLYING, (JournalState.STARTED,), AuthorityKind.CLASSIFY),
        (TransactionState.APPLIED, (JournalState.DONE,), AuthorityKind.CLASSIFY),
        (TransactionState.ROLLING_BACK, (JournalState.STARTED,), AuthorityKind.CLASSIFY),
        (TransactionState.ROLLING_BACK, (JournalState.UNDO_STARTED,), AuthorityKind.CLASSIFY),
        (TransactionState.ROLLED_BACK, (JournalState.UNDONE,), AuthorityKind.DETACH),
    ],
)
def test_single_effect_legal_languages(state, journals, expected):
    snapshot = create_snapshot(state=state, journal=journals[0])
    assert classify_transaction_authority(snapshot).kind is expected


def test_commit_decision_conflict_has_closed_reason():
    snapshot = create_snapshot(
        state=TransactionState.APPLIED,
        journal=JournalState.DONE,
        commit_decision=CommitDecision.COMMITTED,
    )
    decision = classify_transaction_authority(snapshot)
    assert decision.kind is AuthorityKind.HALT
    assert decision.halt_reason is HaltReason.COMMIT_DECISION_CONFLICT


def test_detached_nonterminal_halts_without_reattachment():
    decision = classify_transaction_authority(create_snapshot(active=False))
    assert decision.kind is AuthorityKind.HALT
    assert decision.halt_reason is HaltReason.ACTIVE_BINDING_MISSING
```

Add a three-effect fixture and lock both rolling-back languages:

```python
@pytest.mark.parametrize(
    "states",
    [
        (JournalState.DONE, JournalState.STARTED, JournalState.PENDING),
        (JournalState.DONE, JournalState.UNDO_STARTED, JournalState.UNDONE),
        (JournalState.DONE, JournalState.UNDONE, JournalState.PENDING),
    ],
)
def test_rolling_back_accepts_reachable_frontiers(three_effect_snapshot, states):
    snapshot = three_effect_snapshot(TransactionState.ROLLING_BACK, states)
    assert classify_transaction_authority(snapshot).kind is AuthorityKind.CLASSIFY


def test_rolling_back_rejects_started_followed_by_undone(three_effect_snapshot):
    snapshot = three_effect_snapshot(
        TransactionState.ROLLING_BACK,
        (JournalState.STARTED, JournalState.UNDONE, JournalState.PENDING),
    )
    decision = classify_transaction_authority(snapshot)
    assert decision.halt_reason is HaltReason.JOURNAL_TOPOLOGY_INVALID


@pytest.mark.parametrize(
    "states",
    [
        (JournalState.PENDING, JournalState.UNDONE, JournalState.PENDING),
        (JournalState.UNDONE, JournalState.PENDING, JournalState.UNDONE),
    ],
)
def test_rolling_back_rejects_undone_after_pending(three_effect_snapshot, states):
    snapshot = three_effect_snapshot(TransactionState.ROLLING_BACK, states)
    decision = classify_transaction_authority(snapshot)
    assert decision.kind is AuthorityKind.HALT
    assert decision.halt_reason is HaltReason.JOURNAL_TOPOLOGY_INVALID


@pytest.mark.parametrize(
    ("state", "states"),
    [
        (TransactionState.PREPARED, (JournalState.DONE,) * 3),
        (
            TransactionState.ROLLED_BACK,
            (JournalState.DONE, JournalState.UNDONE, JournalState.PENDING),
        ),
    ],
)
def test_each_state_uses_its_own_journal_language(
    three_effect_snapshot,
    state,
    states,
):
    decision = classify_transaction_authority(three_effect_snapshot(state, states))
    assert decision.kind is AuthorityKind.HALT
    assert decision.halt_reason is HaltReason.JOURNAL_TOPOLOGY_INVALID


@pytest.mark.parametrize("active", [False, True])
def test_halted_short_circuits_language_rederivation(
    halted_authority_snapshot,
    active,
):
    snapshot = halted_authority_snapshot(
        (JournalState.DONE, JournalState.PENDING, JournalState.UNDONE),
        active=active,
    )
    assert classify_transaction_authority(snapshot).kind is AuthorityKind.STABLE_HALT
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_recovery_journal.py -v`

Expected: FAIL because `recovery.journal` does not exist.

- [ ] **Step 3: Implement the transaction matrix and journal recognizers**

Create `python/src/atoms/core/recovery/journal.py`. Use the following recognizers; do not implement one
permissive regex:

```python
from dataclasses import dataclass
from enum import Enum

from atoms.core.fingerprint import PathState
from atoms.core.recovery.model import HaltReason, JournalState


class AuthorityKind(Enum):
    CLASSIFY = "classify"
    DETACH = "detach"
    NO_RECOVERY = "no_recovery"
    STABLE_HALT = "stable_halt"
    HALT = "halt"


@dataclass(frozen=True, slots=True)
class AuthorityDecision:
    kind: AuthorityKind
    halt_reason: HaltReason | None


class FrontierDirection(Enum):
    FORWARD = "forward"
    REVERSE = "reverse"
    INITIAL = "initial"


@dataclass(frozen=True, slots=True)
class PathFrontier:
    path: str
    direction: FrontierDirection
    effect_index: int | None
    effect_id: str | None
    journal_state: JournalState | None
    expected_state: PathState
    admissible_states: tuple[PathState, ...]


def _all(states, wanted):
    return all(state is wanted for state in states)


def _forward_language(states):
    phase = 0
    for state in states:
        if phase == 0 and state is JournalState.DONE:
            continue
        if phase == 0 and state is JournalState.STARTED:
            phase = 1
            continue
        if state is JournalState.PENDING:
            phase = 2
            continue
        return False
    return True


def _reverse_language(states):
    phase = 0
    for state in states:
        if phase == 0 and state is JournalState.DONE:
            continue
        if phase == 0 and state is JournalState.UNDO_STARTED:
            phase = 1
            continue
        if state is JournalState.UNDONE and phase in {0, 1, 2}:
            phase = 2
            continue
        if state is JournalState.PENDING and phase in {0, 2, 3}:
            phase = 3
            continue
        return False
    return True


def _rolled_back_language(states):
    pending = False
    for state in states:
        if state is JournalState.PENDING:
            pending = True
            continue
        if state is JournalState.UNDONE and not pending:
            continue
        return False
    return True
```

`expected_state` is the continuity baseline: the path state before the selected occurrence for a
forward frontier and after it for a reverse frontier. `admissible_states` is the closed set for the
selected journal state. A `STARTED` frontier therefore retains one baseline while explicitly carrying
its pre/intermediate/post admissible states; consumers never mistake the baseline for the whole set.
Task 5 adds the `three_effect_snapshot` and `halted_authority_snapshot` adapters to `conftest.py`.

Assign languages exactly:

| Transaction state | Required journal language |
| --- | --- |
| `PREPARED` | `_all(states, PENDING)` |
| `APPLYING` | `_forward_language(states)` |
| `APPLIED` | `_all(states, DONE)` |
| `ROLLING_BACK` | `_forward_language(states) or _reverse_language(states)` |
| `COMMITTED` | `_all(states, DONE)` |
| `ROLLED_BACK` | `_rolled_back_language(states)` (`UNDONE* PENDING*`, no `DONE` prefix) |
| `HALTED` | no language selection; return the frozen stable halt |

`classify_transaction_authority` applies this exact precedence:

```text
1. exact state/commit-decision compatibility;
2. HALTED → STABLE_HALT regardless of active binding; coherence was enforced by the snapshot factory;
3. legal journal language for the non-halted state;
4. detached terminal → NO_RECOVERY;
5. detached nonterminal → HALT / ACTIVE_BINDING_MISSING;
6. active ROLLED_BACK → DETACH;
7. otherwise → CLASSIFY.
```

For `COMMITTED`, require `COMMITTED` decision and all `DONE`. For every precommit state and
`ROLLED_BACK`, require `UNCOMMITTED`. `HALTED` preserves either decision.

Add one active and one detached `HALTED` test; both must return `STABLE_HALT` with the exact stored
diagnostic and no attempt to invent an active binding. Build each over a frozen
`(DONE, PENDING, UNDONE)` vector that is not legal for any non-halted state; this locks
the pre-language `HALTED` short circuit.

- [ ] **Step 4: Implement frontier reconstruction**

Define exact dataclasses/enums and implement:

```python
def reconstruct_frontiers(snapshot: RecoverySnapshot) -> tuple[PathFrontier, ...]:
    journal_by_id = {row.effect_id: row.state for row in snapshot.journals}
    index_by_id = {
        effect.effect_id: index
        for index, effect in enumerate(snapshot.compiled.spec.effects)
    }
    reverse = snapshot.transaction_state is TransactionState.ROLLING_BACK
    frontiers = []
    for timeline in snapshot.compiled.timelines:
        occurrence_states = [
            (occurrence, journal_by_id[occurrence.effect_id])
            for occurrence in timeline.occurrences
        ]
        if reverse:
            frontier = _reverse_frontier(timeline.path, occurrence_states, index_by_id)
        else:
            frontier = _forward_frontier(timeline.path, occurrence_states, index_by_id)
        frontiers.append(frontier)
    return tuple(frontiers)
```

`_forward_frontier` chooses the unique `STARTED` occurrence when present, otherwise the last `DONE`;
its baseline is the selected occurrence's `pre` for `STARTED` and `post` for `DONE`.
`_reverse_frontier` chooses the latest occurrence in `DONE | STARTED | UNDO_STARTED`;
all-`UNDONE`/`PENDING` returns `INITIAL` with the timeline's first pre-state. Include tests for
repeated-path timelines and a crash between one `DONE` and the next `STARTED`.

An all-`PENDING` forward timeline also returns `INITIAL` at the timeline's first pre-state:

```python
def test_all_pending_forward_frontier_is_initial():
    snapshot = create_snapshot(
        state=TransactionState.PREPARED,
        journal=JournalState.PENDING,
    )
    frontier = reconstruct_frontiers(snapshot)[0]
    assert frontier.direction is FrontierDirection.INITIAL
    assert frontier.expected_state == snapshot.compiled.timelines[0].occurrences[0].pre
```

Within production `classify_recovery`, every uncommitted active state transitions to
`ROLLING_BACK` before frontier reconstruction. Committed cleanup bypasses frontier reconstruction and
uses Task 8's proof-gated scratch-only helper. Direct Task 5/6 classifier tests still exercise forward
in-flight frontiers.

- [ ] **Step 5: Mutation-lock the load-bearing grammar**

Temporarily replace the `ROLLING_BACK` union of forward/reverse recognizers with one
`DONE* (STARTED | UNDO_STARTED)? UNDONE* PENDING*` implementation.

Run: `uv run pytest tests/test_recovery_journal.py -v`

Expected: the `STARTED`-followed-by-`UNDONE` test fails. Restore the correct implementation.

- [ ] **Step 6: Run focused and full checks**

Run:

```bash
uv run pytest tests/test_recovery_journal.py -v
uv run pytest
uv run ruff check .
uv run pyright
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/atoms/core/recovery/journal.py tests/conftest.py tests/test_recovery_journal.py tests/recovery_support.py
git commit -m "feat(recovery): classify journals and path frontiers"
```

---

### Task 6: Joint file variant classifiers

Implement `ReplaceFile` and `CreateFileNoClobber` as joint live/scratch tables. The classifiers return
semantic steps only after matching the whole tuple. `pre == post` has its own replace table and must
not fall through the overlapping general rows.

**Files:**
- Create: `python/src/atoms/core/recovery/variants.py`
- Create: `python/tests/test_recovery_variants_files.py`
- Modify: `python/tests/recovery_support.py`
- Modify: `python/tests/conftest.py`

**Interfaces:**
- Consumes: Task 2 snapshot maps, Task 3 semantic steps, Task 5 frontiers.
- Produces:
  - internal `EffectDecision(steps, refused, halt_reason, expected, observed)`;
  - `classify_effect(snapshot, effect_index, frontiers) -> EffectDecision`;
  - exact file-state and relation normalization helpers.

Every `TransformEffectTuple.variant` comes from an exact-type table:

```python
_EFFECT_VARIANT = {
    ReplaceFile: EffectVariant.REPLACE_FILE,
    CreateFileNoClobber: EffectVariant.CREATE_FILE_NO_CLOBBER,
    DeletePath: EffectVariant.DELETE_PATH,
    MoveNoClobber: EffectVariant.MOVE_NO_CLOBBER,
    CreateDirectory: EffectVariant.CREATE_DIRECTORY,
}
```

Index this table by `type(effect)`; never derive a free-form variant string from a class name.

- [ ] **Step 1: Write failing replace table tests**

Create `python/tests/test_recovery_variants_files.py` using the registered `replace_case` callable,
which builds a one-effect compiled replace snapshot for arbitrary live/staging observations and
journal state. Parameterize the general table:

Task 6 adds `replace_case`, `noop_replace_case`, `create_file_case`, `pending_drift_case`,
`pending_clean_case`, `pending_scratch_case`, and `undone_drift_case` adapters to `conftest.py` in the
same commit as their `make_*` factories.

```python
import pytest

from atoms.core.recovery import HaltReason, JournalState, PreserveExternal
from atoms.core.recovery.variants import EffectDecisionKind, classify_effect


@pytest.mark.parametrize(
    ("live_name", "staging_name", "journal", "expected"),
    [
        ("pre", "absent", JournalState.STARTED, "undo_without_mutation"),
        ("pre", "prefix", JournalState.STARTED, "remove_scratch"),
        ("pre", "post", JournalState.STARTED, "remove_scratch"),
        ("post", "pre", JournalState.STARTED, "exchange_back"),
        ("post", "external", JournalState.STARTED, "refused_exchange_back"),
        ("post", "pre", JournalState.DONE, "exchange_back"),
        ("post", "absent", JournalState.DONE, "halt"),
        ("post", "pre", JournalState.UNDO_STARTED, "exchange_back"),
        ("pre", "post", JournalState.UNDO_STARTED, "remove_scratch"),
        ("pre", "absent", JournalState.UNDO_STARTED, "already_undone"),
    ],
)
def test_replace_joint_table(replace_case, live_name, staging_name, journal, expected):
    snapshot, frontiers = replace_case(live_name, staging_name, journal)
    decision = classify_effect(snapshot, 0, frontiers)
    assert decision.kind.value == expected
```

Add the no-op precedence matrix:

```python
@pytest.mark.parametrize(
    ("staging_name", "journal", "expected"),
    [
        ("absent", JournalState.STARTED, "undo_without_mutation"),
        ("prefix", JournalState.STARTED, "remove_scratch"),
        ("same", JournalState.STARTED, "remove_scratch"),
        ("same", JournalState.DONE, "remove_scratch"),
        ("absent", JournalState.DONE, "halt"),
        ("same", JournalState.UNDO_STARTED, "remove_scratch"),
        ("absent", JournalState.UNDO_STARTED, "already_undone"),
        ("external", JournalState.STARTED, "halt"),
    ],
)
def test_noop_replace_has_nonoverlapping_precedence(
    noop_replace_case,
    staging_name,
    journal,
    expected,
):
    snapshot, frontiers = noop_replace_case(staging_name, journal)
    assert classify_effect(snapshot, 0, frontiers).kind.value == expected
```

- [ ] **Step 2: Write failing create-file table tests**

In the same file:

```python
@pytest.mark.parametrize(
    ("live_name", "staging_name", "journal", "expected"),
    [
        ("absent", "absent", JournalState.STARTED, "undo_without_mutation"),
        ("absent", "prefix", JournalState.STARTED, "remove_scratch"),
        ("absent", "post", JournalState.STARTED, "remove_scratch"),
        ("post", "absent", JournalState.STARTED, "remove_live_creation"),
        ("post", "post", JournalState.STARTED, "refused_preserve_live"),
        ("external", "post", JournalState.STARTED, "refused_preserve_live"),
        ("external", "absent", JournalState.STARTED, "refused_preserve_live"),
        ("post", "absent", JournalState.DONE, "remove_live_creation"),
        ("post", "absent", JournalState.UNDO_STARTED, "remove_live_creation"),
        ("absent", "post", JournalState.UNDO_STARTED, "remove_scratch"),
        ("absent", "absent", JournalState.UNDO_STARTED, "already_undone"),
    ],
)
def test_create_file_joint_table(create_file_case, live_name, staging_name, journal, expected):
    snapshot, frontiers = create_file_case(live_name, staging_name, journal)
    assert classify_effect(snapshot, 0, frontiers).kind.value == expected
```

For every parameterized test, also assert a foreign or diverged staging observation halts without a
mutation step.

Exercise conservative occurrence-local `COMMITTED` rows directly in Task 6:

```python
@pytest.mark.parametrize(
    ("live_name", "staging_name", "expected"),
    [
        ("post", "pre", "remove_scratch"),
        ("post", "absent", "no_action"),
    ],
)
def test_replace_committed_cleanup_rows(
    replace_case,
    live_name,
    staging_name,
    expected,
):
    snapshot, frontiers = replace_case(
        live_name,
        staging_name,
        JournalState.DONE,
        committed=True,
    )
    assert classify_effect(snapshot, 0, frontiers).kind.value == expected


@pytest.mark.parametrize(
    ("staging_name", "expected"),
    [("same", "remove_scratch"), ("absent", "no_action")],
)
def test_noop_replace_committed_cleanup_rows(
    noop_replace_case,
    staging_name,
    expected,
):
    snapshot, frontiers = noop_replace_case(
        staging_name,
        JournalState.DONE,
        committed=True,
    )
    assert classify_effect(snapshot, 0, frontiers).kind.value == expected


@pytest.mark.parametrize(
    ("staging_name", "expected"),
    [("absent", "no_action"), ("post", "halt")],
)
def test_create_file_committed_cleanup_rows(
    create_file_case,
    staging_name,
    expected,
):
    snapshot, frontiers = create_file_case(
        "post",
        staging_name,
        JournalState.DONE,
        committed=True,
    )
    assert classify_effect(snapshot, 0, frontiers).kind.value == expected
```

These `classify_effect` rows require the effect-local live tuple because no transaction-level
final-surface proof has been supplied. They remain conservative direct-classifier coverage; Task 8's
production committed path uses a distinct internal helper only after proving the complete final
surface, which is required for repeated paths.

- [ ] **Step 3: Write failing non-in-flight evidence tests**

```python
def test_pending_live_drift_is_preserved_and_refused(pending_drift_case):
    snapshot, frontiers = pending_drift_case()
    decision = classify_effect(snapshot, 0, frontiers)
    assert decision.kind is EffectDecisionKind.PRESERVE_EXTERNAL
    assert decision.refused
    assert decision.halt_reason is None
    assert any(type(step) is PreserveExternal for step in decision.steps)


def test_pending_initial_tuple_is_explicit_no_action(pending_clean_case):
    snapshot, frontiers = pending_clean_case()
    decision = classify_effect(snapshot, 0, frontiers)
    assert decision.kind is EffectDecisionKind.NO_ACTION
    assert decision.steps == ()


def test_pending_scratch_survivor_halts(pending_scratch_case):
    snapshot, frontiers = pending_scratch_case()
    decision = classify_effect(snapshot, 0, frontiers)
    assert decision.halt_reason is HaltReason.EFFECT_TUPLE_UNATTRIBUTABLE


def test_undone_requires_initial_surface_and_absent_scratch(undone_drift_case):
    snapshot, frontiers = undone_drift_case()
    assert classify_effect(snapshot, 0, frontiers).kind is EffectDecisionKind.HALT
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_recovery_variants_files.py -v`

Expected: FAIL because `recovery.variants` does not exist.

- [ ] **Step 5: Implement normalized tuple evidence**

Create `python/src/atoms/core/recovery/variants.py` with internal exact types:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.errors import ProtocolError
from atoms.core.recovery.journal import PathFrontier
from atoms.core.recovery.model import HaltReason, JournalState
from atoms.core.recovery.plan import (
    JointObservation,
    PreserveExternal,
    RecoveryStep,
    RemoveScratch,
    SettlementKind,
    TransformEffectTuple,
)
from atoms.core.recovery.snapshot import RecoverySnapshot


class EntryClass(Enum):
    ABSENT = "absent"
    PRE = "pre"
    POST = "post"
    PREFIX = "prefix"
    EXTERNAL = "external"


class EffectDecisionKind(Enum):
    NO_ACTION = "no_action"
    PRESERVE_EXTERNAL = "preserve_external"
    UNDO_WITHOUT_MUTATION = "undo_without_mutation"
    REMOVE_SCRATCH = "remove_scratch"
    EXCHANGE_BACK = "exchange_back"
    REFUSED_EXCHANGE_BACK = "refused_exchange_back"
    ALREADY_UNDONE = "already_undone"
    REMOVE_LIVE_CREATION = "remove_live_creation"
    REFUSED_PRESERVE_LIVE = "refused_preserve_live"
    RESTORE_TOMBSTONE = "restore_tombstone"
    REMOVE_ANCHOR = "remove_anchor"
    RESTORE_SOURCE = "restore_source"
    REMOVE_DESTINATION = "remove_destination"
    RESTORE_FROM_ANCHOR = "restore_from_anchor"
    REFUSED_PRESERVE_DESTINATION = "refused_preserve_destination"
    REMOVE_WORK = "remove_work"
    REMOVE_LIVE_DIRECTORY = "remove_live_directory"
    REMOVE_DUAL_NAME_DIRECTORY = "remove_dual_name_directory"
    HALT = "halt"


@dataclass(frozen=True, slots=True)
class EffectDecision:
    kind: EffectDecisionKind
    steps: tuple[RecoveryStep, ...]
    refused: bool
    halt_reason: HaltReason | None
    expected: JointObservation
    observed: JointObservation


def _halt(
    expected: JointObservation,
    observed: JointObservation,
) -> EffectDecision:
    return EffectDecision(
        kind=EffectDecisionKind.HALT,
        steps=(),
        refused=False,
        halt_reason=HaltReason.EFFECT_TUPLE_UNATTRIBUTABLE,
        expected=expected,
        observed=observed,
    )
```

Add `_classify_entry(entry, *, pre, post, build_relation)` with this exact precedence:
`ObservedAbsent → ABSENT`; exact `pre` before exact `post`; exact `post`; `STRICT_PREFIX → PREFIX`;
otherwise `EXTERNAL`. The no-op replace classifier bypasses this pre-before-post ambiguity by using its
dedicated table.

Add step builders `_transform`, `_remove_scratch`, and `_preserve`. Each captures the complete current
joint observation as a provisional `expected_before` and constructs the exact logical `result_after`;
no builder may drop the sibling live/scratch node. Builders are identity-conservative: every result
reuses tokens from the input tuple according to the declared transfer and no builder calls
`EntryIdentity()`. Task 8 rebases provisional mutating-step observations against the pure cursor after
its preceding metadata transition before the steps enter a `RecoveryPlan`.

- [ ] **Step 6: Implement the two exact decision tables**

Before variant dispatch, handle the two non-in-flight states for every variant:

```text
PENDING:
    scratch absent + every owned path at its PathFrontier.expected_state → NO_ACTION;
    scratch absent + any owned path drift → PRESERVE_EXTERNAL for those paths, refused outcome;
    any scratch survivor → halt and preserve it (the effect never acquired mutation authority).
UNDONE:
    scratch absent + every owned path at its initial/reverse-frontier state → NO_ACTION;
    any other tuple → halt and preserve all evidence.
```

Dispatch by exact effect type:

```python
_CLASSIFIERS = {
    ReplaceFile: _classify_replace,
    CreateFileNoClobber: _classify_create_file,
    DeletePath: _classify_delete,
    MoveNoClobber: _classify_move,
    CreateDirectory: _classify_directory,
}


def classify_effect(
    snapshot: RecoverySnapshot,
    effect_index: int,
    frontiers: tuple[PathFrontier, ...],
) -> EffectDecision:
    effect = snapshot.compiled.spec.effects[effect_index]
    journal = snapshot.journals[effect_index].state
    if journal is JournalState.PENDING:
        return _classify_pending(snapshot, effect, frontiers)
    if journal is JournalState.UNDONE:
        return _classify_undone(snapshot, effect, frontiers)
    try:
        classifier = _CLASSIFIERS[type(effect)]
    except KeyError as exc:
        raise ProtocolError("effect variant is outside A3's closed set") from exc
    return classifier(snapshot, effect, frontiers)
```

The Task 7 classifier names must exist as functions that currently return `_halt()` so the dispatch
table is total; Task 7 replaces their bodies.

Encode these replace outcomes exactly:

| State | Tuple | Decision label and semantic steps |
| --- | --- | --- |
| `STARTED` | `(pre,A)` | `undo_without_mutation`; no filesystem step |
| `STARTED` | `(pre,prefix|post)` | `remove_scratch`; `RemoveScratch` |
| `STARTED` | `(post,pre)` | `exchange_back`; `TransformEffectTuple(RESTORE_PRE)` then `RemoveScratch` |
| `STARTED` | `(post,X)` | `refused_exchange_back`; restore X, preserve it, remove post scratch |
| `UNDO_STARTED` | `(post,pre)` | `exchange_back`; restore then remove |
| `UNDO_STARTED` | `(pre,post)` | `remove_scratch` |
| `UNDO_STARTED` | `(pre,A)` | `already_undone` |
| uncommitted `DONE` | `(post,pre)` only | transition-ready `exchange_back` |
| direct classifier, committed `DONE` | `(post,pre)` / `(post,A)` | conservative `REMOVE_SCRATCH` / `NO_ACTION` |

For `pre == post`, encode the dedicated design §9.1 matrix before the general classifier. `(same,X)`
always halts; uncommitted `DONE (same,A)` halts. Committed `(same,same)` removes staging and
committed `(same,A)` is `NO_ACTION`.

Encode these create-file outcomes exactly:

| State | Tuple | Decision label and semantic steps |
| --- | --- | --- |
| `STARTED` | `(A,A|prefix|post)` | clean attributable staging, no live removal |
| `STARTED` | `(post,A)` | quarantine/remove live post |
| `STARTED` | `(post,present attributable staging)` | preserve live, clean staging, refuse |
| `STARTED` | `(X,attributable staging|A)` | preserve X, clean attributable staging, refuse |
| `UNDO_STARTED` | `(post,A)` | quarantine/remove live post |
| `UNDO_STARTED` | `(A,post)` | remove quarantined post |
| `UNDO_STARTED` | `(A,A)` | already undone |
| uncommitted `DONE`; direct committed row | `(post,A)` only | remove live for rollback / conservative committed `NO_ACTION` |

Every tuple not listed returns `_halt(expected, observed)` and no semantic mutation. Non-halt
decisions carry the same two complete joint observations with `halt_reason=None`, so later plan or
diagnostic construction never has to re-read or reclassify the tuple.

- [ ] **Step 7: Run and mutation-check file tables**

Run: `uv run pytest tests/test_recovery_variants_files.py -v`

Then temporarily route no-op replace through the general table.

Run: `uv run pytest tests/test_recovery_variants_files.py -v`

Expected: the no-op precedence cases fail. Restore the dedicated table.

- [ ] **Step 8: Run full checks**

Run:

```bash
uv run pytest
uv run ruff check .
uv run pyright
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add src/atoms/core/recovery/variants.py tests/conftest.py \
  tests/test_recovery_variants_files.py tests/recovery_support.py
git commit -m "feat(recovery): classify file effect recovery"
```

---

### Task 7: Joint delete, move, and directory classifiers

Complete the remaining variant functions. Move and directory classification use equality relations
between snapshot-local identities; diagnostics and semantic plan metadata retain only named-slot
`SAME`/`DIFFERENT` relations.

**Files:**
- Modify: `python/src/atoms/core/recovery/variants.py`
- Create: `python/tests/test_recovery_variants_paths.py`
- Modify: `python/tests/recovery_support.py`
- Modify: `python/tests/conftest.py`

**Interfaces:**
- Consumes: Task 6 `EffectDecision`, tuple normalization, and step builders.
- Produces: complete `_classify_delete`, `_classify_move`, `_classify_directory`; total
  `classify_effect` for all five exact variants.

- [ ] **Step 1: Write failing delete tests**

Create `python/tests/test_recovery_variants_paths.py`:

Task 7 adds `delete_case`, `move_case`, and `directory_case` adapters to `conftest.py` in the same
commit as their `make_*` factories.

```python
import pytest

from atoms.core.recovery import JournalState
from atoms.core.recovery.variants import classify_effect


@pytest.mark.parametrize(
    ("live_name", "tombstone_name", "journal", "expected"),
    [
        ("pre", "absent", JournalState.STARTED, "undo_without_mutation"),
        ("absent", "pre", JournalState.STARTED, "restore_tombstone"),
        ("external", "pre", JournalState.STARTED, "halt"),
        ("absent", "pre", JournalState.DONE, "restore_tombstone"),
        ("absent", "absent", JournalState.DONE, "halt"),
        ("absent", "pre", JournalState.UNDO_STARTED, "restore_tombstone"),
        ("pre", "absent", JournalState.UNDO_STARTED, "already_undone"),
        ("absent", "absent", JournalState.UNDO_STARTED, "halt"),
    ],
)
def test_delete_joint_table(delete_case, live_name, tombstone_name, journal, expected):
    snapshot, frontiers = delete_case(live_name, tombstone_name, journal)
    assert classify_effect(snapshot, 0, frontiers).kind.value == expected
```

Run the same matrix once with a regular-file `pre` and once with a symlink `pre`. Assert the symlink
case carries no identity relation.

- [ ] **Step 2: Write failing move tests**

Use shared/reused `EntryIdentity` objects to create equality partitions:

```python
@pytest.mark.parametrize(
    ("source", "destination", "anchor", "relation", "expected"),
    [
        ("pre", "absent", "absent", None, "undo_without_mutation"),
        ("pre", "absent", "pre", "source_anchor_same", "remove_anchor"),
        ("absent", "pre", "pre", "destination_anchor_same", "restore_source"),
        ("pre", "pre", "pre", "all_same", "remove_destination"),
        ("absent", "absent", "pre", None, "restore_from_anchor"),
        ("pre", "external", "pre", "source_anchor_same", "refused_preserve_destination"),
    ],
)
def test_move_joint_table(
    move_case,
    source,
    destination,
    anchor,
    relation,
    expected,
):
    snapshot, frontiers = move_case(source, destination, anchor, relation)
    assert classify_effect(snapshot, 0, frontiers).kind.value == expected
```

Add negative cases for a foreign source, wrong destination/anchor identity, missing anchor at
uncommitted `DONE`, and postimage without anchor. All halt with no mutation step.

- [ ] **Step 3: Write failing directory tests**

```python
@pytest.mark.parametrize(
    ("live", "work", "relation", "unmodeled", "expected"),
    [
        ("absent", "absent", None, False, "undo_without_mutation"),
        ("absent", "post", None, False, "remove_work"),
        ("post", "absent", None, False, "remove_live_directory"),
        ("post", "post", "same", False, "remove_dual_name_directory"),
        ("external", "post", "different", False, "refused_preserve_live"),
        ("post", "absent", None, True, "halt"),
    ],
)
def test_create_directory_joint_table(
    directory_case,
    live,
    work,
    relation,
    unmodeled,
    expected,
):
    snapshot, frontiers = directory_case(live, work, relation, unmodeled)
    assert classify_effect(snapshot, 0, frontiers).kind.value == expected
```

Add a resolved-topology case where declared descendants are present before reverse ordering and absent
after their simulated reversal. The parent directory may be removed only in the latter prefix.

Exercise conservative occurrence-local `COMMITTED` rows directly in Task 7:

```python
@pytest.mark.parametrize(
    ("tombstone_name", "expected"),
    [("pre", "remove_scratch"), ("absent", "no_action")],
)
def test_delete_committed_cleanup_rows(delete_case, tombstone_name, expected):
    snapshot, frontiers = delete_case(
        "absent",
        tombstone_name,
        JournalState.DONE,
        committed=True,
    )
    assert classify_effect(snapshot, 0, frontiers).kind.value == expected


@pytest.mark.parametrize(
    ("anchor", "relation", "expected"),
    [
        ("pre", "destination_anchor_same", "remove_anchor"),
        ("absent", None, "no_action"),
    ],
)
def test_move_committed_cleanup_rows(move_case, anchor, relation, expected):
    snapshot, frontiers = move_case(
        "absent",
        "pre",
        anchor,
        relation,
        committed=True,
    )
    assert classify_effect(snapshot, 0, frontiers).kind.value == expected


@pytest.mark.parametrize(
    ("work", "relation", "expected"),
    [("absent", None, "no_action"), ("post", "same", "halt")],
)
def test_directory_committed_cleanup_rows(directory_case, work, relation, expected):
    snapshot, frontiers = directory_case(
        "post",
        work,
        relation,
        False,
        committed=True,
    )
    assert classify_effect(snapshot, 0, frontiers).kind.value == expected
```

As in Task 6, these ordinary `classify_effect` rows do not carry the transaction-level final-surface
proof. Task 8 keeps them unchanged and adds direct tests for the separate proof-gated committed
scratch helper, including superseded persistent evidence.

- [ ] **Step 4: Run tests to verify Task 6 stubs fail**

Run: `uv run pytest tests/test_recovery_variants_paths.py -v`

Expected: FAIL because each remaining classifier returns `halt`.

- [ ] **Step 5: Implement delete and move tables**

Replace the stubs with the exact tables:

| Variant/state | Accepted tuple | Semantic outcome |
| --- | --- | --- |
| Delete `STARTED` | `(pre,A)` | no mutation |
| Delete `STARTED` | `(A,pre)` | restore tombstone no-clobber |
| Delete `UNDO_STARTED` | `(A,pre)` / `(pre,A)` | retry restore / already restored |
| Delete uncommitted `DONE` | `(A,pre)` only | restore |
| Delete direct committed row | `(A,pre)` / `(A,A)` | conservative `REMOVE_SCRATCH` / `NO_ACTION` |
| Move any forward/reverse frontier | `(pre,A,A)` | nothing landed |
| Move | `(pre,A,pre)`, source `==` anchor | remove anchor |
| Move | `(A,pre,pre)`, destination `==` anchor | restore source |
| Move | `(pre,pre,pre)`, all equal | remove destination |
| Move | `(A,A,pre)` | restore source from anchor |
| Move | source `==` anchor plus foreign destination | preserve destination, remove anchor, refuse |
| Move uncommitted `DONE` | `(A,pre,pre)`, destination `==` anchor only | ordinary rollback |
| Move direct committed row | `(A,pre,pre)` same / `(A,pre,A)` | conservative `REMOVE_ANCHOR` / `NO_ACTION` |

Move result steps must converge through `(pre,A,pre)` with source `==` anchor, then remove the anchor.
Record only `DiagnosticIdentityRelation("source", "anchor", SAME)` or its destination equivalent in
steps/diagnostics; never store the tokens.

- [ ] **Step 6: Implement directory table and descendant ordering**

Encode:

| Tuple | Semantic outcome |
| --- | --- |
| `(A,A)` | nothing landed |
| `(A,attributable work)` | remove empty work |
| `(post,A)` | quarantine live to work, validate empty, remove |
| `(post,post)`, live `==` work | after descendants reverse: remove stale work name, then ordinary landed rollback |
| live blocker + different attributable work | preserve blocker, remove work, refuse |

Any unmodeled child needed for removal returns `DIRECTORY_NOT_EMPTY`. Under uncommitted `DONE`, accept
only `(post,A)` and only after declared descendants have reversed. The ordinary direct committed row
accepts `(post,A)` as `NO_ACTION`; Task 8's production committed helper instead consumes only absent
`WORK` after the complete final-surface proof. A `DONE` work survivor is a contradiction.

Use `RecoveryTopology` parent edges, not lexical string prefixes, to enumerate modeled direct
descendants.

- [ ] **Step 7: Run focused and full checks**

Run:

```bash
uv run pytest tests/test_recovery_variants_paths.py -v
uv run pytest tests/test_recovery_variants_files.py -v
uv run pytest
uv run ruff check .
uv run pyright
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/atoms/core/recovery/variants.py tests/conftest.py \
  tests/test_recovery_variants_paths.py tests/recovery_support.py
git commit -m "feat(recovery): classify path and directory recovery"
```

---

### Task 8: All-evidence-first transaction classifier

Compose authority, frontiers, and variant decisions into one complete ordered plan. Classification
must inspect every persistent/scratch observation before emitting any semantic step, and it must freeze
token-free diagnostics for every halt.

**Files:**
- Create: `python/src/atoms/core/recovery/diagnostics.py`
- Create: `python/src/atoms/core/recovery/classifier.py`
- Create: `python/tests/test_recovery_classifier.py`
- Modify: `python/src/atoms/core/recovery/__init__.py`
- Modify: `python/src/atoms/core/recovery/variants.py`
- Modify: `python/src/atoms/core/recovery/reducer.py`
- Modify: `python/tests/recovery_support.py`
- Modify: `python/tests/conftest.py`
- Modify: `python/tests/test_recovery_reducer.py`
- Modify: `python/tests/test_recovery_variants_files.py`
- Modify: `python/tests/test_recovery_variants_paths.py`
- Modify: `docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md`
- Modify: `docs/plans/2026-07-28-a3-recovery-reference-model-design.md`
- Modify: `docs/plans/2026-07-28-plan-a3-recovery-reference-model.md`
- Modify: `docs/deferred-obligation-ledger.md`

**Interfaces:**
- Consumes: Tasks 2–7, including Task 4's normative `_apply_steps` reducer kernel.
- Produces: public `classify_recovery(snapshot: RecoverySnapshot) -> RecoveryPlan`.

- [ ] **Step 1: Write failing disposition and ordering tests**

Create `python/tests/test_recovery_classifier.py`:

Task 8 adds `two_effect_snapshot`, `committed_snapshot`, `prepared_drift_snapshot`,
`halted_snapshot`, `recovery_case`, `committed_halt_source`,
`repeated_path_mid_plan_halt`, `committed_repeated_replace_snapshot`,
`committed_superseded_cleanup_case`, and `snapshot_pair_differing_only_dependencies` adapters to
`conftest.py` in the same commit as their `make_*` factories.

```python
import pytest

import atoms.core.recovery.classifier as classifier_module
from atoms.core.recovery import (
    CommitDecision,
    DetachActive,
    HaltReason,
    JournalState,
    PlanDisposition,
    RemoveScratch,
    RollbackResult,
    TransactionState,
    TransitionEffectState,
    TransitionTransactionState,
    TransformEffectTuple,
    apply_recovery_plan,
    classify_recovery,
)
from tests.recovery_support import create_snapshot


def test_detached_terminal_is_no_recovery():
    snapshot = create_snapshot(
        state=TransactionState.ROLLED_BACK,
        journal=JournalState.UNDONE,
        active=False,
    )
    plan = classify_recovery(snapshot)
    assert plan.disposition is PlanDisposition.NO_RECOVERY
    assert plan.steps == ()


def test_active_rolled_back_detaches_without_filesystem_step():
    snapshot = create_snapshot(
        state=TransactionState.ROLLED_BACK,
        journal=JournalState.UNDONE,
        active=True,
    )
    plan = classify_recovery(snapshot)
    assert plan.disposition is PlanDisposition.DETACH_TERMINAL
    assert plan.steps == (DetachActive(),)


def test_uncommitted_rollback_orders_metadata_around_effect_steps():
    source = create_snapshot()
    plan = classify_recovery(source)
    assert plan.disposition is PlanDisposition.ROLL_BACK
    assert isinstance(plan.steps[0], TransitionTransactionState)
    assert plan.steps[0].to_state is TransactionState.ROLLING_BACK
    assert any(isinstance(step, TransitionEffectState) for step in plan.steps)
    assert isinstance(plan.steps[-2], TransitionTransactionState)
    assert plan.steps[-2].rollback_result is RollbackResult.RESTORED
    assert isinstance(plan.steps[-1], DetachActive)


def test_detached_nonterminal_gets_stable_closed_halt():
    plan = classify_recovery(create_snapshot(active=False))
    assert plan.disposition is PlanDisposition.HALT
    assert plan.diagnostic.reason is HaltReason.ACTIVE_BINDING_MISSING
    assert plan.diagnostic.pre_halt_state is TransactionState.APPLYING
```

Add committed cleanup and prepared drift tests:

```python
def test_committed_classification_never_emits_rollback(committed_snapshot):
    plan = classify_recovery(committed_snapshot)
    assert plan.disposition is PlanDisposition.COMMITTED_CLEANUP
    assert all(
        not (
            isinstance(step, TransitionTransactionState)
            and step.to_state is TransactionState.ROLLING_BACK
        )
        for step in plan.steps
    )


def test_prepared_external_drift_is_refused_without_project_mutation(prepared_drift_snapshot):
    plan = classify_recovery(prepared_drift_snapshot)
    assert plan.disposition is PlanDisposition.ROLL_BACK_REFUSED
    assert plan.rollback_result is RollbackResult.EXTERNAL_DRIFT_PRESERVED
    assert not any(type(step).__name__ in {"TransformEffectTuple", "RemoveScratch"} for step in plan.steps)
```

The committed branch has a proof-gated contract distinct from ordinary occurrence-local
`classify_effect`. Add a factory-issued repeated-path replace chain `F→G→H`, with both journals
`DONE`, final live `H`, and retained staging preimages `F` and `G`. Before the correction this must
produce a failing regression because the first replace is incorrectly compared with live `H`.

Add internal `variants.classify_committed_cleanup(snapshot, effect_index)`. Its caller precondition is
an exact `COMMITTED`/all-`DONE` snapshot whose complete compiled final surface has already been proved.
The helper observes only the named scratch slot:

- exact retained replace staging, delete tombstone, or move anchor produces scratch removal;
- absence for those roles is already cleaned;
- create-file staging and create-directory `WORK` must be absent; and
- every retained state/kind mismatch or create survivor halts.

Ordinary `classify_effect` remains unchanged and conservative without that transaction-level proof.
Direct helper tests use globally final-surface-matching snapshots and include superseded Replace,
Delete, and Move persistent evidence.

Committed `RemoveScratch` uses literal scratch-only joint observations: both persistent tuples and
both parent-occupancy tuples are empty. Narrowly extend Task 4's reducer so that shape is accepted only
for `RemoveScratch` on exact `COMMITTED`, with the named effect at exact `DONE` and exactly its compiled
retained scratch key/role. Noncommitted removal still requires complete effect coverage. Add positive
and negative locks for the state, journal, key, and role boundaries.

- [ ] **Step 2: Write the all-evidence-first regression**

Construct a two-effect snapshot where the later effect is repairable and the earlier effect becomes
unattributable only after the later one is projected backward. Monkeypatch the variant module to
record calls:

```python
def test_classifier_checks_every_effect_before_building_action_steps(
    two_effect_snapshot,
    monkeypatch,
):
    seen = []
    original = classifier_module.classify_effect

    def recording(snapshot, effect_index, frontiers):
        seen.append(effect_index)
        return original(snapshot, effect_index, frontiers)

    monkeypatch.setattr(classifier_module, "classify_effect", recording)
    plan = classify_recovery(two_effect_snapshot(first="halt", second="repairable"))
    assert seen == [1, 0]
    assert plan.disposition is PlanDisposition.HALT
    assert not any(type(step).__name__ in {"TransformEffectTuple", "RemoveScratch"} for step in plan.steps)
```

Add a cursor-provenance lock with a repeated-path source whose later effect is repairable and whose
earlier effect halts only after that repair is projected:

```python
def test_mid_plan_halt_labels_projected_journals_without_rewriting_durable_source(
    repeated_path_mid_plan_halt,
):
    source = repeated_path_mid_plan_halt
    plan = classify_recovery(source)
    diagnostic = plan.diagnostic
    assert diagnostic.journals == source.journals
    assert diagnostic.projected_journals != diagnostic.journals
    assert diagnostic.projected_transaction_state is TransactionState.ROLLING_BACK
```

Task 8 also registers `repeated_path_mid_plan_halt` and its explicit support factory in
`conftest.py`.

Add a repeated-path case with two completed replaces on one path. The second replace's rollback must
be projected first; the first replace step's `expected_before` must name the intermediate state
restored by that projection, not the source snapshot's final live state.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_recovery_classifier.py -v`

Expected: FAIL because `classify_recovery` is not exported.

- [ ] **Step 4: Implement token-free halt construction**

Create `python/src/atoms/core/recovery/diagnostics.py` with `_diagnostic_paths`,
`_project_entries`, `_project_identity_relations`, and `_diagnostic`. The module imports only closed
model/plan/snapshot values; it does not classify. Both `classifier.py` and Task 9's
`authorization.py` import these helpers from `diagnostics.py`. Add:

```python
def _diagnostic(
    source: RecoverySnapshot,
    *,
    reason: HaltReason,
    projected: RecoverySnapshot | None = None,
    effect_id: str | None = None,
    expected: JointObservation | None = None,
    observed: JointObservation | None = None,
) -> HaltDiagnostic:
    evidence_snapshot = source if projected is None else projected
    return HaltDiagnostic(
        pre_halt_state=source.transaction_state,
        commit_decision=source.commit_decision,
        journals=source.journals,
        projected_transaction_state=evidence_snapshot.transaction_state,
        projected_journals=evidence_snapshot.journals,
        effect_id=effect_id,
        paths=_diagnostic_paths(expected, observed),
        expected=_project_entries(expected),
        observed=_project_entries(observed),
        identity_relations=(
            *_project_identity_relations("expected", expected),
            *_project_identity_relations("observed", observed),
        ),
        reason=reason,
        operator_action=(
            OperatorAction.REPAIR_DURABLE_METADATA
            if reason in {
                HaltReason.JOURNAL_TOPOLOGY_INVALID,
                HaltReason.COMMIT_DECISION_CONFLICT,
                HaltReason.ACTIVE_BINDING_MISSING,
            }
            else OperatorAction.INSPECT_PRESERVED_EVIDENCE
        ),
    )
```

Create `python/src/atoms/core/recovery/classifier.py` and import `_diagnostic` from
`diagnostics.py`; classifier owns `_halt_plan`, not diagnostic projection.

The durable fields always come from the snapshot to which the halt transition will be applied.
`projected_*` fields identify the pure cursor whose tuple produced `expected` and `observed`. For an
immediate authority halt they equal the durable fields. For a contradiction found after reversing
later effects in `_rollback_plan`, `_halt_plan` receives `source` for durable facts and `cursor` as
`projected`; no semantic step has executed, so it must not report cursor journals as durable. An
authorization mismatch is bound to its already-durable prefix snapshot, so both sets again agree.

`_project_entries` converts every observed entry to `DiagnosticEntry`; it copies state,
`has_unmodeled_child`, and build relation but never identity. `_project_identity_relations` compares
identities only within the one tuple passed to it and emits sorted, namespace-qualified named-slot
`SAME`/`DIFFERENT` relations. It never compares an `expected` token with an `observed` token, because a
fresh authorization observation may use a different token universe. Its result must remain unchanged
when every token inside either tuple is consistently renamed.

`_halt_plan(source, reason, effect_id=None, expected=None, observed=None, *,
projected=None)` creates one `TransitionTransactionState(current, HALTED, None, diagnostic)` for a
first halt. It passes both `source` and `projected` to `_diagnostic`. For an already halted snapshot it
returns the stored diagnostic and no steps.

- [ ] **Step 5: Implement classification pipeline and plan ordering**

Implement exactly:

```python
def classify_recovery(snapshot: RecoverySnapshot) -> RecoveryPlan:
    if type(snapshot) is not RecoverySnapshot:
        raise ProtocolError("snapshot must be a factory-issued RecoverySnapshot")

    authority = classify_transaction_authority(snapshot)
    if authority.kind is AuthorityKind.NO_RECOVERY:
        return _new_no_recovery_plan(snapshot)
    if authority.kind is AuthorityKind.STABLE_HALT:
        return _stable_halt_plan(snapshot)
    if authority.kind is AuthorityKind.HALT:
        return _halt_plan(snapshot, authority.halt_reason)
    if authority.kind is AuthorityKind.DETACH:
        return _terminal_detach_plan(snapshot)

    if snapshot.transaction_state is TransactionState.COMMITTED:
        return _committed_plan(snapshot)
    return _rollback_plan(snapshot)
```

`_rollback_plan` owns a pure planning cursor and uses Task 4's exact step reducer:

```python
def _rollback_plan(source: RecoverySnapshot) -> RecoveryPlan:
    cursor = source
    steps: list[RecoveryStep] = []
    refused = False

    if cursor.transaction_state is not TransactionState.ROLLING_BACK:
        transition = TransitionTransactionState(
            from_state=cursor.transaction_state,
            to_state=TransactionState.ROLLING_BACK,
            rollback_result=None,
            halt_diagnostic=None,
        )
        steps.append(transition)
        cursor = _apply_steps(cursor, (transition,))

    for index in range(len(source.compiled.spec.effects) - 1, -1, -1):
        frontiers = reconstruct_frontiers(cursor)
        decision = classify_effect(cursor, index, frontiers)
        if decision.halt_reason is not None:
            effect = source.compiled.spec.effects[index]
            return _halt_plan(
                source,
                decision.halt_reason,
                effect.effect_id,
                decision.expected,
                decision.observed,
                projected=cursor,
            )
        refused = refused or decision.refused
        local_steps = _reverse_effect_steps(cursor, index, decision)
        steps.extend(local_steps)
        cursor = _apply_steps(cursor, local_steps)

    result = (
        RollbackResult.EXTERNAL_DRIFT_PRESERVED
        if refused
        else RollbackResult.RESTORED
    )
    terminal = (
        TransitionTransactionState(
            from_state=TransactionState.ROLLING_BACK,
            to_state=TransactionState.ROLLED_BACK,
            rollback_result=result,
            halt_diagnostic=None,
        ),
        DetachActive(),
    )
    steps.extend(terminal)
    cursor = _apply_steps(cursor, terminal)
    if cursor.transaction_state is not TransactionState.ROLLED_BACK or cursor.active:
        raise ProtocolError("terminal rollback projection did not settle")
    return _new_action_plan(
        bound_snapshot=source,
        disposition=(
            PlanDisposition.ROLL_BACK_REFUSED
            if refused
            else PlanDisposition.ROLL_BACK
        ),
        steps=tuple(steps),
    )
```

`_reverse_effect_steps` orders:

```text
PENDING or UNDONE: decision semantic/preservation steps only
STARTED or DONE: TransitionEffectState(current → UNDO_STARTED)
decision semantic steps
STARTED, DONE, or UNDO_STARTED: TransitionEffectState(UNDO_STARTED → UNDONE)
```

Variant classifiers return provisional semantic steps together with the original classification
evidence. `_reverse_effect_steps` must bind those steps to the cursor on which they will execute:
import `replace` from `dataclasses` and `_normalize_joint_observation` from `reducer.py`.

```python
def _bind_effect_steps(cursor, provisional_steps):
    bound = []
    current = cursor
    for provisional in provisional_steps:
        step = provisional
        if type(provisional) in {TransformEffectTuple, RemoveScratch}:
            expected = _joint_from_cursor(current, provisional.expected_before)
            result_after = _normalize_joint_observation(
                current,
                provisional.result_after,
            )
            step = replace(
                provisional,
                expected_before=expected,
                result_after=result_after,
            )
        bound.append(step)
        current = _apply_steps(current, (step,))
    return tuple(bound), current
```

`_joint_from_cursor` uses the template's exact persistent path, scratch effect/role, and parent-node
keys to select the corresponding current cursor values and recompute parent occupancy. It then calls
`_normalize_joint_observation`; it never copies a stale relation or allocates an identity.

For `STARTED`/`DONE`, `_reverse_effect_steps` first applies the provisional
`TransitionEffectState(... -> UNDO_STARTED)` to a private cursor, then calls `_bind_effect_steps`.
For `PENDING`/`UNDONE` it binds against the incoming cursor. It appends the final
`UNDO_STARTED -> UNDONE` transition only after the returned semantic cursor is settled. The
classification `decision.expected`/`decision.observed` remain unchanged for diagnostics.

The cursor is never exposed and performs no I/O. It exists so repeated-path and ancestor-dependent
effects are classified against exactly the prefix their plan step will later see. If a later
classification halts, the returned `HaltPlan` is bound to `source` and contains no accumulated action
steps; the simulated cursor has not mutated real evidence.

Add a focused reduction lock for the rebased construction relation:

```python
def test_post_transition_step_precondition_is_rebased():
    source = create_snapshot()
    plan = classify_recovery(source)
    mutating = next(
        step
        for step in plan.steps
        if type(step) in {TransformEffectTuple, RemoveScratch}
    )
    assert mutating.expected_before.scratch[0].file_build_relation is None
    terminal = apply_recovery_plan(source, plan)
    assert terminal.transaction_state is TransactionState.ROLLED_BACK
```

`_committed_plan` first verifies the complete compiled final surface from the transaction's one current
observation per persistent path. It then validates the complete scratch vector through
`classify_committed_cleanup`, without `reconstruct_frontiers` or ordinary `classify_effect`. Exact
retained replace/delete/move scratch yields scratch-only `RemoveScratch`; absence is already cleaned.
Create-file staging and directory `WORK` must be absent. A final-surface mismatch halts with
`COMMITTED_SURFACE_MISMATCH`; any scratch mismatch halts with
`EFFECT_TUPLE_UNATTRIBUTABLE` and no cleanup steps. Only after all evidence passes are removal steps
built in compiled order and followed by `DetachActive`. Rollback steps are forbidden.

`_terminal_detach_plan` produces `DETACH_TERMINAL` and only `DetachActive`. A `PREPARED` plan contains
no filesystem step; it preserves any drift and transitions directly through `ROLLING_BACK` to
`ROLLED_BACK`.

- [ ] **Step 6: Test stable diagnostics and dependency irrelevance**

Add tests:

```python
def test_already_halted_reuses_exact_diagnostic(halted_snapshot):
    plan = classify_recovery(halted_snapshot)
    assert plan.diagnostic is halted_snapshot.halt_diagnostic
    assert plan.steps == ()


def test_dependencies_do_not_change_plan(snapshot_pair_differing_only_dependencies):
    left, right = snapshot_pair_differing_only_dependencies
    assert left.persistent_observations is right.persistent_observations
    assert left.scratch_observations is right.scratch_observations
    left_plan = classify_recovery(left)
    right_plan = classify_recovery(right)
    assert left_plan.disposition == right_plan.disposition
    assert left_plan.steps == right_plan.steps


def test_classifier_plan_reaches_second_pass_fixed_point(recovery_case):
    source = recovery_case("restored")
    next_snapshot = apply_recovery_plan(source, classify_recovery(source))
    fixed = apply_recovery_plan(next_snapshot, classify_recovery(next_snapshot))
    assert fixed == next_snapshot


def test_committed_halt_retains_decision_and_diagnostic(committed_halt_source):
    halted = apply_recovery_plan(
        committed_halt_source,
        classify_recovery(committed_halt_source),
    )
    assert halted.transaction_state is TransactionState.HALTED
    assert halted.commit_decision is CommitDecision.COMMITTED
    assert apply_recovery_plan(halted, classify_recovery(halted)) == halted
```

`make_snapshot_pair_differing_only_dependencies` compiles two specs whose only unequal field is
`dependencies`, then passes the same topology, journal tuple, persistent-observation tuple, and
scratch-observation tuple objects to both snapshot-factory calls. It must not call an observation
builder twice. Together with the identity-conservation constraint, literal step equality then tests
dependency irrelevance rather than accidentally testing two token universes.

- [ ] **Step 7: Run focused and full checks**

Run:

```bash
uv run pytest tests/test_recovery_reducer.py -v
uv run pytest tests/test_recovery_variants_files.py tests/test_recovery_variants_paths.py -v
uv run pytest tests/test_recovery_classifier.py -v
uv run pytest
uv run ruff check .
uv run pyright
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/atoms/core/recovery/classifier.py src/atoms/core/recovery/diagnostics.py \
  src/atoms/core/recovery/__init__.py src/atoms/core/recovery/reducer.py \
  src/atoms/core/recovery/variants.py tests/conftest.py \
  tests/test_recovery_classifier.py tests/test_recovery_reducer.py \
  tests/test_recovery_variants_files.py tests/test_recovery_variants_paths.py \
  tests/recovery_support.py \
  ../docs/deferred-obligation-ledger.md \
  ../docs/plans/2026-07-23-recoverable-fs-effect-engine-design.md \
  ../docs/plans/2026-07-28-a3-recovery-reference-model-design.md \
  ../docs/plans/2026-07-28-plan-a3-recovery-reference-model.md
git commit -m "feat(recovery): classify complete recovery plans"
```

---

### Task 9: Fresh-step authorization and prefix-bound mismatch halt

Authorize only filesystem-mutating plan steps against one exact fresh joint observation. A mismatch
does not retry or reclassify; it returns a halt bound to the already-completed logical prefix with the
fresh conflict substituted.

**Files:**
- Create: `python/src/atoms/core/recovery/authorization.py`
- Create: `python/tests/test_recovery_authorization.py`
- Modify: `python/src/atoms/core/recovery/__init__.py`
- Modify: `python/tests/recovery_support.py`
- Modify: `python/tests/conftest.py`

**Interfaces:**
- Consumes: `RecoveryPlan`, reducer prefix/normalization operations, shared diagnostic projections.
- Produces:
  - `authorize_recovery_step(plan, step_index, observed) -> AuthorizedStep | HaltPlan`.

For a committed cleanup `RemoveScratch`, the expected and fresh `JointObservation` contain exactly the
one retained scratch slot, with empty persistent and parent-occupancy tuples. Add a focused
authorization test proving that scratch-only evidence succeeds and that unrelated persistent evidence
is neither required nor admitted. Ordinary rollback steps retain complete effect joint coverage.

- [ ] **Step 1: Write failing authorization tests**

Create `python/tests/test_recovery_authorization.py`:

Task 9 adds the `classifier_plan` adapter to `conftest.py` in the same commit as
`make_classifier_plan`.

```python
from dataclasses import replace

import pytest

from atoms.core.errors import ProtocolError
from atoms.core.recovery import (
    AuthorizedStep,
    EntryIdentity,
    FileBuildRelation,
    HaltReason,
    JointObservation,
    ObservedFile,
    PersistentObservation,
    PlanDisposition,
    authorize_recovery_step,
    classify_recovery,
)
from tests.recovery_support import create_snapshot, reallocate_joint_identities
from tests.support import G


def first_mutating_step(plan):
    for index, step in enumerate(plan.steps):
        if type(step).__name__ in {"TransformEffectTuple", "RemoveScratch"}:
            return index, step
    raise AssertionError("fixture must produce a mutating step")


def test_fresh_alpha_renamed_observation_authorizes_bound_step():
    plan = classify_recovery(create_snapshot())
    index, step = first_mutating_step(plan)
    fresh = reallocate_joint_identities(step.expected_before)
    assert fresh != step.expected_before
    result = authorize_recovery_step(plan, index, fresh)
    assert type(result) is AuthorizedStep
    assert result.plan is plan
    assert result.step_index == index


def test_changed_precondition_returns_prefix_bound_halt():
    plan = classify_recovery(create_snapshot())
    index, step = first_mutating_step(plan)
    original = step.expected_before.persistent[0]
    changed = JointObservation(
        persistent=(
            PersistentObservation(
                path=original.path,
                entry=ObservedFile(state=G, identity=EntryIdentity()),
            ),
        ),
        scratch=step.expected_before.scratch,
        parent_occupancy=step.expected_before.parent_occupancy,
    )
    result = authorize_recovery_step(plan, index, changed)
    assert result.disposition is PlanDisposition.HALT
    assert result.diagnostic.reason is HaltReason.PLAN_PRECONDITION_CHANGED
    assert result.bound_snapshot != plan.bound_snapshot


def test_nonmutating_step_index_is_protocol_error():
    plan = classify_recovery(create_snapshot())
    with pytest.raises(ProtocolError, match="filesystem-mutating"):
        authorize_recovery_step(plan, 0, JointObservation((), (), ()))


def test_mismatch_halt_normalizes_stale_construction_relation():
    plan = classify_recovery(create_snapshot())
    index, step = first_mutating_step(plan)
    stale_scratch = replace(
        step.expected_before.scratch[0],
        file_build_relation=FileBuildRelation.EXACT,
    )
    stale = replace(step.expected_before, scratch=(stale_scratch,))
    result = authorize_recovery_step(plan, index, stale)
    assert result.diagnostic.reason is HaltReason.PLAN_PRECONDITION_CHANGED
    assert result.bound_snapshot.scratch_observations[0].file_build_relation is None
```

Add exact-type, negative, out-of-range, and `bool` step-index cases.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_recovery_authorization.py -v`

Expected: FAIL because `authorize_recovery_step` is not exported.

- [ ] **Step 3: Implement exact observation coverage and prefix binding**

Create `python/src/atoms/core/recovery/authorization.py`:

```python
def authorize_recovery_step(
    plan: RecoveryPlan,
    step_index: int,
    observed: JointObservation,
) -> AuthorizedStep | HaltPlan:
    if type(plan) not in {ActionPlan, HaltPlan, NoRecoveryPlan}:
        raise ProtocolError("plan must be a factory-issued RecoveryPlan")
    if type(step_index) is not int or not 0 <= step_index < len(plan.steps):
        raise ProtocolError("step_index is outside the plan step range")
    if type(observed) is not JointObservation:
        raise ProtocolError("observed must be an exact JointObservation")

    step = plan.steps[step_index]
    if type(step) not in {TransformEffectTuple, RemoveScratch}:
        raise ProtocolError("step_index must name a filesystem-mutating step")

    prefix = reduce_recovery_plan_prefix(
        plan.bound_snapshot,
        plan,
        completed_steps=step_index,
    )
    expected = step.expected_before
    _validate_joint_coverage(expected, observed)
    if _authorization_projection(observed) == _authorization_projection(expected):
        return _new_authorized_step(plan, step_index, step)
    return _precondition_changed_halt(prefix, expected, observed)
```

`_validate_joint_coverage` requires the exact same persistent path keys, scratch effect/role keys, and
parent nodes as `expected`. Missing/extra/duplicate/wrong exact types raise `ProtocolError`.

`_authorization_projection` returns:

1. every persistent, scratch, and parent-occupancy field with `EntryIdentity` removed;
2. the sorted named-slot identity relations produced by
   `_project_identity_relations("authorization", observation)`.

It compares exact entry variants, fingerprints, build relations, occupancy, and identity partitions.
It never compares a token from `expected` with a token from `observed`. The
`reallocate_joint_identities` helper walks every named slot, allocates one new `EntryIdentity` per old
token, and reuses that new token for every occurrence of the old one; the test above therefore fails
if authorization regresses to dataclass equality or loses an identity relation.

`authorization.py` imports `_diagnostic`, `_project_entries`, and
`_project_identity_relations` from `diagnostics.py`, never from `classifier.py`.

```python
def _authorization_projection(observation: JointObservation):
    return (
        _project_entries(observation),
        observation.parent_occupancy,
        _project_identity_relations("authorization", observation),
    )
```

Add this exact helper to `recovery_support.py`, extending its recovery imports with
`JointObservation` and `ObservedDirectory`:

```python
def reallocate_joint_identities(observation: JointObservation) -> JointObservation:
    replacements: dict[EntryIdentity, EntryIdentity] = {}

    def fresh_identity(identity: EntryIdentity) -> EntryIdentity:
        replacement = replacements.get(identity)
        if replacement is None:
            replacement = EntryIdentity()
            replacements[identity] = replacement
        return replacement

    def fresh_entry(entry):
        if type(entry) is ObservedFile:
            return ObservedFile(entry.state, fresh_identity(entry.identity))
        if type(entry) is ObservedDirectory:
            return ObservedDirectory(
                entry.state,
                fresh_identity(entry.identity),
                entry.has_unmodeled_child,
            )
        return entry

    return JointObservation(
        persistent=tuple(
            PersistentObservation(item.path, fresh_entry(item.entry))
            for item in observation.persistent
        ),
        scratch=tuple(
            ScratchObservation(
                item.effect_id,
                item.role,
                fresh_entry(item.entry),
                item.file_build_relation,
            )
            for item in observation.scratch
        ),
        parent_occupancy=observation.parent_occupancy,
    )
```

`_precondition_changed_halt`:

1. computes `normalized = _normalize_joint_observation(prefix, observed)`;
2. merges `normalized` into the prefix snapshot and rebuilds it through `build_recovery_snapshot`;
3. builds a token-free diagnostic with `PLAN_PRECONDITION_CHANGED` from the original `expected` and
   `observed` tuples, so normalization does not erase the mismatch evidence;
4. returns `_new_halt_plan` bound to that normalized conflicting prefix;
5. includes exactly one transaction transition to `HALTED`;
6. emits no project or scratch mutation.

- [ ] **Step 4: Mutation-lock no reclassification**

Keep a standing AST assertion that the authorization module has no classifier import, then authorize
a well-shaped mismatching observation:

```python
def test_mismatch_does_not_reclassify(classifier_plan):
    import ast
    import inspect

    import atoms.core.recovery.authorization as module

    tree = ast.parse(inspect.getsource(module))
    classifier_imports = [
        node
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "atoms.core.recovery.classifier"
        )
        or (
            isinstance(node, ast.Import)
            and any(
                alias.name == "atoms.core.recovery.classifier"
                for alias in node.names
            )
        )
    ]
    assert classifier_imports == []
    index, step = first_mutating_step(classifier_plan)
    original = step.expected_before.persistent[0]
    changed = JointObservation(
        persistent=(
            PersistentObservation(
                path=original.path,
                entry=ObservedFile(state=G, identity=EntryIdentity()),
            ),
        ),
        scratch=step.expected_before.scratch,
        parent_occupancy=step.expected_before.parent_occupancy,
    )
    result = authorize_recovery_step(classifier_plan, index, changed)
    assert result.diagnostic.reason is HaltReason.PLAN_PRECONDITION_CHANGED
```

- [ ] **Step 5: Run focused and full checks**

Run:

```bash
uv run pytest tests/test_recovery_authorization.py -v
uv run pytest
uv run ruff check .
uv run pyright
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/atoms/core/recovery/authorization.py src/atoms/core/recovery/__init__.py \
  tests/conftest.py tests/test_recovery_authorization.py tests/recovery_support.py
git commit -m "feat(recovery): authorize fresh recovery steps"
```

---

### Task 10: Exhaustive properties, architecture enforcement, and status sync

Lock the cross-cutting guarantees with bounded finite generators and architecture tests. Then update
status documents only after every A3 test and full repository check passes.

**Files:**
- Create: `python/tests/test_recovery_properties.py`
- Create: `python/tests/test_recovery_architecture.py`
- Modify: `python/tests/recovery_support.py`
- Modify: `python/tests/conftest.py`
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/plans/2026-07-28-a3-recovery-reference-model-design.md`

**Interfaces:**
- Consumes: complete A3 public package.
- Produces: no new production API; verification and status only.

- [ ] **Step 1: Add bounded exhaustive journal/state properties**

Create `python/tests/test_recovery_properties.py`. Enumerate products, not random samples:

Task 10 adds `generated_snapshots`, `identity_case`, and `halt_restart_case` adapters to
`conftest.py` in the same commit as their `make_*` factories.

```python
from itertools import product

import pytest

import atoms.core.recovery.classifier as classifier_module
from atoms.core.recovery import (
    CommitDecision,
    JournalState,
    TransactionState,
    apply_recovery_plan,
    classify_recovery,
)


@pytest.mark.parametrize(
    ("transaction_state", "commit_decision", "active", "journal_states"),
    product(
        tuple(TransactionState),
        tuple(CommitDecision),
        (False, True),
        product(tuple(JournalState), repeat=3),
    ),
)
def test_every_well_formed_state_vector_classifies(
    generated_snapshots,
    transaction_state,
    commit_decision,
    active,
    journal_states,
):
    snapshot = generated_snapshots.try_build(
        transaction_state,
        commit_decision,
        active,
        journal_states,
    )
    if snapshot is None:
        return
    plan = classify_recovery(snapshot)
    assert type(plan).__name__ in {"ActionPlan", "HaltPlan", "NoRecoveryPlan"}


def test_state_vector_generator_is_nonvacuous(generated_snapshots):
    accepted, refused = generated_snapshots.count_three_effect_cases()
    assert accepted > 0
    assert refused > 0
```

`try_build` catches only `ProtocolError` from snapshot construction and returns `None`; every other
exception escapes. `count_three_effect_cases` traverses the identical Cartesian product and counts
accepted/refused values, preventing a vacuous refuse-all generator.

This intentionally collects `7 * 2 * 2 * 125 = 3,500` pure cases. Run the focused Task 10 property
file once before the full suite and record its duration; a development-machine target below 15 seconds
is acceptable. If construction exceeds that target, optimize shared immutable fixture inputs without
sampling or reducing the Cartesian product. Do not add a wall-clock assertion to the test suite.

- [ ] **Step 2: Add variant and identity-partition properties**

Generate:

```text
Replace/Create/Delete: every {A, pre, post, prefix, X} joint tuple × relevant journal state.
Move: every set partition of source/destination/anchor identities × presence/state tuple.
CreateDirectory: every live/work equality partition × modeled/unmodeled occupancy.
No-op Replace: pre == post across every staging class and journal state.
```

Properties assert:

```python
def test_identity_alpha_renaming_preserves_classification(identity_case):
    original, renamed = identity_case
    assert plan_projection(classify_recovery(original)) == plan_projection(
        classify_recovery(renamed)
    )


def test_plan_conserves_source_identity_tokens(generated_snapshots):
    source = generated_snapshots.valid_case_with_identity()
    plan = classify_recovery(source)
    assert plan_identity_tokens(plan) <= snapshot_identity_tokens(source)


def test_halt_diagnostic_round_trips_across_token_universe(halt_restart_case):
    before, after_restart = halt_restart_case
    assert before.halt_diagnostic == after_restart.halt_diagnostic
    assert before.persistent_observations != after_restart.persistent_observations
```

`plan_projection` removes `EntryIdentity` objects but retains every named-slot identity relation,
disposition, reason, and semantic step.
`snapshot_identity_tokens` and `plan_identity_tokens` walk the closed snapshot/step dataclasses by
exact type and return the existing token objects as sets; neither helper constructs an identity.

- [ ] **Step 3: Add convergence, determinism, and totality properties**

```python
def test_classification_is_deterministic(generated_snapshots):
    snapshot = generated_snapshots.valid_case()
    assert classify_recovery(snapshot) == classify_recovery(snapshot)


def test_reducer_reaches_second_pass_fixed_point(generated_snapshots):
    source = generated_snapshots.valid_case()
    next_snapshot = apply_recovery_plan(source, classify_recovery(source))
    fixed = apply_recovery_plan(next_snapshot, classify_recovery(next_snapshot))
    assert fixed == next_snapshot


def test_unexpected_internal_fault_propagates(monkeypatch, generated_snapshots):
    class InjectedFault(RuntimeError):
        pass

    def fail(*args, **kwargs):
        raise InjectedFault("injected")

    monkeypatch.setattr(classifier_module, "reconstruct_frontiers", fail)
    with pytest.raises(InjectedFault, match="injected"):
        classify_recovery(generated_snapshots.valid_case())
```

Also assert all-evidence-before-action, committed-never-rolls-back, external drift preservation,
dependency irrelevance, exact source binding, no global scratch-absence fallback, and 1,100-component
topology handling without recursion failure.

- [ ] **Step 4: Add architecture tests**

Create `python/tests/test_recovery_architecture.py`:

```python
import ast
import inspect
from pathlib import Path
from typing import get_type_hints

import atoms.core.recovery as recovery
from atoms.core.compiler import CompiledSpec
from atoms.core.spec import TransactionSpec


def test_public_surface_has_exactly_five_operations():
    operations = {
        name
        for name in recovery.__all__
        if inspect.isfunction(getattr(recovery, name))
    }
    assert operations == {
        "build_recovery_snapshot",
        "classify_recovery",
        "authorize_recovery_step",
        "reduce_recovery_plan_prefix",
        "apply_recovery_plan",
    }


def test_recovery_package_has_no_io_or_sqlite_imports():
    root = Path(__file__).parents[1] / "src" / "atoms" / "core" / "recovery"
    forbidden = {
        "ctypes",
        "datetime",
        "os",
        "pathlib",
        "random",
        "secrets",
        "sqlite3",
        "subprocess",
        "time",
    }
    for source_path in root.glob("*.py"):
        tree = ast.parse(source_path.read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports.add(node.module.split(".", 1)[0])
        assert not imports & forbidden, source_path


def test_a3_does_not_accept_raw_transaction_spec():
    annotations = get_type_hints(recovery.build_recovery_snapshot)
    assert annotations["compiled"] is CompiledSpec
    assert TransactionSpec not in annotations.values()
```

Add an AST test that `classifier.py`, `journal.py`, and `variants.py` never access `.dependencies`, and
an import test proving `atoms.core.recovery` imports without a filesystem/backend module. Also inspect
the collected fixture names and fail if any test argument other than a parameterized name or pytest
builtin lacks a definition in `conftest.py`; this is the standing lock for the fixture registry rather
than a one-time plan review.

- [ ] **Step 5: Run the complete A3 mutation checklist**

Apply one mutation at a time, run the named focused tests, and revert each mutation:

| Mutation | Test that must fail |
| --- | --- |
| classify move paths independently | joint move table/property |
| share APPLIED and COMMITTED decision | committed-never-rolls-back |
| accept STARTED followed by UNDONE | journal-language regression |
| accept UNDONE after the PENDING tail begins | reverse-language tail regression |
| validate a HALTED vector as an ordinary journal language | stable-halt short-circuit |
| recompute halted diagnostic | restart diagnostic equality |
| store raw identity token in diagnostic | token-free field/round-trip test |
| allocate a fresh identity in a step builder | plan identity-conservation property |
| require prefix relation for completed replace | conditional relation test |
| dispatch no-op replace through general rows | no-op precedence matrix |
| compare fresh authorization by raw dataclass equality | fresh alpha-renamed authorization |
| authorize stale observation | authorization mismatch |
| authorize non-mutating step | protocol-error test |
| import classifier from authorization | authorization import-boundary AST test |
| use lexical topology | resolved descendant test |
| give a symlink a decisive opaque identity | delete-symlink identity mutation |
| treat every absent scratch as landed | variant absence matrices |
| omit reverse intermediate | reducer prefix/fixed-point test |
| retain construction relation after leaving STARTED/pre | reducer relation-transition tests |
| skip post-transition mutating-step rebinding | post-transition precondition reduction |
| catch unexpected exception | internal-fault propagation |

- [ ] **Step 6: Run final repository verification**

Run from `python/`:

```bash
uv run pytest tests/test_recovery_properties.py -q --durations=10
uv run pytest -o addopts=
uv run ruff check .
uv run pyright
```

Expected: all tests pass; Ruff reports `All checks passed!`; Pyright reports zero errors.

- [ ] **Step 7: Update status documents**

Only after Step 6 is green:

- change the A3 design status to `Implemented`;
- change README's A3 entry to `implemented` and name the five APIs;
- change AGENTS.md's A3 entry to `implemented`;
- state explicitly that A4–A8 remain unimplemented and no filesystem mutation code has landed;
- keep ledger entries 3, 8, 10, and 12–15 open because their later owners remain outstanding.

- [ ] **Step 8: Commit**

```bash
git add src/atoms/core/recovery tests README.md AGENTS.md docs/plans/2026-07-28-a3-recovery-reference-model-design.md
git commit -m "test(recovery): lock A3 reference-model guarantees"
```

---

## Self-review checklist

Before implementation begins, verify:

- [ ] Every public type named in the A3 design is defined in Tasks 1–3.
- [ ] All five public operations are introduced once and exported from `recovery/__init__.py`.
- [ ] Every journal language and state/commit/active combination maps to Task 5 or Task 8.
- [ ] `HALTED` short-circuits before journal-language selection; `ROLLED_BACK` cannot admit `DONE`.
- [ ] Every row in all five variant tables is exercised in Tasks 6–7 and the finite generators.
- [ ] `DONE` rollback has explicit per-variant coverage separate from `UNDO_STARTED`.
- [ ] No-op replace has its own precedence and mutation test.
- [ ] Diagnostics distinguish durable source journals from projected conflict journals and retain
  named-slot relations, never identity tokens.
- [ ] Conditional `file_build_relation` evidence is tested in both required and forbidden positions.
- [ ] Mutating-step preconditions are rebound after preceding metadata transitions, and mismatch
  snapshots use the same relation normalizer.
- [ ] Prefix reduction precedes fresh authorization, and non-mutating steps cannot be authorized.
- [ ] Fresh authorization compares exact non-identity fields plus identity partitions, not tokens.
- [ ] Step construction is identity-conservative and dependency-only fixtures share observation
  tuples.
- [ ] Repeated-path planning advances a pure cursor through Task 4's reducer before classifying each
  earlier effect; no second projection algorithm exists.
- [ ] Reducer fixed points cover restored, refused, committed, first-halt, repeated-halt, and detached
  terminal outcomes.
- [ ] A3 remains pure and stdlib-only.
- [ ] No task contains placeholder language or an undefined public interface.
- [ ] Every pytest argument is parameterized, built in, or registered explicitly in `conftest.py`.

## Execution handoff

After owner approval, implement with `superpowers:subagent-driven-development`: one fresh implementer
per task, followed by specification-compliance review and code-quality review before the next task.
Use `superpowers:using-git-worktrees` at execution start if the current workspace is not already an
isolated A3 worktree.
