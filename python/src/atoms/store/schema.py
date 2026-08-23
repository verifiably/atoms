"""The durable schema and the facts generated from A1/A3's enums (design §6).

Pure: no sqlite3 import, no connection, no I/O. Everything here is either a constant or a
function of an enum, so a new enum member changes the DDL rather than diverging from it.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import TypeVar

from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    Effect,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.errors import ProtocolError
from atoms.core.recovery.model import (
    CommitDecision,
    JournalState,
    RollbackResult,
    TransactionState,
)
from atoms.core.recovery.plan import EffectVariant

SCHEMA_VERSION = 3
APPLICATION_ID = int.from_bytes(b"atms", "big")

_EnumT = TypeVar("_EnumT", bound=Enum)


def check_list(members: type[_EnumT]) -> str:
    """Render an enum as the value list of a CHECK constraint.

    Sorted so the rendered DDL is a function of the member set alone: a reordering of the
    enum's declaration must not change the stored schema text, because the catalog
    comparison at reopen is exact.
    """
    return ", ".join(f"'{member.value}'" for member in sorted(members, key=lambda m: m.value))


V2_SCHEMA_STATEMENTS: tuple[str, ...] = (
    f"""CREATE TABLE transaction_record (
    txid                TEXT PRIMARY KEY,
    spec_json           TEXT NOT NULL,
    state               TEXT NOT NULL CHECK (state IN ({check_list(TransactionState)})),
    committed           TEXT NOT NULL CHECK (committed IN ({check_list(CommitDecision)})),
    rollback_result     TEXT          CHECK (rollback_result IN ({check_list(RollbackResult)})),
    halt_diagnostic     TEXT,
    registration_digest TEXT UNIQUE,
    settlement_digest   TEXT UNIQUE,
    approval_evidence   TEXT NOT NULL,
    assembly_halt       TEXT
) STRICT;""",
    f"""CREATE TABLE effect (
    txid          TEXT NOT NULL REFERENCES transaction_record(txid),
    effect_id     TEXT NOT NULL,
    variant       TEXT NOT NULL CHECK (variant IN ({check_list(EffectVariant)})),
    journal_state TEXT NOT NULL CHECK (journal_state IN ({check_list(JournalState)})),
    PRIMARY KEY (txid, effect_id)
) STRICT;""",
    """CREATE TABLE blob (
    digest   TEXT PRIMARY KEY,
    byte_len INTEGER NOT NULL CHECK (byte_len >= 0)
) STRICT;""",
    """CREATE TABLE active (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 0),
    txid      TEXT NOT NULL REFERENCES transaction_record(txid)
) STRICT;""",
    """CREATE TRIGGER transaction_record_spec_json_is_write_once
BEFORE UPDATE OF spec_json ON transaction_record
BEGIN
    SELECT RAISE(ABORT, 'spec_json is write-once');
END;""",
    """CREATE TRIGGER trg_registration_write_once
BEFORE UPDATE OF registration_digest ON transaction_record
WHEN OLD.registration_digest IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'registration_digest is write-once');
END;""",
    """CREATE TRIGGER trg_settlement_write_once
BEFORE UPDATE OF settlement_digest ON transaction_record
WHEN OLD.settlement_digest IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'settlement_digest is write-once');
END;""",
    """CREATE TRIGGER trg_evidence_write_once
BEFORE UPDATE OF approval_evidence ON transaction_record
BEGIN
    SELECT RAISE(ABORT, 'approval_evidence is write-once');
END;""",
    """CREATE TRIGGER trg_assembly_halt_write_once
BEFORE UPDATE OF assembly_halt ON transaction_record
WHEN OLD.assembly_halt IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'assembly_halt is write-once');
END;""",
    f"""CREATE TRIGGER trg_registration_window
BEFORE UPDATE OF registration_digest ON transaction_record
WHEN NEW.registration_digest IS NOT NULL AND (
    OLD.state != '{TransactionState.PREPARED.value}'
    OR EXISTS (
        SELECT 1 FROM effect e
        WHERE e.txid = OLD.txid
            AND e.journal_state != '{JournalState.PENDING.value}'
    )
)
BEGIN
    SELECT RAISE(ABORT, 'registration window is closed');
