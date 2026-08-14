"""The recovery-plan executor loop."""

from __future__ import annotations

from atoms.chain.append import append_entry, bootstrap_chain
from atoms.chain.model import GenesisEntry, RegisteredEntry
from atoms.chain.read import validate_chain
from atoms.coordinator.capture import capture_initial_surface
from atoms.coordinator.prepare import open_workspace, prepare_transaction
from atoms.coordinator.recover import run_plan
from atoms.core.recovery import (
    OBSERVED_ABSENT,
    HaltPlan,
    JournalState,
    TransactionState,
    classify_recovery,
)
from atoms.fs.audit import AuditedBackend
from atoms.fs.observe import Observation
from tests.capture_support import AFTER, DictPayloads, digest_of
from tests.coordinator_support import (
    admission_for,
    prepared_with_remove_scratch,
    snapshot_for,
)


def register_transaction(lease, approved) -> str:
    backend = lease._binding.backend
    assert isinstance(backend, AuditedBackend)
    chain_fd = bootstrap_chain(backend, lease._binding.project_root_fd)
    try:
        append_entry(
            backend,
            chain_fd,
            validate_chain(backend, chain_fd),
            GenesisEntry(payload=b"test", baseline=()),
        )
        digest = append_entry(
            backend,
            chain_fd,
            validate_chain(backend, chain_fd),
            RegisteredEntry(
                txid=approved.txid,
                intent_digest=approved.compiled.spec.intent_digest,
                consumer_tag=approved.compiled.spec.consumer_tag,
                initial=(),
                final=(),
                fulfills=approved.compiled.spec.fulfills,
            ),
        )
    finally:
        backend.close_fd(chain_fd)
    with lease._store.transaction() as txn:
        txn.set_registration_digest(approved.txid, digest)
    return digest


def test_metadata_only_plan_settles_and_detaches(leased) -> None:
    with leased() as lease:
        approved = admission_for(lease)
        with open_workspace(lease, approved) as workspace, capture_initial_surface(
            lease,
            approved,
            workspace,
            DictPayloads({digest_of(AFTER): AFTER}),
        ) as captured:
            prepare_transaction(lease, approved, workspace, captured.manifest)
            registration = register_transaction(lease, approved)
            plan = classify_recovery(
                snapshot_for(
                    approved,
                    state=TransactionState.PREPARED,
                    journal=JournalState.PENDING,
                    live=OBSERVED_ABSENT,
                    staged=OBSERVED_ABSENT,
                )
            )

            assert run_plan(lease, approved, captured.descriptors, plan) is plan

        record = lease._store.read_record(approved.txid)
        assert record is not None
        assert record.state is TransactionState.ROLLED_BACK
        assert record.registration_digest == registration
        assert record.settlement_digest is not None
        assert lease._store.read_active() is None


def test_authorization_mismatch_persists_the_factory_halt(leased) -> None:
    with leased() as lease:
        approved, plan = prepared_with_remove_scratch(lease)
        with lease._store.reopen_workspace(approved.txid) as workspace, Observation(
            lease._binding.backend
        ) as observation:
            from atoms.coordinator.descriptors import _build_descriptor_table

            with _build_descriptor_table(
                lease, approved, workspace, observation
            ) as table:
                result = run_plan(lease, approved, table, plan)

        assert type(result) is HaltPlan
        record = lease._store.read_active()
        assert record is not None
        assert record.state is TransactionState.HALTED
        assert record.halt_diagnostic == result.diagnostic
