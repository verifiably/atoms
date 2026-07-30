"""Empirical capability probing (design §8).

These are FUNCTIONAL probes, not conformance tests. They establish that an
operation is present and behaves correctly on this volume right now; they cannot
establish its power-loss guarantee. Flushing a file and its parent successfully is
availability evidence only. The crash claim is carried solely by a matched
allowlist entry.
"""

from __future__ import annotations

import contextlib
import errno
import os
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Iterator

from atoms.core.capabilities import Capability
from atoms.core.errors import CapabilityUnavailable
from atoms.fs.backend import UNSUPPORTED_ERRNO, Backend
from atoms.fs.lock import HeldProjectLock


class ChildExit:
    """Exit codes the §8.3 certification children use to report a specific verdict.

    Named rather than literal so a later reordering cannot silently remap "refused
    with the wrong result code" onto "read the wrong value" — two very different
    conclusions about a volume.
    """

    OK = 0
    STALE_READ = 3
    ACQUIRED_WHILE_HELD = 4
    WRONG_REFUSAL = 5


def _supported(operation: str, probe) -> bool:
    """Run `probe`; report absence only for an errno that conclusively means it.

    This decides *availability*. It is not the right tool for a probe step whose
    evidence is a refusal — see `_refused_with`.
    """
    try:
        probe()
    except OSError as caught:
        if caught.errno in UNSUPPORTED_ERRNO[operation]:
            return False
        raise
    return True


def _refused_with(expected: int, open_attempt) -> bool:
    """True if `open_attempt` refused with exactly `expected`; False if it succeeded.

    A guard is proved by the exact errno it refuses with, never by "some OSError
    happened". Accepting any error here would let a volume failing for an unrelated
    reason — EBADF from a descriptor bug, EIO from failing media — report the guard
    as working, which is design §10's propagation rule read backwards. Anything
    other than `expected` therefore propagates.

    `open_attempt` returns a descriptor when the guard fails to refuse; it is closed
    here so a failed guard does not also leak.
    """
    try:
        opened = open_attempt()
    except OSError as caught:
        if caught.errno == expected:
            return True
        raise
    os.close(opened)
    return False


def _write(parent_fd: int, name: str, payload: bytes) -> None:
    fd = os.open(
        name,
        os.O_CREAT | os.O_WRONLY | os.O_EXCL | os.O_CLOEXEC,
        0o600,
        dir_fd=parent_fd,
    )
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)


def _read(parent_fd: int, name: str) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC, dir_fd=parent_fd)
    try:
        return os.read(fd, 64)
    finally:
        os.close(fd)


def _clear(parent_fd: int) -> None:
    for name in os.listdir(parent_fd):
        info = os.lstat(name, dir_fd=parent_fd)
        if stat.S_ISDIR(info.st_mode):
            os.rmdir(name, dir_fd=parent_fd)
        else:
            os.unlink(name, dir_fd=parent_fd)


@contextlib.contextmanager
def _staged(probe_fd: int) -> Iterator[None]:
    """Empty `probe_fd` on the way out, whatever the body managed to create in it.

    Entered BEFORE anything is created, and that is the whole point: a probe that
    writes two operands and fails on the second must not leave the first behind.
    `_clear` walks `listdir` rather than a list of expected names, so it removes
    exactly what exists however far staging got.
    """
    try:
        yield
    finally:
        _clear(probe_fd)


@contextlib.contextmanager
def _child_pair(backend: Backend, probe_fd: int) -> Iterator[tuple[int, int]]:
    """Two distinct child directories under `probe_fd`, released in reverse order.

    Both descriptors are acquired *inside* the stack, so failing to open the second
    still closes the first — the leak a `src_fd = ...; dst_fd = ...; try:` prologue
    quietly takes on. ExitStack unwinds in reverse registration order, so each
    directory is emptied before its own descriptor closes and `probe/` is emptied
    last, once `src` and `dst` are empty enough to remove. It also keeps unwinding
    after a callback raises, so one failing release cannot strand the others.

    This is heterogeneous cleanup, not a `close_all` batch: ExitStack preserves its
    standard chained-exception behavior if more than one clear/close callback fails.
    The first-failure precedence contract applies only within one explicit descriptor
    batch passed to `close_all`.
    """
    with contextlib.ExitStack() as stack:
        stack.callback(_clear, probe_fd)
        os.mkdir("src", mode=0o700, dir_fd=probe_fd)
        os.mkdir("dst", mode=0o700, dir_fd=probe_fd)
        src_fd = backend.open_child_directory(probe_fd, "src")
        stack.callback(os.close, src_fd)
        stack.callback(_clear, src_fd)
        dst_fd = backend.open_child_directory(probe_fd, "dst")
        stack.callback(os.close, dst_fd)
        stack.callback(_clear, dst_fd)
        yield src_fd, dst_fd


