from dataclasses import replace

import pytest

from atoms.core.compiler import compile_spec
from atoms.core.effects import CreateFileNoClobber
from atoms.core.errors import ProtocolError
from atoms.core.fingerprint import ABSENT
from atoms.core.recovery import (
    OBSERVED_ABSENT,
    CommitDecision,
    DetachActive,
    EffectJournalState,
    EffectVariant,
    EntryIdentity,
    JointObservation,
    JournalState,
    ObservedDirectory,
    ObservedFile,
    ParentOccupancy,
    PersistentNode,
    PersistentObservation,
    PlanDisposition,
    PreserveExternal,
    ProjectRoot,
    RecoveryTopology,
    ScratchNode,
    ScratchObservation,
    ScratchRole,
    SettlementKind,
    TopologyParent,
    TransactionState,
    TransformEffectTuple,
    TransitionEffectState,
    TransitionTransactionState,
    apply_recovery_plan,
    build_recovery_snapshot,
    reduce_recovery_plan_prefix,
)
from atoms.core.recovery.plan import _new_action_plan
from atoms.core.recovery.reducer import _apply_steps, _normalize_joint_observation
from atoms.core.spec import build_spec
from tests.recovery_support import create_snapshot
from tests.support import DIGEST, D, F, G


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


def test_joint_normalization_changes_only_an_irrelevant_relation(replace_transform_case):
    source, transform = replace_transform_case()
    normalized = _normalize_joint_observation(source, transform.result_after)
    assert normalized.persistent is transform.result_after.persistent
    assert normalized.scratch[0].entry is transform.result_after.scratch[0].entry
    assert normalized.scratch[0].file_build_relation is None
    assert normalized.parent_occupancy is transform.result_after.parent_occupancy


def test_joint_normalization_refuses_a_wrong_relation_runtime_type():
    source = create_snapshot(journal=JournalState.UNDO_STARTED)
    malformed = JointObservation(
        persistent=source.persistent_observations,
        scratch=(
            replace(
                source.scratch_observations[0],
                file_build_relation=object(),
            ),
        ),
        parent_occupancy=(),
    )
    with pytest.raises(ProtocolError, match="file_build_relation"):
        _normalize_joint_observation(source, malformed)


def test_joint_normalization_refuses_malformed_topology_node_fields():
    source = create_snapshot()
    malformed = JointObservation(
        persistent=source.persistent_observations,
        scratch=source.scratch_observations,
        parent_occupancy=(
            ParentOccupancy(
                parent=PersistentNode(1),  # type: ignore[arg-type]
                present_children=(),
                has_unmodeled_child=False,
            ),
        ),
    )
    with pytest.raises(ProtocolError, match="topology"):
        _normalize_joint_observation(source, malformed)


@pytest.mark.parametrize(
    "parent",
    [PersistentNode("unknown.txt"), PersistentNode("a.txt")],
)
def test_joint_normalization_requires_an_actual_topology_parent(parent):
    source = create_snapshot()
    malformed = JointObservation(
        persistent=source.persistent_observations,
        scratch=source.scratch_observations,
        parent_occupancy=(
            ParentOccupancy(
                parent=parent,
                present_children=(),
                has_unmodeled_child=False,
            ),
        ),
    )
    with pytest.raises(ProtocolError, match="topology parent"):
        _normalize_joint_observation(source, malformed)


def test_transform_refuses_a_fabricated_identity(reducer_step_cases):
    source, step, _ = reducer_step_cases("transform_tuple")
    fabricated = replace(
        step.result_after.persistent[0],
        entry=replace(
            step.result_after.persistent[0].entry,
            identity=EntryIdentity(),
        ),
    )
    invalid = replace(
        step,
        result_after=replace(
            step.result_after,
            persistent=(fabricated,),
        ),
    )
    with pytest.raises(ProtocolError, match="fabricates"):
        _apply_steps(source, (invalid,))


def test_transform_refuses_a_changed_entry_under_an_existing_identity(
    reducer_step_cases,
):
    source, step, _ = reducer_step_cases("transform_tuple")
    transferred = step.result_after.persistent[0].entry
    invalid = replace(
        step,
        result_after=replace(
            step.result_after,
            persistent=(
                PersistentObservation(
                    "a.txt",
                    ObservedFile(G, transferred.identity),
                ),
            ),
        ),
    )
    with pytest.raises(ProtocolError, match="transferring"):
        _apply_steps(source, (invalid,))


