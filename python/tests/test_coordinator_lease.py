"""A5b tier 1 -- the lease's entry order, reclamation, resolution, and duration."""

from __future__ import annotations

import pytest

from atoms.core.errors import ProtocolError


def test_the_lease_yields_a_working_store(leased):
    with leased() as lease:
        assert lease._store.read_active() is None


def test_the_lease_spends_the_store_on_exit(leased):
    with leased() as lease:
        escaped = lease
    with pytest.raises(ProtocolError) as caught:
        escaped._store.read_active()
    assert "closed" in str(caught.value)


def test_the_lease_holds_no_public_binding_or_store(leased):
    with leased() as lease:
        assert not hasattr(lease, "binding")
        assert not hasattr(lease, "store")


def test_reclamation_removes_orphans_and_spares_referenced_scratch(leased):
    from atoms.coordinator.lease import _reclaim_orphans
    from tests.store_support import one_effect_spec

    with leased() as lease:
        lease._store.create_workspace("orphan1").close()
        lease._store.create_workspace("kept1").close()
        with lease._store.transaction() as txn:
            txn.insert_record("kept1", one_effect_spec())

        workspaces, blobs = _reclaim_orphans(lease._store)

        assert workspaces == ("orphan1",)
        assert blobs == ()
        assert lease._store.list_workspaces() == ("kept1",)


def test_reclamation_removes_an_unindexed_blob(leased):
    """A blob is unindexed exactly when it is on disk with no `blob` row.

    `promote_staging` renames each blob into `blobs/` and flushes it BEFORE its
    `INSERT_BLOB` runs, so a transaction that rolls back leaves precisely that. This is
    the only way to produce one, and asserting reclamation drains an empty list would
    prove nothing.
    """
    from atoms.coordinator.lease import _reclaim_orphans
    from atoms.store.blobs import StagedBlob
    from tests.store_support import digest_of, spec_referencing, stage

    content = b"orphaned by a cut before COMMIT"
    digest = digest_of(content)

    with leased() as lease:
        with lease._store.create_workspace("orphan2") as workspace:
            stage(workspace, "b0", content)
            with pytest.raises(RuntimeError), lease._store.transaction() as txn:
                txn.promote_staging(
                    workspace,
                    (StagedBlob(name="b0", digest=digest, byte_len=len(content)),),
                )
                txn.insert_record("orphan2", spec_referencing(content))
                raise RuntimeError("cut before COMMIT")

        assert lease._store.list_unindexed_blobs() == (digest,)

        workspaces, blobs = _reclaim_orphans(lease._store)

        assert workspaces == ("orphan2",)
        assert blobs == (digest,)
        assert lease._store.list_unindexed_blobs() == ()
