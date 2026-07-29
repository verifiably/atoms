from typing import cast

from atoms.core.compiler import compile_spec
from atoms.core.recovery import (
    OBSERVED_ABSENT,
    CommitDecision,
    EffectJournalState,
    EntryIdentity,
    FileBuildRelation,
    JournalState,
    ObservedFile,
    PersistentObservation,
    RecoveryTopology,
    RollbackResult,
    ScratchObservation,
    ScratchRole,
    TopologyParent,
    TransactionState,
    build_recovery_snapshot,
)
from tests.support import F, valid_spec

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
    live=OBSERVED_ABSENT,
    staging=None,
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