def test_transform_reports_kind_reuse_as_protocol_error(reducer_step_cases):
    source, step, _ = reducer_step_cases("transform_tuple")
    transferred = step.result_after.persistent[0].entry
    invalid = replace(
        step,
        result_after=replace(
            step.result_after,
            persistent=(
                PersistentObservation(
                    "a.txt",
                    ObservedDirectory(D, transferred.identity, False),
                ),
            ),
        ),
    )
    with pytest.raises(ProtocolError, match="transferring"):
        _apply_steps(source, (invalid,))


def test_remove_scratch_requires_the_complete_effect_tuple(reducer_step_cases):
    source, step, _ = reducer_step_cases("remove_scratch")
    incomplete = replace(
        step,
        expected_before=replace(step.expected_before, persistent=()),
        result_after=replace(step.result_after, persistent=()),
    )
    with pytest.raises(ProtocolError, match="omits"):
        _apply_steps(source, (incomplete,))


def test_transform_refuses_nodes_owned_by_another_effect():
    compiled = compile_spec(
        build_spec(
            consumer_tag="cnsmr",
            intent_digest=DIGEST,
            initial_surface={"a.txt": ABSENT, "b.txt": ABSENT},
            final_surface={"a.txt": F, "b.txt": G},
            effects=(
                CreateFileNoClobber("e1", "a.txt", F),
                CreateFileNoClobber("e2", "b.txt", G),
            ),
        )
    )
    project = ProjectRoot()
    topology = RecoveryTopology(
        parents=(
            TopologyParent(PersistentNode("a.txt"), project),
            TopologyParent(PersistentNode("b.txt"), project),
            TopologyParent(ScratchNode("e1", ScratchRole.STAGING), project),
            TopologyParent(ScratchNode("e2", ScratchRole.STAGING), project),
        )
    )
    staged_one = ObservedFile(F, EntryIdentity())
    staged_two = ObservedFile(G, EntryIdentity())
    source = build_recovery_snapshot(
        compiled=compiled,
        topology=topology,
        transaction_state=TransactionState.ROLLING_BACK,
        commit_decision=CommitDecision.UNCOMMITTED,
        rollback_result=None,
        halt_diagnostic=None,
        active=True,
        journals=(
            EffectJournalState("e1", JournalState.UNDO_STARTED),
            EffectJournalState("e2", JournalState.UNDO_STARTED),
        ),
        persistent_observations=(
            PersistentObservation("a.txt", OBSERVED_ABSENT),
            PersistentObservation("b.txt", OBSERVED_ABSENT),
        ),
        scratch_observations=(
            ScratchObservation("e1", ScratchRole.STAGING, staged_one, None),
            ScratchObservation("e2", ScratchRole.STAGING, staged_two, None),
        ),
    )
    expected = JointObservation(
        persistent=source.persistent_observations,
        scratch=source.scratch_observations,
        parent_occupancy=(),
    )
    result = JointObservation(
        persistent=(
            PersistentObservation("a.txt", staged_one),
            PersistentObservation("b.txt", OBSERVED_ABSENT),
        ),
        scratch=(
            ScratchObservation(
                "e1",
                ScratchRole.STAGING,
                OBSERVED_ABSENT,
                None,
            ),
            ScratchObservation(
                "e2",
                ScratchRole.STAGING,
                OBSERVED_ABSENT,
                None,
            ),
        ),
        parent_occupancy=(),
    )
    step = TransformEffectTuple(
        effect_id="e1",
        variant=EffectVariant.CREATE_FILE_NO_CLOBBER,
        settlement=SettlementKind.REPAIR_INTERMEDIATE,
        expected_before=expected,
        result_after=result,
        identity_relations=(),
    )
    with pytest.raises(ProtocolError, match="exact effect"):
        _apply_steps(source, (step,))


