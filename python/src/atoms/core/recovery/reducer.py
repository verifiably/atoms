from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import cast

from atoms.core.compiler import CompiledSpec
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    Effect,
    MoveNoClobber,
    ReplaceFile,
    occurrences,
)
from atoms.core.errors import ProtocolError
from atoms.core.recovery.model import (
    DiagnosticIdentityRelation,
    EffectJournalState,
    FileBuildRelation,
    HaltDiagnostic,
    IdentityRelation,
    JournalState,
    ObservedAbsent,
    ObservedDirectory,
    ObservedEntry,
    ObservedFile,
    PersistentObservation,
    RollbackResult,
    ScratchObservation,
    ScratchRole,
    TransactionState,
)
from atoms.core.recovery.plan import (
    ActionPlan,
    DetachActive,
    EffectVariant,
    HaltPlan,
    JointObservation,
    NoRecoveryPlan,
    ParentOccupancy,
    PreserveExternal,
    RecoveryPlan,
    RecoveryStep,
    RemoveScratch,
    SettlementKind,
    TransformEffectTuple,
    TransitionEffectState,
    TransitionTransactionState,
)
from atoms.core.recovery.snapshot import (
    PersistentNode,
    ProjectRoot,
    RecoverySnapshot,
    ScratchNode,
    TopologyDirectory,
    TopologyNode,
    WorkRoot,
    _validate_observed_entry,
    _validate_topology_node,
    build_recovery_snapshot,
    required_scratch_role,
)

_UNSET = object()

_EFFECT_VARIANTS = {
    ReplaceFile: EffectVariant.REPLACE_FILE,
    CreateFileNoClobber: EffectVariant.CREATE_FILE_NO_CLOBBER,
    DeletePath: EffectVariant.DELETE_PATH,
    MoveNoClobber: EffectVariant.MOVE_NO_CLOBBER,
    CreateDirectory: EffectVariant.CREATE_DIRECTORY,
}

_TOPOLOGY_NODE_TYPES = {
    ProjectRoot,
    WorkRoot,
    TopologyDirectory,
    PersistentNode,
    ScratchNode,
}

_TRANSACTION_RECOVERY_EDGES = {
    (TransactionState.PREPARED, TransactionState.ROLLING_BACK),
    (TransactionState.APPLYING, TransactionState.ROLLING_BACK),
    (TransactionState.APPLIED, TransactionState.ROLLING_BACK),
    (TransactionState.ROLLING_BACK, TransactionState.ROLLED_BACK),
    *(
        (state, TransactionState.HALTED)
        for state in TransactionState
        if state is not TransactionState.HALTED
    ),
}

_EFFECT_RECOVERY_EDGES = {
    (JournalState.STARTED, JournalState.UNDO_STARTED),
    (JournalState.DONE, JournalState.UNDO_STARTED),
    (JournalState.UNDO_STARTED, JournalState.UNDONE),
}


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
    if type(plan) not in {ActionPlan, HaltPlan, NoRecoveryPlan}:
        raise ProtocolError("plan must be a factory-issued RecoveryPlan")
    return reduce_recovery_plan_prefix(snapshot, plan, len(plan.steps))


def _apply_steps(
    snapshot: RecoverySnapshot,
    steps: tuple[RecoveryStep, ...],
) -> RecoverySnapshot:
    if type(snapshot) is not RecoverySnapshot:
        raise ProtocolError("snapshot must be a factory-issued RecoverySnapshot")
    if type(steps) is not tuple:
        raise ProtocolError("steps must be an exact tuple")

    current = snapshot
    for step in steps:
        current = _apply_step(current, step)
    return current


def _apply_step(
    snapshot: RecoverySnapshot,
    step: RecoveryStep,
) -> RecoverySnapshot:
    try:
        reducer = _STEP_REDUCERS[type(step)]
    except KeyError as exc:
        raise ProtocolError("step is outside the closed recovery step set") from exc
    return reducer(snapshot, step)


