"""A5b tier 4 -- transition persistence: prefix validation, barriers, the cursor."""

from __future__ import annotations

import sqlite3

import pytest

from atoms.core.errors import ProtocolError
from atoms.core.recovery import CommitDecision, JournalState, TransactionState
from atoms.core.recovery.model import RollbackResult
from atoms.core.recovery.plan import (
    DetachActive,
    PreserveExternal,
    RemoveScratch,
    TransformEffectTuple,
)
from tests.coordinator_support import (
    prepared_metadata_only,
    prepared_with_halt,
    prepared_with_preserve_external,
    prepared_with_remove_scratch,
    prepared_with_transform,
)


def test_a_metadata_only_plan_detaches_only_after_settlement_binding(leased):
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_metadata_only(lease)
        assert type(plan.steps[-1]) is DetachActive

        with pytest.raises(sqlite3.IntegrityError, match="active delete requires"):
            persist_plan_prefix(lease, approved, plan, 0)

        record = lease._store.read_record(approved.txid)
        assert record is not None
        assert record.state is TransactionState.ROLLED_BACK
        assert record.rollback_result is RollbackResult.RESTORED
        assert lease._store.read_active() is not None

        with lease._store.transaction() as txn:
            txn.set_settlement_digest(approved.txid, "1" * 64)
        assert persist_plan_prefix(
            lease, approved, plan, len(plan.steps) - 1
        ) == len(plan.steps)
        assert lease._store.read_active() is None


def test_the_cursor_stops_at_a_remove_scratch(leased):
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_with_remove_scratch(lease)

        cursor = persist_plan_prefix(lease, approved, plan, 0)

        assert cursor == 2
        assert type(plan.steps[cursor]) is RemoveScratch
        record = lease._store.read_record(approved.txid)
        assert record is not None
        assert record.state is TransactionState.ROLLING_BACK
        assert record.journals[0].state is JournalState.UNDO_STARTED
        assert lease._store.read_active() is not None


def test_the_cursor_stops_at_a_transform(leased):
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_with_transform(lease)

        cursor = persist_plan_prefix(lease, approved, plan, 0)

        assert cursor == 2
        assert type(plan.steps[cursor]) is TransformEffectTuple


def test_preserve_external_writes_nothing_durable(leased):
    from atoms.coordinator.transitions import _persist_one

    with leased() as lease:
        approved, plan = prepared_with_preserve_external(lease)
        step = plan.steps[1]
        assert type(step) is PreserveExternal
        before = lease._store.read_record(approved.txid)

        _persist_one(lease, approved.txid, step)

        assert lease._store.read_record(approved.txid) == before


def test_a_preserve_external_plan_still_reaches_its_terminal_state(leased):
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_with_preserve_external(lease)

        with pytest.raises(sqlite3.IntegrityError, match="active delete requires"):
            persist_plan_prefix(lease, approved, plan, 0)

        record = lease._store.read_record(approved.txid)
        assert record is not None
        assert record.state is TransactionState.ROLLED_BACK


def test_a_halt_persists_its_diagnostic_with_its_state(leased):
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_with_halt(lease)

        assert persist_plan_prefix(lease, approved, plan, 0) == len(plan.steps)

        record = lease._store.read_record(approved.txid)
        assert record is not None
        assert record.state is TransactionState.HALTED
        assert record.halt_diagnostic == plan.diagnostic
        assert record.committed is CommitDecision.UNCOMMITTED


def test_the_first_halt_diagnostic_wins(leased):
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_with_halt(lease)
        persist_plan_prefix(lease, approved, plan, 0)
        first = lease._store.read_record(approved.txid)

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, plan, 0)

        assert "transaction state" in str(caught.value)
        assert lease._store.read_record(approved.txid) == first


def test_a_record_disagreeing_with_the_reduced_prefix_refuses(leased):
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_metadata_only(lease)
        with lease._store.transaction() as txn:
            txn.set_commit_decision(approved.txid, CommitDecision.COMMITTED)

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, plan, 0)

        assert "commit decision" in str(caught.value)


def test_a_plan_bound_to_another_compiled_spec_refuses(leased):
    from atoms.coordinator.transitions import persist_plan_prefix
    from atoms.core.compiler import compile_spec
    from atoms.core.recovery import (
        OBSERVED_ABSENT,
        JournalState,
        TransactionState,
        classify_recovery,
    )
    from tests.coordinator_support import (
        other_file_spec,
        reapproved_under,
        snapshot_for,
    )

    with leased() as lease:
        approved, _ = prepared_metadata_only(lease)
        other = reapproved_under(lease, approved.txid, compile_spec(other_file_spec()))
        foreign_plan = classify_recovery(
            snapshot_for(
                other,
                state=TransactionState.PREPARED,
                journal=JournalState.PENDING,
                live=OBSERVED_ABSENT,
                staged=OBSERVED_ABSENT,
                path="d/g.txt",
            )
        )

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, foreign_plan, 0)

        assert "different compiled spec" in str(caught.value)


