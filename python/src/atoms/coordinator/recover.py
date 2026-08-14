"""Recovery-plan execution and terminal chain settlement."""

from __future__ import annotations

import contextlib
import errno
import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast

from atoms.chain.append import append_entry, apply_survivors
from atoms.chain.errors import ChainStateInvalid
from atoms.chain.model import (
    ChainOutcome,
    Entry,
    RegisteredEntry,
    SettledEntry,
    encode_entry,
    entry_digest,
    state_to_json,
)
from atoms.chain.read import ValidatedChain, validate_chain
from atoms.coordinator.admission import _require_admitted
from atoms.coordinator.descriptors import (
    DescriptorTable,
    _build_descriptor_table,
    _directory_paths,
    _modeled_children,
    _resume_descent,
)
from atoms.coordinator.effects.common import EffectMismatch
from atoms.coordinator.effects.settle import (
    _observe_joint,
    apply_remove_scratch,
    apply_transform,
)
from atoms.coordinator.lease import Lease
from atoms.coordinator.transitions import (
    persist_detach,
    persist_plan_prefix,
)
from atoms.core.assembly import (
    AssemblyFinding,
    AssemblyFindingKind,
    AssemblyHalt,
    AssemblyHaltReason,
    AssemblyOperatorAction,
)
from atoms.core.compiler import compile_spec
from atoms.core.effects import CreateFileNoClobber, ReplaceFile
from atoms.core.errors import (
    PreconditionRefused,
    ProjectApprovalRefused,
    ProtocolError,
    TransactionHalted,
)
from atoms.core.recovery import build_recovery_snapshot, classify_recovery
from atoms.core.recovery.authorization import _mutation_denied, authorize_recovery_step
from atoms.core.recovery.model import (
    OBSERVED_ABSENT,
    FileBuildRelation,
    JournalState,
    ObservedDirectory,
    ObservedEntry,
    ObservedFile,
    PersistentObservation,
    ScratchObservation,
    TransactionState,
)
from atoms.core.recovery.plan import (
    AuthorizedStep,
    DetachActive,
    HaltPlan,
    NoRecoveryPlan,
    RecoveryPlan,
    RemoveScratch,
    TransformEffectTuple,
)
from atoms.core.recovery.snapshot import PersistentNode, TopologyNode
from atoms.core.scratch import CHAIN_LEAF
from atoms.core.spec import TransactionSpec
from atoms.fs.approval import (
    ProjectApprovedSpec,
    ProjectContext,
    _approve_for_recovery,
    decode_approval_evidence,
)
from atoms.fs.audit import AuditedBackend, Provenance, RootKind
from atoms.fs.binding import ProjectBinding
from atoms.fs.bootstrap import WORK_DIRECTORY
from atoms.fs.lookup import read_lookup_constraints
from atoms.fs.observe import Observation
from atoms.fs.resolve import filesystem_type_of
from atoms.fs.volume import read_mount_id
from atoms.store import Store, StoredRecord
from atoms.store.blobs import BLOBS_PARENT, digest_to_leaf
from atoms.store.errors import MetadataStoreInvalid
from atoms.store.records import require_assembly_halt_binding
from atoms.store.workspace import reopen_work_slot, require_staging_discharged


@dataclass(frozen=True, slots=True)
class _Backfill:
    digest: str


@dataclass(frozen=True, slots=True)
class _Append:
    entry: Entry


_ReconciliationAction = _Backfill | _Append


@dataclass(frozen=True, slots=True)
class Reconciliation:
    registration: _ReconciliationAction | None
    settlement: _ReconciliationAction | None


def _registration_entry(spec: TransactionSpec, txid: str) -> RegisteredEntry:
    initial = {item.path: item.state for item in spec.initial_surface}
    final = {item.path: item.state for item in spec.final_surface}
    return RegisteredEntry(
        txid=txid,
        intent_digest=spec.intent_digest,
        consumer_tag=spec.consumer_tag,
        initial=tuple(
            (path, state_to_json(initial[path])) for path in spec.registered_paths
        ),
        final=tuple(
            (path, state_to_json(final[path])) for path in spec.registered_paths
        ),
        fulfills=spec.fulfills,
    )


