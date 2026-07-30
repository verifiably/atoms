"""Linux implementation of the capability protocol (design §5.1, §5.4)."""

from __future__ import annotations

import errno
import fcntl
import os

from atoms.fs.syscalls import linux as sys_linux

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC


class LinuxBackend:
    def open_root(self, path: str) -> int:
        # RESOLVE_NO_SYMLINKS over the complete path: O_NOFOLLOW would guard only
        # the final component and follow every ancestor symlink. RESOLVE_NO_XDEV is
        # deliberately unset — the target mount is not established yet, and refusing
        # a crossing here would refuse a root that simply lives on its own mount.
        return sys_linux.openat2(
            -100,  # AT_FDCWD
            os.fsencode(path),
            _DIR_FLAGS,
            0,
            sys_linux.RESOLVE_NO_SYMLINKS,
        )

    def open_child_directory(self, parent_fd: int, name: str) -> int:
        return sys_linux.openat2(
            parent_fd,
            os.fsencode(name),
            _DIR_FLAGS,
            0,
            sys_linux.RESOLVE_BENEATH
            | sys_linux.RESOLVE_NO_SYMLINKS
            | sys_linux.RESOLVE_NO_XDEV,
        )

    def exchange(self, parent_fd: int, left: str, right: str) -> None:
        sys_linux.renameat2(
            parent_fd,
            os.fsencode(left),
            parent_fd,
            os.fsencode(right),
            sys_linux.RENAME_EXCHANGE,
        )

    def transfer_noclobber(self, src_fd: int, src: str, dst_fd: int, dst: str) -> None:
        sys_linux.renameat2(
            src_fd,
            os.fsencode(src),
            dst_fd,
            os.fsencode(dst),
            sys_linux.RENAME_NOREPLACE,
        )

    def link_anchor(self, src_fd: int, src: str, dst_fd: int, dst: str) -> None:
        os.link(src, dst, src_dir_fd=src_fd, dst_dir_fd=dst_fd, follow_symlinks=False)

    def flush_file(self, fd: int) -> None:
        os.fsync(fd)

    def flush_directory(self, fd: int) -> None:
        os.fsync(fd)

    def open_regular_nofollow(self, parent_fd: int, name: str) -> int:
        return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)

    def symlink_fingerprint(self, parent_fd: int, name: str) -> tuple[os.stat_result, str]:
        info = os.lstat(name, dir_fd=parent_fd)
        return info, os.readlink(name, dir_fd=parent_fd)

    def lock_exclusive(self, fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def try_lock_exclusive(self, fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as caught:
            if caught.errno in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                return False
            raise
        return True
