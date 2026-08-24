"""Public commands that serialize project-chain changes under the recovery lease."""

from __future__ import annotations

import contextlib
import enum
import errno
import os
import stat as _stat_module
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast as _cast

from atoms.chain.append import append_entry as _append_entry
from atoms.chain.append import bootstrap_chain as _bootstrap_chain
from atoms.chain.errors import ChainStateInvalid, PendingUnresolved
from atoms.chain.inspect import (
    OCCUPIED_CHAIN_LEAF,
    AbsentChain,
    ChainDefect,
    ChainInspection,
    ChainScan,
    DefectKind,
    MalformedChain,
    WellFormedChain,
    _pending_of,
)
from atoms.chain.inspect import committed_fulfillments as _committed_fulfillments
from atoms.chain.inspect import fulfills_fault as _fulfills_fault
from atoms.chain.inspect import inspect_scan as _inspect_scan
from atoms.chain.inspect import scan_chain_directory as _scan_chain_directory
from atoms.chain.inspect import staged_pending as _staged_pending
from atoms.chain.model import (
    ChainOutcome,
    Entry,
    GenesisEntry,
    IntentEntry,
    state_to_json,
)
from atoms.chain.read import validate_chain as _validate_chain
from atoms.coordinator import lifecycle as _lifecycle
from atoms.coordinator.capture import PayloadSource
from atoms.coordinator.execute import _run_under_lease
from atoms.coordinator.lifecycle import (
    DestinationOverride,
    LifecycleState,
    RootOperationId,
    RootOperationInvalid,
    RootOperationMismatch,
    SourceSnapshotMoved,
)
from atoms.coordinator.recover import _registered_root, resolve
from atoms.coordinator.root import (
    _claimed_destination_lease as _root_claimed_destination_lease,
)
from atoms.coordinator.root import (
    _creation_lease,
    _lifecycle_view,
    _project_lease,
    _recovery_lease,
    _require_chain_publication,
    _writable_recovery_lease,
)
from atoms.core.compiler import compile_spec
from atoms.core.errors import (
    CapabilityUnavailable,
    PreconditionRefused,
    ProtocolError,
    SpecValidationError,
)
from atoms.core.fingerprint import ABSENT, PathState
from atoms.core.paths import require_rel_path
from atoms.core.recovery.model import (
    ObservedAbsent,
    ObservedDirectory,
    ObservedFile,
    ObservedSymlink,
)
from atoms.core.scratch import CHAIN_LEAF
from atoms.core.spec import TransactionSpec
from atoms.fs.audit import AuditedBackend
from atoms.fs.backend import Backend
from atoms.fs.lock import (
    acquire_existing_project_lock,
    close_all,
    establish_root,
)
from atoms.fs.observe import _UNSUPPORTED as _UNSUPPORTED_ERRNOS
from atoms.fs.observe import Observation
from atoms.fs.volume import StorageProfile
from atoms.store.connection import Store

__all__ = (
    "AbsentChain",
    "ChainDefect",
    "ChainInspection",
    "ChainView",
    "DefectKind",
    "DestinationOverride",
    "Entry",
    "LifecycleState",
    "MalformedChain",
    "NotAttemptedReason",
    "PathObserved",
    "PathReadResult",
    "ReadNotAttempted",
    "ReadUnestablished",
    "RootOperationId",
    "RootOperationInvalid",
    "RootOperationMismatch",
    "SourceSnapshotMoved",
    "TransactionOutcome",
    "UnestablishedReason",
    "WellFormedChain",
    "append_intent",
    "capture_states",
    "fork_root",
    "grant_read_serviceability",
    "inspect_chain",
    "inspect_chain_detached",
    "migrate_root_to_lifecycle_v3",
    "read_chain",
    "read_lifecycle_state",
    "read_path_state",
    "read_pending_fork_operation",
    "register_root",
    "replicate_root",
    "resume_fork_root",
    "run_transaction",
)

_ABSENT_PARENT = frozenset({errno.ENOENT, errno.ENOTDIR, errno.ELOOP})
_UNSTABLE_CHAIN_LEAF = frozenset({errno.ENOTDIR, errno.ELOOP, errno.EXDEV})
_FULFILLS_REFUSAL = {
    "missing": "fulfills names no entry of the validated chain",
    "non-ancestor": "fulfills names an entry that is not an ancestor",
    "non-intent": "fulfills names an entry that is not an intent",
}


@dataclass(frozen=True, slots=True)
class TransactionOutcome:
    txid: str
    outcome: ChainOutcome
    registration: str
    settlement: str
    final_states: tuple[tuple[str, PathState], ...]
    """The complete canonical final surface commit verification observed and
    matched on disk under the lease — every mutated path, not only the
    `registered_paths` subset the chain entry carries. Deliberately no
    default: a defaulted empty tuple would fabricate "no mutated paths" at
    any construction site that forgot it."""


