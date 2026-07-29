from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from atoms.core.compiler import CompiledSpec
from atoms.core.errors import ProtocolError
from atoms.core.fingerprint import PathState
from atoms.core.recovery.model import (
    CommitDecision,
    EffectJournalState,
    HaltDiagnostic,
    HaltReason,
    JournalState,
    TransactionState,
)
from atoms.core.recovery.snapshot import RecoverySnapshot
from atoms.core.timeline import TimelineOccurrence


class AuthorityKind(Enum):
    CLASSIFY = "classify"
    DETACH = "detach"
    NO_RECOVERY = "no_recovery"
    STABLE_HALT = "stable_halt"
    HALT = "halt"


@dataclass(frozen=True, slots=True)
class AuthorityDecision:
    kind: AuthorityKind
    halt_reason: HaltReason | None


class FrontierDirection(Enum):
    FORWARD = "forward"
    REVERSE = "reverse"
    INITIAL = "initial"


@dataclass(frozen=True, slots=True)
class PathFrontier:
    path: str
    direction: FrontierDirection
    effect_index: int | None
    effect_id: str | None
    journal_state: JournalState | None
    expected_state: PathState
    admissible_states: tuple[PathState, ...]


def _require_snapshot(snapshot: object) -> RecoverySnapshot:
    if type(snapshot) is not RecoverySnapshot:
        raise ProtocolError("snapshot must be a factory-issued RecoverySnapshot")
    if type(snapshot.compiled) is not CompiledSpec:
        raise ProtocolError("snapshot compiled value has the wrong exact runtime type")
    if type(snapshot.transaction_state) is not TransactionState:
        raise ProtocolError("snapshot transaction_state has the wrong exact runtime type")
    if type(snapshot.commit_decision) is not CommitDecision:
        raise ProtocolError("snapshot commit_decision has the wrong exact runtime type")
    if type(snapshot.active) is not bool:
        raise ProtocolError("snapshot active binding has the wrong exact runtime type")
    if type(snapshot.journals) is not tuple:
        raise ProtocolError("snapshot journals must be an exact tuple")

    expected_ids = tuple(
        effect.effect_id for effect in snapshot.compiled.spec.effects
    )
    for journal in snapshot.journals:
        if type(journal) is not EffectJournalState:
            raise ProtocolError(
                "snapshot journals contain the wrong exact runtime type"
            )
        if type(journal.effect_id) is not str or type(journal.state) is not JournalState:
            raise ProtocolError("snapshot journal fields have the wrong exact runtime type")
    if tuple(journal.effect_id for journal in snapshot.journals) != expected_ids:
        raise ProtocolError("snapshot journal coverage does not match compiled effects")
    return snapshot


def _all(
    states: tuple[JournalState, ...],
    wanted: JournalState,
) -> bool:
    return all(state is wanted for state in states)


def _forward_language(states: tuple[JournalState, ...]) -> bool:
    phase = 0
    for state in states:
        if phase == 0 and state is JournalState.DONE:
            continue
        if phase == 0 and state is JournalState.STARTED:
            phase = 1
            continue
        if state is JournalState.PENDING:
            phase = 2
            continue
        return False
    return True


def _reverse_language(states: tuple[JournalState, ...]) -> bool:
    phase = 0
    for state in states:
        if phase == 0 and state is JournalState.DONE:
            continue
        if phase == 0 and state is JournalState.UNDO_STARTED:
            phase = 1
            continue
        if state is JournalState.UNDONE and phase in {0, 1, 2}:
            phase = 2
            continue
        if state is JournalState.PENDING and phase in {0, 2, 3}:
            phase = 3
            continue
        return False
    return True


def _rolled_back_language(states: tuple[JournalState, ...]) -> bool:
    pending = False
    for state in states:
        if state is JournalState.PENDING:
            pending = True
            continue
        if state is JournalState.UNDONE and not pending:
            continue
        return False
    return True


