from typing import cast

from atoms.core.compiler import compile_spec
from atoms.core.effects import ReplaceFile
from atoms.core.recovery import (
    OBSERVED_ABSENT,
    CommitDecision,
    DetachActive,
    EffectJournalState,
    EffectVariant,
    EntryIdentity,
    FileBuildRelation,
    JointObservation,
    JournalState,
    ObservedEntry,
    ObservedFile,
    PersistentNode,
    PersistentObservation,
    PreserveExternal,
    RecoveryTopology,
    RemoveScratch,
    RollbackResult,
    ScratchObservation,
    ScratchRole,
    SettlementKind,
    TopologyParent,
    TransactionState,
    TransformEffectTuple,
    TransitionEffectState,
    TransitionTransactionState,
    build_recovery_snapshot,
)
from atoms.core.spec import build_spec
from tests.support import DIGEST, F, G, valid_spec

_DEFAULT = object()


def compiled_create():
    return compile_spec(valid_spec())


def create_topology():
    from atoms.core.recovery import PersistentNode, ProjectRoot, ScratchNode

    project = ProjectRoot()
    live = PersistentNode("a.txt")
    scratch = ScratchNode("e1", ScratchRole.STAGING)
    return RecoveryTopology(
        parents=(
            TopologyParent(node=live, parent=project),
            TopologyParent(node=scratch, parent=project),
        )
    )


def create_snapshot(
    *,
    state=TransactionState.APPLYING,
    journal=JournalState.STARTED,
    live: ObservedEntry = OBSERVED_ABSENT,
    staging: ObservedEntry | None = None,
    relation=_DEFAULT,
    commit_decision=CommitDecision.UNCOMMITTED,
    rollback_result=_DEFAULT,
    halt_diagnostic=None,
    active=True,
):
    staged = ObservedFile(F, EntryIdentity()) if staging is None else staging
    actual_relation: FileBuildRelation | None = (
        FileBuildRelation.EXACT
        if relation is _DEFAULT and journal is JournalState.STARTED and type(staged) is ObservedFile
        else None
        if relation is _DEFAULT
        else cast(FileBuildRelation | None, relation)
    )
    actual_rollback_result: RollbackResult | None = (
        RollbackResult.RESTORED
        if rollback_result is _DEFAULT and state is TransactionState.ROLLED_BACK
        else None
        if rollback_result is _DEFAULT
        else cast(RollbackResult | None, rollback_result)
    )
    return build_recovery_snapshot(
        compiled=compiled_create(),
        topology=create_topology(),
        transaction_state=state,
        commit_decision=commit_decision,
        rollback_result=actual_rollback_result,
        halt_diagnostic=halt_diagnostic,
        active=active,
        journals=(EffectJournalState("e1", journal),),
        persistent_observations=(PersistentObservation("a.txt", live),),
        scratch_observations=(
            ScratchObservation("e1", ScratchRole.STAGING, staged, actual_relation),
        ),
    )


def make_terminal_snapshot():
    return create_snapshot(
        state=TransactionState.ROLLED_BACK,
        journal=JournalState.UNDONE,
    )


def _snapshot_like(
    source,
    *,
    state=None,
    journal=None,
    live=None,
    staging=None,
    relation=_DEFAULT,
    active=None,
):
    source_scratch = source.scratch_observations[0]
    actual_relation = (
        source_scratch.file_build_relation
        if relation is _DEFAULT
        else cast(FileBuildRelation | None, relation)
    )
    return build_recovery_snapshot(
        compiled=source.compiled,
        topology=source.topology,
        transaction_state=source.transaction_state if state is None else state,
        commit_decision=source.commit_decision,
        rollback_result=source.rollback_result,
        halt_diagnostic=source.halt_diagnostic,
        active=source.active if active is None else active,
        journals=(
            EffectJournalState(
                source.journals[0].effect_id,
                source.journals[0].state if journal is None else journal,
            ),
        ),
        persistent_observations=(
            PersistentObservation(
                source.persistent_observations[0].path,
                source.persistent_observations[0].entry if live is None else live,
            ),
        ),
        scratch_observations=(
            ScratchObservation(
                source_scratch.effect_id,
                source_scratch.role,
                source_scratch.entry if staging is None else staging,
                actual_relation,
            ),
        ),
    )


