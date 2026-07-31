"""Tier 2 — topology construction (design §11.2)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from atoms.core.effects import (
    CreateDirectory,
    CreateFileNoClobber,
    DeletePath,
    MoveNoClobber,
    ReplaceFile,
)
from atoms.core.errors import PreconditionRefused, ProjectApprovalRefused
from atoms.core.fingerprint import DirectoryState, SymlinkState
from atoms.core.recovery import (
    PersistentNode,
    ProjectRoot,
    ScratchNode,
    ScratchRole,
    TopologyDirectory,
    WorkRoot,
)
from atoms.fs.resolve import AbsentFrontier, EntryKind, FilesystemIdentity, PresentFrontier
from atoms.fs.topology import (
    ApprovedExistingDirectory,
    ApprovedPlannedDirectory,
    build_topology,
)
from tests.fs_support import (
    EXT4,
    GENERATED_SPECIFICATION_COUNT,
    WORK_CONSTRAINTS,
    compiled_for,
    file_state,
    generated_specifications,
    nonempty_state,
    prefixes_for,
    resolved_prefix,
    work_for,
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


def test_a_replaced_directory_between_resolutions_is_refused():
    compiled = compiled_for(
        CreateFileNoClobber("e1", "d/one", file_state()),
        CreateFileNoClobber("e2", "d/two", file_state()),
    )
    first = resolved_prefix("d/one", existing_depth=1)
    second = resolved_prefix("d/two", existing_depth=1)
    changed = replace(
        second.hops[0].facts,
        identity=FilesystemIdentity(device=41, inode=999_999),
    )
    second = replace(second, hops=(replace(second.hops[0], facts=changed),))

    with pytest.raises(PreconditionRefused):
        build_topology(compiled, {"d/one": first, "d/two": second}, EXT4, None)


@pytest.mark.parametrize(
    "frontier",
    [
        AbsentFrontier(),
        PresentFrontier(
            identity=FilesystemIdentity(device=41, inode=999_999),
            kind=EntryKind.REGULAR_FILE,
        ),
    ],
    ids=["absent", "blocking-file"],
)
def test_a_directory_that_becomes_a_frontier_between_resolutions_is_refused(
    frontier,
):
    compiled = compiled_for(
        CreateFileNoClobber("e1", "d/one", file_state()),
        CreateFileNoClobber("e2", "d/two", file_state()),
    )
    prefixes = {
        "d/one": resolved_prefix("d/one", existing_depth=1),
        "d/two": resolved_prefix("d/two", existing_depth=0, frontier=frontier),
    }

    with pytest.raises(PreconditionRefused):
        build_topology(compiled, prefixes, EXT4, None)


def test_a_live_created_parent_is_still_planned():
    compiled = compiled_for(
        CreateDirectory("mk", "a", DirectoryState(mode=0o755)),
        CreateFileNoClobber("put", "a/leaf", file_state()),
    )
    descendant = resolved_prefix("a/leaf", existing_depth=1, name_max=17)
    endpoint = resolved_prefix(
        "a",
        existing_depth=0,
        name_max=17,
        frontier=PresentFrontier(
            identity=descendant.hops[0].facts.identity,
            kind=EntryKind.DIRECTORY,
        ),
    )
    resolved = build_topology(
        compiled,
        {"a": endpoint, "a/leaf": descendant},
        EXT4,
        WORK_CONSTRAINTS,
    )

    entry = next(
        item for item in resolved.directories if item.node == PersistentNode("a")
    )
    assert isinstance(entry, ApprovedPlannedDirectory)
    assert entry.constraints == WORK_CONSTRAINTS


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
    assert _prepared_snapshot(compiled, resolved).topology == resolved.topology


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


AGREEMENT_CORPUS = {
    "one file in an existing directory": (
        CreateFileNoClobber("e1", "a/b/leaf", file_state()),
    ),
    "a created directory and its child": (
        CreateDirectory("mk", "a", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "a/leaf", file_state()),
    ),
    "nested created directories": (
        CreateDirectory("mk", "a", DirectoryState(mode=0o755)),
        CreateDirectory("mk2", "a/b", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "a/b/leaf", file_state()),
    ),
    "a move across directories": (MoveNoClobber("mv", "src/a", "dst/b", file_state()),),
    "a move within one directory": (MoveNoClobber("mv", "d/a", "d/b", file_state()),),
    "a replace": (ReplaceFile("rp", "d/leaf", nonempty_state(b"old"), nonempty_state(b"new")),),
    "a delete": (DeletePath("rm", "d/leaf", file_state()),),
    "a file ancestor converted to a directory": (
        DeletePath("rm", "p", file_state()),
        CreateDirectory("mk", "p", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "p/q", file_state()),
    ),
    "a symlink removed beside a create": (
        DeletePath("rm", "d/link", SymlinkState(target="x", mode=0o777)),
        CreateFileNoClobber("e1", "d/leaf", file_state()),
    ),
    "every variant at once": (
        DeletePath("rm", "d/old", file_state()),
        CreateDirectory("mk", "d/new", DirectoryState(mode=0o755)),
        CreateFileNoClobber("e1", "d/new/leaf", file_state()),
        ReplaceFile("rp", "d/keep", nonempty_state(b"old"), nonempty_state(b"new")),
        MoveNoClobber("mv", "d/from", "d/new/to", file_state()),
    ),
}
"""Every effect variant, alone and combined, plus the shapes that stress the topology:
nested creation, a move whose endpoints share a parent, and the FILE → ABSENT → DIRECTORY
conversion. Each entry is verified to compile — a case A2 refuses is not a case."""


def _prepared_snapshot(compiled, resolved):
    """Feed a produced topology to A3's own validator.

    This is the strongest single test in the design: _validate_topology independently
    encodes exact persistent and scratch coverage, single-parent-ness, the WorkRoot
    parent rule, the scratch/persistent shared-parent equality, and acyclicity — written
    before A4b-2 existed.
    """
    from atoms.core.recovery import (
        CommitDecision,
        EffectJournalState,
        JournalState,
        ObservedAbsent,
        PersistentObservation,
        ScratchObservation,
        TransactionState,
        build_recovery_snapshot,
    )
    from atoms.core.recovery.snapshot import required_scratch_role

    return build_recovery_snapshot(
        compiled=compiled,
        topology=resolved.topology,
        transaction_state=TransactionState.PREPARED,
        commit_decision=CommitDecision.UNCOMMITTED,
        rollback_result=None,
        halt_diagnostic=None,
        active=True,
        journals=tuple(
            EffectJournalState(effect_id=effect.effect_id, state=JournalState.PENDING)
            for effect in compiled.spec.effects
        ),
        persistent_observations=tuple(
            PersistentObservation(path=timeline.path, entry=ObservedAbsent())
            for timeline in compiled.timelines
        ),
        scratch_observations=tuple(
            ScratchObservation(
                effect_id=effect.effect_id,
                role=required_scratch_role(effect),
                entry=ObservedAbsent(),
                file_build_relation=None,
            )
            for effect in compiled.spec.effects
        ),
    )


@pytest.mark.parametrize("label", sorted(AGREEMENT_CORPUS))
def test_every_produced_topology_validates_through_a3(label):
    compiled = compiled_for(*AGREEMENT_CORPUS[label])
    resolved = build_topology(compiled, prefixes_for(compiled), EXT4, work_for(compiled))
    snapshot = _prepared_snapshot(compiled, resolved)
    assert snapshot.topology == resolved.topology


@pytest.mark.parametrize("label", sorted(AGREEMENT_CORPUS))
def test_the_rerun_reaches_a2s_verdict_on_the_named_corpus(label):
    """The readable half of design §11.4's A2-agreement property. Under today's floor the
    resolved topology is provably identical to the lexical one, so a compiled
    specification and its re-run cannot disagree. A failure means the re-run drifted or
    the floor moved (design §7.4)."""
    from atoms.fs.topology import require_resolved_surface_and_ordering

    compiled = compiled_for(*AGREEMENT_CORPUS[label])
    resolved = build_topology(compiled, prefixes_for(compiled), EXT4, work_for(compiled))
    require_resolved_surface_and_ordering(compiled, resolved)


def test_a3_and_the_rerun_accept_every_specification_the_generator_compiles():
    """Criterion 18 says *every* compiled input, and ten named examples are a corpus, not
    a property. The generator enumerates all ordered sequences of length 1-3 over a fixed
    23-effect pool -- every variant against every path in a five-path alphabet, plus three
    moves -- and keeps the ones A2 admits. On this checkout that is 4841 specifications
    out of 12719 candidates, and it runs in about four seconds.

    Bounded and deterministic rather than random: no seed to record, no flake, and a
    failure is reproducible from its label. The exact count is asserted rather than a
    floor: `checked == compiled_count` would be tautological, since nothing between the
    two increments can skip, and `> 4000` would still pass if the generator quietly lost
    a whole variant. Update the constant deliberately when the pool changes.
    """
    from atoms.fs.topology import require_resolved_surface_and_ordering

    checked = 0
    for label, effects in generated_specifications():
        compiled = compiled_for(*effects)
        resolved = build_topology(
            compiled, prefixes_for(compiled), EXT4, work_for(compiled)
        )
        assert _prepared_snapshot(compiled, resolved).topology == resolved.topology, label
        require_resolved_surface_and_ordering(compiled, resolved)
        checked += 1

    assert checked == GENERATED_SPECIFICATION_COUNT


def test_the_rerun_refuses_a_creation_ordered_after_its_descendant(injected_equivalence):
    """The reachable half of the re-run. A2 phase 13 is lexical: it sees
    CreateDirectory("A") and an effect on "a/x" as unrelated and admits this. Under a
    folding parent they are one directory and the creation is too late.

    The surface half has no reachable case. A declared path becomes an *ancestor node*
    only by being a directory candidate, and a directory candidate is either a lexical
    proper prefix — which A2's own trie already walks — or a CreateDirectory endpoint,
    whose declared state is a DirectoryState and therefore never a blocker. A folding
    file at `A` above `a/x` is refused one phase earlier, by ancestor legality, because
    nothing creates the directory `a`. The surface branch stays as a fail-closed guard.
    """
    from atoms.fs.topology import require_resolved_surface_and_ordering

    injected_equivalence(str.casefold)
    compiled = compiled_for(
        CreateFileNoClobber("e1", "a/x", file_state()),
        CreateDirectory("mk", "A", DirectoryState(mode=0o755)),
    )
    prefixes = {
        "a/x": resolved_prefix("a/x", existing_depth=0),
        "A": resolved_prefix("A", existing_depth=0),
    }
    resolved = build_topology(compiled, prefixes, EXT4, WORK_CONSTRAINTS)
    with pytest.raises(ProjectApprovalRefused) as caught:
        require_resolved_surface_and_ordering(compiled, resolved)
    assert "creation must come first" in str(caught.value)
