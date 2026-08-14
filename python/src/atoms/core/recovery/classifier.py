from __future__ import annotations

from dataclasses import replace
from typing import cast

from atoms.core.errors import ProtocolError
from atoms.core.fingerprint import ABSENT, PathState
from atoms.core.recovery.diagnostics import _diagnostic
from atoms.core.recovery.journal import (
    AuthorityKind,
    classify_transaction_authority,
    reconstruct_frontiers,
)
from atoms.core.recovery.model import (
    HaltDiagnostic,
    HaltReason,
    JournalState,
    ObservedAbsent,
    ObservedContended,
    ObservedDirectory,
    ObservedEntry,
    ObservedFile,
    ObservedInaccessible,
    ObservedSymlink,
    ObservedUnrecognized,
    RollbackResult,
    ScratchRole,
    TransactionState,
)
from atoms.core.recovery.plan import (
    DetachActive,
    JointObservation,
    PlanDisposition,
    PreserveExternal,
    RecoveryPlan,
    RecoveryStep,
    RemoveScratch,
    TransformEffectTuple,
    TransitionEffectState,
    TransitionTransactionState,
    _new_action_plan,
    _new_halt_plan,
    _new_no_recovery_plan,
    _validate_steps,
)
from atoms.core.recovery.reducer import (
    _apply_steps,
    _current_joint_observation,
    _normalize_joint_observation,
    _validate_identity_relations,
    _validate_joint_coverage,
)
from atoms.core.recovery.snapshot import (
    RecoverySnapshot,
    _validate_topology_node,
)
from atoms.core.recovery.variants import (
    EffectDecision,
    EffectDecisionKind,
    _joint,
    classify_committed_cleanup,
    classify_effect,
)

_SEMANTIC_DECISION_STEP_TYPES = {
    TransformEffectTuple,
    RemoveScratch,
    PreserveExternal,
}

_DECISION_STEP_SHAPES = {
    EffectDecisionKind.NO_ACTION: frozenset({()}),
    EffectDecisionKind.PRESERVE_EXTERNAL: frozenset(
        {(PreserveExternal,)}
    ),
    EffectDecisionKind.UNDO_WITHOUT_MUTATION: frozenset({()}),
    EffectDecisionKind.REMOVE_SCRATCH: frozenset({(RemoveScratch,)}),
    EffectDecisionKind.EXCHANGE_BACK: frozenset(
        {(TransformEffectTuple, RemoveScratch)}
    ),
    EffectDecisionKind.REFUSED_EXCHANGE_BACK: frozenset(
        {(TransformEffectTuple, PreserveExternal, RemoveScratch)}
    ),
    EffectDecisionKind.ALREADY_UNDONE: frozenset({()}),
    EffectDecisionKind.REMOVE_LIVE_CREATION: frozenset(
        {(TransformEffectTuple, RemoveScratch)}
    ),
    EffectDecisionKind.REFUSED_PRESERVE_LIVE: frozenset(
        {
            (PreserveExternal,),
            (PreserveExternal, RemoveScratch),
        }
    ),
    EffectDecisionKind.RESTORE_TOMBSTONE: frozenset(
        {(TransformEffectTuple,)}
    ),
    EffectDecisionKind.REMOVE_ANCHOR: frozenset({(RemoveScratch,)}),
    EffectDecisionKind.RESTORE_SOURCE: frozenset(
        {(TransformEffectTuple, RemoveScratch)}
    ),
    EffectDecisionKind.REMOVE_DESTINATION: frozenset(
        {(TransformEffectTuple, RemoveScratch)}
    ),
    EffectDecisionKind.RESTORE_FROM_ANCHOR: frozenset(
        {(TransformEffectTuple, RemoveScratch)}
    ),
    EffectDecisionKind.REFUSED_PRESERVE_DESTINATION: frozenset(
        {(PreserveExternal, RemoveScratch)}
    ),
    EffectDecisionKind.REMOVE_WORK: frozenset({(RemoveScratch,)}),
    EffectDecisionKind.REMOVE_LIVE_DIRECTORY: frozenset(
        {(TransformEffectTuple,)}
    ),
    EffectDecisionKind.REMOVE_DUAL_NAME_DIRECTORY: frozenset(
        {(RemoveScratch, TransformEffectTuple)}
    ),
    EffectDecisionKind.HALT: frozenset({()}),
}

