"""The one refusal A5a adds, and the narrow translation that produces it (design §9)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from atoms.core.errors import AtomsError

SQLITE_CORRUPT = 11
SQLITE_NOTADB = 26


class MetadataStoreInvalid(AtomsError):
    """The durable metadata store cannot be safely interpreted.

    Corruption and forward incompatibility share one type because they share one correct
    response -- stop and preserve evidence -- and the message distinguishes them. Raised
    by the store layer, never as a substitute for ProtocolError, which tells a caller to
    fix its call (authority §11).
    """


@contextmanager
def translated(context: str) -> Iterator[None]:
    """Translate SQLITE_CORRUPT and SQLITE_NOTADB from ONE narrow operation.

    Wrap a single statement, never a protocol: the point of the narrow scope is that the
    result codes the handler can plausibly see are bounded. The default is a bare `raise`,
    so an unrecognized code keeps its own class *and* its traceback and a future SQLite
    code is propagated rather than guessed at.

    The catch has to be sqlite3.DatabaseError, because that is the class SQLite raises for
    the condition being translated -- a garbage file raises exactly DatabaseError with
    sqlite_errorname SQLITE_NOTADB, measured, so no narrower clause reaches it.
    """
    try:
        yield
    except sqlite3.DatabaseError as caught:
        code = getattr(caught, "sqlite_errorcode", None)
        if code is not None and (code & 0xFF) in (SQLITE_CORRUPT, SQLITE_NOTADB):
            raise MetadataStoreInvalid(
                f"the metadata store cannot be interpreted while {context}: {caught}"
            ) from caught
        raise
