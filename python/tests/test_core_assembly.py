from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from atoms.core.assembly import (
    _LOOKUP_PROOFS,
    AssemblyFinding,
    AssemblyFindingKind,
    AssemblyHalt,
    AssemblyHaltReason,
    AssemblyOperatorAction,
    decode_assembly_halt,
    encode_assembly_halt,
)
from atoms.core.errors import ProtocolError
from atoms.fs.lookup import LookupProof


def _finding(kind: AssemblyFindingKind, path: str) -> AssemblyFinding:
    facts = {
        AssemblyFindingKind.NODE_MISSING: (),
        AssemblyFindingKind.WRONG_ENTRY_KIND: (("observed_kind", "other"),),
        AssemblyFindingKind.MOUNT_BOUNDARY: (),
        AssemblyFindingKind.ACCESS_DENIED: (),
        AssemblyFindingKind.IDENTITY_CHANGED: (("st_dev", "1"), ("st_ino", "2")),
        AssemblyFindingKind.CONSTRAINTS_CHANGED: (
            ("lookup_proof", LookupProof.EXACT_BYTES.value),
            ("name_max", "255"),
        ),
        AssemblyFindingKind.MOUNT_CHANGED: (("mount_id", "3"),),
        AssemblyFindingKind.WORK_ROOT_CHANGED: (("work_base", "present"),),
    }
    return AssemblyFinding(path, kind, facts[kind])


def _halt(*findings: AssemblyFinding) -> AssemblyHalt:
    return AssemblyHalt(
        txid="tx-1",
        reason=AssemblyHaltReason.APPROVAL_EVIDENCE_MISMATCH,
        expected='{"approved":true}',
        findings=findings,
        operator_action=AssemblyOperatorAction.RESTORE_APPROVED_TOPOLOGY,
    )


def test_round_trips_every_closed_finding_kind_in_canonical_wire_shape() -> None:
    halt = _halt(*(_finding(kind, f"node-{index}") for index, kind in enumerate(AssemblyFindingKind)))

    encoded = encode_assembly_halt(halt)

    assert encoded == (
        '{"expected":"{\\"approved\\":true}","findings":['
        '{"kind":"node-missing","observed":[],"path":"node-0"},'
        '{"kind":"wrong-entry-kind","observed":[["observed_kind","other"]],"path":"node-1"},'
        '{"kind":"mount-boundary","observed":[],"path":"node-2"},'
        '{"kind":"access-denied","observed":[],"path":"node-3"},'
        '{"kind":"identity-changed","observed":[["st_dev","1"],["st_ino","2"]],"path":"node-4"},'
        '{"kind":"constraints-changed","observed":[["lookup_proof","exact_bytes"],["name_max","255"]],"path":"node-5"},'
        '{"kind":"mount-changed","observed":[["mount_id","3"]],"path":"node-6"},'
        '{"kind":"work-root-changed","observed":[["work_base","present"]],"path":"node-7"}'
        '],"operator_action":"restore-approved-topology",'
        '"reason":"approval-evidence-mismatch","txid":"tx-1"}'
    )
    assert decode_assembly_halt(encoded) == halt


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["findings"][0].update(kind="unknown"),
        lambda value: value.pop("txid"),
        lambda value: value["findings"][0].update(observed=[]),
        lambda value: value["findings"][0]["observed"].append(["extra", "fact"]),
        lambda value: value["findings"][0].update(observed=[["st_dev", "1"], ["st_dev", "2"]]),
        lambda value: value["findings"][0].update(observed=[["st_ino", "2"], ["st_dev", "1"]]),
        lambda value: value.update(findings=list(reversed(value["findings"]))),
    ],
    ids=(
        "unknown-kind",
        "missing-member",
        "wrong-fact-keys",
        "extra-fact-key",
        "duplicate-fact-key",
        "misordered-fact-pairs",
        "unsorted-findings",
    ),
)
def test_decode_refuses_malformed_external_payload(
    mutate: Callable[[dict[str, object]], object],
) -> None:
    halt = _halt(
        _finding(AssemblyFindingKind.IDENTITY_CHANGED, "a"),
        _finding(AssemblyFindingKind.MOUNT_CHANGED, "b"),
    )
    payload = json.loads(encode_assembly_halt(halt))
    mutate(payload)

    with pytest.raises(ProtocolError):
        decode_assembly_halt(json.dumps(payload, separators=(",", ":")))


def test_decode_refuses_duplicate_json_keys() -> None:
    encoded = encode_assembly_halt(_halt(_finding(AssemblyFindingKind.NODE_MISSING, "a")))
    duplicate = encoded.replace('"txid":"tx-1"', '"txid":"tx-1","txid":"tx-1"')

    with pytest.raises(ProtocolError):
        decode_assembly_halt(duplicate)


@pytest.mark.parametrize(
    ("findings", "description"),
    [
        (
            (
                _finding(AssemblyFindingKind.MOUNT_CHANGED, "a"),
                _finding(AssemblyFindingKind.IDENTITY_CHANGED, "a"),
            ),
            "out-of-order findings",
        ),
        (
            (
                _finding(AssemblyFindingKind.NODE_MISSING, "a"),
                _finding(AssemblyFindingKind.MOUNT_CHANGED, "a"),
            ),
            "a missing node sharing its path",
        ),
        (
            (
                _finding(AssemblyFindingKind.WRONG_ENTRY_KIND, "a"),
                _finding(AssemblyFindingKind.MOUNT_CHANGED, "a"),
            ),
            "a wrong-kind node sharing its path",
        ),
        (
            (
                _finding(AssemblyFindingKind.ACCESS_DENIED, "a"),
                _finding(AssemblyFindingKind.IDENTITY_CHANGED, "a"),
            ),
            "an access-denied node sharing its path",
        ),
        ((), "empty findings"),
    ],
)
def test_construction_refuses_invalid_finding_collections(
    findings: tuple[AssemblyFinding, ...], description: str
) -> None:
    with pytest.raises(ProtocolError):
        _halt(*findings)


@pytest.mark.parametrize(
    ("kind", "observed"),
    [
        (
            AssemblyFindingKind.IDENTITY_CHANGED,
            (("st_dev", "1"), ("mount_id", "2")),
        ),
        (
            AssemblyFindingKind.WRONG_ENTRY_KIND,
            (("observed_kind", "socket"),),
        ),
        (
            AssemblyFindingKind.IDENTITY_CHANGED,
            (("st_dev", "1"), ("st_ino", "007")),
        ),
        (
            AssemblyFindingKind.IDENTITY_CHANGED,
            (("st_dev", "1"), ("st_ino", "1٢")),
        ),
        (
            AssemblyFindingKind.WORK_ROOT_CHANGED,
            (("work_base", "maybe"),),
        ),
    ],
)
def test_construction_refuses_invalid_closed_facts(
    kind: AssemblyFindingKind, observed: tuple[tuple[str, str], ...]
) -> None:
    with pytest.raises(ProtocolError):
        AssemblyFinding("a", kind, observed)


def test_lookup_proof_fact_uses_the_lookup_proof_wire_value() -> None:
    finding = _finding(AssemblyFindingKind.CONSTRAINTS_CHANGED, "a")

    assert finding.observed[0] == ("lookup_proof", LookupProof.EXACT_BYTES.value)
    assert _LOOKUP_PROOFS == frozenset(member.value for member in LookupProof)
