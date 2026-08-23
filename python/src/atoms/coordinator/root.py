"""The production composition root and the lease's resource stack (design §4.1, §5.1)."""

from __future__ import annotations

import contextlib
import errno
from collections.abc import Iterator

from atoms.coordinator import lifecycle
from atoms.coordinator.lease import Lease, _reclaim_orphans
from atoms.coordinator.recover import resolve
from atoms.core.capabilities import Capability
from atoms.core.errors import PreconditionRefused
from atoms.fs.approval import _require_capabilities
from atoms.fs.audit import AuditedBackend
from atoms.fs.backend import Backend
from atoms.fs.binding import VolumeEvidence, bind_project_volume
from atoms.fs.bootstrap import reclaim_probe_survivors
from atoms.fs.lock import (
    acquire_existing_project_lock,
    acquire_project_lock,
    establish_root,
    try_acquire_project_lock,
)
from atoms.fs.volume import CERTIFIED_ALLOWLIST, StorageProfile
from atoms.store.connection import open_store


def _require_chain_publication(evidence: VolumeEvidence) -> None:
    _require_capabilities(
        frozenset({Capability.NOCLOBBER_TRANSFER}), evidence
    )


@contextlib.contextmanager
def _project_lease(
    backend: Backend,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
) -> Iterator[Lease]:
    """Design §5.1's entry order up to, but not including, resolution.

    `CERTIFIED_ALLOWLIST` is named only in this module and reaches the one
    `bind_project_volume` call through `_leased_stack`. It is empty until A8b
    crash-certifies a configuration tuple, so this path refuses every real volume today.
    That is the intended fail-closed behaviour, and it is why ledger #18 is proved by an
    architecture assertion over this call rather than by an end-to-end run.

    The `Lease` this yields is one `resolve` has NOT run over, which weakens that
    invariant from a property of the type to a property of the two constructors. The
    compensation is `test_fs_architecture.py`'s clause making `inspect_chain` the only
    public function permitted to name this manager or `resolve`: structural inspection
    must interpose between reclamation and resolution, and nothing else may.

    Reclamation stays above the split because ledger #17 and #23 require it at EVERY
    lease entry, and neither step reads or writes the chain -- an orphan is unreferenced
    by definition. `resolve` is the opposite: it interprets the chain and can append to
    it, which is exactly what must not happen over damage.
    """
    backend = AuditedBackend(
        backend, project_root=project_root, metadata_root=metadata_root
    )
    with acquire_project_lock(backend, metadata_root) as lock, _leased_stack(lock, project_root, storage) as lease:
        yield lease


@contextlib.contextmanager
def _leased_stack(
    lock, project_root: str, storage: StorageProfile
) -> Iterator[Lease]:
    """Reclaim, bind, open, reclaim — under an already-held metadata lock.

    The one production call site that names `bind_project_volume`, shared by
    the creating entry (`_project_lease`) and the gated one
    (`_writable_recovery_lease`) so ledger #18's single-call-site assertion
    stays a fact about this module rather than two copies of a shape.
    """
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
        yield Lease(_binding=binding, _store=store)


def _lifecycle_view(
    backend: Backend,
    root: str,
    metadata_root: str,
    storage: StorageProfile,
) -> lifecycle.CarrierView:
    """The standalone read-only classification, over the certified allowlist.

    Lives here so `CERTIFIED_ALLOWLIST` keeps its single production naming
    site in this module (and stays patchable where every test patches it).
    """
    return lifecycle.read_state(
        backend, root, metadata_root, storage, CERTIFIED_ALLOWLIST
    )


def _canonical_root_path(backend: Backend, root: str) -> str | None:
    """The guarded normalized spelling, or None for a root that is not there —
    which can validate no stored binding."""
    try:
        root_fd, root_path, _ = establish_root(backend, root, create=False)
    except OSError as caught:
        if caught.errno in (errno.ENOENT, errno.ENOTDIR):
            return None
        raise
    backend.close_fd(root_fd)
    return root_path


@contextlib.contextmanager
def _writable_recovery_lease(
    backend: Backend,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
) -> Iterator[Lease]:
    """The gated mutator entry (lifecycle design §7).

    One shared writability gate: classify under the held metadata lock with
    reads alone, refuse without the writable grant, and only then activate the
    existing binding/store/recovery stack under that same lock. A non-writable
    root therefore stops before probing, reclamation, resolution, or any
    sidecar write — the read-only entry performs none of them. The creation
    commands do not pass through here; their pre-grant writes are the recorded
    exception, not this gate's business.
    """
    audited = AuditedBackend(
        backend, project_root=project_root, metadata_root=metadata_root
    )
    try:
        lock = acquire_existing_project_lock(audited, metadata_root)
    except OSError as caught:
        if caught.errno in (errno.ENOENT, errno.ENOTDIR):
            raise PreconditionRefused(
                "root lifecycle state metadata-less does not grant writability"
            ) from caught
        raise
    assert lock is not None
    with lock:
        view = lifecycle.classify_carrier_under_lock(
            lock,
            _canonical_root_path(audited, project_root),
            storage,
            CERTIFIED_ALLOWLIST,
        )
        if view.state is not lifecycle.LifecycleState.WRITABLE:
            raise PreconditionRefused(
                f"root lifecycle state {view.state.value} does not grant "
                "writability"
            )
        with _leased_stack(lock, project_root, storage) as lease:
            resolve(lease._binding, lease._store)
            yield lease


@contextlib.contextmanager
def _recovery_lease(
    backend: Backend,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
) -> Iterator[Lease]:
    """Design §5.1's entry order, exactly: the project lease plus resolution.

    Keeps its name, its signature, and its meaning -- "recovery has resolved before this
    yields" -- so `read_chain` design §8's recovery-barrier property is preserved.
    """
    with _project_lease(backend, project_root, metadata_root, storage) as lease:
        resolve(lease._binding, lease._store)
        yield lease


@contextlib.contextmanager
def _claimed_destination_lease(
    backend: Backend,
    dest_root: str,
    dest_metadata_root: str,
    storage: StorageProfile,
) -> Iterator[Lease]:
    """The copy destination's nonblocking lock plus its bound store lease.

    Lives here because the audit facade and the bind call are this module's
    to construct; the copy commands consume only the lease.
    """
    audited = AuditedBackend(
        backend, project_root=dest_root, metadata_root=dest_metadata_root
    )
    lock = try_acquire_project_lock(audited, dest_metadata_root)
    if lock is None:
        raise PreconditionRefused("copy destination lock is busy")
    with lock, _leased_stack(lock, dest_root, storage) as lease:
        yield lease


@contextlib.contextmanager
def _creation_lease(
    backend: Backend,
    project_root: str,
    metadata_root: str,
    storage: StorageProfile,
) -> Iterator[Lease]:
    """A creating, blocking lease with no recovery resolution — the
    serviceability grant's cold-admission stack."""
    audited = AuditedBackend(
        backend, project_root=project_root, metadata_root=metadata_root
    )
    with acquire_project_lock(audited, metadata_root) as lock, _leased_stack(lock, project_root, storage) as lease:
        yield lease
