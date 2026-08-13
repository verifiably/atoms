"""Policy-derived audit boundary for every engine filesystem operation."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TypeVar

from atoms.core.errors import ProtocolError
from atoms.core.scratch import CHAIN_LEAF, is_scratch_leaf
from atoms.fs.backend import Backend
from atoms.fs.lock import _guarded_spelling


class TargetClass(Enum):
    DECLARED_EFFECT = "declared-effect"
    ENGINE_SCRATCH = "engine-scratch"
    METADATA = "metadata"
    CHAIN_BOOKKEEPING = "chain-bookkeeping"


class RootKind(Enum):
    PROJECT = "project"
    METADATA = "metadata"
    METADATA_PARENT = "metadata-parent"


@dataclass(frozen=True, slots=True)
class Provenance:
    root: RootKind
    path: str


@dataclass(frozen=True, slots=True)
class AuditRecord:
    operation: str
    targets: tuple[tuple[TargetClass, str], ...]


_T = TypeVar("_T")
_Target = tuple[TargetClass, str]


def _require_logical_path(path: str, *, allow_empty: bool) -> None:
    if type(path) is not str or (not path and not allow_empty):
        raise ProtocolError("a provenance path must be a string relative to its root")
    if path:
        components = path.split("/")
        if any(component in {"", ".", ".."} for component in components) or "\x00" in path:
            raise ProtocolError(
                f"provenance path {path!r} must contain only non-dot path components"
            )


def _join(provenance: Provenance, leaf: str) -> str:
    _require_logical_path(leaf, allow_empty=False)
    if "/" in leaf:
        raise ProtocolError(f"leaf {leaf!r} must be a single path component")
    return f"{provenance.path}/{leaf}" if provenance.path else leaf


class AuditedBackend:
    """Backend facade that derives target authority from descriptor provenance."""

    def __init__(self, inner: Backend, *, project_root: str, metadata_root: str) -> None:
        if type(project_root) is not str or not project_root:
            raise ProtocolError("project_root must be a non-empty string")
        if type(metadata_root) is not str or not metadata_root:
            raise ProtocolError("metadata_root must be a non-empty string")
        project_spelling = _guarded_spelling(project_root)
        metadata_spelling = _guarded_spelling(metadata_root)
        if any(
            ".." in spelling.split(os.sep)
            for spelling in (project_spelling, metadata_spelling)
        ):
            raise ProtocolError("configured roots must not contain a parent component")
        if project_spelling == metadata_spelling:
            raise ProtocolError("project_root and metadata_root must be distinct")
        metadata_parent, metadata_leaf = os.path.split(metadata_spelling)
        if not metadata_parent or not metadata_leaf:
            raise ProtocolError("metadata_root must have a parent and a final component")
        roots = {
            project_spelling: Provenance(RootKind.PROJECT, ""),
            metadata_spelling: Provenance(RootKind.METADATA, ""),
        }
        if metadata_parent != project_spelling:
            roots[metadata_parent] = Provenance(RootKind.METADATA_PARENT, "")
        self._inner = inner
        self._roots = roots
        self._metadata_leaf = metadata_leaf
        self._metadata_parent_is_project = metadata_parent == project_spelling
        self._provenance: dict[int, Provenance] = {}
        self._declared_paths: frozenset[str] = frozenset()
        self._records: list[AuditRecord] = []

    @property
    def records(self) -> tuple[AuditRecord, ...]:
        return tuple(self._records)

    def register(self, fd: int, provenance: Provenance) -> None:
        self._require_fd(fd)
        self._require_provenance(provenance)
        if fd in self._provenance:
            raise ProtocolError(f"descriptor {fd} is already registered")
        self._provenance[fd] = provenance

    def rebind(self, fd: int, provenance: Provenance) -> None:
        current = self.provenance_of(fd)
        self._require_provenance(provenance)
        if not (
            current.root is RootKind.METADATA
            and current.path.startswith("work/")
            and provenance.root is RootKind.PROJECT
            and provenance.path in self._declared_paths
        ):
            raise ProtocolError(
                "descriptor rebind is permitted only from metadata work to a declared project path"
            )
        self._provenance[fd] = provenance

    def unregister(self, fd: int) -> None:
        self.provenance_of(fd)
        del self._provenance[fd]

    def provenance_of(self, fd: int) -> Provenance:
        self._require_fd(fd)
        try:
            return self._provenance[fd]
        except KeyError as caught:
            raise ProtocolError(f"descriptor {fd!r} is unregistered") from caught

    def close_fd(self, fd: int) -> None:
        self.unregister(fd)
        self._inner.close_fd(fd)

    def set_declared_paths(self, paths: frozenset[str]) -> None:
        if type(paths) is not frozenset:
            raise ProtocolError("declared paths must be an exact frozenset")
        for path in paths:
            _require_logical_path(path, allow_empty=False)
        self._declared_paths = paths

    def clear_declared_paths(self) -> None:
        self._declared_paths = frozenset()

    def create_exclusive(self, parent_fd: int, name: str, mode: int) -> int:
        provenance = self.provenance_of(parent_fd)
        target = self._classify_child(provenance, name)
        fd = self._inner.create_exclusive(parent_fd, name, mode)
        self.register(fd, Provenance(provenance.root, target[1]))
        self._append("create_exclusive", target)
        return fd

    def write(self, fd: int, data: bytes) -> int:
        target = self._classify_provenance(self.provenance_of(fd))
        return self._audited("write", (target,), lambda: self._inner.write(fd, data))

    def set_mode(self, fd: int, mode: int) -> None:
        target = self._classify_provenance(self.provenance_of(fd))
        self._audited("set_mode", (target,), lambda: self._inner.set_mode(fd, mode))

    def mkdir_child(self, parent_fd: int, name: str, mode: int) -> None:
        provenance = self.provenance_of(parent_fd)
        target = self._classify_child(
            provenance,
            name,
            allow_metadata_root_creation=True,
        )
        self._audited(
            "mkdir_child",
            (target,),
            lambda: self._inner.mkdir_child(parent_fd, name, mode),
        )

    def unlink_child(self, parent_fd: int, name: str) -> None:
        target = self._classify_child(self.provenance_of(parent_fd), name)
        self._audited(
            "unlink_child",
            (target,),
            lambda: self._inner.unlink_child(parent_fd, name),
        )

    def rmdir_child(self, parent_fd: int, name: str) -> None:
        target = self._classify_child(self.provenance_of(parent_fd), name)
        self._audited(
            "rmdir_child",
            (target,),
            lambda: self._inner.rmdir_child(parent_fd, name),
        )

    def symlink_child(self, parent_fd: int, name: str, target: str) -> None:
        audit_target = self._classify_child(self.provenance_of(parent_fd), name)
        self._audited(
            "symlink_child",
            (audit_target,),
            lambda: self._inner.symlink_child(parent_fd, name, target),
        )

    def create_or_open(self, parent_fd: int, name: str, mode: int) -> int:
        provenance = self.provenance_of(parent_fd)
        target = self._classify_child(provenance, name)
        fd = self._inner.create_or_open(parent_fd, name, mode)
        self.register(fd, Provenance(provenance.root, target[1]))
        self._append("create_or_open", target)
        return fd

    def set_marker_xattr(self, fd: int, name: str, value: bytes) -> None:
        target = self._classify_provenance(self.provenance_of(fd))
        self._audited(
            "set_marker_xattr",
            (target,),
            lambda: self._inner.set_marker_xattr(fd, name, value),
        )

    def repair_entry_mode(self, parent_fd: int, name: str, mode: int) -> None:
        target = self._classify_child(self.provenance_of(parent_fd), name)
        self._audited(
            "repair_entry_mode",
            (target,),
            lambda: self._inner.repair_entry_mode(parent_fd, name, mode),
        )

    def open_root(self, path: str) -> int:
        if type(path) is not str or not path:
            raise ProtocolError("a root path must be a non-empty string")
        spelling = _guarded_spelling(path)
        try:
            provenance = self._roots[spelling]
        except KeyError as caught:
            raise ProtocolError(f"path {path!r} is not a configured root") from caught
        fd = self._inner.open_root(spelling)
        self.register(fd, provenance)
        return fd

    def open_child_directory(self, parent_fd: int, name: str) -> int:
        provenance = self.provenance_of(parent_fd)
        path = _join(provenance, name)
        child_provenance = (
            Provenance(RootKind.METADATA, "")
            if self._is_metadata_root_child(provenance, name)
            else Provenance(provenance.root, path)
        )
        fd = self._inner.open_child_directory(parent_fd, name)
        self.register(fd, child_provenance)
        return fd

    def exchange(self, parent_fd: int, left: str, right: str) -> None:
        provenance = self.provenance_of(parent_fd)
        targets = (
            self._classify_child(provenance, left),
            self._classify_child(provenance, right),
        )
        self._audited(
            "exchange",
            targets,
            lambda: self._inner.exchange(parent_fd, left, right),
        )

    def transfer_noclobber(
        self, src_fd: int, src: str, dst_fd: int, dst: str
    ) -> None:
        targets = (
            self._classify_child(self.provenance_of(src_fd), src),
            self._classify_child(self.provenance_of(dst_fd), dst),
        )
        self._audited(
            "transfer_noclobber",
            targets,
            lambda: self._inner.transfer_noclobber(src_fd, src, dst_fd, dst),
        )

    def link_anchor(self, src_fd: int, src: str, dst_fd: int, dst: str) -> None:
        targets = (
            self._classify_child(self.provenance_of(src_fd), src),
            self._classify_child(self.provenance_of(dst_fd), dst),
        )
        self._audited(
            "link_anchor",
            targets,
            lambda: self._inner.link_anchor(src_fd, src, dst_fd, dst),
        )

    def flush_file(self, fd: int) -> None:
        self.provenance_of(fd)
        self._inner.flush_file(fd)

    def flush_directory(self, fd: int) -> None:
        self.provenance_of(fd)
        self._inner.flush_directory(fd)

    def open_regular_nofollow(self, parent_fd: int, name: str) -> int:
        provenance = self.provenance_of(parent_fd)
        path = _join(provenance, name)
        fd = self._inner.open_regular_nofollow(parent_fd, name)
        self.register(fd, Provenance(provenance.root, path))
        return fd

    def symlink_fingerprint(
        self, parent_fd: int, name: str
    ) -> tuple[os.stat_result, str]:
        provenance = self.provenance_of(parent_fd)
        _join(provenance, name)
        return self._inner.symlink_fingerprint(parent_fd, name)

    def lock_exclusive(self, fd: int) -> None:
        self.provenance_of(fd)
        self._inner.lock_exclusive(fd)

    def try_lock_exclusive(self, fd: int) -> bool:
        self.provenance_of(fd)
        return self._inner.try_lock_exclusive(fd)

    def _require_provenance(self, provenance: Provenance) -> None:
        if type(provenance) is not Provenance or type(provenance.root) is not RootKind:
            raise ProtocolError("descriptor provenance has the wrong exact runtime type")
        _require_logical_path(provenance.path, allow_empty=True)

    @staticmethod
    def _require_fd(fd: int) -> None:
        if type(fd) is not int or fd < 0:
            raise ProtocolError("a descriptor must be a non-negative integer")

    def _classify_child(
        self,
        provenance: Provenance,
        leaf: str,
        *,
        allow_metadata_root_creation: bool = False,
    ) -> _Target:
        path = _join(provenance, leaf)
        if provenance.root is RootKind.METADATA:
            return TargetClass.METADATA, path
        if (
            allow_metadata_root_creation
            and self._is_metadata_root_child(provenance, leaf)
        ):
            return TargetClass.METADATA, ""
        if provenance.root is RootKind.PROJECT:
            if path.partition("/")[0] == CHAIN_LEAF:
                return TargetClass.CHAIN_BOOKKEEPING, path
            if is_scratch_leaf(leaf):
                return TargetClass.ENGINE_SCRATCH, path
            if path in self._declared_paths:
                return TargetClass.DECLARED_EFFECT, path
        raise ProtocolError(f"{path!r} is not an authorized target")

    def _is_metadata_root_child(self, provenance: Provenance, leaf: str) -> bool:
        return leaf == self._metadata_leaf and (
            provenance == Provenance(RootKind.METADATA_PARENT, "")
            or self._metadata_parent_is_project
            and provenance == Provenance(RootKind.PROJECT, "")
        )

    def _classify_provenance(self, provenance: Provenance) -> _Target:
        if provenance.root is RootKind.METADATA:
            return TargetClass.METADATA, provenance.path
        if provenance.root is RootKind.PROJECT and provenance.path:
            if provenance.path.partition("/")[0] == CHAIN_LEAF:
                return TargetClass.CHAIN_BOOKKEEPING, provenance.path
            if is_scratch_leaf(provenance.path.rsplit("/", 1)[-1]):
                return TargetClass.ENGINE_SCRATCH, provenance.path
            if provenance.path in self._declared_paths:
                return TargetClass.DECLARED_EFFECT, provenance.path
        raise ProtocolError(f"{provenance.path!r} is not an authorized target")

    def _audited(
        self,
        operation: str,
        targets: tuple[_Target, ...],
        call: Callable[[], _T],
    ) -> _T:
        result = call()
        self._records.append(AuditRecord(operation, targets))
        return result

    def _append(self, operation: str, target: _Target) -> None:
        self._records.append(AuditRecord(operation, (target,)))
