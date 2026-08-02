"""Typed record read/write and the durable encoding of A3's halt diagnostic (design §6.4, §7)."""

from __future__ import annotations

import json
from typing import Any

from atoms.core.fingerprint import (
    AbsentState,
    DirectoryState,
    FileState,
    PathState,
    SymlinkState,
)
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
    TransactionState,
)
from atoms.store.errors import MetadataStoreInvalid

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
