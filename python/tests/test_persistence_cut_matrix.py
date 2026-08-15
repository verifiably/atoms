"""The persistence-cut sweep: A3 agreement over every reconstructible survivor (design §5)."""

from __future__ import annotations

import pytest

MINIMAL = ("minimal-create", "minimal-replace", "minimal-delete", "minimal-move", "minimal-mkdir")
COMPOUND = ("corpus-write", "archive-move", "caught-rollback", "caught-rollback-move")
MOVE_BEARING = ("archive-move", "caught-rollback-move")


@pytest.mark.parametrize("name", MINIMAL)
def test_every_cell_of_the_minimal_scenarios_agrees_with_a3(name, cut_matrix) -> None:
    report = cut_matrix(name)
    assert report.cells > 0
    assert report.classified_cells > 0, (
        "no cell reached classify_recovery: the sweep would assert nothing about A3"
    )
    assert report.disagreements == ()
    assert report.second_pass_violations == ()
    assert report.side_assertion_failures == ()


def test_an_absent_approved_directory_freezes_a_node_missing_halt(
    cut_matrix, ext4_volume, test_storage_profile, monkeypatch
) -> None:
    """Design §4.4's realignment carve-out, backed by a test rather than by prose.

    `realign_durable_identities` rewrites the durable approval evidence's `(st_dev,
    st_ino)` pairs onto the reconstructed inodes, but deliberately leaves an **absent**
    approved directory's recorded identity alone, so a genuinely missing node still
    reaches `_diff_approved_topology`'s `NODE_MISSING` finding. That carve-out is what
    keeps the sweep from realigning away a real fault, and it matters more now that the
    creation-mode ruling drove the sweep's own assembly halts to zero: nothing else in
    the matrix exercises this path any more.

    The world is a real reconstructed cell with an active durable record, minus the
    approved directory `d` -- exactly the crash-plus-operator-damage shape the halt
    exists for. The assertions are that the halt fires, names `NODE_MISSING` on the
    missing path, is **frozen durably** in the record, and survives the second pass
    unchanged (`run_cell`'s own second-pass check).
    """
    import shutil
    from pathlib import Path

    from atoms.core.assembly import AssemblyFindingKind, AssemblyHaltReason
    from tests.persistence_model import (
        _discard_roots,
        _inspect,
        cell_roots,
        enumerate_cells,
        run_cell,
    )

    def remove_approved_directory(project: Path) -> None:
        # Raises FileNotFoundError on a cut that predates `d`; `run_cell` treats that as
        # "this cell cannot carry the drift" and runs it undrifted.
        shutil.rmtree(project / "d")

    stream = cut_matrix.record("minimal-replace")
    cells, _ = enumerate_cells(stream)

    found = None
    for index, cell in enumerate(cells):
        slot = f"absent-{index}"
        result = run_cell(
            cell,
            stream,
            ext4_volume,
            test_storage_profile,
            monkeypatch,
            slot=slot,
            allowlist=cut_matrix.allowlist(),
            drift=remove_approved_directory,
            retain=True,
        )
        if result.counts["assembly_halted"]:
            found = (slot, result)
            break
        _discard_roots(*cell_roots(ext4_volume, slot))

    assert found is not None, "no cell halted on the removed approved directory"
    slot, result = found
    assert result.halted
    assert result.counts["classified"] == 0, "the halt must precede classification"
    assert result.second_pass_violation is None
    assert result.side_assertion_failures == ()

    project_root, metadata_root = cell_roots(ext4_volume, slot)
    facts = _inspect(
        project_root, metadata_root, test_storage_profile, cut_matrix.allowlist()
    )
    halt = facts["record"].assembly_halt
    assert halt is not None, "the halt was raised without being frozen durably"
    assert halt.reason is AssemblyHaltReason.APPROVAL_EVIDENCE_MISMATCH
    missing = [
        finding
        for finding in halt.findings
        if finding.kind is AssemblyFindingKind.NODE_MISSING
    ]
    assert missing, f"expected a NODE_MISSING finding, got {halt.findings!r}"
    assert any(finding.path == "d" for finding in missing), missing


