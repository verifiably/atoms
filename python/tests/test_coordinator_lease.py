"""A5b tier 1 -- the lease's entry order, reclamation, resolution, and duration."""

from __future__ import annotations

import pytest

from atoms.core.errors import ProtocolError


def test_the_lease_yields_a_working_store(leased):
    with leased() as lease:
        assert lease._store.read_active() is None


def test_the_lease_spends_the_store_on_exit(leased):
    with leased() as lease:
        escaped = lease
    with pytest.raises(ProtocolError) as caught:
        escaped._store.read_active()
    assert "closed" in str(caught.value)


def test_the_lease_holds_no_public_binding_or_store(leased):
    with leased() as lease:
        assert not hasattr(lease, "binding")
        assert not hasattr(lease, "store")
