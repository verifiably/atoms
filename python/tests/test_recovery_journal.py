import pytest

from atoms.core.errors import ProtocolError
from atoms.core.recovery import (
    CommitDecision,
    EffectJournalState,
    HaltReason,
    JournalState,
    TransactionState,
)
from atoms.core.recovery.journal import (
    AuthorityKind,
    FrontierDirection,
    classify_transaction_authority,
    reconstruct_frontiers,
)
from tests.recovery_support import create_snapshot


@pytest.mark.parametrize(
    ("state", "journals", "expected"),
    [
        (TransactionState.PREPARED, (JournalState.PENDING,), AuthorityKind.CLASSIFY),
        (TransactionState.APPLYING, (JournalState.STARTED,), AuthorityKind.CLASSIFY),
        (TransactionState.APPLIED, (JournalState.DONE,), AuthorityKind.CLASSIFY),
        (TransactionState.ROLLING_BACK, (JournalState.STARTED,), AuthorityKind.CLASSIFY),
        (
            TransactionState.ROLLING_BACK,
            (JournalState.UNDO_STARTED,),
            AuthorityKind.CLASSIFY,
        ),
        (TransactionState.ROLLED_BACK, (JournalState.UNDONE,), AuthorityKind.DETACH),
    ],
)
def test_single_effect_legal_languages(state, journals, expected):
    snapshot = create_snapshot(state=state, journal=journals[0])
    assert classify_transaction_authority(snapshot).kind is expected


def test_commit_decision_conflict_has_closed_reason():
    snapshot = create_snapshot(
        state=TransactionState.APPLIED,
        journal=JournalState.DONE,
        commit_decision=CommitDecision.COMMITTED,
    )
    decision = classify_transaction_authority(snapshot)
    assert decision.kind is AuthorityKind.HALT
    assert decision.halt_reason is HaltReason.COMMIT_DECISION_CONFLICT


def test_detached_nonterminal_halts_without_reattachment():
    decision = classify_transaction_authority(create_snapshot(active=False))
    assert decision.kind is AuthorityKind.HALT
    assert decision.halt_reason is HaltReason.ACTIVE_BINDING_MISSING


@pytest.mark.parametrize(
    "states",
    [
        (JournalState.DONE, JournalState.STARTED, JournalState.PENDING),
        (JournalState.DONE, JournalState.UNDO_STARTED, JournalState.UNDONE),
        (JournalState.DONE, JournalState.UNDONE, JournalState.PENDING),
        (JournalState.DONE, JournalState.DONE, JournalState.DONE),
        (JournalState.UNDONE, JournalState.UNDONE, JournalState.PENDING),
        (JournalState.PENDING, JournalState.PENDING, JournalState.PENDING),
    ],
)
def test_rolling_back_accepts_reachable_frontiers(three_effect_snapshot, states):
    snapshot = three_effect_snapshot(TransactionState.ROLLING_BACK, states)
    assert classify_transaction_authority(snapshot).kind is AuthorityKind.CLASSIFY


def test_rolling_back_rejects_started_followed_by_undone(three_effect_snapshot):
    snapshot = three_effect_snapshot(
        TransactionState.ROLLING_BACK,
        (JournalState.STARTED, JournalState.UNDONE, JournalState.PENDING),
    )
    decision = classify_transaction_authority(snapshot)
    assert decision.kind is AuthorityKind.HALT
    assert decision.halt_reason is HaltReason.JOURNAL_TOPOLOGY_INVALID


@pytest.mark.parametrize(
    "states",
    [
        (JournalState.PENDING, JournalState.UNDONE, JournalState.PENDING),
        (JournalState.UNDONE, JournalState.PENDING, JournalState.UNDONE),
    ],
)
def test_rolling_back_rejects_undone_after_pending(three_effect_snapshot, states):
    snapshot = three_effect_snapshot(TransactionState.ROLLING_BACK, states)
    decision = classify_transaction_authority(snapshot)
    assert decision.kind is AuthorityKind.HALT
    assert decision.halt_reason is HaltReason.JOURNAL_TOPOLOGY_INVALID


