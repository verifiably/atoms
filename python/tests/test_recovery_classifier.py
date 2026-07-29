from dataclasses import replace
from typing import cast

import pytest

import atoms.core.recovery.classifier as classifier_module
import atoms.core.recovery.diagnostics as diagnostics_module
from atoms.core.errors import ProtocolError
from atoms.core.recovery import (
    OBSERVED_ABSENT,
    ActionPlan,
    CommitDecision,
    DetachActive,
    EffectVariant,
    HaltPlan,
    HaltReason,
    JointObservation,
    JournalState,
    ParentOccupancy,
    PersistentNode,
    PersistentObservation,
    PlanDisposition,
    PreserveExternal,
    ProjectRoot,
    RemoveScratch,
    RollbackResult,
    ScratchRole,
    SettlementKind,
    TransactionState,
    TransformEffectTuple,
    TransitionEffectState,
    TransitionTransactionState,
    apply_recovery_plan,
    classify_recovery,
    reduce_recovery_plan_prefix,
)
from atoms.core.recovery.variants import EffectDecision, EffectDecisionKind
from tests.recovery_support import (
    create_snapshot,
    make_committed_halt_source,
)


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
    assert type(plan) is ActionPlan
    assert plan.disposition is PlanDisposition.ROLL_BACK
    assert isinstance(plan.steps[0], TransitionTransactionState)
    assert plan.steps[0].to_state is TransactionState.ROLLING_BACK
    assert any(isinstance(step, TransitionEffectState) for step in plan.steps)
    assert isinstance(plan.steps[-2], TransitionTransactionState)
    assert plan.steps[-2].rollback_result is RollbackResult.RESTORED
    assert isinstance(plan.steps[-1], DetachActive)


def test_detached_nonterminal_gets_stable_closed_halt():
    plan = classify_recovery(create_snapshot(active=False))
    assert type(plan) is HaltPlan
    assert plan.disposition is PlanDisposition.HALT
    assert plan.diagnostic.reason is HaltReason.ACTIVE_BINDING_MISSING
    assert plan.diagnostic.pre_halt_state is TransactionState.APPLYING


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


def test_committed_repeated_replace_cleanup_uses_final_surface_authority(
    committed_repeated_replace_snapshot,
):
    source = committed_repeated_replace_snapshot
    plan = classify_recovery(source)
    assert type(plan) is ActionPlan
    assert plan.disposition is PlanDisposition.COMMITTED_CLEANUP
    assert tuple(
        step.effect_id for step in plan.steps if type(step) is RemoveScratch
    ) == ("e1", "e2")

    terminal = apply_recovery_plan(source, plan)
    assert terminal.persistent_observations == source.persistent_observations
    assert all(
        scratch.entry is OBSERVED_ABSENT
        for scratch in terminal.scratch_observations
    )
    assert not terminal.active
    assert (
        apply_recovery_plan(terminal, classify_recovery(terminal))
        == terminal
    )


@pytest.mark.parametrize(
    ("case", "expected_removals"),
    [
        ("delete_then_create", ("e1",)),
        ("move_then_replace", ("e1", "e2")),
    ],
)
def test_committed_cleanup_uses_scratch_only_authority_after_surface_proof(
    committed_superseded_cleanup_case,
    case,
    expected_removals,
):
    source, factory_expected = committed_superseded_cleanup_case(case)
    assert factory_expected == expected_removals

    plan = classify_recovery(source)

    assert type(plan) is ActionPlan
    assert plan.disposition is PlanDisposition.COMMITTED_CLEANUP
    assert tuple(
        step.effect_id for step in plan.steps if type(step) is RemoveScratch
    ) == expected_removals
    terminal = apply_recovery_plan(source, plan)
    assert terminal.persistent_observations == source.persistent_observations
    assert all(
        scratch.entry is OBSERVED_ABSENT
        for scratch in terminal.scratch_observations
    )


def test_committed_cleanup_scratch_mismatches_halt(
    replace_case,
    delete_case,
    move_case,
    directory_case,
):
    sources = (
        replace_case(
            "post",
            "external",
            JournalState.DONE,
            committed=True,
        )[0],
        delete_case(
            "absent",
            "external",
            JournalState.DONE,
            committed=True,
        )[0],
        move_case(
            "absent",
            "pre",
            "external",
            None,
            committed=True,
        )[0],
        directory_case(
            "post",
            "post",
            "same",
            False,
            committed=True,
        )[0],
    )

    for source in sources:
        plan = classify_recovery(source)
        assert type(plan) is HaltPlan
        assert plan.diagnostic.reason is HaltReason.EFFECT_TUPLE_UNATTRIBUTABLE
        assert not any(type(step) is RemoveScratch for step in plan.steps)


