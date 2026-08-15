"""Fresh-lease recovery assembly."""

from __future__ import annotations

import os

import pytest

from atoms.chain.append import append_entry, bootstrap_chain
from atoms.chain.model import GenesisEntry
from atoms.chain.read import validate_chain
from atoms.coordinator.admission import admit
from atoms.coordinator.capture import capture_initial_surface
from atoms.coordinator.prepare import open_workspace, prepare_transaction
from atoms.coordinator.recover import _registration_entry
from atoms.core.compiler import compile_spec
from atoms.core.recovery import JournalState, TransactionState
from atoms.fs.audit import AuditedBackend
from tests.capture_support import DictPayloads, digest_of
from tests.coordinator_support import (
    AFTER,
    admission_for,
    compiled_creating_a_directory,
    deep_directory_spec,
)


def _register(lease, approved) -> None:
    backend = lease._binding.backend
    assert isinstance(backend, AuditedBackend)
    chain_fd = bootstrap_chain(backend, lease._binding.project_root_fd)
    try:
        append_entry(
            backend,
            chain_fd,
            validate_chain(backend, chain_fd),
            GenesisEntry(b"root", ()),
        )
        digest = append_entry(
            backend,
            chain_fd,
            validate_chain(backend, chain_fd),
            _registration_entry(approved.compiled.spec, approved.txid),
        )
    finally:
        backend.close_fd(chain_fd)
    with lease._store.transaction() as txn:
        txn.set_registration_digest(approved.txid, digest)


def _prepare_registered(lease) -> str:
    approved = admission_for(lease)
    with open_workspace(lease, approved) as workspace, capture_initial_surface(
        lease,
        approved,
        workspace,
        DictPayloads({digest_of(AFTER): AFTER}),
    ) as captured:
        prepare_transaction(lease, approved, workspace, captured.manifest)
    _register(lease, approved)
    return approved.txid


def _prepare_registered_directory(lease, compiled):
    approved = admit(lease, compiled)
    with open_workspace(lease, approved) as workspace, capture_initial_surface(
        lease,
        approved,
        workspace,
        DictPayloads({digest_of(AFTER): AFTER}),
    ) as captured:
        prepare_transaction(lease, approved, workspace, captured.manifest)
    _register(lease, approved)
    return approved


def test_prepared_transaction_rolls_back_settles_and_detaches_on_reentry(
    coordinator_on, leased
) -> None:
    ingredients = coordinator_on()
    with leased(ingredients) as lease:
        txid = _prepare_registered(lease)

    with leased(ingredients) as lease:
        assert lease._store.read_active() is None
        record = lease._store.read_record(txid)
        assert record is not None
        assert record.state is TransactionState.ROLLED_BACK
        assert record.settlement_digest is not None


def test_recovery_resumes_descent_through_a_created_planned_directory(
    coordinator_on, leased
) -> None:
    ingredients = coordinator_on()
    with leased(ingredients) as lease:
        approved = _prepare_registered_directory(
            lease, compiled_creating_a_directory(lease)
        )
        with lease._store.transaction() as txn:
            txn.set_transaction_state(approved.txid, TransactionState.APPLYING)
            txn.set_journal_state(approved.txid, "e1", JournalState.STARTED)
            txn.set_journal_state(approved.txid, "e1", JournalState.DONE)
        os.mkdir("d", 0o755, dir_fd=lease._binding.project_root_fd)
        txid = approved.txid

    with leased(ingredients) as lease:
        record = lease._store.read_record(txid)
        assert record is not None
        assert record.state is TransactionState.ROLLED_BACK
        with pytest.raises(FileNotFoundError):
            os.stat("d", dir_fd=lease._binding.project_root_fd)


def test_recovery_registers_the_planned_children_of_a_resumed_directory(
    coordinator_on, leased
) -> None:
    """The recovery half of `_register_planned_children`, with children to register.

    `test_recovery_resumes_descent_through_a_created_planned_directory` resumes into a
    directory that has no planned child, so it never enters the registration loop. Here
    "a" and "a/b" both exist when the fresh lease reopens: resuming into "a" must
    register "a/b" as a new stop, and resuming into THAT must register "a/b/c" -- the
    same helper the forward mkdir path calls, driven from the other direction and
    recursing past the first level.
    """
    ingredients = coordinator_on()
    with leased(ingredients) as lease:
        approved = _prepare_registered_directory(
            lease, compile_spec(deep_directory_spec())
        )
        with lease._store.transaction() as txn:
            txn.set_transaction_state(approved.txid, TransactionState.APPLYING)
            for effect_id in ("e1", "e2"):
                txn.set_journal_state(approved.txid, effect_id, JournalState.STARTED)
                txn.set_journal_state(approved.txid, effect_id, JournalState.DONE)
        root_fd = lease._binding.project_root_fd
        os.mkdir("a", 0o755, dir_fd=root_fd)
        os.mkdir("a/b", 0o755, dir_fd=root_fd)
        txid = approved.txid

    with leased(ingredients) as lease:
        record = lease._store.read_record(txid)
        assert record is not None
        assert record.state is TransactionState.ROLLED_BACK
        with pytest.raises(FileNotFoundError):
            os.stat("a", dir_fd=lease._binding.project_root_fd)


def test_foreign_file_at_a_planned_directory_reaches_recovery_classification(
    coordinator_on, leased
) -> None:
    ingredients = coordinator_on()
    with leased(ingredients) as lease:
        approved = _prepare_registered_directory(
            lease, compiled_creating_a_directory(lease)
        )
        fd = os.open(
            "d",
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o644,
            dir_fd=lease._binding.project_root_fd,
        )
        os.close(fd)
        txid = approved.txid

    with leased(ingredients) as lease:
        record = lease._store.read_record(txid)
        assert record is not None
        assert record.state is TransactionState.ROLLED_BACK
        info = os.stat("d", dir_fd=lease._binding.project_root_fd)
        assert info.st_size == 0
