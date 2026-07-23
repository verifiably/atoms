"""The closed effect set and a uniform per-path occurrence view (design §5.2)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import singledispatch

from atoms.core.fingerprint import (
    ABSENT,
    DirectoryState,
    FileState,
    PathState,
    SymlinkState,
)

RelPath = str


@dataclass(frozen=True, slots=True)
class ReplaceFile:
    effect_id: str
    path: RelPath
    pre: FileState
    post: FileState


@dataclass(frozen=True, slots=True)
class CreateFileNoClobber:
    effect_id: str
    path: RelPath
    post: FileState


@dataclass(frozen=True, slots=True)
class DeletePath:
    effect_id: str
    path: RelPath
    pre: FileState | SymlinkState


@dataclass(frozen=True, slots=True)
class MoveNoClobber:
    effect_id: str
    source: RelPath
    destination: RelPath
    source_pre: FileState


@dataclass(frozen=True, slots=True)
class CreateDirectory:
    effect_id: str
    path: RelPath
    post: DirectoryState


Effect = ReplaceFile | CreateFileNoClobber | DeletePath | MoveNoClobber | CreateDirectory


@dataclass(frozen=True, slots=True)
class Occurrence:
    """One path's (pre, post) transition within a single effect."""

    path: RelPath
    pre: PathState
    post: PathState
    role: str


@singledispatch
def occurrences(effect: Effect) -> tuple[Occurrence, ...]:
    raise TypeError(f"unknown effect variant: {type(effect).__name__}")


@occurrences.register
def _(effect: ReplaceFile) -> tuple[Occurrence, ...]:
    return (Occurrence(path=effect.path, pre=effect.pre, post=effect.post, role="target"),)


@occurrences.register
def _(effect: CreateFileNoClobber) -> tuple[Occurrence, ...]:
    return (Occurrence(path=effect.path, pre=ABSENT, post=effect.post, role="target"),)


@occurrences.register
def _(effect: DeletePath) -> tuple[Occurrence, ...]:
    return (Occurrence(path=effect.path, pre=effect.pre, post=ABSENT, role="target"),)


@occurrences.register
def _(effect: MoveNoClobber) -> tuple[Occurrence, ...]:
    return (
        Occurrence(path=effect.source, pre=effect.source_pre, post=ABSENT, role="source"),
        Occurrence(path=effect.destination, pre=ABSENT, post=effect.source_pre, role="destination"),
    )


@occurrences.register
def _(effect: CreateDirectory) -> tuple[Occurrence, ...]:
    return (Occurrence(path=effect.path, pre=ABSENT, post=effect.post, role="target"),)


def effect_id_of(effect: Effect) -> str:
    return effect.effect_id


def variant_name(effect: Effect) -> str:
    return type(effect).__name__
