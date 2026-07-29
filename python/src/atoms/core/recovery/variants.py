from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import cast

from atoms.core.compiler import CompiledSpec
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    Effect,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.errors import ProtocolError
from atoms.core.fingerprint import (
    ABSENT,
    AbsentState,
    DirectoryState,
    FileState,
    PathState,
    SymlinkState,
)
from atoms.core.recovery.journal import (
    FrontierDirection,
    PathFrontier,
)
from atoms.core.recovery.model import (
    OBSERVED_ABSENT,
    CommitDecision,
    FileBuildRelation,
    HaltReason,
    JournalState,
    ObservedAbsent,
    ObservedDirectory,
    ObservedEntry,
    ObservedFile,
    ObservedSymlink,
    PersistentObservation,
    ScratchObservation,
)
from atoms.core.recovery.plan import (
    EffectVariant,
    JointObservation,
    PreserveExternal,
    RecoveryStep,
    RemoveScratch,
    SettlementKind,
    TransformEffectTuple,
)
from atoms.core.recovery.snapshot import (
    PersistentNode,
    RecoverySnapshot,
    TopologyNode,
    persistent_map,
    required_scratch_role,
    scratch_map,
)


class EntryClass(Enum):
    ABSENT = "absent"
    PRE = "pre"
    POST = "post"
    PREFIX = "prefix"
    EXTERNAL = "external"


class EffectDecisionKind(Enum):
    NO_ACTION = "no_action"
    PRESERVE_EXTERNAL = "preserve_external"
    UNDO_WITHOUT_MUTATION = "undo_without_mutation"
    REMOVE_SCRATCH = "remove_scratch"
    EXCHANGE_BACK = "exchange_back"
    REFUSED_EXCHANGE_BACK = "refused_exchange_back"
    ALREADY_UNDONE = "already_undone"
    REMOVE_LIVE_CREATION = "remove_live_creation"
    REFUSED_PRESERVE_LIVE = "refused_preserve_live"
    RESTORE_TOMBSTONE = "restore_tombstone"
    REMOVE_ANCHOR = "remove_anchor"
    RESTORE_SOURCE = "restore_source"
    REMOVE_DESTINATION = "remove_destination"
    RESTORE_FROM_ANCHOR = "restore_from_anchor"
    REFUSED_PRESERVE_DESTINATION = "refused_preserve_destination"
    REMOVE_WORK = "remove_work"
    REMOVE_LIVE_DIRECTORY = "remove_live_directory"
    REMOVE_DUAL_NAME_DIRECTORY = "remove_dual_name_directory"
    HALT = "halt"


@dataclass(frozen=True, slots=True)
class EffectDecision:
    kind: EffectDecisionKind
    steps: tuple[RecoveryStep, ...]
    refused: bool
    halt_reason: HaltReason | None
    expected: JointObservation
    observed: JointObservation


_EFFECT_VARIANT = {
    ReplaceFile: EffectVariant.REPLACE_FILE,
    CreateFileNoClobber: EffectVariant.CREATE_FILE_NO_CLOBBER,
    DeletePath: EffectVariant.DELETE_PATH,
    MoveNoClobber: EffectVariant.MOVE_NO_CLOBBER,
    CreateDirectory: EffectVariant.CREATE_DIRECTORY,
}


def _decision(
    kind: EffectDecisionKind,
    observed: JointObservation,
    *,
    steps: tuple[RecoveryStep, ...] = (),
    refused: bool = False,
) -> EffectDecision:
    return EffectDecision(
        kind=kind,
        steps=steps,
        refused=refused,
        halt_reason=None,
        expected=observed,
        observed=observed,
    )


def _halt(
    expected: JointObservation,
    observed: JointObservation,
) -> EffectDecision:
    return EffectDecision(
        kind=EffectDecisionKind.HALT,
        steps=(),
        refused=False,
        halt_reason=HaltReason.EFFECT_TUPLE_UNATTRIBUTABLE,
        expected=expected,
        observed=observed,
    )


def _entry_state(entry: ObservedEntry) -> PathState:
    entry_type = type(entry)
    if entry_type is ObservedAbsent:
        return ABSENT
    if entry_type is ObservedFile:
        return cast(ObservedFile, entry).state
    if entry_type is ObservedSymlink:
        return cast(ObservedSymlink, entry).state
    if entry_type is ObservedDirectory:
        return cast(ObservedDirectory, entry).state
    raise ProtocolError("observation entry has the wrong exact runtime type")


