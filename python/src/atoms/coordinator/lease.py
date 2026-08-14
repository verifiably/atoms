"""The lease value and the protocol that runs over it (design §5)."""

from __future__ import annotations

from dataclasses import dataclass

from atoms.fs.binding import ProjectBinding
from atoms.store.connection import Store


@dataclass(frozen=True, slots=True)
class Lease:
    """Borrowed resources, not owned ones.

    `_recovery_lease` closes both in reverse acquisition order; a `Lease` that outlives
    its `with` block therefore references spent objects, and every A5a call through it
    refuses. The escape is caught by the layer below rather than by a flag here.
    """

    _binding: ProjectBinding
    _store: Store


def _reclaim_orphans(store: Store) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Ledger #23: reclaim authority §7.3's pre-COMMIT leaves under the held lock.

    A workspace is orphan exactly when no `transaction_record` row names its txid, and
    `list_unindexed_blobs` already means "no `blob` row" -- so nothing a durable record
    references is ever at risk. Returns what was removed, so a caller can assert it.
    """
    removed_workspaces: list[str] = []
    for txid in store.list_workspaces():
        if store.read_record(txid) is not None:
            continue
        store.remove_workspace(store.reopen_workspace(txid))
        removed_workspaces.append(txid)

    removed_blobs: list[str] = []
    for digest in store.list_unindexed_blobs():
        store.remove_unindexed_blob(digest)
        removed_blobs.append(digest)

    return tuple(removed_workspaces), tuple(removed_blobs)
