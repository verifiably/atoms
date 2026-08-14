"""Content-addressed blobs under the engine-owned metadata root (design §7.2, §8)."""

from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
from dataclasses import dataclass
from typing import TYPE_CHECKING

from atoms.core.errors import ProtocolError
from atoms.fs.lock import close_all
from atoms.store.errors import MetadataStoreInvalid, translated
from atoms.store.records import SELECT_BLOB
from atoms.store.workspace import STAGING_PARENT, Workspace

if TYPE_CHECKING:
    from atoms.store.connection import Store

BLOBS_DIRECTORY = "blobs"
SHA256_DIRECTORY = "sha256"
BLOBS_PARENT = f"{BLOBS_DIRECTORY}/{SHA256_DIRECTORY}"
DIGEST_PATTERN = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_READ_CHUNK = 1 << 20
INSERT_BLOB = (
    "INSERT INTO blob (digest, byte_len) VALUES (?, ?) "
    "ON CONFLICT(digest) DO NOTHING"
)
_BEGIN_IMMEDIATE_SQL = "BEGIN IMMEDIATE"
_COMMIT_SQL = "COMMIT"


@dataclass(frozen=True, slots=True)
class StagedBlob:
    """One entry of a promotion manifest. Deliberately unguarded (design §7.1)."""

    name: str
    digest: str
    byte_len: int


def require_digest(value: object) -> str:
    """Design §5.5: exact str, then the grammar."""
    if type(value) is not str:
        raise ProtocolError(f"a digest must be exactly str, got {type(value).__name__}")
    if DIGEST_PATTERN.fullmatch(value) is None:
        raise ProtocolError(f"digest {value!r} is not sha256:<64 lowercase hex>")
    return value


def require_component(label: str, value: object) -> str:
    """Require a single pathname component."""
    if type(value) is not str:
        raise ProtocolError(f"{label} must be exactly str, got {type(value).__name__}")
    if not value or value in (".", "..") or "/" in value or "\x00" in value:
        raise ProtocolError(f"{label} {value!r} is not a single pathname component")
    return value


def digest_to_leaf(digest: str) -> str:
    """The only database-key-to-filename conversion."""
    return require_digest(digest).split(":", 1)[1]


def leaf_to_digest(leaf: str) -> str:
    return require_digest(f"sha256:{leaf}")


def leaf_to_digest_or_refuse(leaf: str) -> str:
    """Require the exact name shape promotion produces under blobs/sha256/."""
    try:
        return leaf_to_digest(leaf)
    except ProtocolError as caught:
        raise MetadataStoreInvalid(
            f"{BLOBS_PARENT}/{leaf!r} is not a name promotion could have written; "
            "leaf names are fixed-width hex by construction"
        ) from caught


def _blobs_fd(store: Store) -> int:
    backend = store._binding._backend
    blobs_fd = backend.open_child_directory(
        store._binding.metadata_root_fd, BLOBS_DIRECTORY
    )
    try:
        return backend.open_child_directory(blobs_fd, SHA256_DIRECTORY)
    finally:
        backend.close_fd(blobs_fd)


def open_entry_nofollow(backend, parent_fd: int, name: str, what: str) -> int:
    """Open a leaf without following symlinks, translating that forbidden shape."""
    try:
        return backend.open_regular_nofollow(parent_fd, name)
    except OSError as caught:
        if caught.errno == errno.ELOOP:
            raise MetadataStoreInvalid(f"{what} is a symlink") from caught
        if caught.errno == errno.ENXIO:
            raise MetadataStoreInvalid(
                f"{what} is not a regular file; no permitted producer could have written it"
            ) from caught
        raise


def verify_leaf(fd: int, digest: str, byte_len: int | None) -> int:
    """Require a regular file with the indexed length and SHA-256, then rewind it."""
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise MetadataStoreInvalid(f"the leaf for {digest} is not a regular file")
    if byte_len is not None and info.st_size != byte_len:
        raise MetadataStoreInvalid(
            f"the leaf for {digest} is {info.st_size} bytes, the index says {byte_len}"
        )
    digester = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while chunk := os.read(fd, _READ_CHUNK):
        digester.update(chunk)
    observed = f"sha256:{digester.hexdigest()}"
    if observed != digest:
        raise MetadataStoreInvalid(f"the leaf named {digest} hashes to {observed}")
    os.lseek(fd, 0, os.SEEK_SET)
    return info.st_size


