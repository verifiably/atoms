"""The fresh-process half of A5b's #17 and #23 claims."""

from __future__ import annotations

import json
import os
import sys

from atoms.coordinator import root
from atoms.fs.binding import bind_project_volume
from atoms.fs.linux import LinuxBackend
from atoms.fs.lock import acquire_project_lock
from atoms.fs.volume import StorageProfile
from atoms.store.connection import open_store
from atoms.store.records import referenced_digests
from tests.coordinator_support import spec_digest
from tests.fs_support import build_test_allowlist

STORAGE = StorageProfile(profile_id="atoms-test-profile")


def _lease_phase(backend, project_root: str, metadata_root: str) -> dict:
    from atoms.coordinator.commands import _registered_root
    from atoms.core.errors import PreconditionRefused

    with root._recovery_lease(
        backend, project_root, metadata_root, STORAGE
    ) as lease:
        active = lease._store.read_active()
        try:
            with _registered_root(lease) as (_, validated):
                chain = {
                    "tip": validated.tip,
                    "digests": [digest for digest, _ in validated.entries],
                }
        except PreconditionRefused:
            chain = None
        return {
            "trapped": None,
            "workspaces": list(lease._store.list_workspaces()),
            "unindexed_blobs": list(lease._store.list_unindexed_blobs()),
            "active": None if active is None else active.txid,
            "chain": chain,
        }


def _durable_phase(backend, project_root: str, metadata_root: str) -> dict:
    with acquire_project_lock(backend, metadata_root) as lock:
        allowlist = build_test_allowlist(lock, project_root, STORAGE)
        with bind_project_volume(
            project_root, lock, allowlist=allowlist, storage=STORAGE
        ) as binding, open_store(binding) as store:
            active = store.read_active()
            if active is None:
                return {"active": None, "state": None, "spec": None, "blobs": {}}
            blobs = {}
            for digest, byte_len in referenced_digests(active.spec):
                fd = store.open_blob(digest)
                try:
                    blobs[digest] = len(os.read(fd, byte_len + 1))
                finally:
                    os.close(fd)
            return {
                "active": active.txid,
                "state": active.state.value,
                "spec": spec_digest(active.spec),
                "blobs": blobs,
            }


def main(project_root: str, metadata_root: str) -> int:
    backend = LinuxBackend()
    with acquire_project_lock(backend, metadata_root) as probe:
        root.CERTIFIED_ALLOWLIST = build_test_allowlist(probe, project_root, STORAGE)
    print(
        json.dumps(
            {
                "lease": _lease_phase(backend, project_root, metadata_root),
                "durable": _durable_phase(backend, project_root, metadata_root),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