def _rebuild(
    snapshot: RecoverySnapshot,
    *,
    transaction_state: TransactionState | object = _UNSET,
    rollback_result: RollbackResult | None | object = _UNSET,
    halt_diagnostic: HaltDiagnostic | None | object = _UNSET,
    active: bool | object = _UNSET,
    journals: tuple[EffectJournalState, ...] | object = _UNSET,
    persistent: tuple[PersistentObservation, ...] | object = _UNSET,
    scratch: tuple[ScratchObservation, ...] | object = _UNSET,
) -> RecoverySnapshot:
    actual_journals = snapshot.journals if journals is _UNSET else journals
    actual_persistent = (
        snapshot.persistent_observations if persistent is _UNSET else persistent
    )
    actual_scratch = snapshot.scratch_observations if scratch is _UNSET else scratch
    if (
        type(actual_journals) is not tuple
        or type(actual_persistent) is not tuple
        or type(actual_scratch) is not tuple
    ):
        raise ProtocolError("reducer projection has non-tuple repeated fields")
    normalized_scratch = _normalize_file_build_relations(
        snapshot.compiled,
        actual_journals,
        actual_persistent,
        actual_scratch,
    )
    return build_recovery_snapshot(
        compiled=snapshot.compiled,
        topology=snapshot.topology,
        transaction_state=cast(
            TransactionState,
            snapshot.transaction_state
            if transaction_state is _UNSET
            else transaction_state,
        ),
        commit_decision=snapshot.commit_decision,
        rollback_result=cast(
            RollbackResult | None,
            snapshot.rollback_result if rollback_result is _UNSET else rollback_result,
        ),
        halt_diagnostic=cast(
            HaltDiagnostic | None,
            snapshot.halt_diagnostic
            if halt_diagnostic is _UNSET
            else halt_diagnostic,
        ),
        active=cast(bool, snapshot.active if active is _UNSET else active),
        journals=actual_journals,
        persistent_observations=actual_persistent,
        scratch_observations=normalized_scratch,
    )


def _transition_transaction(
    snapshot: RecoverySnapshot,
    raw_step: RecoveryStep,
) -> RecoverySnapshot:
    if type(raw_step) is not TransitionTransactionState:
        raise ProtocolError("transaction reducer received the wrong step type")
    step = raw_step
    if type(step.from_state) is not TransactionState or type(step.to_state) is not TransactionState:
        raise ProtocolError("transaction transition states must be exact TransactionState values")
    if snapshot.transaction_state is not step.from_state:
        raise ProtocolError("transaction transition does not match current state")
    if (step.from_state, step.to_state) not in _TRANSACTION_RECOVERY_EDGES:
        raise ProtocolError("transaction transition is not an allowed recovery edge")

    has_result = step.rollback_result is not None
    has_diagnostic = step.halt_diagnostic is not None
    if step.rollback_result is not None and type(step.rollback_result) is not RollbackResult:
        raise ProtocolError("transaction rollback result has the wrong exact runtime type")
    if step.halt_diagnostic is not None and type(step.halt_diagnostic) is not HaltDiagnostic:
        raise ProtocolError("transaction halt diagnostic has the wrong exact runtime type")
    if step.to_state is TransactionState.ROLLED_BACK:
        valid_payload = has_result and not has_diagnostic
    elif step.to_state is TransactionState.HALTED:
        valid_payload = has_diagnostic and not has_result
    else:
        valid_payload = not has_result and not has_diagnostic
    if not valid_payload:
        raise ProtocolError("transaction transition has invalid terminal payloads")
    if (
        step.to_state is TransactionState.HALTED
        and step.halt_diagnostic is not None
        and step.halt_diagnostic.pre_halt_state is not step.from_state
    ):
        raise ProtocolError(
            "halt diagnostic pre-halt state does not match its transition source"
        )

    return _rebuild(
        snapshot,
        transaction_state=step.to_state,
        rollback_result=step.rollback_result,
        halt_diagnostic=step.halt_diagnostic,
    )