def _classify_entry(
    entry: ObservedEntry,
    *,
    pre: PathState,
    post: PathState,
    build_relation: FileBuildRelation | None,
) -> EntryClass:
    if build_relation is not None and type(build_relation) is not FileBuildRelation:
        raise ProtocolError("file build relation has the wrong exact runtime type")
    if type(entry) is ObservedAbsent:
        return EntryClass.ABSENT
    state = _entry_state(entry)
    if state == pre:
        return EntryClass.PRE
    if state == post:
        return EntryClass.POST
    if build_relation is FileBuildRelation.STRICT_PREFIX:
        return EntryClass.PREFIX
    return EntryClass.EXTERNAL


def _effect_paths(effect: Effect) -> tuple[str, ...]:
    effect_type = type(effect)
    if effect_type in {
        ReplaceFile,
        CreateFileNoClobber,
        DeletePath,
        CreateDirectory,
    }:
        return (cast(ReplaceFile | CreateFileNoClobber | DeletePath | CreateDirectory, effect).path,)
    if effect_type is MoveNoClobber:
        move = cast(MoveNoClobber, effect)
        return (move.source, move.destination)
    raise ProtocolError("effect variant is outside A3's closed set")


def _joint(snapshot: RecoverySnapshot, effect: Effect) -> JointObservation:
    persistent = persistent_map(snapshot)
    role = required_scratch_role(effect)
    scratch = scratch_map(snapshot)
    try:
        persistent_observations = tuple(
            PersistentObservation(path, persistent[path])
            for path in _effect_paths(effect)
        )
        scratch_observation = scratch[(effect.effect_id, role)]
    except KeyError as exc:
        raise ProtocolError("snapshot is missing effect observation coverage") from exc
    return JointObservation(
        persistent=persistent_observations,
        scratch=(scratch_observation,),
        parent_occupancy=(),
    )


def _replace_persistent(
    observed: JointObservation,
    entries: tuple[ObservedEntry, ...],
) -> tuple[PersistentObservation, ...]:
    if len(entries) != len(observed.persistent):
        raise ProtocolError("result persistent tuple has the wrong arity")
    return tuple(
        PersistentObservation(item.path, entry)
        for item, entry in zip(observed.persistent, entries, strict=True)
    )


def _replace_scratch(
    observed: JointObservation,
    entry: ObservedEntry,
) -> tuple[ScratchObservation, ...]:
    if len(observed.scratch) != 1:
        raise ProtocolError("effect joint observation has the wrong scratch arity")
    item = observed.scratch[0]
    return (
        ScratchObservation(
            effect_id=item.effect_id,
            role=item.role,
            entry=entry,
            file_build_relation=None,
        ),
    )


def _transform(
    effect: Effect,
    observed: JointObservation,
    *,
    persistent: tuple[ObservedEntry, ...],
    scratch: ObservedEntry,
    settlement: SettlementKind,
) -> TransformEffectTuple:
    try:
        variant = _EFFECT_VARIANT[type(effect)]
    except KeyError as exc:
        raise ProtocolError("effect variant is outside A3's closed set") from exc
    result_after = JointObservation(
        persistent=_replace_persistent(observed, persistent),
        scratch=_replace_scratch(observed, scratch),
        parent_occupancy=observed.parent_occupancy,
    )
    return TransformEffectTuple(
        effect_id=effect.effect_id,
        variant=variant,
        settlement=settlement,
        expected_before=observed,
        result_after=result_after,
        identity_relations=(),
    )


def _remove_scratch(
    effect: Effect,
    observed: JointObservation,
) -> RemoveScratch:
    if len(observed.scratch) != 1:
        raise ProtocolError("effect joint observation has the wrong scratch arity")
    role = required_scratch_role(effect)
    result_after = JointObservation(
        persistent=observed.persistent,
        scratch=_replace_scratch(observed, OBSERVED_ABSENT),
        parent_occupancy=observed.parent_occupancy,
    )
    return RemoveScratch(
        effect_id=effect.effect_id,
        role=role,
        expected_before=observed,
        result_after=result_after,
    )


def _preserve(*nodes: TopologyNode) -> PreserveExternal:
    return PreserveExternal(nodes=tuple(nodes))


