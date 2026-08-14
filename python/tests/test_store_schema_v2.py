"""Schema v2's structural history gates and persisted A7 approval proof."""

from __future__ import annotations

import ast
import inspect
import json
import os
import sqlite3

import pytest

from atoms.core.assembly import (
    AssemblyFinding,
    AssemblyFindingKind,
    AssemblyHalt,
    AssemblyHaltReason,
    AssemblyOperatorAction,
    encode_assembly_halt,
)
from atoms.core.canonical import canonical_json
from atoms.core.effects import CreateDirectory, CreateFileNoClobber
from atoms.core.errors import ProtocolError
from atoms.core.fingerprint import DirectoryState
from atoms.core.recovery.model import (
    CommitDecision,
    JournalState,
    RollbackResult,
    TransactionState,
)
from atoms.store.errors import MetadataStoreInvalid
from atoms.store.schema import SCHEMA_VERSION
from tests.fs_support import compiled_for, file_state
from tests.store_support import one_effect_spec, raw_connect

APPROVAL_EVIDENCE = (
    '{"directories":[{"identity":{"st_dev":1,"st_ino":1},'
    '"lookup_proof":"exact_bytes","name_max":255,"node":"project_root",'
    '"path":""}],"mount_id":1,"work_root":null}'
)
REGISTRATION = "a" * 64
SETTLEMENT = "b" * 64


def _halt(txid: str, *, expected: str = APPROVAL_EVIDENCE) -> AssemblyHalt:
    return AssemblyHalt(
        txid=txid,
        reason=AssemblyHaltReason.APPROVAL_EVIDENCE_MISMATCH,
        expected=expected,
        findings=(
            AssemblyFinding(
                path="project_root",
                kind=AssemblyFindingKind.MOUNT_CHANGED,
                observed=(("mount_id", "2"),),
            ),
        ),
        operator_action=AssemblyOperatorAction.RESTORE_APPROVED_TOPOLOGY,
    )


def _insert(store, txid: str, *, effect_id: str = "e1") -> None:
    with store.transaction() as txn:
        txn.insert_record(
            txid,
            one_effect_spec(effect_id=effect_id),
            approval_evidence=APPROVAL_EVIDENCE,
        )


def _terminal(
    store,
    txid: str,
    *,
    state: TransactionState = TransactionState.COMMITTED,
    registration: str = REGISTRATION,
    settlement: str = SETTLEMENT,
) -> None:
    with store.transaction() as txn:
        txn.insert_record(
            txid,
            one_effect_spec(effect_id=f"e-{txid}"),
            approval_evidence=APPROVAL_EVIDENCE,
        )
        txn.set_registration_digest(txid, registration)
        txn.set_transaction_state(txid, state)
        if state is TransactionState.ROLLED_BACK:
            txn.set_rollback_result(txid, RollbackResult.RESTORED)
        txn.set_settlement_digest(txid, settlement)


def _raw_record(
    connection: sqlite3.Connection,
    txid: str,
    *,
    state: TransactionState = TransactionState.PREPARED,
    registration: str | None = None,
    settlement: str | None = None,
    assembly_halt: str | None = None,
    journal: JournalState = JournalState.PENDING,
) -> None:
    connection.execute(
        "INSERT INTO transaction_record "
        "(txid, spec_json, state, committed, rollback_result, halt_diagnostic, "
        "registration_digest, settlement_digest, approval_evidence, assembly_halt) "
        "VALUES (?, ?, ?, 'uncommitted', NULL, NULL, ?, ?, ?, ?)",
        (
            txid,
            canonical_json(one_effect_spec(effect_id=f"e-{txid}")),
            state.value,
            registration,
            settlement,
            APPROVAL_EVIDENCE,
            assembly_halt,
        ),
    )
    connection.execute(
        "INSERT INTO effect (txid, effect_id, variant, journal_state) "
        "VALUES (?, ?, 'create_directory', ?)",
        (txid, f"e-{txid}", journal.value),
    )


def test_registration_digest_is_write_once_even_when_cleared(
    opened_store, store_binding
):
    _insert(opened_store, "tx1")
    with opened_store.transaction() as txn:
        txn.set_registration_digest("tx1", REGISTRATION)

    raw = raw_connect(store_binding)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="registration_digest is write-once"):
            raw.execute(
                "UPDATE transaction_record SET registration_digest = NULL WHERE txid = ?",
                ("tx1",),
            )
        assert raw.execute(
            "SELECT registration_digest FROM transaction_record WHERE txid = ?", ("tx1",)
        ).fetchone() == (REGISTRATION,)
    finally:
        raw.close()


