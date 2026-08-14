"""Recovery-plan execution and terminal chain settlement."""

from __future__ import annotations

import contextlib
import errno
from collections.abc import Iterator
from dataclasses import dataclass
from typing import cast

from atoms.chain.append import append_entry, apply_survivors
from atoms.chain.errors import ChainStateInvalid
from atoms.chain.model import (
    ChainOutcome,
    Entry,
    RegisteredEntry,
    SettledEntry,
    encode_entry,
    entry_digest,
    state_to_json,
)
from atoms.chain.read import ValidatedChain, validate_chain
from atoms.coordinator.admission import _require_admitted
from atoms.coordinator.descriptors import DescriptorTable
from atoms.coordinator.effects.common import EffectMismatch
from atoms.coordinator.effects.settle import (
    _observe_joint,
    apply_remove_scratch,
    apply_transform,
)
from atoms.coordinator.lease import Lease
from atoms.coordinator.transitions import (
    persist_detach,
    persist_plan_prefix,
)
from atoms.core.errors import PreconditionRefused, ProtocolError
from atoms.core.recovery.authorization import _mutation_denied, authorize_recovery_step
from atoms.core.recovery.model import JournalState, TransactionState
from atoms.core.recovery.plan import (
    AuthorizedStep,
    DetachActive,
    HaltPlan,
    NoRecoveryPlan,
    RecoveryPlan,
    RemoveScratch,
    TransformEffectTuple,
)
from atoms.core.scratch import CHAIN_LEAF
from atoms.core.spec import TransactionSpec
from atoms.fs.approval import ProjectApprovedSpec
from atoms.fs.audit import AuditedBackend
from atoms.store import Store, StoredRecord


@dataclass(frozen=True, slots=True)
class _Backfill:
    digest: str


@dataclass(frozen=True, slots=True)
class _Append:
    entry: Entry


_ReconciliationAction = _Backfill | _Append


@dataclass(frozen=True, slots=True)
class Reconciliation:
    registration: _ReconciliationAction | None
    settlement: _ReconciliationAction | None


def _registration_entry(spec: TransactionSpec, txid: str) -> RegisteredEntry:
    initial = {item.path: item.state for item in spec.initial_surface}
    final = {item.path: item.state for item in spec.final_surface}
    return RegisteredEntry(
        txid=txid,
        intent_digest=spec.intent_digest,
        consumer_tag=spec.consumer_tag,
        initial=tuple(
            (path, state_to_json(initial[path])) for path in spec.registered_paths
        ),
        final=tuple(
            (path, state_to_json(final[path])) for path in spec.registered_paths
        ),
        fulfills=spec.fulfills,
    )


def _terminal_outcome(record: StoredRecord) -> ChainOutcome | None:
    if record.state is TransactionState.COMMITTED:
        return ChainOutcome.COMMITTED
    if record.state is TransactionState.ROLLED_BACK:
        return ChainOutcome.ROLLED_BACK
    return None


