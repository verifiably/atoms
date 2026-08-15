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
                # A record can outlive its blob file: the persistence-cut matrix
                # (`tests/persistence_model.py`) recovers *reconstructed* worlds where a
                # durable `blob` row's bytes were still pending at the cut, and a halted
                # record leaves the transaction active for this phase to read. The
                # missing file is a fact about the world, reported as `null`, not a
                # reason for the observation child to die with a traceback.
                try:
                    fd = store.open_blob(digest)
                except FileNotFoundError:
                    blobs[digest] = None
                    continue
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
    """Recover in a fresh process, then report what the recovery left durable.

    Three phases, and every one of them is an observation the *parent* cannot make
    without leaving its own process: the lease phase runs recovery at entry, the durable
    phase reads the store back through a fresh binding, and the projection phase reads
    the canonical durable projection through the persistence-cut model's own reader
    (`tests/persistence_model.durable_projection`), so the fresh-process placement of a
    cut-matrix cell compares against exactly the document the in-process placement built.

    A lease that halts (`TransactionHalted`) is an outcome, not a crash: the A3-halt
    cells of design §8's placement subset are precisely the ones whose halt diagnostic
    the parent wants to compare, so the halt is caught, reported as `halted`, and the
    remaining phases still run over the world the halt froze.
    """
    from atoms.core.errors import TransactionHalted
    from tests.persistence_model import durable_projection

    backend = LinuxBackend()
    with acquire_project_lock(backend, metadata_root) as probe:
        allowlist = build_test_allowlist(probe, project_root, STORAGE)
    root.CERTIFIED_ALLOWLIST = allowlist
    try:
        lease = _lease_phase(backend, project_root, metadata_root)
        halted = False
    except TransactionHalted:
        lease = None
        halted = True
    print(
        json.dumps(
            {
                "lease": lease,
                "halted": halted,
                "durable": _durable_phase(backend, project_root, metadata_root),
                "projection": durable_projection(
                    project_root, metadata_root, STORAGE, allowlist
                ),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