def test_settlement_digest_is_write_once_even_when_cleared(opened_store, store_binding):
    _terminal(opened_store, "tx1")

    raw = raw_connect(store_binding)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="settlement_digest is write-once"):
            raw.execute(
                "UPDATE transaction_record SET settlement_digest = NULL WHERE txid = ?",
                ("tx1",),
            )
        assert raw.execute(
            "SELECT settlement_digest FROM transaction_record WHERE txid = ?", ("tx1",)
        ).fetchone() == (SETTLEMENT,)
    finally:
        raw.close()


def test_approval_evidence_is_write_once(opened_store, store_binding):
    _insert(opened_store, "tx1")

    raw = raw_connect(store_binding)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="approval_evidence is write-once"):
            raw.execute(
                "UPDATE transaction_record SET approval_evidence = ? WHERE txid = ?",
                ('{"changed":true}', "tx1"),
            )
        assert raw.execute(
            "SELECT approval_evidence FROM transaction_record WHERE txid = ?", ("tx1",)
        ).fetchone() == (APPROVAL_EVIDENCE,)
    finally:
        raw.close()


def test_assembly_halt_is_write_once(opened_store, store_binding):
    _insert(opened_store, "tx1")
    with opened_store.transaction() as txn:
        txn.set_assembly_halt("tx1", _halt("tx1"))

    raw = raw_connect(store_binding)
    try:
        raw.execute("DROP TRIGGER trg_assembly_halt_freezes_record")
        with pytest.raises(
            sqlite3.IntegrityError, match="assembly_halt is write-once"
        ):
            raw.execute(
                "UPDATE transaction_record SET assembly_halt = NULL WHERE txid = ?",
                ("tx1",),
            )
        assert raw.execute(
            "SELECT assembly_halt IS NOT NULL FROM transaction_record WHERE txid = ?",
            ("tx1",),
        ).fetchone() == (1,)
    finally:
        raw.close()


@pytest.mark.parametrize(
    ("state", "journal"),
    [
        (TransactionState.APPLYING, JournalState.PENDING),
        (TransactionState.PREPARED, JournalState.STARTED),
    ],
    ids=("after-prepared", "after-journal-start"),
)
def test_registration_is_written_only_in_the_prepared_all_pending_window(
    opened_store, store_binding, state, journal
):
    _insert(opened_store, "legal")
    with opened_store.transaction() as txn:
        txn.set_registration_digest("legal", REGISTRATION)

    raw = raw_connect(store_binding)
    try:
        _raw_record(raw, "illegal", state=state, journal=journal)
        with pytest.raises(sqlite3.IntegrityError, match="registration window is closed"):
            raw.execute(
                "UPDATE transaction_record SET registration_digest = ? WHERE txid = ?",
                ("c" * 64, "illegal"),
            )
        assert raw.execute(
            "SELECT registration_digest FROM transaction_record WHERE txid = ?",
            ("illegal",),
        ).fetchone() == (None,)
    finally:
        raw.close()


@pytest.mark.parametrize(
    "departure",
    [
        TransactionState.APPLYING,
        TransactionState.ROLLING_BACK,
        TransactionState.APPLIED,
        TransactionState.COMMITTED,
        TransactionState.ROLLED_BACK,
        TransactionState.HALTED,
    ],
)
def test_every_departure_from_prepared_requires_registration(opened_store, departure):
    _insert(opened_store, "legal")
    with opened_store.transaction() as txn:
        txn.set_registration_digest("legal", REGISTRATION)
        txn.set_transaction_state("legal", TransactionState.APPLYING)

    _insert(opened_store, "illegal")
    with pytest.raises(
        sqlite3.IntegrityError, match="departure requires registration"
    ), opened_store.transaction() as txn:
        txn.set_transaction_state("illegal", departure)