def _joint(snapshot):
    return JointObservation(
        persistent=snapshot.persistent_observations,
        scratch=snapshot.scratch_observations,
        parent_occupancy=(),
    )


def _replace_snapshot():
    spec = build_spec(
        consumer_tag="cnsmr",
        intent_digest=DIGEST,
        initial_surface={"a.txt": F},
        final_surface={"a.txt": G},
        effects=(ReplaceFile("e1", "a.txt", F, G),),
    )
    live = ObservedFile(F, EntryIdentity())
    staging = ObservedFile(G, EntryIdentity())
    return build_recovery_snapshot(
        compiled=compile_spec(spec),
        topology=create_topology(),
        transaction_state=TransactionState.APPLYING,
        commit_decision=CommitDecision.UNCOMMITTED,
        rollback_result=None,
        halt_diagnostic=None,
        active=True,
        journals=(EffectJournalState("e1", JournalState.STARTED),),
        persistent_observations=(PersistentObservation("a.txt", live),),
        scratch_observations=(
            ScratchObservation(
                "e1",
                ScratchRole.STAGING,
                staging,
                FileBuildRelation.EXACT,
            ),
        ),
    )


def make_replace_started_case():
    source = _replace_snapshot()
    return (
        source,
        TransitionEffectState(
            effect_id="e1",
            from_state=JournalState.STARTED,
            to_state=JournalState.UNDO_STARTED,
        ),
    )


def make_replace_transform_case():
    source = _replace_snapshot()
    live_before = source.persistent_observations[0].entry
    staging_before = source.scratch_observations[0].entry
    result_after = JointObservation(
        persistent=(PersistentObservation("a.txt", staging_before),),
        scratch=(
            ScratchObservation(
                "e1",
                ScratchRole.STAGING,
                live_before,
                FileBuildRelation.EXACT,
            ),
        ),
        parent_occupancy=(),
    )
    return (
        source,
        TransformEffectTuple(
            effect_id="e1",
            variant=EffectVariant.REPLACE_FILE,
            settlement=SettlementKind.RESTORE_PRE,
            expected_before=_joint(source),
            result_after=result_after,
            identity_relations=(),
        ),
    )


def make_reducer_step_cases(case):
    if case == "transaction_transition":
        source = create_snapshot()
        step = TransitionTransactionState(
            from_state=TransactionState.APPLYING,
            to_state=TransactionState.ROLLING_BACK,
            rollback_result=None,
            halt_diagnostic=None,
        )
        expected = _snapshot_like(source, state=TransactionState.ROLLING_BACK)
    elif case == "effect_transition":
        source = create_snapshot()
        step = TransitionEffectState(
            effect_id="e1",
            from_state=JournalState.STARTED,
            to_state=JournalState.UNDO_STARTED,
        )
        expected = _snapshot_like(
            source,
            journal=JournalState.UNDO_STARTED,
            relation=None,
        )
    elif case == "transform_tuple":
        source = create_snapshot(journal=JournalState.UNDO_STARTED)
        staging = source.scratch_observations[0].entry
        result = _snapshot_like(
            source,
            live=staging,
            staging=OBSERVED_ABSENT,
            relation=None,
        )
        step = TransformEffectTuple(
            effect_id="e1",
            variant=EffectVariant.CREATE_FILE_NO_CLOBBER,
            settlement=SettlementKind.REPAIR_INTERMEDIATE,
            expected_before=_joint(source),
            result_after=_joint(result),
            identity_relations=(),
        )
        expected = result
    elif case == "remove_scratch":
        source = create_snapshot(journal=JournalState.UNDO_STARTED)
        result = _snapshot_like(
            source,
            staging=OBSERVED_ABSENT,
            relation=None,
        )
        step = RemoveScratch(
            effect_id="e1",
            role=ScratchRole.STAGING,
            expected_before=_joint(source),
            result_after=_joint(result),
        )
        expected = result
    elif case == "preserve_external":
        external = ObservedFile(G, EntryIdentity())
        source = create_snapshot(
            journal=JournalState.PENDING,
            live=external,
            staging=OBSERVED_ABSENT,
        )
        step = PreserveExternal(nodes=(PersistentNode("a.txt"),))
        expected = source
    elif case == "detach_active":
        source = make_terminal_snapshot()
        step = DetachActive()
        expected = _snapshot_like(source, active=False)
    else:
        raise AssertionError(f"unknown reducer step case: {case}")
    return source, step, expected
