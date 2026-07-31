"""Tier 4: the one thing injection cannot prove (design §9.4).

FS_CASEFOLD_FL corresponds to actual folding. Requires ATOMS_CASEFOLD_VOLUME.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from atoms.core.errors import ProjectApprovalRefused
from atoms.fs.lookup import LookupProof, inherited_constraints, read_lookup_constraints
from atoms.fs.resolve import PathResolver


def test_the_folded_directory_really_folds(mixed_policy):
    _, folded = mixed_policy
    (folded / "a").write_text("x")
    assert (folded / "A").exists()


def test_the_plain_sibling_does_not_fold(mixed_policy):
    plain, _ = mixed_policy
    (plain / "a").write_text("x")
    assert not (plain / "A").exists()


def test_the_flag_and_the_behaviour_agree(mixed_policy):
    plain, folded = mixed_policy
    for path, expected in (
        (plain, LookupProof.EXACT_BYTES),
        (folded, LookupProof.UNREPRODUCIBLE_CASEFOLD),
    ):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            assert read_lookup_constraints(fd, "ext4").lookup_proof is expected
        finally:
            os.close(fd)


def test_a_directory_created_under_the_plain_sibling_inherits_exact_bytes(mixed_policy):
    """Stronger than a feature-less volume: the filesystem *can* casefold here."""
    plain, _ = mixed_policy
    (plain / "child").mkdir()
    parent_fd = os.open(plain, os.O_RDONLY | os.O_DIRECTORY)
    child_fd = os.open(plain / "child", os.O_RDONLY | os.O_DIRECTORY)
    try:
        parent = read_lookup_constraints(parent_fd, "ext4")
        assert read_lookup_constraints(child_fd, "ext4") == inherited_constraints(
            parent, "ext4"
        )
    finally:
        os.close(parent_fd)
        os.close(child_fd)


def test_a_path_through_the_folded_directory_refuses(
    mixed_policy, casefold_bound_volume
):
    """The point of the tier, and the reason the pair sits on one filesystem.

    Reading the flag proves read_lookup_constraints sees it. Only this proves the
    policy is decided per directory rather than per mount.
    """
    with casefold_bound_volume() as binding:
        resolver = PathResolver(binding)
        with pytest.raises(ProjectApprovalRefused) as caught:
            resolver.resolve("folded/leaf")
        assert "casefold" in str(caught.value).lower()


def test_a_path_through_the_plain_sibling_resolves(
    mixed_policy, casefold_bound_volume
):
    with casefold_bound_volume() as binding:
        resolver = PathResolver(binding)
        prefix = resolver.resolve("plain/leaf")
        assert [hop.declared_component for hop in prefix.hops] == ["plain"]
        assert prefix.frontier_name == "leaf"


def test_the_casefold_flag_cannot_be_changed_on_a_non_empty_directory(casefold_volume):
    """An ext4 behavior record. It backs no safety claim.

    chattr(1) says the attribute can only be *changed* — set or cleared — on an empty
    directory, so this says nothing about the case §6.5 turns on: an empty directory
    whose proof flips while its inode stays equal. Re-reading constraints on every
    traversal is what covers that; this is not a second mechanism.
    """
    occupied = casefold_volume / "occupied"
    occupied.mkdir()
    (occupied / "child").write_text("x")
    completed = subprocess.run(
        ["chattr", "+F", str(occupied)], capture_output=True, text=True, check=False
    )
    assert completed.returncode != 0