@dataclass(frozen=True)
class ChainView:
    """One validated chain, projected for a consumer, holding no engine resource.

    `entries` is the core's linearized order: genesis first, one successor per entry,
    tip last. `genesis_digest` is `entries[0][0]` and `tip` is the validator's own tip,
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


class _OutsideVocabulary(PreconditionRefused):
    """Module-private: `_capture_path` observed an entry outside the closed
    path-state vocabulary. A subclass so existing callers keep catching
    `PreconditionRefused` unchanged while `read_path_state` can classify the
    refusal without message inspection (holdings read design §3)."""


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
                # The named path does not exist when its parent is a file or a symlink
                # either; the parent's own state is separately capturable by naming it.
                if caught.errno in _ABSENT_PARENT:
                    return ABSENT
                raise
            owned.append(parent_fd)
        observed = observation.observe(
            parent_fd, path.rpartition("/")[2], modeled=frozenset()
        )
        if type(observed) is ObservedAbsent:
            return ABSENT
        if type(observed) not in (ObservedFile, ObservedDirectory, ObservedSymlink):
            # The PathState vocabulary is closed by authority §6: an unrepresentable or
            # unreadable entry is refused, never coerced to ABSENT and never widened.
            raise _OutsideVocabulary(
                f"{path!r} was observed as {type(observed).__name__}, which is outside "
                "the closed path-state vocabulary"
            )
        return _cast(ObservedFile | ObservedDirectory | ObservedSymlink, observed).state
    finally:
        close_all(backend, reversed(owned))


def _require_capture_paths(value: object) -> tuple[str, ...]:
    """Design §9.2. Sortedness is `registered_surface`'s requirement, not capture's."""

    if type(value) is not tuple or any(type(path) is not str for path in value):
        raise PreconditionRefused("paths must be an exact tuple of exact strings")
    paths = _cast(tuple[str, ...], value)
    if len(paths) != len(set(paths)):
        # Two rows for one path invite a coherence question that has no good answer.
        raise PreconditionRefused("paths must be duplicate-free")
    try:
        for path in paths:
            require_rel_path("capture path", path)
    except SpecValidationError as caught:
        raise PreconditionRefused(str(caught)) from caught
    return paths


def _require_no_pending(entries: tuple[tuple[str, Entry], ...]) -> None:
    """Design §10: an unsettled chain is not a chain you may mutate.

    Post-recovery this is honest: a durable registration always had a durable record
    before it, so a registration still unsettled after resolution has no record left to
    settle it. Its evidence is gone, and no retry will bring it back.
    """

    pending = _pending_of(entries)
    if pending:
        raise PendingUnresolved(
            "the chain carries unsettled registrations: "
            + ", ".join(f"{txid} at {digest}" for txid, digest in pending)
        )


def _require_admissible_fulfills(
    entries: tuple[tuple[str, Entry], ...], fulfills: str | None
) -> None:
    """Design §11.2's two independent submission checks, before any record exists.

    The referent check is the core's own predicate at the index the registration is
    about to occupy, so "membership in the validated chain" and the inspection's
    "strictly below the referencing index" are the same question -- which is why a
    `fulfills` naming the current tip is accepted.

    The duplicate check cannot be that predicate: the inspection recognizes
    DUPLICATE_FULFILLMENT only at the second committed settlement, which at submission
    has not been appended, has not been decided, and may never be. So this is a
    deliberate over-refusal against a strictly earlier state: a spec it rejects would
    have condemned the chain only if the transaction had been appended AND committed.
    """

    if fulfills is None:
        return
    fault = _fulfills_fault(entries, len(entries), fulfills)
    if fault is not None:
        raise PreconditionRefused(f"{_FULFILLS_REFUSAL[fault]}: {fulfills}")
    if fulfills in _committed_fulfillments(entries):
        raise PreconditionRefused(
            f"an earlier committed registration already fulfills {fulfills}"
        )


def _inspect_under(
    backend: AuditedBackend,
    project_root_fd: int,
    store: Store,
    *,
    after_resolution: bool,
) -> ChainInspection:
    """Registered-mode structural inspection, under the held project lease.

    `_registered_root`'s errno handling reproduced with inspecting dispositions: it
    raises where this must classify, so it stays exactly as it is for the three
    mutators and `read_chain`. A record that contradicts the chain keeps raising --
    that is a claim about the metadata store, which detached mode by construction
    cannot see, so giving it a taxonomy row would fork the taxonomy (design §12).
    """

    try:
        chain_fd = backend.open_child_directory(project_root_fd, CHAIN_LEAF)
    except OSError as caught:
        if caught.errno == errno.ENOENT:
            if store.read_active() is not None:
                raise ChainStateInvalid(
                    "a live transaction record exists without its project chain"
                ) from caught
            return AbsentChain()
        if caught.errno in _UNSTABLE_CHAIN_LEAF:
            return MalformedChain(OCCUPIED_CHAIN_LEAF)
        raise
    try:
        scanned = _scan_chain_directory(backend, chain_fd)
    finally:
        backend.close_fd(chain_fd)
    if type(scanned) is ChainDefect:
        return MalformedChain(scanned)
    scan = _cast(ChainScan, scanned)
    if after_resolution and scan.staged is not None:
        # `resolve` adjudicates every survivor; one that outlives it is the existing
        # engine-internal impossibility, not a fifteenth defect (design §8).
        raise ChainStateInvalid("chain staging appeared after lease resolution")
    result = _inspect_scan(scan)
    if type(result) is AbsentChain and store.read_active() is not None:
        raise ChainStateInvalid(
            "a live transaction record exists without a chain genesis"
        )
    return result


def _inspect_detached(
    backend: AuditedBackend, project_root_fd: int
) -> ChainInspection:
    """The same table under strictly less evidence: no store to consult (design §7.2)."""

    try:
        chain_fd = backend.open_child_directory(project_root_fd, CHAIN_LEAF)
    except OSError as caught:
        if caught.errno == errno.ENOENT:
            return AbsentChain()
        if caught.errno in _UNSTABLE_CHAIN_LEAF:
            return MalformedChain(OCCUPIED_CHAIN_LEAF)
        raise
    try:
        scanned = _scan_chain_directory(backend, chain_fd)
    finally:
        backend.close_fd(chain_fd)
    if type(scanned) is ChainDefect:
        return MalformedChain(scanned)
    scan = _cast(ChainScan, scanned)
    result = _inspect_scan(scan)
    if type(result) is WellFormedChain:
        # Detached mode cannot adjudicate a survivor -- FINISH versus REMOVE is decided
        # by the active record's planned envelope, and there is no store -- so it
        # reports the registration evidence instead of finishing or removing it.
        return _staged_pending(scan, result)
    return result


def _register_completed_retry(
    backend: Backend,
    view: _lifecycle.CarrierView,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
    genesis_payload: bytes,
    registered_surface: tuple[str, ...],
) -> str:
    """A writable root: only its own completed register operation may retry."""
    operation = view.operation
    if operation is None or operation.kind != "register":
        raise PreconditionRefused(
            "this writable root was not created by register_root; forked or "
            "migrated roots are never register retries"
        )
    root_fd, root_path, _ = establish_root(backend, project_root, create=False)
    backend.close_fd(root_fd)
    metadata_fd, metadata_path, _ = establish_root(
        backend, metadata_root, create=False
    )
    backend.close_fd(metadata_fd)
    request = _lifecycle.OperationRequest.of(
        _lifecycle.register_request(
            root_path, metadata_path, storage, genesis_payload, registered_surface
        )
    )
    if operation.request_json != request.operation_json:
        raise RootOperationMismatch(
            "the project root is already registered with a different "
            "payload or surface"
        )
    if operation.genesis_digest is None:
        raise RootOperationInvalid(
            "a completed register operation carries no genesis digest"
        )
    return operation.genesis_digest


def _refuse_bare_genesis(
    backend: Backend,
    project_root: str,
    genesis_payload: bytes,
    registered_surface: tuple[str, ...],
) -> None:
    """A chain with no claim and no carrier is never a register retry.

    Checked before any claim, lock, metadata directory, or store is created,
    so the refusal leaves the arriving tree exactly as it was found.
    """
    inspected = inspect_chain_detached(backend, project_root)
    if type(inspected) is not WellFormedChain or not inspected.entries:
        return
    _digest, genesis = inspected.entries[0]
    if (
        type(genesis) is GenesisEntry
        and genesis.payload == genesis_payload
        and tuple(path for path, _ in genesis.baseline) == registered_surface
    ):
        raise PreconditionRefused(
            "matching genesis has no local initialization operation"
        )
    raise PreconditionRefused(
        "the project root is already registered with a different "
        "payload or surface"
    )


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

    view = _lifecycle_view(backend, project_root, metadata_root, storage)
    if view.state is LifecycleState.WRITABLE:
        return _register_completed_retry(
            backend,
            view,
            project_root,
            metadata_root,
            storage,
            genesis_payload,
            registered_surface,
        )
    if view.state is LifecycleState.BINDING_MISMATCHED:
        raise PreconditionRefused(
            "the root lifecycle binding is mismatched; a moved or copied root "
            "is never a register retry"
        )
    if view.state is LifecycleState.READ_ONLY_SERVICEABLE:
        raise PreconditionRefused(
            "a read-only-serviceable root is never a register retry"
        )
    if view.schema_version == 2:
        raise PreconditionRefused(
            "this root's metadata store is the pre-lifecycle version 2; "
            "migrate_root_to_lifecycle_v3 is its only transition"
        )
    if view.state is LifecycleState.READ_ONLY_UNSERVICEABLE:
        operation = view.operation
        if operation is None or operation.kind != "register":
            raise PreconditionRefused(
                "this read-only root was not created by an interrupted "
                "register_root; replicated or forked roots are never register "
                "retries"
            )
    else:
        # Metadata-less: an existing chain must carry this host's claim or it
        # is a bare copied genesis, refused before anything is created.
        if _root_claim_bytes(backend, project_root) is None:
            _refuse_bare_genesis(
                backend, project_root, genesis_payload, registered_surface
            )

    with _recovery_lease(backend, project_root, metadata_root, storage) as lease:
        chain_backend = _cast(AuditedBackend, lease._binding.backend)
        _require_chain_publication(lease._binding.evidence)
        binding = lease._binding
        request = _lifecycle.OperationRequest.of(
            _lifecycle.register_request(
                binding.project_root_path,
                binding.metadata_root_path,
                storage,
                genesis_payload,
                registered_surface,
            )
        )
        operation_id = _register_operation(lease, binding, request)
        claim = _lifecycle.encode_claim(operation_id, request)

        chain_fd = _bootstrap_chain(chain_backend, binding.project_root_fd)
        try:
            validated = _validate_chain(chain_backend, chain_fd)
            if validated.survivors:
                raise ProtocolError("chain staging appeared after lease resolution")
            _require_no_pending(validated.entries)
            if validated.entries:
                digest, genesis = validated.entries[0]
                if (
                    type(genesis) is not GenesisEntry
                    or genesis.payload != genesis_payload
                    or tuple(path for path, _ in genesis.baseline)
                    != registered_surface
                ):
                    raise RootOperationInvalid(
                        "the durable genesis contradicts this root's recorded "
                        "register operation"
                    )
            else:
                baseline = _capture_baseline(
                    chain_backend,
                    binding.project_root_fd,
                    registered_surface,
                )
                digest = _append_entry(
                    chain_backend,
                    chain_fd,
                    validated,
                    GenesisEntry(genesis_payload, baseline),
                )
        finally:
            chain_backend.close_fd(chain_fd)

        _prove_register_tree(lease, chain_backend, binding, digest)
        _lifecycle.remove_root_claim(
            chain_backend, binding.project_root_fd, claim
        )
        _lifecycle._complete_root_operation(lease._store, final_state="writable")
        return digest


def _root_claim_bytes(backend: Backend, project_root: str) -> bytes | None:
    root_fd, _, _ = establish_root(backend, project_root, create=False)
    try:
        return _lifecycle.read_root_claim(backend, root_fd)
    finally:
        backend.close_fd(root_fd)


def _register_operation(
    lease, binding, request: _lifecycle.OperationRequest
) -> str:
    """Adopt or record the register operation and its unserviceable stamp.

    The claim and the carrier must name one operation: an existing claim with
    different request bytes, or a claim/row identity disagreement, is
    `RootOperationMismatch` before any tree or lifecycle change.
    """
    chain_backend = binding.backend
    row = lease._store.read_root_operation()
    existing_claim = _lifecycle.read_root_claim(
        chain_backend, binding.project_root_fd
    )
    claimed_id: str | None = None
    if existing_claim is not None:
        decoded = _lifecycle.decode_claim(existing_claim)
        if decoded.request_json != request.operation_json:
            raise RootOperationMismatch(
                "the root claim names a different register operation"
            )
        claimed_id = decoded.operation_id

    if row is not None:
        if row.kind != "register" or row.request_json != request.operation_json:
            raise RootOperationMismatch(
                "the recorded operation is not this register request"
            )
        if claimed_id is not None and claimed_id != row.operation_id:
            raise RootOperationMismatch(
                "the root claim and the recorded operation disagree"
            )
        return row.operation_id

    operation_id = claimed_id or _lifecycle.mint_operation_id()
    if existing_claim is None:
        try:
            _lifecycle.create_root_claim(
                chain_backend,
                binding.project_root_fd,
                _lifecycle.encode_claim(operation_id, request),
            )
        except OSError as caught:
            if caught.errno != errno.EEXIST:
                raise
            # Lost the cross-carrier race at the claim: adopt an exact winner,
            # refuse any other. Two metadata roots cannot claim one root.
            raced = _lifecycle.read_root_claim(
                chain_backend, binding.project_root_fd
            )
            if raced is None:
                raise RootOperationMismatch(
                    "the root claim appeared and vanished during registration"
                ) from caught
            decoded = _lifecycle.decode_claim(raced)
            if decoded.request_json != request.operation_json:
                raise RootOperationMismatch(
                    "the root claim names a different register operation"
                ) from caught
            operation_id = decoded.operation_id
    machine_id = _lifecycle._read_machine_identity()
    with lease._store.transaction() as txn:
        txn.insert_root_operation(
            operation_id,
            "register",
            request.operation_json,
            request.operation_hash,
        )
        txn.insert_root_lifecycle(
            "read-only-unserviceable",
            machine_id,
            binding.project_root_path,
            "register",
        )
    return operation_id


def _prove_register_tree(lease, chain_backend, binding, digest: str) -> None:
    """Snapshot the whole registered tree and advance to tree-durable.

    On a retry whose proof is already stored, re-prove instead: the recorded
    genesis digest and snapshot must match the tree as it stands, because
    nothing may mutate a pre-grant root but its own operation.
    """
    snapshot = _lifecycle.tree_snapshot(
        chain_backend, binding.project_root_fd, digest
    )
    row = lease._store.read_root_operation()
    if row is None:
        raise ProtocolError("the register operation row vanished mid-command")
    if row.phase == "recorded":
        with lease._store.transaction() as txn:
            txn.set_root_operation_tree_proof(snapshot.operation_json, digest)
        return
    if row.genesis_digest != digest:
        raise RootOperationInvalid(
            "the recorded genesis digest does not name the durable genesis"
        )
    if row.destination_snapshot_json != snapshot.operation_json:
        raise RootOperationInvalid(
            "the recorded tree proof does not match the pre-grant tree"
        )


def read_lifecycle_state(
    backend: Backend,
    root: str,
    metadata_root: str,
    storage: StorageProfile,
) -> LifecycleState:
    """The closed five-value lifecycle union, validated while reading.

    Never creates or upgrades a root, metadata directory, lock, database,
    schema, row, or WAL (lifecycle design §7).
    """
    return _lifecycle_view(backend, root, metadata_root, storage).state


def append_intent(
    backend: Backend,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
    payload: bytes,
) -> str:
    with _writable_recovery_lease(
        backend, project_root, metadata_root, storage
    ) as lease:
        chain_backend = _cast(AuditedBackend, lease._binding.backend)
        _require_chain_publication(lease._binding.evidence)
        with _registered_root(lease) as (chain_fd, validated):
            _require_no_pending(validated.entries)
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
    with _writable_recovery_lease(
        backend, project_root, metadata_root, storage
    ) as lease:
        _require_chain_publication(lease._binding.evidence)
        with _registered_root(lease) as (chain_fd, validated):
            # An unsettled chain is refused whatever the new spec says, and the
            # `fulfills` gate runs before `admit` -- before any record exists, which is
            # the one point at which refusing is free.
            _require_no_pending(validated.entries)
            _require_admissible_fulfills(validated.entries, compiled.spec.fulfills)
            result = _run_under_lease(
                lease, chain_fd, validated, compiled, payloads
            )
    return TransactionOutcome(
        txid=result.txid,
        outcome=ChainOutcome.COMMITTED,
        registration=result.registration,
        settlement=result.settlement,
        final_states=tuple(
            (entry.path, entry.state) for entry in compiled.spec.final_surface
        ),
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
    view = _lifecycle_view(backend, project_root, metadata_root, storage)
    if view.state in (
        LifecycleState.READ_ONLY_SERVICEABLE,
        LifecycleState.READ_ONLY_UNSERVICEABLE,
    ):
        # The non-writable read: quiescent, coherent under the held lock,
        # never activating recovery (lifecycle design §7).
        if view.schema_version == 2:
            raise PreconditionRefused(
                "a pre-lifecycle version-2 store cannot be read coherently; "
                "migrate_root_to_lifecycle_v3 is its only transition"
            )
        operation = view.operation
        if operation is not None and operation.phase != "complete":
            raise PreconditionRefused(
                "this root carries an incomplete root operation"
            )
        if view.active_txid is not None:
            raise PreconditionRefused(
                "this root carries an active transaction record, which only "
                "its writable owner may resolve"
            )
        with _quiescent_read_only_root(
            backend, project_root, metadata_root
        ) as (_source, validated):
            if validated.tip is None:
                raise ProtocolError("a registered chain has no tip")
            return ChainView(
                genesis_digest=validated.entries[0][0],
                entries=validated.entries,
                tip=validated.tip,
            )
    if view.state is not LifecycleState.WRITABLE:
        if view.active_txid is not None:
            raise ChainStateInvalid(
                "a live transaction record exists on a root whose lifecycle "
                "carries no grant"
            )
        raise PreconditionRefused(
            f"root lifecycle state {view.state.value} does not admit a "
            "coherent chain read"
        )
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


def inspect_chain(
    backend: Backend,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
) -> ChainInspection:
    """Render a structural verdict about a registered root's chain (design §6.2).

    Lock, reclaim probe survivors, bind, open store, reclaim orphans, **inspect**;
    malformed returns without resolving, because recovery must never run over damage.
    Otherwise resolve and inspect again: `resolve` can append a registration or a
    settlement and discharges the staging survivor, so a verdict taken before it would
    be stale in exactly the way the recovery barrier exists to prevent.

    Reclamation sits above the split, so auditing a damaged root does discard
    metadata-side debris before the caller learns the chain is damaged. It touches no
    chain leaf. A consumer that must disturb nothing has `inspect_chain_detached`.
    """
    view = _lifecycle_view(backend, project_root, metadata_root, storage)
    if view.state in (
        LifecycleState.READ_ONLY_SERVICEABLE,
        LifecycleState.READ_ONLY_UNSERVICEABLE,
    ):
        # A non-writable root is inspected without resolution: any staging
        # survivor is reported as detached evidence, never adjudicated.
        lock = acquire_existing_project_lock(backend, metadata_root)
        assert lock is not None
        with lock:
            audited = AuditedBackend.detached(backend, project_root=project_root)
            root_fd = audited.open_root(project_root)
            try:
                return _inspect_detached(audited, root_fd)
            finally:
                audited.close_fd(root_fd)
    if view.state is LifecycleState.METADATA_LESS:
        if view.active_txid is not None:
            raise ChainStateInvalid(
                "a live transaction record exists on a root whose lifecycle "
                "carries no grant"
            )
        # A carrier-less root has nothing for registered mode to be coherent
        # against, and creating a carrier to answer a question is exactly
        # what a read must not do. The detached classification is the whole
        # honest answer: explicitly non-coherent, non-mutating, staging
        # reported as evidence — which is what keeps a cold copy's chain
        # evaluable (its pending honestly unresolved) without a grant.
        audited = AuditedBackend.detached(backend, project_root=project_root)
        root_fd = audited.open_root(project_root)
        try:
            return _inspect_detached(audited, root_fd)
        finally:
            audited.close_fd(root_fd)
    if view.state is not LifecycleState.WRITABLE:
        raise PreconditionRefused(
            f"root lifecycle state {view.state.value} does not admit a "
            "coherent chain inspection"
        )
    with _project_lease(backend, project_root, metadata_root, storage) as lease:
        chain_backend = _cast(AuditedBackend, lease._binding.backend)
        root_fd = lease._binding.project_root_fd
        first = _inspect_under(
            chain_backend, root_fd, lease._store, after_resolution=False
        )
        if type(first) is MalformedChain:
            return first
        resolve(lease._binding, lease._store)
        return _inspect_under(
            chain_backend, root_fd, lease._store, after_resolution=True
        )


def inspect_chain_detached(
    backend: Backend,
    project_root: str,
) -> ChainInspection:
    """Render the same verdict over a root this engine is not registered on.

    Two parameters, not four: an arriving root has no metadata root and no storage
    profile, and accepting either would be a parameter the caller could only fabricate.
    No lock, no volume binding, no store, no `resolve`, no `bootstrap_chain`.

    It gives content-name integrity, canonical decodability, and the linkage and
    entry-class relation over exactly the set it read -- but NOT that this set is the
    durable chain at any instant. Against a concurrently written root it can report a
    stale tip or a spurious defect. That is the price of having no metadata root to
    lock against, and it is why the supported caller is an import boundary whose
    subject is a copy no engine is live on.
    """
    audited = AuditedBackend.detached(backend, project_root=project_root)
    root_fd = audited.open_root(project_root)
    try:
        return _inspect_detached(audited, root_fd)
    finally:
        audited.close_fd(root_fd)


def capture_states(
    backend: Backend,
    root: str,
    paths: tuple[str, ...],
) -> tuple[tuple[str, PathState], ...]:
    """State exactly the named paths, in the caller's order, one pair each.

    No directory walking, no enumeration, no inference of siblings: what to enumerate
    is the consumer's projection under the consumer's grammar, and what a path *is* is
    the engine's. Sharing `_capture_path` with `register_root`'s baseline capture is
    what makes "no second summary model" a mechanism rather than a promise.

    One `Observation` across the whole batch, which is the coherence this command
    exists to provide: identity pinning is shared, so no inode observed early in the
    batch can be recycled onto a different entry later in it. A caller looping over a
    single-path command would get n incomparable observations. Serialization is the
    caller's; this takes no lock and asserts nothing about registration.
    """
    audited = AuditedBackend.detached(backend, project_root=root)
    root_fd = audited.open_root(root)
    try:
        with Observation(audited) as observation:
            return tuple(
                (path, _capture_path(audited, root_fd, path, observation))
                for path in _require_capture_paths(paths)
            )
    finally:
        audited.close_fd(root_fd)


# --- The lease-held single-path read (holdings read/evidence design §3–§4) ---


class NotAttemptedReason(enum.StrEnum):
    PATH_GRAMMAR = "path-grammar"
    ROOT_UNRESOLVABLE = "root-unresolvable"
    LIFECYCLE_STATE = "lifecycle-state"
    QUIESCENCE = "quiescence"


class UnestablishedReason(enum.StrEnum):
    IO_FAILURE = "io-failure"
    OUTSIDE_VOCABULARY = "outside-vocabulary"


@dataclass(frozen=True, slots=True)
class PathObserved:
    state: PathState


@dataclass(frozen=True, slots=True)
class ReadNotAttempted:
    reason: NotAttemptedReason
    lifecycle_state: LifecycleState | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ReadUnestablished:
    reason: UnestablishedReason
    detail: str = ""


PathReadResult = PathObserved | ReadNotAttempted | ReadUnestablished


def _observe_path(
    backend: AuditedBackend, root_fd: int, path: str
) -> PathObserved | ReadUnestablished:
    """The observation phase, under an already-held boundary.

    Routine failures translate; non-routine failures propagate whatever their
    position, under `translated_lookup`'s exact errno classification — the raw
    ancestor opens and descriptor cleanup in `_capture_path` sit outside its
    wrappers, so the classification is applied here as well (design §3).
    """
    try:
        with Observation(backend) as observation:
            observed = _capture_path(backend, root_fd, path, observation)
        return PathObserved(observed)
    except _OutsideVocabulary as caught:
        return ReadUnestablished(UnestablishedReason.OUTSIDE_VOCABULARY, str(caught))
    except PreconditionRefused as caught:
        # translated_lookup's namespace-contradiction arm: for a bare read,
        # concurrent raw-mutation evidence — established nothing.
        return ReadUnestablished(UnestablishedReason.IO_FAILURE, str(caught))
    except OSError as caught:
        if caught.errno in _UNSUPPORTED_ERRNOS:
            raise CapabilityUnavailable(
                f"the backend cannot supply the semantics needed while "
                f"reading {path!r}: {caught}"
            ) from caught
        if caught.errno == errno.EBADF:
            raise ProtocolError(
                f"a descriptor was already closed while reading {path!r}: {caught}"
            ) from caught
        return ReadUnestablished(UnestablishedReason.IO_FAILURE, str(caught))


def read_path_state(
    backend: Backend,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
    path: str,
) -> PathReadResult:
    """Observe exactly one named path under the held boundary (design §3).

    Never creates or upgrades a root, metadata directory, lock, database,
    schema, row, or WAL. The normative translation table in the design decides
    every outcome; the consumer's found/absent classification is the
    consumer's.
    """
    if type(path) is not str:
        raise ProtocolError("path must be an exact str")
    if "\x00" in path:
        raise ProtocolError("path contains a NUL byte")
    try:
        require_rel_path("path", path)
    except SpecValidationError as caught:
        return ReadNotAttempted(NotAttemptedReason.PATH_GRAMMAR, detail=str(caught))
    view = _lifecycle_view(backend, project_root, metadata_root, storage)
    if view.state in (
        LifecycleState.METADATA_LESS,
        LifecycleState.READ_ONLY_UNSERVICEABLE,
        LifecycleState.BINDING_MISMATCHED,
    ):
        if view.active_txid is not None:
            # read_chain's own alarm: a live transaction record on a root whose
            # lifecycle carries no grant is not a routine read outcome.
            raise ChainStateInvalid(
                "a live transaction record exists on a root whose lifecycle "
                "carries no grant"
            )
        return ReadNotAttempted(
            NotAttemptedReason.LIFECYCLE_STATE, lifecycle_state=view.state
        )
    if view.state is LifecycleState.READ_ONLY_SERVICEABLE:
        if view.schema_version == 2:
            return ReadNotAttempted(
                NotAttemptedReason.LIFECYCLE_STATE,
                lifecycle_state=view.state,
                detail="a pre-lifecycle version-2 store cannot be read coherently",
            )
        operation = view.operation
        if operation is not None and operation.phase != "complete":
            return ReadNotAttempted(
                NotAttemptedReason.QUIESCENCE,
                detail="this root carries an incomplete root operation",
            )
        if view.active_txid is not None:
            return ReadNotAttempted(
                NotAttemptedReason.QUIESCENCE,
                detail="this root carries an active transaction record, which "
                "only its writable owner may resolve",
            )
        try:
            with _quiescent_read_only_root(
                backend, project_root, metadata_root
            ) as (source, _validated):
                return _observe_path(
                    _cast(AuditedBackend, source.backend), source.root_fd, path
                )
        except PreconditionRefused as caught:
            return ReadNotAttempted(NotAttemptedReason.QUIESCENCE, detail=str(caught))
        except OSError as caught:
            if caught.errno in (errno.ENOENT, errno.ENOTDIR):
                return ReadNotAttempted(
                    NotAttemptedReason.ROOT_UNRESOLVABLE, detail=str(caught)
                )
            raise
    try:
        # The existing-only gated lease, not the create-capable one: a carrier
        # that vanished after classification must read root-unresolvable, never
        # be recreated and read against fresh (review P1). Classification said
        # writable, so a refusal here is a post-classification state change.
        with (
            _writable_recovery_lease(
                backend, project_root, metadata_root, storage
            ) as lease,
            _registered_root(lease) as (_chain_fd, _validated),
        ):
            chain_backend = _cast(AuditedBackend, lease._binding.backend)
            return _observe_path(
                chain_backend, lease._binding.project_root_fd, path
            )
    except PreconditionRefused as caught:
        return ReadNotAttempted(
            NotAttemptedReason.ROOT_UNRESOLVABLE, detail=str(caught)
        )
    except OSError as caught:
        if caught.errno in (errno.ENOENT, errno.ENOTDIR):
            return ReadNotAttempted(
                NotAttemptedReason.ROOT_UNRESOLVABLE, detail=str(caught)
            )
        raise


# --- Copy commands, serviceability grant, and migration (lifecycle design) ---


@dataclass(slots=True)
class _CopyPlan:
    """Everything one copy operation is, canonically: the identity IS the
    canonical request bytes, with the operation id riding beside them."""

    kind: str
    source_root: str
    source_metadata_root: str
    dest_root: str
    dest_metadata_root: str
    storage: StorageProfile
    source_head: str | None
    genesis_payload: bytes | None
    surface_paths: tuple[str, ...] | None
    overrides: tuple[DestinationOverride, ...] | None
    operation_id: str | None = None

    def request_obj(self) -> dict[str, object]:
        if self.kind == "replicate":
            assert self.source_head is not None
            return _lifecycle.replicate_request(
                self.source_root,
                self.source_metadata_root,
                self.dest_root,
                self.dest_metadata_root,
                self.storage,
                self.source_head,
            )
        assert self.genesis_payload is not None
        assert self.surface_paths is not None and self.overrides is not None
        assert self.source_head is not None
        return _lifecycle.fork_request(
            self.source_root,
            self.source_metadata_root,
            self.dest_root,
            self.dest_metadata_root,
            self.storage,
            self.source_head,
            self.genesis_payload,
            self.surface_paths,
            self.overrides,
        )

    def request(self) -> _lifecycle.OperationRequest:
        return _lifecycle.OperationRequest.of(self.request_obj())

    def claim(self) -> bytes:
        assert self.operation_id is not None
        return _lifecycle.encode_claim(self.operation_id, self.request())

    def final_state(self) -> str:
        return "writable" if self.kind == "fork" else "read-only-unserviceable"


class _NeedsSource(Exception):
    """Internal: destination evidence alone cannot finish this phase."""


@dataclass(frozen=True, slots=True)
class _CopySource:
    backend: Backend
    root_fd: int
    tip: str
    root_path: str


@contextlib.contextmanager
def _quiescent_read_only_root(backend, root: str, metadata_root: str):
    """A held existing metadata lock plus a detached-audited validated chain.

    The non-writable read path (lifecycle design §7): no probe, reclamation,
    recovery, writable open, or sidecar write. Any state that would need
    recovery refuses instead.
    """

    lock = acquire_existing_project_lock(backend, metadata_root)
    assert lock is not None
    with lock:
        audited = AuditedBackend.detached(backend, project_root=root)
        root_fd, root_path, _ = establish_root(audited, root, create=False)
        try:
            try:
                chain_fd = audited.open_child_directory(root_fd, CHAIN_LEAF)
            except OSError as caught:
                raise PreconditionRefused(
                    "this root has no registered chain to read"
                ) from caught
            try:
                validated = _validate_chain(audited, chain_fd)
            finally:
                audited.close_fd(chain_fd)
            if validated.survivors:
                raise PreconditionRefused(
                    "a chain staging survivor requires recovery, which a "
                    "read-only entry never performs"
                )
            if validated.tip is None:
                raise PreconditionRefused("this root's chain has no entries")
            yield _CopySource(
                backend=audited,
                root_fd=root_fd,
                tip=validated.tip,
                root_path=root_path,
            ), validated
        finally:
            audited.close_fd(root_fd)


@contextlib.contextmanager
def _copy_source(backend, source_root: str, source_metadata_root: str, storage):
    """The copy-source lease: coherent for a writable source, quiescent for a
    read-only one, refused otherwise. A missing source propagates OSError."""
    source_fd, _, _ = establish_root(backend, source_root, create=False)
    backend.close_fd(source_fd)
    view = _lifecycle_view(backend, source_root, source_metadata_root, storage)
    if view.state is LifecycleState.WRITABLE:
        with _writable_recovery_lease(
            backend, source_root, source_metadata_root, storage
        ) as lease:
            chain_backend = _cast(AuditedBackend, lease._binding.backend)
            with _registered_root(lease) as (_chain_fd, validated):
                if validated.tip is None:
                    raise PreconditionRefused(
                        "the source root's chain has no entries"
                    )
                yield _CopySource(
                    backend=chain_backend,
                    root_fd=lease._binding.project_root_fd,
                    tip=validated.tip,
                    root_path=lease._binding.project_root_path,
                )
        return
    if view.state in (
        LifecycleState.READ_ONLY_SERVICEABLE,
        LifecycleState.READ_ONLY_UNSERVICEABLE,
    ):
        if view.schema_version == 2:
            raise PreconditionRefused(
                "a pre-lifecycle version-2 source cannot be read coherently; "
                "migrate_root_to_lifecycle_v3 is its only transition"
            )
        operation = view.operation
        if operation is not None and operation.phase != "complete":
            raise PreconditionRefused(
                "the source carries an incomplete root operation"
            )
        with _quiescent_read_only_root(
            backend, source_root, source_metadata_root
        ) as (source, _validated):
            yield source
        return
    raise PreconditionRefused(
        f"root lifecycle state {view.state.value} does not admit this root "
        "as a copy source"
    )


def _copy_declared_paths(
    entries: Mapping[str, object], plan: _CopyPlan
) -> frozenset[str]:
    declared = {
        path
        for path in entries
        if path.partition("/")[0] != CHAIN_LEAF
    }
    if plan.overrides:
        declared.update(override.path for override in plan.overrides)
    return frozenset(declared)


def _verify_copy_row(plan: _CopyPlan, row) -> None:
    if row.kind != plan.kind or row.request_json != plan.request().operation_json:
        raise RootOperationMismatch(
            "the recorded destination operation is not this request"
        )
    if plan.operation_id is None:
        plan.operation_id = row.operation_id
    elif row.operation_id != plan.operation_id:
        raise RootOperationMismatch(
            "the root claim and the recorded operation disagree"
        )


def _apply_overrides(plan: _CopyPlan, dest_backend, dest_root_fd: int) -> None:
    """Retained overrides, applied before baseline capture — idempotently on
    a retry, byte-for-byte or not at all."""
    assert plan.overrides is not None
    for override in plan.overrides:
        parent, _, leaf = override.path.rpartition("/")
        parents = parent.split("/") if parent else []
        opened: list[int] = []
        parent_fd = dest_root_fd
        try:
            for component in parents:
                parent_fd = dest_backend.open_child_directory(
                    parent_fd, component
                )
                opened.append(parent_fd)
            try:
                info = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                info = None
            if info is not None:
                if not _stat_module.S_ISREG(info.st_mode):
                    raise PreconditionRefused(
                        f"override target {override.path!r} is not absent or "
                        "a regular file"
                    )
                fd = dest_backend.open_regular_nofollow(parent_fd, leaf)
                try:
                    existing = _read_all_fd(fd)
                finally:
                    dest_backend.close_fd(fd)
                if (
                    existing == override.payload
                    and _stat_module.S_IMODE(info.st_mode) == override.mode
                ):
                    continue
                dest_backend.unlink_child(parent_fd, leaf)
            fd = dest_backend.create_exclusive(parent_fd, leaf, override.mode)
            try:
                offset = 0
                while offset < len(override.payload):
                    offset += dest_backend.write(fd, override.payload[offset:])
                dest_backend.set_mode(fd, override.mode)
                dest_backend.flush_file(fd)
            finally:
                dest_backend.close_fd(fd)
            dest_backend.flush_directory(parent_fd)
        finally:
            close_all(dest_backend, reversed(opened))


def _read_all_fd(fd: int) -> bytes:
    chunks = []
    while True:
        chunk = os.read(fd, 1 << 16)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _finish_fork_construction(
    plan: _CopyPlan, dest_backend, dest_root_fd: int
) -> str:
    """Overrides, baseline over the destination surface, then the new chain
    with exactly the supplied genesis. Returns the genesis digest."""
    assert plan.genesis_payload is not None and plan.surface_paths is not None
    chain_fd = _bootstrap_chain(dest_backend, dest_root_fd)
    try:
        validated = _validate_chain(dest_backend, chain_fd)
        if validated.survivors:
            raise RootOperationInvalid(
                "the claimed destination chain carries a staging survivor"
            )
        if validated.entries:
            digest, genesis = validated.entries[0]
            if (
                type(genesis) is not GenesisEntry
                or genesis.payload != plan.genesis_payload
                or len(validated.entries) != 1
            ):
                raise RootOperationInvalid(
                    "the claimed destination chain contradicts the retained "
                    "fork operation"
                )
            return digest
        _apply_overrides(plan, dest_backend, dest_root_fd)
        baseline = _capture_baseline(
            _cast(AuditedBackend, dest_backend), dest_root_fd, plan.surface_paths
        )
        return _append_entry(
            dest_backend,
            chain_fd,
            validated,
            GenesisEntry(plan.genesis_payload, baseline),
        )
    finally:
        dest_backend.close_fd(chain_fd)


def _prove_destination(
    plan: _CopyPlan,
    dest_backend: Backend,
    dest_root_fd: int,
    source_entries: Mapping[str, object],
    genesis_digest: str | None,
) -> _lifecycle.OperationRequest:
    """The destination snapshot, with its path set checked against exactly
    what this operation may have written: extras refuse."""
    chain_head = genesis_digest if plan.kind == "fork" else plan.source_head
    assert chain_head is not None
    snapshot = _lifecycle.tree_snapshot(dest_backend, dest_root_fd, chain_head)
    _head, dest_entries = _lifecycle.parse_snapshot(snapshot.operation_json)
    expected = {
        path
        for path in source_entries
        if plan.kind == "replicate" or path.partition("/")[0] != CHAIN_LEAF
    }
    if plan.kind == "fork":
        assert plan.overrides is not None and genesis_digest is not None
        expected.update(override.path for override in plan.overrides)
        expected.add(CHAIN_LEAF)
        expected.add(f"{CHAIN_LEAF}/{genesis_digest}")
    extras = set(dest_entries) - expected
    missing = expected - set(dest_entries)
    if extras or missing:
        raise RootOperationInvalid(
            "the destination tree does not match this operation: extra "
            f"{sorted(extras)}, missing {sorted(missing)}"
        )
    return snapshot


def _advance_copy(plan: _CopyPlan, source: _CopySource | None, lease) -> None:
    """Run the remaining pipeline from the durable phase, raising
    `_NeedsSource` when destination evidence cannot carry a step alone."""
    store = lease._store
    binding = lease._binding
    dest_backend = binding.backend
    dest_root_fd = binding.project_root_fd

    row = store.read_root_operation()
    if row is None:
        if plan.operation_id is None:
            raise ProtocolError("a stamp requires the claimed operation id")
        _lifecycle._stamp_copy_destination(
            store,
            plan.operation_id,
            plan.kind,
            plan.request(),
            _lifecycle._read_machine_identity(),
            binding.project_root_path,
        )
        row = store.read_root_operation()
        assert row is not None
    else:
        _verify_copy_row(plan, row)

    if row.phase == "complete":
        return
    if row.phase == "recorded":
        if source is None:
            raise _NeedsSource
        snapshot = _lifecycle.tree_snapshot(
            source.backend,
            source.root_fd,
            plan.source_head or source.tip,
            exclude_chain=plan.kind == "fork",
        )
        _lifecycle._store_source_snapshot(store, snapshot)
        row = store.read_root_operation()
        assert row is not None

    if row.phase == "source-snapshot-durable":
        assert row.source_snapshot_json is not None
        _head, entries = _lifecycle.parse_snapshot(row.source_snapshot_json)
        declared = _copy_declared_paths(entries, plan)
        dest_backend.set_declared_paths(declared)
        try:
            missing = _lifecycle._copy_tree(
                source.backend if source is not None else None,
                source.root_fd if source is not None else None,
                dest_backend,
                dest_root_fd,
                entries,
            )
            if missing:
                raise _NeedsSource
            genesis_digest = (
                _finish_fork_construction(plan, dest_backend, dest_root_fd)
                if plan.kind == "fork"
                else None
            )
            snapshot = _prove_destination(
                plan, dest_backend, dest_root_fd, entries, genesis_digest
            )
        finally:
            dest_backend.clear_declared_paths()
        dest_backend.flush_directory(dest_root_fd)
        _lifecycle._store_tree_proof(store, snapshot, genesis_digest)
        row = store.read_root_operation()
        assert row is not None

    if row.phase == "tree-durable":
        assert row.source_snapshot_json is not None
        _head, entries = _lifecycle.parse_snapshot(row.source_snapshot_json)
        assert row.destination_snapshot_json is not None
        reproved = _prove_destination(
            plan, dest_backend, dest_root_fd, entries, row.genesis_digest
        )
        if reproved.operation_json != row.destination_snapshot_json:
            raise RootOperationInvalid(
                "the pre-grant destination tree contradicts its stored proof"
            )
        if plan.operation_id is None:
            plan.operation_id = row.operation_id
        _lifecycle.remove_root_claim(
            dest_backend, dest_root_fd, plan.claim()
        )
        _lifecycle._complete_root_operation(
            store, final_state=plan.final_state()
        )


def _preflight_claim(backend: Backend, dest_root: str) -> _lifecycle.RootClaim | None:
    """The published destination's claim, if the destination exists at all."""
    try:
        root_fd, _, _ = establish_root(backend, dest_root, create=False)
    except OSError as caught:
        if caught.errno in (errno.ENOENT, errno.ENOTDIR):
            return None
        raise
    try:
        payload = _lifecycle.read_root_claim(backend, root_fd)
    finally:
        backend.close_fd(root_fd)
    if payload is None:
        return None
    return _lifecycle.decode_claim(payload)


