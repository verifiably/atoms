"""Path-state fingerprints — the declared/observed state of a single path (design §6)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PathKind(Enum):
    ABSENT = "absent"
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"


@dataclass(frozen=True, slots=True)
class AbsentState:
    """The path does not exist."""


@dataclass(frozen=True, slots=True)
class FileState:
    """A regular file: content hash, exact permission bits, and byte length."""

    content_hash: str
    mode: int
    byte_len: int


@dataclass(frozen=True, slots=True)
class DirectoryState:
    """A directory with exact permission bits."""

    mode: int


@dataclass(frozen=True, slots=True)
class SymlinkState:
    """A symlink: its target and exact permission bits (lstat-coherent)."""

    target: str
    mode: int


PathState = AbsentState | FileState | DirectoryState | SymlinkState

ABSENT = AbsentState()


def kind_of(state: PathState) -> PathKind:
    match state:
        case AbsentState():
            return PathKind.ABSENT
        case FileState():
            return PathKind.FILE
        case DirectoryState():
            return PathKind.DIRECTORY
        case SymlinkState():
            return PathKind.SYMLINK
