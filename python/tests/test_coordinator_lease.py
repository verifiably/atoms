"""A5b tier 1 -- the lease's entry order, reclamation, resolution, and duration."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from atoms.core.errors import ProtocolError
from tests.coordinator_support import project_state
from tests.store_support import APPROVAL_EVIDENCE

_CONTENDER = (
    "import fcntl, sys\n"
    "handle = open(sys.argv[1], 'r+')\n"
    "try:\n"
    "    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
    "except BlockingIOError:\n"
    "    sys.exit(3)\n"
    "sys.exit(0)\n"
)


def _contend(metadata_root: str) -> int:
    return subprocess.run(
        [sys.executable, "-c", _CONTENDER, str(Path(metadata_root) / "lock")],
        check=False,
        timeout=30,
    ).returncode


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


def test_reclamation_removes_orphans_and_spares_referenced_scratch(leased):
    from atoms.coordinator.lease import _reclaim_orphans
    from tests.store_support import one_effect_spec

    with leased() as lease:
        lease._store.create_workspace("orphan1").close()
        lease._store.create_workspace("kept1").close()
        with lease._store.transaction() as txn:
            txn.insert_record(
                "kept1", one_effect_spec(), approval_evidence=APPROVAL_EVIDENCE
            )

        workspaces, blobs = _reclaim_orphans(lease._store)

        assert workspaces == ("orphan1",)
        assert blobs == ()
        assert lease._store.list_workspaces() == ("kept1",)


def test_reclamation_removes_an_unindexed_blob(leased):
    """A blob is unindexed exactly when it is on disk with no `blob` row.

    `promote_staging` renames each blob into `blobs/` and flushes it BEFORE its
    `INSERT_BLOB` runs, so a transaction that rolls back leaves precisely that. This is
    the only way to produce one, and asserting reclamation drains an empty list would
    prove nothing.
    """
    from atoms.coordinator.lease import _reclaim_orphans
    from atoms.store.blobs import StagedBlob
    from tests.store_support import digest_of, spec_referencing, stage

    content = b"orphaned by a cut before COMMIT"
    digest = digest_of(content)

    with leased() as lease:
        with lease._store.create_workspace("orphan2") as workspace:
            stage(workspace, "b0", content)
            with pytest.raises(RuntimeError), lease._store.transaction() as txn:
                txn.promote_staging(
                    workspace,
                    (StagedBlob(name="b0", digest=digest, byte_len=len(content)),),
                )
                txn.insert_record(
                    "orphan2",
                    spec_referencing(content),
                    approval_evidence=APPROVAL_EVIDENCE,
                )
                raise RuntimeError("cut before COMMIT")

        assert lease._store.list_unindexed_blobs() == (digest,)

        workspaces, blobs = _reclaim_orphans(lease._store)

        assert workspaces == ("orphan2",)
        assert blobs == (digest,)
        assert lease._store.list_unindexed_blobs() == ()


def test_a_live_record_without_its_chain_fails_closed(leased):
    from atoms.chain.errors import ChainStateInvalid
    from atoms.coordinator.recover import resolve
    from tests.store_support import one_effect_spec

    with leased() as lease:
        with lease._store.transaction() as txn:
            txn.insert_record(
                "tx1", one_effect_spec(), approval_evidence=APPROVAL_EVIDENCE
            )
            txn.set_active("tx1")

        with pytest.raises(ChainStateInvalid):
            resolve(lease._binding, lease._store)


def test_chain_refusal_leaves_the_logical_transaction_state_unchanged(leased):
    from atoms.chain.errors import ChainStateInvalid
    from atoms.coordinator.recover import resolve
    from tests.store_support import one_effect_spec

    with leased() as lease:
        with lease._store.transaction() as txn:
            txn.insert_record(
                "tx1", one_effect_spec(), approval_evidence=APPROVAL_EVIDENCE
            )
            txn.set_active("tx1")
        before = lease._store.read_active()

        with pytest.raises(ChainStateInvalid):
            resolve(lease._binding, lease._store)

        assert lease._store.read_active() == before


def test_no_active_record_resolves_quietly(leased):
    from atoms.coordinator.recover import resolve

    with leased() as lease:
        assert resolve(lease._binding, lease._store) is None


def test_the_lease_holds_the_lock_for_its_whole_duration(leased):
    """Authority §7.1: the lock spans the whole write phase, not merely entry."""
    with leased() as lease:
        metadata_root = os.readlink(f"/proc/self/fd/{lease._binding.metadata_root_fd}")
        assert _contend(metadata_root) == 3
        # Still held after a store write, not merely at entry.
        lease._store.create_workspace("held").close()
        assert _contend(metadata_root) == 3

    assert _contend(metadata_root) == 0


def test_probe_survivors_are_reclaimed_before_an_early_bind_refusal(
    coordinator_on, leased
):
    """Ledger #17, at the one refusal that can discriminate.

    The `FileNotFoundError` carries no filename -- measured `'[Errno 2] No such file or
    directory'` -- so the type is the assertion.
    """
    from atoms.coordinator import root

    ingredients = coordinator_on()
    backend, project_root, metadata_root, storage = ingredients

    # One successful entry, so metadata_root/probe/ exists to be reclaimed from.
    with leased(ingredients):
        pass
    probe_dir = Path(metadata_root) / "probe"
    assert probe_dir.is_dir()
    (probe_dir / "survivor.db").write_text("debris", encoding="utf-8")

    absent = f"{project_root}-does-not-exist"
    assert not Path(absent).exists()
    with pytest.raises(FileNotFoundError), root._recovery_lease(
        backend, absent, metadata_root, storage
    ):
        pass

    assert list(probe_dir.iterdir()) == []


def _trapping_lease(coordinator_on, leased):
    """Ingredients whose project root holds a real file and whose metadata root holds a
    live record, so re-entering `_recovery_lease` over them reaches `_resolve` and traps.

    The file is not incidental: a state comparison over an empty tree has almost nothing
    to compare, and two of the mutations below need an existing path to move.

    The inner `leased(ingredients)` also leaves `root.CERTIFIED_ALLOWLIST` monkeypatched
    for the remainder of the test, since `monkeypatch` is function-scoped and the fixture
    patches rather than restores. That is why each caller below can drive
    `root._recovery_lease` directly and still bind a volume the shipped empty allowlist
    would refuse; the coupling is invisible at the call sites.
    """
    from tests.coordinator_support import make_child_directory
    from tests.store_support import one_effect_spec

    ingredients = coordinator_on()
    with leased(ingredients) as lease:
        make_child_directory(lease)
        fd = os.open(
            "d/f.txt",
            os.O_CREAT | os.O_WRONLY,
            0o644,
            dir_fd=lease._binding.project_root_fd,
        )
        try:
            os.write(fd, b"pre-existing")
        finally:
            os.close(fd)
        with lease._store.transaction() as txn:
            txn.insert_record(
                "tx1", one_effect_spec(), approval_evidence=APPROVAL_EVIDENCE
            )
            txn.set_active("tx1")
    return ingredients


def test_chain_refusal_mutates_no_project_path(coordinator_on, leased):
    from atoms.chain.errors import ChainStateInvalid
    from atoms.coordinator import root

    backend, project_root, metadata_root, storage = _trapping_lease(
        coordinator_on, leased
    )

    before = project_state(project_root)
    with pytest.raises(ChainStateInvalid), root._recovery_lease(
        backend, project_root, metadata_root, storage
    ):
        pass

    assert project_state(project_root) == before


def test_chain_refusal_leaks_no_descriptor(coordinator_on, leased):
    """The trap raises from inside `_recovery_lease`'s generator, before its `yield`,
    so every `with` in the stack unwinds. One count covers the lock fd, both root
    descriptors, and SQLite's own handles -- a leak of any of them moves it."""
    from atoms.chain.errors import ChainStateInvalid
    from atoms.coordinator import root

    backend, project_root, metadata_root, storage = _trapping_lease(
        coordinator_on, leased
    )

    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(ChainStateInvalid), root._recovery_lease(
        backend, project_root, metadata_root, storage
    ):
        pass

    assert len(os.listdir("/proc/self/fd")) == before