def _adopt_claim(plan: _CopyPlan, claim: _lifecycle.RootClaim) -> None:
    """An existing claim is resumable only for the exact operation request;
    replicate adopts the head its first invocation captured."""
    import json as _json

    recorded = _json.loads(claim.request_json)
    if plan.kind == "replicate" and plan.source_head is None:
        head = recorded.get("source_head")
        _lifecycle.require_source_head(head)
        # Adopt the head the first invocation captured, then compare the
        # whole request: a later observed head never redefines the operation.
        plan.source_head = head
    if plan.request_obj() == recorded:
        plan.operation_id = claim.operation_id
        return
    raise RootOperationMismatch(
        "the destination claim names a different operation request"
    )


def _dest_metadata_carrier_exists(backend: Backend, dest_metadata_root: str) -> bool:
    try:
        fd, _, _ = establish_root(backend, dest_metadata_root, create=False)
    except OSError as caught:
        if caught.errno in (errno.ENOENT, errno.ENOTDIR):
            return False
        raise
    try:
        return os.path.exists(
            os.path.join(f"/proc/self/fd/{fd}", "atoms.db")
        )
    finally:
        backend.close_fd(fd)


@contextlib.contextmanager
def _claimed_destination_lease(backend: Backend, plan: _CopyPlan):
    """The destination's nonblocking lock plus its bound store lease."""
    with _root_claimed_destination_lease(
        backend, plan.dest_root, plan.dest_metadata_root, plan.storage
    ) as lease:
        yield lease