@pytest.mark.parametrize("name", COMPOUND)
def test_every_cell_of_the_compound_scenarios_agrees_with_a3(name, cut_matrix) -> None:
    """The multi-effect and caught-rollback half of design §13.4's matrix.

    `corpus-write` (five effects, two nested fresh directories) and `archive-move` (a
    move whose vacated source is re-created) are the documented consumer shapes; the two
    caught scenarios record a *rollback* stream, whose reverse traffic is the only place
    `JournalState.UNDO_STARTED` cuts exist at all.

    Erratum 3: the named-tuple assertion is made only for the move-bearing scenarios --
    `named_tuples` matches a transfer of a token that also carries an anchor insert, and
    a scenario with no move has nothing to match. `corpus-write` legitimately runs zero.
    """
    report = cut_matrix(name, caught=name.startswith("caught"))
    assert report.cells > 0
    assert report.classified_cells > 0, (
        "no cell reached classify_recovery: the sweep would assert nothing about A3"
    )
    assert report.disagreements == ()
    assert report.second_pass_violations == ()
    assert report.side_assertion_failures == ()
    if name in MOVE_BEARING:
        assert report.named_tuple_cells_ran > 0


# The two directions of design §9.4's named tuples, and where each is recorded: the
# forward pair comes from `minimal-move`'s own transfer, the reverse pair from the
# UNDO-era transfer a caught rollback emits when it re-moves the landed move.
MOVE_DIRECTIONS = (("forward", "minimal-move", False), ("reverse", "caught-rollback-move", True))
SOURCE = "project/d/source.txt"
DESTINATION = "project/d/destination.txt"


def _assert_repaired_to_the_move_pre_state(named) -> None:
    """Authority design §9.4's designed repair, asserted on a finished named cell.

    "Each is repaired to the pre-state the same way: remove the destination, or restore
    the source from the anchor" -- so *both* tuples, in *both* directions, owe the same
    world: `(source present, destination absent)` with the ownership anchor reclaimed.
    The reverse tuples are the reverse operation's own intermediates, and its pre-state
    is the same `(source present, destination absent)` the forward move started from.

    `NamedCell.__post_init__` has already required the cell to be clean (A3 agreement,
    the second pass, the side assertions). What is added here is the *value* of the
    repair -- which world recovery actually converged on -- plus the rollback result,
    which is the sharp discriminator: `RESTORED` says the move's own mutations were
    undone, where `EXTERNAL_DRIFT_PRESERVED` would say recovery declined to attribute
    them and left a live blocker in place.
    """
    from atoms.core.recovery import RollbackResult, TransactionState
    from tests.coordinator_support import BEFORE
    from tests.persistence_model import _scratch_survivors

    result = named.result
    where = f"{named.scenario} {named.tuple_name}"
    assert not result.halted, where
    assert result.counts["classified"] == 1, f"{where}: the cell never reached A3"
    assert result.world[SOURCE][:3] == ("file", BEFORE, 0o644), (
        f"{where}: the source was not restored: {result.world.get(SOURCE)!r}"
    )
    assert DESTINATION not in result.world, f"{where}: the destination survived the repair"
    assert _scratch_survivors(result.world) == (), (
        f"{where}: the ownership anchor outlived the repair"
    )
    assert result.projection[0] is TransactionState.ROLLED_BACK, where
    assert result.projection[2] is RollbackResult.RESTORED, (
        f"{where}: rollback result {result.projection[2]!r} is not a repair"
    )


