"""Tier 4 — approve_for_project end to end against a real ext4 volume (design §11.4)."""

from __future__ import annotations

import os

import pytest

from atoms.core.effects import CreateDirectory, CreateFileNoClobber, DeletePath
from atoms.core.errors import ProjectApprovalRefused
from atoms.core.fingerprint import DirectoryState
from atoms.core.recovery import PersistentNode
from atoms.fs.approval import approve_for_project
from atoms.fs.lookup import read_lookup_constraints
from tests.fs_support import compiled_for, file_state


def test_a_wholly_resolvable_specification_approves(approval_context):
    with approval_context() as (context, binding):
        root = binding.project_root_fd
        os.mkdir("d", dir_fd=root)
        compiled = compiled_for(CreateFileNoClobber("e1", "d/leaf", file_state()))
        proof = approve_for_project(compiled, context)

        assert proof.txid == "tx01"
        assert len(proof.paths) == 1
        assert proof.paths[0].leaf == "leaf"
        assert proof.work_base is None


@pytest.mark.parametrize(
    ("effect", "expected"),
    [
        (CreateFileNoClobber("e1", "leaf", file_state()), 0),
        (CreateDirectory("mk", "made", DirectoryState(mode=0o755)), 1),
    ],
    ids=["no-create-directory", "create-directory"],
)
def test_work_base_facts_is_called_exactly_when_a_directory_is_created(
    approval_context, monkeypatch, effect, expected
):
    """Criterion 7 says *iff*, which is two claims, and `proof.work_base is None` proves
    only the weaker one. An implementation that observed the work base and then discarded
    the observation would satisfy that assertion while still issuing the call the criterion
    forbids. Counting on the method fails on the call rather than on the value.

    The call must be skipped rather than merely ignored for the reason design §6.2 step 7
    gives, inherited from A4b-1 §6.6: `work_base_facts` can refuse — an unapprovable or
    over-constrained `work/` raises — and a specification with no `CreateDirectory` has no
    stake in the work namespace and must not be refused by it. (`metadata_root/work` itself
    always exists by this point: A4a's `ensure_metadata_layout` creates it at bind time,
    `bootstrap.py:11`. What is absent is `work/<txid>`, which A5 creates.)

    Patched on the class, not the instance: approval constructs its own `PathResolver`, so
    a test never holds the instance to patch.
    """
    from atoms.fs.resolve import PathResolver

    calls = 0
    original = PathResolver.work_base_facts

    def counting(self):
        nonlocal calls
        calls += 1
        return original(self)

    monkeypatch.setattr(PathResolver, "work_base_facts", counting)
    with approval_context() as (context, _binding):
        proof = approve_for_project(compiled_for(effect), context)

    assert calls == expected
    assert (proof.work_base is not None) is bool(expected)


def test_approval_writes_nothing_into_project_space(approval_context):
    """Criterion 24. Approval is a judgment: it opens project directories read-only and
    must leave every byte of the tree it judged alone. The specification is chosen to
    exercise the branches that are most tempted to write — a `CreateDirectory` whose
    directory does not exist yet, and a scratch set that A5 will later instantiate.

    Compares a full recursive snapshot including names, types, sizes, inodes, and mtimes
    rather than a bare `listdir`: a same-size rewrite keeps both the entry list and the
    size unchanged.
    """
    with approval_context() as (context, binding):
        root = binding.project_root_fd
        os.mkdir("d", dir_fd=root)
        fd = os.open("d/kept", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=root)
        try:
            os.write(fd, b"payload")
        finally:
            os.close(fd)

        before = _tree_snapshot(root)
        compiled = compiled_for(
            CreateDirectory("mk", "d/made", DirectoryState(mode=0o755)),
            CreateFileNoClobber("e1", "d/made/leaf", file_state()),
        )
        approve_for_project(compiled, context)

        assert _tree_snapshot(root) == before