@pytest.mark.parametrize(
    ("state", "states"),
    [
        (TransactionState.PREPARED, (JournalState.DONE,) * 3),
        (
            TransactionState.ROLLED_BACK,
            (JournalState.DONE, JournalState.UNDONE, JournalState.PENDING),
        ),
    ],
)
def test_each_state_uses_its_own_journal_language(
    three_effect_snapshot,
    state,
    states,
):
    decision = classify_transaction_authority(three_effect_snapshot(state, states))
    assert decision.kind is AuthorityKind.HALT
    assert decision.halt_reason is HaltReason.JOURNAL_TOPOLOGY_INVALID


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize(
    "commit_decision",
    [CommitDecision.UNCOMMITTED, CommitDecision.COMMITTED],
)
def test_halted_short_circuits_language_rederivation(
    halted_authority_snapshot,
    active,
    commit_decision,
):
    snapshot = halted_authority_snapshot(
        (JournalState.DONE, JournalState.PENDING, JournalState.UNDONE),
        active=active,
        commit_decision=commit_decision,
    )
    decision = classify_transaction_authority(snapshot)
    assert decision.kind is AuthorityKind.STABLE_HALT
    assert decision.halt_reason is snapshot.halt_diagnostic.reason


@pytest.mark.parametrize(
    ("state", "commit_decision"),
    [
        (TransactionState.PREPARED, CommitDecision.COMMITTED),
        (TransactionState.APPLYING, CommitDecision.COMMITTED),
        (TransactionState.APPLIED, CommitDecision.COMMITTED),
        (TransactionState.ROLLING_BACK, CommitDecision.COMMITTED),
        (TransactionState.ROLLED_BACK, CommitDecision.COMMITTED),
        (TransactionState.COMMITTED, CommitDecision.UNCOMMITTED),
    ],
)
def test_every_state_rejects_an_incompatible_commit_decision(
    three_effect_snapshot,
    state,
    commit_decision,
):
    journal = (
        (JournalState.DONE,) * 3
        if state in {TransactionState.APPLIED, TransactionState.COMMITTED}
        else (JournalState.UNDONE,) * 3
        if state is TransactionState.ROLLED_BACK
        else (JournalState.PENDING,) * 3
    )
    decision = classify_transaction_authority(
        three_effect_snapshot(state, journal, commit_decision=commit_decision)
    )
    assert decision.kind is AuthorityKind.HALT
    assert decision.halt_reason is HaltReason.COMMIT_DECISION_CONFLICT


@pytest.mark.parametrize(
    ("state", "journal"),
    [
        (TransactionState.COMMITTED, JournalState.DONE),
        (TransactionState.ROLLED_BACK, JournalState.UNDONE),
    ],
)
def test_detached_terminal_requires_no_recovery(state, journal):
    snapshot = create_snapshot(
        state=state,
        journal=journal,
        commit_decision=(
            CommitDecision.COMMITTED
            if state is TransactionState.COMMITTED
            else CommitDecision.UNCOMMITTED
        ),
        active=False,
    )
    decision = classify_transaction_authority(snapshot)
    assert decision.kind is AuthorityKind.NO_RECOVERY
    assert decision.halt_reason is None


def test_active_committed_transaction_is_classified_for_cleanup():
    snapshot = create_snapshot(
        state=TransactionState.COMMITTED,
        journal=JournalState.DONE,
        commit_decision=CommitDecision.COMMITTED,
    )
    assert classify_transaction_authority(snapshot).kind is AuthorityKind.CLASSIFY


def test_all_pending_forward_frontier_is_initial():
    snapshot = create_snapshot(
        state=TransactionState.PREPARED,
        journal=JournalState.PENDING,
    )
    frontier = reconstruct_frontiers(snapshot)[0]
    first = snapshot.compiled.timelines[0].occurrences[0]
    assert frontier.direction is FrontierDirection.INITIAL
    assert frontier.effect_index is None
    assert frontier.effect_id is None
    assert frontier.journal_state is None
    assert frontier.expected_state == first.pre
    assert frontier.admissible_states == (first.pre,)


def test_forward_started_frontier_retains_baseline_and_closed_states():
    snapshot = create_snapshot(
        state=TransactionState.APPLYING,
        journal=JournalState.STARTED,
    )
    frontier = reconstruct_frontiers(snapshot)[0]
    occurrence = snapshot.compiled.timelines[0].occurrences[0]
    assert frontier.direction is FrontierDirection.FORWARD
    assert frontier.effect_index == 0
    assert frontier.effect_id == "e1"
    assert frontier.journal_state is JournalState.STARTED
    assert frontier.expected_state == occurrence.pre
    assert frontier.admissible_states == (occurrence.pre, occurrence.post)