def open_blob(store: Store, digest: str) -> int:
    """Verify an indexed blob and transfer its descriptor to the caller."""
    require_digest(digest)
    with store._read_transaction() as connection:
        with translated("reading blob membership"):
            row = connection.execute(SELECT_BLOB, (digest,)).fetchone()
        if row is None:
            raise ProtocolError(f"{digest} is not indexed by this store")
        byte_len = row[0]

    store._require_live()
    backend = store._binding._backend
    try:
        parent = _blobs_fd(store)
    except FileNotFoundError as caught:
        raise MetadataStoreInvalid(
            f"{digest} has a blob row but no leaf under {BLOBS_PARENT}/"
        ) from caught
    try:
        try:
            fd = open_entry_nofollow(
                backend,
                parent,
                digest_to_leaf(digest),
                f"the leaf for {digest}",
            )
        except FileNotFoundError as caught:
            raise MetadataStoreInvalid(
                f"{digest} has a blob row but no leaf under {BLOBS_PARENT}/"
            ) from caught
    finally:
        backend.close_fd(parent)
    try:
        verify_leaf(fd, digest, byte_len)
        store._require_live()
        backend.detach_fd(fd)
    except BaseException:
        backend.close_fd(fd)
        raise
    return fd


def list_unindexed_blobs(store: Store) -> tuple[str, ...]:
    """Return verified leaves with no blob row, from one deferred read snapshot."""
    store._require_live()
    backend = store._binding._backend
    parent = _blobs_fd(store)
    try:
        orphans: list[str] = []
        with store._read_transaction() as connection:
            for leaf in sorted(os.listdir(parent)):
                digest = leaf_to_digest_or_refuse(leaf)
                with translated("reading blob membership during reclamation"):
                    row = connection.execute(SELECT_BLOB, (digest,)).fetchone()
                try:
                    fd = open_entry_nofollow(
                        backend, parent, leaf, f"the leaf for {digest}"
                    )
                except FileNotFoundError as caught:
                    raise MetadataStoreInvalid(
                        f"the leaf for {digest} disappeared during enumeration"
                    ) from caught
                try:
                    verify_leaf(fd, digest, None if row is None else row[0])
                finally:
                    backend.close_fd(fd)
                if row is None:
                    orphans.append(digest)
    finally:
        backend.close_fd(parent)
    store._require_live()
    return tuple(orphans)


def remove_unindexed_blob(store: Store, digest: str) -> None:
    """Verify and unlink one still-unindexed leaf under a writer lock."""
    from atoms.store.connection import _rollback_quietly, gate

    require_digest(digest)
    store._require_live()
    if store._active_transaction is not None:
        raise ProtocolError(
            "remove_unindexed_blob opens its own write transaction and cannot nest "
            "inside this Store's write transaction"
        )
    with translated("beginning reclamation"):
        store._connection.execute(_BEGIN_IMMEDIATE_SQL)
    backend = store._binding._backend
    try:
        with translated("rechecking blob membership during reclamation"):
            row = store._connection.execute(SELECT_BLOB, (digest,)).fetchone()
        if row is not None:
            raise ProtocolError(
                f"{digest} is indexed; remove_unindexed_blob can only delete something "
                "no record names"
            )
        parent = _blobs_fd(store)
        try:
            try:
                fd = open_entry_nofollow(
                    backend,
                    parent,
                    digest_to_leaf(digest),
                    f"the leaf for {digest}",
                )
            except FileNotFoundError as caught:
                raise ProtocolError(
                    f"no leaf for {digest}; the argument is stale, re-enumerate"
                ) from caught
            try:
                verify_leaf(fd, digest, None)
            finally:
                backend.close_fd(fd)
            gate(store._binding)
            try:
                backend.unlink_child(parent, digest_to_leaf(digest))
            except FileNotFoundError as caught:
                raise ProtocolError(
                    f"no leaf for {digest}; the argument is stale, re-enumerate"
                ) from caught
            backend.flush_directory(parent)
        finally:
            backend.close_fd(parent)
        gate(store._binding)
        with translated("ending reclamation"):
            store._connection.execute(_COMMIT_SQL)
    except BaseException:
        _rollback_quietly(store._connection)
        raise