def _transition_effect(
    snapshot: RecoverySnapshot,
    raw_step: RecoveryStep,
) -> RecoverySnapshot:
    if type(raw_step) is not TransitionEffectState:
        raise ProtocolError("effect reducer received the wrong step type")
    step = raw_step
    if type(step.effect_id) is not str:
        raise ProtocolError("effect transition effect_id must be an exact string")
    if type(step.from_state) is not JournalState or type(step.to_state) is not JournalState:
        raise ProtocolError("effect transition states must be exact JournalState values")

    matching = [
        index
        for index, journal in enumerate(snapshot.journals)
        if journal.effect_id == step.effect_id
    ]
    if len(matching) != 1:
        raise ProtocolError("effect transition names an unknown effect")
    index = matching[0]
    if snapshot.journals[index].state is not step.from_state:
        raise ProtocolError("effect transition does not match current journal state")
    if (step.from_state, step.to_state) not in _EFFECT_RECOVERY_EDGES:
        raise ProtocolError("effect transition is not an allowed recovery edge")

    journals = tuple(
        EffectJournalState(journal.effect_id, step.to_state)
        if position == index
        else journal
        for position, journal in enumerate(snapshot.journals)
    )
    return _rebuild(snapshot, journals=journals)


def _transform_effect_tuple(
    snapshot: RecoverySnapshot,
    raw_step: RecoveryStep,
) -> RecoverySnapshot:
    if type(raw_step) is not TransformEffectTuple:
        raise ProtocolError("tuple reducer received the wrong step type")
    step = raw_step
    effect = _validate_mutating_step_header(snapshot, step.effect_id)
    if type(step.variant) is not EffectVariant:
        raise ProtocolError("transform variant must be an exact EffectVariant")
    if _EFFECT_VARIANTS[type(effect)] is not step.variant:
        raise ProtocolError("transform variant does not match its compiled effect")
    if type(step.settlement) is not SettlementKind:
        raise ProtocolError("transform settlement must be an exact SettlementKind")
    _validate_identity_relations(step.identity_relations)

    _validate_joint_coverage(step.expected_before, step.result_after)
    _validate_effect_joint_coverage(snapshot, effect, step.expected_before)
    current = _current_joint_observation(snapshot, step.expected_before)
    if current != step.expected_before:
        raise ProtocolError("transform expected_before does not match current observation")
    _require_exact_entry_transfers(step.expected_before, step.result_after)
    _require_parent_unmodeled_conservation(
        step.expected_before,
        step.result_after,
    )
    _require_removed_directories_empty(
        effect,
        step.expected_before,
        step.result_after,
    )

    persistent, scratch = _merge_joint_observation(snapshot, step.result_after)
    recomputed = _recompute_occupancy(
        snapshot,
        step.result_after.parent_occupancy,
        persistent,
        scratch,
    )
    if recomputed != step.result_after.parent_occupancy:
        raise ProtocolError("transform result_after has stale parent occupancy")
    return _rebuild(snapshot, persistent=persistent, scratch=scratch)


