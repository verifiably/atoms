"""Opening the store: creation, reopen, the pinned profile, and liveness (design §5)."""

from __future__ import annotations

import os
import sqlite3
import stat
from enum import Enum

from atoms.core.errors import CapabilityUnavailable
from atoms.fs.binding import ProjectBinding
from atoms.store.errors import MetadataStoreInvalid, translated
from atoms.store.schema import (
    APPLICATION_ID,
    EXPECTED_CATALOG,
    SCHEMA_STATEMENTS,
    SCHEMA_VERSION,
)

DATABASE_NAME = "atoms.db"
SIDECAR_NAMES = ("atoms.db-wal", "atoms.db-shm", "atoms.db-journal")
DATABASE_ENTRIES = (DATABASE_NAME, *SIDECAR_NAMES)
MINIMUM_SQLITE = (3, 37, 0)
DATABASE_MODE = 0o600

_SET_USER_VERSION = f"PRAGMA user_version = {SCHEMA_VERSION}"
_SET_APPLICATION_ID = f"PRAGMA application_id = {APPLICATION_ID}"
_BEGIN_IMMEDIATE = "BEGIN IMMEDIATE"
_COMMIT = "COMMIT"
_ROLLBACK = "ROLLBACK"
_READ_APPLICATION_ID = "PRAGMA application_id"
_READ_USER_VERSION = "PRAGMA user_version"
_READ_JOURNAL_MODE = "PRAGMA journal_mode"
_SET_JOURNAL_MODE_WAL = "PRAGMA journal_mode = WAL"
_READ_COMPILE_OPTIONS = "PRAGMA compile_options"
_QUICK_CHECK = "PRAGMA quick_check"
_FOREIGN_KEY_CHECK = "PRAGMA foreign_key_check"
_READ_CATALOG = "SELECT type, name, tbl_name, sql FROM sqlite_schema"

_CONNECTION_PRAGMAS: tuple[tuple[str, str, str, object], ...] = (
    ("synchronous", "PRAGMA synchronous = FULL", "PRAGMA synchronous", 2),
    ("foreign_keys", "PRAGMA foreign_keys = ON", "PRAGMA foreign_keys", 1),
    ("temp_store", "PRAGMA temp_store = MEMORY", "PRAGMA temp_store", 2),
    ("trusted_schema", "PRAGMA trusted_schema = OFF", "PRAGMA trusted_schema", 0),
)


def gate(binding: ProjectBinding) -> None:
    """The liveness gate (design §5.4).

    A connection that outlives its binding or its lock is a connection writing to a volume
    nothing holds. `binding.backend` calls `_require_active`, which raises ProtocolError
    when the binding is closed *or* when the lock was released before it
    (`binding.py:110`) -- the idiom approve_for_project already uses at `approval.py:116`.
    """
    backend = binding.backend
    del backend


def require_platform(binding: ProjectBinding) -> None:
    """Refuse a build that cannot supply the semantics the schema depends on."""
    if sqlite3.sqlite_version_info < MINIMUM_SQLITE:
        raise CapabilityUnavailable(
            f"STRICT tables require SQLite >= 3.37; this build is "
            f"{sqlite3.sqlite_version}"
        )
    if not os.path.isdir("/proc/self/fd"):
        raise CapabilityUnavailable(
            "/proc/self/fd is required to repair a database whose mode the umask reduced "
            "below readability (design §5.2 step 2); there is no path-based fallback"
        )
    del binding


def _compile_options(connection: sqlite3.Connection) -> frozenset[str]:
    with translated("reading compile options"):
        return frozenset(row[0] for row in connection.execute(_READ_COMPILE_OPTIONS))


def _require_temp_store_capable(connection: sqlite3.Connection) -> None:
    if "TEMP_STORE=0" in _compile_options(connection):
        raise CapabilityUnavailable(
            "this SQLite build reports TEMP_STORE=0, under which temp_store=MEMORY is "
            "inert and its read-back still reports success"
        )


