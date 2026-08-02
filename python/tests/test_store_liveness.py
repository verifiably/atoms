"""Design §5.4, §7.7, and §7.8 -- the liveness gate, transaction ownership, and close."""

from __future__ import annotations

import os
import sqlite3
import stat

import pytest

from atoms.core.errors import ProtocolError
from atoms.store.connection import open_store
from atoms.store.errors import MetadataStoreInvalid
from tests.store_support import (
    RELEASES,
    STORE_SURFACE,
    close_binding,
    metadata_root_snapshot,
    release_lock,
)


def test_open_store_creates_then_reopens(store_on):
    with store_on() as binding:
        with open_store(binding) as store, store.transaction():
            pass
        with open_store(binding) as store, store.transaction():
            pass


def test_a_nested_transaction_is_refused(opened_store):
    with opened_store.transaction(), pytest.raises(ProtocolError) as caught, opened_store.transaction():
        pass
    assert "nest" in str(caught.value).lower()


def test_a_retained_transaction_is_dead_after_commit(opened_store):
    with opened_store.transaction() as txn:
        pass
    with pytest.raises(ProtocolError):
        txn._require_current()


def test_a_retained_transaction_is_dead_after_rollback(opened_store):
    with pytest.raises(RuntimeError), opened_store.transaction() as txn:
        raise RuntimeError("caller failure")
    with pytest.raises(ProtocolError):
        txn._require_current()


def test_a_stale_transaction_cannot_write_into_a_later_one(opened_store):
    with opened_store.transaction() as first:
        pass
    with opened_store.transaction(), pytest.raises(ProtocolError) as caught:
        first._require_current()
    assert "spent" in str(caught.value).lower() or "current" in str(caught.value).lower()


def test_a_poisoned_transaction_refuses_to_commit(opened_store):
    """Design §7.7. The mechanism here; Task 7 proves it end to end through a real
    mutating method whose failure the caller catches."""
    with pytest.raises(ProtocolError) as caught, opened_store.transaction() as txn:
        txn._poison(RuntimeError("a mutating method raised"))
    assert "poison" in str(caught.value).lower()
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_a_poisoned_transaction_leaves_no_open_transaction(opened_store):
    with pytest.raises(ProtocolError), opened_store.transaction() as txn:
        txn._poison(RuntimeError("a mutating method raised"))
    with opened_store.transaction():
        pass


def test_close_is_idempotent(opened_store):
    opened_store.close()
    opened_store.close()


def test_close_rolls_back_and_spends_an_open_transaction(opened_store):
    """`__enter__` without `__exit__` on purpose: this isolates what `close()` itself
    does. `test_close_inside_a_transaction_body_...` covers the exit."""
    entered = opened_store.transaction()
    txn = entered.__enter__()
    opened_store.close()
    with pytest.raises(ProtocolError):
        txn._require_current()


def test_close_inside_a_transaction_body_raises_protocol_error_not_sqlite(opened_store):
    """The path the `__enter__`-only test above cannot reach (design §7.8).

    `close()` closes the connection, so the exit runs against a closed database. Both
    `connection.in_transaction` and `execute("ROLLBACK")` raise
    `sqlite3.ProgrammingError: Cannot operate on a closed database` there -- measured --
    and either would replace the ProtocolError with one a caller would have to know
    pysqlite's hierarchy to interpret. `_require_current` at the exit names the real
    condition and `_rollback_quietly` refuses to overwrite it.
    """
    with pytest.raises(ProtocolError) as caught, opened_store.transaction() as txn:
        opened_store.close()
    assert "closed" in str(caught.value).lower()
    assert not isinstance(caught.value, sqlite3.Error)
    with pytest.raises(ProtocolError):
        txn._require_current()


def test_a_failed_commit_leaves_no_open_transaction(opened_store, monkeypatch):
    """§7.7's second rule: **every** exit closes the SQLite transaction, COMMIT included.

    A failed COMMIT leaves `in_transaction` true and the next `BEGIN IMMEDIATE` raising
    "cannot start a transaction within a transaction" -- measured -- so a store that
    spent its transaction object and cleared its slot on that path would refuse every
    later transaction, reporting a caller error for a state A5a created. The proof is
    that the *next* transaction opens.

    The failure is injected at the call site rather than by a deferred constraint,
    because this schema has none: `effect.txid REFERENCES transaction_record(txid)` is
    immediate, so the violation raises at the INSERT. What the injection reproduces is
    the state that matters -- the COMMIT statement raised and the transaction is still
    open. It cannot be done by patching `sqlite3.Connection.execute`, which is an
    immutable type: `TypeError: cannot set 'execute' attribute of immutable type`,
    measured. Hence the proxy.
    """
    from tests.store_support import CommitFails

    monkeypatch.setattr(opened_store, "_connection", CommitFails(opened_store._connection))
    with pytest.raises(sqlite3.OperationalError), opened_store.transaction():
        pass
    assert not opened_store._connection.in_transaction
    monkeypatch.undo()
    with opened_store.transaction():
        pass