def _finish_with_source(backend: Backend, plan: _CopyPlan) -> None:
    """The canonical two-lock order: source blocking, then destination tried;
    the phase is re-read after the destination comes back."""
    with _copy_source(
        backend, plan.source_root, plan.source_metadata_root, plan.storage
    ) as source:
        if plan.source_head is None:
            plan.source_head = source.tip
        elif source.tip != plan.source_head:
            raise SourceSnapshotMoved(
                f"the source head moved: expected {plan.source_head}, "
                f"observed {source.tip}"
            )
        with _claimed_destination_lease(backend, plan) as lease:
            row = _lifecycle._reread_phase_after_reacquisition(lease._store)
            if row is not None:
                _verify_copy_row(plan, row)
            _advance_copy(plan, source, lease)


def _copy_root(backend: Backend, plan: _CopyPlan) -> RootOperationId:
    claim = _preflight_claim(backend, plan.dest_root)
    if claim is not None:
        _adopt_claim(plan, claim)
        return _resume_copy(backend, plan)
    try:
        dest_fd, _, _ = establish_root(backend, plan.dest_root, create=False)
    except OSError as caught:
        if caught.errno in (errno.ENOENT, errno.ENOTDIR):
            return _fresh_copy(backend, plan)
        raise
    backend.close_fd(dest_fd)
    # Occupied, claim-free: resumable only through its own matching carrier.
    if not _dest_metadata_carrier_exists(backend, plan.dest_metadata_root):
        raise PreconditionRefused(
            f"the copy destination {plan.dest_root!r} is occupied without a "
            "root claim; no-clobber refuses it"
        )
    return _resume_copy(backend, plan)


