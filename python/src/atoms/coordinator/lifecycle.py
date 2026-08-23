"""Root lifecycle: fail-closed writer state, binding, and creation evidence.

Design 2026-08-23 (docs/2026-08-23-root-lifecycle-commands-design.md, approved
at `b1469f4`). Writability is a durable grant in the host's own `atoms.db`,
bound to the stable machine identity and the canonical root path; the closed
`LifecycleState` union is what every consumer reads, and every cooperative
mutation of an existing root is downstream of a validated writable grant. The
root-creation commands are the single deliberate exception: they write their
claimed destination pre-grant, under their own recorded operation.

Public names are re-exported from `atoms.coordinator.commands`; everything
with a leading underscore here is engine-private, including the seams tests
cut (`_read_machine_identity`, `_complete_root_operation`).
"""

from __future__ import annotations

import base64
import enum
import errno
import hashlib
import json
import os
import secrets
import sqlite3
import stat
import urllib.parse
from dataclasses import dataclass
from typing import NewType, cast

from atoms.chain.model import PathStateJSON, state_to_json
from atoms.core.errors import (
    AtomsError,
    CapabilityUnavailable,
    PreconditionRefused,
    ProtocolError,
)
from atoms.core.fingerprint import (
    DirectoryState,
    FileState,
    PathState,
    SymlinkState,
)
from atoms.core.scratch import CHAIN_LEAF, ROOT_CLAIM_LEAF
from atoms.fs.backend import Backend
from atoms.fs.lock import HeldProjectLock, establish_root
from atoms.fs.volume import (
    StorageProfile,
    build_configuration,
    kernel_identifier,
    read_mountinfo,
    resolve_mount_entry,
)
from atoms.store.errors import MetadataStoreInvalid
from atoms.store.records import (
    RootLifecycleRow,
    RootOperationRow,
    load_root_lifecycle,
    load_root_operation,
    require_root_row_pair,
)
from atoms.store.schema import (
    APPLICATION_ID,
    EXPECTED_CATALOG,
    SCHEMA_VERSION,
    V2_EXPECTED_CATALOG,
)

CLAIM_MODE = 0o600
OPERATION_DOMAIN = "atoms.root-operation.v1"
CLAIM_DOMAIN = "atoms.root-claim.v1"
SNAPSHOT_DOMAIN = "atoms.root-snapshot.v1"

_MACHINE_ID_PATH = "/etc/machine-id"
_HEX_LOWER = frozenset("0123456789abcdef")
_ZERO_MACHINE_ID = "0" * 32

RootOperationId = NewType("RootOperationId", str)


class LifecycleState(enum.StrEnum):
    WRITABLE = "writable"
    READ_ONLY_SERVICEABLE = "read-only-serviceable"
    READ_ONLY_UNSERVICEABLE = "read-only-unserviceable"
    METADATA_LESS = "metadata-less"
    BINDING_MISMATCHED = "binding-mismatched"


@dataclass(frozen=True, slots=True)
class DestinationOverride:
    path: str
    payload: bytes
    mode: int


class SourceSnapshotMoved(PreconditionRefused):
    """The held source head differs from the snapshot the operation names."""


class RootOperationMismatch(PreconditionRefused):
    """A destination operation exists, but this retry does not name it exactly."""


class RootOperationInvalid(AtomsError):
    """Durable operation evidence and destination tree cannot be reconciled."""


# --- Stable machine identity and canonical paths (design §4.2) ---


def _read_machine_identity() -> str:
    """Read the host's stable identity, at each lifecycle boundary, uncached.

    Tests replace this reader; production has no caller-supplied host ID and
    no environment override.
    """
    try:
        with open(_MACHINE_ID_PATH, encoding="ascii") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError) as caught:
        raise CapabilityUnavailable(
            f"the stable machine identity at {_MACHINE_ID_PATH} is unreadable, so "
            "no lifecycle grant can be validated"
        ) from caught
    value = text.rstrip("\n")
    if (
        len(value) != 32
        or not set(value) <= _HEX_LOWER
        or value == _ZERO_MACHINE_ID
    ):
        raise CapabilityUnavailable(
            f"the stable machine identity at {_MACHINE_ID_PATH} is malformed or "
            "uninitialized; it never matches a grant"
        )
    return value


