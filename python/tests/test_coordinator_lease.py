"""A5b tier 1 -- the lease's entry order, reclamation, resolution, and duration."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from atoms.core.errors import ProtocolError

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
            txn.insert_record("kept1", one_effect_spec())

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
                txn.insert_record("orphan2", spec_referencing(content))
                raise RuntimeError("cut before COMMIT")

        assert lease._store.list_unindexed_blobs() == (digest,)

        workspaces, blobs = _reclaim_orphans(lease._store)

        assert workspaces == ("orphan2",)
        assert blobs == (digest,)
        assert lease._store.list_unindexed_blobs() == ()


def test_a_live_record_traps_at_the_next_lease_entry(leased):
    from atoms.coordinator.lease import _resolve
    from tests.store_support import one_effect_spec

    with leased() as lease:
        with lease._store.transaction() as txn:
            txn.insert_record("tx1", one_effect_spec())
            txn.set_active("tx1")

        with pytest.raises(NotImplementedError) as caught:
            _resolve(lease._store)

        assert str(caught.value) == "recovery execution is not implemented until A7"


def test_the_trap_leaves_the_logical_transaction_state_unchanged(leased):
    from atoms.coordinator.lease import _resolve
    from tests.store_support import one_effect_spec

    with leased() as lease:
        with lease._store.transaction() as txn:
            txn.insert_record("tx1", one_effect_spec())
            txn.set_active("tx1")
        before = lease._store.read_active()

        with pytest.raises(NotImplementedError):
            _resolve(lease._store)

        assert lease._store.read_active() == before


def test_no_active_record_resolves_quietly(leased):
    from atoms.coordinator.lease import _resolve

    with leased() as lease:
        assert _resolve(lease._store) is None


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


def _project_state(project_root: str) -> dict[str, tuple[object, ...]]:
    """The project root and every path under it, with the state a mutation would move.

    Three things beyond kind/mode/content, each closing a hole the others leave open:

    * `st_dev` and `st_ino`, because a path replaced by an inode of identical kind,
      mode, and content is otherwise invisible. These are already how A4b states path
      identity, so this is the project's own vocabulary rather than a new one.
    * the root itself, keyed `"."`, because nothing under it records a `chmod` on it --
      and against an empty root, nothing under it records anything at all.
    * `lstat` throughout, so a symlink is compared as a symlink rather than followed.

    Content is hashed rather than compared inline so a failure message stays readable.
    """
    state: dict[str, tuple[object, ...]] = {}

    def record(full: str) -> None:
        info = os.lstat(full)
        if stat.S_ISLNK(info.st_mode):
            payload: object = os.readlink(full)
        elif stat.S_ISDIR(info.st_mode):
            payload = None
        else:
            payload = hashlib.sha256(Path(full).read_bytes()).hexdigest()
        state[os.path.relpath(full, project_root)] = (
            stat.S_IFMT(info.st_mode),
            stat.S_IMODE(info.st_mode),
            info.st_dev,
            info.st_ino,
            info.st_size,
            payload,
        )

    record(project_root)
    for directory, directories, files in os.walk(project_root):
        directories.sort()
        for name in sorted(directories) + sorted(files):
            record(os.path.join(directory, name))
    return state


def _trapping_lease(coordinator_on, leased):
    """Ingredients whose project root holds a real file and whose metadata root holds a
    live record, so re-entering `_recovery_lease` over them reaches `_resolve` and traps.

    The file is not incidental: a state comparison over an empty tree has almost nothing
    to compare, and two of the mutations below need an existing path to move.
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
            txn.insert_record("tx1", one_effect_spec())
            txn.set_active("tx1")
    return ingredients


def test_the_trap_mutates_no_project_path(coordinator_on, leased):
    from atoms.coordinator import root

    backend, project_root, metadata_root, storage = _trapping_lease(
        coordinator_on, leased
    )

    before = _project_state(project_root)
    with pytest.raises(NotImplementedError) as caught, root._recovery_lease(
        backend, project_root, metadata_root, storage
    ):
        pass

    assert str(caught.value) == "recovery execution is not implemented until A7"
    assert _project_state(project_root) == before


def test_the_trap_leaks_no_descriptor(coordinator_on, leased):
    """The trap raises from inside `_recovery_lease`'s generator, before its `yield`,
    so every `with` in the stack unwinds. One count covers the lock fd, both root
    descriptors, and SQLite's own handles -- a leak of any of them moves it."""
    from atoms.coordinator import root

    backend, project_root, metadata_root, storage = _trapping_lease(
        coordinator_on, leased
    )

    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(NotImplementedError) as caught, root._recovery_lease(
        backend, project_root, metadata_root, storage
    ):
        pass

    assert str(caught.value) == "recovery execution is not implemented until A7"
    assert len(os.listdir("/proc/self/fd")) == before


def test_the_trap_releases_the_project_lock(coordinator_on, leased):
    """Separate from the descriptor count so a contender proves the `flock` itself is
    gone, not merely that the number of open files came back."""
    from atoms.coordinator import root

    backend, project_root, metadata_root, storage = _trapping_lease(
        coordinator_on, leased
    )

    with pytest.raises(NotImplementedError) as caught, root._recovery_lease(
        backend, project_root, metadata_root, storage
    ):
        pass

    assert str(caught.value) == "recovery execution is not implemented until A7"
    assert _contend(metadata_root) == 0
