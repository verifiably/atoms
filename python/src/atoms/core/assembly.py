"""Canonical, closed diagnostic evidence for approval-assembly halts."""

from __future__ import annotations

import dataclasses
import enum
import json
from typing import Any, TypeVar, cast

from atoms.core.errors import ProtocolError


class AssemblyHaltReason(enum.Enum):
    APPROVAL_EVIDENCE_MISMATCH = "approval-evidence-mismatch"


class AssemblyOperatorAction(enum.Enum):
    RESTORE_APPROVED_TOPOLOGY = "restore-approved-topology"


class AssemblyFindingKind(enum.Enum):
    NODE_MISSING = "node-missing"
    WRONG_ENTRY_KIND = "wrong-entry-kind"
    IDENTITY_CHANGED = "identity-changed"
    CONSTRAINTS_CHANGED = "constraints-changed"
    MOUNT_CHANGED = "mount-changed"
    WORK_ROOT_CHANGED = "work-root-changed"


_FACT_KEYS: dict[AssemblyFindingKind, tuple[str, ...]] = {
    AssemblyFindingKind.NODE_MISSING: (),
    AssemblyFindingKind.WRONG_ENTRY_KIND: ("observed_kind",),
    AssemblyFindingKind.IDENTITY_CHANGED: ("st_dev", "st_ino"),
    AssemblyFindingKind.CONSTRAINTS_CHANGED: ("lookup_proof", "name_max"),
    AssemblyFindingKind.MOUNT_CHANGED: ("mount_id",),
    AssemblyFindingKind.WORK_ROOT_CHANGED: ("work_base",),
}
_FINDING_ORDER = {kind: index for index, kind in enumerate(AssemblyFindingKind)}
_DECIMAL_FACTS = frozenset({"st_dev", "st_ino", "mount_id", "name_max"})
_LOOKUP_PROOFS = frozenset({"exact_bytes", "unreproducible_casefold"})
_EnumT = TypeVar("_EnumT", bound=enum.Enum)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProtocolError(message)


def _is_decimal(value: str) -> bool:
    return value == "0" or (value[:1] in "123456789" and value.isascii() and value.isdecimal())


@dataclasses.dataclass(frozen=True, slots=True)
class AssemblyFinding:
    path: str
    kind: AssemblyFindingKind
    observed: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _require(type(self.path) is str, "assembly finding path must be an exact str")
        _require(
            type(self.kind) is AssemblyFindingKind,
            "assembly finding kind must be an exact AssemblyFindingKind",
        )
        _require(
            type(self.observed) is tuple,
            "assembly finding observed facts must be an exact tuple",
        )
        for fact in self.observed:
            _require(
                type(fact) is tuple and len(fact) == 2,
                "assembly finding fact must be an exact two-item tuple",
            )
            _require(
                type(fact[0]) is str and type(fact[1]) is str,
                "assembly finding fact keys and values must be exact str values",
            )
        _require(
            tuple(key for key, _ in self.observed) == _FACT_KEYS[self.kind],
            "assembly finding observed fact keys do not match its kind",
        )
        for key, value in self.observed:
            if key in _DECIMAL_FACTS:
                _require(
                    _is_decimal(value),
                    f"assembly finding {key!r} must be a canonical decimal string",
                )
            elif key == "observed_kind":
                _require(
                    value in {"file", "symlink", "other"},
                    "assembly finding observed_kind is outside its closed domain",
                )
            elif key == "lookup_proof":
                _require(
                    value in _LOOKUP_PROOFS,
                    "assembly finding lookup_proof is outside its closed domain",
                )
            elif key == "work_base":
                _require(
                    value in {"present", "absent"},
                    "assembly finding work_base is outside its closed domain",
                )


@dataclasses.dataclass(frozen=True, slots=True)
class AssemblyHalt:
    txid: str
    reason: AssemblyHaltReason
    expected: str
    findings: tuple[AssemblyFinding, ...]
    operator_action: AssemblyOperatorAction

    def __post_init__(self) -> None:
        _require(type(self.txid) is str, "assembly halt txid must be an exact str")
        _require(
            type(self.reason) is AssemblyHaltReason,
            "assembly halt reason must be an exact AssemblyHaltReason",
        )
        _require(type(self.expected) is str, "assembly halt expected must be an exact str")
        _require(
            type(self.findings) is tuple and bool(self.findings),
            "assembly halt findings must be a non-empty exact tuple",
        )
        _require(
            type(self.operator_action) is AssemblyOperatorAction,
            "assembly halt operator_action must be an exact AssemblyOperatorAction",
        )
        for finding in self.findings:
            _require(
                type(finding) is AssemblyFinding,
                "assembly halt findings must be exact AssemblyFinding values",
            )
        _require(
            tuple(sorted(self.findings, key=lambda item: (item.path, _FINDING_ORDER[item.kind]))) == self.findings,
            "assembly halt findings must be ordered by path and finding kind",
        )
        for left, right in zip(self.findings, self.findings[1:], strict=False):
            if left.path == right.path and left.kind in {
                AssemblyFindingKind.NODE_MISSING,
                AssemblyFindingKind.WRONG_ENTRY_KIND,
            }:
                raise ProtocolError("a missing or wrong-kind node must be its path's sole finding")


