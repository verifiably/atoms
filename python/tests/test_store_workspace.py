"""Tier 4 -- workspaces over a real ext4 metadata root (design §8.3, §8.5)."""

from __future__ import annotations

import copy
import errno
import os
import pickle

import pytest

from atoms.core.errors import ProtocolError
from atoms.store.errors import MetadataStoreInvalid
from atoms.store.workspace import Workspace
from tests.store_support import RELEASES, child_dir, metadata_root_snapshot, release_lock, stage


def _child_bytes(parent_fd: int, name: str) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC, dir_fd=parent_fd)
    try:
        return os.read(fd, 4096)
    finally:
        os.close(fd)


def test_prepared_reopen_seams_separate_staging_from_the_work_slot(
    opened_store, store_binding
) -> None:
    from atoms.store.workspace import reopen_work_slot, require_staging_discharged

    opened_store.create_workspace("tx1").close()
    with pytest.raises(MetadataStoreInvalid, match="staging/tx1"):
        require_staging_discharged(opened_store, "tx1")

    with child_dir(store_binding.metadata_root_fd, "staging") as parent_fd:
        os.rmdir("tx1", dir_fd=parent_fd)
    require_staging_discharged(opened_store, "tx1")
    with reopen_work_slot(opened_store, "tx1") as workspace:
        os.fstat(workspace.work_fd)
        with pytest.raises(ProtocolError, match="staging_fd is spent"):
            _ = workspace.staging_fd


def test_prepared_work_slot_absence_is_invalid_store_evidence(
    opened_store, store_binding
) -> None:
    from atoms.store.workspace import reopen_work_slot

    opened_store.create_workspace("tx1").close()
    with child_dir(store_binding.metadata_root_fd, "work") as parent_fd:
        os.rmdir("tx1", dir_fd=parent_fd)

    with pytest.raises(MetadataStoreInvalid, match="work/tx1 is missing"):
        reopen_work_slot(opened_store, "tx1")


def test_creation_makes_both_directories(opened_store, store_binding):
    with opened_store.create_workspace("tx1") as workspace:
        assert workspace.txid == "tx1"
        for parent in ("staging", "work"):
            with child_dir(store_binding.metadata_root_fd, parent) as parent_fd:
                assert "tx1" in os.listdir(parent_fd)


@pytest.mark.parametrize("parent", ["staging", "work"])
def test_workspace_parent_symlink_is_refused_without_outside_mutation(
    opened_store, store_binding, tmp_path, parent
):
    outside = tmp_path / f"outside-{parent}"
    outside.mkdir()
    (outside / "sentinel").write_bytes(b"untouched")
    owned = f"{parent}-owned"
    root = store_binding.metadata_root_fd
    os.rename(parent, owned, src_dir_fd=root, dst_dir_fd=root)
    os.symlink(str(outside), parent, dir_fd=root)

    with pytest.raises(OSError) as caught:
        opened_store.create_workspace("tx1")

    assert caught.value.errno == errno.ELOOP
    assert {entry.name for entry in outside.iterdir()} == {"sentinel"}
    with child_dir(root, owned) as owned_fd:
        assert "tx1" not in os.listdir(owned_fd)


@pytest.mark.parametrize("operation", ["create", "reopen", "remove"])
def test_first_workspace_parent_fd_closes_when_the_second_open_fails(
    opened_store, monkeypatch, operation
):
    from atoms.store import workspace as workspace_module

    workspace = None
    if operation in ("reopen", "remove"):
        workspace = opened_store.create_workspace("tx1")
        if operation == "reopen":
            workspace.close()

    real = workspace_module._parent_fd
    opened: list[int] = []

    def fail_work_parent(store, name):
        if name == workspace_module.WORK_PARENT:
            raise OSError(errno.EIO, "second parent refused")
        fd = real(store, name)
        opened.append(fd)
        return fd

    monkeypatch.setattr(workspace_module, "_parent_fd", fail_work_parent)
    with pytest.raises(OSError) as caught:
        if operation == "create":
            opened_store.create_workspace("tx1")
        elif operation == "reopen":
            opened_store.reopen_workspace("tx1")
        else:
            assert workspace is not None
            opened_store.remove_workspace(workspace)

    assert caught.value.errno == errno.EIO
    assert len(opened) == 1
    with pytest.raises(OSError) as closed:
        os.fstat(opened[0])
    assert closed.value.errno == errno.EBADF