@pytest.mark.parametrize(("direction", "scenario", "caught"), MOVE_DIRECTIONS)
def test_dual_name_tuples_repair_by_removing_the_destination(
    direction, scenario, caught, cut_matrix
) -> None:
    """Design §9.4's dual-name tuple: the insertion is durable, the removal is not.

    The reconstructed world is the one the authority names -- "source, destination, and
    anchor all naming the original inode" -- and that is asserted here, on the cell's own
    modelled tree, before reading the repair: a test that only checked the post-recovery
    world would still pass if `named_tuples` handed back some *other* cell entirely.
    """
    from tests.persistence_model import _scratch_survivors

    named = cut_matrix.named_cell(scenario, f"dual-name-{direction}", caught=caught)
    tree = named.cell.state.tree
    assert SOURCE in tree and DESTINATION in tree, (
        f"{direction}: the dual-name cut does not hold both live names"
    )
    anchors = _scratch_survivors(tree)
    assert len(anchors) == 1, anchors
    # A `world_tree`/`_materialize_tree` file value grows a fourth member -- its
    # hard-link group key -- exactly when the inode carries more than one name, so
    # "all three are 4-tuples sharing one key" is "all three name the original inode".
    named_values = [tree[rel] for rel in (SOURCE, DESTINATION, *anchors)]
    assert all(len(value) == 4 for value in named_values) and (
        len({value[3] for value in named_values}) == 1
    ), f"{direction}: source, destination and anchor are not one inode: {named_values!r}"
    _assert_repaired_to_the_move_pre_state(named)


@pytest.mark.parametrize(("direction", "scenario", "caught"), MOVE_DIRECTIONS)
def test_anchor_only_tuples_repair_by_restoring_the_source(
    direction, scenario, caught, cut_matrix
) -> None:
    """Design §9.4's anchor-only tuple: the removal is durable, the insertion is not.

    "Both persistent paths absent, only the durable anchor surviving" -- so the
    reconstructed world is asserted to hold neither live name, which is what makes the
    repair a *restoration from the anchor* rather than the dual-name tuple's removal of a
    second name. Both reach the same pre-state, which is exactly why the starting world
    has to be pinned here.
    """
    from tests.coordinator_support import BEFORE
    from tests.persistence_model import _scratch_survivors

    named = cut_matrix.named_cell(scenario, f"anchor-only-{direction}", caught=caught)
    tree = named.cell.state.tree
    assert SOURCE not in tree and DESTINATION not in tree, (
        f"{direction}: a persistent name survived the anchor-only cut"
    )
    anchors = _scratch_survivors(tree)
    assert len(anchors) == 1, anchors
    assert tree[anchors[0]][:3] == ("file", BEFORE, 0o644), tree[anchors[0]]
    _assert_repaired_to_the_move_pre_state(named)


def test_drift_cells_preserve_external_blockers(cut_matrix) -> None:
    """Design §6's drift family: an external blocker planted between reconstruction and
    recovery survives every cell that could carry it.

    `drift-blocker` plants a foreign `d/f.txt` over the `DeletePath` target. Recovery may
    refuse to proceed, may undo, may commit -- what it may never do is silently consume
    the blocker, and `preserved_drift_cells` counts only the classified cells where the
    whole planted footprint came back unchanged.

    The count is required to equal `classified_cells`, not merely to be positive: a cell
    that *consumed* the blocker does not raise, it simply fails to increment, so a `> 0`
    assertion would stay green while most of the sweep ate the drift. Equality also pins
    the other half -- every classified cell of this scenario is late enough to carry the
    blocker at all (measured: 18 of 18). A future drift whose target predates some
    classified cut would have to make that carve-out explicit here.
    """
    report = cut_matrix("drift-blocker", drift=True)
    assert report.disagreements == ()
    assert report.second_pass_violations == ()
    assert report.side_assertion_failures == ()
    assert report.classified_cells > 0
    assert report.preserved_drift_cells == report.classified_cells