def _probe_traversal(backend: Backend, probe_fd: int) -> bool:
    with _staged(probe_fd):
        os.mkdir("real", mode=0o700, dir_fd=probe_fd)
        os.symlink("real", "escape", dir_fd=probe_fd)
        # One open, not two: the availability check and the descriptor it produces are
        # the same call. Opening again to "get a real one" would discard a descriptor.
        opened: list[int] = []

        def attempt():
            opened.append(backend.open_child_directory(probe_fd, "real"))

        if not _supported("traversal", attempt):
            return False
        os.close(opened[0])
        # RESOLVE_NO_SYMLINKS reports a symlink component as ELOOP; RESOLVE_BENEATH
        # reports an escape as EXDEV. Both refusals must arrive with exactly that
        # code, or the guard is not what proved itself.
        for refused, expected in (("escape", errno.ELOOP), ("..", errno.EXDEV)):
            if not _refused_with(
                expected,
                lambda name=refused: backend.open_child_directory(probe_fd, name),
            ):
                return False
        return True


def _probe_lock(backend: Backend, lock: HeldProjectLock) -> bool:
    # flock is per open file description, so a second open in this process
    # contends correctly against the already-held lock.
    contender = os.open(
        "lock", os.O_RDWR | os.O_CLOEXEC, dir_fd=lock.metadata_root_fd
    )
    try:
        acquired = None

        def attempt():
            nonlocal acquired
            acquired = backend.try_lock_exclusive(contender)

        if not _supported("lock", attempt):
            return False
        return acquired is False
    finally:
        os.close(contender)


def _probe_exchange(backend: Backend, probe_fd: int) -> bool:
    with _staged(probe_fd):
        _write(probe_fd, "left", b"L")
        _write(probe_fd, "right", b"R")
        if not _supported(
            "exchange", lambda: backend.exchange(probe_fd, "left", "right")
        ):
            return False
        return _read(probe_fd, "left") == b"R" and _read(probe_fd, "right") == b"L"


def _probe_transfer(backend: Backend, probe_fd: int) -> bool:
    # The distinct-parent form is what blob promotion and staging publication use.
    with _child_pair(backend, probe_fd) as (src_fd, dst_fd):
        _write(src_fd, "payload", b"P")
        _write(dst_fd, "payload", b"occupied")
        blocked = False
        try:
            backend.transfer_noclobber(src_fd, "payload", dst_fd, "payload")
        except OSError as caught:
            if caught.errno in UNSUPPORTED_ERRNO["transfer_noclobber"]:
                return False
            if caught.errno != errno.EEXIST:
                raise
            blocked = True
        if not blocked:
            return False
        os.unlink("payload", dir_fd=dst_fd)
        backend.transfer_noclobber(src_fd, "payload", dst_fd, "payload")
        return _read(dst_fd, "payload") == b"P"


def _probe_link(backend: Backend, probe_fd: int) -> bool:
    with _child_pair(backend, probe_fd) as (src_fd, dst_fd):
        _write(src_fd, "payload", b"P")
        if not _supported(
            "link_anchor",
            lambda: backend.link_anchor(src_fd, "payload", dst_fd, "anchor"),
        ):
            return False
        source = os.stat("payload", dir_fd=src_fd)
        anchor = os.stat("anchor", dir_fd=dst_fd)
        return (source.st_dev, source.st_ino) == (anchor.st_dev, anchor.st_ino) and (
            source.st_nlink == 2
        )


def _probe_flush(backend: Backend, probe_fd: int) -> bool:
    with _staged(probe_fd):
        _write(probe_fd, "payload", b"P")
        fd = os.open("payload", os.O_RDONLY | os.O_CLOEXEC, dir_fd=probe_fd)
        try:

            def attempt():
                backend.flush_file(fd)
                backend.flush_directory(probe_fd)

            return _supported("flush", attempt)
        finally:
            # Inside _staged, so the descriptor closes before probe/ is emptied.
            os.close(fd)