def _derive_reconciliation(
    record: StoredRecord | None, validated: ValidatedChain
) -> Reconciliation:
    """Derive the exact chain/store repairs without writing either substrate."""

    if record is None:
        return Reconciliation(None, None)

    entries = dict(validated.entries)
    registrations = [
        (digest, entry)
        for digest, entry in validated.entries
        if type(entry) is RegisteredEntry and entry.txid == record.txid
    ]
    if len(registrations) > 1:
        raise ChainStateInvalid("the chain contains duplicate registration entries")

    registration: _ReconciliationAction | None = None
    if record.registration_digest is not None:
        bound = entries.get(record.registration_digest)
        if type(bound) is not RegisteredEntry or bound.txid != record.txid:
            raise ChainStateInvalid(
                "the registration binding does not resolve to this transaction"
            )
    else:
        registration_window = (
            record.state is TransactionState.PREPARED
            and all(journal.state is JournalState.PENDING for journal in record.journals)
        )
        if not registration_window:
            raise ChainStateInvalid("the durable record is missing its registration")
        registration = (
            _Backfill(registrations[0][0])
            if registrations
            else _Append(_registration_entry(record.spec, record.txid))
        )

    settlements = [
        (digest, entry)
        for digest, entry in validated.entries
        if type(entry) is SettledEntry and entry.txid == record.txid
    ]
    if len(settlements) > 1:
        raise ChainStateInvalid("the chain contains duplicate settlement entries")

    outcome = _terminal_outcome(record)
    committed_halt = (
        record.state is TransactionState.HALTED
        and record.halt_diagnostic is not None
        and record.halt_diagnostic.pre_halt_state is TransactionState.COMMITTED
    )
    settlement: _ReconciliationAction | None = None
    if record.settlement_digest is not None:
        bound = entries.get(record.settlement_digest)
        expected_outcome = ChainOutcome.COMMITTED if committed_halt else outcome
        if (
            type(bound) is not SettledEntry
            or bound.txid != record.txid
            or bound.registration != record.registration_digest
            or bound.outcome is not expected_outcome
        ):
            raise ChainStateInvalid(
                "the settlement binding contradicts the durable record"
            )
        if outcome is None and not committed_halt:
            raise ChainStateInvalid(
                "only a committed halt may retain a terminal settlement"
            )
    elif outcome is not None:
        expected = SettledEntry(
            txid=record.txid,
            registration=cast(str, record.registration_digest),
            outcome=outcome,
        )
        if settlements:
            digest, found = settlements[0]
            if found != expected:
                raise ChainStateInvalid(
                    "the unbound settlement contradicts the durable record"
                )
            settlement = _Backfill(digest)
        else:
            settlement = _Append(expected)
    elif settlements:
        raise ChainStateInvalid("a nonterminal record has a settlement entry")

    return Reconciliation(registration, settlement)


def _perform_reconciliation(
    backend: AuditedBackend,
    store: Store,
    chain_fd: int,
    validated: ValidatedChain,
    actions: Reconciliation,
) -> ValidatedChain:
    """Apply exactly one derived reconciliation decision."""

    appends = tuple(
        action
        for action in (actions.registration, actions.settlement)
        if type(action) is _Append
    )
    if len(appends) > 1:
        raise ProtocolError("one reconciliation cannot append two chain entries")
    envelope = (
        encode_entry(validated.tip, cast(_Append, appends[0]).entry)
        if appends
        else None
    )
    fresh = validate_chain(
        backend, chain_fd, () if envelope is None else (envelope,)
    )
    if fresh.entries != validated.entries or fresh.tip != validated.tip:
        raise ChainStateInvalid("the chain changed after reconciliation derivation")
    fresh = apply_survivors(backend, chain_fd, fresh)
    record = store.read_active()
    if any(action is not None for action in (actions.registration, actions.settlement)):
        if record is None:
            raise ProtocolError("reconciliation actions require an active record")
        txid = record.txid

    for column, action in (
        ("registration", actions.registration),
        ("settlement", actions.settlement),
    ):
        if action is None:
            continue
        if type(action) is _Backfill:
            digest = action.digest
        else:
            planned = cast(bytes, envelope)
            digest = entry_digest(planned)
            if not any(found == digest for found, _ in fresh.entries):
                appended = append_entry(
                    backend, chain_fd, fresh, cast(_Append, action).entry
                )
                if appended != digest:
                    raise ProtocolError("the chain append returned an unexpected digest")
                fresh = validate_chain(backend, chain_fd)
        with store.transaction() as txn:
            if column == "registration":
                txn.set_registration_digest(txid, digest)
            else:
                txn.set_settlement_digest(txid, digest)
    return fresh


