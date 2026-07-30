import errno
import os
import sqlite3
import subprocess

import pytest

from atoms.core.capabilities import Capability
from atoms.core.errors import CapabilityUnavailable
from atoms.fs.backend import UNSUPPORTED_ERRNO
from atoms.fs.lock import acquire_project_lock
from atoms.fs.probe import ChildExit, certify_sqlite_wal, probe_backend
from tests.fs_support import probe_database_path, probe_directory


def test_real_volume_supplies_every_capability(held_lock, metadata_root):
    with held_lock(metadata_root) as lock, probe_directory(lock) as probe_fd:
        supplied = probe_backend(lock.backend, probe_fd, lock)
    assert supplied == frozenset(Capability)


def test_probe_leaves_no_survivors(held_lock, metadata_root):
    with held_lock(metadata_root) as lock, probe_directory(lock) as probe_fd:
        probe_backend(lock.backend, probe_fd, lock)
        assert os.listdir(probe_fd) == []


def test_missing_exchange_is_reported_not_raised(held_lock, metadata_root, fake_backend):
    backend = fake_backend(supplied=set(Capability) - {Capability.ATOMIC_EXCHANGE})
    with held_lock(metadata_root) as lock, probe_directory(lock) as probe_fd:
        supplied = probe_backend(backend, probe_fd, lock)
    assert supplied == frozenset(set(Capability) - {Capability.ATOMIC_EXCHANGE})


_OPTIONAL_CAPABILITIES = tuple(
    capability
    for capability in Capability
    if capability
    not in {Capability.ANCHORED_TRAVERSAL, Capability.ADVISORY_PROJECT_LOCK}
)


@pytest.mark.parametrize("absent", _OPTIONAL_CAPABILITIES)
def test_each_optional_capability_can_be_absent(held_lock, metadata_root, fake_backend, absent):
    backend = fake_backend(supplied=set(Capability) - {absent})
    with held_lock(metadata_root) as lock, probe_directory(lock) as probe_fd:
        supplied = probe_backend(backend, probe_fd, lock)
    assert supplied == frozenset(set(Capability) - {absent})


def test_transfer_reports_absence_when_only_empty_destination_call_is_unsupported(
    held_lock, metadata_root, fake_backend
):
    backend = fake_backend(supplied=set(Capability))
    real_transfer = backend.transfer_noclobber
    calls = 0

    def call_sensitive_transfer(src_fd, src, dst_fd, dst):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(errno.EOPNOTSUPP, "empty-destination transfer unsupported")
        return real_transfer(src_fd, src, dst_fd, dst)

    backend.transfer_noclobber = call_sensitive_transfer
    with held_lock(metadata_root) as lock, probe_directory(lock) as probe_fd:
        supplied = probe_backend(backend, probe_fd, lock)
    assert calls == 2
    assert supplied == frozenset(
        set(Capability) - {Capability.NOCLOBBER_TRANSFER}
    )


def test_unexpected_errno_propagates_rather_than_reporting_absence(
    held_lock, metadata_root, fake_backend
):
    # EBADF is a bug or an environmental failure, never an unsupported operation.
    backend = fake_backend(supplied=set(Capability), exchange_errno=errno.EBADF)
    with held_lock(metadata_root) as lock, probe_directory(lock) as probe_fd:
        with pytest.raises(OSError) as caught:
            probe_backend(backend, probe_fd, lock)
        assert caught.value.errno == errno.EBADF


@pytest.mark.parametrize("code", [errno.EBADF, errno.EIO])
@pytest.mark.parametrize("refused", ["escape", ".."])
def test_a_traversal_refusal_with_the_wrong_errno_propagates(
    held_lock, metadata_root, fake_backend, refused, code
):
    # The traversal probe reads two refusals as evidence its guard works — exactly
    # ELOOP for the symlink component, exactly EXDEV for the escape. Accepting any
    # OSError there would let a volume failing for an unrelated reason report
    # anchored_traversal present, which is the §10 propagation rule read backwards.
    #
    # override_names is what makes this test discriminate. Injected unconditionally,
    # the errno would arrive at the EARLIER availability open of "real" and propagate
    # from _supported, so the assertion would hold without _refused_with ever running
    # — and would keep holding if _refused_with were weakened to accept every OSError.
    # Scoped to one refusal target, each site is exercised on its own.
    backend = fake_backend(
        supplied=set(Capability),
        open_child_directory_errno=code,
        override_names={refused},
    )
    with held_lock(metadata_root) as lock, probe_directory(lock) as probe_fd:
        with pytest.raises(OSError) as caught:
            probe_backend(backend, probe_fd, lock)
        assert caught.value.errno == code


