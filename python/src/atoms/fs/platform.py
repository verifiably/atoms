"""Explicit platform and architecture selection (design §5.3)."""

from __future__ import annotations

import os  # noqa: F401  # referenced by the no-I/O architecture test
import platform
import sys

from atoms.core.errors import CapabilityUnavailable
from atoms.fs.backend import Backend

BACKEND_REVISION = "linux-2"
"""Atoms backend contract revision (design §6.2).

Bump this deliberately when the backend's syscall selection, flag set, or durability ordering
changes. Bumping de-certifies every allowlist entry naming the old revision, which is intended:
a crash test certifies a volume configuration *and* the backend code that issued the syscalls.
"""


def select_backend() -> Backend:
    """Return a Backend for this host, or refuse. Performs no I/O."""
    if sys.platform != "linux":
        raise CapabilityUnavailable(
            f"unsupported platform {sys.platform!r}: only linux is implemented"
        )
    from atoms.fs.syscalls import linux

    machine = platform.machine()
    if not linux.syscall_numbers():
        raise CapabilityUnavailable(
            f"unsupported architecture {machine!r}: no syscall table entry"
        )
    from atoms.fs.linux import LinuxBackend

    return LinuxBackend()
