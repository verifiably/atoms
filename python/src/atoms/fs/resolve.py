"""Anchored rooted path resolution (A4b-1 design §6).

Observation only. Nothing here sees a CompiledSpec, builds a topology, or writes to
project space; A4b-2 composes these observations into the approval proof.
"""

from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass
from enum import Enum

from atoms.core.errors import (
    CapabilityUnavailable,
    PreconditionRefused,
    ProjectApprovalRefused,
    ProtocolError,
    SpecValidationError,
)
from atoms.core.paths import require_rel_path
from atoms.fs.binding import ProjectBinding
from atoms.fs.lock import close_all
from atoms.fs.lookup import DirectoryConstraints, LookupProof, read_lookup_constraints
from atoms.fs.volume import read_mount_id

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


_BLOCKER_KINDS = {
    errno.ENOTDIR: (EntryKind.REGULAR_FILE, EntryKind.OTHER),
    errno.ELOOP: (EntryKind.SYMLINK,),
}


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

    _OBSERVE_FLAGS = os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC

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

    def resolve(self, rel_path: str) -> ResolvedPrefix:
        """Observe the deepest existing directory prefix of one declared path.

        Accepts declared persistent paths only. A4b-2 resolves a scratch parent and
        validates generated leaf names against that parent's approved name_max.
        """
        backend = self._binding.backend
        root_fd = self._binding.project_root_fd
        try:
            require_rel_path("path", rel_path)
        except SpecValidationError as caught:
            raise ProtocolError(
                f"resolve() requires a well-formed project-relative path: {caught}"
            ) from caught
        encoded = len(os.fsencode(rel_path)) + 1
        if encoded > self._path_max:
            raise ProjectApprovalRefused(
                f"path {rel_path!r} needs {encoded} bytes including the terminating NUL, "
                f"over the volume PATH_MAX of {self._path_max}"
            )

        components = rel_path.split("/")
        ancestors, leaf = components[:-1], components[-1]
        # The root is re-observed per call, like every other hop. Constraints read once
        # at construction would otherwise be reported unchecked for this object's life.
        root_facts = self._facts_for(root_fd, rel_path)
        facts = root_facts
        parent_fd = root_fd
        owned: int | None = None
        hops: list[ResolvedHop] = []
        try:
            for index, component in enumerate(ancestors):
                self._require_name_fits(component, facts, rel_path)
                try:
                    child = backend.open_child_directory(parent_fd, component)
                except OSError as caught:
                    return ResolvedPrefix(
                        root=root_facts,
                        hops=tuple(hops),
                        frontier_name=component,
                        frontier=self._frontier_from(
                            caught, parent_fd, component, rel_path
                        ),
                        remainder=tuple(components[index + 1 :]),
                    )
                # Reassign before releasing, so a failing close cannot strand `child`.
                previous, owned = owned, child
                parent_fd = child
                if previous is not None:
                    close_all((previous,))
                facts = self._facts_for(child, rel_path)
                hops.append(ResolvedHop(component, facts))

            self._require_name_fits(leaf, facts, rel_path)
            return ResolvedPrefix(
                root=root_facts,
                hops=tuple(hops),
                frontier_name=leaf,
                frontier=self._observe(parent_fd, leaf, rel_path),
                remainder=(),
            )
        finally:
            if owned is not None:
                close_all((owned,))

    def _require_name_fits(
        self, component: str, facts: DirectoryFacts, rel_path: str
    ) -> None:
        width = len(os.fsencode(component))
        if width > facts.constraints.name_max:
            raise ProjectApprovalRefused(
                f"component {component!r} of {rel_path!r} is {width} bytes, over its "
                f"parent's NAME_MAX of {facts.constraints.name_max}"
            )

    def _frontier_from(
        self, caught: OSError, parent_fd: int, component: str, rel_path: str
    ) -> Frontier:
        if caught.errno is None:
            # No errno is no evidence, so it is not one of the five interpreted codes.
            # Also what keeps _BLOCKER_KINDS.get well-typed: OSError.errno is int|None.
            raise caught
        if caught.errno == errno.ENOENT:
            return AbsentFrontier()
        if caught.errno == errno.EXDEV:
            # RESOLVE_BENEATH and RESOLVE_NO_XDEV share EXDEV, but require_rel_path
            # rejected every escape spelling before any syscall and
            # RESOLVE_NO_SYMLINKS turns symlinks into ELOOP, so no escape reaches here.
            raise ProjectApprovalRefused(
                f"component {component!r} of {rel_path!r} crosses a mount boundary"
            ) from caught
        if caught.errno == errno.ENAMETOOLONG:
            raise ProjectApprovalRefused(
                f"component {component!r} of {rel_path!r} exceeds the filesystem name limit"
            ) from caught
        admissible = _BLOCKER_KINDS.get(caught.errno)
        if admissible is None:
            raise caught
        observed = self._observe(parent_fd, component, rel_path)
        if isinstance(observed, AbsentFrontier) or observed.kind not in admissible:
            raise PreconditionRefused(
                f"component {component!r} of {rel_path!r} changed between the traversal "
                f"attempt and its observation; the engine will not assemble one "
                f"observation from two filesystem moments"
            ) from caught
        return observed

    def _facts_for(self, fd: int, rel_path: str) -> DirectoryFacts:
        """Observe one traversed directory. The memo interns; it never skips the read.

        FS_CASEFOLD_FL can be set on an empty directory without changing its inode, so
        a cache hit that skipped read_lookup_constraints could report EXACT_BYTES for a
        lookup that already happened under casefold semantics — a wrong answer produced
        inside one approval, which is a window ledger #19 does not cover. The memo's
        jobs are to hand back one DirectoryFacts object per identity, so A4b-2 can
        compare by object, and to notice disagreement.
        """
        identity = _identity(os.fstat(fd))
        if identity == self._metadata_identity:
            raise ProjectApprovalRefused(
                f"{rel_path!r} traverses the metadata root by identity "
                f"(device {identity.device}, inode {identity.inode})"
            )
        constraints = read_lookup_constraints(fd, self._filesystem_type)
        if constraints.lookup_proof is LookupProof.UNREPRODUCIBLE_CASEFOLD:
            raise ProjectApprovalRefused(
                f"{rel_path!r} traverses a casefold directory whose lookup relation "
                "cannot be reproduced"
            )
        cached = self._facts_by_identity.get(identity)
        if cached is None:
            facts = DirectoryFacts(identity, constraints)
            self._facts_by_identity[identity] = facts
            return facts
        if cached.constraints != constraints:
            raise PreconditionRefused(
                f"the directory at device {identity.device}, inode {identity.inode} "
                f"changed its lookup constraints during one approval: {cached.constraints} "
                f"then {constraints}; the engine will not assemble one proof from two "
                f"filesystem moments"
            )
        return cached

    def _observe(self, parent_fd: int, name: str, rel_path: str) -> Frontier:
        """Observe one entry coherently: kind, identity, and mount membership.

        O_PATH | O_NOFOLLOW returns the entry itself rather than following a symlink,
        and fdinfo answers for the same descriptor that fstat did — lstat could not
        see a bind mount that shares st_dev with its source.
        """
        try:
            fd = os.open(name, self._OBSERVE_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError:
            return AbsentFrontier()
        except OSError as caught:
            if caught.errno == errno.ENAMETOOLONG:
                raise ProjectApprovalRefused(
                    f"component {name!r} of {rel_path!r} exceeds the filesystem name limit"
                ) from caught
            raise
        try:
            info = os.fstat(fd)
            identity = _identity(info)
            if identity == self._metadata_identity:
                raise ProjectApprovalRefused(
                    f"{rel_path!r} resolves to the metadata root by identity "
                    f"(device {identity.device}, inode {identity.inode})"
                )
            mount = read_mount_id(fd)
            expected = self._binding.evidence.mount_id
            if mount != expected:
                raise ProjectApprovalRefused(
                    f"component {name!r} of {rel_path!r} is on mount {mount}, "
                    f"not the bound volume's mount {expected}"
                )
        finally:
            close_all((fd,))
        return PresentFrontier(identity, _entry_kind(info.st_mode))
