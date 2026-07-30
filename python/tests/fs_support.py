"""Shared factories for the A4a filesystem-layer tests."""

from __future__ import annotations

import os
from pathlib import Path

SUPPORTED_FILESYSTEMS = frozenset({"ext4", "xfs", "btrfs"})

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
    with open("/proc/self/mountinfo", encoding="utf-8") as handle:
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


def make_mountinfo_text():
    def lookup(case: str) -> str:
        return _MOUNTINFO_CASES[case]

    return lookup


def make_fdinfo_text():
    def lookup(case: str) -> str:
        return _FDINFO_CASES[case]

    return lookup
