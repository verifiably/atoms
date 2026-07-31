"""Shared factories for the A4a filesystem-layer tests."""

from __future__ import annotations

import contextlib
import errno as _errno
import os
from pathlib import Path

from atoms.core.capabilities import Capability
from atoms.fs.bootstrap import close_layout, ensure_metadata_layout, verified_child_path
from atoms.fs.linux import LinuxBackend
from atoms.fs.lock import acquire_project_lock
from atoms.fs.volume import (
    AllowlistEntry,
    DurabilityAllowlist,
    build_configuration,
    kernel_identifier,
    read_mountinfo,
    resolve_mount_entry,
)

SUPPORTED_FILESYSTEMS = frozenset({"ext4", "xfs", "btrfs"})
EXT4 = "ext4"

_MOUNTINFO_CASES = {
    "ext4_defaults": (
        "25 30 259:1 / / rw,relatime shared:1 - ext4 /dev/nvme0n1p1 rw\n"
        "41 25 259:2 / /data rw,noatime shared:2 - ext4 /dev/nvme0n1p2 rw\n"
    ),
    "ext4_writeback": (
        "41 25 259:2 / /data rw,noatime shared:2 - ext4 /dev/nvme0n1p2 "
        "rw,data=writeback\n"
    ),
    "ext4_sync": (
        "41 25 259:2 / /data rw,sync,dirsync shared:2 - ext4 /dev/nvme0n1p2 rw\n"
    ),
    "ext4_wrong_field_decoys": (
        "41 25 259:2 / /data rw,noatime,nobarrier,data=writeback,"
        "journal_async_commit,commit=15 shared:2 - ext4 /dev/nvme0n1p2 "
        "rw,sync,dirsync\n"
    ),
    # Every filesystem in the barrier table gets a defaults fixture and a
    # super-options-only fixture. Shipping a table without both would ship an
    # untested durability claim (design §11.1).
    "xfs_defaults": (
        "41 25 259:2 / /data rw,noatime shared:2 - xfs /dev/nvme0n1p2 "
        "rw,attr2,inode64,logbufs=8,logbsize=32k,noquota\n"
    ),
    "xfs_wsync": (
        "41 25 259:2 / /data rw,noatime shared:2 - xfs /dev/nvme0n1p2 "
        "rw,wsync,attr2,inode64,noquota\n"
    ),
    "xfs_wrong_field_decoys": (
        "41 25 259:2 / /data rw,noatime,nobarrier,wsync shared:2 - xfs "
        "/dev/nvme0n1p2 rw,sync\n"
    ),
    "btrfs_defaults": (
        "41 25 0:33 /@ /data rw,noatime shared:2 - btrfs /dev/nvme0n1p2 "
        "rw,space_cache=v2,subvolid=256,subvol=/@\n"
    ),
    "btrfs_flushoncommit": (
        "41 25 0:33 /@ /data rw,noatime shared:2 - btrfs /dev/nvme0n1p2 "
        "rw,flushoncommit,commit=15,space_cache=v2,subvolid=256,subvol=/@\n"
    ),
    "btrfs_wrong_field_decoys": (
        "41 25 0:33 /@ /data rw,noatime,nobarrier,flushoncommit,commit=15,"
        "notreelog shared:2 - btrfs /dev/nvme0n1p2 rw\n"
    ),
    "escaped_space": (
        "41 25 259:2 / /mnt/my\\040volume rw,noatime shared:2 - ext4 /dev/nvme0n1p2 rw\n"
    ),
    "optional_fields": (
        "41 25 259:2 / /shared rw,noatime shared:2 master:7 propagate_from:3 "
        "- ext4 /dev/nvme0n1p2 rw\n"
    ),
    "bind_same_device": (
        "41 25 259:2 / /data rw,noatime shared:2 - ext4 /dev/nvme0n1p2 rw\n"
        "43 25 259:2 /sub /data/bind rw,noatime shared:2 - ext4 /dev/nvme0n1p2 rw\n"
    ),
    "tmpfs": "22 25 0:21 / /tmp rw,nosuid,nodev - tmpfs tmpfs rw,inode64\n",
}

