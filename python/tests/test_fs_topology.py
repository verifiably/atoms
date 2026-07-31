"""Tier 2 — topology construction (design §11.2)."""

from __future__ import annotations

from atoms.core.effects import CreateDirectory, CreateFileNoClobber, MoveNoClobber
from atoms.core.fingerprint import DirectoryState
from atoms.core.recovery import (
    PersistentNode,
    ProjectRoot,
    ScratchNode,
    ScratchRole,
    TopologyDirectory,
    WorkRoot,
)
from atoms.fs.topology import (
    ApprovedExistingDirectory,
    ApprovedPlannedDirectory,
    build_topology,
)
from tests.fs_support import (
    EXT4,
    WORK_CONSTRAINTS,
    compiled_for,
    file_state,
    resolved_prefix,
)


def test_an_undeclared_intermediate_becomes_a_topology_directory():
    compiled = compiled_for(CreateFileNoClobber("e1", "a/b/leaf", file_state()))
    prefixes = {"a/b/leaf": resolved_prefix("a/b/leaf", existing_depth=2)}
    resolved = build_topology(compiled, prefixes, EXT4, None)

    parent = resolved.parent_of("a/b/leaf")
    assert isinstance(parent, TopologyDirectory)
    grandparent = resolved.parent_node_of(parent)
    assert isinstance(grandparent, TopologyDirectory)
    assert resolved.parent_node_of(grandparent) == ProjectRoot()