@pytest.mark.parametrize(
    ("state", "registration"),
    [
        (TransactionState.PREPARED, REGISTRATION),
        (TransactionState.APPLYING, None),
    ],
    ids=("not-applying", "not-registered"),
)
def test_journal_start_requires_applying_and_registration(
    opened_store, store_binding, state, registration
):
    _insert(opened_store, "legal")
    with opened_store.transaction() as txn:
        txn.set_registration_digest("legal", REGISTRATION)
        txn.set_transaction_state("legal", TransactionState.APPLYING)
        txn.set_journal_state("legal", "e1", JournalState.STARTED)

    raw = raw_connect(store_binding)
    try:
        _raw_record(
            raw,
            "illegal",
            state=state,
            registration=None if registration is None else "c" * 64,
        )
        with pytest.raises(sqlite3.IntegrityError, match="journal start requires"):
            raw.execute(
                "UPDATE effect SET journal_state = ? WHERE txid = ?",
                (JournalState.STARTED.value, "illegal"),
            )
        assert raw.execute(
            "SELECT journal_state FROM effect WHERE txid = ?", ("illegal",)
        ).fetchone() == (JournalState.PENDING.value,)
    finally:
        raw.close()


@pytest.mark.parametrize(
    ("state", "registration"),
    [
        (TransactionState.COMMITTED, None),
        (TransactionState.PREPARED, REGISTRATION),
    ],
    ids=("not-registered", "not-terminal"),
)
def test_settlement_requires_a_registered_terminal_record(
    opened_store, store_binding, state, registration
):
    _terminal(opened_store, "legal")

    raw = raw_connect(store_binding)
    try:
        _raw_record(
            raw,
            "illegal",
            state=state,
            registration=None if registration is None else "c" * 64,
        )
        with pytest.raises(sqlite3.IntegrityError, match="settlement requires"):
            raw.execute(
                "UPDATE transaction_record SET settlement_digest = ? WHERE txid = ?",
                ("c" * 64, "illegal"),
            )
        assert raw.execute(
            "SELECT settlement_digest FROM transaction_record WHERE txid = ?",
            ("illegal",),
        ).fetchone() == (None,)
    finally:
        raw.close()


def test_active_rows_cannot_be_updated_even_between_bound_terminal_records(
    opened_store, store_binding
):
    _terminal(opened_store, "tx1", registration="1" * 64, settlement="2" * 64)
    _terminal(opened_store, "tx2", registration="3" * 64, settlement="4" * 64)
    with opened_store.transaction() as txn:
        txn.set_active("tx1")

    raw = raw_connect(store_binding)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="active is never updated"):
            raw.execute("UPDATE active SET txid = ?", ("tx2",))
        assert raw.execute("SELECT txid FROM active").fetchone() == ("tx1",)
    finally:
        raw.close()


def test_publishing_over_an_existing_active_row_hits_the_primary_key(opened_store):
    _terminal(opened_store, "tx1", registration="1" * 64, settlement="2" * 64)
    _terminal(opened_store, "tx2", registration="3" * 64, settlement="4" * 64)
    with opened_store.transaction() as txn:
        txn.set_active("tx1")

    with pytest.raises(
        sqlite3.IntegrityError, match="active.singleton"
    ), opened_store.transaction() as txn:
        txn.set_active("tx2")


def test_active_delete_requires_a_bound_unhalted_terminal_record(
    opened_store,
):
    _terminal(opened_store, "legal")
    with opened_store.transaction() as txn:
        txn.set_active("legal")
        txn.set_active(None)
    assert opened_store.read_active() is None

    _insert(opened_store, "nonterminal")
    with opened_store.transaction() as txn:
        txn.set_active("nonterminal")
    with pytest.raises(
        sqlite3.IntegrityError, match="active delete requires"
    ), opened_store.transaction() as txn:
        txn.set_active(None)


def test_an_active_row_without_a_record_cannot_be_deleted(opened_store, store_binding):
    raw = raw_connect(store_binding)
    try:
        raw.execute("INSERT INTO active (singleton, txid) VALUES (0, 'ghost')")
        with pytest.raises(sqlite3.IntegrityError, match="active delete requires"):
            raw.execute("DELETE FROM active")
        assert raw.execute("SELECT txid FROM active").fetchone() == ("ghost",)
    finally:
        raw.close()


def test_record_delete_requires_a_detached_bound_unhalted_terminal_record(
    opened_store, store_binding
):
    _terminal(opened_store, "legal")
    _insert(opened_store, "nonterminal")

    raw = raw_connect(store_binding)
    try:
        raw.execute("DELETE FROM effect WHERE txid = ?", ("legal",))
        raw.execute("DELETE FROM transaction_record WHERE txid = ?", ("legal",))
        assert raw.execute(
            "SELECT count(*) FROM transaction_record WHERE txid = ?", ("legal",)
        ).fetchone() == (0,)

        raw.execute("DELETE FROM effect WHERE txid = ?", ("nonterminal",))
        with pytest.raises(sqlite3.IntegrityError, match="record delete requires"):
            raw.execute("DELETE FROM transaction_record WHERE txid = ?", ("nonterminal",))
        assert raw.execute(
            "SELECT count(*) FROM transaction_record WHERE txid = ?", ("nonterminal",)
        ).fetchone() == (1,)
    finally:
        raw.close()


