"""Entry observation and the anchored walk (design §6.3-§6.4)."""

from __future__ import annotations

import errno
import os

import pytest

from atoms.core.errors import (
    PreconditionRefused,
    ProjectApprovalRefused,
    ProtocolError,
)
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
    injected_resolver_on, project_root
):
    """Identity, not spelling: an entry matching the metadata root must refuse."""
    from atoms.fs.resolve import FilesystemIdentity

    (project_root / "sentinel").write_text("x")
    info = os.lstat(project_root / "sentinel")
    with injected_resolver_on() as (resolver, binding):
        resolver._metadata_identity = FilesystemIdentity(
            device=info.st_dev, inode=info.st_ino
        )
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver._observe(binding.project_root_fd, "sentinel", "sentinel")
        assert "metadata root" in str(caught.value)


def test_mount_refusal_precedes_metadata_identity_refusal(
    monkeypatch, injected_resolver_on, project_root
):
    """Mount membership is observed before metadata exclusion (design §6.4)."""
    from atoms.fs.resolve import FilesystemIdentity

    (project_root / "sentinel").write_text("x")
    info = os.lstat(project_root / "sentinel")
    with injected_resolver_on() as (resolver, binding):
        resolver._metadata_identity = FilesystemIdentity(
            device=info.st_dev, inode=info.st_ino
        )
        monkeypatch.setattr(
            "atoms.fs.resolve.read_mount_id", lambda fd: binding.evidence.mount_id + 1
        )
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver._observe(binding.project_root_fd, "sentinel", "sentinel")
        assert "mount" in str(caught.value)


def test_an_entry_on_a_different_mount_refuses(
    monkeypatch, injected_resolver_on, project_root
):
    """A bind mount can share st_dev while carrying a distinct mount id, which is
    exactly why the observation goes through O_PATH and read_mount_id."""
    (project_root / "plain").write_text("x")
    with injected_resolver_on() as (resolver, binding):
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


def test_an_enametoolong_leaf_observation_refuses(
    monkeypatch, injected_resolver_on
):
    """Reachable only by injection: _require_name_fits refuses an over-long name
    first, so the kernel disagreeing with fpathconf has no ordinary fixture."""
    injected = OSError(errno.ENAMETOOLONG, "injected")
    calls = []
    with injected_resolver_on() as (resolver, binding):
        _failing_observation(monkeypatch, calls, injected)
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver._observe(binding.project_root_fd, "wide", "wide")
        assert "name limit" in str(caught.value)
        assert caught.value.__cause__ is injected
        assert [name for name, _, _ in calls] == ["wide"]


@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO, errno.EPERM, errno.EMFILE])
def test_unexpected_observation_errors_propagate_unwrapped(
    monkeypatch, injected_resolver_on, code
):
    injected = OSError(code, "injected")
    calls = []
    with injected_resolver_on() as (resolver, binding):
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


def test_a_fully_existing_chain_resolves_with_an_empty_remainder(
    resolver_on, ext4_project_root
):
    (ext4_project_root / "a" / "b").mkdir(parents=True)
    (ext4_project_root / "a" / "b" / "leaf").write_text("x")
    with resolver_on() as (resolver, _):
        prefix = resolver.resolve("a/b/leaf")
        assert [hop.declared_component for hop in prefix.hops] == ["a", "b"]
        assert prefix.frontier_name == "leaf"
        assert prefix.remainder == ()
        assert prefix.frontier.kind is EntryKind.REGULAR_FILE


def test_an_absent_leaf_yields_an_absent_frontier(resolver_on, ext4_project_root):
    (ext4_project_root / "a").mkdir()
    with resolver_on() as (resolver, _):
        prefix = resolver.resolve("a/missing")
        assert prefix.remainder == ()
        assert prefix.frontier_name == "missing"
        assert isinstance(prefix.frontier, AbsentFrontier)


