"""Anchored rooted path resolution (A4b-1 design §6).

Observation only. Nothing here sees a CompiledSpec, builds a topology, or writes to
project space; A4b-2 composes these observations into the approval proof.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from enum import Enum

from atoms.core.errors import CapabilityUnavailable, ProjectApprovalRefused
from atoms.fs.binding import ProjectBinding
from atoms.fs.lookup import DirectoryConstraints, LookupProof, read_lookup_constraints

_LINUX = "linux"


@dataclass(frozen=True, slots=True)
class FilesystemIdentity:
    """A directory or entry's identity. Deliberately excludes declared spelling, so
    two spellings reaching one directory compare equal."""

    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class DirectoryFacts:
    identity: FilesystemIdentity
    constraints: DirectoryConstraints


class EntryKind(Enum):
    DIRECTORY = "directory"
    REGULAR_FILE = "regular_file"
    SYMLINK = "symlink"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class AbsentFrontier:
    """Nothing occupies the frontier name."""


@dataclass(frozen=True, slots=True)
class PresentFrontier:
    identity: FilesystemIdentity
    kind: EntryKind


Frontier = AbsentFrontier | PresentFrontier


@dataclass(frozen=True, slots=True)
class ResolvedHop:
    declared_component: str
    facts: DirectoryFacts


@dataclass(frozen=True, slots=True)
class ResolvedPrefix:
    """The deepest existing directory prefix of one declared path.

    `remainder == ()` means `frontier` describes the declared leaf. Otherwise
    `remainder` holds every declared component after the blocking frontier, ending
    with the leaf.
    """

    root: DirectoryFacts
    hops: tuple[ResolvedHop, ...]
    frontier_name: str
    frontier: Frontier
    remainder: tuple[str, ...]

    @property
    def deepest_constraints(self) -> DirectoryConstraints:
        """Derived rather than stored, so it cannot drift out of step with `hops`."""
        if self.hops:
            return self.hops[-1].facts.constraints
        return self.root.constraints


def _identity(info: os.stat_result) -> FilesystemIdentity:
    return FilesystemIdentity(device=info.st_dev, inode=info.st_ino)


def _entry_kind(mode: int) -> EntryKind:
    if stat.S_ISDIR(mode):
        return EntryKind.DIRECTORY
    if stat.S_ISREG(mode):
        return EntryKind.REGULAR_FILE
    if stat.S_ISLNK(mode):
        return EntryKind.SYMLINK
    return EntryKind.OTHER


def _path_max(fd: int) -> int:
    value = os.fpathconf(fd, "PC_PATH_MAX")
    if value <= 0:
        raise CapabilityUnavailable(
            f"PC_PATH_MAX is indeterminate ({value}); the engine cannot bound path lengths"
        )
    return value


class PathResolver:
    """Approval-scoped. Constructed inside approve_for_project and nowhere else.

    Owns no descriptors between calls: every descriptor it opens is released before
    the call returns, so it needs no context manager, spent flag, or release
    discipline of its own. The memo is a plain dict keyed by directory identity.
    """

    __slots__ = (
        "_binding",
        "_facts_by_identity",
        "_filesystem_type",
        "_metadata_identity",
        "_path_max",
        "_work_base",
    )

    def __init__(self, binding: ProjectBinding) -> None:
        # Liveness gate. project_root_fd routes through ProjectBinding._require_active,
        # which checks the binding's own flag AND lock.held. It is read before
        # `evidence`, a detached value whose property performs no such check. One
        # checked property is sufficient here; resolve() and work_base_facts() each
        # read the properties they need behind the same gate.
        root_fd = binding.project_root_fd
        evidence = binding.evidence
        configuration = evidence.configuration
        if configuration.backend_id != _LINUX:
            raise CapabilityUnavailable(
                f"backend {configuration.backend_id!r} is not {_LINUX!r}; "
                "lookup constraints are read with Linux ext4 flag semantics"
            )
        self._binding = binding
        self._filesystem_type = configuration.filesystem_type
        self._metadata_identity = FilesystemIdentity(
            device=evidence.metadata_root_device, inode=evidence.metadata_root_inode
        )
        self._path_max = _path_max(root_fd)
        identity = _identity(os.fstat(root_fd))
        if identity == self._metadata_identity:
            raise ProjectApprovalRefused(
                "the project root and the metadata root are the same directory; "
                "no declared path could avoid the metadata namespace"
            )
        constraints = read_lookup_constraints(root_fd, self._filesystem_type)
        if constraints.lookup_proof is LookupProof.UNREPRODUCIBLE_CASEFOLD:
            raise ProjectApprovalRefused(
                "the project root is a casefold directory; its lookup relation "
                "cannot be reproduced, so no path beneath it can be approved"
            )
        # Seeds the memo rather than a dedicated field. resolve() re-observes the root
        # through the same path as every other hop (§6.5), so a stored copy would only
        # be a second, unchecked answer.
        self._facts_by_identity: dict[FilesystemIdentity, DirectoryFacts] = {
            identity: DirectoryFacts(identity, constraints)
        }
        self._work_base: DirectoryFacts | None = None