def test_a_plan_bound_to_another_topology_refuses(leased):
    from atoms.coordinator.transitions import persist_plan_prefix
    from atoms.core.recovery import (
        OBSERVED_ABSENT,
        JournalState,
        TransactionState,
        classify_recovery,
    )
    from tests.coordinator_support import flattened_topology, snapshot_for

    with leased() as lease:
        approved, _ = prepared_metadata_only(lease)
        rearranged = flattened_topology(approved)
        assert rearranged != approved.topology

        plan = classify_recovery(
            snapshot_for(
                approved,
                state=TransactionState.PREPARED,
                journal=JournalState.PENDING,
                live=OBSERVED_ABSENT,
                staged=OBSERVED_ABSENT,
                topology=rearranged,
            )
        )

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, plan, 0)

        assert "different topology" in str(caught.value)


def test_a_record_whose_spec_disagrees_with_the_prefix_refuses(leased):
    from atoms.coordinator.transitions import persist_plan_prefix
    from atoms.core.compiler import compile_spec
    from atoms.core.recovery import (
        OBSERVED_ABSENT,
        JournalState,
        TransactionState,
        classify_recovery,
    )
    from tests.coordinator_support import (
        other_file_spec,
        reapproved_under,
        snapshot_for,
    )

    with leased() as lease:
        published, _ = prepared_metadata_only(lease)
        other = reapproved_under(
            lease, published.txid, compile_spec(other_file_spec())
        )
        plan = classify_recovery(
            snapshot_for(
                other,
                state=TransactionState.PREPARED,
                journal=JournalState.PENDING,
                live=OBSERVED_ABSENT,
                staged=OBSERVED_ABSENT,
                path="d/g.txt",
            )
        )

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, other, plan, 0)

        assert "spec disagrees with the plan prefix" in str(caught.value)


def test_no_active_record_refuses(leased):
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_metadata_only(lease)
        with pytest.raises(sqlite3.IntegrityError, match="active delete requires"):
            persist_plan_prefix(lease, approved, plan, 0)
        with lease._store.transaction() as txn:
            txn.set_settlement_digest(approved.txid, "2" * 64)
        persist_plan_prefix(lease, approved, plan, len(plan.steps) - 1)

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, plan, 0)

        assert "no active record" in str(caught.value)


def test_a_start_outside_the_step_range_refuses(leased):
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_metadata_only(lease)

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, plan, len(plan.steps) + 1)

        assert "outside the plan step range" in str(caught.value)


def test_a_record_whose_rollback_result_disagrees_refuses(leased):
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_metadata_only(lease)
        with lease._store.transaction() as txn:
            txn.set_transaction_state(approved.txid, TransactionState.ROLLING_BACK)
        with lease._store.transaction() as txn:
            txn.set_transaction_state(approved.txid, TransactionState.ROLLED_BACK)
            txn.set_rollback_result(
                approved.txid, RollbackResult.EXTERNAL_DRIFT_PRESERVED
            )

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, plan, 2)

        message = str(caught.value)
        assert "rollback result" in message
        assert "external_drift_preserved" in message.lower()


def test_a_record_whose_halt_diagnostic_disagrees_refuses(leased):
    from atoms.coordinator.transitions import persist_plan_prefix
    from tests.store_support import matching_diagnostic

    with leased() as lease:
        approved, plan = prepared_with_halt(lease)
        other = matching_diagnostic("e1")
        assert other != plan.diagnostic

        with lease._store.transaction() as txn:
            txn.set_transaction_state(approved.txid, TransactionState.HALTED)
            txn.set_halt_diagnostic(approved.txid, other)

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, plan, 1)

        assert "halt diagnostic" in str(caught.value)


def test_a_record_whose_journals_disagree_refuses(leased):
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_with_remove_scratch(lease)
        with lease._store.transaction() as txn:
            txn.set_journal_state(approved.txid, "e1", JournalState.DONE)

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, plan, 0)

        assert "journals" in str(caught.value)


def test_a_record_still_active_past_its_detach_refuses(leased):
    from atoms.coordinator.transitions import persist_plan_prefix

    with leased() as lease:
        approved, plan = prepared_metadata_only(lease)
        assert type(plan.steps[2]) is DetachActive

        with lease._store.transaction() as txn:
            txn.set_transaction_state(approved.txid, TransactionState.ROLLING_BACK)
        with lease._store.transaction() as txn:
            txn.set_transaction_state(approved.txid, TransactionState.ROLLED_BACK)
            txn.set_rollback_result(approved.txid, RollbackResult.RESTORED)

        with pytest.raises(ProtocolError) as caught:
            persist_plan_prefix(lease, approved, plan, len(plan.steps))

        assert "projects a detached transaction" in str(caught.value)


def test_each_writable_step_commits_before_the_next_begins(leased, monkeypatch):
    from atoms.coordinator import transitions

    with leased() as lease:
        approved, plan = prepared_metadata_only(lease)
        real = transitions._persist_one
        calls = []

        def cut_after_the_first(lease_, txid, step):
            calls.append(step)
            if len(calls) == 2:
                raise RuntimeError("cut between steps")
            real(lease_, txid, step)

        monkeypatch.setattr(transitions, "_persist_one", cut_after_the_first)

        with pytest.raises(RuntimeError):
            transitions.persist_plan_prefix(lease, approved, plan, 0)

        record = lease._store.read_record(approved.txid)
        assert record is not None
        assert record.state is TransactionState.ROLLING_BACK
        assert record.rollback_result is None
