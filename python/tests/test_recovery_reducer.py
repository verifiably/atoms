from dataclasses import replace

import pytest

from atoms.core.compiler import compile_spec
from atoms.core.effects import CreateDirectory, CreateFileNoClobber
from atoms.core.errors import ProtocolError
from atoms.core.fingerprint import ABSENT
from atoms.core.recovery import (
    OBSERVED_ABSENT,
    CommitDecision,
    DetachActive,
    DiagnosticIdentityRelation,
    EffectJournalState,
    EffectVariant,
    EntryIdentity,
    HaltDiagnostic,
    HaltReason,
    IdentityRelation,
    JointObservation,
    JournalState,
    ObservedDirectory,
    ObservedFile,
    OperatorAction,
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
    WorkRoot,
    apply_recovery_plan,
    build_recovery_snapshot,
    reduce_recovery_plan_prefix,
)
from atoms.core.recovery.plan import _new_action_plan
from atoms.core.recovery.reducer import _apply_steps, _normalize_joint_observation
from atoms.core.spec import build_spec
from tests.recovery_support import create_snapshot, make_move_case
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


def test_transform_may_copy_an_existing_exact_entry_identity():
    source, _ = make_move_case(
        "absent",
        "absent",
        "pre",
        None,
        JournalState.UNDO_STARTED,
    )
    anchor = source.scratch_observations[0].entry
    expected = JointObservation(
        persistent=source.persistent_observations,
        scratch=source.scratch_observations,
        parent_occupancy=(),
    )
    result = JointObservation(
        persistent=(
            PersistentObservation("source.txt", anchor),
            source.persistent_observations[1],
        ),
        scratch=source.scratch_observations,
        parent_occupancy=(),
    )
    step = TransformEffectTuple(
        effect_id="e1",
        variant=EffectVariant.MOVE_NO_CLOBBER,
        settlement=SettlementKind.REPAIR_INTERMEDIATE,
        expected_before=expected,
        result_after=result,
        identity_relations=(
            DiagnosticIdentityRelation(
                "source",
                "anchor",
                IdentityRelation.SAME,
            ),
        ),
    )
    reduced = _apply_steps(source, (step,))
    assert reduced.persistent_observations[0].entry is anchor
    assert reduced.scratch_observations[0].entry is anchor


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


def directory_removal_step(
    *,
    modeled_child=False,
    source_unmodeled=False,
    expected_unmodeled=None,
    omit_occupancy=False,
    add_unrelated_occupancy=False,
    live_kind="directory",
):
    compiled = compile_spec(
        build_spec(
            consumer_tag="cnsmr",
            intent_digest=DIGEST,
            initial_surface={"dir": ABSENT, "dir/child.txt": ABSENT},
            final_surface={"dir": D, "dir/child.txt": F},
            effects=(
                CreateDirectory("e1", "dir", D),
                CreateFileNoClobber("e2", "dir/child.txt", F),
            ),
            dependencies=(("e1", "e2"),),
        )
    )
    project = ProjectRoot()
    work = WorkRoot()
    live_node = PersistentNode("dir")
    child_node = PersistentNode("dir/child.txt")
    work_node = ScratchNode("e1", ScratchRole.WORK)
    child_scratch = ScratchNode("e2", ScratchRole.STAGING)
    topology = RecoveryTopology(
        parents=(
            TopologyParent(work, project),
            TopologyParent(live_node, project),
            TopologyParent(work_node, work),
            TopologyParent(child_node, live_node),
            TopologyParent(child_scratch, live_node),
        )
    )
    directory = ObservedDirectory(D, EntryIdentity(), source_unmodeled)
    live_entry = {
        "directory": directory,
        "absent": OBSERVED_ABSENT,
        "file": ObservedFile(F, EntryIdentity()),
    }[live_kind]
    child_entry = (
        ObservedFile(F, EntryIdentity()) if modeled_child else OBSERVED_ABSENT
    )
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
            EffectJournalState("e2", JournalState.UNDONE),
        ),
        persistent_observations=(
            PersistentObservation("dir", live_entry),
            PersistentObservation("dir/child.txt", child_entry),
        ),
        scratch_observations=(
            ScratchObservation("e1", ScratchRole.WORK, OBSERVED_ABSENT, None),
            ScratchObservation(
                "e2",
                ScratchRole.STAGING,
                OBSERVED_ABSENT,
                None,
            ),
        ),
    )
    unmodeled = (
        source_unmodeled if expected_unmodeled is None else expected_unmodeled
    )
    required_before = ParentOccupancy(
        parent=live_node,
        present_children=(child_node,) if modeled_child else (),
        has_unmodeled_child=unmodeled,
    )
    required_after = ParentOccupancy(
        parent=live_node,
        present_children=(child_node,) if modeled_child else (),
        has_unmodeled_child=unmodeled,
    )
    before_occupancy = () if omit_occupancy else (required_before,)
    after_occupancy = () if omit_occupancy else (required_after,)
    if add_unrelated_occupancy:
        before_occupancy = (
            *before_occupancy,
            ParentOccupancy(
                parent=project,
                present_children=(work, live_node),
                has_unmodeled_child=False,
            ),
        )
        after_occupancy = (
            *after_occupancy,
            ParentOccupancy(
                parent=project,
                present_children=(work,),
                has_unmodeled_child=False,
            ),
        )
    expected = JointObservation(
        persistent=(PersistentObservation("dir", live_entry),),
        scratch=(
            ScratchObservation(
                "e1",
                ScratchRole.WORK,
                OBSERVED_ABSENT,
                None,
            ),
        ),
        parent_occupancy=before_occupancy,
    )
    result = JointObservation(
        persistent=(PersistentObservation("dir", OBSERVED_ABSENT),),
        scratch=(
            ScratchObservation(
                "e1",
                ScratchRole.WORK,
                OBSERVED_ABSENT,
                None,
            ),
        ),
        parent_occupancy=after_occupancy,
    )
    step = TransformEffectTuple(
        effect_id="e1",
        variant=EffectVariant.CREATE_DIRECTORY,
        settlement=SettlementKind.REMOVE_ATTRIBUTABLE_CREATION,
        expected_before=expected,
        result_after=result,
        identity_relations=(),
    )
    return source, step


