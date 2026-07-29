from dataclasses import FrozenInstanceError, replace

import pytest

from atoms.core.errors import ProtocolError
from atoms.core.recovery import (
    ActionPlan,
    AuthorizedStep,
    CommitDecision,
    EffectJournalState,
    EffectVariant,
    HaltDiagnostic,
    HaltPlan,
    HaltReason,
    JointObservation,
    JournalState,
    NoRecoveryPlan,
    OperatorAction,
    PlanDisposition,
    RollbackResult,
    SettlementKind,
    TransactionState,
)
from atoms.core.recovery.plan import (
    DetachActive,
    RemoveScratch,
    TransformEffectTuple,
    TransitionTransactionState,
    _new_action_plan,
    _new_authorized_step,
    _new_halt_plan,
    _new_no_recovery_plan,
)
from tests.recovery_support import create_snapshot


def _halt_diagnostic() -> HaltDiagnostic:
    return HaltDiagnostic(
        pre_halt_state=TransactionState.APPLYING,
        commit_decision=CommitDecision.UNCOMMITTED,
        journals=(EffectJournalState("e1", JournalState.STARTED),),
        projected_transaction_state=TransactionState.APPLYING,
        projected_journals=(EffectJournalState("e1", JournalState.STARTED),),
        effect_id=None,
        paths=(),
        expected=(),
        observed=(),
        identity_relations=(),
        reason=HaltReason.PLAN_PRECONDITION_CHANGED,
        operator_action=OperatorAction.INSPECT_PRESERVED_EVIDENCE,
    )


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
    with pytest.raises(TypeError, match="classify_recovery"):
        replace(plan, bound_snapshot=snapshot)


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
    expected = {
        PlanDisposition.ROLL_BACK: RollbackResult.RESTORED,
        PlanDisposition.ROLL_BACK_REFUSED: RollbackResult.EXTERNAL_DRIFT_PRESERVED,
        PlanDisposition.COMMITTED_CLEANUP: None,
        PlanDisposition.DETACH_TERMINAL: None,
    }
    assert plan.rollback_result is expected[disposition]


def test_no_recovery_plan_is_a_distinct_variant_and_factory_controlled():
    snapshot = create_snapshot(active=False)
    plan = _new_no_recovery_plan(snapshot)
    assert type(plan) is NoRecoveryPlan
    assert plan.disposition is PlanDisposition.NO_RECOVERY
    assert not isinstance(plan, (ActionPlan, HaltPlan))
    with pytest.raises(TypeError, match="classify_recovery"):
        NoRecoveryPlan(bound_snapshot=snapshot)
    with pytest.raises(TypeError, match="classify_recovery"):
        replace(plan)
    with pytest.raises(TypeError, match="classify_recovery"):
        replace(plan, bound_snapshot=snapshot)


def test_halt_plan_is_factory_controlled_with_guarded_replace_paths():
    snapshot = create_snapshot()
    diagnostic = _halt_diagnostic()
    transition = TransitionTransactionState(
        from_state=snapshot.transaction_state,
        to_state=TransactionState.HALTED,
        rollback_result=None,
        halt_diagnostic=diagnostic,
    )
    plan = _new_halt_plan(
        bound_snapshot=snapshot,
        diagnostic=diagnostic,
        steps=(transition,),
    )
    assert plan.disposition is PlanDisposition.HALT

    with pytest.raises(TypeError, match="classify_recovery"):
        HaltPlan(bound_snapshot=snapshot, diagnostic=diagnostic, steps=())
    with pytest.raises(TypeError, match="classify_recovery"):
        replace(plan)
    with pytest.raises(TypeError, match="classify_recovery"):
        replace(plan, bound_snapshot=snapshot)


def test_joint_observation_is_exact_node_evidence():
    snapshot = create_snapshot()
    observed = JointObservation(
        persistent=snapshot.persistent_observations,
        scratch=snapshot.scratch_observations,
        parent_occupancy=(),
    )
    assert observed.persistent == snapshot.persistent_observations
    assert observed.scratch == snapshot.scratch_observations


