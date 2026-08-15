"""The persistence-cut sweep: A3 agreement over every reconstructible survivor (design §5)."""

from __future__ import annotations

import pytest

MINIMAL = ("minimal-create", "minimal-replace", "minimal-delete", "minimal-move", "minimal-mkdir")


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


def test_the_later_placement_modes_are_declared_but_not_yet_built(cut_matrix) -> None:
    """The `Sweeper` accepts Task 7's and Task 9's keywords and refuses to pretend.

    A silently ignored `subprocess_subset=True` would let Task 7's placement test pass
    against the in-process arm it exists to contrast with.
    """
    with pytest.raises(NotImplementedError):
        cut_matrix("minimal-create", subprocess_subset=True)
    with pytest.raises(NotImplementedError):
        cut_matrix("minimal-create", sabotage="drop-barrier")