def test_transform_preserves_directory_unmodeled_child_evidence():
    identity = EntryIdentity()
    source = create_snapshot(
        journal=JournalState.PENDING,
        live=ObservedDirectory(D, identity, False),
        staging=OBSERVED_ABSENT,
    )
    expected = JointObservation(
        persistent=source.persistent_observations,
        scratch=source.scratch_observations,
        parent_occupancy=(),
    )
    result = JointObservation(
        persistent=(
            PersistentObservation(
                "a.txt",
                ObservedDirectory(D, identity, True),
            ),
        ),
        scratch=source.scratch_observations,
        parent_occupancy=(),
    )
    step = TransformEffectTuple(
        effect_id="e1",
        variant=EffectVariant.CREATE_FILE_NO_CLOBBER,
        settlement=SettlementKind.REPAIR_INTERMEDIATE,
        expected_before=expected,
        result_after=result,
        identity_relations=(),
    )
    with pytest.raises(ProtocolError, match="unmodeled"):
        _apply_steps(source, (step,))


def occupancy_transform(result_children, *, has_unmodeled_child=False):
    source = create_snapshot(journal=JournalState.UNDO_STARTED)
    expected = JointObservation(
        persistent=source.persistent_observations,
        scratch=source.scratch_observations,
        parent_occupancy=(
            ParentOccupancy(
                parent=ProjectRoot(),
                present_children=(ScratchNode("e1", ScratchRole.STAGING),),
                has_unmodeled_child=False,
            ),
        ),
    )
    result = JointObservation(
        persistent=(
            PersistentObservation(
                "a.txt",
                source.scratch_observations[0].entry,
            ),
        ),
        scratch=(
            ScratchObservation(
                "e1",
                ScratchRole.STAGING,
                OBSERVED_ABSENT,
                None,
            ),
        ),
        parent_occupancy=(
            ParentOccupancy(
                parent=ProjectRoot(),
                present_children=result_children,
                has_unmodeled_child=has_unmodeled_child,
            ),
        ),
    )
    step = TransformEffectTuple(
        effect_id="e1",
        variant=EffectVariant.CREATE_FILE_NO_CLOBBER,
        settlement=SettlementKind.REPAIR_INTERMEDIATE,
        expected_before=expected,
        result_after=result,
        identity_relations=(),
    )
    return source, step


def test_transform_recomputes_modeled_occupancy_but_preserves_unmodeled_fact():
    source, step = occupancy_transform(
        (PersistentNode("a.txt"),),
        has_unmodeled_child=True,
    )
    with pytest.raises(ProtocolError, match="unmodeled"):
        _apply_steps(source, (step,))


def test_transform_accepts_recomputed_modeled_occupancy():
    source, step = occupancy_transform((PersistentNode("a.txt"),))
    reduced = _apply_steps(source, (step,))
    assert reduced.persistent_observations[0].entry is source.scratch_observations[0].entry
    assert reduced.scratch_observations[0].entry is OBSERVED_ABSENT


def test_transform_refuses_stale_modeled_occupancy():
    source, step = occupancy_transform(
        (ScratchNode("e1", ScratchRole.STAGING),),
    )
    with pytest.raises(ProtocolError, match="stale parent occupancy"):
        _apply_steps(source, (step,))


def test_preserve_external_refuses_unknown_nodes():
    snapshot = create_snapshot()
    with pytest.raises(ProtocolError, match="unknown topology"):
        _apply_steps(
            snapshot,
            (PreserveExternal(nodes=(PersistentNode("unknown.txt"),)),),
        )


def test_detach_refuses_an_already_detached_binding(terminal_snapshot):
    detached = _apply_steps(terminal_snapshot, (DetachActive(),))
    with pytest.raises(ProtocolError, match="already detached"):
        _apply_steps(detached, (DetachActive(),))


def test_transaction_transition_refuses_an_illegal_recovery_edge():
    snapshot = create_snapshot()
    step = TransitionTransactionState(
        from_state=TransactionState.APPLYING,
        to_state=TransactionState.PREPARED,
        rollback_result=None,
        halt_diagnostic=None,
    )
    with pytest.raises(ProtocolError, match="edge"):
        _apply_steps(snapshot, (step,))


def test_effect_transition_refuses_an_illegal_recovery_edge():
    snapshot = create_snapshot()
    step = TransitionEffectState(
        effect_id="e1",
        from_state=JournalState.STARTED,
        to_state=JournalState.PENDING,
    )
    with pytest.raises(ProtocolError, match="edge"):
        _apply_steps(snapshot, (step,))


def test_detach_refuses_a_nonterminal_transaction():
    with pytest.raises(ProtocolError, match="terminal"):
        _apply_steps(create_snapshot(), (DetachActive(),))