def _preflight_entries(binding: ProjectBinding, *, require_absent: bool) -> dict[str, os.stat_result]:
    """fstatat each of the four entries, no-follow. Any symlink refuses."""
    seen: dict[str, os.stat_result] = {}
    for name in DATABASE_ENTRIES:
        try:
            info = os.stat(name, dir_fd=binding.metadata_root_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode):
            raise MetadataStoreInvalid(
                f"{name} under metadata_root is not a regular file; a symlink there "
                "redirects SQLite's own recovery"
            )
        seen[name] = info
    if require_absent and seen:
        raise MetadataStoreInvalid(
            "cannot create a store beside surviving database entries "
            f"{sorted(seen)}; a sidecar with no database is an invalid store shape"
        )
    return seen


def publish_entry(binding: ProjectBinding, fd: int) -> None:
    """Design §5.1 step 3 -- make the mode exact and the directory entry durable.

    SQLite did not create this entry, so nothing in its durability contract promises the
    entry survives power loss: a record could be durable inside a file the directory does
    not name. Every flush goes through the Backend, which is where F_FULLFSYNC lives on
    macOS.
    """
    gate(binding)
    os.fchmod(fd, DATABASE_MODE)
    binding.backend.flush_file(fd)
    binding.backend.flush_directory(binding.metadata_root_fd)


def install_authorizer(connection: sqlite3.Connection) -> None:
    def authorize(action: int, *_rest: object) -> int:
        if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)


def apply_connection_profile(connection: sqlite3.Connection) -> None:
    """The connection-local half of design §5.3, read back one at a time.

    foreign_keys is per-connection and off by default, and journal_mode and foreign_keys
    both fail silently rather than raising -- a store running without them is a store
    whose durability and referential claims are false.
    """
    _require_temp_store_capable(connection)
    for name, setter, reader, expected in _CONNECTION_PRAGMAS:
        with translated(f"setting {name}"):
            connection.execute(setter)
        with translated(f"reading back {name}"):
            observed = connection.execute(reader).fetchone()[0]
        if observed != expected:
            raise CapabilityUnavailable(
                f"PRAGMA {name} would not take: asked for {expected!r}, got {observed!r}"
            )


def apply_persistent_profile(binding: ProjectBinding, connection: sqlite3.Connection) -> None:
    """The persistent half: the WAL transition, which rewrites the database file."""
    gate(binding)
    with translated("setting journal_mode"):
        mode = connection.execute(_SET_JOURNAL_MODE_WAL).fetchone()[0]
    if mode != "wal":
        raise CapabilityUnavailable(
            f"the volume would not accept WAL journalling; journal_mode is {mode!r}"
        )


def _execute_schema(connection: sqlite3.Connection) -> None:
    """Every DDL statement, one execute() each.

    Never executescript: it issues a COMMIT before running, which under
    isolation_level=None ends the transaction this is called inside -- measured,
    in_transaction goes True to False across the call while the application_id written
    before it stays durable.
    """
    for statement in SCHEMA_STATEMENTS:
        with translated("creating the schema"):
            connection.execute(statement)


def _connect(binding: ProjectBinding) -> sqlite3.Connection:
    connection = sqlite3.connect(
        binding.verified_metadata_path(DATABASE_NAME), isolation_level=None
    )
    install_authorizer(connection)
    return connection


def create_store(binding: ProjectBinding) -> sqlite3.Connection:
    """Design §5.1. Returns a connection to a completed store."""
    require_platform(binding)
    _preflight_entries(binding, require_absent=True)

    gate(binding)
    fd = os.open(
        DATABASE_NAME,
        os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_RDWR,
        DATABASE_MODE,
        dir_fd=binding.metadata_root_fd,
    )
    try:
        publish_entry(binding, fd)
    finally:
        os.close(fd)

    connection = _connect(binding)
    try:
        apply_persistent_profile(binding, connection)
        apply_connection_profile(connection)
        initialize_schema(binding, connection)
    except BaseException:
        connection.close()
        raise
    return connection


