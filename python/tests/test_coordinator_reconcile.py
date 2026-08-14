"""Pure chain/store reconciliation decisions."""

from __future__ import annotations

from dataclasses import replace

import pytest

from atoms.chain.append import append_entry, bootstrap_chain
from atoms.chain.errors import ChainStateInvalid
from atoms.chain.model import (
    ChainOutcome,
    Entry,
    GenesisEntry,
    RegisteredEntry,
    SettledEntry,
    encode_entry,
    entry_digest,
)
from atoms.chain.read import (
    STAGING_LEAF,
    SurvivorDisposition,
    ValidatedChain,
    validate_chain,
)
from atoms.coordinator.capture import capture_initial_surface
from atoms.coordinator.prepare import open_workspace, prepare_transaction
from atoms.coordinator.recover import (
    Reconciliation,
    _Append,
    _Backfill,
    _derive_reconciliation,
    _perform_reconciliation,
    _registration_entry,
)
from atoms.core.recovery.model import (
    CommitDecision,
    EffectJournalState,
    JournalState,
    TransactionState,
)
from atoms.fs.audit import AuditedBackend
from atoms.store.records import StoredRecord
from tests.capture_support import DictPayloads, digest_of
from tests.coordinator_support import AFTER, admission_for, file_spec
from tests.store_support import matching_diagnostic


def _chain(*entries: Entry) -> ValidatedChain:
    previous = None
    found = []
    for entry in entries:
        digest = entry_digest(encode_entry(previous, entry))
        found.append((digest, entry))
        previous = digest
    return ValidatedChain(tuple(found), previous, ())


def _record(**changes: object) -> StoredRecord:
    spec = replace(file_spec(), registered_paths=("d/f.txt",))
    record = StoredRecord(
        txid="tx1",
        spec=spec,
        state=TransactionState.PREPARED,
        committed=CommitDecision.UNCOMMITTED,
        rollback_result=None,
        halt_diagnostic=None,
        registration_digest=None,
        settlement_digest=None,
        approval_evidence="evidence",
        assembly_halt=None,
        journals=(EffectJournalState("e1", JournalState.PENDING),),
    )
    return replace(record, **changes)


def _history(record: StoredRecord) -> tuple[ValidatedChain, str, RegisteredEntry]:
    registered = _registration_entry(record.spec, record.txid)
    chain = _chain(GenesisEntry(b"root", ()), registered)
    return chain, chain.entries[-1][0], registered


def test_registration_entry_is_the_canonical_registered_surface_projection() -> None:
    record = _record()

    entry = _registration_entry(record.spec, record.txid)

    assert entry.txid == record.txid
    assert tuple(path for path, _ in entry.initial) == ("d/f.txt",)
    assert tuple(path for path, _ in entry.final) == ("d/f.txt",)
    assert entry.intent_digest == record.spec.intent_digest
    assert entry.consumer_tag == record.spec.consumer_tag


def test_reconciliation_append_uses_the_same_registration_envelope_as_forward() -> None:
    record = _record()
    validated = _chain(GenesisEntry(b"root", ()))
    action = _derive_reconciliation(record, validated).registration

    assert type(action) is _Append
    assert encode_entry(validated.tip, action.entry) == encode_entry(
        validated.tip, _registration_entry(record.spec, record.txid)
    )


def test_missing_prepared_registration_is_appended() -> None:
    record = _record()
    chain = _chain(GenesisEntry(b"root", ()))

    assert _derive_reconciliation(record, chain) == Reconciliation(
        registration=_Append(_registration_entry(record.spec, record.txid)),
        settlement=None,
    )


def test_unique_unbound_registration_is_backfilled() -> None:
    record = _record()
    chain, digest, _ = _history(record)

    assert _derive_reconciliation(record, chain) == Reconciliation(
        registration=_Backfill(digest), settlement=None
    )