def _remove_scratch(
    snapshot: RecoverySnapshot,
    raw_step: RecoveryStep,
) -> RecoverySnapshot:
    if type(raw_step) is not RemoveScratch:
        raise ProtocolError("scratch reducer received the wrong step type")
    step = raw_step
    effect = _validate_mutating_step_header(snapshot, step.effect_id)
    if type(step.role) is not ScratchRole:
        raise ProtocolError("remove scratch role must be an exact ScratchRole")

    _validate_joint_coverage(step.expected_before, step.result_after)
    _validate_effect_joint_coverage(snapshot, effect, step.expected_before)
    current = _current_joint_observation(snapshot, step.expected_before)
    if current != step.expected_before:
        raise ProtocolError("remove scratch expected_before does not match current observation")
    _require_exact_entry_transfers(step.expected_before, step.result_after)
    _require_parent_unmodeled_conservation(
        step.expected_before,
        step.result_after,
    )
    _require_removed_directories_empty(
        effect,
        step.expected_before,
        step.result_after,
    )

    key = (step.effect_id, step.role)
    expected_scratch = {
        (item.effect_id, item.role): item for item in step.expected_before.scratch
    }
    result_scratch = {
        (item.effect_id, item.role): item for item in step.result_after.scratch
    }
    if key not in expected_scratch or key not in result_scratch:
        raise ProtocolError("remove scratch tuple does not contain its named scratch entry")
    if type(expected_scratch[key].entry) is ObservedAbsent:
        raise ProtocolError("remove scratch names an already absent entry")
    removed = result_scratch[key]
    if type(removed.entry) is not ObservedAbsent or removed.file_build_relation is not None:
        raise ProtocolError("remove scratch result must make its named entry absent")
    if step.result_after.persistent != step.expected_before.persistent:
        raise ProtocolError("remove scratch may not change persistent observations")
    for scratch_key, expected in expected_scratch.items():
        if scratch_key != key and result_scratch[scratch_key] != expected:
            raise ProtocolError("remove scratch may not change another scratch entry")

    persistent, scratch = _merge_joint_observation(snapshot, step.result_after)
    recomputed = _recompute_occupancy(
        snapshot,
        step.result_after.parent_occupancy,
        persistent,
        scratch,
    )
    if recomputed != step.result_after.parent_occupancy:
        raise ProtocolError("remove scratch result_after has stale parent occupancy")
    return _rebuild(snapshot, persistent=persistent, scratch=scratch)


def _preserve_external(
    snapshot: RecoverySnapshot,
    raw_step: RecoveryStep,
) -> RecoverySnapshot:
    if type(raw_step) is not PreserveExternal:
        raise ProtocolError("preserve reducer received the wrong step type")
    step = raw_step
    if type(step.nodes) is not tuple:
        raise ProtocolError("preserved nodes must be an exact tuple")
    if len(set(step.nodes)) != len(step.nodes):
        raise ProtocolError("preserved nodes must be unique")
    topology_nodes = _topology_nodes(snapshot)
    for node in step.nodes:
        if type(node) not in _TOPOLOGY_NODE_TYPES or node not in topology_nodes:
            raise ProtocolError("preserve external names an unknown topology node")
    return snapshot


def _detach_active(
    snapshot: RecoverySnapshot,
    raw_step: RecoveryStep,
) -> RecoverySnapshot:
    if type(raw_step) is not DetachActive:
        raise ProtocolError("detach reducer received the wrong step type")
    if not snapshot.active:
        raise ProtocolError("active binding is already detached")
    if snapshot.transaction_state not in {
        TransactionState.COMMITTED,
        TransactionState.ROLLED_BACK,
    }:
        raise ProtocolError("active binding may detach only from a terminal transaction")
    return _rebuild(snapshot, active=False)


def _validate_mutating_step_header(
    snapshot: RecoverySnapshot,
    effect_id: object,
) -> Effect:
    if type(effect_id) is not str:
        raise ProtocolError("mutating step effect_id must be an exact string")
    matches = [
        effect for effect in snapshot.compiled.spec.effects if effect.effect_id == effect_id
    ]
    if len(matches) != 1:
        raise ProtocolError("mutating step names an unknown effect")
    return matches[0]


def _validate_identity_relations(
    relations: tuple[DiagnosticIdentityRelation, ...],
) -> None:
    if type(relations) is not tuple:
        raise ProtocolError("transform identity_relations must be an exact tuple")
    pairs: list[tuple[str, str]] = []
    for relation in relations:
        if type(relation) is not DiagnosticIdentityRelation:
            raise ProtocolError("transform identity relation has the wrong exact runtime type")
        if type(relation.left_slot) is not str or type(relation.right_slot) is not str:
            raise ProtocolError("transform identity relation slots must be exact strings")
        if type(relation.relation) is not IdentityRelation:
            raise ProtocolError("transform identity relation must be an exact IdentityRelation")
        pairs.append((relation.left_slot, relation.right_slot))
    if pairs != sorted(pairs) or len(set(pairs)) != len(pairs):
        raise ProtocolError("transform identity relations must be sorted and unique")


