"""A5b tier 5 -- what a second process sees (design section 10)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.coordinator_support import AFTER, prepared, spec_digest
from tests.store_support import APPROVAL_EVIDENCE, one_effect_spec

ROOT = Path(__file__).resolve().parents[1]


def _second_process(project_root: str, metadata_root: str) -> dict:
    finished = subprocess.run(
        [sys.executable, "-m", "tests.coordinator_child", project_root, metadata_root],
        check=True,
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=120,
    )
    return json.loads(finished.stdout)


def _roots(lease) -> tuple[str, str]:
    return (
        os.readlink(f"/proc/self/fd/{lease._binding.project_root_fd}"),
        os.readlink(f"/proc/self/fd/{lease._binding.metadata_root_fd}"),
    )


def test_a_second_lease_reclaims_both_kinds_of_orphan_and_spares_the_referenced(
    leased,
):
    from atoms.store.blobs import StagedBlob
    from tests.store_support import digest_of, spec_referencing, stage

    content = b"orphaned by a cut before COMMIT"
    digest = digest_of(content)

    with leased() as lease:
        project_root, metadata_root = _roots(lease)
        lease._store.create_workspace("kept").close()
        with lease._store.transaction() as txn:
            txn.insert_record(
                "kept", one_effect_spec(), approval_evidence=APPROVAL_EVIDENCE
            )

        with lease._store.create_workspace("orphan") as workspace:
            stage(workspace, "b0", content)
            with pytest.raises(RuntimeError), lease._store.transaction() as txn:
                txn.promote_staging(
                    workspace,
                    (StagedBlob(name="b0", digest=digest, byte_len=len(content)),),
                )
                txn.insert_record(
                    "orphan",
                    spec_referencing(content),
                    approval_evidence=APPROVAL_EVIDENCE,
                )
                raise RuntimeError("cut before COMMIT")

        assert lease._store.read_record("orphan") is None
        assert lease._store.list_unindexed_blobs() == (digest,)
        assert set(lease._store.list_workspaces()) == {"kept", "orphan"}

    seen = _second_process(project_root, metadata_root)["lease"]

    assert seen["trapped"] is None
    assert seen["workspaces"] == ["kept"]
    assert seen["unindexed_blobs"] == []
    assert seen["active"] is None


def test_a_published_record_traps_a_fresh_lease_and_survives_intact(leased):
    with leased() as lease:
        project_root, metadata_root = _roots(lease)
        approved = prepared(lease)

    seen = _second_process(project_root, metadata_root)

    assert seen["lease"]["trapped"] == "recovery execution is not implemented until A7"
    assert seen["durable"]["active"] == approved.txid
    assert seen["durable"]["state"] == "prepared"
    assert seen["durable"]["spec"] == spec_digest(approved.compiled.spec)
    assert list(seen["durable"]["blobs"].values()) == [len(AFTER)]


def test_a_cut_inside_preparation_publishes_nothing_across_a_restart(
    leased, monkeypatch
):
    from atoms.coordinator.prepare import open_workspace, prepare_transaction
    from atoms.store.connection import _StoreTransaction
    from tests.coordinator_support import admission_for, stage_manifest

    with leased() as lease:
        project_root, metadata_root = _roots(lease)
        approved = admission_for(lease)
        workspace = open_workspace(lease, approved)
        manifest = stage_manifest(workspace)

        def cut(self, txid):
            raise RuntimeError("cut inside prepare_transaction, before COMMIT")

        monkeypatch.setattr(_StoreTransaction, "set_active", cut)
        with pytest.raises(RuntimeError):
            prepare_transaction(lease, approved, workspace, manifest)
        workspace.close()

    seen = _second_process(project_root, metadata_root)

    assert seen["lease"]["trapped"] is None
    assert seen["lease"]["active"] is None
    assert seen["durable"]["active"] is None
    assert seen["durable"]["spec"] is None
    assert seen["lease"]["unindexed_blobs"] == []
