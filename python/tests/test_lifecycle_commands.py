"""Root lifecycle: fail-closed writer state, binding, and the state query.

Contract: docs/2026-08-23-root-lifecycle-commands-design.md (approved at
`b1469f4`). Tier 1 here is the writer-state half — the grant carrier, the
host/root binding, the closed five-value union, and the writability gate in
front of every cooperative mutator. The copy commands, serviceability grant,
and migration are the later tiers of this same file.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from atoms.chain.errors import PendingUnresolved
from atoms.coordinator.commands import (
    LifecycleState,
    append_intent,
    read_lifecycle_state,
)
from atoms.core.errors import PreconditionRefused
from tests.test_coordinator_commands import _enable_commands, _register

GENESIS = b"lifecycle genesis payload"

_OTHER_MACHINE = "f" * 32


def _read_state(ingredients) -> LifecycleState:
    backend, project_root, metadata_root, storage = ingredients
    return read_lifecycle_state(backend, project_root, metadata_root, storage)


def _interrupt_before_grant(ingredients, monkeypatch) -> None:
    """Fabricate the recorded-operation/durable-genesis/no-grant window.

    The cut is the completion seam itself: everything before the final
    lifecycle/complete transaction has run for real — claim, stamp, baseline,
    genesis, tree proof — so the residue is exactly what a crash there leaves.
    """
    from atoms.coordinator import lifecycle

    real = lifecycle._complete_root_operation

    class Interrupted(BaseException):
        pass

    def cut(*args, **kwargs):
        raise Interrupted("cut before the grant")

    monkeypatch.setattr(lifecycle, "_complete_root_operation", cut)
    with pytest.raises(Interrupted):
        _register(ingredients, GENESIS, ())
    monkeypatch.setattr(lifecycle, "_complete_root_operation", real)


def _metadata_entries(metadata_root: str) -> dict[str, tuple[int, int, str]]:
    """Every entry under the metadata root with size, mtime, and content hash."""
    observed: dict[str, tuple[int, int, str]] = {}
    for directory, directories, files in os.walk(metadata_root):
        directories.sort()
        for name in sorted(files):
            full = Path(directory) / name
            info = full.lstat()
            observed[os.path.relpath(full, metadata_root)] = (
                info.st_size,
                info.st_mtime_ns,
                hashlib.sha256(full.read_bytes()).hexdigest(),
            )
    return observed


def test_fresh_register_root_reads_writable(coordinator_on, monkeypatch) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    digest = _register(ingredients, GENESIS, ())
    assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")
    assert _read_state(ingredients) is LifecycleState.WRITABLE


def test_interrupted_registration_matching_retry_grants(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _interrupt_before_grant(ingredients, monkeypatch)
    assert _read_state(ingredients) is LifecycleState.READ_ONLY_UNSERVICEABLE

    digest = _register(ingredients, GENESIS, ())
    assert _read_state(ingredients) is LifecycleState.WRITABLE
    # The retry completed the recorded operation rather than re-minting: the
    # digest is the interrupted attempt's own durable genesis.
    assert digest == _register(ingredients, GENESIS, ())


def test_bare_reregistration_over_an_existing_genesis_never_grants(
    coordinator_on, monkeypatch
) -> None:
    import shutil

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _register(ingredients, GENESIS, ())
    _backend, _project_root, metadata_root, _storage = ingredients
    shutil.rmtree(metadata_root)

    assert _read_state(ingredients) is LifecycleState.METADATA_LESS
    with pytest.raises(
        PreconditionRefused, match="no local initialization operation"
    ):
        _register(ingredients, GENESIS, ())
    assert _read_state(ingredients) is LifecycleState.METADATA_LESS


def test_metadata_less_tree_reads_metadata_less(coordinator_on, monkeypatch) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    assert _read_state(ingredients) is LifecycleState.METADATA_LESS


def test_host_delta_reads_binding_mismatched(coordinator_on, monkeypatch) -> None:
    from atoms.coordinator import lifecycle

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _register(ingredients, GENESIS, ())
    monkeypatch.setattr(
        lifecycle, "_read_machine_identity", lambda: _OTHER_MACHINE
    )
    assert _read_state(ingredients) is LifecycleState.BINDING_MISMATCHED


def test_path_delta_reads_binding_mismatched(coordinator_on, monkeypatch) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _register(ingredients, GENESIS, ())
    backend, project_root, metadata_root, storage = ingredients
    moved = project_root + "-moved"
    os.rename(project_root, moved)
    assert (
        read_lifecycle_state(backend, moved, metadata_root, storage)
        is LifecycleState.BINDING_MISMATCHED
    )


def test_lifecycle_union_has_exactly_five_members() -> None:
    assert {member.value for member in LifecycleState} == {
        "writable",
        "read-only-serviceable",
        "read-only-unserviceable",
        "metadata-less",
        "binding-mismatched",
    }
    assert len(LifecycleState) == 5


def test_nonwritable_mutation_refuses_before_recovery(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator import root as root_module

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _interrupt_before_grant(ingredients, monkeypatch)

    recovery_calls: list[str] = []
    monkeypatch.setattr(
        root_module,
        "resolve",
        lambda *args, **kwargs: recovery_calls.append("resolve"),
    )
    monkeypatch.setattr(
        root_module,
        "reclaim_probe_survivors",
        lambda *args, **kwargs: recovery_calls.append("reclaim"),
    )
    backend, project_root, metadata_root, storage = ingredients
    with pytest.raises(
        PreconditionRefused,
        match="read-only-unserviceable.*does not grant writability",
    ):
        append_intent(backend, project_root, metadata_root, storage, b"intent")
    assert recovery_calls == []


def test_writable_pending_root_reaches_pending_unresolved(
    coordinator_on, monkeypatch
) -> None:
    from atoms.coordinator.commands import run_transaction
    from tests.capture_support import DictPayloads, digest_of
    from tests.coordinator_support import AFTER, create_file_spec
    from tests.test_pending_gate import _unsettle

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    (Path(project_root) / "d").mkdir()
    _register(ingredients, GENESIS, ())
    run_transaction(
        backend,
        project_root,
        metadata_root,
        storage,
        create_file_spec(),
        DictPayloads({digest_of(AFTER): AFTER}),
    )
    _unsettle(project_root)

    assert _read_state(ingredients) is LifecycleState.WRITABLE
    # The lifecycle gate passes — the grant stands — and the refusal is the
    # chain's own, which is the ordering the gate-precedence rows rely on.
    with pytest.raises(PendingUnresolved):
        append_intent(backend, project_root, metadata_root, storage, b"intent")


def test_read_only_lifecycle_open_cannot_write_sqlite_or_sidecars(
    coordinator_on, monkeypatch
) -> None:
    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _register(ingredients, GENESIS, ())
    _backend, _project_root, metadata_root, _storage = ingredients

    before = _metadata_entries(metadata_root)
    assert "atoms.db-wal" not in before and "atoms.db-shm" not in before
    assert _read_state(ingredients) is LifecycleState.WRITABLE
    after = _metadata_entries(metadata_root)
    assert after == before


def test_v2_store_reads_read_only_unserviceable(coordinator_on, monkeypatch) -> None:
    """An exact pre-lifecycle store: bookkeeping exists, but no grant does."""
    from tests.lifecycle_support import fabricate_v2_store

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _backend, _project_root, metadata_root, _storage = ingredients
    fabricate_v2_store(metadata_root)
    assert _read_state(ingredients) is LifecycleState.READ_ONLY_UNSERVICEABLE


def test_normal_store_open_refuses_v2_and_names_the_migration(
    coordinator_on, monkeypatch
) -> None:
    from tests.lifecycle_support import fabricate_v2_store

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    backend, project_root, metadata_root, storage = ingredients
    fabricate_v2_store(metadata_root)
    with pytest.raises(
        PreconditionRefused, match="migrate_root_to_lifecycle_v3"
    ):
        _register(ingredients, GENESIS, ())
    # The mutator gate refuses first, in the gate's own words: a v2 store
    # holds bookkeeping but no grant.
    with pytest.raises(
        PreconditionRefused, match="read-only-unserviceable.*does not grant"
    ):
        append_intent(backend, project_root, metadata_root, storage, b"intent")


def test_operation_row_without_lifecycle_row_is_invalid(
    coordinator_on, monkeypatch
) -> None:
    """The atomic pair, broken by hand: a raw write, never a cooperative state."""
    from atoms.store.errors import MetadataStoreInvalid
    from tests.lifecycle_support import fabricate_v3_store

    ingredients = coordinator_on()
    _enable_commands(ingredients, monkeypatch)
    _backend, _project_root, metadata_root, _storage = ingredients
    database = fabricate_v3_store(metadata_root)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO root_operation (singleton, operation_id, kind, phase,"
            " request_json, request_hash) VALUES (0, ?, 'register', 'recorded',"
            " '{}', ?)",
            ("a" * 32, "b" * 64),
        )
    with pytest.raises(MetadataStoreInvalid):
        _read_state(ingredients)