_FDINFO_CASES = {
    "plain": "pos:\t0\nflags:\t02000000\nmnt_id:\t41\nino:\t131074\n",
}


def _filesystem_type_for(path: Path) -> str | None:
    """Return the filesystem type backing `path`, from /proc/self/mountinfo."""
    target = os.stat(path)
    device = f"{os.major(target.st_dev)}:{os.minor(target.st_dev)}"
    with open(
        "/proc/self/mountinfo",
        encoding="utf-8",
        errors="surrogateescape",
    ) as handle:
        for line in handle:
            fields = line.split()
            separator = fields.index("-")
            if fields[2] == device:
                return fields[separator + 1]
    return None


def resolve_test_volume() -> Path | None:
    """Resolve a writable directory on a supported filesystem, or None.

    Tier 3 must not assume this machine's layout: /tmp is tmpfs on most Linux
    systems and refuses by design, so pytest's default tmp_path is unusable here.
    """
    declared = os.environ.get("ATOMS_TEST_VOLUME")
    if declared:
        return Path(declared)
    repository = Path(__file__).parents[2]
    if _filesystem_type_for(repository) in SUPPORTED_FILESYSTEMS:
        return repository / ".atoms-test-volume"
    return None


def test_volume_or_skip_reason() -> tuple[Path | None, str]:
    resolved = resolve_test_volume()
    if resolved is not None:
        return resolved, ""
    repository = Path(__file__).parents[2]
    found = _filesystem_type_for(repository)
    return None, (
        f"no supported test volume: repository filesystem is {found!r}; "
        "set ATOMS_TEST_VOLUME to a directory on ext4, xfs, or btrfs"
    )


def ext4_volume_or_skip_reason() -> tuple[Path | None, str]:
    """A4b-1 approves only ext4, while A4a admits ext4, xfs, and btrfs.

    A contributor on btrfs must be told why this suite skips, not merely that it does.
    """
    resolved = resolve_test_volume()
    if resolved is None:
        return None, "no test volume: set ATOMS_TEST_VOLUME to a directory on ext4"
    probe = resolved if resolved.exists() else resolved.parent
    found = _filesystem_type_for(probe)
    if found != EXT4:
        return None, (
            f"A4b-1 approves only ext4; the test volume is {found!r}. "
            "Set ATOMS_TEST_VOLUME to a directory on ext4."
        )
    return resolved, ""


def descriptor_count() -> int:
    """Open descriptors for this process.

    The listing itself opens one descriptor, so this contributes a constant; only
    deltas between counts measured this way are meaningful.
    """
    return len(os.listdir("/proc/self/fd"))


def make_mountinfo_text():
    def lookup(case: str) -> str:
        return _MOUNTINFO_CASES[case]

    return lookup


def make_fdinfo_text():
    def lookup(case: str) -> str:
        return _FDINFO_CASES[case]

    return lookup


def make_metadata_root(base):
    """A path that does NOT yet exist, so bootstrap creation is exercised."""
    return base / "metadata"


def make_project_root(base):
    root = base / "project"
    root.mkdir()
    return root