def test_assembly_halt_freezes_every_record_update(opened_store):
    _insert(opened_store, "tx1")
    with opened_store.transaction() as txn:
        txn.set_assembly_halt("tx1", _halt("tx1"))

    with pytest.raises(
        sqlite3.IntegrityError, match="assembly_halt freezes record"
    ), opened_store.transaction() as txn:
        txn.set_commit_decision("tx1", CommitDecision.COMMITTED)


def test_assembly_halt_freezes_every_journal_update(opened_store):
    _insert(opened_store, "legal", effect_id="legal-effect")
    with opened_store.transaction() as txn:
        txn.set_journal_state("legal", "legal-effect", JournalState.DONE)

    _insert(opened_store, "halted", effect_id="halted-effect")
    with opened_store.transaction() as txn:
        txn.set_assembly_halt("halted", _halt("halted"))
    with pytest.raises(
        sqlite3.IntegrityError, match="assembly_halt freezes journal"
    ), opened_store.transaction() as txn:
        txn.set_journal_state("halted", "halted-effect", JournalState.DONE)


def test_schema_v2_has_the_new_columns_and_complete_trigger_set(
    opened_store, store_binding
):
    assert SCHEMA_VERSION == 2
    raw = raw_connect(store_binding)
    try:
        columns = {row[1]: (row[2], row[3]) for row in raw.execute(
            "PRAGMA table_info(transaction_record)"
        )}
        triggers = {row[0] for row in raw.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'trigger'"
        )}
    finally:
        raw.close()
    assert columns["registration_digest"] == ("TEXT", 0)
    assert columns["settlement_digest"] == ("TEXT", 0)
    assert columns["approval_evidence"] == ("TEXT", 1)
    assert columns["assembly_halt"] == ("TEXT", 0)
    assert {
        "trg_registration_write_once",
        "trg_settlement_write_once",
        "trg_evidence_write_once",
        "trg_assembly_halt_write_once",
        "trg_registration_window",
        "trg_departure_needs_registration",
        "trg_journal_start_gate",
        "trg_settlement_gate",
        "trg_active_no_update",
        "trg_active_delete_gate",
        "trg_record_delete_gate",
        "trg_assembly_halt_freezes_record",
        "trg_assembly_halt_freezes_journal",
    } <= triggers


def test_digest_writers_and_assembly_halt_round_trip_through_stored_record(
    opened_store,
):
    halt = _halt("tx1")
    with opened_store.transaction() as txn:
        txn.insert_record(
            "tx1", one_effect_spec(), approval_evidence=APPROVAL_EVIDENCE
        )
        txn.set_registration_digest("tx1", REGISTRATION)
        txn.set_transaction_state("tx1", TransactionState.COMMITTED)
        txn.set_settlement_digest("tx1", SETTLEMENT)
        txn.set_assembly_halt("tx1", halt)

    record = opened_store.read_record("tx1")
    assert record is not None
    assert record.registration_digest == REGISTRATION
    assert record.settlement_digest == SETTLEMENT
    assert record.approval_evidence == APPROVAL_EVIDENCE
    assert record.assembly_halt == halt


@pytest.mark.parametrize(
    ("method", "value"),
    [
        ("set_registration_digest", 3),
        ("set_settlement_digest", b"digest"),
        ("set_assembly_halt", object()),
    ],
)
def test_new_writers_refuse_wrong_exact_types_inside_the_transaction(
    opened_store, method, value
):
    _insert(opened_store, "tx1")
    with pytest.raises(
        ProtocolError, match="exactly"
    ), opened_store.transaction() as txn:
        getattr(txn, method)("tx1", value)


@pytest.mark.parametrize(
    ("method", "value"),
    [
        ("set_registration_digest", REGISTRATION),
        ("set_settlement_digest", SETTLEMENT),
        ("set_assembly_halt", _halt("ghost")),
    ],
)
def test_new_writers_refuse_an_unknown_record(opened_store, method, value):
    with pytest.raises(
        ProtocolError, match="ghost"
    ), opened_store.transaction() as txn:
        getattr(txn, method)("ghost", value)