def test_an_absent_ancestor_stops_the_walk_and_keeps_the_remainder(resolver_on):
    with resolver_on() as (resolver, _):
        prefix = resolver.resolve("a/b/leaf")
        assert prefix.hops == ()
        assert prefix.frontier_name == "a"
        assert prefix.remainder == ("b", "leaf")
        assert isinstance(prefix.frontier, AbsentFrontier)


@pytest.mark.parametrize(
    ("maker", "kind"),
    [
        (lambda base: base.write_text("x"), EntryKind.REGULAR_FILE),
        (lambda base: os.mkfifo(base), EntryKind.OTHER),
    ],
)
def test_a_non_directory_ancestor_becomes_a_blocking_frontier(
    resolver_on, ext4_project_root, maker, kind
):
    maker(ext4_project_root / "a")
    with resolver_on() as (resolver, _):
        prefix = resolver.resolve("a/b/leaf")
        assert prefix.hops == ()
        assert prefix.frontier_name == "a"
        assert prefix.remainder == ("b", "leaf")
        assert prefix.frontier.kind is kind


def test_a_symlink_ancestor_becomes_a_symlink_blocking_frontier(
    resolver_on, ext4_project_root
):
    (ext4_project_root / "real").mkdir()
    os.symlink("real", ext4_project_root / "a")
    with resolver_on() as (resolver, _):
        prefix = resolver.resolve("a/b")
        assert prefix.frontier_name == "a"
        assert prefix.remainder == ("b",)
        assert prefix.frontier.kind is EntryKind.SYMLINK


def test_an_errno_the_observation_contradicts_is_reported_as_drift(
    monkeypatch, injected_resolver_on, project_root
):
    """ENOTDIR with a directory actually present means the entry changed between
    the two calls; that is drift, not a frontier."""
    (project_root / "a").mkdir()
    with injected_resolver_on() as (resolver, binding):
        backend = binding.backend
        real_open_child = backend.open_child_directory

        def refuse(parent_fd, name):
            if name == "a":
                raise OSError(errno.ENOTDIR, "injected")
            return real_open_child(parent_fd, name)

        monkeypatch.setattr(type(backend), "open_child_directory", staticmethod(refuse))
        with pytest.raises(PreconditionRefused) as caught:
            resolver.resolve("a/b")
        assert "changed" in str(caught.value) or "drift" in str(caught.value).lower()


def test_exdev_from_the_traversal_refuses_as_a_mount_crossing(
    monkeypatch, injected_resolver_on, project_root
):
    """Injected because require_rel_path rejects every escape spelling first, so the
    only real EXDEV is a mount crossing — which tier 3's bind-mount child exercises."""
    (project_root / "a").mkdir()
    injected = OSError(errno.EXDEV, "injected")
    calls = []
    with injected_resolver_on() as (resolver, binding):
        backend = binding.backend

        def refuse(parent_fd, name):
            calls.append((parent_fd, name))
            raise injected

        monkeypatch.setattr(type(backend), "open_child_directory", staticmethod(refuse))
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver.resolve("a/b")
        assert "mount boundary" in str(caught.value)
        assert caught.value.__cause__ is injected
        assert calls == [(binding.project_root_fd, "a")]


def test_enametoolong_from_the_traversal_refuses(
    monkeypatch, injected_resolver_on
):
    """The kernel disagreeing with fpathconf is the filesystem's answer, not a defect,
    so it stays a refusal. _require_name_fits refuses first for any name we can
    construct, which is why this branch needs injection."""
    injected = OSError(errno.ENAMETOOLONG, "injected")
    calls = []
    with injected_resolver_on() as (resolver, binding):
        backend = binding.backend

        def refuse(parent_fd, name):
            calls.append((parent_fd, name))
            raise injected

        monkeypatch.setattr(type(backend), "open_child_directory", staticmethod(refuse))
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver.resolve("a/b")
        assert "name limit" in str(caught.value)
        assert caught.value.__cause__ is injected
        assert calls == [(binding.project_root_fd, "a")]


