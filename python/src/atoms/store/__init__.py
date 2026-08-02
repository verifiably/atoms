"""A5a — the durable metadata store (design §4.1).

The public surface is exactly the five names below. `_StoreTransaction` is obtained only
by entering `Store.transaction()` and is deliberately absent.
"""

from __future__ import annotations

from atoms.store.blobs import StagedBlob
from atoms.store.connection import Store, open_store
from atoms.store.records import StoredRecord
from atoms.store.workspace import Workspace

__all__ = ("StagedBlob", "Store", "StoredRecord", "Workspace", "open_store")