def test_corruption_reported_by_commit_is_translated_and_rolled_back(
    opened_store, monkeypatch
):
    from tests.store_support import CorruptsStatement, one_effect_spec

    proxy = CorruptsStatement(opened_store._connection, "COMMIT")
    monkeypatch.setattr(opened_store, "_connection", proxy)
    with pytest.raises(MetadataStoreInvalid) as caught, opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
    assert isinstance(caught.value.__cause__, sqlite3.DatabaseError)
    assert not proxy.in_transaction


def test_a_transaction_after_close_raises_protocol_error_not_sqlite(opened_store):
    opened_store.close()
    with pytest.raises(ProtocolError), opened_store.transaction():
        pass


def test_the_store_exposes_no_connection(opened_store):
    assert not any(
        name for name in dir(opened_store) if "connect" in name and not name.startswith("_")
    )


def test_transaction_entry_refuses_after_the_binding_closes(store_on):
    with store_on() as binding:
        store = open_store(binding)
    with pytest.raises(ProtocolError), store.transaction():
        pass
    store.close()


def test_close_still_succeeds_on_a_dead_binding(store_on):
    with store_on() as binding:
        store = open_store(binding)
    store.close()


@pytest.mark.parametrize("release", RELEASES, ids=("closed_binding", "released_lock"))
def test_lock_released_before_commit_rolls_back(store_on, release):
    from tests.store_support import one_effect_spec, raw_path

    with store_on() as binding:
        path = raw_path(binding)
        store = open_store(binding)
        with pytest.raises(ProtocolError) as caught, store.transaction() as txn:
            txn.insert_record("tx1", one_effect_spec())
            release(binding)
        assert ("closed" if release.__name__ == "close_binding" else "lock") in str(caught.value).lower()
        store.close()
        raw = sqlite3.connect(path, isolation_level=None)
        try:
            assert raw.execute("SELECT count(*) FROM transaction_record").fetchone() == (0,)
        finally:
            raw.close()


@pytest.mark.parametrize("release", RELEASES, ids=("closed_binding", "released_lock"))
def test_lock_released_during_a_load_returns_no_record(store_on, monkeypatch, release):
    from atoms.store import records as records_module
    from tests.store_support import one_effect_spec

    with store_on() as binding:
        store = open_store(binding)
        with store.transaction() as txn:
            txn.insert_record("tx1", one_effect_spec())
        real = records_module.coherence_findings

        def release_then_check(connection, txid):
            release(binding)
            return real(connection, txid)

        monkeypatch.setattr(records_module, "coherence_findings", release_then_check)
        with pytest.raises(ProtocolError) as caught:
            store.read_record("tx1")
        assert ("closed" if release.__name__ == "close_binding" else "lock") in str(caught.value).lower()
        store.close()


def test_a_successful_read_ends_with_rollback(opened_store):
    from tests.store_support import one_effect_spec

    with opened_store.transaction() as txn:
        txn.insert_record("tx1", one_effect_spec())
    statements: list[str] = []
    opened_store._connection.set_trace_callback(statements.append)
    try:
        assert opened_store.read_record("tx1") is not None
    finally:
        opened_store._connection.set_trace_callback(None)
    assert any(statement == "ROLLBACK" for statement in statements)
    assert not any(statement == "COMMIT" for statement in statements)


@pytest.mark.parametrize(
    ("method", "arguments"), STORE_SURFACE, ids=[case[0] for case in STORE_SURFACE]
)
def test_every_store_operation_after_close_raises_protocol_error(
    opened_store, method, arguments
):
    opened_store.close()
    with pytest.raises(ProtocolError) as caught:
        result = getattr(opened_store, method)(*arguments)
        if method == "transaction":
            result.__enter__()
    assert not isinstance(caught.value, sqlite3.Error)
    assert "Store is closed" in str(caught.value)