def _finding_obj(finding: AssemblyFinding) -> dict[str, object]:
    return {
        "path": finding.path,
        "kind": finding.kind.value,
        "observed": [list(fact) for fact in finding.observed],
    }


def encode_assembly_halt(halt: AssemblyHalt) -> str:
    _require(type(halt) is AssemblyHalt, "assembly halt must be an exact AssemblyHalt")
    AssemblyHalt(
        halt.txid,
        halt.reason,
        halt.expected,
        halt.findings,
        halt.operator_action,
    )
    return json.dumps(
        {
            "txid": halt.txid,
            "reason": halt.reason.value,
            "expected": halt.expected,
            "findings": [_finding_obj(item) for item in halt.findings],
            "operator_action": halt.operator_action.value,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError(f"duplicate assembly-halt JSON key: {key!r}")
        result[key] = value
    return result


def _object(value: object, label: str) -> dict[str, Any]:
    _require(type(value) is dict, f"{label} must be an object")
    return cast(dict[str, Any], value)


def _string(value: object, label: str) -> str:
    _require(type(value) is str, f"{label} must be an exact str")
    return cast(str, value)


def _only(obj: dict[str, Any], fields: tuple[str, ...], label: str) -> None:
    _require(set(obj) == set(fields), f"{label} has missing or unexpected fields")


def _enum(value: object, kind: type[_EnumT], label: str) -> _EnumT:
    try:
        return kind(_string(value, label))
    except ValueError as exc:
        raise ProtocolError(f"{label} is outside its closed domain") from exc


def decode_assembly_halt(payload: str) -> AssemblyHalt:
    _require(type(payload) is str, "assembly halt payload must be an exact str")
    try:
        raw = json.loads(payload, object_pairs_hook=_no_duplicate_keys)
    except ProtocolError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProtocolError("assembly halt payload is not valid JSON") from exc
    obj = _object(raw, "assembly halt payload")
    _only(obj, ("txid", "reason", "expected", "findings", "operator_action"), "assembly halt payload")
    raw_findings = obj["findings"]
    _require(type(raw_findings) is list, "assembly halt findings must be an array")
    findings: list[AssemblyFinding] = []
    for index, raw_finding in enumerate(raw_findings):
        finding = _object(raw_finding, f"assembly halt findings[{index}]")
        _only(finding, ("path", "kind", "observed"), f"assembly halt findings[{index}]")
        raw_observed = finding["observed"]
        _require(
            type(raw_observed) is list,
            f"assembly halt findings[{index}].observed must be an array",
        )
        observed: list[tuple[str, str]] = []
        for fact_index, raw_fact in enumerate(raw_observed):
            _require(
                type(raw_fact) is list and len(raw_fact) == 2,
                f"assembly halt findings[{index}].observed[{fact_index}] must be a pair",
            )
            observed.append(
                (
                    _string(raw_fact[0], f"assembly halt findings[{index}] fact key"),
                    _string(raw_fact[1], f"assembly halt findings[{index}] fact value"),
                )
            )
        findings.append(
            AssemblyFinding(
                _string(finding["path"], f"assembly halt findings[{index}].path"),
                _enum(finding["kind"], AssemblyFindingKind, f"assembly halt findings[{index}].kind"),
                tuple(observed),
            )
        )
    halt = AssemblyHalt(
        _string(obj["txid"], "assembly halt txid"),
        _enum(obj["reason"], AssemblyHaltReason, "assembly halt reason"),
        _string(obj["expected"], "assembly halt expected"),
        tuple(findings),
        _enum(obj["operator_action"], AssemblyOperatorAction, "assembly halt operator_action"),
    )
    _require(encode_assembly_halt(halt) == payload, "assembly halt payload is not canonical")
    return halt
