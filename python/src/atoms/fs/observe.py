"""The coherent observation mechanism (design §6).

One `Observation` is one pass and owns one token universe. It produces the primitive
facts A3 consumes and never a verdict: this module may import `atoms.core.recovery.model`
and no other `core.recovery` module, which is how ledger #13's "may not pre-classify them
into a recovery outcome" becomes a mechanical property rather than a review promise. An
architecture test asserts the whitelist over every form the import can take: a direct
`from atoms.core.recovery import ...`, a plain `import atoms.core.recovery`, a
`from atoms.core import recovery` that names the parent package, and the relative
equivalent of any of these.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import os
import stat
from collections.abc import Iterator
from typing import Self

from atoms.core.errors import CapabilityUnavailable, PreconditionRefused, ProtocolError
from atoms.core.fingerprint import DirectoryState, FileState, SymlinkState
from atoms.core.recovery.model import (
    OBSERVED_ABSENT,
    EntryIdentity,
    FileBuildRelation,
    ObservedDirectory,
    ObservedEntry,
    ObservedFile,
    ObservedSymlink,
)
from atoms.fs.backend import Backend

_READ_CHUNK = 1 << 20

# Errnos with a defined domain meaning after approval (design §9.1). Everything else
# propagates with its own class and traceback -- A5a's rule for SQLite result codes,
# applied to errno. Reads, writes, and flushes raise EIO, ENOSPC, EROFS and more, and
# none of those is external state contradicting the frozen spec.
_NAMESPACE_CONTRADICTIONS = frozenset(
    {errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EXDEV}
)
_UNSUPPORTED = frozenset({errno.ENOSYS, errno.EOPNOTSUPP, errno.ENOTSUP})


@contextlib.contextmanager
def translated_lookup(context: str) -> Iterator[None]:
    """Translate one narrow operation's errno. Wrap a statement, never a protocol."""
    try:
        yield
    except OSError as caught:
        if caught.errno in _NAMESPACE_CONTRADICTIONS:
            raise PreconditionRefused(
                f"the namespace no longer matches approval while {context}: {caught}"
            ) from caught
        if caught.errno in _UNSUPPORTED:
            raise CapabilityUnavailable(
                f"the backend cannot supply the semantics needed while {context}: {caught}"
            ) from caught
        if caught.errno == errno.EBADF:
            raise ProtocolError(
                f"a descriptor was already closed while {context}: {caught}"
            ) from caught
        raise