def _require_root_spelling(label: str, value: object) -> str:
    if type(value) is not str:
        raise ProtocolError(f"{label} must be an exact str")
    if not value:
        raise ProtocolError(f"{label} must not be empty")
    if "\x00" in value:
        raise ProtocolError(f"{label} contains a NUL byte")
    return value


def _require_storage(storage: object) -> StorageProfile:
    if type(storage) is not StorageProfile:
        raise ProtocolError("storage must be an exact StorageProfile")
    return storage


# --- The one canonical serializer (design §5.1) ---


def _validate_canonical_value(value: object, label: str) -> None:
    if value is None:
        return
    if type(value) is str:
        if "\x00" in value:
            raise ProtocolError(f"{label} contains a NUL byte")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as caught:
            raise ProtocolError(
                f"{label} contains a lone surrogate and cannot be encoded"
            ) from caught
        return
    if type(value) is int:
        return
    if type(value) is list:
        for index, item in enumerate(cast(list[object], value)):
            _validate_canonical_value(item, f"{label}[{index}]")
        return
    if type(value) is dict:
        for key, item in cast(dict[object, object], value).items():
            if type(key) is not str:
                raise ProtocolError(f"{label} keys must be exact strings")
            _validate_canonical_value(item, f"{label}.{key}")
        return
    raise ProtocolError(
        f"{label} has type {type(value).__name__}, outside the closed canonical "
        "grammar (dict, list, str, int, None)"
    )


def _canonical_bytes(value: object) -> bytes:
    """The one private serializer: closed grammar, sorted keys, tight separators."""
    _validate_canonical_value(value, "canonical value")
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _decode_canonical(data: bytes, error: type[AtomsError], label: str) -> object:
    """Decode canonical bytes, accepting only bytes that re-encode identically."""
    try:
        decoded = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as caught:
        raise error(f"{label} is not canonical UTF-8 JSON") from caught
    try:
        canonical = _canonical_bytes(decoded)
    except ProtocolError as caught:
        raise error(f"{label} is outside the closed canonical grammar") from caught
    if canonical != data:
        raise error(f"{label} bytes are not the canonical encoding")
    return decoded


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mint_operation_id() -> RootOperationId:
    return RootOperationId(secrets.token_hex(16))


def _require_operation_id(value: object) -> str:
    if type(value) is not str:
        raise ProtocolError("operation_id must be an exact str")
    if len(value) != 32 or not set(value) <= _HEX_LOWER:
        raise PreconditionRefused(
            "operation_id must be 32 lowercase hexadecimal characters"
        )
    return value


def register_request(
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
    genesis_payload: bytes,
    registered_surface: tuple[str, ...],
) -> dict[str, object]:
    """The closed register request shape, over canonical guarded spellings."""
    return {
        "domain": OPERATION_DOMAIN,
        "kind": "register",
        "project_root": project_root,
        "metadata_root": metadata_root,
        "storage_profile": {"profile_id": storage.profile_id},
        "genesis_payload_b64": _b64(genesis_payload),
        "registered_surface": list(registered_surface),
    }


@dataclass(frozen=True, slots=True)
class OperationRequest:
    """One canonical request: the bytes are the identity, the hash never alone."""

    operation_json: str
    operation_hash: str

    @classmethod
    def of(cls, request_obj: dict[str, object]) -> OperationRequest:
        encoded = _canonical_bytes(request_obj)
        return cls(
            operation_json=encoded.decode("utf-8"),
            operation_hash=_sha256_hex(encoded),
        )


# --- The root-local claim (design §5.1) ---


def encode_claim(operation_id: str, request: OperationRequest) -> bytes:
    return _canonical_bytes(
        {
            "domain": CLAIM_DOMAIN,
            "operation_id": operation_id,
            "request_hash": request.operation_hash,
            "request_json": request.operation_json,
        }
    )


@dataclass(frozen=True, slots=True)
class RootClaim:
    operation_id: str
    request_hash: str
    request_json: str


