from dataclasses import replace
from typing import cast

import pytest

from atoms.core.errors import ProtocolError
from atoms.core.recovery import (
    OBSERVED_ABSENT,
    AuthorizedStep,
    EntryIdentity,
    FileBuildRelation,
    HaltPlan,
    HaltReason,
    IdentityRelation,
    JointObservation,
    JournalState,
    ObservedFile,
    ParentOccupancy,
    PersistentNode,
    PersistentObservation,
    PlanDisposition,
    RecoveryPlan,
    RemoveScratch,
    TransactionState,
    TransformEffectTuple,
    TransitionTransactionState,
    authorize_recovery_step,
    classify_recovery,
    reduce_recovery_plan_prefix,
)
from tests.recovery_support import (
    create_snapshot,
    make_directory_descendant_case,
    reallocate_joint_identities,
)
from tests.support import G


def first_mutating_step(
    plan: RecoveryPlan,
) -> tuple[int, TransformEffectTuple | RemoveScratch]:
    for index, step in enumerate(plan.steps):
        if type(step).__name__ in {"TransformEffectTuple", "RemoveScratch"}:
            return index, cast(
                TransformEffectTuple | RemoveScratch,
                step,
            )
    raise AssertionError("fixture must produce a mutating step")


def changed_persistent_state(
    step: TransformEffectTuple | RemoveScratch,
) -> JointObservation:
    original = step.expected_before.persistent[0]
    return JointObservation(
        persistent=(
            PersistentObservation(
                path=original.path,
                entry=ObservedFile(state=G, identity=EntryIdentity()),
            ),
        ),
        scratch=step.expected_before.scratch,
        parent_occupancy=step.expected_before.parent_occupancy,
    )


def test_fresh_alpha_renamed_observation_authorizes_bound_step():
    plan = classify_recovery(create_snapshot())
    index, step = first_mutating_step(plan)
    fresh = reallocate_joint_identities(step.expected_before)
    assert fresh != step.expected_before

    result = authorize_recovery_step(plan, index, fresh)

    assert type(result) is AuthorizedStep
    assert result.plan is plan
    assert result.step_index == index
    assert result.step is step


def test_changed_precondition_returns_prefix_bound_halt():
    plan = classify_recovery(create_snapshot())
    index, step = first_mutating_step(plan)
    prefix = reduce_recovery_plan_prefix(
        plan.bound_snapshot,
        plan,
        completed_steps=index,
    )
    changed = changed_persistent_state(step)

    result = authorize_recovery_step(
        plan,
        index,
        changed,
    )

    assert type(result) is HaltPlan
    assert result.disposition is PlanDisposition.HALT
    assert result.diagnostic.reason is HaltReason.PLAN_PRECONDITION_CHANGED
    assert result.bound_snapshot != plan.bound_snapshot
    assert result.bound_snapshot.transaction_state is prefix.transaction_state
    assert result.bound_snapshot.journals == prefix.journals
    assert (
        result.bound_snapshot.persistent_observations[0]
        == changed.persistent[0]
    )
    assert len(result.steps) == 1
    assert type(result.steps[0]) is TransitionTransactionState
    assert result.steps[0].from_state is prefix.transaction_state
    assert result.steps[0].to_state is TransactionState.HALTED
    assert not any(
        type(item) in {TransformEffectTuple, RemoveScratch}
        for item in result.steps
    )


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

    assert type(result) is HaltPlan
    assert result.diagnostic.reason is HaltReason.PLAN_PRECONDITION_CHANGED
    assert result.bound_snapshot.scratch_observations[0].file_build_relation is None
    observed_scratch = next(
        item
        for item in result.diagnostic.observed
        if item.slot == "scratch:e1:staging"
    )
    assert observed_scratch.file_build_relation is FileBuildRelation.EXACT


def test_plan_requires_factory_issued_exact_type():
    with pytest.raises(ProtocolError, match="factory-issued RecoveryPlan"):
        authorize_recovery_step(
            cast(RecoveryPlan, object()),
            0,
            JointObservation((), (), ()),
        )