@pytest.mark.parametrize("code", [errno.EBADF, errno.EIO])
def test_a_nofollow_refusal_with_the_wrong_errno_propagates(
    held_lock, metadata_root, fake_backend, code
):
    # Scoped to "alias", the symlink leaf whose ELOOP refusal is the evidence; the
    # "payload" open that establishes availability still succeeds.
    backend = fake_backend(
        supplied=set(Capability),
        open_regular_nofollow_errno=code,
        override_names={"alias"},
    )
    with held_lock(metadata_root) as lock, probe_directory(lock) as probe_fd:
        with pytest.raises(OSError) as caught:
            probe_backend(backend, probe_fd, lock)
        assert caught.value.errno == code


def test_a_failed_second_child_open_leaks_no_descriptor(
    held_lock, metadata_root, fake_backend
):
    # Both child descriptors are acquired inside the cleanup scope, so failing on the
    # second still releases the first. The distinct-parent probes are the only place
    # two are held at once, and a `src_fd = ...; dst_fd = ...; try:` prologue leaks
    # src_fd on exactly this path — invisibly, because the probe still refuses
    # correctly and only the descriptor count betrays it.
    backend = fake_backend(
        supplied=set(Capability),
        open_child_directory_errno=errno.EIO,
        override_names={"dst"},
    )
    with held_lock(metadata_root) as lock, probe_directory(lock) as probe_fd:
        # /proc/self/fd includes the descriptor listdir itself holds, which is the
        # lowest free one in both calls, so the count is stable without a leak.
        before = len(os.listdir("/proc/self/fd"))
        with pytest.raises(OSError) as caught:
            probe_backend(backend, probe_fd, lock)
        assert caught.value.errno == errno.EIO
        assert len(os.listdir("/proc/self/fd")) == before
        assert os.listdir(probe_fd) == []


def test_outer_cleanup_reclaims_a_survivor_after_inner_cleanup_fails_once(
    held_lock, metadata_root, fake_backend, monkeypatch
):
    backend = fake_backend(supplied=set(Capability))
    real_unlink = os.unlink
    failed_once = False

    def fail_one_release(name, *, dir_fd):
        nonlocal failed_once
        if name == "right" and not failed_once:
            failed_once = True
            raise OSError(errno.EIO, "injected release failure")
        return real_unlink(name, dir_fd=dir_fd)

    monkeypatch.setattr("atoms.fs.probe.os.unlink", fail_one_release)
    with held_lock(metadata_root) as lock, probe_directory(lock) as probe_fd:
        with pytest.raises(OSError) as caught:
            probe_backend(backend, probe_fd, lock)
        assert caught.value.errno == errno.EIO
        assert failed_once
        assert os.listdir(probe_fd) == []


def test_initial_cleanup_failure_is_retried_by_the_outer_cleanup(
    held_lock, metadata_root, fake_backend, monkeypatch
):
    backend = fake_backend(supplied=set(Capability))
    real_unlink = os.unlink
    failed_once = False

    def fail_initial_release(name, *, dir_fd):
        nonlocal failed_once
        if name == "stale" and not failed_once:
            failed_once = True
            raise OSError(errno.EIO, "injected initial cleanup failure")
        return real_unlink(name, dir_fd=dir_fd)

    with held_lock(metadata_root) as lock, probe_directory(lock) as probe_fd:
        stale_fd = os.open(
            "stale",
            os.O_CREAT | os.O_WRONLY | os.O_EXCL | os.O_CLOEXEC,
            0o600,
            dir_fd=probe_fd,
        )
        os.close(stale_fd)
        monkeypatch.setattr("atoms.fs.probe.os.unlink", fail_initial_release)
        with pytest.raises(OSError) as caught:
            probe_backend(backend, probe_fd, lock)
        assert caught.value.errno == errno.EIO
        assert failed_once
        assert os.listdir(probe_fd) == []


