"""Root establishment and the advisory project lock (design §5.4, §7.1)."""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterable
from typing import Never, Self

from atoms.core.errors import CapabilityUnavailable, ProtocolError
from atoms.fs.backend import UNSUPPORTED_ERRNO, Backend

SYNC_IGNORE_ATTRIBUTE = "user.com.dropbox.ignored"

_TOKEN = object()


def close_all(backend: Backend, fds: Iterable[int]) -> None:
    """Close every descriptor, attempting each, then raise the first failure.

    A failed close does not un-open the descriptors after it, so a plain loop that
    lets the first failure escape leaks every later one for the process lifetime.
    The first failure is the one raised, because it is the one with a cause; later
    ones are almost always the same cause repeated.

    Used wherever more than one descriptor is released at once — the lock's two, and
    the layout's one per component — so the rule lives in one place rather than being
    re-derived at each site. Called from a `finally` or an unwind path, a failure here
    propagates and the original exception becomes its `__context__`.
    """
    first: OSError | None = None
    for fd in fds:
        try:
            backend.close_fd(fd)
        except OSError as caught:
            if first is None:
                first = caught
    if first is not None:
        raise first


def _guarded_spelling(path: str) -> str:
    """Absolute form of `path` with empty and '.' components dropped, '..' KEPT.

    os.path.abspath must NOT be used before the guarded walk: it calls normpath,
    which collapses '..' lexically, so 'aliased/../elsewhere' becomes 'elsewhere'
    and RESOLVE_NO_SYMLINKS never sees 'aliased' at all. Dropping '' and '.' is safe
    under any symlink arrangement — neither changes which entry a path names — but
    every '..' must reach the kernel, which refuses it exactly when an earlier
    component is a symlink and resolves it normally otherwise.

    os.getcwd() is itself symlink-free, so prefixing it introduces no component the
    kernel would have to resolve.
    """
    absolute = path if os.path.isabs(path) else os.path.join(os.getcwd(), path)
    kept = [component for component in absolute.split(os.sep) if component not in ("", ".")]
    return os.sep + os.sep.join(kept)


def _guarded_open(backend: Backend, path: str) -> int:
    """open_root, converting an unavailable guarded walk per design §10.

    Reads the shared backend table rather than a local copy, so a kernel without
    openat2 surfaces identically here and in the probe.
    """
    try:
        return backend.open_root(path)
    except OSError as caught:
        if caught.errno in UNSUPPORTED_ERRNO["traversal"]:
            raise CapabilityUnavailable(
                f"anchored_traversal is unavailable, so no root can be guarded: {path!r}"
            ) from caught
        raise


def establish_root(backend: Backend, path: str, create: bool) -> tuple[int, str, bool]:
    """Open a root through guarded traversal, optionally creating its final leaf.

    Returns (descriptor, normalized path, created). Normalization runs only AFTER
    the guarded walk succeeds, and that ordering is the whole point: the walk proves
    no component is a symlink, and only then can collapsing '..' not change which
    entry the path names.
    """
    if not path:
        raise ProtocolError("root spelling must not be empty")
    if "\x00" in path:
        raise ProtocolError(f"root spelling contains a NUL byte: {path!r}")
    spelled = _guarded_spelling(path)
    try:
        fd = _guarded_open(backend, spelled)
    except OSError as caught:
        if not create or caught.errno != errno.ENOENT:
            raise
    else:
        return fd, os.path.normpath(spelled), False
    parent, leaf = os.path.split(spelled)
    if not leaf or leaf == "..":
        raise ProtocolError(
            f"cannot create a root whose final component is {leaf!r}: {path!r}"
        )
    # Only the final leaf is created, and only relative to a guarded parent
    # descriptor. A missing parent refuses: a component walk that creates as it
    # goes has races this layer has no need to take on.
    parent_fd = _guarded_open(backend, parent)
    try:
        backend.mkdir_child(parent_fd, leaf, 0o700)
        # Reopen through guarded traversal even though we just created it, so the
        # descriptor is guard-checked on the same terms as the existing-root case.
        fd = backend.open_child_directory(parent_fd, leaf)
    except BaseException:
        backend.close_fd(parent_fd)
        raise
    # `fd` is owned from here on, so BOTH remaining steps run under that ownership.
    # A `finally: os.close(parent_fd)` around the block above would leak `fd` if that
    # close failed. A later child-close failure is retained as context but cannot
    # replace that first parent-close failure.
    try:
        backend.close_fd(parent_fd)
    except BaseException as first:
        try:
            backend.close_fd(fd)
        except OSError:
            raise first
        raise
    # An `fstat` that raises leaves `fd` exactly as open as one reporting the wrong
    # type — the leak is in the validation, not just its verdict.
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise ProtocolError(f"created metadata root is not a directory: {spelled!r}")
    except BaseException:
        backend.close_fd(fd)
        raise
    return fd, os.path.normpath(spelled), True