def test_committed_surface_mismatch_precedes_effect_cleanup(create_file_case):
    source, _ = create_file_case(
        "external",
        "absent",
        JournalState.DONE,
        committed=True,
    )
    plan = classify_recovery(source)
    assert type(plan) is HaltPlan
    assert plan.disposition is PlanDisposition.HALT
    assert plan.diagnostic.reason is HaltReason.COMMITTED_SURFACE_MISMATCH
    assert not any(type(step) in {TransformEffectTuple, RemoveScratch} for step in plan.steps)


def test_prepared_external_drift_is_refused_without_project_mutation(
    prepared_drift_snapshot,
):
    plan = classify_recovery(prepared_drift_snapshot)
    assert type(plan) is ActionPlan
    assert plan.disposition is PlanDisposition.ROLL_BACK_REFUSED
    assert plan.rollback_result is RollbackResult.EXTERNAL_DRIFT_PRESERVED
    assert not any(
        type(step).__name__ in {"TransformEffectTuple", "RemoveScratch"}
        for step in plan.steps
    )


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
    plan = classify_recovery(
        two_effect_snapshot(first="halt", second="repairable")
    )
    assert seen == [1, 0]
    assert plan.disposition is PlanDisposition.HALT
    assert not any(
        type(step).__name__ in {"TransformEffectTuple", "RemoveScratch"}
        for step in plan.steps
    )


def test_classifier_rejects_non_enum_effect_decision_kind(monkeypatch):
    original = classifier_module.classify_effect

    def malformed(snapshot, effect_index, frontiers):
        decision = original(snapshot, effect_index, frontiers)
        return replace(
            decision,
            kind=cast(EffectDecisionKind, "halt"),
        )

    monkeypatch.setattr(classifier_module, "classify_effect", malformed)

    with pytest.raises(ProtocolError, match="decision kind"):
        classify_recovery(create_snapshot())


def test_classifier_rejects_halt_decision_with_steps(monkeypatch):
    original = classifier_module.classify_effect

    def malformed(snapshot, effect_index, frontiers):
        decision = original(snapshot, effect_index, frontiers)
        assert decision.steps
        return replace(
            decision,
            kind=EffectDecisionKind.HALT,
            halt_reason=HaltReason.EFFECT_TUPLE_UNATTRIBUTABLE,
        )

    monkeypatch.setattr(classifier_module, "classify_effect", malformed)

    with pytest.raises(ProtocolError, match="halt decision payload"):
        classify_recovery(create_snapshot())


def test_effect_decision_validator_rejects_closed_step_and_nested_enum_corruption():
    source = create_snapshot()
    evidence = JointObservation(
        persistent=source.persistent_observations,
        scratch=source.scratch_observations,
        parent_occupancy=(),
    )
    remove = RemoveScratch(
        effect_id="e1",
        role=ScratchRole.STAGING,
        expected_before=evidence,
        result_after=evidence,
    )
    transform = TransformEffectTuple(
        effect_id="e1",
        variant=EffectVariant.CREATE_FILE_NO_CLOBBER,
        settlement=SettlementKind.REMOVE_ATTRIBUTABLE_CREATION,
        expected_before=evidence,
        result_after=evidence,
        identity_relations=(),
    )
    base = EffectDecision(
        kind=EffectDecisionKind.NO_ACTION,
        steps=(),
        refused=False,
        halt_reason=None,
        expected=evidence,
        observed=evidence,
    )
    malformed = (
        replace(
            base,
            kind=EffectDecisionKind.REMOVE_SCRATCH,
            steps=(cast(RemoveScratch, object()),),
        ),
        replace(
            base,
            kind=EffectDecisionKind.REMOVE_SCRATCH,
            steps=(
                replace(
                    remove,
                    role=cast(ScratchRole, "staging"),
                ),
            ),
        ),
        replace(
            base,
            kind=EffectDecisionKind.REMOVE_LIVE_CREATION,
            steps=(
                replace(
                    transform,
                    variant=cast(EffectVariant, "create_file_no_clobber"),
                ),
            ),
        ),
        replace(
            base,
            kind=EffectDecisionKind.REMOVE_LIVE_CREATION,
            steps=(
                replace(
                    transform,
                    settlement=cast(
                        SettlementKind,
                        "remove_attributable_creation",
                    ),
                ),
            ),
        ),
    )

    for decision in malformed:
        with pytest.raises(ProtocolError):
            classifier_module._validate_effect_decision(decision)


