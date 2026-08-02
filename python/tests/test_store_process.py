"""Tier 5 -- the durability claim A5a actually makes (design §11.5)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from atoms.core.canonical import canonical_json
from atoms.store.blobs import StagedBlob
from atoms.store.connection import open_store
from atoms.store.records import referenced_digests
from tests.store_support import digest_of, spec_referencing, stage

ROOT = Path(__file__).resolve().parents[1]


def test_a_committed_record_reads_back_identically_in_a_fresh_process(store_on):
    content = b"durable bytes"
    digest = digest_of(content)
    with store_on() as binding:
        project_root = os.readlink(f"/proc/self/fd/{binding.project_root_fd}")
        metadata_root = os.readlink(f"/proc/self/fd/{binding.metadata_root_fd}")
        with open_store(binding) as store:
            with store.create_workspace("tx1") as workspace:
                stage(workspace, "capture", content)
                with store.transaction() as txn:
                    txn.promote_staging(
                        workspace,
                        (
                            StagedBlob(
                                name="capture",
                                digest=digest,
                                byte_len=len(content),
                            ),
                        ),
                    )
                    txn.insert_record("tx1", spec_referencing(content))
                    txn.set_active("tx1")
            written = store.read_record("tx1")
        assert written is not None
        expected = {
            "spec": canonical_json(written.spec),
            "state": written.state.value,
            "journals": [
                [journal.effect_id, journal.state.value]
                for journal in written.journals
            ],
            "blobs": {digest: len(content)},
            "active": "tx1",
        }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.store_child",
            project_root,
            metadata_root,
            "tx1",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == expected, result.stderr


def test_a_committed_record_never_names_a_missing_blob(store_on):
    """The behavioural half of ledger #22."""
    content = b"referenced"
    digest = digest_of(content)
    with store_on() as binding:
        with open_store(binding) as store, store.create_workspace("tx1") as workspace:
            stage(workspace, "capture", content)
            with store.transaction() as txn:
                txn.promote_staging(
                    workspace,
                    (
                        StagedBlob(
                            name="capture", digest=digest, byte_len=len(content)
                        ),
                    ),
                )
                txn.insert_record("tx1", spec_referencing(content))
        with open_store(binding) as reopened:
            record = reopened.read_record("tx1")
            assert record is not None
            resolved = [entry for entry, _ in referenced_digests(record.spec)]
            assert resolved == [digest]
            for entry in resolved:
                os.close(reopened.open_blob(entry))
