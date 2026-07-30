"""Atoms filesystem layer: platform capabilities and project volume binding."""

from atoms.core.capabilities import Capability
from atoms.fs.backend import Backend
from atoms.fs.binding import ProjectBinding, VolumeEvidence, bind_project_volume
from atoms.fs.bootstrap import reclaim_probe_survivors
from atoms.fs.lock import HeldProjectLock, acquire_project_lock
from atoms.fs.platform import select_backend
from atoms.fs.volume import (
    CERTIFIED_ALLOWLIST,
    AllowlistEntry,
    DurabilityAllowlist,
    StorageProfile,
    VolumeConfiguration,
)

__all__ = [
    "CERTIFIED_ALLOWLIST",
    "AllowlistEntry",
    "Backend",
    "Capability",
    "DurabilityAllowlist",
    "HeldProjectLock",
    "ProjectBinding",
    "StorageProfile",
    "VolumeConfiguration",
    "VolumeEvidence",
    "acquire_project_lock",
    "bind_project_volume",
    "reclaim_probe_survivors",
    "select_backend",
]
