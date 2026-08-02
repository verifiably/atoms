"""Typed record read/write and the durable encoding of A3's halt diagnostic (design §6.4, §7)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from atoms.core.canonical import canonical_json, from_canonical_json
from atoms.core.compiler import compile_spec
from atoms.core.errors import ProtocolError, SpecValidationError
from atoms.core.fingerprint import (
    AbsentState,
    DirectoryState,
    FileState,
    PathState,
    SymlinkState,
)
from atoms.core.identifiers import is_valid_identifier
from atoms.core.recovery.model import (
    CommitDecision,
    DiagnosticEntry,
    DiagnosticIdentityRelation,
    EffectJournalState,
    FileBuildRelation,
    HaltDiagnostic,
    HaltReason,
    IdentityRelation,
    JournalState,
    OperatorAction,
    RollbackResult,
    TransactionState,
)
from atoms.core.spec import TransactionSpec
from atoms.store.errors import MetadataStoreInvalid
from atoms.store.schema import variant_of

_DIAGNOSTIC_FIELDS = (
    "pre_halt_state",
    "commit_decision",
    "journals",
    "projected_transaction_state",
    "projected_journals",
    "effect_id",
    "paths",
    "expected",
    "observed",
    "identity_relations",
    "reason",
    "operator_action",
)


def _refuse(message: str) -> None:
    raise MetadataStoreInvalid(f"halt diagnostic payload is malformed: {message}")


def _state_obj(state: PathState) -> dict[str, Any]:
    if isinstance(state, AbsentState):
        return {"kind": "absent"}
    if isinstance(state, FileState):
        return {
            "kind": "file",
            "content_hash": state.content_hash,
            "mode": state.mode,
            "byte_len": state.byte_len,
        }
    if isinstance(state, DirectoryState):
        return {"kind": "directory", "mode": state.mode}
    return {"kind": "symlink", "target": state.target, "mode": state.mode}


def _entry_obj(entry: DiagnosticEntry) -> dict[str, Any]:
    return {
        "slot": entry.slot,
        "state": _state_obj(entry.state),
        "has_unmodeled_child": entry.has_unmodeled_child,
        "file_build_relation": (
            None if entry.file_build_relation is None else entry.file_build_relation.value
        ),
    }


def encode_diagnostic(diagnostic: HaltDiagnostic) -> str:
    """Explicit, field by field. Nothing here reflects over the dataclass, so a field A3
    adds does not silently become durable (ledger #12)."""
    return json.dumps(
        {
            "pre_halt_state": diagnostic.pre_halt_state.value,
            "commit_decision": diagnostic.commit_decision.value,
            "journals": [
                {"effect_id": j.effect_id, "state": j.state.value}
                for j in diagnostic.journals
            ],
            "projected_transaction_state": diagnostic.projected_transaction_state.value,
            "projected_journals": [
                {"effect_id": j.effect_id, "state": j.state.value}
                for j in diagnostic.projected_journals
            ],
            "effect_id": diagnostic.effect_id,
            "paths": list(diagnostic.paths),
            "expected": [_entry_obj(e) for e in diagnostic.expected],
            "observed": [_entry_obj(e) for e in diagnostic.observed],
            "identity_relations": [
                {
                    "left_slot": r.left_slot,
                    "right_slot": r.right_slot,
                    "relation": r.relation.value,
                }
                for r in diagnostic.identity_relations
            ],
            "reason": diagnostic.reason.value,
            "operator_action": diagnostic.operator_action.value,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            _refuse(f"duplicate key {key!r}")
        seen[key] = value
    return seen


def _member(enum_type: Any, value: Any, label: str) -> Any:
    try:
        return enum_type(value)
    except ValueError:
        _refuse(f"{value!r} is not a {label}")


def _text(obj: dict[str, Any], key: str) -> str:
    """A field the encoder wrote as a string, refused if it is anything else.

    `halt_diagnostic` is a bare TEXT column -- no CHECK, nothing SQLite verifies -- so
    the decoder is the whole boundary for it, and a decoder that copies a field through
    unexamined builds a `HaltDiagnostic` whose `effect_id` is an int. That is not
    corruption A5b can see: it type-checks, it round-trips, and it fails somewhere much
    later in A3.
    """
    value = obj[key]
    if type(value) is not str:
        _refuse(f"{key} must be a string, got {type(value).__name__}")
    return value


def _integer(obj: dict[str, Any], key: str) -> int:
    value = obj[key]
    # bool before int: `True` is an `int` subclass, and a mode of True is not a mode.
    if type(value) is not int:
        _refuse(f"{key} must be an integer, got {type(value).__name__}")
    return value


def _flag(obj: dict[str, Any], key: str) -> bool:
    value = obj[key]
    if type(value) is not bool:
        _refuse(f"{key} must be a boolean, got {type(value).__name__}")
    return value


def _optional_text(obj: dict[str, Any], key: str) -> str | None:
    """A field A3 declares as `str | None`. Null is a *value* here, not a missing field.

    `HaltDiagnostic.effect_id` is `str | None` and `DiagnosticEntry.has_unmodeled_child`
    is `bool | None` (`atoms/core/recovery/model.py`). Routing either through the
    non-null helper refuses the encoder's own output: measured, the two builders in
    `every_diagnostic_shape()` fail with `effect_id must be a string, got NoneType` and
    `has_unmodeled_child must be a boolean, got NoneType`. Null passes; anything else
    still goes through the strict helper, so `{"effect_id": 7}` refuses as before.
    """
    if obj[key] is None:
        return None
    return _text(obj, key)


def _optional_flag(obj: dict[str, Any], key: str) -> bool | None:
    if obj[key] is None:
        return None
    return _flag(obj, key)


def _sequence(obj: dict[str, Any], key: str) -> list[Any]:
    """A field the encoder wrote as a JSON array.

    Without this, `{"journals": 5}` reaches `tuple(_decode_journal(j) for j in 5)` and
    leaves as `TypeError: 'int' object is not iterable` -- a raw exception from a
    persisted-input path whose contract is `MetadataStoreInvalid`. A string is refused
    too, since it is iterable and would decode character by character.
    """
    value = obj[key]
    if type(value) is not list:
        _refuse(f"{key} must be an array, got {type(value).__name__}")
    return value


def _decode_state(obj: Any) -> PathState:
    if not isinstance(obj, dict):
        _refuse("a path state must be an object")
    kind = obj.get("kind")
    if kind == "absent":
        _require_keys(obj, {"kind"})
        return AbsentState()
    if kind == "file":
        _require_keys(obj, {"kind", "content_hash", "mode", "byte_len"})
        return FileState(
            content_hash=_text(obj, "content_hash"),
            mode=_integer(obj, "mode"),
            byte_len=_integer(obj, "byte_len"),
        )
    if kind == "directory":
        _require_keys(obj, {"kind", "mode"})
        return DirectoryState(mode=_integer(obj, "mode"))
    if kind == "symlink":
        _require_keys(obj, {"kind", "target", "mode"})
        return SymlinkState(target=_text(obj, "target"), mode=_integer(obj, "mode"))
    _refuse(f"{kind!r} is not a path-state kind")
    raise AssertionError("unreachable")


def _require_keys(obj: dict[str, Any], expected: set[str]) -> None:
    if set(obj) != expected:
        _refuse(f"expected keys {sorted(expected)}, got {sorted(obj)}")


def _decode_journal(obj: Any) -> EffectJournalState:
    if not isinstance(obj, dict):
        _refuse("a journal entry must be an object")
    _require_keys(obj, {"effect_id", "state"})
    return EffectJournalState(
        effect_id=_text(obj, "effect_id"),
        state=_member(JournalState, obj["state"], "JournalState"),
    )


def _decode_entry(obj: Any) -> DiagnosticEntry:
    if not isinstance(obj, dict):
        _refuse("a diagnostic entry must be an object")
    _require_keys(obj, {"slot", "state", "has_unmodeled_child", "file_build_relation"})
    relation = obj["file_build_relation"]
    return DiagnosticEntry(
        slot=_text(obj, "slot"),
        state=_decode_state(obj["state"]),
        has_unmodeled_child=_optional_flag(obj, "has_unmodeled_child"),
        file_build_relation=(
            None if relation is None
            else _member(FileBuildRelation, relation, "FileBuildRelation")
        ),
    )


def _decode_relation(obj: Any) -> DiagnosticIdentityRelation:
    if not isinstance(obj, dict):
        _refuse("an identity relation must be an object")
    _require_keys(obj, {"left_slot", "right_slot", "relation"})
    return DiagnosticIdentityRelation(
        left_slot=_text(obj, "left_slot"),
        right_slot=_text(obj, "right_slot"),
        relation=_member(IdentityRelation, obj["relation"], "IdentityRelation"),
    )


def decode_diagnostic(text: str) -> HaltDiagnostic:
    try:
        obj = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError as caught:
        raise MetadataStoreInvalid(
            f"halt diagnostic payload is not valid JSON: {caught}"
        ) from caught
    if not isinstance(obj, dict):
        _refuse("the payload must be an object")
    _require_keys(obj, set(_DIAGNOSTIC_FIELDS))
    paths = _sequence(obj, "paths")
    for index, entry in enumerate(paths):
        if type(entry) is not str:
            _refuse(f"paths[{index}] must be a string, got {type(entry).__name__}")
    return HaltDiagnostic(
        pre_halt_state=_member(TransactionState, obj["pre_halt_state"], "TransactionState"),
        commit_decision=_member(CommitDecision, obj["commit_decision"], "CommitDecision"),
        journals=tuple(_decode_journal(j) for j in _sequence(obj, "journals")),
        projected_transaction_state=_member(
            TransactionState, obj["projected_transaction_state"], "TransactionState"
        ),
        projected_journals=tuple(
            _decode_journal(j) for j in _sequence(obj, "projected_journals")
        ),
        effect_id=_optional_text(obj, "effect_id"),
        paths=tuple(paths),
        expected=tuple(_decode_entry(e) for e in _sequence(obj, "expected")),
        observed=tuple(_decode_entry(e) for e in _sequence(obj, "observed")),
        identity_relations=tuple(
            _decode_relation(r) for r in _sequence(obj, "identity_relations")
        ),
        reason=_member(HaltReason, obj["reason"], "HaltReason"),
        operator_action=_member(OperatorAction, obj["operator_action"], "OperatorAction"),
    )


INSERT_RECORD = (
    "INSERT INTO transaction_record "
    "(txid, spec_json, state, committed, rollback_result, halt_diagnostic) "
    "VALUES (?, ?, ?, ?, NULL, NULL)"
)
INSERT_EFFECT = (
    "INSERT INTO effect (txid, effect_id, variant, journal_state) VALUES (?, ?, ?, ?)"
)
UPDATE_STATE = "UPDATE transaction_record SET state = ? WHERE txid = ?"
UPDATE_COMMITTED = "UPDATE transaction_record SET committed = ? WHERE txid = ?"
UPDATE_ROLLBACK_RESULT = "UPDATE transaction_record SET rollback_result = ? WHERE txid = ?"
UPDATE_HALT_DIAGNOSTIC = "UPDATE transaction_record SET halt_diagnostic = ? WHERE txid = ?"
UPDATE_JOURNAL_STATE = (
    "UPDATE effect SET journal_state = ? WHERE txid = ? AND effect_id = ?"
)
UPSERT_ACTIVE = (
    "INSERT INTO active (singleton, txid) VALUES (0, ?) "
    "ON CONFLICT(singleton) DO UPDATE SET txid = excluded.txid"
)
DELETE_ACTIVE = "DELETE FROM active"


def require_identifier(label: str, value: object) -> str:
    """Design §5.5, in two steps and in this order.

    `require_valid_identifier` raises SpecValidationError for '../x' and a raw TypeError
    for 3, None, and b'tx' -- measured for all four -- because it hands its argument
    straight to a compiled pattern. A TypeError from inside a validator is
    indistinguishable from a bug in A5a's own code, and it is the response to the exact
    input a hostile caller supplies. So A5a reuses A1's *predicate* and supplies its own
    refusal.

    Step 1 is what makes step 2 total: is_valid_identifier is only safe to call once the
    argument is known to be a str. Exact type, per the A4b precedent at approval.py:96 --
    a str subclass passes an isinstance gate and can then behave differently at the
    syscall.
    """
    if type(value) is not str:
        raise ProtocolError(
            f"{label} must be exactly str, got {type(value).__name__}"
        )
    if not is_valid_identifier(value):
        raise ProtocolError(
            f"{label} {value!r} is not 1-64 characters of [A-Za-z0-9_-]"
        )
    return value


def require_member(label: str, value: Enum, enum_type: type[Enum]) -> str:
    """The exact-type gate for every enum a caller hands in, returning the stored value.

    Reading `.value` first is what made this necessary: `set_transaction_state(txid,
    "applied")` raised `AttributeError: 'str' object has no attribute 'value'` before any
    check ran, and §9's table is explicit that a wrong exact type is `ProtocolError` --
    caller misuse -- not a stray attribute error from inside the store. `type(...) is not`
    rather than `isinstance`, matching every other exact-type gate here: a subclass of
    `TransactionState` is not one of A3's members, and `IntEnum`-style coercions are
    exactly what STRICT columns exist to refuse.

    Returning the value rather than the member is what keeps the call site one line and
    leaves no second place to forget the check.
    """
    if type(value) is not enum_type:
        raise ProtocolError(
            f"{label} must be exactly {enum_type.__name__}, got {type(value).__name__}"
        )
    return value.value


RULE_SPEC_DECODES = "spec_json_decodes"
RULE_SPEC_CANONICAL = "spec_json_canonical"
RULE_SPEC_COMPILES = "spec_json_compiles"
RULE_EFFECT_COVERAGE = "effect_coverage"
RULE_EFFECT_VARIANT = "effect_variant"
RULE_BLOB_ROW_PRESENT = "blob_row_present"
RULE_BLOB_BYTE_LEN = "blob_byte_len"
RULE_ROLLBACK_RESULT = "rollback_result_exactly"
RULE_HALT_DIAGNOSTIC = "halt_diagnostic_exactly"
RULE_DIAGNOSTIC_DECISION = "diagnostic_commit_decision"
RULE_DIAGNOSTIC_JOURNALS = "diagnostic_journal_vector"
RULE_ACTIVE_RECORD = "active_record_exists"
COHERENCE_RULES: tuple[str, ...] = (
    RULE_SPEC_DECODES, RULE_SPEC_CANONICAL, RULE_SPEC_COMPILES, RULE_EFFECT_COVERAGE,
    RULE_EFFECT_VARIANT, RULE_BLOB_ROW_PRESENT, RULE_BLOB_BYTE_LEN,
    RULE_ROLLBACK_RESULT, RULE_HALT_DIAGNOSTIC, RULE_DIAGNOSTIC_DECISION,
    RULE_DIAGNOSTIC_JOURNALS, RULE_ACTIVE_RECORD,
)
SELECT_RECORD = "SELECT spec_json, state, committed, rollback_result, halt_diagnostic FROM transaction_record WHERE txid = ?"
SELECT_EFFECTS = "SELECT effect_id, variant, journal_state FROM effect WHERE txid = ?"
SELECT_BLOB = "SELECT byte_len FROM blob WHERE digest = ?"
SELECT_ACTIVE = "SELECT txid FROM active"


@dataclass(frozen=True, slots=True)
class StoredRecord:
    txid: str
    spec: TransactionSpec
    state: TransactionState
    committed: CommitDecision
    rollback_result: RollbackResult | None
    halt_diagnostic: HaltDiagnostic | None
    journals: tuple[EffectJournalState, ...]


def _finding(rule: str, detail: str) -> str:
    return f"{rule}: {detail}"


def referenced_digests(spec: TransactionSpec) -> tuple[tuple[str, int], ...]:
    return tuple(sorted({
        (entry.state.content_hash, entry.state.byte_len)
        for entry in spec.initial_surface if isinstance(entry.state, FileState)
    }))


def journal_vector(
    spec: TransactionSpec, rows: dict[str, tuple[str, str]]
) -> tuple[EffectJournalState, ...]:
    return tuple(
        EffectJournalState(effect.effect_id, JournalState(rows[effect.effect_id][1]))
        for effect in spec.effects
    )


def coherence_findings(connection: Any, txid: str) -> tuple[str, ...]:
    row = connection.execute(SELECT_RECORD, (txid,)).fetchone()
    if row is None:
        return ()
    spec_json, state_value, committed_value, rollback_value, diagnostic_text = row
    try:
        spec = from_canonical_json(spec_json)
    except (SpecValidationError, ValueError) as caught:
        return (_finding(RULE_SPEC_DECODES, f"spec_json does not decode: {caught}"),)
    findings: list[str] = []
    if canonical_json(spec) != spec_json:
        findings.append(_finding(RULE_SPEC_CANONICAL, "spec_json is not canonical"))
    try:
        compile_spec(spec)
    except SpecValidationError as caught:
        findings.append(_finding(RULE_SPEC_COMPILES, f"spec_json is a spec A2 would refuse: {caught}"))
    stored = {
        effect_id: (variant, journal)
        for effect_id, variant, journal in connection.execute(SELECT_EFFECTS, (txid,))
    }
    declared = {effect.effect_id: variant_of(effect).value for effect in spec.effects}
    covered = set(declared) == set(stored)
    for effect_id in sorted(set(declared) - set(stored)):
        findings.append(_finding(RULE_EFFECT_COVERAGE, f"effect row missing for effect_id {effect_id!r}"))
    for effect_id in sorted(set(stored) - set(declared)):
        findings.append(_finding(RULE_EFFECT_COVERAGE, f"effect row {effect_id!r} is not in spec_json"))
    for effect_id in sorted(set(declared) & set(stored)):
        if stored[effect_id][0] != declared[effect_id]:
            findings.append(_finding(RULE_EFFECT_VARIANT, f"effect {effect_id!r} has variant {stored[effect_id][0]!r}, spec_json says {declared[effect_id]!r}"))
    for digest, byte_len in referenced_digests(spec):
        blob = connection.execute(SELECT_BLOB, (digest,)).fetchone()
        if blob is None:
            findings.append(_finding(RULE_BLOB_ROW_PRESENT, f"no blob row for referenced digest {digest!r}"))
        elif blob[0] != byte_len:
            findings.append(_finding(RULE_BLOB_BYTE_LEN, f"blob {digest!r} has byte_len {blob[0]}, the record declares {byte_len}"))
    state = TransactionState(state_value)
    if (state is TransactionState.ROLLED_BACK) != (rollback_value is not None):
        findings.append(_finding(RULE_ROLLBACK_RESULT, "rollback_result must be present exactly when state is rolled_back"))
    if (state is TransactionState.HALTED) != (diagnostic_text is not None):
        findings.append(_finding(RULE_HALT_DIAGNOSTIC, "halt_diagnostic must be present exactly when state is halted"))
    if diagnostic_text is not None:
        diagnostic = decode_diagnostic(diagnostic_text)
        if diagnostic.commit_decision.value != committed_value:
            findings.append(_finding(RULE_DIAGNOSTIC_DECISION, "the diagnostic's commit_decision disagrees with the durable row"))
        if covered:
            if diagnostic.journals != journal_vector(spec, stored):
                findings.append(_finding(RULE_DIAGNOSTIC_JOURNALS, "the diagnostic's journal vector disagrees with the durable rows"))
        else:
            findings.append(_finding(RULE_DIAGNOSTIC_JOURNALS, "the diagnostic's journal vector cannot be compared: the effect rows do not cover spec_json"))
    active = connection.execute(SELECT_ACTIVE).fetchone()
    if active is not None and connection.execute(SELECT_RECORD, (active[0],)).fetchone() is None:
        findings.append(_finding(RULE_ACTIVE_RECORD, f"active names txid {active[0]!r}, which has no record"))
    return tuple(findings)


def load_record(connection: Any, txid: str) -> StoredRecord | None:
    row = connection.execute(SELECT_RECORD, (txid,)).fetchone()
    if row is None:
        return None
    findings = coherence_findings(connection, txid)
    if findings:
        raise MetadataStoreInvalid(f"the record for txid {txid!r} cannot be interpreted: " + "; ".join(findings))
    spec_json, state_value, committed_value, rollback_value, diagnostic_text = row
    spec = from_canonical_json(spec_json)
    rows = {
        effect_id: (variant, journal)
        for effect_id, variant, journal in connection.execute(SELECT_EFFECTS, (txid,))
    }
    return StoredRecord(
        txid, spec, TransactionState(state_value), CommitDecision(committed_value),
        None if rollback_value is None else RollbackResult(rollback_value),
        None if diagnostic_text is None else decode_diagnostic(diagnostic_text),
        journal_vector(spec, rows),
    )
