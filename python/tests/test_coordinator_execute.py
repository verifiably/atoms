"""Forward-spine failure boundaries."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from atoms.chain.errors import ChainStateInvalid
from atoms.chain.model import ChainOutcome, SettledEntry, decode_entry
from atoms.coordinator.commands import run_transaction
from atoms.core.errors import PreconditionRefused
from atoms.core.recovery import TransactionState
from atoms.store.errors import MetadataStoreInvalid
from tests.capture_support import DictPayloads, digest_of
from tests.coordinator_support import AFTER, create_file_spec
from tests.test_coordinator_commands import _durable_entries, _enable_commands, _register


def _record(metadata_root: str):
    with sqlite3.connect(Path(metadata_root) / "atoms.db") as connection:
        return connection.execute(
            "SELECT state, registration_digest, settlement_digest FROM transaction_record"
        ).fetchone()


def test_unregistered_root_refuses_before_a_transaction_write(
    coordinator_on, leased, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    (Path(project_root) / "d").mkdir()
    with leased(ingredients):
        pass
    database = Path(metadata_root) / "atoms.db"
    before = database.read_bytes()

    with pytest.raises(PreconditionRefused):
        run_transaction(
            backend,
            project_root,
            metadata_root,
            storage,
            create_file_spec(),
            DictPayloads({digest_of(AFTER): AFTER}),
        )

    assert database.read_bytes() == before
    assert _record(metadata_root) is None


def test_substrate_invalid_failure_preserves_the_applying_record(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator import execute

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    (Path(project_root) / "d").mkdir()
    _register(ingredients, b"root", ())

    def fail(*args, **kwargs):
        raise ChainStateInvalid("injected chain failure")

    monkeypatch.setattr(execute.create_file, "apply", fail)
    with pytest.raises(ChainStateInvalid, match="injected chain failure"):
        run_transaction(
            backend,
            project_root,
            metadata_root,
            storage,
            create_file_spec(),
            DictPayloads({digest_of(AFTER): AFTER}),
        )

    state, registration, settlement = _record(metadata_root)
    assert state == TransactionState.APPLYING.value
    assert registration is not None
    assert settlement is None
    assert not any(
        type(decode_entry(value)[1]) is SettledEntry
        for value in _durable_entries(project_root).values()
    )


def test_caught_rollback_uses_one_observation_universe(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator import execute

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    (Path(project_root) / "d").mkdir()
    _register(ingredients, b"root", ())
    constructed = 0
    original = execute.Observation

    class CountingObservation(original):
        def __init__(self, backend):
            nonlocal constructed
            constructed += 1
            super().__init__(backend)

    def fail(*args, **kwargs):
        raise RuntimeError("injected forward failure")

    monkeypatch.setattr(execute, "Observation", CountingObservation)
    monkeypatch.setattr(execute.create_file, "apply", fail)
    with pytest.raises(RuntimeError, match="injected forward failure"):
        run_transaction(
            backend,
            project_root,
            metadata_root,
            storage,
            create_file_spec(),
            DictPayloads({digest_of(AFTER): AFTER}),
        )

    assert constructed == 1
    state, registration, settlement = _record(metadata_root)
    assert state == TransactionState.ROLLED_BACK.value
    assert registration is not None and settlement is not None


def test_exception_after_registration_publication_is_backfilled_before_rollback(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator import execute

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    (Path(project_root) / "d").mkdir()
    _register(ingredients, b"root", ())
    append = execute.append_entry
    armed = True

    def append_then_fail(*args, **kwargs):
        nonlocal armed
        digest = append(*args, **kwargs)
        if armed:
            armed = False
            raise RuntimeError("cut after registration publication")
        return digest

    monkeypatch.setattr(execute, "append_entry", append_then_fail)
    with pytest.raises(RuntimeError, match="cut after registration publication"):
        run_transaction(
            backend,
            project_root,
            metadata_root,
            storage,
            create_file_spec(),
            DictPayloads({digest_of(AFTER): AFTER}),
        )

    state, registration, settlement = _record(metadata_root)
    assert state == TransactionState.ROLLED_BACK.value
    assert registration is not None and settlement is not None
    settled = [
        decode_entry(value)[1] for value in _durable_entries(project_root).values()
    ]
    assert any(
        type(entry) is SettledEntry and entry.outcome is ChainOutcome.ROLLED_BACK
        for entry in settled
    )


def test_schema_refuses_applying_when_registration_binding_is_dropped(
    coordinator_on, monkeypatch
) -> None:
    from atoms.store.connection import _StoreTransaction

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    (Path(project_root) / "d").mkdir()
    _register(ingredients, b"root", ())
    monkeypatch.setattr(
        _StoreTransaction, "set_registration_digest", lambda self, txid, digest: None
    )

    with pytest.raises(MetadataStoreInvalid):
        run_transaction(
            backend,
            project_root,
            metadata_root,
            storage,
            create_file_spec(),
            DictPayloads({digest_of(AFTER): AFTER}),
        )

    state, registration, settlement = _record(metadata_root)
    assert state == TransactionState.PREPARED.value
    assert registration is None and settlement is None