def _terminal_outcome(record: StoredRecord) -> ChainOutcome | None:
    if record.state is TransactionState.COMMITTED:
        return ChainOutcome.COMMITTED
    if record.state is TransactionState.ROLLED_BACK:
        return ChainOutcome.ROLLED_BACK
    return None


def _derive_reconciliation(
    record: StoredRecord | None, validated: ValidatedChain
) -> Reconciliation:
    """Derive the exact chain/store repairs without writing either substrate."""

    if record is None:
        return Reconciliation(None, None)

    entries = dict(validated.entries)
    registrations = [
        (digest, entry)
        for digest, entry in validated.entries
        if type(entry) is RegisteredEntry and entry.txid == record.txid
    ]
    if len(registrations) > 1:
        raise ChainStateInvalid("the chain contains duplicate registration entries")

    registration: _ReconciliationAction | None = None
    if record.registration_digest is not None:
        bound = entries.get(record.registration_digest)
        if type(bound) is not RegisteredEntry or bound.txid != record.txid:
            raise ChainStateInvalid(
                "the registration binding does not resolve to this transaction"
            )
    else:
        registration_window = (
            record.state is TransactionState.PREPARED
            and all(journal.state is JournalState.PENDING for journal in record.journals)
        )
        if not registration_window:
            raise ChainStateInvalid("the durable record is missing its registration")
        registration = (
            _Backfill(registrations[0][0])
            if registrations
            else _Append(_registration_entry(record.spec, record.txid))
        )

    settlements = [
        (digest, entry)
        for digest, entry in validated.entries
        if type(entry) is SettledEntry and entry.txid == record.txid
    ]
    if len(settlements) > 1:
        raise ChainStateInvalid("the chain contains duplicate settlement entries")

    outcome = _terminal_outcome(record)
    committed_halt = (
        record.state is TransactionState.HALTED
        and record.halt_diagnostic is not None
        and record.halt_diagnostic.pre_halt_state is TransactionState.COMMITTED
    )
    settlement: _ReconciliationAction | None = None
    if record.settlement_digest is not None:
        bound = entries.get(record.settlement_digest)
        expected_outcome = ChainOutcome.COMMITTED if committed_halt else outcome
        if (
            type(bound) is not SettledEntry
            or bound.txid != record.txid
            or bound.registration != record.registration_digest
            or bound.outcome is not expected_outcome
        ):
            raise ChainStateInvalid(
                "the settlement binding contradicts the durable record"
            )
        if outcome is None and not committed_halt:
            raise ChainStateInvalid(
                "only a committed halt may retain a terminal settlement"
            )
    elif outcome is not None:
        expected = SettledEntry(
            txid=record.txid,
            registration=cast(str, record.registration_digest),
            outcome=outcome,
        )
        if settlements:
            digest, found = settlements[0]
            if found != expected:
                raise ChainStateInvalid(
                    "the unbound settlement contradicts the durable record"
                )
            settlement = _Backfill(digest)
        else:
            settlement = _Append(expected)
    elif settlements:
        raise ChainStateInvalid("a nonterminal record has a settlement entry")

    return Reconciliation(registration, settlement)


