"""A5b tier 2 -- admission: generation, the loop, re-resolution, and translation."""

from __future__ import annotations

import pytest

from atoms.core.errors import PreconditionRefused, ProtocolError
from atoms.core.identifiers import is_valid_identifier
from tests.coordinator_support import compiled_for
from tests.store_support import one_effect_spec


def test_new_txid_is_a_valid_identifier():
    from atoms.coordinator.admission import new_txid

    for _ in range(64):
        assert is_valid_identifier(new_txid())


def test_new_txid_does_not_repeat():
    from atoms.coordinator.admission import new_txid

    assert len({new_txid() for _ in range(512)}) == 512


def test_a_candidate_a_durable_record_owns_is_discarded(leased, monkeypatch):
    from atoms.coordinator import admission

    with leased() as lease:
        with lease._store.transaction() as txn:
            txn.insert_record("taken", one_effect_spec())

        issued = iter(["taken", "free"])
        monkeypatch.setattr(admission, "new_txid", lambda: next(issued))

        approved = admission.admit(lease, compiled_for(lease))

        assert approved.txid == "free"


def test_every_candidate_owned_by_a_record_exhausts_the_bound(leased, monkeypatch):
    from atoms.coordinator import admission

    with leased() as lease:
        for txid in ("a", "b", "c"):
            with lease._store.transaction() as txn:
                txn.insert_record(txid, one_effect_spec())

        issued = iter(["a", "b", "c"])
        monkeypatch.setattr(admission, "new_txid", lambda: next(issued))

        with pytest.raises(PreconditionRefused) as caught:
            admission.admit(lease, compiled_for(lease))

        message = str(caught.value)
        assert "no usable txid after 3 attempts" in message
        # It must NOT claim scratch occupancy, which is not why it refused.
        assert "scratch occupied" not in message


def test_the_proof_binds_to_the_lease_that_issued_it(leased):
    from atoms.coordinator.admission import _require_admitted, admit

    with leased() as lease:
        approved = admit(lease, compiled_for(lease))

        assert _require_admitted(lease, approved) is None


def test_a_proof_from_another_binding_is_refused(leased):
    from atoms.coordinator.admission import _require_admitted, admit

    with leased() as first, leased() as second:
        approved = admit(first, compiled_for(first))

        with pytest.raises(ProtocolError) as caught:
            _require_admitted(second, approved)

        assert "binding" in str(caught.value)


def test_a_raw_compiled_spec_is_refused(leased):
    from typing import cast

    from atoms.coordinator.admission import _require_admitted
    from atoms.fs.approval import ProjectApprovedSpec

    with leased() as lease:
        raw = cast(ProjectApprovedSpec, compiled_for(lease))

        with pytest.raises(ProtocolError) as caught:
            _require_admitted(lease, raw)

        assert "ProjectApprovedSpec" in str(caught.value)
