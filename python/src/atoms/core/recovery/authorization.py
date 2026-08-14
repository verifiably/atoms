from __future__ import annotations

from typing import cast

from atoms.core.errors import ProtocolError
from atoms.core.recovery.classifier import _non_authorizable
from atoms.core.recovery.diagnostics import (
    _diagnostic,
    _project_entries,
    _project_identity_relations,
)
from atoms.core.recovery.model import HaltReason, TransactionState
from atoms.core.recovery.plan import (
    ActionPlan,
    AuthorizedStep,
    HaltPlan,
    JointObservation,
    NoRecoveryPlan,
    RecoveryPlan,
    RemoveScratch,
    TransformEffectTuple,
    TransitionTransactionState,
    _new_authorized_step,
    _new_halt_plan,
)
from atoms.core.recovery.reducer import (
    _merge_joint_observation,
    _normalize_joint_observation,
    _validate_joint_coverage,
    reduce_recovery_plan_prefix,
)
from atoms.core.recovery.snapshot import (
    RecoverySnapshot,
    build_recovery_snapshot,
)


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

    raw_step = plan.steps[step_index]
    if type(raw_step) not in {TransformEffectTuple, RemoveScratch}:
        raise ProtocolError(
            "step_index must name a filesystem-mutating step"
        )
    step = cast(TransformEffectTuple | RemoveScratch, raw_step)

    prefix = reduce_recovery_plan_prefix(
        plan.bound_snapshot,
        plan,
        completed_steps=step_index,
    )
    expected = step.expected_before
    _validate_joint_coverage(expected, observed)
    if _non_authorizable(observed):
        return _halt_for_observation(
            prefix,
            expected,
            observed,
            HaltReason.PLAN_PRECONDITION_CHANGED,
        )
    if _authorization_projection(observed) == _authorization_projection(
        expected
    ):
        return _new_authorized_step(plan, step_index, step)
    return _precondition_changed_halt(prefix, expected, observed)


def _authorization_projection(observation: JointObservation):
    return (
        _project_entries(observation),
        observation.parent_occupancy,
        _project_identity_relations("authorization", observation),
    )


def _precondition_changed_halt(
    prefix: RecoverySnapshot,
    expected: JointObservation,
    observed: JointObservation,
) -> HaltPlan:
    return _halt_for_observation(
        prefix, expected, observed, HaltReason.PLAN_PRECONDITION_CHANGED
    )


def _mutation_denied(
    authorized: AuthorizedStep, observed: JointObservation
) -> HaltPlan:
    if type(authorized) is not AuthorizedStep:
        raise ProtocolError("authorized must be an exact AuthorizedStep")
    if type(observed) is not JointObservation:
        raise ProtocolError("observed must be an exact JointObservation")
    expected = authorized.step.expected_before
    _validate_joint_coverage(expected, observed)
    if _authorization_projection(observed) != _authorization_projection(expected):
        raise ProtocolError("observed no longer authorizes the supplied step")
    prefix = reduce_recovery_plan_prefix(
        authorized.plan.bound_snapshot,
        authorized.plan,
        completed_steps=authorized.step_index,
    )
    return _halt_for_observation(
        prefix, expected, observed, HaltReason.MUTATION_DENIED
    )


def _halt_for_observation(
    prefix: RecoverySnapshot,
    expected: JointObservation,
    observed: JointObservation,
    reason: HaltReason,
) -> HaltPlan:
    normalized = _normalize_joint_observation(prefix, observed)
    persistent, scratch = _merge_joint_observation(prefix, normalized)
    conflicting_prefix = build_recovery_snapshot(
        compiled=prefix.compiled,
        topology=prefix.topology,
        transaction_state=prefix.transaction_state,
        commit_decision=prefix.commit_decision,
        rollback_result=prefix.rollback_result,
        halt_diagnostic=prefix.halt_diagnostic,
        active=prefix.active,
        journals=prefix.journals,
        persistent_observations=persistent,
        scratch_observations=scratch,
    )
    diagnostic = _diagnostic(
        conflicting_prefix,
        reason=reason,
        expected=expected,
        observed=observed,
    )
    transition = TransitionTransactionState(
        from_state=conflicting_prefix.transaction_state,
        to_state=TransactionState.HALTED,
        rollback_result=None,
        halt_diagnostic=diagnostic,
    )
    return _new_halt_plan(
        bound_snapshot=conflicting_prefix,
        diagnostic=diagnostic,
        steps=(transition,),
    )