@pytest.mark.parametrize(
    ("target", "failure_occurrence"),
    [pytest.param("payload", 2, id="transfer"), pytest.param("anchor", 1, id="link")],
)
def test_nested_pair_cleanup_failure_preserves_original_error_and_reclaims_root(
    held_lock,
    metadata_root,
    fake_backend,
    monkeypatch,
    target,
    failure_occurrence,
):
    backend = fake_backend(supplied=set(Capability))
    real_unlink = os.unlink
    target_unlinks = 0

    def fail_nested_release_once(name, *, dir_fd):
        nonlocal target_unlinks
        if name == target:
            target_unlinks += 1
            if target_unlinks == failure_occurrence:
                raise OSError(errno.EIO, f"injected {target} cleanup failure")
        return real_unlink(name, dir_fd=dir_fd)

    monkeypatch.setattr("atoms.fs.probe.os.unlink", fail_nested_release_once)
    with held_lock(metadata_root) as lock, probe_directory(lock) as probe_fd:
        with pytest.raises(OSError) as caught:
            probe_backend(backend, probe_fd, lock)
        assert caught.value.errno == errno.EIO
        assert target_unlinks >= failure_occurrence
        assert os.listdir(probe_fd) == []


def test_sqlite_wal_certification_succeeds_on_the_test_volume(held_lock, metadata_root):
    with held_lock(metadata_root) as lock, probe_database_path(lock) as database:
        certify_sqlite_wal(database)


def test_sqlite_certification_observes_the_commit_across_processes(
    held_lock, metadata_root
):
    with held_lock(metadata_root) as lock, probe_database_path(lock) as database:
        certify_sqlite_wal(database)
        # The second child's committed user_version=2 must be what survives.
        connection = sqlite3.connect(database)
        try:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        finally:
            connection.close()