@pytest.mark.parametrize("operation", ["create", "reopen", "remove"])
def test_workspace_parent_cleanup_attempts_both_and_preserves_the_first_failure(
    opened_store, monkeypatch, operation
):
    from atoms.fs.linux import LinuxBackend
    from atoms.store import workspace as workspace_module

    workspace = None
    if operation in ("reopen", "remove"):
        workspace = opened_store.create_workspace("tx1")
        if operation == "reopen":
            workspace.close()

    backend = opened_store._binding.backend
    assert isinstance(backend, LinuxBackend)
    parents: list[int] = []
    attempted: list[int] = []
    real_parent_fds = workspace_module._parent_fds
    real_close = LinuxBackend.close_fd

    def recording_parent_fds(store):
        result = real_parent_fds(store)
        parents.extend(result)
        return result

    def failing_close(self, fd):
        real_close(self, fd)
        if fd not in parents:
            return
        attempted.append(fd)
        code = errno.EIO if fd == parents[0] else errno.ENOSPC
        raise OSError(code, "injected workspace-parent close failure")

    with monkeypatch.context() as patched:
        patched.setattr(workspace_module, "_parent_fds", recording_parent_fds)
        patched.setattr(LinuxBackend, "close_fd", failing_close)
        with pytest.raises(OSError) as caught:
            if operation == "create":
                opened_store.create_workspace("tx1")
            elif operation == "reopen":
                opened_store.reopen_workspace("tx1")
            else:
                assert workspace is not None
                opened_store.remove_workspace(workspace)
    try:
        assert caught.value.errno == errno.EIO
        assert attempted == parents
        for fd in parents:
            with pytest.raises(OSError) as closed:
                os.fstat(fd)
            assert closed.value.errno == errno.EBADF
    finally:
        for fd in parents:
            try:
                os.fstat(fd)
            except OSError as caught:
                assert caught.errno == errno.EBADF
                continue
            backend.close_fd(fd)


def test_creation_refuses_when_either_directory_exists(opened_store):
    opened_store.create_workspace("tx1").close()
    with pytest.raises(ProtocolError) as caught:
        opened_store.create_workspace("tx1")
    assert "staging/tx1 already exists" in str(caught.value)


def test_creation_refuses_when_only_the_work_directory_exists(opened_store, store_binding):
    with child_dir(store_binding.metadata_root_fd, "work") as work_parent:
        os.mkdir("tx1", dir_fd=work_parent)
    with pytest.raises(ProtocolError) as caught:
        opened_store.create_workspace("tx1")
    assert "work/tx1 already exists" in str(caught.value)


def test_creation_gate_before_the_first_mkdir_leaves_both_names_absent(
    store_on, monkeypatch
):
    from atoms.store import workspace as workspace_module
    from atoms.store.connection import open_store

    with store_on() as binding, metadata_root_snapshot(binding) as root:
        store = open_store(binding)
        real = workspace_module._stat_or_none
        calls = 0

        def release_after_second_preflight(parent_fd, name):
            nonlocal calls
            result = real(parent_fd, name)
            calls += 1
            if calls == 2:
                release_lock(binding)
            return result

        monkeypatch.setattr(workspace_module, "_stat_or_none", release_after_second_preflight)
        with pytest.raises(ProtocolError) as caught:
            store.create_workspace("tx1")
        assert "lock" in str(caught.value).lower()
        for parent in ("staging", "work"):
            with child_dir(root, parent) as parent_fd:
                assert "tx1" not in os.listdir(parent_fd)
        store.close()


def test_creation_gate_before_the_second_mkdir_leaves_only_staging(store_on, monkeypatch):
    from atoms.store import workspace as workspace_module
    from atoms.store.connection import open_store

    with store_on() as binding, metadata_root_snapshot(binding) as root:
        store = open_store(binding)
        real = workspace_module.os.mkdir
        calls: list[str] = []

        def release_after_first_mkdir(name, mode=0o777, *, dir_fd=None):
            result = real(name, mode, dir_fd=dir_fd)
            calls.append(name)
            if len(calls) == 1:
                release_lock(binding)
            return result

        monkeypatch.setattr(workspace_module.os, "mkdir", release_after_first_mkdir)
        with pytest.raises(ProtocolError) as caught:
            store.create_workspace("tx1")
        assert "lock" in str(caught.value).lower()
        assert calls == ["tx1"]
        with child_dir(root, "staging") as staging:
            assert "tx1" in os.listdir(staging)
        with child_dir(root, "work") as work:
            assert "tx1" not in os.listdir(work)
        store.close()


