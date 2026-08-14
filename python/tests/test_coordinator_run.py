"""Public forward transaction execution."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from atoms.chain.model import ChainOutcome, RegisteredEntry, SettledEntry, decode_entry
from atoms.coordinator.commands import TransactionOutcome, run_transaction
from atoms.core.recovery import CommitDecision, TransactionState
from tests.capture_support import DictPayloads, digest_of
from tests.coordinator_support import (
    AFTER,
    BEFORE,
    create_file_spec,
    delete_spec,
    directory_spec,
    move_spec,
    replace_spec,
)
from tests.test_coordinator_commands import _durable_entries, _enable_commands, _register


def test_run_transaction_commits_the_world_chain_and_public_outcome(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    (Path(project_root) / "d").mkdir()
    genesis = _register(ingredients, b"root", ())

    outcome = run_transaction(
        backend,
        project_root,
        metadata_root,
        storage,
        create_file_spec(),
        DictPayloads({digest_of(AFTER): AFTER}),
    )

    assert outcome == TransactionOutcome(
        outcome.txid,
        ChainOutcome.COMMITTED,
        outcome.registration,
        outcome.settlement,
    )
    assert (Path(project_root) / "d/f.txt").read_bytes() == AFTER
    entries = _durable_entries(project_root)
    registered_previous, registered = decode_entry(entries[outcome.registration])
    settled_previous, settled = decode_entry(entries[outcome.settlement])
    assert registered_previous == genesis
    assert type(registered) is RegisteredEntry and registered.txid == outcome.txid
    assert settled_previous == outcome.registration
    assert settled == SettledEntry(
        outcome.txid, outcome.registration, ChainOutcome.COMMITTED
    )


def test_run_transaction_executes_a_created_directory_before_its_child(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    _register(ingredients, b"root", ())

    outcome = run_transaction(
        backend,
        project_root,
        metadata_root,
        storage,
        directory_spec(),
        DictPayloads({digest_of(AFTER): AFTER}),
    )

    assert outcome.outcome is ChainOutcome.COMMITTED
    assert (Path(project_root) / "d/f.txt").read_bytes() == AFTER


def test_keyboard_interrupt_rolls_back_before_it_is_reraised(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator import execute

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    (Path(project_root) / "d").mkdir()
    _register(ingredients, b"root", ())

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(execute.create_file, "apply", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_transaction(
            backend,
            project_root,
            metadata_root,
            storage,
            create_file_spec(),
            DictPayloads({digest_of(AFTER): AFTER}),
        )

    assert not (Path(project_root) / "d/f.txt").exists()
    decoded = [decode_entry(value)[1] for value in _durable_entries(project_root).values()]
    settled = next(item for item in decoded if type(item) is SettledEntry)
    assert settled.outcome is ChainOutcome.ROLLED_BACK
    with sqlite3.connect(Path(metadata_root) / "atoms.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM active").fetchone()[0] == 0


def test_failure_after_the_commit_decision_never_enters_rollback(
    coordinator_on, leased, monkeypatch
) -> None:
    from atoms.coordinator import execute

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    (Path(project_root) / "d").mkdir()
    _register(ingredients, b"root", ())

    def cut_after_commit(lease, approved, table, chain_fd):
        del table, chain_fd
        with lease._store.transaction() as txn:
            txn.set_commit_decision(approved.txid, CommitDecision.COMMITTED)
            txn.set_transaction_state(approved.txid, TransactionState.COMMITTED)
        raise RuntimeError("cut after committed")

    monkeypatch.setattr(execute, "finalize_commit", cut_after_commit)
    with pytest.raises(RuntimeError, match="cut after committed"):
        run_transaction(
            backend,
            project_root,
            metadata_root,
            storage,
            create_file_spec(),
            DictPayloads({digest_of(AFTER): AFTER}),
        )
    assert (Path(project_root) / "d/f.txt").read_bytes() == AFTER

    with leased(ingredients) as lease:
        assert lease._store.read_active() is None
    decoded = [decode_entry(value)[1] for value in _durable_entries(project_root).values()]
    settled = next(item for item in decoded if type(item) is SettledEntry)
    assert settled.outcome is ChainOutcome.COMMITTED


def test_caught_failure_before_registration_is_reconciled_then_rolled_back(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator import execute

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    (Path(project_root) / "d").mkdir()
    _register(ingredients, b"root", ())

    def cut_before_registration(*args, **kwargs):
        raise RuntimeError("cut before registration")

    monkeypatch.setattr(execute, "append_entry", cut_before_registration)
    with pytest.raises(RuntimeError, match="cut before registration"):
        run_transaction(
            backend,
            project_root,
            metadata_root,
            storage,
            create_file_spec(),
            DictPayloads({digest_of(AFTER): AFTER}),
        )

    decoded = [decode_entry(value)[1] for value in _durable_entries(project_root).values()]
    assert sum(type(item) is RegisteredEntry for item in decoded) == 1
    settled = next(item for item in decoded if type(item) is SettledEntry)
    assert settled.outcome is ChainOutcome.ROLLED_BACK


def test_registration_carries_the_specs_consumer_intent_and_surface_projection(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    (Path(project_root) / "d").mkdir()
    _register(ingredients, b"root", ())
    spec = replace(
        create_file_spec(),
        consumer_tag="consumer-x",
        intent_digest="sha256:" + "a" * 64,
        fulfills="b" * 64,
        registered_paths=("d/f.txt",),
    )

    outcome = run_transaction(
        backend,
        project_root,
        metadata_root,
        storage,
        spec,
        DictPayloads({digest_of(AFTER): AFTER}),
    )

    registered = decode_entry(
        _durable_entries(project_root)[outcome.registration]
    )[1]
    assert type(registered) is RegisteredEntry
    assert registered.consumer_tag == "consumer-x"
    assert registered.intent_digest == "sha256:" + "a" * 64
    assert registered.fulfills == "b" * 64
    assert tuple(path for path, _ in registered.initial) == ("d/f.txt",)
    assert tuple(path for path, _ in registered.final) == ("d/f.txt",)


@pytest.mark.parametrize("variant", ["replace", "delete", "move"])
def test_run_transaction_commits_and_cleans_the_existing_entry_variants(
    coordinator_on, monkeypatch, variant
) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    directory = Path(project_root) / "d"
    directory.mkdir()
    if variant == "move":
        (directory / "source.txt").write_bytes(BEFORE)
        spec = move_spec()
        payloads = DictPayloads({})
    else:
        (directory / "f.txt").write_bytes(BEFORE)
        spec = replace_spec() if variant == "replace" else delete_spec()
        payloads = (
            DictPayloads({digest_of(AFTER): AFTER})
            if variant == "replace"
            else DictPayloads({})
        )
    _register(ingredients, b"root", ())

    outcome = run_transaction(
        backend, project_root, metadata_root, storage, spec, payloads
    )

    assert outcome.outcome is ChainOutcome.COMMITTED
    if variant == "replace":
        assert (directory / "f.txt").read_bytes() == AFTER
    elif variant == "delete":
        assert not (directory / "f.txt").exists()
    else:
        assert not (directory / "source.txt").exists()
        assert (directory / "destination.txt").read_bytes() == BEFORE
    assert all(not path.name.startswith(".#~") for path in directory.iterdir())