def _tree_snapshot(root_fd: int, path: str = ".") -> dict[str, tuple]:
    """Every entry beneath `root_fd`, keyed by relative path.

    `st_ino` and `st_mtime_ns` are included so a same-size rewrite or an atomic replace is
    visible, not just a change in the entry list.

    The descriptor is closed explicitly. `os.scandir(fd)` does not take ownership of the
    descriptor it is handed and exiting its context manager does not close it, so relying
    on that leaks one per directory per call.
    """
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=root_fd)
    try:
        info = os.fstat(fd)
        snapshot: dict[str, tuple] = {
            path: (True, info.st_mode, info.st_size, info.st_ino, info.st_mtime_ns)
        }
        with os.scandir(fd) as items:
            for entry in items:
                relative = f"{path}/{entry.name}"
                info = entry.stat(follow_symlinks=False)
                snapshot[relative] = (
                    entry.is_dir(follow_symlinks=False),
                    info.st_mode,
                    info.st_size,
                    info.st_ino,
                    info.st_mtime_ns,
                )
                if entry.is_dir(follow_symlinks=False):
                    snapshot |= _tree_snapshot(root_fd, relative)
        return snapshot
    finally:
        os.close(fd)


def test_work_base_is_retained_and_matches_an_independent_observation(approval_context):
    """Asserted against a fresh fstat and constraints read on metadata_root/work, so the
    retained baseline is checked against the filesystem rather than against the same call
    that produced it."""
    with approval_context() as (context, binding):
        compiled = compiled_for(
            CreateDirectory("mk", "made", DirectoryState(mode=0o755))
        )
        proof = approve_for_project(compiled, context)

        assert proof.work_base is not None
        fd = os.open(
            "work",
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
            dir_fd=binding.metadata_root_fd,
        )
        try:
            info = os.fstat(fd)
            constraints = read_lookup_constraints(
                fd, binding.evidence.configuration.filesystem_type
            )
        finally:
            os.close(fd)

        assert proof.work_base.identity.device == info.st_dev
        assert proof.work_base.identity.inode == info.st_ino
        assert proof.work_base.constraints == constraints


def test_a_file_ancestor_the_timeline_converts_approves_on_disk(approval_context):
    with approval_context() as (context, binding):
        root = binding.project_root_fd
        fd = os.open("p", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=root)
        os.close(fd)
        compiled = compiled_for(
            DeletePath("rm", "p", file_state()),
            CreateDirectory("mk", "p", DirectoryState(mode=0o755)),
            CreateFileNoClobber("e1", "p/q", file_state()),
        )
        approve_for_project(compiled, context)


def test_two_spellings_stay_two_directories_on_a_real_volume(approval_context):
    """Criterion 15's `EXACT_BYTES` half, against a real fixture rather than a synthetic
    table. Physical `a` exists, so `a/x` resolves through it and keys by inode; `A` is a
    planned directory keyed by (root, "A"). ext4 without casefold distinguishes them, so
    approval issues two nodes — and this is the half that needs no injected double,
    because the real volume supplies the relation."""
    with approval_context() as (context, binding):
        root = binding.project_root_fd
        os.mkdir("a", dir_fd=root)
        compiled = compiled_for(
            CreateDirectory("mk", "A", DirectoryState(mode=0o755)),
            CreateFileNoClobber("e1", "a/x", file_state()),
        )
        proof = approve_for_project(compiled, context)

        parent_of_x = next(
            entry.parent_node for entry in proof.paths if entry.path == "a/x"
        )
        assert parent_of_x != PersistentNode("A")
        assert PersistentNode("A") in {edge.node for edge in proof.topology.parents}


def test_a_component_over_name_max_is_refused(approval_context):
    with approval_context() as (context, _binding):
        compiled = compiled_for(
            CreateFileNoClobber("e1", "x" * 300, file_state())
        )
        with pytest.raises(ProjectApprovalRefused) as caught:
            approve_for_project(compiled, context)
        assert "NAME_MAX" in str(caught.value)


def test_a_path_over_path_max_is_refused(approval_context):
    with approval_context() as (context, _binding):
        deep = "/".join(["d"] * 3000)
        compiled = compiled_for(CreateFileNoClobber("e1", deep, file_state()))
        with pytest.raises(ProjectApprovalRefused) as caught:
            approve_for_project(compiled, context)
        assert "PATH_MAX" in str(caught.value)


def test_a_nested_metadata_root_is_refused_through_approval(
    ext4_nested_bound_volume,
):
    """Reached through approve_for_project rather than the resolver, which is what
    ledger #5 asks for."""
    from atoms.fs.approval import ProjectContext

    with ext4_nested_bound_volume() as binding:
        context = ProjectContext(binding=binding, txid="tx01")
        compiled = compiled_for(
            CreateFileNoClobber("e1", "metadata/leaf", file_state())
        )
        with pytest.raises(ProjectApprovalRefused) as caught:
            approve_for_project(compiled, context)
        assert "metadata root" in str(caught.value)
