"""Shared factories for the A4a filesystem-layer tests."""

from __future__ import annotations

import os
from pathlib import Path

SUPPORTED_FILESYSTEMS = frozenset({"ext4", "xfs", "btrfs"})


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
