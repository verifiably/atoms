"""Metadata layout, probe reclamation, and verified pathnames (design §7.2, §7.3, §9.4)."""

from __future__ import annotations

import os
import stat

from atoms.core.errors import ProtocolError
from atoms.fs.lock import HeldProjectLock, close_all

METADATA_LAYOUT = ("probe", "staging", "work", "blobs/sha256")
PROBE_DIRECTORY = "probe"
WORK_DIRECTORY = "work"


def _open_or_create_child(backend, parent_fd: int, name: str) -> int:
    """mkdirat, tolerate EEXIST, then ALWAYS reopen through guarded traversal.

    Step two runs on both paths, created and pre-existing, and that is the point:
    tolerating EEXIST without reopening would accept whatever already occupies the
    name — a symlink pointing out of the store, or a regular file — as though we
    had created it.
    """
    try:
        backend.mkdir_child(parent_fd, name, 0o700)
    except FileExistsError:
        pass
    fd = backend.open_child_directory(parent_fd, name)
    # The verification runs under the descriptor's ownership, not beside it: an fstat
    # that raises leaves `fd` exactly as open as one that reports the wrong type, so
    # closing only on the wrong-type branch leaks on the other.
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise ProtocolError(f"metadata layout component is not a directory: {name!r}")
    except BaseException:
        backend.close_fd(fd)
        raise
    return fd


def close_layout(backend, retained: dict[str, int]) -> None:
    """Release retained layout descriptors in reverse opening order (design §7.2, §9.3).

    `retained` is insertion-ordered by METADATA_LAYOUT, so `values()` is *opening*
    order and must be reversed. Reverse order is the rule for nested descriptors: a
    child is released before the parent it was reached through, so no release ever
    depends on a descriptor that is already gone.

    This is the single place that knows the ordering. Production and the test helper
    both call it, so neither can release in an order the other does not.
    """
    close_all(backend, reversed(list(retained.values())))


def ensure_metadata_layout(lock: HeldProjectLock) -> dict[str, int]:
    """Create and verify every layout directory. Returns retained descriptors."""
    backend = lock.backend
    retained: dict[str, int] = {}
    for relative in METADATA_LAYOUT:
        parent_fd = lock.metadata_root_fd
        opened: list[int] = []
        try:
            for component in relative.split("/"):
                fd = _open_or_create_child(backend, parent_fd, component)
                opened.append(fd)
                parent_fd = fd
        except BaseException:
            # Every descriptor opened so far is released in one pass, so a close that
            # fails part-way does not abandon the rest. If a close does fail, that
            # failure propagates with the original refusal as its __context__ — the
            # same rule the binding's final reclamation follows.
            #
            # Acquisition order is `retained` then this component's `opened` chain,
            # parent to child, so reversing the concatenation is reverse acquisition
            # order: deepest first, and the lock's metadata root — which we never
            # opened — untouched.
            close_all(backend, reversed([*retained.values(), *opened]))
            raise
        retained[relative] = opened[-1]
        # The intermediate ancestors of the retained leaf, deepest first.
        try:
            close_all(backend, reversed(opened[:-1]))
        except BaseException as first:
            # The leaf is already in `retained`, so ownership cannot be returned to a
            # caller when an intermediate release fails. Unwind the complete retained
            # set before propagating that FIRST release failure. A retained-layout
            # close failure remains its context but cannot replace it.
            try:
                close_layout(backend, retained)
            except OSError:
                raise first
            raise
    return retained


def _remove_tree(backend, parent_fd: int, name: str) -> None:
    try:
        entry = os.lstat(name, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(entry.st_mode):
        # Never follow a symlink: unlink the link itself.
        backend.unlink_child(parent_fd, name)
        return
    child_fd = backend.open_child_directory(parent_fd, name)
    try:
        for inner in os.listdir(child_fd):
            _remove_tree(backend, child_fd, inner)
    finally:
        backend.close_fd(child_fd)
    backend.rmdir_child(parent_fd, name)


def reclaim_probe_survivors(lock: HeldProjectLock) -> None:
    """Empty `probe/` unconditionally. Never creates it.

    Under the held lock there can be no live probe but our own, so no attempt is
    made to distinguish a live probe from debris. `probe/` itself is different:
    a symlink or non-directory there is refused rather than unlinked, because
    A4a should not destroy something it did not create, and because metadata_root
    is engine-owned space whose invariant is already broken if that occurs.
    """
    backend = lock.backend
    root_fd = lock.metadata_root_fd
    try:
        entry = os.lstat(PROBE_DIRECTORY, dir_fd=root_fd)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(entry.st_mode):
        raise ProtocolError(
            f"metadata_root/{PROBE_DIRECTORY} is not a directory; refusing to unlink it"
        )
    probe_fd = backend.open_child_directory(root_fd, PROBE_DIRECTORY)
    try:
        for inner in os.listdir(probe_fd):
            _remove_tree(backend, probe_fd, inner)
    finally:
        backend.close_fd(probe_fd)


def verified_child_path(
    metadata_root_fd: int,
    metadata_root_path: str,
    expected_device: int,
    expected_inode: int,
    name: str,
) -> str:
    """Return `<verified metadata_root>/<name>` after re-confirming the root's identity.

    SQLite is the authority's one path-addressed exception: sqlite3.connect opens
    by pathname through its own VFS, so no descriptor can participate. This is the
    verify-then-open the authority requires, performed at the moment of use rather
    than trusted from bootstrap. Both the SQLite probe and A5 go through it.
    """
    if (
        not name
        or name in (".", "..")
        or "/" in name
        or os.sep in name
        or "\x00" in name
    ):
        raise ProtocolError(f"not a single path component: {name!r}")
    info = os.fstat(metadata_root_fd)
    if (info.st_dev, info.st_ino) != (expected_device, expected_inode):
        raise ProtocolError("metadata root identity changed since binding")
    return os.path.join(metadata_root_path, name)