def _validate_effect_joint_coverage(
    snapshot: RecoverySnapshot,
    effect: Effect,
    observation: JointObservation,
) -> None:
    persistent_keys = {item.path for item in observation.persistent}
    required_paths = {item.path for item in occurrences(effect)}
    if persistent_keys != required_paths:
        raise ProtocolError(
            "effect tuple omits or adds persistent nodes instead of exact effect coverage"
        )
    scratch_keys = {(item.effect_id, item.role) for item in observation.scratch}
    required_key = (effect.effect_id, required_scratch_role(effect))
    if scratch_keys != {required_key}:
        raise ProtocolError(
            "effect tuple omits or adds scratch nodes instead of exact effect coverage"
        )
    occupancy_keys = {item.parent for item in observation.parent_occupancy}
    required_occupancy = _required_effect_occupancy_parents(
        snapshot,
        effect,
        observation,
    )
    if occupancy_keys != required_occupancy:
        raise ProtocolError(
            "effect tuple does not have exact affected occupancy coverage"
        )


def _required_effect_occupancy_parents(
    snapshot: RecoverySnapshot,
    effect: Effect,
    observation: JointObservation,
) -> set[TopologyNode]:
    topology_parents = {edge.parent for edge in snapshot.topology.parents}
    return {
        node
        for node, entry in _effect_observed_nodes(effect, observation)
        if type(entry) is ObservedDirectory and node in topology_parents
    }


def _effect_observed_nodes(
    effect: Effect,
    observation: JointObservation,
) -> tuple[tuple[TopologyNode, ObservedEntry], ...]:
    persistent_by_path = {
        item.path: item.entry for item in observation.persistent
    }
    scratch_by_key = {
        (item.effect_id, item.role): item.entry for item in observation.scratch
    }
    persistent = tuple(
        (PersistentNode(item.path), persistent_by_path[item.path])
        for item in occurrences(effect)
    )
    scratch_role = required_scratch_role(effect)
    scratch = ScratchNode(effect.effect_id, scratch_role)
    return (
        *persistent,
        ((scratch, scratch_by_key[(effect.effect_id, scratch_role)])),
    )


def _validate_joint_coverage(
    expected: JointObservation,
    result: JointObservation,
) -> None:
    expected_keys = _joint_keys(expected)
    result_keys = _joint_keys(result)
    if expected_keys != result_keys:
        raise ProtocolError("joint observations do not have exact matching node coverage")


def _joint_keys(
    observation: JointObservation,
) -> tuple[
    frozenset[str],
    frozenset[tuple[str, ScratchRole]],
    frozenset[TopologyNode],
]:
    if type(observation) is not JointObservation:
        raise ProtocolError("step observation must be an exact JointObservation")
    if (
        type(observation.persistent) is not tuple
        or type(observation.scratch) is not tuple
        or type(observation.parent_occupancy) is not tuple
    ):
        raise ProtocolError("step observation fields must be exact tuples")

    persistent_keys: list[str] = []
    for item in observation.persistent:
        if type(item) is not PersistentObservation or type(item.path) is not str:
            raise ProtocolError("step persistent observation has the wrong exact runtime type")
        _validate_observed_entry(item.entry, "step persistent observations")
        persistent_keys.append(item.path)
    scratch_keys: list[tuple[str, ScratchRole]] = []
    for item in observation.scratch:
        if (
            type(item) is not ScratchObservation
            or type(item.effect_id) is not str
            or type(item.role) is not ScratchRole
        ):
            raise ProtocolError("step scratch observation has the wrong exact runtime type")
        _validate_observed_entry(item.entry, "step scratch observations")
        if (
            item.file_build_relation is not None
            and type(item.file_build_relation) is not FileBuildRelation
        ):
            raise ProtocolError(
                "step scratch file_build_relation has the wrong exact runtime type"
            )
        scratch_keys.append((item.effect_id, item.role))
    parent_keys: list[TopologyNode] = []
    for item in observation.parent_occupancy:
        if type(item) is not ParentOccupancy:
            raise ProtocolError("step parent occupancy has the wrong exact runtime type")
        if type(item.parent) not in _TOPOLOGY_NODE_TYPES:
            raise ProtocolError("step parent occupancy names the wrong node type")
        _validate_topology_node(item.parent)
        if type(item.present_children) is not tuple:
            raise ProtocolError("step present children must be an exact tuple")
        if type(item.has_unmodeled_child) is not bool:
            raise ProtocolError("step parent occupancy flag must be an exact bool")
        for child in item.present_children:
            if type(child) not in _TOPOLOGY_NODE_TYPES:
                raise ProtocolError("step parent occupancy child has the wrong node type")
            _validate_topology_node(child)
        if len(set(item.present_children)) != len(item.present_children):
            raise ProtocolError("step parent occupancy children must be unique")
        parent_keys.append(item.parent)

    if len(set(persistent_keys)) != len(persistent_keys):
        raise ProtocolError("step persistent observation keys must be unique")
    if len(set(scratch_keys)) != len(scratch_keys):
        raise ProtocolError("step scratch observation keys must be unique")
    if len(set(parent_keys)) != len(parent_keys):
        raise ProtocolError("step parent occupancy keys must be unique")
    return (
        frozenset(persistent_keys),
        frozenset(scratch_keys),
        frozenset(parent_keys),
    )


