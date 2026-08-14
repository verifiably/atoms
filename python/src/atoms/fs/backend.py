"""The semantic filesystem capability protocol (design §5.1).

The surface is determined by what must be *probed*, not by anticipation of later
stages: exactly one operation set per design §5.5 capability, and every method is
called by the probe that reports it. There is deliberately no supplied_capabilities
method — a backend that reported its own capabilities would make the evidence
circular, when the probe exists precisely to establish what the backend cannot be
trusted to assert.
"""

from __future__ import annotations

import errno
import os
from collections.abc import Callable
from typing import Protocol

# Errno values that conclusively mean "this volume does not support the operation".
#
# EINVAL from renameat2 and EPERM from link are ambiguous in general — they equally
# signal a malformed argument or a permission failure. What disambiguates them is
# the probe precondition: every probe constructs its own operands, inside a
# directory it created, under the held project lock, immediately before the call.
# Arguments are therefore valid and permissions guaranteed by construction, so
# neither interpretation is reachable. The same errno from any other call site
# propagates.
#
# The table lives here, beside the protocol, because it has two consumers: probe.py
# reports absence from it, and lock.py converts the "traversal" and "lock" entries to
# CapabilityUnavailable on the bootstrap path (design §10). probe.py imports lock.py,
# so defining it there would force either a cycle or a second copy that drifts.
#
# Primary manual bases, recorded per operation rather than inferred:
# - renameat2(2): EINVAL when the filesystem does not support a requested flag.
# - link(2): EPERM when the filesystem does not support hard links.
# - fsync(2): EINVAL when the descriptor's object does not support synchronization.
# - symlink(2): EPERM when the filesystem does not support symbolic-link creation;
#   the probe stages creation inside the symlink_fingerprint capability check.
# - errno(3): ENOSYS is "function not implemented"; ENOTSUP is "operation not
#   supported". ENOTSUP and EOPNOTSUPP have the same numeric value on Linux, but
#   both spellings remain because a structural Backend may come from another OS.
# - flock(2): ENOLCK means lock-record memory exhaustion, so it is deliberately
#   absent here and propagates.
UNSUPPORTED_ERRNO: dict[str, frozenset[int]] = {
    # renameat2(2) RENAME_EXCHANGE.
    "exchange": frozenset({errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.ENOTSUP}),
    # renameat2(2) RENAME_NOREPLACE.
    "transfer_noclobber": frozenset(
        {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.ENOTSUP}
    ),
    # link(2), plus the semantic Backend "operation not supported" result.
    "link_anchor": frozenset({errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP}),
    # fsync(2), plus the semantic Backend "operation not supported" result.
    "flush": frozenset({errno.EINVAL, errno.EOPNOTSUPP, errno.ENOTSUP}),
    # open(2) has no Linux-specific unsupported O_NOFOLLOW result; this is the
    # semantic Backend result only.
    "open_regular_nofollow": frozenset({errno.EOPNOTSUPP, errno.ENOTSUP}),
    # symlink(2), plus the semantic Backend "operation not supported" result.
    "symlink_fingerprint": frozenset({errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP}),
    # Semantic Backend result only. flock(2) ENOLCK is transient exhaustion.
    "lock": frozenset({errno.EOPNOTSUPP, errno.ENOTSUP}),
    # ENOSYS is the kernel without openat2. EOPNOTSUPP/ENOTSUP is a backend that
    # cannot supply the guarded walk at all — the shape a restricted backend takes.
    "traversal": frozenset({errno.ENOSYS, errno.EOPNOTSUPP, errno.ENOTSUP}),
}


class Backend(Protocol):
    def create_exclusive(self, parent_fd: int, name: str, mode: int) -> int: ...

    def write(self, fd: int, data: bytes) -> int: ...

    def set_mode(self, fd: int, mode: int) -> None: ...

    def mkdir_child(self, parent_fd: int, name: str, mode: int) -> None: ...

    def unlink_child(self, parent_fd: int, name: str) -> None: ...

    def rmdir_child(self, parent_fd: int, name: str) -> None: ...

    def symlink_child(self, parent_fd: int, name: str, target: str) -> None: ...

    def create_or_open(self, parent_fd: int, name: str, mode: int) -> int: ...

    def open_existing(
        self,
        parent_fd: int,
        name: str,
        *,
        read_write: bool = False,
        nofollow: bool = False,
    ) -> int: ...

    def set_marker_xattr(self, fd: int, name: str, value: bytes) -> None: ...

    def repair_entry_mode(
        self,
        parent_fd: int,
        name: str,
        mode: int,
        *,
        before_change: Callable[[], None],
    ) -> None: ...

    def close_fd(self, fd: int) -> None: ...

    def detach_fd(self, fd: int) -> None: ...

    # anchored_traversal
    def open_root(self, path: str) -> int:
        """Establish an anchor. Refuses a symlink at *any* component, not just the leaf."""
        ...

    def open_child_directory(self, parent_fd: int, name: str) -> int:
        """Guarded traversal of one component beneath a retained descriptor."""
        ...

    def open_directory_handle(self, parent_fd: int, name: str) -> int: ...

    # atomic_exchange
    def exchange(self, parent_fd: int, left: str, right: str) -> None: ...

    # noclobber_transfer
    def transfer_noclobber(self, src_fd: int, src: str, dst_fd: int, dst: str) -> None: ...

    # identity_anchor
    def link_anchor(self, src_fd: int, src: str, dst_fd: int, dst: str) -> None: ...

    # durable_publish
    def flush_file(self, fd: int) -> None: ...

    def flush_directory(self, fd: int) -> None: ...

    # nofollow_coherent_read
    def open_regular_nofollow(self, parent_fd: int, name: str) -> int: ...

    # symlink_fingerprint
    def symlink_fingerprint(self, parent_fd: int, name: str) -> tuple[os.stat_result, str]:
        """lstat + readlink. Deliberately NOT descriptor-coherent (design §5.1)."""
        ...

    # advisory_project_lock
    def lock_exclusive(self, fd: int) -> None: ...

    def try_lock_exclusive(self, fd: int) -> bool:
        """Non-blocking acquisition. A successful lock proves acquisition, not exclusion."""
        ...