class RestrictedBackend:
    """A capability-restricted backend proving the protocol admits a non-Linux one.

    It delegates to a real LinuxBackend for supplied capabilities and raises a
    chosen errno for absent ones, so every refusal branch is reachable without a
    filesystem that genuinely lacks the operation.

    A method-level key such as `open_child_directory_errno` targets one concrete
    method. A contract-level key from UNSUPPORTED_ERRNO, such as `traversal_errno`
    or `flush_errno`, targets every method implementing that capability. The latter
    is what lets the table-derived mutation matrix cover every operation key.

    `override_names` narrows either form to specific final components. This is
    load-bearing, not a convenience: a probe whose evidence is a refusal calls the
    same operation twice — once to establish availability, once to require the
    refusal — and an unscoped override fails the FIRST call, so the test would pass
    through the availability path and keep passing if the refusal check were
    weakened to accept any OSError. Scoping by name puts the injection at the step
    under test. A method that passes no `name` is never overridden while
    `override_names` is set, which is the intended reading of "only these names".
    """

    _ABSENT_ERRNO = _errno.EOPNOTSUPP

    def __init__(self, supplied, lock_excludes=True, override_names=None, **errno_overrides):
        self._supplied = set(supplied)
        self._lock_excludes = lock_excludes
        self._override_names = None if override_names is None else frozenset(override_names)
        self._overrides = errno_overrides
        self._real = LinuxBackend()

    def _dispatch(self, capability, operation, *args, contract=None, name=None):
        # Method-level overrides preserve the named-refusal injection used by the
        # exact-errno guard tests. Contract-level overrides key directly from
        # UNSUPPORTED_ERRNO and drive its complete generated matrix.
        override = self._overrides.get(f"{operation}_errno")
        if override is None and contract is not None:
            override = self._overrides.get(f"{contract}_errno")
        if override is not None and (
            self._override_names is None or name in self._override_names
        ):
            raise OSError(override, "injected")
        if capability not in self._supplied:
            raise OSError(self._ABSENT_ERRNO, "capability withheld")
        return getattr(self._real, operation)(*args)

    def open_root(self, path):
        return self._dispatch(
            Capability.ANCHORED_TRAVERSAL,
            "open_root",
            path,
            contract="traversal",
            name=path,
        )

    def open_child_directory(self, parent_fd, name):
        return self._dispatch(
            Capability.ANCHORED_TRAVERSAL,
            "open_child_directory",
            parent_fd,
            name,
            contract="traversal",
            name=name,
        )

    def exchange(self, parent_fd, left, right):
        return self._dispatch(
            Capability.ATOMIC_EXCHANGE,
            "exchange",
            parent_fd,
            left,
            right,
            contract="exchange",
        )

    def transfer_noclobber(self, src_fd, src, dst_fd, dst):
        return self._dispatch(
            Capability.NOCLOBBER_TRANSFER,
            "transfer_noclobber",
            src_fd,
            src,
            dst_fd,
            dst,
            contract="transfer_noclobber",
        )

    def link_anchor(self, src_fd, src, dst_fd, dst):
        return self._dispatch(
            Capability.IDENTITY_ANCHOR,
            "link_anchor",
            src_fd,
            src,
            dst_fd,
            dst,
            contract="link_anchor",
        )

    def flush_file(self, fd):
        return self._dispatch(
            Capability.DURABLE_PUBLISH, "flush_file", fd, contract="flush"
        )

    def flush_directory(self, fd):
        return self._dispatch(
            Capability.DURABLE_PUBLISH, "flush_directory", fd, contract="flush"
        )

    def open_regular_nofollow(self, parent_fd, name):
        return self._dispatch(
            Capability.NOFOLLOW_COHERENT_READ,
            "open_regular_nofollow",
            parent_fd,
            name,
            contract="open_regular_nofollow",
            name=name,
        )

    def symlink_fingerprint(self, parent_fd, name):
        return self._dispatch(
            Capability.SYMLINK_FINGERPRINT,
            "symlink_fingerprint",
            parent_fd,
            name,
            contract="symlink_fingerprint",
            name=name,
        )

    def lock_exclusive(self, fd):
        return self._dispatch(
            Capability.ADVISORY_PROJECT_LOCK,
            "lock_exclusive",
            fd,
            contract="lock",
        )

    def try_lock_exclusive(self, fd):
        if not self._lock_excludes:
            # A filesystem where flock succeeds but does not actually exclude —
            # the real case on NFS without a working lock daemon.
            return True
        return self._dispatch(
            Capability.ADVISORY_PROJECT_LOCK,
            "try_lock_exclusive",
            fd,
            contract="lock",
        )