def _current_joint_observation(
    snapshot: RecoverySnapshot,
    template: JointObservation,
) -> JointObservation:
    persistent_keys, scratch_keys, _ = _joint_keys(template)
    persistent_by_path = {
        item.path: item for item in snapshot.persistent_observations
    }
    scratch_by_key = {
        (item.effect_id, item.role): item for item in snapshot.scratch_observations
    }
    if not persistent_keys <= persistent_by_path.keys():
        raise ProtocolError("step observation names an unknown persistent path")
    if not scratch_keys <= scratch_by_key.keys():
        raise ProtocolError("step observation names an unknown scratch entry")
    persistent = tuple(persistent_by_path[item.path] for item in template.persistent)
    scratch = tuple(
        scratch_by_key[(item.effect_id, item.role)] for item in template.scratch
    )
    occupancy = _recompute_occupancy(
        snapshot,
        template.parent_occupancy,
        snapshot.persistent_observations,
        snapshot.scratch_observations,
        validate_current_directory=True,
    )
    return JointObservation(
        persistent=persistent,
        scratch=scratch,
        parent_occupancy=occupancy,
    )


def _merge_joint_observation(
    snapshot: RecoverySnapshot,
    observation: JointObservation,
) -> tuple[tuple[PersistentObservation, ...], tuple[ScratchObservation, ...]]:
    persistent_keys, scratch_keys, _ = _joint_keys(observation)
    source_persistent = {item.path for item in snapshot.persistent_observations}
    source_scratch = {
        (item.effect_id, item.role) for item in snapshot.scratch_observations
    }
    if not persistent_keys <= source_persistent:
        raise ProtocolError("step result names an unknown persistent path")
    if not scratch_keys <= source_scratch:
        raise ProtocolError("step result names an unknown scratch entry")

    persistent_updates = {item.path: item for item in observation.persistent}
    scratch_updates = {
        (item.effect_id, item.role): item for item in observation.scratch
    }
    persistent = tuple(
        persistent_updates.get(item.path, item)
        for item in snapshot.persistent_observations
    )
    scratch = tuple(
        scratch_updates.get((item.effect_id, item.role), item)
        for item in snapshot.scratch_observations
    )
    return persistent, scratch


def _require_exact_entry_transfers(
    expected: JointObservation,
    result: JointObservation,
) -> None:
    available = {
        entry
        for entry in (
            *(item.entry for item in expected.persistent),
            *(item.entry for item in expected.scratch),
        )
        if type(entry) is not ObservedAbsent
    }
    for entry in (
        *(item.entry for item in result.persistent),
        *(item.entry for item in result.scratch),
    ):
        if type(entry) is ObservedAbsent:
            continue
        if entry not in available:
            raise ProtocolError(
                "step result fabricates or alters an entry, including identity or "
                "unmodeled evidence, instead of transferring it exactly"
            )


