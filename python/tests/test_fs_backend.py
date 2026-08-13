import errno
import os
import stat

import pytest

from atoms.fs.backend import UNSUPPORTED_ERRNO


def test_unsupported_errno_sets_exclude_ambiguous_generic_failures():
    # A generic failure must never be readable as "this volume lacks the operation":
    # that would report a durable volume as capability-poor (design §10).
    for operation, codes in UNSUPPORTED_ERRNO.items():
        assert errno.EBADF not in codes, operation
        assert errno.EMFILE not in codes, operation
        assert errno.EFAULT not in codes, operation
    # flock(2) defines ENOLCK as exhaustion of kernel lock-record memory, not lack
    # of advisory-lock support. It is environmental failure and must propagate.
    assert errno.ENOLCK not in UNSUPPORTED_ERRNO["lock"]


def test_unsupported_errno_covers_every_probed_operation():
    # The bootstrap path in Task 4 reads "traversal" and "lock" from this same table.
    assert set(UNSUPPORTED_ERRNO) == {
        "exchange",
        "flush",
        "link_anchor",
        "lock",
        "open_regular_nofollow",
        "symlink_fingerprint",
        "transfer_noclobber",
        "traversal",
    }


def test_open_root_opens_an_existing_directory(test_volume, linux_backend):
    root = test_volume / "root"
    root.mkdir()
    fd = linux_backend.open_root(str(root))
    try:
        assert stat.S_ISDIR(os.fstat(fd).st_mode)
        assert os.fstat(fd).st_ino == os.stat(root).st_ino
    finally:
        os.close(fd)


def test_open_root_refuses_a_symlinked_ancestor(test_volume, linux_backend):
    # O_NOFOLLOW would guard only the final component and admit this exact case.
    real = test_volume / "real"
    (real / "inner").mkdir(parents=True)
    (test_volume / "aliased").symlink_to(real)
    with pytest.raises(OSError) as caught:
        linux_backend.open_root(str(test_volume / "aliased" / "inner"))
    assert caught.value.errno == errno.ELOOP


def test_open_root_refuses_a_symlinked_leaf(test_volume, linux_backend):
    (test_volume / "target").mkdir()
    (test_volume / "leaf").symlink_to(test_volume / "target")
    with pytest.raises(OSError) as caught:
        linux_backend.open_root(str(test_volume / "leaf"))
    assert caught.value.errno == errno.ELOOP


def test_open_root_refuses_a_non_directory(test_volume, linux_backend):
    plain = test_volume / "plain"
    plain.write_text("x")
    with pytest.raises(OSError) as caught:
        linux_backend.open_root(str(plain))
    assert caught.value.errno == errno.ENOTDIR


def test_open_root_uses_cloexec(test_volume, linux_backend):
    fd = linux_backend.open_root(str(test_volume))
    try:
        assert os.get_inheritable(fd) is False
    finally:
        os.close(fd)


