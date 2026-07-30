"""Mount identity, durability configuration, and the certified allowlist (design §6)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from atoms.core.errors import CapabilityUnavailable
from atoms.fs.platform import BACKEND_REVISION

_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")


@dataclass(frozen=True, slots=True)
class MountEntry:
    mount_id: int
    device: str
    mount_point: str
    mount_options: tuple[str, ...]
    filesystem_type: str
    super_options: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VolumeConfiguration:
    backend_id: str
    backend_revision: str
    kernel_identifier: str
    filesystem_type: str
    barrier_options: tuple[str, ...]
    durability_features: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StorageProfile:
    """A declaration by the trusted composition root, never a runtime observation.

    No syscall can establish whether device firmware honors a cache flush; that is
    what crash certification tests. /sys/block is deliberately not consulted:
    `rotational` describes media classification, `write_cache` is a writable kernel
    view that can suppress flushes without changing hardware, and a dm/md topology
    describes routing — none is a durability verdict.
    """

    profile_id: str


@dataclass(frozen=True, slots=True)
class AllowlistEntry:
    configuration: VolumeConfiguration
    storage: StorageProfile
    certification_ref: str


@dataclass(frozen=True, slots=True)
class DurabilityAllowlist:
    entries: frozenset[AllowlistEntry] = field(default_factory=frozenset)

    def match(
        self, configuration: VolumeConfiguration, storage: StorageProfile
    ) -> AllowlistEntry | None:
        for entry in self.entries:
            if entry.configuration == configuration and entry.storage == storage:
                return entry
        return None


CERTIFIED_ALLOWLIST = DurabilityAllowlist(entries=frozenset())
"""Empty until A8 crash-certifies a configuration tuple. Production binding fails closed."""


# Per-filesystem barrier-relevant options: the exact mountinfo field each is read
# from, and the value it normalizes to when absent. Field 6 is per-mount (VFS
# flags); field 11 is superblock options, where filesystem-specific durability
# values live. Reading only field 6 would find no data= at all.
_SUPER = "super"
_PER_MOUNT = "mount"

_BARRIER_OPTIONS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "ext4": (
        ("barrier", _SUPER, "barrier=1"),
        ("data", _SUPER, "data=ordered"),
        ("journal_async_commit", _SUPER, ""),
        ("commit", _SUPER, "commit=5"),
        ("sync", _PER_MOUNT, "async"),
        ("dirsync", _PER_MOUNT, ""),
    ),
    "xfs": (
        ("barrier", _SUPER, "barrier=1"),
        ("wsync", _SUPER, ""),
        ("sync", _PER_MOUNT, "async"),
    ),
    "btrfs": (
        ("barrier", _SUPER, "barrier=1"),
        ("flushoncommit", _SUPER, "noflushoncommit"),
        ("commit", _SUPER, "commit=30"),
        ("notreelog", _SUPER, ""),
    ),
}


def _unescape(value: str) -> str:
    return _OCTAL_ESCAPE.sub(lambda found: chr(int(found.group(1), 8)), value)


def parse_mountinfo(text: str) -> tuple[MountEntry, ...]:
    entries = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        separator = fields.index("-")
        entries.append(
            MountEntry(
                mount_id=int(fields[0]),
                device=fields[2],
                mount_point=_unescape(fields[4]),
                mount_options=tuple(fields[5].split(",")),
                filesystem_type=fields[separator + 1],
                super_options=tuple(fields[separator + 3].split(",")),
            )
        )
    return tuple(entries)


def parse_mount_id(fdinfo_text: str) -> int:
    for line in fdinfo_text.splitlines():
        if line.startswith("mnt_id:"):
            return int(line.split()[1])
    raise CapabilityUnavailable("fdinfo carries no mnt_id field")


def read_mount_id(fd: int) -> int:
    with open(f"/proc/self/fdinfo/{fd}", encoding="utf-8") as handle:
        return parse_mount_id(handle.read())


def resolve_mount_entry(fd: int, mountinfo_text: str) -> MountEntry:
    """Resolve the mount backing a held descriptor.

    Keyed on mount ID rather than st_dev: bind mounts can share a device while
    carrying distinct per-mount options, so device-keying is ambiguous.
    """
    wanted = read_mount_id(fd)
    for entry in parse_mountinfo(mountinfo_text):
        if entry.mount_id == wanted:
            return entry
    raise CapabilityUnavailable(f"no mount entry for mount id {wanted}")


def _select(name: str, source: str, absent: str, entry: MountEntry) -> str:
    options = entry.super_options if source == _SUPER else entry.mount_options
    for option in options:
        if option == name or option.startswith(f"{name}="):
            return option
        if option == f"no{name}":
            return option
    return absent


def build_configuration(entry: MountEntry, kernel_identifier: str) -> VolumeConfiguration:
    table = _BARRIER_OPTIONS.get(entry.filesystem_type)
    if table is None:
        raise CapabilityUnavailable(
            f"filesystem {entry.filesystem_type!r} has no barrier-option table; "
            "the engine cannot decide which of its options bear on durability"
        )
    values = {_select(name, source, absent, entry) for name, source, absent in table}
    values.discard("")
    return VolumeConfiguration(
        backend_id="linux",
        backend_revision=BACKEND_REVISION,
        kernel_identifier=kernel_identifier,
        filesystem_type=entry.filesystem_type,
        barrier_options=tuple(sorted(values)),
        # No certified entry references a superblock feature yet, and an entry may
        # not reference a feature whose resolver does not exist. Empty means
        # "nothing claimed", not "unresolved".
        durability_features=(),
    )


def read_mountinfo() -> str:
    with open("/proc/self/mountinfo", encoding="utf-8") as handle:
        return handle.read()


def kernel_identifier() -> str:
    return os.uname().release