def _preflight(store: Store, workspace: Workspace, manifest: tuple[StagedBlob, ...]) -> None:
    """Validate the complete batch before the first irreversible transfer."""
    backend = store._binding._backend
    if type(manifest) is not tuple:
        raise ProtocolError(f"the manifest must be exactly tuple, got {type(manifest).__name__}")
    lengths: dict[str, int] = {}
    names: set[str] = set()
    for entry in manifest:
        if type(entry) is not StagedBlob:
            raise ProtocolError(
                "every manifest element must be exactly StagedBlob, got "
                f"{type(entry).__name__}"
            )
        require_component("a manifest name", entry.name)
        require_digest(entry.digest)
        if type(entry.byte_len) is not int:
            raise ProtocolError(
                f"byte_len must be exactly int, got {type(entry.byte_len).__name__}; "
                "bool is an int subclass and is refused"
            )
        if entry.byte_len < 0:
            raise ProtocolError(f"byte_len {entry.byte_len} is negative")
        if entry.name in names:
            raise ProtocolError(
                f"manifest name {entry.name!r} appears twice; the second rename would "
                "fail ENOENT mid-batch"
            )
        names.add(entry.name)
        previous = lengths.setdefault(entry.digest, entry.byte_len)
        if previous != entry.byte_len:
            raise ProtocolError(
                f"digest {entry.digest} carries byte_len {previous} and {entry.byte_len} "
                "in one manifest; both descriptions arrived in the same argument"
            )

    staging_fd = workspace.staging_fd
    present = set(os.listdir(staging_fd))
    if present != names:
        raise ProtocolError(
            f"the manifest does not describe staging/{workspace.txid}/ exactly: "
            f"missing {sorted(present - names)}, absent {sorted(names - present)}"
        )

    for entry in manifest:
        fd = open_entry_nofollow(
            backend,
            staging_fd,
            entry.name,
            f"staging/{workspace.txid}/{entry.name}",
        )
        try:
            verify_leaf(fd, entry.digest, entry.byte_len)
        finally:
            backend.close_fd(fd)

    parent = _blobs_fd(store)
    try:
        for digest in sorted(lengths):
            with translated("reading blob membership during promotion"):
                row = store._connection.execute(SELECT_BLOB, (digest,)).fetchone()
            if row is None:
                continue
            if row[0] != lengths[digest]:
                raise MetadataStoreInvalid(
                    f"the stored row for {digest} says byte_len {row[0]}, the verified "
                    f"content is {lengths[digest]}"
                )
            try:
                fd = open_entry_nofollow(
                    backend,
                    parent,
                    digest_to_leaf(digest),
                    f"the leaf for {digest}",
                )
            except FileNotFoundError as caught:
                raise MetadataStoreInvalid(
                    f"{digest} is indexed but its leaf is gone; promotion does not "
                    "silently re-create a blob open_blob calls unreadable"
                ) from caught
            try:
                verify_leaf(fd, digest, row[0])
            finally:
                backend.close_fd(fd)
    finally:
        backend.close_fd(parent)


def promote_staging(
    store: Store, workspace: Workspace, manifest: tuple[StagedBlob, ...]
) -> None:
    """Publish and index one complete staging manifest."""
    from atoms.store.connection import gate

    if type(workspace) is not Workspace:
        raise ProtocolError(f"expected exactly Workspace, got {type(workspace).__name__}")
    if workspace._store is not store:
        raise ProtocolError("this workspace belongs to a different Store")
    _preflight(store, workspace, manifest)
    gate(store._binding)

    backend = store._binding._backend
    staging_fd = workspace.staging_fd
    parent = _blobs_fd(store)
    spent = False
    try:
        for entry in manifest:
            gate(store._binding)
            leaf = digest_to_leaf(entry.digest)
            try:
                backend.transfer_noclobber(staging_fd, entry.name, parent, leaf)
                spent = True
            except FileExistsError:
                existing = open_entry_nofollow(
                    backend,
                    parent,
                    leaf,
                    f"the existing leaf for {entry.digest}",
                )
                try:
                    verify_leaf(existing, entry.digest, entry.byte_len)
                finally:
                    backend.close_fd(existing)
                gate(store._binding)
                backend.unlink_child(staging_fd, entry.name)
                spent = True
        backend.flush_directory(parent)
        backend.flush_directory(staging_fd)
        remaining = os.listdir(staging_fd)
        if remaining:
            raise ProtocolError(
                f"staging/{workspace.txid}/ still holds {sorted(remaining)} after the "
                "rename loop; step 1's entry-set comparison and the loop disagree"
            )
    finally:
        if spent:
            try:
                close_all(backend, (staging_fd, parent))
            finally:
                workspace._staging_fd = None
        else:
            backend.close_fd(parent)

    staging_parent = backend.open_child_directory(
        store._binding.metadata_root_fd, STAGING_PARENT
    )
    try:
        gate(store._binding)
        backend.rmdir_child(staging_parent, workspace.txid)
        workspace._spend_staging()
        backend.flush_directory(staging_parent)
    finally:
        backend.close_fd(staging_parent)

    for entry in manifest:
        with translated("indexing a promoted blob"):
            store._connection.execute(INSERT_BLOB, (entry.digest, entry.byte_len))