def _compatible_commit_decision(
    state: TransactionState,
    decision: CommitDecision,
) -> bool:
    if state is TransactionState.HALTED:
        return True
    if state is TransactionState.COMMITTED:
        return decision is CommitDecision.COMMITTED
    return decision is CommitDecision.UNCOMMITTED


def _legal_journal_language(
    state: TransactionState,
    states: tuple[JournalState, ...],
) -> bool:
    if state is TransactionState.PREPARED:
        return _all(states, JournalState.PENDING)
    if state is TransactionState.APPLYING:
        return _forward_language(states)
    if state is TransactionState.APPLIED:
        return _all(states, JournalState.DONE)
    if state is TransactionState.ROLLING_BACK:
        return _forward_language(states) or _reverse_language(states)
    if state is TransactionState.COMMITTED:
        return _all(states, JournalState.DONE)
    if state is TransactionState.ROLLED_BACK:
        return _rolled_back_language(states)
    raise ProtocolError("transaction state is outside the non-halted language matrix")


def classify_transaction_authority(
    snapshot: RecoverySnapshot,
) -> AuthorityDecision:
    snapshot = _require_snapshot(snapshot)
    state = snapshot.transaction_state
    if not _compatible_commit_decision(state, snapshot.commit_decision):
        return AuthorityDecision(
            kind=AuthorityKind.HALT,
            halt_reason=HaltReason.COMMIT_DECISION_CONFLICT,
        )

    if state is TransactionState.HALTED:
        diagnostic = snapshot.halt_diagnostic
        if type(diagnostic) is not HaltDiagnostic:
            raise ProtocolError("HALTED snapshot is missing its exact halt diagnostic")
        if type(diagnostic.reason) is not HaltReason:
            raise ProtocolError("halt diagnostic reason has the wrong exact runtime type")
        return AuthorityDecision(
            kind=AuthorityKind.STABLE_HALT,
            halt_reason=diagnostic.reason,
        )

    states = tuple(journal.state for journal in snapshot.journals)
    if not _legal_journal_language(state, states):
        return AuthorityDecision(
            kind=AuthorityKind.HALT,
            halt_reason=HaltReason.JOURNAL_TOPOLOGY_INVALID,
        )

    terminal = state in {
        TransactionState.COMMITTED,
        TransactionState.ROLLED_BACK,
    }
    if not snapshot.active and terminal:
        return AuthorityDecision(
            kind=AuthorityKind.NO_RECOVERY,
            halt_reason=None,
        )
    if not snapshot.active:
        return AuthorityDecision(
            kind=AuthorityKind.HALT,
            halt_reason=HaltReason.ACTIVE_BINDING_MISSING,
        )
    if state is TransactionState.ROLLED_BACK:
        return AuthorityDecision(
            kind=AuthorityKind.DETACH,
            halt_reason=None,
        )
    return AuthorityDecision(
        kind=AuthorityKind.CLASSIFY,
        halt_reason=None,
    )


def _unique_states(*states: PathState) -> tuple[PathState, ...]:
    unique: list[PathState] = []
    for state in states:
        if state not in unique:
            unique.append(state)
    return tuple(unique)


def _initial_frontier(
    path: str,
    occurrences: list[tuple[TimelineOccurrence, JournalState]],
) -> PathFrontier:
    if not occurrences:
        raise ProtocolError("compiled path timeline has no occurrences")
    initial = occurrences[0][0].pre
    return PathFrontier(
        path=path,
        direction=FrontierDirection.INITIAL,
        effect_index=None,
        effect_id=None,
        journal_state=None,
        expected_state=initial,
        admissible_states=(initial,),
    )


def _effect_index(
    effect_id: str,
    index_by_id: dict[str, int],
) -> int:
    try:
        return index_by_id[effect_id]
    except KeyError as exc:
        raise ProtocolError(
            "timeline occurrence is missing from compiled effect order"
        ) from exc


