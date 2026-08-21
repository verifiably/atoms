"""Public commands that serialize project-chain changes under the recovery lease."""

from __future__ import annotations

import errno
from dataclasses import dataclass
from typing import cast as _cast

from atoms.chain.append import append_entry as _append_entry
from atoms.chain.append import bootstrap_chain as _bootstrap_chain
from atoms.chain.model import (
    ChainOutcome,
    Entry,
    GenesisEntry,
    IntentEntry,
    state_to_json,
)
from atoms.chain.read import validate_chain as _validate_chain
from atoms.coordinator.capture import PayloadSource
from atoms.coordinator.execute import _run_under_lease
from atoms.coordinator.recover import _registered_root
from atoms.coordinator.root import _recovery_lease, _require_chain_publication
from atoms.core.compiler import compile_spec
from atoms.core.errors import PreconditionRefused, ProtocolError, SpecValidationError
from atoms.core.fingerprint import ABSENT, PathState
from atoms.core.paths import require_rel_path
from atoms.core.recovery.model import (
    ObservedAbsent,
    ObservedDirectory,
    ObservedFile,
    ObservedSymlink,
)
from atoms.core.spec import TransactionSpec
from atoms.fs.audit import AuditedBackend
from atoms.fs.backend import Backend
from atoms.fs.lock import close_all
from atoms.fs.observe import Observation
from atoms.fs.volume import StorageProfile

__all__ = (
    "ChainView",
    "Entry",
    "TransactionOutcome",
    "append_intent",
    "read_chain",
    "register_root",
    "run_transaction",
)


@dataclass(frozen=True, slots=True)
class TransactionOutcome:
    txid: str
    outcome: ChainOutcome
    registration: str
    settlement: str


@dataclass(frozen=True)
class ChainView:
    """One validated chain, projected for a consumer, holding no engine resource.

    `entries` is `_linearize`'s order: genesis first, one successor per entry, tip
    last. `genesis_digest` is `entries[0][0]` and `tip` is the validator's own tip,
    not a re-derivation of `entries[-1][0]`.
    """

    genesis_digest: str
    entries: tuple[tuple[str, Entry], ...]
    tip: str


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
            validated = _validate_chain(chain_backend, chain_fd)
            if validated.survivors:
                raise ProtocolError("chain staging appeared after lease resolution")
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


def run_transaction(
    backend: Backend,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
    spec: TransactionSpec,
    payloads: PayloadSource,
) -> TransactionOutcome:
    compiled = compile_spec(spec)
    with _recovery_lease(
        backend, project_root, metadata_root, storage
    ) as lease:
        _require_chain_publication(lease._binding.evidence)
        with _registered_root(lease) as (chain_fd, validated):
            result = _run_under_lease(
                lease, chain_fd, validated, compiled, payloads
            )
    return TransactionOutcome(
        txid=result.txid,
        outcome=ChainOutcome.COMMITTED,
        registration=result.registration,
        settlement=result.settlement,
    )


def read_chain(
    backend: Backend,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
) -> ChainView:
    """Project the validated chain `_registered_root` already computed.

    The lease is what makes the answer true: it holds the project lock and resolves
    recovery before yielding, so this reads no survivor as chain state and races no
    cooperating writer. `_require_chain_publication` is deliberately absent -- the
    three mutators call it to refuse before their own append, and this command has
    none.
    """
    with (
        _recovery_lease(backend, project_root, metadata_root, storage) as lease,
        _registered_root(lease) as (_chain_fd, validated),
    ):
        if validated.tip is None:
            raise ProtocolError("a registered chain has no tip")
        return ChainView(
            genesis_digest=validated.entries[0][0],
            entries=validated.entries,
            tip=validated.tip,
        )