def test_action_factory_refuses_non_action_disposition_and_invalid_steps():
    snapshot = create_snapshot()
    with pytest.raises(ProtocolError, match="non-action disposition"):
        _new_action_plan(
            bound_snapshot=snapshot,
            disposition=PlanDisposition.HALT,
            steps=(),
        )
    with pytest.raises(ProtocolError, match="closed step variants"):
        _new_action_plan(
            bound_snapshot=snapshot,
            disposition=PlanDisposition.ROLL_BACK,
            steps=(object(),),  # type: ignore[arg-type]
        )


def test_authorized_step_is_factory_controlled_and_limited_to_mutations():
    snapshot = create_snapshot()
    plan = _new_action_plan(
        bound_snapshot=snapshot,
        disposition=PlanDisposition.ROLL_BACK,
        steps=(),
    )
    observation = JointObservation((), (), ())
    step = RemoveScratch(
        effect_id="e1",
        role=snapshot.scratch_observations[0].role,
        expected_before=observation,
        result_after=observation,
    )
    authorized = _new_authorized_step(plan, 0, step)
    assert authorized.step is step
    with pytest.raises(TypeError, match="authorize_recovery_step"):
        AuthorizedStep(plan=plan, step_index=0, step=step)
    with pytest.raises(TypeError, match="authorize_recovery_step"):
        replace(authorized)
    with pytest.raises(TypeError, match="authorize_recovery_step"):
        replace(authorized, step_index=1)
    with pytest.raises(ProtocolError, match="filesystem-mutating"):
        _new_authorized_step(plan, 0, DetachActive())  # type: ignore[arg-type]


def test_halt_plan_factory_accepts_only_bound_halt_transition():
    snapshot = create_snapshot()
    diagnostic = _halt_diagnostic()
    invalid_transition = TransitionTransactionState(
        from_state=TransactionState.PREPARED,
        to_state=TransactionState.HALTED,
        rollback_result=None,
        halt_diagnostic=diagnostic,
    )
    with pytest.raises(ProtocolError, match="invalid transition"):
        _new_halt_plan(
            bound_snapshot=snapshot,
            diagnostic=diagnostic,
            steps=(invalid_transition,),
        )


@pytest.mark.parametrize(
    "steps",
    [
        lambda transition: [transition],
        lambda transition: (object(),),
    ],
)
def test_halt_plan_factory_refuses_non_exact_step_containers_and_types(steps):
    snapshot = create_snapshot()
    diagnostic = _halt_diagnostic()
    transition = TransitionTransactionState(
        from_state=snapshot.transaction_state,
        to_state=TransactionState.HALTED,
        rollback_result=None,
        halt_diagnostic=diagnostic,
    )
    with pytest.raises(ProtocolError):
        _new_halt_plan(
            bound_snapshot=snapshot,
            diagnostic=diagnostic,
            steps=steps(transition),  # type: ignore[arg-type]
        )


def test_action_factory_refuses_non_exact_disposition():
    with pytest.raises(ProtocolError, match="disposition"):
        _new_action_plan(
            bound_snapshot=create_snapshot(),
            disposition=[],  # type: ignore[arg-type]
            steps=(),
        )


@pytest.mark.parametrize(
    ("variant", "settlement", "message"),
    [
        (None, SettlementKind.RESTORE_PRE, "variant"),
        (EffectVariant.CREATE_FILE_NO_CLOBBER, None, "settlement"),
    ],
)
def test_transform_steps_require_exact_enum_payloads_at_factory_boundaries(
    variant,
    settlement,
    message,
):
    snapshot = create_snapshot()
    plan = _new_action_plan(
        bound_snapshot=snapshot,
        disposition=PlanDisposition.ROLL_BACK,
        steps=(),
    )
    observation = JointObservation((), (), ())
    step = TransformEffectTuple(
        effect_id="e1",
        variant=variant,  # type: ignore[arg-type]
        settlement=settlement,  # type: ignore[arg-type]
        expected_before=observation,
        result_after=observation,
        identity_relations=(),
    )
    with pytest.raises(ProtocolError, match=message):
        _new_action_plan(
            bound_snapshot=snapshot,
            disposition=PlanDisposition.ROLL_BACK,
            steps=(step,),
        )
    with pytest.raises(ProtocolError, match=message):
        _new_authorized_step(plan, 0, step)
