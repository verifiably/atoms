import ctypes
import errno
import os

import pytest

from atoms.fs.syscalls import linux


def test_resolve_and_rename_constants_match_kernel_values():
    assert linux.RESOLVE_NO_XDEV == 0x01
    assert linux.RESOLVE_NO_SYMLINKS == 0x04
    assert linux.RESOLVE_BENEATH == 0x08
    assert linux.RENAME_NOREPLACE == 1
    assert linux.RENAME_EXCHANGE == 2


def test_open_how_struct_is_three_u64_fields():
    assert ctypes.sizeof(linux.OpenHow) == 24
    assert [field[0] for field in linux.OpenHow._fields_] == ["flags", "mode", "resolve"]


def test_openat2_opens_a_directory_relative_to_a_descriptor(tmp_path):
    (tmp_path / "child").mkdir()
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        fd = linux.openat2(
            parent_fd,
            b"child",
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
            0,
            linux.RESOLVE_BENEATH | linux.RESOLVE_NO_SYMLINKS,
        )
        try:
            assert os.fstat(fd).st_ino == os.stat(tmp_path / "child").st_ino
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)


def test_openat2_refuses_a_symlink_component_under_no_symlinks(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to("real")
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        with pytest.raises(OSError) as caught:
            linux.openat2(
                parent_fd,
                b"link",
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
                0,
                linux.RESOLVE_BENEATH | linux.RESOLVE_NO_SYMLINKS,
            )
        assert caught.value.errno == errno.ELOOP
    finally:
        os.close(parent_fd)


def test_openat2_propagates_enoent_with_true_errno(tmp_path):
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        with pytest.raises(OSError) as caught:
            linux.openat2(parent_fd, b"missing", os.O_RDONLY, 0, linux.RESOLVE_BENEATH)
        assert caught.value.errno == errno.ENOENT
    finally:
        os.close(parent_fd)


def test_renameat2_noreplace_refuses_an_existing_destination(tmp_path):
    (tmp_path / "src").write_text("s")
    (tmp_path / "dst").write_text("d")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        with pytest.raises(OSError) as caught:
            linux.renameat2(fd, b"src", fd, b"dst", linux.RENAME_NOREPLACE)
        assert caught.value.errno == errno.EEXIST
        assert (tmp_path / "src").read_text() == "s"
    finally:
        os.close(fd)


def test_renameat2_exchange_swaps_two_entries(tmp_path):
    (tmp_path / "a").write_text("A")
    (tmp_path / "b").write_text("B")
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        linux.renameat2(fd, b"a", fd, b"b", linux.RENAME_EXCHANGE)
    finally:
        os.close(fd)
    assert (tmp_path / "a").read_text() == "B"
    assert (tmp_path / "b").read_text() == "A"
