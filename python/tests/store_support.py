"""Builders shared by every store tier."""

from __future__ import annotations

import contextlib
import hashlib
import os
import sqlite3
from contextlib import contextmanager
from typing import Any

from atoms.core.effects import CreateDirectory, CreateFileNoClobber, ReplaceFile
from atoms.core.fingerprint import ABSENT, AbsentState, DirectoryState, FileState, SymlinkState
from atoms.core.recovery.model import (
    CommitDecision,
    DiagnosticEntry,
    DiagnosticIdentityRelation,
    EffectJournalState,
    FileBuildRelation,
    HaltDiagnostic,
    HaltReason,
    IdentityRelation,
    JournalState,
    OperatorAction,
    TransactionState,
)
from atoms.core.spec import build_spec

DATABASE_ENTRIES = ("atoms.db", "atoms.db-wal", "atoms.db-shm", "atoms.db-journal")
SHARED_DIGEST = "sha256:" + "a" * 64
APPROVAL_EVIDENCE = '{"directories":[],"mount_id":1,"work_root":null}'


@contextmanager
def child_dir(root_fd: int, name: str):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=root_fd)
    try:
        yield fd
    finally:
        os.close(fd)


def file_state(content: bytes, mode: int = 0o644) -> FileState:
    return FileState(
        content_hash="sha256:" + hashlib.sha256(content).hexdigest(),
        mode=mode,
        byte_len=len(content),
    )


def digest_of(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def registration_digest(txid: str) -> str:
    return hashlib.sha256(f"registered:{txid}".encode()).hexdigest()


def one_effect_spec(effect_id: str = "e1"):
    """A minimal blob-free spec that compile_spec accepts."""
    post = DirectoryState(mode=0o755)
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "0" * 64,
        initial_surface={"a.txt": ABSENT},
        final_surface={"a.txt": post},
        effects=[CreateDirectory(effect_id=effect_id, path="a.txt", post=post)],
    )


def spec_referencing(*contents: bytes):
    """A create-from-absent spec whose final surface references every content."""
    names = [f"f{index}.txt" for index in range(len(contents))]
    post = {
        name: file_state(content)
        for name, content in zip(names, contents, strict=True)
    }
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "5" * 64,
        initial_surface={name: ABSENT for name in names},
        final_surface=post,
        effects=[
            CreateFileNoClobber(effect_id=f"e{index}", path=name, post=post[name])
            for index, name in enumerate(names)
        ],
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
    """One digest declared at two byte_lens across the initial and final surfaces.

    compile_spec accepts this -- measured -- because it never compares two paths'
    fingerprints to each other. A hash and a length are both properties of the same
    bytes, so the store refuses it under §7.6.
    """
    pre = FileState(content_hash=SHARED_DIGEST, mode=0o644, byte_len=5)
    post = FileState(content_hash=SHARED_DIGEST, mode=0o644, byte_len=6)
    return build_spec(
        consumer_tag="test",
        intent_digest="sha256:" + "3" * 64,
        initial_surface={"a.txt": pre},
        final_surface={"a.txt": post},
        effects=[ReplaceFile(effect_id="e1", path="a.txt", pre=pre, post=post)],
    )


def stage(workspace, name: str, content: bytes) -> None:
    """Write one preparation blob through the borrowed staging anchor."""
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

#: Design §7.1's public Store methods and one valid-enough argument tuple for each.
#: `close` is absent because it is the sole operation that succeeds after close.
STORE_SURFACE: tuple[tuple[str, tuple[object, ...]], ...] = (
    ("transaction", ()),
    ("read_record", ("tx1",)),
    ("read_active", ()),
    ("open_blob", ("sha256:" + "a" * 64,)),
    ("list_unindexed_blobs", ()),
    ("remove_unindexed_blob", ("sha256:" + "a" * 64,)),
    ("create_workspace", ("tx1",)),
    ("reopen_workspace", ("tx1",)),
    ("list_workspaces", ()),
    ("remove_workspace", (None,)),
)


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


class CorruptsStatement:
    """A connection proxy injecting SQLITE_CORRUPT at one exact execute call."""

    def __init__(
        self, connection: sqlite3.Connection, statement: str, *, occurrence: int = 1
    ) -> None:
        self._connection = connection
        self._statement = statement
        self._occurrence = occurrence
        self._seen = 0

    def execute(self, statement: str, *parameters: Any):
        if statement == self._statement:
            self._seen += 1
            if self._seen == self._occurrence:
                error = sqlite3.DatabaseError("synthetic corruption")
                error.sqlite_errorcode = sqlite3.SQLITE_CORRUPT  # type: ignore[attr-defined]
                raise error
        return self._connection.execute(statement, *parameters)

    def __getattr__(self, name: str):
        return getattr(self._connection, name)


