"""Authority §7.3 steps 2-4 (design §7)."""

from __future__ import annotations

from atoms.coordinator.admission import _require_admitted, _require_work_slot_free
from atoms.coordinator.lease import Lease
from atoms.core.errors import ProtocolError
from atoms.fs.approval import ProjectApprovedSpec
from atoms.store.blobs import StagedBlob
from atoms.store.workspace import Workspace


def open_workspace(lease: Lease, approved: ProjectApprovedSpec) -> Workspace:
    """Ledger #19: re-resolve the work base before creating anything under it."""
    _require_admitted(lease, approved)
    if approved.work_base is not None:
        _require_work_slot_free(lease, approved)
    return lease._store.create_workspace(approved.txid)


def prepare_transaction(
    lease: Lease,
    approved: ProjectApprovedSpec,
    workspace: Workspace,
    manifest: tuple[StagedBlob, ...],
) -> None:
    """Apply the gate set, then authority §7.3 steps 2-4 in one transaction."""
    _require_admitted(lease, approved)
    if workspace.txid != approved.txid:
        raise ProtocolError(
            f"workspace txid {workspace.txid!r} does not match the proof's "
            f"{approved.txid!r}; ledger #21 forbids executing a proof under any other "
            "txid"
        )

    with lease._store.transaction() as txn:
        txn.promote_staging(workspace, manifest)
        txn.insert_record(approved.txid, approved.compiled.spec)
        txn.set_active(approved.txid)