@pytest.mark.parametrize(
    ("state", "journal"),
    [
        (TransactionState.APPLYING, JournalState.PENDING),
        (TransactionState.PREPARED, JournalState.STARTED),
    ],
)
def test_missing_registration_outside_the_only_crash_window_is_invalid(
    state: TransactionState, journal: JournalState
) -> None:
    record = _record(
        state=state, journals=(EffectJournalState("e1", journal),)
    )

    with pytest.raises(ChainStateInvalid, match="registration"):
        _derive_reconciliation(record, _chain(GenesisEntry(b"root", ())))


def test_bound_registration_must_resolve_and_match_the_txid() -> None:
    record = _record(registration_digest="a" * 64)

    with pytest.raises(ChainStateInvalid, match="registration"):
        _derive_reconciliation(record, _chain(GenesisEntry(b"root", ())))

    other = _registration_entry(record.spec, "tx2")
    chain = _chain(GenesisEntry(b"root", ()), other)
    with pytest.raises(ChainStateInvalid, match="registration"):
        _derive_reconciliation(
            replace(record, registration_digest=chain.entries[-1][0]), chain
        )


def test_duplicate_registration_txid_is_invalid() -> None:
    record = _record()
    registered = _registration_entry(record.spec, record.txid)
    chain = _chain(GenesisEntry(b"root", ()), registered, registered)

    with pytest.raises(ChainStateInvalid, match="duplicate registration"):
        _derive_reconciliation(record, chain)


@pytest.mark.parametrize(
    ("state", "outcome"),
    [
        (TransactionState.COMMITTED, ChainOutcome.COMMITTED),
        (TransactionState.ROLLED_BACK, ChainOutcome.ROLLED_BACK),
    ],
)
def test_missing_terminal_settlement_is_appended(
    state: TransactionState, outcome: ChainOutcome
) -> None:
    base = _record()
    chain, registration, _ = _history(base)
    record = replace(
        base, state=state, registration_digest=registration
    )

    assert _derive_reconciliation(record, chain) == Reconciliation(
        registration=None,
        settlement=_Append(SettledEntry("tx1", registration, outcome)),
    )


def test_unique_unbound_terminal_settlement_is_backfilled() -> None:
    base = _record()
    chain, registration, registered = _history(base)
    settled = SettledEntry("tx1", registration, ChainOutcome.COMMITTED)
    chain = _chain(GenesisEntry(b"root", ()), registered, settled)
    record = replace(
        base,
        state=TransactionState.COMMITTED,
        committed=CommitDecision.COMMITTED,
        registration_digest=registration,
    )

    assert _derive_reconciliation(record, chain) == Reconciliation(
        registration=None, settlement=_Backfill(chain.entries[-1][0])
    )


@pytest.mark.parametrize(
    ("outcome", "registration"),
    [
        (ChainOutcome.ROLLED_BACK, None),
        (ChainOutcome.COMMITTED, "f" * 64),
    ],
)
def test_unbound_terminal_settlement_must_match_record(
    outcome: ChainOutcome, registration: str | None
) -> None:
    base = _record()
    original, digest, registered = _history(base)
    del original
    settled = SettledEntry("tx1", registration or digest, outcome)
    chain = _chain(GenesisEntry(b"root", ()), registered, settled)
    record = replace(
        base,
        state=TransactionState.COMMITTED,
        committed=CommitDecision.COMMITTED,
        registration_digest=digest,
    )

    with pytest.raises(ChainStateInvalid, match="settlement"):
        _derive_reconciliation(record, chain)


def test_bound_settlement_must_resolve_to_the_exact_record_decision() -> None:
    base = _record()
    _, registration, registered = _history(base)
    settled = SettledEntry("tx1", registration, ChainOutcome.ROLLED_BACK)
    chain = _chain(GenesisEntry(b"root", ()), registered, settled)
    record = replace(
        base,
        state=TransactionState.COMMITTED,
        committed=CommitDecision.COMMITTED,
        registration_digest=registration,
        settlement_digest=chain.entries[-1][0],
    )

    with pytest.raises(ChainStateInvalid, match="settlement"):
        _derive_reconciliation(record, chain)