def decode_claim(data: bytes) -> RootClaim:
    """Decode a root claim; anything non-canonical is `RootOperationInvalid`."""
    decoded = _decode_canonical(data, RootOperationInvalid, "root claim")
    if type(decoded) is not dict:
        raise RootOperationInvalid("root claim must be a canonical object")
    obj = cast(dict[str, object], decoded)
    if set(obj) != {"domain", "operation_id", "request_hash", "request_json"}:
        raise RootOperationInvalid("root claim has missing or unexpected fields")
    if obj["domain"] != CLAIM_DOMAIN:
        raise RootOperationInvalid("root claim carries the wrong domain")
    operation_id = obj["operation_id"]
    request_hash = obj["request_hash"]
    request_json = obj["request_json"]
    if (
        type(operation_id) is not str
        or len(operation_id) != 32
        or not set(operation_id) <= _HEX_LOWER
    ):
        raise RootOperationInvalid("root claim operation_id is malformed")
    if (
        type(request_hash) is not str
        or len(request_hash) != 64
        or not set(request_hash) <= _HEX_LOWER
    ):
        raise RootOperationInvalid("root claim request_hash is malformed")
    if type(request_json) is not str:
        raise RootOperationInvalid("root claim request_json must be a string")
    if _sha256_hex(request_json.encode("utf-8")) != request_hash:
        raise RootOperationInvalid(
            "root claim request_hash does not name its request bytes"
        )
    _decode_canonical(
        request_json.encode("utf-8"), RootOperationInvalid, "root claim request"
    )
    return RootClaim(
        operation_id=operation_id,
        request_hash=request_hash,
        request_json=request_json,
    )


