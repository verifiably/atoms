import errno
import os
import stat

import pytest

from atoms.core.capabilities import Capability
from atoms.core.errors import ProtocolError
from atoms.fs.bootstrap import (
    METADATA_LAYOUT,
    ensure_metadata_layout,
    reclaim_probe_survivors,
    verified_child_path,
)
from atoms.fs.lock import acquire_project_lock, close_all
from tests.fs_support import metadata_layout


def test_layout_creates_every_directory(held_lock, metadata_root):
    with held_lock(metadata_root) as lock, metadata_layout(lock):
        pass
    for relative in METADATA_LAYOUT:
        assert (metadata_root / relative).is_dir()


def test_layout_returns_one_owned_descriptor_per_component(held_lock, metadata_root):
    # The return value is the ownership contract: one descriptor per component,
    # each a directory, each O_CLOEXEC, and each the caller's to close.
    with held_lock(metadata_root) as lock, metadata_layout(lock) as retained:
        assert sorted(retained) == sorted(METADATA_LAYOUT)
        for relative, fd in retained.items():
            assert stat.S_ISDIR(os.fstat(fd).st_mode), relative
            assert os.get_inheritable(fd) is False, relative


def test_layout_descriptors_are_released_in_reverse_opening_order(
    held_lock, metadata_root, monkeypatch
):
    # Design §7.2 and §9.3: nested descriptors are released child-before-parent, so no
    # release depends on one already gone. `retained` is insertion-ordered by
    # METADATA_LAYOUT, which makes values() OPENING order — passing it straight to
    # close_all reverses nothing and reads as correct, which is why the order is
    # pinned here rather than left to the comment in close_layout.
    calls = []

    def recording(backend, fds):
        order = list(fds)
        calls.append(order)
        close_all(backend, order)

    monkeypatch.setattr("atoms.fs.bootstrap.close_all", recording)
    with held_lock(metadata_root) as lock, metadata_layout(lock) as retained:
        opening_order = [retained[relative] for relative in METADATA_LAYOUT]
    # The last close_all is close_layout's; earlier ones released the intermediate
    # `blobs` descriptor on the way to `blobs/sha256`.
    assert calls[-1] == list(reversed(opening_order))
    assert calls[-1] != opening_order, "four distinct descriptors, so this is not a tie"


def test_failed_intermediate_release_unwinds_the_retained_layout(
    held_lock, metadata_root, monkeypatch
):
    # `blobs/sha256` opens an intermediate `blobs` descriptor before the retained
    # leaf. If releasing that intermediate fails after the leaf enters `retained`,
    # ensure_metadata_layout returns no mapping for the caller to own; it must unwind
    # the whole retained set itself.
    real_close_all = close_all
    injected = False

    def fail_nonempty_intermediate(backend, fds):
        nonlocal injected
        order = list(fds)
        real_close_all(backend, order)
        if order and not injected:
            injected = True
            raise OSError(errno.EIO, "injected intermediate release failure")

    with held_lock(metadata_root) as lock:
        before = len(os.listdir("/proc/self/fd"))
        monkeypatch.setattr("atoms.fs.bootstrap.close_all", fail_nonempty_intermediate)
        with pytest.raises(OSError) as caught:
            ensure_metadata_layout(lock)
        assert caught.value.errno == errno.EIO
        assert injected is True
        assert len(os.listdir("/proc/self/fd")) == before


def test_intermediate_release_failure_precedes_retained_layout_release_failure(
    held_lock, metadata_root, monkeypatch
):
    # Releasing the `blobs` intermediate fails first; unwinding the four retained
    # leaf descriptors fails second. Both calls really close every descriptor before
    # raising so the count proves complete unwind, while distinct errnos pin the
    # first-failure precedence across the nested cleanup boundary.
    real_close_all = close_all
    nonempty_releases = []
    errors = (errno.EIO, errno.ENOSPC)

    def fail_each_nonempty_release(backend, fds):
        order = list(fds)
        real_close_all(backend, order)
        if order:
            code = errors[len(nonempty_releases)]
            nonempty_releases.append(order)
            raise OSError(code, "injected release failure")

    with held_lock(metadata_root) as lock:
        before = len(os.listdir("/proc/self/fd"))
        monkeypatch.setattr(
            "atoms.fs.bootstrap.close_all", fail_each_nonempty_release
        )
        with pytest.raises(OSError) as caught:
            ensure_metadata_layout(lock)
        assert caught.value.errno == errno.EIO
        assert [len(order) for order in nonempty_releases] == [1, 4]
        assert len({fd for order in nonempty_releases for fd in order}) == 5
        assert len(os.listdir("/proc/self/fd")) == before