def _resume_copy(backend: Backend, plan: _CopyPlan) -> RootOperationId:
    # First: what can the destination alone prove?
    needs_source = False
    with _claimed_destination_lease(backend, plan) as lease:
        row = lease._store.read_root_operation()
        if row is not None:
            if plan.kind == "replicate" and plan.source_head is None:
                # Adopt the retained head before comparing the full request.
                import json as _json

                recorded = _json.loads(row.request_json)
                head = recorded.get("source_head")
                _lifecycle.require_source_head(head)
                plan.source_head = head
            _verify_copy_row(plan, row)
        if row is None and plan.operation_id is None:
            raise PreconditionRefused(
                f"the copy destination {plan.dest_root!r} carries neither a "
                "claim nor a recorded operation; no-clobber refuses it"
            )
        try:
            _advance_copy(plan, None, lease)
        except _NeedsSource:
            needs_source = True
    if needs_source:
        _finish_with_source(backend, plan)
    assert plan.operation_id is not None
    return RootOperationId(plan.operation_id)


def _fresh_copy(backend: Backend, plan: _CopyPlan) -> RootOperationId:
    with _copy_source(
        backend, plan.source_root, plan.source_metadata_root, plan.storage
    ) as source:
        if plan.kind == "fork":
            assert plan.source_head is not None
            if source.tip != plan.source_head:
                raise SourceSnapshotMoved(
                    f"the source head moved: expected {plan.source_head}, "
                    f"observed {source.tip}"
                )
        else:
            plan.source_head = source.tip
        plan.operation_id = str(_lifecycle.mint_operation_id())
        dest_parent, dest_leaf = os.path.split(plan.dest_root)
        try:
            _lifecycle._publish_claimed_destination(
                backend, dest_parent, dest_leaf, plan.claim(), plan.operation_id
            )
        except OSError as caught:
            if caught.errno != errno.EEXIST:
                raise
            raced = _preflight_claim(backend, plan.dest_root)
            if raced is None:
                raise PreconditionRefused(
                    f"the copy destination {plan.dest_root!r} appeared without "
                    "a root claim; no-clobber refuses it"
                ) from caught
            plan.operation_id = None
            _adopt_claim(plan, raced)
        with _claimed_destination_lease(backend, plan) as lease:
            _advance_copy(plan, source, lease)
    assert plan.operation_id is not None
    return RootOperationId(plan.operation_id)