def _perform_reconciliation(
    backend: AuditedBackend,
    store: Store,
    chain_fd: int,
    validated: ValidatedChain,
    actions: Reconciliation,
) -> ValidatedChain:
    """Apply exactly one derived reconciliation decision."""

    appends = tuple(
        action
        for action in (actions.registration, actions.settlement)
        if type(action) is _Append
    )
    if len(appends) > 1:
        raise ProtocolError("one reconciliation cannot append two chain entries")
    envelope = (
        encode_entry(validated.tip, cast(_Append, appends[0]).entry)
        if appends
        else None
    )
    fresh = validate_chain(
        backend, chain_fd, () if envelope is None else (envelope,)
    )
    if fresh.entries != validated.entries or fresh.tip != validated.tip:
        raise ChainStateInvalid("the chain changed after reconciliation derivation")
    fresh = apply_survivors(backend, chain_fd, fresh)
    record = store.read_active()
    if any(action is not None for action in (actions.registration, actions.settlement)):
        if record is None:
            raise ProtocolError("reconciliation actions require an active record")
        txid = record.txid

    for column, action in (
        ("registration", actions.registration),
        ("settlement", actions.settlement),
    ):
        if action is None:
            continue
        if type(action) is _Backfill:
            digest = action.digest
        else:
            planned = cast(bytes, envelope)
            digest = entry_digest(planned)
            if not any(found == digest for found, _ in fresh.entries):
                appended = append_entry(
                    backend, chain_fd, fresh, cast(_Append, action).entry
                )
                if appended != digest:
                    raise ProtocolError("the chain append returned an unexpected digest")
                fresh = validate_chain(backend, chain_fd)
        with store.transaction() as txn:
            if column == "registration":
                txn.set_registration_digest(txid, digest)
            else:
                txn.set_settlement_digest(txid, digest)
    return fresh


def _factless(path: str, kind: AssemblyFindingKind) -> AssemblyFinding:
    return AssemblyFinding(path, kind, ())


def _wrong_kind(parent_fd: int, component: str, path: str) -> AssemblyFinding:
    info = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
    if stat.S_ISREG(info.st_mode):
        kind = "file"
    elif stat.S_ISLNK(info.st_mode):
        kind = "symlink"
    else:
        kind = "other"
    return AssemblyFinding(
        path, AssemblyFindingKind.WRONG_ENTRY_KIND, (("observed_kind", kind),)
    )


def _directory_changes(
    binding: ProjectBinding,
    fd: int,
    path: str,
    expected: dict[str, Any],
) -> list[AssemblyFinding]:
    findings: list[AssemblyFinding] = []
    identity = expected["identity"]
    if identity is not None:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (identity["st_dev"], identity["st_ino"]):
            findings.append(
                AssemblyFinding(
                    path,
                    AssemblyFindingKind.IDENTITY_CHANGED,
                    (("st_dev", str(info.st_dev)), ("st_ino", str(info.st_ino))),
                )
            )
    constraints = read_lookup_constraints(fd, filesystem_type_of(binding))
    if (
        constraints.lookup_proof.value != expected["lookup_proof"]
        or constraints.name_max != expected["name_max"]
    ):
        findings.append(
            AssemblyFinding(
                path,
                AssemblyFindingKind.CONSTRAINTS_CHANGED,
                (
                    ("lookup_proof", constraints.lookup_proof.value),
                    ("name_max", str(constraints.name_max)),
                ),
            )
        )
    return findings


def _work_base_findings(
    binding: ProjectBinding, expected: dict[str, Any] | None
) -> tuple[list[AssemblyFinding], int | None]:
    if expected is None:
        return [], None
    backend = cast(AuditedBackend, binding.backend)
    path = ".#~work_base"
    try:
        fd = backend.open_child_directory(binding.metadata_root_fd, WORK_DIRECTORY)
    except OSError as caught:
        if caught.errno == errno.EXDEV:
            return [_factless(path, AssemblyFindingKind.MOUNT_BOUNDARY)], None
        if caught.errno in {errno.EACCES, errno.EPERM}:
            return [_factless(path, AssemblyFindingKind.ACCESS_DENIED)], None
        if caught.errno == errno.ENOENT:
            return [
                AssemblyFinding(
                    path,
                    AssemblyFindingKind.WORK_ROOT_CHANGED,
                    (("work_base", "absent"),),
                )
            ], None
        if caught.errno in {errno.ENOTDIR, errno.ELOOP}:
            return [
                AssemblyFinding(
                    path,
                    AssemblyFindingKind.WORK_ROOT_CHANGED,
                    (("work_base", "present"),),
                )
            ], None
        raise
    info = os.fstat(fd)
    constraints = read_lookup_constraints(fd, filesystem_type_of(binding))
    identity = expected["identity"]
    changed = (
        (info.st_dev, info.st_ino) != (identity["st_dev"], identity["st_ino"])
        or constraints.lookup_proof.value != expected["lookup_proof"]
        or constraints.name_max != expected["name_max"]
    )
    findings = (
        [
            AssemblyFinding(
                path,
                AssemblyFindingKind.WORK_ROOT_CHANGED,
                (("work_base", "present"),),
            )
        ]
        if changed
        else []
    )
    return findings, fd