def test_creation_flushes_both_workspace_parents(opened_store, store_binding, monkeypatch):
    backend = store_binding.backend
    real = backend.flush_directory
    flushed: list[int] = []
    with child_dir(store_binding.metadata_root_fd, "staging") as staging:
        staging_inode = os.fstat(staging).st_ino
    with child_dir(store_binding.metadata_root_fd, "work") as work:
        work_inode = os.fstat(work).st_ino

    def record(fd: int) -> None:
        flushed.append(os.fstat(fd).st_ino)
        real(fd)

    monkeypatch.setattr(backend, "flush_directory", record)
    opened_store.create_workspace("tx1").close()
    assert flushed == [staging_inode, work_inode]


def test_the_anchors_are_usable_by_a6_and_a7(opened_store):
    with opened_store.create_workspace("tx1") as workspace:
        stage(workspace, "capture", b"bytes")
        os.mkdir("built", dir_fd=workspace.work_fd)
        assert "capture" in os.listdir(workspace.staging_fd)
        assert "built" in os.listdir(workspace.work_fd)


def test_reading_an_anchor_after_close_raises_rather_than_returning_a_stale_integer(opened_store):
    workspace = opened_store.create_workspace("tx1")
    workspace.close()
    with pytest.raises(ProtocolError):
        _ = workspace.staging_fd
    with pytest.raises(ProtocolError):
        _ = workspace.work_fd


def test_close_is_idempotent(opened_store):
    workspace = opened_store.create_workspace("tx1")
    workspace.close()
    workspace.close()


def test_close_attempts_both_anchors_and_clears_ownership_after_failure(
    opened_store, monkeypatch
):
    from atoms.fs.linux import LinuxBackend

    workspace = opened_store.create_workspace("tx1")
    owned = [workspace.staging_fd, workspace.work_fd]
    backend = opened_store._binding.backend
    assert isinstance(backend, LinuxBackend)
    attempted: list[int] = []
    real_close = LinuxBackend.close_fd

    def failing_close(self, fd):
        attempted.append(fd)
        real_close(self, fd)
        code = errno.EIO if fd == owned[0] else errno.ENOSPC
        raise OSError(code, "injected workspace close failure")

    try:
        with monkeypatch.context() as patched:
            patched.setattr(LinuxBackend, "close_fd", failing_close)
            with pytest.raises(OSError) as caught:
                workspace.close()
        assert caught.value.errno == errno.EIO
        assert attempted == owned
        assert workspace._closed
        assert (workspace._staging_fd, workspace._work_fd) == (None, None)
        assert workspace not in opened_store._workspaces
        for fd in owned:
            with pytest.raises(OSError) as closed:
                os.fstat(fd)
            assert closed.value.errno == errno.EBADF
    finally:
        for fd in owned:
            try:
                os.fstat(fd)
            except OSError as caught:
                assert caught.errno == errno.EBADF
                continue
            backend.close_fd(fd)
        workspace._staging_fd = None
        workspace._work_fd = None
        opened_store._workspaces.discard(workspace)


@pytest.mark.parametrize("disposition", ["both", "staging", "work"])
def test_reopen_accepts_every_legal_disposition(opened_store, store_binding, disposition):
    opened_store.create_workspace("tx1").close()
    root = store_binding.metadata_root_fd
    if disposition == "staging":
        os.rmdir("work/tx1", dir_fd=root)
    if disposition == "work":
        os.rmdir("staging/tx1", dir_fd=root)
    with opened_store.reopen_workspace("tx1") as workspace:
        if disposition in ("both", "staging"):
            assert isinstance(workspace.staging_fd, int)
        else:
            with pytest.raises(ProtocolError):
                _ = workspace.staging_fd
        if disposition in ("both", "work"):
            assert isinstance(workspace.work_fd, int)
        else:
            with pytest.raises(ProtocolError):
                _ = workspace.work_fd


def test_reopen_refuses_when_neither_directory_exists(opened_store):
    with pytest.raises(ProtocolError):
        opened_store.reopen_workspace("tx1")


def test_reopen_refuses_a_symlink_at_a_workspace_name(opened_store, store_binding, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "someone-elses-file").write_bytes(b"x")
    with child_dir(store_binding.metadata_root_fd, "staging") as parent_fd:
        os.symlink(str(outside), "tx1", dir_fd=parent_fd)
    with pytest.raises(MetadataStoreInvalid) as caught:
        opened_store.reopen_workspace("tx1")
    assert "not a directory" in str(caught.value)
    with child_dir(store_binding.metadata_root_fd, "staging") as parent_fd:
        assert "tx1" in os.listdir(parent_fd)
    assert (outside / "someone-elses-file").exists()