_REFUSED_DECISION_KINDS = {
    EffectDecisionKind.PRESERVE_EXTERNAL,
    EffectDecisionKind.REFUSED_EXCHANGE_BACK,
    EffectDecisionKind.REFUSED_PRESERVE_LIVE,
    EffectDecisionKind.REFUSED_PRESERVE_DESTINATION,
}


def classify_recovery(snapshot: RecoverySnapshot) -> RecoveryPlan:
    if type(snapshot) is not RecoverySnapshot:
        raise ProtocolError("snapshot must be a factory-issued RecoverySnapshot")

    authority = classify_transaction_authority(snapshot)
    if authority.kind is AuthorityKind.NO_RECOVERY:
        return _new_no_recovery_plan(snapshot)
    if authority.kind is AuthorityKind.STABLE_HALT:
        return _stable_halt_plan(snapshot)
    if authority.kind is AuthorityKind.HALT:
        return _halt_plan(snapshot, cast(HaltReason, authority.halt_reason))
    if authority.kind is AuthorityKind.DETACH:
        return _terminal_detach_plan(snapshot)
    if authority.kind is not AuthorityKind.CLASSIFY:
        raise ProtocolError("transaction authority decision is outside the closed set")

    for effect in snapshot.compiled.spec.effects:
        observed = _joint(snapshot, effect)
        if _non_authorizable(observed):
            return _halt_plan(
                snapshot,
                HaltReason.EFFECT_TUPLE_UNATTRIBUTABLE,
                effect.effect_id,
                observed=observed,
            )

    if snapshot.transaction_state is TransactionState.COMMITTED:
        return _committed_plan(snapshot)
    return _rollback_plan(snapshot)


def _non_authorizable(observed: JointObservation) -> bool:
    entries = (
        *(item.entry for item in observed.persistent),
        *(item.entry for item in observed.scratch),
    )
    return any(
        type(entry) in {
            ObservedUnrecognized,
            ObservedContended,
            ObservedInaccessible,
        }
        or type(entry) is ObservedDirectory
        and entry.has_unmodeled_child is None
        for entry in entries
    )


def _stable_halt_plan(source: RecoverySnapshot) -> RecoveryPlan:
    diagnostic = source.halt_diagnostic
    if type(diagnostic) is not HaltDiagnostic:
        raise ProtocolError("HALTED snapshot is missing its exact halt diagnostic")
    return _new_halt_plan(
        bound_snapshot=source,
        diagnostic=diagnostic,
        steps=(),
    )


def _halt_plan(
    source: RecoverySnapshot,
    reason: HaltReason,
    effect_id: str | None = None,
    expected: JointObservation | None = None,
    observed: JointObservation | None = None,
    *,
    projected: RecoverySnapshot | None = None,
) -> RecoveryPlan:
    if type(source) is not RecoverySnapshot:
        raise ProtocolError("halt source must be a factory-issued RecoverySnapshot")
    if source.transaction_state is TransactionState.HALTED:
        return _stable_halt_plan(source)

    diagnostic = _diagnostic(
        source,
        reason=reason,
        projected=projected,
        effect_id=effect_id,
        expected=expected,
        observed=observed,
    )
    transition = TransitionTransactionState(
        from_state=source.transaction_state,
        to_state=TransactionState.HALTED,
        rollback_result=None,
        halt_diagnostic=diagnostic,
    )
    return _new_halt_plan(
        bound_snapshot=source,
        diagnostic=diagnostic,
        steps=(transition,),
    )


def _terminal_detach_plan(source: RecoverySnapshot) -> RecoveryPlan:
    return _new_action_plan(
        bound_snapshot=source,
        disposition=PlanDisposition.DETACH_TERMINAL,
        steps=(DetachActive(),),
    )


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
        _validate_effect_decision(decision)
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
    if (
        cursor.transaction_state is not TransactionState.ROLLED_BACK
        or cursor.active
    ):
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