def _write_all(backend: Backend, fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = backend.write(fd, data[offset:])
        if type(written) is not int or not 0 < written <= len(data) - offset:
            raise ProtocolError("backend.write did not make a valid forward step")
        offset += written


def _read_fd(fd: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 1 << 16)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def create_root_claim(backend: Backend, root_fd: int, claim: bytes) -> None:
    """Create and durably publish the fixed claim leaf, no-clobber.

    EEXIST propagates: adopting or refusing an existing claim is the caller's
    exact-retry decision, made over the claim's decoded bytes.
    """
    fd = backend.create_exclusive(root_fd, ROOT_CLAIM_LEAF, CLAIM_MODE)
    try:
        _write_all(backend, fd, claim)
        backend.flush_file(fd)
    finally:
        backend.close_fd(fd)
    backend.flush_directory(root_fd)


def read_root_claim(backend: Backend, root_fd: int) -> bytes | None:
    """The claim leaf's bytes, or None when absent. A non-regular squatter on
    the reserved name is `RootOperationInvalid`."""
    try:
        fd = backend.open_regular_nofollow(root_fd, ROOT_CLAIM_LEAF)
    except OSError as caught:
        if caught.errno == errno.ENOENT:
            return None
        if caught.errno in (errno.ELOOP, errno.EISDIR, errno.ENOTDIR):
            raise RootOperationInvalid(
                "the reserved root-claim leaf is not a regular file"
            ) from caught
        raise
    try:
        return _read_fd(fd)
    finally:
        backend.close_fd(fd)


def remove_root_claim(
    backend: Backend, root_fd: int, expected: bytes
) -> None:
    """Verify and remove the claim, then flush the root directory.

    Absence is legitimate: a crash between removal and completion leaves the
    retained operation row owning the retry.
    """
    found = read_root_claim(backend, root_fd)
    if found is None:
        return
    if found != expected:
        raise RootOperationInvalid(
            "the root claim does not carry this operation's bytes"
        )
    backend.unlink_child(root_fd, ROOT_CLAIM_LEAF)
    backend.flush_directory(root_fd)


# --- Snapshot proof (design §5.2) ---


def _state_of_entry(
    backend: Backend, parent_fd: int, name: str, root_device: int
) -> tuple[PathState, bool]:
    """(state, is_directory) for one child, symlinks never followed."""
    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if info.st_dev != root_device:
        raise PreconditionRefused(
            f"snapshot walk crossed a mount boundary at {name!r}"
        )
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISDIR(info.st_mode):
        return DirectoryState(mode), True
    if stat.S_ISLNK(info.st_mode):
        target = os.readlink(name, dir_fd=parent_fd)
        return SymlinkState(target, mode), False
    if stat.S_ISREG(info.st_mode):
        fd = backend.open_regular_nofollow(parent_fd, name)
        try:
            content = _read_fd(fd)
        finally:
            backend.close_fd(fd)
        return (
            FileState(f"sha256:{_sha256_hex(content)}", mode, len(content)),
            False,
        )
    raise PreconditionRefused(
        f"snapshot walk found {name!r} outside the closed "
        "directory/regular-file/symlink vocabulary"
    )


def _walk_tree(
    backend: Backend,
    parent_fd: int,
    prefix: str,
    root_device: int,
    entries: dict[str, PathStateJSON],
    *,
    exclude_chain: bool,
) -> None:
    try:
        names = sorted(os.listdir(parent_fd))
    except OSError as caught:
        raise PreconditionRefused(
            f"snapshot walk cannot list {prefix or '.'!r}"
        ) from caught
    for name in names:
        try:
            name.encode("utf-8")
        except UnicodeEncodeError as caught:
            raise PreconditionRefused(
                f"snapshot walk found a non-UTF-8 name under {prefix or '.'!r}"
            ) from caught
        path = f"{prefix}/{name}" if prefix else name
        if not prefix and name == ROOT_CLAIM_LEAF:
            continue
        if not prefix and exclude_chain and name == CHAIN_LEAF:
            continue
        state, is_directory = _state_of_entry(backend, parent_fd, name, root_device)
        if path in entries:
            raise PreconditionRefused(
                f"snapshot walk found duplicate spelling {path!r}"
            )
        entries[path] = state_to_json(state)
        if is_directory:
            child_fd = backend.open_child_directory(parent_fd, name)
            try:
                _walk_tree(
                    backend,
                    child_fd,
                    path,
                    root_device,
                    entries,
                    exclude_chain=exclude_chain,
                )
            finally:
                backend.close_fd(child_fd)


def tree_snapshot(
    backend: Backend, root_fd: int, chain_head: str, *, exclude_chain: bool = False
) -> OperationRequest:
    """The canonical snapshot object over one root, as retained proof bytes.

    Reuses OperationRequest's (json, hash) shape: snapshots are internal retry
    proof, compared as canonical bytes exactly like requests.
    """
    root_device = os.fstat(root_fd).st_dev
    entries: dict[str, PathStateJSON] = {}
    _walk_tree(backend, root_fd, "", root_device, entries, exclude_chain=exclude_chain)
    obj: dict[str, object] = {
        "domain": SNAPSHOT_DOMAIN,
        "chain_head": chain_head,
        "entries": [
            [path, [list(pair) for pair in entries[path]]]
            for path in sorted(entries)
        ],
    }
    return OperationRequest.of(obj)


# --- Read-only classification (design §7) ---

_RO_PRAGMAS = (
    ("application_id", "PRAGMA application_id"),
    ("user_version", "PRAGMA user_version"),
)


def _sidecar_size(metadata_root_fd: int, name: str) -> int | None:
    try:
        info = os.stat(name, dir_fd=metadata_root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise MetadataStoreInvalid(
            f"{name} under metadata_root is not a regular file"
        )
    return info.st_size


def _open_read_only(
    metadata_root_fd: int, metadata_root_path: str
) -> sqlite3.Connection:
    """Strict read-only: never creates or changes a WAL/SHM sidecar.

    A plain `mode=ro` open of a WAL store creates the `-shm`/`-wal` pair
    whenever directory permissions allow — measured — so the mode is chosen
    from the sidecar state instead. No live WAL frames: `immutable=1`, sound
    under the held exclusive metadata lock, which is what keeps the store
    still for the read. A live WAL with its on-disk index: the unix VFS's
    `readonly_shm=1`, which reads the committed WAL state and writes nothing.
    A live WAL without its index refuses — reading it would mean creating the
    index, and this entry has no writable fallback.
    """
    if _sidecar_size(metadata_root_fd, "atoms.db-journal") not in (None, 0):
        raise MetadataStoreInvalid(
            "a rollback journal survives beside this store; it is not a shape "
            "the engine writes"
        )
    wal = _sidecar_size(metadata_root_fd, "atoms.db-wal")
    shm = _sidecar_size(metadata_root_fd, "atoms.db-shm")
    quoted = urllib.parse.quote(os.path.join(metadata_root_path, "atoms.db"))
    if wal in (None, 0):
        uri = f"file:{quoted}?immutable=1"
    elif shm is None:
        raise MetadataStoreInvalid(
            "the store's live WAL cannot be read without creating its index; "
            "a read-only entry has no writable fallback"
        )
    else:
        uri = f"file:{quoted}?mode=ro&readonly_shm=1"
    try:
        connection = sqlite3.connect(uri, uri=True, isolation_level=None)
    except sqlite3.Error as caught:
        raise MetadataStoreInvalid(
            f"the metadata store cannot be opened read-only: {caught}"
        ) from caught

    def authorize(action: int, *_rest: object) -> int:
        if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)
    return connection


def _read_catalog(
    connection: sqlite3.Connection,
) -> frozenset[tuple[str, str, str, str | None]]:
    rows = connection.execute("SELECT type, name, tbl_name, sql FROM sqlite_schema")
    return frozenset(
        (kind, name, tbl, None if sql is None else sql.rstrip().rstrip(";"))
        for kind, name, tbl, sql in rows.fetchall()
    )


@dataclass(frozen=True, slots=True)
class CarrierView:
    """One read-only classification of a metadata carrier's stored facts."""

    state: LifecycleState
    lifecycle: RootLifecycleRow | None
    operation: RootOperationRow | None
    schema_version: int


def _classify_connection(
    connection: sqlite3.Connection,
    machine_id: str,
    root_path: str | None,
) -> CarrierView:
    try:
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        catalog = _read_catalog(connection)
    except sqlite3.Error as caught:
        raise MetadataStoreInvalid(
            f"the metadata store cannot be read: {caught}"
        ) from caught
    if application_id == 0 and user_version == 0 and not catalog:
        # Interrupted store creation: pre-stamp initialization residue.
        return CarrierView(LifecycleState.METADATA_LESS, None, None, 0)
    if application_id != APPLICATION_ID:
        raise MetadataStoreInvalid(
            f"application_id {application_id} is not this engine's "
            f"({APPLICATION_ID})"
        )
    if user_version == 2:
        if catalog != V2_EXPECTED_CATALOG:
            raise MetadataStoreInvalid(
                "user_version 2 with a catalog that is not the exact "
                "pre-lifecycle store"
            )
        return CarrierView(LifecycleState.READ_ONLY_UNSERVICEABLE, None, None, 2)
    if user_version != SCHEMA_VERSION:
        raise MetadataStoreInvalid(
            f"incompatible store version {user_version}; this build knows "
            f"{SCHEMA_VERSION}"
        )
    if catalog != EXPECTED_CATALOG:
        raise MetadataStoreInvalid(
            f"schema does not match version {SCHEMA_VERSION}"
        )
    lifecycle = load_root_lifecycle(connection)
    operation = load_root_operation(connection)
    require_root_row_pair(lifecycle, operation)
    if lifecycle is None:
        return CarrierView(LifecycleState.METADATA_LESS, None, operation, 3)
    if root_path is None or (lifecycle.machine_id, lifecycle.root_path) != (
        machine_id,
        root_path,
    ):
        return CarrierView(
            LifecycleState.BINDING_MISMATCHED, lifecycle, operation, 3
        )
    return CarrierView(LifecycleState(lifecycle.state), lifecycle, operation, 3)


def _verify_volume(
    metadata_root_fd: int, storage: StorageProfile, allowlist: object
) -> None:
    """Read-only volume verification: the resolved tuple must be certified."""
    entry = resolve_mount_entry(metadata_root_fd, read_mountinfo())
    configuration = build_configuration(
        entry, kernel_identifier(), directory_fd=metadata_root_fd
    )
    matched = allowlist.match(configuration, storage)  # type: ignore[attr-defined]
    if matched is None:
        raise CapabilityUnavailable(
            "volume configuration is not on the supplied durability allowlist: "
            f"{configuration.filesystem_type} {configuration.barrier_options} "
            f"profile={storage.profile_id!r}"
        )


def _database_stat(metadata_root_fd: int) -> os.stat_result | None:
    try:
        info = os.stat(
            "atoms.db", dir_fd=metadata_root_fd, follow_symlinks=False
        )
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise MetadataStoreInvalid(
            "atoms.db under metadata_root is not a regular file"
        )
    return info


def classify_carrier_under_lock(
    lock: HeldProjectLock,
    root_path: str | None,
    storage: StorageProfile,
    allowlist: object,
) -> CarrierView:
    """Classify under an already-held metadata lock, with reads alone.

    Never creates or repairs a root, metadata directory, lock, database,
    schema, row, or WAL; refuses rather than falling back to a writable open.
    """
    machine_id = _read_machine_identity()
    _verify_volume(lock.metadata_root_fd, storage, allowlist)
    if _database_stat(lock.metadata_root_fd) is None:
        return CarrierView(LifecycleState.METADATA_LESS, None, None, 0)
    connection = _open_read_only(lock.metadata_root_fd, lock.metadata_root_path)
    try:
        return _classify_connection(connection, machine_id, root_path)
    finally:
        connection.close()


def read_state(
    backend: Backend,
    root: str,
    metadata_root: str,
    storage: StorageProfile,
    allowlist: object,
) -> CarrierView:
    """The full standalone query behind `read_lifecycle_state`.

    Existing-only throughout: an absent metadata root, lock, or database is
    metadata-less; an absent root cannot validate any stored row and reads
    binding-mismatched when a row exists.
    """
    root = _require_root_spelling("root", root)
    metadata_root = _require_root_spelling("metadata_root", metadata_root)
    _require_storage(storage)

    root_path: str | None
    try:
        root_fd, root_path, _ = establish_root(backend, root, create=False)
    except OSError as caught:
        if caught.errno not in (errno.ENOENT, errno.ENOTDIR):
            raise
        root_fd, root_path = None, None
    try:
        try:
            metadata_fd, metadata_path, _ = establish_root(
                backend, metadata_root, create=False
            )
        except OSError as caught:
            if caught.errno in (errno.ENOENT, errno.ENOTDIR):
                return CarrierView(LifecycleState.METADATA_LESS, None, None, 0)
            raise
        try:
            try:
                lock_fd = backend.open_existing(
                    metadata_fd, "lock", read_write=True, nofollow=True
                )
            except OSError as caught:
                if caught.errno == errno.ENOENT:
                    if _database_stat(metadata_fd) is not None:
                        raise MetadataStoreInvalid(
                            "a metadata store exists without its lock file"
                        ) from caught
                    return CarrierView(
                        LifecycleState.METADATA_LESS, None, None, 0
                    )
                raise
            try:
                if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
                    raise MetadataStoreInvalid(
                        "metadata_root/lock is not a regular file"
                    )
                backend.lock_exclusive(lock_fd)
                machine_id = _read_machine_identity()
                _verify_volume(metadata_fd, storage, allowlist)
                if _database_stat(metadata_fd) is None:
                    return CarrierView(
                        LifecycleState.METADATA_LESS, None, None, 0
                    )
                connection = _open_read_only(metadata_fd, metadata_path)
                try:
                    return _classify_connection(
                        connection, machine_id, root_path
                    )
                finally:
                    connection.close()
            finally:
                backend.close_fd(lock_fd)
        finally:
            backend.close_fd(metadata_fd)
    finally:
        if root_fd is not None:
            backend.close_fd(root_fd)


# --- Completion seam (design §5) ---


def _complete_root_operation(store: object, *, final_state: str) -> None:
    """The one atomic lifecycle/complete transaction, last in every creation.

    The lifecycle transition precedes the operation's `tree-durable ->
    complete` update inside the same transaction, which is what lets the
    completion trigger validate the final lifecycle state. Tests cut exactly
    this seam to fabricate the recorded/durable/no-grant window.
    """
    with store.transaction() as txn:  # type: ignore[attr-defined]
        txn.set_root_lifecycle_state(final_state)
        txn.set_root_operation_phase("complete")
