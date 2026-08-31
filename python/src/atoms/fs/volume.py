"""Mount identity, durability configuration, and the certified allowlist (design §6)."""

from __future__ import annotations

import errno
import fcntl
import os
import re
import struct
from dataclasses import dataclass, field

from atoms.core.errors import CapabilityUnavailable
from atoms.fs.platform import BACKEND_REVISION

_DECIMAL = re.compile(r"[0-9]+")
_DEVICE = re.compile(r"[0-9]+:[0-9]+")
_KERNEL_ESCAPE = re.compile(r"\\(?:040|011|012|134)")
_INVALID_KERNEL_ESCAPE = re.compile(r"\\(?!040|011|012|134)")
_KERNEL_ESCAPE_VALUES = {
    r"\040": " ",
    r"\011": "\t",
    r"\012": "\n",
    r"\134": "\\",
}

_EXT4_TUNE_SB_PARAMS_SIZE = 232
_EXT4_IOC_GET_TUNE_SB_PARAM = (
    (2 << 30)  # _IOC_READ
    | (_EXT4_TUNE_SB_PARAMS_SIZE << 16)
    | (ord("f") << 8)
    | 45
)
_FEATURE_OFFSETS = (64, 68, 72)  # feature_compat, feature_incompat, feature_ro_compat


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
class FeatureMasks:
    compat: int
    incompat: int
    ro_compat: int


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


CERTIFIED_ALLOWLIST = DurabilityAllowlist(
    entries=frozenset(
        {
            AllowlistEntry(
                configuration=VolumeConfiguration(
                    backend_id="linux",
                    backend_revision="linux-4",
                    kernel_identifier="7.1.11-arch1-1",
                    filesystem_type="ext4",
                    barrier_options=("async", "barrier=1", "commit=5", "data=ordered"),
                    durability_features=(
                        "compat=0x3c",
                        "incompat=0x246",
                        "ro_compat=0x46b",
                    ),
                ),
                storage=StorageProfile(profile_id="flush-honoring-disk.v1"),
                certification_ref=(
                    "docs/certification/2026-08-30-ext4-linux-7.1.11-arch1-1.json"
                ),
            )
        }
    )
)
"""Populated by the A8 certification run; see the named record. Recertification
after a kernel or backend-revision change: python -m tools.certify run --all."""


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
    invalid = _INVALID_KERNEL_ESCAPE.search(value)
    if invalid is not None:
        raise ValueError(f"unsupported kernel escape at offset {invalid.start()}")
    return _KERNEL_ESCAPE.sub(
        lambda found: _KERNEL_ESCAPE_VALUES[found.group(0)],
        value,
    )


def _mountinfo_error(line_number: int, reason: str) -> CapabilityUnavailable:
    return CapabilityUnavailable(f"malformed mountinfo line {line_number}: {reason}")


def _decimal_field(token: str, name: str, line_number: int) -> int:
    if _DECIMAL.fullmatch(token) is None:
        raise _mountinfo_error(line_number, f"{name} is not an unsigned decimal token")
    return int(token)


def _option_field(token: str, name: str, line_number: int) -> tuple[str, ...]:
    options = tuple(token.split(","))
    if any(not option for option in options):
        raise _mountinfo_error(line_number, f"{name} contains an empty option")
    return options


def parse_mountinfo(text: str) -> tuple[MountEntry, ...]:
    entries: list[MountEntry] = []
    mount_ids: set[int] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        fields = line.split()
        if fields.count("-") != 1:
            raise _mountinfo_error(
                line_number, "expected exactly one field separator"
            )
        separator = fields.index("-")
        if separator < 6:
            raise _mountinfo_error(
                line_number, "record is missing required pre-separator fields"
            )
        if len(fields) != separator + 4:
            raise _mountinfo_error(
                line_number, "record must carry exactly three post-separator fields"
            )
        mount_id = _decimal_field(fields[0], "mount id", line_number)
        _decimal_field(fields[1], "parent mount id", line_number)
        if _DEVICE.fullmatch(fields[2]) is None:
            raise _mountinfo_error(
                line_number, "device is not an unsigned major:minor pair"
            )
        if mount_id in mount_ids:
            raise _mountinfo_error(line_number, f"duplicate mount id {mount_id}")
        mount_ids.add(mount_id)
        try:
            _unescape(fields[3])
            mount_point = _unescape(fields[4])
            _unescape(fields[separator + 2])
        except ValueError as caught:
            raise _mountinfo_error(line_number, str(caught)) from caught
        entries.append(
            MountEntry(
                mount_id=mount_id,
                device=fields[2],
                mount_point=mount_point,
                mount_options=_option_field(
                    fields[5], "mount options", line_number
                ),
                filesystem_type=fields[separator + 1],
                super_options=_option_field(
                    fields[separator + 3], "super options", line_number
                ),
            )
        )
    return tuple(entries)