def test_the_gate_runs_before_the_orphan_unlink(store_on, monkeypatch):
    from atoms.store import blobs as blobs_module

    with store_on() as binding:
        store = open_store(binding)
        content = b"orphaned"
        from tests.store_support import child_dir, digest_of, stage

        digest = digest_of(content)
        with store.create_workspace("tx1") as workspace:
            stage(workspace, "one", content)
            with pytest.raises(ProtocolError) as promotion_error, store.transaction() as txn:
                txn.promote_staging(
                    workspace,
                    (
                        blobs_module.StagedBlob(
                            name="one", digest=digest, byte_len=len(content)
                        ),
                    ),
                )
        assert digest in str(promotion_error.value)
        real = blobs_module.verify_leaf

        def release_then_verify(fd, digest_, byte_len):
            result = real(fd, digest_, byte_len)
            release_lock(binding)
            return result

        with metadata_root_snapshot(binding) as root_fd:
            monkeypatch.setattr(blobs_module, "verify_leaf", release_then_verify)
            with pytest.raises(ProtocolError) as caught:
                store.remove_unindexed_blob(digest)
            assert "lock" in str(caught.value)
            store.close()
            with child_dir(root_fd, "blobs/sha256") as blobs_fd:
                assert os.listdir(blobs_fd) != []


def test_remove_workspace_is_not_exempt_from_the_gate(store_on):
    with store_on() as binding:
        store = open_store(binding)
        workspace = store.create_workspace("tx1")
    with pytest.raises(ProtocolError) as caught:
        store.remove_workspace(workspace)
    assert "closed" in str(caught.value)
    workspace.close()
    store.close()


def test_open_blob_is_refused_inside_this_stores_write_transaction(store_on):
    from atoms.store.blobs import StagedBlob
    from tests.store_support import digest_of, spec_referencing, stage

    content = b"in flight"
    digest = digest_of(content)
    with (
        store_on() as binding,
        open_store(binding) as store,
        store.create_workspace("tx1") as workspace,
    ):
        stage(workspace, "capture", content)
        with store.transaction() as txn:
            txn.promote_staging(
                workspace,
                (StagedBlob(name="capture", digest=digest, byte_len=len(content)),),
            )
            txn.insert_record("tx1", spec_referencing(content))
            with pytest.raises(ProtocolError) as caught:
                store.open_blob(digest)
            assert "write transaction" in str(caught.value)


def _release_after(monkeypatch, module, name, binding, occurrence, release=close_binding):
    real = getattr(module, name)
    calls = {"n": 0}

    def wrapper(*args, **kwargs):
        result = real(*args, **kwargs)
        calls["n"] += 1
        if calls["n"] == occurrence:
            release(binding)
        return result

    monkeypatch.setattr(module, name, wrapper)


@pytest.mark.parametrize("release", RELEASES, ids=("closed_binding", "released_lock"))
def test_the_gate_runs_between_create_workspaces_two_mkdirs(store_on, monkeypatch, release):
    from tests.store_support import child_dir

    with store_on() as binding:
        store = open_store(binding)
        with metadata_root_snapshot(binding) as root_fd:
            _release_after(monkeypatch, os, "mkdir", binding, 1, release)
            with pytest.raises(ProtocolError) as caught:
                store.create_workspace("tx1")
            assert ("closed" if release is close_binding else "lock") in str(caught.value)
            store.close()
            with child_dir(root_fd, "work") as work_fd:
                assert "tx1" not in os.listdir(work_fd)


@pytest.mark.parametrize("release", RELEASES, ids=("closed_binding", "released_lock"))
def test_the_gate_runs_before_promotions_staging_rmdir(store_on, monkeypatch, release):
    from atoms.store.blobs import StagedBlob
    from tests.store_support import child_dir, digest_of, spec_referencing, stage

    content = b"one"
    with store_on() as binding:
        store = open_store(binding)
        workspace = store.create_workspace("tx1")
        stage(workspace, "capture", content)
        with metadata_root_snapshot(binding) as root_fd:
            _release_after(
                monkeypatch, binding.backend.__class__, "flush_directory", binding, 2, release
            )
            with pytest.raises(ProtocolError) as caught, store.transaction() as txn:
                txn.promote_staging(
                    workspace,
                    (
                        StagedBlob(
                            name="capture",
                            digest=digest_of(content),
                            byte_len=len(content),
                        ),
                    ),
                )
                txn.insert_record("tx1", spec_referencing(content))
            assert ("closed" if release is close_binding else "lock") in str(caught.value)
            workspace.close()
            store.close()
            with child_dir(root_fd, "staging") as staging_fd:
                assert "tx1" in os.listdir(staging_fd)