@pytest.mark.parametrize("name", ("minimal-move", "minimal-replace"))
def test_subprocess_placement_matches_in_process(name, cut_matrix) -> None:
    """Design §8's placement axis: the same cell, recovered in a fresh process, agrees.

    The subset is declared, never sampled -- `Sweeper.__call__`'s docstring states the
    rule, and the arm itself asserts the rule selected something, so a rule that silently
    selected nothing fails inside the sweep rather than leaving every caller to remember
    an emptiness check.

    Two scenarios because the rule's three clauses are not all live in one stream:
    `minimal-move` is the only minimal scenario carrying design §9.4's named tuples, and
    it never halts; `minimal-replace` carries the A3 halts whose *persisted halt
    diagnostic* is the arm's exact-comparison target. Sweeping only the first would leave
    that comparison comparing `None` to `None` in every cell.
    """
    report = cut_matrix(name, subprocess_subset=True)
    assert report.disagreements == ()
    assert report.subprocess_disagreements == ()
    if name == "minimal-replace":
        assert report.subprocess_halt_cells > 0, (
            "no halted cell crossed the placement boundary: the exact halt-diagnostic "
            "comparison asserted nothing"
        )


def _mkdir_publication_cut(stream) -> tuple[int, tuple]:
    """The cut *between* `CreateDirectory`'s two publication flushes, and the work-slot
    entry key whose removal is not durable there.

    Authority §9.5 orders the cross-directory publication "fsync the live parent first
    ... then fsync `work/` so the staging-name removal is durable, and only then mark
    `DONE`". Both flushes are found from the recorded stream rather than assumed: the
    publication is the two-unit `remove`+`insert` `Mutation` whose insert names `d` in the
    project root, and the cut is one past the first `Barrier` covering that insert. The
    returned key is the transfer's `remove`, still pending at that cut -- design §4.3's
    "the work-slot removal unit did not survive".
    """
    from tests.persistence_model import Barrier, Mutation, pending_keys_at

    insert_key = ("entry", stream.roots["project"], "d")
    publication = None
    for index, event in enumerate(stream.events):
        if type(event) is not Mutation or len(event.units) != 2:
            continue
        by_change = {unit.change: unit for unit in event.units}
        if set(by_change) != {"remove", "insert"}:
            continue
        if by_change["insert"].key == insert_key:
            assert publication is None, "minimal-mkdir published `d` more than once"
            publication = (index, by_change["remove"].key)
    assert publication is not None, "no publication of `d` in the recorded stream"
    index, remove_key = publication
    for position in range(index + 1, len(stream.events)):
        event = stream.events[position]
        if type(event) is Barrier and insert_key in event.covered:
            cut = position + 1
            assert remove_key in pending_keys_at(stream, cut), (
                "the live-parent flush also covered the work-slot removal: the two "
                "publication updates are not separately durable in this recording"
            )
            return cut, remove_key
    raise AssertionError("the publication was never followed by a live-parent flush")


