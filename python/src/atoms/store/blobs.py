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
from atoms.store.errors import MetadataStoreInvalid
from atoms.store.records import SELECT_BLOB

if TYPE_CHECKING:
    from atoms.store.connection import Store

BLOBS_PARENT = "blobs/sha256"
DIGEST_PATTERN = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_READ_CHUNK = 1 << 20


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


def _blobs_fd(store: Store) -> int:
    return os.open(
        BLOBS_PARENT,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
        dir_fd=store._binding.metadata_root_fd,
    )


def open_entry_nofollow(parent_fd: int, name: str, what: str) -> int:
    """Open a leaf without following symlinks, translating that forbidden shape."""
    try:
        return os.open(
            name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd
        )
    except OSError as caught:
        if caught.errno == errno.ELOOP:
            raise MetadataStoreInvalid(f"{what} is a symlink") from caught
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
        row = connection.execute(SELECT_BLOB, (digest,)).fetchone()
        if row is None:
            raise ProtocolError(f"{digest} is not indexed by this store")
        byte_len = row[0]

    store._require_live()
    try:
        parent = _blobs_fd(store)
    except FileNotFoundError as caught:
        raise MetadataStoreInvalid(
            f"{digest} has a blob row but no leaf under {BLOBS_PARENT}/"
        ) from caught
    try:
        try:
            fd = open_entry_nofollow(parent, digest_to_leaf(digest), f"the leaf for {digest}")
        except FileNotFoundError as caught:
            raise MetadataStoreInvalid(
                f"{digest} has a blob row but no leaf under {BLOBS_PARENT}/"
            ) from caught
    finally:
        os.close(parent)
    try:
        verify_leaf(fd, digest, byte_len)
        store._require_live()
    except BaseException:
        os.close(fd)
        raise
    return fd
