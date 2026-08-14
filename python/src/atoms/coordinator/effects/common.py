"""Shared mechanics for forward and recovery mutations."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from collections.abc import Callable
from typing import TypeVar

from atoms.core.errors import AtomsError, ProtocolError
from atoms.core.fingerprint import FileState
from atoms.fs.audit import Provenance, RootKind
from atoms.store.blobs import BLOBS_PARENT, digest_to_leaf
from atoms.store.connection import Store
from atoms.store.errors import MetadataStoreInvalid

_CHUNK = 64 * 1024
_T = TypeVar("_T")

_DETERMINATE = {
    "unlink_child": frozenset(
        {errno.ENOENT, errno.EISDIR, errno.EBUSY, errno.EACCES, errno.EPERM}
    ),
    "rmdir_child": frozenset(
        {
            errno.ENOENT,
            errno.ENOTDIR,
            errno.EBUSY,
            errno.EACCES,
            errno.EPERM,
            errno.ENOTEMPTY,
            errno.EEXIST,
        }
    ),
    "transfer_noclobber": frozenset(
        {
            errno.ENOENT,
            errno.EEXIST,
            errno.ENOTDIR,
            errno.EXDEV,
            errno.EBUSY,
            errno.EACCES,
            errno.EPERM,
        }
    ),
    "exchange": frozenset(
        {
            errno.ENOENT,
            errno.ENOTDIR,
            errno.EXDEV,
            errno.EBUSY,
            errno.EACCES,
            errno.EPERM,
        }
    ),
    "link_anchor": frozenset(
        {errno.ENOENT, errno.EEXIST, errno.ENOTDIR, errno.EXDEV, errno.EACCES, errno.EPERM}
    ),
    "create_exclusive": frozenset(
        {errno.ENOENT, errno.EEXIST, errno.ENOTDIR, errno.EACCES}
    ),
    "mkdir_child": frozenset(
        {errno.ENOENT, errno.EEXIST, errno.ENOTDIR, errno.EACCES}
    ),
    "repair_entry_mode": frozenset(
        {errno.ENOENT, errno.ENOTDIR, errno.EACCES, errno.EPERM}
    ),
    "lstat": frozenset({errno.ENOENT, errno.ENOTDIR, errno.EACCES}),
    "open_regular_nofollow": frozenset(
        {errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EISDIR, errno.EACCES}
    ),
    "symlink_fingerprint": frozenset(
        {errno.ENOENT, errno.ENOTDIR, errno.EINVAL, errno.EACCES}
    ),
    "open_child_directory": frozenset(
        {errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EXDEV, errno.EACCES}
    ),
}


class EffectMismatch(AtomsError):
    def __init__(
        self, operation: str, slot: str, error_number: int | None = None
    ) -> None:
        self.operation = operation
        self.slot = slot
        self.errno = error_number
        suffix = "" if error_number is None else f" (errno {error_number})"
        super().__init__(f"{operation} disagreed with {slot}{suffix}")


def run_determinate(
    operation: str,
    slot: str,
    call: Callable[[], _T],
    *,
    passthrough: tuple[int, ...] = (),
) -> _T:
    try:
        determinate = _DETERMINATE[operation]
    except KeyError as caught:
        raise ProtocolError(f"unknown determinate operation {operation!r}") from caught
    if any(type(value) is not int for value in passthrough):
        raise ProtocolError("passthrough errnos must be integers")
    try:
        return call()
    except OSError as caught:
        if caught.errno in passthrough:
            raise
        if caught.errno in determinate:
            raise EffectMismatch(operation, slot, caught.errno) from caught
        raise


def stream_blob(backend, store: Store, state: FileState, dest_fd: int) -> None:
    source_fd = store.open_blob(state.content_hash)
    backend.register(
        source_fd,
        Provenance(
            RootKind.METADATA,
            f"{BLOBS_PARENT}/{digest_to_leaf(state.content_hash)}",
        ),
    )
    digest = hashlib.sha256()
    length = 0
    try:
        while chunk := os.read(source_fd, _CHUNK):
            offset = 0
            while offset < len(chunk):
                written = backend.write(dest_fd, chunk[offset:])
                if written <= 0:
                    raise MetadataStoreInvalid("a blob write made no progress")
                digest.update(chunk[offset : offset + written])
                length += written
                offset += written
    finally:
        backend.close_fd(source_fd)
    observed = "sha256:" + digest.hexdigest()
    if observed != state.content_hash or length != state.byte_len:
        raise MetadataStoreInvalid(
            f"blob produced digest {observed} and length {length}, expected "
            f"{state.content_hash} and {state.byte_len}"
        )


def build_staged_file(
    backend, store: Store, parent_fd: int, leaf: str, state: FileState
) -> int:
    fd = run_determinate(
        "create_exclusive",
        leaf,
        lambda: backend.create_exclusive(parent_fd, leaf, 0o600),
    )
    try:
        stream_blob(backend, store, state, fd)
        backend.set_mode(fd, state.mode)
        backend.flush_file(fd)
    except BaseException:
        backend.close_fd(fd)
        raise
    return fd


def verify_live_file(
    backend, retained_fd: int, parent_fd: int, leaf: str, state: FileState
) -> None:
    live = run_determinate(
        "lstat", leaf, lambda: os.lstat(leaf, dir_fd=parent_fd)
    )
    retained = os.fstat(retained_fd)
    if (live.st_dev, live.st_ino) != (retained.st_dev, retained.st_ino):
        raise EffectMismatch("verify_identity", leaf)
    digest = hashlib.sha256()
    length = 0
    os.lseek(retained_fd, 0, os.SEEK_SET)
    while chunk := os.read(retained_fd, _CHUNK):
        digest.update(chunk)
        length += len(chunk)
    if "sha256:" + digest.hexdigest() != state.content_hash or length != state.byte_len:
        raise EffectMismatch("verify_content", leaf)
    if stat.S_IMODE(retained.st_mode) != state.mode:
        raise EffectMismatch("verify_mode", leaf)