def test_effect_decision_validator_rejects_kind_payload_inconsistency():
    source = create_snapshot()
    evidence = JointObservation(
        persistent=source.persistent_observations,
        scratch=source.scratch_observations,
        parent_occupancy=(),
    )
    base = EffectDecision(
        kind=EffectDecisionKind.NO_ACTION,
        steps=(),
        refused=False,
        halt_reason=None,
        expected=evidence,
        observed=evidence,
    )
    preserve = PreserveExternal(nodes=(PersistentNode("a.txt"),))
    malformed = (
        replace(
            base,
            kind=EffectDecisionKind.HALT,
        ),
        replace(
            base,
            halt_reason=HaltReason.EFFECT_TUPLE_UNATTRIBUTABLE,
        ),
        replace(
            base,
            refused=True,
        ),
        replace(
            base,
            kind=EffectDecisionKind.PRESERVE_EXTERNAL,
            steps=(preserve,),
        ),
        replace(
            base,
            kind=EffectDecisionKind.REMOVE_LIVE_CREATION,
            steps=(preserve,),
        ),
        replace(
            base,
            refused=cast(bool, 1),
        ),
    )

    for decision in malformed:
        with pytest.raises(ProtocolError):
            classifier_module._validate_effect_decision(decision)


@pytest.mark.parametrize(
    "occupancy",
    [
        pytest.param(cast(ParentOccupancy, object()), id="member"),
        pytest.param(
            ParentOccupancy(
                parent=cast(ProjectRoot, object()),
                present_children=(),
                has_unmodeled_child=False,
            ),
            id="parent",
        ),
        pytest.param(
            ParentOccupancy(
                parent=ProjectRoot(),
                present_children=cast(tuple[ProjectRoot, ...], []),
                has_unmodeled_child=False,
            ),
            id="children-tuple",
        ),
        pytest.param(
            ParentOccupancy(
                parent=ProjectRoot(),
                present_children=(cast(ProjectRoot, object()),),
                has_unmodeled_child=False,
            ),
            id="child",
        ),
        pytest.param(
            ParentOccupancy(
                parent=ProjectRoot(),
                present_children=(),
                has_unmodeled_child=cast(bool, 0),
            ),
            id="boolean",
        ),
    ],
)
def test_diagnostic_rejects_malformed_parent_occupancy(occupancy):
    source = create_snapshot()
    evidence = JointObservation(
        persistent=source.persistent_observations,
        scratch=source.scratch_observations,
        parent_occupancy=(occupancy,),
    )

    with pytest.raises(ProtocolError):
        diagnostics_module._diagnostic(
            source,
            reason=HaltReason.EFFECT_TUPLE_UNATTRIBUTABLE,
            expected=evidence,
            observed=evidence,
        )


def test_mid_plan_halt_labels_projected_journals_without_rewriting_durable_source(
    repeated_path_mid_plan_halt,
):
    source = repeated_path_mid_plan_halt
    plan = classify_recovery(source)
    assert type(plan) is HaltPlan
    diagnostic = plan.diagnostic
    assert diagnostic.journals == source.journals
    assert diagnostic.projected_journals != diagnostic.journals
    assert (
        diagnostic.projected_transaction_state
        is TransactionState.ROLLING_BACK
    )


def test_repeated_path_step_binds_to_intermediate_cursor(two_effect_snapshot):
    source = two_effect_snapshot(first="repairable", second="repairable")
    plan = classify_recovery(source)
    first_effect_transform = next(
        step
        for step in plan.steps
        if type(step) is TransformEffectTuple and step.effect_id == "e1"
    )
    restored_by_second_effect = source.scratch_observations[1].entry
    assert (
        first_effect_transform.expected_before.persistent[0].entry
        is restored_by_second_effect
    )
    assert (
        first_effect_transform.expected_before.persistent[0].entry
        is not source.persistent_observations[0].entry
    )
    terminal = apply_recovery_plan(source, plan)
    assert terminal.transaction_state is TransactionState.ROLLED_BACK
    assert terminal.persistent_observations[0].entry is (
        source.scratch_observations[0].entry
    )


def test_joint_rebinding_reads_values_from_advanced_cursor(
    two_effect_snapshot,
):
    source = two_effect_snapshot(first="repairable", second="repairable")
    plan = classify_recovery(source)
    assert type(plan) is ActionPlan
    first_effect_undo = next(
        index
        for index, step in enumerate(plan.steps)
        if (
            type(step) is TransitionEffectState
            and step.effect_id == "e1"
            and step.to_state is JournalState.UNDO_STARTED
        )
    )
    cursor = reduce_recovery_plan_prefix(
        source,
        plan,
        first_effect_undo + 1,
    )
    stale_source_tuple = JointObservation(
        persistent=(
            PersistentObservation(
                "a.txt",
                source.persistent_observations[0].entry,
            ),
        ),
        scratch=(source.scratch_observations[0],),
        parent_occupancy=(),
    )

    rebound = classifier_module._joint_from_cursor(
        cursor,
        stale_source_tuple,
    )

    assert rebound.persistent[0].entry is source.scratch_observations[1].entry
    assert (
        rebound.persistent[0].entry
        is not stale_source_tuple.persistent[0].entry
    )


