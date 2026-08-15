"""Public forward transaction execution."""

from __future__ import annotations

import sqlite3
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from atoms.chain.model import ChainOutcome, RegisteredEntry, SettledEntry, decode_entry
from atoms.coordinator.commands import TransactionOutcome, run_transaction
from atoms.core.recovery import CommitDecision, TransactionState
from atoms.core.scratch import CHAIN_LEAF
from tests.capture_support import DictPayloads, digest_of
from tests.coordinator_support import (
    AFTER,
    BEFORE,
    create_file_spec,
    deep_directory_spec,
    delete_spec,
    directory_spec,
    move_spec,
    nested_directory_spec,
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


def test_run_transaction_creates_directly_nested_planned_directories(
    coordinator_on, monkeypatch
) -> None:
    """Authority §9.5: a fresh directory's descriptor is handed to its descendants.

    Both directories are absent at capture, so `_build_descriptor_table` can only stop
    at "data"; "data/records" becomes reachable exactly when the forward
    `CreateDirectory` for "data" publishes and hands its retained descriptor down.
    """
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    root = Path(project_root)
    (root / "index.txt").write_bytes(BEFORE)
    _register(ingredients, b"root", ())

    outcome = run_transaction(
        backend,
        project_root,
        metadata_root,
        storage,
        nested_directory_spec(),
        DictPayloads({digest_of(AFTER): AFTER}),
    )

    assert outcome.outcome is ChainOutcome.COMMITTED
    assert stat.S_IMODE((root / "data").stat().st_mode) == 0o755
    assert stat.S_IMODE((root / "data" / "records").stat().st_mode) == 0o755
    assert (root / "data" / "records" / "one.txt").read_bytes() == AFTER
    assert (root / "data" / "records" / "two.txt").read_bytes() == AFTER
    assert (root / "index.txt").read_bytes() == AFTER


def test_run_transaction_creates_a_three_level_planned_directory_chain(
    coordinator_on, monkeypatch
) -> None:
    """Depth beyond two: the handoff is a general mechanism, not a one-shot."""
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    root = Path(project_root)
    _register(ingredients, b"root", ())

    outcome = run_transaction(
        backend,
        project_root,
        metadata_root,
        storage,
        deep_directory_spec(),
        DictPayloads({digest_of(AFTER): AFTER}),
    )

    assert outcome.outcome is ChainOutcome.COMMITTED
    for relative in ("a", "a/b", "a/b/c"):
        assert stat.S_IMODE((root / relative).stat().st_mode) == 0o755
    assert (root / "a" / "b" / "c" / "f.txt").read_bytes() == AFTER


def test_caught_rollback_after_nested_creates_restores_the_initial_surface(
    coordinator_on, monkeypatch
) -> None:
    """The confirmed cascade: rollback must reach both nested directories.

    "data" is adopted and leaves `table.stops`, so `_roll_back`'s rescan never revisits
    it; only the forward registration of "data/records" keeps the inner directory in the
    table at all.
    """
    from atoms.coordinator import execute

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    root = Path(project_root)
    (root / "index.txt").write_bytes(BEFORE)
    _register(ingredients, b"root", ())

    def fail(*args, **kwargs):
        raise RuntimeError("cut after the nested creates")

    monkeypatch.setattr(execute.replace_file, "apply", fail)
    with pytest.raises(RuntimeError, match="cut after the nested creates"):
        run_transaction(
            backend,
            project_root,
            metadata_root,
            storage,
            nested_directory_spec(),
            DictPayloads({digest_of(AFTER): AFTER}),
        )

    assert not (root / "data").exists()
    assert (root / "index.txt").read_bytes() == BEFORE
    # The whole project surface, not just the declared paths: the only survivor beside
    # the restored file is the chain the engine owns. No staging or tombstone leaf, and
    # no half-created directory, is left anywhere under the root.
    assert {path.name for path in root.iterdir()} == {CHAIN_LEAF, "index.txt"}
    # The slot itself outlives the rollback by design -- the orphan sweep reclaims it
    # once the record is gone -- but nothing the mkdirs built may still be inside it.
    work = Path(metadata_root) / "work"
    assert [entry.name for entry in work.iterdir()] != []
    assert list(work.rglob("*/*")) == []
    with sqlite3.connect(Path(metadata_root) / "atoms.db") as connection:
        state = connection.execute(
            "SELECT state FROM transaction_record ORDER BY rowid DESC LIMIT 1"
        ).fetchone()[0]
        assert connection.execute("SELECT COUNT(*) FROM active").fetchone()[0] == 0
    assert state == TransactionState.ROLLED_BACK.value
    decoded = [decode_entry(value)[1] for value in _durable_entries(project_root).values()]
    settled = next(item for item in decoded if type(item) is SettledEntry)
    assert settled.outcome is ChainOutcome.ROLLED_BACK


def test_caught_rollback_resumes_the_descent_into_a_published_directory(
    coordinator_on, monkeypatch
) -> None:
    """`_roll_back`'s rescan, on a stop that IS occupied by a directory.

    The cut lands between publication and adoption of "a/b": the directory is on disk,
    but nothing adopted its descriptor, so its forward-registered stop survives into
    `_roll_back`. The rescan re-observes it as a directory and resumes the descent,
    which adopts it and registers "a/b/c" -- the same helper, reached from the rollback
    route rather than the forward one.
    """
    from atoms.coordinator import execute

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    root = Path(project_root)
    _register(ingredients, b"root", ())
    apply = execute.create_directory.apply
    applied = {"n": 0}

    def apply_then_cut(backend, site, effect, *, gate):
        fd = apply(backend, site, effect, gate=gate)
        applied["n"] += 1
        if applied["n"] < 2:
            return fd
        # Exactly the boundary the stop must survive: published, never adopted.
        backend.close_fd(fd)
        raise RuntimeError("cut between publication and adoption")

    monkeypatch.setattr(execute.create_directory, "apply", apply_then_cut)
    with pytest.raises(RuntimeError, match="cut between publication and adoption"):
        run_transaction(
            backend,
            project_root,
            metadata_root,
            storage,
            deep_directory_spec(),
            DictPayloads({digest_of(AFTER): AFTER}),
        )

    assert applied["n"] == 2
    assert {path.name for path in root.iterdir()} == {CHAIN_LEAF}
    with sqlite3.connect(Path(metadata_root) / "atoms.db") as connection:
        state = connection.execute(
            "SELECT state FROM transaction_record ORDER BY rowid DESC LIMIT 1"
        ).fetchone()[0]
    assert state == TransactionState.ROLLED_BACK.value


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
