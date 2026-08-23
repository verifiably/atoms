"""Public commands that serialize project-chain changes under the recovery lease."""

from __future__ import annotations

import errno
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
    _lifecycle_view,
    _project_lease,
    _recovery_lease,
    _require_chain_publication,
    _writable_recovery_lease,
)
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
from atoms.core.scratch import CHAIN_LEAF
from atoms.core.spec import TransactionSpec
from atoms.fs.audit import AuditedBackend
from atoms.fs.backend import Backend
from atoms.fs.lock import close_all, establish_root
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
    "RootOperationId",
    "RootOperationInvalid",
    "RootOperationMismatch",
    "SourceSnapshotMoved",
    "TransactionOutcome",
    "WellFormedChain",
    "append_intent",
    "capture_states",
    "inspect_chain",
    "inspect_chain_detached",
    "read_chain",
    "read_lifecycle_state",
    "register_root",
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
            raise PreconditionRefused(
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