def _diff_approved_topology(
    binding: ProjectBinding, expected: dict[str, Any], txid: str
) -> tuple[AssemblyFinding, ...]:
    """Compare the current rooted directory facts with the closed durable document."""

    backend = cast(AuditedBackend, binding.backend)
    findings: list[AssemblyFinding] = []
    work_findings, work_base_fd = _work_base_findings(
        binding, cast(dict[str, Any] | None, expected["work_root"])
    )
    findings.extend(work_findings)
    owned: list[int] = []
    project_fds: dict[str, int] = {"": binding.project_root_fd}
    blocked: set[str] = set()
    try:
        directories = cast(list[dict[str, Any]], expected["directories"])
        for item in sorted(
            directories,
            key=lambda row: (
                -1 if row["path"] is None else cast(str, row["path"]).count("/"),
                "" if row["path"] is None else cast(str, row["path"]),
            ),
        ):
            path = item["path"]
            if path is None:
                pseudo = ".#~work_root"
                if work_base_fd is None:
                    continue
                parent_fd, component = work_base_fd, txid
                planned = False
            else:
                path = cast(str, path)
                if path == "":
                    fd = binding.project_root_fd
                    findings.extend(_directory_changes(binding, fd, path, item))
                    observed_mount = read_mount_id(fd)
                    if observed_mount != expected["mount_id"]:
                        findings.append(
                            AssemblyFinding(
                                path,
                                AssemblyFindingKind.MOUNT_CHANGED,
                                (("mount_id", str(observed_mount)),),
                            )
                        )
                    continue
                if any(path == prefix or path.startswith(f"{prefix}/") for prefix in blocked):
                    continue
                parent_path, _, component = path.rpartition("/")
                parent_fd = project_fds[parent_path]
                pseudo = path
                planned = item["identity"] is None
            try:
                fd = backend.open_child_directory(parent_fd, component)
            except OSError as caught:
                if caught.errno == errno.EXDEV:
                    findings.append(
                        _factless(pseudo, AssemblyFindingKind.MOUNT_BOUNDARY)
                    )
                elif caught.errno in {errno.EACCES, errno.EPERM}:
                    findings.append(
                        _factless(pseudo, AssemblyFindingKind.ACCESS_DENIED)
                    )
                elif not planned and caught.errno == errno.ENOENT:
                    findings.append(
                        _factless(pseudo, AssemblyFindingKind.NODE_MISSING)
                    )
                elif not planned and caught.errno in {errno.ENOTDIR, errno.ELOOP}:
                    findings.append(_wrong_kind(parent_fd, component, pseudo))
                if path is not None:
                    blocked.add(cast(str, path))
                if caught.errno not in {
                    errno.ENOENT,
                    errno.ENOTDIR,
                    errno.ELOOP,
                    errno.EXDEV,
                    errno.EACCES,
                    errno.EPERM,
                }:
                    raise
                continue
            owned.append(fd)
            if path is not None:
                project_fds[cast(str, path)] = fd
            findings.extend(_directory_changes(binding, fd, pseudo, item))
    finally:
        for fd in reversed(owned):
            backend.close_fd(fd)
        if work_base_fd is not None:
            backend.close_fd(work_base_fd)
    order = {kind: index for index, kind in enumerate(AssemblyFindingKind)}
    return tuple(sorted(findings, key=lambda item: (item.path, order[item.kind])))