def _forward_frontier(
    path: str,
    occurrences: list[tuple[TimelineOccurrence, JournalState]],
    index_by_id: dict[str, int],
) -> PathFrontier:
    started = [
        occurrence
        for occurrence, state in occurrences
        if state is JournalState.STARTED
    ]
    if len(started) > 1:
        raise ProtocolError("forward path frontier has multiple STARTED occurrences")
    if started:
        selected = started[0]
        return PathFrontier(
            path=path,
            direction=FrontierDirection.FORWARD,
            effect_index=_effect_index(selected.effect_id, index_by_id),
            effect_id=selected.effect_id,
            journal_state=JournalState.STARTED,
            expected_state=selected.pre,
            admissible_states=_unique_states(selected.pre, selected.post),
        )

    completed = [
        occurrence
        for occurrence, state in occurrences
        if state is JournalState.DONE
    ]
    if not completed:
        return _initial_frontier(path, occurrences)
    selected = completed[-1]
    return PathFrontier(
        path=path,
        direction=FrontierDirection.FORWARD,
        effect_index=_effect_index(selected.effect_id, index_by_id),
        effect_id=selected.effect_id,
        journal_state=JournalState.DONE,
        expected_state=selected.post,
        admissible_states=(selected.post,),
    )


def _reverse_frontier(
    path: str,
    occurrences: list[tuple[TimelineOccurrence, JournalState]],
    index_by_id: dict[str, int],
) -> PathFrontier:
    candidates = [
        (occurrence, state)
        for occurrence, state in occurrences
        if state
        in {
            JournalState.DONE,
            JournalState.STARTED,
            JournalState.UNDO_STARTED,
        }
    ]
    if not candidates:
        return _initial_frontier(path, occurrences)

    selected, journal_state = candidates[-1]
    if journal_state is JournalState.DONE:
        admissible = (selected.post,)
    elif journal_state is JournalState.STARTED:
        admissible = _unique_states(selected.pre, selected.post)
    elif journal_state is JournalState.UNDO_STARTED:
        admissible = _unique_states(selected.post, selected.pre)
    else:
        raise ProtocolError("reverse frontier selected an unsupported journal state")
    return PathFrontier(
        path=path,
        direction=FrontierDirection.REVERSE,
        effect_index=_effect_index(selected.effect_id, index_by_id),
        effect_id=selected.effect_id,
        journal_state=journal_state,
        expected_state=selected.post,
        admissible_states=admissible,
    )


def reconstruct_frontiers(
    snapshot: RecoverySnapshot,
) -> tuple[PathFrontier, ...]:
    snapshot = _require_snapshot(snapshot)
    journal_by_id = {row.effect_id: row.state for row in snapshot.journals}
    index_by_id = {
        effect.effect_id: index
        for index, effect in enumerate(snapshot.compiled.spec.effects)
    }
    reverse = snapshot.transaction_state is TransactionState.ROLLING_BACK
    frontiers: list[PathFrontier] = []
    for timeline in snapshot.compiled.timelines:
        if type(timeline.occurrences) is not tuple:
            raise ProtocolError("compiled path timeline occurrences must be an exact tuple")
        occurrence_states: list[tuple[TimelineOccurrence, JournalState]] = []
        for occurrence in timeline.occurrences:
            if type(occurrence) is not TimelineOccurrence:
                raise ProtocolError(
                    "compiled path timeline contains the wrong occurrence type"
                )
            try:
                journal_state = journal_by_id[occurrence.effect_id]
            except KeyError as exc:
                raise ProtocolError(
                    "timeline occurrence has no journal coverage"
                ) from exc
            occurrence_states.append((occurrence, journal_state))
        if reverse:
            frontier = _reverse_frontier(
                timeline.path,
                occurrence_states,
                index_by_id,
            )
        else:
            frontier = _forward_frontier(
                timeline.path,
                occurrence_states,
                index_by_id,
            )
        frontiers.append(frontier)
    return tuple(frontiers)
