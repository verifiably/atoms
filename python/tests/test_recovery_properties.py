from dataclasses import fields, is_dataclass
from itertools import product

import pytest

import atoms.core.recovery.classifier as classifier_module
from atoms.core.errors import ProtocolError
from atoms.core.recovery import (
    ActionPlan,
    CommitDecision,
    DetachActive,
    EntryIdentity,
    HaltPlan,
    JointObservation,
    JournalState,
    NoRecoveryPlan,
    ObservedAbsent,
    ObservedDirectory,
    ObservedFile,
    ObservedSymlink,
    PlanDisposition,
    PreserveExternal,
    RecoverySnapshot,
    RemoveScratch,
    RollbackResult,
    TransactionState,
    TransformEffectTuple,
    TransitionEffectState,
    TransitionTransactionState,
    apply_recovery_plan,
    classify_recovery,
)
from atoms.core.recovery.variants import EffectDecision, classify_effect
from tests.recovery_support import make_directory_descendant_case

_PLAN_TYPES = {ActionPlan, HaltPlan, NoRecoveryPlan}
_FILE_OBSERVATION_CLASSES = ("absent", "pre", "post", "prefix", "foreign")
_RELEVANT_JOURNALS = (
    JournalState.STARTED,
    JournalState.DONE,
    JournalState.UNDO_STARTED,
)
_NON_IDENTITY_STEPS = {
    TransitionTransactionState,
    TransitionEffectState,
    PreserveExternal,
    DetachActive,
}


def plan_projection(plan):
    identity_labels: dict[EntryIdentity, int] = {}

    def project(value):
        if type(value) is EntryIdentity:
            if value not in identity_labels:
                identity_labels[value] = len(identity_labels)
            return ("EntryIdentity", identity_labels[value])
        if is_dataclass(value):
            return (
                type(value).__name__,
                tuple(
                    (field.name, project(getattr(value, field.name)))
                    for field in fields(value)
                ),
            )
        if type(value) is tuple:
            return tuple(project(item) for item in value)
        return value

    return project(plan)


def _entry_identity_tokens(entry) -> set[EntryIdentity]:
    if type(entry) in {ObservedAbsent, ObservedSymlink}:
        return set()
    if type(entry) in {ObservedFile, ObservedDirectory}:
        return {entry.identity}
    raise AssertionError("identity walker encountered an open observation variant")


def _joint_identity_tokens(observation: JointObservation) -> set[EntryIdentity]:
    if type(observation) is not JointObservation:
        raise AssertionError("identity walker requires an exact JointObservation")
    return {
        token
        for item in (*observation.persistent, *observation.scratch)
        for token in _entry_identity_tokens(item.entry)
    }


def snapshot_identity_tokens(snapshot: RecoverySnapshot) -> set[EntryIdentity]:
    if type(snapshot) is not RecoverySnapshot:
        raise AssertionError("identity walker requires an exact RecoverySnapshot")
    return {
        token
        for item in (
            *snapshot.persistent_observations,
            *snapshot.scratch_observations,
        )
        for token in _entry_identity_tokens(item.entry)
    }


def plan_identity_tokens(plan) -> set[EntryIdentity]:
    if type(plan) not in _PLAN_TYPES:
        raise AssertionError("identity walker requires a factory-issued recovery plan")
    tokens = snapshot_identity_tokens(plan.bound_snapshot)
    for step in plan.steps:
        if type(step) in {TransformEffectTuple, RemoveScratch}:
            tokens.update(_joint_identity_tokens(step.expected_before))
            tokens.update(_joint_identity_tokens(step.result_after))
        elif type(step) not in _NON_IDENTITY_STEPS:
            raise AssertionError("identity walker encountered an open recovery step")
    return tokens


def _assert_closed_effect_decision(case) -> None:
    snapshot, frontiers = case
    decision = classify_effect(snapshot, 0, frontiers)
    assert type(decision) is EffectDecision


