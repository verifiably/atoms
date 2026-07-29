from dataclasses import FrozenInstanceError, fields

import pytest

from atoms.core.recovery import (
    EntryIdentity,
    HaltDiagnostic,
    HaltReason,
    IdentityRelation,
    JournalState,
    ObservedFile,
    TransactionState,
)
from tests.support import F


def test_state_and_reason_values_are_closed_enums_with_string_values():
    assert [state.value for state in TransactionState] == [
        "prepared",
        "applying",
        "applied",
        "committed",
        "rolling_back",
        "rolled_back",
        "halted",
    ]
    assert HaltReason.ACTIVE_BINDING_MISSING.value == "active_binding_missing"
    assert HaltReason.COMMIT_DECISION_CONFLICT.value == "commit_decision_conflict"
    assert TransactionState.PREPARED != "prepared"


def test_entry_identity_is_opaque_snapshot_local_and_repr_safe():
    first = EntryIdentity()
    same = first
    other = EntryIdentity()
    assert first == same
    assert first != other
    assert repr(first) == "<entry-identity>"
    assert fields(EntryIdentity)[0].repr is False


def test_observations_are_frozen():
    observed = ObservedFile(state=F, identity=EntryIdentity())
    with pytest.raises(FrozenInstanceError):
        observed.state = F  # type: ignore[misc]


def test_halt_diagnostic_has_no_identity_token_field():
    field_types = {field.name: str(field.type) for field in fields(HaltDiagnostic)}
    assert all("EntryIdentity" not in field_type for field_type in field_types.values())
    assert "identity_relations" in field_types
    assert "projected_journals" in field_types
    assert IdentityRelation.SAME.value == "same"
    assert JournalState.STARTED.value == "started"
