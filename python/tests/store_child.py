"""The reader half of design §11.5's tier-5 claim.

Run as `python -m tests.store_child <project_root> <metadata_root> <txid>`; prints one
JSON object describing what a fresh process reads back. A module rather than an inline
`-c` string because it re-runs the whole bind, including acquiring the project lock,
which is precisely the part a same-process reopen skips.
"""

from __future__ import annotations

import json
import os
import sys

from atoms.core.canonical import canonical_json
from atoms.fs.binding import bind_project_volume
from atoms.fs.linux import LinuxBackend
from atoms.fs.lock import acquire_project_lock
from atoms.fs.volume import StorageProfile
from atoms.store.connection import open_store
from atoms.store.records import referenced_digests
from tests.fs_support import build_test_allowlist

STORAGE = StorageProfile(profile_id="atoms-test-profile")


def main(project_root: str, metadata_root: str, txid: str) -> int:
    with acquire_project_lock(LinuxBackend(), metadata_root) as lock:
        allowlist = build_test_allowlist(lock, project_root, STORAGE)
        with bind_project_volume(
            project_root, lock, allowlist=allowlist, storage=STORAGE
        ) as binding, open_store(binding) as store:
            record = store.read_record(txid)
            if record is None:
                raise SystemExit(f"no record for txid {txid!r}")
            blobs = {}
            for digest, byte_len in referenced_digests(record.spec):
                fd = store.open_blob(digest)
                try:
                    blobs[digest] = len(os.read(fd, byte_len + 1))
                finally:
                    os.close(fd)
            active = store.read_active()
    print(
        json.dumps(
            {
                "spec": canonical_json(record.spec),
                "state": record.state.value,
                "journals": [
                    [journal.effect_id, journal.state.value]
                    for journal in record.journals
                ],
                "blobs": blobs,
                "active": None if active is None else active.txid,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:4]))