def test_post_transition_step_precondition_is_rebased():
    source = create_snapshot()
    plan = classify_recovery(source)
    mutating = cast(
        TransformEffectTuple | RemoveScratch,
        next(
            step
            for step in plan.steps
            if type(step) in {TransformEffectTuple, RemoveScratch}
        ),
    )
    assert mutating.expected_before.scratch[0].file_build_relation is None
    terminal = apply_recovery_plan(source, plan)
    assert terminal.transaction_state is TransactionState.ROLLED_BACK


def test_already_halted_reuses_exact_diagnostic(halted_snapshot):
    plan = classify_recovery(halted_snapshot)
    assert type(plan) is HaltPlan
    assert plan.diagnostic is halted_snapshot.halt_diagnostic
    assert plan.steps == ()


def test_dependencies_do_not_change_plan(
    snapshot_pair_differing_only_dependencies,
):
    left, right = snapshot_pair_differing_only_dependencies
    assert left.persistent_observations is right.persistent_observations
    assert left.scratch_observations is right.scratch_observations
    left_plan = classify_recovery(left)
    right_plan = classify_recovery(right)
    assert left_plan.disposition == right_plan.disposition
    assert left_plan.steps == right_plan.steps


@pytest.mark.parametrize(
    (
        "case",
        "expected_disposition",
        "expected_terminal_state",
        "expected_rollback_result",
    ),
    [
        (
            "restored",
            PlanDisposition.ROLL_BACK,
            TransactionState.ROLLED_BACK,
            RollbackResult.RESTORED,
        ),
        (
            "refused",
            PlanDisposition.ROLL_BACK_REFUSED,
            TransactionState.ROLLED_BACK,
            RollbackResult.EXTERNAL_DRIFT_PRESERVED,
        ),
        (
            "committed",
            PlanDisposition.COMMITTED_CLEANUP,
            TransactionState.COMMITTED,
            None,
        ),
        (
            "halt",
            PlanDisposition.HALT,
            TransactionState.HALTED,
            None,
        ),
    ],
)
def test_classifier_plan_reaches_second_pass_fixed_point(
    recovery_case,
    case,
    expected_disposition,
    expected_terminal_state,
    expected_rollback_result,
):
    source = recovery_case(case)
    first_plan = classify_recovery(source)
    assert first_plan.disposition is expected_disposition

    next_snapshot = apply_recovery_plan(source, first_plan)
    assert next_snapshot.transaction_state is expected_terminal_state
    assert next_snapshot.rollback_result is expected_rollback_result

    fixed = apply_recovery_plan(
        next_snapshot,
        classify_recovery(next_snapshot),
    )
    assert fixed == next_snapshot


def test_committed_halt_retains_decision_and_diagnostic(
    committed_halt_source,
):
    halted = apply_recovery_plan(
        committed_halt_source,
        classify_recovery(committed_halt_source),
    )
    assert halted.transaction_state is TransactionState.HALTED
    assert halted.commit_decision is CommitDecision.COMMITTED
    assert apply_recovery_plan(halted, classify_recovery(halted)) == halted


def test_halt_diagnostic_is_token_free_and_stable_across_token_universes():
    left_plan = classify_recovery(make_committed_halt_source())
    right_plan = classify_recovery(make_committed_halt_source())
    assert type(left_plan) is HaltPlan
    assert type(right_plan) is HaltPlan
    left = left_plan.diagnostic
    right = right_plan.diagnostic
    assert left == right
    assert left.paths == ()
    assert tuple(entry.slot for entry in left.expected) == (
        "scratch:e1:staging",
    )
    assert tuple(entry.slot for entry in left.observed) == (
        "scratch:e1:staging",
    )
    assert left.identity_relations == ()


def test_classifier_refuses_non_snapshot_exact_type():
    with pytest.raises(
        ProtocolError,
        match="factory-issued RecoverySnapshot",
    ):
        classify_recovery(object())  # type: ignore[arg-type]


def test_unexpected_classifier_fault_propagates(monkeypatch):
    class InjectedFault(RuntimeError):
        pass

    def fail(_snapshot):
        raise InjectedFault("injected")

    monkeypatch.setattr(classifier_module, "reconstruct_frontiers", fail)
    with pytest.raises(InjectedFault, match="injected"):
        classify_recovery(create_snapshot())