@pytest.mark.parametrize(
    ("transaction_state", "commit_decision", "active", "journal_states"),
    tuple(
        product(
            tuple(TransactionState),
            tuple(CommitDecision),
            (False, True),
            product(tuple(JournalState), repeat=3),
        )
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
    assert type(plan) in _PLAN_TYPES


def test_state_vector_generator_is_nonvacuous(generated_snapshots):
    accepted, refused = generated_snapshots.count_three_effect_cases()
    assert accepted + refused == 3_500
    assert accepted > 0
    assert refused > 0


def test_replace_create_and_delete_cover_every_joint_observation(
    replace_case,
    create_file_case,
    delete_case,
):
    for live_name, scratch_name, journal in product(
        _FILE_OBSERVATION_CLASSES,
        _FILE_OBSERVATION_CLASSES,
        _RELEVANT_JOURNALS,
    ):
        _assert_closed_effect_decision(
            replace_case(live_name, scratch_name, journal)
        )
        _assert_closed_effect_decision(
            create_file_case(live_name, scratch_name, journal)
        )
        _assert_closed_effect_decision(
            delete_case(live_name, scratch_name, journal)
        )


def test_noop_replace_covers_every_staging_class_and_relevant_journal(
    noop_replace_case,
):
    staging_classes = (
        "absent",
        "same",
        "prefix",
        "diverged",
        "foreign",
    )
    for staging_name, journal in product(
        staging_classes,
        _RELEVANT_JOURNALS,
    ):
        _assert_closed_effect_decision(
            noop_replace_case(staging_name, journal)
        )


def test_move_covers_every_identity_partition_and_presence_tuple(move_case):
    identity_partitions = (
        "different",
        "source_destination_same",
        "source_anchor_same",
        "destination_anchor_same",
        "all_same",
    )
    entry_classes = ("absent", "pre", "external", "foreign")
    for (
        source_name,
        destination_name,
        anchor_name,
        relation,
        journal,
    ) in product(
        entry_classes,
        entry_classes,
        entry_classes,
        identity_partitions,
        _RELEVANT_JOURNALS,
    ):
        _assert_closed_effect_decision(
            move_case(
                source_name,
                destination_name,
                anchor_name,
                relation,
                journal,
            )
        )


def test_directory_covers_identity_occupancy_and_descendant_partitions(
    directory_case,
):
    for live_name, work_name, relation, unmodeled, journal in product(
        ("absent", "post", "external"),
        ("absent", "post", "external"),
        ("different", "same"),
        (False, True),
        _RELEVANT_JOURNALS,
    ):
        _assert_closed_effect_decision(
            directory_case(
                live_name,
                work_name,
                relation,
                unmodeled,
                journal,
            )
        )
    for descendant_present in (False, True):
        _assert_closed_effect_decision(
            make_directory_descendant_case(
                descendant_present=descendant_present
            )
        )


def test_identity_alpha_renaming_preserves_classification(identity_case):
    original, renamed = identity_case
    assert plan_projection(classify_recovery(original)) == plan_projection(
        classify_recovery(renamed)
    )


def test_plan_conserves_source_identity_tokens(generated_snapshots):
    source = generated_snapshots.valid_case_with_identity()
    plan = classify_recovery(source)
    assert plan_identity_tokens(plan) <= snapshot_identity_tokens(source)


def test_halt_diagnostic_round_trips_across_token_universe(
    halt_restart_case,
):
    before, after_restart = halt_restart_case
    assert before.halt_diagnostic == after_restart.halt_diagnostic
    assert (
        before.persistent_observations
        != after_restart.persistent_observations
    )


def test_classification_is_deterministic(generated_snapshots):
    snapshot = generated_snapshots.valid_case()
    assert classify_recovery(snapshot) == classify_recovery(snapshot)


def test_reducer_reaches_second_pass_fixed_point(generated_snapshots):
    source = generated_snapshots.valid_case()
    next_snapshot = apply_recovery_plan(
        source,
        classify_recovery(source),
    )
    fixed = apply_recovery_plan(
        next_snapshot,
        classify_recovery(next_snapshot),
    )
    assert fixed == next_snapshot


@pytest.mark.parametrize(
    "case",
    ("restored", "refused", "committed", "halt"),
)
def test_every_recovery_outcome_reaches_a_second_pass_fixed_point(
    recovery_case,
    case,
):
    source = recovery_case(case)
    next_snapshot = apply_recovery_plan(
        source,
        classify_recovery(source),
    )
    assert (
        apply_recovery_plan(
            next_snapshot,
            classify_recovery(next_snapshot),
        )
        == next_snapshot
    )


def test_stable_halt_and_detached_terminal_are_fixed_points(
    halted_snapshot,
    terminal_snapshot,
):
    halted = apply_recovery_plan(
        halted_snapshot,
        classify_recovery(halted_snapshot),
    )
    assert halted == halted_snapshot

    detached = apply_recovery_plan(
        terminal_snapshot,
        classify_recovery(terminal_snapshot),
    )
    assert not detached.active
    assert (
        apply_recovery_plan(detached, classify_recovery(detached))
        == detached
    )


def test_unexpected_internal_fault_propagates(
    monkeypatch,
    generated_snapshots,
):
    class InjectedFault(RuntimeError):
        pass

    def fail(*args, **kwargs):
        raise InjectedFault("injected")

    monkeypatch.setattr(classifier_module, "reconstruct_frontiers", fail)
    with pytest.raises(InjectedFault, match="injected"):
        classify_recovery(generated_snapshots.valid_case())


def test_classifier_gathers_all_evidence_before_returning_a_halt(
    two_effect_snapshot,
    monkeypatch,
):
    seen: list[int] = []
    original = classifier_module.classify_effect

    def recording(snapshot, effect_index, frontiers):
        seen.append(effect_index)
        return original(snapshot, effect_index, frontiers)

    monkeypatch.setattr(classifier_module, "classify_effect", recording)
    plan = classify_recovery(
        two_effect_snapshot(first="halt", second="repairable")
    )

    assert seen == [1, 0]
    assert type(plan) is HaltPlan
    assert not any(
        type(step) in {TransformEffectTuple, RemoveScratch}
        for step in plan.steps
    )


def test_committed_recovery_never_rolls_back(committed_snapshot):
    plan = classify_recovery(committed_snapshot)
    assert plan.disposition is PlanDisposition.COMMITTED_CLEANUP
    assert all(
        not (
            type(step) is TransitionTransactionState
            and step.to_state is TransactionState.ROLLING_BACK
        )
        for step in plan.steps
    )


def test_committed_repeated_path_cleanup_preserves_the_final_surface(
    committed_repeated_replace_snapshot,
):
    source = committed_repeated_replace_snapshot
    plan = classify_recovery(source)
    terminal = apply_recovery_plan(source, plan)

    assert plan.disposition is PlanDisposition.COMMITTED_CLEANUP
    assert terminal.persistent_observations == source.persistent_observations
    assert all(
        type(step) is not TransformEffectTuple for step in plan.steps
    )


def test_external_drift_is_preserved(prepared_drift_snapshot):
    source = prepared_drift_snapshot
    terminal = apply_recovery_plan(source, classify_recovery(source))

    assert terminal.persistent_observations == source.persistent_observations
    assert (
        terminal.rollback_result
        is RollbackResult.EXTERNAL_DRIFT_PRESERVED
    )


def test_dependencies_are_irrelevant_to_recovery_order(
    snapshot_pair_differing_only_dependencies,
):
    left, right = snapshot_pair_differing_only_dependencies
    assert left.persistent_observations is right.persistent_observations
    assert left.scratch_observations is right.scratch_observations

    left_plan = classify_recovery(left)
    right_plan = classify_recovery(right)
    assert left_plan.disposition == right_plan.disposition
    assert left_plan.steps == right_plan.steps


def test_plan_is_bound_to_its_exact_source(identity_case):
    original, renamed = identity_case
    plan = classify_recovery(original)

    with pytest.raises(
        ProtocolError,
        match="bound source snapshot",
    ):
        apply_recovery_plan(renamed, plan)


def test_scratch_absence_has_variant_specific_semantics(
    replace_case,
    create_file_case,
):
    lost_replace_preimage, _ = replace_case(
        "post",
        "absent",
        JournalState.DONE,
    )
    landed_create, _ = create_file_case(
        "post",
        "absent",
        JournalState.DONE,
    )

    assert type(classify_recovery(lost_replace_preimage)) is HaltPlan
    assert type(classify_recovery(landed_create)) is ActionPlan


def test_1100_component_topology_classifies_without_recursion_failure(
    generated_snapshots,
):
    source = generated_snapshots.deep_topology_case()
    assert type(classify_recovery(source)) in _PLAN_TYPES
