"""work_base_facts(): the existing metadata_root/work base only (design §6.6)."""

from __future__ import annotations

import errno
import os

import pytest

from atoms.core.errors import ProjectApprovalRefused, ProtocolError
from atoms.fs.resolve import PathResolver
from tests.fs_support import descriptor_count


def test_construction_does_not_touch_the_work_base(monkeypatch, ext4_bound_volume):
    """Lazy: an unapprovable work/ must not refuse a spec with no CreateDirectory."""
    with ext4_bound_volume() as binding:
        calls = []
        backend = binding.backend
        real = backend.open_child_directory
        monkeypatch.setattr(
            type(backend),
            "open_child_directory",
            staticmethod(
                lambda parent_fd, name: calls.append(name) or real(parent_fd, name)
            ),
        )
        PathResolver(binding)
        assert "work" not in calls


def test_work_base_facts_reports_the_real_directory(resolver_on, ext4_metadata_root):
    with resolver_on() as (resolver, _):
        facts = resolver.work_base_facts()
        info = os.stat(ext4_metadata_root / "work")
        assert (facts.identity.device, facts.identity.inode) == (
            info.st_dev,
            info.st_ino,
        )


def test_repeated_calls_open_the_directory_once(monkeypatch, resolver_on):
    with resolver_on() as (resolver, binding):
        calls = []
        backend = binding.backend
        real = backend.open_child_directory
        monkeypatch.setattr(
            type(backend),
            "open_child_directory",
            staticmethod(
                lambda parent_fd, name: calls.append(name) or real(parent_fd, name)
            ),
        )
        first = resolver.work_base_facts()
        second = resolver.work_base_facts()
        assert first is second
        assert calls.count("work") == 1


def test_a_cached_result_still_fails_after_the_binding_closes(resolver_on):
    """Liveness is read before the memo, so memoization is not a bypass."""
    with resolver_on() as (resolver, _):
        resolver.work_base_facts()
    with pytest.raises(ProtocolError):
        resolver.work_base_facts()


def test_the_work_base_fails_after_the_lock_is_released(resolver_after_lock_release):
    """The other half of the liveness gate.

    _require_active checks the binding's own flag AND lock.held; only the second has
    fired here. The memo is empty in this case — the populated-memo bypass is what the
    closed-binding test above covers — so between them both gates are proved to sit
    ahead of the memo.
    """
    with pytest.raises(ProtocolError) as caught:
        resolver_after_lock_release.work_base_facts()
    assert "lock" in str(caught.value)


def test_a_failing_release_is_not_cached(monkeypatch, resolver_on):
    """The cache is populated only after the descriptor is released."""
    with resolver_on() as (resolver, _):
        import atoms.fs.resolve as module

        real_close_all = module.close_all
        failures = []

        def failing(fds):
            failures.append(tuple(fds))
            real_close_all(fds)
            raise OSError(errno.EIO, "injected close failure")

        monkeypatch.setattr(module, "close_all", failing)
        with pytest.raises(OSError):
            resolver.work_base_facts()
        monkeypatch.setattr(module, "close_all", real_close_all)
        # The failed call cached nothing, so this one must re-open and succeed.
        assert resolver.work_base_facts() is not None
        assert len(failures) == 1


@pytest.mark.parametrize(
    "code", [errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EXDEV]
)
def test_a_namespace_contradiction_is_an_internal_error(
    monkeypatch, resolver_on, code
):
    with resolver_on() as (resolver, binding):
        backend = binding.backend

        def refuse(parent_fd, name):
            raise OSError(code, "injected")

        monkeypatch.setattr(type(backend), "open_child_directory", staticmethod(refuse))
        with pytest.raises(ProtocolError) as caught:
            resolver.work_base_facts()
        assert "work" in str(caught.value)


@pytest.mark.parametrize("code", [errno.EIO, errno.EMFILE, errno.EACCES, errno.EPERM])
def test_unrelated_system_failures_propagate_unchanged(monkeypatch, resolver_on, code):
    """EIO and EMFILE are not violated invariants and must not be relabelled."""
    injected = OSError(code, "injected")
    calls = []
    with resolver_on() as (resolver, binding):
        backend = binding.backend

        def refuse(parent_fd, name):
            calls.append((parent_fd, name))
            raise injected

        monkeypatch.setattr(type(backend), "open_child_directory", staticmethod(refuse))
        with pytest.raises(OSError) as caught:
            resolver.work_base_facts()
        assert caught.value is injected
        assert calls == [(binding.metadata_root_fd, "work")]


def test_a_work_base_on_another_mount_is_an_internal_error(monkeypatch, resolver_on):
    with resolver_on() as (resolver, binding):
        monkeypatch.setattr(
            "atoms.fs.resolve.read_mount_id", lambda fd: binding.evidence.mount_id + 1
        )
        with pytest.raises(ProtocolError):
            resolver.work_base_facts()


def test_a_casefold_work_base_refuses(monkeypatch, resolver_on):
    from atoms.fs.lookup import DirectoryConstraints, LookupProof

    with resolver_on() as (resolver, _):
        monkeypatch.setattr(
            "atoms.fs.resolve.read_lookup_constraints",
            lambda fd, filesystem_type: DirectoryConstraints(
                lookup_proof=LookupProof.UNREPRODUCIBLE_CASEFOLD, name_max=255
            ),
        )
        with pytest.raises(ProjectApprovalRefused):
            resolver.work_base_facts()


def test_every_failure_path_releases_the_descriptor(monkeypatch, resolver_on):
    with resolver_on() as (resolver, binding):
        monkeypatch.setattr(
            "atoms.fs.resolve.read_mount_id", lambda fd: binding.evidence.mount_id + 1
        )
        before = descriptor_count()
        with pytest.raises(ProtocolError):
            resolver.work_base_facts()
        assert descriptor_count() == before
