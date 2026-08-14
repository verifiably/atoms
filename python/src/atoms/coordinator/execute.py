"""Forward execution with recovery-model rollback on every caught failure."""

from __future__ import annotations

import sqlite3
from typing import cast

from atoms.chain.append import append_entry
from atoms.chain.errors import ChainStateInvalid
from atoms.chain.read import ValidatedChain, validate_chain
from atoms.coordinator.admission import _require_admitted, admit
from atoms.coordinator.capture import PayloadSource, capture_initial_surface
from atoms.coordinator.commit import _CommitResult, finalize_commit, verify_committed_surface
from atoms.coordinator.descriptors import (
    DescriptorTable,
    WalkStop,
    _directory_paths,
    _modeled_children,
    _resume_descent,
)
from atoms.coordinator.effects import create_directory, create_file, delete_path, move, replace_file
from atoms.coordinator.effects.common import EffectMismatch
from atoms.coordinator.effects.sites import (
    CreateFileSite,
    DeleteSite,
    MkdirSite,
    MoveSite,
    ReplaceSite,
    _site_for,
)
from atoms.coordinator.lease import Lease
from atoms.coordinator.prepare import open_workspace, prepare_transaction
from atoms.coordinator.recover import (
    _derive_reconciliation,
    _observe_snapshot,
    _perform_reconciliation,
    _registration_entry,
    run_plan,
)
from atoms.core.compiler import CompiledSpec
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    Effect,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.errors import PreconditionRefused, ProtocolError, TransactionHalted
from atoms.core.recovery import (
    HaltPlan,
    JournalState,
    ObservedDirectory,
    PersistentNode,
    TransactionState,
    classify_recovery,
)
from atoms.fs.approval import ProjectApprovedSpec
from atoms.fs.audit import AuditedBackend
from atoms.fs.observe import Observation
from atoms.store.errors import MetadataStoreInvalid


def _apply_effect(
    lease: Lease,
    approved: ProjectApprovedSpec,
    table: DescriptorTable,
    effect: Effect,
) -> None:
    backend = cast(AuditedBackend, lease._binding.backend)
    site = _site_for(approved, table, effect)
    if type(effect) is ReplaceFile and type(site) is ReplaceSite:
        replace_file.apply(backend, lease._store, site, effect)
    elif type(effect) is CreateFileNoClobber and type(site) is CreateFileSite:
        create_file.apply(backend, lease._store, site, effect)
    elif type(effect) is DeletePath and type(site) is DeleteSite:
        delete_path.apply(backend, site, effect)
    elif type(effect) is MoveNoClobber and type(site) is MoveSite:
        move.apply(backend, lease._store, site, effect)
    elif type(effect) is CreateDirectory and type(site) is MkdirSite:
        fd = create_directory.apply(
            backend,
            site,
            effect,
            gate=lambda: _require_admitted(lease, approved),
        )
        try:
            table.adopt(PersistentNode(effect.path), fd)
        except BaseException:
            backend.close_fd(fd)
            raise
    else:
        raise ProtocolError("effect and descriptor site variants disagree")


def _roll_back(
    lease: Lease,
    approved: ProjectApprovedSpec,
    table: DescriptorTable,
    chain_fd: int,
    caught: BaseException,
) -> None:
    del caught
    backend = cast(AuditedBackend, lease._binding.backend)
    validated = validate_chain(backend, chain_fd)
    record = lease._store.read_active()
    if record is None:
        raise ProtocolError("caught rollback has no active transaction")
    actions = _derive_reconciliation(record, validated)
    _perform_reconciliation(
        backend, lease._store, chain_fd, validated, actions
    )
    record = lease._store.read_active()
    if record is None:
        raise ProtocolError("registration reconciliation detached the transaction")

    with Observation(backend) as observation:
        paths = _directory_paths(approved)
        table._stops = tuple(
            WalkStop(
                stop.node,
                stop.path,
                stop.parent_fd,
                stop.component,
                observation.observe(
                    stop.parent_fd,
                    stop.component,
                    modeled=_modeled_children(paths, stop.node),
                ),
            )
            for stop in table.stops
        )
        for stop in tuple(table.stops):
            if type(stop.observed) is ObservedDirectory:
                _resume_descent(table, backend, observation, approved, stop.node)
        snapshot = _observe_snapshot(
            lease, approved, table, observation, record
        )
    result = run_plan(lease, approved, table, classify_recovery(snapshot))
    if type(result) is HaltPlan:
        raise TransactionHalted(result.diagnostic)


def _run_under_lease(
    lease: Lease,
    chain_fd: int,
    validated: ValidatedChain,
    compiled: CompiledSpec,
    payloads: PayloadSource,
) -> _CommitResult:
    approved = admit(lease, compiled)
    with open_workspace(lease, approved) as workspace, capture_initial_surface(
        lease, approved, workspace, payloads
    ) as captured:
        prepare_transaction(lease, approved, workspace, captured.manifest)
        backend = cast(AuditedBackend, lease._binding.backend)
        backend.set_declared_paths(
            frozenset(path.path for path in approved.paths)
        )
        try:
            try:
                registration = append_entry(
                    backend,
                    chain_fd,
                    validated,
                    _registration_entry(approved.compiled.spec, approved.txid),
                )
                with lease._store.transaction() as txn:
                    txn.set_registration_digest(approved.txid, registration)
                with lease._store.transaction() as txn:
                    txn.set_transaction_state(
                        approved.txid, TransactionState.APPLYING
                    )
                for effect in approved.compiled.spec.effects:
                    with lease._store.transaction() as txn:
                        txn.set_journal_state(
                            approved.txid, effect.effect_id, JournalState.STARTED
                        )
                    _apply_effect(lease, approved, captured.descriptors, effect)
                    with lease._store.transaction() as txn:
                        txn.set_journal_state(
                            approved.txid, effect.effect_id, JournalState.DONE
                        )
                with lease._store.transaction() as txn:
                    txn.set_transaction_state(
                        approved.txid, TransactionState.APPLIED
                    )
                verify_committed_surface(
                    lease, approved, captured.descriptors
                )
            except (ChainStateInvalid, MetadataStoreInvalid):
                raise
            except sqlite3.IntegrityError as caught:
                raise MetadataStoreInvalid(
                    f"the metadata store refused an executor transition: {caught}"
                ) from caught
            except BaseException as caught:
                _roll_back(
                    lease,
                    approved,
                    captured.descriptors,
                    chain_fd,
                    caught,
                )
                if isinstance(caught, (EffectMismatch, PreconditionRefused)):
                    raise PreconditionRefused(str(caught)) from caught
                raise
            return finalize_commit(
                lease, approved, captured.descriptors, chain_fd
            )
        finally:
            backend.clear_declared_paths()
