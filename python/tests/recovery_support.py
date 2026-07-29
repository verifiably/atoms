from itertools import product
from typing import cast

from atoms.core.compiler import compile_spec
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.errors import ProtocolError
from atoms.core.fingerprint import ABSENT, DirectoryState, FileState, SymlinkState
from atoms.core.recovery import (
    OBSERVED_ABSENT,
    CommitDecision,
    DetachActive,
    EffectJournalState,
    EffectVariant,
    EntryIdentity,
    FileBuildRelation,
    HaltDiagnostic,
    HaltReason,
    JointObservation,
    JournalState,
    ObservedDirectory,
    ObservedEntry,
    ObservedFile,
    ObservedSymlink,
    OperatorAction,
    PersistentNode,
    PersistentObservation,
    PreserveExternal,
    ProjectRoot,
    RecoverySnapshot,
    RecoveryTopology,
    RemoveScratch,
    RollbackResult,
    ScratchNode,
    ScratchObservation,
    ScratchRole,
    SettlementKind,
    TopologyDirectory,
    TopologyParent,
    TransactionState,
    TransformEffectTuple,
    TransitionEffectState,
    TransitionTransactionState,
    WorkRoot,
    apply_recovery_plan,
    build_recovery_snapshot,
    classify_recovery,
)
from atoms.core.recovery.journal import reconstruct_frontiers
from atoms.core.spec import build_spec
from tests.support import DIGEST, D, F, G, L, valid_spec

_DEFAULT = object()
_EXTERNAL = FileState(
    content_hash="sha256:" + "9" * 64,
    mode=0o600,
    byte_len=19,
)
_PREFIX = FileState(
    content_hash="sha256:" + "8" * 64,
    mode=0o644,
    byte_len=2,
)
_EXTERNAL_DIRECTORY = DirectoryState(mode=0o700)
_EXTERNAL_SYMLINK = SymlinkState(target="../external", mode=0o777)


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


def make_three_effect_snapshot(
    state: TransactionState,
    states: tuple[JournalState, JournalState, JournalState],
    *,
    active: bool = True,
    commit_decision: CommitDecision | None = None,
    halt_diagnostic: HaltDiagnostic | None = None,
):
    h = FileState(content_hash="sha256:" + "3" * 64, mode=0o644, byte_len=7)
    i = FileState(content_hash="sha256:" + "4" * 64, mode=0o644, byte_len=11)
    effects = (
        ReplaceFile("e1", "a.txt", F, G),
        ReplaceFile("e2", "a.txt", G, h),
        ReplaceFile("e3", "a.txt", h, i),
    )
    compiled = compile_spec(
        build_spec(
            consumer_tag="cnsmr",
            intent_digest=DIGEST,
            initial_surface={"a.txt": F},
            final_surface={"a.txt": i},
            effects=effects,
            dependencies=(("e1", "e3"),),
        )
    )
    project = ProjectRoot()
    topology = RecoveryTopology(
        parents=(
            TopologyParent(node=PersistentNode("a.txt"), parent=project),
            *(
                TopologyParent(
                    node=ScratchNode(effect.effect_id, ScratchRole.STAGING),
                    parent=project,
                )
                for effect in effects
            ),
        )
    )
    actual_decision = (
        CommitDecision.COMMITTED
        if commit_decision is None and state is TransactionState.COMMITTED
        else CommitDecision.UNCOMMITTED
        if commit_decision is None
        else commit_decision
    )
    return build_recovery_snapshot(
        compiled=compiled,
        topology=topology,
        transaction_state=state,
        commit_decision=actual_decision,
        rollback_result=(
            RollbackResult.RESTORED
            if state is TransactionState.ROLLED_BACK
            else None
        ),
        halt_diagnostic=halt_diagnostic,
        active=active,
        journals=tuple(
            EffectJournalState(effect.effect_id, journal)
            for effect, journal in zip(effects, states, strict=True)
        ),
        persistent_observations=(
            PersistentObservation("a.txt", OBSERVED_ABSENT),
        ),
        scratch_observations=tuple(
            ScratchObservation(
                effect.effect_id,
                ScratchRole.STAGING,
                OBSERVED_ABSENT,
                None,
            )
            for effect in effects
        ),
    )


