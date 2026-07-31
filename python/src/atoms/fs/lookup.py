"""Per-directory lookup constraints (A4b-1 design §5).

Linux and ext4 only, deliberately. FS_IOC_GETFLAGS reports *that* an ext4 directory
casefolds but never *which* Unicode version governs the comparison: the encoding lives
in the superblock, is not exported through /sys, and has no statx attribute. A folding
directory is therefore reported as unreproducible rather than modeled, and every other
filesystem refuses until it has a proof mechanism of its own.
"""

from __future__ import annotations

import array
import errno
import fcntl
import os
from dataclasses import dataclass
from enum import Enum

from atoms.core.errors import CapabilityUnavailable

FS_IOC_GETFLAGS = 0x80086601
"""_IOR('f', 1, long) on 64-bit Linux."""

FS_CASEFOLD_FL = 0x40000000
"""Per-directory case-insensitive lookup, settable only on a casefold-enabled ext4."""

EXT4_NAME_MAX = 255
"""ext4 bounds a filename to 255 bytes (kernel ext4 directory format)."""

_SUPPORTED_FILESYSTEM = "ext4"


class LookupProof(Enum):
    """What the engine can *prove* about a directory's lookup relation.

    Not a description of the semantics: UNREPRODUCIBLE_CASEFOLD says the relation
    cannot be reproduced, never that it is known.
    """

    EXACT_BYTES = "exact_bytes"
    UNREPRODUCIBLE_CASEFOLD = "unreproducible_casefold"


@dataclass(frozen=True, slots=True)
class DirectoryConstraints:
    lookup_proof: LookupProof
    name_max: int


def _require_supported(filesystem_type: str) -> None:
    if filesystem_type != _SUPPORTED_FILESYSTEM:
        raise CapabilityUnavailable(
            f"filesystem {filesystem_type!r} has no lookup-proof mechanism; "
            "only non-casefold ext4 can be approved"
        )


def _raw_flags(fd: int) -> int:
    buffer = array.array("l", [0])
    try:
        fcntl.ioctl(fd, FS_IOC_GETFLAGS, buffer, True)
    except OSError as caught:
        if caught.errno == errno.ENOTTY:
            raise CapabilityUnavailable(
                "FS_IOC_GETFLAGS is not implemented for this directory; "
                "the engine cannot determine its lookup relation"
            ) from caught
        raise
    return buffer[0]


def _casefold_flag(fd: int) -> bool:
    return bool(_raw_flags(fd) & FS_CASEFOLD_FL)


def _name_max(fd: int) -> int:
    value = os.fpathconf(fd, "PC_NAME_MAX")
    if value <= 0:
        raise CapabilityUnavailable(
            f"PC_NAME_MAX is indeterminate ({value}); the engine cannot bound component names"
        )
    return value


def read_lookup_constraints(fd: int, filesystem_type: str) -> DirectoryConstraints:
    """Observe one directory's lookup constraints through a held descriptor.

    Dispatch precedes the ioctl so ext4 flag semantics are never applied to a
    filesystem that does not define them.
    """
    _require_supported(filesystem_type)
    proof = (
        LookupProof.UNREPRODUCIBLE_CASEFOLD
        if _casefold_flag(fd)
        else LookupProof.EXACT_BYTES
    )
    return DirectoryConstraints(lookup_proof=proof, name_max=_name_max(fd))


def inherited_constraints(
    parent: DirectoryConstraints, filesystem_type: str
) -> DirectoryConstraints:
    """Constraints a directory created under `parent` will carry.

    ext4 inherits the casefold flag, so the parent's proof carries down unchanged; a
    directory that does not exist yet cannot be queried for PC_NAME_MAX, so the bound
    is ext4's documented 255-byte filename limit.
    """
    _require_supported(filesystem_type)
    return DirectoryConstraints(
        lookup_proof=parent.lookup_proof, name_max=EXT4_NAME_MAX
    )
