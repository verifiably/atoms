"""Descriptor-anchored sites for effect execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from atoms.coordinator.descriptors import DescriptorTable
from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    Effect,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.errors import ProtocolError
from atoms.core.recovery.model import ScratchRole
from atoms.fs.approval import ProjectApprovedSpec


@dataclass(frozen=True, slots=True)
class ReplaceSite:
    effect_id: str
    parent_fd: int
    live_leaf: str
    staging_leaf: str


@dataclass(frozen=True, slots=True)
class CreateFileSite:
    effect_id: str
    parent_fd: int
    live_leaf: str
    staging_leaf: str


@dataclass(frozen=True, slots=True)
class DeleteSite:
    effect_id: str
    parent_fd: int
    live_leaf: str
    tombstone_leaf: str


@dataclass(frozen=True, slots=True)
class MoveSite:
    effect_id: str
    source_fd: int
    source_leaf: str
    anchor_leaf: str
    destination_fd: int
    destination_leaf: str


@dataclass(frozen=True, slots=True)
class MkdirSite:
    effect_id: str
    work_fd: int
    work_leaf: str
    parent_fd: int
    live_leaf: str


Site: TypeAlias = ReplaceSite | CreateFileSite | DeleteSite | MoveSite | MkdirSite


def _site_for(
    approved: ProjectApprovedSpec, table: DescriptorTable, effect: Effect
) -> Site:
    paths = {entry.path: entry for entry in approved.paths}
    scratch = {
        (entry.effect_id, entry.role): entry for entry in approved.scratch
    }

    def path(value: str):
        try:
            entry = paths[value]
        except KeyError as caught:
            raise ProtocolError(f"approved proof has no path row for {value!r}") from caught
        return table.fd_for(entry.parent_node), entry.leaf

    def scratch_slot(role: ScratchRole):
        try:
            entry = scratch[(effect.effect_id, role)]
        except KeyError as caught:
            raise ProtocolError(
                f"approved proof has no {role.value} scratch row for {effect.effect_id!r}"
            ) from caught
        return table.fd_for(entry.parent_node), entry.leaf

    match effect:
        case ReplaceFile():
            parent_fd, live_leaf = path(effect.path)
            scratch_fd, staging_leaf = scratch_slot(ScratchRole.STAGING)
            if scratch_fd != parent_fd:
                raise ProtocolError("replace staging and live path have different parents")
            return ReplaceSite(effect.effect_id, parent_fd, live_leaf, staging_leaf)
        case CreateFileNoClobber():
            parent_fd, live_leaf = path(effect.path)
            scratch_fd, staging_leaf = scratch_slot(ScratchRole.STAGING)
            if scratch_fd != parent_fd:
                raise ProtocolError("create staging and live path have different parents")
            return CreateFileSite(effect.effect_id, parent_fd, live_leaf, staging_leaf)
        case DeletePath():
            parent_fd, live_leaf = path(effect.path)
            scratch_fd, tombstone_leaf = scratch_slot(ScratchRole.TOMBSTONE)
            if scratch_fd != parent_fd:
                raise ProtocolError("delete tombstone and live path have different parents")
            return DeleteSite(effect.effect_id, parent_fd, live_leaf, tombstone_leaf)
        case MoveNoClobber():
            source_fd, source_leaf = path(effect.source)
            destination_fd, destination_leaf = path(effect.destination)
            anchor_fd, anchor_leaf = scratch_slot(ScratchRole.ANCHOR)
            if anchor_fd != source_fd:
                raise ProtocolError("move anchor and source path have different parents")
            return MoveSite(
                effect.effect_id,
                source_fd,
                source_leaf,
                anchor_leaf,
                destination_fd,
                destination_leaf,
            )
        case CreateDirectory():
            parent_fd, live_leaf = path(effect.path)
            work_fd, work_leaf = scratch_slot(ScratchRole.WORK)
            return MkdirSite(effect.effect_id, work_fd, work_leaf, parent_fd, live_leaf)
        case _:
            raise ProtocolError(f"unknown effect variant {type(effect).__name__}")