def _persist_assembly_halt(store: Store, halt: AssemblyHalt) -> None:
    with store.transaction() as txn:
        txn.set_assembly_halt(halt.txid, halt)


def _halt_for_findings(
    store: Store, record: StoredRecord, findings: tuple[AssemblyFinding, ...]
) -> None:
    halt = AssemblyHalt(
        txid=record.txid,
        reason=AssemblyHaltReason.APPROVAL_EVIDENCE_MISMATCH,
        expected=record.approval_evidence,
        findings=findings,
        operator_action=AssemblyOperatorAction.RESTORE_APPROVED_TOPOLOGY,
    )
    _persist_assembly_halt(store, halt)
    raise TransactionHalted(halt)


def _observe_snapshot(
    lease: Lease,
    approved: ProjectApprovedSpec,
    table: DescriptorTable,
    observation: Observation,
    record: StoredRecord,
):
    paths = _directory_paths(approved)
    stops = {stop.node: stop.observed for stop in table.stops}
    persistent: list[PersistentObservation] = []
    for row in approved.paths:
        node = PersistentNode(row.path)
        if node in stops:
            entry = stops[node]
        elif table.is_unreachable(row.parent_node):
            entry = OBSERVED_ABSENT
        else:
            entry = observation.observe(
                table.fd_for(row.parent_node),
                row.leaf,
                modeled=_recovery_modeled_children(approved, paths, node),
            )
        persistent.append(PersistentObservation(row.path, entry))

    live = {item.path: item.entry for item in persistent}
    journals = {item.effect_id: item.state for item in record.journals}
    effects = {effect.effect_id: effect for effect in approved.compiled.spec.effects}
    scratch: list[ScratchObservation] = []
    backend = cast(AuditedBackend, lease._binding.backend)
    for row in approved.scratch:
        if table.is_unreachable(row.parent_node):
            entry: ObservedEntry = OBSERVED_ABSENT
        else:
            entry = observation.observe(
                table.fd_for(row.parent_node), row.leaf, modeled=frozenset()
            )
        relation: FileBuildRelation | None = None
        effect = effects[row.effect_id]
        relation_required = (
            journals[row.effect_id] is JournalState.STARTED
            and type(entry) is ObservedFile
            and (
                type(effect) is CreateFileNoClobber
                or type(effect) is ReplaceFile
                and type(live[effect.path]) is ObservedFile
                and cast(ObservedFile, live[effect.path]).state == effect.pre
            )
        )
        if relation_required:
            state = cast(CreateFileNoClobber | ReplaceFile, effect).post
            blob_fd = lease._store.open_blob(state.content_hash)
            backend.register(
                blob_fd,
                Provenance(
                    RootKind.METADATA,
                    f"{BLOBS_PARENT}/{digest_to_leaf(state.content_hash)}",
                ),
            )
            try:
                relation = observation.build_relation(
                    observation.pinned_descriptor(cast(ObservedFile, entry).identity),
                    blob_fd,
                )
            finally:
                backend.close_fd(blob_fd)
        scratch.append(ScratchObservation(row.effect_id, row.role, entry, relation))

    return build_recovery_snapshot(
        compiled=approved.compiled,
        topology=approved.topology,
        transaction_state=record.state,
        commit_decision=record.committed,
        rollback_result=record.rollback_result,
        halt_diagnostic=record.halt_diagnostic,
        active=True,
        journals=record.journals,
        persistent_observations=tuple(persistent),
        scratch_observations=tuple(scratch),
    )


def _recovery_modeled_children(
    approved: ProjectApprovedSpec,
    paths: dict[TopologyNode, str],
    node: TopologyNode,
) -> frozenset[str]:
    return _modeled_children(paths, node) | frozenset(
        row.leaf for row in approved.scratch if row.parent_node == node
    )


