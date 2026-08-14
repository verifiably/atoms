"""The production composition root and the lease's resource stack (design §4.1, §5.1)."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from atoms.coordinator.lease import Lease, _reclaim_orphans
from atoms.coordinator.recover import resolve
from atoms.core.capabilities import Capability
from atoms.fs.approval import _require_capabilities
from atoms.fs.audit import AuditedBackend
from atoms.fs.backend import Backend
from atoms.fs.binding import VolumeEvidence, bind_project_volume
from atoms.fs.bootstrap import reclaim_probe_survivors
from atoms.fs.lock import acquire_project_lock
from atoms.fs.volume import CERTIFIED_ALLOWLIST, StorageProfile
from atoms.store.connection import open_store


def _require_chain_publication(evidence: VolumeEvidence) -> None:
    _require_capabilities(
        frozenset({Capability.NOCLOBBER_TRANSFER}), evidence
    )


@contextlib.contextmanager
def _recovery_lease(
    backend: Backend,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
) -> Iterator[Lease]:
    """Design §5.1's entry order, exactly.

    The one production call site that names `CERTIFIED_ALLOWLIST`. It is empty until A8
    crash-certifies a configuration tuple, so this path refuses every real volume today.
    That is the intended fail-closed behaviour, and it is why ledger #18 is proved by an
    architecture assertion over this call rather than by an end-to-end run.
    """
    backend = AuditedBackend(
        backend, project_root=project_root, metadata_root=metadata_root
    )
    with acquire_project_lock(backend, metadata_root) as lock:
        # Ledger #17 requires reclamation at EVERY lease entry. bind_project_volume
        # reclaims at its own step 4, which it reaches only after checks that can
        # refuse first (binding.py:169-172), so the lease calls it directly.
        reclaim_probe_survivors(lock)
        with bind_project_volume(
            project_root, lock, allowlist=CERTIFIED_ALLOWLIST, storage=storage
        ) as binding, open_store(binding) as store:
            # Reclamation precedes resolution: orphans are unreferenced by definition,
            # and #23 says "at every lease entry", which holds only if it runs even
            # when resolution then refuses, halts, or traps.
            _reclaim_orphans(store)
            resolve(binding, store)
            yield Lease(_binding=binding, _store=store)
