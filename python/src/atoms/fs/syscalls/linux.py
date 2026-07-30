"""ctypes bindings for the Linux syscalls the stdlib does not expose (design §5.2)."""

from __future__ import annotations

import ctypes
import os
import platform

RESOLVE_NO_XDEV = 0x01
RESOLVE_NO_MAGICLINKS = 0x02
RESOLVE_NO_SYMLINKS = 0x04
RESOLVE_BENEATH = 0x08

RENAME_NOREPLACE = 1
RENAME_EXCHANGE = 2

# Syscall numbers are per-architecture. An unlisted architecture refuses in platform.py
# rather than guessing a number.
_SYSCALL_NUMBERS = {
    "x86_64": {"openat2": 437, "renameat2": 316},
    "aarch64": {"openat2": 437, "renameat2": 276},
}


class OpenHow(ctypes.Structure):
    """struct open_how from linux/openat2.h: three u64 fields, 24 bytes."""

    _fields_ = [
        ("flags", ctypes.c_uint64),
        ("mode", ctypes.c_uint64),
        ("resolve", ctypes.c_uint64),
    ]


_libc = ctypes.CDLL(None, use_errno=True)
_syscall = _libc.syscall
_syscall.restype = ctypes.c_long


def syscall_numbers() -> dict[str, int]:
    """Return this architecture's syscall numbers, or an empty mapping if unlisted."""
    return _SYSCALL_NUMBERS.get(platform.machine(), {})


def _raise_errno() -> None:
    code = ctypes.get_errno()
    raise OSError(code, os.strerror(code))


def _require_c_string(path: bytes) -> None:
    if b"\x00" in path:
        raise ValueError("embedded null byte")


def openat2(dirfd: int, path: bytes, flags: int, mode: int, resolve: int) -> int:
    """openat2(2). No glibc wrapper exists, so this always goes through syscall()."""
    _require_c_string(path)
    how = OpenHow(flags=flags, mode=mode, resolve=resolve)
    ctypes.set_errno(0)
    result = _syscall(
        ctypes.c_long(syscall_numbers()["openat2"]),
        ctypes.c_int(dirfd),
        ctypes.c_char_p(path),
        ctypes.byref(how),
        ctypes.c_size_t(ctypes.sizeof(OpenHow)),
    )
    if result < 0:
        _raise_errno()
    return int(result)


def _resolve_renameat2():
    """glibc has exposed renameat2 since 2.28; fall back to raw syscall otherwise."""
    entry = getattr(_libc, "renameat2", None)
    if entry is not None:
        entry.restype = ctypes.c_int
        entry.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        return entry
    return None


_renameat2_symbol = _resolve_renameat2()


def renameat2(
    olddirfd: int,
    oldpath: bytes,
    newdirfd: int,
    newpath: bytes,
    flags: int,
) -> None:
    _require_c_string(oldpath)
    _require_c_string(newpath)
    ctypes.set_errno(0)
    if _renameat2_symbol is not None:
        result = _renameat2_symbol(olddirfd, oldpath, newdirfd, newpath, flags)
    else:
        result = _syscall(
            ctypes.c_long(syscall_numbers()["renameat2"]),
            ctypes.c_int(olddirfd),
            ctypes.c_char_p(oldpath),
            ctypes.c_int(newdirfd),
            ctypes.c_char_p(newpath),
            ctypes.c_uint(flags),
        )
    if result < 0:
        _raise_errno()