def resolve(binding: ProjectBinding, store: Store) -> None:
    """Resolve one active transaction through the seven pinned recovery phases."""

    backend = cast(AuditedBackend, binding.backend)
    record = store.read_active()
    try:
        chain_fd = backend.open_child_directory(binding.project_root_fd, CHAIN_LEAF)
    except OSError as caught:
        if caught.errno == errno.ENOENT and record is None:
            return
        if caught.errno == errno.ENOENT:
            raise ChainStateInvalid(
                "a live transaction record exists without its project chain"
            ) from caught
        if caught.errno in {errno.ENOTDIR, errno.ELOOP, errno.EXDEV}:
            raise ChainStateInvalid(
                "the reserved chain leaf is not a stable directory"
            ) from caught
        raise
    try:
        validated = validate_chain(backend, chain_fd)
        if record is not None and not validated.entries:
            raise ChainStateInvalid(
                "a live transaction record exists without a chain genesis"
            )
        actions = _derive_reconciliation(record, validated)
        if record is not None and record.state is TransactionState.HALTED:
            if record.halt_diagnostic is None:
                raise ProtocolError("a HALTED record has no frozen diagnostic")
            raise TransactionHalted(record.halt_diagnostic)
        if record is not None and record.assembly_halt is not None:
            require_assembly_halt_binding(
                record.txid, record.approval_evidence, record.assembly_halt
            )
            raise TransactionHalted(record.assembly_halt)
        _perform_reconciliation(
            backend, store, chain_fd, validated, actions
        )
    finally:
        backend.close_fd(chain_fd)

    record = store.read_active()
    if record is None:
        return
    compiled = compile_spec(record.spec)
    expected = decode_approval_evidence(record.approval_evidence)
    findings = _diff_approved_topology(binding, expected, record.txid)
    if findings:
        _halt_for_findings(store, record, findings)

    def moved_world(caught: BaseException) -> None:
        changed = _diff_approved_topology(binding, expected, record.txid)
        if changed:
            _halt_for_findings(store, record, changed)
        raise ProtocolError(
            "recovery approval disagrees with an unchanged topology diff"
        ) from caught

    try:
        approved = _approve_for_recovery(
            compiled,
            ProjectContext(binding=binding, txid=record.txid),
            evidence=expected,
        )
    except (ProjectApprovalRefused, PreconditionRefused) as caught:
        moved_world(caught)
        raise AssertionError("unreachable")

    lease = Lease(_binding=binding, _store=store)
    require_staging_discharged(store, record.txid)
    try:
        workspace = reopen_work_slot(store, record.txid)
    except (MetadataStoreInvalid, OSError) as caught:
        if isinstance(caught, OSError) and caught.errno not in {
            errno.EACCES,
            errno.EPERM,
            errno.EXDEV,
        }:
            raise
        changed = _diff_approved_topology(binding, expected, record.txid)
        if any(
            item.path in {".#~work_root", ".#~work_base"} for item in changed
        ):
            _halt_for_findings(store, record, changed)
        raise

    with workspace, Observation(backend) as observation:
        try:
            table = _build_descriptor_table(
                lease, approved, workspace, observation
            )
        except (ProjectApprovalRefused, PreconditionRefused) as caught:
            moved_world(caught)
            raise AssertionError("unreachable")
        with table:
            try:
                for stop in tuple(table.stops):
                    if type(stop.observed) is ObservedDirectory:
                        _resume_descent(
                            table, backend, observation, approved, stop.node
                        )
            except PreconditionRefused as caught:
                moved_world(caught)
                raise AssertionError("unreachable")
            snapshot = _observe_snapshot(
                lease, approved, table, observation, record
            )
            plan = classify_recovery(snapshot)
            backend.set_declared_paths(
                frozenset(path.path for path in approved.paths)
            )
            try:
                result = run_plan(lease, approved, table, plan)
            finally:
                backend.clear_declared_paths()
    if type(result) is HaltPlan:
        raise TransactionHalted(result.diagnostic)