END;""",
    f"""CREATE TRIGGER trg_departure_needs_registration
BEFORE UPDATE OF state ON transaction_record
WHEN OLD.state = '{TransactionState.PREPARED.value}'
    AND NEW.state != '{TransactionState.PREPARED.value}'
    AND OLD.registration_digest IS NULL
BEGIN
    SELECT RAISE(ABORT, 'departure requires registration');
END;""",
    f"""CREATE TRIGGER trg_journal_start_gate
BEFORE UPDATE OF journal_state ON effect
WHEN NEW.journal_state = '{JournalState.STARTED.value}'
    AND OLD.journal_state = '{JournalState.PENDING.value}'
    AND EXISTS (
        SELECT 1 FROM transaction_record t
        WHERE t.txid = NEW.txid
            AND (t.state != '{TransactionState.APPLYING.value}'
                OR t.registration_digest IS NULL)
    )
BEGIN
    SELECT RAISE(ABORT, 'journal start requires applying and registration');
END;""",
    f"""CREATE TRIGGER trg_settlement_gate
BEFORE UPDATE OF settlement_digest ON transaction_record
WHEN NEW.settlement_digest IS NOT NULL AND (
    OLD.registration_digest IS NULL
    OR OLD.state NOT IN (
        '{TransactionState.COMMITTED.value}',
        '{TransactionState.ROLLED_BACK.value}'
    )
)
BEGIN
    SELECT RAISE(ABORT, 'settlement requires a registered terminal record');
END;""",
    """CREATE TRIGGER trg_active_no_update
BEFORE UPDATE ON active
BEGIN
    SELECT RAISE(ABORT, 'active is never updated');
END;""",
    f"""CREATE TRIGGER trg_active_delete_gate
BEFORE DELETE ON active
WHEN NOT EXISTS (
    SELECT 1 FROM transaction_record t
    WHERE t.txid = OLD.txid
        AND t.state IN (
            '{TransactionState.COMMITTED.value}',
            '{TransactionState.ROLLED_BACK.value}'
        )
        AND t.registration_digest IS NOT NULL
        AND t.settlement_digest IS NOT NULL
        AND t.assembly_halt IS NULL
)
BEGIN
    SELECT RAISE(ABORT, 'active delete requires a bound unhalted terminal record');
END;""",
    f"""CREATE TRIGGER trg_record_delete_gate
BEFORE DELETE ON transaction_record
WHEN OLD.state NOT IN (
        '{TransactionState.COMMITTED.value}',
        '{TransactionState.ROLLED_BACK.value}'
    )
    OR OLD.registration_digest IS NULL
    OR OLD.settlement_digest IS NULL
    OR OLD.assembly_halt IS NOT NULL
    OR EXISTS (SELECT 1 FROM active WHERE active.txid = OLD.txid)
BEGIN
    SELECT RAISE(ABORT, 'record delete requires a detached bound unhalted terminal record');
END;""",
    """CREATE TRIGGER trg_assembly_halt_freezes_record
BEFORE UPDATE ON transaction_record
WHEN OLD.assembly_halt IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'assembly_halt freezes record');
END;""",
    """CREATE TRIGGER trg_assembly_halt_freezes_journal
BEFORE UPDATE OF journal_state ON effect
WHEN EXISTS (
    SELECT 1 FROM transaction_record t
    WHERE t.txid = NEW.txid AND t.assembly_halt IS NOT NULL
)
BEGIN
    SELECT RAISE(ABORT, 'assembly_halt freezes journal');