def _remove_after_transform(
    effect: Effect,
    transform: TransformEffectTuple,
) -> RemoveScratch:
    return _remove_scratch(effect, transform.result_after)


def _classify_noop_replace(
    snapshot: RecoverySnapshot,
    effect: ReplaceFile,
    observed: JointObservation,
) -> EffectDecision:
    live = observed.persistent[0].entry
    staging_observation = observed.scratch[0]
    staging = staging_observation.entry
    journal = _journal(snapshot, effect.effect_id)
    if (
        type(live) is not ObservedFile
        or cast(ObservedFile, live).state != effect.pre
    ):
        return _halt(observed, observed)

    staging_absent = type(staging) is ObservedAbsent
    staging_same = (
        type(staging) is ObservedFile
        and cast(ObservedFile, staging).state == effect.pre
    )
    staging_prefix = (
        staging_observation.file_build_relation
        is FileBuildRelation.STRICT_PREFIX
    )

    if snapshot.commit_decision is CommitDecision.COMMITTED:
        if journal is JournalState.DONE and staging_same:
            remove = _remove_scratch(effect, observed)
            return _decision(
                EffectDecisionKind.REMOVE_SCRATCH,
                observed,
                steps=(remove,),
            )
        if journal is JournalState.DONE and staging_absent:
            return _decision(EffectDecisionKind.NO_ACTION, observed)
        return _halt(observed, observed)

    if journal is JournalState.STARTED:
        if staging_absent:
            return _decision(
                EffectDecisionKind.UNDO_WITHOUT_MUTATION,
                observed,
            )
        if staging_prefix or staging_same:
            remove = _remove_scratch(effect, observed)
            return _decision(
                EffectDecisionKind.REMOVE_SCRATCH,
                observed,
                steps=(remove,),
            )
    elif journal is JournalState.DONE:
        if staging_same:
            remove = _remove_scratch(effect, observed)
            return _decision(
                EffectDecisionKind.REMOVE_SCRATCH,
                observed,
                steps=(remove,),
            )
    elif journal is JournalState.UNDO_STARTED:
        if staging_same:
            remove = _remove_scratch(effect, observed)
            return _decision(
                EffectDecisionKind.REMOVE_SCRATCH,
                observed,
                steps=(remove,),
            )
        if staging_absent:
            return _decision(EffectDecisionKind.ALREADY_UNDONE, observed)
    return _halt(observed, observed)


def _classify_replace(
    snapshot: RecoverySnapshot,
    effect: Effect,
    frontiers: tuple[PathFrontier, ...],
) -> EffectDecision:
    del frontiers
    if type(effect) is not ReplaceFile:
        raise ProtocolError("replace classifier received the wrong effect variant")
    replace = cast(ReplaceFile, effect)
    observed = _joint(snapshot, replace)
    if replace.pre == replace.post:
        return _classify_noop_replace(snapshot, replace, observed)

    live = observed.persistent[0].entry
    staging_observation = observed.scratch[0]
    staging = staging_observation.entry
    live_class = _classify_entry(
        live,
        pre=replace.pre,
        post=replace.post,
        build_relation=None,
    )
    staging_class = _classify_entry(
        staging,
        pre=replace.pre,
        post=replace.post,
        build_relation=staging_observation.file_build_relation,
    )
    journal = _journal(snapshot, replace.effect_id)

    if snapshot.commit_decision is CommitDecision.COMMITTED:
        if (
            journal is JournalState.DONE
            and live_class is EntryClass.POST
            and staging_class is EntryClass.PRE
        ):
            remove = _remove_scratch(replace, observed)
            return _decision(
                EffectDecisionKind.REMOVE_SCRATCH,
                observed,
                steps=(remove,),
            )
        if (
            journal is JournalState.DONE
            and live_class is EntryClass.POST
            and staging_class is EntryClass.ABSENT
        ):
            return _decision(EffectDecisionKind.NO_ACTION, observed)
        return _halt(observed, observed)

    if journal is JournalState.STARTED:
        if (
            live_class is EntryClass.PRE
            and staging_class is EntryClass.ABSENT
        ):
            return _decision(
                EffectDecisionKind.UNDO_WITHOUT_MUTATION,
                observed,
            )
        if (
            live_class is EntryClass.PRE
            and staging_class in {EntryClass.PREFIX, EntryClass.POST}
        ):
            remove = _remove_scratch(replace, observed)
            return _decision(
                EffectDecisionKind.REMOVE_SCRATCH,
                observed,
                steps=(remove,),
            )
        if (
            live_class is EntryClass.POST
            and staging_class is EntryClass.PRE
        ):
            return _replace_exchange(replace, observed, refused=False)
        if (
            live_class is EntryClass.POST
            and staging_class is EntryClass.EXTERNAL
        ):
            return _replace_exchange(replace, observed, refused=True)
    elif journal is JournalState.DONE:
        if (
            live_class is EntryClass.POST
            and staging_class is EntryClass.PRE
        ):
            return _replace_exchange(replace, observed, refused=False)
    elif journal is JournalState.UNDO_STARTED:
        if (
            live_class is EntryClass.POST
            and staging_class is EntryClass.PRE
        ):
            return _replace_exchange(replace, observed, refused=False)
        if (
            live_class is EntryClass.PRE
            and staging_class is EntryClass.POST
        ):
            remove = _remove_scratch(replace, observed)
            return _decision(
                EffectDecisionKind.REMOVE_SCRATCH,
                observed,
                steps=(remove,),
            )
        if (
            live_class is EntryClass.PRE
            and staging_class is EntryClass.ABSENT
        ):
            return _decision(EffectDecisionKind.ALREADY_UNDONE, observed)
    return _halt(observed, observed)


