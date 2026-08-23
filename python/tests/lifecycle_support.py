"""Raw carrier fabrication for the lifecycle tiers.

Plain functions, not fixtures (the fixture registry lives in conftest.py).
These write metadata carriers with raw sqlite, deliberately outside every
engine path: the shapes under test are exactly the ones cooperative code
never commits — a pre-lifecycle v2 store, an operation row with no lifecycle
row — plus the exact v2 vintage the migration command accepts.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from atoms.store.schema import APPLICATION_ID


def _fabricate(metadata_root: str, statements: tuple[str, ...], version: int) -> Path:
    root = Path(metadata_root)
    root.mkdir(parents=True, exist_ok=True)
    database = root / "atoms.db"
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        for statement in statements:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {version}")
        connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    finally:
        connection.close()
    os.chmod(database, 0o600)
    return database


def fabricate_v2_store(metadata_root: str) -> Path:
    """An exact pre-lifecycle store: the frozen v2 catalog at user_version 2."""
    from atoms.store.schema import V2_SCHEMA_STATEMENTS

    return _fabricate(metadata_root, V2_SCHEMA_STATEMENTS, 2)


def fabricate_v3_store(metadata_root: str) -> Path:
    """A bare current-version carrier with no lifecycle or operation row."""
    from atoms.store.schema import SCHEMA_STATEMENTS

    return _fabricate(metadata_root, SCHEMA_STATEMENTS, 3)


def claim_operation_id(root: str) -> str:
    """The operation_id a durable root claim names, read with raw json."""
    import json

    payload = (Path(root) / ".#~root-claim").read_bytes()
    return json.loads(payload)["operation_id"]