def _rollback_quietly(connection: sqlite3.Connection) -> None:
    """Close whatever SQLite transaction is still open. **The package's one swallow.**

    Every explicit transaction in the package ends through this on its failing path --
    initialization here, the write transaction and the read transaction in §7.4, and
    §7.3's reclamation -- so "every exit closes the SQLite transaction" is one function
    rather than four copies of a shape, and the COMMIT is inside the guarded region at
    all four sites.

    It returns instead of raising. Design §7.7 is explicit that the exit "rolls back
    whatever is still open on *any* failing path and re-raises the original exception,
    never masking it with the rollback's own", and the caller's bare `raise` is what
    leaves. There is nothing else it could do: the transaction is lost either way, and
    replacing the cause with the consequence is what makes a failure unreadable.

    `in_transaction` is read *inside* the try because on a connection something already
    closed it does not return False -- it raises `ProgrammingError: Cannot operate on a
    closed database`, measured. That is not hypothetical: `Store.close()` called inside
    a `with transaction()` body leaves exactly that state, and reading the flag outside
    the try would replace the `ProtocolError` the caller earned with pysqlite's.

    Task 13 permits `except sqlite3.DatabaseError` without a bare `raise` in this
    function and in no other, by name (criterion 43).
    """
    try:
        if connection.in_transaction:
            connection.execute(_ROLLBACK)
    except sqlite3.DatabaseError:
        return


def initialize_schema(binding: ProjectBinding, connection: sqlite3.Connection) -> None:
    """Design §5.1 step 5 -- one explicit transaction, gated immediately before COMMIT.

    The COMMIT is inside the `try`, not after it: a COMMIT that fails leaves the
    transaction open (measured), and `create_store` would then close a connection with a
    half-initialized schema still uncommitted rather than rolled back.
    """
    with translated("beginning initialization"):
        connection.execute(_BEGIN_IMMEDIATE)
    try:
        _execute_schema(connection)
        with translated("stamping the schema version"):
            connection.execute(_SET_USER_VERSION)
        with translated("stamping the application id"):
            connection.execute(_SET_APPLICATION_ID)
        gate(binding)
        with translated("committing initialization"):
            connection.execute(_COMMIT)
    except BaseException:
        _rollback_quietly(connection)
        raise


class Verdict(Enum):
    COMPLETED = "completed"
    RESUMABLE = "resumable"


def repair_unpublished(binding: ProjectBinding) -> None:
    """Design §5.2 step 2 -- re-run §5.1 step 3 before SQLite is involved at all.

    Ordering this after classification made it unreachable: at 0o400 SQLite falls back to
    a read-only open, so the classification reads succeed and the first write of the
    resume fails 'attempt to write a readonly database'; at 0o000 nothing opens at all.
    Three failures, one cause -- the repair was issued from a position that could no
    longer carry it out.

    O_PATH is the only open that succeeds at mode 0o000, and fchmod on it fails EBADF, so
    the chmod goes through /proc/self/fd. That is race-free for the reason it exists: the
    descriptor pins the inode step 1 stat'd, so nothing between the two can substitute a
    symlink or a different file.

    The gate sits **immediately before the chmod**, not before the O_PATH open. The open
    is not a mutation, and gating in front of it would prove the lease was held when the
    repair began rather than when it changed a mode -- §5.4's exact complaint about a
    single gate at the front of an operation, in miniature.
    """
    path_fd = os.open(
        DATABASE_NAME,
        os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=binding.metadata_root_fd,
    )
    try:
        gate(binding)
        os.chmod(f"/proc/self/fd/{path_fd}", DATABASE_MODE)
    finally:
        os.close(path_fd)
    fd = os.open(
        DATABASE_NAME,
        os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=binding.metadata_root_fd,
    )
    try:
        publish_entry(binding, fd)
    finally:
        os.close(fd)


