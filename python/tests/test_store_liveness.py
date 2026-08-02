"""Design §5.4, §7.7, and §7.8 -- the liveness gate, transaction ownership, and close."""

from __future__ import annotations

import sqlite3

import pytest

from atoms.core.errors import ProtocolError
from atoms.store.connection import open_store
from tests.store_support import RELEASES


def test_open_store_creates_then_reopens(store_on):
    with store_on() as binding:
        with open_store(binding) as store, store.transaction():
            pass
        with open_store(binding) as store, store.transaction():
            pass


def test_a_nested_transaction_is_refused(opened_store):
    with opened_store.transaction(), pytest.raises(ProtocolError) as caught, opened_store.transaction():
        pass
    assert "nest" in str(caught.value).lower()


def test_a_retained_transaction_is_dead_after_commit(opened_store):
    with opened_store.transaction() as txn:
        pass
    with pytest.raises(ProtocolError):
        txn._require_current()


def test_a_retained_transaction_is_dead_after_rollback(opened_store):
    with pytest.raises(RuntimeError), opened_store.transaction() as txn:
        raise RuntimeError("caller failure")
    with pytest.raises(ProtocolError):
        txn._require_current()


def test_a_stale_transaction_cannot_write_into_a_later_one(opened_store):
    with opened_store.transaction() as first:
        pass
    with opened_store.transaction(), pytest.raises(ProtocolError) as caught:
        first._require_current()
    assert "spent" in str(caught.value).lower() or "current" in str(caught.value).lower()


def test_a_poisoned_transaction_refuses_to_commit(opened_store):
    """Design §7.7. The mechanism here; Task 7 proves it end to end through a real
    mutating method whose failure the caller catches."""
    with pytest.raises(ProtocolError) as caught, opened_store.transaction() as txn:
        txn._poison(RuntimeError("a mutating method raised"))
    assert "poison" in str(caught.value).lower()
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_a_poisoned_transaction_leaves_no_open_transaction(opened_store):
    with pytest.raises(ProtocolError), opened_store.transaction() as txn:
        txn._poison(RuntimeError("a mutating method raised"))
    with opened_store.transaction():
        pass


def test_close_is_idempotent(opened_store):
    opened_store.close()
    opened_store.close()


def test_close_rolls_back_and_spends_an_open_transaction(opened_store):
    """`__enter__` without `__exit__` on purpose: this isolates what `close()` itself
    does. `test_close_inside_a_transaction_body_...` covers the exit."""
    entered = opened_store.transaction()
    txn = entered.__enter__()
    opened_store.close()
    with pytest.raises(ProtocolError):
        txn._require_current()


def test_close_inside_a_transaction_body_raises_protocol_error_not_sqlite(opened_store):
    """The path the `__enter__`-only test above cannot reach (design §7.8).

    `close()` closes the connection, so the exit runs against a closed database. Both
    `connection.in_transaction` and `execute("ROLLBACK")` raise
    `sqlite3.ProgrammingError: Cannot operate on a closed database` there -- measured --
    and either would replace the ProtocolError with one a caller would have to know
    pysqlite's hierarchy to interpret. `_require_current` at the exit names the real
    condition and `_rollback_quietly` refuses to overwrite it.
    """
    with pytest.raises(ProtocolError) as caught, opened_store.transaction() as txn:
        opened_store.close()
    assert "closed" in str(caught.value).lower()
    assert not isinstance(caught.value, sqlite3.Error)
    with pytest.raises(ProtocolError):
        txn._require_current()


def test_a_failed_commit_leaves_no_open_transaction(opened_store, monkeypatch):
    """§7.7's second rule: **every** exit closes the SQLite transaction, COMMIT included.

    A failed COMMIT leaves `in_transaction` true and the next `BEGIN IMMEDIATE` raising
    "cannot start a transaction within a transaction" -- measured -- so a store that
    spent its transaction object and cleared its slot on that path would refuse every
    later transaction, reporting a caller error for a state A5a created. The proof is
    that the *next* transaction opens.

    The failure is injected at the call site rather than by a deferred constraint,
    because this schema has none: `effect.txid REFERENCES transaction_record(txid)` is
    immediate, so the violation raises at the INSERT. What the injection reproduces is
    the state that matters -- the COMMIT statement raised and the transaction is still
    open. It cannot be done by patching `sqlite3.Connection.execute`, which is an
    immutable type: `TypeError: cannot set 'execute' attribute of immutable type`,
    measured. Hence the proxy.
    """
    from tests.store_support import CommitFails

    monkeypatch.setattr(opened_store, "_connection", CommitFails(opened_store._connection))
    with pytest.raises(sqlite3.OperationalError), opened_store.transaction():
        pass
    assert not opened_store._connection.in_transaction
    monkeypatch.undo()
    with opened_store.transaction():
        pass


def test_a_transaction_after_close_raises_protocol_error_not_sqlite(opened_store):
    opened_store.close()
    with pytest.raises(ProtocolError), opened_store.transaction():
        pass


def test_the_store_exposes_no_connection(opened_store):
    assert not any(
        name for name in dir(opened_store) if "connect" in name and not name.startswith("_")
    )


def test_a_released_lock_refuses_every_operation(store_on):
    with store_on() as binding:
        store = open_store(binding)
    with pytest.raises(ProtocolError), store.transaction():
        pass
    store.close()


def test_close_still_succeeds_on_a_dead_binding(store_on):
    with store_on() as binding:
        store = open_store(binding)
    store.close()


@pytest.mark.parametrize("release", RELEASES, ids=("closed_binding", "released_lock"))
def test_lock_released_before_commit_rolls_back(store_on, release):
    from tests.store_support import one_effect_spec, raw_path

    with store_on() as binding:
        path = raw_path(binding)
        store = open_store(binding)
        with pytest.raises(ProtocolError), store.transaction() as txn:
            txn.insert_record("tx1", one_effect_spec())
            release(binding)
        store.close()
        raw = sqlite3.connect(path, isolation_level=None)
        try:
            assert raw.execute("SELECT count(*) FROM transaction_record").fetchone() == (0,)
        finally:
            raw.close()