@contextlib.contextmanager
def _registered_root(lease: Lease) -> Iterator[tuple[int, ValidatedChain]]:
    backend = cast(AuditedBackend, lease._binding.backend)
    try:
        chain_fd = backend.open_child_directory(
            lease._binding.project_root_fd, CHAIN_LEAF
        )
    except OSError as caught:
        if caught.errno == errno.ENOENT:
            if lease._store.read_active() is not None:
                raise ChainStateInvalid(
                    "a live transaction record exists without its project chain"
                ) from caught
            raise PreconditionRefused("the project root is not registered") from caught
        if caught.errno in {errno.ENOTDIR, errno.ELOOP, errno.EXDEV}:
            raise ChainStateInvalid(
                "the reserved chain leaf is not a stable directory"
            ) from caught
        raise

    try:
        validated = validate_chain(backend, chain_fd)
        if not validated.entries:
            if lease._store.read_active() is not None:
                raise ChainStateInvalid(
                    "a live transaction record exists without a chain genesis"
                )
            raise PreconditionRefused("the project root is not registered")
        yield chain_fd, validated
    finally:
        backend.close_fd(chain_fd)


def run_plan(
    lease: Lease,
    approved: ProjectApprovedSpec,
    table: DescriptorTable,
    plan: RecoveryPlan,
) -> RecoveryPlan:
    _require_admitted(lease, approved)
    if type(plan) is NoRecoveryPlan:
        return plan
    cursor = 0
    while cursor < len(plan.steps):
        cursor = persist_plan_prefix(lease, approved, plan, cursor)
        if cursor == len(plan.steps):
            break
        step = plan.steps[cursor]
        if type(step) is DetachActive:
            _reconcile_settlement(lease, approved)
            cursor = persist_detach(lease, approved, plan, cursor)
            continue
        if type(step) not in {TransformEffectTuple, RemoveScratch}:
            raise ProtocolError("the plan stopped before a non-mutating step")
        step = cast(TransformEffectTuple | RemoveScratch, step)
        observed = _observe_for_step(lease, approved, table, step)
        authorized = authorize_recovery_step(plan, cursor, observed)
        if type(authorized) is HaltPlan:
            _persist_halt(lease, approved, authorized)
            return authorized
        authorized = cast(AuthorizedStep, authorized)
        try:
            _execute_mutating(lease, approved, table, authorized)
        except EffectMismatch as mismatch:
            observed = _observe_for_step(lease, approved, table, step)
            reconsidered = authorize_recovery_step(plan, cursor, observed)
            if type(reconsidered) is HaltPlan:
                _persist_halt(lease, approved, reconsidered)
                return reconsidered
            reconsidered = cast(AuthorizedStep, reconsidered)
            if mismatch.errno in {errno.EACCES, errno.EPERM}:
                denied = _mutation_denied(reconsidered, observed)
                _persist_halt(lease, approved, denied)
                return denied
            raise ProtocolError(
                "a recovery mutation failed while its observable world stayed unchanged"
            ) from mismatch
        cursor += 1
    return plan


def _observe_for_step(
    lease: Lease,
    approved: ProjectApprovedSpec,
    table: DescriptorTable,
    step: TransformEffectTuple | RemoveScratch,
):
    return _observe_joint(lease, approved, table, step.expected_before)


def _execute_mutating(
    lease: Lease,
    approved: ProjectApprovedSpec,
    table: DescriptorTable,
    authorized: AuthorizedStep,
) -> None:
    if type(authorized.step) is TransformEffectTuple:
        apply_transform(lease, approved, table, authorized)
        return
    if type(authorized.step) is RemoveScratch:
        apply_remove_scratch(lease, approved, table, authorized)
        return
    raise ProtocolError("authorization proof names no filesystem mutation")


def _persist_halt(
    lease: Lease, approved: ProjectApprovedSpec, plan: HaltPlan
) -> None:
    if persist_plan_prefix(lease, approved, plan, 0) != len(plan.steps):
        raise ProtocolError("a halt plan unexpectedly contains a filesystem mutation")


def _reconcile_settlement(lease: Lease, approved: ProjectApprovedSpec) -> None:
    record = lease._store.read_active()
    if record is None or record.txid != approved.txid:
        raise ProtocolError("the proof's transaction is not active")

    with _registered_root(lease) as (chain_fd, validated):
        actions = _derive_reconciliation(record, validated)
        _perform_reconciliation(
            cast(AuditedBackend, lease._binding.backend),
            lease._store,
            chain_fd,
            validated,
            actions,
        )