END;""",
)


ROOT_LIFECYCLE_V3_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE root_lifecycle (
    singleton  INTEGER PRIMARY KEY CHECK (singleton = 0),
    state      TEXT NOT NULL CHECK (state IN (
        'writable', 'read-only-serviceable', 'read-only-unserviceable'
    )),
    machine_id TEXT NOT NULL,
    root_path  TEXT NOT NULL,
    origin     TEXT NOT NULL CHECK (origin IN (
        'register', 'replicate', 'fork',
        'read-serviceability', 'migration-v2'
    )),
    CHECK (length(machine_id) = 32
        AND machine_id NOT GLOB '*[^0-9a-f]*'
        AND machine_id != '00000000000000000000000000000000'),
    CHECK (length(root_path) > 0),
    CHECK (
        (origin IN ('register', 'fork') AND state IN (
            'read-only-unserviceable', 'writable'
        ))
        OR (origin = 'replicate' AND state IN (
            'read-only-unserviceable', 'read-only-serviceable'
        ))
        OR (origin = 'read-serviceability'
            AND state = 'read-only-serviceable')
        OR (origin = 'migration-v2' AND state = 'writable')
    )
) STRICT;""",
    """CREATE TABLE root_operation (
    singleton                 INTEGER PRIMARY KEY CHECK (singleton = 0),
    operation_id              TEXT NOT NULL UNIQUE,
    kind                      TEXT NOT NULL CHECK (kind IN (
        'register', 'replicate', 'fork'
    )),
    phase                     TEXT NOT NULL CHECK (phase IN (
        'recorded', 'source-snapshot-durable',
        'tree-durable', 'complete'
    )),
    request_json              TEXT NOT NULL,
    request_hash              TEXT NOT NULL,
    source_snapshot_json      TEXT,
    destination_snapshot_json TEXT,
    genesis_digest            TEXT,
    CHECK (length(operation_id) = 32
        AND operation_id NOT GLOB '*[^0-9a-f]*'),
    CHECK (length(request_hash) = 64
        AND request_hash NOT GLOB '*[^0-9a-f]*'),
    CHECK (genesis_digest IS NULL OR (
        length(genesis_digest) = 64
        AND genesis_digest NOT GLOB '*[^0-9a-f]*'
    )),
    CHECK (
        (kind = 'register'
            AND source_snapshot_json IS NULL
            AND (
                (phase = 'recorded'
                    AND destination_snapshot_json IS NULL
                    AND genesis_digest IS NULL)
                OR (phase IN ('tree-durable', 'complete')
                    AND destination_snapshot_json IS NOT NULL
                    AND genesis_digest IS NOT NULL)
            ))
        OR (kind = 'replicate'
            AND genesis_digest IS NULL
            AND (
                (phase = 'recorded'
                    AND source_snapshot_json IS NULL
                    AND destination_snapshot_json IS NULL)
                OR (phase = 'source-snapshot-durable'
                    AND source_snapshot_json IS NOT NULL
                    AND destination_snapshot_json IS NULL)
                OR (phase IN ('tree-durable', 'complete')
                    AND source_snapshot_json IS NOT NULL
                    AND destination_snapshot_json IS NOT NULL)
            ))
        OR (kind = 'fork' AND (
            (phase = 'recorded'
                AND source_snapshot_json IS NULL
                AND destination_snapshot_json IS NULL
                AND genesis_digest IS NULL)
            OR (phase = 'source-snapshot-durable'
                AND source_snapshot_json IS NOT NULL
                AND destination_snapshot_json IS NULL
                AND genesis_digest IS NULL)
            OR (phase IN ('tree-durable', 'complete')
                AND source_snapshot_json IS NOT NULL
                AND destination_snapshot_json IS NOT NULL
                AND genesis_digest IS NOT NULL)
        ))
    )
) STRICT;""",
    """CREATE TRIGGER trg_root_lifecycle_insert_gate
BEFORE INSERT ON root_lifecycle
WHEN NOT (
    (NEW.origin IN ('register', 'replicate', 'fork')
        AND NEW.state = 'read-only-unserviceable'
        AND EXISTS (
            SELECT 1 FROM root_operation
            WHERE singleton = 0
                AND kind = NEW.origin
                AND phase = 'recorded'
        ))
    OR (NEW.origin = 'read-serviceability'
        AND NEW.state = 'read-only-serviceable'
        AND NOT EXISTS (SELECT 1 FROM root_operation))
    OR (NEW.origin = 'migration-v2'
        AND NEW.state = 'writable'
        AND NOT EXISTS (SELECT 1 FROM root_operation))
)
BEGIN
    SELECT RAISE(ABORT, 'root lifecycle insert lacks initial operation state');
END;""",
    """CREATE TRIGGER trg_root_lifecycle_identity_write_once
BEFORE UPDATE OF machine_id, root_path, origin ON root_lifecycle
WHEN NEW.machine_id IS NOT OLD.machine_id
    OR NEW.root_path IS NOT OLD.root_path
    OR NEW.origin IS NOT OLD.origin
BEGIN
    SELECT RAISE(ABORT, 'root lifecycle binding and origin are write-once');
END;""",
    """CREATE TRIGGER trg_root_lifecycle_transition
BEFORE UPDATE OF state ON root_lifecycle
WHEN NOT (
    NEW.state = OLD.state
    OR (OLD.state = 'read-only-unserviceable'
        AND NEW.state = 'read-only-serviceable'
        AND NOT EXISTS (
            SELECT 1 FROM root_operation WHERE phase != 'complete'
        ))
    OR (OLD.state = 'read-only-unserviceable'
        AND NEW.state = 'writable'
        AND OLD.origin IN ('register', 'fork')
        AND EXISTS (
            SELECT 1 FROM root_operation
            WHERE kind = OLD.origin AND phase = 'tree-durable'
        ))
)
BEGIN
    SELECT RAISE(ABORT, 'illegal root lifecycle transition');
END;""",
    """CREATE TRIGGER trg_root_lifecycle_no_delete
BEFORE DELETE ON root_lifecycle
BEGIN
    SELECT RAISE(ABORT, 'root lifecycle is retained');
END;""",
    """CREATE TRIGGER trg_root_operation_insert_gate
BEFORE INSERT ON root_operation
WHEN NEW.phase != 'recorded'
    OR EXISTS (SELECT 1 FROM root_lifecycle)
BEGIN
    SELECT RAISE(ABORT, 'root operation insert requires empty lifecycle and recorded phase');
END;""",
    """CREATE TRIGGER trg_root_operation_identity_write_once
BEFORE UPDATE OF operation_id, kind, request_json, request_hash ON root_operation
WHEN NEW.operation_id IS NOT OLD.operation_id
    OR NEW.kind IS NOT OLD.kind
    OR NEW.request_json IS NOT OLD.request_json
    OR NEW.request_hash IS NOT OLD.request_hash
BEGIN
    SELECT RAISE(ABORT, 'root operation identity is write-once');
END;""",
    """CREATE TRIGGER trg_root_operation_proof_write_once
BEFORE UPDATE OF source_snapshot_json,
    destination_snapshot_json, genesis_digest ON root_operation
WHEN (OLD.source_snapshot_json IS NOT NULL
        AND NEW.source_snapshot_json IS NOT OLD.source_snapshot_json)
    OR (OLD.destination_snapshot_json IS NOT NULL
        AND NEW.destination_snapshot_json IS NOT OLD.destination_snapshot_json)
    OR (OLD.genesis_digest IS NOT NULL
        AND NEW.genesis_digest IS NOT OLD.genesis_digest)
BEGIN
    SELECT RAISE(ABORT, 'root operation proof fields are write-once');
END;""",
    """CREATE TRIGGER trg_root_operation_phase_transition
BEFORE UPDATE OF phase ON root_operation
WHEN NOT (
    (OLD.phase = 'recorded'
        AND NEW.phase = 'source-snapshot-durable'
        AND OLD.kind IN ('replicate', 'fork'))
    OR (OLD.phase = 'recorded'
        AND NEW.phase = 'tree-durable'
        AND OLD.kind = 'register')
    OR (OLD.phase = 'source-snapshot-durable'
        AND NEW.phase = 'tree-durable')
    OR (OLD.phase = 'tree-durable' AND NEW.phase = 'complete')
)
BEGIN
    SELECT RAISE(ABORT, 'illegal root operation phase transition');
END;""",
    """CREATE TRIGGER trg_root_operation_complete_gate
BEFORE UPDATE OF phase ON root_operation
WHEN NEW.phase = 'complete' AND NOT EXISTS (
    SELECT 1 FROM root_lifecycle
    WHERE singleton = 0
        AND origin = NEW.kind
        AND state = CASE NEW.kind
            WHEN 'replicate' THEN 'read-only-unserviceable'
            ELSE 'writable'
        END
)
BEGIN
    SELECT RAISE(ABORT, 'root operation completion lacks lifecycle state');
END;""",
    """CREATE TRIGGER trg_root_operation_no_delete
BEFORE DELETE ON root_operation
BEGIN
    SELECT RAISE(ABORT, 'root operation is retained');
END;""",
)
"""Design 2026-08-23 §5, verbatim: the DDL is the contract, including trigger
names and error strings. Version 3 is exactly the frozen v2 statements plus
these, in this order."""