def _probe_nofollow_read(backend: Backend, probe_fd: int) -> bool:
    with _staged(probe_fd):
        _write(probe_fd, "payload", b"P")
        os.symlink("payload", "alias", dir_fd=probe_fd)
        opened: list[int] = []

        def attempt():
            opened.append(backend.open_regular_nofollow(probe_fd, "payload"))

        if not _supported("open_regular_nofollow", attempt):
            return False
        try:
            if not stat.S_ISREG(os.fstat(opened[0]).st_mode):
                return False
            if os.read(opened[0], 8) != b"P":
                return False
        finally:
            os.close(opened[0])
        # O_NOFOLLOW on a symlink leaf refuses with exactly ELOOP. Any other errno
        # is an unrelated failure and must not be read as a working guard.
        return _refused_with(
            errno.ELOOP,
            lambda: backend.open_regular_nofollow(probe_fd, "alias"),
        )


def _probe_symlink_fingerprint(backend: Backend, probe_fd: int) -> bool:
    with _staged(probe_fd):
        # symlink(2) reports EPERM when this filesystem cannot create symlinks,
        # and EPERM is part of symlink_fingerprint's own unsupported set. That is
        # why creation belongs inside this capability's _supported call. This is
        # specific to that documented basis, not a general staging rule for the
        # traversal and nofollow probes.
        if not _supported(
            "symlink_fingerprint",
            lambda: os.symlink("../target", "alias", dir_fd=probe_fd),
        ):
            return False
        captured: list[tuple] = []

        def attempt():
            captured.append(backend.symlink_fingerprint(probe_fd, "alias"))

        if not _supported("symlink_fingerprint", attempt):
            return False
        info, target = captured[0]
        return stat.S_ISLNK(info.st_mode) and target == "../target"


def probe_backend(
    backend: Backend, probe_root_fd: int, lock: HeldProjectLock
) -> frozenset[Capability]:
    """Return exactly the capabilities this volume supplies. The sole producer."""
    _clear(probe_root_fd)
    supplied: set[Capability] = set()
    try:
        if _probe_traversal(backend, probe_root_fd):
            supplied.add(Capability.ANCHORED_TRAVERSAL)
        if _probe_lock(backend, lock):
            supplied.add(Capability.ADVISORY_PROJECT_LOCK)
        if _probe_exchange(backend, probe_root_fd):
            supplied.add(Capability.ATOMIC_EXCHANGE)
        if _probe_transfer(backend, probe_root_fd):
            supplied.add(Capability.NOCLOBBER_TRANSFER)
        if _probe_link(backend, probe_root_fd):
            supplied.add(Capability.IDENTITY_ANCHOR)
        if _probe_flush(backend, probe_root_fd):
            supplied.add(Capability.DURABLE_PUBLISH)
        if _probe_nofollow_read(backend, probe_root_fd):
            supplied.add(Capability.NOFOLLOW_COHERENT_READ)
        if _probe_symlink_fingerprint(backend, probe_root_fd):
            supplied.add(Capability.SYMLINK_FINGERPRINT)
        return frozenset(supplied)
    finally:
        _clear(probe_root_fd)


# Design §8.3 steps 4-5: read the parent's committed state concurrently with the
# parent's held write lock, then require the write lock to refuse. busy_timeout is 0
# so the refusal is immediate rather than a wait.
_CHILD_CONTENDER = f"""
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1], timeout=0, isolation_level=None)
if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
    sys.exit({ChildExit.STALE_READ})
try:
    connection.execute("BEGIN IMMEDIATE")
except sqlite3.OperationalError as caught:
    # Only SQLITE_BUSY proves cross-process write exclusion. SQLite distinguishes it
    # from I/O, protocol, permission, and internal errors, and a volume whose WAL
    # index never opens at all would raise one of those — indistinguishable from
    # correct exclusion if any OperationalError were accepted. Compare the PRIMARY
    # code so an extended SQLITE_BUSY_* variant still counts.
    if caught.sqlite_errorcode & 0xFF != sqlite3.SQLITE_BUSY:
        sys.exit({ChildExit.WRONG_REFUSAL})
    connection.close()
    sys.exit({ChildExit.OK})
sys.exit({ChildExit.ACQUIRED_WHILE_HELD})
"""