def _require_removed_directories_empty(
    effect: Effect,
    expected: JointObservation,
    result: JointObservation,
) -> None:
    if type(effect) is not CreateDirectory:
        return
    expected_nodes = dict(_effect_observed_nodes(effect, expected))
    result_nodes = dict(_effect_observed_nodes(effect, result))
    removed_directory = False
    for node, entry in expected_nodes.items():
        result_entry = result_nodes[node]
        if type(result_entry) is not ObservedAbsent or result_entry == entry:
            continue
        if type(entry) is not ObservedDirectory:
            raise ProtocolError(
                "CreateDirectory removal requires an exact directory before-entry"
            )
        removed_directory = True
    if all(type(entry) is ObservedAbsent for entry in result_nodes.values()) and (
        not removed_directory
    ):
        raise ProtocolError(
            "CreateDirectory removal requires an exact directory before-entry"
        )

    surviving_entries = {
        entry
        for entry in (
            *(item.entry for item in result.persistent),
            *(item.entry for item in result.scratch),
        )
        if type(entry) is not ObservedAbsent
    }
    occupancy_by_parent = {
        item.parent: item for item in expected.parent_occupancy
    }
    for node, entry in _effect_observed_nodes(effect, expected):
        if type(entry) is not ObservedDirectory or entry in surviving_entries:
            continue
        occupancy = occupancy_by_parent.get(node)
        if entry.has_unmodeled_child or (
            occupancy is not None and occupancy.present_children
        ):
            raise ProtocolError("directory is not empty and may not be removed")


def _require_parent_unmodeled_conservation(
    expected: JointObservation,
    result: JointObservation,
) -> None:
    expected_facts = {
        item.parent: item.has_unmodeled_child
        for item in expected.parent_occupancy
    }
    if any(
        item.has_unmodeled_child != expected_facts[item.parent]
        for item in result.parent_occupancy
    ):
        raise ProtocolError("step result changes parent unmodeled-child evidence")


def _topology_nodes(snapshot: RecoverySnapshot) -> set[TopologyNode]:
    nodes: set[TopologyNode] = {ProjectRoot()}
    for edge in snapshot.topology.parents:
        nodes.update((edge.node, edge.parent))
    return nodes


def _recompute_occupancy(
    snapshot: RecoverySnapshot,
    occupancy: tuple[ParentOccupancy, ...],
    persistent: tuple[PersistentObservation, ...],
    scratch: tuple[ScratchObservation, ...],
    *,
    validate_current_directory: bool = False,
) -> tuple[ParentOccupancy, ...]:
    persistent_by_path = {item.path: item.entry for item in persistent}
    scratch_by_key = {
        (item.effect_id, item.role): item.entry for item in scratch
    }
    _validate_occupancy_topology(snapshot, occupancy)
    result: list[ParentOccupancy] = []
    for item in occupancy:
        has_unmodeled_child = item.has_unmodeled_child
        if validate_current_directory:
            has_unmodeled_child = _current_directory_unmodeled_fact(
                item.parent,
                persistent_by_path,
                scratch_by_key,
            )
        present_children: list[TopologyNode] = []
        for edge in snapshot.topology.parents:
            if edge.parent != item.parent:
                continue
            child = edge.node
            if type(child) is PersistentNode:
                present = type(persistent_by_path[child.path]) is not ObservedAbsent
            elif type(child) is ScratchNode:
                key = (child.effect_id, child.role)
                present = type(scratch_by_key[key]) is not ObservedAbsent
            else:
                present = True
            if present:
                present_children.append(child)
        result.append(
            ParentOccupancy(
                parent=item.parent,
                present_children=tuple(present_children),
                has_unmodeled_child=has_unmodeled_child,
            )
        )
    return tuple(result)