def test_forward_frontier_uses_compiled_occurrence_order_after_completed_effect(
    three_effect_snapshot,
):
    snapshot = three_effect_snapshot(
        TransactionState.APPLYING,
        (JournalState.DONE, JournalState.PENDING, JournalState.PENDING),
    )
    frontier = reconstruct_frontiers(snapshot)[0]
    occurrences = snapshot.compiled.timelines[0].occurrences
    assert frontier.direction is FrontierDirection.FORWARD
    assert frontier.effect_index == 0
    assert frontier.effect_id == "e1"
    assert frontier.journal_state is JournalState.DONE
    assert frontier.expected_state == occurrences[0].post
    assert frontier.admissible_states == (occurrences[0].post,)


def test_forward_frontier_selects_started_repeated_path_occurrence(
    three_effect_snapshot,
):
    snapshot = three_effect_snapshot(
        TransactionState.APPLYING,
        (JournalState.DONE, JournalState.STARTED, JournalState.PENDING),
    )
    frontier = reconstruct_frontiers(snapshot)[0]
    occurrence = snapshot.compiled.timelines[0].occurrences[1]
    assert frontier.effect_index == 1
    assert frontier.effect_id == "e2"
    assert frontier.journal_state is JournalState.STARTED
    assert frontier.expected_state == occurrence.pre
    assert frontier.admissible_states == (occurrence.pre, occurrence.post)


@pytest.mark.parametrize(
    ("states", "expected_index", "expected_journal"),
    [
        (
            (JournalState.DONE, JournalState.UNDONE, JournalState.PENDING),
            0,
            JournalState.DONE,
        ),
        (
            (JournalState.DONE, JournalState.UNDO_STARTED, JournalState.UNDONE),
            1,
            JournalState.UNDO_STARTED,
        ),
        (
            (JournalState.DONE, JournalState.STARTED, JournalState.PENDING),
            1,
            JournalState.STARTED,
        ),
    ],
)
def test_reverse_frontier_selects_latest_applied_or_in_flight_occurrence(
    three_effect_snapshot,
    states,
    expected_index,
    expected_journal,
):
    snapshot = three_effect_snapshot(TransactionState.ROLLING_BACK, states)
    frontier = reconstruct_frontiers(snapshot)[0]
    occurrence = snapshot.compiled.timelines[0].occurrences[expected_index]
    assert frontier.direction is FrontierDirection.REVERSE
    assert frontier.effect_index == expected_index
    assert frontier.effect_id == f"e{expected_index + 1}"
    assert frontier.journal_state is expected_journal
    assert frontier.expected_state == occurrence.post
    assert frontier.admissible_states == (
        (occurrence.post,)
        if expected_journal is JournalState.DONE
        else (occurrence.post, occurrence.pre)
        if expected_journal is JournalState.UNDO_STARTED
        else (occurrence.pre, occurrence.post)
    )


def test_all_undone_or_pending_reverse_frontier_is_initial(
    three_effect_snapshot,
):
    snapshot = three_effect_snapshot(
        TransactionState.ROLLING_BACK,
        (JournalState.UNDONE, JournalState.UNDONE, JournalState.PENDING),
    )
    frontier = reconstruct_frontiers(snapshot)[0]
    first = snapshot.compiled.timelines[0].occurrences[0]
    assert frontier.direction is FrontierDirection.INITIAL
    assert frontier.expected_state == first.pre
    assert frontier.admissible_states == (first.pre,)


def test_public_entry_points_reject_non_snapshot_values():
    with pytest.raises(ProtocolError, match="factory-issued RecoverySnapshot"):
        classify_transaction_authority(object())  # type: ignore[arg-type]
    with pytest.raises(ProtocolError, match="factory-issued RecoverySnapshot"):
        reconstruct_frontiers(object())  # type: ignore[arg-type]


def test_malformed_journal_internals_raise_protocol_error():
    snapshot = create_snapshot()
    object.__setattr__(
        snapshot,
        "journals",
        (EffectJournalState("not-e1", JournalState.STARTED),),
    )
    with pytest.raises(ProtocolError, match="journal coverage"):
        reconstruct_frontiers(snapshot)


def test_malformed_transaction_state_raises_protocol_error():
    snapshot = create_snapshot()
    object.__setattr__(snapshot, "transaction_state", "applying")
    with pytest.raises(ProtocolError, match="transaction_state"):
        classify_transaction_authority(snapshot)