def every_diagnostic_shape() -> tuple[HaltDiagnostic, ...]:
    """One diagnostic per structurally distinct shape: empty tuples, populated tuples,
    a null effect_id, and every optional field on both settings."""
    populated = HaltDiagnostic(
        pre_halt_state=TransactionState.APPLYING,
        commit_decision=CommitDecision.COMMITTED,
        journals=(
            EffectJournalState(effect_id="e1", state=JournalState.DONE),
            EffectJournalState(effect_id="e2", state=JournalState.UNDO_STARTED),
        ),
        projected_transaction_state=TransactionState.HALTED,
        projected_journals=(EffectJournalState(effect_id="e1", state=JournalState.DONE),),
        effect_id="e2",
        paths=("a.txt", "dir/b.txt"),
        expected=(
            DiagnosticEntry(
                slot="pre", state=file_state(b"x"), has_unmodeled_child=False,
                file_build_relation=FileBuildRelation.EXACT,
            ),
        ),
        observed=(
            DiagnosticEntry(
                slot="post", state=DirectoryState(mode=0o750), has_unmodeled_child=True,
                file_build_relation=None,
            ),
            DiagnosticEntry(
                slot="link", state=SymlinkState(target="x", mode=0o777),
                has_unmodeled_child=None, file_build_relation=FileBuildRelation.DIVERGED,
            ),
        ),
        identity_relations=(
            DiagnosticIdentityRelation(
                left_slot="pre", right_slot="post", relation=IdentityRelation.SAME
            ),
        ),
        reason=HaltReason.EFFECT_TUPLE_UNATTRIBUTABLE,
        operator_action=OperatorAction.REPAIR_DURABLE_METADATA,
    )
    empty = HaltDiagnostic(
        pre_halt_state=TransactionState.ROLLING_BACK,
        commit_decision=CommitDecision.UNCOMMITTED,
        journals=(),
        projected_transaction_state=TransactionState.HALTED,
        projected_journals=(),
        effect_id=None,
        paths=(),
        expected=(DiagnosticEntry(
            slot="only", state=AbsentState(), has_unmodeled_child=None,
            file_build_relation=None,
        ),),
        observed=(),
        identity_relations=(),
        reason=HaltReason.DIRECTORY_NOT_EMPTY,
        operator_action=OperatorAction.INSPECT_PRESERVED_EVIDENCE,
    )
    return (populated, empty)


def matching_diagnostic(effect_id: str = "only") -> HaltDiagnostic:
    """A diagnostic that AGREES with a one_effect_spec record's durable rows.

    §7.6 compares a stored diagnostic's commit_decision and journal vector against the
    record row and the effect rows, so a diagnostic assembled at random cannot be
    committed at all. A test that needs a *coherent* HALTED record on disk -- to then
    break one specific thing about it -- needs this one.
    """
    return HaltDiagnostic(
        pre_halt_state=TransactionState.PREPARED,
        commit_decision=CommitDecision.UNCOMMITTED,
        journals=(EffectJournalState(effect_id=effect_id, state=JournalState.PENDING),),
        projected_transaction_state=TransactionState.HALTED,
        projected_journals=(),
        effect_id=effect_id,
        paths=("a.txt",),
        expected=(),
        observed=(),
        identity_relations=(),
        reason=HaltReason.DIRECTORY_NOT_EMPTY,
        operator_action=OperatorAction.INSPECT_PRESERVED_EVIDENCE,
    )


def non_compiling_spec(effect_id: str = "only"):
    return build_spec(
        consumer_tag="test", intent_digest="sha256:" + "4" * 64,
        initial_surface={"a.txt": ABSENT}, final_surface={"a.txt": ABSENT},
        effects=[CreateFileNoClobber(effect_id=effect_id, path="a.txt", post=file_state(b"after"))],
    )


def commit_record(store, txid: str, spec, *contents: bytes) -> None:
    from atoms.store.blobs import StagedBlob
    from atoms.store.records import referenced_digests

    referenced = set(referenced_digests(spec))
    supplied = {(digest_of(content), len(content)): content for content in contents}
    assert set(supplied) == referenced
    if not referenced:
        with store.transaction() as txn:
            txn.insert_record(txid, spec, approval_evidence=APPROVAL_EVIDENCE)
            txn.set_registration_digest(txid, registration_digest(txid))
        return

    with store.create_workspace(txid) as workspace:
        manifest = []
        for index, ((digest, byte_len), content) in enumerate(sorted(supplied.items())):
            name = f"blob-{index}"
            stage(workspace, name, content)
            manifest.append(StagedBlob(name=name, digest=digest, byte_len=byte_len))
        with store.transaction() as txn:
            txn.promote_staging(workspace, tuple(manifest))
            txn.insert_record(txid, spec, approval_evidence=APPROVAL_EVIDENCE)
            txn.set_registration_digest(txid, registration_digest(txid))