@pytest.mark.parametrize("step_index", [-1, 10**6, True, 1.0])
def test_step_index_requires_bounded_exact_integer(step_index):
    plan = classify_recovery(create_snapshot())
    with pytest.raises(ProtocolError, match="outside the plan step range"):
        authorize_recovery_step(plan, step_index, JointObservation((), (), ()))


def test_observed_requires_exact_joint_observation():
    plan = classify_recovery(create_snapshot())
    index, _ = first_mutating_step(plan)
    with pytest.raises(ProtocolError, match="exact JointObservation"):
        authorize_recovery_step(
            plan,
            index,
            cast(JointObservation, object()),
        )


@pytest.mark.parametrize("missing_field", ["persistent", "scratch"])
def test_ordinary_rollback_authorization_requires_complete_effect_joint_coverage(
    missing_field,
):
    plan = classify_recovery(create_snapshot())
    index, step = first_mutating_step(plan)
    assert step.expected_before.persistent
    assert step.expected_before.scratch
    incomplete = (
        replace(step.expected_before, persistent=())
        if missing_field == "persistent"
        else replace(step.expected_before, scratch=())
    )

    with pytest.raises(ProtocolError, match="exact matching node coverage"):
        authorize_recovery_step(plan, index, incomplete)


def test_authorization_refuses_duplicate_observation_keys():
    plan = classify_recovery(create_snapshot())
    index, step = first_mutating_step(plan)
    duplicate = replace(
        step.expected_before,
        persistent=(
            step.expected_before.persistent[0],
            step.expected_before.persistent[0],
        ),
    )
    with pytest.raises(ProtocolError, match="keys must be unique"):
        authorize_recovery_step(plan, index, duplicate)


def test_authorization_refuses_wrong_repeated_field_type():
    plan = classify_recovery(create_snapshot())
    index, step = first_mutating_step(plan)
    malformed = replace(
        step.expected_before,
        persistent=cast(
            tuple[PersistentObservation, ...],
            list(step.expected_before.persistent),
        ),
    )
    with pytest.raises(ProtocolError, match="fields must be exact tuples"):
        authorize_recovery_step(plan, index, malformed)


def test_named_slot_identity_partition_change_halts(replace_case):
    source, _ = replace_case(
        "pre",
        "post",
        JournalState.STARTED,
    )
    plan = classify_recovery(source)
    index, step = first_mutating_step(plan)
    fresh = reallocate_joint_identities(step.expected_before)
    persistent = fresh.persistent[0].entry
    scratch = fresh.scratch[0]
    assert type(persistent) is ObservedFile
    assert type(scratch.entry) is ObservedFile
    changed = replace(
        fresh,
        scratch=(
            replace(
                scratch,
                entry=replace(
                    scratch.entry,
                    identity=persistent.identity,
                ),
            ),
        ),
    )

    result = authorize_recovery_step(plan, index, changed)

    assert type(result) is HaltPlan
    assert result.diagnostic.reason is HaltReason.PLAN_PRECONDITION_CHANGED
    assert tuple(
        relation.relation
        for relation in result.diagnostic.identity_relations
    ) == (
        IdentityRelation.DIFFERENT,
        IdentityRelation.SAME,
    )


def test_nonempty_parent_occupancy_exact_match_authorizes():
    source, _ = make_directory_descendant_case(descendant_present=False)
    plan = classify_recovery(source)
    index, step = first_mutating_step(plan)
    assert step.expected_before.parent_occupancy == (
        ParentOccupancy(
            parent=PersistentNode("A"),
            present_children=(),
            has_unmodeled_child=False,
        ),
    )
    fresh = reallocate_joint_identities(step.expected_before)

    authorized = authorize_recovery_step(plan, index, fresh)

    assert type(authorized) is AuthorizedStep
    assert authorized.step is step


def test_nonempty_parent_occupancy_mismatch_halts():
    source, _ = make_directory_descendant_case(descendant_present=False)
    plan = classify_recovery(source)
    index, step = first_mutating_step(plan)
    assert step.expected_before.parent_occupancy == (
        ParentOccupancy(
            parent=PersistentNode("A"),
            present_children=(),
            has_unmodeled_child=False,
        ),
    )
    fresh = reallocate_joint_identities(step.expected_before)
    changed = replace(
        fresh,
        parent_occupancy=(
            replace(
                fresh.parent_occupancy[0],
                has_unmodeled_child=True,
            ),
        ),
    )
    halted = authorize_recovery_step(plan, index, changed)

    assert type(halted) is HaltPlan
    assert halted.diagnostic.reason is HaltReason.PLAN_PRECONDITION_CHANGED