def test_chain_refusal_releases_the_project_lock(coordinator_on, leased):
    """Separate from the descriptor count so a contender proves the `flock` itself is
    gone, not merely that the number of open files came back."""
    from atoms.chain.errors import ChainStateInvalid
    from atoms.coordinator import root

    backend, project_root, metadata_root, storage = _trapping_lease(
        coordinator_on, leased
    )

    with pytest.raises(ChainStateInvalid), root._recovery_lease(
        backend, project_root, metadata_root, storage
    ):
        pass

    assert _contend(metadata_root) == 0


def _workspaces_outside_the_lease(
    backend, project_root: str, metadata_root: str, storage
) -> tuple[str, ...]:
    """Read scratch back without going through `_recovery_lease`.

    Once a record is active every lease entry traps, so the lease cannot report on what
    its own entry did. This is the plain lock/bind/open stack `tests/store_child.py`
    already uses, minus the process boundary, with the same test allowlist the `leased`
    fixture builds.
    """
    from atoms.fs.binding import bind_project_volume
    from atoms.fs.lock import acquire_project_lock
    from atoms.store.connection import open_store
    from tests.fs_support import build_test_allowlist

    with acquire_project_lock(backend, metadata_root) as lock:
        allowlist = build_test_allowlist(lock, project_root, storage)
        with bind_project_volume(
            project_root, lock, allowlist=allowlist, storage=storage
        ) as binding, open_store(binding) as store:
            return store.list_workspaces()


def test_reclamation_survives_a_trapping_lease_entry(coordinator_on, leased):
    """`root.py`'s ordering claim, which the trap is the first thing able to test.

    Ledger #23 says reclamation runs at EVERY lease entry, which holds only if it runs
    even when resolution then refuses, halts, or traps. The two reclamation tests above
    call `_reclaim_orphans` directly inside a lease body and so never observe the entry
    path; moving `_reclaim_orphans(store)` after resolution leaves both green and
    breaks only this one.
    """
    from atoms.chain.errors import ChainStateInvalid
    from atoms.coordinator import root
    from tests.store_support import one_effect_spec

    ingredients = coordinator_on()
    backend, project_root, metadata_root, storage = ingredients

    with leased(ingredients) as lease:
        lease._store.create_workspace("orphan3").close()
        with lease._store.transaction() as txn:
            txn.insert_record(
                "tx1", one_effect_spec(), approval_evidence=APPROVAL_EVIDENCE
            )
            txn.set_active("tx1")
        assert lease._store.list_workspaces() == ("orphan3",)

    with pytest.raises(ChainStateInvalid), root._recovery_lease(
        backend, project_root, metadata_root, storage
    ):
        pass

    assert _workspaces_outside_the_lease(
        backend, project_root, metadata_root, storage
    ) == ()
