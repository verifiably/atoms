from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import NoReturn, cast

from atoms.core.compiler import CompiledSpec
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.errors import ProtocolError
from atoms.core.fingerprint import AbsentState, DirectoryState, FileState, SymlinkState
from atoms.core.recovery.model import (
    CommitDecision,
    DiagnosticEntry,
    DiagnosticIdentityRelation,
    EffectJournalState,
    EntryIdentity,
    FileBuildRelation,
    HaltDiagnostic,
    HaltReason,
    IdentityRelation,
    JournalState,
    ObservedAbsent,
    ObservedDirectory,
    ObservedEntry,
    ObservedFile,
    ObservedSymlink,
    OperatorAction,
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


def persistent_map(snapshot: RecoverySnapshot) -> dict[str, ObservedEntry]:
    return {item.path: item.entry for item in snapshot.persistent_observations}


def scratch_map(snapshot: RecoverySnapshot) -> dict[tuple[str, ScratchRole], ScratchObservation]:
    return {(item.effect_id, item.role): item for item in snapshot.scratch_observations}


def effect_index(snapshot: RecoverySnapshot) -> dict[str, int]:
    return {effect.effect_id: index for index, effect in enumerate(snapshot.compiled.spec.effects)}


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
    _validate_observations(
        compiled,
        transaction_state,
        journals,
        persistent_observations,
        scratch_observations,
    )
    _validate_halt_diagnostic(halt_diagnostic)
    _validate_terminal_payloads(
        transaction_state,
        commit_decision,
        rollback_result,
        halt_diagnostic,
        journals,
    )

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


def _validate_journal_coverage(
    compiled: CompiledSpec,
    journals: tuple[EffectJournalState, ...],
) -> None:
    expected_ids = tuple(effect.effect_id for effect in compiled.spec.effects)
    for journal in journals:
        _require_exact(journal, EffectJournalState, "journals")
        _require_exact(journal.effect_id, str, "journals")
        _require_exact(journal.state, JournalState, "journals")
    if tuple(journal.effect_id for journal in journals) != expected_ids:
        _fail("journals do not provide exact compiled effect coverage")


def _validate_persistent_coverage(
    compiled: CompiledSpec,
    observations: tuple[PersistentObservation, ...],
) -> None:
    expected_paths = tuple(timeline.path for timeline in compiled.timelines)
    for observation in observations:
        _require_exact(observation, PersistentObservation, "persistent_observations")
        _require_exact(observation.path, str, "persistent_observations")
    if tuple(observation.path for observation in observations) != expected_paths:
        _fail("persistent_observations do not provide exact compiled path coverage")


def _validate_scratch_coverage(
    compiled: CompiledSpec,
    observations: tuple[ScratchObservation, ...],
) -> None:
    expected = tuple((effect.effect_id, required_scratch_role(effect)) for effect in compiled.spec.effects)
    for observation in observations:
        _require_exact(observation, ScratchObservation, "scratch_observations")
        _require_exact(observation.effect_id, str, "scratch_observations")
        _require_exact(observation.role, ScratchRole, "scratch_observations")
    if tuple((observation.effect_id, observation.role) for observation in observations) != expected:
        _fail("scratch_observations do not provide exact compiled scratch coverage")


def _validate_topology(compiled: CompiledSpec, topology: RecoveryTopology) -> None:
    _require_tuple(topology.parents, "topology.parents")
    parents: dict[TopologyNode, TopologyNode] = {}
    children: dict[TopologyNode, list[TopologyNode]] = {}
    nodes: set[TopologyNode] = set()
    project = ProjectRoot()

    for edge in topology.parents:
        _require_exact(edge, TopologyParent, "topology.parents")
        _validate_topology_node(edge.node)
        _validate_topology_node(edge.parent)
        if edge.node == edge.parent:
            _fail("topology may not contain a self-parent edge")
        if type(edge.node) is ProjectRoot:
            _fail("topology project root may not have a parent")
        if edge.node in parents:
            _fail("topology node has more than one parent")
        parents[edge.node] = edge.parent
        children.setdefault(edge.parent, []).append(edge.node)
        nodes.update((edge.node, edge.parent))

    if project not in nodes:
        _fail("topology is missing the project root")
    if WorkRoot() in parents and parents[WorkRoot()] != project:
        _fail("topology work root must be parented by the project root")

    expected_persistent = {PersistentNode(timeline.path) for timeline in compiled.timelines}
    expected_scratch = {
        ScratchNode(effect.effect_id, required_scratch_role(effect)) for effect in compiled.spec.effects
    }
    actual_persistent = {node for node in nodes if type(node) is PersistentNode}
    actual_scratch = {node for node in nodes if type(node) is ScratchNode}
    if actual_persistent != expected_persistent:
        _fail("topology does not provide exact persistent coverage")
    if actual_scratch != expected_scratch:
        _fail("topology does not provide exact scratch coverage")

    for scratch in expected_scratch:
        parent = parents[scratch]
        if scratch.role is ScratchRole.WORK:
            if type(parent) is not WorkRoot:
                _fail("topology work scratch must be parented by the work root")
        elif type(parent) is WorkRoot:
            _fail("topology non-work scratch may not be parented by the work root")

    for effect in compiled.spec.effects:
        scratch = ScratchNode(effect.effect_id, required_scratch_role(effect))
        if type(effect) is CreateDirectory:
            continue
        if type(effect) is MoveNoClobber:
            persistent_path = cast(MoveNoClobber, effect).source
        else:
            persistent_path = cast(ReplaceFile | CreateFileNoClobber | DeletePath, effect).path
        if parents[scratch] != parents[PersistentNode(persistent_path)]:
            _fail("topology scratch and persistent nodes must share the same resolved parent")

    indegree = {node: 0 for node in nodes}
    for node in parents:
        indegree[node] += 1
    worklist: list[TopologyNode] = [project]
    visited: set[TopologyNode] = set()
    while worklist:
        node = worklist.pop()
        if node in visited:
            continue
        visited.add(node)
        for child in children.get(node, ()):
            indegree[child] -= 1
            if indegree[child] == 0:
                worklist.append(child)
    if visited != nodes:
        _fail("topology must be an acyclic tree rooted at the project root")


def _validate_topology_node(node: object) -> None:
    node_type = type(node)
    if node_type not in {ProjectRoot, WorkRoot, TopologyDirectory, PersistentNode, ScratchNode}:
        _fail("topology node has the wrong exact runtime type")
    if node_type is TopologyDirectory:
        directory = cast(TopologyDirectory, node)
        _require_exact(directory.node_id, int, "topology directory node_id")
    elif node_type is PersistentNode:
        persistent = cast(PersistentNode, node)
        _require_exact(persistent.path, str, "topology persistent node path")
    elif node_type is ScratchNode:
        scratch = cast(ScratchNode, node)
        _require_exact(scratch.effect_id, str, "topology scratch node effect_id")
        _require_exact(scratch.role, ScratchRole, "topology scratch node role")


def _validate_observations(
    compiled: CompiledSpec,
    transaction_state: TransactionState,
    journals: tuple[EffectJournalState, ...],
    persistent_observations: tuple[PersistentObservation, ...],
    scratch_observations: tuple[ScratchObservation, ...],
) -> None:
    for observation in persistent_observations:
        _validate_observed_entry(observation.entry, "persistent_observations")
    for observation in scratch_observations:
        _validate_observed_entry(observation.entry, "scratch_observations")
        _require_optional_exact(
            observation.file_build_relation,
            FileBuildRelation,
            "file_build_relation",
        )

    journals_by_id = {journal.effect_id: journal.state for journal in journals}
    persistent_by_path = {observation.path: observation.entry for observation in persistent_observations}
    effects_by_id = {effect.effect_id: effect for effect in compiled.spec.effects}
    for observation in scratch_observations:
        effect = effects_by_id[observation.effect_id]
        relation_required = _construction_relation_required(
            effect,
            journals_by_id[observation.effect_id],
            persistent_by_path,
            observation,
        )
        if relation_required != (observation.file_build_relation is not None):
            _fail("file_build_relation is present in the wrong construction state")


def _validate_observed_entry(entry: object, label: str) -> None:
    entry_type = type(entry)
    if entry_type is ObservedAbsent:
        return
    if entry_type is ObservedFile:
        file_entry = cast(ObservedFile, entry)
        _validate_file_state(file_entry.state, label)
        _require_exact(file_entry.identity, EntryIdentity, label)
        return
    if entry_type is ObservedSymlink:
        symlink_entry = cast(ObservedSymlink, entry)
        _validate_symlink_state(symlink_entry.state, label)
        return
    if entry_type is ObservedDirectory:
        directory_entry = cast(ObservedDirectory, entry)
        _validate_directory_state(directory_entry.state, label)
        _require_exact(directory_entry.identity, EntryIdentity, label)
        _require_exact(directory_entry.has_unmodeled_child, bool, label)
        return
    _fail(f"{label} entry has the wrong exact runtime type")


def _construction_relation_required(
    effect: object,
    journal: JournalState,
    persistent_by_path: Mapping[str, ObservedEntry],
    scratch: ScratchObservation,
) -> bool:
    if journal is not JournalState.STARTED or type(scratch.entry) is not ObservedFile:
        return False
    if type(effect) is CreateFileNoClobber:
        return True
    if type(effect) is ReplaceFile:
        live = persistent_by_path[effect.path]
        return type(live) is ObservedFile and live.state == effect.pre
    return False


def _validate_halt_diagnostic(diagnostic: HaltDiagnostic | None) -> None:
    if diagnostic is None:
        return
    _require_exact(diagnostic.pre_halt_state, TransactionState, "halt_diagnostic")
    if diagnostic.pre_halt_state is TransactionState.HALTED:
        _fail("halt_diagnostic may not name HALTED as its pre-halt state")
    _require_exact(diagnostic.commit_decision, CommitDecision, "halt_diagnostic")
    _validate_diagnostic_journals(diagnostic.journals, "halt_diagnostic.journals")
    _require_exact(
        diagnostic.projected_transaction_state,
        TransactionState,
        "halt_diagnostic",
    )
    _validate_diagnostic_journals(diagnostic.projected_journals, "halt_diagnostic.projected_journals")
    if tuple(item.effect_id for item in diagnostic.projected_journals) != tuple(
        item.effect_id for item in diagnostic.journals
    ):
        _fail("halt_diagnostic projected journals have the wrong effect vector")
    if diagnostic.effect_id is not None:
        _require_exact(diagnostic.effect_id, str, "halt_diagnostic")
    _validate_sorted_strings(diagnostic.paths, "halt_diagnostic.paths")
    _validate_diagnostic_entries(diagnostic.expected, "halt_diagnostic.expected")
    _validate_diagnostic_entries(diagnostic.observed, "halt_diagnostic.observed")
    _validate_identity_relations(diagnostic.identity_relations)
    _require_exact(diagnostic.reason, HaltReason, "halt_diagnostic.reason")
    _require_exact(diagnostic.operator_action, OperatorAction, "halt_diagnostic.operator_action")


def _validate_diagnostic_journals(items: object, label: str) -> None:
    _require_tuple(items, label)
    for item in cast(tuple[object, ...], items):
        _require_exact(item, EffectJournalState, label)
        journal = cast(EffectJournalState, item)
        _require_exact(journal.effect_id, str, label)
        _require_exact(journal.state, JournalState, label)


def _validate_sorted_strings(items: object, label: str) -> None:
    _require_tuple(items, label)
    values = cast(tuple[object, ...], items)
    for item in values:
        _require_exact(item, str, label)
    strings = cast(tuple[str, ...], values)
    if tuple(sorted(strings)) != strings or len(set(strings)) != len(strings):
        _fail(f"{label} must be sorted and unique")


def _validate_diagnostic_entries(items: object, label: str) -> None:
    _require_tuple(items, label)
    slots: list[str] = []
    for item in cast(tuple[object, ...], items):
        _require_exact(item, DiagnosticEntry, label)
        entry = cast(DiagnosticEntry, item)
        _require_exact(entry.slot, str, label)
        _validate_path_state(entry.state, label)
        _require_optional_exact(entry.has_unmodeled_child, bool, label)
        _require_optional_exact(entry.file_build_relation, FileBuildRelation, label)
        slots.append(entry.slot)
    if slots != sorted(slots) or len(set(slots)) != len(slots):
        _fail(f"{label} slots must be sorted and unique")


def _validate_identity_relations(items: object) -> None:
    _require_tuple(items, "halt_diagnostic.identity_relations")
    pairs: list[tuple[str, str]] = []
    for item in cast(tuple[object, ...], items):
        _require_exact(item, DiagnosticIdentityRelation, "halt_diagnostic.identity_relations")
        relation = cast(DiagnosticIdentityRelation, item)
        _require_exact(relation.left_slot, str, "halt_diagnostic.identity_relations")
        _require_exact(relation.right_slot, str, "halt_diagnostic.identity_relations")
        _require_exact(relation.relation, IdentityRelation, "halt_diagnostic.identity_relations")
        pairs.append((relation.left_slot, relation.right_slot))
    if pairs != sorted(pairs) or len(set(pairs)) != len(pairs):
        _fail("halt_diagnostic.identity_relations must be sorted and unique")


def _validate_path_state(state: object, label: str) -> None:
    if type(state) is AbsentState:
        return
    if type(state) is FileState:
        _validate_file_state(state, label)
    elif type(state) is DirectoryState:
        _validate_directory_state(state, label)
    elif type(state) is SymlinkState:
        _validate_symlink_state(state, label)
    else:
        _fail(f"{label} state has the wrong exact runtime type")


def _validate_file_state(state: object, label: str) -> None:
    _require_exact(state, FileState, label)
    file_state = cast(FileState, state)
    _require_exact(file_state.content_hash, str, label)
    _require_exact(file_state.mode, int, label)
    _require_exact(file_state.byte_len, int, label)


def _validate_directory_state(state: object, label: str) -> None:
    _require_exact(state, DirectoryState, label)
    directory_state = cast(DirectoryState, state)
    _require_exact(directory_state.mode, int, label)


def _validate_symlink_state(state: object, label: str) -> None:
    _require_exact(state, SymlinkState, label)
    symlink_state = cast(SymlinkState, state)
    _require_exact(symlink_state.target, str, label)
    _require_exact(symlink_state.mode, int, label)


def _validate_terminal_payloads(
    transaction_state: TransactionState,
    commit_decision: CommitDecision,
    rollback_result: RollbackResult | None,
    halt_diagnostic: HaltDiagnostic | None,
    journals: tuple[EffectJournalState, ...],
) -> None:
    if (rollback_result is not None) != (transaction_state is TransactionState.ROLLED_BACK):
        _fail("rollback_result must be present exactly for ROLLED_BACK")
    if (halt_diagnostic is not None) != (transaction_state is TransactionState.HALTED):
        _fail("halt_diagnostic must be present exactly for HALTED")
    if halt_diagnostic is not None and (
        commit_decision != halt_diagnostic.commit_decision or journals != halt_diagnostic.journals
    ):
        _fail("halt_diagnostic does not match current durable state")
