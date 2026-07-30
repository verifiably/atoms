import copy
import dataclasses
import errno
import os
import pickle
import stat
import subprocess
import sys

import pytest

from atoms.core.capabilities import Capability
from atoms.core.errors import CapabilityUnavailable, ProtocolError
from atoms.fs.lock import (
    HeldProjectLock,
    acquire_project_lock,
    close_all,
    establish_root,
)


def test_acquire_creates_metadata_root_and_lock(linux_backend, metadata_root):
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        assert lock.held is True
        assert (metadata_root / "lock").is_file()
        assert os.fstat(lock.metadata_root_fd).st_ino == os.stat(metadata_root).st_ino


def test_acquire_is_idempotent_over_an_existing_root(linux_backend, metadata_root):
    with acquire_project_lock(linux_backend, str(metadata_root)):
        pass
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        assert lock.held is True


def test_metadata_root_path_is_normalized_and_absolute(linux_backend, metadata_root):
    unnormalized = f"{metadata_root}/./"
    with acquire_project_lock(linux_backend, unnormalized) as lock:
        assert lock.metadata_root_path == os.path.abspath(str(metadata_root))


def test_establish_root_normalizes_only_after_the_guarded_walk(linux_backend, test_volume):
    # os.path.abspath calls normpath, which collapses 'aliased/..' lexically to
    # test_volume — a real directory that exists — so a normalize-first
    # implementation would succeed here and never show the kernel the symlink.
    # The path the caller wrote must reach openat2 with its spelling intact.
    real = test_volume / "real"
    real.mkdir()
    (test_volume / "aliased").symlink_to(real)
    with pytest.raises(OSError) as caught:
        establish_root(linux_backend, str(test_volume / "aliased" / ".."), create=False)
    assert caught.value.errno == errno.ELOOP


def test_establish_root_resolves_a_parent_component_after_a_real_directory(
    linux_backend, test_volume
):
    # The converse of the test above, so the guard is not merely refusing every '..':
    # with no symlink in the path, the lexical and kernel resolutions agree, and
    # normalization after the walk is what produces the returned pathname.
    (test_volume / "real").mkdir()
    fd, normalized, created = establish_root(
        linux_backend, str(test_volume / "real" / ".."), create=False
    )
    try:
        assert created is False
        assert normalized == os.path.abspath(str(test_volume))
        assert os.fstat(fd).st_ino == os.stat(test_volume).st_ino
    finally:
        os.close(fd)


def test_establish_root_parent_close_failure_releases_the_child(
    linux_backend, metadata_root, monkeypatch
):
    # Once open_child_directory returns, the child must be owned before releasing its
    # parent: a failed parent close otherwise exits with no returned fd and leaks the
    # child. The injected close really releases the parent before raising, so the fd
    # count isolates the child rather than counting an intentionally failed release.
    real_close = os.close
    calls = 0

    def fail_first_close(fd):
        nonlocal calls
        calls += 1
        real_close(fd)
        if calls == 1:
            raise OSError(errno.EIO, "injected parent release failure")

    before = len(os.listdir("/proc/self/fd"))
    with monkeypatch.context() as patched:
        patched.setattr("atoms.fs.lock.os.close", fail_first_close)
        with pytest.raises(OSError) as caught:
            establish_root(linux_backend, str(metadata_root), create=True)
    assert caught.value.errno == errno.EIO
    assert calls == 2, "the child must be released after the parent release fails"
    assert len(os.listdir("/proc/self/fd")) == before


def test_establish_root_preserves_parent_close_failure_when_child_close_also_fails(
    linux_backend, metadata_root, monkeypatch
):
    # Both close attempts really release their descriptor before raising. The first
    # failure therefore remains the causal error, while distinct errnos prove the
    # later child-release failure cannot replace it.
    real_close = os.close
    attempted = []
    errors = (errno.EIO, errno.ENOSPC)

    def fail_each_close(fd):
        code = errors[len(attempted)]
        attempted.append(fd)
        real_close(fd)
        raise OSError(code, "injected release failure")

    before = len(os.listdir("/proc/self/fd"))
    with monkeypatch.context() as patched:
        patched.setattr("atoms.fs.lock.os.close", fail_each_close)
        with pytest.raises(OSError) as caught:
            establish_root(linux_backend, str(metadata_root), create=True)
    assert caught.value.errno == errno.EIO
    assert len(attempted) == 2
    assert attempted[0] != attempted[1]
    assert len(os.listdir("/proc/self/fd")) == before