@pytest.mark.parametrize("column", ["registration_digest", "settlement_digest"])
def test_digest_columns_are_unique(opened_store, store_binding, column):
    raw = raw_connect(store_binding)
    try:
        _raw_record(
            raw,
            "tx1",
            state=TransactionState.COMMITTED,
            registration="c" * 64,
            settlement="d" * 64,
        )
        _raw_record(
            raw,
            "tx2",
            state=(
                TransactionState.PREPARED
                if column == "registration_digest"
                else TransactionState.COMMITTED
            ),
            registration=None if column == "registration_digest" else "e" * 64,
            settlement=None,
        )
        value = raw.execute(
            f"SELECT {column} FROM transaction_record WHERE txid = ?", ("tx1",)
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match=f"transaction_record.{column}"):
            raw.execute(
                f"UPDATE transaction_record SET {column} = ? WHERE txid = ?",
                (value, "tx2"),
            )
    finally:
        raw.close()


def test_a_malformed_stored_assembly_halt_fails_closed(opened_store, store_binding):
    _insert(opened_store, "tx1")
    raw = raw_connect(store_binding)
    try:
        raw.execute(
            "UPDATE transaction_record SET assembly_halt = ? WHERE txid = ?",
            ("{}", "tx1"),
        )
    finally:
        raw.close()
    with pytest.raises(MetadataStoreInvalid, match="assembly halt"):
        opened_store.read_record("tx1")


@pytest.mark.parametrize(
    "halt",
    [
        _halt("another-txid"),
        _halt("tx1", expected='{"different":true}'),
    ],
    ids=("different-txid", "different-approval-evidence"),
)
def test_assembly_halt_writer_requires_the_records_exact_bindings(opened_store, halt):
    _insert(opened_store, "tx1")

    with pytest.raises(
        ProtocolError, match="assembly halt.*record"
    ), opened_store.transaction() as txn:
        txn.set_assembly_halt("tx1", halt)

    record = opened_store.read_record("tx1")
    assert record is not None
    assert record.assembly_halt is None


@pytest.mark.parametrize(
    "halt",
    [
        _halt("another-txid"),
        _halt("tx1", expected='{"different":true}'),
    ],
    ids=("different-txid", "different-approval-evidence"),
)
def test_hostile_stored_assembly_halt_binding_fails_closed(
    opened_store, store_binding, halt
):
    _insert(opened_store, "tx1")
    raw = raw_connect(store_binding)
    try:
        raw.execute(
            "UPDATE transaction_record SET assembly_halt = ? WHERE txid = ?",
            (encode_assembly_halt(halt), "tx1"),
        )
    finally:
        raw.close()

    with pytest.raises(MetadataStoreInvalid, match="assembly halt.*record"):
        opened_store.read_record("tx1")


def test_active_publication_has_no_upsert_path():
    from atoms.store import connection

    tree = ast.parse(inspect.getsource(connection))
    assert not any(
        isinstance(node, ast.Name) and node.id == "UPSERT_ACTIVE"
        for node in ast.walk(tree)
    )


def test_approval_evidence_has_the_exact_canonical_wire_shape(approval_context):
    from atoms.fs.approval import approve_for_project, encode_approval_evidence

    compiled = compiled_for(
        CreateFileNoClobber("e1", "d/f.txt", file_state())
    )
    with approval_context() as (context, binding):
        os.mkdir("d", dir_fd=binding.project_root_fd)
        root = os.fstat(binding.project_root_fd)
        child = os.stat("d", dir_fd=binding.project_root_fd)
        approved = approve_for_project(compiled, context)
        expected = (
            '{"directories":['
            f'{{"identity":{{"st_dev":{root.st_dev},"st_ino":{root.st_ino}}},'
            '"lookup_proof":"exact_bytes","name_max":255,"node":"project_root","path":""},'
            f'{{"identity":{{"st_dev":{child.st_dev},"st_ino":{child.st_ino}}},'
            '"lookup_proof":"exact_bytes","name_max":255,'
            '"node":"topology_directory:0","path":"d"}],'
            f'"mount_id":{binding.evidence.mount_id},"work_root":null}}'
        )
        encoded = encode_approval_evidence(approved)

    assert encoded == expected
    assert json.dumps(
        json.loads(encoded), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ) == encoded


