"""Committed-surface proof and irreversible commit completion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from atoms.chain.append import append_entry
from atoms.chain.model import ChainOutcome, SettledEntry
from atoms.chain.read import validate_chain
from atoms.coordinator.admission import _require_admitted
from atoms.coordinator.descriptors import DescriptorTable
from atoms.coordinator.effects.common import EffectMismatch
from atoms.coordinator.lease import Lease
from atoms.coordinator.recover import _observe_snapshot, run_plan
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.errors import ProtocolError, TransactionHalted
from atoms.core.fingerprint import AbsentState, DirectoryState, FileState, SymlinkState
from atoms.core.recovery import (
    CommitDecision,
    HaltPlan,
    ObservedAbsent,
    ObservedDirectory,
    ObservedFile,
    ObservedSymlink,
    ScratchRole,
    TransactionState,
    classify_recovery,
)
from atoms.fs.approval import ProjectApprovedSpec
from atoms.fs.audit import AuditedBackend
from atoms.fs.observe import Observation


@dataclass(frozen=True, slots=True)
class _CommitResult:
    txid: str
    registration: str
    settlement: str


def _matches(entry, state) -> bool:
    if type(state) is AbsentState:
        return type(entry) is ObservedAbsent
    if type(state) is FileState:
        return type(entry) is ObservedFile and entry.state == state
    if type(state) is SymlinkState:
        return type(entry) is ObservedSymlink and entry.state == state
    if type(state) is DirectoryState:
        return (
            type(entry) is ObservedDirectory
            and entry.state == state
            and entry.has_unmodeled_child is False
        )
    return False


def verify_committed_surface(
    lease: Lease, approved: ProjectApprovedSpec, table: DescriptorTable
) -> None:
    _require_admitted(lease, approved)
    record = lease._store.read_active()
    if record is None or record.txid != approved.txid:
        raise ProtocolError("the proof's transaction is not active")
    backend = cast(AuditedBackend, lease._binding.backend)
    with Observation(backend) as observation:
        snapshot = _observe_snapshot(lease, approved, table, observation, record)

    persistent = {
        item.path: item.entry for item in snapshot.persistent_observations
    }
    for expected in approved.compiled.spec.final_surface:
        if not _matches(persistent[expected.path], expected.state):
            raise EffectMismatch("verify_final_surface", expected.path)

    scratch = {
        (item.effect_id, item.role): item.entry
        for item in snapshot.scratch_observations
    }
    for effect in approved.compiled.spec.effects:
        if type(effect) is ReplaceFile:
            entry = scratch[(effect.effect_id, ScratchRole.STAGING)]
            matches = _matches(entry, effect.pre)
        elif type(effect) is CreateFileNoClobber:
            matches = type(
                scratch[(effect.effect_id, ScratchRole.STAGING)]
            ) is ObservedAbsent
        elif type(effect) is DeletePath:
            entry = scratch[(effect.effect_id, ScratchRole.TOMBSTONE)]
            matches = _matches(entry, effect.pre)
        elif type(effect) is MoveNoClobber:
            entry = scratch[(effect.effect_id, ScratchRole.ANCHOR)]
            destination = persistent[effect.destination]
            matches = (
                _matches(entry, effect.source_pre)
                and type(entry) is ObservedFile
                and type(destination) is ObservedFile
                and entry.identity is destination.identity
            )
        elif type(effect) is CreateDirectory:
            matches = type(
                scratch[(effect.effect_id, ScratchRole.WORK)]
            ) is ObservedAbsent
        else:
            raise ProtocolError("the compiled effect is outside the closed variant set")
        if not matches:
            raise EffectMismatch("verify_scratch_surface", effect.effect_id)


def finalize_commit(
    lease: Lease,
    approved: ProjectApprovedSpec,
    table: DescriptorTable,
    chain_fd: int,
) -> _CommitResult:
    _require_admitted(lease, approved)
    with lease._store.transaction() as txn:
        txn.set_commit_decision(approved.txid, CommitDecision.COMMITTED)
        txn.set_transaction_state(approved.txid, TransactionState.COMMITTED)

    record = lease._store.read_active()
    if record is None or record.registration_digest is None:
        raise ProtocolError("committed transaction is missing its registration")
    backend = cast(AuditedBackend, lease._binding.backend)
    settlement = append_entry(
        backend,
        chain_fd,
        validate_chain(backend, chain_fd),
        SettledEntry(
            txid=approved.txid,
            registration=record.registration_digest,
            outcome=ChainOutcome.COMMITTED,
        ),
    )
    with lease._store.transaction() as txn:
        txn.set_settlement_digest(approved.txid, settlement)

    record = lease._store.read_active()
    if record is None:
        raise ProtocolError("the committed transaction detached before cleanup")
    with Observation(backend) as observation:
        snapshot = _observe_snapshot(lease, approved, table, observation, record)
    result = run_plan(lease, approved, table, classify_recovery(snapshot))
    if type(result) is HaltPlan:
        raise TransactionHalted(result.diagnostic)
    return _CommitResult(
        approved.txid, cast(str, record.registration_digest), settlement
    )