def test_duplicate_settlement_txid_is_invalid() -> None:
    base = _record()
    _, registration, registered = _history(base)
    settled = SettledEntry("tx1", registration, ChainOutcome.COMMITTED)
    chain = _chain(GenesisEntry(b"root", ()), registered, settled, settled)
    record = replace(
        base,
        state=TransactionState.COMMITTED,
        committed=CommitDecision.COMMITTED,
        registration_digest=registration,
    )

    with pytest.raises(ChainStateInvalid, match="duplicate settlement"):
        _derive_reconciliation(record, chain)


def test_nonterminal_record_cannot_have_a_settlement() -> None:
    base = _record()
    _, registration, registered = _history(base)
    settled = SettledEntry("tx1", registration, ChainOutcome.COMMITTED)
    chain = _chain(GenesisEntry(b"root", ()), registered, settled)
    record = replace(base, registration_digest=registration)

    with pytest.raises(ChainStateInvalid, match="settlement"):
        _derive_reconciliation(record, chain)


def test_committed_halt_accepts_only_its_bound_committed_settlement() -> None:
    base = _record()
    _, registration, registered = _history(base)
    settled = SettledEntry("tx1", registration, ChainOutcome.COMMITTED)
    chain = _chain(GenesisEntry(b"root", ()), registered, settled)
    diagnostic = replace(
        matching_diagnostic(),
        pre_halt_state=TransactionState.COMMITTED,
        commit_decision=CommitDecision.COMMITTED,
    )
    record = replace(
        base,
        state=TransactionState.HALTED,
        committed=CommitDecision.COMMITTED,
        halt_diagnostic=diagnostic,
        registration_digest=registration,
        settlement_digest=chain.entries[-1][0],
    )

    assert _derive_reconciliation(record, chain) == Reconciliation(None, None)

    with pytest.raises(ChainStateInvalid, match="settlement"):
        _derive_reconciliation(
            replace(record, halt_diagnostic=matching_diagnostic()), chain
        )


def test_no_record_requires_no_reconciliation() -> None:
    assert _derive_reconciliation(
        None, _chain(GenesisEntry(b"root", ()))
    ) == Reconciliation(None, None)


def test_perform_reconciliation_appends_and_binds_registration(leased) -> None:
    with leased() as lease:
        approved = admission_for(lease)
        with open_workspace(lease, approved) as workspace, capture_initial_surface(
            lease, approved, workspace, DictPayloads({digest_of(AFTER): AFTER})
        ) as captured:
            prepare_transaction(lease, approved, workspace, captured.manifest)

        backend = lease._binding.backend
        assert isinstance(backend, AuditedBackend)
        chain_fd = bootstrap_chain(backend, lease._binding.project_root_fd)
        try:
            append_entry(
                backend,
                chain_fd,
                ValidatedChain((), None, ()),
                GenesisEntry(b"root", ()),
            )
            record = lease._store.read_active()
            assert record is not None
            before = validate_chain(backend, chain_fd)
            envelope = encode_entry(
                before.tip, _registration_entry(record.spec, record.txid)
            )
            staging_fd = backend.create_exclusive(chain_fd, STAGING_LEAF, 0o600)
            try:
                assert backend.write(staging_fd, envelope) == len(envelope)
                backend.flush_file(staging_fd)
            finally:
                backend.close_fd(staging_fd)
            validated = validate_chain(backend, chain_fd)
            assert validated.survivors[0].disposition is SurvivorDisposition.REMOVE
            actions = _derive_reconciliation(record, validated)

            fresh = _perform_reconciliation(
                backend, lease._store, chain_fd, validated, actions
            )
        finally:
            backend.close_fd(chain_fd)

        stored = lease._store.read_active()
        assert stored is not None and stored.registration_digest == fresh.tip
        assert type(fresh.entries[-1][1]) is RegisteredEntry
        assert fresh.survivors == ()