def _validate_effect_decision(decision: EffectDecision) -> None:
    if type(decision) is not EffectDecision:
        raise ProtocolError("effect classifier returned the wrong exact decision type")
    if type(decision.kind) is not EffectDecisionKind:
        raise ProtocolError("effect decision kind must be an exact EffectDecisionKind")
    _validate_steps(decision.steps)
    if any(
        type(step) not in _SEMANTIC_DECISION_STEP_TYPES
        for step in decision.steps
    ):
        raise ProtocolError(
            "effect decision steps must be closed semantic step variants"
        )
    for step in decision.steps:
        if type(step) is TransformEffectTuple:
            transform = cast(TransformEffectTuple, step)
            if type(transform.effect_id) is not str:
                raise ProtocolError(
                    "effect decision transform effect_id must be an exact string"
                )
            _validate_identity_relations(transform.identity_relations)
            _validate_joint_coverage(
                transform.expected_before,
                transform.result_after,
            )
        elif type(step) is RemoveScratch:
            remove = cast(RemoveScratch, step)
            if type(remove.effect_id) is not str:
                raise ProtocolError(
                    "effect decision scratch effect_id must be an exact string"
                )
            if type(remove.role) is not ScratchRole:
                raise ProtocolError(
                    "effect decision scratch role must be an exact ScratchRole"
                )
            _validate_joint_coverage(
                remove.expected_before,
                remove.result_after,
            )
        else:
            preserve = cast(PreserveExternal, step)
            if type(preserve.nodes) is not tuple:
                raise ProtocolError(
                    "effect decision preserved nodes must be an exact tuple"
                )
            for node in preserve.nodes:
                _validate_topology_node(node)
    if type(decision.refused) is not bool:
        raise ProtocolError("effect decision refused flag must be an exact bool")
    if (
        decision.halt_reason is not None
        and type(decision.halt_reason) is not HaltReason
    ):
        raise ProtocolError("effect decision halt reason has the wrong exact type")
    if (
        type(decision.expected) is not JointObservation
        or type(decision.observed) is not JointObservation
    ):
        raise ProtocolError("effect decision evidence must be exact joint observations")
    _validate_joint_coverage(decision.expected, decision.observed)

    if decision.kind is EffectDecisionKind.HALT:
        if (
            decision.steps
            or decision.refused
            or decision.halt_reason is None
        ):
            raise ProtocolError("effect halt decision payload is inconsistent")
        return
    if decision.halt_reason is not None:
        raise ProtocolError(
            "non-halt effect decision may not carry a halt reason"
        )
    if decision.refused is not (decision.kind in _REFUSED_DECISION_KINDS):
        raise ProtocolError(
            "effect decision refused flag is inconsistent with its kind"
        )
    step_shape = tuple(type(step) for step in decision.steps)
    allowed_shapes = _DECISION_STEP_SHAPES.get(decision.kind)
    if allowed_shapes is None or step_shape not in allowed_shapes:
        raise ProtocolError(
            "effect decision step payload is inconsistent with its kind"
        )


def _reverse_effect_steps(
    cursor: RecoverySnapshot,
    effect_index: int,
    decision: EffectDecision,
) -> tuple[RecoveryStep, ...]:
    effect = cursor.compiled.spec.effects[effect_index]
    journal = cursor.journals[effect_index].state
    if type(journal) is not JournalState:
        raise ProtocolError("effect journal state has the wrong exact runtime type")

    steps: list[RecoveryStep] = []
    current = cursor
    if journal in {JournalState.STARTED, JournalState.DONE}:
        transition = TransitionEffectState(
            effect_id=effect.effect_id,
            from_state=journal,
            to_state=JournalState.UNDO_STARTED,
        )
        steps.append(transition)
        current = _apply_steps(current, (transition,))

    bound_steps, current = _bind_effect_steps(current, decision.steps)
    steps.extend(bound_steps)

    if journal in {
        JournalState.STARTED,
        JournalState.DONE,
        JournalState.UNDO_STARTED,
    }:
        transition = TransitionEffectState(
            effect_id=effect.effect_id,
            from_state=JournalState.UNDO_STARTED,
            to_state=JournalState.UNDONE,
        )
        steps.append(transition)
        current = _apply_steps(current, (transition,))
    elif journal not in {JournalState.PENDING, JournalState.UNDONE}:
        raise ProtocolError("effect journal state is outside the closed set")

    if current.journals[effect_index].state is not (
        journal
        if journal in {JournalState.PENDING, JournalState.UNDONE}
        else JournalState.UNDONE
    ):
        raise ProtocolError("effect reversal projection did not settle")
    return tuple(steps)