def _current_directory_unmodeled_fact(
    parent: TopologyNode,
    persistent_by_path: Mapping[str, ObservedEntry],
    scratch_by_key: Mapping[tuple[str, ScratchRole], ObservedEntry],
) -> bool:
    if type(parent) is PersistentNode:
        entry = persistent_by_path[parent.path]
    elif type(parent) is ScratchNode:
        entry = scratch_by_key[(parent.effect_id, parent.role)]
    else:
        raise ProtocolError(
            "current occupancy parent has no exact directory observation"
        )
    if type(entry) is not ObservedDirectory:
        raise ProtocolError(
            "current occupancy parent is absent or not an exact directory"
        )
    return entry.has_unmodeled_child


def _validate_occupancy_topology(
    snapshot: RecoverySnapshot,
    occupancy: tuple[ParentOccupancy, ...],
) -> None:
    direct_children: dict[TopologyNode, set[TopologyNode]] = {}
    for edge in snapshot.topology.parents:
        direct_children.setdefault(edge.parent, set()).add(edge.node)
    for item in occupancy:
        children = direct_children.get(item.parent)
        if children is None:
            raise ProtocolError(
                "parent occupancy does not name an actual topology parent"
            )
        if any(child not in children for child in item.present_children):
            raise ProtocolError(
                "parent occupancy names a child outside its topology parent"
            )


def _normalize_file_build_relations(
    compiled: CompiledSpec,
    journals: tuple[EffectJournalState, ...],
    persistent: tuple[PersistentObservation, ...],
    scratch: tuple[ScratchObservation, ...],
) -> tuple[ScratchObservation, ...]:
    journals_by_id = {item.effect_id: item.state for item in journals}
    persistent_by_path = {item.path: item.entry for item in persistent}
    effects_by_id = {effect.effect_id: effect for effect in compiled.spec.effects}
    normalized: list[ScratchObservation] = []
    for item in scratch:
        try:
            effect = effects_by_id[item.effect_id]
            journal = journals_by_id[item.effect_id]
        except (AttributeError, KeyError) as exc:
            raise ProtocolError("scratch normalization has incomplete effect coverage") from exc
        relation_required = _construction_relation_required(
            effect,
            journal,
            persistent_by_path,
            item,
        )
        normalized.append(
            item
            if relation_required or item.file_build_relation is None
            else ScratchObservation(
                effect_id=item.effect_id,
                role=item.role,
                entry=item.entry,
                file_build_relation=None,
            )
        )
    return tuple(normalized)


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
        try:
            live = persistent_by_path[effect.path]
        except KeyError as exc:
            raise ProtocolError("replace relation normalization is missing its live path") from exc
        return type(live) is ObservedFile and live.state == effect.pre
    return False


def _normalize_joint_observation(
    snapshot: RecoverySnapshot,
    observation: JointObservation,
) -> JointObservation:
    if type(snapshot) is not RecoverySnapshot:
        raise ProtocolError("snapshot must be a factory-issued RecoverySnapshot")
    persistent, scratch = _merge_joint_observation(snapshot, observation)
    _validate_occupancy_topology(snapshot, observation.parent_occupancy)
    normalized = _normalize_file_build_relations(
        snapshot.compiled,
        snapshot.journals,
        persistent,
        scratch,
    )
    normalized_by_key = {
        (item.effect_id, item.role): item for item in normalized
    }
    return JointObservation(
        persistent=observation.persistent,
        scratch=tuple(
            normalized_by_key[(item.effect_id, item.role)]
            for item in observation.scratch
        ),
        parent_occupancy=observation.parent_occupancy,
    )


_StepReducer = Callable[[RecoverySnapshot, RecoveryStep], RecoverySnapshot]

_STEP_REDUCERS: dict[type[object], _StepReducer] = {
    TransitionTransactionState: _transition_transaction,
    TransitionEffectState: _transition_effect,
    TransformEffectTuple: _transform_effect_tuple,
    RemoveScratch: _remove_scratch,
    PreserveExternal: _preserve_external,
    DetachActive: _detach_active,
}
