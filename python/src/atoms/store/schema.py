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

SCHEMA_VERSION = 2
APPLICATION_ID = int.from_bytes(b"atms", "big")

_EnumT = TypeVar("_EnumT", bound=Enum)


def check_list(members: type[_EnumT]) -> str:
    """Render an enum as the value list of a CHECK constraint.

    Sorted so the rendered DDL is a function of the member set alone: a reordering of the
    enum's declaration must not change the stored schema text, because the catalog
    comparison at reopen is exact.
    """
    return ", ".join(f"'{member.value}'" for member in sorted(members, key=lambda m: m.value))


SCHEMA_STATEMENTS: tuple[str, ...] = (
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


EXPECTED_CATALOG: frozenset[tuple[str, str, str, str | None]] = frozenset(
    [_catalog_row(statement) for statement in SCHEMA_STATEMENTS]
    + [
        ("index", "sqlite_autoindex_transaction_record_1", "transaction_record", None),
        ("index", "sqlite_autoindex_transaction_record_2", "transaction_record", None),
        ("index", "sqlite_autoindex_transaction_record_3", "transaction_record", None),
        ("index", "sqlite_autoindex_effect_1", "effect", None),
        ("index", "sqlite_autoindex_blob_1", "blob", None),
    ]
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