def test_the_gate_runs_between_the_eexist_hash_and_the_source_unlink(store_on, monkeypatch):
    from atoms.store import blobs as blobs_module
    from atoms.store.blobs import StagedBlob
    from tests.store_support import child_dir, digest_of, spec_referencing, stage

    content = b"shared"
    digest = digest_of(content)
    with store_on() as binding:
        store = open_store(binding)
        with child_dir(binding.metadata_root_fd, "blobs/sha256") as blobs_fd:
            fd = os.open(
                blobs_module.digest_to_leaf(digest),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
                dir_fd=blobs_fd,
            )
            try:
                os.write(fd, content)
            finally:
                os.close(fd)
        workspace = store.create_workspace("tx1")
        stage(workspace, "capture", content)
        real = blobs_module.verify_leaf
        seen = {"n": 0}

        def release_on_the_destination_hash(fd_, digest_, byte_len):
            result = real(fd_, digest_, byte_len)
            seen["n"] += 1
            if seen["n"] == 2:
                binding.__exit__(None, None, None)
            return result

        monkeypatch.setattr(blobs_module, "verify_leaf", release_on_the_destination_hash)
        with pytest.raises(ProtocolError) as caught, store.transaction() as txn:
            txn.promote_staging(
                workspace,
                (StagedBlob(name="capture", digest=digest, byte_len=len(content)),),
            )
            txn.insert_record("tx1", spec_referencing(content))
        assert "closed" in str(caught.value)
        assert "capture" in os.listdir(workspace._staging_fd)
        workspace.close()
        store.close()


@pytest.mark.parametrize("half", ["staging", "work"])
def test_the_gate_runs_before_each_of_remove_workspaces_rmdirs(store_on, monkeypatch, half):
    from tests.store_support import child_dir, stage

    with store_on() as binding:
        store = open_store(binding)
        workspace = store.create_workspace("tx1")
        with metadata_root_snapshot(binding) as root_fd:
            if half == "staging":
                stage(workspace, "capture", b"x")
                _release_after(monkeypatch, os, "unlink", binding, 1)
            else:
                _release_after(monkeypatch, os, "rmdir", binding, 1)
            with pytest.raises(ProtocolError) as caught:
                store.remove_workspace(workspace)
            assert "closed" in str(caught.value)
            workspace.close()
            store.close()
            with child_dir(root_fd, half) as parent_fd:
                assert "tx1" in os.listdir(parent_fd)


def test_the_gate_runs_before_creations_openat(store_on):
    with store_on() as binding, metadata_root_snapshot(binding) as root_fd:
        binding.__exit__(None, None, None)
        with pytest.raises(ProtocolError) as caught:
            open_store(binding)
        assert "closed" in str(caught.value)
        assert "atoms.db" not in os.listdir(root_fd)


def test_the_gate_runs_before_creations_fchmod(store_on, monkeypatch):
    from atoms.store.connection import create_store

    with store_on() as binding, metadata_root_snapshot(binding) as root_fd:
        _release_after(monkeypatch, os, "open", binding, 1)
        with pytest.raises(ProtocolError) as caught:
            create_store(binding)
        assert "closed" in str(caught.value)
        assert "atoms.db" in os.listdir(root_fd)
        assert os.stat("atoms.db", dir_fd=root_fd).st_size == 0


def test_the_gate_runs_before_the_wal_transition(store_on, monkeypatch):
    from atoms.store import connection as connection_module
    from atoms.store.connection import create_store
    from tests.store_support import raw_path

    with store_on() as binding:
        path = raw_path(binding)
        _release_after(monkeypatch, connection_module, "publish_entry", binding, 1)
        with pytest.raises(ProtocolError) as caught:
            create_store(binding)
        assert "closed" in str(caught.value)
        raw = sqlite3.connect(path, isolation_level=None)
        try:
            assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        finally:
            raw.close()


def test_the_gate_runs_during_the_ddl_loop_before_its_commit(store_on, monkeypatch):
    from atoms.store import connection as connection_module
    from atoms.store.connection import create_store
    from tests.store_support import raw_path

    with store_on() as binding:
        path = raw_path(binding)
        _release_after(monkeypatch, connection_module, "_execute_schema", binding, 1)
        with pytest.raises(ProtocolError) as caught:
            create_store(binding)
        assert "closed" in str(caught.value)
        raw = sqlite3.connect(path, isolation_level=None)
        try:
            assert raw.execute("PRAGMA application_id").fetchone()[0] == 0
            assert raw.execute("PRAGMA user_version").fetchone()[0] == 0
            assert raw.execute("SELECT count(*) FROM sqlite_schema").fetchone()[0] == 0
        finally:
            raw.close()


