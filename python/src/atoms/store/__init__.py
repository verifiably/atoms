"""A5a — the durable metadata store (design §4.1).

The public surface is exactly five names, re-exported here once the modules that define
them exist. Filled in by Task 12; `_StoreTransaction` is never among them — it is
obtained only by entering `Store.transaction()`.
"""

from __future__ import annotations

__all__: tuple[str, ...] = ()