SCHEMA_STATEMENTS: tuple[str, ...] = (
    *V2_SCHEMA_STATEMENTS,
    *ROOT_LIFECYCLE_V3_STATEMENTS,
)


def _catalog_row(statement: str) -> tuple[str, str, str, str]:
    """Derive one (type, name, tbl_name, sql) row from a DDL statement.

    `sql` is the statement with its terminal semicolon stripped, because that is what
    SQLite stores -- measured. Deriving the expected catalog from the same text the store
    was created from is what makes the comparison a schema check rather than a
    transcription check.
    """
    body = statement.strip().rstrip(";")
    head = body.split("(", 1)[0].split()
    kind = head[1].lower()
    if kind == "trigger":
        name = head[2]
        table = body.split(" ON ", 1)[1].split()[0]
        return ("trigger", name, table, body)
    name = head[2]
    return ("table", name, name, body)


_V2_AUTOINDEXES: tuple[tuple[str, str, str, None], ...] = (
    ("index", "sqlite_autoindex_transaction_record_1", "transaction_record", None),
    ("index", "sqlite_autoindex_transaction_record_2", "transaction_record", None),
    ("index", "sqlite_autoindex_transaction_record_3", "transaction_record", None),
    ("index", "sqlite_autoindex_effect_1", "effect", None),
    ("index", "sqlite_autoindex_blob_1", "blob", None),
)