def parse_mount_id(fdinfo_text: str) -> int:
    found: int | None = None
    for line_number, line in enumerate(fdinfo_text.splitlines(), start=1):
        if line.startswith("mnt_id:"):
            fields = line.split()
            if (
                len(fields) != 2
                or fields[0] != "mnt_id:"
                or _DECIMAL.fullmatch(fields[1]) is None
            ):
                raise CapabilityUnavailable(
                    f"malformed fdinfo mnt_id record at line {line_number}: "
                    "expected exactly one unsigned decimal token"
                )
            if found is not None:
                raise CapabilityUnavailable(
                    f"duplicate fdinfo mnt_id record at line {line_number}"
                )
            found = int(fields[1])
    if found is None:
        raise CapabilityUnavailable("fdinfo carries no mnt_id field")
    return found


def read_mount_id(fd: int) -> int:
    path = f"/proc/self/fdinfo/{fd}"
    with open(path, encoding="utf-8", errors="surrogateescape") as handle:
        text = handle.read()
    try:
        return parse_mount_id(text)
    except CapabilityUnavailable as caught:
        raise CapabilityUnavailable(
            f"cannot resolve mount id from {path}: {caught}"
        ) from caught


def resolve_mount_entry(fd: int, mountinfo_text: str) -> MountEntry:
    """Resolve the mount backing a held descriptor.

    Keyed on mount ID rather than st_dev: bind mounts can share a device while
    carrying distinct per-mount options, so device-keying is ambiguous.
    """
    wanted = read_mount_id(fd)
    try:
        entries = parse_mountinfo(mountinfo_text)
    except CapabilityUnavailable as caught:
        raise CapabilityUnavailable(
            f"cannot resolve mountinfo for descriptor {fd}: {caught}"
        ) from caught
    for entry in entries:
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


def resolve_ext4_feature_masks(directory_fd: int) -> FeatureMasks:
    """Read all ext4 superblock feature masks (design §7.4)."""
    buffer = bytearray(_EXT4_TUNE_SB_PARAMS_SIZE)
    try:
        fcntl.ioctl(directory_fd, _EXT4_IOC_GET_TUNE_SB_PARAM, buffer)
    except OSError as error:
        if error.errno in (errno.ENOTTY, errno.EOPNOTSUPP, errno.EINVAL):
            raise CapabilityUnavailable(
                "ext4 feature masks unresolvable: the kernel does not support "
                "EXT4_IOC_GET_TUNE_SB_PARAM"
            ) from error
        raise
    compat, incompat, ro_compat = (
        struct.unpack_from("<I", buffer, offset)[0] for offset in _FEATURE_OFFSETS
    )
    return FeatureMasks(compat=compat, incompat=incompat, ro_compat=ro_compat)


def feature_mask_options(masks: FeatureMasks) -> tuple[str, str, str]:
    return (
        f"compat={masks.compat:#x}",
        f"incompat={masks.incompat:#x}",
        f"ro_compat={masks.ro_compat:#x}",
    )


def build_configuration(
    entry: MountEntry, kernel_identifier: str, *, directory_fd: int
) -> VolumeConfiguration:
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
        # ext4 claims the masks from its already-held directory; xfs and btrfs
        # currently claim no feature masks.
        durability_features=(
            feature_mask_options(resolve_ext4_feature_masks(directory_fd))
            if entry.filesystem_type == "ext4"
            else ()
        ),
    )


def read_mountinfo() -> str:
    with open(
        "/proc/self/mountinfo",
        encoding="utf-8",
        errors="surrogateescape",
    ) as handle:
        return handle.read()


def kernel_identifier() -> str:
    return os.uname().release
