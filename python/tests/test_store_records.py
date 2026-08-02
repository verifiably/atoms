"""Tier 2 -- the record layer over an in-memory-shaped store (design §11.2)."""

from __future__ import annotations

import json

import pytest

from atoms.core.fingerprint import AbsentState, DirectoryState, FileState, SymlinkState
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
from atoms.store.records import decode_diagnostic, encode_diagnostic
from tests.store_support import every_diagnostic_shape


@pytest.mark.parametrize("diagnostic", every_diagnostic_shape(), ids=lambda d: d.reason.value)
def test_the_diagnostic_round_trips_exactly(diagnostic):
    assert decode_diagnostic(encode_diagnostic(diagnostic)) == diagnostic


@pytest.mark.parametrize("diagnostic", every_diagnostic_shape(), ids=lambda d: d.reason.value)
def test_the_encoded_form_contains_no_entry_identity(diagnostic):
    """Ledger #12: a snapshot-local identity token must never become durable. A3 keeps
    HaltDiagnostic token-free by construction, which is what makes it storable at all."""
    text = encode_diagnostic(diagnostic)
    assert "identity" not in text or "identity_relations" in text
    assert "entry-identity" not in text
    assert "_token" not in text


def test_every_halt_reason_and_operator_action_encodes():
    """Parametrized over the enums, so a new member fails here rather than at the first
    halt a real recovery produces."""
    for reason in HaltReason:
        for action in OperatorAction:
            diagnostic = HaltDiagnostic(
                pre_halt_state=TransactionState.APPLYING,
                commit_decision=CommitDecision.UNCOMMITTED,
                journals=(),
                projected_transaction_state=TransactionState.HALTED,
                projected_journals=(),
                effect_id=None,
                paths=(),
                expected=(),
                observed=(),
                identity_relations=(),
                reason=reason,
                operator_action=action,
            )
            assert decode_diagnostic(encode_diagnostic(diagnostic)) == diagnostic


@pytest.mark.parametrize(
    "state",
    [
        AbsentState(),
        FileState(content_hash="sha256:" + "a" * 64, mode=0o644, byte_len=3),
        DirectoryState(mode=0o755),
        SymlinkState(target="../elsewhere", mode=0o777),
    ],
)
def test_every_path_state_variant_round_trips_inside_a_diagnostic_entry(state):
    diagnostic = HaltDiagnostic(
        pre_halt_state=TransactionState.APPLYING,
        commit_decision=CommitDecision.UNCOMMITTED,
        journals=(EffectJournalState(effect_id="e1", state=JournalState.STARTED),),
        projected_transaction_state=TransactionState.HALTED,
        projected_journals=(),
        effect_id="e1",
        paths=("a.txt",),
        expected=(
            DiagnosticEntry(
                slot="expected", state=state, has_unmodeled_child=None,
                file_build_relation=None,
            ),
        ),
        observed=(
            DiagnosticEntry(
                slot="observed", state=state, has_unmodeled_child=True,
                file_build_relation=FileBuildRelation.STRICT_PREFIX,
            ),
        ),
        identity_relations=(
            DiagnosticIdentityRelation(
                left_slot="expected", right_slot="observed",
                relation=IdentityRelation.DIFFERENT,
            ),
        ),
        reason=HaltReason.PLAN_PRECONDITION_CHANGED,
        operator_action=OperatorAction.INSPECT_PRESERVED_EVIDENCE,
    )
    assert decode_diagnostic(encode_diagnostic(diagnostic)) == diagnostic


@pytest.mark.parametrize(
    "mutate",
    [
        lambda obj: obj.pop("reason"),
        lambda obj: obj.update({"reason": "no_such_reason"}),
        lambda obj: obj.update({"unexpected": 1}),
        lambda obj: obj.update({"pre_halt_state": "not_a_state"}),
        lambda obj: obj.update({"journals": [{"effect_id": "e1"}]}),
        # Shapes that reached a *raw* exception before the field checks existed.
        # `journals: 5` left as `TypeError: 'int' object is not iterable`; `paths` as a
        # string decoded character by character and refused nothing; `effect_id: 7` was
        # copied straight through into a HaltDiagnostic A3 would later choke on.
        lambda obj: obj.update({"journals": 5}),
        lambda obj: obj.update({"paths": "a.txt"}),
        lambda obj: obj.update({"paths": ["a.txt", 7]}),
        lambda obj: obj.update({"effect_id": 7}),
        lambda obj: obj.update({"expected": {"slot": "a"}}),
        lambda obj: obj["journals"][0].update({"effect_id": None}),
        # The two nullable fields: null is a value, but only null. A helper that admits
        # `None` must not thereby admit everything else.
        lambda obj: obj["expected"][0].update({"has_unmodeled_child": "yes"}),
        lambda obj: obj.update({"effect_id": False}),
    ],
)
def test_a_malformed_diagnostic_payload_refuses(mutate):
    """A missing field, an extra one, an unknown enum member, and a field of the wrong
    primitive type all refuse -- design §9 lists a malformed payload as a
    MetadataStoreInvalid shape, and `halt_diagnostic` is the one column with no CHECK
    behind it, so this decoder is its entire boundary."""
    diagnostic = every_diagnostic_shape()[0]
    obj = json.loads(encode_diagnostic(diagnostic))
    mutate(obj)
    with pytest.raises(MetadataStoreInvalid):
        decode_diagnostic(json.dumps(obj))


def test_a_duplicate_key_in_the_payload_refuses():
    diagnostic = every_diagnostic_shape()[0]
    text = encode_diagnostic(diagnostic)
    doubled = text[:-1] + ', "reason": "directory_not_empty"}'
    with pytest.raises(MetadataStoreInvalid):
        decode_diagnostic(doubled)
