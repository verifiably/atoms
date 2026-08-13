"""Semantic filesystem capability vocabulary (design §5.5), expressed as data."""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from functools import singledispatch

from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    Effect,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.fingerprint import FileState


class Capability(Enum):
    ATOMIC_EXCHANGE = "atomic_exchange"
    NOCLOBBER_TRANSFER = "noclobber_transfer"
    IDENTITY_ANCHOR = "identity_anchor"
    ANCHORED_TRAVERSAL = "anchored_traversal"
    DURABLE_PUBLISH = "durable_publish"
    NOFOLLOW_COHERENT_READ = "nofollow_coherent_read"
    SYMLINK_FINGERPRINT = "symlink_fingerprint"
    ADVISORY_PROJECT_LOCK = "advisory_project_lock"


ALWAYS_REQUIRED: frozenset[Capability] = frozenset(
    {
        Capability.ANCHORED_TRAVERSAL,
        Capability.DURABLE_PUBLISH,
        Capability.ADVISORY_PROJECT_LOCK,
        Capability.NOCLOBBER_TRANSFER,
    }
)


@singledispatch
def variant_capabilities(effect: Effect) -> frozenset[Capability]:
    raise TypeError(f"unknown effect variant: {type(effect).__name__}")


@variant_capabilities.register
def _(effect: ReplaceFile) -> frozenset[Capability]:
    return frozenset({Capability.ATOMIC_EXCHANGE, Capability.NOFOLLOW_COHERENT_READ})


@variant_capabilities.register
def _(effect: CreateFileNoClobber) -> frozenset[Capability]:
    return frozenset({Capability.NOCLOBBER_TRANSFER})


@variant_capabilities.register
def _(effect: DeletePath) -> frozenset[Capability]:
    read = (
        Capability.NOFOLLOW_COHERENT_READ
        if isinstance(effect.pre, FileState)
        else Capability.SYMLINK_FINGERPRINT
    )
    return frozenset({Capability.NOCLOBBER_TRANSFER, read})


@variant_capabilities.register
def _(effect: MoveNoClobber) -> frozenset[Capability]:
    return frozenset(
        {
            Capability.IDENTITY_ANCHOR,
            Capability.NOCLOBBER_TRANSFER,
            Capability.NOFOLLOW_COHERENT_READ,
        }
    )


@variant_capabilities.register
def _(effect: CreateDirectory) -> frozenset[Capability]:
    return frozenset({Capability.NOCLOBBER_TRANSFER})


def required_capabilities(effects: Iterable[Effect]) -> frozenset[Capability]:
    caps = set(ALWAYS_REQUIRED)
    for effect in effects:
        caps |= variant_capabilities(effect)
    return frozenset(caps)