def test_sqlite_writer_refuses_a_wrong_committed_predecessor(
    held_lock, metadata_root, monkeypatch
):
    real_run = subprocess.run
    calls = 0

    def replace_predecessor_before_writer(argv, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            connection = sqlite3.connect(argv[3], isolation_level=None)
            try:
                connection.execute("PRAGMA user_version=9")
            finally:
                connection.close()
        return real_run(argv, **kwargs)

    monkeypatch.setattr(
        "atoms.fs.probe.subprocess.run", replace_predecessor_before_writer
    )
    with (
        held_lock(metadata_root) as lock,
        probe_database_path(lock) as database,
        pytest.raises(CapabilityUnavailable, match="predecessor"),
    ):
        certify_sqlite_wal(database)
    assert calls == 2


def test_certification_uses_two_children_so_the_parent_can_release_between_them(
    held_lock, metadata_root, monkeypatch
):
    # The parent must release the write lock BETWEEN the contending child and the
    # writing child. One blocking child cannot express that: it would wait for the
    # parent's commit while the parent waited for it to exit, and the choreography
    # would resolve only by one side timing out. Two invocations make the release
    # point explicit, and this test is what stops a later "simplification" back to
    # one child from looking harmless.
    real_run = subprocess.run
    scripts = []

    def recording_run(argv, **kwargs):
        scripts.append(argv[2])
        return real_run(argv, **kwargs)

    monkeypatch.setattr("atoms.fs.probe.subprocess.run", recording_run)
    with held_lock(metadata_root) as lock, probe_database_path(lock) as database:
        certify_sqlite_wal(database)
    assert len(scripts) == 2
    assert "user_version=2" not in scripts[0], "the contending child must not write"
    assert "user_version=2" in scripts[1], "the writing child must run after the release"


@pytest.mark.parametrize(("failing_child", "phase"), [(0, "contention"), (1, "write")])
def test_a_certification_child_that_times_out_refuses(
    held_lock, metadata_root, monkeypatch, failing_child, phase
):
    # A child that never finishes is the failure mode a volume with broken locking is
    # most likely to produce. TimeoutExpired escaping would put it outside the §10
    # contract, where every other certification failure already lands, and would hand
    # A5 a third exception type to know about. The phase must be named: "the write
    # child hung" and "the contention child hung" are different volume diagnoses.
    real_run = subprocess.run
    calls = []

    def timing_out_run(argv, **kwargs):
        calls.append(argv)
        if len(calls) - 1 == failing_child:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return real_run(argv, **kwargs)

    monkeypatch.setattr("atoms.fs.probe.subprocess.run", timing_out_run)
    with (
        held_lock(metadata_root) as lock,
        probe_database_path(lock) as database,
        pytest.raises(CapabilityUnavailable, match=phase),
    ):
        certify_sqlite_wal(database)


def test_sqlite_certification_removes_its_files(held_lock, metadata_root):
    with held_lock(metadata_root) as lock, probe_database_path(lock) as database:
        certify_sqlite_wal(database, cleanup=True)
        # All three of the database, -wal, and -shm names must be gone, so the
        # assertion is on the directory rather than on the three names.
        assert os.listdir(os.path.dirname(database)) == []


def test_sqlite_certification_refuses_when_wal_is_unavailable(tmp_path, monkeypatch):
    class Cursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchone(self):
            return self._rows[0] if self._rows else None

    class RefusingConnection:
        def execute(self, statement, *args):
            # Production calls .fetchone() on what execute returns, so the fake must
            # be cursor-shaped. Returning a bare list raises AttributeError and the
            # test would pass for the wrong reason.
            if "journal_mode" in statement:
                return Cursor([("delete",)])
            return Cursor([])

        def close(self):
            pass

    monkeypatch.setattr(
        "atoms.fs.probe.sqlite3.connect", lambda *a, **k: RefusingConnection()
    )
    with pytest.raises(CapabilityUnavailable, match="WAL"):
        certify_sqlite_wal(str(tmp_path / "x.db"))


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (ChildExit.STALE_READ, "could not read"),
        (ChildExit.ACQUIRED_WHILE_HELD, "acquired the write lock"),
        (ChildExit.WRONG_REFUSAL, "SQLITE_BUSY"),
    ],
)
def test_each_child_verdict_refuses_with_its_own_reason(
    held_lock, metadata_root, monkeypatch, code, expected
):
    # Exit codes are named, not literal, so a reordering cannot silently remap
    # 'refused with the wrong result code' onto 'read the wrong value'. WRONG_REFUSAL
    # is the one that matters most: it is how a volume whose WAL index never opens
    # is kept from masquerading as one that correctly excludes a second writer.
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, code, b"", b"")

    monkeypatch.setattr("atoms.fs.probe.subprocess.run", fake_run)
    with (
        held_lock(metadata_root) as lock,
        probe_database_path(lock) as database,
        pytest.raises(CapabilityUnavailable, match=expected),
    ):
        certify_sqlite_wal(database)


_OPERATION_CAPABILITY = {
    "exchange": Capability.ATOMIC_EXCHANGE,
    "transfer_noclobber": Capability.NOCLOBBER_TRANSFER,
    "link_anchor": Capability.IDENTITY_ANCHOR,
    "flush": Capability.DURABLE_PUBLISH,
    "open_regular_nofollow": Capability.NOFOLLOW_COHERENT_READ,
    "symlink_fingerprint": Capability.SYMLINK_FINGERPRINT,
    "lock": Capability.ADVISORY_PROJECT_LOCK,
    "traversal": Capability.ANCHORED_TRAVERSAL,
}

# Generated from production, with ENOTSUP/EOPNOTSUPP deduplicated by numeric value
# on Linux. A copied list could silently miss a newly admitted table entry.
_EFFECTIVE_UNSUPPORTED_PAIRS = tuple(
    (operation, code)
    for operation, codes in UNSUPPORTED_ERRNO.items()
    for code in sorted(codes)
)