def replicate_root(
    backend: Backend,
    source_root: str,
    source_metadata_root: str,
    dest_root: str,
    dest_metadata_root: str,
    storage: StorageProfile,
) -> RootOperationId:
    plan = _plan_copy(
        backend,
        "replicate",
        source_root,
        source_metadata_root,
        dest_root,
        dest_metadata_root,
        storage,
        source_head=None,
        genesis_payload=None,
        surface_paths=None,
        dest_overrides=None,
    )
    return _copy_root(backend, plan)


def fork_root(
    backend: Backend,
    source_root: str,
    source_metadata_root: str,
    dest_root: str,
    dest_metadata_root: str,
    storage: StorageProfile,
    *,
    expected_source_head: str,
    genesis_payload: bytes,
    surface_paths: tuple[str, ...],
    dest_overrides: tuple[DestinationOverride, ...],
) -> RootOperationId:
    if type(genesis_payload) is not bytes:
        raise ProtocolError("genesis_payload must be exact bytes")
    _lifecycle.require_source_head(expected_source_head)
    surface_paths = _validate_registered_surface(surface_paths)
    dest_overrides = _lifecycle.require_overrides(dest_overrides)
    plan = _plan_copy(
        backend,
        "fork",
        source_root,
        source_metadata_root,
        dest_root,
        dest_metadata_root,
        storage,
        source_head=expected_source_head,
        genesis_payload=genesis_payload,
        surface_paths=surface_paths,
        dest_overrides=dest_overrides,
    )
    return _copy_root(backend, plan)