def test_directory_removal_requires_exact_occupancy_coverage():
    source, step = directory_removal_step(omit_occupancy=True)
    with pytest.raises(ProtocolError, match="occupancy coverage"):
        _apply_steps(source, (step,))


def test_directory_removal_refuses_unrelated_occupancy():
    source, step = directory_removal_step(add_unrelated_occupancy=True)
    with pytest.raises(ProtocolError, match="occupancy coverage"):
        _apply_steps(source, (step,))


def test_directory_removal_refuses_modeled_children():
    source, step = directory_removal_step(modeled_child=True)
    with pytest.raises(ProtocolError, match="directory is not empty"):
        _apply_steps(source, (step,))


def test_directory_removal_refuses_unmodeled_children():
    source, step = directory_removal_step(source_unmodeled=True)
    with pytest.raises(ProtocolError, match="directory is not empty"):
        _apply_steps(source, (step,))


def test_expected_occupancy_unmodeled_fact_comes_from_current_directory():
    source, step = directory_removal_step(
        source_unmodeled=True,
        expected_unmodeled=False,
    )
    with pytest.raises(ProtocolError, match="expected_before"):
        _apply_steps(source, (step,))


@pytest.mark.parametrize("live_kind", ["absent", "file"])
def test_expected_occupancy_requires_current_directory_evidence(live_kind):
    source, step = directory_removal_step(
        live_kind=live_kind,
        omit_occupancy=True,
    )
    with pytest.raises(ProtocolError, match="exact directory"):
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


def test_halt_transition_diagnostic_is_bound_to_its_source_state():
    snapshot = create_snapshot()
    diagnostic = HaltDiagnostic(
        pre_halt_state=TransactionState.PREPARED,
        commit_decision=snapshot.commit_decision,
        journals=snapshot.journals,
        projected_transaction_state=snapshot.transaction_state,
        projected_journals=snapshot.journals,
        effect_id=None,
        paths=(),
        expected=(),
        observed=(),
        identity_relations=(),
        reason=HaltReason.ACTIVE_BINDING_MISSING,
        operator_action=OperatorAction.REPAIR_DURABLE_METADATA,
    )
    step = TransitionTransactionState(
        from_state=TransactionState.APPLYING,
        to_state=TransactionState.HALTED,
        rollback_result=None,
        halt_diagnostic=diagnostic,
    )
    with pytest.raises(ProtocolError, match="pre-halt"):
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
