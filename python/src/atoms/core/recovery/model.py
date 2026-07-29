from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from atoms.core.effects import RelPath
from atoms.core.fingerprint import DirectoryState, FileState, PathState, SymlinkState


class TransactionState(Enum):
    PREPARED = "prepared"
    APPLYING = "applying"
    APPLIED = "applied"
    COMMITTED = "committed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    HALTED = "halted"


class CommitDecision(Enum):
    UNCOMMITTED = "uncommitted"
    COMMITTED = "committed"


class JournalState(Enum):
    PENDING = "pending"
    STARTED = "started"
    DONE = "done"
    UNDO_STARTED = "undo_started"
    UNDONE = "undone"


class RollbackResult(Enum):
    RESTORED = "restored"
    EXTERNAL_DRIFT_PRESERVED = "external_drift_preserved"


class HaltReason(Enum):
    JOURNAL_TOPOLOGY_INVALID = "journal_topology_invalid"
    COMMIT_DECISION_CONFLICT = "commit_decision_conflict"
    ACTIVE_BINDING_MISSING = "active_binding_missing"
    EFFECT_TUPLE_UNATTRIBUTABLE = "effect_tuple_unattributable"
    DIRECTORY_NOT_EMPTY = "directory_not_empty"
    COMMITTED_SURFACE_MISMATCH = "committed_surface_mismatch"
    PLAN_PRECONDITION_CHANGED = "plan_precondition_changed"


class ScratchRole(Enum):
    STAGING = "staging"
    TOMBSTONE = "tombstone"
    ANCHOR = "anchor"
    WORK = "work"


class FileBuildRelation(Enum):
    EXACT = "exact"
    STRICT_PREFIX = "strict_prefix"
    DIVERGED = "diverged"


class IdentityRelation(Enum):
    SAME = "same"
    DIFFERENT = "different"


class OperatorAction(Enum):
    INSPECT_PRESERVED_EVIDENCE = "inspect_preserved_evidence"
    REPAIR_DURABLE_METADATA = "repair_durable_metadata"


@dataclass(frozen=True, slots=True, repr=False, init=False)
class EntryIdentity:
    _token: object = field(repr=False)

    def __init__(self) -> None:
        object.__setattr__(self, "_token", object())

    def __repr__(self) -> str:
        return "<entry-identity>"


@dataclass(frozen=True, slots=True)
class ObservedAbsent:
    pass


@dataclass(frozen=True, slots=True)
class ObservedFile:
    state: FileState
    identity: EntryIdentity


@dataclass(frozen=True, slots=True)
class ObservedSymlink:
    state: SymlinkState


@dataclass(frozen=True, slots=True)
class ObservedDirectory:
    state: DirectoryState
    identity: EntryIdentity
    has_unmodeled_child: bool


ObservedEntry = ObservedAbsent | ObservedFile | ObservedSymlink | ObservedDirectory
OBSERVED_ABSENT = ObservedAbsent()


@dataclass(frozen=True, slots=True)
class EffectJournalState:
    effect_id: str
    state: JournalState


@dataclass(frozen=True, slots=True)
class PersistentObservation:
    path: RelPath
    entry: ObservedEntry


@dataclass(frozen=True, slots=True)
class ScratchObservation:
    effect_id: str
    role: ScratchRole
    entry: ObservedEntry
    file_build_relation: FileBuildRelation | None


@dataclass(frozen=True, slots=True)
class DiagnosticEntry:
    slot: str
    state: PathState
    has_unmodeled_child: bool | None
    file_build_relation: FileBuildRelation | None


@dataclass(frozen=True, slots=True)
class DiagnosticIdentityRelation:
    left_slot: str
    right_slot: str
    relation: IdentityRelation


@dataclass(frozen=True, slots=True)
class HaltDiagnostic:
    pre_halt_state: TransactionState
    commit_decision: CommitDecision
    journals: tuple[EffectJournalState, ...]
    projected_transaction_state: TransactionState
    projected_journals: tuple[EffectJournalState, ...]
    effect_id: str | None
    paths: tuple[RelPath, ...]
    expected: tuple[DiagnosticEntry, ...]
    observed: tuple[DiagnosticEntry, ...]
    identity_relations: tuple[DiagnosticIdentityRelation, ...]
    reason: HaltReason
    operator_action: OperatorAction
