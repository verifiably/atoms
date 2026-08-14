"""Committed-surface proof and catch-boundary checks."""

from __future__ import annotations

import errno
import os
import sqlite3
from pathlib import Path

import pytest

from atoms.coordinator.commands import run_transaction
from atoms.coordinator.effects.common import EffectMismatch
from atoms.core.errors import PreconditionRefused, TransactionHalted
from atoms.core.recovery import CommitDecision, TransactionState
from tests.capture_support import DictPayloads, digest_of
from tests.coordinator_support import AFTER, BEFORE, POST, PRE, create_file_spec, replace_spec
from tests.test_coordinator_commands import _enable_commands, _register


def _active_record(metadata_root: str):
    with sqlite3.connect(Path(metadata_root) / "atoms.db") as connection:
        return connection.execute(
            "SELECT state, committed FROM transaction_record"
        ).fetchone()


def test_committed_proof_checks_final_surface_before_scratch(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator import commit

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    directory = Path(project_root) / "d"
    directory.mkdir()
    (directory / "f.txt").write_bytes(BEFORE)
    _register(ingredients, b"root", ())
    matched = []
    original = commit._matches

    def fail_scratch(entry, state):
        matched.append(state)
        return False if state == PRE else original(entry, state)

    monkeypatch.setattr(commit, "_matches", fail_scratch)
    with pytest.raises(PreconditionRefused) as caught:
        run_transaction(
            backend,
            project_root,
            metadata_root,
            storage,
            replace_spec(),
            DictPayloads({digest_of(AFTER): AFTER}),
        )

    assert isinstance(caught.value.__cause__, EffectMismatch)
    assert matched[:2] == [POST, PRE]
    assert (directory / "f.txt").read_bytes() == BEFORE


def test_commit_decision_and_state_share_one_store_commit(
    coordinator_on, monkeypatch
) -> None:
    from atoms.store.connection import _StoreTransaction

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    (Path(project_root) / "d").mkdir()
    _register(ingredients, b"root", ())
    barriers = 0
    commit_transactions = []
    committed_states = []
    barrier = _StoreTransaction._run_barrier
    set_decision = _StoreTransaction.set_commit_decision
    set_state = _StoreTransaction.set_transaction_state

    def record_decision(self, txid, decision):
        if decision is CommitDecision.COMMITTED:
            commit_transactions.append(self)
        return set_decision(self, txid, decision)

    def record_state(self, txid, state):
        if state is TransactionState.COMMITTED:
            committed_states.append(self)
        return set_state(self, txid, state)

    def count_committed(self):
        nonlocal barriers
        if commit_transactions and self is commit_transactions[0]:
            barriers += 1
        return barrier(self)

    monkeypatch.setattr(_StoreTransaction, "set_commit_decision", record_decision)
    monkeypatch.setattr(_StoreTransaction, "set_transaction_state", record_state)
    monkeypatch.setattr(_StoreTransaction, "_run_barrier", count_committed)
    run_transaction(
        backend,
        project_root,
        metadata_root,
        storage,
        create_file_spec(),
        DictPayloads({digest_of(AFTER): AFTER}),
    )

    assert commit_transactions == committed_states
    assert len(commit_transactions) == barriers == 1


def test_replace_namespace_race_is_refused_only_after_restoration(
    coordinator_on, monkeypatch
) -> None:
    from atoms.fs.audit import AuditedBackend

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    directory = Path(project_root) / "d"
    directory.mkdir()
    live = directory / "f.txt"
    live.write_bytes(BEFORE)
    _register(ingredients, b"root", ())
    exchange = AuditedBackend.exchange
    armed = True

    def refuse_exchange(self, parent_fd, left, right):
        nonlocal armed
        if armed and left == "f.txt":
            armed = False
            raise OSError(errno.ENOENT, "injected namespace race")
        return exchange(self, parent_fd, left, right)

    monkeypatch.setattr(AuditedBackend, "exchange", refuse_exchange)
    with pytest.raises(PreconditionRefused) as caught:
        run_transaction(
            backend,
            project_root,
            metadata_root,
            storage,
            replace_spec(),
            DictPayloads({digest_of(AFTER): AFTER}),
        )

    assert isinstance(caught.value.__cause__, EffectMismatch)
    assert live.read_bytes() == BEFORE


def test_unrecognized_entry_during_caught_rollback_persists_a_halt(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator import execute

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    directory = Path(project_root) / "d"
    directory.mkdir()
    _register(ingredients, b"root", ())
    apply = execute.create_file.apply

    def publish_fifo(*args, **kwargs):
        apply(*args, **kwargs)
        live = directory / "f.txt"
        live.unlink()
        os.mkfifo(live)
        raise RuntimeError("force caught rollback")

    monkeypatch.setattr(execute.create_file, "apply", publish_fifo)
    with pytest.raises(TransactionHalted):
        run_transaction(
            backend,
            project_root,
            metadata_root,
            storage,
            create_file_spec(),
            DictPayloads({digest_of(AFTER): AFTER}),
        )

    assert _active_record(metadata_root)[0] == TransactionState.HALTED.value
