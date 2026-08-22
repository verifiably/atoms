"""The post-recovery pending gate (design §10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from atoms.chain.errors import PendingUnresolved
from atoms.chain.inspect import WellFormedChain
from atoms.chain.model import RegisteredEntry, SettledEntry, decode_entry
from atoms.coordinator.commands import (
    append_intent,
    capture_states,
    inspect_chain,
    inspect_chain_detached,
    read_chain,
    register_root,
    run_transaction,
)
from atoms.core.fingerprint import DirectoryState
from atoms.core.scratch import CHAIN_LEAF
from atoms.fs.linux import LinuxBackend
from tests.capture_support import DictPayloads, digest_of
from tests.coordinator_support import AFTER, create_file_spec
from tests.test_coordinator_commands import (
    _durable_entries,
    _enable_commands,
    _register,
)


def _unsettle(project_root: str) -> tuple[str, str]:
    """Remove the settlement leaf by hand, leaving evidence-starved pending work."""
    registration: tuple[str, str] | None = None
    settlement: str | None = None
    for name, payload in _durable_entries(project_root).items():
        entry = decode_entry(payload)[1]
        if type(entry) is RegisteredEntry:
            registration = (entry.txid, name)
        elif type(entry) is SettledEntry:
            settlement = name
    assert registration is not None and settlement is not None
    (Path(project_root) / CHAIN_LEAF / settlement).unlink()
    return registration


def _run(ingredients):
    backend, project_root, metadata_root, storage = ingredients
    return run_transaction(
        backend,
        project_root,
        metadata_root,
        storage,
        create_file_spec(),
        DictPayloads({digest_of(AFTER): AFTER}),
    )


def test_the_three_mutators_refuse_an_unsettled_chain(coordinator_on, monkeypatch) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    (Path(project_root) / "d").mkdir()
    _register(ingredients, b"root", ())
    _run(ingredients)
    pending = _unsettle(project_root)

    with pytest.raises(PendingUnresolved, match="unsettled registrations"):
        register_root(backend, project_root, metadata_root, storage, b"root", ())
    with pytest.raises(PendingUnresolved):
        append_intent(backend, project_root, metadata_root, storage, b"an intent")
    with pytest.raises(PendingUnresolved):
        _run(ingredients)

    view = read_chain(backend, project_root, metadata_root, storage)
    inspected = inspect_chain(backend, project_root, metadata_root, storage)
    detached = inspect_chain_detached(LinuxBackend(), project_root)
    captured = capture_states(backend, project_root, ("d",))

    assert view.tip == pending[1]
    assert type(inspected) is WellFormedChain and inspected.pending == (pending,)
    assert type(detached) is WellFormedChain and detached.pending == (pending,)
    assert [path for path, _ in captured] == ["d"]
    assert type(captured[0][1]) is DirectoryState


def test_a_settled_chain_refuses_none_of_the_seven(coordinator_on, monkeypatch) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    (Path(project_root) / "d").mkdir()
    genesis = _register(ingredients, b"root", ())
    _run(ingredients)

    assert (
        register_root(backend, project_root, metadata_root, storage, b"root", ())
        == genesis
    )
    assert append_intent(backend, project_root, metadata_root, storage, b"an intent")
    assert read_chain(backend, project_root, metadata_root, storage).tip
    inspected = inspect_chain(backend, project_root, metadata_root, storage)
    assert type(inspected) is WellFormedChain and inspected.pending == ()
    assert type(inspect_chain_detached(LinuxBackend(), project_root)) is WellFormedChain
    assert capture_states(backend, project_root, ("d",))


def test_the_gate_is_post_recovery_and_admits_an_interrupted_transaction(
    coordinator_on, monkeypatch
) -> None:
    """Design §10.1: a registration recovery can still settle is not evidence-starved."""
    from atoms.coordinator import execute
    from atoms.core.recovery import CommitDecision, TransactionState

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    (Path(project_root) / "d").mkdir()
    _register(ingredients, b"root", ())
    finalize_commit = execute.finalize_commit
    armed = True

    def cut_once(lease, approved, table, chain_fd):
        nonlocal armed
        if not armed:
            return finalize_commit(lease, approved, table, chain_fd)
        armed = False
        with lease._store.transaction() as txn:
            txn.set_commit_decision(approved.txid, CommitDecision.COMMITTED)
            txn.set_transaction_state(approved.txid, TransactionState.COMMITTED)
        raise RuntimeError("cut after committed")

    monkeypatch.setattr(execute, "finalize_commit", cut_once)
    with pytest.raises(RuntimeError, match="cut after committed"):
        _run(ingredients)

    detached = inspect_chain_detached(LinuxBackend(), project_root)
    assert type(detached) is WellFormedChain and len(detached.pending) == 1

    assert append_intent(backend, project_root, metadata_root, storage, b"an intent")

    inspected = inspect_chain(backend, project_root, metadata_root, storage)
    assert type(inspected) is WellFormedChain and inspected.pending == ()