def _plan_copy(
    backend: Backend,
    kind: str,
    source_root: str,
    source_metadata_root: str,
    dest_root: str,
    dest_metadata_root: str,
    storage: StorageProfile,
    *,
    source_head: str | None,
    genesis_payload: bytes | None,
    surface_paths: tuple[str, ...] | None,
    dest_overrides: tuple[DestinationOverride, ...] | None,
) -> _CopyPlan:
    # The source spellings canonicalize without requiring existence: a
    # tree-durable or complete retry never opens the source, and a moved or
    # missing source must not block the suffix destination evidence can
    # finish (design §9). The paths that DO need the source raise their own
    # OSError when they open it.
    source_canonical, _ = _lifecycle.canonical_absent_ok(backend, source_root)
    source_metadata_canonical, _ = _lifecycle.canonical_absent_ok(
        backend, source_metadata_root
    )
    dest_canonical, _exists = _lifecycle.canonical_absent_ok(backend, dest_root)
    dest_metadata_canonical, _ = _lifecycle.canonical_absent_ok(
        backend, dest_metadata_root
    )
    _lifecycle.require_nonoverlapping(
        {
            "source_root": source_canonical,
            "source_metadata_root": source_metadata_canonical,
            "dest_root": dest_canonical,
            "dest_metadata_root": dest_metadata_canonical,
        }
    )
    return _CopyPlan(
        kind=kind,
        source_root=source_canonical,
        source_metadata_root=source_metadata_canonical,
        dest_root=dest_canonical,
        dest_metadata_root=dest_metadata_canonical,
        storage=storage,
        source_head=source_head,
        genesis_payload=genesis_payload,
        surface_paths=surface_paths,
        overrides=dest_overrides,
    )


def read_pending_fork_operation(
    backend: Backend,
    dest_root: str,
    dest_metadata_root: str,
    storage: StorageProfile,
) -> RootOperationId | None:
    """The narrow pending seam: a caller resumes before minting new child
    bytes. No claim or carrier is None; only a fork's own evidence answers."""
    import json as _json

    dest_canonical, dest_exists = _lifecycle.canonical_absent_ok(
        backend, dest_root
    )
    dest_metadata_canonical, _ = _lifecycle.canonical_absent_ok(
        backend, dest_metadata_root
    )
    claim: _lifecycle.RootClaim | None = None
    if dest_exists:
        claim = _preflight_claim(backend, dest_canonical)
    if claim is not None:
        recorded = _json.loads(claim.request_json)
        if recorded.get("kind") != "fork":
            raise PreconditionRefused(
                "the destination retains a non-fork operation"
            )
        if (
            recorded.get("dest_root") != dest_canonical
            or recorded.get("dest_metadata_root") != dest_metadata_canonical
        ):
            raise RootOperationMismatch(
                "the destination claim names a different destination"
            )
    view = _lifecycle_view(
        backend, dest_root, dest_metadata_root, storage
    ) if dest_exists else None
    if view is None or view.state is LifecycleState.METADATA_LESS:
        if view is not None and view.schema_version == 2:
            raise PreconditionRefused(
                "the destination carries a pre-lifecycle version-2 store"
            )
        return None if claim is None else RootOperationId(claim.operation_id)
    if view.state is LifecycleState.BINDING_MISMATCHED:
        raise PreconditionRefused("destination lifecycle binding mismatched")
    if view.schema_version == 2:
        raise PreconditionRefused(
            "the destination carries a pre-lifecycle version-2 store"
        )
    operation = view.operation
    if operation is None:
        if claim is not None:
            raise RootOperationMismatch(
                "the destination claim has no recorded operation"
            )
        return None
    if operation.kind != "fork":
        raise PreconditionRefused(
            "the destination retains a non-fork operation"
        )
    if claim is not None and claim.operation_id != operation.operation_id:
        raise RootOperationMismatch(
            "the root claim and the recorded operation disagree"
        )
    if operation.phase == "complete":
        if claim is not None:
            raise RootOperationInvalid(
                "a root claim survived the operation it names"
            )
        return None
    return RootOperationId(operation.operation_id)