def test_a_declared_intermediate_keeps_its_persistent_node():
    """A3 requires exact persistent coverage, so a declared directory may not also get a
    TopologyDirectory — that would be a second node for one directory."""
    compiled = compiled_for(
        CreateDirectory("mk", "a", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "a/leaf", file_state()),
    )
    prefixes = {
        "a": resolved_prefix("a", existing_depth=0),
        "a/leaf": resolved_prefix("a/leaf", existing_depth=0),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    assert resolved.parent_of("a/leaf") == PersistentNode("a")


def test_a_create_directory_endpoint_is_a_directory_candidate():
    """The half of the candidate set that makes design §5.4's case representable. `A` is
    nobody's lexical prefix, so only its being a CreateDirectory endpoint puts it in the
    key space where `a` can merge into it."""
    compiled = compiled_for(CreateDirectory("mk", "A", DirectoryState(mode=0o755)))
    prefixes = {"A": resolved_prefix("A", existing_depth=0)}
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    assert resolved.directory_node("A") == PersistentNode("A")


def test_the_work_root_exists_exactly_when_a_create_directory_does():
    with_dir = compiled_for(CreateDirectory("mk", "a", DirectoryState(mode=0o755)))
    nodes = build_topology(
        with_dir, {"a": resolved_prefix("a", existing_depth=0)}, EXT4, WORK_CONSTRAINTS
    ).topology.parents
    assert any(edge.node == WorkRoot() for edge in nodes)

    without = compiled_for(CreateFileNoClobber("e1", "leaf", file_state()))
    nodes = build_topology(
        without, {"leaf": resolved_prefix("leaf", existing_depth=0)}, EXT4, None
    ).topology.parents
    assert not any(edge.node == WorkRoot() for edge in nodes)


def test_work_scratch_parents_to_the_work_root_and_others_to_their_path():
    compiled = compiled_for(
        CreateDirectory("mk", "a", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "b/leaf", file_state()),
    )
    prefixes = {
        "a": resolved_prefix("a", existing_depth=0),
        "b/leaf": resolved_prefix("b/leaf", existing_depth=1),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    assert resolved.parent_node_of(ScratchNode("mk", ScratchRole.WORK)) == WorkRoot()
    assert resolved.parent_node_of(
        ScratchNode("e1", ScratchRole.STAGING)
    ) == resolved.parent_of("b/leaf")


def test_move_scratch_parents_to_the_source_not_the_destination():
    """A3's _validate_topology compares against the source's parent specifically."""
    compiled = compiled_for(MoveNoClobber("mv", "src/a", "dst/b", file_state()))
    prefixes = {
        "src/a": resolved_prefix("src/a", existing_depth=1),
        "dst/b": resolved_prefix("dst/b", existing_depth=1),
    }
    resolved = build_topology(compiled, prefixes, EXT4, None)
    assert resolved.parent_node_of(
        ScratchNode("mv", ScratchRole.ANCHOR)
    ) == resolved.parent_of("src/a")


def test_the_directory_partition_is_exactly_existing_root_and_intermediates():
    """Design §7.3's two invariants, asserted as a partition: no declared path can be an
    existing directory (no effect declares a DirectoryState precondition), and no
    undeclared intermediate can be planned (it would need a CreateDirectory naming it,
    which would make it declared)."""
    compiled = compiled_for(
        CreateDirectory("mk", "a", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "x/y/leaf", file_state()),
    )
    prefixes = {
        "a": resolved_prefix("a", existing_depth=0),
        "x/y/leaf": resolved_prefix("x/y/leaf", existing_depth=2),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)

    existing = {
        entry.node
        for entry in resolved.directories
        if isinstance(entry, ApprovedExistingDirectory)
    }
    planned = {
        entry.node
        for entry in resolved.directories
        if isinstance(entry, ApprovedPlannedDirectory)
    }
    parents = {edge.parent for edge in resolved.topology.parents}

    assert existing == {
        node for node in parents if isinstance(node, (ProjectRoot, TopologyDirectory))
    }
    assert planned == {
        node for node in parents if isinstance(node, (WorkRoot, PersistentNode))
    }
    assert not existing & planned


def test_a_directory_that_parents_nothing_is_not_retained():
    """§7.3's partition names the *parent* PersistentNodes. A lone CreateDirectory has
    nothing declared beneath it, so its constraints bound no name and retaining them would
    put a fact in the proof that no later stage can act on."""
    compiled = compiled_for(CreateDirectory("mk", "a", DirectoryState(mode=0o755)))
    resolved = build_topology(
        compiled, {"a": resolved_prefix("a", existing_depth=0)}, EXT4, WORK_CONSTRAINTS
    )
    assert {entry.node for entry in resolved.directories} == {
        ProjectRoot(),
        WorkRoot(),
    }


def test_node_ids_are_reproducible_across_repeated_construction():
    compiled = compiled_for(
        CreateFileNoClobber("e1", "a/b/one", file_state()),
        CreateFileNoClobber("e2", "c/d/two", file_state()),
    )
    prefixes = {
        "a/b/one": resolved_prefix("a/b/one", existing_depth=2),
        "c/d/two": resolved_prefix("c/d/two", existing_depth=2),
    }
    first = build_topology(compiled, prefixes, EXT4, None)
    second = build_topology(compiled, prefixes, EXT4, None)
    assert first.topology == second.topology


def test_two_paths_through_one_physical_directory_share_its_node():
    """Identity keying, which needs no double: two prefixes that resolved to the same
    inode are the same directory whatever they are spelled."""
    compiled = compiled_for(
        CreateFileNoClobber("e1", "d/one", file_state()),
        CreateFileNoClobber("e2", "d/two", file_state()),
    )
    prefixes = {
        "d/one": resolved_prefix("d/one", existing_depth=1),
        "d/two": resolved_prefix("d/two", existing_depth=1),
    }
    resolved = build_topology(compiled, prefixes, EXT4, None)
    assert resolved.parent_of("d/one") == resolved.parent_of("d/two")


def test_every_planned_component_is_asked_about(injected_equivalence):
    """The positive half of the bypass check for construction: each planned prefix must
    be keyed through the helper, so `a` and `b` both appear. The root and any existing
    prefix key by identity and correctly do not."""
    calls = injected_equivalence(lambda name: name)
    compiled = compiled_for(CreateFileNoClobber("e1", "a/b/leaf", file_state()))
    prefixes = {"a/b/leaf": resolved_prefix("a/b/leaf", existing_depth=0)}
    build_topology(compiled, prefixes, EXT4, None)
    assert calls == ["a", "b"]


def test_planned_directories_merge_under_a_folding_key(injected_equivalence):
    """Design §5.4's case. CreateDirectory("A") with an effect on a/x is one directory
    under a folding parent. No LookupProof member is both insensitive and reproducible,
    so the double is the only route to the behaviour (design §6.3.2).

    A2 admits the pair: its phase 4 key is the whole path, and "a" differs from "a/x"."""
    injected_equivalence(str.casefold)
    compiled = compiled_for(
        CreateDirectory("mk", "A", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "a/x", file_state()),
    )
    prefixes = {
        "A": resolved_prefix("A", existing_depth=0),
        "a/x": resolved_prefix("a/x", existing_depth=0),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    assert resolved.parent_of("a/x") == PersistentNode("A")
    assert resolved.directory_node("a") == PersistentNode("A")


def test_the_same_pair_stays_two_directories_under_exact_bytes():
    """The counterpart, without the double: `A` and `a` are two directories, so `a/x`
    hangs off an undeclared intermediate instead. The pair proves the merge is the
    equivalence function's doing rather than an accident of construction — and Task 3
    refuses this one, because nothing creates that intermediate."""
    compiled = compiled_for(
        CreateDirectory("mk", "A", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "a/x", file_state()),
    )
    prefixes = {
        "A": resolved_prefix("A", existing_depth=0),
        "a/x": resolved_prefix("a/x", existing_depth=0),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    assert isinstance(resolved.parent_of("a/x"), TopologyDirectory)
    assert resolved.directory_node("A") == PersistentNode("A")