def _replace_exchange(
    effect: ReplaceFile,
    observed: JointObservation,
    *,
    refused: bool,
) -> EffectDecision:
    live = observed.persistent[0].entry
    staging = observed.scratch[0].entry
    transform = _transform(
        effect,
        observed,
        persistent=(staging,),
        scratch=live,
        settlement=SettlementKind.RESTORE_PRE,
    )
    remove = _remove_after_transform(effect, transform)
    if refused:
        preserve = _preserve(PersistentNode(effect.path))
        return _decision(
            EffectDecisionKind.REFUSED_EXCHANGE_BACK,
            observed,
            steps=(transform, preserve, remove),
            refused=True,
        )
    return _decision(
        EffectDecisionKind.EXCHANGE_BACK,
        observed,
        steps=(transform, remove),
    )


def _classify_create_file(
    snapshot: RecoverySnapshot,
    effect: Effect,
    frontiers: tuple[PathFrontier, ...],
) -> EffectDecision:
    del frontiers
    if type(effect) is not CreateFileNoClobber:
        raise ProtocolError("create-file classifier received the wrong effect variant")
    create = cast(CreateFileNoClobber, effect)
    observed = _joint(snapshot, create)
    live = observed.persistent[0].entry
    staging_observation = observed.scratch[0]
    staging = staging_observation.entry
    live_class = _classify_entry(
        live,
        pre=ABSENT,
        post=create.post,
        build_relation=None,
    )
    staging_class = _classify_entry(
        staging,
        pre=ABSENT,
        post=create.post,
        build_relation=staging_observation.file_build_relation,
    )
    journal = _journal(snapshot, create.effect_id)

    if snapshot.commit_decision is CommitDecision.COMMITTED:
        if (
            journal is JournalState.DONE
            and live_class is EntryClass.POST
            and staging_class is EntryClass.ABSENT
        ):
            return _decision(EffectDecisionKind.NO_ACTION, observed)
        return _halt(observed, observed)

    if journal is JournalState.STARTED:
        if (
            live_class is EntryClass.ABSENT
            and staging_class is EntryClass.ABSENT
        ):
            return _decision(
                EffectDecisionKind.UNDO_WITHOUT_MUTATION,
                observed,
            )
        if (
            live_class is EntryClass.ABSENT
            and staging_class in {EntryClass.PREFIX, EntryClass.POST}
        ):
            remove = _remove_scratch(create, observed)
            return _decision(
                EffectDecisionKind.REMOVE_SCRATCH,
                observed,
                steps=(remove,),
            )
        if (
            live_class is EntryClass.POST
            and staging_class is EntryClass.ABSENT
        ):
            return _remove_live_creation(create, observed)
        if (
            live_class is EntryClass.POST
            and staging_class in {EntryClass.PREFIX, EntryClass.POST}
        ):
            return _refuse_create_live(
                create,
                observed,
                remove_staging=True,
            )
        if (
            live_class is EntryClass.EXTERNAL
            and staging_class
            in {EntryClass.ABSENT, EntryClass.PREFIX, EntryClass.POST}
        ):
            return _refuse_create_live(
                create,
                observed,
                remove_staging=staging_class is not EntryClass.ABSENT,
            )
    elif journal is JournalState.DONE:
        if (
            live_class is EntryClass.POST
            and staging_class is EntryClass.ABSENT
        ):
            return _remove_live_creation(create, observed)
    elif journal is JournalState.UNDO_STARTED:
        if (
            live_class is EntryClass.POST
            and staging_class is EntryClass.ABSENT
        ):
            return _remove_live_creation(create, observed)
        if (
            live_class is EntryClass.ABSENT
            and staging_class is EntryClass.POST
        ):
            remove = _remove_scratch(create, observed)
            return _decision(
                EffectDecisionKind.REMOVE_SCRATCH,
                observed,
                steps=(remove,),
            )
        if (
            live_class is EntryClass.ABSENT
            and staging_class is EntryClass.ABSENT
        ):
            return _decision(EffectDecisionKind.ALREADY_UNDONE, observed)
    return _halt(observed, observed)


