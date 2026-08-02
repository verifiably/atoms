"""Tier 3 -- creation and reopen (design §11.3)."""

from __future__ import annotations

import os
import sqlite3

import pytest

from atoms.core.errors import CapabilityUnavailable, ProtocolError
from atoms.store.connection import create_store, gate
from atoms.store.errors import MetadataStoreInvalid
from atoms.store.schema import APPLICATION_ID, EXPECTED_CATALOG, SCHEMA_VERSION
from tests.store_support import DATABASE_ENTRIES, raw_connect


def test_creation_produces_a_completed_store(store_on):
    with store_on() as binding:
        connection = create_store(binding)
        try:
            assert connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
            assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            catalog = frozenset(
                (kind, name, tbl, None if sql is None else sql.rstrip().rstrip(";"))
                for kind, name, tbl, sql in connection.execute(
                    "SELECT type, name, tbl_name, sql FROM sqlite_schema"
                )
            )
            assert catalog == EXPECTED_CATALOG
        finally:
            connection.close()


def test_creation_sets_the_exact_mode_regardless_of_umask(store_on):
    """openat's mode is a request the umask subtracts from; step 3 makes it exact."""
    previous = os.umask(0o077)
    try:
        with store_on() as binding:
            create_store(binding).close()
            mode = os.stat("atoms.db", dir_fd=binding.metadata_root_fd).st_mode & 0o777
            assert mode == 0o600
    finally:
        os.umask(previous)


def test_a_second_concurrent_creation_raises_rather_than_reinitializing(store_on):
    with store_on() as binding:
        create_store(binding).close()
        with pytest.raises(MetadataStoreInvalid):
            create_store(binding)


@pytest.mark.parametrize("sidecar", ["atoms.db-wal", "atoms.db-shm", "atoms.db-journal"])
def test_creation_refuses_a_sidecar_surviving_without_the_database(store_on, sidecar):
    """O_EXCL covers atoms.db alone. Without this preflight, creation is the only path
    with no sidecar check while reopen has one -- and a planted -journal symlink is
    silently unlinked by the first WAL transition, measured (design §5.1 step 1)."""
    with store_on() as binding:
        fd = os.open(sidecar, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600,
                     dir_fd=binding.metadata_root_fd)
        os.close(fd)
        with pytest.raises(MetadataStoreInvalid) as caught:
            create_store(binding)
        assert sidecar in str(caught.value)
        assert sidecar in os.listdir(binding.metadata_root_fd), "the sidecar was destroyed"


@pytest.mark.parametrize("entry", DATABASE_ENTRIES)
def test_creation_refuses_a_symlink_at_any_of_the_four_entries(store_on, entry):
    with store_on() as binding:
        os.symlink("/etc/passwd", entry, dir_fd=binding.metadata_root_fd)
        with pytest.raises(MetadataStoreInvalid):
            create_store(binding)


def test_the_connection_profile_is_read_back(store_on):
    with store_on() as binding:
        connection = create_store(binding)
        try:
            assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
            assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            assert connection.execute("PRAGMA temp_store").fetchone()[0] == 2
            assert connection.execute("PRAGMA trusted_schema").fetchone()[0] == 0
        finally:
            connection.close()


def test_the_authorizer_denies_attach(store_on, tmp_path):
    with store_on() as binding:
        connection = create_store(binding)
        try:
            with pytest.raises(sqlite3.DatabaseError):
                connection.execute(f"ATTACH DATABASE '{tmp_path / 'other.db'}' AS other")
        finally:
            connection.close()


def test_an_old_sqlite_refuses_with_capability_unavailable(store_on, monkeypatch):
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 36, 0))
    with store_on() as binding:
        with pytest.raises(CapabilityUnavailable) as caught:
            create_store(binding)
        assert "3.37" in str(caught.value)


def test_a_temp_store_zero_build_refuses_with_capability_unavailable(store_on, monkeypatch):
    """temp_store=MEMORY is inert under TEMP_STORE=0 and the read-back still reports
    success, so the compile option is the only evidence (design §5.3)."""
    import atoms.store.connection as connection_module

    monkeypatch.setattr(connection_module, "_compile_options", lambda _c: frozenset({"TEMP_STORE=0"}))
    with store_on() as binding:
        with pytest.raises(CapabilityUnavailable) as caught:
            create_store(binding)
        assert "TEMP_STORE" in str(caught.value)


def test_the_ddl_transaction_is_atomic(store_on, monkeypatch):
    """A cut inside step 5 must leave (0, 0, empty) -- never a partial schema with a
    committed application_id, which is what executescript produces (design §5.1 step 5)."""
    import atoms.store.connection as connection_module

    real = connection_module._execute_schema

    def cut(connection):
        real(connection)
        raise KeyboardInterrupt("cut inside step 5")

    monkeypatch.setattr(connection_module, "_execute_schema", cut)
    with store_on() as binding:
        with pytest.raises(KeyboardInterrupt):
            create_store(binding)
        raw = raw_connect(binding)
        try:
            assert raw.execute("PRAGMA application_id").fetchone()[0] == 0
            assert raw.execute("PRAGMA user_version").fetchone()[0] == 0
            assert raw.execute("SELECT count(*) FROM sqlite_schema").fetchone()[0] == 0
        finally:
            raw.close()


def test_the_gate_refuses_a_closed_binding(store_on):
    with store_on() as binding:
        create_store(binding).close()
    with pytest.raises(ProtocolError):
        gate(binding)


def test_the_gate_refuses_a_released_lock_under_a_live_binding(store_on):
    """The gate's *other* branch, and the one nothing else reaches.

    `binding.__exit__()` sets `_active = False`, so `_require_active` refuses at its
    first check (`binding.py:111`) and never evaluates the second. But the lock and the
    binding are separate objects with separate lifetimes: `HeldProjectLock.__exit__`
    releases flock and closes both descriptors while leaving `binding.active` True, and
    `binding.py:113` is the only thing standing between that state and a write. This
    asserts the branch by its message, because both branches raise `ProtocolError`.
    """
    from tests.store_support import release_lock

    with store_on() as binding:
        create_store(binding).close()
        release_lock(binding)
        assert binding.active
        with pytest.raises(ProtocolError) as caught:
            gate(binding)
        assert "lock" in str(caught.value).lower()