def make_fake_backend():
    def build(supplied, lock_excludes=True, override_names=None, **errno_overrides):
        return RestrictedBackend(
            supplied,
            lock_excludes=lock_excludes,
            override_names=override_names,
            **errno_overrides,
        )

    return build


@contextlib.contextmanager
def metadata_layout(lock):
    """Own the descriptors ensure_metadata_layout returns; close each exactly once.

    A test that drops the return value leaks one descriptor per layout component and
    silently violates the plan's own close-exactly-once audit, which is why the audit
    gets a helper rather than a reminder. Release goes through the same close_layout
    production uses, so the helper cannot pass while production releases in a
    different order or with a weaker guarantee.
    """
    retained = ensure_metadata_layout(lock)
    try:
        yield retained
    finally:
        close_layout(retained)


@contextlib.contextmanager
def probe_directory(lock):
    """Yield an owned descriptor to `probe/` with the whole layout owned around it.

    Building the layout and then reopening `probe/` separately would strand the four
    layout descriptors, so the probe descriptor is taken from the layout itself.
    """
    with metadata_layout(lock) as retained:
        yield retained["probe"]


@contextlib.contextmanager
def probe_database_path(lock):
    """Yield the verified pathname of a throwaway database inside `probe/`.

    Goes through verified_child_path rather than joining, because that is the only
    sanctioned way a pathname escapes the descriptor discipline (design §9.4).
    """
    with metadata_layout(lock):
        info = os.fstat(lock.metadata_root_fd)
        probe_dir = verified_child_path(
            lock.metadata_root_fd, lock.metadata_root_path, info.st_dev, info.st_ino, "probe"
        )
        yield os.path.join(probe_dir, "certify.db")


def build_test_allowlist(lock, project_root, storage):
    """A singleton allowlist naming the resolved tuple of the actual test volume.

    This is the deliberate test assumption made visible: production passes
    CERTIFIED_ALLOWLIST, which ships empty. Tuple-resolution correctness is proved
    separately against fixture mountinfo text, never by this live-volume path.
    """
    backend = lock.backend
    fd = backend.open_root(str(project_root))
    try:
        entry = resolve_mount_entry(fd, read_mountinfo())
    finally:
        os.close(fd)
    configuration = build_configuration(entry, kernel_identifier())
    return DurabilityAllowlist(
        entries=frozenset(
            {
                AllowlistEntry(
                    configuration=configuration,
                    storage=storage,
                    certification_ref="test-injected-not-crash-certified",
                )
            }
        )
    )


def make_test_allowlist():
    return build_test_allowlist


def make_bound_volume(backend_factory, project_root, metadata_root, storage):
    from atoms.fs.binding import bind_project_volume

    @contextlib.contextmanager
    def bind(withhold=frozenset()):
        from atoms.core.capabilities import Capability

        backend = (
            backend_factory(supplied=set(Capability) - set(withhold))
            if withhold
            else LinuxBackend()
        )
        with acquire_project_lock(backend, str(metadata_root)) as lock:
            allowlist = build_test_allowlist(lock, project_root, storage)
            with bind_project_volume(
                str(project_root), lock, allowlist=allowlist, storage=storage
            ) as binding:
                yield binding

    return bind


def find_distinct_mount(base):
    """A writable directory on a mount whose ID differs from `base`'s, or None."""
    base_fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        from atoms.fs.volume import read_mount_id

        base_id = read_mount_id(base_fd)
    finally:
        os.close(base_fd)
    for candidate in ("/tmp", "/dev/shm", f"/run/user/{os.getuid()}"):
        path = Path(candidate)
        if not path.is_dir() or not os.access(path, os.W_OK):
            continue
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            if read_mount_id(fd) != base_id:
                return path
        finally:
            os.close(fd)
    return None