@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO, errno.EPERM, errno.EMFILE])
def test_an_unexpected_traversal_errno_propagates_unwrapped(
    monkeypatch, injected_resolver_on, code
):
    injected = OSError(code, "injected")
    calls = []
    with injected_resolver_on() as (resolver, binding):
        backend = binding.backend

        def refuse(parent_fd, name):
            calls.append((parent_fd, name))
            raise injected

        monkeypatch.setattr(type(backend), "open_child_directory", staticmethod(refuse))
        with pytest.raises(OSError) as caught:
            resolver.resolve("a/b")
        assert caught.value is injected
        assert calls == [(binding.project_root_fd, "a")]


@pytest.mark.parametrize("bad", ["/absolute", "a/../b", ".", "a/", "a//b", "a/\x00b"])
def test_a_malformed_path_is_an_internal_contract_violation(
    injected_resolver_on, bad
):
    with injected_resolver_on() as (resolver, _), pytest.raises(ProtocolError):
        resolver.resolve(bad)


def test_a_malformed_path_is_rejected_before_any_syscall(
    monkeypatch, injected_resolver_on
):
    """The EXDEV interpretation depends on no escape route reaching openat2."""
    with injected_resolver_on() as (resolver, binding):
        calls = []
        backend = binding.backend
        monkeypatch.setattr(
            type(backend),
            "open_child_directory",
            staticmethod(lambda parent_fd, name: calls.append(name)),
        )
        with pytest.raises(ProtocolError):
            resolver.resolve("a/../b")
        assert calls == []


def test_a_component_over_name_max_refuses(injected_resolver_on):
    with injected_resolver_on() as (resolver, _):
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver.resolve("a" * 256)
        assert "NAME_MAX" in str(caught.value) or "name limit" in str(caught.value)


def test_name_max_is_taken_from_the_parent_that_performs_the_lookup(
    monkeypatch, injected_resolver_on, project_root
):
    """Injecting a narrow limit at one hop, not at the root.

    Without this, an implementation that applied the root's name_max to every
    component would pass every other limit test in this file.
    """
    from atoms.fs.lookup import DirectoryConstraints, LookupProof

    (project_root / "a").mkdir()
    with injected_resolver_on() as (resolver, _):
        import atoms.fs.resolve as module

        real = module.read_lookup_constraints
        target = os.stat(project_root / "a").st_ino

        def narrow(fd, filesystem_type):
            if os.fstat(fd).st_ino == target:
                return DirectoryConstraints(
                    lookup_proof=LookupProof.EXACT_BYTES, name_max=3
                )
            return real(fd, filesystem_type)

        monkeypatch.setattr(module, "read_lookup_constraints", narrow)
        # The root still admits the same name, so this is the hop's limit and not
        # the volume's.
        assert resolver.resolve("abcd").frontier_name == "abcd"
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver.resolve("a/abcd")
        assert "NAME_MAX of 3" in str(caught.value)


def test_a_path_over_path_max_refuses(resolver_on):
    with resolver_on() as (resolver, _):
        long_path = "/".join(["a" * 200] * 30)
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver.resolve(long_path)
        assert "PATH_MAX" in str(caught.value)


def test_limits_are_measured_in_encoded_bytes_not_characters(injected_resolver_on):
    """A 200-character UTF-8 name can exceed a 255-byte bound."""
    with injected_resolver_on() as (resolver, _):
        name = "é" * 200  # 400 bytes when encoded
        assert len(name) < 255 < len(os.fsencode(name))
        with pytest.raises(ProjectApprovalRefused):
            resolver.resolve(name)


def _counting_reader(monkeypatch, calls, replacement=None):
    """Patch read_lookup_constraints, recording the inode of every read."""
    import atoms.fs.resolve as module

    real = module.read_lookup_constraints

    def counted(fd, filesystem_type):
        inode = os.fstat(fd).st_ino
        calls.append(inode)
        if replacement is not None:
            substitute = replacement(inode, len(calls))
            if substitute is not None:
                return substitute
        return real(fd, filesystem_type)

    monkeypatch.setattr(module, "read_lookup_constraints", counted)


