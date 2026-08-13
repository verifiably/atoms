"""Tier 5 -- the durability claim A5a actually makes (design §11.5)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from atoms.core.canonical import canonical_json
from atoms.store.blobs import StagedBlob
from atoms.store.connection import open_store
from atoms.store.records import referenced_digests
from tests.store_support import (
    APPROVAL_EVIDENCE,
    digest_of,
    replace_spec,
    spec_referencing,
    stage,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("spec", "contents"),
    [
        pytest.param(
            spec_referencing(b"durable postimage"),
            (b"durable postimage",),
            id="create-from-absent",
        ),
        pytest.param(
            replace_spec(before=b"durable preimage", after=b"durable postimage"),
            (b"durable preimage", b"durable postimage"),
            id="replace",
        ),
    ],
)
def test_a_committed_record_reads_all_blobs_in_a_fresh_process(
    store_on, spec, contents
):
    expected_blobs = {digest_of(content): len(content) for content in contents}
    with store_on() as binding:
        project_root = os.readlink(f"/proc/self/fd/{binding.project_root_fd}")
        metadata_root = os.readlink(f"/proc/self/fd/{binding.metadata_root_fd}")
        with open_store(binding) as store:
            with store.create_workspace("tx1") as workspace:
                manifest = []
                for index, content in enumerate(contents):
                    name = f"blob-{index}"
                    digest = digest_of(content)
                    stage(workspace, name, content)
                    manifest.append(
                        StagedBlob(
                            name=name,
                            digest=digest,
                            byte_len=len(content),
                        )
                    )
                with store.transaction() as txn:
                    txn.promote_staging(workspace, tuple(manifest))
                    txn.insert_record(
                        "tx1", spec, approval_evidence=APPROVAL_EVIDENCE
                    )
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
            "blobs": expected_blobs,
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
                txn.insert_record(
                    "tx1",
                    spec_referencing(content),
                    approval_evidence=APPROVAL_EVIDENCE,
                )
        with open_store(binding) as reopened:
            record = reopened.read_record("tx1")
            assert record is not None
            resolved = [entry for entry, _ in referenced_digests(record.spec)]
            assert resolved == [digest]
            for entry in resolved:
                os.close(reopened.open_blob(entry))