def test_reopen_refuses_a_regular_file_at_a_workspace_name(opened_store, store_binding):
    with child_dir(store_binding.metadata_root_fd, "work") as parent_fd:
        os.close(os.open("tx1", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=parent_fd))
    with pytest.raises(MetadataStoreInvalid):
        opened_store.reopen_workspace("tx1")


def test_reopen_closes_the_staging_half_when_the_work_half_refuses(
    opened_store, store_binding
):
    from tests.store_support import open_descriptor_count

    opened_store.create_workspace("tx1").close()
    with child_dir(store_binding.metadata_root_fd, "work") as parent_fd:
        os.rmdir("tx1", dir_fd=parent_fd)
        os.symlink("/tmp", "tx1", dir_fd=parent_fd)
    before = open_descriptor_count()
    with pytest.raises(MetadataStoreInvalid):
        opened_store.reopen_workspace("tx1")
    assert open_descriptor_count() == before


def test_close_closes_every_workspace_the_store_issued(opened_store):
    first = opened_store.create_workspace("tx1")
    second = opened_store.create_workspace("tx2")
    second.close()
    opened_store.close()
    assert first._closed
    assert (first._staging_fd, first._work_fd) == (None, None)
    with pytest.raises(ProtocolError):
        _ = first.staging_fd
    first.close()


def test_every_txid_list_workspaces_reports_is_one_reopen_accepts(opened_store, store_binding):
    opened_store.create_workspace("tx1").close()
    opened_store.create_workspace("tx2").close()
    os.rmdir("work/tx2", dir_fd=store_binding.metadata_root_fd)
    for txid in opened_store.list_workspaces():
        opened_store.reopen_workspace(txid).close()
    assert set(opened_store.list_workspaces()) == {"tx1", "tx2"}


def test_removal_empties_a_non_empty_staging_directory(opened_store, store_binding):
    with opened_store.create_workspace("tx1") as workspace:
        for name in ("a", "b", "c"):
            stage(workspace, name, name.encode())
        opened_store.remove_workspace(workspace)
    assert opened_store.list_workspaces() == ()
    with child_dir(store_binding.metadata_root_fd, "staging") as parent_fd:
        assert "tx1" not in os.listdir(parent_fd)


def test_removal_refuses_a_non_empty_work_directory(opened_store):
    with opened_store.create_workspace("tx1") as workspace:
        os.mkdir("partial", dir_fd=workspace.work_fd)
        with pytest.raises(ProtocolError):
            opened_store.remove_workspace(workspace)
    assert "tx1" in opened_store.list_workspaces()


def test_removal_preflights_work_before_unlinking_staged_bytes(opened_store):
    with opened_store.create_workspace("tx1") as workspace:
        staged = {"a": b"one", "b": b"two"}
        for name, content in staged.items():
            stage(workspace, name, content)
        os.mkdir("partial", dir_fd=workspace.work_fd)
        with pytest.raises(ProtocolError) as caught:
            opened_store.remove_workspace(workspace)
        assert "work/tx1/ is not empty" in str(caught.value)
        assert {
            name: _child_bytes(workspace.staging_fd, name) for name in staged
        } == staged
        assert os.listdir(workspace.work_fd) == ["partial"]


def test_removal_is_not_idempotent(opened_store):
    workspace = opened_store.create_workspace("tx1")
    opened_store.remove_workspace(workspace)
    with pytest.raises(ProtocolError) as caught:
        opened_store.remove_workspace(workspace)
    assert "workspace is closed" in str(caught.value)


def test_removal_gate_before_each_staging_unlink_preserves_later_files(store_on, monkeypatch):
    from atoms.store import workspace as workspace_module
    from atoms.store.connection import open_store

    with store_on() as binding, metadata_root_snapshot(binding) as root:
        store = open_store(binding)
        workspace = store.create_workspace("tx1")
        stage(workspace, "a", b"one")
        stage(workspace, "b", b"two")
        real = workspace_module.os.unlink
        calls: list[str] = []

        def release_after_first_unlink(name, *, dir_fd=None):
            result = real(name, dir_fd=dir_fd)
            calls.append(name)
            if len(calls) == 1:
                release_lock(binding)
            return result

        monkeypatch.setattr(workspace_module.os, "unlink", release_after_first_unlink)
        with pytest.raises(ProtocolError) as caught:
            store.remove_workspace(workspace)
        assert "lock" in str(caught.value).lower()
        assert calls == ["a"]
        with child_dir(root, "staging") as staging, child_dir(staging, "tx1") as tx_staging:
            assert os.listdir(tx_staging) == ["b"]
            assert _child_bytes(tx_staging, "b") == b"two"
        store.close()


