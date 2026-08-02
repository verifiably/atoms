"""Tier 3 -- creation and reopen (design §11.3)."""

from __future__ import annotations

import os
import sqlite3

import pytest

from atoms.core.errors import CapabilityUnavailable, ProtocolError
from atoms.store.connection import create_store, gate, reopen_store
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


def _cut_after_creating(binding, *, umask: int) -> None:
    """Reproduce §5.1's cut between step 2 and step 3: the file exists, unpublished."""
    previous = os.umask(umask)
    try:
        fd = os.open(
            "atoms.db",
            os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_RDWR,
            0o600,
            dir_fd=binding.metadata_root_fd,
        )
        os.close(fd)
    finally:
        os.umask(previous)


def _cut_after_publishing(binding) -> None:
    """The cut between step 3 and step 4: published, zero length, still delete mode."""
    _cut_after_creating(binding, umask=0o022)


def _cut_after_wal(binding) -> None:
    """The cut between step 4 and step 5: WAL set, schema still empty."""
    _cut_after_publishing(binding)
    connection = raw_connect(binding)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
    finally:
        connection.close()


@pytest.mark.parametrize("umask", [0o277, 0o777])
def test_the_unpublished_cut_resumes_to_the_exact_mode(store_on, umask):
    """0o277 leaves 0o400 and 0o777 leaves 0o000, and they fail differently: at 0o400
    SQLite falls back to a read-only open so classification succeeds and the first write
    raises 'attempt to write a readonly database'; at 0o000 nothing opens at all. Only
    the second distinguishes §5.2 step 2's O_PATH route from a plain fchmod."""
    with store_on() as binding:
        _cut_after_creating(binding, umask=umask)
        connection = reopen_store(binding)
        try:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            connection.close()
        mode = os.stat("atoms.db", dir_fd=binding.metadata_root_fd).st_mode & 0o777
        assert mode == 0o600


@pytest.mark.parametrize("cut", ["published", "wal"])
def test_every_later_creation_cut_resumes(store_on, cut):
    with store_on() as binding:
        {"published": _cut_after_publishing, "wal": _cut_after_wal}[cut](binding)
        connection = reopen_store(binding)
        try:
            assert connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
            assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        finally:
            connection.close()


def test_a_resumed_store_equals_an_uninterrupted_one(store_on):
    """Schema, version, and mode -- the third is where the unpublished cut is caught."""
    with store_on() as binding:
        create_store(binding).close()
        reference = frozenset(raw_connect(binding).execute("SELECT type, name, tbl_name, sql FROM sqlite_schema"))
        reference_mode = os.stat("atoms.db", dir_fd=binding.metadata_root_fd).st_mode & 0o777
    with store_on() as binding:
        _cut_after_publishing(binding)
        reopen_store(binding).close()
        resumed = frozenset(raw_connect(binding).execute("SELECT type, name, tbl_name, sql FROM sqlite_schema"))
        resumed_mode = os.stat("atoms.db", dir_fd=binding.metadata_root_fd).st_mode & 0o777
    assert resumed == reference
    assert resumed_mode == reference_mode


def test_a_completed_store_has_its_journal_mode_queried_and_never_set(store_on, monkeypatch):
    with store_on() as binding:
        create_store(binding).close()
        import atoms.store.connection as connection_module

        def refuse(*_args, **_kwargs):
            raise AssertionError("a completed store must never have journal_mode set")

        monkeypatch.setattr(connection_module, "apply_persistent_profile", refuse)
        reopen_store(binding).close()


def test_a_non_empty_delete_mode_database_is_refused_and_stays_in_delete_mode(store_on):
    """The assertion that would have caught the original defect: reopen must not convert
    a database while deciding whether to refuse it (design §5.2 step 3)."""
    with store_on() as binding:
        raw = raw_connect(binding)
        try:
            raw.execute("CREATE TABLE foreign_thing (a TEXT)")
        finally:
            raw.close()
        with pytest.raises(MetadataStoreInvalid):
            reopen_store(binding)
        raw = raw_connect(binding)
        try:
            assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        finally:
            raw.close()


@pytest.mark.parametrize(
    ("application_id", "user_version", "create_table", "fragment"),
    [
        (APPLICATION_ID, SCHEMA_VERSION + 1, True, "store version"),
        (APPLICATION_ID, 99, True, "store version"),
        (0, 0, True, "version zero"),
        (12345, 0, False, "not this engine"),
    ],
)
def test_every_version_table_row_produces_its_verdict(
    store_on, application_id, user_version, create_table, fragment
):
    with store_on() as binding:
        raw = raw_connect(binding)
        try:
            if create_table:
                raw.execute("CREATE TABLE something (a TEXT)")
            raw.execute(f"PRAGMA application_id = {application_id}")
            raw.execute(f"PRAGMA user_version = {user_version}")
        finally:
            raw.close()
        with pytest.raises(MetadataStoreInvalid) as caught:
            reopen_store(binding)
        assert fragment.split()[0] in str(caught.value).lower()


def test_a_same_version_wrong_schema_store_is_refused(store_on):
    with store_on() as binding:
        create_store(binding).close()
        raw = raw_connect(binding)
        try:
            raw.execute("CREATE TABLE extra (a TEXT)")
        finally:
            raw.close()
        with pytest.raises(MetadataStoreInvalid) as caught:
            reopen_store(binding)
        assert "schema" in str(caught.value).lower()


def test_a_failing_integrity_check_refuses(store_on, monkeypatch):
    with store_on() as binding:
        create_store(binding).close()
        import atoms.store.connection as connection_module

        monkeypatch.setattr(
            connection_module, "_integrity_findings", lambda _c: ("page 4 is malformed",)
        )
        with pytest.raises(MetadataStoreInvalid) as caught:
            reopen_store(binding)
        assert "malformed" in str(caught.value)


def test_a_check_violating_row_refuses_at_reopen(store_on):
    """What closes the durable-enum path, with no code in `load_record` to close it.

    `TransactionState(state_value)` would raise a raw `ValueError` on a value outside the
    enum, and translating that in every reader would be handling for a state the store
    cannot be in. This is the proof of "cannot": the `state` CHECK list is generated from
    the enum (§6.1), the catalog comparison proves the list is the current one, and
    `PRAGMA quick_check` -- which `open_database` runs on **every** reopen -- reports a
    violated CHECK. Measured: a row written under `PRAGMA ignore_check_constraints = ON`
    reads back happily and `quick_check` returns `CHECK constraint failed in
    transaction_record`. So a foreign writer can put a bogus state in the file, and no
    reader in this package will ever see it.

    Written with a raw connection because no A5a surface can produce the row -- which is
    the point.
    """
    with store_on() as binding:
        create_store(binding).close()
        raw = raw_connect(binding)
        try:
            raw.execute("PRAGMA ignore_check_constraints = ON")
            raw.execute(
                "INSERT INTO transaction_record "
                "(txid, spec_json, state, committed) VALUES ('tx1', '{}', 'bogus', "
                "'uncommitted')"
            )
        finally:
            raw.close()
        with pytest.raises(MetadataStoreInvalid) as caught:
            reopen_store(binding)
        assert "integrity" in str(caught.value).lower()


def test_reopen_refuses_a_symlinked_sidecar_before_sqlite_opens_anything(store_on):
    with store_on() as binding:
        create_store(binding).close()
        os.symlink("/etc/passwd", "atoms.db-journal", dir_fd=binding.metadata_root_fd)
        with pytest.raises(MetadataStoreInvalid):
            reopen_store(binding)
        assert os.readlink("atoms.db-journal", dir_fd=binding.metadata_root_fd) == "/etc/passwd"
