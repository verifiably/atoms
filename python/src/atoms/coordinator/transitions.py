"""A3 transition persistence in plan order (design §8)."""

from __future__ import annotations

from atoms.coordinator.admission import _require_admitted
from atoms.coordinator.lease import Lease
from atoms.core.canonical import canonical_json
from atoms.core.errors import ProtocolError
from atoms.core.recovery.plan import (
    DetachActive,
    PreserveExternal,
    RecoveryPlan,
    RecoveryStep,
    RemoveScratch,
    TransformEffectTuple,
    TransitionEffectState,
    TransitionTransactionState,
)
from atoms.core.recovery.reducer import reduce_recovery_plan_prefix
from atoms.fs.approval import ProjectApprovedSpec
from atoms.store.records import StoredRecord

_STOP = (TransformEffectTuple, RemoveScratch, DetachActive)


def persist_plan_prefix(
    lease: Lease, approved: ProjectApprovedSpec, plan: RecoveryPlan, start: int
) -> int:
    """Persist metadata-only steps and stop before the first mutating step."""
    _require_admitted(lease, approved)
    if type(start) is not int or not 0 <= start <= len(plan.steps):
        raise ProtocolError(
            f"start {start!r} is outside the plan step range 0..{len(plan.steps)}"
        )

    record = lease._store.read_active()
    if record is None:
        raise ProtocolError("no active record to advance")
    if record.txid != approved.txid:
        raise ProtocolError(
            f"active record {record.txid!r} is not the proof's {approved.txid!r}"
        )

    _require_projection_matches(record, plan, start, approved)

    cursor = start
    while cursor < len(plan.steps):
        step = plan.steps[cursor]
        if type(step) in _STOP:
            return cursor
        _persist_one(lease, record.txid, step)
        cursor += 1
    return cursor


def _require_projection_matches(
    record: StoredRecord,
    plan: RecoveryPlan,
    start: int,
    approved: ProjectApprovedSpec,
) -> None:
    """Compare every durable field with the plan prefix reduced to `start`."""
    # Chain bindings and assembly evidence are enforced at their owning seams; they are
    # deliberately outside A3's transition projection.
    snapshot = plan.bound_snapshot
    if snapshot.compiled != approved.compiled:
        raise ProtocolError(
            "the plan's bound snapshot names a different compiled spec than the proof"
        )
    if snapshot.topology != approved.topology:
        raise ProtocolError(
            "the plan's bound snapshot names a different topology than the proof"
        )

    expected = reduce_recovery_plan_prefix(snapshot, plan, completed_steps=start)
    if canonical_json(record.spec) != canonical_json(expected.compiled.spec):
        raise ProtocolError(
            f"the active record's spec disagrees with the plan prefix reduced to step "
            f"{start}"
        )
    for label, stored, projected in (
        ("transaction state", record.state, expected.transaction_state),
        ("commit decision", record.committed, expected.commit_decision),
        ("rollback result", record.rollback_result, expected.rollback_result),
        ("halt diagnostic", record.halt_diagnostic, expected.halt_diagnostic),
        ("journals", record.journals, expected.journals),
    ):
        if stored != projected:
            raise ProtocolError(
                f"the active record's {label} disagrees with the plan prefix reduced "
                f"to step {start}: stored {stored!r}, projected {projected!r}"
            )
    if not expected.active:
        raise ProtocolError(
            f"the plan prefix reduced to step {start} projects a detached transaction, "
            "but this record is still the active one"
        )


def _persist_one(lease: Lease, txid: str, step: RecoveryStep) -> None:
    """Use one transaction for each step that has durable state to write."""
    if type(step) is PreserveExternal:
        return

    if type(step) is TransitionEffectState:
        with lease._store.transaction() as txn:
            txn.set_journal_state(txid, step.effect_id, step.to_state)
        return

    if type(step) is TransitionTransactionState:
        with lease._store.transaction() as txn:
            txn.set_transaction_state(txid, step.to_state)
            if step.rollback_result is not None:
                txn.set_rollback_result(txid, step.rollback_result)
            if step.halt_diagnostic is not None:
                txn.set_halt_diagnostic(txid, step.halt_diagnostic)
        return

    raise ProtocolError(
        f"step {type(step).__name__} is neither mutating nor persistable; the walk "
        "should have returned before reaching it"
    )


def persist_detach(
    lease: Lease,
    approved: ProjectApprovedSpec,
    plan: RecoveryPlan,
    cursor: int,
) -> int:
    _require_admitted(lease, approved)
    if type(cursor) is not int or not 0 <= cursor < len(plan.steps):
        raise ProtocolError("detach cursor is outside the plan step range")
    if type(plan.steps[cursor]) is not DetachActive:
        raise ProtocolError("detach cursor does not name DetachActive")
    record = lease._store.read_active()
    if record is None or record.txid != approved.txid:
        raise ProtocolError("the proof's transaction is not active")
    _require_projection_matches(record, plan, cursor, approved)
    if record.registration_digest is None or record.settlement_digest is None:
        raise ProtocolError("detach requires registration and settlement bindings")
    with lease._store.transaction() as txn:
        txn.set_active(None)
    return cursor + 1