def test_establish_root_fstat_failure_releases_the_child(
    linux_backend, metadata_root, monkeypatch
):
    # Validation can raise rather than merely report the wrong type. The fresh child
    # descriptor is already owned when fstat runs, so both outcomes release it.
    def fail_fstat(fd):
        raise OSError(errno.EIO, "injected child validation failure")

    before = len(os.listdir("/proc/self/fd"))
    with monkeypatch.context() as patched:
        patched.setattr("atoms.fs.lock.os.fstat", fail_fstat)
        with pytest.raises(OSError) as caught:
            establish_root(linux_backend, str(metadata_root), create=True)
    assert caught.value.errno == errno.EIO
    assert len(os.listdir("/proc/self/fd")) == before


def test_acquire_refuses_a_parent_component_as_the_leaf(linux_backend, metadata_root):
    # Because normalization is deferred, '..' can still be the final component when
    # the creation branch is reached. mkdir('..') is not a coherent request.
    with pytest.raises(ProtocolError, match="final component"):
        acquire_project_lock(linux_backend, str(metadata_root / "absent" / ".."))


def test_acquire_refuses_a_symlink_at_lock(linux_backend, metadata_root):
    metadata_root.mkdir(parents=True)
    (metadata_root / "lock").symlink_to("/etc/passwd")
    with (
        pytest.raises(OSError) as caught,
        acquire_project_lock(linux_backend, str(metadata_root)),
    ):
        pass
    assert caught.value.errno == errno.ELOOP


def test_acquire_refuses_a_fifo_at_lock_after_regular_file_validation(
    linux_backend, metadata_root
):
    # Opening a directory O_RDWR can fail before fstat, so it would not prove the
    # explicit regular-file validation exists. A FIFO opens O_RDWR without blocking
    # and reaches fstat; removing the validation lets flock succeed.
    metadata_root.mkdir(parents=True)
    os.mkfifo(metadata_root / "lock", mode=0o600)
    with (
        pytest.raises(ProtocolError, match="regular file"),
        acquire_project_lock(linux_backend, str(metadata_root)),
    ):
        pass
    assert stat.S_ISFIFO(os.lstat(metadata_root / "lock").st_mode)


def test_acquire_refuses_a_missing_parent(linux_backend, metadata_root):
    # A4a creates only the final leaf, never intermediate directories.
    deep = metadata_root / "absent" / "store"
    with pytest.raises(OSError) as caught, acquire_project_lock(linux_backend, str(deep)):
        pass
    assert caught.value.errno == errno.ENOENT


def test_absent_anchored_traversal_refuses_with_capability_unavailable(
    metadata_root, fake_backend
):
    # Design §10 assigns an unavailable bootstrap prerequisite to
    # CapabilityUnavailable, not a bare OSError: nothing has gone wrong at the
    # syscall level, the volume simply cannot supply the guarded walk. This is also
    # the shape a kernel without openat2 takes, which reports ENOSYS.
    backend = fake_backend(supplied=set(Capability) - {Capability.ANCHORED_TRAVERSAL})
    with pytest.raises(CapabilityUnavailable, match="anchored_traversal"):
        acquire_project_lock(backend, str(metadata_root))


def test_openat2_enosys_refuses_with_capability_unavailable(metadata_root, fake_backend):
    # A current test kernel supplies openat2, so force its operational absence.
    # This must take the shared traversal conversion path, not escape as OSError.
    backend = fake_backend(supplied=set(Capability), traversal_errno=errno.ENOSYS)
    with pytest.raises(CapabilityUnavailable, match="anchored_traversal"):
        acquire_project_lock(backend, str(metadata_root))