@contextlib.contextmanager
def _registered_root(lease: Lease) -> Iterator[tuple[int, ValidatedChain]]:
    backend = cast(AuditedBackend, lease._binding.backend)
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
            raise PreconditionRefused("the project root is not registered") from caught
        if caught.errno in {errno.ENOTDIR, errno.ELOOP, errno.EXDEV}:
            raise ChainStateInvalid(
                "the reserved chain leaf is not a stable directory"
            ) from caught
        raise

    try:
        validated = validate_chain(backend, chain_fd)
        if validated.survivors:
            raise ChainStateInvalid("chain staging appeared after lease resolution")
        if not validated.entries:
            if lease._store.read_active() is not None:
                raise ChainStateInvalid(
                    "a live transaction record exists without a chain genesis"
                )
            raise PreconditionRefused("the project root is not registered")
        yield chain_fd, validated
    finally:
        backend.close_fd(chain_fd)


def run_plan(
    lease: Lease,
    approved: ProjectApprovedSpec,
    table: DescriptorTable,
    plan: RecoveryPlan,
) -> RecoveryPlan:
    _require_admitted(lease, approved)
    if type(plan) is NoRecoveryPlan:
        return plan
    cursor = 0
    while cursor < len(plan.steps):
        cursor = persist_plan_prefix(lease, approved, plan, cursor)
        if cursor == len(plan.steps):
            break
        step = plan.steps[cursor]
        if type(step) is DetachActive:
            _reconcile_settlement(lease, approved)
            cursor = persist_detach(lease, approved, plan, cursor)
            continue
        if type(step) not in {TransformEffectTuple, RemoveScratch}:
            raise ProtocolError("the plan stopped before a non-mutating step")
        step = cast(TransformEffectTuple | RemoveScratch, step)
        observed = _observe_for_step(lease, approved, table, step)
        authorized = authorize_recovery_step(plan, cursor, observed)
        if type(authorized) is HaltPlan:
            _persist_halt(lease, approved, authorized)
            return authorized
        authorized = cast(AuthorizedStep, authorized)
        try:
            _execute_mutating(lease, approved, table, authorized)
        except EffectMismatch as mismatch:
            observed = _observe_for_step(lease, approved, table, step)
            reconsidered = authorize_recovery_step(plan, cursor, observed)
            if type(reconsidered) is HaltPlan:
                _persist_halt(lease, approved, reconsidered)
                return reconsidered
            reconsidered = cast(AuthorizedStep, reconsidered)
            if mismatch.errno in {errno.EACCES, errno.EPERM}:
                denied = _mutation_denied(reconsidered, observed)
                _persist_halt(lease, approved, denied)
                return denied
            raise ProtocolError(
                "a recovery mutation failed while its observable world stayed unchanged"
            ) from mismatch
        cursor += 1
    return plan


def _observe_for_step(
    lease: Lease,
    approved: ProjectApprovedSpec,
    table: DescriptorTable,
    step: TransformEffectTuple | RemoveScratch,
):
    return _observe_joint(lease, approved, table, step.expected_before)


def _execute_mutating(
    lease: Lease,
    approved: ProjectApprovedSpec,
    table: DescriptorTable,
    authorized: AuthorizedStep,
) -> None:
    if type(authorized.step) is TransformEffectTuple:
        apply_transform(lease, approved, table, authorized)
        return
    if type(authorized.step) is RemoveScratch:
        apply_remove_scratch(lease, approved, table, authorized)
        return
    raise ProtocolError("authorization proof names no filesystem mutation")


def _persist_halt(
    lease: Lease, approved: ProjectApprovedSpec, plan: HaltPlan
) -> None:
    if persist_plan_prefix(lease, approved, plan, 0) != len(plan.steps):
        raise ProtocolError("a halt plan unexpectedly contains a filesystem mutation")


def _reconcile_settlement(lease: Lease, approved: ProjectApprovedSpec) -> None:
    record = lease._store.read_active()
    if record is None or record.txid != approved.txid:
        raise ProtocolError("the proof's transaction is not active")

    with _registered_root(lease) as (chain_fd, validated):
        actions = _derive_reconciliation(record, validated)
        _perform_reconciliation(
            cast(AuditedBackend, lease._binding.backend),
            lease._store,
            chain_fd,
            validated,
            actions,
        )
