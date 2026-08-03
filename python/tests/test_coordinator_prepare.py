"""A5b tier 3 -- preparation: the gates, work-base re-resolution, publication."""

from __future__ import annotations

from typing import cast

import pytest

from atoms.core.errors import PreconditionRefused, ProtocolError
from atoms.core.recovery import CommitDecision, TransactionState
from atoms.fs.approval import ProjectApprovedSpec
from tests.coordinator_support import (
    admission_for,
    compiled_creating_a_directory,
    compiled_for,
    stage_manifest,
)


def test_preparation_publishes_the_record_in_one_commit(leased):
    from atoms.coordinator.prepare import open_workspace, prepare_transaction

    with leased() as lease:
        approved = admission_for(lease)
        workspace = open_workspace(lease, approved)
        manifest = stage_manifest(workspace)

        prepare_transaction(lease, approved, workspace, manifest)
        workspace.close()

        record = lease._store.read_active()
        assert record is not None
        assert record.txid == approved.txid
        assert record.state is TransactionState.PREPARED
        assert record.committed is CommitDecision.UNCOMMITTED


def test_nothing_is_durable_until_the_publication_commit(leased, monkeypatch):
    """The single barrier authority §7.3 step 4 requires.

    The cut is driven THROUGH `prepare_transaction`, not by repeating its body: a test
    that re-implements the transaction stays green when the production function's own
    ordering is wrong, which is the whole thing this asserts. Patching the last call
    inside the body is the smallest cut that leaves the earlier writes staged.
    """
    from atoms.coordinator.prepare import open_workspace, prepare_transaction
    from atoms.store.connection import _StoreTransaction

    with leased() as lease:
        approved = admission_for(lease)
        workspace = open_workspace(lease, approved)
        manifest = stage_manifest(workspace)

        def cut(self, txid):
            raise RuntimeError("cut inside prepare_transaction, before COMMIT")

        monkeypatch.setattr(_StoreTransaction, "set_active", cut)

        with pytest.raises(RuntimeError) as caught:
            prepare_transaction(lease, approved, workspace, manifest)

        workspace.close()
        assert "before COMMIT" in str(caught.value)
        assert lease._store.read_record(approved.txid) is None
        assert lease._store.read_active() is None


def test_a_workspace_from_another_transaction_is_refused(leased):
    from atoms.coordinator.prepare import prepare_transaction

    with leased() as lease:
        approved = admission_for(lease)
        foreign = lease._store.create_workspace("someone-else")

        with pytest.raises(ProtocolError) as caught:
            prepare_transaction(lease, approved, foreign, ())

        foreign.close()
        message = str(caught.value)
        assert "someone-else" in message
        assert approved.txid in message


def test_a_proof_bound_to_another_binding_is_refused(leased):
    from atoms.coordinator.prepare import prepare_transaction

    with leased() as first, leased() as second:
        approved = admission_for(first)
        workspace = second._store.create_workspace(approved.txid)

        with pytest.raises(ProtocolError) as caught:
            prepare_transaction(second, approved, workspace, ())

        workspace.close()
        assert "binding" in str(caught.value)


def test_open_workspace_refuses_a_raw_compiled_spec(leased):
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        raw = cast(ProjectApprovedSpec, compiled_for(lease))

        with pytest.raises(ProtocolError) as caught:
            open_workspace(lease, raw)

        assert "ProjectApprovedSpec" in str(caught.value)


def test_open_workspace_re_resolves_the_work_base_before_creating_anything(leased):
    from atoms.coordinator import admission
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = admission.admit(lease, compiled_creating_a_directory(lease))
        assert approved.work_base is not None
        lease._store.create_workspace(approved.txid).close()

        with pytest.raises(PreconditionRefused) as caught:
            open_workspace(lease, approved)

        assert f"work/{approved.txid} already exists" in str(caught.value)


def test_a_spec_without_a_work_base_skips_the_comparison(leased):
    """`work_base` is None unless the spec contains a CreateDirectory. Treating None as
    a mismatch would refuse every transaction that creates no directory; treating it as
    an empty baseline would let a fresh observation authorize itself."""
    from atoms.coordinator.prepare import open_workspace

    with leased() as lease:
        approved = admission_for(lease)
        assert approved.work_base is None

        workspace = open_workspace(lease, approved)
        assert workspace.txid == approved.txid
        workspace.close()