def test_removal_gate_before_staging_rmdir_preserves_both_directories(store_on, monkeypatch):
    from atoms.store.connection import open_store

    with store_on() as binding, metadata_root_snapshot(binding) as root:
        store = open_store(binding)
        workspace = store.create_workspace("tx1")
        backend = binding.backend
        real = backend.flush_directory
        flushes = 0

        def release_after_staging_flush(fd):
            nonlocal flushes
            real(fd)
            flushes += 1
            if flushes == 1:
                release_lock(binding)

        monkeypatch.setattr(backend, "flush_directory", release_after_staging_flush)
        with pytest.raises(ProtocolError) as caught:
            store.remove_workspace(workspace)
        assert "lock" in str(caught.value).lower()
        with child_dir(root, "staging") as staging:
            assert "tx1" in os.listdir(staging)
        with child_dir(root, "work") as work:
            assert "tx1" in os.listdir(work)
        store.close()


def test_removal_gate_before_work_rmdir_preserves_work_directory(store_on, monkeypatch):
    from atoms.store.connection import open_store

    with store_on() as binding, metadata_root_snapshot(binding) as root:
        store = open_store(binding)
        workspace = store.create_workspace("tx1")
        backend = binding.backend
        real = backend.flush_directory
        flushes = 0

        def release_after_staging_parent_flush(fd):
            nonlocal flushes
            real(fd)
            flushes += 1
            if flushes == 2:
                release_lock(binding)

        monkeypatch.setattr(backend, "flush_directory", release_after_staging_parent_flush)
        with pytest.raises(ProtocolError) as caught:
            store.remove_workspace(workspace)
        assert "lock" in str(caught.value).lower()
        with child_dir(root, "staging") as staging:
            assert "tx1" not in os.listdir(staging)
        with child_dir(root, "work") as work:
            assert "tx1" in os.listdir(work)
        store.close()


def test_removal_flushes_the_work_parent_last(opened_store, store_binding, monkeypatch):
    workspace = opened_store.create_workspace("tx1")
    backend = store_binding.backend
    real = backend.flush_directory
    flushed: list[int] = []
    with child_dir(store_binding.metadata_root_fd, "work") as work:
        work_inode = os.fstat(work).st_ino

    def record(fd: int) -> None:
        flushed.append(os.fstat(fd).st_ino)
        real(fd)

    monkeypatch.setattr(backend, "flush_directory", record)
    opened_store.remove_workspace(workspace)
    assert flushed[-1] == work_inode


def test_a_failure_after_the_staging_rmdir_leaves_no_anchor_on_a_gone_directory(
    opened_store, store_binding, monkeypatch
):
    workspace = opened_store.create_workspace("tx1")
    backend = store_binding.backend
    real = backend.flush_directory
    calls: list[int] = []

    def failing(fd: int) -> None:
        calls.append(fd)
        if len(calls) == 2:
            raise OSError(errno.EIO, "injected")
        real(fd)

    monkeypatch.setattr(backend, "flush_directory", failing)
    with pytest.raises(OSError) as caught:
        opened_store.remove_workspace(workspace)
    assert caught.value.errno == errno.EIO

    with pytest.raises(ProtocolError) as caught:
        _ = workspace.staging_fd
    assert "spent" in str(caught.value)
    assert workspace.work_fd >= 0
    assert opened_store.list_workspaces() == ("tx1",)

    monkeypatch.setattr(backend, "flush_directory", real)
    opened_store.remove_workspace(workspace)
    assert opened_store.list_workspaces() == ()


@pytest.mark.parametrize("disposition", ["both", "staging", "work"])
def test_removal_succeeds_on_every_legal_disposition(opened_store, store_binding, disposition):
    opened_store.create_workspace("tx1").close()
    root = store_binding.metadata_root_fd
    if disposition == "staging":
        os.rmdir("work/tx1", dir_fd=root)
    if disposition == "work":
        os.rmdir("staging/tx1", dir_fd=root)
    workspace = opened_store.reopen_workspace("tx1")
    opened_store.remove_workspace(workspace)
    assert opened_store.list_workspaces() == ()