# Design §8.3 step 7, run only after the parent has committed. A SEPARATE invocation:
# the parent cannot wait for a child that is itself waiting for the parent's commit.
_CHILD_WRITER = f"""
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1], timeout=30, isolation_level=None)
connection.execute("BEGIN IMMEDIATE")
connection.execute("PRAGMA user_version=2")
connection.execute("COMMIT")
connection.close()
sys.exit({ChildExit.OK})
"""


_CHILD_TIMEOUT_SECONDS = 60


def _run_child(
    script: str, database_path: str, phase: str
) -> subprocess.CompletedProcess:
    """Run one certification child under a bounded timeout.

    A child that does not finish is a REFUSAL, not an escaping error. Design §8.3 puts
    every certification failure under CapabilityUnavailable, and a TimeoutExpired
    reaching the caller would put the one failure mode a broken-locking volume is most
    likely to produce outside the §10 contract — handing A5 a third exception type to
    know about for no gain. Only TimeoutExpired is caught: an OSError from spawning the
    interpreter is a bug in this process, not a verdict about the volume.

    Each child re-resolves the database by pathname. That is acceptable only because
    probe/ is engine-owned, sits under the held project lock, and contains no
    transaction state; the exemption extends to nothing outside probe/.
    """
    try:
        return subprocess.run(
            [sys.executable, "-c", script, database_path],
            timeout=_CHILD_TIMEOUT_SECONDS,
            capture_output=True,
            check=False,
        )
    except subprocess.TimeoutExpired as caught:
        raise CapabilityUnavailable(
            f"the SQLite-WAL {phase} child did not finish within "
            f"{_CHILD_TIMEOUT_SECONDS}s, so cross-process WAL coordination is unproven"
        ) from caught


def certify_sqlite_wal(database_path: str, cleanup: bool = False) -> None:
    """Certify the volume can host the SQLite-WAL metadata store (design §8.3).

    Opening a database and selecting WAL mode is insufficient: WAL can operate
    without shared memory when SQLite runs in exclusive locking mode. The second
    reader must be a separate PROCESS, because a same-process connection exercises
    WAL but not SQLite's cross-process POSIX locking contract, and the shared-memory
    WAL index exists precisely to coordinate readers across processes.

    The choreography runs as TWO child invocations. The parent must release its write
    lock between the contending read (steps 4-5) and the child write (step 7), and a
    single blocking child cannot express that: the parent would block waiting for a
    child that is blocked waiting for the parent's commit, and the sequence would
    resolve only by one side timing out. Splitting at the release point makes the
    ordering explicit and the outcome deterministic.
    """
    parent = sqlite3.connect(database_path, isolation_level=None)
    try:
        mode = parent.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if str(mode).lower() != "wal":
            raise CapabilityUnavailable(
                f"volume cannot host SQLite in WAL mode (journal_mode={mode!r})"
            )
        parent.execute("PRAGMA synchronous=FULL")
        parent.execute("PRAGMA user_version=1")

        parent.execute("BEGIN IMMEDIATE")
        try:
            contended = _run_child(_CHILD_CONTENDER, database_path, "contention")
        finally:
            # Release before inspecting the verdict, so no refusal path can leave the
            # write lock held while the second child needs it.
            parent.execute("COMMIT")
        if contended.returncode == ChildExit.STALE_READ:
            raise CapabilityUnavailable(
                "a second process could not read the committed WAL state while a "
                "writer held the lock"
            )
        if contended.returncode == ChildExit.ACQUIRED_WHILE_HELD:
            raise CapabilityUnavailable(
                "a second process acquired the write lock while it was held"
            )
        if contended.returncode == ChildExit.WRONG_REFUSAL:
            raise CapabilityUnavailable(
                "a second process was refused the write lock with something other "
                "than SQLITE_BUSY, so cross-process exclusion is unproven"
            )
        if contended.returncode != ChildExit.OK:
            raise CapabilityUnavailable(
                f"SQLite-WAL contention child failed: {contended.stderr!r}"
            )

        wrote = _run_child(_CHILD_WRITER, database_path, "write")
        if wrote.returncode != ChildExit.OK:
            raise CapabilityUnavailable(
                f"a second process could not write once the lock was released: "
                f"{wrote.stderr!r}"
            )
        observed = parent.execute("PRAGMA user_version").fetchone()[0]
        if observed != 2:
            raise CapabilityUnavailable(
                f"the child's committed write was not observed (user_version={observed})"
            )
    finally:
        parent.close()
    if cleanup:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(database_path + suffix)
            except FileNotFoundError:
                pass