_BOOTSTRAP_OPERATIONS = frozenset({"lock", "traversal"})


def test_operational_enosys_cases_are_in_the_generated_matrix():
    # A current kernel will not naturally return these, so the fake must keep the
    # openat2 and both renameat2 operational-absence paths reachable.
    for operation in ("traversal", "exchange", "transfer_noclobber"):
        assert (operation, errno.ENOSYS) in _EFFECTIVE_UNSUPPORTED_PAIRS


@pytest.mark.parametrize(
    ("operation", "code"),
    [
        pair
        for pair in _EFFECTIVE_UNSUPPORTED_PAIRS
        if pair[0] not in _BOOTSTRAP_OPERATIONS
    ],
)
def test_each_effective_unsupported_errno_removes_exactly_its_capability(
    held_lock, metadata_root, fake_backend, operation, code
):
    # This is the licensed half: the probe owns valid operands under the lock.
    backend = fake_backend(supplied=set(Capability), **{f"{operation}_errno": code})
    with held_lock(metadata_root) as lock, probe_directory(lock) as probe_fd:
        supplied = probe_backend(backend, probe_fd, lock)
    assert supplied == frozenset(
        set(Capability) - {_OPERATION_CAPABILITY[operation]}
    )


@pytest.mark.parametrize(
    ("operation", "code"),
    [
        pair
        for pair in _EFFECTIVE_UNSUPPORTED_PAIRS
        if pair[0] in _BOOTSTRAP_OPERATIONS
    ],
)
def test_each_bootstrap_unsupported_errno_refuses_before_probing(
    metadata_root, fake_backend, operation, code
):
    backend = fake_backend(supplied=set(Capability), **{f"{operation}_errno": code})
    expected = (
        "anchored_traversal"
        if operation == "traversal"
        else "advisory_project_lock"
    )
    with pytest.raises(CapabilityUnavailable, match=expected):
        acquire_project_lock(backend, str(metadata_root))


def _invoke_with_invalid_descriptor(backend, operation, invalid_fd):
    # Non-empty components are load-bearing: an empty pathname produces ENOENT
    # before the kernel consults the invalid descriptor. These calls exercise the
    # real Linux backend outside probe_backend's licensed precondition.
    if operation == "exchange":
        return backend.exchange(invalid_fd, "left", "right")
    if operation == "transfer_noclobber":
        return backend.transfer_noclobber(
            invalid_fd, "source", invalid_fd, "destination"
        )
    if operation == "link_anchor":
        return backend.link_anchor(invalid_fd, "source", invalid_fd, "anchor")
    if operation == "flush":
        return backend.flush_file(invalid_fd)
    if operation == "open_regular_nofollow":
        return backend.open_regular_nofollow(invalid_fd, "entry")
    if operation == "symlink_fingerprint":
        return backend.symlink_fingerprint(invalid_fd, "entry")
    if operation == "lock":
        return backend.try_lock_exclusive(invalid_fd)
    if operation == "traversal":
        return backend.open_child_directory(invalid_fd, "entry")
    raise AssertionError(f"unmapped operation: {operation}")


@pytest.mark.parametrize("operation", tuple(_OPERATION_CAPABILITY))
def test_linux_backend_propagates_ebadf_outside_probe_precondition(
    linux_backend, operation
):
    # This half observes the real backend; dictating an errno through RestrictedBackend
    # would only prove that the test double can raise. EBADF is never availability
    # evidence, so every operation must expose it unchanged.
    assert all(errno.EBADF not in codes for codes in UNSUPPORTED_ERRNO.values())
    with pytest.raises(OSError) as caught:
        # A closed positive descriptor reaches every real syscall. CPython rejects
        # a negative descriptor before fsync/flock with ValueError.
        invalid_fd = os.open(__file__, os.O_RDONLY | os.O_CLOEXEC)
        os.close(invalid_fd)
        _invoke_with_invalid_descriptor(linux_backend, operation, invalid_fd)
    assert caught.value.errno == errno.EBADF
