"""Linux implementation of the capability protocol (design §5.1, §5.4)."""

from __future__ import annotations

import errno
import fcntl
import os
from collections.abc import Callable

from atoms.fs.syscalls import linux as sys_linux

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
_DIR_HANDLE_FLAGS = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _c_string_path(value: str) -> bytes:
    encoded = os.fsencode(value)
    if b"\x00" in encoded:
        raise ValueError("embedded null byte")
    return encoded


class LinuxBackend:
    def create_exclusive(self, parent_fd: int, name: str, mode: int) -> int:
        return os.open(
            name,
            os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_RDWR | os.O_CLOEXEC,
            mode,
            dir_fd=parent_fd,
        )

    def write(self, fd: int, data: bytes) -> int:
        return os.write(fd, data)

    def set_mode(self, fd: int, mode: int) -> None:
        os.fchmod(fd, mode)

    def mkdir_child(self, parent_fd: int, name: str, mode: int) -> None:
        os.mkdir(name, mode, dir_fd=parent_fd)

    def unlink_child(self, parent_fd: int, name: str) -> None:
        os.unlink(name, dir_fd=parent_fd)

    def rmdir_child(self, parent_fd: int, name: str) -> None:
        os.rmdir(name, dir_fd=parent_fd)

    def symlink_child(self, parent_fd: int, name: str, target: str) -> None:
        os.symlink(target, name, dir_fd=parent_fd)

    def create_or_open(self, parent_fd: int, name: str, mode: int) -> int:
        return os.open(
            name,
            os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR | os.O_CLOEXEC,
            mode,
            dir_fd=parent_fd,
        )

    def open_existing(
        self,
        parent_fd: int,
        name: str,
        *,
        read_write: bool = False,
        nofollow: bool = False,
    ) -> int:
        flags = (os.O_RDWR if read_write else os.O_RDONLY) | os.O_CLOEXEC
        if nofollow:
            flags |= os.O_NOFOLLOW
        return os.open(name, flags, dir_fd=parent_fd)

    def set_marker_xattr(self, fd: int, name: str, value: bytes) -> None:
        os.setxattr(fd, name, value)

    def repair_entry_mode(
        self,
        parent_fd: int,
        name: str,
        mode: int,
        *,
        before_change: Callable[[], None],
    ) -> None:
        path_fd = os.open(
            name,
            os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
        try:
            before_change()
            os.chmod(f"/proc/self/fd/{path_fd}", mode)
        finally:
            os.close(path_fd)

    def close_fd(self, fd: int) -> None:
        os.close(fd)

    def detach_fd(self, fd: int) -> None:
        pass

    def open_root(self, path: str) -> int:
        # RESOLVE_NO_SYMLINKS over the complete path: O_NOFOLLOW would guard only
        # the final component and follow every ancestor symlink. RESOLVE_NO_XDEV is
        # deliberately unset — the target mount is not established yet, and refusing
        # a crossing here would refuse a root that simply lives on its own mount.
        return sys_linux.openat2(
            -100,  # AT_FDCWD
            _c_string_path(path),
            _DIR_FLAGS,
            0,
            sys_linux.RESOLVE_NO_SYMLINKS,
        )

    def open_child_directory(self, parent_fd: int, name: str) -> int:
        return sys_linux.openat2(
            parent_fd,
            _c_string_path(name),
            _DIR_FLAGS,
            0,
            sys_linux.RESOLVE_BENEATH
            | sys_linux.RESOLVE_NO_SYMLINKS
            | sys_linux.RESOLVE_NO_XDEV,
        )

    def open_directory_handle(self, parent_fd: int, name: str) -> int:
        return sys_linux.openat2(
            parent_fd,
            _c_string_path(name),
            _DIR_HANDLE_FLAGS,
            0,
            sys_linux.RESOLVE_BENEATH
            | sys_linux.RESOLVE_NO_SYMLINKS
            | sys_linux.RESOLVE_NO_XDEV,
        )

    def exchange(self, parent_fd: int, left: str, right: str) -> None:
        sys_linux.renameat2(
            parent_fd,
            _c_string_path(left),
            parent_fd,
            _c_string_path(right),
            sys_linux.RENAME_EXCHANGE,
        )

    def transfer_noclobber(self, src_fd: int, src: str, dst_fd: int, dst: str) -> None:
        sys_linux.renameat2(
            src_fd,
            _c_string_path(src),
            dst_fd,
            _c_string_path(dst),
            sys_linux.RENAME_NOREPLACE,
        )

    def link_anchor(self, src_fd: int, src: str, dst_fd: int, dst: str) -> None:
        os.link(src, dst, src_dir_fd=src_fd, dst_dir_fd=dst_fd, follow_symlinks=False)

    def flush_file(self, fd: int) -> None:
        os.fsync(fd)

    def flush_directory(self, fd: int) -> None:
        os.fsync(fd)

    def open_regular_nofollow(self, parent_fd: int, name: str) -> int:
        return os.open(
            name,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )

    def symlink_fingerprint(self, parent_fd: int, name: str) -> tuple[os.stat_result, str]:
        info = os.lstat(name, dir_fd=parent_fd)
        return info, os.readlink(name, dir_fd=parent_fd)

    def lock_exclusive(self, fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def try_lock_exclusive(self, fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as caught:
            if caught.errno == errno.EWOULDBLOCK:
                return False
            raise
        return True