def _remove_live_creation(
    effect: CreateFileNoClobber,
    observed: JointObservation,
) -> EffectDecision:
    live = observed.persistent[0].entry
    transform = _transform(
        effect,
        observed,
        persistent=(OBSERVED_ABSENT,),
        scratch=live,
        settlement=SettlementKind.REMOVE_ATTRIBUTABLE_CREATION,
    )
    remove = _remove_after_transform(effect, transform)
    return _decision(
        EffectDecisionKind.REMOVE_LIVE_CREATION,
        observed,
        steps=(transform, remove),
    )


def _refuse_create_live(
    effect: CreateFileNoClobber,
    observed: JointObservation,
    *,
    remove_staging: bool,
) -> EffectDecision:
    steps: tuple[RecoveryStep, ...] = (
        _preserve(PersistentNode(effect.path)),
    )
    if remove_staging:
        steps = (*steps, _remove_scratch(effect, observed))
    return _decision(
        EffectDecisionKind.REFUSED_PRESERVE_LIVE,
        observed,
        steps=steps,
        refused=True,
    )


def _classify_delete(
    snapshot: RecoverySnapshot,
    effect: Effect,
    frontiers: tuple[PathFrontier, ...],
) -> EffectDecision:
    del frontiers
    observed = _joint(snapshot, effect)
    return _halt(observed, observed)


def _classify_move(
    snapshot: RecoverySnapshot,
    effect: Effect,
    frontiers: tuple[PathFrontier, ...],
) -> EffectDecision:
    del frontiers
    observed = _joint(snapshot, effect)
    return _halt(observed, observed)


def _classify_directory(
    snapshot: RecoverySnapshot,
    effect: Effect,
    frontiers: tuple[PathFrontier, ...],
) -> EffectDecision:
    del frontiers
    observed = _joint(snapshot, effect)
    return _halt(observed, observed)


_CLASSIFIERS = {
    ReplaceFile: _classify_replace,
    CreateFileNoClobber: _classify_create_file,
    DeletePath: _classify_delete,
    MoveNoClobber: _classify_move,
    CreateDirectory: _classify_directory,
}


def _journal(snapshot: RecoverySnapshot, effect_id: str) -> JournalState:
    for row in snapshot.journals:
        if row.effect_id == effect_id:
            if type(row.state) is not JournalState:
                raise ProtocolError("journal state has the wrong exact runtime type")
            return row.state
    raise ProtocolError("snapshot is missing effect journal coverage")


