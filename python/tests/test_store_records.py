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
    ("mutate", "names"),
    [
        (
            lambda obj: obj.pop("reason"),
            # `_require_keys` is the only check that can catch a missing field, so there is
            # no field-specific path underneath it to mask -- but the assertion still has to
            # prove *reason specifically* went missing, not merely that some key did. The
            # `got` list is the complete, alphabetically sorted set of the other 11 fields
            # with `reason` absent; pinned whole, it cannot be produced by any other case's
            # mutation (verified: absent from all 12 other real messages).
            (
                "got ['commit_decision', 'effect_id', 'expected', 'identity_relations', "
                "'journals', 'observed', 'operator_action', 'paths', 'pre_halt_state', "
                "'projected_journals', 'projected_transaction_state']"
            ),
        ),
        (lambda obj: obj.update({"reason": "no_such_reason"}), "HaltReason"),
        (lambda obj: obj.update({"unexpected": 1}), "unexpected"),
        (lambda obj: obj.update({"pre_halt_state": "not_a_state"}), "TransactionState"),
        (
            lambda obj: obj.update({"journals": [{"effect_id": "e1"}]}),
            "expected keys ['effect_id', 'state'], got ['effect_id']",
        ),
        # Shapes that reached a *raw* exception before the field checks existed.
        # `journals: 5` left as `TypeError: 'int' object is not iterable`; `paths` as a
        # string decoded character by character and refused nothing; `effect_id: 7` was
        # copied straight through into a HaltDiagnostic A3 would later choke on.
        (lambda obj: obj.update({"journals": 5}), "journals must be an array"),
        (lambda obj: obj.update({"paths": "a.txt"}), "paths must be an array"),
        (lambda obj: obj.update({"paths": ["a.txt", 7]}), "paths[1]"),
        (lambda obj: obj.update({"effect_id": 7}), "effect_id must be a string"),
        (lambda obj: obj.update({"expected": {"slot": "a"}}), "expected must be an array"),
        (lambda obj: obj["journals"][0].update({"effect_id": None}), "NoneType"),
        # The two nullable fields: null is a value, but only null. A helper that admits
        # `None` must not thereby admit everything else.
        (lambda obj: obj["expected"][0].update({"has_unmodeled_child": "yes"}), "has_unmodeled_child"),
        (lambda obj: obj.update({"effect_id": False}), "effect_id must be a string, got bool"),
    ],
)
def test_a_malformed_diagnostic_payload_refuses(mutate, names):
    """A missing field, an extra one, an unknown enum member, and a field of the wrong
    primitive type all refuse -- design §9 lists a malformed payload as a
    MetadataStoreInvalid shape, and `halt_diagnostic` is the one column with no CHECK
    behind it, so this decoder is its entire boundary.

    Asserting only `MetadataStoreInvalid` proves *something* refused, not that it refused
    for the stated reason: a regression that let an unrelated, over-eager check fire first
    would still raise the right exception type while masking a broken field-specific path.
    So each case also asserts the field or member name its own refusal must name.
    """
    diagnostic = every_diagnostic_shape()[0]
    obj = json.loads(encode_diagnostic(diagnostic))
    mutate(obj)
    with pytest.raises(MetadataStoreInvalid) as caught:
        decode_diagnostic(json.dumps(obj))
    assert names in str(caught.value)


def test_a_duplicate_key_in_the_payload_refuses():
    diagnostic = every_diagnostic_shape()[0]
    text = encode_diagnostic(diagnostic)
    doubled = text[:-1] + ', "reason": "directory_not_empty"}'
    with pytest.raises(MetadataStoreInvalid):
        decode_diagnostic(doubled)