def test_absent_advisory_lock_refuses_with_capability_unavailable(
    metadata_root, fake_backend
):
    backend = fake_backend(supplied=set(Capability) - {Capability.ADVISORY_PROJECT_LOCK})
    with pytest.raises(CapabilityUnavailable, match="advisory_project_lock"):
        acquire_project_lock(backend, str(metadata_root))


def test_sets_the_sync_ignore_marker_on_creation(linux_backend, metadata_root):
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        try:
            value = os.getxattr(lock.metadata_root_fd, "user.com.dropbox.ignored")
        except OSError as caught:
            # getxattr(2): ENOTSUP/EOPNOTSUPP means xattrs are unsupported or
            # disabled. ENODATA means the implementation failed to create this
            # specific marker and must fail rather than laundering the defect as a
            # platform skip.
            if caught.errno in {errno.ENOTSUP, errno.EOPNOTSUPP}:
                pytest.skip("filesystem does not support user extended attributes")
            raise
        assert value == b"1"


def test_sync_ignore_marker_failure_does_not_fail_bootstrap(
    linux_backend, metadata_root, monkeypatch
):
    def refuse(*args, **kwargs):
        raise OSError(95, "Operation not supported")

    monkeypatch.setattr("atoms.fs.lock.os.setxattr", refuse)
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        assert lock.held is True


def test_accessors_refuse_after_release(linux_backend, metadata_root):
    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        pass
    assert lock.held is False
    with pytest.raises(ProtocolError):
        _ = lock.metadata_root_fd
    with pytest.raises(ProtocolError):
        _ = lock.metadata_root_path
    with pytest.raises(ProtocolError):
        _ = lock.backend


def test_release_is_idempotent_without_a_second_close(
    linux_backend, metadata_root, monkeypatch
):
    releases = []

    def recording_close_all(fds):
        order = tuple(fds)
        releases.append(order)
        close_all(order)

    lock = acquire_project_lock(linux_backend, str(metadata_root))
    monkeypatch.setattr("atoms.fs.lock.close_all", recording_close_all)
    with lock:
        pass
    assert len(releases) == 1
    lock.__exit__(None, None, None)
    assert lock.held is False
    assert len(releases) == 1


def test_exceptional_exit_still_releases(linux_backend, metadata_root):
    with (
        pytest.raises(RuntimeError),
        acquire_project_lock(linux_backend, str(metadata_root)) as lock,
    ):
        raise RuntimeError("boom")
    assert lock.held is False


def test_close_all_attempts_every_descriptor_and_raises_the_first_failure(test_volume):
    # A failed close does not un-open the descriptors after it, so a loop that lets the
    # first failure escape leaks every later one for the process lifetime — and in the
    # lock's case `held` is already False by then, so nothing ever retries. This is the
    # release path every multi-descriptor site delegates to: HeldProjectLock.__exit__,
    # ensure_metadata_layout's unwind, and bind_project_volume's finally.
    #
    # The failure is real rather than monkeypatched: closing an already-closed
    # descriptor fails with EBADF, and patching os.close would replace it process-wide
    # for everything running inside the window.
    first = os.open(str(test_volume), os.O_RDONLY | os.O_CLOEXEC)
    second = os.open(str(test_volume), os.O_RDONLY | os.O_CLOEXEC)
    os.close(first)

    with pytest.raises(OSError) as caught:
        close_all((first, second))
    assert caught.value.errno == errno.EBADF
    # fstat rather than a second close: it proves `second` was reached without risking
    # closing whatever might have taken that number.
    with pytest.raises(OSError) as reached:
        os.fstat(second)
    assert reached.value.errno == errno.EBADF


