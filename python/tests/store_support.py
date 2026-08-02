"""Builders shared by every store tier."""

from __future__ import annotations

import contextlib
import hashlib
import os
import sqlite3
from typing import Any

from atoms.core.effects import CreateFileNoClobber, ReplaceFile
from atoms.core.fingerprint import ABSENT, FileState
from atoms.core.spec import build_spec

DATABASE_ENTRIES = ("atoms.db", "atoms.db-wal", "atoms.db-shm", "atoms.db-journal")
SHARED_DIGEST = "sha256:" + "a" * 64


def file_state(content: bytes, mode: int = 0o644) -> FileState:
    return FileState(
        content_hash="sha256:" + hashlib.sha256(content).hexdigest(),
        mode=mode,
        byte_len=len(content),
    )


def digest_of(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def one_effect_spec(effect_id: str = "e1", content: bytes = b"after"):
    """A minimal spec that compile_spec accepts: one CreateFileNoClobber."""
    post = file_state(content)
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "0" * 64,
        initial_surface={"a.txt": ABSENT},
        final_surface={"a.txt": post},
        effects=[CreateFileNoClobber(effect_id=effect_id, path="a.txt", post=post)],
    )


def replace_spec(effect_id: str = "e1", before: bytes = b"before", after: bytes = b"after"):
    pre, post = file_state(before), file_state(after)
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "1" * 64,
        initial_surface={"a.txt": pre},
        final_surface={"a.txt": post},
        effects=[ReplaceFile(effect_id=effect_id, path="a.txt", pre=pre, post=post)],
    )


def duplicate_effect_spec():
    """Two effects sharing one effect_id, for design §7.7's poison rule.

    build_spec does not validate and insert_record does not compile, so the `effect`
    PRIMARY KEY is what refuses -- on the *second* INSERT, after the record row and the
    first effect row of the same method have already been written. That is the shape the
    poison rule exists for.
    """
    post = file_state(b"after")
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "2" * 64,
        initial_surface={"a.txt": ABSENT, "b.txt": ABSENT},
        final_surface={"a.txt": post, "b.txt": post},
        effects=[
            CreateFileNoClobber(effect_id="dup", path="a.txt", post=post),
            CreateFileNoClobber(effect_id="dup", path="b.txt", post=post),
        ],
    )


def two_length_spec():
    """One digest declared at two byte_lens across the initial surface.

    compile_spec accepts this -- measured -- because it never compares two paths'
    fingerprints to each other. A hash and a length are both properties of the same
    bytes, so the store refuses it under §7.6.
    """
    pre_a = FileState(content_hash=SHARED_DIGEST, mode=0o644, byte_len=5)
    pre_b = FileState(content_hash=SHARED_DIGEST, mode=0o644, byte_len=6)
    post_a, post_b = file_state(b"one"), file_state(b"two")
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "3" * 64,
        initial_surface={"a.txt": pre_a, "b.txt": pre_b},
        final_surface={"a.txt": post_a, "b.txt": post_b},
        effects=[
            ReplaceFile(effect_id="e1", path="a.txt", pre=pre_a, post=post_a),
            ReplaceFile(effect_id="e2", path="b.txt", pre=pre_b, post=post_b),
        ],
    )


def stage(workspace, name: str, content: bytes) -> None:
    """Write one capture into staging/<txid>/ through the borrowed anchor, as A6 does."""
    fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=workspace.staging_fd)
    try:
        os.write(fd, content)
    finally:
        os.close(fd)


def entries_of(fd: int) -> set[str]:
    return set(os.listdir(fd))


def raw_connect(binding) -> sqlite3.Connection:
    """A connection that bypasses the store, for asserting on durable rows directly.

    Takes a **live** binding: it goes through `verified_metadata_path`, which calls
    `_require_active` (`binding.py:139`), so a test that has already released the
    binding gets `ProtocolError` here rather than the row count it was asking for. Use
    `raw_path(binding)` before releasing when the assertion has to outlive the binding.
    """
    return sqlite3.connect(raw_path(binding), isolation_level=None)