@pytest.mark.parametrize("release", RELEASES, ids=("closed_binding", "released_lock"))
def test_the_gate_runs_before_the_repair_chmod(store_on, monkeypatch, release):
    from atoms.store.connection import reopen_store

    with store_on() as binding, metadata_root_snapshot(binding) as root_fd:
        previous = os.umask(0o277)
        try:
            fd = os.open(
                "atoms.db",
                os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_RDWR,
                0o400,
                dir_fd=root_fd,
            )
            os.close(fd)
            _release_after(monkeypatch, os, "open", binding, 1, release)
            with pytest.raises(ProtocolError) as caught:
                reopen_store(binding)
            assert ("closed" if release is close_binding else "lock") in str(caught.value)
            assert stat.S_IMODE(os.stat("atoms.db", dir_fd=root_fd).st_mode) == 0o400
        finally:
            os.umask(previous)


def test_orphan_flush_continues_on_its_held_descriptor_after_unlink(store_on, monkeypatch):
    from atoms.store import blobs as blobs_module
    from tests.store_support import digest_of, stage

    with store_on() as binding:
        store = open_store(binding)
        content = b"orphaned"
        digest = digest_of(content)
        with store.create_workspace("tx1") as workspace:
            stage(workspace, "one", content)
            with pytest.raises(ProtocolError) as promotion_error, store.transaction() as txn:
                txn.promote_staging(
                    workspace,
                    (blobs_module.StagedBlob("one", digest, len(content)),),
                )
        assert digest in str(promotion_error.value)
        assert "does not reference" in str(promotion_error.value)
        real_unlink = os.unlink
        real_flush = binding.backend.flush_directory
        flushed: list[int] = []

        def unlink_then_release(*args, **kwargs):
            real_unlink(*args, **kwargs)
            release_lock(binding)

        def record_flush(fd):
            flushed.append(os.fstat(fd).st_ino)
            real_flush(fd)

        monkeypatch.setattr(os, "unlink", unlink_then_release)
        monkeypatch.setattr(binding.backend, "flush_directory", record_flush)
        with pytest.raises(ProtocolError) as caught:
            store.remove_unindexed_blob(digest)
        assert "lock" in str(caught.value)
        assert len(flushed) == 1
        assert not store._connection.in_transaction
        store.close()


@pytest.mark.parametrize("release", RELEASES, ids=("closed_binding", "released_lock"))
def test_unindexed_enumeration_rechecks_liveness_after_verification(
    store_on, monkeypatch, release
):
    from atoms.store import blobs as blobs_module
    from tests.store_support import child_dir, digest_of, stage

    with store_on() as binding:
        store = open_store(binding)
        content = b"orphaned"
        digest = digest_of(content)
        with store.create_workspace("tx1") as workspace:
            stage(workspace, "one", content)
            with pytest.raises(ProtocolError) as promotion_error, store.transaction() as txn:
                txn.promote_staging(
                    workspace,
                    (blobs_module.StagedBlob("one", digest, len(content)),),
                )
        assert digest in str(promotion_error.value)
        assert "does not reference" in str(promotion_error.value)

        real_verify = blobs_module.verify_leaf

        def release_after_verification(fd, digest_, byte_len):
            result = real_verify(fd, digest_, byte_len)
            release(binding)
            return result

        statements: list[str] = []
        store._connection.set_trace_callback(statements.append)
        with metadata_root_snapshot(binding) as root_fd:
            with child_dir(root_fd, "blobs/sha256") as parent:
                blobs_path = os.readlink(f"/proc/self/fd/{parent}")
            monkeypatch.setattr(blobs_module, "verify_leaf", release_after_verification)
            try:
                with pytest.raises(ProtocolError) as caught:
                    store.list_unindexed_blobs()
            finally:
                store._connection.set_trace_callback(None)
            assert ("closed" if release is close_binding else "lock") in str(caught.value)
            assert statements[-1] == "ROLLBACK"
            assert "COMMIT" not in statements
            with child_dir(root_fd, "blobs/sha256") as parent:
                assert digest.split(":", 1)[1] in os.listdir(parent)
            targets: list[str] = []
            for fd in os.listdir("/proc/self/fd"):
                try:
                    targets.append(os.readlink(f"/proc/self/fd/{fd}"))
                except FileNotFoundError:
                    pass
            assert blobs_path not in targets
        store.close()