@pytest.mark.parametrize(
    "invoke",
    [
        pytest.param(
            lambda backend: backend.open_root("root\x00shadow"),
            id="nul-root",
        ),
        pytest.param(
            lambda backend: backend.open_child_directory(7, "child\x00shadow"),
            id="nul-child",
        ),
        pytest.param(
            lambda backend: backend.exchange(7, "left\x00shadow", "right"),
            id="nul-exchange-left",
        ),
        pytest.param(
            lambda backend: backend.exchange(7, "left", "right\x00shadow"),
            id="nul-exchange-right",
        ),
        pytest.param(
            lambda backend: backend.transfer_noclobber(
                7, "source\x00shadow", 8, "destination"
            ),
            id="nul-transfer-source",
        ),
        pytest.param(
            lambda backend: backend.transfer_noclobber(
                7, "source", 8, "destination\x00shadow"
            ),
            id="nul-transfer-destination",
        ),
    ],
)
def test_linux_backend_refuses_nul_before_calling_raw_path_wrappers(
    monkeypatch, linux_backend, invoke
):
    calls = []

    def recording_wrapper(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr("atoms.fs.linux.sys_linux.openat2", recording_wrapper)
    monkeypatch.setattr("atoms.fs.linux.sys_linux.renameat2", recording_wrapper)

    with pytest.raises(ValueError, match="embedded null byte"):
        invoke(linux_backend)

    assert calls == [], "the string adapter must reject before the raw wrapper"


def test_open_child_directory_refuses_a_symlink(test_volume, linux_backend):
    (test_volume / "real").mkdir()
    (test_volume / "link").symlink_to("real")
    parent = linux_backend.open_root(str(test_volume))
    try:
        with pytest.raises(OSError) as caught:
            linux_backend.open_child_directory(parent, "link")
        assert caught.value.errno == errno.ELOOP
    finally:
        os.close(parent)


def test_open_child_directory_refuses_a_parent_component(test_volume, linux_backend):
    # RESOLVE_BENEATH reports an escape as EXDEV. The exact code matters: the probe
    # treats this refusal as evidence the guard works, so any other errno there is
    # an unrelated failure and must propagate rather than be read as success.
    parent = linux_backend.open_root(str(test_volume))
    try:
        with pytest.raises(OSError) as caught:
            linux_backend.open_child_directory(parent, "..")
        assert caught.value.errno == errno.EXDEV
    finally:
        os.close(parent)


def test_exchange_swaps_entries_in_one_parent(test_volume, linux_backend):
    (test_volume / "a").write_text("A")
    (test_volume / "b").write_text("B")
    fd = linux_backend.open_root(str(test_volume))
    try:
        linux_backend.exchange(fd, "a", "b")
    finally:
        os.close(fd)
    assert (test_volume / "a").read_text() == "B"
    assert (test_volume / "b").read_text() == "A"


def test_transfer_noclobber_across_distinct_parents(test_volume, linux_backend):
    # The distinct-parent form is what blob promotion and staging publication use.
    (test_volume / "src").mkdir()
    (test_volume / "dst").mkdir()
    (test_volume / "src" / "f").write_text("payload")
    (test_volume / "dst" / "f").write_text("occupied")
    src_fd = linux_backend.open_root(str(test_volume / "src"))
    dst_fd = linux_backend.open_root(str(test_volume / "dst"))
    try:
        with pytest.raises(OSError) as caught:
            linux_backend.transfer_noclobber(src_fd, "f", dst_fd, "f")
        assert caught.value.errno == errno.EEXIST
        os.unlink(test_volume / "dst" / "f")
        linux_backend.transfer_noclobber(src_fd, "f", dst_fd, "f")
    finally:
        os.close(src_fd)
        os.close(dst_fd)
    assert (test_volume / "dst" / "f").read_text() == "payload"
    assert not (test_volume / "src" / "f").exists()


def test_link_anchor_across_distinct_parents_shares_identity(test_volume, linux_backend):
    (test_volume / "src").mkdir()
    (test_volume / "dst").mkdir()
    (test_volume / "src" / "f").write_text("payload")
    src_fd = linux_backend.open_root(str(test_volume / "src"))
    dst_fd = linux_backend.open_root(str(test_volume / "dst"))
    try:
        linux_backend.link_anchor(src_fd, "f", dst_fd, "anchor")
    finally:
        os.close(src_fd)
        os.close(dst_fd)
    source = os.stat(test_volume / "src" / "f")
    anchor = os.stat(test_volume / "dst" / "anchor")
    assert (source.st_dev, source.st_ino) == (anchor.st_dev, anchor.st_ino)
    assert source.st_nlink == 2


def test_open_regular_nofollow_reads_and_refuses_a_symlink_leaf(test_volume, linux_backend):
    (test_volume / "f").write_text("payload")
    (test_volume / "link").symlink_to("f")
    parent = linux_backend.open_root(str(test_volume))
    try:
        fd = linux_backend.open_regular_nofollow(parent, "f")
        try:
            assert os.read(fd, 16) == b"payload"
            assert stat.S_ISREG(os.fstat(fd).st_mode)
        finally:
            os.close(fd)
        with pytest.raises(OSError) as caught:
            linux_backend.open_regular_nofollow(parent, "link")
        assert caught.value.errno == errno.ELOOP
    finally:
        os.close(parent)


def test_symlink_fingerprint_is_lstat_coherent(test_volume, linux_backend):
    (test_volume / "link").symlink_to("../target")
    parent = linux_backend.open_root(str(test_volume))
    try:
        info, target = linux_backend.symlink_fingerprint(parent, "link")
    finally:
        os.close(parent)
    assert stat.S_ISLNK(info.st_mode)
    assert target == "../target"


def test_flush_file_and_directory_succeed(test_volume, linux_backend):
    (test_volume / "f").write_text("payload")
    parent = linux_backend.open_root(str(test_volume))
    fd = os.open(test_volume / "f", os.O_RDONLY | os.O_CLOEXEC)
    try:
        linux_backend.flush_file(fd)
        linux_backend.flush_directory(parent)
    finally:
        os.close(fd)
        os.close(parent)


def test_try_lock_exclusive_contends_across_open_file_descriptions(test_volume, linux_backend):
    # flock is per open file description, so two opens in one process contend.
    path = test_volume / "lock"
    path.write_text("")
    first = os.open(path, os.O_RDWR | os.O_CLOEXEC)
    second = os.open(path, os.O_RDWR | os.O_CLOEXEC)
    try:
        linux_backend.lock_exclusive(first)
        assert linux_backend.try_lock_exclusive(second) is False
    finally:
        os.close(first)
        os.close(second)


def test_try_lock_exclusive_propagates_eacces(monkeypatch, linux_backend):
    failure = OSError(errno.EACCES, "injected environmental failure")

    def raise_eacces(_fd, _operation):
        raise failure

    monkeypatch.setattr("atoms.fs.linux.fcntl.flock", raise_eacces)

    with pytest.raises(OSError) as caught:
        linux_backend.try_lock_exclusive(42)
    assert caught.value is failure


def test_create_exclusive_returns_a_read_write_descriptor(tmp_path, linux_backend):
    root_fd = linux_backend.open_root(str(tmp_path))
    try:
        fd = linux_backend.create_exclusive(root_fd, "created", 0o600)
        try:
            assert os.write(fd, b"payload") == len(b"payload")
            assert os.lseek(fd, 0, os.SEEK_SET) == 0
            assert os.read(fd, len(b"payload")) == b"payload"
        finally:
            os.close(fd)
    finally:
        os.close(root_fd)


def test_write_writes_bytes_to_a_descriptor(tmp_path, linux_backend):
    path = tmp_path / "written"
    path.touch()
    root_fd = linux_backend.open_root(str(tmp_path))
    try:
        fd = os.open("written", os.O_RDWR | os.O_CLOEXEC, dir_fd=root_fd)
        try:
            assert linux_backend.write(fd, b"payload") == len(b"payload")
        finally:
            os.close(fd)
    finally:
        os.close(root_fd)
    assert path.read_bytes() == b"payload"


def test_set_mode_changes_the_open_entry_mode(tmp_path, linux_backend):
    path = tmp_path / "mode"
    path.touch()
    root_fd = linux_backend.open_root(str(tmp_path))
    try:
        fd = os.open("mode", os.O_RDONLY | os.O_CLOEXEC, dir_fd=root_fd)
        try:
            linux_backend.set_mode(fd, 0o600)
        finally:
            os.close(fd)
    finally:
        os.close(root_fd)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_mkdir_child_creates_a_directory(tmp_path, linux_backend):
    root_fd = linux_backend.open_root(str(tmp_path))
    try:
        linux_backend.mkdir_child(root_fd, "child", 0o700)
    finally:
        os.close(root_fd)
    assert (tmp_path / "child").is_dir()


def test_unlink_child_removes_a_file(tmp_path, linux_backend):
    path = tmp_path / "child"
    path.touch()
    root_fd = linux_backend.open_root(str(tmp_path))
    try:
        linux_backend.unlink_child(root_fd, "child")
    finally:
        os.close(root_fd)
    assert not path.exists()


def test_rmdir_child_removes_an_empty_directory(tmp_path, linux_backend):
    path = tmp_path / "child"
    path.mkdir()
    root_fd = linux_backend.open_root(str(tmp_path))
    try:
        linux_backend.rmdir_child(root_fd, "child")
    finally:
        os.close(root_fd)
    assert not path.exists()


def test_symlink_child_creates_the_requested_target(tmp_path, linux_backend):
    root_fd = linux_backend.open_root(str(tmp_path))
    try:
        linux_backend.symlink_child(root_fd, "link", "target")
    finally:
        os.close(root_fd)
    assert os.readlink(tmp_path / "link") == "target"


def test_create_or_open_preserves_existing_file_contents(tmp_path, linux_backend):
    path = tmp_path / "lock"
    path.write_bytes(b"existing")
    root_fd = linux_backend.open_root(str(tmp_path))
    try:
        fd = linux_backend.create_or_open(root_fd, "lock", 0o600)
        try:
            assert os.read(fd, len(b"existing")) == b"existing"
        finally:
            os.close(fd)
    finally:
        os.close(root_fd)
    assert path.read_bytes() == b"existing"


def test_open_existing_refuses_a_missing_entry_without_creating_it(
    tmp_path, linux_backend
):
    root_fd = linux_backend.open_root(str(tmp_path))
    try:
        with pytest.raises(FileNotFoundError):
            linux_backend.open_existing(root_fd, "missing")
    finally:
        os.close(root_fd)
    assert not (tmp_path / "missing").exists()


def test_set_marker_xattr_sets_the_marker(tmp_path, linux_backend):
    path = tmp_path / "marked"
    path.touch()
    root_fd = linux_backend.open_root(str(tmp_path))
    try:
        fd = os.open("marked", os.O_RDONLY | os.O_CLOEXEC, dir_fd=root_fd)
        try:
            linux_backend.set_marker_xattr(fd, "user.atoms-test", b"marker")
        finally:
            os.close(fd)
    finally:
        os.close(root_fd)
    assert os.getxattr(path, "user.atoms-test") == b"marker"


def test_repair_entry_mode_restores_a_mode_zero_entry(tmp_path, linux_backend):
    path = tmp_path / "database"
    path.touch(mode=0o000)
    root_fd = linux_backend.open_root(str(tmp_path))
    try:
        with pytest.raises(PermissionError):
            os.open("database", os.O_RDWR | os.O_CLOEXEC, dir_fd=root_fd)
        linux_backend.repair_entry_mode(
            root_fd,
            "database",
            0o600,
            before_change=lambda: None,
        )
        fd = os.open("database", os.O_RDWR | os.O_CLOEXEC, dir_fd=root_fd)
        os.close(fd)
    finally:
        os.close(root_fd)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_close_fd_closes_the_descriptor(tmp_path, linux_backend):
    root_fd = linux_backend.open_root(str(tmp_path))
    linux_backend.close_fd(root_fd)
    with pytest.raises(OSError) as caught:
        os.fstat(root_fd)
    assert caught.value.errno == errno.EBADF
