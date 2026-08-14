"""Executor terminal states agree with A3's abstract reducer."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from atoms.coordinator.commands import run_transaction
from atoms.core.recovery import apply_recovery_plan
from atoms.fs.binding import bind_project_volume
from atoms.fs.lock import acquire_project_lock
from atoms.store.connection import open_store
from tests.capture_support import DictPayloads, digest_of
from tests.coordinator_support import AFTER, create_file_spec
from tests.fs_support import build_test_allowlist
from tests.test_coordinator_commands import _enable_commands, _register


def _txid(metadata_root: str) -> str:
    with sqlite3.connect(Path(metadata_root) / "atoms.db") as connection:
        return connection.execute(
            "SELECT txid FROM transaction_record ORDER BY rowid DESC LIMIT 1"
        ).fetchone()[0]


def _durable_projection(ingredients, txid):
    backend, project_root, metadata_root, storage = ingredients
    with acquire_project_lock(backend, metadata_root) as lock:
        allowlist = build_test_allowlist(lock, project_root, storage)
        with bind_project_volume(
            project_root, lock, allowlist=allowlist, storage=storage
        ) as binding, open_store(binding) as store:
            record = store.read_record(txid)
            active = store.read_active()
    assert record is not None
    return (
        record.state,
        record.committed,
        record.rollback_result,
        record.journals,
        active is not None,
    )


def _model_projection(snapshot, plan):
    reduced = apply_recovery_plan(snapshot, plan)
    return (
        reduced.transaction_state,
        reduced.commit_decision,
        reduced.rollback_result,
        reduced.journals,
        reduced.active,
    )


def test_clean_commit_reaches_the_a3_fixed_point(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator import commit

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    (Path(project_root) / "d").mkdir()
    _register(ingredients, b"root", ())
    classified = []
    classify = commit.classify_recovery

    def capture(snapshot):
        plan = classify(snapshot)
        classified.append((snapshot, plan))
        return plan

    monkeypatch.setattr(commit, "classify_recovery", capture)
    outcome = run_transaction(
        backend,
        project_root,
        metadata_root,
        storage,
        create_file_spec(),
        DictPayloads({digest_of(AFTER): AFTER}),
    )

    assert len(classified) == 1
    assert _durable_projection(ingredients, outcome.txid) == _model_projection(
        *classified[0]
    )


def test_caught_rollback_reaches_the_a3_fixed_point(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator import execute

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    (Path(project_root) / "d").mkdir()
    _register(ingredients, b"root", ())
    classified = []
    classify = execute.classify_recovery

    def capture(snapshot):
        plan = classify(snapshot)
        classified.append((snapshot, plan))
        return plan

    def fail(*args, **kwargs):
        raise RuntimeError("caught rollback")

    monkeypatch.setattr(execute, "classify_recovery", capture)
    monkeypatch.setattr(execute.create_file, "apply", fail)
    with pytest.raises(RuntimeError, match="caught rollback"):
        run_transaction(
            backend,
            project_root,
            metadata_root,
            storage,
            create_file_spec(),
            DictPayloads({digest_of(AFTER): AFTER}),
        )

    assert len(classified) == 1
    assert _durable_projection(ingredients, _txid(metadata_root)) == _model_projection(
        *classified[0]
    )
