from __future__ import annotations

from atoms.core.errors import ProtocolError
from atoms.core.fingerprint import ABSENT, PathState
from atoms.core.recovery.model import (
    DiagnosticEntry,
    DiagnosticIdentityRelation,
    EntryIdentity,
    FileBuildRelation,
    HaltDiagnostic,
    HaltReason,
    IdentityRelation,
    ObservedAbsent,
    ObservedDirectory,
    ObservedEntry,
    ObservedFile,
    ObservedSymlink,
    OperatorAction,
    PersistentObservation,
    ScratchObservation,
    ScratchRole,
)
from atoms.core.recovery.plan import JointObservation
from atoms.core.recovery.snapshot import RecoverySnapshot


def _persistent_slot(path: str) -> str:
    return f"persistent:{path}"


def _scratch_slot(effect_id: str, role: ScratchRole) -> str:
    return f"scratch:{effect_id}:{role.value}"


def _entry_fields(
    entry: ObservedEntry,
) -> tuple[PathState, bool | None, EntryIdentity | None]:
    if type(entry) is ObservedAbsent:
        return ABSENT, None, None
    if type(entry) is ObservedFile:
        return entry.state, None, entry.identity
    if type(entry) is ObservedSymlink:
        return entry.state, None, None
    if type(entry) is ObservedDirectory:
        return entry.state, entry.has_unmodeled_child, entry.identity
    raise ProtocolError("diagnostic observation entry has the wrong exact runtime type")


def _named_entries(
    observation: JointObservation | None,
) -> tuple[
    tuple[str, ObservedEntry, FileBuildRelation | None],
    ...,
]:
    if observation is None:
        return ()
    if type(observation) is not JointObservation:
        raise ProtocolError("diagnostic evidence must be an exact JointObservation")
    if (
        type(observation.persistent) is not tuple
        or type(observation.scratch) is not tuple
        or type(observation.parent_occupancy) is not tuple
    ):
        raise ProtocolError("diagnostic evidence fields must be exact tuples")

    entries: list[
        tuple[str, ObservedEntry, FileBuildRelation | None]
    ] = []
    for item in observation.persistent:
        if type(item) is not PersistentObservation or type(item.path) is not str:
            raise ProtocolError(
                "diagnostic persistent evidence has the wrong exact runtime type"
            )
        _entry_fields(item.entry)
        entries.append((_persistent_slot(item.path), item.entry, None))
    for item in observation.scratch:
        if (
            type(item) is not ScratchObservation
            or type(item.effect_id) is not str
            or type(item.role) is not ScratchRole
        ):
            raise ProtocolError(
                "diagnostic scratch evidence has the wrong exact runtime type"
            )
        if (
            item.file_build_relation is not None
            and type(item.file_build_relation) is not FileBuildRelation
        ):
            raise ProtocolError(
                "diagnostic scratch build relation has the wrong exact runtime type"
            )
        _entry_fields(item.entry)
        entries.append(
            (
                _scratch_slot(item.effect_id, item.role),
                item.entry,
                item.file_build_relation,
            )
        )

    entries.sort(key=lambda item: item[0])
    slots = tuple(item[0] for item in entries)
    if len(set(slots)) != len(slots):
        raise ProtocolError("diagnostic evidence slots must be unique")
    return tuple(entries)


def _diagnostic_paths(
    expected: JointObservation | None,
    observed: JointObservation | None,
) -> tuple[str, ...]:
    paths: set[str] = set()
    for observation in (expected, observed):
        if observation is None:
            continue
        if type(observation) is not JointObservation:
            raise ProtocolError(
                "diagnostic evidence must be an exact JointObservation"
            )
        if type(observation.persistent) is not tuple:
            raise ProtocolError(
                "diagnostic persistent evidence must be an exact tuple"
            )
        for item in observation.persistent:
            if type(item) is not PersistentObservation or type(item.path) is not str:
                raise ProtocolError(
                    "diagnostic persistent evidence has the wrong exact runtime type"
                )
            paths.add(item.path)
    return tuple(sorted(paths))


def _project_entries(
    observation: JointObservation | None,
) -> tuple[DiagnosticEntry, ...]:
    projected: list[DiagnosticEntry] = []
    for slot, entry, build_relation in _named_entries(observation):
        state, has_unmodeled_child, _ = _entry_fields(entry)
        projected.append(
            DiagnosticEntry(
                slot=slot,
                state=state,
                has_unmodeled_child=has_unmodeled_child,
                file_build_relation=build_relation,
            )
        )
    return tuple(projected)


def _project_identity_relations(
    namespace: str,
    observation: JointObservation | None,
) -> tuple[DiagnosticIdentityRelation, ...]:
    if type(namespace) is not str:
        raise ProtocolError("diagnostic identity namespace must be an exact string")
    identified: list[tuple[str, EntryIdentity]] = []
    for slot, entry, _ in _named_entries(observation):
        _, _, identity = _entry_fields(entry)
        if identity is not None:
            identified.append((f"{namespace}:{slot}", identity))

    relations: list[DiagnosticIdentityRelation] = []
    for left_index, (left_slot, left_identity) in enumerate(identified):
        for right_slot, right_identity in identified[left_index + 1 :]:
            relations.append(
                DiagnosticIdentityRelation(
                    left_slot=left_slot,
                    right_slot=right_slot,
                    relation=(
                        IdentityRelation.SAME
                        if left_identity is right_identity
                        else IdentityRelation.DIFFERENT
                    ),
                )
            )
    relations.sort(key=lambda item: (item.left_slot, item.right_slot))
    return tuple(relations)


def _diagnostic(
    source: RecoverySnapshot,
    *,
    reason: HaltReason,
    projected: RecoverySnapshot | None = None,
    effect_id: str | None = None,
    expected: JointObservation | None = None,
    observed: JointObservation | None = None,
) -> HaltDiagnostic:
    if type(source) is not RecoverySnapshot:
        raise ProtocolError("diagnostic source must be a factory-issued RecoverySnapshot")
    if projected is not None and type(projected) is not RecoverySnapshot:
        raise ProtocolError(
            "diagnostic projection must be a factory-issued RecoverySnapshot"
        )
    if type(reason) is not HaltReason:
        raise ProtocolError("diagnostic reason must be an exact HaltReason")
    if effect_id is not None and type(effect_id) is not str:
        raise ProtocolError("diagnostic effect_id must be an exact string")

    evidence_snapshot = source if projected is None else projected
    return HaltDiagnostic(
        pre_halt_state=source.transaction_state,
        commit_decision=source.commit_decision,
        journals=source.journals,
        projected_transaction_state=evidence_snapshot.transaction_state,
        projected_journals=evidence_snapshot.journals,
        effect_id=effect_id,
        paths=_diagnostic_paths(expected, observed),
        expected=_project_entries(expected),
        observed=_project_entries(observed),
        identity_relations=(
            *_project_identity_relations("expected", expected),
            *_project_identity_relations("observed", observed),
        ),
        reason=reason,
        operator_action=(
            OperatorAction.REPAIR_DURABLE_METADATA
            if reason
            in {
                HaltReason.JOURNAL_TOPOLOGY_INVALID,
                HaltReason.COMMIT_DECISION_CONFLICT,
                HaltReason.ACTIVE_BINDING_MISSING,
            }
            else OperatorAction.INSPECT_PRESERVED_EVIDENCE
        ),
    )