def test_layout_fstat_failure_releases_every_owned_descriptor(
    held_lock, metadata_root, monkeypatch
):
    # _open_or_create_child owns each fresh descriptor before validating it. A
    # validation syscall that raises therefore releases the fresh descriptor plus
    # every descriptor retained by earlier layout components.
    real_fstat = os.fstat
    calls = 0

    def fail_second_validation(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(errno.EIO, "injected layout validation failure")
        return real_fstat(fd)

    with held_lock(metadata_root) as lock:
        before = len(os.listdir("/proc/self/fd"))
        monkeypatch.setattr("atoms.fs.bootstrap.os.fstat", fail_second_validation)
        with pytest.raises(OSError) as caught:
            ensure_metadata_layout(lock)
        assert caught.value.errno == errno.EIO
        assert len(os.listdir("/proc/self/fd")) == before


def test_layout_is_idempotent_over_existing_directories(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        with metadata_layout(lock):
            pass
        with metadata_layout(lock) as retained:
            assert sorted(retained) == sorted(METADATA_LAYOUT)
    assert (metadata_root / "blobs" / "sha256").is_dir()


def test_layout_refuses_a_symlink_occupying_a_name(held_lock, metadata_root):
    # Tolerating EEXIST without reopening would adopt whatever occupies the name.
    # Failure-path tests call ensure_metadata_layout directly legitimately: it
    # raises, returns nothing, and closes what it had already opened, so there is no
    # descriptor mapping for a caller to own.
    with held_lock(metadata_root) as lock:
        os.symlink("/etc", "staging", dir_fd=lock.metadata_root_fd)
        with pytest.raises((OSError, ProtocolError)):
            ensure_metadata_layout(lock)


def test_layout_refuses_a_regular_file_occupying_a_name(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        fd = os.open("work", os.O_CREAT | os.O_WRONLY, 0o600, dir_fd=lock.metadata_root_fd)
        os.close(fd)
        with pytest.raises((OSError, ProtocolError)):
            ensure_metadata_layout(lock)


def test_reclamation_is_a_noop_when_probe_is_absent(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        reclaim_probe_survivors(lock)
        assert not (metadata_root / "probe").exists()


def test_reclamation_empties_a_real_probe_directory(held_lock, metadata_root):
    with held_lock(metadata_root) as lock, metadata_layout(lock):
        probe = metadata_root / "probe"
        (probe / "nested").mkdir()
        (probe / "nested" / "file").write_text("debris")
        (probe / "loose").write_text("debris")
        reclaim_probe_survivors(lock)
        assert probe.is_dir()
        assert list(probe.iterdir()) == []


def test_reclamation_does_not_follow_a_symlink_out_of_probe(
    held_lock, metadata_root, test_volume
):
    outside = test_volume / "outside"
    outside.write_text("must survive")
    with held_lock(metadata_root) as lock, metadata_layout(lock):
        (metadata_root / "probe" / "escape").symlink_to(outside)
        reclaim_probe_survivors(lock)
    assert outside.read_text() == "must survive"
    assert list((metadata_root / "probe").iterdir()) == []


def test_reclamation_refuses_a_symlink_at_probe_itself(
    held_lock, metadata_root, test_volume
):
    # Reclamation runs before layout creation, so it, not the EEXIST path,
    # meets an anomalous probe/ first. It refuses rather than unlinking a leaf
    # A4a did not create.
    elsewhere = test_volume / "elsewhere"
    elsewhere.mkdir()
    with held_lock(metadata_root) as lock:
        os.symlink(str(elsewhere), "probe", dir_fd=lock.metadata_root_fd)
        with pytest.raises((OSError, ProtocolError)):
            reclaim_probe_survivors(lock)
        assert os.readlink("probe", dir_fd=lock.metadata_root_fd) == str(elsewhere)


def test_reclamation_refuses_a_regular_file_at_probe(held_lock, metadata_root):
    payload = b"engine-owned name must survive refusal"
    with held_lock(metadata_root) as lock:
        fd = os.open("probe", os.O_CREAT | os.O_WRONLY, 0o600, dir_fd=lock.metadata_root_fd)
        try:
            assert os.write(fd, payload) == len(payload)
            before = os.fstat(fd)
        finally:
            os.close(fd)

        with pytest.raises(ProtocolError, match="refusing to unlink"):
            reclaim_probe_survivors(lock)

        after = os.lstat("probe", dir_fd=lock.metadata_root_fd)
        assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
        read_fd = os.open(
            "probe",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=lock.metadata_root_fd,
        )
        try:
            assert os.read(read_fd, len(payload) + 1) == payload
        finally:
            os.close(read_fd)


def test_reclamation_refuses_probe_mount_point_without_touching_it(
    metadata_root, fake_backend
):
    # A name-scoped EXDEV models RESOLVE_NO_XDEV refusing `probe` itself. The
    # refusal must propagate, and neither the directory nor its survivor evidence
    # may be guessed away.
    backend = fake_backend(
        supplied=set(Capability),
        override_names={"probe"},
        open_child_directory_errno=errno.EXDEV,
    )
    payload = b"mounted survivor evidence"
    with acquire_project_lock(backend, str(metadata_root)) as lock:
        os.mkdir("probe", mode=0o700, dir_fd=lock.metadata_root_fd)
        before = os.lstat("probe", dir_fd=lock.metadata_root_fd)
        survivor_fd = os.open(
            "probe/survivor",
            os.O_CREAT | os.O_WRONLY | os.O_CLOEXEC,
            0o600,
            dir_fd=lock.metadata_root_fd,
        )
        try:
            assert os.write(survivor_fd, payload) == len(payload)
        finally:
            os.close(survivor_fd)

        with pytest.raises(OSError) as caught:
            reclaim_probe_survivors(lock)
        assert caught.value.errno == errno.EXDEV

        after = os.lstat("probe", dir_fd=lock.metadata_root_fd)
        assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
        assert (metadata_root / "probe" / "survivor").read_bytes() == payload


def test_verified_child_path_joins_after_confirming_identity(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        info = os.fstat(lock.metadata_root_fd)
        resolved = verified_child_path(
            lock.metadata_root_fd, lock.metadata_root_path, info.st_dev, info.st_ino, "atoms.db"
        )
    assert resolved == os.path.join(os.path.abspath(str(metadata_root)), "atoms.db")


def test_verified_child_path_refuses_a_non_component(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        info = os.fstat(lock.metadata_root_fd)
        for name in ("../escape", "nested/child", "", ".", "atoms.db\x00shadow"):
            with pytest.raises(ProtocolError):
                verified_child_path(
                    lock.metadata_root_fd,
                    lock.metadata_root_path,
                    info.st_dev,
                    info.st_ino,
                    name,
                )


def test_verified_child_path_refuses_nul_before_identity_syscalls(
    held_lock, metadata_root, monkeypatch
):
    with held_lock(metadata_root) as lock:
        info = os.fstat(lock.metadata_root_fd)

        def reached_fstat(fd):
            raise AssertionError(f"NUL component reached fstat for descriptor {fd}")

        monkeypatch.setattr("atoms.fs.bootstrap.os.fstat", reached_fstat)
        with pytest.raises(ProtocolError, match="component"):
            verified_child_path(
                lock.metadata_root_fd,
                lock.metadata_root_path,
                info.st_dev,
                info.st_ino,
                "atoms.db\x00shadow",
            )


def test_verified_child_path_refuses_an_identity_mismatch(held_lock, metadata_root):
    with held_lock(metadata_root) as lock:
        info = os.fstat(lock.metadata_root_fd)
        with pytest.raises(ProtocolError, match="identity"):
            verified_child_path(
                lock.metadata_root_fd,
                lock.metadata_root_path,
                info.st_dev,
                info.st_ino + 1,
                "atoms.db",
            )
