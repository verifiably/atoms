"""Recovery-plan execution and terminal chain settlement."""

from __future__ import annotations

import contextlib
import errno
from collections.abc import Iterator
from typing import cast

from atoms.chain.append import append_entry, apply_survivors
from atoms.chain.errors import ChainStateInvalid
from atoms.chain.model import ChainOutcome, SettledEntry
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
from atoms.core.recovery.model import TransactionState
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
from atoms.fs.approval import ProjectApprovedSpec
from atoms.fs.audit import AuditedBackend


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
        validated = apply_survivors(
            backend, chain_fd, validate_chain(backend, chain_fd)
        )
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
    if record.registration_digest is None:
        raise ProtocolError("settlement requires a registration binding")
    if record.state is TransactionState.COMMITTED:
        outcome = ChainOutcome.COMMITTED
    elif record.state is TransactionState.ROLLED_BACK:
        outcome = ChainOutcome.ROLLED_BACK
    else:
        raise ProtocolError("only a terminal transaction can settle")

    with _registered_root(lease) as (chain_fd, validated):
        matching = [
            (digest, entry)
            for digest, entry in validated.entries
            if type(entry) is SettledEntry and entry.txid == approved.txid
        ]
        if len(matching) > 1:
            raise ChainStateInvalid("the chain contains duplicate settlement entries")
        if record.settlement_digest is not None:
            if not matching or matching[0][0] != record.settlement_digest:
                raise ChainStateInvalid(
                    "the settlement binding does not resolve to this transaction"
                )
            entry = cast(SettledEntry, matching[0][1])
            if entry.registration != record.registration_digest or entry.outcome is not outcome:
                raise ChainStateInvalid("the bound settlement contradicts the durable record")
            return
        if matching:
            digest, entry = matching[0]
            settled = cast(SettledEntry, entry)
            if settled.registration != record.registration_digest or settled.outcome is not outcome:
                raise ChainStateInvalid("the unbound settlement contradicts the durable record")
        else:
            digest = append_entry(
                cast(AuditedBackend, lease._binding.backend),
                chain_fd,
                validated,
                SettledEntry(
                    txid=approved.txid,
                    registration=record.registration_digest,
                    outcome=outcome,
                ),
            )
    with lease._store.transaction() as txn:
        txn.set_settlement_digest(approved.txid, digest)