def _inject_one_identity(monkeypatch, path: str) -> dict[str, int]:
    """Report the live directory at `path` and the work survivor with ONE identity, at
    **both** of recovery's observation seams (erratum 4).

    `atoms.fs.observe.Observation` mints an identity token per pinned descriptor, so two
    distinct inodes can never share one; the injection is therefore applied above the
    filesystem, to what recovery *observes*. Both routes are wrapped because they feed
    two different decisions:

    - `_observe_snapshot` builds the snapshot `classify_recovery` reads, which is where
      `_classify_directory`'s `same_identity` chooses landed-effect repair over
      "preserve the live blocker" (`core/recovery/variants.py`);
    - `_observe_for_step` re-observes the world before each mutating step, and
      `authorize_recovery_step` compares the *identity relations* of what it sees against
      the ones the plan step expects. Injecting only the first leaves authorization
      seeing the true, distinct inodes: measured, recovery then halts
      `PLAN_PRECONDITION_CHANGED` with `expected ... SAME` against `observed ...
      DIFFERENT` -- a false blocker arriving one phase later than the one the design
      names.

    The shared token is the live directory's own identity, never a fabricated object:
    `Observation.pinned_descriptor` resolves an identity to the descriptor pinning it,
    and an invented token would be a value the pass never observed.

    Returns the per-seam fired counters, so the caller can require each wrapper to have
    done something -- a seam that is renamed or bypassed makes the test fail rather than
    quietly pass on an uninjected world.
    """
    import dataclasses

    from atoms.coordinator import recover
    from atoms.core.recovery import (
        JointObservation,
        ObservedDirectory,
        build_recovery_snapshot,
    )

    fired = {"snapshot": 0, "step": 0}

    def share(persistent, scratch):
        live = next(
            (
                item
                for item in persistent
                if item.path == path and type(item.entry) is ObservedDirectory
            ),
            None,
        )
        # `minimal-mkdir` has exactly one directory-valued scratch slot -- e1's
        # `.#~<txid>.e1.work` staging leaf; e2's staging slot is a file. A second one
        # would make "the work survivor" ambiguous, so it fails rather than picking.
        directories = [item for item in scratch if type(item.entry) is ObservedDirectory]
        assert len(directories) <= 1, f"ambiguous work survivor: {directories!r}"
        work = directories[0] if directories else None
        if live is None or work is None:
            return scratch, False
        shared = dataclasses.replace(
            work, entry=dataclasses.replace(work.entry, identity=live.entry.identity)
        )
        return tuple(shared if item is work else item for item in scratch), True

    inner_snapshot = recover._observe_snapshot
    inner_step = recover._observe_for_step

    def observe_snapshot(*args, **kwargs):
        snapshot = inner_snapshot(*args, **kwargs)
        scratch, hit = share(
            snapshot.persistent_observations, snapshot.scratch_observations
        )
        fired["snapshot"] += int(hit)
        return build_recovery_snapshot(
            compiled=snapshot.compiled,
            topology=snapshot.topology,
            transaction_state=snapshot.transaction_state,
            commit_decision=snapshot.commit_decision,
            rollback_result=snapshot.rollback_result,
            halt_diagnostic=snapshot.halt_diagnostic,
            active=snapshot.active,
            journals=snapshot.journals,
            persistent_observations=snapshot.persistent_observations,
            scratch_observations=scratch,
        )

    def observe_for_step(*args, **kwargs):
        observed = inner_step(*args, **kwargs)
        scratch, hit = share(observed.persistent, observed.scratch)
        fired["step"] += int(hit)
        return JointObservation(observed.persistent, scratch, observed.parent_occupancy)

    monkeypatch.setattr(recover, "_observe_snapshot", observe_snapshot)
    monkeypatch.setattr(recover, "_observe_for_step", observe_for_step)
    return fired