def test_close_all_raises_the_first_of_several_failures(monkeypatch):
    # The test above has one real failure, so it proves "keep going" but not the stated
    # precedence. Two failures with distinct errnos make "the FIRST failure" observable:
    # a `first = caught` that kept overwriting would surface ENOSPC instead.
    #
    # The descriptors are fictitious numbers, never closed, because failing_close raises
    # before touching them. The patch is scoped to one call: `atoms.fs.lock.os` IS the
    # os module, so a wider window would replace close for everything inside it.
    codes = {4242: errno.EIO, 4243: errno.ENOSPC}
    attempted = []

    def failing_close(fd):
        attempted.append(fd)
        raise OSError(codes[fd], "injected")

    with monkeypatch.context() as patched:
        patched.setattr("atoms.fs.lock.os.close", failing_close)
        with pytest.raises(OSError) as caught:
            close_all((4242, 4243))
    assert caught.value.errno == errno.EIO, "the first failure is what propagates"
    assert attempted == [4242, 4243], "a failure must not stop the remaining closes"


def test_lock_refuses_ordinary_construction():
    # Downstream signatures treat the type as proof a real lock is held, so a bare
    # object must not be able to fabricate that proof. Matches the CompiledSpec and
    # RecoveryPlan guards, which also raise TypeError.
    with pytest.raises(TypeError, match="acquire_project_lock"):
        HeldProjectLock()


def test_lock_refuses_dataclass_replacement():
    # HeldProjectLock is deliberately NOT a dataclass — it owns descriptors and a
    # spent flag, which are not value semantics — so replace() refuses for want of
    # __dataclass_fields__ rather than for want of the token. Either way, no copy of
    # a held lock can be fabricated, which is what downstream signatures rely on.
    with pytest.raises(TypeError):
        dataclasses.replace(object.__new__(HeldProjectLock))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("duplicate", "message"),
    [
        pytest.param(copy.copy, "cannot be copied", id="copy"),
        pytest.param(copy.deepcopy, "cannot be deep-copied", id="deepcopy"),
        pytest.param(
            lambda value: pickle.loads(pickle.dumps(value)),
            "cannot be pickled",
            id="pickle-round-trip",
        ),
    ],
)
def test_held_lock_refuses_duplicate_ownership(
    linux_backend, metadata_root, monkeypatch, duplicate, message
):
    # Each generic duplication route would otherwise manufacture a second live owner
    # of the same descriptor integers. Refusal must leave the real owner usable, and
    # its eventual exit must remain the descriptors' one release.
    releases = []

    def recording_close_all(fds):
        order = tuple(fds)
        releases.append(order)
        close_all(order)

    with acquire_project_lock(linux_backend, str(metadata_root)) as lock:
        root_fd = lock.metadata_root_fd
        root_identity = os.fstat(root_fd)
        monkeypatch.setattr("atoms.fs.lock.close_all", recording_close_all)

        with pytest.raises(TypeError, match=message):
            duplicate(lock)

        assert lock.held is True
        assert os.fstat(root_fd) == root_identity

    assert len(releases) == 1
    assert len(releases[0]) == 2
    assert releases[0].count(root_fd) == 1
    with pytest.raises(OSError) as caught:
        os.fstat(root_fd)
    assert caught.value.errno == errno.EBADF


_CONTENDER = (
    "import fcntl, sys\n"
    "handle = open(sys.argv[1], 'r+')\n"
    "try:\n"
    "    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "except BlockingIOError:\n"
    "    sys.exit(3)\n"
    "sys.exit(0)\n"
)


def test_a_second_process_cannot_acquire_the_same_lock(linux_backend, metadata_root):
    with acquire_project_lock(linux_backend, str(metadata_root)):
        finished = subprocess.run(
            [sys.executable, "-c", _CONTENDER, str(metadata_root / "lock")],
            check=False,
            timeout=30,
        )
    assert finished.returncode == 3


def test_a_second_process_can_acquire_once_the_lock_is_released(
    linux_backend, metadata_root
):
    # The other direction, which design §11.3 requires: without it, a lock that
    # never grants to anyone would satisfy the exclusion test above.
    with acquire_project_lock(linux_backend, str(metadata_root)):
        pass
    finished = subprocess.run(
        [sys.executable, "-c", _CONTENDER, str(metadata_root / "lock")],
        check=False,
        timeout=30,
    )
    assert finished.returncode == 0