def test_a_foreign_or_forged_workspace_is_refused(opened_store):
    with pytest.raises(TypeError):
        Workspace()
    workspace = opened_store.create_workspace("tx1")
    with pytest.raises(ProtocolError):
        copy.copy(workspace)
    with pytest.raises(ProtocolError):
        copy.deepcopy(workspace)
    with pytest.raises(ProtocolError):
        pickle.dumps(workspace)
    workspace.close()


def test_removal_refuses_a_value_that_is_not_exactly_workspace(opened_store):
    with pytest.raises(ProtocolError) as caught:
        opened_store.remove_workspace(object())
    assert "expected exactly Workspace, got object" in str(caught.value)


def test_a_workspace_from_another_store_over_the_same_binding_is_refused(store_on):
    from atoms.store.connection import open_store

    with store_on() as binding, open_store(binding) as first, open_store(binding) as second:
        workspace = first.create_workspace("tx1")
        with pytest.raises(ProtocolError) as caught:
            second.remove_workspace(workspace)
        assert "different Store over the same binding" in str(caught.value)
        workspace.close()


def test_removal_preserves_the_whole_directory_when_the_invalid_entry_is_last(opened_store):
    with opened_store.create_workspace("tx1") as workspace:
        for name in ("a", "b", "c"):
            stage(workspace, name, name.encode())
        os.mkdir("zz-not-a-file", dir_fd=workspace.staging_fd)
        with pytest.raises(MetadataStoreInvalid):
            opened_store.remove_workspace(workspace)
        assert {"a", "b", "c", "zz-not-a-file"} <= set(os.listdir(workspace.staging_fd))


@pytest.mark.parametrize("parent", ["staging", "work"])
def test_enumeration_refuses_an_entry_no_permitted_producer_could_have_written(
    opened_store, store_binding, parent
):
    with child_dir(store_binding.metadata_root_fd, parent) as parent_fd:
        fd = os.open("stray", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=parent_fd)
        os.close(fd)
        with pytest.raises(MetadataStoreInvalid):
            opened_store.list_workspaces()
        assert "stray" in os.listdir(parent_fd)


def test_enumeration_refuses_a_valid_txid_symlink(opened_store, store_binding, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    with child_dir(store_binding.metadata_root_fd, "staging") as staging:
        os.symlink(str(outside), "tx1", dir_fd=staging)
        with pytest.raises(MetadataStoreInvalid) as caught:
            opened_store.list_workspaces()
        assert "staging/tx1 is not a directory" in str(caught.value)
        assert "tx1" in os.listdir(staging)


@pytest.mark.parametrize("parent", ["staging", "work"])
def test_enumeration_refuses_a_directory_whose_name_is_not_a_txid(
    opened_store, store_binding, parent
):
    with child_dir(store_binding.metadata_root_fd, parent) as parent_fd:
        os.mkdir("not a txid", dir_fd=parent_fd)
        with pytest.raises(MetadataStoreInvalid):
            opened_store.list_workspaces()
        assert "not a txid" in os.listdir(parent_fd)


@pytest.mark.parametrize("bad", [3, None, b"tx", "../escape", "", "x" * 65])
def test_the_txid_is_validated_before_any_mkdir(opened_store, bad):
    with pytest.raises(ProtocolError):
        opened_store.create_workspace(bad)


@pytest.mark.parametrize("release", RELEASES, ids=("closed_binding", "released_lock"))
def test_borrowed_workspace_anchors_name_each_dead_binding_branch(store_on, release):
    from atoms.store.connection import open_store

    with store_on() as binding:
        store = open_store(binding)
        workspace = store.create_workspace("tx1")
        release(binding)
        expected = "closed" if release.__name__ == "close_binding" else "lock"
        for name in ("staging_fd", "work_fd"):
            with pytest.raises(ProtocolError) as caught:
                getattr(workspace, name)
            assert expected in str(caught.value).lower()
        workspace.close()
        workspace.close()
        store.close()


@pytest.mark.parametrize("release", RELEASES, ids=("closed_binding", "released_lock"))
def test_removal_names_each_dead_binding_branch(store_on, release):
    from atoms.store.connection import open_store

    with store_on() as binding:
        store = open_store(binding)
        workspace = store.create_workspace("tx1")
        release(binding)
        with pytest.raises(ProtocolError) as caught:
            store.remove_workspace(workspace)
        expected = "closed" if release.__name__ == "close_binding" else "lock"
        assert expected in str(caught.value).lower()
        workspace.close()
        workspace.close()
        store.close()