def test_same_inode_work_survivor_is_landed_not_blocker(
    cut_matrix, ext4_volume, test_storage_profile, monkeypatch
) -> None:
    """Design §4.5: the §9.5 tuple at the observation seam.

    §9.5's intermediate -- the live parent durably holding the published directory while
    the `work/` staging name's removal is not yet durable, both names one inode -- is not
    host-reconstructible, and design §4.5 records why: no in-process cut can materialize
    it (after `transfer_noclobber` returns the `work/` name is already gone), Linux
    forbids hard-linking directories, and ext4 journals a rename as one atomic
    transaction, so no completion-ordered replay prefix splits the two directory updates.
    Measured here as well: the staging leaf's own *name* is never separately flushed
    (§9.5 flushes the staging descriptor, which covers its mode, not its parent's entry),
    so the recorded stream carries no durable work-slot name for a survivor subset to
    keep at all.

    So the physical world is the reachable half -- the cut between the two publication
    flushes, with the work-slot removal not surviving -- plus the one entry no cut can
    produce, spliced into the cell's modelled tree at the mode §9.5's `fchmod` gives the
    staging directory. Reconstruction mints it as a *separate inode* (`os.mkdir`), and the
    identity §9.5 describes is injected at both observation seams instead.

    The assertions are the design's own sentence, "recovery removes the stale `work/`
    entry and treats the effect as landed, subject to the transaction's forward/rollback
    decision", read against an uncommitted transaction: the effect is attributed, so the
    rollback *undoes* it -- `RESTORED`, with `d` gone and the work slot empty. The
    misreading this test exists to exclude produces the opposite world, and both of its
    forms were measured before the injection was complete: with no injection at all,
    recovery reads the two inodes as a foreign blocker and finishes
    `EXTERNAL_DRIFT_PRESERVED` with `d` preserved; with classification injected alone, it
    halts `PLAN_PRECONDITION_CHANGED` at authorization.
    """
    import dataclasses

    from atoms.core.recovery import RollbackResult, TransactionState
    from tests.persistence_model import (
        Cell,
        Skip,
        _occupied_slots,
        apply_survivors,
        complete_named_cell,
        durable_state,
        run_cell,
    )

    stream = cut_matrix.record("minimal-mkdir")
    cut, remove_key = _mkdir_publication_cut(stream)
    base = durable_state(stream, cut)
    # No pending unit survives: the live insertion is already durable at this cut (the
    # live-parent flush covered it) and the work-slot removal is exactly what must not
    # land. `complete_named_cell` folds back the non-`entry` and probe-noise keys the
    # sweep folds, leaving the entry topology as chosen.
    published = apply_survivors(base, stream, cut, frozenset())
    assert not isinstance(published, Skip), published
    cell = complete_named_cell(Cell(cut, frozenset(), published), stream)

    slots = [
        rel
        for rel in cell.state.tree
        if rel.startswith("metadata/work/") and rel.count("/") == 2
    ]
    assert len(slots) == 1, f"expected exactly one work slot, got {slots}"
    survivor = f"{slots[0]}/{remove_key[2]}"
    assert cell.state.tree["project/d"] == ("dir", 0o755), cell.state.tree["project/d"]
    assert survivor not in cell.state.tree, (
        "the work-slot name is durable at this cut after all: the §9.5 world is "
        "reachable and must be swept rather than spliced"
    )
    tree = dict(cell.state.tree)
    tree[survivor] = ("dir", 0o755)
    cell = dataclasses.replace(cell, state=dataclasses.replace(cell.state, tree=tree))

    fired = _inject_one_identity(monkeypatch, "d")
    result = run_cell(
        cell,
        stream,
        ext4_volume,
        test_storage_profile,
        monkeypatch,
        slot="same-inode-work-survivor",
        allowlist=cut_matrix.allowlist(),
    )

    assert fired["snapshot"] > 0, "the classification seam never saw the two directories"
    assert fired["step"] > 0, "the authorization seam never saw the two directories"
    assert not result.halted, "recovery halted on the §9.5 intermediate"
    assert result.counts["classified"] == 1
    assert result.disagreement is None, result.disagreement
    assert result.second_pass_violation is None, result.second_pass_violation
    assert result.side_assertion_failures == (), result.side_assertion_failures
    assert result.projection is not None, "the cell carried no durable record"
    assert result.projection[0] is TransactionState.ROLLED_BACK
    assert result.projection[2] is RollbackResult.RESTORED, (
        f"the work survivor was misread as a blocker: {result.projection[2]!r}"
    )
    assert survivor not in result.world, "the stale work-slot name survived recovery"
    assert _occupied_slots(result.world) == (), _occupied_slots(result.world)
    assert "project/d" not in result.world, (
        "the landed directory was preserved instead of being undone"
    )


def test_the_sabotage_mode_is_declared_but_not_yet_built(cut_matrix) -> None:
    """The `Sweeper` accepts Task 9's keyword and refuses to pretend."""
    with pytest.raises(NotImplementedError):
        cut_matrix("minimal-create", sabotage="drop-barrier")