def test_every_traversal_of_a_directory_re_reads_its_constraints(
    monkeypatch, injected_resolver_on, project_root
):
    """The memo interns; it must not suppress observation (design §6.5).

    FS_CASEFOLD_FL can be set on an empty directory without changing its inode, so a
    cache hit that skips the read can report stale semantics for a lookup that has
    already happened under the new ones — inside a single approval, which is a window
    ledger #19 does not cover.
    """
    (project_root / "a" / "b").mkdir(parents=True)
    (project_root / "a" / "c").mkdir()
    calls: list[int] = []
    with injected_resolver_on() as (resolver, _):
        _counting_reader(monkeypatch, calls)
        resolver.resolve("a/b/x")
        resolver.resolve("a/c/y")
        assert calls.count(os.stat(project_root / "a").st_ino) == 2


def test_the_memo_interns_one_facts_value_per_identity(
    injected_resolver_on, project_root
):
    """Re-reading must not mean re-allocating: A4b-2 compares facts by object."""
    (project_root / "a" / "b").mkdir(parents=True)
    (project_root / "a" / "c").mkdir()
    with injected_resolver_on() as (resolver, _):
        first = resolver.resolve("a/b/x")
        second = resolver.resolve("a/c/y")
        assert first.hops[0].facts is second.hops[0].facts
        assert first.root is second.root


def test_a_changed_name_max_between_two_resolutions_refuses(
    monkeypatch, injected_resolver_on, project_root
):
    from atoms.fs.lookup import DirectoryConstraints, LookupProof

    (project_root / "a").mkdir()
    target = os.stat(project_root / "a").st_ino
    calls: list[int] = []
    with injected_resolver_on() as (resolver, _):
        seen = []

        def substitute(inode, _count):
            if inode != target:
                return None
            seen.append(inode)
            return DirectoryConstraints(
                lookup_proof=LookupProof.EXACT_BYTES,
                name_max=255 if len(seen) == 1 else 200,
            )

        _counting_reader(monkeypatch, calls, substitute)
        resolver.resolve("a/x")
        with pytest.raises(PreconditionRefused) as caught:
            resolver.resolve("a/y")
        assert "lookup constraints" in str(caught.value)


def test_a_proof_that_turns_casefold_between_two_resolutions_refuses(
    monkeypatch, injected_resolver_on, project_root
):
    from atoms.fs.lookup import DirectoryConstraints, LookupProof

    (project_root / "a").mkdir()
    target = os.stat(project_root / "a").st_ino
    calls: list[int] = []
    with injected_resolver_on() as (resolver, _):
        seen = []

        def substitute(inode, _count):
            if inode != target:
                return None
            seen.append(inode)
            if len(seen) == 1:
                return None
            return DirectoryConstraints(
                lookup_proof=LookupProof.UNREPRODUCIBLE_CASEFOLD, name_max=255
            )

        _counting_reader(monkeypatch, calls, substitute)
        resolver.resolve("a/x")
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver.resolve("a/y")
        assert "casefold" in str(caught.value).lower()


def test_the_project_root_is_re_read_on_every_resolution(
    monkeypatch, injected_resolver_on
):
    """Constraints read once at construction would otherwise be reported unchecked
    for the resolver's whole life."""
    calls: list[int] = []
    with injected_resolver_on() as (resolver, binding):
        root_inode = os.fstat(binding.project_root_fd).st_ino
        _counting_reader(monkeypatch, calls)
        resolver.resolve("x")
        resolver.resolve("y")
        assert calls.count(root_inode) == 2