def test_later_mutating_authorization_uses_complete_reduced_prefix(
    committed_repeated_replace_snapshot,
):
    source = committed_repeated_replace_snapshot
    plan = classify_recovery(source)
    mutating_steps = [
        (
            index,
            cast(TransformEffectTuple | RemoveScratch, step),
        )
        for index, step in enumerate(plan.steps)
        if type(step) in {TransformEffectTuple, RemoveScratch}
    ]
    assert len(mutating_steps) == 2
    index, step = mutating_steps[1]
    prefix = reduce_recovery_plan_prefix(
        plan.bound_snapshot,
        plan,
        completed_steps=index,
    )
    assert prefix.scratch_observations[0].entry is OBSERVED_ABSENT
    assert (
        prefix.scratch_observations[1].entry
        is source.scratch_observations[1].entry
    )
    fresh = reallocate_joint_identities(step.expected_before)

    authorized = authorize_recovery_step(plan, index, fresh)

    assert type(authorized) is AuthorizedStep
    assert authorized.step is step

    changed = replace(
        fresh,
        scratch=(
            replace(
                fresh.scratch[0],
                entry=OBSERVED_ABSENT,
            ),
        ),
    )
    halted = authorize_recovery_step(plan, index, changed)

    assert type(halted) is HaltPlan
    assert halted.diagnostic.reason is HaltReason.PLAN_PRECONDITION_CHANGED
    assert halted.bound_snapshot.journals == prefix.journals
    assert (
        halted.bound_snapshot.persistent_observations
        == prefix.persistent_observations
    )
    assert tuple(
        item.entry
        for item in halted.bound_snapshot.scratch_observations
    ) == (OBSERVED_ABSENT, OBSERVED_ABSENT)


def test_committed_remove_scratch_authorizes_only_fresh_scratch_evidence(
    committed_snapshot,
):
    plan = classify_recovery(committed_snapshot)
    index, step = first_mutating_step(plan)
    assert type(step) is RemoveScratch
    assert step.expected_before.persistent == ()
    assert len(step.expected_before.scratch) == 1
    assert step.expected_before.parent_occupancy == ()
    fresh = reallocate_joint_identities(step.expected_before)

    result = authorize_recovery_step(plan, index, fresh)

    assert type(result) is AuthorizedStep
    assert result.step is step


def test_committed_remove_scratch_refuses_unrelated_persistent_evidence(
    committed_snapshot,
):
    plan = classify_recovery(committed_snapshot)
    index, step = first_mutating_step(plan)
    assert type(step) is RemoveScratch
    extra = replace(
        reallocate_joint_identities(step.expected_before),
        persistent=(committed_snapshot.persistent_observations[0],),
    )

    with pytest.raises(ProtocolError, match="exact matching node coverage"):
        authorize_recovery_step(plan, index, extra)


def test_mismatch_does_not_reclassify(classifier_plan):
    import ast
    import inspect

    import atoms.core.recovery.authorization as module

    tree = ast.parse(inspect.getsource(module))
    classifier_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "classify_recovery"
    ]
    assert classifier_calls == []
    index, step = first_mutating_step(classifier_plan)

    result = authorize_recovery_step(
        classifier_plan,
        index,
        changed_persistent_state(step),
    )

    assert type(result) is HaltPlan
    assert result.diagnostic.reason is HaltReason.PLAN_PRECONDITION_CHANGED


def test_unexpected_authorization_fault_propagates(monkeypatch):
    import atoms.core.recovery.authorization as module

    class InjectedFault(RuntimeError):
        pass

    def fail(_observation):
        raise InjectedFault("injected")

    monkeypatch.setattr(module, "_authorization_projection", fail)
    plan = classify_recovery(create_snapshot())
    index, step = first_mutating_step(plan)
    with pytest.raises(InjectedFault, match="injected"):
        authorize_recovery_step(plan, index, step.expected_before)