def test_approval_evidence_is_stable_until_a_directory_identity_changes(
    approval_context,
):
    from atoms.fs.approval import approve_for_project, encode_approval_evidence

    compiled = compiled_for(
        CreateFileNoClobber("e1", "d/f.txt", file_state())
    )
    with approval_context() as (context, binding):
        os.mkdir("d", dir_fd=binding.project_root_fd)
        first = encode_approval_evidence(approve_for_project(compiled, context))
        second = encode_approval_evidence(approve_for_project(compiled, context))
        os.rename(
            "d",
            "old-d",
            src_dir_fd=binding.project_root_fd,
            dst_dir_fd=binding.project_root_fd,
        )
        os.mkdir("d", dir_fd=binding.project_root_fd)
        replaced = encode_approval_evidence(approve_for_project(compiled, context))

    assert first == second
    assert replaced != first


def test_approval_evidence_carries_the_physical_work_root_facts(approval_context):
    from atoms.fs.approval import approve_for_project, encode_approval_evidence

    compiled = compiled_for(
        CreateDirectory("e1", "d", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e2", "d/f.txt", file_state()),
    )
    with approval_context() as (context, binding):
        work = os.stat("work", dir_fd=binding.metadata_root_fd)
        encoded = encode_approval_evidence(approve_for_project(compiled, context))

    evidence = json.loads(encoded)
    assert evidence["work_root"] == {
        "identity": {"st_dev": work.st_dev, "st_ino": work.st_ino},
        "lookup_proof": "exact_bytes",
        "name_max": 255,
    }
    work_entry = next(
        item for item in evidence["directories"] if item["node"] == "work_root"
    )
    assert work_entry["identity"] is None
    assert work_entry["path"] is None
    planned_entry = next(
        item for item in evidence["directories"] if item["node"] == "persistent:d"
    )
    assert planned_entry["identity"] is None
    assert planned_entry["path"] == "d"
    nodes = [item["node"] for item in evidence["directories"]]
    assert nodes == ["persistent:d", "project_root", "work_root"]


def test_approval_evidence_refuses_an_unknown_node_type(approval_context):
    from atoms.fs.approval import approve_for_project, encode_approval_evidence
    from atoms.fs.lookup import DirectoryConstraints, LookupProof
    from atoms.fs.topology import ApprovedPlannedDirectory

    compiled = compiled_for(
        CreateDirectory("e1", "d", DirectoryState(mode=0o755))
    )
    with approval_context() as (context, _binding):
        approved = approve_for_project(compiled, context)
        object.__setattr__(
            approved,
            "directories",
            (
                ApprovedPlannedDirectory(
                    node=object(),  # type: ignore[arg-type]
                    constraints=DirectoryConstraints(
                        lookup_proof=LookupProof.EXACT_BYTES, name_max=255
                    ),
                ),
            ),
        )
        with pytest.raises(ProtocolError, match="topology node"):
            encode_approval_evidence(approved)


@pytest.mark.parametrize("bad", [True, "41"], ids=("bool", "string"))
@pytest.mark.parametrize(
    ("member", "attribute"),
    [
        ("st_dev", "device"),
        ("st_ino", "inode"),
        ("mount_id", "mount_id"),
        ("name_max", "name_max"),
        ("node_id", "node_id"),
    ],
)
def test_approval_evidence_refuses_non_integer_numeric_facts(
    approval_context, member, attribute, bad
):
    from atoms.core.recovery import TopologyDirectory
    from atoms.fs.approval import approve_for_project, encode_approval_evidence
    from atoms.fs.topology import ApprovedExistingDirectory

    compiled = compiled_for(
        CreateFileNoClobber("e1", "d/f.txt", file_state())
    )
    with approval_context() as (context, binding):
        os.mkdir("d", dir_fd=binding.project_root_fd)
        approved = approve_for_project(compiled, context)
        directory = next(
            entry
            for entry in approved.directories
            if type(entry.node) is TopologyDirectory
        )
        assert type(directory) is ApprovedExistingDirectory

        if member in {"st_dev", "st_ino"}:
            target = directory.identity
        elif member == "mount_id":
            target = approved.binding.evidence
        elif member == "name_max":
            target = directory.constraints
        else:
            target = directory.node
        object.__setattr__(target, attribute, bad)

        with pytest.raises(ProtocolError, match=member):
            encode_approval_evidence(approved)