def raw_path(binding) -> str:
    """The verified absolute path to `atoms.db`, captured while the binding is alive.

    A `str` outlives the binding; the descriptor behind it does not. This exists because
    several liveness tests release the lock mid-operation and then have to assert on
    what is durable -- which is exactly when `raw_connect(binding)` no longer works.
    """
    return binding.verified_metadata_path("atoms.db")


@contextlib.contextmanager
def metadata_root_snapshot(binding):
    """A `metadata_root` descriptor that survives the lease ending.

    `HeldProjectLock.__exit__` closes the metadata-root descriptor it owns
    (`lock.py:203`) and `ProjectBinding` refuses to hand it out once inactive, so a test
    that ends a lease mid-operation and then wants to look at the directory has to have
    duplicated it first. `os.dup` shares the open file description, so the duplicate
    names the same directory whatever happens to the path or to the original.
    """
    fd = os.dup(binding.metadata_root_fd)
    try:
        yield fd
    finally:
        os.close(fd)


def release_lock(binding) -> None:
    """Release the project lock while leaving the binding open.

    `binding.__exit__()` is **not** this: it closes the project-root descriptor and sets
    `_active = False` (`binding.py:150-153`), so `_require_active` refuses at its first
    check and the second one is never reached. That second check --
    `if not self._lock.held` (`binding.py:113`) -- is a distinct branch guarding a
    distinct real state: A5b's lease can end while the binding object it was built on is
    still perfectly alive. A test that only ever calls `binding.__exit__()` proves the
    gate refuses a closed binding and says nothing about a released lease.

    The lock is private to the binding on purpose (§8.3's reasoning for `_store`), so
    this reaches through the slot. It lives here once rather than at each call site so
    that the reach is a single reviewed line.
    """
    binding._lock.__exit__(None, None, None)


def open_descriptor_count() -> int:
    """How many descriptors this process holds, for leak assertions.

    Counts rather than compares sets: `listdir` of `/proc/self/fd` needs a descriptor of
    its own, which appears in its own listing and takes whichever number is free -- so
    two listings can differ in *which* numbers they contain while holding the same count.
    The transient one is present in both, so a difference of one is a leak. `/proc` is
    already a hard requirement here (`require_platform`), so this is not a new one.
    """
    return len(os.listdir("/proc/self/fd"))


def close_binding(binding) -> None:
    binding.__exit__(None, None, None)


#: The two ways a lease ends, and they are **not** the same branch of the gate.
#: `close_binding` trips `_require_active`'s first check (`binding.py:111`);
#: `release_lock` leaves the binding active and trips the second
#: (`if not self._lock.held`, `binding.py:113`). A suite that only ever closes the
#: binding proves the gate refuses a closed binding and says nothing about a lease that
#: ended under a binding still in use -- which is the state A5b actually produces, since
#: the lock is what the lease *is* and the binding object outlives it.
#:
#: Every gate tier is parametrized over both, so a gate that happened to read only
#: `binding.active` would fail half of them.
RELEASES = (close_binding, release_lock)


class CommitFails:
    """A connection proxy whose COMMIT raises with the transaction left open.

    `sqlite3.Connection` is an immutable type -- `monkeypatch.setattr` on its `execute`
    raises `TypeError: cannot set 'execute' attribute of immutable type`, measured -- so
    the injection has to be a proxy installed on `Store._connection`. Everything except
    COMMIT forwards, `in_transaction` included, which is what lets the assertion be on
    the connection's real state rather than on the exception alone.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def execute(self, statement: str, *parameters: Any):
        if statement.strip().upper() == "COMMIT":
            raise sqlite3.OperationalError("disk I/O error")
        return self._connection.execute(statement, *parameters)

    def __getattr__(self, name: str):
        return getattr(self._connection, name)
