"""Public commands that serialize project-chain changes under the recovery lease."""

from __future__ import annotations

import contextlib
import errno
from collections.abc import Iterator
from typing import cast as _cast

from atoms.chain.append import append_entry as _append_entry
from atoms.chain.append import apply_survivors as _apply_survivors
from atoms.chain.append import bootstrap_chain as _bootstrap_chain
from atoms.chain.errors import ChainStateInvalid
from atoms.chain.model import GenesisEntry, IntentEntry, state_to_json
from atoms.chain.read import ValidatedChain
from atoms.chain.read import validate_chain as _validate_chain
from atoms.coordinator.lease import Lease
from atoms.coordinator.root import _recovery_lease, _require_chain_publication
from atoms.core.errors import PreconditionRefused, ProtocolError, SpecValidationError
from atoms.core.fingerprint import ABSENT, PathState
from atoms.core.paths import require_rel_path
from atoms.core.recovery.model import (
    ObservedAbsent,
    ObservedDirectory,
    ObservedFile,
    ObservedSymlink,
)
from atoms.core.scratch import CHAIN_LEAF
from atoms.fs.audit import AuditedBackend
from atoms.fs.backend import Backend
from atoms.fs.lock import close_all
from atoms.fs.observe import Observation
from atoms.fs.volume import StorageProfile

__all__ = ("append_intent", "register_root")


@contextlib.contextmanager
def _registered_root(lease: Lease) -> Iterator[tuple[int, ValidatedChain]]:
    backend = _cast(AuditedBackend, lease._binding.backend)
    try:
        chain_fd = backend.open_child_directory(
            lease._binding.project_root_fd, CHAIN_LEAF
        )
    except OSError as caught:
        if caught.errno == errno.ENOENT:
            if lease._store.read_active() is not None:
                raise ChainStateInvalid(
                    "a live transaction record exists without its project chain"
                ) from caught
            raise PreconditionRefused(
                "the project root is not registered"
            ) from caught
        if caught.errno in {errno.ENOTDIR, errno.ELOOP, errno.EXDEV}:
            raise ChainStateInvalid(
                "the reserved chain leaf is not a stable directory"
            ) from caught
        raise

    try:
        validated = _apply_survivors(
            backend, chain_fd, _validate_chain(backend, chain_fd)
        )
        if not validated.entries:
            if lease._store.read_active() is not None:
                raise ChainStateInvalid(
                    "a live transaction record exists without a chain genesis"
                )
            raise PreconditionRefused("the project root is not registered")
        yield chain_fd, validated
    finally:
        backend.close_fd(chain_fd)


def _capture_baseline(
    backend: AuditedBackend,
    project_root_fd: int,
    paths: tuple[str, ...],
) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
    with Observation(backend) as observation:
        return tuple(
            (
                path,
                state_to_json(
                    _capture_path(backend, project_root_fd, path, observation)
                ),
            )
            for path in paths
        )


def _validate_registered_surface(value: object) -> tuple[str, ...]:
    if type(value) is not tuple or any(type(path) is not str for path in value):
        raise PreconditionRefused(
            "registered_surface must be an exact tuple of exact strings"
        )
    paths = _cast(tuple[str, ...], value)
    if paths != tuple(sorted(paths)):
        raise PreconditionRefused("registered_surface must be sorted")
    if len(paths) != len(set(paths)):
        raise PreconditionRefused("registered_surface must be duplicate-free")
    try:
        for path in paths:
            require_rel_path("registered surface path", path)
    except SpecValidationError as caught:
        raise PreconditionRefused(str(caught)) from caught
    return paths


def _capture_path(
    backend: AuditedBackend,
    project_root_fd: int,
    path: str,
    observation: Observation,
) -> PathState:
    parent_fd = project_root_fd
    owned: list[int] = []
    try:
        for component in path.split("/")[:-1]:
            try:
                parent_fd = backend.open_child_directory(parent_fd, component)
            except OSError as caught:
                if caught.errno == errno.ENOENT:
                    return ABSENT
                raise
            owned.append(parent_fd)
        observed = observation.observe(
            parent_fd, path.rpartition("/")[2], modeled=frozenset()
        )
        if type(observed) is ObservedAbsent:
            return ABSENT
        return _cast(ObservedFile | ObservedDirectory | ObservedSymlink, observed).state
    finally:
        close_all(backend, reversed(owned))


def register_root(
    backend: Backend,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
    genesis_payload: bytes,
    registered_surface: tuple[str, ...],
) -> str:
    if type(genesis_payload) is not bytes:
        raise ProtocolError("genesis_payload must be exact bytes")
    registered_surface = _validate_registered_surface(registered_surface)
    with _recovery_lease(backend, project_root, metadata_root, storage) as lease:
        chain_backend = _cast(AuditedBackend, lease._binding.backend)
        _require_chain_publication(lease._binding.evidence)
        chain_fd = _bootstrap_chain(
            chain_backend, lease._binding.project_root_fd
        )
        try:
            validated = _apply_survivors(
                chain_backend,
                chain_fd,
                _validate_chain(chain_backend, chain_fd),
            )
            if validated.entries:
                digest, genesis = validated.entries[0]
                if (
                    type(genesis) is GenesisEntry
                    and genesis.payload == genesis_payload
                    and tuple(path for path, _ in genesis.baseline)
                    == registered_surface
                ):
                    return digest
                raise PreconditionRefused(
                    "the project root is already registered with a different "
                    "payload or surface"
                )
            baseline = _capture_baseline(
                chain_backend,
                lease._binding.project_root_fd,
                registered_surface,
            )
            return _append_entry(
                chain_backend,
                chain_fd,
                validated,
                GenesisEntry(genesis_payload, baseline),
            )
        finally:
            chain_backend.close_fd(chain_fd)


def append_intent(
    backend: Backend,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
    payload: bytes,
) -> str:
    with _recovery_lease(backend, project_root, metadata_root, storage) as lease:
        chain_backend = _cast(AuditedBackend, lease._binding.backend)
        _require_chain_publication(lease._binding.evidence)
        with _registered_root(lease) as (chain_fd, validated):
            return _append_entry(
                chain_backend, chain_fd, validated, IntentEntry(payload)
            )
