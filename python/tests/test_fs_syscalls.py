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


@pytest.mark.parametrize("path", [b"prefix\x00suffix", b"\x00"])
def test_openat2_rejects_nul_before_calling_libc(monkeypatch, path):
    calls = []

    def recording_syscall(*args):
        calls.append(args)
        return 0

    monkeypatch.setattr(linux, "_syscall", recording_syscall)

    with pytest.raises(ValueError, match="embedded null byte"):
        linux.openat2(7, path, os.O_RDONLY, 0, linux.RESOLVE_BENEATH)

    assert calls == [], "a refused C-string pathname must not reach libc"


def test_openat2_nul_suffix_cannot_open_the_existing_prefix(tmp_path):
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        try:
            opened = linux.openat2(
                parent_fd,
                b"prefix\x00/missing",
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
                0,
                linux.RESOLVE_BENEATH,
            )
        except ValueError as caught:
            assert "embedded null byte" in str(caught)
        else:
            os.close(opened)
            pytest.fail("the NUL suffix was truncated and the existing prefix opened")
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


@pytest.mark.parametrize("route", ["libc-symbol", "raw-syscall"])
@pytest.mark.parametrize("nul_operand", ["oldpath", "newpath"])
def test_renameat2_rejects_each_nul_operand_before_either_libc_route(
    monkeypatch, route, nul_operand
):
    calls = []

    def recording_call(*args):
        calls.append(args)
        return 0

    monkeypatch.setattr(
        linux,
        "_renameat2_symbol",
        recording_call if route == "libc-symbol" else None,
    )
    monkeypatch.setattr(linux, "_syscall", recording_call)
    oldpath = b"old\x00shadow" if nul_operand == "oldpath" else b"old"
    newpath = b"new\x00shadow" if nul_operand == "newpath" else b"new"

    with pytest.raises(ValueError, match="embedded null byte"):
        linux.renameat2(7, oldpath, 8, newpath, linux.RENAME_NOREPLACE)

    assert calls == [], "both operands must be validated before selecting a libc route"
