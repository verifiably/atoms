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
from tests.store_support import child_dir, stage


def test_creation_makes_both_directories(opened_store, store_binding):
    with opened_store.create_workspace("tx1") as workspace:
        assert workspace.txid == "tx1"
        for parent in ("staging", "work"):
            with child_dir(store_binding.metadata_root_fd, parent) as parent_fd:
                assert "tx1" in os.listdir(parent_fd)


def test_creation_refuses_when_either_directory_exists(opened_store):
    opened_store.create_workspace("tx1").close()
    with pytest.raises(ProtocolError):
        opened_store.create_workspace("tx1")


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


def test_removal_is_not_idempotent(opened_store):
    workspace = opened_store.create_workspace("tx1")
    opened_store.remove_workspace(workspace)
    with pytest.raises(ProtocolError):
        opened_store.remove_workspace(workspace)


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
    with pytest.raises(OSError):
        opened_store.remove_workspace(workspace)

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


def test_a_workspace_from_another_store_over_the_same_binding_is_refused(store_on):
    from atoms.store.connection import open_store

    with store_on() as binding, open_store(binding) as first, open_store(binding) as second:
        workspace = first.create_workspace("tx1")
        with pytest.raises(ProtocolError):
            second.remove_workspace(workspace)
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