class Observation:
    """One pass, one token universe, one pinned descriptor per observed identity.

    `(st_dev, st_ino)` identifies an entry uniquely only while its inode stays
    allocated. Recording the key and closing the descriptor would let an unlink and a
    create recycle that inode and map two sequentially distinct entries onto one token --
    an identity equality A3 would believe. The retained descriptor makes the reuse
    impossible rather than unlikely.

    Every descriptor this class opens is either handed to `_pin` -- which owns it from
    that moment -- or closed on the way out. There is no path on which one is neither.
    """

    __slots__ = ("_backend", "_closed", "_pins", "_tokens")

    def __init__(self, backend: Backend) -> None:
        self._backend = backend
        self._tokens: dict[tuple[int, int], EntryIdentity] = {}
        self._pins: dict[EntryIdentity, int] = {}
        self._closed = False

    def observe(
        self,
        parent_fd: int,
        leaf: str,
        *,
        sink_fd: int | None = None,
        modeled: frozenset[str] | None = None,
    ) -> ObservedEntry:
        """One entry, observed coherently.

        `modeled` is REQUIRED when the entry turns out to be a directory: occupancy is
        evidence, and reporting `has_unmodeled_child=False` without enumerating would be
        a failure to look recorded as a finding of absence.
        """
        self._require_open()
        _require_leaf(leaf)
        with translated_lookup(f"looking up {leaf!r}"):
            try:
                info = os.lstat(leaf, dir_fd=parent_fd)
            except FileNotFoundError:
                return OBSERVED_ABSENT
        if stat.S_ISLNK(info.st_mode):
            return self._observe_symlink(parent_fd, leaf)
        if stat.S_ISDIR(info.st_mode):
            if modeled is None:
                raise ProtocolError(
                    f"{leaf!r} is a directory; observing one requires its modeled child "
                    "names, because occupancy evidence may not be fabricated"
                )
            return self._observe_directory(parent_fd, leaf, modeled)
        if stat.S_ISREG(info.st_mode):
            return self._observe_file(parent_fd, leaf, sink_fd)
        raise PreconditionRefused(
            f"{leaf!r} is neither a regular file, directory, nor symlink "
            f"(st_mode {info.st_mode:#o}); no declared state can describe it"
        )

    def pinned_descriptor(self, identity: EntryIdentity) -> int:
        """The retained descriptor for an observed identity. Borrowed, never closed."""
        self._require_open()
        pinned = self._pins.get(identity)
        if pinned is None:
            raise ProtocolError("that identity was not observed in this pass")
        return pinned

    def build_relation(self, staged_fd: int, planned_fd: int) -> FileBuildRelation:
        """Compare a staged object with the planned blob (design §6.3).

        The planned blob arrives as an OPEN DESCRIPTOR supplied by the caller, never a
        digest this module resolves: that is both the coherence rule and what keeps
        `atoms.store` out of `atoms/fs/`.
        """
        self._require_open()
        os.lseek(staged_fd, 0, os.SEEK_SET)
        os.lseek(planned_fd, 0, os.SEEK_SET)
        while True:
            staged = _read_exactly(staged_fd, _READ_CHUNK)
            planned = _read_exactly(planned_fd, _READ_CHUNK)
            if staged == planned:
                if not staged:
                    return FileBuildRelation.EXACT
                continue
            shortest = min(len(staged), len(planned))
            if staged[:shortest] != planned[:shortest]:
                return FileBuildRelation.DIVERGED
            # One side ran out first. A short read cannot cause this: `_read_exactly`
            # returns fewer bytes only at end of file.
            return (
                FileBuildRelation.STRICT_PREFIX
                if len(staged) < len(planned)
                else FileBuildRelation.DIVERGED
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for fd in self._pins.values():
            os.close(fd)
        self._pins.clear()
        self._tokens.clear()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _observe_file(self, parent_fd: int, leaf: str, sink_fd: int | None) -> ObservedFile:
        identity, info = self._open_and_pin(
            lambda: self._backend.open_regular_nofollow(parent_fd, leaf),
            leaf,
            stat.S_ISREG,
            "a regular file",
        )
        # Stream from the PINNED descriptor: one descriptor per identity, and any
        # failure below leaves it owned by the pass rather than orphaned.
        pinned = self._pins[identity]
        os.lseek(pinned, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        length = 0
        while True:
            chunk = os.read(pinned, _READ_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            length += len(chunk)
            if sink_fd is not None:
                _write_all(sink_fd, chunk)
        return ObservedFile(
            state=FileState(
                content_hash="sha256:" + digest.hexdigest(),
                mode=stat.S_IMODE(info.st_mode),
                byte_len=length,
            ),
            identity=identity,
        )

    def _observe_directory(
        self, parent_fd: int, leaf: str, modeled: frozenset[str]
    ) -> ObservedDirectory:
        identity, info = self._open_and_pin(
            lambda: self._backend.open_child_directory(parent_fd, leaf),
            leaf,
            stat.S_ISDIR,
            "a directory",
        )
        with translated_lookup(f"enumerating {leaf!r}"):
            present = os.listdir(self._pins[identity])
        return ObservedDirectory(
            state=DirectoryState(mode=stat.S_IMODE(info.st_mode)),
            identity=identity,
            has_unmodeled_child=any(name not in modeled for name in present),
        )

    def _observe_symlink(self, parent_fd: int, leaf: str) -> ObservedSymlink:
        with translated_lookup(f"fingerprinting symlink {leaf!r}"):
            info, target = self._backend.symlink_fingerprint(parent_fd, leaf)
        # No descriptor and no identity: O_NOFOLLOW fails by design on a symlink leaf,
        # so there is nothing to be coherent about (design §6.2).
        return ObservedSymlink(
            state=SymlinkState(target=target, mode=stat.S_IMODE(info.st_mode))
        )

    def _open_and_pin(
        self, opener, leaf: str, predicate, description: str
    ) -> tuple[EntryIdentity, os.stat_result]:
        """Open, confirm the kind, and transfer ownership -- or close and raise."""
        with translated_lookup(f"opening {leaf!r}"):
            fd = opener()
        try:
            info = os.fstat(fd)
            if not predicate(info.st_mode):
                raise PreconditionRefused(
                    f"{leaf!r} stopped being {description} between lookup and open"
                )
        except BaseException:
            os.close(fd)
            raise
        return self._pin(info, fd), info

    def _pin(self, info: os.stat_result, fd: int) -> EntryIdentity:
        """Takes ownership of `fd` unconditionally: it is retained or closed here."""
        key = (info.st_dev, info.st_ino)
        existing = self._tokens.get(key)
        if existing is not None:
            os.close(fd)
            return existing
        token = EntryIdentity()
        self._tokens[key] = token
        self._pins[token] = fd
        return token

    def _require_open(self) -> None:
        if self._closed:
            raise ProtocolError("this observation pass is closed")


def _require_leaf(leaf: str) -> None:
    if type(leaf) is not str:
        raise ProtocolError(f"a leaf must be exactly str, got {type(leaf).__name__}")
    if not leaf or leaf in (".", "..") or "/" in leaf or "\x00" in leaf:
        raise ProtocolError(f"{leaf!r} is not a single pathname component")


def _read_exactly(fd: int, size: int) -> bytes:
    """Read up to `size`, returning short only at end of file."""
    parts: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.read(fd, remaining)
        if not chunk:
            break
        parts.append(chunk)
        remaining -= len(chunk)
    return b"".join(parts)


def _write_all(fd: int, chunk: bytes) -> None:
    view = memoryview(chunk)
    while view:
        view = view[os.write(fd, view) :]