def _plan_from_fork_request(
    recorded: dict[str, object], storage: StorageProfile
) -> _CopyPlan:
    import base64 as _base64

    overrides = tuple(
        DestinationOverride(
            path=str(entry["path"]),
            payload=_base64.b64decode(str(entry["payload_b64"])),
            mode=int(entry["mode"]),  # type: ignore[arg-type]
        )
        for entry in _cast(list[dict[str, object]], recorded["dest_overrides"])
    )
    return _CopyPlan(
        kind="fork",
        source_root=str(recorded["source_root"]),
        source_metadata_root=str(recorded["source_metadata_root"]),
        dest_root=str(recorded["dest_root"]),
        dest_metadata_root=str(recorded["dest_metadata_root"]),
        storage=storage,
        source_head=str(recorded["expected_source_head"]),
        genesis_payload=_base64.b64decode(str(recorded["genesis_payload_b64"])),
        surface_paths=tuple(
            str(path) for path in _cast(list[object], recorded["surface_paths"])
        ),
        overrides=overrides,
    )


def resume_fork_root(
    backend: Backend,
    dest_root: str,
    dest_metadata_root: str,
    storage: StorageProfile,
    operation_id: RootOperationId,
) -> RootOperationId:
    """Resume the retained fork by identity: the caller supplies no child
    bytes — the claim before the stamp or the row after it carries them."""
    import json as _json

    if type(operation_id) is not str:
        raise ProtocolError("operation_id must be an exact str")
    pending = read_pending_fork_operation(
        backend, dest_root, dest_metadata_root, storage
    )
    if pending is None or pending != operation_id:
        raise RootOperationMismatch(
            "no pending fork operation with this identity"
        )
    claim = _preflight_claim(backend, dest_root)
    if claim is not None:
        recorded = _json.loads(claim.request_json)
    else:
        view = _lifecycle_view(backend, dest_root, dest_metadata_root, storage)
        operation = view.operation
        assert operation is not None
        recorded = _json.loads(operation.request_json)
    if recorded.get("storage_profile") != {"profile_id": storage.profile_id}:
        raise RootOperationMismatch(
            "the retained operation names a different storage profile"
        )
    plan = _plan_from_fork_request(recorded, storage)
    plan.operation_id = str(operation_id)
    return _resume_copy(backend, plan)


def _require_grantable_tree(backend: Backend, root: str) -> None:
    """Structural quiescence for read admission: no creation residue, no
    staging survivor, and a well-formed registered chain."""
    root_fd, _, _ = establish_root(backend, root, create=False)
    try:
        claim = _lifecycle.read_root_claim(backend, root_fd)
        if claim is not None:
            raise PreconditionRefused(
                "the root carries a root-claim residue of an incomplete "
                "creation and cannot be made serviceable"
            )
        try:
            os.stat(
                f"{CHAIN_LEAF}/.#~stage",
                dir_fd=root_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise PreconditionRefused(
                "the root carries a chain staging survivor and cannot be "
                "made serviceable"
            )
    finally:
        backend.close_fd(root_fd)
    inspected = inspect_chain_detached(backend, root)
    if type(inspected) is not WellFormedChain or not inspected.entries:
        raise PreconditionRefused(
            "the root's chain is not a well-formed registered chain"
        )


def grant_read_serviceability(
    backend: Backend,
    root: str,
    metadata_root: str,
    storage: StorageProfile,
) -> None:
    """Structural read admission (lifecycle design §12): no verdict channel,
    no attestation, no writable grant, and no write when already granted."""
    root_fd, _root_canonical, _ = establish_root(backend, root, create=False)
    backend.close_fd(root_fd)
    view = _lifecycle_view(backend, root, metadata_root, storage)
    if view.state is LifecycleState.WRITABLE:
        raise PreconditionRefused(
            "a writable root does not take a read-serviceability grant"
        )
    if view.state is LifecycleState.BINDING_MISMATCHED:
        raise PreconditionRefused(
            "the root lifecycle binding is mismatched; the grant never "
            "overwrites a mismatch"
        )
    if view.schema_version == 2:
        raise PreconditionRefused(
            "this root's metadata store is the pre-lifecycle version 2; "
            "migrate_root_to_lifecycle_v3 or discard the carrier before "
            "cold admission"
        )
    _require_grantable_tree(backend, root)
    if view.state is LifecycleState.READ_ONLY_SERVICEABLE:
        # Already granted: the same checks re-ran; nothing is written.
        return
    if view.state is LifecycleState.READ_ONLY_UNSERVICEABLE:
        operation = view.operation
        if operation is None or operation.phase != "complete":
            raise PreconditionRefused(
                "the root carries an incomplete root operation and cannot "
                "be made serviceable"
            )
        lock = acquire_existing_project_lock(backend, metadata_root)
        assert lock is not None
        with lock:
            _lifecycle._transition_serviceability(lock.metadata_root_path)
        return

    # Metadata-less: cold admission creates fresh matching bookkeeping.
    machine_id = _lifecycle._read_machine_identity()
    with _creation_lease(backend, root, metadata_root, storage) as lease, lease._store.transaction() as txn:
        txn.insert_root_lifecycle(
            "read-only-serviceable",
            machine_id,
            lease._binding.project_root_path,
            "read-serviceability",
        )


def migrate_root_to_lifecycle_v3(
    backend: Backend,
    root: str,
    metadata_root: str,
    storage: StorageProfile,
) -> None:
    """The version-exact operator act: invoking it is the attestation that
    this host is the pre-lifecycle minting host (lifecycle design §13)."""
    root_fd, root_canonical, _ = establish_root(backend, root, create=False)
    backend.close_fd(root_fd)
    view = _lifecycle_view(backend, root, metadata_root, storage)
    if view.state is LifecycleState.METADATA_LESS:
        raise PreconditionRefused(
            "a metadata-less root has no version-2 store to migrate"
        )
    if view.state is LifecycleState.BINDING_MISMATCHED:
        raise PreconditionRefused(
            "the root lifecycle binding is mismatched; migration never "
            "overwrites a mismatch"
        )
    if view.schema_version == 3:
        lifecycle_row = view.lifecycle
        if (
            lifecycle_row is not None
            and lifecycle_row.origin == "migration-v2"
            and view.state is LifecycleState.WRITABLE
        ):
            return  # the no-write exact retry of the matching migration
        raise PreconditionRefused(
            "this store already carries the version-3 lifecycle; migration "
            "accepts only the exact pre-lifecycle version 2"
        )
    inspected = inspect_chain_detached(backend, root)
    if type(inspected) is not WellFormedChain or not inspected.entries:
        raise PreconditionRefused(
            "migration requires a well-formed registered chain"
        )
    if inspected.pending and any(
        digest not in {name for name, _entry in inspected.entries}
        for _txid, digest in inspected.pending
    ):
        raise PreconditionRefused(
            "migration refuses a chain staging survivor"
        )
    machine_id = _lifecycle._read_machine_identity()
    lock = acquire_existing_project_lock(backend, metadata_root)
    assert lock is not None
    with lock:
        _lifecycle._migrate_v2_store(
            lock.metadata_root_path, machine_id, root_canonical
        )