class HeldProjectLock:
    """Proof that the project lock is held, with the metadata root open.

    Factory-controlled: acquire_project_lock is the sole construction authority,
    because reclaim_probe_survivors, probe_backend, and bind_project_volume all
    treat this type as proof rather than re-checking.
    """

    __slots__ = ("_backend", "_held", "_lock_fd", "_root_fd", "_root_path")

    def __init__(self, *, _construction_token: object | None = None, **kwargs) -> None:
        if _construction_token is not _TOKEN:
            raise TypeError("HeldProjectLock values are created only by acquire_project_lock")
        self._backend = kwargs["backend"]
        self._root_fd = kwargs["root_fd"]
        self._root_path = kwargs["root_path"]
        self._lock_fd = kwargs["lock_fd"]
        self._held = True

    def __copy__(self) -> Never:
        raise TypeError("HeldProjectLock cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> Never:
        raise TypeError("HeldProjectLock cannot be deep-copied")

    def __reduce__(self) -> Never:
        raise TypeError("HeldProjectLock cannot be pickled")

    def __reduce_ex__(self, protocol: int) -> Never:
        raise TypeError("HeldProjectLock cannot be pickled")

    def _require_held(self) -> None:
        if not self._held:
            raise ProtocolError("the project lock has been released")

    @property
    def backend(self) -> Backend:
        self._require_held()
        return self._backend

    @property
    def metadata_root_fd(self) -> int:
        self._require_held()
        return self._root_fd

    @property
    def metadata_root_path(self) -> str:
        self._require_held()
        return self._root_path

    @property
    def held(self) -> bool:
        return self._held

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        if not self._held:
            return
        self._held = False
        # Both descriptors are released even if the first close fails. A failed close
        # does not un-open the second, and stopping there would leak the metadata root
        # for the process lifetime — while `held` already reads False, so nothing would
        # ever retry it. The lock descriptor goes first: closing it is what releases
        # flock, and that must not depend on the root closing cleanly.
        close_all(self._backend, (self._lock_fd, self._root_fd))


def acquire_existing_project_lock(
    backend: Backend, metadata_root: str, *, blocking: bool = True
) -> HeldProjectLock | None:
    """Existing-only acquisition: opens the root and lock, creates neither.

    The lifecycle read paths and the mutator gate must be able to hold the
    metadata lock without conjuring a metadata root for a tree that has none —
    creation stays `acquire_project_lock`'s. An absent root or lock propagates
    the OSError; the caller classifies it (usually as metadata-less). With
    ``blocking=False`` a busy lock returns None instead of waiting, which is
    the copy commands' second-lock discipline.
    """
    root_fd, root_path, created = establish_root(backend, metadata_root, create=False)
    assert not created
    try:
        lock_fd = backend.open_existing(
            root_fd, "lock", read_write=True, nofollow=True
        )
    except BaseException:
        backend.close_fd(root_fd)
        raise
    try:
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise ProtocolError("metadata_root/lock is not a regular file")
        if blocking:
            try:
                backend.lock_exclusive(lock_fd)
            except OSError as caught:
                if caught.errno in UNSUPPORTED_ERRNO["lock"]:
                    raise CapabilityUnavailable(
                        "advisory_project_lock is unavailable on this volume, so "
                        "access to metadata_root cannot be serialized"
                    ) from caught
                raise
        elif not backend.try_lock_exclusive(lock_fd):
            close_all(backend, (lock_fd, root_fd))
            return None
    except BaseException:
        close_all(backend, (lock_fd, root_fd))
        raise
    return HeldProjectLock(
        _construction_token=_TOKEN,
        backend=backend,
        root_fd=root_fd,
        root_path=root_path,
        lock_fd=lock_fd,
    )


def acquire_project_lock(backend: Backend, metadata_root: str) -> HeldProjectLock:
    root_fd, root_path, created = establish_root(backend, metadata_root, create=True)
    try:
        if created:
            # Best-effort: the metadata store is single-host by construction, so a
            # synced copy is neither required nor trusted. Any failure is swallowed
            # and weakens no single-host guarantee.
            try:
                backend.set_marker_xattr(root_fd, SYNC_IGNORE_ATTRIBUTE, b"1")
            except OSError:
                pass
        lock_fd = backend.create_or_open(root_fd, "lock", 0o600)
    except BaseException:
        backend.close_fd(root_fd)
        raise
    try:
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise ProtocolError("metadata_root/lock is not a regular file")
        try:
            backend.lock_exclusive(lock_fd)
        except OSError as caught:
            if caught.errno in UNSUPPORTED_ERRNO["lock"]:
                raise CapabilityUnavailable(
                    "advisory_project_lock is unavailable on this volume, so access to "
                    "metadata_root cannot be serialized"
                ) from caught
            raise
    except BaseException:
        # One pass, not two sequential closes: a failure closing `lock_fd` must not
        # abandon `root_fd`, and this unwind runs on the CapabilityUnavailable path
        # that an unlockable volume takes, where a leak would be permanent.
        close_all(backend, (lock_fd, root_fd))
        raise
    return HeldProjectLock(
        _construction_token=_TOKEN,
        backend=backend,
        root_fd=root_fd,
        root_path=root_path,
        lock_fd=lock_fd,
    )