def _frontier_map(
    frontiers: tuple[PathFrontier, ...],
) -> dict[str, PathFrontier]:
    if type(frontiers) is not tuple:
        raise ProtocolError("frontiers must be an exact tuple")
    by_path: dict[str, PathFrontier] = {}
    for frontier in frontiers:
        if type(frontier) is not PathFrontier:
            raise ProtocolError("frontiers contain the wrong exact runtime type")
        if type(frontier.path) is not str:
            raise ProtocolError("frontier path has the wrong exact runtime type")
        if type(frontier.direction) is not FrontierDirection:
            raise ProtocolError("frontier direction has the wrong exact runtime type")
        if frontier.effect_index is not None and type(frontier.effect_index) is not int:
            raise ProtocolError("frontier effect_index has the wrong exact runtime type")
        if frontier.effect_id is not None and type(frontier.effect_id) is not str:
            raise ProtocolError("frontier effect_id has the wrong exact runtime type")
        if (
            frontier.journal_state is not None
            and type(frontier.journal_state) is not JournalState
        ):
            raise ProtocolError("frontier journal_state has the wrong exact runtime type")
        _require_path_state(frontier.expected_state, "frontier expected_state")
        if type(frontier.admissible_states) is not tuple:
            raise ProtocolError("frontier admissible_states must be an exact tuple")
        for state in frontier.admissible_states:
            _require_path_state(state, "frontier admissible state")
        if frontier.path in by_path:
            raise ProtocolError("frontiers contain duplicate path coverage")
        by_path[frontier.path] = frontier
    return by_path


def _require_path_state(state: object, label: str) -> None:
    if type(state) not in {AbsentState, FileState, DirectoryState, SymlinkState}:
        raise ProtocolError(f"{label} has the wrong exact runtime type")


def _at_frontier(
    observed: JointObservation,
    frontiers: dict[str, PathFrontier],
) -> tuple[bool, tuple[PersistentNode, ...]]:
    drifted: list[PersistentNode] = []
    for item in observed.persistent:
        try:
            expected = frontiers[item.path].expected_state
        except KeyError as exc:
            raise ProtocolError("frontiers are missing an owned path") from exc
        if _entry_state(item.entry) != expected:
            drifted.append(PersistentNode(item.path))
    return not drifted, tuple(drifted)


def _classify_pending(
    snapshot: RecoverySnapshot,
    effect: Effect,
    frontiers: dict[str, PathFrontier],
) -> EffectDecision:
    observed = _joint(snapshot, effect)
    if any(type(item.entry) is not ObservedAbsent for item in observed.scratch):
        return _halt(observed, observed)
    clean, drifted = _at_frontier(observed, frontiers)
    if clean:
        return _decision(EffectDecisionKind.NO_ACTION, observed)
    return _decision(
        EffectDecisionKind.PRESERVE_EXTERNAL,
        observed,
        steps=(_preserve(*drifted),),
        refused=True,
    )


def _classify_undone(
    snapshot: RecoverySnapshot,
    effect: Effect,
    frontiers: dict[str, PathFrontier],
) -> EffectDecision:
    observed = _joint(snapshot, effect)
    scratch_absent = all(
        type(item.entry) is ObservedAbsent for item in observed.scratch
    )
    clean, _ = _at_frontier(observed, frontiers)
    if scratch_absent and clean:
        return _decision(EffectDecisionKind.NO_ACTION, observed)
    return _halt(observed, observed)


def classify_effect(
    snapshot: RecoverySnapshot,
    effect_index: int,
    frontiers: tuple[PathFrontier, ...],
) -> EffectDecision:
    if type(snapshot) is not RecoverySnapshot:
        raise ProtocolError("snapshot must be a factory-issued RecoverySnapshot")
    if type(snapshot.compiled) is not CompiledSpec:
        raise ProtocolError("snapshot compiled value has the wrong exact runtime type")
    if type(effect_index) is not int:
        raise ProtocolError("effect_index has the wrong exact runtime type")
    if effect_index < 0 or effect_index >= len(snapshot.compiled.spec.effects):
        raise ProtocolError("effect_index is outside compiled effect coverage")
    frontier_by_path = _frontier_map(frontiers)
    expected_paths = {
        observation.path for observation in snapshot.persistent_observations
    }
    if set(frontier_by_path) != expected_paths:
        raise ProtocolError("frontiers do not provide exact snapshot path coverage")
    effect = snapshot.compiled.spec.effects[effect_index]
    journal = _journal(snapshot, effect.effect_id)
    if journal is JournalState.PENDING:
        return _classify_pending(snapshot, effect, frontier_by_path)
    if journal is JournalState.UNDONE:
        return _classify_undone(snapshot, effect, frontier_by_path)
    try:
        classifier = _CLASSIFIERS[type(effect)]
    except KeyError as exc:
        raise ProtocolError("effect variant is outside A3's closed set") from exc
    return classifier(snapshot, effect, frontiers)
