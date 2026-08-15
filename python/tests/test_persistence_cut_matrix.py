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


def test_the_sabotage_mode_is_declared_but_not_yet_built(cut_matrix) -> None:
    """The `Sweeper` accepts Task 9's keyword and refuses to pretend."""
    with pytest.raises(NotImplementedError):
        cut_matrix("minimal-create", sabotage="drop-barrier")