def test_a_changed_project_root_name_max_between_resolutions_refuses(
    monkeypatch, injected_resolver_on
):
    """The root gets the same disagreement check as an intermediate directory.

    Both leaves are absent, so the root is the only directory whose constraints are
    read after the patch. The first read agrees with construction; the second changes
    only name_max and must refuse rather than returning the interned root facts.
    """
    from atoms.fs.lookup import DirectoryConstraints

    with injected_resolver_on() as (resolver, _):
        import atoms.fs.resolve as module

        real = module.read_lookup_constraints
        reads: list[DirectoryConstraints] = []

        def changed(fd, filesystem_type):
            constraints = real(fd, filesystem_type)
            reads.append(constraints)
            if len(reads) == 1:
                return constraints
            return DirectoryConstraints(
                lookup_proof=constraints.lookup_proof,
                name_max=constraints.name_max - 1,
            )

        monkeypatch.setattr(module, "read_lookup_constraints", changed)
        resolver.resolve("x")
        with pytest.raises(PreconditionRefused) as caught:
            resolver.resolve("y")
        assert "lookup constraints" in str(caught.value)
        assert len(reads) == 2


def test_a_project_root_that_turns_casefold_after_construction_refuses(
    monkeypatch, injected_resolver_on
):
    from atoms.fs.lookup import DirectoryConstraints, LookupProof

    with injected_resolver_on() as (resolver, _):
        import atoms.fs.resolve as module

        monkeypatch.setattr(
            module,
            "read_lookup_constraints",
            lambda fd, filesystem_type: DirectoryConstraints(
                lookup_proof=LookupProof.UNREPRODUCIBLE_CASEFOLD, name_max=255
            ),
        )
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver.resolve("x")
        assert "casefold" in str(caught.value).lower()


def test_a_casefold_directory_mid_walk_refuses(
    monkeypatch, injected_resolver_on, project_root
):
    from atoms.fs.lookup import DirectoryConstraints, LookupProof

    (project_root / "a").mkdir()
    with injected_resolver_on() as (resolver, _):
        target = os.stat(project_root / "a").st_ino
        import atoms.fs.resolve as module

        real = module.read_lookup_constraints

        def folded(fd, filesystem_type):
            if os.fstat(fd).st_ino == target:
                return DirectoryConstraints(
                    lookup_proof=LookupProof.UNREPRODUCIBLE_CASEFOLD, name_max=255
                )
            return real(fd, filesystem_type)

        monkeypatch.setattr(module, "read_lookup_constraints", folded)
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver.resolve("a/b")
        assert "casefold" in str(caught.value).lower()


def test_resolution_after_the_binding_closes_refuses(injected_resolver_on):
    with injected_resolver_on() as (resolver, _):
        pass
    with pytest.raises(ProtocolError):
        resolver.resolve("anything")


def test_resolution_after_the_lock_is_released_refuses(
    injected_resolver_after_lock_release,
):
    """A distinct liveness failure from a closed binding: _require_active checks the
    binding's own flag AND lock.held, and only the second has fired here."""
    with pytest.raises(ProtocolError) as caught:
        injected_resolver_after_lock_release.resolve("anything")
    assert "lock" in str(caught.value)


def test_the_walk_leaks_no_descriptor_on_the_success_path(
    resolver_on, ext4_project_root
):
    (ext4_project_root / "a" / "b" / "c").mkdir(parents=True)
    with resolver_on() as (resolver, _):
        before = descriptor_count()
        resolver.resolve("a/b/c/leaf")
        assert descriptor_count() == before


def test_the_walk_leaks_no_descriptor_on_a_refusal_path(
    monkeypatch, resolver_on, ext4_project_root
):
    # The leaf must EXIST. _observe returns AbsentFrontier before it ever calls
    # read_mount_id, so an absent leaf makes this walk succeed and the test assert
    # descriptor hygiene about a path that never refused.
    (ext4_project_root / "a" / "b").mkdir(parents=True)
    (ext4_project_root / "a" / "b" / "leaf").write_text("x")
    with resolver_on() as (resolver, binding):
        monkeypatch.setattr(
            "atoms.fs.resolve.read_mount_id", lambda fd: binding.evidence.mount_id + 1
        )
        before = descriptor_count()
        with pytest.raises(ProjectApprovalRefused):
            resolver.resolve("a/b/leaf")
        assert descriptor_count() == before