def _integrity_findings(connection: sqlite3.Connection) -> tuple[str, ...]:
    with translated("checking integrity"):
        quick = tuple(row[0] for row in connection.execute(_QUICK_CHECK))
        foreign = tuple(str(row) for row in connection.execute(_FOREIGN_KEY_CHECK))
    return tuple(finding for finding in quick if finding != "ok") + foreign


def _catalog(connection: sqlite3.Connection) -> frozenset[tuple[str, str, str, str | None]]:
    with translated("reading the catalog"):
        rows = connection.execute(_READ_CATALOG).fetchall()
    return frozenset(
        (kind, name, tbl, None if sql is None else sql.rstrip().rstrip(";"))
        for kind, name, tbl, sql in rows
    )


def classify(connection: sqlite3.Connection) -> Verdict:
    """Identity, version, and schema -- from reads alone (design §5.2 step 4)."""
    with translated("reading identity"):
        application_id = connection.execute(_READ_APPLICATION_ID).fetchone()[0]
        user_version = connection.execute(_READ_USER_VERSION).fetchone()[0]
    catalog = _catalog(connection)

    if application_id == APPLICATION_ID:
        if user_version != SCHEMA_VERSION:
            raise MetadataStoreInvalid(
                f"incompatible store version {user_version}; this build knows "
                f"{SCHEMA_VERSION}. A newer store is not corrupt -- it is unreadable by "
                "this build -- and both mean stop, do not interpret this"
            )
        if catalog != EXPECTED_CATALOG:
            missing = EXPECTED_CATALOG - catalog
            extra = catalog - EXPECTED_CATALOG
            raise MetadataStoreInvalid(
                f"schema does not match version {SCHEMA_VERSION}: missing "
                f"{sorted(name for _k, name, _t, _s in missing)}, unexpected "
                f"{sorted(name for _k, name, _t, _s in extra)}"
            )
        return Verdict.COMPLETED

    if application_id == 0 and user_version == 0 and not catalog:
        return Verdict.RESUMABLE

    if application_id == 0:
        raise MetadataStoreInvalid(
            "version zero with a non-empty schema is not an initialization this engine "
            f"interrupted: user_version={user_version}, {len(catalog)} schema objects"
        )
    raise MetadataStoreInvalid(
        f"application_id {application_id} is not this engine's ({APPLICATION_ID})"
    )


def open_database(binding: ProjectBinding) -> sqlite3.Connection:
    """Design §5.2, steps 1 through 6."""
    require_platform(binding)
    seen = _preflight_entries(binding, require_absent=False)
    if DATABASE_NAME not in seen:
        raise MetadataStoreInvalid(
            f"{DATABASE_NAME} is absent under metadata_root; "
            f"surviving entries {sorted(seen)}"
        )
    if seen[DATABASE_NAME].st_size == 0:
        repair_unpublished(binding)

    connection = _connect(binding)
    try:
        verdict = classify(connection)
        if verdict is Verdict.RESUMABLE:
            apply_persistent_profile(binding, connection)
            apply_connection_profile(connection)
            initialize_schema(binding, connection)
        else:
            with translated("reading journal_mode"):
                mode = connection.execute(_READ_JOURNAL_MODE).fetchone()[0]
            if mode != "wal":
                raise MetadataStoreInvalid(
                    f"a completed store must already be in WAL; this one reports "
                    f"{mode!r}. Converting it would rewrite a database on a guess"
                )
            apply_connection_profile(connection)
        findings = _integrity_findings(connection)
        if findings:
            raise MetadataStoreInvalid(
                "integrity check failed: " + "; ".join(findings)
            )
    except BaseException:
        connection.close()
        raise
    return connection


def reopen_store(binding: ProjectBinding) -> sqlite3.Connection:
    """Alias naming the caller's intent; `open_database` is the shared implementation."""
    return open_database(binding)