V2_EXPECTED_CATALOG: frozenset[tuple[str, str, str, str | None]] = frozenset(
    [_catalog_row(statement) for statement in V2_SCHEMA_STATEMENTS]
    + list(_V2_AUTOINDEXES)
)
"""The pre-lifecycle store, frozen: `migrate_root_to_lifecycle_v3` proves this
exact catalog before its writable open, and the read-only classifier reads an
exact match as read-only unserviceable."""

EXPECTED_CATALOG: frozenset[tuple[str, str, str, str | None]] = frozenset(
    [_catalog_row(statement) for statement in SCHEMA_STATEMENTS]
    + list(_V2_AUTOINDEXES)
    + [("index", "sqlite_autoindex_root_operation_1", "root_operation", None)]
)

EFFECT_VARIANTS: Mapping[type[Effect], EffectVariant] = {
    ReplaceFile: EffectVariant.REPLACE_FILE,
    CreateFileNoClobber: EffectVariant.CREATE_FILE_NO_CLOBBER,
    DeletePath: EffectVariant.DELETE_PATH,
    MoveNoClobber: EffectVariant.MOVE_NO_CLOBBER,
    CreateDirectory: EffectVariant.CREATE_DIRECTORY,
}


def variant_of(effect: Effect) -> EffectVariant:
    """The `effect.variant` column value for one effect.

    Lookup is by `type(effect)`, exact -- not isinstance, which would accept a subclass
    and record it as its base. `variant_name` is deliberately unused: it returns
    `type(effect).__name__` ('ReplaceFile') while the column accepts EffectVariant's
    values ('replace_file'), and deriving one from the other by case transformation would
    make the stored value depend on a class name with no reason to keep matching.
    """
    variant = EFFECT_VARIANTS.get(type(effect))
    if variant is None:
        raise ProtocolError(
            f"{type(effect).__name__} has no EffectVariant; add it to EFFECT_VARIANTS "
            "in atoms/store/schema.py beside the CHECK list it must agree with"
        )
    return variant