def _bind_effect_steps(
    cursor: RecoverySnapshot,
    provisional_steps: tuple[RecoveryStep, ...],
) -> tuple[tuple[RecoveryStep, ...], RecoverySnapshot]:
    if type(provisional_steps) is not tuple:
        raise ProtocolError("provisional effect steps must be an exact tuple")

    bound: list[RecoveryStep] = []
    current = cursor
    for provisional in provisional_steps:
        if type(provisional) not in {
            TransformEffectTuple,
            RemoveScratch,
            PreserveExternal,
        }:
            raise ProtocolError("effect decision contains a non-semantic step")
        step = provisional
        if type(provisional) in {TransformEffectTuple, RemoveScratch}:
            mutating = cast(
                TransformEffectTuple | RemoveScratch,
                provisional,
            )
            expected = _joint_from_cursor(
                current,
                mutating.expected_before,
            )
            result_after = _normalize_joint_observation(
                current,
                mutating.result_after,
            )
            step = replace(
                mutating,
                expected_before=expected,
                result_after=result_after,
            )
        bound.append(step)
        current = _apply_steps(current, (step,))
    return tuple(bound), current


def _joint_from_cursor(
    cursor: RecoverySnapshot,
    template: JointObservation,
) -> JointObservation:
    current = _current_joint_observation(cursor, template)
    return _normalize_joint_observation(cursor, current)


def _observed_state(entry: ObservedEntry) -> PathState:
    if type(entry) is ObservedAbsent:
        return ABSENT
    if type(entry) is ObservedFile:
        return entry.state
    if type(entry) is ObservedSymlink:
        return entry.state
    if type(entry) is ObservedDirectory:
        return entry.state
    raise ProtocolError("persistent observation has the wrong exact runtime type")


def _committed_surface_matches(source: RecoverySnapshot) -> bool:
    final_by_path = {
        item.path: item.state for item in source.compiled.spec.final_surface
    }
    return all(
        _observed_state(observation.entry)
        == final_by_path[observation.path]
        for observation in source.persistent_observations
    )


def _committed_plan(source: RecoverySnapshot) -> RecoveryPlan:
    if not _committed_surface_matches(source):
        return _halt_plan(
            source,
            HaltReason.COMMITTED_SURFACE_MISMATCH,
        )

    cursor = source
    steps: list[RecoveryStep] = []
    for index, effect in enumerate(source.compiled.spec.effects):
        decision = classify_committed_cleanup(cursor, index)
        _validate_effect_decision(decision)
        if decision.halt_reason is not None:
            return _halt_plan(
                source,
                decision.halt_reason,
                effect.effect_id,
                decision.expected,
                decision.observed,
                projected=cursor,
            )
        if decision.refused:
            raise ProtocolError("committed cleanup may not refuse rollback")
        if any(type(step) is not RemoveScratch for step in decision.steps):
            raise ProtocolError(
                "committed cleanup effect decision contains a non-cleanup step"
            )
        local_steps, cursor = _bind_effect_steps(cursor, decision.steps)
        steps.extend(local_steps)

    detach = DetachActive()
    steps.append(detach)
    cursor = _apply_steps(cursor, (detach,))
    if cursor.transaction_state is not TransactionState.COMMITTED or cursor.active:
        raise ProtocolError("terminal committed projection did not settle")
    return _new_action_plan(
        bound_snapshot=source,
        disposition=PlanDisposition.COMMITTED_CLEANUP,
        steps=tuple(steps),
    )
