"""Entry observation and the anchored walk (design §6.3-§6.4)."""

from __future__ import annotations

import errno
import os

import pytest

from atoms.core.errors import ProjectApprovalRefused
from atoms.fs.resolve import AbsentFrontier, EntryKind, PresentFrontier
from tests.fs_support import descriptor_count


def test_an_absent_entry_is_reported_absent(resolver_on):
    with resolver_on() as (resolver, binding):
        frontier = resolver._observe(binding.project_root_fd, "missing", "missing")
        assert isinstance(frontier, AbsentFrontier)


def test_a_regular_file_is_observed_with_its_identity(resolver_on, ext4_project_root):
    (ext4_project_root / "plain").write_text("x")
    with resolver_on() as (resolver, binding):
        frontier = resolver._observe(binding.project_root_fd, "plain", "plain")
        info = os.lstat(ext4_project_root / "plain")
        assert isinstance(frontier, PresentFrontier)
        assert frontier.kind is EntryKind.REGULAR_FILE
        assert (frontier.identity.device, frontier.identity.inode) == (
            info.st_dev,
            info.st_ino,
        )


def test_a_symlink_is_observed_rather_than_followed(resolver_on, ext4_project_root):
    (ext4_project_root / "target").write_text("x")
    os.symlink("target", ext4_project_root / "alias")
    with resolver_on() as (resolver, binding):
        frontier = resolver._observe(binding.project_root_fd, "alias", "alias")
        assert frontier.kind is EntryKind.SYMLINK
        assert frontier.identity.inode == os.lstat(ext4_project_root / "alias").st_ino


def test_a_directory_leaf_is_observed_without_being_opened(
    resolver_on, ext4_project_root
):
    (ext4_project_root / "child").mkdir()
    with resolver_on() as (resolver, binding):
        frontier = resolver._observe(binding.project_root_fd, "child", "child")
        assert frontier.kind is EntryKind.DIRECTORY


def test_a_fifo_is_observed_as_other(resolver_on, ext4_project_root):
    os.mkfifo(ext4_project_root / "pipe")
    with resolver_on() as (resolver, binding):
        frontier = resolver._observe(binding.project_root_fd, "pipe", "pipe")
        assert frontier.kind is EntryKind.OTHER


def test_an_entry_whose_identity_is_the_metadata_root_refuses(
    resolver_on, ext4_project_root
):
    """Identity, not spelling: an entry matching the metadata root must refuse."""
    from atoms.fs.resolve import FilesystemIdentity

    (ext4_project_root / "sentinel").write_text("x")
    info = os.lstat(ext4_project_root / "sentinel")
    with resolver_on() as (resolver, binding):
        resolver._metadata_identity = FilesystemIdentity(
            device=info.st_dev, inode=info.st_ino
        )
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver._observe(binding.project_root_fd, "sentinel", "sentinel")
        assert "metadata root" in str(caught.value)


def test_an_entry_on_a_different_mount_refuses(
    monkeypatch, resolver_on, ext4_project_root
):
    """A bind mount can share st_dev while carrying a distinct mount id, which is
    exactly why the observation goes through O_PATH and read_mount_id."""
    (ext4_project_root / "plain").write_text("x")
    with resolver_on() as (resolver, binding):
        monkeypatch.setattr(
            "atoms.fs.resolve.read_mount_id", lambda fd: binding.evidence.mount_id + 1
        )
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver._observe(binding.project_root_fd, "plain", "plain")
        assert "mount" in str(caught.value)


def test_observation_releases_its_descriptor_on_the_success_path(
    resolver_on, ext4_project_root
):
    (ext4_project_root / "plain").write_text("x")
    with resolver_on() as (resolver, binding):
        before = descriptor_count()
        resolver._observe(binding.project_root_fd, "plain", "plain")
        assert descriptor_count() == before


def test_observation_releases_its_descriptor_on_the_refusal_path(
    monkeypatch, resolver_on, ext4_project_root
):
    (ext4_project_root / "plain").write_text("x")
    with resolver_on() as (resolver, binding):
        monkeypatch.setattr(
            "atoms.fs.resolve.read_mount_id", lambda fd: binding.evidence.mount_id + 1
        )
        before = descriptor_count()
        with pytest.raises(ProjectApprovalRefused):
            resolver._observe(binding.project_root_fd, "plain", "plain")
        assert descriptor_count() == before


def _failing_observation(monkeypatch, calls, injected):
    """Fail only the O_PATH observation, passing every other os.open through.

    `atoms.fs.resolve.os` IS the `os` module, so patching through that path replaces
    os.open process-wide. An unconditional replacement breaks pytest's own teardown,
    so the substitute has to recognise the call it is meant to fail.
    """
    real_open = os.open

    def refuse(name, flags, *args, **kwargs):
        if flags & os.O_PATH:
            calls.append((name, flags, kwargs.get("dir_fd")))
            raise injected
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr("atoms.fs.resolve.os.open", refuse)


def test_an_enametoolong_leaf_observation_refuses(monkeypatch, resolver_on):
    """Reachable only by injection: _require_name_fits refuses an over-long name
    first, so the kernel disagreeing with fpathconf has no ordinary fixture."""
    injected = OSError(errno.ENAMETOOLONG, "injected")
    calls = []
    with resolver_on() as (resolver, binding):
        _failing_observation(monkeypatch, calls, injected)
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver._observe(binding.project_root_fd, "wide", "wide")
        assert "name limit" in str(caught.value)
        assert caught.value.__cause__ is injected
        assert [name for name, _, _ in calls] == ["wide"]


@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO, errno.EPERM, errno.EMFILE])
def test_unexpected_observation_errors_propagate_unwrapped(
    monkeypatch, resolver_on, code
):
    injected = OSError(code, "injected")
    calls = []
    with resolver_on() as (resolver, binding):
        _failing_observation(monkeypatch, calls, injected)
        with pytest.raises(OSError) as caught:
            resolver._observe(binding.project_root_fd, "anything", "anything")
        assert caught.value is injected
        assert calls == [
            (
                "anything",
                os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                binding.project_root_fd,
            )
        ]