def make_halted_authority_snapshot(
    states: tuple[JournalState, JournalState, JournalState],
    *,
    active: bool,
    commit_decision: CommitDecision = CommitDecision.UNCOMMITTED,
):
    journals = tuple(
        EffectJournalState(effect_id, journal)
        for effect_id, journal in zip(("e1", "e2", "e3"), states, strict=True)
    )
    diagnostic = HaltDiagnostic(
        pre_halt_state=TransactionState.ROLLING_BACK,
        commit_decision=commit_decision,
        journals=journals,
        projected_transaction_state=TransactionState.ROLLING_BACK,
        projected_journals=journals,
        effect_id=None,
        paths=(),
        expected=(),
        observed=(),
        identity_relations=(),
        reason=HaltReason.EFFECT_TUPLE_UNATTRIBUTABLE,
        operator_action=OperatorAction.INSPECT_PRESERVED_EVIDENCE,
    )
    return make_three_effect_snapshot(
        TransactionState.HALTED,
        states,
        active=active,
        commit_decision=commit_decision,
        halt_diagnostic=diagnostic,
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


def reallocate_joint_identities(observation: JointObservation) -> JointObservation:
    replacements: dict[EntryIdentity, EntryIdentity] = {}

    def fresh_identity(identity: EntryIdentity) -> EntryIdentity:
        replacement = replacements.get(identity)
        if replacement is None:
            replacement = EntryIdentity()
            replacements[identity] = replacement
        return replacement

    def fresh_entry(entry):
        if type(entry) is ObservedFile:
            return ObservedFile(entry.state, fresh_identity(entry.identity))
        if type(entry) is ObservedDirectory:
            return ObservedDirectory(
                entry.state,
                fresh_identity(entry.identity),
                entry.has_unmodeled_child,
            )
        return entry

    return JointObservation(
        persistent=tuple(
            PersistentObservation(item.path, fresh_entry(item.entry))
            for item in observation.persistent
        ),
        scratch=tuple(
            ScratchObservation(
                item.effect_id,
                item.role,
                fresh_entry(item.entry),
                item.file_build_relation,
            )
            for item in observation.scratch
        ),
        parent_occupancy=observation.parent_occupancy,
    )


def make_classifier_plan():
    return classify_recovery(create_snapshot())


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


def _file_effect_topology(effect_id: str):
    project = ProjectRoot()
    return RecoveryTopology(
        parents=(
            TopologyParent(node=PersistentNode("a.txt"), parent=project),
            TopologyParent(
                node=ScratchNode(effect_id, ScratchRole.STAGING),
                parent=project,
            ),
        )
    )


def _file_entry(name: str, *, pre: FileState, post: FileState):
    if name == "foreign":
        return ObservedSymlink(SymlinkState("elsewhere", 0o777))
    states = {
        "pre": pre,
        "post": post,
        "same": pre,
        "prefix": _PREFIX,
        "external": _EXTERNAL,
        "diverged": _EXTERNAL,
    }
    if name == "absent":
        return OBSERVED_ABSENT
    try:
        return ObservedFile(states[name], EntryIdentity())
    except KeyError as exc:
        raise AssertionError(f"unknown file observation name: {name}") from exc


def _file_relation(
    *,
    effect,
    live_name: str,
    staging_name: str,
    journal: JournalState,
):
    if journal is not JournalState.STARTED or staging_name == "absent":
        return None
    if staging_name == "foreign":
        return None
    if type(effect) is ReplaceFile and live_name not in {"pre", "same"}:
        return None
    if staging_name == "prefix":
        return FileBuildRelation.STRICT_PREFIX
    if staging_name == "diverged":
        return FileBuildRelation.DIVERGED
    if staging_name in {"pre", "post", "same"}:
        return FileBuildRelation.EXACT
    return FileBuildRelation.DIVERGED


def _transaction_fields(journal: JournalState, *, committed: bool):
    if committed:
        return (
            TransactionState.COMMITTED,
            CommitDecision.COMMITTED,
            None,
        )
    if journal in {JournalState.DONE, JournalState.UNDO_STARTED}:
        return (
            TransactionState.ROLLING_BACK,
            CommitDecision.UNCOMMITTED,
            None,
        )
    if journal is JournalState.UNDONE:
        return (
            TransactionState.ROLLED_BACK,
            CommitDecision.UNCOMMITTED,
            RollbackResult.RESTORED,
        )
    if journal is JournalState.PENDING:
        return (
            TransactionState.PREPARED,
            CommitDecision.UNCOMMITTED,
            None,
        )
    return (
        TransactionState.APPLYING,
        CommitDecision.UNCOMMITTED,
        None,
    )


def _make_file_effect_case(
    effect,
    live_name: str,
    staging_name: str,
    journal: JournalState,
    *,
    committed: bool,
):
    pre = effect.pre if type(effect) is ReplaceFile else F
    state, decision, rollback_result = _transaction_fields(
        journal,
        committed=committed,
    )
    compiled = compile_spec(
        build_spec(
            consumer_tag="cnsmr",
            intent_digest=DIGEST,
            initial_surface=(
                {"a.txt": effect.pre}
                if type(effect) is ReplaceFile
                else {"a.txt": ABSENT}
            ),
            final_surface={"a.txt": effect.post},
            effects=(effect,),
        )
    )
    snapshot = build_recovery_snapshot(
        compiled=compiled,
        topology=_file_effect_topology(effect.effect_id),
        transaction_state=state,
        commit_decision=decision,
        rollback_result=rollback_result,
        halt_diagnostic=None,
        active=True,
        journals=(EffectJournalState(effect.effect_id, journal),),
        persistent_observations=(
            PersistentObservation(
                "a.txt",
                _file_entry(live_name, pre=pre, post=effect.post),
            ),
        ),
        scratch_observations=(
            ScratchObservation(
                effect.effect_id,
                ScratchRole.STAGING,
                _file_entry(staging_name, pre=pre, post=effect.post),
                _file_relation(
                    effect=effect,
                    live_name=live_name,
                    staging_name=staging_name,
                    journal=journal,
                ),
            ),
        ),
    )
    return snapshot, reconstruct_frontiers(snapshot)


def make_replace_case(
    live_name: str,
    staging_name: str,
    journal: JournalState,
    *,
    committed: bool = False,
):
    return _make_file_effect_case(
        ReplaceFile("e1", "a.txt", F, G),
        live_name,
        staging_name,
        journal,
        committed=committed,
    )


def make_noop_replace_case(
    staging_name: str,
    journal: JournalState,
    *,
    committed: bool = False,
):
    return _make_file_effect_case(
        ReplaceFile("e1", "a.txt", F, F),
        "same",
        staging_name,
        journal,
        committed=committed,
    )


def make_create_file_case(
    live_name: str,
    staging_name: str,
    journal: JournalState,
    *,
    committed: bool = False,
):
    return _make_file_effect_case(
        CreateFileNoClobber("e1", "a.txt", G),
        live_name,
        staging_name,
        journal,
        committed=committed,
    )


def _single_effect_snapshot(
    *,
    effect,
    initial_surface,
    final_surface,
    topology: RecoveryTopology,
    journal: JournalState,
    persistent_observations: tuple[PersistentObservation, ...],
    scratch_observation: ScratchObservation,
    committed: bool,
):
    state, decision, rollback_result = _transaction_fields(
        journal,
        committed=committed,
    )
    snapshot = build_recovery_snapshot(
        compiled=compile_spec(
            build_spec(
                consumer_tag="cnsmr",
                intent_digest=DIGEST,
                initial_surface=initial_surface,
                final_surface=final_surface,
                effects=(effect,),
            )
        ),
        topology=topology,
        transaction_state=state,
        commit_decision=decision,
        rollback_result=rollback_result,
        halt_diagnostic=None,
        active=True,
        journals=(EffectJournalState(effect.effect_id, journal),),
        persistent_observations=persistent_observations,
        scratch_observations=(scratch_observation,),
    )
    return snapshot, reconstruct_frontiers(snapshot)


def make_delete_case(
    live_name: str,
    tombstone_name: str,
    journal: JournalState,
    *,
    committed: bool = False,
    symlink: bool = False,
):
    pre = L if symlink else F
    effect = DeletePath("e1", "a.txt", pre)
    project = ProjectRoot()

    def entry(name: str):
        if name == "absent":
            return OBSERVED_ABSENT
        if name == "pre":
            return (
                ObservedSymlink(L)
                if symlink
                else ObservedFile(F, EntryIdentity())
            )
        if name == "post":
            return ObservedFile(G, EntryIdentity())
        if name == "prefix":
            return ObservedFile(_PREFIX, EntryIdentity())
        if name == "external":
            return (
                ObservedSymlink(_EXTERNAL_SYMLINK)
                if symlink
                else ObservedFile(_EXTERNAL, EntryIdentity())
            )
        if name == "foreign":
            return ObservedSymlink(_EXTERNAL_SYMLINK)
        raise AssertionError(f"unknown delete observation name: {name}")

    topology = RecoveryTopology(
        parents=(
            TopologyParent(PersistentNode("a.txt"), project),
            TopologyParent(
                ScratchNode("e1", ScratchRole.TOMBSTONE),
                project,
            ),
        )
    )
    return _single_effect_snapshot(
        effect=effect,
        initial_surface={"a.txt": pre},
        final_surface={"a.txt": ABSENT},
        topology=topology,
        journal=journal,
        persistent_observations=(
            PersistentObservation("a.txt", entry(live_name)),
        ),
        scratch_observation=ScratchObservation(
            "e1",
            ScratchRole.TOMBSTONE,
            entry(tombstone_name),
            None,
        ),
        committed=committed,
    )


def _move_identities(relation: str | None):
    source = EntryIdentity()
    destination = EntryIdentity()
    anchor = EntryIdentity()
    if relation == "source_anchor_same":
        anchor = source
    elif relation == "destination_anchor_same":
        anchor = destination
    elif relation == "source_destination_same":
        destination = source
    elif relation == "all_same":
        destination = source
        anchor = source
    elif relation not in {None, "different"}:
        raise AssertionError(f"unknown move identity relation: {relation}")
    return source, destination, anchor


def make_move_case(
    source_name: str,
    destination_name: str,
    anchor_name: str,
    relation: str | None,
    journal: JournalState | None = None,
    *,
    committed: bool = False,
):
    actual_journal = (
        JournalState.DONE
        if journal is None and committed
        else JournalState.STARTED
        if journal is None
        else journal
    )
    effect = MoveNoClobber("e1", "source.txt", "destination.txt", F)
    source_id, destination_id, anchor_id = _move_identities(relation)

    def entry(name: str, identity: EntryIdentity):
        if name == "absent":
            return OBSERVED_ABSENT
        if name == "pre":
            return ObservedFile(F, identity)
        if name == "external":
            return ObservedFile(_EXTERNAL, identity)
        if name == "foreign":
            return ObservedSymlink(_EXTERNAL_SYMLINK)
        raise AssertionError(f"unknown move observation name: {name}")

    project = ProjectRoot()
    topology = RecoveryTopology(
        parents=(
            TopologyParent(PersistentNode("source.txt"), project),
            TopologyParent(PersistentNode("destination.txt"), project),
            TopologyParent(ScratchNode("e1", ScratchRole.ANCHOR), project),
        )
    )
    return _single_effect_snapshot(
        effect=effect,
        initial_surface={
            "source.txt": F,
            "destination.txt": ABSENT,
        },
        final_surface={
            "source.txt": ABSENT,
            "destination.txt": F,
        },
        topology=topology,
        journal=actual_journal,
        persistent_observations=(
            PersistentObservation(
                "source.txt",
                entry(source_name, source_id),
            ),
            PersistentObservation(
                "destination.txt",
                entry(destination_name, destination_id),
            ),
        ),
        scratch_observation=ScratchObservation(
            "e1",
            ScratchRole.ANCHOR,
            entry(anchor_name, anchor_id),
            None,
        ),
        committed=committed,
    )


def _directory_identities(relation: str | None):
    live = EntryIdentity()
    work = EntryIdentity()
    if relation == "same":
        work = live
    elif relation not in {None, "different"}:
        raise AssertionError(f"unknown directory identity relation: {relation}")
    return live, work


def make_directory_case(
    live_name: str,
    work_name: str,
    relation: str | None,
    unmodeled: bool,
    journal: JournalState | None = None,
    *,
    committed: bool = False,
):
    actual_journal = (
        JournalState.DONE
        if journal is None and committed
        else JournalState.STARTED
        if journal is None
        else journal
    )
    effect = CreateDirectory("e1", "dir", D)
    live_id, work_id = _directory_identities(relation)

    def entry(name: str, identity: EntryIdentity):
        if name == "absent":
            return OBSERVED_ABSENT
        if name == "post":
            return ObservedDirectory(D, identity, unmodeled)
        if name == "external":
            return ObservedDirectory(
                _EXTERNAL_DIRECTORY,
                identity,
                unmodeled,
            )
        raise AssertionError(f"unknown directory observation name: {name}")

    project = ProjectRoot()
    work_root = WorkRoot()
    topology = RecoveryTopology(
        parents=(
            TopologyParent(work_root, project),
            TopologyParent(PersistentNode("dir"), project),
            TopologyParent(
                ScratchNode("e1", ScratchRole.WORK),
                work_root,
            ),
        )
    )
    return _single_effect_snapshot(
        effect=effect,
        initial_surface={"dir": ABSENT},
        final_surface={"dir": D},
        topology=topology,
        journal=actual_journal,
        persistent_observations=(
            PersistentObservation("dir", entry(live_name, live_id)),
        ),
        scratch_observation=ScratchObservation(
            "e1",
            ScratchRole.WORK,
            entry(work_name, work_id),
            None,
        ),
        committed=committed,
    )


def make_directory_descendant_case(*, descendant_present: bool):
    effects = (
        CreateDirectory("e1", "A", D),
        CreateFileNoClobber("e2", "a/child.txt", F),
    )
    compiled = compile_spec(
        build_spec(
            consumer_tag="cnsmr",
            intent_digest=DIGEST,
            initial_surface={
                "A": ABSENT,
                "a/child.txt": ABSENT,
            },
            final_surface={
                "A": D,
                "a/child.txt": F,
            },
            effects=effects,
            dependencies=(("e1", "e2"),),
        )
    )
    project = ProjectRoot()
    work_root = WorkRoot()
    live = PersistentNode("A")
    topology = RecoveryTopology(
        parents=(
            TopologyParent(work_root, project),
            TopologyParent(live, project),
            TopologyParent(ScratchNode("e1", ScratchRole.WORK), work_root),
            TopologyParent(PersistentNode("a/child.txt"), live),
            TopologyParent(ScratchNode("e2", ScratchRole.STAGING), live),
        )
    )
    snapshot = build_recovery_snapshot(
        compiled=compiled,
        topology=topology,
        transaction_state=TransactionState.ROLLING_BACK,
        commit_decision=CommitDecision.UNCOMMITTED,
        rollback_result=None,
        halt_diagnostic=None,
        active=True,
        journals=(
            EffectJournalState("e1", JournalState.DONE),
            EffectJournalState(
                "e2",
                (
                    JournalState.UNDO_STARTED
                    if descendant_present
                    else JournalState.UNDONE
                ),
            ),
        ),
        persistent_observations=(
            PersistentObservation(
                "A",
                ObservedDirectory(D, EntryIdentity(), False),
            ),
            PersistentObservation(
                "a/child.txt",
                (
                    ObservedFile(F, EntryIdentity())
                    if descendant_present
                    else OBSERVED_ABSENT
                ),
            ),
        ),
        scratch_observations=(
            ScratchObservation(
                "e1",
                ScratchRole.WORK,
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
    )
    return snapshot, reconstruct_frontiers(snapshot)


def make_pending_drift_case():
    return make_create_file_case(
        "external",
        "absent",
        JournalState.PENDING,
    )


def make_pending_clean_case():
    return make_create_file_case(
        "absent",
        "absent",
        JournalState.PENDING,
    )


def make_pending_scratch_case():
    return make_create_file_case(
        "absent",
        "post",
        JournalState.PENDING,
    )


def make_undone_drift_case():
    return make_create_file_case(
        "external",
        "absent",
        JournalState.UNDONE,
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


def make_two_effect_snapshot(*, first: str, second: str):
    outcomes = {"halt", "repairable"}
    if first not in outcomes or second not in outcomes:
        raise AssertionError("two-effect outcomes must be 'halt' or 'repairable'")

    h = FileState(
        content_hash="sha256:" + "3" * 64,
        mode=0o644,
        byte_len=7,
    )
    effects = (
        ReplaceFile("e1", "a.txt", F, G),
        ReplaceFile("e2", "a.txt", G, h),
    )
    compiled = compile_spec(
        build_spec(
            consumer_tag="cnsmr",
            intent_digest=DIGEST,
            initial_surface={"a.txt": F},
            final_surface={"a.txt": h},
            effects=effects,
        )
    )
    project = ProjectRoot()
    topology = RecoveryTopology(
        parents=(
            TopologyParent(PersistentNode("a.txt"), project),
            TopologyParent(
                ScratchNode("e1", ScratchRole.STAGING),
                project,
            ),
            TopologyParent(
                ScratchNode("e2", ScratchRole.STAGING),
                project,
            ),
        )
    )
    live = ObservedFile(h, EntryIdentity())
    first_preimage = ObservedFile(F, EntryIdentity())
    second_preimage = ObservedFile(G, EntryIdentity())
    return build_recovery_snapshot(
        compiled=compiled,
        topology=topology,
        transaction_state=TransactionState.APPLIED,
        commit_decision=CommitDecision.UNCOMMITTED,
        rollback_result=None,
        halt_diagnostic=None,
        active=True,
        journals=(
            EffectJournalState("e1", JournalState.DONE),
            EffectJournalState("e2", JournalState.DONE),
        ),
        persistent_observations=(PersistentObservation("a.txt", live),),
        scratch_observations=(
            ScratchObservation(
                "e1",
                ScratchRole.STAGING,
                (
                    first_preimage
                    if first == "repairable"
                    else OBSERVED_ABSENT
                ),
                None,
            ),
            ScratchObservation(
                "e2",
                ScratchRole.STAGING,
                (
                    second_preimage
                    if second == "repairable"
                    else OBSERVED_ABSENT
                ),
                None,
            ),
        ),
    )


def make_committed_repeated_replace_snapshot():
    source = make_two_effect_snapshot(
        first="repairable",
        second="repairable",
    )
    return build_recovery_snapshot(
        compiled=source.compiled,
        topology=source.topology,
        transaction_state=TransactionState.COMMITTED,
        commit_decision=CommitDecision.COMMITTED,
        rollback_result=None,
        halt_diagnostic=None,
        active=True,
        journals=source.journals,
        persistent_observations=source.persistent_observations,
        scratch_observations=source.scratch_observations,
    )


def make_committed_superseded_cleanup_case(case: str):
    project = ProjectRoot()
    if case == "delete_then_create":
        effects = (
            DeletePath("e1", "a.txt", F),
            CreateFileNoClobber("e2", "a.txt", G),
        )
        initial_surface = {"a.txt": F}
        final_surface = {"a.txt": G}
        topology = RecoveryTopology(
            parents=(
                TopologyParent(PersistentNode("a.txt"), project),
                TopologyParent(
                    ScratchNode("e1", ScratchRole.TOMBSTONE),
                    project,
                ),
                TopologyParent(
                    ScratchNode("e2", ScratchRole.STAGING),
                    project,
                ),
            )
        )
        persistent = (
            PersistentObservation(
                "a.txt",
                ObservedFile(G, EntryIdentity()),
            ),
        )
        scratch = (
            ScratchObservation(
                "e1",
                ScratchRole.TOMBSTONE,
                ObservedFile(F, EntryIdentity()),
                None,
            ),
            ScratchObservation(
                "e2",
                ScratchRole.STAGING,
                OBSERVED_ABSENT,
                None,
            ),
        )
        expected_removals = ("e1",)
    elif case == "move_then_replace":
        effects = (
            MoveNoClobber("e1", "source.txt", "destination.txt", F),
            ReplaceFile("e2", "destination.txt", F, G),
        )
        initial_surface = {
            "source.txt": F,
            "destination.txt": ABSENT,
        }
        final_surface = {
            "source.txt": ABSENT,
            "destination.txt": G,
        }
        topology = RecoveryTopology(
            parents=(
                TopologyParent(PersistentNode("source.txt"), project),
                TopologyParent(PersistentNode("destination.txt"), project),
                TopologyParent(
                    ScratchNode("e1", ScratchRole.ANCHOR),
                    project,
                ),
                TopologyParent(
                    ScratchNode("e2", ScratchRole.STAGING),
                    project,
                ),
            )
        )
        persistent = (
            PersistentObservation("source.txt", OBSERVED_ABSENT),
            PersistentObservation(
                "destination.txt",
                ObservedFile(G, EntryIdentity()),
            ),
        )
        scratch = (
            ScratchObservation(
                "e1",
                ScratchRole.ANCHOR,
                ObservedFile(F, EntryIdentity()),
                None,
            ),
            ScratchObservation(
                "e2",
                ScratchRole.STAGING,
                ObservedFile(F, EntryIdentity()),
                None,
            ),
        )
        expected_removals = ("e1", "e2")
    else:
        raise AssertionError(f"unknown committed cleanup case: {case}")

    snapshot = build_recovery_snapshot(
        compiled=compile_spec(
            build_spec(
                consumer_tag="cnsmr",
                intent_digest=DIGEST,
                initial_surface=initial_surface,
                final_surface=final_surface,
                effects=effects,
            )
        ),
        topology=topology,
        transaction_state=TransactionState.COMMITTED,
        commit_decision=CommitDecision.COMMITTED,
        rollback_result=None,
        halt_diagnostic=None,
        active=True,
        journals=tuple(
            EffectJournalState(effect.effect_id, JournalState.DONE)
            for effect in effects
        ),
        persistent_observations=persistent,
        scratch_observations=scratch,
    )
    return snapshot, expected_removals


def make_committed_snapshot():
    snapshot, _ = make_replace_case(
        "post",
        "pre",
        JournalState.DONE,
        committed=True,
    )
    return snapshot


def make_prepared_drift_snapshot():
    snapshot, _ = make_pending_drift_case()
    return snapshot


def make_halted_snapshot():
    source = create_snapshot()
    diagnostic = HaltDiagnostic(
        pre_halt_state=source.transaction_state,
        commit_decision=source.commit_decision,
        journals=source.journals,
        projected_transaction_state=source.transaction_state,
        projected_journals=source.journals,
        effect_id="e1",
        paths=("a.txt",),
        expected=(),
        observed=(),
        identity_relations=(),
        reason=HaltReason.EFFECT_TUPLE_UNATTRIBUTABLE,
        operator_action=OperatorAction.INSPECT_PRESERVED_EVIDENCE,
    )
    return build_recovery_snapshot(
        compiled=source.compiled,
        topology=source.topology,
        transaction_state=TransactionState.HALTED,
        commit_decision=source.commit_decision,
        rollback_result=None,
        halt_diagnostic=diagnostic,
        active=source.active,
        journals=source.journals,
        persistent_observations=source.persistent_observations,
        scratch_observations=source.scratch_observations,
    )


def make_committed_halt_source():
    snapshot, _ = make_create_file_case(
        "post",
        "post",
        JournalState.DONE,
        committed=True,
    )
    return snapshot


def make_recovery_case(case: str):
    if case == "restored":
        return create_snapshot()
    if case == "refused":
        return make_prepared_drift_snapshot()
    if case == "committed":
        return make_committed_snapshot()
    if case == "halt":
        return make_committed_halt_source()
    raise AssertionError(f"unknown recovery case: {case}")


def make_repeated_path_mid_plan_halt():
    return make_two_effect_snapshot(first="halt", second="repairable")


def make_snapshot_pair_differing_only_dependencies():
    effects = (
        CreateFileNoClobber("e1", "a.txt", F),
        CreateFileNoClobber("e2", "b.txt", G),
    )

    def compiled(dependencies):
        return compile_spec(
            build_spec(
                consumer_tag="cnsmr",
                intent_digest=DIGEST,
                initial_surface={
                    "a.txt": ABSENT,
                    "b.txt": ABSENT,
                },
                final_surface={
                    "a.txt": F,
                    "b.txt": G,
                },
                effects=effects,
                dependencies=dependencies,
            )
        )

    left_compiled = compiled(())
    right_compiled = compiled((("e1", "e2"),))
    project = ProjectRoot()
    topology = RecoveryTopology(
        parents=(
            TopologyParent(PersistentNode("a.txt"), project),
            TopologyParent(PersistentNode("b.txt"), project),
            TopologyParent(
                ScratchNode("e1", ScratchRole.STAGING),
                project,
            ),
            TopologyParent(
                ScratchNode("e2", ScratchRole.STAGING),
                project,
            ),
        )
    )
    journals = (
        EffectJournalState("e1", JournalState.DONE),
        EffectJournalState("e2", JournalState.DONE),
    )
    persistent_observations = (
        PersistentObservation("a.txt", ObservedFile(F, EntryIdentity())),
        PersistentObservation("b.txt", ObservedFile(G, EntryIdentity())),
    )
    scratch_observations = (
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
    )

    def snapshot(compiled_spec):
        return build_recovery_snapshot(
            compiled=compiled_spec,
            topology=topology,
            transaction_state=TransactionState.APPLIED,
            commit_decision=CommitDecision.UNCOMMITTED,
            rollback_result=None,
            halt_diagnostic=None,
            active=True,
            journals=journals,
            persistent_observations=persistent_observations,
            scratch_observations=scratch_observations,
        )

    return snapshot(left_compiled), snapshot(right_compiled)


class _GeneratedSnapshots:
    def try_build(
        self,
        transaction_state: TransactionState,
        commit_decision: CommitDecision,
        active: bool,
        journal_states: tuple[
            JournalState,
            JournalState,
            JournalState,
        ],
    ) -> RecoverySnapshot | None:
        try:
            return make_three_effect_snapshot(
                transaction_state,
                journal_states,
                active=active,
                commit_decision=commit_decision,
            )
        except ProtocolError:
            return None

    def count_three_effect_cases(self) -> tuple[int, int]:
        accepted = 0
        refused = 0
        for (
            transaction_state,
            commit_decision,
            active,
            journal_states,
        ) in product(
            tuple(TransactionState),
            tuple(CommitDecision),
            (False, True),
            product(tuple(JournalState), repeat=3),
        ):
            if (
                self.try_build(
                    transaction_state,
                    commit_decision,
                    active,
                    cast(
                        tuple[
                            JournalState,
                            JournalState,
                            JournalState,
                        ],
                        journal_states,
                    ),
                )
                is None
            ):
                refused += 1
            else:
                accepted += 1
        return accepted, refused

    def valid_case(self) -> RecoverySnapshot:
        return create_snapshot()

    def valid_case_with_identity(self) -> RecoverySnapshot:
        source, _ = make_move_case(
            "absent",
            "pre",
            "pre",
            "destination_anchor_same",
            JournalState.DONE,
        )
        return source

    def deep_topology_case(self) -> RecoverySnapshot:
        compiled = compiled_create()
        project = ProjectRoot()
        directories = tuple(
            TopologyDirectory(index) for index in range(1100)
        )
        persistent = PersistentNode("a.txt")
        scratch = ScratchNode("e1", ScratchRole.STAGING)
        topology = RecoveryTopology(
            parents=(
                TopologyParent(directories[0], project),
                *(
                    TopologyParent(node, parent)
                    for node, parent in zip(
                        directories[1:],
                        directories,
                    )
                ),
                TopologyParent(persistent, directories[-1]),
                TopologyParent(scratch, directories[-1]),
            )
        )
        return build_recovery_snapshot(
            compiled=compiled,
            topology=topology,
            transaction_state=TransactionState.APPLYING,
            commit_decision=CommitDecision.UNCOMMITTED,
            rollback_result=None,
            halt_diagnostic=None,
            active=True,
            journals=(
                EffectJournalState("e1", JournalState.PENDING),
            ),
            persistent_observations=(
                PersistentObservation("a.txt", OBSERVED_ABSENT),
            ),
            scratch_observations=(
                ScratchObservation(
                    "e1",
                    ScratchRole.STAGING,
                    OBSERVED_ABSENT,
                    None,
                ),
            ),
        )


def _reallocate_snapshot_identities(
    source: RecoverySnapshot,
) -> RecoverySnapshot:
    renamed = reallocate_joint_identities(
        JointObservation(
            persistent=source.persistent_observations,
            scratch=source.scratch_observations,
            parent_occupancy=(),
        )
    )
    return build_recovery_snapshot(
        compiled=source.compiled,
        topology=source.topology,
        transaction_state=source.transaction_state,
        commit_decision=source.commit_decision,
        rollback_result=source.rollback_result,
        halt_diagnostic=source.halt_diagnostic,
        active=source.active,
        journals=source.journals,
        persistent_observations=renamed.persistent,
        scratch_observations=renamed.scratch,
    )


def make_generated_snapshots():
    return _GeneratedSnapshots()


def make_identity_case():
    original = make_generated_snapshots().valid_case_with_identity()
    return original, _reallocate_snapshot_identities(original)


def make_halt_restart_case():
    source = make_committed_halt_source()
    before = apply_recovery_plan(source, classify_recovery(source))
    restarted = _reallocate_snapshot_identities(before)
    after_restart = apply_recovery_plan(
        restarted,
        classify_recovery(restarted),
    )
    return before, after_restart
